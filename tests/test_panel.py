"""Модуль Waybar и короткая сводка состояния."""

from wayswitch.panel import Flash, status_line, waybar_payload

ON = {"active": True, "paused": False, "layout": "ru", "backend": "sway",
      "corrections_total": 40, "manual_total": 8}


def test_waybar_states(ru_locale):
    assert waybar_payload(None)["class"] == "off"
    assert waybar_payload(ON) == {**waybar_payload(ON), "text": "RU", "class": "on"}
    assert waybar_payload({**ON, "paused": True})["class"] == "paused"
    fixed = waybar_payload(ON, Flash("fixed", "ghbdtn", "привет"))
    assert fixed["class"] == "fixed" and fixed["text"] == "ghbdtn → привет"
    undone = waybar_payload(ON, Flash("undone", "кем бы", "кембы"))
    assert undone["class"] == "undone" and undone["text"] == "кембы — запомнено"


def test_waybar_tooltip_human(ru_locale):
    tip = waybar_payload(ON)["tooltip"]
    assert "русская" in tip and "Sway IPC" in tip and "40 автоматически" in tip


def test_status_line(ru_locale):
    assert status_line(None) == "WaySwitch не запущен"
    assert "48 исправлений" in status_line(ON)


def test_waybar_and_status_line_are_english_in_c(locale_env):
    locale_env(LC_ALL="C", LANGUAGE="ru")
    tip = waybar_payload(ON)["tooltip"]
    assert "Layout: Russian" in tip and "40 automatic, 8 manual" in tip
    assert waybar_payload(None)["tooltip"] == "WaySwitch is not running"
    assert waybar_payload(ON, Flash("undone", "ghbdtn", "привет"))["text"] == \
        "привет — remembered"
    assert "48 corrections this session" in status_line(ON)
    assert "1 correction this session" in status_line({**ON, "corrections_total": 1,
                                                      "manual_total": 0})


def test_status_line_russian_plural(ru_locale):
    assert "21 исправление за сеанс" in status_line({**ON, "corrections_total": 21,
                                                    "manual_total": 0})
