"""Диалоги окна: выбор клавиши, добавление программы, жесты и клавиши,
«О WaySwitch», журнал, подтверждения выхода и сброса.

Все — AdwDialog/AdwAlertDialog (libadwaita 1.5+), показываются поверх
родительского окна через present(parent).
"""

from __future__ import annotations

from collections.abc import Callable

from wayswitch import __version__
from wayswitch import config as cfgmod
from wayswitch.gui import gesture_keys, texts
from wayswitch.gui.widgets import boxed_list, clear_list, keycap, keycaps, run_in_thread
from wayswitch.i18n import gettext as _

REPO_URL = "https://github.com/Wetoshkin/WaySwitch"


# --- клавиша исправления -----------------------------------------------------------

def key_capture_dialog(parent, current: str, on_done: Callable[[str | None], None],
                       title: str | None = None, hint: str | None = None):
    """«Нажмите клавишу» — как выбор сочетаний в Параметрах GNOME.

    on_done(имя) — выбрана клавиша; on_done(None) — Backspace, «без клавиши».
    Esc и «Отмена» закрывают диалог без изменений.
    """
    from gi.repository import Adw, Gtk

    title = title or _("Correction key")
    hint = hint or _("Press the key you will use to fix the last word")
    dialog = Adw.Dialog(title=title, content_width=400)
    chosen = {"name": current}
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
    cancel = Gtk.Button(label=_("Cancel"))
    cancel.connect("clicked", lambda *_: dialog.close())
    done = Gtk.Button(label=_("Done"))
    done.add_css_class("suggested-action")

    def finish(*_):
        dialog.close()
        on_done(chosen["name"])

    done.connect("clicked", finish)
    header.pack_start(cancel)
    header.pack_end(done)
    toolbar.add_top_bar(header)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=28,
                  margin_bottom=36, margin_start=32, margin_end=32)
    icon = Gtk.Image(icon_name="input-keyboard-symbolic", pixel_size=56)
    icon.add_css_class("dim-label")
    hint_label = Gtk.Label(label=hint, wrap=True, justify=Gtk.Justification.CENTER,
                           max_width_chars=34)
    hint_label.add_css_class("dim-label")
    cap = keycap(gesture_keys.human_name(current) if current else "…", large=True)
    cap.set_halign(Gtk.Align.CENTER)
    note = Gtk.Label(label=_("Esc — cancel · Backspace — no key"))
    note.add_css_class("dim-label")
    note.add_css_class("caption")
    for widget in (icon, hint_label, cap, note):
        box.append(widget)
    toolbar.set_content(box)
    dialog.set_child(toolbar)

    controller = Gtk.EventControllerKey()

    def on_key(_ctl, _keyval, keycode, _state):
        verdict, value = gesture_keys.capture(keycode - gesture_keys.GDK_KEYCODE_OFFSET)
        if verdict == "cancel":
            dialog.close()
        elif verdict == "clear":
            dialog.close()
            on_done(None)
        elif verdict == "key":
            chosen["name"] = value
            cap.set_label(gesture_keys.human_name(value))
            note.set_label(_("Press “Done” (or Enter) or choose another key"))
        elif verdict == "done":
            finish()
        else:
            note.set_label(value)
        return True  # клавиша не уходит дальше: Alt не открывает меню и т. п.

    controller.connect("key-pressed", on_key)
    dialog.add_controller(controller)
    dialog.present(parent)
    return dialog


# --- добавить приложение -----------------------------------------------------------

def personal_word_dialog(parent, entry, on_save):
    from gi.repository import Adw, Gtk

    from wayswitch.learning import normalize_word

    dialog = Adw.Dialog(title=_("Edit word") if entry else _("Add a word"), content_width=420)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                  margin_top=12, margin_bottom=24, margin_start=20, margin_end=20)
    group = Adw.PreferencesGroup(description=_(
        "Enter the word in the correct layout, not an arbitrary replacement phrase"))
    language = Adw.ComboRow(title=_("Language"),
                           model=Gtk.StringList.new([_("English"), _("Russian")]))
    language.set_selected(1 if entry and entry[0] == "ru" else 0)
    word = Adw.EntryRow(title=_("Word in the correct layout"))
    if entry:
        word.set_text(entry[1])
    group.add(language)
    group.add(word)
    box.append(group)
    error = Gtk.Label(wrap=True, xalign=0)
    error.add_css_class("error")
    box.append(error)
    save = Gtk.Button(label=_("Save"))
    save.add_css_class("suggested-action")

    def apply(*_args):
        lang = ("en", "ru")[language.get_selected()]
        try:
            value = normalize_word(lang, word.get_text())
        except ValueError:
            error.set_label(_("Use 1–64 letters of the selected language; no spaces"))
            return
        if on_save((lang, value)):
            dialog.close()
        else:
            error.set_label(_("The dictionary was not saved"))

    save.connect("clicked", apply)
    word.connect("apply", apply)
    box.append(save)
    toolbar.set_content(box)
    dialog.set_child(toolbar)
    dialog.present(parent)
    return dialog


def _app_info(name: str):
    """Ярлык программы по имени из at-spi: значок и человеческое название."""
    from gi.repository import Gio

    try:
        found = Gio.DesktopAppInfo.search(name)
    except Exception:  # noqa: BLE001
        return None
    for group in found:
        for desktop_id in group:
            info = Gio.DesktopAppInfo.new(desktop_id)
            if info is not None:
                return info
    return None


def app_row(name: str, suffix=None, subtitle: str | None = None):
    """Строка программы: значок из ярлыка, название, внизу — что сравнивается."""
    from gi.repository import Adw, Gtk

    info = _app_info(name)
    title = info.get_display_name() if info is not None else name
    row = Adw.ActionRow(title=title, subtitle=subtitle if subtitle is not None else name,
                        use_markup=False)
    row.set_title_lines(1)
    icon = Gtk.Image(pixel_size=32)
    gicon = info.get_icon() if info is not None else None
    if gicon is not None:
        icon.set_from_gicon(gicon)
    else:
        icon.set_from_icon_name("application-x-executable-symbolic")
    row.add_prefix(icon)
    if suffix is not None:
        row.add_suffix(suffix)
    return row


def add_app_dialog(parent, on_add: Callable[[str], None]):
    """Открытые программы (из at-spi) и поиск/ввод своего названия.

    Раньше была кнопка «Добавить текущую программу» — но в момент нажатия
    активное окно — сам WaySwitch. Здесь человек выбирает программу из списка.
    """
    from gi.repository import Adw, Gtk

    from wayswitch import a11y

    dialog = Adw.Dialog(title=_("Add application"), content_width=420, content_height=540)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=6,
                  margin_bottom=24, margin_start=20, margin_end=20)
    search = Gtk.SearchEntry(placeholder_text=_("Search or part of a window name"))
    box.append(search)

    title = Gtk.Label(label=_("Open windows"), xalign=0.0)
    title.add_css_class("heading")
    box.append(title)
    listing = boxed_list()
    box.append(listing)
    footer = Gtk.Label(
        label=_("Match is case-insensitive and by part of the name: “telegram” also "
                "matches TelegramDesktop. Enter in the search field adds what is typed."),
        wrap=True, xalign=0.0)
    footer.add_css_class("dim-label")
    footer.add_css_class("caption")
    box.append(footer)

    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    scroller.set_child(box)
    toolbar.set_content(scroller)
    dialog.set_child(toolbar)

    state = {"apps": [], "error": None}

    def add(name: str):
        name = name.strip()
        if name:
            dialog.close()
            on_add(name)

    def render():
        clear_list(listing)
        query = search.get_text().strip().lower()
        excluded = [a.lower() for a in cfgmod.load_apps()]
        apps = [a for a in state["apps"] if not query or query in a.lower()]
        if not apps:
            text = (_("The accessibility bridge is not responding — type the name into "
                      "the search")
                    if state["error"] else
                    _("Nothing found — Enter adds what is typed") if query else
                    _("Loading…") if state["error"] is None and not state["apps"] else
                    _("No open applications visible"))
            listing.append(Adw.ActionRow(title=text))
            return
        for name in apps:
            already = name.lower() in excluded
            if already:
                mark = Gtk.Image(icon_name="object-select-symbolic")
                row = app_row(name, mark, _("Already excluded"))
                row.set_sensitive(False)
            else:
                plus = Gtk.Image(icon_name="list-add-symbolic")
                plus.add_css_class("dim-label")
                row = app_row(name, plus)
                row.set_activatable(True)
                row.connect("activated", lambda _r, n=name: add(n))
            listing.append(row)

    def loaded(result):
        if isinstance(result, Exception):
            state["error"] = result
            state["apps"] = []
        else:
            state["error"] = False
            state["apps"] = sorted(result, key=str.lower)
        render()

    search.connect("search-changed", lambda *_: render())
    search.connect("activate", lambda *_: add(search.get_text()))
    render()
    # at-spi — десятки D-Bus-вызовов к чужим программам: только в потоке.
    run_in_thread(a11y.list_applications, loaded)
    dialog.present(parent)
    return dialog


# --- жесты и клавиши ---------------------------------------------------------------

def gesture_rows(cfg) -> list[tuple[str, str, list[str]]]:
    """(название, подпись, клавиши) для «Жесты и клавиши» и шага онбординга.

    Чистая функция: зависит только от конфига.
    """
    undo = _("Within {seconds} s, the word is remembered").format(
        seconds=int(cfg.general.undo_window_sec))
    mode = cfg.gesture.manual
    if mode == "double_shift":
        return [(_("Last word"), "", ["Shift", "Shift"]),
                (_("Whole phrase"), "", ["Shift", "Shift", "Shift"]),
                (_("Undo correction"), undo, ["Shift", "Shift"])]
    if mode == "tap_key":
        key = gesture_keys.human_name(cfg.gesture.tap_key)
        return [(_("Last word"), "", [key]),
                (_("Undo correction"), undo, [key])]
    if mode == "pause_key":
        return [(_("Last word"), "", ["Pause"]),
                (_("Undo correction"), undo, ["Pause"])]
    return []


def selection_rows(cfg) -> list[tuple[str, str, list[str]]]:
    """Строки действий над выделенным для «Жесты и клавиши»: исправить,
    транслитерация, число прописью. Без клавиши показываем только первое —
    остальные необязательны и только загромождали бы список."""
    rows = [selection_row(cfg)]
    for field_name, title, *_rest in texts.SELECTION_KEY_ROWS[1:]:
        key = getattr(cfg.gesture, field_name)
        if key:
            rows.append((_(title), _("Selected text"), [gesture_keys.human_name(key)]))
    return rows


def selection_row(cfg) -> tuple[str, str, list[str]]:
    """Строка «Выделенный текст» для «Жесты и клавиши»."""
    if cfg.gesture.selection_key:
        return (_("Selected text"), "",
                [gesture_keys.human_name(cfg.gesture.selection_key)])
    return (_("Selected text"), _("No key chosen. Choose it in Settings → "
                                  "Correction → “Fix selection”"), [])


def shortcuts_dialog(parent, cfg):
    from gi.repository import Adw

    dialog = Adw.Dialog(title=_("Gestures and keys"), content_width=460)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    page = Adw.PreferencesPage()

    fix = Adw.PreferencesGroup(title=_("Correction"))
    rows = gesture_rows(cfg)
    if not rows:
        fix.add(Adw.ActionRow(title=_("Manual gesture is off"),
                              subtitle=_("Turn it on in Settings → Correction")))
    for title, subtitle, keys in [*rows, *selection_rows(cfg)]:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        if keys:
            row.add_suffix(keycaps(keys))
        fix.add(row)
    page.add(fix)

    window = Adw.PreferencesGroup(title=_("Window"))
    for title, keys in ((_("Settings"), ["Ctrl", ","]), (_("Pause / resume"), ["Ctrl", "P"]),
                        (_("Gestures and keys"), ["Ctrl", "?"]), (_("Quit"), ["Ctrl", "Q"])):
        row = Adw.ActionRow(title=title)
        row.add_suffix(keycaps(keys))
        window.add(row)
    page.add(window)

    note = Adw.PreferencesGroup(
        description=_("Shift presses must be consecutive, without a pause. Gestures do not "
                      "work on the lock screen. For selected text you can also assign any "
                      "shortcut in the system settings: the command "
                      "“wayswitch fix-selection”."))
    page.add(note)
    toolbar.set_content(page)
    dialog.set_child(toolbar)
    dialog.present(parent)
    return dialog


# --- журнал службы -----------------------------------------------------------------

def read_journal(lines: int = 200) -> str:
    from wayswitch.gui import portable

    if portable.package_kind():
        return (_("The service log is read in a terminal on the host system:\n\n"
                  "journalctl --user -u wayswitch.service -n 200 --no-pager\n\n")
                + portable.instructions())
    import subprocess

    try:
        out = subprocess.run(["journalctl", "--user", "-u", "wayswitch", "-n", str(lines),
                              "--no-pager", "-o", "short-iso"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        return _("Log unavailable: {error}").format(error=exc)
    return (out.stdout or out.stderr or _("Log is empty")).strip()


def journal_dialog(parent):
    from gi.repository import Adw, Gdk, Gtk

    dialog = Adw.Dialog(title=_("WaySwitch log"), content_width=720, content_height=520)
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=_("Copy"))
    header.pack_start(copy)
    toolbar.add_top_bar(header)
    view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                        top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
    view.get_buffer().set_text(_("Loading…"))
    scroller = Gtk.ScrolledWindow(vexpand=True)
    scroller.set_child(view)
    toolbar.set_content(scroller)
    dialog.set_child(toolbar)

    def loaded(text):
        view.get_buffer().set_text(str(text))
        adj = scroller.get_vadjustment()
        adj.set_value(adj.get_upper())

    def do_copy(*_):
        buf = view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        Gdk.Display.get_default().get_clipboard().set(text)

    copy.connect("clicked", do_copy)
    run_in_thread(read_journal, loaded)
    dialog.present(parent)
    return dialog


# --- о программе -------------------------------------------------------------------

def debug_info() -> str:
    """Текст для «Устранение неполадок»: doctor + версии. Блокирует."""
    import platform

    from wayswitch import doctor
    from wayswitch.gui import portable

    try:
        if portable.package_kind():
            # debug_info выполняется в фоновом потоке: здесь уместен синхронный
            # запрос, а асинхронный прокси не успеет ответить до возврата отчёта.
            from wayswitch.cli import call_daemon

            try:
                (status,) = call_daemon("GetStatus")
            except Exception:  # noqa: BLE001 — служба не запущена/не ответила
                status = None
            report = portable.connection_report(status)
        else:
            report = doctor.format_report(doctor.run_checks())
    except Exception as exc:  # noqa: BLE001
        report = _("doctor failed: {error}").format(error=exc)
    return "\n".join([f"WaySwitch {__version__}", f"Python {platform.python_version()}",
                      f"{platform.system()} {platform.release()}", "", report])


def about_dialog(parent, update=None):
    from gi.repository import Adw, Gtk

    about = Adw.AboutDialog(
        application_name="WaySwitch", application_icon="io.github.Wetoshkin.WaySwitch",
        developer_name="Wetoshkin", version=__version__, website=REPO_URL,
        issue_url=f"{REPO_URL}/issues", license_type=Gtk.License.MIT_X11,
        copyright="© 2026 Илья Ветошкин",
        developers=["Илья Ветошкин https://github.com/Wetoshkin"],
        comments=_("Fixes words typed in the wrong layout on Wayland: GNOME, "
                   "KDE Plasma, Sway, Hyprland, Xfce, MATE, Budgie, LXQt."),
        debug_info=_("Collecting information…"), debug_info_filename="wayswitch-debug.txt")
    if update is not None:
        about.set_release_notes_version(update.version)
        about.set_release_notes(
            _("<p>Version {version} is available. Update with the “Update” button in the "
              "main window or in Settings → System.</p>").format(version=update.version))
    about.add_link(_("What's new"), f"{REPO_URL}/releases")
    about.add_legal_section(
        _("Dictionaries"), "© Hermit Dave, FrequencyWords (OpenSubtitles corpus)",
        Gtk.License.CUSTOM,
        _("Dictionary data — CC-BY-SA 4.0: "
          "https://creativecommons.org/licenses/by-sa/4.0/"))
    run_in_thread(debug_info, lambda text: about.set_debug_info(str(text)))
    about.present(parent)
    return about


# --- подтверждения -----------------------------------------------------------------

def quit_dialog(parent, on_background: Callable[[], None], on_stop: Callable[[], None]):
    from gi.repository import Adw

    from wayswitch.gui import portable

    dialog = Adw.AlertDialog(
        heading=_("Pause correction?") if portable.package_kind()
        else _("Stop correction?"),
        body=_("You can close the window and WaySwitch will keep running in the background."))
    # В узком диалоге кнопки идут снизу вверх в порядке добавления: так
    # «Работать в фоне» оказывается сверху, как в макете.
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("stop", _("Pause") if portable.package_kind() else _("Stop"))
    dialog.add_response("background", _("Run in background"))
    dialog.set_response_appearance("background", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_response_appearance("stop", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("background")
    dialog.set_close_response("cancel")
    if hasattr(dialog, "set_prefer_wide_layout"):
        dialog.set_prefer_wide_layout(False)

    def on_response(_d, response):
        if response == "background":
            on_background()
        elif response == "stop":
            on_stop()

    dialog.connect("response", on_response)
    dialog.present(parent)
    return dialog


def uninstall_dialog(parent, on_uninstall: Callable[[], None]):
    from gi.repository import Adw

    dialog = Adw.AlertDialog(
        heading=_("Remove WaySwitch?"),
        body=_("A terminal window will open: confirm the removal there and enter the "
               "password for your account. Settings will remain in case you reinstall."))
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("uninstall", _("Remove"))
    dialog.set_response_appearance("uninstall", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: on_uninstall() if r == "uninstall" else None)
    dialog.present(parent)
    return dialog


def command_dialog(parent, command: str):
    """Терминал не нашёлся: показать команду, чтобы выполнить её вручную."""
    from gi.repository import Adw, Gdk

    dialog = Adw.AlertDialog(
        heading=_("Run the command in a terminal"),
        body=_("Could not open a terminal. Open one yourself (Windows logo key → "
               "“Terminal”), paste the command with Ctrl+Shift+V and press Enter:"
               "\n\n{command}").format(command=command))
    dialog.add_response("close", _("Close"))
    dialog.add_response("copy", _("Copy command"))
    dialog.set_response_appearance("copy", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_close_response("close")

    def on_response(_d, response):
        if response == "copy":
            Gdk.Display.get_default().get_clipboard().set(command)

    dialog.connect("response", on_response)
    dialog.present(parent)
    return dialog


def reset_dialog(parent, on_reset: Callable[[], None]):
    from gi.repository import Adw

    dialog = Adw.AlertDialog(heading=_("Reset settings?"),
                             body=_("All settings will return to their defaults. "
                                    "Exception lists will remain."))
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("reset", _("Reset"))
    dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: on_reset() if r == "reset" else None)
    dialog.present(parent)
    return dialog
