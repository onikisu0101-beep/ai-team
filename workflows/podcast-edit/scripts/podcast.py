#!/usr/bin/env python3
"""ポッドキャスト自動編集パイプライン

使い方:
  podcast.py list                 未処理の録音を一覧（ドライブ「01.Podcast音声アップロード」）
  podcast.py prepare <file_id>    ダウンロード → 文字起こし → カット候補（cuts_draft.json）
  podcast.py apply-edit <file_id> edit.txt（消す部分を {種類:…} で囲んだ台本）→ cuts.json と kept.txt を作る
  podcast.py render <file_id>     cuts.json に従って編集 → 仕上げ → 再文字起こしで確認
  podcast.py publish <file_id>    編集済み音声（MP3）を「02.Podcast編集済み」へ保存し、元ファイルを「処理済み」へ移動

prepare が出す script.txt（読める台本）を Claude が edit.txt にコピーし、消す部分を {種類:…} で囲む。
apply-edit がそれを秒数のカット（cuts.json）に変換する（手順と基準は .claude/commands/podcast.md）。
"""
import base64, collections, glob, json, os, re, subprocess, sys, wave
from pathlib import Path

MODEL_DIR = Path(os.environ.get("PODCAST_MODEL_DIR", Path.home() / ".cache" / "podcast-edit"))
WORK_DIR = Path(os.environ.get("PODCAST_WORK_DIR", "/tmp/podcast-work"))
GAS_URL = os.environ.get("PODCAST_GAS_URL")
GAS_TOKEN = os.environ.get("PODCAST_GAS_TOKEN")
SR = 48000

# ---- 編集の標準設定（v2: 2026-09-27 ユーザー確認済み） ----
MIN_CUT = 0.30      # これより短いフィラーは詰めずに同じ長さの無音へ置き換える（「ま」1文字は触らない）
PAUSE_SHORT = 0.22  # フィラーを切った跡に入れる間
PAUSE_LONG = 0.50   # 長い沈黙・1秒以上のカットの跡に入れる間
LONG_GAP = 0.80     # これより長い沈黙は PAUSE_LONG まで詰める
MASTER = "highpass=f=80,afftdn=nf=-25,acompressor=threshold=-20dB:ratio=2.5:attack=10:release=150"
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
FILLERS = sorted(["えっと", "えーと", "えと", "あのー", "あの", "まあ", "えー", "あー", "うーん", "うん", "え", "はい"], key=len, reverse=True)


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def work(fid):
    d = WORK_DIR / fid
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------- ドライブ（Google Apps Script 経由） ----------------
def gas(action, **payload):
    import requests
    if not GAS_URL or not GAS_TOKEN:
        sys.exit("PODCAST_GAS_URL / PODCAST_GAS_TOKEN が未設定です（README のセットアップ手順を参照）")
    r = requests.post(GAS_URL, json={"token": GAS_TOKEN, "action": action, **payload}, timeout=600)
    r.raise_for_status()
    res = r.json()
    if not res.get("ok"):
        sys.exit(f"Apps Script エラー: {res.get('error')}")
    return res


def cmd_list():
    files = gas("list")["files"]
    print(json.dumps(files, ensure_ascii=False, indent=1))


# ---------------- 文字起こし ----------------
def recognizer():
    import sherpa_onnx
    d = glob.glob(str(MODEL_DIR / "sherpa-onnx-sense-voice-*"))[0]
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=d + "/model.int8.onnx", tokens=d + "/tokens.txt", language="ja", use_itn=False, num_threads=4)


def transcribe(wav16k):
    import sherpa_onnx
    import numpy as np
    rec = recognizer()
    w = wave.open(str(wav16k))
    sr = w.getframerate()
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(MODEL_DIR / "silero_vad.onnx")
    cfg.silero_vad.min_silence_duration = 0.25
    cfg.silero_vad.min_speech_duration = 0.1
    cfg.silero_vad.max_speech_duration = 20
    cfg.sample_rate = sr
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=len(a) / sr + 10)
    segs = []

    def drain():
        while not vad.empty():
            segs.append((vad.front.start, np.array(vad.front.samples)))
            vad.pop()
    ws = cfg.silero_vad.window_size
    for i in range(0, len(a), ws):
        vad.accept_waveform(a[i:i + ws])
        drain()
    vad.flush()
    drain()
    out = []
    for st, smp in segs:
        s = rec.create_stream()
        s.accept_waveform(sr, smp)
        rec.decode_stream(s)
        t0 = st / sr
        out.append({"start": round(t0, 2), "end": round(t0 + len(smp) / sr, 2), "text": s.result.text,
                    "tokens": list(s.result.tokens), "ts": [round(t0 + x, 2) for x in s.result.timestamps]})
    fill_gaps(out, a, sr, rec)
    return out


def fill_gaps(tr, a, sr, rec, min_gap=0.4):
    """間に埋もれて1回目の認識で落ちた短い発声（「あの」「うん」「はい」など）を拾い直す。
    min_gap 秒以上空いた文字と文字のあいだを個別に認識し、聞こえた語を区間に差し込む。"""
    added = 0
    for seg in tr:
        toks, ts = seg["tokens"], seg["ts"]
        bounds = [seg["start"]] + ts + [seg["end"]]
        new_toks, new_ts, new_added = [], [], []
        for i in range(len(bounds) - 1):
            if i > 0:
                new_toks.append(toks[i - 1])
                new_ts.append(ts[i - 1])
                new_added.append(False)
            lo = bounds[i] + (0.25 if i > 0 else 0.0)  # 前の文字の発音（余韻）ぶんを空ける
            hi = bounds[i + 1] - 0.03
            if bounds[i + 1] - bounds[i] < min_gap or hi - lo < 0.15:
                continue
            st = rec.create_stream()
            st.accept_waveform(sr, a[int(lo * sr):int(hi * sr)])
            rec.decode_stream(st)
            txt = st.result.text.strip()
            if not txt or len(txt) > 6:
                continue  # 何も聞こえない／長すぎる（誤認識の可能性）ものは捨てる
            if i > 0 and txt == toks[i - 1]:
                continue  # 直前の文字の余韻を拾っただけ
            for t, x in zip(st.result.tokens, st.result.timestamps):
                new_toks.append(t)
                new_ts.append(round(lo + x, 2))
                new_added.append(True)
            added += 1
        seg["tokens"], seg["ts"], seg["added"] = new_toks, new_ts, new_added
        seg["text"] = "".join(new_toks)
    return added


def to16k(src, dst):
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)], check=True)


# ---------------- カット候補 ----------------
def suggest_cuts(tr, dur):
    """機械的に見つかるカット候補（フィラー・長い沈黙）。言い直し・繰り返し・脱線は Claude が追加する。"""
    cuts = []
    if tr and tr[0]["start"] > 0.3:
        cuts.append([0.0, round(tr[0]["start"] - 0.25, 2), "無音", "冒頭の無音", "頭出しを詰める"])
    for k, seg in enumerate(tr):
        toks, ts = seg["tokens"], seg["ts"]
        gap_before = lambda i: 9 if i == 0 else ts[i] - ts[i - 1]
        gap_after = lambda i: 9 if i >= len(ts) else ts[i] - ts[i - 1]
        i = 0
        while i < len(toks):
            hit = None
            for f in FILLERS:  # 長いものから順に照合
                n = len(f)
                if "".join(toks[i:i + n]) != f:
                    continue
                # 単語の一部（「伝える」の「え」など）を拾わないよう、前後に間があるものだけ
                need = 0.25 if n == 1 else 0.15
                if gap_before(i) >= need or gap_after(i + n) >= need:
                    hit = (f, n)
                    break
            if not hit:
                i += 1
                continue
            f, n = hit
            s = round(ts[i] - 0.03, 2) if i else round(max(0, seg["start"] - 0.05), 2)
            e = round(ts[i + n] - 0.03, 2) if i + n < len(ts) else round(seg["end"] + 0.05, 2)
            note = "要確認: 指示語・相づちの可能性" if f in ("あの", "はい", "うん") else ""
            cuts.append([s, e, "フィラー", f, note])
            i += n
        # 文の途中の長い沈黙
        for j in range(1, len(ts)):
            if ts[j] - ts[j - 1] > LONG_GAP + 0.2:
                cuts.append([round(ts[j - 1] + 0.2, 2), round(ts[j] - 0.1, 2), "無音", "長い間", ""])
        # 区間どうしの長い沈黙
        if k + 1 < len(tr) and tr[k + 1]["start"] - seg["end"] > LONG_GAP:
            cuts.append([round(seg["end"] + 0.1, 2), round(tr[k + 1]["start"] - 0.1, 2), "無音", "長い間", ""])
    if tr and dur - tr[-1]["end"] > 0.8:
        cuts.append([round(tr[-1]["end"] + 0.4, 2), 9999, "無音", "末尾の無音・雑音", ""])
    return sorted(cuts)


def find_repeats(tr, window=2.5, min_len=3):
    """直後に同じ言い回しをもう一度言っている箇所（言い直しの候補）を探す。
    離れた場所での繰り返し（同じ話を2回する）は表記ゆれで一致しにくいので、Claude が文脈で探す。"""
    chars = [(t, s) for seg in tr for t, s in zip(seg["tokens"], seg["ts"])]
    text = "".join(t for t, _ in chars)
    # 文字位置 → 開始秒（トークンは1文字とは限らないので展開する）
    pos = []
    for t, s in chars:
        pos += [s] * len(t)
    found = []
    i = 0
    while i < len(text):
        best = None
        for L in range(20, min_len - 1, -1):
            if i + L > len(text):
                continue
            frag = text[i:i + L]
            if set(frag) <= set("えあのまうんはいねこっとー") or len(set(frag)) < 3:
                continue  # フィラーだけ・同じ文字の連続は除外
            j = text.find(frag, i + L)
            # 1回目の言い終わりから2回目の言い始めまでが window 秒以内
            if j != -1 and pos[j] - pos[i + L - 1] <= window:
                best = (L, j)
                break
        if best:
            L, j = best
            found.append({"first": round(pos[i], 2), "second": round(pos[j], 2), "text": text[i:i + L],
                          "context": text[max(0, i - 5):j + L + 5]})
            i += L
        else:
            i += 1
    return found


RETAKE_CUES = ["もう一回", "もう1回", "言い直", "撮り直", "ごめん", "間違えた", "じゃなくて"]
FILLER_RE = re.compile("えっと|えーと|えと|あのー|まあ|えー|あー|うーん")


def _bigrams(t):
    return {t[k:k + 2] for k in range(len(t) - 1)}


def find_retakes(tr, window=60.0, threshold=0.45, max_join=3, min_chars=6):
    """同じ内容を（言葉を変えて）もう一度話している「撮り直し」の候補を探す。
    連続する1〜max_join 個の VAD 区間をひとかたまりにし、window 秒以内にある後のかたまりと
    文字2-gram の Dice 係数で比べる。既定では前のテイクを消す想定で、判断は Claude が行う。"""
    chunks = []
    for i in range(len(tr)):
        for k in range(1, max_join + 1):
            if i + k > len(tr):
                break
            seg = tr[i:i + k]
            raw = "".join(o["text"] for o in seg)
            clean = FILLER_RE.sub("", raw)
            if len(clean) >= min_chars:
                chunks.append({"i": i, "j": i + k - 1, "start": seg[0]["start"], "end": seg[-1]["end"],
                               "text": raw, "bg": _bigrams(clean)})
    pairs = []
    for a in chunks:
        for b in chunks:
            if b["i"] <= a["j"] or b["start"] - a["start"] > window:
                continue
            inter = len(a["bg"] & b["bg"])
            score = 2 * inter / (len(a["bg"]) + len(b["bg"]))
            if score >= threshold:
                pairs.append((score, a, b))
    found, used = [], []
    for score, a, b in sorted(pairs, key=lambda x: -x[0]):
        spans = [(a["start"], a["end"]), (b["start"], b["end"])]
        if any(s < ue and us < e for s, e in spans for us, ue in used):
            continue  # すでに採用した候補と時間が重なるものは捨てる
        used += spans
        cues = [{"time": o["start"], "text": o["text"]} for o in tr
                if a["start"] <= o["start"] < b["start"] and any(c in o["text"] for c in RETAKE_CUES)]
        found.append({"take1": [a["start"], a["end"]], "take2": [b["start"], b["end"]], "score": round(score, 2),
                      "text1": a["text"], "text2": b["text"], "cues": cues})
    return sorted(found, key=lambda x: x["take1"][0])


def find_retake_cues(tr):
    """「もう一回」「ごめん」など撮り直しの合図になる言葉を含む区間。"""
    return [{"time": o["start"], "text": o["text"]} for o in tr if any(c in o["text"] for c in RETAKE_CUES)]

# ---------------- テキスト編集方式（台本 script.txt ⇄ edit.txt） ----------------
LINE_GAP = 0.45      # 行（フレーズ）を区切る間
NOTE_RE = re.compile(r"〈[^〉]*〉|／[^／]*／")
EDIT_RE = re.compile(r"\{(?:(言い直し|撮り直し|言い間違い|フィラー|吃音|脱線|繰り返し|ノイズ|その他)[:：])?(.*?)\}", re.S)
EDIT_KINDS = {"言い間違い": "言い直し", "繰り返し": "撮り直し", None: "その他"}


def flat_chars(tr):
    """全区間の文字を1列に並べ、各文字の開始・終了秒と所属区間を持たせる。"""
    chars = []
    for k, seg in enumerate(tr):
        toks, ts = seg["tokens"], seg["ts"]
        for i, (t, st) in enumerate(zip(toks, ts)):
            en = ts[i + 1] if i + 1 < len(ts) else seg["end"]
            gapfill = seg.get("added", [False] * len(toks))[i]
            for c in t:
                chars.append({"c": c, "start": st, "end": en, "seg": k, "gap": gapfill})
    return chars


def build_lines(chars):
    """間（LINE_GAP 秒以上）か区間の切れ目で区切った行。各行は chars の [a, b) 範囲。"""
    lines, a = [], 0
    for i in range(1, len(chars) + 1):
        if i == len(chars) or chars[i]["start"] - chars[i - 1]["start"] >= LINE_GAP + 0.12 \
                or chars[i]["gap"] != chars[i - 1]["gap"] \
                or (chars[i]["seg"] != chars[i - 1]["seg"] and chars[i]["start"] - chars[i - 1]["end"] >= LINE_GAP):
            lines.append((a, i))
            a = i
    return lines


def char_notes(tr, chars):
    """表示用の注記: フィラー候補の長さ〈0.72s〉、吃音の疑い〈吃音?〉。文字位置 → 注記。"""
    notes = {}
    # suggest_cuts と同じ基準でフィラーを探す（時刻 → 文字位置に対応づける）
    for s, e, ty, content, _ in suggest_cuts(tr, 0):
        if ty != "フィラー":
            continue
        for i, ch in enumerate(chars):
            if abs(ch["start"] - (s + 0.03)) < 0.011 or (abs(ch["start"] - s) < 0.06 and ch["c"] == content[0]):
                j = i + len(content) - 1
                if j < len(chars) and "".join(c["c"] for c in chars[i:j + 1]) == content:
                    dur = chars[j]["end"] - chars[i]["start"]
                    notes[j] = f"〈{dur:.2f}s〉"
                break
    # 吃音: 同じ1〜2文字が間をあけて続けて出る（「そ、そ、それ」）
    for i in range(len(chars) - 1):
        for L in (1, 2):
            if i + 2 * L > len(chars):
                continue
            a = "".join(c["c"] for c in chars[i:i + L])
            b = "".join(c["c"] for c in chars[i + L:i + 2 * L])
            # 1回目の最後の文字から2回目の頭までが、普通の話す速さ（1文字0.06〜0.12秒）より空いている
            gap = chars[i + L]["start"] - chars[i + L - 1]["start"]
            if a == b and gap >= 0.25 and not re.search(r"[0-9０-９A-Za-z]", a) and not FILLER_RE.fullmatch(a):
                notes[i + 2 * L - 1] = notes.get(i + 2 * L - 1, "") + "〈吃音?〉"
    return notes


def fmt_t(x):
    return f"{int(x // 60):02d}:{x % 60:04.1f}"


def write_script(d, tr):
    chars = flat_chars(tr)
    lines = build_lines(chars)
    notes = char_notes(tr, chars)
    out = []
    for n, (a, b) in enumerate(lines):
        gap = chars[a]["start"] - chars[a - 1]["end"] if a else chars[a]["start"]
        body = "".join(chars[i]["c"] + notes.get(i, "") for i in range(a, b))
        pause = f"／{gap:.1f}s／" if gap >= LONG_GAP else ""
        tag = "〈間の声〉" if all(chars[i]["gap"] for i in range(a, b)) else ""
        out.append(f"L{n:03d} [{fmt_t(chars[a]['start'])}] {pause}{body}{tag}")
    # 機械の候補を行番号で示す
    def line_of(t):
        for n, (a, b) in enumerate(lines):
            if chars[a]["start"] - 0.05 <= t < chars[b - 1]["end"] + 0.05:
                return f"L{n:03d}"
        return "?"
    cand = [f"- 言い直し候補 {line_of(r['first'])}「{r['text']}」…{r['context']}…" for r in find_repeats(tr)]
    cand += [f"- 撮り直し候補 {line_of(r['take1'][0])}〜{line_of(r['take2'][0])}（類似度 {r['score']}）" for r in find_retakes(tr)]
    cand += [f"- 撮り直しの合図 {line_of(c['time'])}「{c['text']}」" for c in find_retake_cues(tr)]
    text = "\n".join(out)
    (d / "script.txt").write_text(text + "\n")
    (d / "lines.json").write_text(json.dumps(lines))
    return text, cand


def parse_edit(d):
    """edit.txt → 削除する文字範囲のリスト [(a, b, 種類, 文字列)] と、残る行の文章。"""
    tr = json.loads((d / "transcript.json").read_text())
    chars = flat_chars(tr)
    lines = [tuple(x) for x in json.loads((d / "lines.json").read_text())]
    raw = (d / "edit.txt").read_text()
    # 行頭の「Lnnn [mm:ss.s] 」を外し、行ごとに文字位置の起点を覚える
    body, starts = [], []
    for ln in raw.splitlines():
        m = re.match(r"\s*L(\d{3})\s*\[[^\]]*\]\s?(.*)$", ln)
        if not m:
            if ln.strip():
                body.append(ln)  # 括弧が行をまたいだ続き（行番号なし）はそのまま連結
            continue
        n = int(m.group(1))
        starts.append((len("".join(body)), n))
        body.append(m.group(2))
    text = "".join(body)
    # 注記を外す（括弧の位置は保つ）
    text = NOTE_RE.sub("", text)
    deletions, plain, pos = [], [], 0
    for m in EDIT_RE.finditer(text):
        plain.append(text[pos:m.start()])
        a = len("".join(plain))
        plain.append(m.group(2))
        b = len("".join(plain))
        kind = EDIT_KINDS.get(m.group(1), m.group(1))
        deletions.append((a, b, kind, m.group(2)))
        pos = m.end()
    plain.append(text[pos:])
    plain = "".join(plain)
    if "{" in plain or "}" in plain:
        sys.exit("edit.txt の { } が閉じていません")
    orig = "".join(c["c"] for c in chars)
    if plain != orig:
        k = next((i for i, (x, y) in enumerate(zip(plain, orig)) if x != y), min(len(plain), len(orig)))
        ln = next(f"L{n:03d}" for n, (a, b) in enumerate(lines) if a <= min(k, len(chars) - 1) < b)
        sys.exit(f"edit.txt の本文が台本と一致しません（{ln} 付近: 台本「{orig[k:k + 15]}」/ edit「{plain[k:k + 15]}」）。"
                 "文字は書き換えず、消す部分を {種類:…} で囲むだけにしてください")
    return tr, chars, lines, deletions


def cmd_apply_edit(fid):
    d = work(fid)
    tr, chars, lines, deletions = parse_edit(d)
    cuts = []
    for a, b, kind, txt in deletions:
        if a >= b:
            continue
        first, last = chars[a], chars[b - 1]
        s = first["start"] - 0.03 if a and chars[a - 1]["seg"] == first["seg"] else max(0, tr[first["seg"]]["start"] - 0.05)
        e = chars[b]["start"] - 0.03 if b < len(chars) and chars[b]["seg"] == last["seg"] else tr[last["seg"]]["end"] + 0.05
        cuts.append([round(s, 2), round(e, 2), kind, txt[:40], ""])
    # 長い沈黙・冒頭末尾の無音は機械的に詰める
    dur = wave.open(str(d / "in16k.wav")).getnframes() / 16000
    cuts += [c for c in suggest_cuts(tr, dur) if c[2] == "無音"]
    cuts.sort()
    (d / "cuts.json").write_text(json.dumps(cuts, ensure_ascii=False, indent=0))
    deleted = set(i for a, b, *_ in deletions for i in range(a, b))
    kept = []
    for n, (a, b) in enumerate(lines):
        t = "".join(chars[i]["c"] for i in range(a, b) if i not in deleted)
        if t:
            kept.append(f"L{n:03d} {t}")
    (d / "kept.txt").write_text("\n".join(kept) + "\n")
    cnt = collections.Counter(kind for _a, _b, kind, _t in deletions)
    total = sum(chars[b - 1]["end"] - chars[a]["start"] for a, b, *_ in deletions if a < b)
    print(f"カット {len(deletions)} 箇所（約 {total:.0f} 秒）: " + " / ".join(f"{k} {v}" for k, v in cnt.items()))
    print("# 編集後に残る文章（聞き手として通しで読み、重複・途切れ・意味の通らない所がないか確認する）:")
    print("\n".join(kept))


def cmd_prepare(fid):
    d = work(fid)
    src = d / "input.audio"
    if not src.exists():
        res = gas("download", id=fid)
        src.write_bytes(base64.b64decode(res["base64"]))
        (d / "meta.json").write_text(json.dumps({"id": fid, "name": res["name"]}, ensure_ascii=False))
    to16k(src, d / "in16k.wav")
    tr = transcribe(d / "in16k.wav")
    (d / "transcript.json").write_text(json.dumps(tr, ensure_ascii=False))
    dur = wave.open(str(d / "in16k.wav")).getnframes() / 16000
    draft = suggest_cuts(tr, dur)
    (d / "cuts_draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=0))
    reps = find_repeats(tr)
    (d / "repeats.json").write_text(json.dumps(reps, ensure_ascii=False, indent=1))
    retakes = find_retakes(tr)
    cues = find_retake_cues(tr)
    (d / "retakes.json").write_text(json.dumps({"retakes": retakes, "cues": cues}, ensure_ascii=False, indent=1))
    print(f"# {json.loads((d / 'meta.json').read_text())['name']}  長さ {dur:.1f}s  区間 {len(tr)}  カット候補 {len(draft)}")
    script, cand = write_script(d, tr)
    print(f"# 作業フォルダ: {d}")
    print("# 台本 script.txt（行番号 [開始時刻] 本文。〈0.72s〉はフィラー候補の長さ、〈吃音?〉は吃音の疑い、／1.2s／は直前の間）:")
    print(script)
    print("\n# 機械が見つけた候補（判断は文脈で。候補にないものも全文を読んで探す）:")
    print("\n".join(cand) if cand else "- なし")


# ---------------- 編集・仕上げ ----------------
def cmd_render(fid):
    import numpy as np
    d = work(fid)
    CUTS = [tuple(c) for c in json.loads((d / "cuts.json").read_text())]
    FF = ffmpeg()
    subprocess.run([FF, "-v", "error", "-y", "-i", str(d / "input.audio"), "-ac", "1", "-ar", str(SR), "-f", "f32le", str(d / "raw.f32")], check=True)
    a = np.fromfile(d / "raw.f32", dtype=np.float32)
    dur = len(a) / SR
    cuts = [c for c in sorted(CUTS) if not (c[2] == "フィラー" and c[1] - c[0] < MIN_CUT and c[3] == "ま")]
    keep, t = [], 0.0
    for s, e, ty, *_ in cuts:
        p = PAUSE_LONG if (ty == "無音" or e - s >= 1.0) else (e - s if e - s < MIN_CUT else PAUSE_SHORT)
        if s > t:
            keep.append([t, s, p])
        elif keep and (ty == "無音" or e - s >= 1.0):
            keep[-1][2] = PAUSE_LONG
        t = max(t, e)
    if t < dur:
        keep.append([t, dur, 0])
    keep[-1][2] = 0
    F = int(0.02 * SR)
    parts = []
    for s, e, p in keep:
        seg = a[int(s * SR):int(e * SR)].copy()
        if len(seg) > 2 * F:
            if s > 0:
                seg[:F] *= np.linspace(0, 1, F, dtype=np.float32)
            if p:
                seg[-F:] *= np.linspace(1, 0, F, dtype=np.float32)
        parts.append(seg)
        if p:
            parts.append(np.zeros(int(p * SR), dtype=np.float32))
    out = np.concatenate(parts)
    fo = int(0.3 * SR)
    out[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)
    out.tofile(d / "cut.f32")
    raw = ["-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", str(d / "cut.f32")]
    m = subprocess.run([FF, "-hide_banner", *raw, "-af", f"{MASTER},{LOUDNORM}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    j = json.loads(m[m.rindex("{"):m.rindex("}") + 1])
    ln = (f"{LOUDNORM}:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}"
          f":measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    subprocess.run([FF, "-v", "error", "-y", *raw, "-af", f"{MASTER},{ln},aresample={SR}", "-c:a", "libmp3lame", "-b:a", "128k", str(d / "edited.mp3")], check=True)
    for f in ("raw.f32", "cut.f32"):
        (d / f).unlink()
    (d / "cuts_applied.json").write_text(json.dumps(
        [dict(start=s, end=min(e, dur), type=ty, content=c, reason=r) for s, e, ty, c, r in cuts], ensure_ascii=False, indent=1))
    # 確認用: 編集後をもう一度文字起こし
    to16k(d / "edited.mp3", d / "verify16k.wav")
    text = "".join(o["text"] for o in transcribe(d / "verify16k.wav"))
    (d / "verify.txt").write_text(text)
    print(f"元 {dur:.1f}s → 編集後 {len(out) / SR:.1f}s  カット {len(cuts)} 箇所（「ま」見送り {len(CUTS) - len(cuts)}）")
    print("# 編集後の文字起こし（言葉の欠け・残ったフィラーを確認）:\n" + text)


def cutlog(d, name, dur_in, dur_out):
    c = json.loads((d / "cuts_applied.json").read_text())
    tr = json.loads((d / "transcript.json").read_text())
    f = lambda x: f"{int(x // 60)}:{x % 60:05.2f}"
    cnt = collections.Counter(x["type"] for x in c)
    L = [f"# カットログ：{name}", "", f"- 元の長さ: {f(dur_in)} → 編集後: {f(dur_out)}（{len(c)}箇所）",
         "- 内訳: " + " / ".join(f"{k} {v}" for k, v in cnt.items()),
         "- 仕上げ: 80Hz以下カット、ノイズ除去、軽いコンプレッサー、ラウドネス -16 LUFS", "",
         "## カット一覧（元音声の時刻）", "", "| 時刻 | 種類 | 内容 | 理由 |", "|---|---|---|---|"]
    for x in c:
        why = "無音に置き換え（テンポはそのまま）" if x["type"] == "フィラー" and x["end"] - x["start"] < MIN_CUT else x["reason"]
        L.append(f"| {f(x['start'])}–{f(x['end'])} | {x['type']} | {x['content']} | {why} |")
    L += ["", "## 文字起こし（元音声、自動認識のため誤字あり）", ""] + [f"- [{f(o['start'])}] {o['text']}" for o in tr if o["text"]]
    return "\n".join(L)


def duration(p):
    out = subprocess.run([ffmpeg(), "-i", str(p)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def cmd_publish(fid):
    d = work(fid)
    name = json.loads((d / "meta.json").read_text())["name"]
    stem = re.sub(r"\.[^.]+$", "", name)
    # カットログはドライブには置かず、作業フォルダに残して報告に使う
    (d / "cutlog.md").write_text(cutlog(d, name, duration(d / "input.audio"), duration(d / "edited.mp3")))
    res = gas("publish", sourceId=fid, files=[
        {"name": f"{stem}_編集済み.mp3", "mimeType": "audio/mpeg", "base64": base64.b64encode((d / "edited.mp3").read_bytes()).decode()},
    ])
    print(f"保存しました: {stem}_編集済み.mp3（元ファイルは「処理済み」へ移動） {res.get('saved')}")
    print(f"カットログ: {d / 'cutlog.md'}")


if __name__ == "__main__":
    cmds = {"list": cmd_list, "prepare": cmd_prepare, "apply-edit": cmd_apply_edit, "render": cmd_render, "publish": cmd_publish}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]](*sys.argv[2:])
