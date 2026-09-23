"""Тесты чистых функций GUI: не требуют gi/GTK, годятся для CI без дисплея."""

from wayswitch.gui.app import layout_label, parse_gui_args
from wayswitch.gui.autostart import tray_desktop_text
from wayswitch.gui.tray import menu_layout


def test_layout_label_unknown_index():
    assert layout_label(-1) == "неизвестно"
    assert layout_label(None) == "неизвестно"
    assert layout_label(1) == "индекс 1"


def test_desktop_entry():
    text = tray_desktop_text()
    assert "[Desktop Entry]" in text and "Exec=wayswitch-gui --tray" in text
    assert "X-GNOME-Autostart-enabled=true" in text


def test_menu_layout_reflects_status():
    items = dict((i, (label, enabled)) for i, label, enabled in menu_layout(None))
    assert items["settings"][1] and not items["pause"][1]
    items = dict((i, (label, enabled)) for i, label, enabled in
                 menu_layout({"paused": True, "auto_correct": True, "active": False}))
    assert "Возобновить" in items["pause"][0]
    assert "✓" in items["auto"][0]


def test_parse_gui_args():
    assert parse_gui_args(["gui"]) is False
    assert parse_gui_args(["--tray"]) is True
    assert parse_gui_args(["gui", "--tray"]) is True
