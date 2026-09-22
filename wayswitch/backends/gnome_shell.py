"""Раскладка и печать через наше расширение GNOME Shell (D-Bus)."""

from __future__ import annotations

from collections.abc import Callable

from wayswitch.backends import gnome_common
from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.keymap import LayoutSpec

BUS_NAME = "ru.siberia.WaySwitch.Shell"
OBJECT_PATH = "/ru/siberia/WaySwitch/Shell"
INTERFACE = "ru.siberia.WaySwitch.Shell"


def shell_extension_present() -> bool:
    from gi.repository import Gio

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "NameHasOwner", Gio.Variant("(s)", (BUS_NAME,)),
                          Gio.VariantType("(b)"), Gio.DBusCallFlags.NONE, 1000, None)
    return bool(reply.unpack()[0])


class GnomeShellBackend(LayoutBackend):
    name = "shell"
    supports_auto = True

    def __init__(self):
        from gi.repository import Gio

        self._Gio = Gio
        self._specs = gnome_common.read_sources()
        self._proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, BUS_NAME, OBJECT_PATH,
            INTERFACE, None)
        if self._proxy.get_name_owner() is None:
            raise BackendError("расширение WaySwitch не активно")
        self._callbacks: list[Callable[[int, bool], None]] = []
        self._expected: int | None = None
        self._proxy.connect("g-signal", self._on_signal)
        self._current = self._query()

    def _query(self) -> int | None:
        index, _ident = self._proxy.call_sync("GetLayout", None, 0, 1000, None).unpack()
        return index if 0 <= index < len(self._specs) else None

    def _on_signal(self, _proxy, _sender, signal: str, params) -> None:
        if signal != "LayoutChanged":
            return
        (index,) = params.unpack()
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
        self._proxy.call_sync("SetLayout", self._Gio.Variant("(u)", (index,)), 0, 1000, None)
        self._current = index

    def wait_applied(self, index: int, timeout: float) -> bool:
        return self._current == index  # SetLayout синхронен внутри Shell

    def on_change(self, cb: Callable[[int, bool], None]) -> None:
        self._callbacks.append(cb)

    def backspace(self, count: int) -> None:
        self._proxy.call_sync("Backspace", self._Gio.Variant("(u)", (count,)), 0, 2000, None)

    def type_text(self, text: str) -> None:
        self._proxy.call_sync("TypeText", self._Gio.Variant("(s)", (text,)), 0, 2000, None)


class ShellTypist:
    """Печать через расширение: keysym-ы не зависят от раскладки."""

    def __init__(self, backend: GnomeShellBackend):
        self._backend = backend

    def backspace(self, count: int) -> None:
        self._backend.backspace(count)

    def type_text(self, text: str) -> None:
        self._backend.type_text(text)
