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


def test_gui_app_id_does_not_clash_with_daemon():
    """Общее D-Bus-имя с демоном не даёт окну настроек открыться."""
    from wayswitch.dbus_service import BUS_NAME
    from wayswitch.gui import app as gui_app

    assert gui_app.APP_ID != BUS_NAME


def test_window_size_grows_with_content():
    """Окно подстраивается под самую высокую страницу, а не 560x640 всегда."""
    from wayswitch.gui.app import window_size_for

    width, height = window_size_for([(560, 640), (620, 900)])
    assert width > 620 and height > 900


def test_window_size_clamped_by_screen():
    from wayswitch.gui.app import window_size_for

    width, height = window_size_for([(1600, 4000)], screen=(1920, 1080))
    assert width <= int(1920 * 0.9) and height <= int(1080 * 0.85)


def test_window_size_has_minimum():
    from wayswitch.gui.app import window_size_for

    # Без страниц — прежний размер с полями, но не меньше минимума.
    width, height = window_size_for([])
    assert width >= 560 and height >= 480


def test_repo_url_points_to_github():
    from wayswitch.gui.app import REPO_URL

    assert REPO_URL.startswith("https://github.com/Wetoshkin/WaySwitch")


def test_gesture_picker_only_allowed_keys():
    """Каждая кнопка экранной клавиатуры — из белого списка жестовых клавиш."""
    from wayswitch.gui.gesture_keys import all_key_names, is_allowed

    names = all_key_names()
    assert names, "клавиатура пустая"
    assert all(is_allowed(name) for name in names), [n for n in names if not is_allowed(n)]


def test_gesture_picker_has_no_typing_keys():
    """Букв и цифр в выборе жеста быть не должно: жест срабатывал бы на каждой букве."""
    from wayswitch.gui.gesture_keys import all_key_names

    for name in all_key_names():
        assert len(name) > 1, name


def test_gesture_button_label_shows_side():
    from wayswitch.gui.gesture_keys import button_label

    assert button_label("leftctrl") == "Ctrl (левый)"
    assert button_label("f7") == "f7"


def test_config_rejects_letter_as_tap_key(tmp_path):
    """Валидация: буква не может быть клавишей жеста."""
    from wayswitch import config as cfgmod

    path = tmp_path / "c.toml"
    path.write_text('[gesture]\nmanual = "tap_key"\ntap_key = "a"\n', encoding="utf-8")
    try:
        cfgmod.load(path)
    except cfgmod.ConfigError:
        return
    raise AssertionError("буква в tap_key должна отвергаться")
