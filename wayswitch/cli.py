"""Командная строка: запуск демона, диагностика, управление по D-Bus."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wayswitch import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wayswitch",
                                description="Автоисправление раскладки для GNOME/Wayland")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="запустить демон")
    run.add_argument("--verbose", "-v", action="store_true")
    run.add_argument("--dry-run", action="store_true", help="решать, но не печатать")
    run.add_argument("--config", type=Path)
    dry = sub.add_parser("dry-run", help="демон в режиме наблюдения (verbose, без действий)")
    dry.add_argument("--config", type=Path)
    sub.add_parser("doctor", help="проверить окружение")
    sub.add_parser("devices", help="список устройств ввода")
    sub.add_parser("pause", help="приостановить исправления")
    sub.add_parser("resume", help="возобновить")
    sub.add_parser("fix", help="исправить последнее слово")
    sub.add_parser("status", help="состояние демона")
    sub.add_parser("gui", help="открыть настройки")
    sub.add_parser("version")
    return p


def call_daemon(method: str):
    from gi.repository import Gio

    from wayswitch.dbus_service import BUS_NAME, INTERFACE, OBJECT_PATH

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, method, None, None,
                          Gio.DBusCallFlags.NONE, 3000, None)
    return reply.unpack() if reply is not None else None


def _devices() -> int:
    from evdev import InputDevice, ecodes, list_devices

    from wayswitch.keys import classify

    for path in list_devices():
        try:
            dev = InputDevice(path)
        except PermissionError:
            print(f"{path:<20} (нет доступа)")
            continue
        kind = classify(dev.name, set(dev.capabilities().get(ecodes.EV_KEY, [])))
        print(f"{path:<20} {dev.name:<45} {kind or ''}")
        dev.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    if ns.command == "version":
        print(f"wayswitch {__version__}")
        return 0
    if ns.command in ("run", "dry-run"):
        from wayswitch.daemon import run

        dry = ns.command == "dry-run" or ns.dry_run
        verbose = dry or ns.verbose if ns.command == "run" else True
        return run(ns.config, verbose, dry)
    if ns.command == "doctor":
        from wayswitch.doctor import exit_code, format_report, run_checks

        checks = run_checks()
        print(format_report(checks))
        return exit_code(checks)
    if ns.command == "devices":
        return _devices()
    if ns.command == "gui":
        from wayswitch.gui.app import main as gui_main

        return gui_main()
    try:
        if ns.command == "pause":
            call_daemon("Pause")
            print("пауза")
        elif ns.command == "resume":
            call_daemon("Resume")
            print("работает")
        elif ns.command == "fix":
            (ok,) = call_daemon("FixLastWord")
            print("исправлено" if ok else "нечего исправлять")
        elif ns.command == "status":
            (status,) = call_daemon("GetStatus")
            for k, v in sorted(status.items()):
                print(f"{k}: {v}")
    except Exception as e:  # noqa: BLE001
        print(f"демон не отвечает: {e}", file=sys.stderr)
        return 2
    return 0
