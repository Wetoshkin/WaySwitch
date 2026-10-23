"""Тесты чистых частей GUI: не требуют gi/GTK, годятся для CI без дисплея."""

import ast
from pathlib import Path

from wayswitch.doctor import Check
from wayswitch.gui import gesture_keys, texts
from wayswitch.gui.app import parse_gui_args, update_text
from wayswitch.gui.autostart import tray_desktop_text
from wayswitch.gui.tray import menu_layout
from wayswitch.keycodes import KEY_A, KEY_BACKSPACE, KEY_ESC, KEY_F7, KEY_LEFTCTRL, KEY_RIGHTALT

GUI = Path(__file__).resolve().parents[1] / "wayswitch" / "gui"


def test_desktop_entry():
    text = tray_desktop_text()
    assert "[Desktop Entry]" in text and "Exec=wayswitch-gui --tray" in text
    assert "X-GNOME-Autostart-enabled=true" in text


def test_tray_autostart_migrates_legacy_name(tmp_path, monkeypatch):
    """Старый файл автозапуска удаляется, иначе после обновления два значка."""
    from wayswitch.gui import autostart

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy = tmp_path / "autostart" / "ru.siberia.WaySwitch.desktop"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("x", encoding="utf-8")
    assert autostart.is_tray_enabled()
    autostart.set_tray_enabled(True)
    assert not legacy.exists()
    assert (tmp_path / "autostart" / autostart.DESKTOP_NAME).exists()


def test_menu_layout_reflects_status():
    items = {i.ident: i for i in menu_layout(None)}
    assert items["settings"].enabled and not items["pause"].enabled
    assert "Не запущен" in items["summary"].label
    items = {i.ident: i for i in menu_layout({"paused": True, "auto_correct": True})}
    assert items["pause"].label == "Возобновить"
    assert items["auto"].toggle is True


def test_menu_layout_shows_update():
    idents = [i.ident for i in menu_layout({"auto_correct": True}, update="2.1.0")]
    assert "update" in idents
    assert "update" not in [i.ident for i in menu_layout({"auto_correct": True})]


def test_parse_gui_args():
    assert parse_gui_args(["gui"]) is False
    assert parse_gui_args(["--tray"]) is True
    assert parse_gui_args(["gui", "--tray"]) is True


def test_gui_app_id_does_not_clash_with_daemon():
    """Общее D-Bus-имя с демоном не даёт окну настроек открыться."""
    from wayswitch.dbus_service import BUS_NAME
    from wayswitch.gui import app as gui_app

    assert gui_app.APP_ID != BUS_NAME


def test_repo_url_points_to_github():
    from wayswitch.gui.app import REPO_URL

    assert REPO_URL.startswith("https://github.com/Wetoshkin/WaySwitch")


def test_update_text():
    from wayswitch.updates import Release

    assert "2.1.0" in update_text(Release("2.1.0", "u"), checked=True)
    assert "последняя" in update_text(None, checked=True)


# --- тексты -----------------------------------------------------------------------

def test_plural():
    assert [texts.plural(n, "слово", "слова", "слов") for n in (1, 2, 5, 11, 21, 22, 112)] \
        == ["слово", "слова", "слов", "слов", "слово", "слова", "слов"]


def test_format_units():
    assert texts.format_seconds(5.0) == "5 с"
    assert texts.format_seconds(0.5) == "0,5 с"
    assert texts.format_seconds(0) == "нет"
    assert texts.format_ms(30) == "30 мс"


def test_status_view_human_words():
    view = texts.status_view({"active": True, "auto_correct": True, "backend": "shell",
                              "layout": "ru"})
    assert view.kind == "on" and view.title == "Исправление включено"
    assert "русская" in view.subtitle and "GNOME Shell" in view.subtitle
    # Ни «демон», ни «бэкенд», ни «индекс» человеку не показываем.
    for status in (None, {"paused": True}, {"locked": True}, {"active": False},
                   {"active": True, "auto_correct": False}):
        v = texts.status_view(status)
        for word in ("демон", "бэкенд", "индекс"):
            assert word not in (v.title + v.subtitle).lower()


def test_status_view_states():
    assert texts.status_view(None).kind == "off"
    assert texts.status_view({"paused": True}).kind == "paused"
    assert texts.status_view({"paused": True}).switch_active is False
    assert texts.status_view({"locked": True}).kind == "locked"
    assert texts.status_view({"active": False, "auto_correct": True}).kind == "manual"


def test_tray_summary():
    assert texts.tray_summary(None) == "Не запущен"
    text = texts.tray_summary({"corrections_total": 40, "manual_total": 8})
    assert "48 слов" in text


def test_history_keeps_newest_first_and_limit():
    h = texts.History(limit=3)
    for i in range(5):
        h.add(f"w{i}", f"f{i}", manual=False, at=1000.0 + i)
    assert [e.original for e in h] == ["w4", "w3", "w2"]
    h.mark_excluded("W3")
    assert [e.kind for e in h] == ["auto", "excluded", "auto"]
    h.clear()
    assert len(h) == 0


def test_onboarding_rows_map_doctor_checks():
    checks = [Check("Сессия Wayland", True, "GNOME", key="wayland"),
              Check("Две раскладки", True, "us, ru", key="layouts"),
              Check("Клавиатуры читаются", False, "нет доступа", key="keyboards"),
              Check("Расширение", False, "", required=False, key="extension"),
              Check("Что-то техническое", False, "", key="")]
    rows = texts.onboarding_rows(checks)
    assert [r.key for r in rows] == ["wayland", "layouts", "keyboards", "extension"]
    assert rows[1].subtitle == "English (US), Русская"
    assert rows[2].state == "fail" and rows[2].action == "logout"
    assert rows[3].state == "optional"
    assert texts.onboarding_blocking(rows)
    assert not texts.onboarding_blocking([r for r in rows if r.state != "fail"])


# --- клавиша жеста ------------------------------------------------------------------

def test_gesture_human_names():
    assert gesture_keys.human_name("leftctrl") == "Левый Ctrl"
    assert gesture_keys.human_name("rightalt") == "Правый Alt"
    assert gesture_keys.human_name("f7") == "F7"


def test_gesture_capture():
    assert gesture_keys.capture(KEY_ESC) == ("cancel", "")
    assert gesture_keys.capture(KEY_BACKSPACE) == ("clear", "")
    assert gesture_keys.capture(KEY_LEFTCTRL) == ("key", "leftctrl")
    assert gesture_keys.capture(KEY_RIGHTALT) == ("key", "rightalt")
    assert gesture_keys.capture(KEY_F7) == ("key", "f7")
    # Буква не годится: её нажатие — это набранная буква.
    assert gesture_keys.capture(KEY_A)[0] == "reject"


def test_gesture_rows_follow_config():
    from wayswitch import config as cfgmod
    from wayswitch.gui.dialogs import gesture_rows

    cfg = cfgmod.Config()
    assert gesture_rows(cfg)[0][2] == ["Shift", "Shift"]
    cfg.gesture.manual = "tap_key"
    cfg.gesture.tap_key = "rightalt"
    assert gesture_rows(cfg)[0][2] == ["Правый Alt"]
    cfg.gesture.manual = "none"
    assert gesture_rows(cfg) == []


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


# --- стиль и чистота ----------------------------------------------------------------

def test_css_has_accent_for_both_libadwaita_generations():
    from wayswitch.gui.style import css_for

    modern = css_for(dark=True, modern=True)
    legacy = css_for(dark=False, modern=False)
    assert "--accent-bg-color: #c46a32" in modern
    assert "@define-color accent_bg_color #b8622c" in legacy
    assert "ws_success" in modern and ".ws-keycap" in legacy


def test_gui_modules_import_gi_lazily():
    """Модули gui импортируются без GTK: gi только внутри функций."""
    for path in GUI.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                module = getattr(node, "module", "") or ""
                assert not module.startswith("gi") and "gi" not in names, path.name
