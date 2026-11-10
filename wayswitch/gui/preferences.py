"""Диалог «Параметры» (Ctrl+,): три страницы — Исправление, Исключения, Система.

Изменения применяются сразу: каждое пишет config.toml и просит демон
перечитать его (Reload). Кнопки «Сохранить» нет — в GNOME так не принято, а
у прежней кнопки внизу длинной страницы изменения молча терялись при уходе
на другую вкладку. Для необратимых действий — тост с «Отменить».
"""

from __future__ import annotations

import copy

from wayswitch import config as cfgmod
from wayswitch.gui import autostart, dialogs, gesture_keys, portable, texts
from wayswitch.gui.widgets import StepRow, boxed_list, clear_list, flat_icon_button, keycap
from wayswitch.i18n import gettext as _

EXTENSION_UUID = "wayswitch@wetoshkin.github.io"


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
        self.broken = None
        try:
            self.cfg = cfgmod.load()
        except cfgmod.ConfigError as exc:
            # Битый конфиг не должен мешать открыть параметры: показываем
            # исходные значения и предупреждаем. Файл перепишется, только если
            # человек что-то поменяет сам.
            self.cfg = cfgmod.Config()
            self.broken = str(exc)
        self._saved = copy.deepcopy(self.cfg)  # последнее, что удалось записать
        self._loading = False
        self._save_source = 0
        # Что человек поменял в этом окне: (секция, ключ) → значение. При
        # записи файл перечитывается и поверх кладутся только эти поля —
        # иначе переключатель из меню значка, сделанный при открытом окне,
        # затёрся бы старым снимком.
        self._dirty: dict[tuple[str, str], object] = {}
        self._replace = False
        self.dialog = Adw.PreferencesDialog(search_enabled=False)
        self.dialog.set_content_width(640)
        self.dialog.add(self._page_correction())
        self.dialog.add(self._page_exceptions())
        self.dialog.add(self._page_system())
        self.dialog.connect("closed", lambda *_: self._flush())

    def present(self, parent) -> None:
        self.dialog.present(parent)
        if self.broken:
            self.toast(_("Settings could not be loaded — defaults are shown"))

    # --- сохранение ----------------------------------------------------------

    def _changed(self) -> None:
        """Отложенная запись: щелчки по «+» подряд — одна запись и один Reload."""
        from gi.repository import GLib

        if self._loading:
            return
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(300, self._flush)

    def _flush(self, *_args):
        from gi.repository import GLib

        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        if not self._dirty and not self._replace:
            return GLib.SOURCE_REMOVE  # ничего не меняли — файл не трогаем
        if self._replace:
            fresh = self.cfg
        else:
            try:
                fresh = cfgmod.load()
            except cfgmod.ConfigError:
                fresh = self.cfg
            for (section, key), value in self._dirty.items():
                setattr(getattr(fresh, section), key, value)
        try:
            cfgmod.save(fresh)
        except (cfgmod.ConfigError, OSError) as exc:
            self.toast(_("Could not save: {error}").format(error=exc))
            if isinstance(exc, cfgmod.ConfigError):
                # Плохое значение не должно блокировать все следующие правки:
                # отбрасываем несохранённое и показываем то, что в файле.
                self._dirty.clear()
                self._replace = False
                try:
                    self.cfg = cfgmod.load()
                except cfgmod.ConfigError:
                    self.cfg = copy.deepcopy(self._saved)
                self._load_correction()
                self._load_system()
            return GLib.SOURCE_REMOVE
        self.cfg = fresh
        self._saved = copy.deepcopy(fresh)
        self._dirty.clear()
        self._replace = False
        self.broken = None
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
        page = Adw.PreferencesPage(title=_("Correction"),
                                   icon_name="tools-check-spelling-symbolic")

        auto = Adw.PreferencesGroup(title=_("Automatically"))
        self.sw_auto = Adw.SwitchRow(
            title=_("Fix on Space"),
            subtitle=_("A word in the other layout is changed as soon as it is typed"))
        self.sw_auto.connect("notify::active", lambda r, _p: self._set(
            "general", "auto_correct", r.get_active()))
        auto.add(self.sw_auto)
        self.cb_sens = Adw.ComboRow(
            title=_("Sensitivity"),
            subtitle=_("The higher it is, the more often short and rare words are corrected"),
            model=Gtk.StringList.new([_(choice) for choice in texts.SENSITIVITY_CHOICES]))
        self.cb_sens.connect("notify::selected", lambda r, _p: self._set(
            "general", "sensitivity", cfgmod.SENSITIVITY_NAMES[r.get_selected()]))
        auto.add(self.cb_sens)
        page.add(auto)

        manual = Adw.PreferencesGroup(title=_("Manual"))
        self.cb_manual = Adw.ComboRow(
            title=_("Gesture"),
            model=Gtk.StringList.new([_(choice) for choice in texts.MANUAL_CHOICES]))
        self.cb_manual.connect("notify::selected", lambda r, _p: self._set_manual(
            cfgmod.MANUAL_MODES[r.get_selected()]))
        manual.add(self.cb_manual)
        self.row_key = Adw.ActionRow(title=_("Key"),
                                     subtitle=_("One press corrects the last word"),
                                     activatable=True)
        self.key_cap = keycap(gesture_keys.human_name(c.gesture.tap_key))
        self.row_key.add_suffix(self.key_cap)
        self.row_key.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.row_key.connect("activated", lambda *_: dialogs.key_capture_dialog(
            self.dialog, self.cfg.gesture.tap_key, self._on_key_chosen))
        manual.add(self.row_key)
        # Клавиши действий над выделенным текстом: своя строка у каждой.
        self.sel_caps = {}
        from wayswitch.backends.desktop import GNOME, detect_desktop

        # Выделенный текст читает расширение GNOME Shell — в других средах
        # клавиши ничего не сделают, о чём честно и пишем.
        only_gnome = "" if detect_desktop() == GNOME else _(" (GNOME only for now)")
        for field_name, title, subtitle, hint in texts.SELECTION_KEY_ROWS:
            row_title, row_hint = _(title), _(hint)
            row = Adw.ActionRow(title=row_title, subtitle=_(subtitle) + only_gnome,
                                activatable=True)
            cap = keycap("")
            row.add_suffix(cap)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda *_, f=field_name, t=row_title, h=row_hint:
                        dialogs.key_capture_dialog(
                            self.dialog, getattr(self.cfg.gesture, f) or "",
                            lambda name, f=f: self._on_sel_key_chosen(f, name),
                            title=t, hint=h))
            self.sel_caps[field_name] = cap
            manual.add(row)
        self.step_undo = StepRow(
            _("Undo window"), _("Repeating the gesture within this time returns the word"),
            c.general.undo_window_sec, 0, 60, 1, texts.format_seconds,
            lambda v: self._set("general", "undo_window_sec", float(v)))
        manual.add(self.step_undo.row)
        self.step_learn = StepRow(
            _("Remember a word after undos"),
            _("How many times to undo a correction before WaySwitch stops touching the word"),
            c.general.learn_after_undos, 1, 5, 1, texts.format_times,
            lambda v: self._set("general", "learn_after_undos", int(v)))
        manual.add(self.step_learn.row)
        page.add(manual)

        what = Adw.PreferencesGroup(title=_("What to correct"))
        self.switches = {}
        for key, title, subtitle in (
                ("punct_fix", _("Punctuation"), "пирогб → пирог,   домю → дом."),
                ("particles_fix", _("Joined particles"),
                 "чтото → что-то,   кембы → кем бы"),
                ("split_words_fix", _("Glued words"), "сонатуБаха → сонату Баха"),
                ("two_capitals_fix", _("Two capitals at the start"), "ПРивет → Привет"),
                ("caps_fix", _("Accidental Caps Lock"),
                 _("пРИВЕТ → Привет, and Caps Lock is turned off")),
                ("early_switch", _("Switch at the start of a word"),
                 _("ghbd → прив in Writer/Calc; when retyping with keys — after Space")),
                ("skip_password_fields", _("Password and terminal protection"),
                 _("Auto-correction is off. The manual gesture is available, "
                   "history is hidden"))):
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.connect("notify::active", lambda r, _p, k=key: self._set(
                "general", k, r.get_active()))
            self.switches[key] = row
            what.add(row)
        self.step_guard = StepRow(
            _("Pause after editing text"),
            _("After Backspace, arrows and a manual layout switch"),
            c.general.edit_guard_sec, 0, 10, 0.5, texts.format_seconds,
            lambda v: self._set("general", "edit_guard_sec", float(v)))
        what.add(self.step_guard.row)
        self.sw_sound = Adw.SwitchRow(title=_("Sound on correction"))
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
            for field_name, cap in self.sel_caps.items():
                value = getattr(c.gesture, field_name)
                cap.set_label(gesture_keys.human_name(value) if value else _("None"))
            self.step_learn.set_value(c.general.learn_after_undos)
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
        self._dirty[(section, key)] = value
        self._changed()

    def _set_manual(self, mode: str) -> None:
        # Строка «Клавиша» нужна только для жеста «Нажатие клавиши».
        self.row_key.set_visible(mode == "tap_key")
        if self._loading:
            return
        g = self.cfg.gesture
        for field_name, title, *_rest in texts.SELECTION_KEY_ROWS:
            if cfgmod.selection_key_conflict(g, getattr(g, field_name), mode, field=field_name):
                # Одна клавиша не может и исправлять слово, и выделение.
                self._set("gesture", field_name, "")
                self.sel_caps[field_name].set_label(_("None"))
                self.toast(_("The key for “{title}” was cleared: the new gesture uses it")
                           .format(title=_(title)))
        self._set("gesture", "manual", mode)

    def _on_key_chosen(self, name: str | None) -> None:
        if name is None:
            # Backspace в диалоге: «без клавиши» — жест выключается.
            self.cb_manual.set_selected(cfgmod.MANUAL_MODES.index("none"))
            return
        code = cfgmod.key_code(name)
        for field_name in cfgmod.SELECTION_KEY_FIELDS:
            if code == cfgmod.key_code(getattr(self.cfg.gesture, field_name)):
                self.toast(_("This key is already used for another action"))
                return
        if self.cfg.gesture.pause_hotkey and code == cfgmod.key_code(self.cfg.gesture.pause_hotkey):
            self.toast(_("This key pauses WaySwitch"))
            return
        self._set("gesture", "tap_key", name)
        self.key_cap.set_label(gesture_keys.human_name(name))

    def _on_sel_key_chosen(self, field_name: str, name: str | None) -> None:
        name = name or ""
        conflict = cfgmod.selection_key_conflict(self.cfg.gesture, name, field=field_name)
        if conflict:
            # Причина приходит из общей проверки конфига; в GUI не показываем её
            # русский текст, чтобы английское окно оставалось английским.
            self.toast(_("Cannot use this key: it is already assigned to another action"))
            return
        self._set("gesture", field_name, name)
        self.sel_caps[field_name].set_label(gesture_keys.human_name(name) if name
                                            else _("None"))

    # --- страница «Исключения» --------------------------------------------------

    def _page_exceptions(self):
        from gi.repository import Adw

        page = Adw.PreferencesPage(title=_("Exceptions"),
                                   icon_name="action-unavailable-symbolic")
        words = Adw.PreferencesGroup(
            title=_("Words"),
            description=_("These words are never corrected. Words whose correction you "
                          "undid are added here too."))
        self.words_box = boxed_list()
        self.entry_word = Adw.EntryRow(title=_("Add a word"), show_apply_button=True)
        self.entry_word.connect("apply", lambda *_: self._add_word())
        words.add(self.words_box)
        page.add(words)

        apps = Adw.PreferencesGroup(title=_("Applications"),
                                    description=_("WaySwitch does not correct anything "
                                                  "in these applications."))
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
            row = Adw.ActionRow(title=word, use_markup=False)
            row.add_css_class("ws-mono")
            row.add_suffix(flat_icon_button(
                "user-trash-symbolic", _("Remove “{word}”").format(word=word),
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

        self.toast(_("Word “{word}” removed").format(word=word), _("Undo"), undo)

    def _render_apps(self) -> None:
        from gi.repository import Gtk

        clear_list(self.apps_box)
        for app in cfgmod.load_apps():
            self.apps_box.append(dialogs.app_row(
                app, flat_icon_button(
                    "user-trash-symbolic", _("Remove “{app}”").format(app=app),
                    lambda a=app: self._remove_app(a))))
        add = Gtk.ListBoxRow(activatable=True)
        inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=14, margin_bottom=14)
        inner.append(Gtk.Image(icon_name="list-add-symbolic"))
        inner.append(Gtk.Label(label=_("Add application…")))
        inner.add_css_class("ws-accent-text")
        add.set_child(inner)
        self.apps_box.append(add)
        self._add_app_row = add

    def _on_app_row(self, _box, row) -> None:
        if row is self._add_app_row:
            dialogs.add_app_dialog(self.dialog, self._add_app)

    def _add_app(self, name: str) -> None:
        if cfgmod.add_app(name):
            self.toast(_("{app} added to exceptions").format(app=name))
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

        self.toast(_("{app} removed from exceptions").format(app=app), _("Undo"), undo)

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

        page = Adw.PreferencesPage(title=_("System"), icon_name="preferences-system-symbolic")
        status = self.proxy.status or {}
        desktop = detect_desktop()

        if not portable.package_kind() and desktop == GNOME \
                and status.get("backend") not in ("shell", None):
            text = _("Enable the GNOME Shell extension — the layout will switch "
                     "without delay")
            if hasattr(page, "set_banner"):
                banner = Adw.Banner(title=text, button_label=_("Enable"), revealed=True)
                banner.connect("button-clicked", lambda b: self._enable_extension(b))
                page.set_banner(banner)
            else:
                group = Adw.PreferencesGroup()
                row = Adw.ActionRow(title=text)
                button = Gtk.Button(label=_("Enable"), valign=Gtk.Align.CENTER)
                button.add_css_class("suggested-action")
                button.connect("clicked", lambda b: self._enable_extension(b))
                row.add_suffix(button)
                group.add(row)
                page.add(group)

        start = Adw.PreferencesGroup(title=_("Startup"))
        tiling = desktop in (SWAY, HYPRLAND)
        sw_service = Adw.SwitchRow(
            title=_("Start at sign-in"),
            subtitle=(_("Via systemd. If the session runs without it, add to the config: ")
                      + ("exec-once = wayswitch run" if desktop == HYPRLAND
                         else "exec wayswitch run")) if tiling else "",
            active=not portable.package_kind() and autostart.is_service_enabled())
        sw_service.connect("notify::active",
                           lambda r, _p: autostart.set_service_enabled(r.get_active()))
        start.add(sw_service)
        sw_tray = Adw.SwitchRow(title=texts.tray_title(desktop),
                                subtitle=texts.tray_hint(desktop),
                                active=not portable.package_kind() and autostart.is_tray_enabled())
        sw_tray.connect("notify::active",
                        lambda r, _p: autostart.set_tray_enabled(r.get_active()))
        start.add(sw_tray)
        if portable.package_kind():
            for row in (sw_service, sw_tray):
                row.set_sensitive(False)
                row.set_subtitle(_("Configured on the host system"))
            companion = Adw.ActionRow(title=_("Native service"),
                                      subtitle=_("Installation, autostart and diagnostics "
                                                 "on the host"))
            button = Gtk.Button(label=_("Instructions"), valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda *_: portable.show_instructions(self.dialog))
            companion.add_suffix(button)
            start.add(companion)
        page.add(start)

        if desktop in (SWAY, HYPRLAND):
            # В тайловых композиторах трея обычно нет: индикатор — модуль
            # Waybar, управление — команды на горячих клавишах. Даём готовые
            # фрагменты конфигов.
            from wayswitch import panel

            bar = Adw.PreferencesGroup(
                title=_("Panel"),
                description=_("The module shows the layout and corrections right in Waybar."))
            keys_snippet = panel.HYPRLAND_SNIPPET if desktop == HYPRLAND else panel.SWAY_SNIPPET
            for title, subtitle, text in (
                    (_("Waybar module"), _("Fragments for config.jsonc and style.css"),
                     panel.WAYBAR_CONFIG + "\n\n" + panel.WAYBAR_CSS),
                    (_("Shortcuts"),
                     _("Pause and correction for ") + ("hyprland.conf" if desktop == HYPRLAND
                                                       else "sway/config"),
                     keys_snippet)):
                row = Adw.ActionRow(title=title, subtitle=subtitle)
                copy = Gtk.Button(label=_("Copy"), valign=Gtk.Align.CENTER)
                copy.connect("clicked", lambda _b, t=text, n=title: self._copy(t, n))
                row.add_suffix(copy)
                bar.add(row)
            page.add(bar)

        if desktop in COMPOSITOR_DESKTOPS:
            track = Adw.PreferencesGroup(
                title=_("Layout tracking"),
                description=_("Your compositor does not report which layout is active. "
                              "WaySwitch counts layout-switch shortcut presses — this is "
                              "wrong if you change the layout with the mouse."))
            self.sw_track = Adw.SwitchRow(
                title=_("Correct automatically by the counter"),
                subtitle=_("Without this, the manual gesture and “Fix selection” still work"))
            self.sw_track.set_active(self.cfg.backend.track_hotkey)
            self.sw_track.connect("notify::active", lambda r, _p: self._set(
                "backend", "track_hotkey", r.get_active()))
            track.add(self.sw_track)
            page.add(track)

        updates = Adw.PreferencesGroup(title=_("Updates"))
        self.sw_updates = Adw.SwitchRow(
            title=_("Notify about new versions"),
            subtitle=_("Ask GitHub once a day whether a new version exists. Nothing else "
                       "is sent"))
        if not portable.can_self_update():
            self.sw_updates.set_sensitive(False)
            self.sw_updates.set_subtitle(_(portable.PACKAGED_UPDATE_TEXT))
        self.sw_updates.connect("notify::active", lambda r, _p: self._set(
            "general", "check_updates", r.get_active()))
        updates.add(self.sw_updates)
        self.row_check = Adw.ActionRow(title=_("Check now"),
                                       subtitle=self.app.update_text())
        check = Gtk.Button(label=_("Check"), valign=Gtk.Align.CENTER,
                           sensitive=portable.can_self_update())
        check.connect("clicked", lambda b: self._check_updates(b))
        self.row_check.add_suffix(check)
        # «Обновить» — пока есть новая версия; запускает команду в терминале.
        self.btn_update = Gtk.Button(label=_("Update"), valign=Gtk.Align.CENTER,
                                     visible=self.app.update is not None)
        self.btn_update.add_css_class("suggested-action")
        self.btn_update.connect("clicked", lambda *_: self.app.run_update())
        self.row_check.add_suffix(self.btn_update)
        updates.add(self.row_check)
        page.add(updates)

        remove = Adw.PreferencesGroup(title=_("Removal"))
        row_remove = Adw.ActionRow(
            title=_("Remove WaySwitch"),
            subtitle=_("Instructions for the window and the separate native service")
            if portable.package_kind() else
            _("A terminal will open: confirm the removal and enter your password"))
        btn_remove = Gtk.Button(label=_("Remove…"), valign=Gtk.Align.CENTER)
        btn_remove.add_css_class("destructive-action")
        btn_remove.connect("clicked", lambda *_: self.app.ask_uninstall())
        row_remove.add_suffix(btn_remove)
        remove.add(row_remove)
        page.add(remove)

        layout = Adw.PreferencesGroup(title=_("Layout switching"))
        current = texts.BACKEND_HUMAN.get(str(status.get("backend") or ""), "")
        self.cb_backend = Adw.ComboRow(
            title=_("Method"),
            subtitle=(_("Currently: {backend}").format(backend=_(current))) if current
            else _("WaySwitch is not running"),
            model=Gtk.StringList.new([_(choice) for choice in texts.BACKEND_CHOICES]))
        self.cb_backend.connect("notify::selected", lambda r, _p: self._set(
            "backend", "prefer", cfgmod.BACKEND_PREFS[r.get_selected()]))
        layout.add(self.cb_backend)
        more = Adw.ExpanderRow(title=_("Advanced"))
        self.step_settle = StepRow(
            _("Wait after switching the layout"),
            _("Increase it if the first letter is typed in the old layout"),
            self.cfg.typing.settle_ms, 0, 500, 10, texts.format_ms,
            lambda v: self._set("typing", "settle_ms", int(v)))
        more.add_row(self.step_settle.row)
        self.step_delay = StepRow(
            _("Delay between characters"),
            _("For applications that lose fast input"),
            self.cfg.typing.key_delay_ms, 0, 100, 1, texts.format_ms,
            lambda v: self._set("typing", "key_delay_ms", int(v)))
        more.add_row(self.step_delay.row)
        self.cb_typing = Adw.ComboRow(
            title=_("How to type"), subtitle=_("Only for the GNOME Shell extension"),
            model=Gtk.StringList.new([_("Extension keys"), _("Like a real keyboard")]))
        self.cb_typing.connect("notify::selected", lambda r, _p: self._set(
            "backend", "shell_typing", cfgmod.SHELL_TYPING[r.get_selected()]))
        more.add_row(self.cb_typing)
        layout.add(more)
        page.add(layout)

        reset_group = Adw.PreferencesGroup()
        reset_box = boxed_list()
        reset = Gtk.ListBoxRow(activatable=True)
        label = Gtk.Label(label=_("Reset all settings…"), margin_top=14, margin_bottom=14)
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
            self.sw_updates.set_active(c.general.check_updates and portable.can_self_update())
            self.cb_backend.set_selected(cfgmod.BACKEND_PREFS.index(c.backend.prefer))
            self.step_settle.set_value(c.typing.settle_ms)
            self.step_delay.set_value(c.typing.key_delay_ms)
            self.cb_typing.set_selected(cfgmod.SHELL_TYPING.index(c.backend.shell_typing))
            if getattr(self, "sw_track", None) is not None:
                self.sw_track.set_active(c.backend.track_hotkey)
        finally:
            self._loading = False

    def _copy(self, text: str, what: str) -> None:
        from gi.repository import Gdk

        Gdk.Display.get_default().get_clipboard().set(text)
        self.toast(_("{what}: copied to the clipboard").format(what=what))

    def _enable_extension(self, widget) -> None:
        if enable_extension():
            self.toast(_("The extension is enabled. If the layout does not switch, "
                         "sign out and sign in again"))
            if hasattr(widget, "set_revealed"):
                widget.set_revealed(False)
        else:
            self.toast(_("Could not enable it: the extension is not installed"))

    def _check_updates(self, button) -> None:
        button.set_sensitive(False)
        self.row_check.set_subtitle(_("Checking…"))

        def done(_result):
            button.set_sensitive(True)
            self.row_check.set_subtitle(self.app.update_text(checked=True))
            self.btn_update.set_visible(self.app.update is not None)

        self.app.check_updates(force=True, done=done)

    def _reset(self) -> None:
        before = copy.deepcopy(self.cfg)
        self.cfg = cfgmod.Config()
        self._load_correction()
        self._load_system()
        self._replace = True
        self._flush()

        def undo():
            self.cfg = before
            self._load_correction()
            self._load_system()
            self._replace = True
            self._flush()

        self.toast(_("Settings reset"), _("Undo"), undo)
