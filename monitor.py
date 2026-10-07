"""Мониторинг бота: статус по запросу и отчёт в группу раз в N часов.

Куда слать отчёты, задаётся командой /monitor_here прямо в группе —
адрес чата сохраняется в monitor_chat.txt, правка .env не нужна.
"""
import collections
import os
import re
import subprocess
import time
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
CHAT_FILE = os.path.join(BASE, 'monitor_chat.txt')
STARTED = time.time()
PERIOD = int(os.getenv('MONITOR_HOURS', '4')) * 3600

# обрывы связи — не поломка: бот переподключается сам, считаем их отдельно
NETWORK = re.compile(r'ReadTimeout|ConnectionError|ConnectionReset|NameResolution|MaxRetry'
                     r'|Connection aborted|Connection reset|Broken pipe|Errno 54|Errno 8'
                     r'|Temporary failure|timed out|ProtocolError|RemoteDisconnected', re.I)
NOISE = re.compile(r'^\s*(raise |self\.|return |during handling|the above exception'
                   r'|traceback \(most recent|file ")', re.I)


def chat_id():
    """Чат, куда уходят отчёты (0 — рассылка выключена)."""
    env = os.getenv('MONITOR_CHAT', '').strip()
    if env:
        return int(env)
    if os.path.exists(CHAT_FILE):
        try:
            return int(open(CHAT_FILE).read().strip())
        except ValueError:
            pass
    return 0


def set_chat(value):
    with open(CHAT_FILE, 'w') as f:
        f.write(str(value))


def uptime():
    s = int(time.time() - STARTED)
    d, s = divmod(s, 86400)
    h, m = divmod(s // 60, 60)
    return (f'{d} д ' if d else '') + f'{h} ч {m} мин'


def version():
    try:
        return subprocess.run(['git', 'log', '--oneline', '-1'], cwd=BASE, timeout=5,
                              capture_output=True, text=True).stdout.strip()[:60]
    except Exception:
        return 'неизвестно'


def _rows(path):
    if not os.path.exists(path):
        return []
    return [l.split(';') for l in open(path, encoding='utf-8').read().splitlines()[1:] if l.strip()]


def _parse(d):
    for fmt in ('%d.%m.%Y %H:%M', '%d.%m.%Y'):
        try:
            return datetime.strptime(d.strip(), fmt)
        except ValueError:
            continue
    return None


def journal_stats(days=7):
    """Что бот сделал за последние дни по своим журналам."""
    issued = _rows(os.path.join(BASE, 'issued.csv'))
    emails = _rows(os.path.join(BASE, 'emails.csv'))
    edge = datetime.now() - timedelta(days=days)
    today = date.today().strftime('%d.%m.%Y')

    week_issued = [r for r in issued if len(r) > 3 and (_parse(r[3]) or edge) >= edge]
    week_mails = [r for r in emails if r and (_parse(r[0]) or edge) >= edge]
    last = issued[-1] if issued else None
    return {
        'today_issued': sum(1 for r in issued if len(r) > 3 and r[3].strip() == today),
        'today_mails': sum(1 for r in emails if r and r[0].startswith(today)),
        'week_issued': len(week_issued),
        'week_mails': len(week_mails),
        'courses': collections.Counter(r[2].strip() for r in week_issued if len(r) > 2),
        'last': (last[1].strip(), last[2].strip(), last[3].strip()) if last and len(last) > 3 else None,
    }


def sheet_stats(sheet_fn, days=7):
    """Кто из менторов выдавал за последние дни и на сколько хватит кодов."""
    book = sheet_fn()
    stats = book.stats()
    edge = datetime.now() - timedelta(days=days)
    mentors = collections.Counter()
    recent = 0
    for row in book._rows():
        row = row + [''] * (9 - len(row))
        when = _parse(row[7])
        if when and when >= edge and row[8].strip() == 'Действителен':
            recent += 1
            if row[2].strip():
                mentors[row[2].strip()] += 1
    weeks_left = round(stats['свободно'] / recent, 1) if recent else None
    return stats, mentors, weeks_left


def log_health(log_path, limit=3):
    """Делим лог на сетевые обрывы (не страшно) и настоящие ошибки."""
    if not log_path or not os.path.exists(log_path):
        return 0, []
    try:
        lines = open(log_path, encoding='utf-8', errors='replace').read().splitlines()[-600:]
    except OSError:
        return 0, []

    drops, problems = 0, []
    for line in lines:
        text = line.strip()
        if not text or NOISE.match(text):
            continue
        if NETWORK.search(text):
            drops += 1
            continue
        if re.search(r'Error|Exception|CRITICAL', text) and 'telebot' not in text.lower():
            problems.append(text[:90])
    seen = []
    for p in reversed(problems):          # только последние и без повторов
        if p not in seen:
            seen.append(p)
        if len(seen) >= limit:
            break
    return drops, seen


def status(sheet_fn=None, log_path=None, days=7):
    """Короткий отчёт о состоянии бота."""
    out = [f'🟢 Бот работает · {datetime.now():%d.%m %H:%M}',
           f'⏱ Аптайм: {uptime()}',
           f'🔖 Версия: {version()}', '']

    if sheet_fn:
        try:
            stats, mentors, weeks = sheet_stats(sheet_fn, days)
            line = f'📇 Коды: свободно {stats["свободно"]}, выдано {stats["выдано"]}'
            if weeks:
                line += f' · хватит примерно на {weeks} нед.'
            out.append(line)
            if stats['свободно'] < 50:
                out.append(f'⚠️ Коды заканчиваются: осталось {stats["свободно"]}')
            if mentors:
                top = ', '.join(f'{m} — {n}' for m, n in mentors.most_common(5))
                out.append(f'👥 Менторы за {days} дн.: {top}')
        except Exception as e:
            out.append(f'❌ Таблица недоступна: {str(e)[:60]}')

    j = journal_stats(days)
    out.append(f'📄 Сегодня: сертификатов {j["today_issued"]}, писем {j["today_mails"]}')
    out.append(f'📊 За {days} дн.: сертификатов {j["week_issued"]}, писем {j["week_mails"]}')
    if j['courses']:
        out.append('📚 Курсы: ' + ', '.join(f'{c} — {n}' for c, n in j['courses'].most_common(5)))
    if j['last']:
        fio, course, when = j['last']
        out.append(f'🕒 Последняя выдача: {fio} · {course} · {when}')
    elif not j['week_issued']:
        out.append('🕒 Выдач пока не было')

    drops, problems = log_health(log_path)
    out.append('')
    if drops:
        out.append(f'📶 Связь обрывалась {drops} раз — бот восстановился сам')
    if problems:
        out.append('❗️ Требуют внимания:')
        out += [f'• {p}' for p in problems]
    elif not drops:
        out.append('✅ Ошибок в логе нет')
    return '\n'.join(out)


def start_heartbeat(bot, sheet_fn, log_path=None):
    """Фоновый поток: отчёт в группу раз в MONITOR_HOURS часов."""
    import threading

    def loop():
        while True:
            time.sleep(PERIOD)
            target = chat_id()
            if not target:
                continue
            try:
                bot.send_message(target, status(sheet_fn, log_path))
            except Exception as e:
                print(f'мониторинг: не удалось отправить отчёт — {e}')

    threading.Thread(target=loop, daemon=True).start()


def notify_start(bot, log_path=None):
    """Сообщение при запуске — так видно каждое падение и перезапуск."""
    target = chat_id()
    if not target:
        return
    drops, problems = log_health(log_path, limit=2)
    text = f'🔄 Бот запущен · {datetime.now():%d.%m %H:%M}\n🔖 Версия: {version()}'
    if problems:
        text += '\n\nПеред этим в логе было:\n' + '\n'.join(f'• {p}' for p in problems)
    elif drops:
        text += f'\n\nДо перезапуска связь обрывалась {drops} раз.'
    try:
        bot.send_message(target, text)
    except Exception as e:
        print(f'мониторинг: не удалось сообщить о запуске — {e}')
