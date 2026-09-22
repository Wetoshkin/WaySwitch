"""Фейки для тестов: табличная раскладка us/ru, бэкенд, печатающая машинка."""

from dataclasses import dataclass

from wayswitch import keycodes as kc
from wayswitch.keymap import LayoutSpec, TableKeymap

US, RU = 0, 1

_US_ROWS = [
    (2, "1!"), (3, "2@"), (4, "3#"), (5, "4$"), (6, "5%"), (7, "6^"), (8, "7&"),
    (9, "8*"), (10, "9("), (11, "0)"), (12, "-_"), (13, "=+"),
    (16, "qQ"), (17, "wW"), (18, "eE"), (19, "rR"), (20, "tT"), (21, "yY"), (22, "uU"),
    (23, "iI"), (24, "oO"), (25, "pP"), (26, "[{"), (27, "]}"),
    (30, "aA"), (31, "sS"), (32, "dD"), (33, "fF"), (34, "gG"), (35, "hH"), (36, "jJ"),
    (37, "kK"), (38, "lL"), (39, ";:"), (40, "'\""), (41, "`~"), (43, "\\|"),
    (44, "zZ"), (45, "xX"), (46, "cC"), (47, "vV"), (48, "bB"), (49, "nN"), (50, "mM"),
    (51, ",<"), (52, ".>"), (53, "/?"), (57, "  "),
]
_RU_ROWS = [
    (2, "1!"), (3, '2"'), (4, "3№"), (5, "4;"), (6, "5%"), (7, "6:"), (8, "7?"),
    (9, "8*"), (10, "9("), (11, "0)"), (12, "-_"), (13, "=+"),
    (16, "йЙ"), (17, "цЦ"), (18, "уУ"), (19, "кК"), (20, "еЕ"), (21, "нН"), (22, "гГ"),
    (23, "шШ"), (24, "щЩ"), (25, "зЗ"), (26, "хХ"), (27, "ъЪ"),
    (30, "фФ"), (31, "ыЫ"), (32, "вВ"), (33, "аА"), (34, "пП"), (35, "рР"), (36, "оО"),
    (37, "лЛ"), (38, "дД"), (39, "жЖ"), (40, "эЭ"), (41, "ёЁ"), (43, "\\/"),
    (44, "яЯ"), (45, "чЧ"), (46, "сС"), (47, "мМ"), (48, "иИ"), (49, "тТ"), (50, "ьЬ"),
    (51, "бБ"), (52, "юЮ"), (53, ".,"), (57, "  "),
]
US_TABLE = {code: (pair[0], pair[1]) for code, pair in _US_ROWS}
RU_TABLE = {code: (pair[0], pair[1]) for code, pair in _RU_ROWS}


def make_keymap() -> TableKeymap:
    return TableKeymap([US_TABLE, RU_TABLE])


@dataclass(frozen=True)
class K:
    code: int
    shift: bool = False
    caps: bool = False


def keys_for_text(keymap, text: str, group: int) -> list[K]:
    """Нажатия, которыми текст набирается в данной группе; KeyError, если символа нет."""
    keys = []
    for ch in text:
        r = keymap.key_for(ch, group)
        if r is None:
            raise KeyError(ch)
        keys.append(K(r[0], r[1]))
    return keys


class RecordingTypist:
    """Запоминает события uinput и умеет сказать, что осталось нажатым."""

    def __init__(self, fail_at: int | None = None):
        self.events: list = []
        self.fail_at = fail_at
        self._writes = 0

    def key(self, code: int, value: int) -> None:
        self._writes += 1
        if self.fail_at is not None and self._writes >= self.fail_at:
            raise OSError("uinput write failed")
        self.events.append((code, value))

    def syn(self) -> None:
        self.events.append("syn")

    def stuck(self) -> set[int]:
        held = set()
        for ev in self.events:
            if ev == "syn":
                continue
            code, value = ev
            (held.add if value else held.discard)(code)
        return held

    def taps(self) -> list[tuple[int, bool]]:
        """Нажатия обычных клавиш с признаком «Shift был зажат»."""
        out, shift = [], False
        for ev in self.events:
            if ev == "syn":
                continue
            code, value = ev
            if code in kc.SHIFT_KEYS:
                shift = bool(value)
            elif value == 1:
                out.append((code, shift))
        return out


class RecordingTextTypist:
    def __init__(self):
        self.calls: list = []

    def backspace(self, n: int) -> None:
        self.calls.append(("backspace", n))

    def type_text(self, text: str) -> None:
        self.calls.append(("type", text))


class FakeBackend:
    name = "fake"
    supports_auto = True

    def __init__(self, layouts=None, current=0, fail_switch=False):
        self._layouts = layouts or [LayoutSpec("us"), LayoutSpec("ru")]
        self.current_index = current
        self.set_calls: list[int] = []
        self.fail_switch = fail_switch
        self._callbacks = []

    def layouts(self):
        return list(self._layouts)

    def xkb_options(self):
        return []

    def current(self):
        return self.current_index

    def set(self, index: int) -> None:
        self.set_calls.append(index)
        if not self.fail_switch:
            self.current_index = index

    def wait_applied(self, index: int, timeout: float) -> bool:
        return self.current_index == index

    def on_change(self, cb):
        self._callbacks.append(cb)

    def emit_external_change(self, index: int):
        self.current_index = index
        for cb in self._callbacks:
            cb(index, True)

    def close(self):
        pass
