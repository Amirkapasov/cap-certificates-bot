"""Телеграм-бот выдачи сертификатов и благодарностей CAP Education.

Основной курс (Python, Web) выдаётся пакетом на ученика:
  сертификат + благодарность родителю (+ бонусные сертификаты: ИИ, Комп. грамотность,
  Мобилография). Каждый сертификат берёт свободный код из таблицы «Оффбординг»,
  строка заполняется сама: ФИО, ментор, ✓ Сертификат, курс, дата, «Действителен»,
  у основного курса ещё ✓ Благодарственное письмо.
Без подключённой таблицы код вводится вручную: «CAP-XXXXXX Ученик / Родитель».
"""
import glob
import itertools
import os
import re
import time
import traceback
from datetime import date

import telebot
from telebot import apihelper, types

import crm
import mailer
import qr
import sheets
from render import load_config, render


def _load_env(path='.env'):
    """Читаем настройки из .env, чтобы не держать их в коде."""
    if os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())


_load_env()
TOKEN = os.getenv('CERT_BOT_TOKEN', '')
ADMINS = [int(x) for x in os.getenv('CERT_ADMINS', '').replace(' ', '').split(',') if x]
USE_SHEET = sheets.sheet_available()  # иначе код вводится вручную
USE_CRM = crm.enabled()               # подтягивать родителя и почту из Альфа-CRM
CODE_RE = re.compile(r'CAP-[A-Z0-9]{6}', re.I)
PARENT_SPLIT = re.compile(r'\s*/\s*|\s*;\s*|\s+[—–-]\s+')  # «Ученик / Родитель»
LOG = 'issued.csv'
KZ_TO_RU = str.maketrans('әғқңөұүһіё', 'агкноуухие')

apihelper.CONNECT_TIMEOUT = 30   # при слабом интернете картинки грузятся дольше
apihelper.READ_TIMEOUT = 120
bot = telebot.TeleBot(TOKEN)
CFG = load_config()
TPL = CFG['templates']
BONUS = [k for k, t in TPL.items() if t.get('bonus')]
HIDDEN = set(CFG.get('mentors_hidden', []))  # бывшие менторы: в таблице остаются, в кнопках нет
state = {}    # chat_id -> {'tpl', 'step', 'mentor', 'bonus': set()}
pending = {}   # id пакета -> что отправить на почту (ждёт подтверждения)
_pack_seq = itertools.count(1)  # номера не переиспользуются, иначе письма затирают друг друга


# ---------- служебное ----------

def allowed(chat_id):
    return not ADMINS or chat_id in ADMINS


def answer(call):
    """Подтверждаем нажатие кнопки; если Telegram счёл его устаревшим — не падаем."""
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def sheet():
    return sheets.open_sheet()


def today():
    return date.today().strftime('%d.%m.%Y')


def _safe(name):
    return re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')


def _norm(text):
    """Сравнение имён без учёта регистра, пробелов и казахских букв (Құндыз = Кундыз)."""
    return ' '.join(text.casefold().translate(KZ_TO_RU).split())


def log_issue(code, fio, course, day):
    new = not os.path.exists(LOG)
    with open(LOG, 'a', encoding='utf-8') as f:
        if new:
            f.write('код;ФИО;курс;дата\n')
        f.write(f'{code};{fio};{course};{day}\n')


def used_codes():
    if not os.path.exists(LOG):
        return set()
    return {line.split(';')[0] for line in open(LOG, encoding='utf-8')}


def send_file(chat_id, path, caption, tries=3):
    """Один файл с повторами. True — ушёл."""
    for attempt in range(tries):
        try:
            with open(path, 'rb') as f:
                bot.send_document(chat_id, f, caption=caption)
            return True
        except Exception as e:
            print(f'send_document попытка {attempt + 1}: {e}')
            time.sleep(3 * (attempt + 1))
    return False


def send_pack(chat_id, files, caption, tries=3):
    """Пакет файлов одним сообщением (удобно пересылать и сохранять для почты)."""
    if len(files) == 1:
        return send_file(chat_id, files[0], caption, tries)
    for attempt in range(tries):
        handles = [open(p, 'rb') for p in files]
        try:
            media = [types.InputMediaDocument(h) for h in handles]
            media[-1].caption = caption
            bot.send_media_group(chat_id, media)
            return True
        except Exception as e:
            print(f'send_media_group попытка {attempt + 1}: {e}')
            time.sleep(3 * (attempt + 1))
        finally:
            for h in handles:
                h.close()
    # не вышло пачкой — пробуем по одному
    return all(send_file(chat_id, p, caption if p == files[-1] else '') for p in files)


# ---------- клавиатуры ----------

def kb():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(*[types.InlineKeyboardButton(t['title'], callback_data=f'tpl:{k}')
                 for k, t in TPL.items()])
    return markup


def bonus_kb(chosen):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for k in BONUS:
        mark = '✅' if k in chosen else '⬜️'
        markup.add(types.InlineKeyboardButton(f'{mark} {TPL[k]["title"]}', callback_data=f'b:{k}'))
    markup.add(types.InlineKeyboardButton('Далее →', callback_data='b:done'))
    return markup


def mentor_kb(names):
    markup = types.InlineKeyboardMarkup(row_width=3)
    shown = [n for n in names if n.strip() not in HIDDEN] or names
    markup.add(*[types.InlineKeyboardButton(n.strip(), callback_data=f'm:{n}') for n in shown])
    markup.add(types.InlineKeyboardButton('Без ментора', callback_data='m:'))
    return markup


def match_mentor(text, options):
    """Возвращает (точное имя из списка таблицы, варианты для кнопок)."""
    t = _norm(text)
    exact = [o for o in options if _norm(o) == t]
    if exact:
        return exact[0], []
    close = [o for o in options if _norm(o).startswith(t) or t in _norm(o)]
    if len(close) == 1:
        return close[0], []
    if not close:
        close = [o for o in options if _norm(o)[:2] == t[:2]]
    return None, close[:12]


# ---------- команды ----------

@bot.message_handler(commands=['start', 'help'])
def start(message):
    if not allowed(message.chat.id):
        bot.send_message(message.chat.id, f'Нет доступа. Ваш ID: {message.chat.id}')
        return
    state.pop(message.chat.id, None)
    bot.send_message(
        message.chat.id,
        'Выдача документов CAP Education.\n\n'
        'Python / Web — пакет на ученика: сертификат + благодарность родителю '
        '(+ бонусные сертификаты по желанию).\n\n'
        + ('Достаточно ФИО ученика — родителя и почту бот возьмёт из CRM:\n'
           'Иванов Иван\n'
           'Можно указать и вручную: Иванов Иван / Иванова Мария / mama@gmail.com\n'
           '/crm Фамилия Имя — проверить, что есть в CRM\n\n'
           if USE_CRM else
           'ФИО пишется так:\nИванов Иван / Иванова Мария / mama@gmail.com\n'
           '(почта не обязательна — без неё файлы придут только сюда)\n\n')
        + ('/stats — сколько кодов свободно\n'
           '/revoke CAP-XXXXXX — отозвать сертификат\n'
           '/resend CAP-XXXXXX — прислать готовый сертификат ещё раз\n'
           '/mail CAP-XXXXXX — дослать письмо родителю'
           if USE_SHEET else
           'Таблица не подключена — перед ФИО укажите свободный код:\n'
           'CAP-FYBFYC Иванов Иван / Иванова Мария / mama@gmail.com\n\n'
           '/log — последние выданные (для переноса в таблицу)'),
        reply_markup=kb())


@bot.message_handler(commands=['stats'])
def stats(message):
    if not allowed(message.chat.id):
        return
    try:
        s = sheet().stats()
        bot.send_message(message.chat.id, f'Свободно: {s["свободно"]}\nВыдано: {s["выдано"]}\n'
                                          f'Строк с кодами: {s["всего"]}')
    except Exception as e:
        bot.send_message(message.chat.id, f'Таблица недоступна: {e}')


@bot.message_handler(commands=['revoke'])
def revoke(message):
    if not allowed(message.chat.id):
        return
    m = CODE_RE.search(message.text)
    if not m:
        bot.send_message(message.chat.id, 'Используйте: /revoke CAP-XXXXXX')
        return
    try:
        row = sheet().revoke(m.group(0).upper())
        bot.send_message(message.chat.id, f'{m.group(0).upper()} отозван (строка {row}).')
    except Exception as e:
        bot.send_message(message.chat.id, f'Ошибка: {e}')


@bot.message_handler(commands=['resend'])
def resend(message):
    if not allowed(message.chat.id):
        return
    m = CODE_RE.search(message.text)
    if not m:
        bot.send_message(message.chat.id, 'Используйте: /resend CAP-XXXXXX')
        return
    code = m.group(0).upper()
    files = sorted(glob.glob(f'out/sert_*_{code}.png'), key=os.path.getmtime)
    if not files:
        bot.send_message(message.chat.id, f'Файл для {code} не найден.')
        return
    main = [f for f in files if os.path.basename(f).endswith(f'_{code}.png')
            and '_'.join(os.path.basename(f).split('_')[-2:]) == f'{code}.png']
    fio = os.path.basename(main[-1] if main else files[-1])[5:-len(code) - 5].replace('_', ' ')
    if not send_pack(message.chat.id, files, f'{fio}\n{code}\n{qr.verify_link(code)}'):
        bot.send_message(message.chat.id, 'Снова не отправилось — проверьте интернет и повторите.')


@bot.message_handler(commands=['crm'])
def crm_lookup(message):
    if not allowed(message.chat.id):
        return
    query = message.text.partition(' ')[2].strip()
    if not query:
        bot.send_message(message.chat.id, 'Используйте: /crm Фамилия Имя')
        return
    try:
        found = crm.find(query)
    except Exception as e:
        bot.send_message(message.chat.id, f'CRM недоступна: {e}')
        return
    if not found:
        bot.send_message(message.chat.id, 'В CRM никого не нашёл.')
        return
    bot.send_message(message.chat.id, '\n\n'.join(
        f'{c["student"]}\nРодитель: {c["parent"] or "—"}\nПочта: {c["email"] or "—"}\n'
        f'Телефон: {c["phone"] or "—"}' for c in found[:5]))


@bot.message_handler(commands=['mail'])
def mail_again(message):
    """Отправить письмо по уже выданному коду: /mail CAP-XXXXXX [почта]"""
    if not allowed(message.chat.id):
        return
    m = CODE_RE.search(message.text)
    if not m:
        bot.send_message(message.chat.id, 'Используйте: /mail CAP-XXXXXX [почта@mail.com]')
        return
    code = m.group(0).upper()
    files = sorted(glob.glob(f'out/sert_*_{code}.png'), key=os.path.getmtime)
    if not files:
        bot.send_message(message.chat.id, f'Файлов для {code} нет — выдайте сертификат заново.')
        return

    rec = next((r.split(';') for r in reversed(open(LOG, encoding='utf-8').read().splitlines())
                if r.startswith(code + ';')), None)
    if not rec:
        bot.send_message(message.chat.id, f'{code} нет в журнале выдач.')
        return
    _, student, course, day = rec[0], rec[1], rec[2], rec[3]

    em = mailer.EMAIL_RE.search(message.text)
    email, parent = (em.group(0) if em else ''), ''
    if not email and USE_CRM:
        try:
            found = crm.find(student)
            if len(found) == 1:
                email, parent = found[0]['email'], found[0]['parent']
        except Exception as e:
            bot.send_message(message.chat.id, f'CRM недоступна: {e}')
    if not email:
        bot.send_message(message.chat.id, f'Не знаю почту для «{student}». '
                                          f'Укажите: /mail {code} почта@mail.com')
        return

    key = next((k for k, t in TPL.items() if t.get('course') == course), None)
    lang = TPL.get(key, {}).get('lang', 'ru')
    letter_key = TPL.get(key, {}).get('letter')
    items = [('sert', course)]
    if parent and letter_key:
        path = f'out/{letter_key}_{_safe(parent)}.png'
        if os.path.exists(path):
            files.append(path)
            items.append(('blago', course))
    offer_email(message.chat.id, {'email': email, 'student': student, 'parent': parent,
                                  'course': course, 'lang': lang, 'files': files,
                                  'items': items, 'links': [qr.verify_link(code)]})


@bot.message_handler(commands=['log'])
def show_log(message):
    if not allowed(message.chat.id):
        return
    if not os.path.exists(LOG):
        bot.send_message(message.chat.id, 'Пока ничего не выдано.')
        return
    rows = open(LOG, encoding='utf-8').read().splitlines()[1:][-20:]
    bot.send_message(message.chat.id, 'Последние выданные:\n\n' + '\n'.join(
        r.replace(';', ' · ') for r in rows))


# ---------- шаги: курс → ментор → бонусы → ФИО ----------

@bot.callback_query_handler(func=lambda c: c.data.startswith('tpl:'))
def choose(call):
    chat_id = call.message.chat.id
    answer(call)
    if not allowed(chat_id):
        return
    key = call.data[4:]
    state[chat_id] = {'tpl': key, 'mentor': '', 'bonus': set()}
    if USE_SHEET and TPL[key]['kind'] == 'sert':
        state[chat_id]['step'] = 'mentor'
        bot.send_message(chat_id, f'{TPL[key]["title"]}\nНапишите имя ментора:',
                         reply_markup=mentor_kb([]))
    else:
        after_mentor(chat_id)


def after_mentor(chat_id):
    st = state[chat_id]
    if TPL[st['tpl']].get('letter'):
        st['step'] = 'bonus'
        bot.send_message(chat_id, 'Добавить бонусные сертификаты на имя ученика?',
                         reply_markup=bonus_kb(st['bonus']))
    else:
        ask_names(chat_id)


def ask_names(chat_id):
    st = state[chat_id]
    st['step'] = 'names'
    tpl = TPL[st['tpl']]
    lines = []
    if st.get('mentor'):
        lines.append(f'Ментор: {st["mentor"].strip()}')
    if st['bonus']:
        lines.append('Бонусы: ' + ', '.join(TPL[k]['title'] for k in BONUS if k in st['bonus']))
    if tpl.get('letter'):
        example = ('Иванов Иван   (родителя и почту возьму из CRM)' if USE_CRM
                   else 'Иванов Иван / Иванова Мария / mama@gmail.com')
        if not USE_SHEET:
            example = 'CAP-XXXXXX ' + example
        lines.append(f'Пришлите «ФИО ученика / ФИО родителя», каждого с новой строки:\n{example}')
    elif tpl['kind'] == 'sert' and not USE_SHEET:
        lines.append('Пришлите «CAP-XXXXXX ФИО ученика», каждого с новой строки:')
    else:
        lines.append(tpl['ask'] + ':')
    bot.send_message(chat_id, '\n'.join(lines))


@bot.callback_query_handler(func=lambda c: c.data.startswith('m:'))
def choose_mentor(call):
    chat_id = call.message.chat.id
    answer(call)
    if state.get(chat_id, {}).get('step') != 'mentor':
        return
    state[chat_id]['mentor'] = call.data[2:]
    after_mentor(chat_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith('b:'))
def toggle_bonus(call):
    chat_id = call.message.chat.id
    answer(call)
    st = state.get(chat_id)
    if not st or st.get('step') != 'bonus':
        return
    key = call.data[2:]
    if key == 'done':
        ask_names(chat_id)
        return
    st['bonus'] ^= {key}
    try:
        bot.edit_message_reply_markup(chat_id, call.message.message_id,
                                      reply_markup=bonus_kb(st['bonus']))
    except Exception:
        pass


# ---------- письмо родителю ----------

def offer_email(chat_id, pack):
    """Показываем, что уйдёт на почту, и ждём подтверждения."""
    pack_id = str(next(_pack_seq))
    pending[pack_id] = pack
    subject, body = mailer.build_text(pack['lang'], pack['student'], pack['course'],
                                      pack['items'], pack['links'])
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(f'📧 Отправить на {pack["email"]}',
                                          callback_data=f'mail:{pack_id}'))
    markup.add(types.InlineKeyboardButton('Не отправлять', callback_data=f'nomail:{pack_id}'))
    preview = '\n'.join(body.splitlines()[:6])
    bot.send_message(chat_id, f'Письмо для {pack["email"]}\n\nТема: {subject}\n\n{preview}…\n\n'
                              f'Вложений: {len(pack["files"])}', reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith(('mail:', 'nomail:')))
def send_email(call):
    chat_id = call.message.chat.id
    answer(call)
    action, pack_id = call.data.split(':', 1)
    pack = pending.pop(pack_id, None)
    if not pack:
        bot.send_message(chat_id, 'Это письмо уже отправлено, отменено или бот перезапускался.\n'
                                  'Дослать: /mail CAP-XXXXXX (адрес возьму из CRM) '
                                  'или /mail CAP-XXXXXX почта@mail.com')
        return
    if action == 'nomail':
        bot.send_message(chat_id, f'Не отправлено ({pack["student"]}).')
        return
    subject, body = mailer.build_text(pack['lang'], pack['student'], pack['course'],
                                      pack['items'], pack['links'])
    try:
        mailer.send(pack['email'], subject, body, pack['files'])
        bot.send_message(chat_id, f'✅ Письмо отправлено на {pack["email"]}.')
    except Exception as e:
        traceback.print_exc()
        pending[pack_id] = pack
        bot.send_message(chat_id, f'Не удалось отправить: {e}')


# ---------- выдача ----------

def make_sert(book, key, fio, mentor, day, code=None, letter=False):
    """Основной сертификат: берёт свободный код из таблицы, с QR, строка в таблице."""
    course = TPL[key]['course']
    if code is None:
        code = book.issue(fio, course, day, mentor, letter)['code']
    qr_path = qr.make_qr(code, f'out/qr_{code}.png')
    path = f'out/sert_{_safe(fio)}_{code}.png'
    render(key, {'name': fio, 'number': code, 'date': day, 'qr': qr_path}, path, CFG)
    log_issue(code, fio, course, day)
    return code, path


def make_bonus(key, fio, day, code):
    """Бонусный сертификат: тот же номер, без QR и без строки в таблице."""
    path = f'out/sert_{_safe(fio)}_{key}_{code}.png'
    render(key, {'name': fio, 'number': code, 'date': day}, path, CFG)
    return path


def issue_blago(chat_id, names, key):
    day = today()
    for fio in names:
        path = f'out/{key}_{_safe(fio)}.png'
        render(key, {'name': fio, 'date': day}, path, CFG)
        if not send_file(chat_id, path, f'{fio} · {day}'):
            bot.send_message(chat_id, f'⚠️ {fio}: файл не отправился, пришлите ФИО ещё раз.')
    bot.send_message(chat_id, f'Готово: {len(names)} шт.', reply_markup=kb())


def parse_line(line):
    """«[CAP-XXXXXX] Ученик / Родитель / почта» -> (code|None, ученик, родитель, почта)."""
    m = CODE_RE.search(line)
    code = m.group(0).upper() if m else None
    rest = CODE_RE.sub('', line).strip(' \t:,.')
    e = mailer.EMAIL_RE.search(rest)
    email = e.group(0) if e else ''
    if e:
        rest = (rest[:e.start()] + ' ' + rest[e.end():])
    parts = [p.strip(' -—–/;,') for p in PARENT_SPLIT.split(rest.strip(), maxsplit=1)]
    student = parts[0]
    parent = parts[1] if len(parts) > 1 else ''
    return code, student, parent, email


def issue_sert(chat_id, lines, st):
    key, mentor = st['tpl'], st.get('mentor', '')
    tpl = TPL[key]
    letter_key = tpl.get('letter')
    day = today()
    book = None
    if USE_SHEET:
        try:
            book = sheet()
        except Exception as e:
            bot.send_message(chat_id, f'Таблица недоступна: {e}')
            return

    seen = used_codes()
    students = 0
    for line in lines:
        code, student, parent, email = parse_line(line)
        if not student:
            continue
        if USE_CRM and letter_key and not (parent and email):
            try:
                found = crm.find(student)
            except Exception as e:
                found = []
                bot.send_message(chat_id, f'CRM недоступна ({e}) — беру, что указано вручную.')
            if len(found) > 1:
                names = '\n'.join(f'• {c["student"]}' for c in found[:6])
                bot.send_message(chat_id, f'«{student}»: в CRM несколько учеников —\n{names}\n'
                                          'Напишите ФИО полностью.')
                continue
            if found:
                c = found[0]
                student = c['student'] or student
                parent = parent or c['parent']
                email = email or c['email']
                bot.send_message(chat_id, f'CRM: {student}\nРодитель: {parent or "—"}\n'
                                          f'Почта: {email or "—"}')
            else:
                bot.send_message(chat_id, f'«{student}» — в CRM не нашёл, '
                                          'укажите родителя и почту вручную.')
        if not book and not code:
            bot.send_message(chat_id, f'«{line}» — нужен формат: CAP-XXXXXX Ученик / Родитель')
            continue
        if code and code in seen:
            bot.send_message(chat_id, f'⚠️ {code} уже выдавался через бота — проверьте таблицу.')

        files, codes, items = [], [], []
        try:
            c, p = make_sert(book, key, student, mentor, day, code,
                             letter=bool(letter_key and parent))
            files.append(p)
            codes.append(c)
            items.append(('sert', tpl['course']))
            for b in (k for k in BONUS if k in st['bonus']):
                files.append(make_bonus(b, student, day, codes[0]))
                items.append(('sert', TPL[b]['course']))
        except Exception as e:
            traceback.print_exc()
            done = f' Уже выданы: {", ".join(codes)}.' if codes else ''
            bot.send_message(chat_id, f'«{student}» — ошибка: {e}.{done}')
            if not files:
                continue

        if letter_key:
            if parent:
                path = f'out/{letter_key}_{_safe(parent)}.png'
                render(letter_key, {'name': parent, 'date': day}, path, CFG)
                files.append(path)
                items.append(('blago', tpl['course']))
            else:
                bot.send_message(chat_id, f'«{student}» — не указан родитель, благодарность не сделана '
                                          '(формат: Ученик / Родитель).')

        caption = (f'{student}' + (f'\nРодитель: {parent}' if parent else '')
                   + f'\n{tpl["course"]} · {day}\n' + '\n'.join(codes))
        if not send_pack(chat_id, files, caption):
            bot.send_message(chat_id, f'⚠️ {student}: коды {", ".join(codes)} уже в таблице, но файлы '
                                      f'не отправились. Не выдавайте заново — /resend {codes[0]}')
        if email:
            offer_email(chat_id, {'email': email, 'student': student, 'parent': parent,
                                  'course': tpl['course'], 'lang': tpl.get('lang', 'ru'),
                                  'files': list(files), 'items': items,
                                  'links': [qr.verify_link(c) for c in codes]})
        seen.update(codes)
        students += 1

    if not students:
        return
    if book:
        tail = f'Свободных кодов осталось: {book.stats()["свободно"]}'
    else:
        tail = (f'Не забудьте в таблице «Оффбординг» по этим кодам вписать ФИО, курс, '
                f'дату {day} и статус «Действителен».')
    bot.send_message(chat_id, f'Готово: {students} уч.\n{tail}', reply_markup=kb())


@bot.message_handler(func=lambda m: True, content_types=['text'])
def flow(message):
    chat_id = message.chat.id
    if not allowed(chat_id):
        return
    st = state.get(chat_id)
    if not st:
        bot.send_message(chat_id, 'Сначала выберите курс:', reply_markup=kb())
        return

    if st.get('step') == 'mentor':
        try:
            options = sheet().mentors()
        except Exception as e:
            bot.send_message(chat_id, f'Не удалось получить список менторов: {e}')
            return
        name, close = match_mentor(message.text, options)
        if name:
            st['mentor'] = name
            bot.send_message(chat_id, f'Ментор: {name.strip()}')
            after_mentor(chat_id)
        else:
            bot.send_message(chat_id, 'Не нашёл такого ментора. Выберите:',
                             reply_markup=mentor_kb(close or options))
        return
    if st.get('step') == 'bonus':
        bot.send_message(chat_id, 'Отметьте бонусы кнопками и нажмите «Далее →».',
                         reply_markup=bonus_kb(st['bonus']))
        return

    lines = [l.strip() for l in message.text.splitlines() if l.strip()]
    if not lines:
        return
    try:
        if TPL[st['tpl']]['kind'] == 'blago':
            issue_blago(chat_id, lines, st['tpl'])
        else:
            issue_sert(chat_id, lines, st)
        state.pop(chat_id, None)
    except FileNotFoundError as e:
        bot.send_message(chat_id, f'Нет файла шаблона: {e}')
    except Exception as e:
        traceback.print_exc()
        bot.send_message(chat_id, f'Ошибка: {e}')


if __name__ == '__main__':
    if not TOKEN:
        raise SystemExit('Задайте CERT_BOT_TOKEN в .env')
    print('bot started')
    bot.infinity_polling()
