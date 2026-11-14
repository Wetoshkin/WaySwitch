#!/usr/bin/env python3
"""Real-widget geometry checks for the adaptive GTK/libadwaita dialogs.

Runs on Linux under Xvfb (CI installs the package and its GTK dependencies):

    PYTHONPATH=. dbus-run-session -- xvfb-run -a python3 tools/smoke_gui_geometry.py

The tool uses an isolated XDG profile and fake updater/proxy objects, so it does
not read a real user configuration, check the network, or start an update.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace


def _load_gtk():
    try:
        import gi
    except ImportError as exc:  # Windows runners do not have PyGObject.
        raise SystemExit("GTK geometry smoke needs Linux with PyGObject and Xvfb") from exc
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk, Pango

    return Adw, GLib, Gtk, Pango


Adw, GLib, Gtk, Pango = _load_gtk()

from wayswitch import companion, updater, updates  # noqa: E402
from wayswitch.gui import (  # noqa: E402
    autostart,
    companion_dialog,
    dialogs,
    preferences,
    style,
    update_dialog,
)


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def ancestors(widget):
    parent = widget.get_parent()
    while parent is not None:
        yield parent
        parent = parent.get_parent()


def settle(seconds: float = 0.35) -> None:
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)


def wait_until(predicate, description: str, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {description}")
        settle(0.02)


def use_locale(language: str) -> None:
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        os.environ.pop(name, None)
    os.environ["LANG"] = "ru_RU.UTF-8" if language == "ru" else "en_US.UTF-8"


def use_appearance(font: str, dark: bool) -> None:
    settings = Gtk.Settings.get_default()
    settings.set_property("gtk-font-name", font)
    manager = Adw.StyleManager.get_default()
    manager.set_color_scheme(
        Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
    settle(0.1)


def make_window(app, width: int, height: int):
    window = Adw.ApplicationWindow(application=app)
    # Размер стенда — область содержимого, без зависящей от X11 тени окна.
    window.set_decorated(False)
    window.set_default_size(width, height)
    window.set_content(Gtk.Label(label="WaySwitch geometry probe"))
    window.present()
    wait_until(window.get_mapped, f"{width}x{height} parent window")
    settle()
    assert window.get_width() == width and window.get_height() == height, (
        f"test viewport: expected {width}x{height}, "
        f"got {window.get_width()}x{window.get_height()}")
    return window


def label_of(widget) -> str:
    getter = getattr(widget, "get_label", None)
    return getter() if getter is not None else ""


def assert_dialog_fits(name: str, window, dialog) -> None:
    assert dialog.get_mapped(), f"{name}: dialog was not mapped"
    assert dialog.get_width() <= window.get_width() + 1, (
        f"{name}: dialog {dialog.get_width()}px is wider than "
        f"the {window.get_width()}px window")
    assert dialog.get_height() <= window.get_height() + 1, (
        f"{name}: dialog {dialog.get_height()}px is taller than "
        f"the {window.get_height()}px window")


def assert_no_horizontal_clipping(name: str, window, dialog) -> None:
    over = []
    for widget in walk(dialog):
        if not widget.get_mapped() or not isinstance(widget, (Gtk.Button, Gtk.Entry, Gtk.Expander)):
            continue
        ok, rect = widget.compute_bounds(window)
        if not ok:
            continue
        right = rect.get_x() + rect.get_width()
        if rect.get_x() < -0.5 or right > window.get_width() + 0.5:
            over.append((type(widget).__name__, label_of(widget),
                         round(rect.get_x(), 1), round(right, 1)))
    assert not over, (
        f"{name}: controls are clipped by the {window.get_width()}px window: {over}")


def assert_long_text_wraps(name: str, window, dialog, token: str) -> None:
    widgets = []
    for widget in walk(dialog):
        if isinstance(widget, Gtk.Label) and token in widget.get_text():
            widgets.append(widget)
        elif isinstance(widget, Gtk.TextView):
            buffer = widget.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
            if token in text:
                widgets.append(widget)
    assert widgets, f"{name}: no text widget displays the long token"
    for widget in widgets:
        if isinstance(widget, Gtk.Label):
            assert widget.get_wrap(), f"{name}: long text is not wrapping"
            assert widget.get_wrap_mode() == Pango.WrapMode.WORD_CHAR, (
                f"{name}: long unbroken text must use WORD_CHAR wrapping")
            layout = widget.get_layout()
            assert layout is not None
            assert not layout.is_ellipsized(), f"{name}: long text is ellipsized"
            assert layout.get_line_count() > 1, (
                f"{name}: long token did not wrap to a second line")
        else:
            assert widget.get_wrap_mode() == Gtk.WrapMode.WORD_CHAR, (
                f"{name}: long unbroken text must use WORD_CHAR wrapping")
        ok, rect = widget.compute_bounds(window)
        assert ok
        assert rect.get_x() + rect.get_width() <= window.get_width() + 0.5, (
            f"{name}: wrapped text exceeds the window width")


def update_scenario(window, _language):
    note_token = "N" + "x" * 140
    command_token = "C" + "y" * 140
    source = updater.Source(
        "appimage", installed="1.2.0",
        path="/tmp/" + command_token + "/WaySwitch.AppImage")
    release = updates.Release("1.2.1-" + note_token, "https://example.invalid",
                              notes="Release notes with a long token:\n" + note_token)
    info = updater.UpdateInfo(
        source=source, available=release, state="available", command="update",
        automatic=True, message="Package checks precede installation.\n" + note_token)
    app = SimpleNamespace(
        proxy=SimpleNamespace(status={"version": "1.2.0"}, call=lambda *args: None),
        update_text=lambda: "Isolated update profile",
        _parent=lambda: window,
        update_info=info,
        check_updates=lambda **kwargs: kwargs["done"](),
    )
    dialog = update_dialog.show(app)
    wait_until(dialog.get_mapped, "updates dialog")
    settle()
    expanders = [widget for widget in walk(dialog) if isinstance(widget, Gtk.Expander)]
    assert len(expanders) == 1, "updates dialog should have one command expander"
    expanders[0].set_expanded(True)
    wait_until(lambda: any(
        isinstance(widget, Gtk.Label) and command_token in widget.get_text()
        and widget.get_mapped() for widget in walk(dialog)), "update command label")
    settle()
    return dialog, (note_token, command_token)


def preferences_scenario(window, language):
    proxy = SimpleNamespace(status={}, call=lambda *args: None)
    app = SimpleNamespace(proxy=proxy, update_text=lambda: "Isolated profile")
    prefs = preferences.Preferences(app)
    prefs.present(window)
    wait_until(lambda: prefs.dialog.get_mapped(), "preferences dialog")
    settle()
    pages = [widget for widget in walk(prefs.dialog)
             if isinstance(widget, Adw.PreferencesPage) and widget.get_title()]
    assert len(pages) == 4, f"expected four preference pages, got {len(pages)}"
    title = "Словарь" if language == "ru" else "Dictionary"
    dictionary = next((page for page in pages if page.get_title() == title), None)
    assert dictionary is not None, (
        f"dictionary page title is not the short navigation label: "
        f"{[page.get_title() for page in pages]}")
    prefs.dialog.set_visible_page(dictionary)
    settle()

    add_labels = {"Добавить слово", "Add a word"}
    add = next((button for button in walk(prefs.dialog)
                if isinstance(button, Gtk.Button)
                and button.get_tooltip_text() in add_labels), None)
    assert add is not None, "dictionary add button is missing"
    assert add.get_mapped(), "dictionary add button is not mapped"
    assert add.get_valign() == Gtk.Align.CENTER, (
        "dictionary add button must be centered, not stretched to the description height")
    assert add.get_height() <= 60, (
        f"dictionary add button is {add.get_height()}px high; it should stay compact")

    if window.get_width() <= 480:
        bars = [widget for widget in walk(prefs.dialog)
                if isinstance(widget, Adw.ViewSwitcherBar)]
        assert bars and bars[0].get_mapped(), "expected the native bottom view switcher"
        bar_labels = [widget for widget in walk(bars[0])
                      if isinstance(widget, Gtk.Label) and widget.get_mapped()
                      and widget.get_text() == title]
        assert bar_labels, "dictionary label is missing from the bottom view switcher"
        for label in bar_labels:
            assert not label.get_layout().is_ellipsized(), (
                f"dictionary label {title!r} is truncated in the bottom view switcher")
    return prefs.dialog, ()


def editor_scenario(window, language):
    dialog = dialogs.personal_word_dialog(window, None, lambda value: True)
    wait_until(dialog.get_mapped, "word editor dialog")
    settle()
    header = next(widget for widget in walk(dialog) if isinstance(widget, Adw.HeaderBar))
    cancel_label = "Отмена" if language == "ru" else "Cancel"
    save_label = "Сохранить" if language == "ru" else "Save"
    cancel = next(button for button in walk(header)
                  if isinstance(button, Gtk.Button) and button.get_label() == cancel_label)
    save = next(button for button in walk(header)
                if isinstance(button, Gtk.Button) and button.get_label() == save_label)
    ok, cancel_rect = cancel.compute_bounds(header)
    ok_save, save_rect = save.compute_bounds(header)
    assert ok and ok_save
    assert save_rect.get_x() > cancel_rect.get_x(), (
        "editor Cancel must be at the start and Save at the end of the header")
    saves = [button for button in walk(dialog)
             if isinstance(button, Gtk.Button) and button.get_label() == save_label]
    assert saves == [save], "the editor primary action must exist only in the header"
    entry = next(widget for widget in walk(dialog) if isinstance(widget, Adw.EntryRow))
    assert any(isinstance(parent, Gtk.ScrolledWindow) for parent in ancestors(entry)), (
        "the editor body must be scrollable")
    return dialog, ()


def companion_scenario(window, _language):
    token = "P" + "z" * 140
    package = companion.Package(
        "CachyOS", "pacman", candidate="1.2.1-1", installed="",
        command="/usr/bin/" + token, automatic=True,
        message="Package details with a long token: " + token)
    original = companion_dialog.run_in_thread
    companion_dialog.run_in_thread = lambda work, done: done(package)
    try:
        dialog = companion_dialog.show(window)
        wait_until(dialog.get_mapped, "companion dialog")
    finally:
        companion_dialog.run_in_thread = original
    settle()
    return dialog, (token,)


def main() -> None:
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise SystemExit("GTK geometry smoke needs a display; use xvfb-run")
    scratch = Path("build")
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gui-geometry-", dir=scratch) as profile:
        os.environ["XDG_CONFIG_HOME"] = str(Path(profile).resolve())
        Adw.init()
        style.install()
        # Never inspect the host service or tray from this isolated smoke test.
        autostart.is_service_enabled = lambda: False
        autostart.is_tray_enabled = lambda: False
        app = Adw.Application(application_id="io.github.Wetoshkin.WaySwitch.GeometryTest")
        app.register(None)

        scenarios = (
            (update_scenario, [
                ("updates-360-ru", 360, 600, "ru", "Cantarell 11", False),
                ("updates-360-ru-large-dark", 360, 600, "ru", "Cantarell 16", True),
                ("updates-360-en-large-dark", 360, 600, "en", "Cantarell 15", True),
                ("updates-600-ru", 600, 700, "ru", "Cantarell 11", False),
                ("updates-600-en-dark", 600, 700, "en", "Cantarell 11", True),
                ("updates-900-ru-large", 900, 700, "ru", "Cantarell 15", False),
                ("updates-900-en", 900, 700, "en", "Cantarell 11", False),
            ]),
            (preferences_scenario, [
                ("dictionary-360-ru", 360, 600, "ru", "Cantarell 11", False),
                ("dictionary-360-en", 360, 600, "en", "Cantarell 11", False),
                ("dictionary-360-ru-large-dark", 360, 600, "ru", "Cantarell 15", True),
                ("dictionary-600-ru", 600, 700, "ru", "Cantarell 11", False),
            ]),
            (editor_scenario, [
                ("editor-360-ru-large-dark", 360, 600, "ru", "Cantarell 15", True),
                ("editor-360-en", 360, 600, "en", "Cantarell 11", False),
            ]),
            (companion_scenario, [
                ("companion-360-ru", 360, 600, "ru", "Cantarell 11", False),
                ("companion-600-en-large-dark", 600, 700, "en", "Cantarell 15", True),
            ]),
        )
        scenario_count = 0
        for scenario, cases in scenarios:
            for name, width, height, language, font, dark in cases:
                use_locale(language)
                use_appearance(font, dark)
                window = make_window(app, width, height)
                dialog = None
                try:
                    dialog, tokens = scenario(window, language)
                    assert_dialog_fits(name, window, dialog)
                    assert_no_horizontal_clipping(name, window, dialog)
                    for token in tokens:
                        assert_long_text_wraps(name, window, dialog, token)
                    print(json.dumps({"scenario": name,
                                      "window": [window.get_width(), window.get_height()],
                                      "dialog": [dialog.get_width(), dialog.get_height()]},
                                     ensure_ascii=False), flush=True)
                    scenario_count += 1
                finally:
                    if dialog is not None:
                        dialog.close()
                    settle(0.1)
                    window.destroy()
                    settle(0.1)

    print(f"GUI geometry smoke passed ({scenario_count} scenarios)")


if __name__ == "__main__":
    main()
