"""Отправка письма родителю с сертификатами через Gmail (тот же аккаунт, что и таблица).

Текст писем — в email_templates.json, его можно править без правки кода.
"""
import base64
import json
import mimetypes
import os
import re
from email.message import EmailMessage

from sheets import oauth_creds

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(BASE, 'email_templates.json')
LOG = os.path.join(BASE, 'emails.csv')
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')


def load_templates():
    with open(TEMPLATES, encoding='utf-8') as f:
        return json.load(f)


def _key(text):
    return re.sub(r'[^a-zа-яё]', '', text.casefold())


def next_courses(t, course, done):
    """Курсы для скидки: свой список под курс, минус уже пройденные."""
    by_course = t.get('next_courses_by_course', {})
    base = t.get('next_courses', [])
    ck = _key(course)
    for name, courses in by_course.items():
        if _key(name) in ck:
            base = courses
            break
    passed = {_key(d) for d in done}
    out = []
    for c in base:
        key = _key(c)
        if not any(key in p or p in key for p in passed if p):
            out.append(c)
    return out or base


def build_text(lang, student, course, items, links):
    """items — [(тип, курс)], где тип: 'sert' или 'blago'."""
    t = load_templates()
    tpl = t.get(lang, t['ru'])
    labels = t['labels'].get(lang, t['labels']['ru'])
    lines = ['— ' + labels[kind].format(course=name) for kind, name in items]
    done = [name for kind, name in items if kind == 'sert']
    return (tpl['subject'].format(student=student, course=course),
            tpl['body'].format(student=student, course=course,
                               attachments='\n'.join(lines),
                               links='\n'.join(links),
                               next_courses=', '.join(next_courses(t, course, done))))


def make_message(to, subject, body, files, sender='me'):
    msg = EmailMessage()
    msg['To'] = to
    msg['Subject'] = subject
    msg.set_content(body)
    for path in files:
        ctype, _ = mimetypes.guess_type(path)
        maintype, subtype = (ctype or 'application/octet-stream').split('/', 1)
        with open(path, 'rb') as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                               filename=os.path.basename(path))
    return msg


def send(to, subject, body, files):
    """Отправляет письмо от имени вошедшего аккаунта (адрес Gmail подставит сам)."""
    from googleapiclient.discovery import build

    service = build('gmail', 'v1', credentials=oauth_creds(), cache_discovery=False)
    msg = make_message(to, subject, body, files)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId='me', body={'raw': raw}).execute()
    log(to, subject)


def log(to, subject):
    new = not os.path.exists(LOG)
    from datetime import datetime
    with open(LOG, 'a', encoding='utf-8') as f:
        if new:
            f.write('когда;кому;тема\n')
        f.write(f'{datetime.now():%d.%m.%Y %H:%M};{to};{subject}\n')
