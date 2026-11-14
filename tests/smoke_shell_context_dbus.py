"""Private-bus integration check for owner, payload, and connection lifecycle."""

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wayswitch.shell_context import BUS_NAME, INTERFACE, OBJECT_PATH, ShellContext, ShellWatch


def main():
    assert os.environ.get("WAYSWITCH_PRIVATE_BUS_TEST") == "1", "use pytest wrapper"
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    invalidations = []
    watch = ShellWatch(bus, lambda: invalidations.append(True))
    assert all(watch.query() == ShellContext() for _ in range(100))
    ready, stop = threading.Event(), threading.Event()
    server = {}
    calls = []

    def serve():
        context = GLib.MainContext.new()
        context.push_thread_default()
        connection = Gio.DBusConnection.new_for_address_sync(
            os.environ["DBUS_SESSION_BUS_ADDRESS"],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        info = Gio.DBusNodeInfo.new_for_xml('''<node><interface name="'''+INTERFACE+'''">
        <method name="GetCapabilities"><arg type="as" direction="out"/></method>
        <method name="GetFocusedContext"><arg type="as" direction="out"/>
        <arg type="b" direction="out"/><arg type="b" direction="out"/>
        <arg type="u" direction="out"/><arg type="u" direction="out"/>
        <arg type="u" direction="out"/></method>
        <signal name="FocusedContextChanged"/>
        <signal name="FocusedContextChanged2"><arg type="as"/><arg type="b"/>
        <arg type="b"/><arg type="u"/><arg type="u"/><arg type="u"/></signal>
        </interface></node>''').interfaces[0]

        def method(_conn, _sender, _path, _iface, name, _params, invocation):
            calls.append(name)
            value = ((["focused_context_payload_v2"],) if name == "GetCapabilities"
                     else (["editor"], False, True, 0, 0, 123))
            invocation.return_value(GLib.Variant(
                "(as)" if name == "GetCapabilities" else "(asbbuuu)", value))

        registration = connection.register_object(OBJECT_PATH, info, method, None, None)
        connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                             "org.freedesktop.DBus", "RequestName",
                             GLib.Variant("(su)", (BUS_NAME, 0)), None,
                             Gio.DBusCallFlags.NONE, 1000, None)
        server["bus"] = connection
        ready.set()
        while not stop.is_set():
            while context.pending():
                context.iteration(False)
            stop.wait(0.005)
        connection.unregister_object(registration)
        connection.close_sync(None)
        context.pop_thread_default()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    def dispatch_until(predicate):
        deadline = time.monotonic() + 3
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("event was not dispatched")

    try:
        assert ready.wait(3)
        dispatch_until(lambda: bool(invalidations))
        assert watch.query().pid == 123
        calls.clear()
        generation = watch.generation
        server["bus"].emit_signal(None, OBJECT_PATH, INTERFACE, "FocusedContextChanged", None)
        server["bus"].emit_signal(None, OBJECT_PATH, INTERFACE, "FocusedContextChanged2",
                                  GLib.Variant("(asbbuuu)", (["editor"], False, True, 8, 0, 123)))
        dispatch_until(lambda: watch.generation >= generation + 2)
        assert watch.query().protected()
        assert not calls
        assert not watch.query(fresh=True).protected()
        assert calls == ["GetFocusedContext"]
        # No probe polling thread is running while the following event arrives.
        generation = watch.generation
        server["bus"].emit_signal(None, OBJECT_PATH, INTERFACE, "FocusedContextChanged", None)
        dispatch_until(lambda: watch.generation > generation)
        assert not watch.query().protected()
        stop.set()
        thread.join(3)
        generation = watch.generation
        dispatch_until(lambda: watch.generation > generation)
        assert watch.query() == ShellContext()
    finally:
        stop.set()
        thread.join(3)
        watch.close()
    print("private Gio owner/payload/idle/cleanup: OK")


if __name__ == "__main__":
    main()
