"""Одноразовый вход в Google: откроет браузер, сохранит разрешение в token.json.

Запуск: .venv/bin/python google_login.py
Входить нужно аккаунтом, у которого есть права редактора на таблицу «Оффбординг».
"""
import os

from google_auth_oauthlib.flow import InstalledAppFlow

from sheets import CREDS, SCOPES, TOKEN

flow = InstalledAppFlow.from_client_secrets_file(CREDS, SCOPES)
creds = flow.run_local_server(port=0, prompt='consent',
                              success_message='Готово! Окно можно закрыть и вернуться в чат.')
with open(TOKEN, 'w') as f:
    f.write(creds.to_json())
os.chmod(TOKEN, 0o600)
print('token.json сохранён')
