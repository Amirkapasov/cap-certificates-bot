"""Мониторинг бота: статус по запросу и отчёт в группу раз в N часов.

Куда слать отчёты, задаётся командой /monitor_here прямо в группе —
адрес чата сохраняется в monitor_chat.txt, правка .env не нужна.
"""
import os
import subprocess
import time
from datetime import date, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CHAT_FILE = os.path.join(BASE, 'monitor_chat.txt')
STARTED = time.time()
PERIOD = int(os.getenv('MONITOR_HOURS', '4')) * 3600


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


def count_today(path, column=None):
    """Сколько строк в журнале за сегодня."""
    if not os.path.exists(path):
        return 0
    today = date.today().strftime('%d.%m.%Y')
    return sum(1 for line in open(path, encoding='utf-8') if today in line)


def recent_errors(log_path, limit=3):
    """Последние строки с ошибками из лога бота."""
    if not os.path.exists(log_path):
        return []
    try:
        lines = open(log_path, encoding='utf-8', errors='replace').read().splitlines()[-400:]
    except OSError:
        return []
    bad = [l for l in lines if 'Error' in l or 'Traceback' in l or 'error' in l.lower()]
    return [l.strip()[:90] for l in bad[-limit:]]


def status(sheet_fn=None, log_path=None):
    """Короткий отчёт о состоянии бота."""
    lines = [f'🟢 Бот работает · {datetime.now():%d.%m %H:%M}',
             f'Аптайм: {uptime()}',
             f'Версия: {version()}']

    try:
        s = sheet_fn().stats() if sheet_fn else None
        lines.append(f'Таблица: свободно {s["свободно"]}, выдано {s["выдано"]}' if s
                     else 'Таблица: не подключена')
        if s and s['свободно'] < 50:
            lines.append(f'⚠️ Коды заканчиваются: осталось {s["свободно"]}')
    except Exception as e:
        lines.append(f'❌ Таблица недоступна: {str(e)[:60]}')

    lines.append(f'Сегодня: сертификатов {count_today(os.path.join(BASE, "issued.csv"))}, '
                 f'писем {count_today(os.path.join(BASE, "emails.csv"))}')

    errors = recent_errors(log_path or os.path.join(BASE, 'logs', 'bot.log'))
    if errors:
        lines.append('Последние ошибки в логе:')
        lines += [f'• {e}' for e in errors]
    return '\n'.join(lines)


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
    errors = recent_errors(log_path or os.path.join(BASE, 'logs', 'bot.log'), limit=2)
    text = f'🔄 Бот запущен · {datetime.now():%d.%m %H:%M}\nВерсия: {version()}'
    if errors:
        text += '\n\nПеред этим в логе было:\n' + '\n'.join(f'• {e}' for e in errors)
    try:
        bot.send_message(target, text)
    except Exception as e:
        print(f'мониторинг: не удалось сообщить о запуске — {e}')
