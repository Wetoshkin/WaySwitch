"""Связь GUI с демоном по D-Bus: статус, сигналы, вызовы.

Все обращения к службе асинхронные: окно не ждёт D-Bus в главном цикле.
Ответы привязываются к запросу и владельцу имени; после смены владельца,
закрытия приложения или более нового запроса устаревший ответ отбрасывается.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from wayswitch.dbus_service import BUS_NAME, INTERFACE, OBJECT_PATH

log = logging.getLogger("wayswitch.gui")

STATUS_TIMEOUT_MS = 2000
CALL_TIMEOUT_MS = 3000


class DaemonProxy:
    def __init__(self, on_status: Callable[[dict | None], None],
                 on_corrected: Callable[[str, str, bool], None]):
        self.on_status = on_status
        self.on_corrected = on_corrected
        self.status: dict | None = None
        self._proxy = None
        self._starting = False
        self._closed = False
        self._cancellable = None
        self._owner = None
        self._owner_generation = 0
        self._signal_handler = 0
        self._owner_handler = 0
        # Номер последнего запроса/события статуса. Ответ с устаревшим номером
        # не должен затирать более новый статус.
        self._status_request = 0

    def start(self) -> None:
        """Создать прокси асинхронно; GetStatus будет запрошен после готовности."""
        if self._closed or self._proxy is not None or self._starting:
            return
        from gi.repository import Gio

        self._starting = True
        self._cancellable = Gio.Cancellable()
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, BUS_NAME, OBJECT_PATH,
            INTERFACE, self._cancellable, self._on_proxy_ready, None)

    def _on_proxy_ready(self, _source, result, _user_data) -> None:
        from gi.repository import Gio

        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except Exception as e:  # noqa: BLE001
            self._starting = False
            if not self._closed:
                log.warning("D-Bus proxy: %s", e)
                self.refresh()
            return
        self._starting = False
        if self._closed:
            return
        self._proxy = proxy
        self._owner = proxy.get_name_owner()
        self._owner_generation += 1
        self._signal_handler = proxy.connect("g-signal", self._on_signal)
        self._owner_handler = proxy.connect("notify::g-name-owner", self._on_owner_changed)
        self.refresh()

    def _on_owner_changed(self, proxy, _pspec) -> None:
        if self._closed:
            return
        owner = proxy.get_name_owner()
        if owner == self._owner:
            return
        self._owner = owner
        self._owner_generation += 1
        self._status_request += 1
        if self.status is not None:
            self.status = None
            self.on_status(None)
        if owner is not None:
            self.refresh()

    def refresh(self, on_done: Callable[[dict | None], None] | None = None) -> None:
        """Запросить GetStatus, не блокируя главный цикл.

        on_done получает применённый статус; для устаревшего ответа — текущий
        статус, уже установленный более новым сигналом или запросом.
        """
        if self._closed:
            return
        self._status_request += 1
        request = self._status_request
        proxy = self._proxy
        owner = proxy.get_name_owner() if proxy is not None else None
        if owner is None:
            self.status = None
            self.on_status(None)
            if on_done is not None:
                on_done(None)
            return
        from gi.repository import Gio

        try:
            proxy.call(
                "GetStatus", None, Gio.DBusCallFlags.NONE, STATUS_TIMEOUT_MS,
                self._cancellable, self._on_status_reply,
                (request, owner, self._owner_generation, on_done))
        except Exception as e:  # noqa: BLE001
            log.warning("GetStatus: %s", e)
            self.status = None
            self.on_status(None)
            if on_done is not None:
                on_done(None)

    def _on_status_reply(self, proxy, result, context) -> None:
        request, owner, generation, on_done = context
        status = None
        error = None
        try:
            reply = proxy.call_finish(result)
            (status,) = reply.unpack()
        except Exception as e:  # noqa: BLE001
            error = e
        if self._closed:
            return
        if (request != self._status_request or generation != self._owner_generation
                or proxy.get_name_owner() != owner):
            if error is not None:
                log.warning("GetStatus: %s", error)
            if on_done is not None:
                on_done(self.status)
            return
        if error is not None:
            log.warning("GetStatus: %s", error)
            status = None
        self.status = status
        self.on_status(status)
        if on_done is not None:
            on_done(status)

    def _on_signal(self, proxy, sender: str, signal: str, params) -> None:
        if self._closed or sender != proxy.get_name_owner():
            return
        if signal == "StatusChanged":
            self._status_request += 1
            (self.status,) = params.unpack()
            self.on_status(self.status)
        elif signal == "Corrected":
            self.on_corrected(*params.unpack())

    def call(self, method: str, on_done: Callable[[object], None] | None = None) -> bool:
        """Отправить метод без ожидания ответа.

        Возвращает True, если запрос отправлен. on_done вызывается только по
        завершении реального D-Bus-вызова; если владельца нет, обратного вызова
        не будет — вызывающий не должен показывать ошибку до ответа службы.
        Fire-and-forget-вызовы (on_done=None) ответ не обрабатывают.
        """
        if self._closed:
            return False
        proxy = self._proxy
        if proxy is None:
            return False
        owner = proxy.get_name_owner()
        if owner is None:
            return False
        from gi.repository import Gio

        try:
            proxy.call(
                method, None, Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS,
                self._cancellable, self._on_call_reply,
                (method, owner, self._owner_generation, on_done))
        except Exception as e:  # noqa: BLE001
            log.warning("%s: %s", method, e)
            if on_done is not None:
                on_done(None)
            return False
        return True

    def _on_call_reply(self, proxy, result, context) -> None:
        method, owner, generation, on_done = context
        value = None
        error = None
        try:
            reply = proxy.call_finish(result)
            value = reply.unpack() if reply is not None else None
        except Exception as e:  # noqa: BLE001
            error = e
        if (self._closed or generation != self._owner_generation
                or proxy.get_name_owner() != owner):
            return
        if error is not None:
            log.warning("%s: %s", method, error)
            value = None
        if on_done is not None:
            on_done(value)

    def close(self) -> None:
        """Отменить запросы и отключить сигналы при завершении приложения."""
        if self._closed:
            return
        self._closed = True
        self._status_request += 1
        self._owner_generation += 1
        self._owner = None
        if self._cancellable is not None:
            self._cancellable.cancel()
        proxy = self._proxy
        self._proxy = None
        self._starting = False
        if proxy is not None:
            for handler in (self._signal_handler, self._owner_handler):
                if handler:
                    try:
                        proxy.disconnect(handler)
                    except Exception:  # noqa: BLE001
                        pass
        self._signal_handler = 0
        self._owner_handler = 0
