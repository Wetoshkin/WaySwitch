import gc
import sys
import weakref
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wayswitch.session import SessionGuard


def test_prepare_for_sleep_signal_after_start(monkeypatch, tmp_path):
    subscriptions = []

    class Manager:
        def connect(self, _signal, callback):
            self.signal = callback
            subscriptions.append(weakref.ref(self))

    session = Mock()
    session.get_cached_property.side_effect = lambda name: SimpleNamespace(
        unpack=lambda: {"Active": True, "LockedHint": False, "Id": "25"}[name])
    gio = Mock()
    gio.DBusProxy.new_sync.side_effect = lambda *args: (
        Manager() if args[-2].endswith(".Manager") else session)
    monkeypatch.setitem(sys.modules, "gi.repository", SimpleNamespace(Gio=gio, GLib=Mock()))
    monkeypatch.setattr("wayswitch.session.SCREENSAVERS", ())
    monkeypatch.setattr("wayswitch.session.process_names", lambda: ["cosmic-greeter"])
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    asleep = []
    guard = SessionGuard(lambda _: None, asleep.append, cosmic=True)
    monkeypatch.setattr(guard, "_find_session", lambda *a, **kw: "/session")
    guard.start()
    assert not guard.input_locked()
    gc.collect()
    manager = subscriptions[0]()
    assert manager is not None
    manager.signal(manager, None, "PrepareForSleep", SimpleNamespace(unpack=lambda: (True,)))
    assert guard.input_locked() and asleep == [True]


def test_lockfile_uses_graphical_session_and_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_SESSION_ID", "999")  # SSH
    seen = []
    guard = SessionGuard(seen.append, lambda _: None, cosmic=True)
    assert guard.input_locked()
    guard._set_cosmic_session("25")
    guard._greeter_ready = True
    (tmp_path / "cosmic-greeter-999.lock").touch()
    assert not guard.input_locked()
    marker = tmp_path / "cosmic-greeter-25.lock"
    marker.touch()
    assert guard.input_locked()
    marker.unlink()
    assert not guard.input_locked()
    assert seen == [True, False, True, False]
    monkeypatch.delenv("XDG_SESSION_ID")
    assert not guard.input_locked()


@pytest.mark.parametrize("reason", [PermissionError, OSError])
def test_unreadable_cosmic_marker_blocks_input(tmp_path, monkeypatch, reason):
    guard = SessionGuard(lambda _: None, lambda _: None, cosmic=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    guard._set_cosmic_session("25")
    guard._greeter_ready = True

    def fail(*_args):
        raise reason("unavailable")
    monkeypatch.setattr(type(tmp_path), "lstat", fail)
    assert guard.input_locked()


def test_lock_signal_without_marker_stays_locked_until_resolved(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    guard = SessionGuard(lambda _: None, lambda _: None, cosmic=True)
    guard._set_cosmic_session("25")
    guard._greeter_ready = True
    assert not guard.input_locked()
    guard._on_cosmic_signal(None, None, "Lock", None)
    assert guard.input_locked()
    assert guard.input_locked()
    # Голый Unlock — просьба greeter снять экран, а не подтверждение композитора.
    guard._on_cosmic_signal(None, None, "Unlock", None)
    assert guard.input_locked()
    marker = tmp_path / "cosmic-greeter-25.lock"
    marker.touch()
    assert guard.input_locked()
    marker.unlink()
    assert not guard.input_locked()  # Controller всё ещё ждёт явного Resume


def test_cold_unwritable_runtime_remembers_possible_unmarked_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    guard = SessionGuard(lambda _: None, lambda _: None, cosmic=True)
    guard._set_cosmic_session("25")
    guard._greeter_ready = True
    with monkeypatch.context() as patch:
        patch.setattr("wayswitch.session.os.access", lambda *_: False)
        assert guard.input_locked()
    assert guard.input_locked()
