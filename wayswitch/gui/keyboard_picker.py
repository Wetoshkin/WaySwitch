"""Экранная клавиатура для выбора клавиши жеста: клик вместо ввода имени."""

from __future__ import annotations

from collections.abc import Callable

from wayswitch.gui.gesture_keys import ROWS, button_label


def build_picker_window(parent, current: str, on_pick: Callable[[str], None]):
    """Окно с клавишами. Возвращает окно (модальное, поверх настроек).

    Импорт gi внутри функции: модуль остаётся импортируемым без GTK — так его
    данные (ROWS) проверяются тестами без графической сессии.
    """
    import gi

    gi.require_version("Adw", "1")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Adw, Gtk

    window = Adw.Window(transient_for=parent, modal=True, title="Клавиша жеста")
    window.set_default_size(560, 320)

    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    header.set_show_end_title_buttons(False)
    cancel = Gtk.Button(label="Отмена")
    cancel.connect("clicked", lambda *_: window.close())
    header.pack_end(cancel)
    toolbar.add_top_bar(header)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                  margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
    hint = Gtk.Label(
        label="Нажмите клавишу, которой будете переворачивать последнее слово.\n"
              "Буквы и цифры не подходят: их одиночное нажатие — это просто буква.",
        xalign=0.0, wrap=True)
    hint.add_css_class("dim-label")
    box.append(hint)

    grid = Gtk.Grid(row_spacing=6, column_spacing=6)
    for row_index, row in enumerate(ROWS):
        for col_index, (_label, name) in enumerate(row):
            button = Gtk.ToggleButton(label=button_label(name))
            button.set_hexpand(True)
            if name == current:
                button.set_active(True)
                button.add_css_class("suggested-action")

            def on_clicked(_button, key_name=name):
                on_pick(key_name)
                window.close()

            button.connect("clicked", on_clicked)
            grid.attach(button, col_index, row_index, 1, 1)
    box.append(grid)

    toolbar.set_content(box)
    window.set_content(toolbar)
    return window
