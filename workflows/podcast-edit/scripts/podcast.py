#!/usr/bin/env python3
"""ポッドキャスト自動編集パイプライン

使い方:
  podcast.py list                 未処理の録音を一覧（ドライブ「01.Podcast音声アップロード」）
  podcast.py prepare <file_id>    ダウンロード → 文字起こし → カット候補（cuts_draft.json）
  podcast.py render <file_id>     cuts.json に従って編集 → 仕上げ → 再文字起こしで確認
  podcast.py publish <file_id>    編集済み音声（MP3）を「02.Podcast編集済み」へ保存し、元ファイルを「処理済み」へ移動

cuts.json は prepare が出す cuts_draft.json を Claude が見直して作る（.claude/commands/podcast.md 参照）。
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
    return out


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
    print(f"# 作業フォルダ: {d}\n# 文字起こし（各文字の開始秒）:")
    for i, o in enumerate(tr):
        print(f"[{i}] {o['start']:.2f}-{o['end']:.2f} " + " ".join(f"{t}{s:.2f}" for t, s in zip(o["tokens"], o["ts"])))
    print(f"\n# 言い直しの候補（直後に同じ言い回し）: {len(reps)} 件 → 1件ずつ文脈を見て判断する")
    for r in reps:
        print(f"- {r['first']:.2f}s と {r['second']:.2f}s 「{r['text']}」  …{r['context']}…")
    print(f"\n# 撮り直しの候補（同じ内容を話し直している可能性）: {len(retakes)} 件 → 1件ずつ文脈を見て判断する")
    for r in retakes:
        print(f"- テイク1 {r['take1'][0]:.2f}-{r['take1'][1]:.2f}s「{r['text1']}」\n"
              f"  テイク2 {r['take2'][0]:.2f}-{r['take2'][1]:.2f}s「{r['text2']}」 類似度 {r['score']}")
    if cues:
        print("# 撮り直しの合図になる言葉: " + " / ".join(f"{c['time']:.2f}s「{c['text']}」" for c in cues))


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
    if len(sys.argv) < 2 or sys.argv[1] not in ("list", "prepare", "render", "publish"):
        sys.exit(__doc__)
    {"list": cmd_list, "prepare": cmd_prepare, "render": cmd_render, "publish": cmd_publish}[sys.argv[1]](*sys.argv[2:])
