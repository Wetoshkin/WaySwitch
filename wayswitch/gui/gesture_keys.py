"""Клавиша жеста «Нажатие клавиши»: человеческие имена и разбор нажатия в
диалоге «Клавиша исправления».

Годятся только клавиши из ``keycodes.GESTURE_KEY_NAMES``: буквы и цифры жестом
быть не могут — одиночное нажатие буквы это и есть набранная буква, жест
срабатывал бы на каждом слове.
"""

from __future__ import annotations

from wayswitch.keycodes import (
    GESTURE_KEY_NAMES,
    KEY_BACKSPACE,
    KEY_ENTER,
    KEY_ESC,
    KEY_KPENTER,
    key_name,
)

_SIDES = {
    "leftctrl": "Левый Ctrl", "rightctrl": "Правый Ctrl",
    "leftshift": "Левый Shift", "rightshift": "Правый Shift",
    "leftalt": "Левый Alt", "rightalt": "Правый Alt",
    "leftmeta": "Левый Super", "rightmeta": "Правый Super",
}
_PLAIN = {
    "capslock": "CapsLock", "numlock": "NumLock", "scrolllock": "ScrollLock",
    "pause": "Pause", "insert": "Insert", "menu": "Menu", "compose": "Compose",
    "escape": "Esc", "esc": "Esc", "printscreen": "PrintScreen",
}

# Wayland/X: аппаратный код клавиши в GTK = код evdev + 8.
GDK_KEYCODE_OFFSET = 8


def human_name(name: str) -> str:
    """leftctrl → «Левый Ctrl», f7 → «F7»."""
    name = name.lower()
    if name in _SIDES:
        return _SIDES[name]
    if name in _PLAIN:
        return _PLAIN[name]
    if name.startswith("f") and name[1:].isdigit():
        return name.upper()
    return name


def is_allowed(name: str) -> bool:
    return name.lower() in GESTURE_KEY_NAMES


def capture(evdev_code: int) -> tuple[str, str]:
    """Что значит нажатие в диалоге выбора клавиши.

    ("cancel", "") — Esc, закрыть без изменений;
    ("clear", "") — Backspace, жест «Нажатие клавиши» выключается;
    ("done", "") — Enter, как кнопка «Готово» (без мыши);
    ("key", имя) — подходящая клавиша;
    ("reject", подсказка) — буква, цифра или иная неподходящая клавиша.
    """
    if evdev_code == KEY_ESC:
        return "cancel", ""
    if evdev_code == KEY_BACKSPACE:
        return "clear", ""
    if evdev_code in (KEY_ENTER, KEY_KPENTER):
        return "done", ""
    name = key_name(evdev_code)
    if is_allowed(name):
        return "key", name
    return "reject", "Эта клавиша не подойдёт: её нажатие печатает текст"
