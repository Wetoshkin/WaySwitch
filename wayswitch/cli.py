"""Командная строка: запуск демона, диагностика, управление по D-Bus."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wayswitch import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wayswitch",
                                description="Исправление раскладки на Wayland")
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
    sub.add_parser("toggle", help="пауза или возобновление одной командой (для горячей клавиши)")
    sub.add_parser("waybar", help="поток JSON для модуля Waybar")
    sub.add_parser("fix", help="исправить последнее слово")
    sub.add_parser("fix-selection", help="исправить выделенный текст (назначьте сочетание "
                                          "клавиш в настройках системы)")
    status = sub.add_parser("status", help="состояние демона")
    status.add_argument("--json", action="store_true", help="машиночитаемый вывод")
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


def _waybar() -> int:
    """Поток JSON для Waybar: строка на каждое изменение состояния.

    Подписывается на сигналы демона по D-Bus и следит за его появлением на
    шине — модуль показывает «не запущен» и оживает сам, когда служба стартует.
    """
    import json

    from gi.repository import Gio, GLib

    from wayswitch.dbus_service import BUS_NAME, INTERFACE, OBJECT_PATH
    from wayswitch.panel import FLASH_SEC, Flash, waybar_payload

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    state = {"status": None, "flash": None, "timer": 0, "last": None}

    def emit():
        line = json.dumps(waybar_payload(state["status"], state["flash"]), ensure_ascii=False)
        if line != state["last"]:
            state["last"] = line
            print(line, flush=True)

    def refresh():
        try:
            reply = bus.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, "GetStatus", None, None,
                                  Gio.DBusCallFlags.NONE, 2000, None)
            (state["status"],) = reply.unpack()
        except GLib.Error:
            state["status"] = None
        emit()

    def flash(kind, original, fixed):
        state["flash"] = Flash(kind, original, fixed)
        if state["timer"]:
            GLib.source_remove(state["timer"])

        def clear():
            state["flash"], state["timer"] = None, 0
            emit()
            return GLib.SOURCE_REMOVE

        state["timer"] = GLib.timeout_add(int(FLASH_SEC * 1000), clear)
        emit()

    def on_signal(_conn, _sender, _path, _iface, signal, params):
        if signal == "StatusChanged":
            (new,) = params.unpack()
            old = state["status"] or {}
            state["status"] = new
            if int(new.get("undo_total", 0)) > int(old.get("undo_total", 0) or 0) \
                    and state["flash"] is not None:
                flash("undone", state["flash"].original, state["flash"].fixed)
            else:
                emit()
        elif signal == "Corrected":
            original, fixed, _manual = params.unpack()
            flash("fixed", original, fixed)

    bus.signal_subscribe(BUS_NAME, INTERFACE, None, OBJECT_PATH, None,
                         Gio.DBusSignalFlags.NONE, on_signal)
    Gio.bus_watch_name_on_connection(bus, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
                                     lambda *_: refresh(), lambda *_: refresh())
    refresh()
    try:
        GLib.MainLoop().run()
    except KeyboardInterrupt:
        pass
    return 0


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
    if ns.command == "waybar":
        return _waybar()
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
        elif ns.command == "toggle":
            (status,) = call_daemon("GetStatus")
            call_daemon("Resume" if status.get("paused") else "Pause")
            print("работает" if status.get("paused") else "пауза")
        elif ns.command == "fix":
            (ok,) = call_daemon("FixLastWord")
            print("исправлено" if ok else "нечего исправлять")
        elif ns.command == "fix-selection":
            (ok,) = call_daemon("FixSelection")
            print("исправлено" if ok else "нечего исправлять: выделите текст")
        elif ns.command == "status":
            (status,) = call_daemon("GetStatus")
            if ns.json:
                import json

                print(json.dumps(status, ensure_ascii=False, sort_keys=True))
            else:
                from wayswitch.panel import status_line

                print(status_line(status))
                for k, v in sorted(status.items()):
                    print(f"  {k}: {v}")
    except Exception as e:  # noqa: BLE001
        if getattr(ns, "json", False):
            print('{"running": false}')
            return 2
        print(f"WaySwitch не отвечает: {e}", file=sys.stderr)
        print("Запустить: systemctl --user start wayswitch; проверить: wayswitch doctor",
              file=sys.stderr)
        return 2
    return 0
