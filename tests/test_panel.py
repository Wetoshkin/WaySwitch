"""Модуль Waybar и короткая сводка состояния."""

from wayswitch.panel import Flash, status_line, waybar_payload

ON = {"active": True, "paused": False, "layout": "ru", "backend": "sway",
      "corrections_total": 40, "manual_total": 8}


def test_waybar_states():
    assert waybar_payload(None)["class"] == "off"
    assert waybar_payload(ON) == {**waybar_payload(ON), "text": "RU", "class": "on"}
    assert waybar_payload({**ON, "paused": True})["class"] == "paused"
    fixed = waybar_payload(ON, Flash("fixed", "ghbdtn", "привет"))
    assert fixed["class"] == "fixed" and fixed["text"] == "ghbdtn → привет"
    undone = waybar_payload(ON, Flash("undone", "кем бы", "кембы"))
    assert undone["class"] == "undone" and undone["text"] == "кембы — запомнено"


def test_waybar_tooltip_human():
    tip = waybar_payload(ON)["tooltip"]
    assert "русская" in tip and "Sway IPC" in tip and "40 автоматически" in tip


def test_status_line():
    assert status_line(None) == "WaySwitch не запущен"
    assert "48 исправлений" in status_line(ON)
