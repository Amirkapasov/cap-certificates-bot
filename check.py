"""Проверка настроек перед запуском: .venv/bin/python check.py

Ничего не меняет и не отправляет — только читает.
"""
import json
import os
import sys

OK, FAIL, WARN = '✅', '❌', '⚠️ '
problems = 0


def report(ok, title, detail='', warn=False):
    global problems
    mark = WARN if warn else (OK if ok else FAIL)
    if not ok and not warn:
        problems += 1
    print(f'{mark} {title}' + (f' — {detail}' if detail else ''))


def main():
    import cert_bot  # подтягивает .env и конфиг

    print('— настройки —')
    report(bool(cert_bot.TOKEN), 'токен бота', 'нет CERT_BOT_TOKEN в .env' if not cert_bot.TOKEN else '')
    report(True, 'админы', ', '.join(map(str, cert_bot.ADMINS)) or 'доступ у всех (CERT_ADMINS пуст)',
           warn=not cert_bot.ADMINS)

    print('\n— шаблоны и шрифты —')
    cfg = cert_bot.CFG
    missing = [t['file'] for t in cfg['templates'].values() if not os.path.exists(t['file'])]
    report(not missing, f'шаблоны ({len(cfg["templates"])})', ', '.join(missing))
    bad_fonts = [f['path'] for f in cfg['fonts'].values() if not os.path.exists(f['path'])]
    report(not bad_fonts, 'шрифты', ', '.join(bad_fonts))

    print('\n— Telegram —')
    try:
        me = cert_bot.bot.get_me()
        report(True, 'бот', f'@{me.username}')
    except Exception as e:
        report(False, 'бот', str(e)[:80])

    print('\n— Google-таблица —')
    if not cert_bot.USE_SHEET:
        report(False, 'таблица', 'не настроена: нет token.json или CERT_SHEET_ID', warn=True)
    else:
        try:
            book = cert_bot.sheet()
            s = book.stats()
            report(True, 'таблица', f'лист «{book.ws.title}», свободно {s["свободно"]}, выдано {s["выдано"]}')
            mentors = book.mentors()
            hidden = len(cert_bot.HIDDEN)
            report(bool(mentors), 'список менторов', f'{len(mentors)} имён, скрыто в кнопках {hidden}')
        except Exception as e:
            report(False, 'таблица', str(e)[:120])

    print('\n— Альфа-CRM —')
    if not cert_bot.USE_CRM:
        report(False, 'CRM', 'не настроена (ALFA_HOST / ALFA_API_KEY)', warn=True)
    else:
        try:
            import crm
            crm.token()
            report(True, 'CRM', f'{os.environ["ALFA_HOST"]}, филиал {crm._branch()}')
        except Exception as e:
            report(False, 'CRM', str(e)[:120])

    print('\n— почта —')
    try:
        scopes = json.load(open('token.json')).get('scopes', [])
    except Exception:
        scopes = []
    gmail = any('gmail.send' in s for s in scopes)
    report(gmail, 'разрешение на отправку писем',
           'нет — запустите google_login.py' if not gmail else 'gmail.send')
    try:
        import mailer
        t = mailer.load_templates()
        langs = [k for k in t if k in ('ru', 'kz', 'en')]
        report(len(langs) == 3, 'шаблоны писем', ', '.join(langs))
    except Exception as e:
        report(False, 'шаблоны писем', str(e)[:80])

    print()
    if problems:
        print(f'Проблем: {problems}. Бот запустится, но часть функций будет недоступна.')
        sys.exit(1)
    print('Всё готово — можно запускать: .venv/bin/python cert_bot.py')


if __name__ == '__main__':
    main()
