"""Поиск ученика в Альфа-CRM: ФИО родителя и его почта.

Доступы — в .env (ALFA_HOST, ALFA_EMAIL, ALFA_API_KEY, ALFA_BRANCH).
Используется только чтение: карточки в CRM бот не меняет.
"""
import os
import re
import time

import requests

KZ_TO_RU = str.maketrans('әғқңөұүһіё', 'агкноуухие')
_token = {'value': '', 'until': 0}


def enabled():
    return bool(os.getenv('ALFA_HOST') and os.getenv('ALFA_API_KEY'))


def _host():
    return os.environ['ALFA_HOST']


def _branch():
    return os.getenv('ALFA_BRANCH', '1')


def token():
    """Токен живёт около часа, поэтому держим его в памяти."""
    if _token['value'] and time.time() < _token['until']:
        return _token['value']
    r = requests.post(f'https://{_host()}/v2api/auth/login', timeout=30,
                      json={'email': os.environ['ALFA_EMAIL'],
                            'api_key': os.environ['ALFA_API_KEY']})
    r.raise_for_status()
    _token.update(value=r.json()['token'], until=time.time() + 50 * 60)
    return _token['value']


def _post(method, payload, page=0):
    r = requests.post(f'https://{_host()}/v2api/{_branch()}/{method}?page={page}',
                      headers={'X-ALFACRM-TOKEN': token()}, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def norm(text):
    return ' '.join(str(text).casefold().translate(KZ_TO_RU).split())


def _card(item, archived=False):
    email = next((e for e in (item.get('email') or []) if e), '')
    phone = next((p for p in (item.get('phone') or []) if p), '')
    return {'id': item.get('id'), 'student': (item.get('name') or '').strip(),
            'parent': (item.get('legal_name') or '').strip(),
            'email': email.strip(), 'phone': phone.strip(),
            'is_study': bool(item.get('is_study')), 'archived': archived}


def find(name):
    """Ищем ученика по ФИО (порядок слов и казахские буквы не важны)."""
    words = [w for w in re.split(r'\s+', name.strip()) if len(w) > 1]
    if not words:
        return []

    found = {}
    for query in [name.strip()] + words:
        for archived in (False, True):   # выпускников в CRM переносят в архив
            payload = {'name': query}
            if archived:
                payload['removed'] = 1
            try:
                data = _post('customer/index', payload)
            except Exception:
                continue
            for item in data.get('items', []):
                found.setdefault(item['id'], (item, archived))
        if len(found) == 1 and query == name.strip():
            break

    need = [norm(w) for w in words]
    matches = [_card(i, arch) for i, arch in found.values()
               if all(w in norm(i.get('name', '')) for w in need)]
    matches.sort(key=lambda c: (c['archived'], c['student']))
    return matches
