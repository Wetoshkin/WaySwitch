"""Opt-in GTK focus/IPC check on a real desktop; never injects keyboard input."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.profile_focus import Timings, profile_bus_calls
from wayswitch import a11y


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect-shell", choices=("present", "absent"), required=True)
    args = parser.parse_args()
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib, Gtk

    context = GLib.MainContext.default()
    def until(condition):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if condition():
                return
            time.sleep(0.01)
        raise AssertionError("desktop focus/event deadline exceeded")

    app = Gtk.Application(application_id="io.github.Wetoshkin.WaySwitchContextProbe",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    window = Gtk.ApplicationWindow(application=app, title="WaySwitch: focus context probe")
    window.set_default_size(650, 180)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    normal, password = Gtk.TextView(), Gtk.PasswordEntry()
    box.append(normal)
    box.append(password)
    window.set_child(box)
    window.present()
    normal.grab_focus()
    probe = a11y.make_probe(True)
    metadata = None
    stats = Timings()
    profile_bus_calls(stats, Gio.DBusConnection)
    result = {"schema_version": 1, "success": False, "expected_shell": args.expect_shell}
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        present = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", GLib.Variant("(s)", ("io.github.Wetoshkin.WaySwitch.Shell",)),
            GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 500, None).unpack()[0]
        assert present == (args.expect_shell == "present"), "unexpected Shell service state"
        result["shell_present"] = present
        until(window.is_active)
        probe.poke()
        metadata = a11y._new_a11y_connection()
        def owned_snapshot(protected):
            snap = probe.snapshot()
            if snap.protection(time.monotonic()) is not protected:
                probe.poke()
                return False
            if snap.field is None:
                current = a11y.query_shell_context()
                return (protected and args.expect_shell == "present" and current is not None
                        and current.protected() and current.pid == os.getpid())
            owner = metadata.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "GetConnectionUnixProcessID", GLib.Variant("(s)", (snap.field[0],)),
                GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 500, None).unpack()[0]
            return owner == os.getpid()
        until(lambda: owned_snapshot(False))
        result["normal_field_verified"] = True
        password.grab_focus()
        probe.poke()
        until(lambda: owned_snapshot(True))
        result["password_field_protected"] = True
        probe.close()
        until(lambda: probe._thread is None)
        normal.grab_focus()
        def ready():
            if not normal.has_focus():
                return False
            current = a11y.query_shell_context()
            return (current is not None and current.pid == os.getpid() and not current.protected()
                    if present else True)
        until(ready)

        def measure(query):
            stats.stages.clear()
            start = time.monotonic_ns()
            for _ in range(100):
                got = query()
                assert got is not None
                assert (got.pid == os.getpid() and not got.protected()
                        if args.expect_shell == "present" else got == a11y.ShellContext())
            return {"elapsed_ns": time.monotonic_ns() - start,
                    "calls": {name: stage["calls"] for name, stage in stats.stages.items()}}
        result["direct"] = measure(a11y.query_shell_context)
        assert result["direct"]["calls"].get("shell_ipc.GetFocusedContext") == 100
        result["success"] = True
    finally:
        probe.close()
        a11y._close_private_connection(metadata)
        stats.restore()
        window.destroy()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("desktop context/normal/password/IPC: OK")


if __name__ == "__main__":
    main()
