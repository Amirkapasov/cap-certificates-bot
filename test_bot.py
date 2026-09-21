"""Тесты: .venv/bin/python -m unittest test_bot -v

Настоящая таблица, CRM и Telegram не затрагиваются — используется заглушка листа.
"""
import threading
import unittest

import cert_bot
import sheets


class FakeWorksheet:
    """Лист в памяти: колонки A..L, задержки имитируют сеть."""

    def __init__(self, rows):
        self.rows = [list(r) + [''] * (12 - len(r)) for r in rows]
        self.lock = threading.Lock()

    @property
    def row_count(self):
        return sheets.FIRST_ROW + len(self.rows) - 1

    def _idx(self, cell):
        col = ord(cell[0]) - ord('A')
        return int(cell[1:]) - sheets.FIRST_ROW, col

    def get(self, _range):
        return [r[:9] for r in self.rows]

    def acell(self, cell):
        i, c = self._idx(cell)
        return type('Cell', (), {'value': self.rows[i][c]})

    def update_acell(self, cell, value):
        i, c = self._idx(cell)
        threading.Event().wait(0.01)     # пауза: сюда и попадает гонка
        self.rows[i][c] = value

    def batch_update(self, updates, value_input_option=None):
        for u in updates:
            rng = u['range'].split(':')[0]
            i, c = self._idx(rng)
            for k, v in enumerate(u['values'][0]):
                self.rows[i][c + k] = v


def make_sheet(free=3):
    book = sheets.ApiSheet.__new__(sheets.ApiSheet)   # без обращения к Google
    rows = [[f'CAP-TEST{i:02d}', '', '', '', '', '', '', '', 'Свободен'] for i in range(free)]
    book.ws = FakeWorksheet(rows)
    return book


class TestIssue(unittest.TestCase):
    def test_writes_row(self):
        book = make_sheet()
        res = book.issue('Иванов Иван', 'Python', '01.01.2026', mentor='Алия', letter=True)
        row = book.ws.rows[0]
        self.assertEqual(res['code'], 'CAP-TEST00')
        self.assertEqual(row[1], 'Иванов Иван')
        self.assertEqual(row[2], 'Алия')
        self.assertEqual(row[4], True)          # ✓ сертификат
        self.assertEqual(row[6:9], ['Python', '01.01.2026', 'Действителен'])
        self.assertEqual(row[11], True)         # ✓ благодарственное письмо

    def test_parallel_mentors_get_different_codes(self):
        """Главное: одновременная выдача не выдаёт один код двоим."""
        book = make_sheet(free=6)
        codes, errors = [], []

        def issue(name):
            try:
                codes.append(book.issue(name, 'Python', '01.01.2026')['code'])
            except Exception as e:                       # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=issue, args=(f'Ученик {i}',)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(codes), len(set(codes)), f'коды повторились: {codes}')
        names = [r[1] for r in book.ws.rows]
        self.assertEqual(len(names), len(set(names)), 'ФИО затёрли друг друга')

    def test_skips_row_taken_by_hand(self):
        """Если строку заняли руками, бот берёт следующую."""
        book = make_sheet(free=2)
        original = book.ws.update_acell

        def steal(cell, value):
            original(cell, value)
            if cell == 'B2':                # «человек» перезаписал ту же ячейку
                book.ws.rows[0][1] = 'Кто-то другой'

        book.ws.update_acell = steal
        res = book.issue('Иванов Иван', 'Python', '01.01.2026')
        self.assertEqual(res['code'], 'CAP-TEST01')
        self.assertEqual(book.ws.rows[0][1], 'Кто-то другой')
        self.assertEqual(book.ws.rows[1][1], 'Иванов Иван')

    def test_no_free_codes(self):
        book = make_sheet(free=0)
        with self.assertRaises(RuntimeError):
            book.issue('Иванов Иван', 'Python', '01.01.2026')


class TestParsing(unittest.TestCase):
    def test_line_formats(self):
        cases = {
            'Иванов Иван': (None, 'Иванов Иван', '', ''),
            'Иванов Иван / Иванова Мария': (None, 'Иванов Иван', 'Иванова Мария', ''),
            'Қапасов Әмір; Қапасова Айгүл; a@b.kz': ('', 'Қапасов Әмір', 'Қапасова Айгүл', 'a@b.kz'),
            'CAP-G2A73X Ким Валерия / Ким Анна / m@mail.ru':
                ('CAP-G2A73X', 'Ким Валерия', 'Ким Анна', 'm@mail.ru'),
            'Сидоров-Иванов Олег - Сидорова Ольга':
                (None, 'Сидоров-Иванов Олег', 'Сидорова Ольга', ''),
        }
        for line, expected in cases.items():
            code, student, parent, email = cert_bot.parse_line(line)
            exp_code, exp_student, exp_parent, exp_email = expected
            if exp_code:
                self.assertEqual(code, exp_code, line)
            self.assertEqual(student, exp_student, line)
            self.assertEqual(parent, exp_parent, line)
            self.assertEqual(email, exp_email, line)


class TestMentorMatch(unittest.TestCase):
    OPTIONS = ['Абильмансур', 'Амир Капасов', 'Амир Кабиев', 'Кұндыз', 'Мөлдір',
               'Диана', 'Диана Е', 'Диана Н', 'Каракат']

    def test_exact_and_fuzzy(self):
        for text, expected in [('Абильмансур', 'Абильмансур'), ('кундыз', 'Кұндыз'),
                               ('молдир', 'Мөлдір'), ('амир кап', 'Амир Капасов'),
                               ('КАРАКАТ', 'Каракат'), ('диана е', 'Диана Е')]:
            name, _ = cert_bot.match_mentor(text, self.OPTIONS)
            self.assertEqual(name, expected, text)

    def test_exact_name_wins_over_longer(self):
        """«диана» — это Диана, а не Диана Е: точное совпадение важнее."""
        name, _ = cert_bot.match_mentor('диана', self.OPTIONS)
        self.assertEqual(name, 'Диана')

    def test_ambiguous_returns_options(self):
        name, close = cert_bot.match_mentor('диан', self.OPTIONS)
        self.assertIsNone(name)
        self.assertEqual(set(close), {'Диана', 'Диана Е', 'Диана Н'})

    def test_unknown(self):
        name, _ = cert_bot.match_mentor('Неизвестный', self.OPTIONS)
        self.assertIsNone(name)


if __name__ == '__main__':
    unittest.main()
