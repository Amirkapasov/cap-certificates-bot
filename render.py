"""Наложение ФИО, № кода, даты и QR на шаблоны сертификатов CAP Education."""
import json
import os

from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, 'config.json')
ANCHORS = {'center': 'ms', 'left': 'ls', 'right': 'rs'}  # y — базовая линия текста


def load_config():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        return json.load(f)


_font_cache = {}


def _font_file(spec):
    """Путь к шрифту: основной (macOS) или первый доступный запасной (Linux-сервер)."""
    key = spec['path']
    if key in _font_cache:
        return _font_cache[key]
    for path, index in [(spec['path'], spec.get('index', 0))] + \
                       [(p, 0) for p in spec.get('fallbacks', [])]:
        if os.path.exists(path):
            _font_cache[key] = (path, index)
            return _font_cache[key]
    raise FileNotFoundError(
        f'не найден шрифт {spec["path"]} и запасные варианты {spec.get("fallbacks", [])}')


def _font(cfg, name, px):
    path, index = _font_file(cfg['fonts'][name])
    return ImageFont.truetype(path, px, index=index)


def _fit(draw, text, cfg, font_name, px, max_px):
    """Уменьшаем кегль, пока длинное ФИО не влезет в линию."""
    while px > 10:
        font = _font(cfg, font_name, px)
        if draw.textlength(text, font=font) <= max_px:
            return font
        px -= 2
    return _font(cfg, font_name, 10)


def render(template_key, values, out_path, config=None):
    """values: {'name': ..., 'number': 'CAP-XXXXXX', 'date': '18.09.2026', 'qr': 'путь.png'}"""
    cfg = config or load_config()
    tpl = cfg['templates'][template_key]
    path = os.path.join(BASE, tpl['file'])
    if not os.path.exists(path):
        raise FileNotFoundError(tpl['file'])

    img = Image.open(path).convert('RGB')
    w, h = img.size
    draw = ImageDraw.Draw(img)

    for key, field in tpl['fields'].items():
        value = values.get(key)

        if field.get('type') == 'image' and not value:
            # QR не нужен (бонусный сертификат) — закрываем картинку-заглушку фоном
            x0, y0, x1, y1 = field['box']
            box = (round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h))
            bg = img.getpixel((max(0, box[0] - 25), (box[1] + box[3]) // 2))
            draw.rectangle(box, fill=bg)
            continue
        if not value:
            continue

        if field.get('type') == 'image':
            x0, y0, x1, y1 = field['box']
            box = (round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h))
            pic = Image.open(value).convert('RGB').resize(
                (box[2] - box[0], box[3] - box[1]), Image.LANCZOS)
            img.paste(pic, box[:2])
            continue

        text = field.get('prefix', '') + str(value).strip()
        font = _fit(draw, text, cfg, field['font'], round(field['size'] * h),
                    field.get('max_width', 1.0) * w)
        draw.text((field['x'] * w, field['y'] * h), text, font=font,
                  fill=field['color'], anchor=ANCHORS[field.get('align', 'center')])

    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    img.save(out_path, 'PNG')
    return out_path
