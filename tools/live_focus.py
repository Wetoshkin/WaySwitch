"""Opt-in GNOME/GTK4 readback benchmark, typing only into its own verified window.

The normal daemon must use a separate synthetic configuration. This tool never
changes configuration, stops services, or changes layout except through ordinary
guarded correction inputs. Unsupported or unverifiable focus stops injection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace


class GuardedKeyboard:
    def __init__(self, device, guard):
        self.device, self.guard = device, guard
        self.held = set()
        self.failed = False

    def press(self, code):
        if self.failed or not self.guard():
            self.failed = True
            raise RuntimeError("fixture focus or session is not verified")
        started = time.monotonic_ns()
        self.held.add(code)
        try:
            self.device.write(1, code, 1)
            self.device.syn()
            self.device.write(1, code, 0)
            self.held.remove(code)
            self.device.syn()
        except Exception:
            self.failed = True
            raise
        return started

    def close(self):
        self.failed = True
        try:
            for code in tuple(self.held):
                try:
                    self.device.write(1, code, 0)
                    self.held.remove(code)
                    self.device.syn()
                except OSError:
                    pass
        finally:
            self.device.close()


def check_refusal(guard):
    events = []
    recording = SimpleNamespace(write=lambda *event: events.append(event), syn=lambda: None)
    keyboard = GuardedKeyboard(recording, guard)
    try:
        keyboard.press(34)
    except RuntimeError:
        return not events
    return False


def single_graphical_session(sessions, selected, uid):
    graphical = [s for s in sessions if s["Type"] in {"wayland", "x11"}]
    return (len(graphical) == 1 and graphical[0]["Id"] == selected
            and graphical[0]["Seat"][0] == "seat0" and graphical[0]["User"][0] == uid
            and graphical[0]["Remote"] is False and graphical[0]["Type"] == "wayland")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--idle-seconds", type=float, default=0)
    parser.add_argument("--poll-ms", type=int, default=20)
    parser.add_argument("--app-sha", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check-refusal", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 1000 or not 0 <= args.warmup <= 20:
        parser.error("samples must be 1..1000 and warmup 0..20")
    if not 0 <= args.idle_seconds <= 600:
        parser.error("idle seconds must be 0..600")
    if not 1 <= args.poll_ms <= 1000:
        parser.error("poll milliseconds must be 1..1000")
    try:
        return _run(args)
    except Exception as exc:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        report = {"schema_version": 1, "result_kind": "live", "success": False,
                  "layout_restored": False, "errors": [type(exc).__name__],
                  "settings": {"samples": args.samples},
                  "samples": {"kind": "live", "attempts": 0, "records": []},
                  "cases": [{"id": "initialization", "status": "error"}]}
        args.output.write_text(json.dumps(report) + "\n", encoding="utf-8")
        print("live benchmark: initialization failed (" + type(exc).__name__ + ")")
        return 1


def _run(args):
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    import gi

    from tools.benchmark_focus import DATA_FILES, summarize_samples
    from wayswitch import __version__

    gi.require_version("Gtk", "4.0")
    from evdev import UInput, ecodes
    from gi.repository import Gio, GLib, Gtk

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    shell = "io.github.Wetoshkin.WaySwitch.Shell"
    path = "/io/github/Wetoshkin/WaySwitch/Shell"
    def call(connection, dest, obj, iface, method, variant=None):
        return connection.call_sync(dest, obj, iface, method, variant, None,
                                    Gio.DBusCallFlags.NONE, 500, None).unpack()
    session_path = call(system, "org.freedesktop.login1", "/org/freedesktop/login1",
                        "org.freedesktop.login1.Manager", "GetSession",
                        GLib.Variant("(s)", (args.session_id,)))[0]
    sessions = call(system, "org.freedesktop.login1", "/org/freedesktop/login1",
                    "org.freedesktop.login1.Manager", "ListSessions")[0]
    properties = [call(system, "org.freedesktop.login1", entry[4],
                       "org.freedesktop.DBus.Properties", "GetAll", GLib.Variant(
                           "(s)", ("org.freedesktop.login1.Session",)))[0] for entry in sessions]
    if not single_graphical_session(properties, args.session_id, os.getuid()):
        raise RuntimeError("requires exactly one local Wayland session on seat0 for this user")
    sources = Gio.Settings.new("org.gnome.desktop.input-sources").get_value("sources").unpack()
    if len(sources) != 2 or set(sources) != {("xkb", "us"), ("xkb", "ru")}:
        raise RuntimeError("live harness requires exactly GNOME xkb us/ru (no settings changed)")
    def layout():
        index = call(bus, shell, path, shell, "GetLayout")[0]
        if type(index) is not int or not 0 <= index < len(sources):
            raise RuntimeError("unknown layout")
        return sources[index][1]

    initial_layout = layout()
    app = Gtk.Application(application_id="io.github.Wetoshkin.WaySwitchFocusBenchmark",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    records, setup_errors = [], []
    state = {"trial": None, "restoring": False, "finished": False,
             "remaining": args.samples + args.warmup, "refusal_checked": False}
    keyboard = None
    ready_deadline = time.monotonic() + 12

    def guard():
        try:
            if (not window.get_visible() or not window.is_active()
                    or window.get_focus() is not view or not view.get_editable()):
                return False
            for prop, expected in (("Active", True), ("LockedHint", False)):
                got = call(system, "org.freedesktop.login1", session_path,
                           "org.freedesktop.DBus.Properties", "Get",
                           GLib.Variant("(ss)", ("org.freedesktop.login1.Session", prop)))[0]
                if type(got) is not bool or got is not expected:
                    return False
            context = call(bus, shell, path, shell, "GetFocusedContext")
            return context[5] == os.getpid() and context[1] is False
        except Exception:
            return False

    def text():
        buffer = view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def quit_run():
        state["finished"] = True
        app.quit()
        return False

    def finish(status):
        trial = state["trial"]
        if trial is None:
            return False
        records.append({"id": len(records), "kind": trial["kind"], "status": status,
                        "latency_ms": ((trial["read_at"] - trial["boundary"]) / 1e6
                                       if status == "ok" else None),
                        "first_key_to_read_ms": ((trial["read_at"] - trial["first"]) / 1e6
                                                 if status == "ok" else None),
                        "guard_ns": trial["guard_ns"], "observed_length": len(text())})
        records[-1]["direction"] = trial["direction"]
        state["trial"] = None
        if trial["kind"] == "restore" or status != "ok":
            keyboard.failed = True
            return quit_run()
        GLib.timeout_add(300, next_trial)
        return False

    def settled():
        trial = state["trial"]
        if trial is not None:
            if text() != trial["expected"] or not guard():
                finish("duplicate")
            else:
                try:
                    finish("ok" if layout() == trial["target_layout"] else "layout-mismatch")
                except Exception:
                    finish("refused")
        return False

    def send_key():
        trial = state["trial"]
        if trial is None or state["finished"]:
            return False
        try:
            start = time.monotonic_ns()
            code = trial["codes"].pop(0)
            timestamp = keyboard.press(code)
            trial["guard_ns"] += timestamp - start
            if trial["first"] is None:
                trial["first"] = timestamp
            if not trial["codes"]:
                trial["boundary"] = timestamp
                return False
            return True
        except Exception:
            return finish("refused")

    def monitor():
        trial = state["trial"]
        if trial is None or state["finished"]:
            return not state["finished"]
        if trial["boundary"] is not None and trial["read_at"] is None:
            if text() == trial["expected"]:
                trial["read_at"] = time.monotonic_ns()
                GLib.timeout_add(200, settled)
            elif time.monotonic() > trial["deadline"]:
                finish("lost" if not text() else "timeout")
        return True

    def next_trial():
        if state["finished"]:
            return False
        if not guard():
            if time.monotonic() < ready_deadline and not records:
                GLib.timeout_add(100, next_trial)
                return False
            setup_errors.append("fixture focus/session unavailable; no further injection")
            return quit_run()
        try:
            current = layout()
        except Exception:
            setup_errors.append("layout unavailable")
            return quit_run()
        if state["remaining"] == 0:
            if current == initial_layout:
                return quit_run()
            state["restoring"] = True
        kind = ("restore" if state["restoring"] else
                "warmup" if len(records) < args.warmup else "sample")
        if kind != "restore":
            state["remaining"] -= 1
        # Programmatic reset touches only this process's synthetic widget.
        view.get_buffer().set_text("")
        codes, expected = (([34, 35, 48, 32, 20, 49, 57], "привет ") if current == "us"
                           else ([35, 18, 38, 38, 24, 57], "hello "))
        state["trial"] = {"kind": kind, "codes": codes, "expected": expected,
                          "direction": current, "target_layout": "ru" if current == "us" else "us",
                          "first": None, "boundary": None, "read_at": None,
                          "deadline": time.monotonic() + args.idle_seconds + 5, "guard_ns": 0}
        delay = int(args.idle_seconds * 1000) if kind == "sample" else 200
        GLib.timeout_add(max(200, delay), lambda: (GLib.timeout_add(40, send_key), False)[1])
        return False

    def activate(_app):
        nonlocal keyboard, window, view
        window = Gtk.ApplicationWindow(
            application=app, title="WaySwitch: synthetic focus benchmark")
        view = Gtk.TextView(monospace=True)
        window.set_default_size(740, 220)
        window.set_child(view)
        window.present()
        view.grab_focus()
        if args.check_refusal:
            window.set_visible(False)
            state["refusal_checked"] = check_refusal(guard)
            window.present()
            view.grab_focus()
            if not state["refusal_checked"]:
                setup_errors.append("negative focus probe failed (recording device only)")
                quit_run()
                return
        try:
            device = UInput({ecodes.EV_KEY: list(range(1, 128))},
                            name="WaySwitch Focus Benchmark Input")
            keyboard = GuardedKeyboard(device, guard)
            info = subprocess.run(["udevadm", "info", "--query=property",
                                   "--name", device.device.path], capture_output=True,
                                  text=True, timeout=3, check=True).stdout
            seat = next((line[8:] for line in info.splitlines()
                         if line.startswith("ID_SEAT=")), "seat0")
            if seat != "seat0":
                raise RuntimeError("test input device belongs to another seat")
        except Exception as exc:
            setup_errors.append("input initialization failed: " + type(exc).__name__)
            quit_run()
            return
        GLib.timeout_add(1500, next_trial)
        GLib.timeout_add(args.poll_ms, monitor)
        window.connect("close-request", lambda *_: (quit_run(), False)[1])

    window = view = None
    app.connect("activate", activate)
    try:
        app.run([])
    finally:
        if keyboard is not None:
            keyboard.close()
        if state["trial"] is not None:
            records.append({"id": len(records), "kind": state["trial"]["kind"],
                            "status": "refused", "latency_ms": None,
                            "observed_length": len(text())})
    try:
        restored = layout() == initial_layout
    except Exception:
        restored = False
    samples = [record for record in records if record["kind"] == "sample"]
    failed = setup_errors or any(record["status"] != "ok" for record in records)
    directions = {r.get("direction") for r in samples if r["status"] == "ok"}
    if (len(samples) != args.samples or not restored
            or args.check_refusal and not state["refusal_checked"]
            or args.samples > 1 and directions != {"us", "ru"}):
        failed = True
    report = {
        "schema_version": 1, "result_kind": "live", "app": {
            "version": __version__, "sha": args.app_sha},
        "harness": {"name": "live_focus", "version": 1,
                    "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "gtk": Gtk.get_major_version(), "clock": "monotonic_ns"},
        "settings": {"samples": args.samples, "warmup": args.warmup,
                     "idle_seconds": args.idle_seconds, "key_interval_ms": 40,
                     "poll_ms": args.poll_ms,
                     "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest()},
        "data": {name: hashlib.sha256((root / "wayswitch/data" / name).read_bytes()).hexdigest()
                 for name in DATA_FILES},
        "seed": 0,
        "corpus": {"sha256": hashlib.sha256(b"us:ghbdtn ;ru:hello ;v1").hexdigest()},
        "samples": {"kind": "live", "attempts": len(samples), "records": samples},
        "summary": summarize_samples({"records": samples}), "records": records,
        "errors": setup_errors, "layout_restored": restored,
        "refusal_checked": state["refusal_checked"], "success": not bool(failed),
        "directions": sorted(d for d in directions if d is not None),
        "metric": "last boundary key write to verified GTK buffer read; not rendered pixels",
        "unavailable": ["frame_render", "client_layout_ack"],
        "cases": [{"id": str(r["id"]), "kind": "widget", "actual": {
            "matched": r["status"] == "ok", "length": r["observed_length"]},
            "status": "pass" if r["status"] == "ok" else "fail"} for r in records],
    }
    if setup_errors or not restored:
        report["cases"].append({"id": "setup-or-restore", "kind": "widget",
                                "status": "error", "error": "setup or restoration failed"})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print("live benchmark:", "PASS" if not failed else "FAIL", f"({len(samples)} samples)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
