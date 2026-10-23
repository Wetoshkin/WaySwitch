"""Какая графическая среда и какой протокол сессии.

Раскладку в Wayland каждая среда отдаёт по-своему: GNOME — через наше
расширение Shell, KDE Plasma — через D-Bus KWin (org.kde.keyboard). Поэтому
бэкенд выбирается по среде, а не «какой первый ответил».

Чистые функции: окружение передаётся словарём, чтобы это тестировалось без
настоящей сессии.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

GNOME = "gnome"
KDE = "kde"
OTHER = "other"

DESKTOP_NAMES = {GNOME: "GNOME", KDE: "KDE Plasma", OTHER: "другая среда"}


def detect_desktop(env: Mapping[str, str] | None = None) -> str:
    """Среда по XDG_CURRENT_DESKTOP.

    Строка бывает составной (``ubuntu:GNOME``, ``KDE``, ``GNOME-Classic:GNOME``),
    поэтому ищем подстроку, а не сравниваем целиком.
    """
    source = os.environ if env is None else env
    raw = (source.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "kde" in raw or "plasma" in raw:
        return KDE
    if "gnome" in raw or "unity" in raw or "budgie" in raw or "pantheon" in raw:
        return GNOME
    return OTHER


def is_wayland(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    if source.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    # Подсказка на случай, если переменная пустая, а сессия всё-таки Wayland.
    return bool(source.get("WAYLAND_DISPLAY"))


def session_summary(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    kind = "Wayland" if is_wayland(source) else (source.get("XDG_SESSION_TYPE") or "неизвестно")
    return f"{DESKTOP_NAMES[detect_desktop(source)]} / {kind}"
