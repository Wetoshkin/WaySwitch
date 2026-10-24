"""Главное окно: состояние, проверка исправления, последние исправления.

Одна страница вместо прежних четырёх вкладок: каждый день нужна только она.
Параметры — в отдельном диалоге (Ctrl+,), сведения о программе — в «О WaySwitch».
Если служба не отвечает, вместо страницы — AdwStatusPage с кнопкой «Запустить».
"""

from __future__ import annotations

from wayswitch import config as cfgmod
from wayswitch.gui import autostart, dialogs, texts
from wayswitch.gui.widgets import boxed_list, clear_list, flat_icon_button

STATUS_ICONS = {
    "on": "object-select-symbolic",
    "paused": "media-playback-pause-symbolic",
    "locked": "system-lock-screen-symbolic",
    "manual": "input-keyboard-symbolic",
    "off": "system-shutdown-symbolic",
}
HISTORY_ICONS = {
    "auto": "tools-check-spelling-symbolic",
    "manual": "input-keyboard-symbolic",
    "excluded": "edit-undo-symbolic",
}


def _heading(text: str, description: str | None = None):
    from gi.repository import Gtk

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_bottom=8)
    title = Gtk.Label(label=text, xalign=0.0)
    title.add_css_class("heading")
    box.append(title)
    if description:
        desc = Gtk.Label(label=description, xalign=0.0, wrap=True)
        desc.add_css_class("dim-label")
        desc.add_css_class("caption")
        box.append(desc)
    return box


class MainWindow:
    def __init__(self, app):
        from gi.repository import Adw, Gio, Gtk

        self.app = app
        self.window = Adw.ApplicationWindow(application=app, title="WaySwitch")
        # 640: «Параметры» открываются поверх окна и в 600 px не помещают
        # заголовки трёх вкладок.
        self.window.set_default_size(640, 740)
        self.window.set_size_request(360, 400)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Параметры", "app.preferences")
        section.append("Жесты и клавиши", "app.shortcuts")
        section.append("О WaySwitch", "app.about")
        menu.append_section(None, section)
        tail = Gio.Menu()
        tail.append("Выйти", "app.quit")
        menu.append_section(None, tail)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                     primary=True, tooltip_text="Главное меню")
        menu_button.update_property([Gtk.AccessibleProperty.LABEL], ["Главное меню"])
        header.pack_end(menu_button)
        toolbar.add_top_bar(header)

        self.update_banner = Adw.Banner(button_label="Подробнее")
        self.update_banner.connect("button-clicked", lambda *_: self.app.open_release())
        toolbar.add_top_bar(self.update_banner)

        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._main_page(), "main")
        self.stack.add_named(self._off_page(), "off")
        self.toasts.set_child(self.stack)
        toolbar.set_content(self.toasts)
        self.window.set_content(toolbar)

        self._pause_toast = None
        self._last_kind = None
        self.render_status(app.proxy.status)
        self.render_history()
        self.render_update(app.update)

    def present(self) -> None:
        self.window.present()

    # --- страницы ----------------------------------------------------------------

    def _main_page(self):
        from gi.repository import Adw, Gtk

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=12,
                          margin_bottom=32, margin_start=12, margin_end=12)
        content.append(self._status_card())
        content.append(self._test_group())
        content.append(self._history_group())
        clamp = Adw.Clamp(maximum_size=520, child=content)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(clamp)
        return scroller

    def _status_card(self):
        from gi.repository import Gtk

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("card")
        top = Gtk.Box(spacing=14, margin_top=16, margin_bottom=16, margin_start=16,
                      margin_end=16)
        self.status_icon = Gtk.Image(pixel_size=22, valign=Gtk.Align.CENTER)
        self.status_icon.add_css_class("ws-status-icon")
        top.append(self.status_icon)
        texts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True,
                            valign=Gtk.Align.CENTER)
        self.status_title = Gtk.Label(xalign=0.0, wrap=True)
        self.status_title.add_css_class("ws-status-title")
        self.status_subtitle = Gtk.Label(xalign=0.0, wrap=True)
        self.status_subtitle.add_css_class("dim-label")
        self.status_subtitle.add_css_class("caption")
        texts_box.append(self.status_title)
        texts_box.append(self.status_subtitle)
        top.append(texts_box)
        self.status_switch = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Пауза (Ctrl+P)")
        self.status_switch.update_property([Gtk.AccessibleProperty.LABEL],
                                           ["Исправление включено"])
        self._switch_handler = self.status_switch.connect("notify::active",
                                                          self._on_switch)
        top.append(self.status_switch)
        card.append(top)
        card.append(Gtk.Separator())

        # Grid, а не homogeneous-бокс: разделители не должны делить ширину
        # поровну с ячейками.
        grid = Gtk.Grid()
        grid.add_css_class("ws-stats")
        self.stat_labels = {}
        col = 0
        for key, caption in (("corrections_total", "автоматически"),
                             ("manual_total", "вручную"), ("undo_total", "отменено")):
            if col:
                grid.attach(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), col, 0, 1, 1)
                col += 1
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            cell.add_css_class("ws-stat")
            number = Gtk.Label(label="0", xalign=0.0)
            number.add_css_class("ws-stat-number")
            cap = Gtk.Label(label=caption, xalign=0.0)
            cap.add_css_class("dim-label")
            cap.add_css_class("caption")
            cell.append(number)
            cell.append(cap)
            grid.attach(cell, col, 0, 1, 1)
            col += 1
            self.stat_labels[key] = number
        card.append(grid)
        return card

    def _test_group(self):
        from gi.repository import Adw, Gtk

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(_heading("Проверка", "Включите английскую раскладку, наберите ghbdtn и "
                                        "нажмите пробел."))
        listing = boxed_list()
        self.test_entry = Adw.EntryRow(title="Попробуйте здесь")
        self.test_entry.add_css_class("ws-test-entry")
        self.layout_badge = Gtk.Label(valign=Gtk.Align.CENTER)
        self.layout_badge.add_css_class("ws-keycap")
        self.layout_badge.set_tooltip_text("Текущая раскладка")
        self.test_entry.add_suffix(self.layout_badge)
        listing.append(self.test_entry)
        box.append(listing)
        self.test_result = Gtk.Label(xalign=0.0, wrap=True, margin_top=8, margin_start=2)
        self.test_result.add_css_class("caption")
        self.test_result.add_css_class("ws-success")
        self.test_result.set_visible(False)  # появляется после первого исправления
        box.append(self.test_result)
        return box

    def _history_group(self):
        from gi.repository import Gtk

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(margin_bottom=8)
        title = Gtk.Label(label="Последние исправления", xalign=0.0, hexpand=True)
        title.add_css_class("heading")
        head.append(title)
        self.clear_button = Gtk.Button(label="Очистить")
        self.clear_button.add_css_class("flat")
        self.clear_button.connect("clicked", lambda *_: self._clear_history())
        head.append(self.clear_button)
        box.append(head)
        self.history_box = boxed_list()
        box.append(self.history_box)
        note = Gtk.Label(
            label="Кнопка «↶» запоминает слово: WaySwitch больше не будет его исправлять. "
                  "Вернуть текст, исправленный только что, можно тем же жестом.",
            xalign=0.0, wrap=True, margin_top=10, margin_start=2)
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        box.append(note)
        return box

    def _off_page(self):
        from gi.repository import Adw, Gtk

        page = Adw.StatusPage(icon_name="system-shutdown-symbolic",
                              title="WaySwitch не запущен",
                              description="Слова сейчас не исправляются. Служба могла "
                                          "завершиться с ошибкой или ещё не включена.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      halign=Gtk.Align.CENTER)
        start = Gtk.Button(label="Запустить")
        start.add_css_class("pill")
        start.add_css_class("suggested-action")
        start.connect("clicked", lambda *_: self._start_service())
        journal = Gtk.Button(label="Показать журнал")
        journal.add_css_class("pill")
        journal.connect("clicked", lambda *_: dialogs.journal_dialog(self.window))
        box.append(start)
        box.append(journal)
        listing = boxed_list()
        listing.set_margin_top(18)
        self.off_autostart = Adw.SwitchRow(title="Запускать при входе в систему")
        self.off_autostart.connect("notify::active", lambda r, _p: autostart.set_service_enabled(
            r.get_active()) if r.get_active() != autostart.is_service_enabled() else None)
        listing.append(self.off_autostart)
        box.append(listing)
        page.set_child(box)
        return page

    # --- состояние ---------------------------------------------------------------

    def render_status(self, status: dict | None) -> None:
        from gi.repository import Adw, Gtk

        view = texts.status_view(status)
        if view.kind == "off":
            self.stack.set_visible_child_name("off")
            self.off_autostart.set_active(autostart.is_service_enabled())
            self._dismiss_pause_toast()
            self._last_kind = view.kind
            return
        self.stack.set_visible_child_name("main")
        self.status_title.set_label(view.title)
        self.status_subtitle.set_label(view.subtitle)
        self.status_icon.set_from_icon_name(STATUS_ICONS[view.kind])
        for kind in STATUS_ICONS:
            self.status_icon.remove_css_class(kind)
        self.status_icon.add_css_class(view.kind)
        self.status_switch.handler_block(self._switch_handler)
        self.status_switch.set_active(view.switch_active)
        self.status_switch.handler_unblock(self._switch_handler)
        self.status_switch.set_sensitive(view.switch_sensitive)
        self.status_switch.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Исправление включено" if view.switch_active else "Исправление на паузе"])
        for key, label in self.stat_labels.items():
            label.set_label(str(int(status.get(key, 0) or 0)))
        alphabet = status.get("layout")
        self.layout_badge.set_label({"ru": "RU", "latin": "EN"}.get(alphabet, "··"))

        if view.kind == "paused" and self._last_kind != "paused":
            toast = Adw.Toast(title="Исправление приостановлено", button_label="Возобновить",
                              timeout=0)
            toast.connect("button-clicked", lambda *_: self.app.proxy.call("Resume"))
            self._pause_toast = toast
            self.toasts.add_toast(toast)
        elif view.kind != "paused":
            self._dismiss_pause_toast()
        self._last_kind = view.kind

    def _dismiss_pause_toast(self) -> None:
        if self._pause_toast is not None:
            self._pause_toast.dismiss()
            self._pause_toast = None

    def _on_switch(self, switch, _pspec) -> None:
        self.app.proxy.call("Resume" if switch.get_active() else "Pause")

    def _start_service(self) -> None:
        from gi.repository import GLib

        if not autostart.start_service():
            self.toast("Не удалось запустить службу — откройте журнал")
            return

        def check():
            # Служба могла стартовать и сразу выйти (например, не нашла,
            # чем переключать раскладку): подсказать, где искать причину.
            if self.app.proxy.status is None:
                self.toast("Служба не запустилась", "Журнал",
                           lambda: dialogs.journal_dialog(self.window))
            return GLib.SOURCE_REMOVE

        GLib.timeout_add_seconds(3, check)

    def toast(self, text: str, button: str | None = None, on_button=None) -> None:
        from gi.repository import Adw

        toast = Adw.Toast(title=text, timeout=4)
        if button and on_button:
            toast.set_button_label(button)
            toast.connect("button-clicked", lambda *_: on_button())
        self.toasts.add_toast(toast)

    def render_update(self, release) -> None:
        if release is None:
            self.update_banner.set_revealed(False)
            return
        self.update_banner.set_title(f"Вышла версия {release.version}")
        self.update_banner.set_revealed(True)

    # --- история -----------------------------------------------------------------

    def on_corrected(self, original: str, fixed: str, manual: bool) -> None:
        if self.test_entry.has_focus() or self.test_entry.get_focus_child() is not None:
            self.test_result.set_label(
                f"✓ Исправлено: {original} → {fixed}. Раскладка переключена.")
            self.test_result.set_visible(True)
            self.test_entry.add_css_class("fixed")
            from gi.repository import GLib

            def calm():
                self.test_entry.remove_css_class("fixed")
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(2500, calm)
        self.render_history()

    def render_history(self) -> None:
        from gi.repository import Adw, GLib, Gtk

        clear_list(self.history_box)
        entries = list(self.app.history)
        self.clear_button.set_sensitive(bool(entries))
        if not entries:
            empty = Adw.ActionRow(title="Пока ничего не исправлено",
                                  subtitle="Исправления этого сеанса появятся здесь")
            self.history_box.append(empty)
            return
        for entry in entries:
            esc = GLib.markup_escape_text
            if entry.kind == "excluded":
                row = Adw.ActionRow(title=esc(entry.original),
                                    subtitle="Больше не исправляется")
            else:
                title = (f'<span font_family="monospace" strikethrough="true" alpha="60%">'
                         f'{esc(entry.original)}</span>  →  {esc(entry.fixed)}')
                row = Adw.ActionRow(title=title, use_markup=True)
            row.set_title_lines(1)
            icon = Gtk.Image(icon_name=HISTORY_ICONS[entry.kind])
            icon.add_css_class("dim-label")
            icon.set_tooltip_text({"auto": "Автоматически", "manual": "Вручную",
                                   "excluded": "В исключениях"}[entry.kind])
            row.add_prefix(icon)
            clock = Gtk.Label(label=entry.clock(), valign=Gtk.Align.CENTER)
            clock.add_css_class("dim-label")
            clock.add_css_class("caption")
            row.add_suffix(clock)
            if entry.kind != "excluded":
                row.add_suffix(flat_icon_button(
                    "edit-undo-symbolic", f"Больше не исправлять «{entry.original}»",
                    lambda e=entry: self._exclude(e)))
            self.history_box.append(row)

    def _exclude(self, entry) -> None:
        cfgmod.add_exception(entry.original)
        self.app.history.mark_excluded(entry.original)
        self.app.proxy.call("Reload")
        self.render_history()

        def undo():
            cfgmod.remove_exception(entry.original)
            entry.excluded = False
            self.app.proxy.call("Reload")
            self.render_history()

        self.toast(f"«{entry.original}» больше не исправляется", "Отменить", undo)

    def _clear_history(self) -> None:
        self.app.history.clear()
        self.render_history()

