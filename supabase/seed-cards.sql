-- ===========================================================================
--  seed-cards.sql — ライダー・ウェイト版タロット78枚 シードデータ
-- ---------------------------------------------------------------------------
--  前提: 先に supabase/gacha.sql を実行してテーブルを作成しておくこと
--
--  実行方法:
--    A) Supabase Dashboard > SQL Editor に貼り付けて Run
--    B) psql "$DATABASE_URL" -f supabase/seed-cards.sql
--
--  ※ 冪等（再実行すると既存レコードを最新の内容に上書きします）
--  ※ 意味の出典: guidelines/03_tarot-content-guide.md
--
--  レアリティ設計
--    SSR : 大アルカナ 22枚          (排出率  5%)
--    SR  : コートカード 16枚        (排出率 15%)  ページ／ナイト／クイーン／キング
--    R   : エース 4枚               (排出率 20%)
--    N   : 数札 2〜10 の36枚        (排出率 60%)
--
--  コード体系
--    M00〜M21 : 大アルカナ
--    W01〜W14 : ワンド     （1=エース, 11=ページ, 12=ナイト, 13=クイーン, 14=キング）
--    C01〜C14 : カップ
--    S01〜S14 : ソード
--    P01〜P14 : ペンタクル
-- ===========================================================================

begin;

-- ---------------------------------------------------------------------------
-- 1. スートマスタ
-- ---------------------------------------------------------------------------
insert into public.suits (suit, name_ja, name_en, element, theme, sort_order) values
  ('wands',     'ワンド',     'Wands',     'fire',  '情熱・行動・創造・仕事',     1),
  ('cups',      'カップ',     'Cups',      'water', '感情・愛・人間関係・直感',   2),
  ('swords',    'ソード',     'Swords',    'air',   '思考・言葉・葛藤・真実',     3),
  ('pentacles', 'ペンタクル', 'Pentacles', 'earth', '物質・お金・仕事・現実',     4)
on conflict (suit) do update
  set name_ja    = excluded.name_ja,
      name_en    = excluded.name_en,
      element    = excluded.element,
      theme      = excluded.theme,
      sort_order = excluded.sort_order;

-- ---------------------------------------------------------------------------
-- 2. カード78枚
-- ---------------------------------------------------------------------------
insert into public.cards
  (code, arcana, suit, number, name_ja, name_en, rarity, upright_meaning, reversed_meaning, image_path)
values
-- ========================= 大アルカナ 22枚 / SSR =========================
('M00','major',null, 0,'愚者','The Fool','SSR',
 '新しい始まり、自由、冒険','無謀、準備不足、先走り','cards/M00.webp'),
('M01','major',null, 1,'魔術師','The Magician','SSR',
 '意志力、スキル活用、行動','詐欺、未活用の才能、自信過剰','cards/M01.webp'),
('M02','major',null, 2,'女教皇','The High Priestess','SSR',
 '直感、内なる知恵、神秘','秘密、直感の無視、過度な内向き','cards/M02.webp'),
('M03','major',null, 3,'女帝','The Empress','SSR',
 '豊かさ、創造性、母性','依存、過保護、創造性の停滞','cards/M03.webp'),
('M04','major',null, 4,'皇帝','The Emperor','SSR',
 '安定、権威、構造','独裁、硬直、コントロール欲','cards/M04.webp'),
('M05','major',null, 5,'法王','The Hierophant','SSR',
 '伝統、精神的指導、慣習','束縛、反権威、固定観念','cards/M05.webp'),
('M06','major',null, 6,'恋人','The Lovers','SSR',
 '選択、調和、愛','不調和、誘惑、選択の回避','cards/M06.webp'),
('M07','major',null, 7,'戦車','The Chariot','SSR',
 '意志の勝利、前進、自信','暴走、コントロール喪失、停滞','cards/M07.webp'),
('M08','major',null, 8,'力','Strength','SSR',
 '内なる強さ、勇気、忍耐','自信喪失、感情の抑圧、恐れ','cards/M08.webp'),
('M09','major',null, 9,'隠者','The Hermit','SSR',
 '内省、孤独の智慧、探求','孤立、引きこもり、方向性の喪失','cards/M09.webp'),
('M10','major',null,10,'運命の輪','Wheel of Fortune','SSR',
 '転換点、運命の変化、チャンス','悪運、変化への抵抗、惰性','cards/M10.webp'),
('M11','major',null,11,'正義','Justice','SSR',
 '公平、真実、因果応報','不公平、不誠実、回避','cards/M11.webp'),
('M12','major',null,12,'吊るされた男','The Hanged Man','SSR',
 '犠牲、視点の転換、待機','無駄な犠牲、停滞、マルチタスク','cards/M12.webp'),
('M13','major',null,13,'死神','Death','SSR',
 '終わりと始まり、変革、手放し','変化への抵抗、停滞、しがみつき','cards/M13.webp'),
('M14','major',null,14,'節制','Temperance','SSR',
 'バランス、調和、中庸','不均衡、過剰、極端','cards/M14.webp'),
('M15','major',null,15,'悪魔','The Devil','SSR',
 '誘惑、執着、物質的束縛','解放、気づき、執着からの脱出','cards/M15.webp'),
('M16','major',null,16,'塔','The Tower','SSR',
 '突破口、崩壊と再生、覚醒','危機の回避、変化の遅延、軽い変化','cards/M16.webp'),
('M17','major',null,17,'星','The Star','SSR',
 '希望、癒し、ガイダンス','絶望、方向性の喪失、幻想','cards/M17.webp'),
('M18','major',null,18,'月','The Moon','SSR',
 '幻想、潜在意識、恐れ','混乱の解消、明確さ、真実の露呈','cards/M18.webp'),
('M19','major',null,19,'太陽','The Sun','SSR',
 '喜び、成功、明晰さ','一時的な挫折、曇り、過度な楽観','cards/M19.webp'),
('M20','major',null,20,'審判','Judgement','SSR',
 '覚醒、再生、呼びかけ','自己批判、変化への恐れ、否定','cards/M20.webp'),
('M21','major',null,21,'世界','The World','SSR',
 '完成、達成、統合','未完成、閉塞、遅延','cards/M21.webp'),

-- ===================== ワンド（火）14枚 / 情熱・行動 =====================
('W01','minor','wands', 1,'ワンドのエース','Ace of Wands','R',
 '新しい始まり、創造的エネルギー、情熱の火種','遅延、機会の喪失、エネルギー不足','cards/W01.webp'),
('W02','minor','wands', 2,'ワンドの2','Two of Wands','N',
 '計画、将来への視野、次のステップの準備','恐れ、計画の欠如、先延ばし','cards/W02.webp'),
('W03','minor','wands', 3,'ワンドの3','Three of Wands','N',
 '拡大、先見の明、結果の到来','遅延、障害、視野の狭さ','cards/W03.webp'),
('W04','minor','wands', 4,'ワンドの4','Four of Wands','N',
 '収穫、祝福、安定した基盤、達成','不安定、達成の遅れ、基盤の揺らぎ','cards/W04.webp'),
('W05','minor','wands', 5,'ワンドの5','Five of Wands','N',
 '競争、対立、切磋琢磨','回避、和解、内なる葛藤','cards/W05.webp'),
('W06','minor','wands', 6,'ワンドの6','Six of Wands','N',
 '勝利、称賛、目標達成の認識','傲慢、承認欲求、不認識','cards/W06.webp'),
('W07','minor','wands', 7,'ワンドの7','Seven of Wands','N',
 '防衛、立場を守る、粘り強さ','諦め、圧倒される、守りすぎ','cards/W07.webp'),
('W08','minor','wands', 8,'ワンドの8','Eight of Wands','N',
 '素早い行動、スピード、状況の急展開','遅延、フラストレーション、行き違い','cards/W08.webp'),
('W09','minor','wands', 9,'ワンドの9','Nine of Wands','N',
 '粘り強さ、最後の力を振り絞る、警戒','防衛的すぎ、疲弊、意地の張り過ぎ','cards/W09.webp'),
('W10','minor','wands',10,'ワンドの10','Ten of Wands','N',
 '重荷、過剰な責任、頑張りすぎ','放棄、責任転嫁、重荷からの解放','cards/W10.webp'),
('W11','minor','wands',11,'ワンドのページ','Page of Wands','SR',
 '熱意、探求心、新しいアイデア','方向性の欠如、衝動的、未熟','cards/W11.webp'),
('W12','minor','wands',12,'ワンドのナイト','Knight of Wands','SR',
 '行動力、情熱的な前進、挑戦','無謀、方向転換、燃え尽き','cards/W12.webp'),
('W13','minor','wands',13,'ワンドのクイーン','Queen of Wands','SR',
 '自信、活力、カリスマ、独立心','支配的、自己中心、エネルギーの過剰','cards/W13.webp'),
('W14','minor','wands',14,'ワンドのキング','King of Wands','SR',
 'リーダーシップ、ビジョン、起業家精神','独裁的、衝動的、権力の乱用','cards/W14.webp'),

-- ===================== カップ（水）14枚 / 感情・愛 =======================
('C01','minor','cups', 1,'カップのエース','Ace of Cups','R',
 '新しい愛、感情の始まり、感受性の開花','感情の抑圧、愛情の欠乏、直感の無視','cards/C01.webp'),
('C02','minor','cups', 2,'カップの2','Two of Cups','N',
 'パートナーシップ、深いつながり、相互理解','不調和、別れ、コミュニケーション不足','cards/C02.webp'),
('C03','minor','cups', 3,'カップの3','Three of Cups','N',
 'お祝い、友情、感謝、コミュニティ','孤立、過剰な飲食、第三者の介入','cards/C03.webp'),
('C04','minor','cups', 4,'カップの4','Four of Cups','N',
 '内省、退屈、機会を見逃している','新しい視点、機会への気づき、行動開始','cards/C04.webp'),
('C05','minor','cups', 5,'カップの5','Five of Cups','N',
 '悲しみ、後悔、失望','受け入れ、前進、過去からの解放','cards/C05.webp'),
('C06','minor','cups', 6,'カップの6','Six of Cups','N',
 '郷愁、懐かしい思い出、純粋さ','過去への執着、現実逃避、子供っぽさ','cards/C06.webp'),
('C07','minor','cups', 7,'カップの7','Seven of Cups','N',
 '幻想、多すぎる選択肢、夢想','現実逃避からの脱出、明確な選択、幻滅','cards/C07.webp'),
('C08','minor','cups', 8,'カップの8','Eight of Cups','N',
 '手放し、より深いものを求める旅立ち','逃避、停滞、過去への執着','cards/C08.webp'),
('C09','minor','cups', 9,'カップの9','Nine of Cups','N',
 '願いの成就、感情的満足、幸運','不満、過剰な贅沢、表面的な幸せ','cards/C09.webp'),
('C10','minor','cups',10,'カップの10','Ten of Cups','N',
 '究極の幸福、家族の調和、長期的な充実','家族の不和、崩壊、理想と現実のギャップ','cards/C10.webp'),
('C11','minor','cups',11,'カップのページ','Page of Cups','SR',
 '感受性の高さ、直感的なメッセージ、夢想家','感情的不安定、幼稚な嫉妬、自己陶酔','cards/C11.webp'),
('C12','minor','cups',12,'カップのナイト','Knight of Cups','SR',
 'ロマンス、理想主義、感情的な旅','気まぐれ、感情的操作、幻想的な愛','cards/C12.webp'),
('C13','minor','cups',13,'カップのクイーン','Queen of Cups','SR',
 '共感力、直感力、感情的成熟','感情依存、気分屋、境界線の欠如','cards/C13.webp'),
('C14','minor','cups',14,'カップのキング','King of Cups','SR',
 '感情のコントロール、共感と知性のバランス','感情的操作、抑圧、気分の波が激しい','cards/C14.webp'),

-- ===================== ソード（風）14枚 / 思考・真実 =====================
('S01','minor','swords', 1,'ソードのエース','Ace of Swords','R',
 '明晰さ、真実、新しい考え方、突破口','混乱、誤解、思考の停滞','cards/S01.webp'),
('S02','minor','swords', 2,'ソードの2','Two of Swords','N',
 '均衡、葛藤の回避、決断できない状態','決断、行き詰まりの解消、情報が増える','cards/S02.webp'),
('S03','minor','swords', 3,'ソードの3','Three of Swords','N',
 '悲しみ、心の痛み、裏切り','癒し、回復、悲しみの受け入れ','cards/S03.webp'),
('S04','minor','swords', 4,'ソードの4','Four of Swords','N',
 '休息、回復、内省の時間','停滞、回復への抵抗、再始動','cards/S04.webp'),
('S05','minor','swords', 5,'ソードの5','Five of Swords','N',
 '対立、敗北、空虚な勝利','和解、対立の終わり、過去の手放し','cards/S05.webp'),
('S06','minor','swords', 6,'ソードの6','Six of Swords','N',
 '移行、前進、嵐の後の平和','逃避、問題の先送り、移行の困難','cards/S06.webp'),
('S07','minor','swords', 7,'ソードの7','Seven of Swords','N',
 '策略、回避、ずる賢さ','正直さへの回帰、戦略の見直し、告白','cards/S07.webp'),
('S08','minor','swords', 8,'ソードの8','Eight of Swords','N',
 '束縛、制限、自分で作った檻','自由、解放、制限からの脱出','cards/S08.webp'),
('S09','minor','swords', 9,'ソードの9','Nine of Swords','N',
 '不安、悪夢、過度な心配','苦しみの終わり、最悪期の通過、回復','cards/S09.webp'),
('S10','minor','swords',10,'ソードの10','Ten of Swords','N',
 '終わり、裏切り、強制的な終焉','回復、再生、最悪からの回復','cards/S10.webp'),
('S11','minor','swords',11,'ソードのページ','Page of Swords','SR',
 '鋭い知性、好奇心、観察力','情報の悪用、噂話、批判的すぎ','cards/S11.webp'),
('S12','minor','swords',12,'ソードのナイト','Knight of Swords','SR',
 'スピード、行動力、直接的なコミュニケーション','衝動的、攻撃的、一方通行の主張','cards/S12.webp'),
('S13','minor','swords',13,'ソードのクイーン','Queen of Swords','SR',
 '独立心、知的明晰さ、率直さ','冷淡、批判的、感情の切り捨て','cards/S13.webp'),
('S14','minor','swords',14,'ソードのキング','King of Swords','SR',
 '理性、倫理的権威、公正な判断','操作的、冷酷、権力の乱用','cards/S14.webp'),

-- =================== ペンタクル（地）14枚 / 物質・現実 ===================
('P01','minor','pentacles', 1,'ペンタクルのエース','Ace of Pentacles','R',
 '新しい物質的機会、収入の始まり、豊かさの種','機会の喪失、物質的な停滞、計画倒れ','cards/P01.webp'),
('P02','minor','pentacles', 2,'ペンタクルの2','Two of Pentacles','N',
 'バランス、柔軟な対応、複数のことの管理','不均衡、キャパオーバー、優先順位の乱れ','cards/P02.webp'),
('P03','minor','pentacles', 3,'ペンタクルの3','Three of Pentacles','N',
 '共同作業、職人技、計画の具現化','孤立した作業、手抜き、評価されない努力','cards/P03.webp'),
('P04','minor','pentacles', 4,'ペンタクルの4','Four of Pentacles','N',
 '安定、守りの姿勢、財産の保持','守銭奴的思考からの解放、手放し、寛大さ','cards/P04.webp'),
('P05','minor','pentacles', 5,'ペンタクルの5','Five of Pentacles','N',
 '貧困感、物質的苦難、孤立','回復、支援の受け入れ、苦難の終わり','cards/P05.webp'),
('P06','minor','pentacles', 6,'ペンタクルの6','Six of Pentacles','N',
 '寛大さ、分かち合い、公平な与え与えられ','不平等、貸し借りの不均衡、見返り期待','cards/P06.webp'),
('P07','minor','pentacles', 7,'ペンタクルの7','Seven of Pentacles','N',
 '忍耐、長期的な投資、結果待ち','成果なし、焦り、見直しが必要','cards/P07.webp'),
('P08','minor','pentacles', 8,'ペンタクルの8','Eight of Pentacles','N',
 '職人精神、スキルの向上、地道な努力','完璧主義、単純作業への埋没、怠慢','cards/P08.webp'),
('P09','minor','pentacles', 9,'ペンタクルの9','Nine of Pentacles','N',
 '豊かさ、自立、努力の結果の享受','物質への過度な執着、自己評価の低さ','cards/P09.webp'),
('P10','minor','pentacles',10,'ペンタクルの10','Ten of Pentacles','N',
 '長期的な豊かさ、遺産、家族の安定','家族の問題、財産問題、価値観の相違','cards/P10.webp'),
('P11','minor','pentacles',11,'ペンタクルのページ','Page of Pentacles','SR',
 '学習意欲、新しいスキルの習得、機会の発見','怠慢、機会を無駄にする、非現実的な夢','cards/P11.webp'),
('P12','minor','pentacles',12,'ペンタクルのナイト','Knight of Pentacles','SR',
 '勤勉さ、安定、着実な前進、信頼性','停滞、頑固さ、変化への抵抗','cards/P12.webp'),
('P13','minor','pentacles',13,'ペンタクルのクイーン','Queen of Pentacles','SR',
 '豊かさ、実用的な知恵、母性的な繁栄','物質主義、過保護、自己不信','cards/P13.webp'),
('P14','minor','pentacles',14,'ペンタクルのキング','King of Pentacles','SR',
 '繁栄、ビジネス成功、財政的安定','強欲、物質偏重、支配欲','cards/P14.webp')

on conflict (code) do update
  set arcana           = excluded.arcana,
      suit             = excluded.suit,
      number           = excluded.number,
      name_ja          = excluded.name_ja,
      name_en          = excluded.name_en,
      rarity           = excluded.rarity,
      upright_meaning  = excluded.upright_meaning,
      reversed_meaning = excluded.reversed_meaning,
      image_path       = excluded.image_path;

-- ---------------------------------------------------------------------------
-- 3. キーワード配列を意味文から自動生成
--    「新しい始まり、自由、冒険」→ {新しい始まり, 自由, 冒険}
-- ---------------------------------------------------------------------------
update public.cards
   set upright_keywords  = string_to_array(upright_meaning,  '、'),
       reversed_keywords = string_to_array(reversed_meaning, '、');

commit;

-- ===========================================================================
--  投入結果の確認
-- ===========================================================================

-- 合計78枚になっているか
select count(*) as total_cards from public.cards;

-- レアリティ別の枚数と排出率
select c.rarity,
       count(*)                                                              as cards,
       r.weight                                                              as weight,
       round(100.0 * r.weight / sum(r.weight) over (), 2)                     as pull_rate_pct,
       round(100.0 * r.weight / sum(r.weight) over () / count(*), 3)          as per_card_pct
  from public.cards c
  join public.gacha_rates r on r.rarity = c.rarity
 group by c.rarity, r.weight
 order by c.rarity desc;

-- スート別の枚数（大アルカナ22 / 各スート14）
select coalesce(s.name_ja, '大アルカナ') as group_name,
       count(*)                          as cards
  from public.cards c
  left join public.suits s on s.suit = c.suit
 group by coalesce(s.name_ja, '大アルカナ'), s.sort_order
 order by s.sort_order nulls first;
