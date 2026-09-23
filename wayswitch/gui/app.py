"""Окно настроек WaySwitch на GTK4/libadwaita и запуск трея."""

from __future__ import annotations

import sys

from wayswitch import __version__
from wayswitch import config as cfgmod
from wayswitch.gui import autostart
from wayswitch.gui.daemon_proxy import DaemonProxy
from wayswitch.gui.tray import StatusNotifier

APP_ID = "ru.siberia.WaySwitch"


def layout_label(index) -> str:
    """Подпись строки «Раскладка» по layout_index из GetStatus.

    Демон отдаёт -1 (None в контроллере), когда бэкенд не знает текущую
    группу — показывать «индекс -1» бессмысленно. Чистая функция.
    """
    if index is None or index < 0:
        return "неизвестно"
    return f"индекс {index}"


def parse_gui_args(argv: list[str]) -> bool:
    """Разобрать argv GUI: единственный поддерживаемый флаг — ``--tray``.

    Возвращает True, если запрошен запуск без окна (только значок в трее).
    Чистая функция — не трогает ни gi, ни sys.argv, годится для юнит-теста.
    """
    return "--tray" in argv


def main(argv: list[str] | None = None) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, Gtk

    argv = sys.argv[1:] if argv is None else argv
    tray_only = parse_gui_args(argv)

    class Window(Adw.ApplicationWindow):
        def __init__(self, app, proxy: DaemonProxy):
            super().__init__(application=app, title="WaySwitch")
            self.set_default_size(560, 640)
            self.proxy = proxy
            self.cfg = cfgmod.load()
            toolbar = Adw.ToolbarView()
            header = Adw.HeaderBar()
            self.stack = Adw.ViewStack()
            switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
            header.set_title_widget(switcher)
            toolbar.add_top_bar(header)
            toolbar.set_content(self.stack)
            self.set_content(toolbar)
            self.stack.add_titled_with_icon(self._status_page(), "status", "Статус",
                                            "dialog-information-symbolic")
            self.stack.add_titled_with_icon(self._settings_page(), "settings", "Настройки",
                                            "preferences-system-symbolic")
            self.stack.add_titled_with_icon(self._exceptions_page(), "exceptions", "Исключения",
                                            "edit-clear-all-symbolic")
            self.stack.add_titled_with_icon(self._autostart_page(), "autostart", "Автозапуск",
                                            "system-run-symbolic")
            self.render_status(proxy.status)

        # --- Статус ---------------------------------------------------------------
        def _status_page(self):
            page = Adw.PreferencesPage()
            group = Adw.PreferencesGroup(title="Демон")
            self.row_state = Adw.ActionRow(title="Состояние")
            self.row_backend = Adw.ActionRow(title="Бэкенд раскладки")
            self.row_layout = Adw.ActionRow(title="Текущая раскладка")
            self.row_count = Adw.ActionRow(title="Исправлений за сессию")
            self.btn_pause = Gtk.Button(label="Пауза", valign=Gtk.Align.CENTER)
            self.btn_pause.connect("clicked", lambda *_: self._toggle_pause())
            self.row_state.add_suffix(self.btn_pause)
            for r in (self.row_state, self.row_backend, self.row_layout, self.row_count):
                group.add(r)
            page.add(group)
            test = Adw.PreferencesGroup(title="Проверка",
                                        description="Наберите здесь «ghbdtn » — слово должно "
                                                    "исправиться в «привет».")
            test.add(Adw.EntryRow(title="Тестовое поле"))
            page.add(test)
            log_group = Adw.PreferencesGroup(title="Последние исправления")
            self.log_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.log_box.add_css_class("boxed-list")
            log_group.add(self.log_box)
            page.add(log_group)
            return page

        def render_status(self, status: dict | None):
            if status is None:
                self.row_state.set_subtitle("демон не запущен — systemctl --user start wayswitch")
                self.btn_pause.set_sensitive(False)
                return
            self.btn_pause.set_sensitive(True)
            self.btn_pause.set_label("Возобновить" if status.get("paused") else "Пауза")
            state = "пауза" if status.get("paused") else \
                "заблокировано" if status.get("locked") else \
                "работает" if status.get("active") else "только ручной режим"
            self.row_state.set_subtitle(state + (" (dry-run)" if status.get("dry_run") else ""))
            self.row_backend.set_subtitle(str(status.get("backend", "?")))
            self.row_layout.set_subtitle(layout_label(status.get("layout_index")))
            self.row_count.set_subtitle(
                f"авто {status.get('corrections_total', 0)}, вручную "
                f"{status.get('manual_total', 0)}, откатов {status.get('undo_total', 0)}")

        def add_correction(self, original: str, fixed: str, manual: bool):
            row = Adw.ActionRow(title=f"{original} → {fixed}",
                                subtitle="вручную" if manual else "автоматически")
            self.log_box.prepend(row)
            while (child := self.log_box.get_row_at_index(20)) is not None:
                self.log_box.remove(child)

        def _toggle_pause(self):
            st = self.proxy.status or {}
            self.proxy.call("Resume" if st.get("paused") else "Pause")

        # --- Настройки --------------------------------------------------------------
        def _settings_page(self):
            page = Adw.PreferencesPage()
            g = Adw.PreferencesGroup(title="Автоисправление")
            self.sw_auto = Adw.SwitchRow(title="Исправлять автоматически",
                                         subtitle="На пробеле после слова")
            self.sw_auto.set_active(self.cfg.general.auto_correct)
            g.add(self.sw_auto)
            self.cb_sens = Adw.ComboRow(title="Чувствительность")
            names = Gtk.StringList.new(["Осторожная", "Обычная", "Агрессивная"])
            self.cb_sens.set_model(names)
            self.cb_sens.set_selected(cfgmod.SENSITIVITY_NAMES.index(self.cfg.general.sensitivity))
            g.add(self.cb_sens)
            page.add(g)
            g2 = Adw.PreferencesGroup(title="Ручное исправление")
            self.cb_manual = Adw.ComboRow(title="Жест")
            self.cb_manual.set_model(Gtk.StringList.new(
                ["Двойной Shift (тройной — фраза)", "Клавиша Pause", "Выключено"]))
            self.cb_manual.set_selected(cfgmod.MANUAL_MODES.index(self.cfg.gesture.manual))
            g2.add(self.cb_manual)
            self.sp_undo = Adw.SpinRow.new_with_range(0, 60, 1)
            self.sp_undo.set_title("Окно отката, с")
            self.sp_undo.set_subtitle("Жест в это время возвращает слово и учит исключение")
            # undo_window_sec — float (например, 5.0/0.5); без set_digits
            # SpinRow молча округляет ввод до целого.
            self.sp_undo.set_digits(1)
            self.sp_undo.set_value(self.cfg.general.undo_window_sec)
            g2.add(self.sp_undo)
            page.add(g2)
            g3 = Adw.PreferencesGroup(title="Тонкая настройка")
            self.sp_settle = Adw.SpinRow.new_with_range(0, 2000, 5)
            self.sp_settle.set_title("Запас после смены раскладки, мс")
            self.sp_settle.set_value(self.cfg.typing.settle_ms)
            g3.add(self.sp_settle)
            self.sp_delay = Adw.SpinRow.new_with_range(0, 100, 1)
            self.sp_delay.set_title("Пауза между клавишами, мс")
            self.sp_delay.set_value(self.cfg.typing.key_delay_ms)
            g3.add(self.sp_delay)
            self.cb_backend = Adw.ComboRow(title="Бэкенд раскладки")
            self.cb_backend.set_model(Gtk.StringList.new(
                ["Автовыбор", "Расширение GNOME Shell", "IBus", "Системный хоткей"]))
            self.cb_backend.set_selected(cfgmod.BACKEND_PREFS.index(self.cfg.backend.prefer))
            g3.add(self.cb_backend)
            page.add(g3)
            gs = Adw.PreferencesGroup()
            btn = Gtk.Button(label="Сохранить", halign=Gtk.Align.END)
            btn.add_css_class("suggested-action")
            btn.connect("clicked", lambda *_: self._save())
            gs.add(btn)
            page.add(gs)
            return page

        def _save(self):
            c = self.cfg
            c.general.auto_correct = self.sw_auto.get_active()
            c.general.sensitivity = cfgmod.SENSITIVITY_NAMES[self.cb_sens.get_selected()]
            c.gesture.manual = cfgmod.MANUAL_MODES[self.cb_manual.get_selected()]
            c.general.undo_window_sec = float(self.sp_undo.get_value())
            c.typing.settle_ms = int(self.sp_settle.get_value())
            c.typing.key_delay_ms = int(self.sp_delay.get_value())
            c.backend.prefer = cfgmod.BACKEND_PREFS[self.cb_backend.get_selected()]
            cfgmod.save(c)
            self.proxy.call("Reload")

        # --- Исключения -------------------------------------------------------------
        def _exceptions_page(self):
            page = Adw.PreferencesPage()
            g = Adw.PreferencesGroup(title="Слова, которые не исправляются",
                                     description="Попадают сюда после отката жестом")
            self.entry_exc = Adw.EntryRow(title="Добавить слово")
            self.entry_exc.connect("apply", lambda *_: self._add_exception())
            self.entry_exc.set_show_apply_button(True)
            g.add(self.entry_exc)
            self.exc_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.exc_box.add_css_class("boxed-list")
            g.add(self.exc_box)
            page.add(g)
            self._render_exceptions()
            return page

        def _render_exceptions(self):
            while (child := self.exc_box.get_row_at_index(0)) is not None:
                self.exc_box.remove(child)
            for word in sorted(cfgmod.load_exceptions()):
                row = Adw.ActionRow(title=word)
                btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
                btn.add_css_class("flat")
                btn.connect("clicked", lambda _b, w=word: self._remove_exception(w))
                row.add_suffix(btn)
                self.exc_box.append(row)

        def _add_exception(self):
            word = self.entry_exc.get_text().strip()
            if word:
                cfgmod.add_exception(word)
                self.entry_exc.set_text("")
                self._render_exceptions()
                self.proxy.call("Reload")

        def _remove_exception(self, word: str):
            cfgmod.remove_exception(word)
            self._render_exceptions()
            self.proxy.call("Reload")

        # --- Автозапуск -------------------------------------------------------------
        def _autostart_page(self):
            page = Adw.PreferencesPage()
            g = Adw.PreferencesGroup(title="Автозапуск")
            sw1 = Adw.SwitchRow(title="Демон при входе в систему",
                                subtitle="systemctl --user enable wayswitch")
            sw1.set_active(autostart.is_service_enabled())
            sw1.connect("notify::active",
                       lambda r, _: autostart.set_service_enabled(r.get_active()))
            g.add(sw1)
            sw2 = Adw.SwitchRow(title="Значок в трее при входе",
                                subtitle="Нужно расширение AppIndicator (в Ubuntu есть)")
            sw2.set_active(autostart.is_tray_enabled())
            sw2.connect("notify::active", lambda r, _: autostart.set_tray_enabled(r.get_active()))
            g.add(sw2)
            page.add(g)
            about = Adw.PreferencesGroup(title="О программе")
            about.add(Adw.ActionRow(title="WaySwitch", subtitle=f"версия {__version__}"))
            page.add(about)
            return page

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
            self.window: Window | None = None
            self.proxy = DaemonProxy(self._on_status, self._on_corrected)
            self.tray = StatusNotifier(
                on_toggle_auto=self._toggle_auto,
                on_pause=lambda: self.proxy.call(
                    "Resume" if (self.proxy.status or {}).get("paused") else "Pause"),
                on_fix=lambda: self.proxy.call("FixLastWord"),
                on_settings=self.show_window, on_quit=self.quit)

        def do_startup(self):
            Adw.Application.do_startup(self)
            self.proxy.start()
            self.tray.start()
            if tray_only:
                self.hold()  # живём без окна

        def do_activate(self):
            if not tray_only or self.window:
                self.show_window()

        def show_window(self):
            if self.window is None:
                self.window = Window(self, self.proxy)
                self.window.connect("close-request", self._on_close)
            self.window.present()

        def _on_close(self, *_):
            if tray_only:
                self.window.set_visible(False)
                return True
            return False

        def _on_status(self, status):
            self.tray.update(status)
            if self.window:
                self.window.render_status(status)

        def _on_corrected(self, original, fixed, manual):
            if self.window:
                self.window.add_correction(original, fixed, manual)

        def _toggle_auto(self):
            cfg = cfgmod.load()
            cfg.general.auto_correct = not cfg.general.auto_correct
            cfgmod.save(cfg)
            self.proxy.call("Reload")

    # Приложение не умеет открывать файлы (FLAGS_NONE): любой позиционный
    # аргумент кроме имени программы приводит к ошибке GApplication
    # («This application can not open files») и коду выхода 1. --tray уже
    # разобран выше — в run() он и всё остальное из argv не передаётся.
    return App().run([sys.argv[0]])
