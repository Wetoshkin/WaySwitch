import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_controller import env as controller_env
from tests.test_controller import type_text
from wayswitch.session import SessionGuard


@pytest.fixture
def native_scope(monkeypatch):
    class Session:
        owner = ":1.7"
        properties = {"Active": True, "LockedHint": False}
        error = None
        owner_error = None
        after_reply = None

        def __init__(self):
            self.calls = []
            self.callbacks = {}

        def get_name_owner(self):
            if self.owner_error is not None:
                raise self.owner_error
            return self.owner

        def connect(self, signal, callback):
            self.callbacks[signal] = callback

        def get_cached_property(self, name):
            return SimpleNamespace(unpack=lambda: {"Active": True, "LockedHint": False}[name])

        def call_sync(self, method, params, flags, timeout, cancellable):
            self.calls.append((method, params.unpack(), flags, timeout, cancellable))
            if self.error is not None:
                raise self.error
            if self.after_reply is not None:
                self.after_reply()
            return SimpleNamespace(unpack=lambda: (self.properties,))

    session = Session()
    gio = Mock()
    gio.DBusProxy.new_sync.side_effect = lambda *args: (
        Mock() if args[-2].endswith(".Manager") else session)
    glib = SimpleNamespace(
        Variant=lambda signature, values: SimpleNamespace(unpack=lambda: values),
        timeout_add=Mock())
    monkeypatch.setitem(sys.modules, "gi.repository", SimpleNamespace(Gio=gio, GLib=glib))
    monkeypatch.setattr("wayswitch.session.SCREENSAVERS", ())
    monkeypatch.setattr("wayswitch.session.process_names", lambda: [])

    def ready(guard):
        guard._session = session
        guard._native_session_owner = session.owner
        return guard

    def start(guard):
        monkeypatch.setattr(guard, "_find_session", lambda *a, **kw: "/session")
        guard.start()
        return guard

    return SimpleNamespace(session=session, ready=ready, start=start)


def test_unstarted_native_guard_does_not_authorize_input():
    guard = SessionGuard(lambda _: None, lambda _: None, input_guard=lambda: False)
    assert guard.input_locked()


def test_failed_graphical_session_selection_stays_locked(native_scope, monkeypatch, caplog):
    guard = SessionGuard(lambda _: None, lambda _: None, input_guard=lambda: False)
    monkeypatch.setattr(guard, "_find_session", Mock(
        side_effect=RuntimeError("private logind error")))
    guard.start()
    assert guard.input_locked()
    native_scope.ready(guard)
    assert guard.input_locked()
    assert "private logind error" not in caplog.text


@pytest.mark.parametrize("properties", [
    {}, {"Active": True}, {"LockedHint": False},
    {"Active": 1, "LockedHint": False}, {"Active": True, "LockedHint": 0},
    {"Active": "true", "LockedHint": False}, None,
    {"Active": True, "LockedHint": True},
])
def test_native_session_unknown_properties_are_locked(native_scope, properties):
    native_scope.session.properties = properties
    guard = native_scope.ready(SessionGuard(
        lambda _: None, lambda _: None, input_guard=lambda: False))
    assert guard.input_locked()


def test_native_vt_gate_uses_fresh_rpc_without_cached_property_signal(native_scope):
    env = controller_env.__wrapped__()
    ctrl = env["ctrl"]
    guard = native_scope.ready(SessionGuard(
        ctrl.set_locked, lambda _: None, input_guard=lambda: False))
    ctrl.screen_locked = guard.input_locked
    type_text(env, "ghb")
    assert not ctrl.buffer.is_empty()
    native_scope.session.properties = {"Active": False, "LockedHint": False}
    type_text(env, "syntheticpassword ")
    assert ctrl.locked and ctrl.buffer.is_empty()
    assert not ctrl.manual_word() and not ctrl.manual_phrase()
    assert not ctrl.transform_selection("layout", "synthetic")
    assert not ctrl.resume(confirm_lock=True)
    assert env["corrected"] == [] and ctrl._last_fix is None
    assert native_scope.session.calls
    assert all(call == ("org.freedesktop.DBus.Properties.GetAll",
                        ("org.freedesktop.login1.Session",), 0, 50, None)
               for call in native_scope.session.calls)
    native_scope.session.properties = {"Active": True, "LockedHint": False}
    type_text(env, "hello ")
    assert not ctrl.locked and ctrl.keymap.decode(ctrl.buffer.phrase(), 0) == "hello "


def test_native_session_rpc_error_wipes_buffer_and_recovers_same_owner(native_scope, caplog):
    env = controller_env.__wrapped__()
    ctrl = env["ctrl"]
    guard = native_scope.ready(SessionGuard(
        ctrl.set_locked, lambda _: None, input_guard=lambda: False))
    ctrl.screen_locked = guard.input_locked
    type_text(env, "ghb")
    native_scope.session.error = RuntimeError("private RPC response")
    type_text(env, "synthetic ")
    assert ctrl.locked and ctrl.buffer.is_empty() and env["corrected"] == []
    assert "private RPC response" not in caplog.text
    native_scope.session.error = None
    type_text(env, "hello ")
    assert not ctrl.locked and ctrl.keymap.decode(ctrl.buffer.phrase(), 0) == "hello "


def test_native_logind_owner_loss_latches_and_clears_without_next_key(native_scope):
    env = controller_env.__wrapped__()
    ctrl = env["ctrl"]
    guard = native_scope.start(SessionGuard(
        ctrl.set_locked, lambda _: None, input_guard=lambda: False))
    ctrl.screen_locked = guard.input_locked
    type_text(env, "ghb")
    native_scope.session.owner = None
    native_scope.session.callbacks["notify::g-name-owner"](native_scope.session, None)
    assert ctrl.locked and ctrl.buffer.is_empty()
    native_scope.session.owner = ":1.8"
    native_scope.session.properties = {"Active": True, "LockedHint": False}
    type_text(env, "synthetic ")
    assert ctrl.locked and ctrl.buffer.is_empty() and env["corrected"] == []
    assert not ctrl.manual_word() and not ctrl.resume(confirm_lock=True)


def test_native_owner_change_is_checked_even_before_notify_delivery(native_scope):
    guard = native_scope.ready(SessionGuard(
        lambda _: None, lambda _: None, input_guard=lambda: False))
    assert not guard.input_locked()
    native_scope.session.owner = ":1.8"
    assert guard.input_locked()
    native_scope.session.owner = ":1.7"
    assert guard.input_locked()


def test_native_session_missing_rpc_support_fails_closed(native_scope, monkeypatch):
    guard = native_scope.ready(SessionGuard(
        lambda _: None, lambda _: None, input_guard=lambda: False))
    monkeypatch.setitem(sys.modules, "gi.repository", SimpleNamespace())
    assert guard.input_locked()


def test_native_owner_change_during_rpc_rejects_valid_reply(native_scope):
    guard = native_scope.ready(SessionGuard(
        lambda _: None, lambda _: None, input_guard=lambda: False))
    native_scope.session.after_reply = lambda: setattr(native_scope.session, "owner", ":1.8")
    assert guard.input_locked()
    native_scope.session.after_reply = None
    native_scope.session.owner = ":1.7"
    assert guard.input_locked()


def test_native_owner_notification_error_latches_closed(native_scope, caplog):
    seen = []
    guard = native_scope.start(SessionGuard(seen.append, lambda _: None,
                                            input_guard=lambda: False))
    native_scope.session.owner_error = RuntimeError("private owner response")
    native_scope.session.callbacks["notify::g-name-owner"](native_scope.session, None)
    assert guard.locked and seen == [True]
    native_scope.session.owner_error = None
    assert guard.input_locked()
    assert "private owner response" not in caplog.text


def test_native_guard_refreshes_before_each_input_and_reports_transitions(native_scope):
    states = iter([False, True, True, False])
    seen = []
    guard = native_scope.ready(SessionGuard(
        seen.append, lambda _: None, input_guard=lambda: next(states)))
    assert not guard.input_locked()
    assert guard.input_locked()
    assert guard.input_locked()
    assert not guard.input_locked()
    assert seen == [True, False]


@pytest.mark.parametrize("unknown", [None, 0, "false", RuntimeError("private detail")])
def test_native_guard_unknown_or_error_is_locked_without_logging(unknown, caplog):
    def query():
        if isinstance(unknown, Exception):
            raise unknown
        return unknown

    seen = []
    guard = SessionGuard(seen.append, lambda _: None, input_guard=query)
    assert guard.input_locked() and seen == [True]
    assert "private detail" not in caplog.text


def test_native_unlock_does_not_override_other_session_protection(native_scope):
    guard = native_scope.ready(SessionGuard(
        lambda _: None, lambda _: None, input_guard=lambda: False))
    guard._screensaver["independent"] = True
    guard._update()
    assert guard.input_locked()


def test_native_guard_start_uses_display_session_and_applies_cold_lock(monkeypatch):
    session = Mock()
    session.get_name_owner.return_value = ":1.7"
    session.call_sync.return_value = SimpleNamespace(
        unpack=lambda: ({"Active": True, "LockedHint": False},))
    session.get_cached_property.side_effect = lambda name: SimpleNamespace(
        unpack=lambda: {"Active": True, "LockedHint": False}[name])
    gio = Mock()
    gio.DBusProxy.new_sync.side_effect = lambda *args: (
        Mock() if args[-2].endswith(".Manager") else session)
    monkeypatch.setitem(sys.modules, "gi.repository", SimpleNamespace(Gio=gio, GLib=Mock()))
    monkeypatch.setattr("wayswitch.session.SCREENSAVERS", ())
    monkeypatch.setattr("wayswitch.session.process_names", lambda: [])
    seen, display_only = [], []
    guard = SessionGuard(seen.append, lambda _: None, input_guard=lambda: True)
    monkeypatch.setattr(guard, "_find_session", lambda *a, **kw: (
        display_only.append(kw["display_only"]) or "/session"))
    guard.start()
    assert display_only == [True] and guard.locked and seen == [True]


@pytest.mark.parametrize("cold", [False, True])
def test_first_locked_key_is_not_buffered_and_old_word_is_discarded(cold, native_scope):
    env = controller_env.__wrapped__()
    ctrl = env["ctrl"]
    native = [cold]
    guard = native_scope.ready(SessionGuard(
        ctrl.set_locked, lambda _: None, input_guard=lambda: native[0]))
    ctrl.screen_locked = guard.input_locked
    if not cold:
        type_text(env, "ghb")
        assert not ctrl.buffer.is_empty()
        native[0] = True
    type_text(env, "ghbdtn ")
    assert ctrl.locked and ctrl.buffer.is_empty()
    assert ctrl._last_fix is None and env["corrected"] == []
    assert ctrl.stats["corrections"] == ctrl.stats["manual"] == 0
    native[0] = False
    type_text(env, "hello ")
    assert not ctrl.locked
    assert ctrl.keymap.decode(ctrl.buffer.phrase(), 0) == "hello "
    assert env["corrected"] == []
