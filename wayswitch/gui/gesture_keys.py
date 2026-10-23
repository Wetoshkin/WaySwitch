"""Раскладка экранной клавиатуры для выбора клавиши жеста.

Только клавиши из ``keycodes.GESTURE_KEY_NAMES``: буквы и цифры жестом быть не
могут — одиночный тап буквы это и есть набранная буква, жест срабатывал бы на
каждом слове. Тест проверяет, что каждая кнопка здесь входит в этот список.
"""

from __future__ import annotations

from wayswitch.keycodes import GESTURE_KEY_NAMES

# (подпись на кнопке, имя клавиши в конфиге)
ROWS: list[list[tuple[str, str]]] = [
    [("Ctrl", "leftctrl"), ("Ctrl", "rightctrl"), ("Shift", "leftshift"),
     ("Shift", "rightshift"), ("Alt", "leftalt"), ("Alt", "rightalt")],
    [("Super", "leftmeta"), ("Super", "rightmeta"), ("CapsLock", "capslock"),
     ("NumLock", "numlock"), ("ScrollLock", "scrolllock"), ("Pause", "pause")],
    [("F1", "f1"), ("F2", "f2"), ("F3", "f3"), ("F4", "f4"), ("F5", "f5"), ("F6", "f6")],
    [("F7", "f7"), ("F8", "f8"), ("F9", "f9"), ("F10", "f10"), ("F11", "f11"), ("F12", "f12")],
    [("Insert", "insert"), ("Menu", "menu"), ("Esc", "escape")],
]

# Левая и правая клавиша называются одинаково на кнопке, поэтому поясняем.
HINTS = {
    "leftctrl": "левый", "rightctrl": "правый",
    "leftshift": "левый", "rightshift": "правый",
    "leftalt": "левый", "rightalt": "правый",
    "leftmeta": "левый", "rightmeta": "правый",
}


def all_key_names() -> list[str]:
    return [name for row in ROWS for _label, name in row]


def button_label(name: str) -> str:
    """Подпись кнопки с уточнением стороны для парных клавиш."""
    hint = HINTS.get(name)
    if not hint:
        return name
    base = {"leftctrl": "Ctrl", "rightctrl": "Ctrl", "leftshift": "Shift",
            "rightshift": "Shift", "leftalt": "Alt", "rightalt": "Alt",
            "leftmeta": "Super", "rightmeta": "Super"}[name]
    return f"{base} ({hint})"


def is_allowed(name: str) -> bool:
    return name.lower() in GESTURE_KEY_NAMES
