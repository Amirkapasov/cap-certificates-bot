// CAP Education: certificate bot backend. Paste into Extensions > Apps Script,
// then Deploy > Manage deployments > edit > New version, Who has access: Anyone.
const SECRET = 'PASTE_SECRET_FROM_ENV';  // APPS_SCRIPT_SECRET из .env
const SHEET_GID = 678851710;
const HEADER = 'ID (\u043a\u043e\u0434)';
const FIRST_ROW = 2;
const FREE = '\u0421\u0432\u043e\u0431\u043e\u0434\u0435\u043d', VALID = '\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0442\u0435\u043b\u0435\u043d', REVOKED = '\u041e\u0442\u043e\u0437\u0432\u0430\u043d';
// A code | B fio | C mentor | D comment | E cert | F graduation | G course | H date | I status

function doPost(e) {
  const req = JSON.parse(e.postData.contents);
  if (req.secret !== SECRET) return out({ok: false, error: 'forbidden'});

  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    if (req.action === 'sheets') return out(listSheets());
    const sh = findSheet();
    if (!sh) return out({ok: false, error: '\u043b\u0438\u0441\u0442 \u0441 \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043a\u043e\u043c \u00ab' + HEADER + '\u00bb \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d'});
    if (req.action === 'issue')  return out(issue(sh, req));
    if (req.action === 'stats')  return out(stats(sh));
    if (req.action === 'revoke') return out(revoke(sh, req.code));
    return out({ok: false, error: 'unknown action'});
  } catch (err) {
    return out({ok: false, error: String(err)});
  } finally {
    lock.releaseLock();
  }
}

function findSheet() {
  const all = SpreadsheetApp.getActive().getSheets();
  return all.find(s => s.getSheetId() === SHEET_GID)
      || all.find(s => String(s.getRange(1, 1).getValue()).trim() === HEADER);
}

function listSheets() {
  const book = SpreadsheetApp.getActive();
  return {ok: true, file: book.getName(), sheets: book.getSheets().map(s => ({
    name: s.getName(), gid: s.getSheetId(), rows: s.getLastRow(),
    header: s.getLastColumn() ? s.getRange(1, 1, 1, Math.min(9, s.getLastColumn())).getValues()[0] : []
  }))};
}

function data(sh) {
  return sh.getRange(FIRST_ROW, 1, sh.getLastRow() - FIRST_ROW + 1, 9).getValues();
}

function issue(sh, req) {
  const rows = data(sh);
  for (let i = 0; i < rows.length; i++) {
    const [code, fio, , , , , , , status] = rows[i];
    if (code && !String(fio).trim() && String(status).trim() === FREE) {
      const r = FIRST_ROW + i;
      sh.getRange(r, 2).setValue(req.fio);
      if (req.mentor) sh.getRange(r, 3).setValue(req.mentor);
      sh.getRange(r, 5).setValue(true);
      sh.getRange(r, 7, 1, 3).setValues([[req.course, req.date, VALID]]);
      return {ok: true, code: code, row: r};
    }
  }
  return {ok: false, error: '\u0441\u0432\u043e\u0431\u043e\u0434\u043d\u044b\u0445 \u043a\u043e\u0434\u043e\u0432 \u043d\u0435 \u043e\u0441\u0442\u0430\u043b\u043e\u0441\u044c'};
}

function stats(sh) {
  const st = data(sh).map(r => String(r[8]).trim());
  return {ok: true, sheet: sh.getName(),
          free: st.filter(s => s === FREE).length,
          valid: st.filter(s => s === VALID).length};
}

function revoke(sh, code) {
  const rows = data(sh);
  for (let i = 0; i < rows.length; i++) {
    if (String(rows[i][0]).trim() === String(code).trim()) {
      sh.getRange(FIRST_ROW + i, 9).setValue(REVOKED);
      return {ok: true, row: FIRST_ROW + i};
    }
  }
  return {ok: false, error: '\u043a\u043e\u0434 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d'};
}

function out(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
