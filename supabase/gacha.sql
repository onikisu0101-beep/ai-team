-- ===========================================================================
--  gacha.sql — タロットカード・ガチャ / Supabase スキーマ定義
-- ---------------------------------------------------------------------------
--  実行順序:
--    1) supabase/gacha.sql       ← このファイル（型・テーブル・関数・RLS）
--    2) supabase/seed-cards.sql  ← 78枚のカードデータ投入
--
--  実行方法:
--    A) Supabase Dashboard > SQL Editor に貼り付けて Run
--    B) psql "$DATABASE_URL" -f supabase/gacha.sql
--
--  ※ 冪等（何度実行しても同じ結果になる）ように書いてあります。
--  ※ カード意味データの出典: guidelines/03_tarot-content-guide.md
-- ===========================================================================

begin;

-- ---------------------------------------------------------------------------
-- 0. 拡張機能
-- ---------------------------------------------------------------------------
create extension if not exists pgcrypto with schema extensions;

-- ---------------------------------------------------------------------------
-- 1. 列挙型（ENUM）
--    create type は IF NOT EXISTS 非対応のため DO ブロックで冪等化
-- ---------------------------------------------------------------------------
do $$ begin
  create type public.card_arcana as enum ('major', 'minor');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.card_suit as enum ('wands', 'cups', 'swords', 'pentacles');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.card_element as enum ('fire', 'water', 'air', 'earth');
exception when duplicate_object then null; end $$;

-- 並び順が重要（N < R < SR < SSR）。確定枠・天井の比較に使う
do $$ begin
  create type public.card_rarity as enum ('N', 'R', 'SR', 'SSR');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.card_orientation as enum ('upright', 'reversed');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.draw_source as enum ('daily', 'single', 'multi');
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------------------
-- 2. マスタテーブル
-- ---------------------------------------------------------------------------

-- 2-1. スート（小アルカナの組）マスタ
create table if not exists public.suits (
  suit        public.card_suit   primary key,
  name_ja     text               not null,
  name_en     text               not null,
  element     public.card_element not null,
  theme       text               not null,   -- 例: 情熱・行動・創造・仕事
  sort_order  smallint           not null
);

comment on table public.suits is '小アルカナ4スートのマスタ（エレメント・テーマ）';

-- 2-2. カードマスタ（78枚）
create table if not exists public.cards (
  id                uuid                primary key default gen_random_uuid(),
  code              text                not null unique,   -- M00 / W01 / C14 など
  arcana            public.card_arcana  not null,
  suit              public.card_suit    references public.suits (suit),
  number            smallint            not null,          -- 大:0-21 / 小:1-14
  name_ja           text                not null,
  name_en           text                not null,
  rarity            public.card_rarity  not null,
  upright_meaning   text                not null,
  reversed_meaning  text                not null,
  upright_keywords  text[]              not null default '{}',
  reversed_keywords text[]              not null default '{}',
  image_path        text,
  created_at        timestamptz         not null default now(),

  -- 大アルカナは suit なし・0〜21、小アルカナは suit あり・1〜14
  constraint cards_arcana_shape check (
    (arcana = 'major' and suit is null     and number between 0 and 21) or
    (arcana = 'minor' and suit is not null and number between 1 and 14)
  ),
  constraint cards_unique_position unique (arcana, suit, number)
);

comment on table  public.cards is 'ライダー・ウェイト版タロット78枚のマスタ';
comment on column public.cards.number is '大アルカナ:0-21 / 小アルカナ:1=エース, 11=ページ, 12=ナイト, 13=クイーン, 14=キング';

create index if not exists cards_rarity_idx on public.cards (rarity);
create index if not exists cards_arcana_idx on public.cards (arcana);

-- 2-3. 排出率テーブル（コードを触らずに確率を調整できるようにする）
create table if not exists public.gacha_rates (
  rarity   public.card_rarity primary key,
  weight   numeric(8,4)       not null check (weight >= 0),
  label    text               not null,
  enabled  boolean            not null default true
);

comment on table public.gacha_rates is 'レアリティ別の排出重み。weight の合計に対する比率で抽選される';

insert into public.gacha_rates (rarity, weight, label) values
  ('SSR', 5.0000,  '大アルカナ'),
  ('SR',  15.0000, 'コートカード（ページ〜キング）'),
  ('R',   20.0000, 'エース'),
  ('N',   60.0000, '数札（2〜10）')
on conflict (rarity) do update
  set weight = excluded.weight,
      label  = excluded.label;

-- 2-4. ガチャ設定（1行のみ）
create table if not exists public.gacha_config (
  id                      boolean            primary key default true check (id),
  gem_cost_per_draw       int                not null default 10  check (gem_cost_per_draw >= 0),
  multi_draw_count        int                not null default 10  check (multi_draw_count >= 2),
  multi_guarantee_rarity  public.card_rarity not null default 'SR',
  ssr_pity_threshold      int                not null default 50  check (ssr_pity_threshold >= 1),
  signup_bonus_gems       int                not null default 100 check (signup_bonus_gems >= 0),
  daily_login_gems        int                not null default 10  check (daily_login_gems >= 0),
  updated_at              timestamptz        not null default now()
);

comment on table  public.gacha_config is 'ガチャの全体設定（単一行）';
comment on column public.gacha_config.multi_guarantee_rarity is '10連の最終枠で確定させる最低レアリティ';
comment on column public.gacha_config.ssr_pity_threshold is '天井: この回数だけSSRが出なければ次でSSR確定';

insert into public.gacha_config (id) values (true) on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- 3. ユーザーデータ
-- ---------------------------------------------------------------------------

-- 3-1. プロフィール
create table if not exists public.profiles (
  id                 uuid        primary key references auth.users (id) on delete cascade,
  display_name       text,
  avatar_url         text,
  gems               int         not null default 0 check (gems >= 0),
  pity_counter       int         not null default 0 check (pity_counter >= 0),
  total_draws        int         not null default 0 check (total_draws >= 0),
  last_login_on      date,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

comment on column public.profiles.pity_counter is 'SSRが出ていない連続回数（天井カウンタ）';

-- 3-2. 抽選履歴
create table if not exists public.draws (
  id           uuid                    primary key default gen_random_uuid(),
  user_id      uuid                    not null references public.profiles (id) on delete cascade,
  card_id      uuid                    not null references public.cards (id),
  orientation  public.card_orientation not null,
  rarity       public.card_rarity      not null,
  source       public.draw_source      not null,
  drawn_at     timestamptz             not null default now(),
  -- JST基準の「引いた日」。デイリー1回制限の判定に使う
  drawn_on     date generated always as (((drawn_at at time zone 'Asia/Tokyo'))::date) stored
);

comment on table  public.draws is 'ガチャ／デイリーの抽選履歴';
comment on column public.draws.drawn_on is 'JST(Asia/Tokyo)基準の日付。日付境界は日本時間の0時';

create index if not exists draws_user_drawn_at_idx on public.draws (user_id, drawn_at desc);

-- デイリーカードは 1ユーザー 1日 1枚まで（DBレベルで保証）
create unique index if not exists draws_one_daily_per_user_per_day
  on public.draws (user_id, drawn_on)
  where source = 'daily';

-- 3-3. コレクション（所持カード）
create table if not exists public.user_cards (
  user_id         uuid        not null references public.profiles (id) on delete cascade,
  card_id         uuid        not null references public.cards (id),
  owned_count     int         not null default 0 check (owned_count >= 0),
  upright_count   int         not null default 0 check (upright_count >= 0),
  reversed_count  int         not null default 0 check (reversed_count >= 0),
  first_drawn_at  timestamptz not null default now(),
  last_drawn_at   timestamptz not null default now(),
  primary key (user_id, card_id)
);

comment on table public.user_cards is 'ユーザーごとのカードコレクション（重複所持数つき）';

-- ---------------------------------------------------------------------------
-- 4. 共通トリガ
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

drop trigger if exists gacha_config_set_updated_at on public.gacha_config;
create trigger gacha_config_set_updated_at
  before update on public.gacha_config
  for each row execute function public.set_updated_at();

-- サインアップ時にプロフィールを自動生成し、初回ボーナスジェムを付与
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_bonus int;
begin
  select signup_bonus_gems into v_bonus from public.gacha_config where id;

  insert into public.profiles (id, display_name, gems)
  values (
    new.id,
    coalesce(
      new.raw_user_meta_data ->> 'display_name',
      new.raw_user_meta_data ->> 'name',
      split_part(coalesce(new.email, 'guest@example.com'), '@', 1)
    ),
    coalesce(v_bonus, 0)
  )
  on conflict (id) do nothing;

  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------
-- 5. 抽選ロジック（内部ヘルパー）
-- ---------------------------------------------------------------------------

-- 正位置／逆位置を 50:50 で決定
create or replace function public.roll_orientation()
returns public.card_orientation
language sql
volatile
as $$
  select case when random() < 0.5 then 'upright' else 'reversed' end::public.card_orientation;
$$;

-- 重み付き抽選で1枚選ぶ。p_min_rarity を指定するとそれ以上のレアリティに限定（確定枠用）
create or replace function public.pick_card(p_min_rarity public.card_rarity default null)
returns setof public.cards
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
  v_rarity public.card_rarity;
  v_total  numeric;
  v_roll   numeric;
  v_rows   int;
begin
  select sum(weight) into v_total
    from public.gacha_rates
   where enabled
     and (p_min_rarity is null or rarity >= p_min_rarity);

  if coalesce(v_total, 0) <= 0 then
    raise exception 'gacha_rates に有効な排出率が設定されていません' using errcode = 'P0001';
  end if;

  v_roll := random() * v_total;

  -- 累積重みで該当レアリティを決定
  select r.rarity into v_rarity
    from (
      select rarity,
             sum(weight) over (order by rarity
                               rows between unbounded preceding and current row) as cum
        from public.gacha_rates
       where enabled
         and (p_min_rarity is null or rarity >= p_min_rarity)
    ) r
   where v_roll <= r.cum
   order by r.cum
   limit 1;

  return query
    select * from public.cards
     where rarity = v_rarity
     order by random()
     limit 1;

  get diagnostics v_rows = row_count;

  -- 該当レアリティのカードが未投入の場合のフォールバック（seed-cards.sql 未実行など）
  if v_rows = 0 then
    return query
      select * from public.cards
       where (p_min_rarity is null or rarity >= p_min_rarity)
       order by random()
       limit 1;

    get diagnostics v_rows = row_count;
    if v_rows = 0 then
      raise exception 'カードが1枚も登録されていません。supabase/seed-cards.sql を実行してください'
        using errcode = 'P0001';
    end if;
  end if;
end;
$$;

-- コレクションに加算し、新規獲得かどうかを返す
create or replace function public.add_to_collection(
  p_user        uuid,
  p_card        uuid,
  p_orientation public.card_orientation
)
returns boolean
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
  v_is_new boolean;
begin
  insert into public.user_cards as uc
    (user_id, card_id, owned_count, upright_count, reversed_count, first_drawn_at, last_drawn_at)
  values
    (p_user, p_card, 1,
     case when p_orientation = 'upright'  then 1 else 0 end,
     case when p_orientation = 'reversed' then 1 else 0 end,
     now(), now())
  on conflict (user_id, card_id) do update
    set owned_count    = uc.owned_count + 1,
        upright_count  = uc.upright_count  + case when p_orientation = 'upright'  then 1 else 0 end,
        reversed_count = uc.reversed_count + case when p_orientation = 'reversed' then 1 else 0 end,
        last_drawn_at  = now()
  returning (xmax = 0) into v_is_new;   -- xmax = 0 なら INSERT（＝初獲得）

  return coalesce(v_is_new, false);
end;
$$;

-- ---------------------------------------------------------------------------
-- 6. 公開RPC（クライアントから呼ぶ関数）
-- ---------------------------------------------------------------------------

-- 6-1. デイリーカード（無料・1日1枚・JST基準）
--      その日すでに引いていれば同じカードを返す（冪等）
create or replace function public.draw_daily_card()
returns table (
  draw_id       uuid,
  card_id       uuid,
  code          text,
  name_ja       text,
  name_en       text,
  arcana        public.card_arcana,
  suit          public.card_suit,
  rarity        public.card_rarity,
  orientation   public.card_orientation,
  meaning       text,
  keywords      text[],
  image_path    text,
  is_new        boolean,
  already_drawn boolean,
  drawn_at      timestamptz
)
language plpgsql
volatile
security definer
set search_path = public
as $$
#variable_conflict use_column
declare
  v_user    uuid := auth.uid();
  v_today   date := ((now() at time zone 'Asia/Tokyo'))::date;
  v_draw    public.draws%rowtype;
  v_card    public.cards%rowtype;
  v_or      public.card_orientation;
  v_is_new  boolean := false;
  v_already boolean := false;
  v_bonus   int;
begin
  if v_user is null then
    raise exception 'ログインが必要です' using errcode = '42501';
  end if;

  select * into v_draw
    from public.draws
   where user_id = v_user and source = 'daily' and drawn_on = v_today
   limit 1;

  if found then
    v_already := true;
    select * into v_card from public.cards where id = v_draw.card_id;
  else
    select * into v_card from public.pick_card(null);
    v_or := public.roll_orientation();

    insert into public.draws (user_id, card_id, orientation, rarity, source)
    values (v_user, v_card.id, v_or, v_card.rarity, 'daily')
    returning * into v_draw;

    v_is_new := public.add_to_collection(v_user, v_card.id, v_or);

    -- ログインボーナス（1日1回、デイリーカードと同時に付与）
    select daily_login_gems into v_bonus from public.gacha_config where id;

    update public.profiles
       set gems          = gems + coalesce(v_bonus, 0),
           total_draws   = total_draws + 1,
           last_login_on = v_today
     where id = v_user;
  end if;

  return query
    select v_draw.id,
           v_card.id,
           v_card.code,
           v_card.name_ja,
           v_card.name_en,
           v_card.arcana,
           v_card.suit,
           v_card.rarity,
           v_draw.orientation,
           case when v_draw.orientation = 'upright'
                then v_card.upright_meaning else v_card.reversed_meaning end,
           case when v_draw.orientation = 'upright'
                then v_card.upright_keywords else v_card.reversed_keywords end,
           v_card.image_path,
           v_is_new,
           v_already,
           v_draw.drawn_at;
end;
$$;

comment on function public.draw_daily_card() is
  'デイリーカードを1枚引く。JST基準で1日1回。同日2回目以降は同じカードを返す';

-- 6-2. 有料ガチャ（1連 / 10連）
--      ・ジェムを消費
--      ・10連の最終枠は multi_guarantee_rarity 以上を確定
--      ・ssr_pity_threshold 回SSRが出なければ天井でSSR確定
create or replace function public.draw_gacha(p_count int default 1)
returns table (
  draw_id     uuid,
  card_id     uuid,
  code        text,
  name_ja     text,
  name_en     text,
  arcana      public.card_arcana,
  suit        public.card_suit,
  rarity      public.card_rarity,
  orientation public.card_orientation,
  meaning     text,
  keywords    text[],
  image_path  text,
  is_new      boolean,
  slot        int,
  gems_left   int
)
language plpgsql
volatile
security definer
set search_path = public
as $$
#variable_conflict use_column
declare
  v_user    uuid := auth.uid();
  v_cfg     public.gacha_config%rowtype;
  v_profile public.profiles%rowtype;
  v_cost    int;
  v_source  public.draw_source;
  v_i       int;
  v_min     public.card_rarity;
  v_best    public.card_rarity := 'N';
  v_pity    int;
  v_card    public.cards%rowtype;
  v_or      public.card_orientation;
  v_draw    public.draws%rowtype;
  v_is_new  boolean;
  v_gems    int;
begin
  if v_user is null then
    raise exception 'ログインが必要です' using errcode = '42501';
  end if;

  select * into v_cfg from public.gacha_config where id;

  if p_count is null or p_count < 1 or p_count > v_cfg.multi_draw_count then
    raise exception '引ける回数は1〜%回です（指定値: %）', v_cfg.multi_draw_count, p_count
      using errcode = '22023';
  end if;

  -- 同時実行による二重消費を防ぐため行ロック
  select * into v_profile from public.profiles where id = v_user for update;
  if not found then
    raise exception 'プロフィールが見つかりません。先にサインアップしてください' using errcode = 'P0001';
  end if;

  v_cost   := v_cfg.gem_cost_per_draw * p_count;
  v_source := case when p_count >= v_cfg.multi_draw_count then 'multi' else 'single' end;

  if v_profile.gems < v_cost then
    raise exception 'ジェムが足りません（必要: % / 所持: %）', v_cost, v_profile.gems
      using errcode = 'P0001';
  end if;

  v_gems := v_profile.gems - v_cost;
  v_pity := v_profile.pity_counter;

  for v_i in 1 .. p_count loop
    v_min := null;

    -- 天井: 次の1回でSSR確定
    if v_pity + 1 >= v_cfg.ssr_pity_threshold then
      v_min := 'SSR';
    end if;

    -- 10連の最終枠: まだSR以上が出ていなければ確定枠に切り替え
    if v_min is null
       and v_i = p_count
       and p_count >= v_cfg.multi_draw_count
       and v_best < v_cfg.multi_guarantee_rarity then
      v_min := v_cfg.multi_guarantee_rarity;
    end if;

    select * into v_card from public.pick_card(v_min);
    v_or := public.roll_orientation();

    if v_card.rarity = 'SSR' then
      v_pity := 0;
    else
      v_pity := v_pity + 1;
    end if;

    if v_card.rarity > v_best then
      v_best := v_card.rarity;
    end if;

    insert into public.draws (user_id, card_id, orientation, rarity, source)
    values (v_user, v_card.id, v_or, v_card.rarity, v_source)
    returning * into v_draw;

    v_is_new := public.add_to_collection(v_user, v_card.id, v_or);

    draw_id     := v_draw.id;
    card_id     := v_card.id;
    code        := v_card.code;
    name_ja     := v_card.name_ja;
    name_en     := v_card.name_en;
    arcana      := v_card.arcana;
    suit        := v_card.suit;
    rarity      := v_card.rarity;
    orientation := v_or;
    meaning     := case when v_or = 'upright'
                        then v_card.upright_meaning else v_card.reversed_meaning end;
    keywords    := case when v_or = 'upright'
                        then v_card.upright_keywords else v_card.reversed_keywords end;
    image_path  := v_card.image_path;
    is_new      := v_is_new;
    slot        := v_i;
    gems_left   := v_gems;

    return next;
  end loop;

  update public.profiles
     set gems         = v_gems,
         pity_counter = v_pity,
         total_draws  = total_draws + p_count
   where id = v_user;

  return;
end;
$$;

comment on function public.draw_gacha(int) is
  'ジェムを消費してガチャを引く。10連は最終枠SR以上確定＋天井カウンタあり';

-- 6-3. コレクション達成率
create or replace function public.get_collection_progress()
returns table (
  rarity      public.card_rarity,
  total_cards bigint,
  owned_cards bigint,
  percent     numeric
)
language sql
stable
security definer
set search_path = public
as $$
  select c.rarity,
         count(*)                                            as total_cards,
         count(uc.card_id)                                   as owned_cards,
         round(100.0 * count(uc.card_id) / nullif(count(*), 0), 1) as percent
    from public.cards c
    left join public.user_cards uc
      on uc.card_id = c.id
     and uc.user_id = auth.uid()
   group by c.rarity
   order by c.rarity desc;
$$;

-- ---------------------------------------------------------------------------
-- 7. ビュー
-- ---------------------------------------------------------------------------

-- 図鑑ビュー: 全78枚 ＋ 自分の所持状況
create or replace view public.v_my_collection
with (security_invoker = on) as
  select c.id,
         c.code,
         c.arcana,
         c.suit,
         s.name_ja as suit_name_ja,
         s.element,
         c.number,
         c.name_ja,
         c.name_en,
         c.rarity,
         c.upright_meaning,
         c.reversed_meaning,
         c.upright_keywords,
         c.reversed_keywords,
         c.image_path,
         coalesce(uc.owned_count, 0)    as owned_count,
         coalesce(uc.upright_count, 0)  as upright_count,
         coalesce(uc.reversed_count, 0) as reversed_count,
         (uc.card_id is not null)       as owned,
         uc.first_drawn_at,
         uc.last_drawn_at
    from public.cards c
    left join public.suits s on s.suit = c.suit
    left join public.user_cards uc
      on uc.card_id = c.id
     and uc.user_id = auth.uid();

-- 直近の抽選履歴（カード情報つき）
create or replace view public.v_my_draw_history
with (security_invoker = on) as
  select d.id as draw_id,
         d.drawn_at,
         d.drawn_on,
         d.source,
         d.orientation,
         c.code,
         c.name_ja,
         c.name_en,
         c.rarity,
         c.image_path,
         case when d.orientation = 'upright'
              then c.upright_meaning else c.reversed_meaning end as meaning
    from public.draws d
    join public.cards c on c.id = d.card_id
   where d.user_id = auth.uid()
   order by d.drawn_at desc;

-- ---------------------------------------------------------------------------
-- 8. Row Level Security
-- ---------------------------------------------------------------------------
alter table public.suits        enable row level security;
alter table public.cards        enable row level security;
alter table public.gacha_rates  enable row level security;
alter table public.gacha_config enable row level security;
alter table public.profiles     enable row level security;
alter table public.draws        enable row level security;
alter table public.user_cards   enable row level security;

-- マスタ系は全員読み取り可（書き込みは service_role のみ＝RLSをバイパス）
drop policy if exists "suits are viewable by everyone" on public.suits;
create policy "suits are viewable by everyone"
  on public.suits for select to anon, authenticated using (true);

drop policy if exists "cards are viewable by everyone" on public.cards;
create policy "cards are viewable by everyone"
  on public.cards for select to anon, authenticated using (true);

drop policy if exists "rates are viewable by everyone" on public.gacha_rates;
create policy "rates are viewable by everyone"
  on public.gacha_rates for select to anon, authenticated using (true);

drop policy if exists "config is viewable by everyone" on public.gacha_config;
create policy "config is viewable by everyone"
  on public.gacha_config for select to anon, authenticated using (true);

-- ユーザーデータは本人のみ
drop policy if exists "users can read own profile" on public.profiles;
create policy "users can read own profile"
  on public.profiles for select to authenticated using (auth.uid() = id);

-- gems / pity_counter はRPC経由でのみ更新させたいので、更新可能なのは表示名とアバターのみ
drop policy if exists "users can update own profile" on public.profiles;
create policy "users can update own profile"
  on public.profiles for update to authenticated
  using (auth.uid() = id)
  with check (auth.uid() = id);

drop policy if exists "users can read own draws" on public.draws;
create policy "users can read own draws"
  on public.draws for select to authenticated using (auth.uid() = user_id);

drop policy if exists "users can read own collection" on public.user_cards;
create policy "users can read own collection"
  on public.user_cards for select to authenticated using (auth.uid() = user_id);

-- INSERT / UPDATE / DELETE のポリシーは意図的に作らない。
-- 抽選・コレクション更新は SECURITY DEFINER の RPC からのみ行われる。

-- ---------------------------------------------------------------------------
-- 9. 権限
-- ---------------------------------------------------------------------------
grant usage on schema public to anon, authenticated;

grant select on public.suits, public.cards, public.gacha_rates, public.gacha_config to anon, authenticated;
grant select on public.profiles, public.draws, public.user_cards to authenticated;
grant update (display_name, avatar_url) on public.profiles to authenticated;
grant select on public.v_my_collection, public.v_my_draw_history to authenticated;

-- 内部ヘルパーはクライアントに公開しない
revoke all on function public.pick_card(public.card_rarity)                             from public, anon, authenticated;
revoke all on function public.add_to_collection(uuid, uuid, public.card_orientation)    from public, anon, authenticated;
revoke all on function public.roll_orientation()                                        from public, anon;
revoke all on function public.handle_new_user()                                         from public, anon, authenticated;

-- 公開RPC
revoke all on function public.draw_daily_card()          from public;
revoke all on function public.draw_gacha(int)            from public;
revoke all on function public.get_collection_progress()  from public;

grant execute on function public.draw_daily_card()         to authenticated;
grant execute on function public.draw_gacha(int)           to authenticated;
grant execute on function public.get_collection_progress() to authenticated;

commit;

-- ===========================================================================
--  使い方メモ（supabase-js）
-- ---------------------------------------------------------------------------
--  // デイリーカード
--  const { data, error } = await supabase.rpc('draw_daily_card')
--
--  // 10連ガチャ
--  const { data, error } = await supabase.rpc('draw_gacha', { p_count: 10 })
--
--  // 図鑑
--  const { data } = await supabase.from('v_my_collection').select('*').order('code')
--
--  // 達成率
--  const { data } = await supabase.rpc('get_collection_progress')
--
--  // ジェム付与などの管理操作は service_role キーで直接 UPDATE してください
-- ===========================================================================
