"""Раскладка и печать через наше расширение GNOME Shell (D-Bus)."""

from __future__ import annotations

import time
from collections.abc import Callable

from wayswitch.backends import gnome_common
from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.keymap import LayoutSpec

BUS_NAME = "ru.siberia.WaySwitch.Shell"
OBJECT_PATH = "/ru/siberia/WaySwitch/Shell"
INTERFACE = "ru.siberia.WaySwitch.Shell"


def shell_extension_present() -> bool:
    # Variant/VariantType живут в пространстве GLib, а не Gio: обращение к ним
    # через Gio даёт AttributeError (кросс-namespace-поиска в gi нет).
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "NameHasOwner", GLib.Variant("(s)", (BUS_NAME,)),
                          GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 1000, None)
    return bool(reply.unpack()[0])


def extension_version() -> int | None:
    """Версия протокола загруженного расширения (None — старое, без GetVersion)."""
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    try:
        reply = bus.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, "GetVersion", None,
                              GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 1000, None)
    except GLib.Error:
        return 1
    return int(reply.unpack()[0])


class GnomeShellBackend(LayoutBackend):
    name = "shell"
    supports_auto = True

    def __init__(self, settle_ms: int = 30):
        from gi.repository import Gio, GLib

        self._GLib = GLib
        self._settle = settle_ms
        self._specs = gnome_common.read_sources()
        self._proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, BUS_NAME, OBJECT_PATH,
            INTERFACE, None)
        if self._proxy.get_name_owner() is None:
            raise BackendError("расширение WaySwitch не активно")
        self._callbacks: list[Callable[[int, bool], None]] = []
        self._expected: int | None = None
        self._proxy.connect("g-signal", self._on_signal)
        # На экране блокировки расширение выключается (нет session-modes) и имя
        # уходит с шины; при разблокировке экспортируется заново. Пока имени
        # нет, LayoutChanged не приходит, поэтому при возвращении владельца
        # переопрашиваем раскладку — пользователь мог сменить её на экране входа.
        self._proxy.connect("notify::g-name-owner", self._on_owner_changed)
        self._current = self._query()
        # Человек добавил или переставил раскладку — индексы сдвинулись.
        try:
            self._sources_settings = gnome_common._settings(gnome_common.SCHEMA_SOURCES)
            self._sources_settings.connect("changed::sources",
                                           lambda *_: self.layouts_changed())
        except BackendError:
            self._sources_settings = None

    def _query(self) -> int | None:
        index, _ident = self._proxy.call_sync("GetLayout", None, 0, 1000, None).unpack()
        return index if 0 <= index < len(self._specs) else None

    def _on_owner_changed(self, proxy, _pspec) -> None:
        if proxy.get_name_owner() is None:
            self._current = None
            return
        try:
            index = self._query()
        except Exception:  # noqa: BLE001 — расширение только что появилось, могло не ответить
            index = None
        changed = index != self._current
        self._current = index
        if changed and index is not None:
            for cb in self._callbacks:
                cb(index, True)

    def _on_signal(self, _proxy, _sender, signal: str, params) -> None:
        if signal != "LayoutChanged":
            return
        (index,) = params.unpack()
        if not (0 <= index < len(self._specs)):
            self._current = None
            return
        external = index != self._expected
        self._expected = None
        self._current = index
        for cb in self._callbacks:
            cb(index, external)

    def layouts(self) -> list[LayoutSpec]:
        return list(self._specs)

    def xkb_options(self) -> list[str]:
        return gnome_common.read_xkb_options()

    def current(self) -> int | None:
        return self._current

    def set(self, index: int) -> None:
        self._expected = index
        try:
            self._proxy.call_sync("SetLayout", self._GLib.Variant("(u)", (index,)), 0, 1000, None)
        except Exception:
            self._expected = None
            raise
        self._current = index

    def wait_applied(self, index: int, timeout: float) -> bool:
        # SetLayout внутри Shell синхронен, но композитору нужно несколько
        # миллисекунд, чтобы новые события с клавиатуры уже раскладывались по
        # новому keymap: иначе первый символ уходит в прежнюю раскладку.
        if self._settle > 0:
            time.sleep(self._settle / 1000)
        return self._current == index

    def on_change(self, cb: Callable[[int, bool], None]) -> None:
        self._callbacks.append(cb)

    def get_selection(self) -> str | None:
        """Выделенный текст (PRIMARY) — через расширение, буфер обмена не трогаем."""
        try:
            (text,) = self._proxy.call_sync("GetSelection", None, 0, 1000, None).unpack()
        except self._GLib.Error as exc:
            if "UnknownMethod" in str(exc):
                # Shell держит загруженной старую копию расширения до перелогина.
                raise BackendError("расширение старой версии: выйдите из сеанса и "
                                   "войдите снова") from exc
            raise
        return text or None

    def backspace(self, count: int) -> None:
        self._proxy.call_sync("Backspace", self._GLib.Variant("(u)", (count,)), 0, 2000, None)

    def type_text(self, text: str) -> None:
        self._proxy.call_sync("TypeText", self._GLib.Variant("(s)", (text,)), 0, 2000, None)


class ShellTypist:
    """Печать через расширение: keysym-ы не зависят от раскладки."""

    def __init__(self, backend: GnomeShellBackend, keys=None):
        self._backend = backend
        # uinput-устройство демона: им нажимаем Caps Lock (расширение печатает
        # keysym-ами и Caps Lock не трогает).
        self._keys = keys

    def toggle_caps(self) -> None:
        if self._keys is None:
            return
        from wayswitch import keycodes as kc

        for value in (1, 0):
            self._keys.key(kc.KEY_CAPSLOCK, value)
            self._keys.syn()

    def backspace(self, count: int) -> None:
        self._backend.backspace(count)

    def type_text(self, text: str) -> None:
        self._backend.type_text(text)
