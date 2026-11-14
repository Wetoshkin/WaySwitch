"""Командная строка: запуск демона, диагностика, управление по D-Bus."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from wayswitch import __version__
from wayswitch.config import SENSITIVITY_NAMES
from wayswitch.i18n import gettext as _


def build_parser() -> argparse.ArgumentParser:
    argparse._ = _
    p = argparse.ArgumentParser(
        prog="wayswitch", description=_("Keyboard layout correction on Wayland")
    )
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help=_("start the daemon"))
    run.add_argument("--verbose", "-v", action="store_true")
    run.add_argument(
        "--dry-run", action="store_true", help=_("detect corrections without typing them")
    )
    run.add_argument("--config", type=Path)
    dry = sub.add_parser("dry-run", help=_("observe input (verbose, no actions)"))
    dry.add_argument("--config", type=Path)
    doctor = sub.add_parser("doctor", help=_("check the environment and daemon state"))
    doctor.add_argument(
        "--delay",
        type=int,
        default=0,
        metavar=_("SECONDS"),
        help=_("wait before checking so you can focus the desired field (default: 0)"),
    )
    sub.add_parser("devices", help=_("list input devices"))
    sub.add_parser("pause", help=_("pause corrections"))
    sub.add_parser("resume", help=_("resume corrections"))
    sub.add_parser("toggle", help=_("toggle pause (for a keyboard shortcut)"))
    sub.add_parser("waybar", help=_("stream JSON for Waybar"))
    sub.add_parser("fix", help=_("correct the last word"))
    sub.add_parser(
        "fix-selection", help=_("correct selected text (assign a shortcut in system settings)")
    )
    sub.add_parser("translit", help=_("transliterate selected text: Привет ↔ Privet"))
    sub.add_parser("change-case", help=_("cycle the case of selected text"))
    mixed = sub.add_parser("mixed-layout", help=_("convert selected words to a target layout"))
    mixed.add_argument("--to", dest="to_lang", required=True, choices=("ru", "en"),
                       help=_("explicit target language"))
    sub.add_parser(
        "number-words", help=_("spell out the selected number in Russian: 125 → сто двадцать пять")
    )
    status = sub.add_parser("status", help=_("show daemon state"))
    status.add_argument("--json", action="store_true", help=_("machine-readable output"))
    explain = sub.add_parser("explain", help=_("explain the detector decision for a word"))
    explain.add_argument("word", help=_("word typed in the source layout"))
    explain.add_argument(
        "--from",
        dest="from_lang",
        choices=("en", "ru"),
        default="en",
        help=_("source layout: en = us (default), ru"),
    )
    explain.add_argument(
        "--sensitivity",
        choices=SENSITIVITY_NAMES,
        help=_("detector threshold; defaults to config.toml"),
    )
    explain.add_argument("--json", action="store_true", help=_("machine-readable output"))
    sub.add_parser("gui", help=_("open settings"))
    sub.add_parser("version")
    update = sub.add_parser("update", help=_("check for updates from the installation source"))
    update.add_argument(
        "--install",
        action="store_true",
        help=_("install an available update through the original source"),
    )
    return p


def call_daemon(method: str, target: str | None = None):
    from gi.repository import Gio, GLib

    from wayswitch.dbus_service import BUS_NAME, INTERFACE, OBJECT_PATH

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    try:
        reply = bus.call_sync(
            BUS_NAME, OBJECT_PATH, INTERFACE, method,
            GLib.Variant("(s)", (target,)) if target is not None else None,
            None, Gio.DBusCallFlags.NONE, 3000, None
        )
    except GLib.Error as exc:
        if Gio.DBusError.get_remote_error(exc) == BUS_NAME + ".ScreenLocked":
            Gio.DBusError.strip_remote_error(exc)
            raise PermissionError(exc.message) from exc
        raise
    return reply.unpack() if reply is not None else None


_UNCONFIRMED_HINT = "Check the result: select a single line in a regular editable field."

_SELECTION_COMMANDS = {
    "change-case": ("ChangeCaseSelection", "done", "Could not confirm the selection change."),
    "mixed-layout": ("MixedLayoutSelection", "done", "Could not confirm the selection change."),
    "fix-selection": (
        "FixSelection",
        "corrected",
        "Could not confirm correction of the selected text. " + _UNCONFIRMED_HINT,
    ),
    "translit": (
        "TranslitSelection",
        "done",
        "Could not confirm transliteration of the selected text. " + _UNCONFIRMED_HINT,
    ),
    "number-words": (
        "NumberWordsSelection",
        "done",
        "Could not confirm spelling out the number. " + _UNCONFIRMED_HINT,
    ),
}

_SELECTION_UNAVAILABLE = (
    "Selection correction is unavailable in the current service. "
    "Check desktop support and update the WaySwitch service."
)


def _single(reply):
    """Единственный элемент ответа D-Bus; None для пустого или неожиданного ответа."""
    return reply[0] if isinstance(reply, tuple) and len(reply) == 1 else None


def _selection(command: str, target: str | None = None) -> int:
    status = _single(call_daemon("GetStatus"))
    if not isinstance(status, dict) or status.get("selection_supported") is not True:
        print(_(_SELECTION_UNAVAILABLE))
        return 1
    method, done, unconfirmed = _SELECTION_COMMANDS[command]
    reply = call_daemon(method, target) if target is not None else call_daemon(method)
    if _single(reply) is True:
        print(_(done))
        return 0
    # bool не говорит, изменился ли текст: подтвердить успех нельзя, но и обратное не факт.
    print(_(unconfirmed))
    return 1


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

    loop = GLib.MainLoop()

    def emit():
        line = json.dumps(waybar_payload(state["status"], state["flash"]), ensure_ascii=False)
        if line != state["last"]:
            state["last"] = line
            try:
                print(line, flush=True)
            except (BrokenPipeError, OSError):
                loop.quit()  # Waybar закрыл канал — процесс больше не нужен

    def refresh():
        try:
            reply = bus.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                "GetStatus",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
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
            if (
                int(new.get("undo_total", 0)) > int(old.get("undo_total", 0) or 0)
                and state["flash"] is not None
            ):
                flash("undone", state["flash"].original, state["flash"].fixed)
            else:
                emit()
        elif signal == "Corrected":
            original, fixed, _manual = params.unpack()
            flash("fixed", original, fixed)

    bus.signal_subscribe(
        BUS_NAME, INTERFACE, None, OBJECT_PATH, None, Gio.DBusSignalFlags.NONE, on_signal
    )
    Gio.bus_watch_name_on_connection(
        bus, BUS_NAME, Gio.BusNameWatcherFlags.NONE, lambda *_: refresh(), lambda *_: refresh()
    )
    refresh()
    try:
        loop.run()
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
            print(_("{path:<20} (access denied)").format(path=path))
            continue
        kind = classify(dev.name, set(dev.capabilities().get(ecodes.EV_KEY, [])))
        print(f"{path:<20} {dev.name:<45} {kind or ''}")
        dev.close()
    return 0


def _explain(ns) -> int:
    from wayswitch.explain import ExplainError, run_explain

    try:
        return run_explain(ns.word, ns.from_lang, ns.sensitivity, json_output=ns.json)
    except ExplainError as e:
        print(f"wayswitch explain: {e}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.command == "version":
        print(f"wayswitch {__version__}")
        return 0
    if ns.command == "update":
        from wayswitch import updater

        return updater.cli_update(install=ns.install)
    if ns.command in ("run", "dry-run"):
        from wayswitch.daemon import run

        dry = ns.command == "dry-run" or ns.dry_run
        verbose = dry or ns.verbose if ns.command == "run" else True
        from wayswitch.config import ConfigError
        try:
            return run(ns.config, verbose, dry)
        except ConfigError as exc:
            print(_("Invalid configuration: {error}").format(error=exc), file=sys.stderr)
            return 2
    if ns.command == "doctor":
        from wayswitch.doctor import exit_code, format_report, run_checks

        if ns.delay < 0:
            parser.error(_("--delay: seconds must be nonnegative"))
        if ns.delay:
            print(
                _(
                    "Checks start in {ns_delay} s. Focus a regular input field in the "
                    "desired app and keep it focused until checking finishes."
                ).format(ns_delay=ns.delay),
                file=sys.stderr,
                flush=True,
            )
            try:
                time.sleep(ns.delay)
            except OverflowError:
                parser.error(_("--delay: too many seconds"))
        checks = run_checks()
        print(f"WaySwitch {__version__}")
        print(format_report(checks))
        return exit_code(checks)
    if ns.command == "devices":
        return _devices()
    if ns.command == "explain":
        return _explain(ns)
    if ns.command == "waybar":
        return _waybar()
    if ns.command == "gui":
        from wayswitch.gui.app import main as gui_main

        return gui_main()
    try:
        if ns.command in _SELECTION_COMMANDS:
            return _selection(ns.command, getattr(ns, "to_lang", None))
        if ns.command == "pause":
            call_daemon("Pause")
            print(_("paused"))
        elif ns.command == "resume":
            call_daemon("Resume")
            print(_("running"))
        elif ns.command == "toggle":
            (status,) = call_daemon("GetStatus")
            call_daemon("Resume" if status.get("paused") else "Pause")
            print(_("running") if status.get("paused") else _("paused"))
        elif ns.command == "fix":
            (ok,) = call_daemon("FixLastWord")
            print(_("corrected") if ok else _("nothing to correct"))
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
    except PermissionError as e:
        print(e, file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        if getattr(ns, "json", False):
            print('{"running": false}')
            return 2
        print(_("WaySwitch is not responding: {e}").format(e=e), file=sys.stderr)
        print(
            _("Start: systemctl --user start wayswitch; diagnose: wayswitch doctor"),
            file=sys.stderr,
        )
        return 2
    return 0
