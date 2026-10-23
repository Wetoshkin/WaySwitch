"""Приложение WaySwitch на GTK4/libadwaita: главное окно, параметры, значок в
панели, проверка обновлений.

Окно и значок — один процесс (GApplication с APP_ID): повторный запуск
``wayswitch-gui`` при живом значке не плодит второй экземпляр, а показывает
окно уже работающего.
"""

from __future__ import annotations

import sys

from wayswitch import __version__, updates
from wayswitch import config as cfgmod
from wayswitch.gui import autostart, texts
from wayswitch.gui.daemon_proxy import DaemonProxy
from wayswitch.gui.dialogs import REPO_URL  # noqa: F401 — реэкспорт для тестов
from wayswitch.gui.tray import StatusNotifier

# Своё имя, отдельное от демона (ru.siberia.WaySwitch): с общим GApplication
# пытается зарегистрироваться на уже занятом имени, не находит там org.gtk.Actions
# и падает с «Failed to register», не показав окна. С отдельным именем окно
# открывается, а демон остаётся доступен через DaemonProxy.
APP_ID = "ru.siberia.WaySwitch.Settings"

# Первая проверка обновлений — не сразу после входа: сеть ещё поднимается.
UPDATE_FIRST_DELAY_SEC = 20
UPDATE_PERIOD_SEC = 6 * 3600

ACCELS = {
    "app.preferences": ["<Control>comma"],
    "app.shortcuts": ["<Control>question"],
    "app.pause": ["<Control>p"],
    "app.quit": ["<Control>q"],
    "window.close": ["<Control>w"],
}


def parse_gui_args(argv: list[str]) -> bool:
    """Разобрать argv GUI: единственный поддерживаемый флаг — ``--tray``.

    Возвращает True, если запрошен запуск без окна (только значок в панели).
    Чистая функция — не трогает ни gi, ни sys.argv, годится для юнит-теста.
    """
    return "--tray" in argv


def update_text(release, checked: bool) -> str:
    """Подпись строки «Проверить сейчас» в Параметрах → Система."""
    if release is not None:
        return f"Доступна версия {release.version} (сейчас {__version__})"
    if checked:
        return f"Установлена последняя версия ({__version__})"
    return f"Версия {__version__}"


def _open_uri(uri: str) -> None:
    from gi.repository import Gio

    Gio.AppInfo.launch_default_for_uri(uri, None)


def main(argv: list[str] | None = None) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib

    argv = sys.argv[1:] if argv is None else argv
    tray_only = parse_gui_args(argv)

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
            self.history = texts.History()
            self.update: updates.Release | None = None
            self._update_checked = False
            self._main = None
            self._onboarding = None
            self._stopping = False
            self._was_running = False
            self.proxy = DaemonProxy(self._on_status, self._on_corrected)
            self.tray = StatusNotifier({
                "auto": self._toggle_auto,
                "pause": self._toggle_pause,
                "fix": lambda: self.proxy.call("FixLastWord"),
                "fix_selection": lambda: self.proxy.call("FixSelection"),
                "update": self.open_release,
                "settings": self.show_window,
                "quit": self._ask_quit,
            })

        # --- жизненный цикл ----------------------------------------------------

        def do_startup(self):
            Adw.Application.do_startup(self)
            from wayswitch.gui import style

            style.install()
            for name, handler in (("preferences", self.show_preferences),
                                  ("shortcuts", self.show_shortcuts),
                                  ("about", self.show_about),
                                  ("pause", self._toggle_pause),
                                  ("quit", self._ask_quit),
                                  ("show-window", self.show_window)):
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", lambda *_a, h=handler: h())
                self.add_action(action)
            for action, accels in ACCELS.items():
                self.set_accels_for_action(action, accels)
            self.proxy.start()
            self.tray.start()
            if tray_only:
                self.hold()  # живём без окна
            GLib.timeout_add_seconds(UPDATE_FIRST_DELAY_SEC, self._periodic_update)

        def do_activate(self):
            if not tray_only or self._main is not None:
                self.show_window()

        # --- окна ----------------------------------------------------------------

        def show_window(self):
            from wayswitch.gui import onboarding

            if self._onboarding is not None:
                self._onboarding.present()
                return
            if self._main is None and onboarding.needed():
                self._onboarding = onboarding.Onboarding(self, self._after_onboarding)
                self._onboarding.present()
                return
            self._ensure_main().present()

        def _after_onboarding(self):
            self._onboarding = None
            self._ensure_main().present()

        def _ensure_main(self):
            from wayswitch.gui.window import MainWindow

            if self._main is None:
                self._main = MainWindow(self)
                self._main.window.connect("close-request", self._on_close)
            return self._main

        def _on_close(self, *_):
            if tray_only:
                # Значок остаётся — окно просто прячем, история не теряется.
                self._main.window.set_visible(False)
                return True
            self._main = None
            return False

        def _parent(self):
            return self._ensure_main().window

        def show_preferences(self):
            from wayswitch.gui.preferences import Preferences

            parent = self._parent()
            self._ensure_main().present()
            Preferences(self).present(parent)

        def show_shortcuts(self):
            from wayswitch.gui import dialogs

            try:
                cfg = cfgmod.load()
            except cfgmod.ConfigError:
                cfg = cfgmod.Config()
            self._ensure_main().present()
            dialogs.shortcuts_dialog(self._parent(), cfg)

        def show_about(self):
            from wayswitch.gui import dialogs

            self._ensure_main().present()
            dialogs.about_dialog(self._parent(), self.update)

        def _ask_quit(self):
            from wayswitch.gui import dialogs

            self._ensure_main().present()
            dialogs.quit_dialog(self._parent(), self._quit_background, self._quit_stop)

        def _quit_background(self):
            # Служба продолжает исправлять; закрываем только окно (и значок,
            # если это не процесс значка).
            if self._main is not None:
                self._main.window.close()

        def _quit_stop(self):
            self._stopping = True
            autostart.stop_service()
            self.quit()

        # --- демон ---------------------------------------------------------------

        def _on_status(self, status):
            if status is None and self._was_running and not self._stopping:
                self._notify_stopped()
            self._was_running = status is not None
            self.tray.update(status, self.update.version if self.update else None)
            if self._main is not None:
                self._main.render_status(status)

        def _notify_stopped(self):
            note = Gio.Notification.new("Исправление перестало работать")
            note.set_body("Служба WaySwitch остановилась. Откройте WaySwitch, чтобы "
                          "запустить её снова и посмотреть журнал.")
            note.set_default_action("app.show-window")
            note.add_button("Открыть", "app.show-window")
            self.send_notification("stopped", note)

        def _on_corrected(self, original, fixed, manual):
            self.history.add(original, fixed, manual)
            if self._main is not None:
                self._main.on_corrected(original, fixed, manual)

        def _toggle_pause(self):
            st = self.proxy.status or {}
            self.proxy.call("Resume" if st.get("paused") else "Pause")

        def _toggle_auto(self):
            try:
                cfg = cfgmod.load()
            except cfgmod.ConfigError:
                return
            cfg.general.auto_correct = not cfg.general.auto_correct
            cfgmod.save(cfg)
            self.proxy.call("Reload")

        # --- обновления ----------------------------------------------------------

        def _periodic_update(self):
            self.check_updates(force=False)
            GLib.timeout_add_seconds(UPDATE_PERIOD_SEC, self._periodic_update)
            return GLib.SOURCE_REMOVE

        def check_updates(self, force: bool, done=None):
            from wayswitch.gui.widgets import run_in_thread

            try:
                enabled = cfgmod.load().general.check_updates
            except cfgmod.ConfigError:
                enabled = True
            if not (enabled or force):
                if done:
                    done(None)
                return

            def finished(result):
                if not isinstance(result, Exception):
                    self.update = result
                    self._update_checked = True
                    if result is not None and updates.should_notify(result.version):
                        self._notify_update(result)
                self.tray.update(self.proxy.status,
                                 self.update.version if self.update else None)
                if self._main is not None:
                    self._main.render_update(self.update)
                if done:
                    done(result)

            run_in_thread(lambda: updates.check(__version__, force=force), finished)

        def _notify_update(self, release):
            note = Gio.Notification.new(f"Вышла WaySwitch {release.version}")
            note.set_body("Откройте страницу релиза — там пакет и инструкция по обновлению.")
            note.set_default_action("app.show-window")
            self.send_notification("update", note)

        def update_text(self, checked: bool = False) -> str:
            return update_text(self.update, checked or self._update_checked)

        def open_release(self):
            _open_uri(self.update.url if self.update else updates.RELEASES_URL)

    # Приложение не умеет открывать файлы (FLAGS_NONE): любой позиционный
    # аргумент кроме имени программы приводит к ошибке GApplication
    # («This application can not open files») и коду выхода 1. --tray уже
    # разобран выше — в run() он и всё остальное из argv не передаётся.
    return App().run([sys.argv[0]])
