"""Окно настроек WaySwitch на GTK4/libadwaita и запуск трея."""

from __future__ import annotations

import sys

from wayswitch import __version__, a11y
from wayswitch import config as cfgmod
from wayswitch.gui import autostart
from wayswitch.gui.daemon_proxy import DaemonProxy
from wayswitch.gui.tray import StatusNotifier

# Своё имя, отдельное от демона (ru.siberia.WaySwitch): с общим GApplication
# пытается зарегистрироваться на уже занятом имени, не находит там org.gtk.Actions
# и падает с «Failed to register», не показав окна. С отдельным именем окно
# открывается, а демон остаётся доступен через DaemonProxy.
APP_ID = "ru.siberia.WaySwitch.Settings"
REPO_URL = "https://github.com/Wetoshkin/WaySwitch"


def window_size_for(pages: list[tuple[int, int]], screen: tuple[int, int] | None = None,
                    margin: int = 48) -> tuple[int, int]:
    """Размер окна по содержимому страниц.

    Раньше окно всегда открывалось 560x640, а страница настроек выше — её
    приходилось тянуть руками. Берём natural-размер каждой страницы, добавляем
    поля и ограничиваем экраном, чтобы окно не упиралось в панели.
    """
    width = max((w for w, _h in pages), default=560) + margin
    height = max((h for _w, h in pages), default=640) + margin
    if screen is not None:
        screen_w, screen_h = screen
        width = min(width, int(screen_w * 0.9))
        height = min(height, int(screen_h * 0.85))
    # Минимум — прежняя ширина окна: тексты подсказок длинные, уже 560 некрасиво.
    return max(width, 560), max(height, 480)


def layout_label(index) -> str:
    """Подпись строки «Раскладка» по layout_index из GetStatus.

    Демон отдаёт -1 (None в контроллере), когда бэкенд не знает текущую
    группу — показывать «индекс -1» бессмысленно. Чистая функция.
    """
    if index is None or index < 0:
        return "неизвестно"
    return f"индекс {index}"


def _open_uri(uri: str) -> None:
    """Открыть ссылку системным браузером (без gi до вызова)."""
    from gi.repository import Gio

    Gio.AppInfo.launch_default_for_uri(uri, None)


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
            self._header = header
            menu = Gio.Menu()
            menu.append("О программе WaySwitch", "app.about")
            menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                        tooltip_text="Меню")
            # Подпись для скринридеров: у кнопки-иконки своего текста нет.
            menu_button.update_property([Gtk.AccessibleProperty.LABEL], ["Меню"])
            header.pack_end(menu_button)
            toolbar.add_top_bar(header)
            self.toasts = Adw.ToastOverlay()
            self.toasts.set_child(self.stack)
            toolbar.set_content(self.toasts)
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
            # После первого показа мерим страницы и подгоняем окно под них.
            self.connect("map", lambda *_: self._apply_size())

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
                ["Двойной Shift (тройной — фраза)", "Одиночный тап клавиши",
                 "Клавиша Pause", "Выключено"]))
            self.cb_manual.set_selected(cfgmod.MANUAL_MODES.index(self.cfg.gesture.manual))
            self.cb_manual.connect("notify::selected",
                                   lambda *_: self._update_tap_key_sensitivity())
            g2.add(self.cb_manual)
            self.er_tap = Adw.EntryRow(title="Клавиша для тапа")
            self.er_tap.set_text(self.cfg.gesture.tap_key)
            # Поле не для ручного ввода: клавиша выбирается на экранной
            # клавиатуре, чтобы нельзя было вписать букву или опечатку.
            self.er_tap.set_editable(False)
            self.er_tap.set_tooltip_text("Нажмите кнопку справа и выберите клавишу")
            self._update_tap_key_sensitivity()
            g2.add(self.er_tap)
            # Отдельная строка, а не суффикс EntryRow: суффикс не попадает в дерево
            # доступности, и кнопку не находят ни скринридеры, ни автопроверка.
            self.row_pick = Adw.ActionRow(title="Выбрать клавишу",
                                          subtitle="Экранная клавиатура: нажать нужную")
            self.btn_pick = Gtk.Button(label="Выбрать", valign=Gtk.Align.CENTER)
            self.btn_pick.connect("clicked", lambda *_: self._pick_tap_key())
            self.row_pick.add_suffix(self.btn_pick)
            g2.add(self.row_pick)
            self.sp_undo = Adw.SpinRow.new_with_range(0, 60, 1)
            self.sp_undo.set_title("Окно отката, с")
            self.sp_undo.set_subtitle("Жест в это время возвращает слово и учит исключение")
            # undo_window_sec — float (например, 5.0/0.5); без set_digits
            # SpinRow молча округляет ввод до целого.
            self.sp_undo.set_digits(1)
            self.sp_undo.set_value(self.cfg.general.undo_window_sec)
            g2.add(self.sp_undo)
            page.add(g2)
            gr = Adw.PreferencesGroup(
                title="Правила",
                description="Как в Punto Switcher: что чинить, а где не мешать")
            self.sw_punct = Adw.SwitchRow(title="Знаки препинания из другой раскладки",
                                          subtitle="«пирогб» → «пирог,», «рубю» → «руб.»")
            self.sw_punct.set_active(self.cfg.general.punct_fix)
            gr.add(self.sw_punct)
            self.sw_split = Adw.SwitchRow(title="Слипшиеся слова",
                                          subtitle="«сонатуБаха» → «сонату Баха» (по регистру)")
            self.sw_split.set_active(self.cfg.general.split_words_fix)
            gr.add(self.sw_split)
            self.sw_particles = Adw.SwitchRow(title="Слипшиеся частицы",
                                              subtitle="«чтото» → «что-то», «кембы» → «кем бы»")
            self.sw_particles.set_active(self.cfg.general.particles_fix)
            gr.add(self.sw_particles)
            self.sw_password = Adw.SwitchRow(title="Не трогать поля паролей",
                                             subtitle="Определяется через at-spi: роль поля")
            self.sw_password.set_active(self.cfg.general.skip_password_fields)
            gr.add(self.sw_password)
            self.sp_guard = Adw.SpinRow.new_with_range(0, 10, 0.5)
            self.sp_guard.set_title("Тишина после правки текста, с")
            self.sp_guard.set_subtitle("После Backspace, стрелок и ручной смены раскладки")
            self.sp_guard.set_digits(1)
            self.sp_guard.set_value(self.cfg.general.edit_guard_sec)
            gr.add(self.sp_guard)
            self.sw_sound = Adw.SwitchRow(title="Звуковой сигнал после исправления",
                                          subtitle="Короткий щелчок, как в Punto Switcher")
            self.sw_sound.set_active(self.cfg.general.sound_on_fix)
            gr.add(self.sw_sound)
            self.sw_tab = Adw.SwitchRow(
                title="Исправлять слово перед Tab",
                subtitle="Осторожно: Tab в терминале выбирает автодополнение")
            self.sw_tab.set_active(self.cfg.general.fix_on_tab)
            gr.add(self.sw_tab)
            self.sw_enter = Adw.SwitchRow(
                title="Исправлять слово перед Enter",
                subtitle="Автодополнение в IDE тоже срабатывает на Enter")
            self.sw_enter.set_active(self.cfg.general.fix_on_enter)
            gr.add(self.sw_enter)
            page.add(gr)
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
                ["Автовыбор", "Расширение GNOME Shell", "KDE Plasma (D-Bus KWin)",
                 "IBus", "Системный хоткей"]))
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

        def _apply_size(self):
            """Подогнать окно под содержимое страниц (один раз после показа)."""
            pages = []
            model = self.stack.get_pages()
            for i in range(model.get_n_items()):
                child = model.get_item(i).get_child()
                if child is None:
                    continue
                width = child.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
                height = child.measure(Gtk.Orientation.VERTICAL, -1)[1]
                pages.append((width, height))
            screen = None
            display = self.get_display()
            if display is not None:
                monitors = display.get_monitors()
                if monitors is not None and monitors.get_n_items():
                    geometry = monitors.get_item(0).get_geometry()
                    screen = (geometry.width, geometry.height)
            # Ширину задаёт ещё и шапка: ViewSwitcher с четырьмя вкладками шире
            # иной страницы, и при измерении одних страниц вкладки не вмещались.
            header_width = self._header.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            pages.append((header_width, 0))
            width, height = window_size_for(pages, screen)
            self.set_default_size(width, height)

        def _pick_tap_key(self):
            from wayswitch.gui.keyboard_picker import build_picker_window

            # Одно окно на все нажатия: иначе кнопка «Выбрать» открывает стопку
            # одинаковых модальных окон.
            existing = getattr(self, "_pick_window", None)
            if existing is not None and existing.get_visible():
                existing.present()
                return
            self._pick_window = build_picker_window(
                self, self.er_tap.get_text().strip().lower(), self._on_tap_key_picked)
            self._pick_window.present()

        def _on_tap_key_picked(self, name: str):
            self.er_tap.set_text(name)

        def _update_tap_key_sensitivity(self):
            # Поле клавиши имеет смысл только в режиме одиночного тапа.
            active = cfgmod.MANUAL_MODES[self.cb_manual.get_selected()] == "tap_key"
            self.er_tap.set_sensitive(active)

        def _save(self):
            c = self.cfg
            c.general.auto_correct = self.sw_auto.get_active()
            c.general.sensitivity = cfgmod.SENSITIVITY_NAMES[self.cb_sens.get_selected()]
            c.gesture.manual = cfgmod.MANUAL_MODES[self.cb_manual.get_selected()]
            # Неизвестное имя клавиши не сохраняем: иначе демон отвергнет конфиг
            # целиком. Поле показывает, что реально записалось.
            tap_key = (self.er_tap.get_text() or "").strip().lower()
            c.gesture.tap_key = tap_key if tap_key in cfgmod.KEY_NAMES else "leftctrl"
            self.er_tap.set_text(c.gesture.tap_key)
            c.general.undo_window_sec = float(self.sp_undo.get_value())
            c.general.punct_fix = self.sw_punct.get_active()
            c.general.particles_fix = self.sw_particles.get_active()
            c.general.split_words_fix = self.sw_split.get_active()
            c.general.skip_password_fields = self.sw_password.get_active()
            c.general.edit_guard_sec = float(self.sp_guard.get_value())
            c.general.fix_on_tab = self.sw_tab.get_active()
            c.general.fix_on_enter = self.sw_enter.get_active()
            c.general.sound_on_fix = self.sw_sound.get_active()
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

            # Приложения: сюда попадает всё, где автоисправление мешает —
            # самой частой болью пользователей Punto Switcher были именно
            # «лезет в Telegram / IDE / консоль».
            ga = Adw.PreferencesGroup(
                title="Программы, где не исправлять",
                description="Сравнение по вхождению без регистра: «telegram» "
                            "подойдёт и для TelegramDesktop")
            self.entry_app = Adw.EntryRow(title="Добавить программу")
            self.entry_app.connect("apply", lambda *_: self._add_app())
            self.entry_app.set_show_apply_button(True)
            ga.add(self.entry_app)
            add_current = Adw.ActionRow(
                title="Добавить текущую программу",
                subtitle="Ту, в которой сейчас окно на переднем плане")
            add_btn = Gtk.Button(label="Добавить", valign=Gtk.Align.CENTER)
            add_btn.connect("clicked", lambda *_: self._add_current_app())
            add_current.add_suffix(add_btn)
            ga.add(add_current)
            self.app_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.app_box.add_css_class("boxed-list")
            ga.add(self.app_box)
            page.add(ga)

            self._render_exceptions()
            self._render_apps()
            return page

        def _toast(self, text: str):
            """Короткое сообщение поверх содержимого (Adw.Toast)."""
            self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

        def _render_apps(self):
            while (child := self.app_box.get_row_at_index(0)) is not None:
                self.app_box.remove(child)
            apps = cfgmod.load_apps()
            if not apps:
                row = Adw.ActionRow(title="Список пуст",
                                    subtitle="Автоисправление работает во всех программах")
                self.app_box.append(row)
                return
            for app in apps:
                row = Adw.ActionRow(title=app)
                btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
                btn.add_css_class("flat")
                btn.connect("clicked", lambda _b, a=app: self._remove_app(a))
                row.add_suffix(btn)
                self.app_box.append(row)

        def _add_app(self, name: str | None = None):
            value = (name if name is not None else self.entry_app.get_text()).strip()
            if not value:
                return
            cfgmod.add_app(value)
            self.entry_app.set_text("")
            self._render_apps()
            self.proxy.call("Reload")

        def _remove_app(self, app: str):
            cfgmod.remove_app(app)
            self._render_apps()
            self.proxy.call("Reload")

        def _add_current_app(self):
            """Взять имя активного приложения из at-spi (один запрос, не поток)."""
            try:
                name, _password = a11y.query_focus()
            except Exception:
                name = None
            if name:
                self._add_app(name)
                self._toast(f"Добавлено: {name}")
            else:
                self._toast("Не удалось определить активное окно")

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
            repo_row = Adw.ActionRow(title="Исходники на GitHub", subtitle=REPO_URL)
            repo_btn = Gtk.Button(label="Открыть", valign=Gtk.Align.CENTER)
            repo_btn.connect("clicked", lambda *_: _open_uri(REPO_URL))
            repo_row.add_suffix(repo_btn)
            about.add(repo_row)
            about_row = Adw.ActionRow(title="О программе", subtitle="лицензия, авторы, ссылки")
            about_btn = Gtk.Button(label="Подробнее", valign=Gtk.Align.CENTER)
            about_btn.connect("clicked", lambda *_: self.get_application().show_about())
            about_row.add_suffix(about_btn)
            about.add(about_row)
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

        def show_about(self):
            """Окно «О программе»: версия, лицензия, автор и ссылки.

            Своё окно, а не Adw.AboutDialog: тот на этой сборке молча ничего не
            показывает, а нам нужны ещё и кликабельные ссылки на GitHub.
            """
            about = Adw.Window(transient_for=self.window, modal=True, title="О программе")
            about.set_default_size(480, 420)
            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(Adw.HeaderBar())
            page = Adw.PreferencesPage()

            main = Adw.PreferencesGroup(title="WaySwitch")
            main.add(Adw.ActionRow(title="Версия", subtitle=__version__))
            main.add(Adw.ActionRow(title="Лицензия", subtitle="MIT"))
            main.add(Adw.ActionRow(title="Автор", subtitle="Илья Ветошкин"))
            main.add(Adw.ActionRow(
                title="Что делает",
                subtitle="Автоисправление раскладки на Wayland: GNOME и KDE Plasma. "
                         "Читает клавиатуру через /dev/input, печатает через uinput."))
            page.add(main)

            links = Adw.PreferencesGroup(title="Ссылки")
            repo_row = Adw.ActionRow(title="GitHub", subtitle=REPO_URL)
            repo_btn = Gtk.Button(label="Открыть", valign=Gtk.Align.CENTER)
            repo_btn.connect("clicked", lambda *_: _open_uri(REPO_URL))
            repo_row.add_suffix(repo_btn)
            links.add(repo_row)
            issues_row = Adw.ActionRow(title="Сообщить о проблеме",
                                       subtitle=f"{REPO_URL}/issues")
            issues_btn = Gtk.Button(label="Открыть", valign=Gtk.Align.CENTER)
            issues_btn.connect("clicked", lambda *_: _open_uri(f"{REPO_URL}/issues"))
            issues_row.add_suffix(issues_btn)
            links.add(issues_row)
            page.add(links)

            toolbar.set_content(page)
            about.set_content(toolbar)
            about.present()

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
