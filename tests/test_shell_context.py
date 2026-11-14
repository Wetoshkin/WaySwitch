"""Owner changes and stale replies must never authorize a cached focus."""

import sys
from types import SimpleNamespace

import pytest

from wayswitch.shell_context import ShellContext, ShellWatch


class RemoteError(Exception):
    pass


@pytest.fixture
def shell(monkeypatch):
    class Bus:
        def __init__(self):
            self.owner = ":1.10"
            self.context = (["editor"], False, True, 0, 0, 123)
            self.calls = []
            self.subscriptions = {}
            self.handlers = {}
            self.hook = None

        def signal_subscribe(self, sender, iface, member, path, arg, flags, callback, data):
            token = len(self.subscriptions) + 1
            self.subscriptions[token] = (member, callback)
            return token

        def signal_unsubscribe(self, token):
            del self.subscriptions[token]

        def connect(self, signal, callback):
            self.handlers[1] = callback
            return 1

        def disconnect(self, token):
            del self.handlers[token]

        def call_sync(self, dest, path, iface, method, args, *rest):
            self.calls.append(method)
            if self.hook:
                result = self.hook(method)
                if result is not None:
                    return SimpleNamespace(unpack=lambda: result)
            if method == "GetNameOwner":
                if not self.owner:
                    raise RemoteError("org.freedesktop.DBus.Error.NameHasNoOwner")
                value = (self.owner,)
            elif method == "GetCapabilities":
                value = (["focused_context_payload_v2"],)
            elif method == "GetFocusedContext":
                value = self.context
            elif method == "GetFocusedApp":
                value = (["editor"],)
            else:
                raise AssertionError(method)
            return SimpleNamespace(unpack=lambda: value)

        def emit(self, member, values=(), sender=":1.10"):
            for name, callback in list(self.subscriptions.values()):
                if name == member:
                    callback(self, sender, "/", "iface", member,
                             SimpleNamespace(unpack=lambda: values), None)

    gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0),
                          DBusSignalFlags=SimpleNamespace(NONE=0),
                          DBusError=SimpleNamespace(get_remote_error=lambda e: str(e)))
    glib = SimpleNamespace(Variant=lambda sig, value: value, VariantType=lambda sig: sig)
    monkeypatch.setitem(sys.modules, "gi.repository", SimpleNamespace(Gio=gio, GLib=glib))
    return Bus()


def test_absent_owner_skips_one_hundred_context_queries(shell):
    shell.owner = None
    watch = ShellWatch(shell, lambda: None)
    for _ in range(100):
        assert watch.query() == ShellContext()
    assert shell.calls == ["GetNameOwner"]
    watch.close()
    assert not shell.subscriptions and not shell.handlers


def test_absent_present_absent_and_late_old_owner(shell):
    watch = ShellWatch(shell, lambda: None)
    assert watch.query().app_ids == ("editor",)
    shell.emit("NameOwnerChanged", ("io.github.Wetoshkin.WaySwitch.Shell", ":1.10", ""))
    assert watch.query() == ShellContext()
    shell.emit("NameOwnerChanged", ("io.github.Wetoshkin.WaySwitch.Shell", "", ":1.11"))
    shell.emit("FocusedContextChanged2", shell.context, sender=":1.10")
    assert watch.query().app_ids == ("editor",)
    assert shell.calls.count("GetCapabilities") == 2


def test_initial_lookup_does_not_overwrite_owner_event(shell):
    def lookup(method):
        if method == "GetNameOwner":
            shell.emit("NameOwnerChanged", ("io.github.Wetoshkin.WaySwitch.Shell", "", ""))
            return (":1.10",)
    shell.hook = lookup
    watch = ShellWatch(shell, lambda: None)
    assert watch.query() == ShellContext()


def test_payload_saves_initial_read_but_never_fresh_check(shell):
    now = [10.0]
    watch = ShellWatch(shell, lambda: None, clock=lambda: now[0])
    assert watch.query() is not None  # capability discovery
    shell.calls.clear()
    shell.emit("FocusedContextChanged2", shell.context)
    assert watch.query().pid == 123
    assert shell.calls == []
    assert watch.query(fresh=True).pid == 123
    assert shell.calls == ["GetFocusedContext"]
    now[0] += 3.01
    assert watch.query().pid == 123
    assert shell.calls == ["GetFocusedContext"] * 2


@pytest.mark.parametrize("payload", [
    (["a"] * 17, False, True, 0, 0, 0),
    (["a" * 257], False, True, 0, 0, 0),
    (["editor"], False, True, -1, 0, 0),
    (["editor"], False, True, True, 0, 0),
    (["editor"], False, False, 0, 0, 0),
    (["editor"], "false", True, 0, 0, 0),
])
def test_bad_payload_invalidates_previous_safe_value(shell, payload):
    watch = ShellWatch(shell, lambda: None)
    watch.query()
    shell.emit("FocusedContextChanged2", shell.context)
    shell.emit("FocusedContextChanged2", payload)
    shell.calls.clear()
    assert watch.query().pid == 123
    assert shell.calls == ["GetFocusedContext"]


def test_legacy_invalidation_followed_by_payload_and_protected_context(shell):
    invalidations = []
    watch = ShellWatch(shell, lambda: invalidations.append(True))
    watch.query()
    shell.calls.clear()
    shell.emit("FocusedContextChanged")
    shell.emit("FocusedContextChanged2", (["editor"], False, True, 8, 128, 0))
    assert watch.query().protected()
    assert shell.calls == []
    assert invalidations


def test_old_reply_cannot_hide_new_payload_or_a_b_a_change(shell):
    watch = ShellWatch(shell, lambda: None)
    watch.query()
    generation = watch.generation

    def delayed(method):
        if method == "GetFocusedContext":
            shell.emit("FocusedContextChanged")
            shell.emit("FocusedContextChanged2", (["other"], True, True, 0, 0, 2))
            return shell.context
    shell.hook = delayed
    assert watch.query(fresh=True) is None
    assert watch.generation != generation
    assert watch.query().protected()


def test_transport_error_is_unknown_and_close_rejects_late_callbacks(shell):
    def timeout(method):
        raise RemoteError("org.freedesktop.DBus.Error.NoReply")
    shell.hook = timeout
    watch = ShellWatch(shell, lambda: None)
    assert watch.query() is None
    callbacks = list(shell.subscriptions.values())
    watch.close()
    for member, cb in callbacks:
        cb(None, ":1.10", "", "", member,
           SimpleNamespace(unpack=lambda: shell.context), None)
    assert watch.query() is None
    assert not shell.subscriptions


def test_legacy_method_discovery_resets_after_owner_change(shell):
    def legacy(method):
        if method in {"GetFocusedContext", "GetCapabilities"}:
            raise RemoteError("org.freedesktop.DBus.Error.UnknownMethod")
    shell.hook = legacy
    watch = ShellWatch(shell, lambda: None)
    assert watch.query().app_ids == ("editor",)
    assert watch.query().app_ids == ("editor",)
    assert shell.calls.count("GetFocusedContext") == 1
    shell.hook = None
    shell.emit("NameOwnerChanged", ("io.github.Wetoshkin.WaySwitch.Shell", ":1.10", ":1.11"))
    assert watch.query().pid == 123
    assert shell.calls.count("GetFocusedContext") == 2


def test_unreadable_payload_still_invalidates_previous_focus(shell):
    invalidations = []
    watch = ShellWatch(shell, lambda: invalidations.append(True))
    watch.query()
    shell.emit("FocusedContextChanged2", shell.context)
    invalidations.clear()
    def broken():
        raise ValueError("malformed variant")
    for member, callback in shell.subscriptions.values():
        if member == "FocusedContextChanged2":
            callback(shell, ":1.10", "/", "iface", member,
                     SimpleNamespace(unpack=broken), None)
    assert invalidations == [True]
    shell.calls.clear()
    assert watch.query().pid == 123
    assert shell.calls == ["GetFocusedContext"]


def test_initial_lookup_timeout_requires_owner_recovery_before_safe_context(shell):
    def timeout(method):
        if method == "GetNameOwner":
            raise RemoteError("org.freedesktop.DBus.Error.NoReply")
    shell.hook = timeout
    watch = ShellWatch(shell, lambda: None)
    assert watch.query() is None
    shell.hook = None
    assert watch.query().pid == 123
    generation = watch.generation
    shell.emit("FocusedContextChanged")
    assert watch.generation > generation


def test_extension_without_either_context_method_keeps_atspi_fallback(shell):
    def unsupported(method):
        if method != "GetNameOwner":
            raise RemoteError("org.freedesktop.DBus.Error.UnknownMethod")
    shell.hook = unsupported
    watch = ShellWatch(shell, lambda: None)
    assert watch.query() == ShellContext()
    assert watch.query() == ShellContext()
