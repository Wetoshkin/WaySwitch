"""Охрана сессии: блокировка экрана и сон.

Пока экран заблокирован, исправлять нельзя — иначе набранное до блокировки
могло бы уйти в поле пароля. Источники: org.gnome.ScreenSaver (мгновенно)
и logind (LockedHint/Active своей сессии, PrepareForSleep).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

log = logging.getLogger("wayswitch")


class SessionGuard:
    def __init__(self, on_locked: Callable[[bool], None], on_sleep: Callable[[bool], None]):
        self.on_locked = on_locked
        self.on_sleep = on_sleep
        self.locked = False
        self._screensaver_active = False
        self._logind_locked = False

    def start(self) -> None:
        from gi.repository import Gio, GLib

        try:
            self._ss = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, "org.gnome.ScreenSaver",
                "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", None)
            self._ss.connect("g-signal", self._on_ss_signal)
            self._screensaver_active = bool(self._ss.call_sync("GetActive", None, 0, 1000,
                                                                None).unpack()[0])
        except Exception as e:  # noqa: BLE001
            log.warning("org.gnome.ScreenSaver недоступен: %s", e)
        try:
            system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            manager = Gio.DBusProxy.new_sync(system, Gio.DBusProxyFlags.NONE, None,
                                             "org.freedesktop.login1", "/org/freedesktop/login1",
                                             "org.freedesktop.login1.Manager", None)
            manager.connect("g-signal", self._on_manager_signal)
            (path,) = manager.call_sync("GetSessionByPID", GLib.Variant("(u)", (os.getpid(),)),
                                        0, 1000, None).unpack()
            self._session = Gio.DBusProxy.new_sync(system, Gio.DBusProxyFlags.NONE, None,
                                                   "org.freedesktop.login1", path,
                                                   "org.freedesktop.login1.Session", None)
            self._session.connect("g-properties-changed", self._on_session_props)
            self._read_session()
        except Exception as e:  # noqa: BLE001
            log.warning("logind недоступен, охрана только по ScreenSaver: %s", e)
        self._update()

    def _read_session(self) -> None:
        locked = self._session.get_cached_property("LockedHint")
        active = self._session.get_cached_property("Active")
        self._logind_locked = bool(locked and locked.unpack()) or bool(active is not None
                                                                       and not active.unpack())

    def _on_ss_signal(self, _p, _s, signal: str, params) -> None:
        if signal == "ActiveChanged":
            self._screensaver_active = bool(params.unpack()[0])
            self._update()

    def _on_session_props(self, _p, _changed, _invalidated) -> None:
        self._read_session()
        self._update()

    def _on_manager_signal(self, _p, _s, signal: str, params) -> None:
        if signal == "PrepareForSleep":
            self.on_sleep(bool(params.unpack()[0]))

    def _update(self) -> None:
        locked = self._screensaver_active or self._logind_locked
        if locked != self.locked:
            self.locked = locked
            log.info("сессия %s", "заблокирована" if locked else "активна")
            self.on_locked(locked)
