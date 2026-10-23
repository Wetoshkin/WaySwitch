"""Диалог «Параметры» (Ctrl+,): три страницы — Исправление, Исключения, Система.

Изменения применяются сразу: каждое пишет config.toml и просит демон
перечитать его (Reload). Кнопки «Сохранить» нет — в GNOME так не принято, а
у прежней кнопки внизу длинной страницы изменения молча терялись при уходе
на другую вкладку. Для необратимых действий — тост с «Отменить».
"""

from __future__ import annotations

import copy

from wayswitch import config as cfgmod
from wayswitch.gui import autostart, dialogs, gesture_keys, texts
from wayswitch.gui.widgets import StepRow, boxed_list, clear_list, flat_icon_button, keycap

EXTENSION_UUID = "wayswitch@siberia.ru"


def enable_extension() -> bool:
    import subprocess

    try:
        r = subprocess.run(["gnome-extensions", "enable", EXTENSION_UUID],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


class Preferences:
    """Собирает AdwPreferencesDialog и держит связь виджетов с конфигом."""

    def __init__(self, app):
        from gi.repository import Adw

        self.app = app
        self.proxy = app.proxy
        try:
            self.cfg = cfgmod.load()
        except cfgmod.ConfigError:
            # Битый конфиг не должен мешать открыть параметры: показываем
            # исходные значения, первое же изменение перезапишет файл.
            self.cfg = cfgmod.Config()
        self._loading = False
        self._save_source = 0
        self.dialog = Adw.PreferencesDialog(search_enabled=False)
        self.dialog.set_content_width(640)
        self.dialog.add(self._page_correction())
        self.dialog.add(self._page_exceptions())
        self.dialog.add(self._page_system())
        self.dialog.connect("closed", lambda *_: self._flush())

    def present(self, parent) -> None:
        self.dialog.present(parent)

    # --- сохранение ----------------------------------------------------------

    def _changed(self) -> None:
        """Отложенная запись: щелчки по «+» подряд — одна запись и один Reload."""
        from gi.repository import GLib

        if self._loading:
            return
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(300, self._flush)

    def _flush(self, *_):
        from gi.repository import GLib

        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        try:
            cfgmod.save(self.cfg)
        except (cfgmod.ConfigError, OSError) as exc:
            self.toast(f"Не удалось сохранить: {exc}")
            return False
        self.proxy.call("Reload")
        return GLib.SOURCE_REMOVE

    def toast(self, text: str, button: str | None = None, on_button=None) -> None:
        from gi.repository import Adw

        toast = Adw.Toast(title=text, timeout=4)
        if button and on_button:
            toast.set_button_label(button)
            toast.connect("button-clicked", lambda *_: on_button())
        self.dialog.add_toast(toast)

    # --- страница «Исправление» -------------------------------------------------

    def _page_correction(self):
        from gi.repository import Adw, Gtk

        c = self.cfg
        page = Adw.PreferencesPage(title="Исправление", icon_name="tools-check-spelling-symbolic")

        auto = Adw.PreferencesGroup(title="Автоматически")
        self.sw_auto = Adw.SwitchRow(
            title="Исправлять на пробеле",
            subtitle="Слово в чужой раскладке меняется, как только оно набрано")
        self.sw_auto.connect("notify::active", lambda r, _p: self._set(
            "general", "auto_correct", r.get_active()))
        auto.add(self.sw_auto)
        self.cb_sens = Adw.ComboRow(
            title="Чувствительность",
            subtitle="Чем выше, тем чаще правит короткие и редкие слова",
            model=Gtk.StringList.new(list(texts.SENSITIVITY_CHOICES)))
        self.cb_sens.connect("notify::selected", lambda r, _p: self._set(
            "general", "sensitivity", cfgmod.SENSITIVITY_NAMES[r.get_selected()]))
        auto.add(self.cb_sens)
        page.add(auto)

        manual = Adw.PreferencesGroup(title="Вручную")
        self.cb_manual = Adw.ComboRow(title="Жест",
                                      model=Gtk.StringList.new(list(texts.MANUAL_CHOICES)))
        self.cb_manual.connect("notify::selected", lambda r, _p: self._set_manual(
            cfgmod.MANUAL_MODES[r.get_selected()]))
        manual.add(self.cb_manual)
        self.row_key = Adw.ActionRow(title="Клавиша",
                                     subtitle="Одно нажатие исправляет последнее слово",
                                     activatable=True)
        self.key_cap = keycap(gesture_keys.human_name(c.gesture.tap_key))
        self.row_key.add_suffix(self.key_cap)
        self.row_key.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.row_key.connect("activated", lambda *_: dialogs.key_capture_dialog(
            self.dialog, self.cfg.gesture.tap_key, self._on_key_chosen))
        manual.add(self.row_key)
        self.row_sel = Adw.ActionRow(
            title="Исправить выделенное",
            subtitle="Одно нажатие переводит выделенный текст в другую раскладку",
            activatable=True)
        self.sel_cap = keycap("")
        self.row_sel.add_suffix(self.sel_cap)
        self.row_sel.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.row_sel.connect("activated", lambda *_: dialogs.key_capture_dialog(
            self.dialog, self.cfg.gesture.selection_key or "", self._on_sel_key_chosen,
            title="Выделенный текст",
            hint="Нажмите клавишу, которой будете исправлять выделенный текст"))
        manual.add(self.row_sel)
        self.step_undo = StepRow(
            "Время на отмену", "Повтор жеста в это время вернёт слово и запомнит его",
            c.general.undo_window_sec, 0, 60, 1, texts.format_seconds,
            lambda v: self._set("general", "undo_window_sec", float(v)))
        manual.add(self.step_undo.row)
        page.add(manual)

        what = Adw.PreferencesGroup(title="Что исправлять")
        self.switches = {}
        for key, title, subtitle in (
                ("punct_fix", "Знаки препинания", "пирогб → пирог,   домю → дом."),
                ("particles_fix", "Слитные частицы", "чтото → что-то,   кембы → кем бы"),
                ("split_words_fix", "Слипшиеся слова", "сонатуБаха → сонату Баха"),
                ("two_capitals_fix", "Две заглавные в начале", "ПРивет → Привет"),
                ("skip_password_fields", "Не исправлять в полях пароля",
                 "Работает не во всех приложениях")):
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.connect("notify::active", lambda r, _p, k=key: self._set(
                "general", k, r.get_active()))
            self.switches[key] = row
            what.add(row)
        self.step_guard = StepRow(
            "Пауза после правки текста", "После Backspace, стрелок и ручной смены раскладки",
            c.general.edit_guard_sec, 0, 10, 0.5, texts.format_seconds,
            lambda v: self._set("general", "edit_guard_sec", float(v)))
        what.add(self.step_guard.row)
        self.sw_sound = Adw.SwitchRow(title="Звук при исправлении")
        self.sw_sound.connect("notify::active", lambda r, _p: self._set(
            "general", "sound_on_fix", r.get_active()))
        what.add(self.sw_sound)
        page.add(what)
        self._load_correction()
        return page

    def _load_correction(self) -> None:
        c = self.cfg
        self._loading = True
        try:
            self.sw_auto.set_active(c.general.auto_correct)
            self.cb_sens.set_selected(cfgmod.SENSITIVITY_NAMES.index(c.general.sensitivity))
            self.cb_manual.set_selected(cfgmod.MANUAL_MODES.index(c.gesture.manual))
            self.key_cap.set_label(gesture_keys.human_name(c.gesture.tap_key))
            self.row_key.set_visible(c.gesture.manual == "tap_key")
            self.sel_cap.set_label(gesture_keys.human_name(c.gesture.selection_key)
                                   if c.gesture.selection_key else "Нет")
            self.step_undo.set_value(c.general.undo_window_sec)
            for key, row in self.switches.items():
                row.set_active(getattr(c.general, key))
            self.step_guard.set_value(c.general.edit_guard_sec)
            self.sw_sound.set_active(c.general.sound_on_fix)
        finally:
            self._loading = False

    def _set(self, section: str, key: str, value) -> None:
        if self._loading:
            return
        setattr(getattr(self.cfg, section), key, value)
        self._changed()

    def _set_manual(self, mode: str) -> None:
        # Строка «Клавиша» нужна только для жеста «Нажатие клавиши».
        self.row_key.set_visible(mode == "tap_key")
        self._set("gesture", "manual", mode)

    def _on_key_chosen(self, name: str | None) -> None:
        if name is None:
            # Backspace в диалоге: «без клавиши» — жест выключается.
            self.cb_manual.set_selected(cfgmod.MANUAL_MODES.index("none"))
            return
        if name == self.cfg.gesture.selection_key:
            self.toast("Эта клавиша уже исправляет выделенный текст")
            return
        self.cfg.gesture.tap_key = name
        self.key_cap.set_label(gesture_keys.human_name(name))
        self._changed()

    def _on_sel_key_chosen(self, name: str | None) -> None:
        name = name or ""
        if name and self.cfg.gesture.manual == "tap_key" and name == self.cfg.gesture.tap_key:
            self.toast("Эта клавиша уже исправляет последнее слово")
            return
        self.cfg.gesture.selection_key = name
        self.sel_cap.set_label(gesture_keys.human_name(name) if name else "Нет")
        self._changed()

    # --- страница «Исключения» --------------------------------------------------

    def _page_exceptions(self):
        from gi.repository import Adw

        page = Adw.PreferencesPage(title="Исключения", icon_name="action-unavailable-symbolic")
        words = Adw.PreferencesGroup(
            title="Слова",
            description="Эти слова никогда не исправляются. Сюда же попадают слова, "
                        "исправление которых вы отменили.")
        self.words_box = boxed_list()
        self.entry_word = Adw.EntryRow(title="Добавить слово", show_apply_button=True)
        self.entry_word.connect("apply", lambda *_: self._add_word())
        words.add(self.words_box)
        page.add(words)

        apps = Adw.PreferencesGroup(title="Приложения",
                                    description="В этих приложениях WaySwitch ничего не "
                                                "исправляет.")
        self.apps_box = boxed_list()
        self._add_app_row = None
        self.apps_box.connect("row-activated", self._on_app_row)
        apps.add(self.apps_box)
        page.add(apps)
        self._render_words()
        self._render_apps()
        return page

    def _render_words(self) -> None:
        from gi.repository import Adw

        # EntryRow переиспользуется: перед очисткой списка вынимаем его.
        if self.entry_word.get_parent() is not None:
            self.words_box.remove(self.entry_word)
        clear_list(self.words_box)
        self.words_box.append(self.entry_word)
        for word in sorted(cfgmod.load_exceptions()):
            row = Adw.ActionRow(title=word)
            row.add_css_class("ws-mono")
            row.add_suffix(flat_icon_button("user-trash-symbolic", f"Удалить «{word}»",
                                            lambda w=word: self._remove_word(w)))
            self.words_box.append(row)

    def _add_word(self) -> None:
        word = self.entry_word.get_text().strip()
        if not word:
            return
        cfgmod.add_exception(word)
        self.entry_word.set_text("")
        self._render_words()
        self.proxy.call("Reload")

    def _remove_word(self, word: str) -> None:
        cfgmod.remove_exception(word)
        self._render_words()
        self.proxy.call("Reload")

        def undo():
            cfgmod.add_exception(word)
            self._render_words()
            self.proxy.call("Reload")

        self.toast(f"Слово «{word}» удалено", "Отменить", undo)

    def _render_apps(self) -> None:
        from gi.repository import Gtk

        clear_list(self.apps_box)
        for app in cfgmod.load_apps():
            self.apps_box.append(dialogs.app_row(
                app, flat_icon_button("user-trash-symbolic", f"Удалить «{app}»",
                                      lambda a=app: self._remove_app(a))))
        add = Gtk.ListBoxRow(activatable=True)
        inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=14, margin_bottom=14)
        inner.append(Gtk.Image(icon_name="list-add-symbolic"))
        inner.append(Gtk.Label(label="Добавить приложение…"))
        inner.add_css_class("ws-accent-text")
        add.set_child(inner)
        self.apps_box.append(add)
        self._add_app_row = add

    def _on_app_row(self, _box, row) -> None:
        if row is self._add_app_row:
            dialogs.add_app_dialog(self.dialog, self._add_app)

    def _add_app(self, name: str) -> None:
        if cfgmod.add_app(name):
            self.toast(f"{name} добавлен в исключения")
        self._render_apps()
        self.proxy.call("Reload")

    def _remove_app(self, app: str) -> None:
        cfgmod.remove_app(app)
        self._render_apps()
        self.proxy.call("Reload")

        def undo():
            cfgmod.add_app(app)
            self._render_apps()
            self.proxy.call("Reload")

        self.toast(f"{app} удалён из исключений", "Отменить", undo)

    # --- страница «Система» -----------------------------------------------------

    def _page_system(self):
        from gi.repository import Adw, Gtk

        from wayswitch.backends.desktop import (
            COMPOSITOR_DESKTOPS,
            GNOME,
            HYPRLAND,
            SWAY,
            detect_desktop,
        )

        page = Adw.PreferencesPage(title="Система", icon_name="preferences-system-symbolic")
        status = self.proxy.status or {}
        desktop = detect_desktop()

        if desktop == GNOME and status.get("backend") not in ("shell", None):
            text = "Включите расширение GNOME Shell — раскладка будет переключаться без задержки"
            if hasattr(page, "set_banner"):
                banner = Adw.Banner(title=text, button_label="Включить", revealed=True)
                banner.connect("button-clicked", lambda b: self._enable_extension(b))
                page.set_banner(banner)
            else:
                group = Adw.PreferencesGroup()
                row = Adw.ActionRow(title=text)
                button = Gtk.Button(label="Включить", valign=Gtk.Align.CENTER)
                button.add_css_class("suggested-action")
                button.connect("clicked", lambda b: self._enable_extension(b))
                row.add_suffix(button)
                group.add(row)
                page.add(group)

        start = Adw.PreferencesGroup(title="Запуск")
        tiling = desktop in (SWAY, HYPRLAND)
        sw_service = Adw.SwitchRow(
            title="Запускать при входе в систему",
            subtitle="Через systemd. Если сеанс запущен без него — добавьте в конфиг "
                     "exec-once = wayswitch run" if tiling else "",
            active=autostart.is_service_enabled())
        sw_service.connect("notify::active",
                           lambda r, _p: autostart.set_service_enabled(r.get_active()))
        start.add(sw_service)
        sw_tray = Adw.SwitchRow(title=texts.tray_title(desktop),
                                subtitle=texts.tray_hint(desktop),
                                active=autostart.is_tray_enabled())
        sw_tray.connect("notify::active",
                        lambda r, _p: autostart.set_tray_enabled(r.get_active()))
        start.add(sw_tray)
        page.add(start)

        if desktop in (SWAY, HYPRLAND):
            # В тайловых композиторах трея обычно нет: индикатор — модуль
            # Waybar, управление — команды на горячих клавишах. Даём готовые
            # фрагменты конфигов.
            from wayswitch import panel

            bar = Adw.PreferencesGroup(
                title="Панель",
                description="Модуль показывает раскладку и исправления прямо в Waybar.")
            keys_snippet = panel.HYPRLAND_SNIPPET if desktop == HYPRLAND else panel.SWAY_SNIPPET
            for title, subtitle, text in (
                    ("Модуль для Waybar", "Фрагменты config.jsonc и style.css",
                     panel.WAYBAR_CONFIG + "\n\n" + panel.WAYBAR_CSS),
                    ("Горячие клавиши",
                     "Пауза и исправление для " + ("hyprland.conf" if desktop == HYPRLAND
                                                   else "sway/config"),
                     keys_snippet)):
                row = Adw.ActionRow(title=title, subtitle=subtitle)
                copy = Gtk.Button(label="Скопировать", valign=Gtk.Align.CENTER)
                copy.connect("clicked", lambda _b, t=text, n=title: self._copy(t, n))
                row.add_suffix(copy)
                bar.add(row)
            page.add(bar)

        if desktop in COMPOSITOR_DESKTOPS:
            track = Adw.PreferencesGroup(
                title="Отслеживание раскладки",
                description="Ваш композитор не сообщает, какая раскладка включена. WaySwitch "
                            "считает нажатия сочетания переключения — это ошибается, если "
                            "менять раскладку мышью.")
            self.sw_track = Adw.SwitchRow(title="Исправлять автоматически по счётчику",
                                          subtitle="Без этого работают ручной жест и "
                                                   "«Исправить выделенное»")
            self.sw_track.set_active(self.cfg.backend.track_hotkey)
            self.sw_track.connect("notify::active", lambda r, _p: self._set(
                "backend", "track_hotkey", r.get_active()))
            track.add(self.sw_track)
            page.add(track)

        updates = Adw.PreferencesGroup(title="Обновления")
        self.sw_updates = Adw.SwitchRow(
            title="Сообщать о новых версиях",
            subtitle="Раз в сутки спрашивать GitHub, вышла ли новая версия. Больше ничего "
                     "не отправляется")
        self.sw_updates.connect("notify::active", lambda r, _p: self._set(
            "general", "check_updates", r.get_active()))
        updates.add(self.sw_updates)
        self.row_check = Adw.ActionRow(title="Проверить сейчас",
                                       subtitle=self.app.update_text())
        check = Gtk.Button(label="Проверить", valign=Gtk.Align.CENTER)
        check.connect("clicked", lambda b: self._check_updates(b))
        self.row_check.add_suffix(check)
        updates.add(self.row_check)
        page.add(updates)

        layout = Adw.PreferencesGroup(title="Переключение раскладки")
        current = texts.BACKEND_HUMAN.get(str(status.get("backend") or ""), "")
        self.cb_backend = Adw.ComboRow(
            title="Способ",
            subtitle=("Сейчас " + current.replace("через ", "используется ")) if current
            else "WaySwitch не запущен",
            model=Gtk.StringList.new(list(texts.BACKEND_CHOICES)))
        self.cb_backend.connect("notify::selected", lambda r, _p: self._set(
            "backend", "prefer", cfgmod.BACKEND_PREFS[r.get_selected()]))
        layout.add(self.cb_backend)
        more = Adw.ExpanderRow(title="Дополнительно")
        self.step_settle = StepRow(
            "Ожидание после смены раскладки",
            "Увеличьте, если первая буква печатается в старой раскладке",
            self.cfg.typing.settle_ms, 0, 500, 10, texts.format_ms,
            lambda v: self._set("typing", "settle_ms", int(v)))
        more.add_row(self.step_settle.row)
        self.step_delay = StepRow(
            "Задержка между символами", "Для приложений, которые теряют быстрый ввод",
            self.cfg.typing.key_delay_ms, 0, 100, 1, texts.format_ms,
            lambda v: self._set("typing", "key_delay_ms", int(v)))
        more.add_row(self.step_delay.row)
        self.cb_typing = Adw.ComboRow(
            title="Чем печатать", subtitle="Только для расширения GNOME Shell",
            model=Gtk.StringList.new(["Клавишами расширения", "Как настоящая клавиатура"]))
        self.cb_typing.connect("notify::selected", lambda r, _p: self._set(
            "backend", "shell_typing", cfgmod.SHELL_TYPING[r.get_selected()]))
        more.add_row(self.cb_typing)
        layout.add(more)
        page.add(layout)

        reset_group = Adw.PreferencesGroup()
        reset_box = boxed_list()
        reset = Gtk.ListBoxRow(activatable=True)
        label = Gtk.Label(label="Сбросить все параметры…", margin_top=14, margin_bottom=14)
        label.add_css_class("error")
        reset.set_child(label)
        reset_box.append(reset)
        reset_box.connect("row-activated", lambda *_: dialogs.reset_dialog(
            self.dialog, self._reset))
        reset_group.add(reset_box)
        page.add(reset_group)
        self._load_system()
        return page

    def _load_system(self) -> None:
        c = self.cfg
        self._loading = True
        try:
            self.sw_updates.set_active(c.general.check_updates)
            self.cb_backend.set_selected(cfgmod.BACKEND_PREFS.index(c.backend.prefer))
            self.step_settle.set_value(c.typing.settle_ms)
            self.step_delay.set_value(c.typing.key_delay_ms)
            self.cb_typing.set_selected(cfgmod.SHELL_TYPING.index(c.backend.shell_typing))
        finally:
            self._loading = False

    def _copy(self, text: str, what: str) -> None:
        from gi.repository import Gdk

        Gdk.Display.get_default().get_clipboard().set(text)
        self.toast(f"{what}: скопировано в буфер обмена")

    def _enable_extension(self, widget) -> None:
        if enable_extension():
            self.toast("Расширение включено. Если раскладка не переключается — выйдите "
                       "из системы и войдите снова")
            if hasattr(widget, "set_revealed"):
                widget.set_revealed(False)
        else:
            self.toast("Не удалось включить: расширение не установлено")

    def _check_updates(self, button) -> None:
        button.set_sensitive(False)
        self.row_check.set_subtitle("Проверяю…")

        def done(_result):
            button.set_sensitive(True)
            self.row_check.set_subtitle(self.app.update_text(checked=True))

        self.app.check_updates(force=True, done=done)

    def _reset(self) -> None:
        before = copy.deepcopy(self.cfg)
        self.cfg = cfgmod.Config()
        self._load_correction()
        self._load_system()
        self._flush()

        def undo():
            self.cfg = before
            self._load_correction()
            self._load_system()
            self._flush()

        self.toast("Параметры сброшены", "Отменить", undo)
