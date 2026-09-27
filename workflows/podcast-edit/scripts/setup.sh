#!/usr/bin/env bash
# ポッドキャスト自動編集の実行環境を準備する（クラウド環境はセッションごとにリセットされるため毎回実行）
set -euo pipefail
MODEL_DIR="${PODCAST_MODEL_DIR:-$HOME/.cache/podcast-edit}"
mkdir -p "$MODEL_DIR"
pip install -q sherpa-onnx imageio-ffmpeg numpy requests 2>&1 | grep -v "WARNING: Running pip as the 'root' user" || true
BASE=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models
if ! ls "$MODEL_DIR"/sherpa-onnx-sense-voice-*/model.int8.onnx >/dev/null 2>&1; then
  curl -sSL -o "$MODEL_DIR/sv.tar.bz2" "$BASE/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"  # 約1GB
  tar xjf "$MODEL_DIR/sv.tar.bz2" -C "$MODEL_DIR" && rm "$MODEL_DIR/sv.tar.bz2"
fi
[ -f "$MODEL_DIR/silero_vad.onnx" ] || curl -sSL -o "$MODEL_DIR/silero_vad.onnx" "$BASE/silero_vad.onnx"
echo "setup ok: $MODEL_DIR"
