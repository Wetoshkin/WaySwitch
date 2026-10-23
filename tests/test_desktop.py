"""Определение среды и протокола сессии."""

from wayswitch.backends import desktop
from wayswitch.backends.desktop import (
    GNOME,
    KDE,
    OTHER,
    _session_env,
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


# --- окружение сессии, когда мы запущены не из неё --------------------------

class _FakeRun:
    """Подмена subprocess.run: systemctl --user show-environment."""

    def __init__(self, text: str = "", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **_kwargs):
        self.calls.append(list(cmd))
        if self.error is not None:
            raise self.error

        class Result:
            stdout = self.text

        return Result()


def test_session_env_asks_user_manager_when_variable_empty(monkeypatch):
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    run = _FakeRun("XDG_CURRENT_DESKTOP=KDE\nXDG_SESSION_TYPE=wayland\n")
    env = _session_env(run)
    assert env["XDG_CURRENT_DESKTOP"] == "KDE"
    assert env["XDG_SESSION_TYPE"] == "wayland"
    assert run.calls and run.calls[0][:3] == ["systemctl", "--user", "show-environment"]


def test_session_env_does_not_ask_when_variable_present(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    run = _FakeRun("XDG_CURRENT_DESKTOP=KDE\n")
    assert _session_env(run)["XDG_CURRENT_DESKTOP"] == "GNOME"
    assert run.calls == []


def test_session_env_survives_missing_systemctl(monkeypatch):
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    env = _session_env(_FakeRun(error=FileNotFoundError("нет systemctl")))
    assert "XDG_CURRENT_DESKTOP" not in env


def test_detect_desktop_from_bare_shell(monkeypatch):
    """doctor из ssh или cron: своих переменных нет, а среда есть у менеджера."""
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.setattr(desktop.subprocess, "run",
                        _FakeRun("XDG_CURRENT_DESKTOP=ubuntu:GNOME\nWAYLAND_DISPLAY=wayland-0\n"))
    assert detect_desktop() == GNOME
    assert is_wayland() is True
