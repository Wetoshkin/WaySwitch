"""Сборка демона: устройства, бэкенд, контроллер, D-Bus, охрана сессии — в GLib."""

from __future__ import annotations

import logging
import signal
from pathlib import Path

from wayswitch import config as cfgmod
from wayswitch.actuator import UInputTypist
from wayswitch.backends.select import choose_backend
from wayswitch.controller import Controller
from wayswitch.dbus_service import DaemonService
from wayswitch.detector import SENSITIVITY, Detector, LanguageModel
from wayswitch.keymap import XkbKeymap
from wayswitch.keys import DeviceWatcher
from wayswitch.session import SessionGuard

log = logging.getLogger("wayswitch")


def run(config_path: Path | None, verbose: bool, dry_run: bool) -> int:
    from gi.repository import GLib

    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    config = cfgmod.load(config_path)
    try:
        typist = UInputTypist()
    except OSError as e:
        log.error("не открыть /dev/uinput: %s (правило udev? см. wayswitch doctor)", e)
        return 1
    backend = choose_backend(config.backend.prefer, typist, config.typing.settle_ms)
    log.info("бэкенд раскладки: %s, источники: %s", backend.name,
             [s.xkb_id for s in backend.layouts()])
    if backend.name == "shell":
        from wayswitch.backends.gnome_shell import ShellTypist

        typist_for_controller = ShellTypist(backend)
    else:
        typist_for_controller = typist
    keymap = XkbKeymap.from_names(backend.layouts(), backend.xkb_options())
    models = {}
    for group in range(keymap.num_groups):
        lang = {"ru": "ru", "latin": "en"}.get(keymap.alphabet(group))
        if lang and lang not in models:
            models[lang] = LanguageModel.load(lang)
    if set(models) != {"ru", "en"} or keymap.num_groups != 2:
        log.warning("нужны ровно две раскладки: русская и латинская; авторежим выключен")
        backend.supports_auto = False
    detector = Detector(keymap, models, cfgmod.load_exceptions(),
                        SENSITIVITY[config.general.sensitivity])

    ctx = GLib.MainContext.default()

    def pump() -> None:
        while ctx.pending():
            ctx.iteration(False)

    loop = GLib.MainLoop()
    exit_code = 0

    def on_fatal(code: int) -> None:
        nonlocal exit_code
        exit_code = code
        loop.quit()

    service: DaemonService | None = None
    controller = Controller(
        config, keymap, detector, backend, typist_for_controller, pump=pump, dry_run=dry_run,
        on_corrected=lambda o, f, m: service and service.emit_corrected(o, f, m),
        on_status=lambda s: service and service.emit_status(s),
        persist_exception=cfgmod.add_exception,
    )
    backend.on_change(controller.on_layout_changed)

    def on_key(code: int, value: int, device_id: int) -> None:
        observe = getattr(backend, "observe_physical", None)
        if observe:
            observe(code, value)
        controller.on_key(code, value, device_id)

    watcher = DeviceWatcher(on_key, controller.on_click, controller.on_resync,
                            controller.on_device_gone, controller.set_caps)
    watcher.start()
    if not any(kind == "keyboard" for _, _, kind in watcher.devices()):
        log.error("ни одной клавиатуры не открыто (права на /dev/input?)")
        return 1

    def on_sleep(entering: bool) -> None:
        controller.buffer.reset()
        if not entering:
            watcher.schedule_rescans()

    guard = SessionGuard(controller.set_locked, on_sleep)
    guard.start()

    def reload() -> None:
        try:
            controller.reload_config(cfgmod.load(config_path))
            detector.exceptions.clear()
            detector.exceptions.update(cfgmod.load_exceptions())
            log.info("конфиг перечитан")
        except cfgmod.ConfigError as e:
            log.error("конфиг не перечитан: %s", e)

    service = DaemonService(controller, reload, on_fatal)
    service.start()

    from gi.repository import Gio

    path = config_path or cfgmod.default_path()
    monitor = Gio.File.new_for_path(str(path)).monitor_file(Gio.FileMonitorFlags.NONE, None)
    monitor.connect("changed", lambda *_: reload())

    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, lambda *_: (loop.quit(), False)[1])
    log.info("WaySwitch запущен%s", " (dry-run)" if dry_run else "")
    try:
        loop.run()
    finally:
        watcher.stop()
        typist.close()
    return exit_code
