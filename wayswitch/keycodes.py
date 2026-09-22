"""Коды клавиш Linux (input-event-codes.h), нужные демону.

Константы продублированы, чтобы чистая логика не зависела от python-evdev
и тесты запускались на любой ОС.
"""

KEY_ESC = 1
KEY_1, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6, KEY_7, KEY_8, KEY_9, KEY_0 = range(2, 12)
KEY_MINUS = 12
KEY_EQUAL = 13
KEY_BACKSPACE = 14
KEY_TAB = 15
KEY_Q, KEY_W, KEY_E, KEY_R, KEY_T, KEY_Y, KEY_U, KEY_I, KEY_O, KEY_P = range(16, 26)
KEY_LEFTBRACE = 26
KEY_RIGHTBRACE = 27
KEY_ENTER = 28
KEY_LEFTCTRL = 29
KEY_A, KEY_S, KEY_D, KEY_F, KEY_G, KEY_H, KEY_J, KEY_K, KEY_L = range(30, 39)
KEY_SEMICOLON = 39
KEY_APOSTROPHE = 40
KEY_GRAVE = 41
KEY_LEFTSHIFT = 42
KEY_BACKSLASH = 43
KEY_Z, KEY_X, KEY_C, KEY_V, KEY_B, KEY_N, KEY_M = range(44, 51)
KEY_COMMA = 51
KEY_DOT = 52
KEY_SLASH = 53
KEY_RIGHTSHIFT = 54
KEY_KPASTERISK = 55
KEY_LEFTALT = 56
KEY_SPACE = 57
KEY_CAPSLOCK = 58
KEY_F1, KEY_F2, KEY_F3, KEY_F4, KEY_F5, KEY_F6, KEY_F7, KEY_F8, KEY_F9, KEY_F10 = range(59, 69)
KEY_NUMLOCK = 69
KEY_SCROLLLOCK = 70
KEY_KP7, KEY_KP8, KEY_KP9, KEY_KPMINUS, KEY_KP4, KEY_KP5, KEY_KP6, KEY_KPPLUS = range(71, 79)
KEY_KP1, KEY_KP2, KEY_KP3, KEY_KP0, KEY_KPDOT = range(79, 84)
KEY_102ND = 86
KEY_F11 = 87
KEY_F12 = 88
KEY_KPENTER = 96
KEY_RIGHTCTRL = 97
KEY_KPSLASH = 98
KEY_RIGHTALT = 100
KEY_HOME = 102
KEY_UP = 103
KEY_PAGEUP = 104
KEY_LEFT = 105
KEY_RIGHT = 106
KEY_END = 107
KEY_DOWN = 108
KEY_PAGEDOWN = 109
KEY_INSERT = 110
KEY_DELETE = 111
KEY_PAUSE = 119
KEY_LEFTMETA = 125
KEY_RIGHTMETA = 126
KEY_COMPOSE = 127
KEY_F13, KEY_F14, KEY_F15, KEY_F16, KEY_F17, KEY_F18 = range(183, 189)
KEY_F19, KEY_F20, KEY_F21, KEY_F22, KEY_F23, KEY_F24 = range(189, 195)

BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112
BTN_TASK = 0x117

# Наборы клавиш, которыми оперирует буфер и контроллер.
SHIFT_KEYS = frozenset({KEY_LEFTSHIFT, KEY_RIGHTSHIFT})
CTRL_KEYS = frozenset({KEY_LEFTCTRL, KEY_RIGHTCTRL})
ALT_KEYS = frozenset({KEY_LEFTALT, KEY_RIGHTALT})
META_KEYS = frozenset({KEY_LEFTMETA, KEY_RIGHTMETA})
COMMAND_MODIFIERS = CTRL_KEYS | ALT_KEYS | META_KEYS
MODIFIER_KEYS = SHIFT_KEYS | COMMAND_MODIFIERS
DIGIT_KEYS = frozenset(range(KEY_1, KEY_0 + 1))
# Клавиши, которые входят в слово помимо букв: цифры, дефис, равно.
WORD_EXTRA_KEYS = DIGIT_KEYS | {KEY_MINUS, KEY_EQUAL}
# Клавиши, после которых буфер бессмысленен: каретка переехала или ввод завершён.
RESET_KEYS = frozenset(
    {
        KEY_ENTER, KEY_KPENTER, KEY_TAB, KEY_ESC,
        KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT,
        KEY_HOME, KEY_END, KEY_PAGEUP, KEY_PAGEDOWN,
        KEY_DELETE, KEY_INSERT,
    }
    | set(range(KEY_F1, KEY_F10 + 1))
    | {KEY_F11, KEY_F12}
    | set(range(KEY_F13, KEY_F24 + 1))
)


def is_pointer_button(code: int) -> bool:
    """Кнопка мыши/тачпада: BTN_LEFT..BTN_TASK."""
    return BTN_LEFT <= code <= BTN_TASK


# Имена клавиш для конфига и разбора системного хоткея.
KEY_NAMES: dict[str, int] = {
    "space": KEY_SPACE, "tab": KEY_TAB, "enter": KEY_ENTER, "return": KEY_ENTER,
    "esc": KEY_ESC, "escape": KEY_ESC, "backspace": KEY_BACKSPACE,
    "insert": KEY_INSERT, "delete": KEY_DELETE, "home": KEY_HOME, "end": KEY_END,
    "pause": KEY_PAUSE, "scrolllock": KEY_SCROLLLOCK, "capslock": KEY_CAPSLOCK,
    "menu": KEY_COMPOSE, "compose": KEY_COMPOSE,
    "leftshift": KEY_LEFTSHIFT, "rightshift": KEY_RIGHTSHIFT, "shift": KEY_LEFTSHIFT,
    "leftctrl": KEY_LEFTCTRL, "rightctrl": KEY_RIGHTCTRL, "ctrl": KEY_LEFTCTRL,
    "control": KEY_LEFTCTRL,
    "leftalt": KEY_LEFTALT, "rightalt": KEY_RIGHTALT, "alt": KEY_LEFTALT,
    "leftmeta": KEY_LEFTMETA, "rightmeta": KEY_RIGHTMETA, "super": KEY_LEFTMETA,
    "meta": KEY_LEFTMETA, "win": KEY_LEFTMETA,
    "grave": KEY_GRAVE, "minus": KEY_MINUS, "equal": KEY_EQUAL,
    "semicolon": KEY_SEMICOLON, "apostrophe": KEY_APOSTROPHE, "comma": KEY_COMMA,
    "period": KEY_DOT, "slash": KEY_SLASH, "backslash": KEY_BACKSLASH,
    "bracketleft": KEY_LEFTBRACE, "bracketright": KEY_RIGHTBRACE,
}
KEY_NAMES.update({f"f{i}": code for i, code in enumerate(range(KEY_F1, KEY_F10 + 1), 1)})
KEY_NAMES.update({"f11": KEY_F11, "f12": KEY_F12})
KEY_NAMES.update({chr(ord("a") + i): c for i, c in enumerate(
    [KEY_A, KEY_B, KEY_C, KEY_D, KEY_E, KEY_F, KEY_G, KEY_H, KEY_I, KEY_J, KEY_K, KEY_L,
     KEY_M, KEY_N, KEY_O, KEY_P, KEY_Q, KEY_R, KEY_S, KEY_T, KEY_U, KEY_V, KEY_W, KEY_X,
     KEY_Y, KEY_Z])})
KEY_NAMES.update({str(i): c for i, c in zip([1, 2, 3, 4, 5, 6, 7, 8, 9, 0],
                                            range(KEY_1, KEY_0 + 1), strict=False)})

_NAME_BY_CODE = {code: name for name, code in KEY_NAMES.items()}
# Для модификаторов оставляем полное имя (leftshift, а не shift).
for _name in ("leftshift", "rightshift", "leftctrl", "rightctrl", "leftalt", "rightalt",
              "leftmeta", "rightmeta", "enter", "esc", "period"):
    _NAME_BY_CODE[KEY_NAMES[_name]] = _name


def key_name(code: int) -> str:
    """Человекочитаемое имя клавиши; для неизвестных — число."""
    return _NAME_BY_CODE.get(code, str(code))
