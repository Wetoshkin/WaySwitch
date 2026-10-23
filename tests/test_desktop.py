"""Определение среды и протокола сессии."""

from wayswitch.backends.desktop import (
    GNOME,
    KDE,
    OTHER,
    detect_desktop,
    is_wayland,
    session_summary,
)


def test_detects_gnome():
    assert detect_desktop({"XDG_CURRENT_DESKTOP": "GNOME"}) == GNOME
    # Ubuntu присылает составную строку
    assert detect_desktop({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}) == GNOME
    assert detect_desktop({"XDG_CURRENT_DESKTOP": "GNOME-Classic:GNOME"}) == GNOME


def test_detects_kde():
    assert detect_desktop({"XDG_CURRENT_DESKTOP": "KDE"}) == KDE
    assert detect_desktop({"XDG_CURRENT_DESKTOP": "plasma"}) == KDE


def test_other_desktop():
    for raw in ("XFCE", "MATE", "sway", "Hyprland", ""):
        assert detect_desktop({"XDG_CURRENT_DESKTOP": raw}) == OTHER


def test_missing_env_is_other():
    assert detect_desktop({}) == OTHER


def test_wayland_by_session_type():
    assert is_wayland({"XDG_SESSION_TYPE": "wayland"}) is True
    assert is_wayland({"XDG_SESSION_TYPE": "x11"}) is False


def test_wayland_by_display_when_type_empty():
    # XDG_SESSION_TYPE иногда пустая (запуск из другого юнита) — смотрим на дисплей
    assert is_wayland({"WAYLAND_DISPLAY": "wayland-0"}) is True
    assert is_wayland({}) is False


def test_session_summary_mentions_desktop_and_protocol():
    text = session_summary({"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"})
    assert "KDE Plasma" in text and "Wayland" in text
