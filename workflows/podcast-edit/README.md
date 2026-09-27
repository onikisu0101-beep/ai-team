# 🎙️ ポッドキャスト自動編集ワークフロー

録音した音声から「えー」「あー」などのフィラー、言い直し、ノイズを取り除き、編集済みの音声として保存する。

---

## 決定事項（2026-09-27）

| 項目 | 決定 |
|-----|-----|
| 自動化レベル | **Phase C（手動）から開始**。カットの質を確かめてから Phase A（1時間ごとの定期チェック）か Phase B（完全自動）へ進む |
| カットの強さ | **しっかり**。フィラーに加えて、言い直し・噛み・繰り返しもカットする |
| 脱線トーク | 文脈で判断する。面白い脱線は残し、**本題と無関係でつまらない脱線だけ**カットする |
| 確認フロー | 確認は挟まず、**そのままカットして保存**する。ただし元ファイルは必ず残し、カットログを添える |
| 話者 | 基本は1人。対談が混ざる場合は、声質の違いで話者を見分け、相手の相づちや発言を誤ってカットしない |

---

## ドライブのフォルダ

| 用途 | フォルダ | ID |
|-----|---------|----|
| 入力（未編集） | [01.Podcast音声アップロード](https://drive.google.com/drive/folders/1mnLOMeeIudZR88n1RU9-8uxarAbRkMn3) | `1mnLOMeeIudZR88n1RU9-8uxarAbRkMn3` |
| 出力（編集済み） | [02.Podcast編集済み](https://drive.google.com/drive/folders/1VMFzXA0zVblbjCl6x1K2RAhBwWmCYDL9) | `1VMFzXA0zVblbjCl6x1K2RAhBwWmCYDL9` |

※ 今のドライブ連携では、数MBの音声を書き戻すのは難しい見込み。テスト段階では、編集済みの音声はチャットで直接渡す。自動保存は、Googleの認証情報を環境に登録して Drive API を直接使う形で後から整える。

---

## 処理フロー

```
① 録音 → Googleドライブ「01.Podcast音声アップロード」にアップ
② ユーザーが「編集して」と依頼（Phase C）
③ ドライブからダウンロード → 16kHz モノラルに変換
④ 文字起こし（SenseVoice / 1文字ずつのタイムスタンプ付き）
⑤ カット候補を検出
   - 機械検出: フィラー、0.8秒を超える無音（0.4秒に詰める）、破裂音、リップノイズ
   - Claude判断: 言い直し、繰り返し、つまらない脱線
⑥ ffmpeg で編集
   - カット（つなぎ目に 10〜30ms のクロスフェード）
   - ノイズ除去（afftdn）、破裂音対策（highpass 80Hz）
   - ラウドネス正規化（-16 LUFS / True Peak -1.5 dB）
⑦ ドライブ「02.Podcast編集済み」に保存
   - 編集済み音声
   - カットログ（何秒目の何を、なぜカットしたか）
   - 文字起こし全文（ショーノートや他部門の投稿ネタに流用する）
```

---

## 技術メモ

- **文字起こしモデル**: `sherpa-onnx` + SenseVoice（日本語に対応し、1文字ずつのタイムスタンプが取れる）
  - Whisper はフィラーを省いて書き起こす傾向があるため、フィラー検出には不向き
  - huggingface.co は現在の環境のネットワーク設定でブロックされているため、GitHub Releases から取得する
- **ffmpeg**: apt ではインストールできないため、`imageio-ffmpeg`（pip）に同梱されているバイナリを使う
- クラウド環境はセッションごとにリセットされるため、毎回セットアップが必要

### セットアップ手順

```bash
pip install sherpa-onnx imageio-ffmpeg numpy
curl -sSL -o sv.tar.bz2 \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2
tar xjf sv.tar.bz2 && rm sv.tar.bz2   # 約1GB
```

---

## 次のステップ

- [ ] 5分程度のテスト録音で Phase C を試す
- [ ] SenseVoice がフィラー（えー・あー）を文字として拾えるか検証する。拾えない場合は、音声波形からの検出を追加する
- [ ] カットの強さ・脱線の判断基準をユーザーのフィードバックで調整する
- [ ] 処理をスクリプト化する（`workflows/podcast-edit/` 配下）
- [ ] Phase A / B に移行する
