/**
 * ポッドキャスト自動編集：ドライブ連携用 Google Apps Script
 *
 * Claude のセッションから次の3つを行うための窓口。
 *   list     : 「01.Podcast音声アップロード」の未処理の音声を一覧
 *   download : 音声を base64 で返す
 *   publish  : 編集済みファイルを「02.Podcast編集済み」に保存し、元ファイルを「01/処理済み」へ移動
 *
 * さらに、10分ごとに「01」を確認し、新しい録音があるときだけ Claude の定期実行（ルーティン）を呼び出す。
 *   checkAndFire   : 時間主導トリガーから呼ばれる本体
 *   installTrigger : 10分ごとのトリガーを作る（エディタから1回だけ実行する）
 *
 * デプロイ手順は README の「自動化のセットアップ」を参照。
 * スクリプト プロパティ:
 *   TOKEN         : Claude のセッションとの合言葉（推測されにくい長い文字列）
 *   ROUTINE_TOKEN : ルーティンの API トリガーで発行したトークン
 */
const INPUT_FOLDER_ID = '1mnLOMeeIudZR88n1RU9-8uxarAbRkMn3';  // 01.Podcast音声アップロード
const OUTPUT_FOLDER_ID = '1VMFzXA0zVblbjCl6x1K2RAhBwWmCYDL9'; // 02.Podcast編集済み
const DONE_FOLDER_NAME = '処理済み';
const ROUTINE_FIRE_URL = 'https://api.anthropic.com/v1/claude_code/routines/trig_01DxuKSW7KBYQk1GpMizYGry/fire';
const REFIRE_AFTER_MS = 3 * 60 * 60 * 1000; // 呼び出し後3時間たっても録音が残っていたら、処理失敗とみなして呼び直す
const MAX_FIRES_PER_FILE = 3;

function doPost(e) {
  let body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return json_({ ok: false, error: 'bad request' });
  }
  const token = PropertiesService.getScriptProperties().getProperty('TOKEN');
  if (!token || body.token !== token) return json_({ ok: false, error: 'unauthorized' });

  const input = DriveApp.getFolderById(INPUT_FOLDER_ID);
  switch (body.action) {
    case 'list': {
      const files = [];
      const it = input.getFiles();
      while (it.hasNext()) {
        const f = it.next();
        if (isAudio_(f)) {
          files.push({ id: f.getId(), name: f.getName(), size: f.getSize(), created: f.getDateCreated() });
        }
      }
      return json_({ ok: true, files: files });
    }
    case 'download': {
      const f = DriveApp.getFileById(body.id);
      if (!isIn_(f, input)) return json_({ ok: false, error: 'not in input folder' });
      return json_({ ok: true, name: f.getName(), mimeType: f.getMimeType(), base64: Utilities.base64Encode(f.getBlob().getBytes()) });
    }
    case 'publish': {
      const src = DriveApp.getFileById(body.sourceId);
      if (!isIn_(src, input)) return json_({ ok: false, error: 'not in input folder' });
      const output = DriveApp.getFolderById(OUTPUT_FOLDER_ID);
      const saved = (body.files || []).map(function (x) {
        const blob = Utilities.newBlob(Utilities.base64Decode(x.base64), x.mimeType, x.name);
        return output.createFile(blob).getId();
      });
      src.moveTo(doneFolder_(input));
      return json_({ ok: true, saved: saved });
    }
  }
  return json_({ ok: false, error: 'unknown action' });
}

/** 10分ごとに実行: 新しい録音があるときだけルーティンを呼び出す */
function checkAndFire() {
  const props = PropertiesService.getScriptProperties();
  const fired = JSON.parse(props.getProperty('FIRED') || '{}'); // fileId -> {at, count}
  const now = Date.now();
  const pending = [];
  const present = {};
  const it = DriveApp.getFolderById(INPUT_FOLDER_ID).getFiles();
  while (it.hasNext()) {
    const f = it.next();
    if (!isAudio_(f)) continue;
    const id = f.getId();
    present[id] = true;
    const rec = fired[id];
    if (!rec || (now - rec.at > REFIRE_AFTER_MS && rec.count < MAX_FIRES_PER_FILE)) pending.push(f);
  }
  // 処理済みになった録音の記録は消す
  Object.keys(fired).forEach(function (id) { if (!present[id]) delete fired[id]; });
  if (pending.length) {
    const res = UrlFetchApp.fetch(ROUTINE_FIRE_URL, {
      method: 'post',
      contentType: 'application/json',
      headers: {
        'Authorization': 'Bearer ' + props.getProperty('ROUTINE_TOKEN'),
        'anthropic-beta': 'experimental-cc-routine-2026-04-01',
        'anthropic-version': '2023-06-01',
      },
      payload: JSON.stringify({ text: '新しい録音: ' + pending.map(function (f) { return f.getName(); }).join(', ') }),
      muteHttpExceptions: true,
    });
    const code = res.getResponseCode();
    console.log('fire ' + code + ' ' + res.getContentText().slice(0, 300));
    if (code >= 200 && code < 300) {
      pending.forEach(function (f) {
        const rec = fired[f.getId()] || { count: 0 };
        fired[f.getId()] = { at: now, count: rec.count + 1 };
      });
    }
  }
  props.setProperty('FIRED', JSON.stringify(fired));
}

/** エディタから1回だけ実行: 10分ごとの checkAndFire トリガーを作る（重複は作らない） */
function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'checkAndFire') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('checkAndFire').timeBased().everyMinutes(10).create();
  console.log('installed: checkAndFire every 10 minutes');
}

function isAudio_(f) {
  return f.getMimeType().indexOf('audio/') === 0 || /\.(m4a|mp3|wav|aac)$/i.test(f.getName());
}

function isIn_(file, folder) {
  const parents = file.getParents();
  while (parents.hasNext()) if (parents.next().getId() === folder.getId()) return true;
  return false;
}

function doneFolder_(input) {
  const it = input.getFoldersByName(DONE_FOLDER_NAME);
  return it.hasNext() ? it.next() : input.createFolder(DONE_FOLDER_NAME);
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
