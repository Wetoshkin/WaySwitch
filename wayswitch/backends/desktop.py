"""Какая графическая среда и какой протокол сессии.

Раскладку в Wayland каждая среда отдаёт по-своему: GNOME — через наше
расширение Shell, KDE Plasma — через D-Bus KWin (org.kde.keyboard). Поэтому
бэкенд выбирается по среде, а не «какой первый ответил».

Чистые функции: окружение передаётся словарём, чтобы это тестировалось без
настоящей сессии.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping

GNOME = "gnome"
KDE = "kde"
SWAY = "sway"
HYPRLAND = "hyprland"
XFCE = "xfce"
MATE = "mate"
CINNAMON = "cinnamon"
BUDGIE = "budgie"
LXQT = "lxqt"
OTHER = "other"

DESKTOP_NAMES = {GNOME: "GNOME", KDE: "KDE Plasma", SWAY: "Sway", HYPRLAND: "Hyprland",
                 XFCE: "Xfce", MATE: "MATE", CINNAMON: "Cinnamon", BUDGIE: "Budgie",
                 LXQT: "LXQt", OTHER: "другая среда"}

# Среды, которые в Wayland работают поверх отдельного композитора (labwc,
# Wayfire): раскладка — в его конфиге XKB, переключение — его сочетанием.
COMPOSITOR_DESKTOPS = frozenset({XFCE, MATE, CINNAMON, BUDGIE, LXQT, OTHER})


def _session_env(runner=None) -> dict[str, str]:
    """Окружение графической сессии, даже если мы запущены из голого шелла.

    Демон стартует из сессии и видит переменные сам, а `doctor` из ssh, cron
    или из терминала агента — нет: там `XDG_CURRENT_DESKTOP` пуст, и среда
    определялась как «другая». Спрашиваем менеджер пользователя — он эти
    переменные хранит (systemctl --user show-environment).
    """
    if os.environ.get("XDG_CURRENT_DESKTOP"):
        return dict(os.environ)
    run = subprocess.run if runner is None else runner
    env = dict(os.environ)
    try:
        out = run(["systemctl", "--user", "show-environment"],
                  capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return env
    for line in out.splitlines():
        name, sep, value = line.partition("=")
        if sep and name in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "WAYLAND_DISPLAY",
                            "DISPLAY", "XAUTHORITY", "SWAYSOCK",
                            "HYPRLAND_INSTANCE_SIGNATURE"):
            env.setdefault(name, value)
    return env


def detect_desktop(env: Mapping[str, str] | None = None) -> str:
    """Среда по XDG_CURRENT_DESKTOP.

    Строка бывает составной (``ubuntu:GNOME``, ``KDE``, ``GNOME-Classic:GNOME``),
    поэтому ищем подстроку, а не сравниваем целиком.
    """
    source = _session_env() if env is None else env
    raw = (source.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "kde" in raw or "plasma" in raw:
        return KDE
    if "hyprland" in raw or source.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return HYPRLAND
    if "sway" in raw or source.get("SWAYSOCK"):
        return SWAY
    # Budgie, Cinnamon и прочие пишут себя рядом с GNOME («Budgie:GNOME»),
    # поэтому проверяем их раньше GNOME: gnome-shell у них нет.
    for name in (BUDGIE, CINNAMON, XFCE, MATE, LXQT):
        if name in raw:
            return name
    if "gnome" in raw or "unity" in raw:
        return GNOME
    return OTHER


def is_wayland(env: Mapping[str, str] | None = None) -> bool:
    source = _session_env() if env is None else env
    if source.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    # Подсказка на случай, если переменная пустая, а сессия всё-таки Wayland.
    return bool(source.get("WAYLAND_DISPLAY"))


def session_summary(env: Mapping[str, str] | None = None) -> str:
    source = _session_env() if env is None else env
    kind = "Wayland" if is_wayland(source) else (source.get("XDG_SESSION_TYPE") or "неизвестно")
    return f"{DESKTOP_NAMES[detect_desktop(source)]} / {kind}"
