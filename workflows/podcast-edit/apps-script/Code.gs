/**
 * ポッドキャスト自動編集：ドライブ連携用 Google Apps Script
 *
 * Claude のセッションから次の3つを行うための窓口。
 *   list     : 「01.Podcast音声アップロード」の未処理の音声を一覧
 *   download : 音声を base64 で返す
 *   publish  : 編集済みファイルを「02.Podcast編集済み」に保存し、元ファイルを「01/処理済み」へ移動
 *
 * デプロイ手順は README の「自動化のセットアップ」を参照。
 * スクリプトのプロパティ TOKEN に、推測されにくい長い文字列を設定すること。
 */
const INPUT_FOLDER_ID = '1mnLOMeeIudZR88n1RU9-8uxarAbRkMn3';  // 01.Podcast音声アップロード
const OUTPUT_FOLDER_ID = '1VMFzXA0zVblbjCl6x1K2RAhBwWmCYDL9'; // 02.Podcast編集済み
const DONE_FOLDER_NAME = '処理済み';

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
        if (f.getMimeType().indexOf('audio/') === 0 || /\.(m4a|mp3|wav|aac)$/i.test(f.getName())) {
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
