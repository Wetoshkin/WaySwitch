"""Выбор бэкенда раскладки под графическую среду.

Wayland только: X11 не поддерживаем. Каждая среда отдаёт раскладку по-своему,
поэтому порядок попыток зависит от среды, а не «кто первый ответил»:

- GNOME — наше расширение Shell, затем IBus (страховка), затем системный хоткей;
- KDE Plasma — D-Bus KWin (org.kde.keyboard), затем системный хоткей;
- прочие среды — только хоткей (эмуляция смены раскладки через uinput),
  авторежим при этом выключен: текущую раскладку прочитать неоткуда.

Явная настройка ``prefer`` сильнее среды.
"""

from __future__ import annotations

import logging

from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.backends.desktop import (
    COMPOSITOR_DESKTOPS,
    HYPRLAND,
    KDE,
    OTHER,
    SWAY,
    detect_desktop,
)

log = logging.getLogger("wayswitch")

# Порядок попыток по среде для prefer = "auto".
BACKEND_ORDERS = {
    "gnome": ["shell", "ibus", "hotkey"],
    # В KDE системный хоткей GNOME не годится: свои раскладки и сочетания.
    KDE: ["kde"],
    SWAY: ["sway"],
    HYPRLAND: ["hyprland"],
    **{d: ["compositor", "hotkey"] for d in COMPOSITOR_DESKTOPS},
}


def order_for(desktop: str, prefer: str) -> list[str]:
    """Какие бэкенды пробовать. Чистая функция — тестируется без сессии."""
    if prefer != "auto":
        return [prefer]
    return list(BACKEND_ORDERS.get(desktop, BACKEND_ORDERS[OTHER]))


def choose_backend(prefer: str, typist, settle_ms: int,
                   desktop: str | None = None, track_hotkey: bool = False) -> LayoutBackend:
    order = order_for(detect_desktop() if desktop is None else desktop, prefer)
    errors = []
    for name in order:
        try:
            if name == "shell":
                from wayswitch.backends.gnome_shell import (
                    GnomeShellBackend,
                    shell_extension_present,
                )

                if not shell_extension_present():
                    raise BackendError("расширение не на шине")
                return GnomeShellBackend(settle_ms)
            if name == "kde":
                from wayswitch.backends.kde_plasma import KdePlasmaBackend, kde_available

                if not kde_available():
                    raise BackendError("KWin не отдаёт org.kde.keyboard")
                return KdePlasmaBackend(settle_ms)
            if name == "ibus":
                from wayswitch.backends.gnome_ibus import GnomeIbusBackend

                return GnomeIbusBackend(settle_ms)
            if name == "sway":
                from wayswitch.backends.sway import SwayBackend

                return SwayBackend(settle_ms)
            if name == "hyprland":
                from wayswitch.backends.hyprland import HyprlandBackend

                return HyprlandBackend(settle_ms)
            if name == "compositor":
                from wayswitch.backends.compositor import CompositorBackend

                return CompositorBackend(typist, settle_ms, track=track_hotkey)
            if name == "hotkey":
                from wayswitch.backends.hotkey import HotkeyBackend

                return HotkeyBackend(typist, settle_ms)
        except Exception as e:  # noqa: BLE001 — любой сбой бэкенда: пробуем следующий
            errors.append(f"{name}: {e}")
            log.info("бэкенд %s недоступен: %s", name, e)
    raise BackendError("нет доступного бэкенда раскладки: " + "; ".join(errors))
