"""Первый запуск: три шага в AdwCarousel.

1. Что делает WaySwitch.
2. Проверка системы — графическая версия ``wayswitch doctor``: обязательные
   пункты держат кнопку «Продолжить» серой, пока не исправлены.
3. Два жеста, автозапуск и значок в панели.

Признак «уже показывали» — файл ~/.config/wayswitch/onboarded.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable

from wayswitch import config as cfgmod
from wayswitch.gui import autostart, dialogs, portable, texts
from wayswitch.gui.preferences import enable_extension
from wayswitch.gui.widgets import boxed_list, clear_list, keycaps, run_in_thread
from wayswitch.i18n import gettext as _


def flag_path():
    return cfgmod.config_dir() / "onboarded"


def needed() -> bool:
    return not flag_path().exists()


def mark_done() -> None:
    try:
        path = flag_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1\n", encoding="utf-8")
    except OSError:
        pass


def _spawn(argv: list[str]) -> bool:
    if shutil.which(argv[0]) is None:
        return False
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True


def logout() -> bool:
    """Выход из сеанса: группа input и права udev применяются только так."""
    return (_spawn(["gnome-session-quit", "--logout"])
            or _spawn(["qdbus6", "org.kde.LogoutPrompt", "/LogoutPrompt", "promptLogout"])
            or _spawn(["qdbus", "org.kde.LogoutPrompt", "/LogoutPrompt", "promptLogout"]))


def open_layout_settings() -> bool:
    from wayswitch.backends.desktop import COSMIC, detect_desktop

    if detect_desktop() == COSMIC:
        return _spawn(["cosmic-settings", "keyboard"])
    return (_spawn(["gnome-control-center", "keyboard"])
            or _spawn(["systemsettings", "kcm_keyboard"]))


ROW_ICONS = {"ok": ("object-select-symbolic", "success"),
             "fail": ("dialog-error-symbolic", "error"),
             "optional": ("radio-symbolic", "dim-label")}
ACTIONS = {"logout": ("Sign out", logout),
           "extension": ("Enable", enable_extension),
           "layouts": ("Open settings", open_layout_settings)}


class Onboarding:
    def __init__(self, app, on_finish: Callable[[], None]):
        from gi.repository import Adw, Gtk

        self.app = app
        self.on_finish = on_finish
        self.window = Adw.Window(application=app, title=_("Welcome to WaySwitch"),
                                 default_width=560, default_height=640)
        toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar(show_title=False)
        self.back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=_("Back"))
        self.back.connect("clicked", lambda *_: self._go(self._index() - 1))
        self.header.pack_start(self.back)
        toolbar.add_top_bar(self.header)

        self.carousel = Adw.Carousel(interactive=False, vexpand=True)
        self.carousel.append(self._welcome())
        self.carousel.append(self._checks())
        self.carousel.append(self._done())
        self.carousel.connect("page-changed", lambda *_: self._sync())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.carousel)
        dots = Adw.CarouselIndicatorDots(carousel=self.carousel, margin_bottom=20)
        box.append(dots)
        toolbar.set_content(box)
        self.window.set_content(toolbar)
        self.window.connect("close-request", lambda *_: (self._finish(), False)[1])
        self._finished = False
        self._sync()

    def present(self) -> None:
        self.window.present()

    def _index(self) -> int:
        return int(round(self.carousel.get_position()))

    def _go(self, index: int) -> None:
        index = max(0, min(self.carousel.get_n_pages() - 1, index))
        self.carousel.scroll_to(self.carousel.get_nth_page(index), True)
        if index == 1:
            self._run_checks()

    def _sync(self) -> None:
        self.back.set_visible(self._index() > 0)

    def _page(self, *widgets, button=None):
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, hexpand=True,
                       margin_start=40, margin_end=40, margin_bottom=8)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, vexpand=True)
        for w in widgets:
            body.append(w)
        page.append(body)
        if button is not None:
            page.append(button)
        return page

    # --- шаги --------------------------------------------------------------------

    def _welcome(self):
        from gi.repository import Gtk

        icon = Gtk.Image(icon_name="io.github.Wetoshkin.WaySwitch", pixel_size=128, margin_top=40)
        title = Gtk.Label(label=_("Welcome to WaySwitch"), wrap=True)
        title.add_css_class("title-1")
        text = Gtk.Label(label=_("Typed a word in the wrong layout — WaySwitch will fix it "
                                 "on Space and switch the layout."), wrap=True,
                         justify=Gtk.Justification.CENTER)
        text.add_css_class("dim-label")
        if portable.package_kind():
            text.set_label(_("This is the settings window. To correct the layout and use "
                             "gestures, install the separate native WaySwitch service on "
                             "the host system. The next step will check the connection and "
                             "show the instructions."))
        sample = Gtk.Label(use_markup=True, label=(
            '<span font_family="monospace" strikethrough="true" alpha="60%">ghbdtn</span>'
            '   →   <b>привет</b>'))
        start = Gtk.Button(label=_("Start"), halign=Gtk.Align.CENTER)
        start.add_css_class("pill")
        start.add_css_class("suggested-action")
        start.connect("clicked", lambda *_: self._go(1))
        return self._page(icon, title, text, sample, button=start)

    def _checks(self):
        from gi.repository import Gtk

        title = Gtk.Label(label=_("System check"), margin_top=12)
        title.add_css_class("title-2")
        self.checks_hint = Gtk.Label(label=_("Checking…"), wrap=True, xalign=0.0)
        self.checks_hint.add_css_class("dim-label")
        self.checks_box = boxed_list()
        self.checks_box.set_valign(Gtk.Align.START)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        again = Gtk.Button(label=_("Check again"))
        again.add_css_class("pill")
        again.connect("clicked", lambda *_: self._run_checks())
        self.continue_button = Gtk.Button(label=_("Continue"), sensitive=False)
        self.continue_button.add_css_class("pill")
        self.continue_button.add_css_class("suggested-action")
        self.continue_button.connect("clicked", lambda *_: self._go(2))
        buttons.append(again)
        buttons.append(self.continue_button)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      propagate_natural_height=True)
        scroller.set_child(self.checks_box)
        return self._page(title, self.checks_hint, scroller, button=buttons)

    def _run_checks(self) -> None:
        if portable.package_kind():
            self._show_portable_checks()
            return
        from wayswitch import doctor

        self.checks_hint.set_label(_("Checking…"))
        self.continue_button.set_sensitive(False)
        run_in_thread(doctor.run_checks, self._show_checks)

    def _show_portable_checks(self) -> None:
        from gi.repository import Adw, Gtk

        self.app.proxy.refresh()
        connected = self.app.proxy.status is not None
        clear_list(self.checks_box)
        self.checks_hint.set_label(
            _("The native service is connected. Settings and gestures are shared with "
              "the host system.")
            if connected else
            _("First install and start the native service on the host system."))
        row = Adw.ActionRow(title=_("Native WaySwitch service"),
                            subtitle=_("Connected over D-Bus") if connected else
                            _("This window has no access to the keyboard and does not "
                              "replace the service"))
        button = Gtk.Button(label=_("Instructions"), valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda *_: portable.show_instructions(self.window))
        row.add_suffix(button)
        self.checks_box.append(row)
        self.continue_button.set_sensitive(connected)

    def _show_checks(self, result) -> None:
        from gi.repository import Adw, Gtk

        clear_list(self.checks_box)
        if isinstance(result, Exception):
            self.checks_hint.set_label(
                _("The check failed: {error}").format(error=result))
            self.continue_button.set_sensitive(True)
            return
        rows = texts.onboarding_rows(result)
        blocking = texts.onboarding_blocking(rows)
        self.checks_hint.set_label(texts.onboarding_hint(rows))
        for item in rows:
            row = Adw.ActionRow(title=item.title, subtitle=item.subtitle)
            icon_name, css = ROW_ICONS[item.state]
            icon = Gtk.Image(icon_name=icon_name)
            icon.add_css_class(css)
            row.add_prefix(icon)
            if item.state == "fail":
                row.add_css_class("ws-fail-row")
            if item.action in ACTIONS:
                label, fn = ACTIONS[item.action]
                button = Gtk.Button(label=_(label), valign=Gtk.Align.CENTER)
                button.connect("clicked", lambda _b, f=fn: (f(), self._run_checks()))
                row.add_suffix(button)
            self.checks_box.append(row)
        self.continue_button.set_sensitive(not blocking)

    def _done(self):
        from gi.repository import Adw, Gtk

        try:
            cfg = cfgmod.load()
        except cfgmod.ConfigError:
            cfg = cfgmod.Config()
        icon = Gtk.Image(icon_name="object-select-symbolic", pixel_size=36,
                         halign=Gtk.Align.CENTER, margin_top=12)
        icon.add_css_class("ws-done-icon")
        title = Gtk.Label(label=_("Setup complete"))
        title.add_css_class("title-1")
        sub = Gtk.Label(label=_("Remember two gestures"))
        sub.add_css_class("dim-label")
        gestures = boxed_list()
        rows = dialogs.gesture_rows(cfg)
        for name, subtitle, keys in rows[:1] + rows[-1:] if rows else []:
            row = Adw.ActionRow(title=_("Fix manually") if name == _("Last word")
                                else name,
                                subtitle=_("If WaySwitch missed a word")
                                if name == _("Last word") else subtitle)
            row.add_suffix(keycaps(keys))
            gestures.append(row)
        switches = boxed_list()
        self.sw_service = Adw.SwitchRow(title=_("Start at sign-in"), active=True)
        from wayswitch.backends.desktop import detect_desktop

        self.sw_tray = Adw.SwitchRow(title=texts.tray_title(detect_desktop()), active=True)
        self.sw_updates = Adw.SwitchRow(title=_("Notify about new versions"),
                                        subtitle=_("Ask GitHub once a day"),
                                        active=cfg.general.check_updates
                                        and portable.can_self_update())
        if not portable.can_self_update():
            self.sw_updates.set_sensitive(False)
            self.sw_updates.set_subtitle(_(portable.PACKAGED_UPDATE_TEXT))
        if portable.package_kind():
            for row in (self.sw_service, self.sw_tray):
                row.set_active(False)
                row.set_sensitive(False)
                row.set_subtitle(_("Configured on the host system, see the service "
                                   "instructions"))
        for row in (self.sw_service, self.sw_tray, self.sw_updates):
            switches.append(row)
        finish = Gtk.Button(label=_("Open WaySwitch"), halign=Gtk.Align.CENTER)
        finish.add_css_class("pill")
        finish.add_css_class("suggested-action")
        finish.connect("clicked", lambda *_: self.window.close())
        return self._page(icon, title, sub, gestures, switches, button=finish)

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        if self._index() == 2:
            # Выбор применяем, только если человек дошёл до последнего шага.
            if not portable.package_kind():
                autostart.set_service_enabled(self.sw_service.get_active())
                autostart.set_tray_enabled(self.sw_tray.get_active())
            try:
                cfg = cfgmod.load()
                if portable.can_self_update():
                    cfg.general.check_updates = self.sw_updates.get_active()
                cfgmod.save(cfg)
            except (cfgmod.ConfigError, OSError):
                pass
        if self._index() == 2:
            # Закрыли раньше последнего шага — служба не включена, покажем
            # первый запуск ещё раз в следующий раз.
            mark_done()
        self.on_finish()
