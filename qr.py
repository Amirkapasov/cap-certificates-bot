"""Генерация QR-кода для сертификата (ссылка на страницу проверки)."""
import os

import qrcode

VERIFY_URL = os.getenv('CERT_VERIFY_URL', 'https://capedu.kz/verify.html?id={code}')


def make_qr(code, out_path, box_size=20, border=2):
    img = qrcode.make(VERIFY_URL.format(code=code),
                      box_size=box_size, border=border)
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    img.convert('RGB').save(out_path)
    return out_path


def verify_link(code):
    return VERIFY_URL.format(code=code)
