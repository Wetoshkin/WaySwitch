"""Сборка демона: устройства, бэкенд, контроллер, D-Bus, охрана сессии — в GLib."""

from __future__ import annotations

import logging
import signal
import threading
import time
from pathlib import Path

from wayswitch import a11y as cfgmod_a11y
from wayswitch import config as cfgmod
from wayswitch.actuator import UInputTypist
from wayswitch.backends.base import BackendError
from wayswitch.backends.desktop import detect_desktop, session_summary
from wayswitch.backends.select import choose_backend
from wayswitch.controller import Controller
from wayswitch.dbus_service import DaemonService
from wayswitch.detector import SENSITIVITY, Detector, LanguageModel
from wayswitch.keymap import XkbKeymap
from wayswitch.keys import DeviceWatcher
from wayswitch.session import SessionGuard
from wayswitch.sound import ensure_and_play

log = logging.getLogger("wayswitch")

STARTUP_ATTEMPTS = 12
STARTUP_DELAY = 5
RESTART = 75  # не в RestartPreventExitStatus: systemd перезапустит службу


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
    # При входе служба может стартовать раньше, чем KWin опубликовал раскладки
    # или Shell включил расширение: даём среде минуту, прежде чем сдаться.
    backend = None
    for attempt in range(STARTUP_ATTEMPTS):
        try:
            backend = choose_backend(config.backend.prefer, typist, config.typing.settle_ms,
                                     track_hotkey=config.backend.track_hotkey)
            break
        except BackendError as e:
            if attempt == STARTUP_ATTEMPTS - 1:
                # Перезапуск не поможет: среда та же. Код 4 — в RestartPreventExitStatus.
                log.error("%s. Подробности: wayswitch doctor", e)
                typist.close()
                return 4
            log.info("бэкенд пока недоступен (%s), повтор через %d с", e, STARTUP_DELAY)
            time.sleep(STARTUP_DELAY)
    log.info("среда: %s", session_summary())
    log.info("бэкенд раскладки: %s, источники: %s", backend.name,
             [s.xkb_id for s in backend.layouts()])
    if backend.name in ("kde", "sway", "hyprland"):
        # Расширения нет: печатаем сканкодами через uinput. Раскладку
        # переключает композитор, паузу после переключения ждёт wait_applied.
        log.info("печать символов: uinput (бэкенд %s)", backend.name)
        typist_for_controller = typist
    elif backend.name == "shell" and config.backend.shell_typing == "uinput":
        # Печать сканкодами через uinput, хотя раскладку переключает расширение:
        # обход того, что виртуальное устройство Mutter теряет последнюю клавишу
        # серии (пробел после исправленного слова). Сканкоды требуют уже
        # применённой раскладки — SetLayout синхронен внутри Shell.
        log.info("печать символов: uinput (shell_typing=uinput)")
        typist_for_controller = typist
    elif backend.name == "shell":
        from wayswitch.backends.gnome_shell import ShellTypist

        typist_for_controller = ShellTypist(backend)
    else:
        typist_for_controller = typist
        if backend.name == "ibus" and not backend.verify_layout():
            # На GNOME 46+ глобальный движок IBus — не то же самое, что
            # раскладка компоузитора: set_global_engine() меняет движок, а
            # раскладка ввода остаётся прежней. Демон тогда уверен, что
            # переключил, стирает слово и печатает его в старой раскладке —
            # на экране не меняется ничего.
            log.error(
                "IBus-движок расходится с раскладкой GNOME (источники: %s): на этом "
                "GNOME IBus не переключает раскладку, автоисправление сломало бы "
                "текст. Включите расширение wayswitch@siberia.ru "
                "(gnome-extensions enable wayswitch@siberia.ru) и перезапустите "
                "демон: systemctl --user restart wayswitch",
                [s.xkb_id for s in backend.layouts()])
            backend.supports_auto = False
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

    def restart(reason: str) -> None:
        """Выйти с кодом RESTART: systemd (Restart=on-failure) поднимет службу
        заново, и она выберет бэкенд и раскладки с нуля."""
        log.info("перезапуск: %s", reason)
        on_fatal(RESTART)

    # Список раскладок поменялся — индексы сдвинулись, keymap устарел.
    backend.on_layouts_changed = lambda: restart("список раскладок изменился")
    if backend.name in ("ibus", "hotkey") and detect_desktop() == "gnome":
        # Стартовали раньше расширения GNOME Shell: когда оно появится —
        # перейти на него (без него на GNOME 46+ автоисправления нет).
        from gi.repository import Gio

        from wayswitch.backends.gnome_shell import BUS_NAME as SHELL_NAME

        Gio.bus_watch_name(Gio.BusType.SESSION, SHELL_NAME, Gio.BusNameWatcherFlags.NONE,
                           lambda *_: restart("появилось расширение GNOME Shell"), None)

    service: DaemonService | None = None
    # Сведения о фокусе (приложение, поле пароля) нужны и автоисправлению, и
    # ручным жестам: пароль, исправленный двойным Shift, не должен уйти в
    # историю окна. Поток опроса стартует лениво, на первой клавише.
    probe = cfgmod_a11y.make_probe(enabled=True)

    last_beep = [0.0]

    def _corrected(old: str, new: str, manual: bool) -> None:
        """Событие исправления: наружу в D-Bus, при желании — со звуком."""
        if service is not None:
            service.emit_corrected(old, new, manual)
        # controller.config, а не config из замыкания: после Reload настройка
        # звука должна действовать сразу, без перезапуска демона.
        now = time.monotonic()
        if controller.config.general.sound_on_fix and not manual \
                and now - last_beep[0] >= 0.1:
            last_beep[0] = now
            # Сигнал в отдельном потоке: плеер не должен задерживать ввод.
            threading.Thread(
                target=ensure_and_play, args=(cfgmod.config_dir(),), daemon=True).start()
    controller = Controller(
        config, keymap, detector, backend, typist_for_controller, pump=pump, dry_run=dry_run,
        on_corrected=lambda o, f, m: _corrected(o, f, m),
        on_status=lambda s: service and service.emit_status(s),
        persist_exception=cfgmod.add_exception,
        probe=probe,
        apps_provider=cfgmod.load_apps,
        # Исполнение фикса — вне диспатча evdev-источника: он не реентрантен, и
        # пока его колбэк не вернулся, события той же клавиатуры (отпускание
        # клавиш, прерывающее нажатие) не доставляются. См. Controller.__init__.
        schedule=lambda fn: GLib.idle_add(lambda: (fn(), False)[1]),
    )
    backend.on_change(controller.on_layout_changed)

    def on_key(code: int, value: int, device_id: int) -> None:
        observe = getattr(backend, "observe_physical", None)
        if observe:
            observe(code, value)
        controller.on_key(code, value, device_id)

    watcher = DeviceWatcher(on_key, controller.on_click, controller.on_resync,
                            controller.on_device_gone, controller.set_caps,
                            on_scroll=controller.on_scroll)
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
            new_config = cfgmod.load(config_path)
            controller.reload_config(new_config)
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
    # Одно сохранение — несколько событий (os.replace: DELETED/CREATED/…,
    # редактор: CHANGED×N). Перечитываем один раз — по CHANGES_DONE_HINT,
    # который GLib шлёт после серии изменений (и синтетически после rename);
    # если этого события в биндинге нет (очень старый GLib) — по CHANGED.
    done_event = getattr(Gio.FileMonitorEvent, "CHANGES_DONE_HINT", Gio.FileMonitorEvent.CHANGED)

    def on_config_changed(_monitor, _file, _other, event_type) -> None:
        if event_type == done_event:
            reload()

    monitor.connect("changed", on_config_changed)

    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, lambda *_: (loop.quit(), False)[1])
    log.info("WaySwitch запущен%s", " (dry-run)" if dry_run else "")
    try:
        loop.run()
    finally:
        watcher.stop()
        typist.close()
    return exit_code
