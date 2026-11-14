"""Opt-in daemon stage timings; arguments, field text and results are never recorded.

Run against an isolated configuration instead of the normal service, then stop
the process normally to write the report. Instrumented runs have wrapper overhead
and must be compared separately from uninstrumented widget latency runs.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import inspect
import json
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path


class Timings:
    def __init__(self, *, limit=20000, clock=time.monotonic_ns):
        self.stages = {}
        self._originals = []
        self._limit, self._clock = limit, clock
        self._lock = threading.Lock()

    def wrap(self, owner, name, label):
        original = inspect.getattr_static(owner, name)
        method = (original.__func__ if isinstance(original, (classmethod, staticmethod))
                  else original)

        @functools.wraps(method)
        def measured(*args, **kwargs):
            start = self._clock()
            failed = False
            try:
                return method(*args, **kwargs)
            except BaseException:
                failed = True
                raise
            finally:
                self.record(label, self._clock() - start, failed)

        self._originals.append((owner, name, original))
        setattr(owner, name, type(original)(measured)
                if isinstance(original, (classmethod, staticmethod)) else measured)

    def record(self, label, elapsed, failed=False):
        with self._lock:
            stage = self.stages.setdefault(label, {
                "calls": 0, "errors": 0, "durations_ns": [], "dropped": 0})
            stage["calls"] += 1
            stage["errors"] += int(failed)
            if len(stage["durations_ns"]) < self._limit:
                stage["durations_ns"].append(elapsed)
            else:
                stage["dropped"] += 1

    def restore(self):
        for owner, name, original in reversed(self._originals):
            setattr(owner, name, original)
        self._originals.clear()


def profile_bus_calls(stats, connection_type):
    original = connection_type.call_sync
    def measured(connection, destination, path, interface, method, *args, **kwargs):
        shell_query = (interface == "io.github.Wetoshkin.WaySwitch.Shell"
                       and method in {"GetFocusedContext", "GetFocusedApp", "GetCapabilities"})
        owner_query = False
        if interface == "org.freedesktop.DBus" and method in {"GetNameOwner", "NameHasOwner"}:
            try:
                owner_query = args[0].unpack() == ("io.github.Wetoshkin.WaySwitch.Shell",)
            except (IndexError, AttributeError, TypeError):
                pass
        if not shell_query and not owner_query:
            return original(connection, destination, path, interface, method, *args, **kwargs)
        started, failed = stats._clock(), False
        try:
            return original(connection, destination, path, interface, method, *args, **kwargs)
        except BaseException:
            failed = True
            raise
        finally:
            stats.record("shell_ipc." + method, stats._clock() - started, failed)
    stats._originals.append((connection_type, "call_sync", original))
    connection_type.call_sync = measured


def main():
    started = time.monotonic_ns()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from gi.repository import Gio, GLib

    from wayswitch import __version__, a11y, daemon, editable, learning
    from wayswitch import config as cfgmod
    from wayswitch.actuator import UInputTypist
    from wayswitch.backends.gnome_shell import GnomeShellBackend, ShellTypist
    from wayswitch.controller import Controller
    from wayswitch.dbus_service import DaemonService
    from wayswitch.detector import Detector, LanguageModel

    if args.state_dir.resolve() == cfgmod.config_dir().resolve():
        parser.error("state directory must differ from the normal user configuration")
    args.state_dir.mkdir(parents=True, exist_ok=True)
    # GNOME reads its real dconf; only WaySwitch's synthetic state is redirected.
    cfgmod.config_dir = lambda: args.state_dir
    learning.config_dir = lambda: args.state_dir

    imports_ns = time.monotonic_ns() - started
    stats = Timings()
    profile_bus_calls(stats, Gio.DBusConnection)
    for owner, name, label in [
        (LanguageModel, "load", "model_load"),
        (Controller, "on_key", "event_handler"),
        (Detector, "decide", "decision"),
        (a11y, "query_shell_context", "context"),
        (a11y, "find_focus", "focus_scan"),
        (a11y._DbusTree, "_call", "atspi_call"),
        (GnomeShellBackend, "set", "layout_set"),
        (GnomeShellBackend, "wait_applied", "layout_wait"),
        (Controller, "_apply_fix", "fix"),
        (editable, "_replace_checked", "native_replace"),
        (UInputTypist, "key", "uinput_write"),
        (ShellTypist, "type_text", "shell_type"),
        (DaemonService, "start", "dbus_export"),
    ]:
        stats.wrap(owner, name, label)
    ready = []

    def dispatched():
        ready.append(time.monotonic_ns() - started)
        return False

    GLib.idle_add(dispatched)
    code = 1
    try:
        code = daemon.run(args.config, False, False)
        return code
    finally:
        stats.restore()
        import resource

        try:
            sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                          text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            sha = None
        rss = {}
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(("VmRSS:", "VmHWM:", "Threads:")):
                key, value = line.split(":", 1)
                rss[key] = int(value.split()[0])
        report = {
            "schema_version": 1, "result_kind": "daemon_profile", "app_sha": sha,
            "app_version": __version__, "harness_sha256": hashlib.sha256(
                Path(__file__).read_bytes()).hexdigest(),
            "environment": {"python": platform.python_version(), "platform": platform.platform()},
            "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
            "imports_ns": imports_ns, "first_main_dispatch_ns": ready[0] if ready else None,
            "duration_ns": time.monotonic_ns() - started, "exit_code": code,
            "memory_kib": {**rss, "peak_process": resource.getrusage(
                resource.RUSAGE_SELF).ru_maxrss},
            "fd_count_at_exit": len(list(Path("/proc/self/fd").iterdir())),
            "stages": stats.stages,
            "unavailable": ["frame_render", "client_layout_ack", "GUI_and_helper_RSS"],
            "note": "nested stages overlap; wrapper overhead; event handler is not kernel delivery",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
