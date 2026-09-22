"""D-Bus-сервис демона: управление и статус для CLI и GUI."""

from __future__ import annotations

import logging
from collections.abc import Callable

BUS_NAME = "ru.siberia.WaySwitch"
OBJECT_PATH = "/ru/siberia/WaySwitch"
INTERFACE = "ru.siberia.WaySwitch.Daemon"
INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Pause"/>
    <method name="Resume"/>
    <method name="FixLastWord"><arg type="b" name="ok" direction="out"/></method>
    <method name="FixPhrase"><arg type="b" name="ok" direction="out"/></method>
    <method name="Reload"/>
    <method name="GetStatus"><arg type="a{{sv}}" name="status" direction="out"/></method>
    <signal name="StatusChanged"><arg type="a{{sv}}" name="status"/></signal>
    <signal name="Corrected">
      <arg type="s" name="original"/><arg type="s" name="fixed"/><arg type="b" name="manual"/>
    </signal>
  </interface>
</node>
"""
log = logging.getLogger("wayswitch")


def _to_variant_dict(status: dict):
    from gi.repository import GLib

    out = {}
    for k, v in status.items():
        if isinstance(v, bool):
            out[k] = GLib.Variant("b", v)
        elif isinstance(v, int):
            out[k] = GLib.Variant("i", v)
        elif v is None:
            out[k] = GLib.Variant("i", -1)
        else:
            out[k] = GLib.Variant("s", str(v))
    return GLib.Variant("a{sv}", out)


class DaemonService:
    def __init__(self, controller, on_reload: Callable[[], None],
                 on_fatal: Callable[[int], None]):
        self.controller = controller
        self.on_reload = on_reload
        self.on_fatal = on_fatal
        self._conn = None

    def start(self) -> None:
        from gi.repository import Gio

        self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self._conn.register_object(OBJECT_PATH, node.interfaces[0], self._on_call, None, None)
        # name_acquired_closure=None, name_lost_closure=self._on_name_lost —
        # последний срабатывает и когда имя вообще не удалось занять (уже
        # запущен другой экземпляр демона), не только при потере владения.
        Gio.bus_own_name_on_connection(self._conn, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                                       None, self._on_name_lost)

    def _on_name_lost(self, _conn, _name) -> None:
        # raise SystemExit здесь ненадёжен: колбэк вызывается из GI/GLib,
        # исключение не долетит до run() предсказуемо — вместо этого просим
        # демон завершиться штатно через колбэк на mainloop.
        log.error("имя %s занято — демон уже запущен", BUS_NAME)
        self.on_fatal(3)

    def _on_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        from gi.repository import GLib

        c = self.controller
        if method == "Pause":
            c.pause()
            invocation.return_value(None)
        elif method == "Resume":
            c.resume()
            invocation.return_value(None)
        elif method == "FixLastWord":
            invocation.return_value(GLib.Variant("(b)", (c.manual_word(),)))
        elif method == "FixPhrase":
            invocation.return_value(GLib.Variant("(b)", (c.manual_phrase(),)))
        elif method == "Reload":
            self.on_reload()
            invocation.return_value(None)
        elif method == "GetStatus":
            invocation.return_value(GLib.Variant.new_tuple(_to_variant_dict(c.status())))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def emit_status(self, status: dict) -> None:
        if self._conn:
            from gi.repository import GLib

            self._conn.emit_signal(None, OBJECT_PATH, INTERFACE, "StatusChanged",
                                   GLib.Variant.new_tuple(_to_variant_dict(status)))

    def emit_corrected(self, original: str, fixed: str, manual: bool) -> None:
        if self._conn:
            from gi.repository import GLib

            self._conn.emit_signal(None, OBJECT_PATH, INTERFACE, "Corrected",
                                   GLib.Variant("(ssb)", (original, fixed, manual)))
