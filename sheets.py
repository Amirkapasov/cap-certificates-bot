"""Запись в Google-таблицу «Оффбординг».

Основной способ — Google Sheets API от имени аккаунта куратора (OAuth):
  credentials.json — ключ приложения из Google Cloud,
  token.json — разрешение после входа (создаётся `python google_login.py`).
Запасной — Apps Script (apps_script.gs), если задан APPS_SCRIPT_URL.

Колонки: A код | B ФИО | C Ментор | D Комментарий | E Сертификат ✓ |
         F Выпускной ✓ | G Курс | H Дата | I Статус | J QR | K Проверить | L Благодарность ✓
"""
import os
import threading

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CREDS = os.path.join(BASE, 'credentials.json')
TOKEN = os.path.join(BASE, 'token.json')
SCOPES = ['https://www.googleapis.com/auth/spreadsheets',
          'https://www.googleapis.com/auth/gmail.send']  # запись в таблицу + письма родителям
SHEET_GID = 678851710
HEADER = 'ID (код)'
FIRST_ROW = 2
FREE, VALID, REVOKED = 'Свободен', 'Действителен', 'Отозван'
_issue_lock = threading.Lock()   # два ментора не должны занять один код одновременно


def oauth_creds():
    """Сохранённое разрешение; обновляет его само, когда истекает."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not os.path.exists(TOKEN):
        raise RuntimeError('нет token.json — запустите: python google_login.py')
    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN, 'w') as f:
            f.write(creds.to_json())
    return creds


class ApiSheet:
    """Таблица через Google Sheets API."""

    def __init__(self):
        import gspread
        sheet_id = os.getenv('CERT_SHEET_ID', '')
        if not sheet_id:
            raise RuntimeError('не задан CERT_SHEET_ID в .env')
        self.book = gspread.authorize(oauth_creds()).open_by_key(sheet_id)
        self.ws = self._find_ws()

    def _find_ws(self):
        sheets = self.book.worksheets()
        for ws in sheets:
            if ws.id == SHEET_GID:
                return ws
        for ws in sheets:
            if str(ws.acell('A1').value or '').strip() == HEADER:
                return ws
        raise RuntimeError(f'в таблице нет листа с заголовком «{HEADER}»')

    def _rows(self):
        return self.ws.get(f'A{FIRST_ROW}:I{self.ws.row_count}')

    def mentors(self):
        """Имена из выпадающего списка колонки «Ментор» — ровно как в таблице."""
        meta = self.book.fetch_sheet_metadata({
            'includeGridData': 'true',
            'ranges': f"'{self.ws.title}'!C{FIRST_ROW}:C{FIRST_ROW + 300}",
            'fields': 'sheets.data.rowData.values.dataValidation'})
        for row in meta['sheets'][0]['data'][0].get('rowData', []):
            for cell in row.get('values', []):
                cond = cell.get('dataValidation', {}).get('condition', {})
                if cond.get('type') == 'ONE_OF_LIST':
                    return [v['userEnteredValue'] for v in cond.get('values', [])]
        return sorted({v.strip() for v in self.ws.col_values(3)[1:] if v.strip()})

    def issue(self, fio, course, issued_on, mentor='', letter=False, attempts=5):
        """Занимает свободный код под ученика.

        Строка сначала «захватывается» (пишем ФИО и сразу перечитываем): если её
        успел занять кто-то другой — из бота или руками в таблице, — берём следующую.
        """
        with _issue_lock:
            for _ in range(attempts):
                row = self._free_row()
                if not row:
                    raise RuntimeError('свободных кодов не осталось')
                r, code = row
                self.ws.update_acell(f'B{r}', fio)
                if (self.ws.acell(f'B{r}').value or '').strip() != fio.strip():
                    continue          # строку перехватили — пробуем следующую
                updates = [{'range': f'E{r}', 'values': [[True]]},
                           {'range': f'G{r}:I{r}', 'values': [[course, issued_on, VALID]]}]
                if mentor:
                    updates.append({'range': f'C{r}', 'values': [[mentor]]})
                if letter:            # L — «Благодарственное письмо» ✓
                    updates.append({'range': f'L{r}', 'values': [[True]]})
                self.ws.batch_update(updates, value_input_option='USER_ENTERED')
                return {'code': code, 'row': r, 'fio': fio,
                        'course': course, 'date': issued_on}
        raise RuntimeError('не удалось занять код: строки разбирают быстрее, попробуйте ещё раз')

    def _free_row(self):
        """Первая строка с кодом, пустым ФИО и статусом «Свободен»."""
        for i, row in enumerate(self._rows()):
            row = row + [''] * (9 - len(row))
            code, name, status = row[0].strip(), row[1].strip(), row[8].strip()
            if code and not name and status == FREE:
                return FIRST_ROW + i, code
        return None

    def stats(self):
        st = [(r[8].strip() if len(r) > 8 else '') for r in self._rows()]
        return {'свободно': st.count(FREE), 'выдано': st.count(VALID),
                'всего': sum(1 for x in st if x)}

    def revoke(self, code):
        for i, row in enumerate(self._rows()):
            if row and row[0].strip() == code.strip():
                self.ws.update_acell(f'I{FIRST_ROW + i}', REVOKED)
                return FIRST_ROW + i
        raise RuntimeError(f'код {code} не найден')


class ScriptSheet:
    """Та же таблица, но через Apps Script — без Google Cloud и ключей.

    Код скрипта — apps_script.gs, URL и секрет — в .env.
    """

    def __init__(self):
        self.url = os.getenv('APPS_SCRIPT_URL', '')
        self.secret = os.getenv('APPS_SCRIPT_SECRET', '')
        if not self.url:
            raise RuntimeError('не задан APPS_SCRIPT_URL в .env')

    def _call(self, **payload):
        # Apps Script отвечает редиректом 302 — requests сам его проходит
        resp = requests.post(self.url, json={'secret': self.secret, **payload}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if not data.get('ok'):
            raise RuntimeError(data.get('error', 'ошибка таблицы'))
        return data

    def mentors(self):
        return []

    def issue(self, fio, course, issued_on, mentor='', letter=False):
        data = self._call(action='issue', fio=fio, course=course,
                          date=issued_on, mentor=mentor)
        return {'code': data['code'], 'row': data['row'], 'fio': fio,
                'course': course, 'date': issued_on}

    def stats(self):
        data = self._call(action='stats')
        return {'свободно': data['free'], 'выдано': data['valid'],
                'всего': data['free'] + data['valid']}

    def revoke(self, code):
        return self._call(action='revoke', code=code)['row']


def sheet_available():
    return bool((os.path.exists(TOKEN) and os.getenv('CERT_SHEET_ID'))
                or os.getenv('APPS_SCRIPT_URL'))


def open_sheet():
    if os.path.exists(TOKEN) and os.getenv('CERT_SHEET_ID'):
        return ApiSheet()
    return ScriptSheet()
