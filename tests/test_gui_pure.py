"""Тесты чистых функций GUI: не требуют gi/GTK, годятся для CI без дисплея."""

from wayswitch.gui.autostart import tray_desktop_text
from wayswitch.gui.tray import menu_layout


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
