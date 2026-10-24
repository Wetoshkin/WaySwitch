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

REPO_URL = "https://github.com/Wetoshkin/WaySwitch"


# --- клавиша исправления -----------------------------------------------------------

def key_capture_dialog(parent, current: str, on_done: Callable[[str | None], None],
                       title: str = "Клавиша исправления",
                       hint: str = "Нажмите клавишу, которой будете исправлять последнее слово"):
    """«Нажмите клавишу» — как выбор сочетаний в Параметрах GNOME.

    on_done(имя) — выбрана клавиша; on_done(None) — Backspace, «без клавиши».
    Esc и «Отмена» закрывают диалог без изменений.
    """
    from gi.repository import Adw, Gtk

    dialog = Adw.Dialog(title=title, content_width=400)
    chosen = {"name": current}
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
    cancel = Gtk.Button(label="Отмена")
    cancel.connect("clicked", lambda *_: dialog.close())
    done = Gtk.Button(label="Готово")
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
    note = Gtk.Label(label="Esc — отмена · Backspace — без клавиши")
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
            note.set_label("Нажмите «Готово» (или Enter) или выберите другую клавишу")
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

    dialog = Adw.Dialog(title="Добавить приложение", content_width=420, content_height=540)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=6,
                  margin_bottom=24, margin_start=20, margin_end=20)
    search = Gtk.SearchEntry(placeholder_text="Поиск или часть названия окна")
    box.append(search)

    title = Gtk.Label(label="Открытые окна", xalign=0.0)
    title.add_css_class("heading")
    box.append(title)
    listing = boxed_list()
    box.append(listing)
    footer = Gtk.Label(
        label="Совпадение ищется по части имени без учёта регистра: «telegram» подойдёт "
              "и для TelegramDesktop. Enter в поиске добавит то, что написано.",
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
            text = ("Мост доступности не отвечает — впишите название в поиск"
                    if state["error"] else
                    "Ничего не найдено — Enter добавит написанное" if query else
                    "Загрузка…" if state["error"] is None and not state["apps"] else
                    "Открытых программ не видно")
            listing.append(Adw.ActionRow(title=text))
            return
        for name in apps:
            already = name.lower() in excluded
            if already:
                mark = Gtk.Image(icon_name="object-select-symbolic")
                row = app_row(name, mark, "Уже в исключениях")
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
    undo = f"В течение {int(cfg.general.undo_window_sec)} с, слово запоминается"
    mode = cfg.gesture.manual
    if mode == "double_shift":
        return [("Последнее слово", "", ["Shift", "Shift"]),
                ("Вся фраза", "", ["Shift", "Shift", "Shift"]),
                ("Отменить исправление", undo, ["Shift", "Shift"])]
    if mode == "tap_key":
        key = gesture_keys.human_name(cfg.gesture.tap_key)
        return [("Последнее слово", "", [key]),
                ("Отменить исправление", undo, [key])]
    if mode == "pause_key":
        return [("Последнее слово", "", ["Pause"]),
                ("Отменить исправление", undo, ["Pause"])]
    return []


def selection_rows(cfg) -> list[tuple[str, str, list[str]]]:
    """Строки действий над выделенным для «Жесты и клавиши»: исправить,
    транслитерация, число прописью. Без клавиши показываем только первое —
    остальные необязательны и только загромождали бы список."""
    rows = [selection_row(cfg)]
    for field_name, title, *_rest in texts.SELECTION_KEY_ROWS[1:]:
        key = getattr(cfg.gesture, field_name)
        if key:
            rows.append((title, "Выделенный текст", [gesture_keys.human_name(key)]))
    return rows


def selection_row(cfg) -> tuple[str, str, list[str]]:
    """Строка «Выделенный текст» для «Жесты и клавиши»."""
    if cfg.gesture.selection_key:
        return ("Выделенный текст", "", [gesture_keys.human_name(cfg.gesture.selection_key)])
    return ("Выделенный текст", "Клавиша не выбрана. Выберите её в Параметрах → "
            "Исправление → «Исправить выделенное»", [])


def shortcuts_dialog(parent, cfg):
    from gi.repository import Adw

    dialog = Adw.Dialog(title="Жесты и клавиши", content_width=460)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    page = Adw.PreferencesPage()

    fix = Adw.PreferencesGroup(title="Исправление")
    rows = gesture_rows(cfg)
    if not rows:
        fix.add(Adw.ActionRow(title="Ручной жест выключен",
                              subtitle="Включите его в Параметрах → Исправление"))
    for title, subtitle, keys in [*rows, *selection_rows(cfg)]:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        if keys:
            row.add_suffix(keycaps(keys))
        fix.add(row)
    page.add(fix)

    window = Adw.PreferencesGroup(title="Окно")
    for title, keys in (("Параметры", ["Ctrl", ","]), ("Пауза / продолжить", ["Ctrl", "P"]),
                        ("Жесты и клавиши", ["Ctrl", "?"]), ("Выйти", ["Ctrl", "Q"])):
        row = Adw.ActionRow(title=title)
        row.add_suffix(keycaps(keys))
        window.add(row)
    page.add(window)

    note = Adw.PreferencesGroup(
        description="Нажатия Shift — подряд, без паузы. Жесты не работают на экране "
                    "блокировки. Для выделенного текста можно назначить и любое сочетание "
                    "в настройках системы: команда «wayswitch fix-selection».")
    page.add(note)
    toolbar.set_content(page)
    dialog.set_child(toolbar)
    dialog.present(parent)
    return dialog


# --- журнал службы -----------------------------------------------------------------

def read_journal(lines: int = 200) -> str:
    import subprocess

    try:
        out = subprocess.run(["journalctl", "--user", "-u", "wayswitch", "-n", str(lines),
                              "--no-pager", "-o", "short-iso"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Журнал недоступен: {exc}"
    return (out.stdout or out.stderr or "Журнал пуст").strip()


def journal_dialog(parent):
    from gi.repository import Adw, Gdk, Gtk

    dialog = Adw.Dialog(title="Журнал WaySwitch", content_width=720, content_height=520)
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Скопировать")
    header.pack_start(copy)
    toolbar.add_top_bar(header)
    view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                        top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
    view.get_buffer().set_text("Загрузка…")
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

    try:
        report = doctor.format_report(doctor.run_checks())
    except Exception as exc:  # noqa: BLE001
        report = f"doctor не выполнился: {exc}"
    return "\n".join([f"WaySwitch {__version__}", f"Python {platform.python_version()}",
                      f"{platform.system()} {platform.release()}", "", report])


def about_dialog(parent, update=None):
    from gi.repository import Adw, Gtk

    about = Adw.AboutDialog(
        application_name="WaySwitch", application_icon="ru.siberia.WaySwitch",
        developer_name="Wetoshkin", version=__version__, website=REPO_URL,
        issue_url=f"{REPO_URL}/issues", license_type=Gtk.License.MIT_X11,
        copyright="© 2026 Илья Ветошкин",
        developers=["Илья Ветошкин https://github.com/Wetoshkin"],
        comments="Исправляет слова, набранные не в той раскладке, на Wayland: GNOME, "
                 "KDE Plasma, Sway, Hyprland, Xfce, MATE, Budgie, LXQt.",
        debug_info="Сбор сведений…", debug_info_filename="wayswitch-debug.txt")
    if update is not None:
        about.set_release_notes_version(update.version)
        about.set_release_notes(
            f"<p>Вышла версия {update.version}. Обновить — кнопкой «Обновить» в "
            "главном окне или в Параметрах → Система.</p>")
    about.add_link("Что нового", f"{REPO_URL}/releases")
    about.add_legal_section(
        "Словари", "© Hermit Dave, FrequencyWords (по корпусу OpenSubtitles)",
        Gtk.License.CUSTOM, "Данные словарей — CC-BY-SA 4.0: "
        "https://creativecommons.org/licenses/by-sa/4.0/")
    run_in_thread(debug_info, lambda text: about.set_debug_info(str(text)))
    about.present(parent)
    return about


# --- подтверждения -----------------------------------------------------------------

def quit_dialog(parent, on_background: Callable[[], None], on_stop: Callable[[], None]):
    from gi.repository import Adw

    dialog = Adw.AlertDialog(
        heading="Остановить исправление?",
        body="Окно можно закрыть, а WaySwitch продолжит работать в фоне.")
    # В узком диалоге кнопки идут снизу вверх в порядке добавления: так
    # «Работать в фоне» оказывается сверху, как в макете.
    dialog.add_response("cancel", "Отмена")
    dialog.add_response("stop", "Остановить")
    dialog.add_response("background", "Работать в фоне")
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
        heading="Удалить WaySwitch?",
        body="Откроется окно терминала: там нужно будет подтвердить удаление и ввести "
             "пароль от вашей учётной записи. Настройки останутся на случай "
             "переустановки.")
    dialog.add_response("cancel", "Отмена")
    dialog.add_response("uninstall", "Удалить")
    dialog.set_response_appearance("uninstall", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: on_uninstall() if r == "uninstall" else None)
    dialog.present(parent)
    return dialog


def command_dialog(parent, command: str):
    """Терминал не нашёлся: показать команду, чтобы выполнить её вручную."""
    from gi.repository import Adw, Gdk

    dialog = Adw.AlertDialog(
        heading="Выполните команду в терминале",
        body="Не удалось открыть терминал. Откройте его сами (клавиша с логотипом "
             "Windows → «Терминал»), вставьте команду сочетанием Ctrl+Shift+V и "
             f"нажмите Enter:\n\n{command}")
    dialog.add_response("close", "Закрыть")
    dialog.add_response("copy", "Скопировать команду")
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

    dialog = Adw.AlertDialog(heading="Сбросить параметры?",
                             body="Все параметры вернутся к исходным. Списки исключений "
                                  "останутся.")
    dialog.add_response("cancel", "Отмена")
    dialog.add_response("reset", "Сбросить")
    dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: on_reset() if r == "reset" else None)
    dialog.present(parent)
    return dialog
