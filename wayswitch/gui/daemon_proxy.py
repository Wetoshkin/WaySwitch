"""Связь GUI с демоном по D-Bus: статус, сигналы, вызовы."""

from __future__ import annotations

import logging
from collections.abc import Callable

from wayswitch.dbus_service import BUS_NAME, INTERFACE, OBJECT_PATH

log = logging.getLogger("wayswitch.gui")


class DaemonProxy:
    def __init__(self, on_status: Callable[[dict | None], None],
                 on_corrected: Callable[[str, str, bool], None]):
        self.on_status = on_status
        self.on_corrected = on_corrected
        self.status: dict | None = None
        self._proxy = None

    def start(self) -> None:
        from gi.repository import Gio

        self._proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, BUS_NAME, OBJECT_PATH,
            INTERFACE, None)
        self._proxy.connect("g-signal", self._on_signal)
        self._proxy.connect("notify::g-name-owner", lambda *_: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        if self._proxy.get_name_owner() is None:
            self.status = None
        else:
            try:
                (self.status,) = self._proxy.call_sync("GetStatus", None, 0, 2000, None).unpack()
            except Exception as e:  # noqa: BLE001
                log.warning("GetStatus: %s", e)
                self.status = None
        self.on_status(self.status)

    def _on_signal(self, _p, _s, signal: str, params) -> None:
        if signal == "StatusChanged":
            (self.status,) = params.unpack()
            self.on_status(self.status)
        elif signal == "Corrected":
            self.on_corrected(*params.unpack())

    def call(self, method: str):
        if self._proxy is None or self._proxy.get_name_owner() is None:
            return None
        try:
            reply = self._proxy.call_sync(method, None, 0, 3000, None)
            return reply.unpack() if reply is not None else None
        except Exception as e:  # noqa: BLE001
            log.warning("%s: %s", method, e)
            return None
