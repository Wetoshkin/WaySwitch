"""Выбор бэкенда: расширение → IBus → хоткей."""

from __future__ import annotations

import logging

from wayswitch.backends.base import BackendError, LayoutBackend

log = logging.getLogger("wayswitch")


def choose_backend(prefer: str, typist, settle_ms: int) -> LayoutBackend:
    order = {"auto": ["shell", "ibus", "hotkey"], "shell": ["shell"], "ibus": ["ibus"],
             "hotkey": ["hotkey"]}[prefer]
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
                return GnomeShellBackend()
            if name == "ibus":
                from wayswitch.backends.gnome_ibus import GnomeIbusBackend

                return GnomeIbusBackend(settle_ms)
            if name == "hotkey":
                from wayswitch.backends.hotkey import HotkeyBackend

                return HotkeyBackend(typist, settle_ms)
        except Exception as e:  # noqa: BLE001 — любой сбой бэкенда: пробуем следующий
            errors.append(f"{name}: {e}")
            log.info("бэкенд %s недоступен: %s", name, e)
    raise BackendError("нет доступного бэкенда раскладки: " + "; ".join(errors))
