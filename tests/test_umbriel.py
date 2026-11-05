"""Umbriel uses verified native layout state, never a layout counter."""

import errno
import json
import os
import socket
import sys
import tempfile
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from wayswitch.backends import umbriel
from wayswitch.backends.base import BackendError
from wayswitch.keymap import LayoutSpec

NAMES = ["English (US)", "Russian"]
RULES = {"English (US)": LayoutSpec("us"), "Russian": LayoutSpec("ru")}


def state(index=0, names=None):
    return {"ok": {"names": list(NAMES if names is None else names), "current_index": index}}


def event(index=0, names=None):
    return {"event": "keyboard_layout", "data": state(index, names)["ok"]}


class Ipc:
    def __init__(self, index=0):
        self.index = index
        self.names = list(NAMES)
        self.requests = []
        self.applies = True
        self.reply = None

    def request(self, payload, timeout=2.0):
        assert 0 < timeout <= 2
        self.requests.append(payload)
        if self.reply is not None:
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply
        if payload == {"cmd": "keyboard-layouts"}:
            return state(self.index, self.names)
        assert payload == {"cmd": "action", "action": "keyboard-layout-next"}
        if self.applies:
            self.index = 1 - self.index
        return {"ok": None}


class Watch:
    def __init__(self):
        self.closed = 0

    def __call__(self, ipc, callback):
        self.callback = callback
        callback(event(ipc.index, ipc.names))
        return self.close

    def close(self):
        self.closed += 1


class FocusIpc(Ipc):
    def __init__(self):
        super().__init__()
        self.focus_reply = {"ok": {"active": True, "title": "private title"}}
        self.focus_timeouts = []

    def request(self, payload, timeout=2.0):
        if payload != {"cmd": "focused-window"}:
            return super().request(payload, timeout)
        self.requests.append(payload)
        self.focus_timeouts.append(timeout)
        if isinstance(self.focus_reply, Exception):
            raise self.focus_reply
        return self.focus_reply


def backend(ipc=None, watch=None):
    return umbriel.UmbrielBackend(ipc=ipc or Ipc(), watch=watch or Watch(), rules=RULES)


def test_socket_selection_is_exact_and_does_not_guess_another_session():
    assert umbriel.find_socket({"UMBRIEL_SOCKET": "/run/user/5/explicit.sock"}) == (
        "/run/user/5/explicit.sock")
    env = {"XDG_RUNTIME_DIR": "/run/user/5", "WAYLAND_DISPLAY": "wayland-2"}
    assert umbriel.find_socket(env) == "/run/user/5/umbriel-wayland-2.sock"
    assert umbriel.find_socket({"XDG_RUNTIME_DIR": "/run/user/5"}) is None
    assert umbriel.find_socket({"WAYLAND_DISPLAY": "wayland-2"}) is None
    env["WAYLAND_DISPLAY"] = "../other"
    assert umbriel.find_socket(env) is None


@pytest.mark.parametrize("reply", [
    None, [], {"err": "private diagnostic"}, {"ok": None}, {"ok": True},
    {"ok": state()["ok"], "err": "bad"}, state(True), state(0.0), state(-1), state(2),
    state(names=[]), state(names=["Russian", "Russian"]), state(names=["", "Russian"]),
    state(names=[5, "Russian"]), state(names=["a" * 257, "Russian"]),
    state(names=[str(i) for i in range(9)]),
])
def test_untrusted_layout_metadata_is_rejected_without_echoing_payload(reply):
    with pytest.raises(BackendError) as error:
        umbriel.keyboard_state(reply)
    assert "private diagnostic" not in str(error.value)


def test_state_retains_actual_order_and_variant_names():
    names, index = umbriel.keyboard_state(state(1, ["Russian (phonetic)", "English (Dvorak)"]))
    assert names == ["Russian (phonetic)", "English (Dvorak)"] and index == 1


def test_query_only_input_guard_needs_no_keyboard_backend_or_subscription():
    ipc = FocusIpc()
    assert umbriel.query_input_locked(ipc) is False
    ipc.focus_reply = {"ok": {"active": False}}
    assert umbriel.query_input_locked(ipc) is True
    assert ipc.requests == [{"cmd": "focused-window"}] * 2
    assert ipc.focus_timeouts == [.05] * 2


@pytest.mark.parametrize("reply", [
    None, [], {}, {"err": "private title"}, {"ok": None}, {"ok": []}, {"ok": True},
    {"ok": {}}, {"ok": {"active": False}}, {"ok": {"active": None}},
    {"ok": {"active": 1}}, {"ok": {"active": "true"}},
    {"ok": {"active": True}, "err": "private title"},
    TimeoutError("private title"), OSError("private title"), BackendError("private title"),
])
def test_input_guard_cold_unknown_or_inactive_state_locks_without_logging(reply, caplog, capsys):
    ipc = FocusIpc()
    ipc.focus_reply = reply
    b = backend(ipc)
    assert b.input_locked() is True
    assert ipc.requests == [{"cmd": "keyboard-layouts"}, {"cmd": "focused-window"}]
    assert ipc.focus_timeouts == [.05] and b.current() == 0
    assert not caplog.records and capsys.readouterr() == ("", "")


def test_input_guard_requeries_every_key_without_caching_an_unlocked_result(caplog, capsys):
    ipc = FocusIpc()
    b = backend(ipc)
    assert b.input_locked() is False
    ipc.focus_reply = {"ok": {"active": False, "title": "private title"}}
    assert b.input_locked() is True
    ipc.focus_reply = TimeoutError("private title")
    assert b.input_locked() is True
    ipc.focus_reply = {"ok": {"active": True, "title": "private title"}}
    assert b.input_locked() is False
    assert ipc.focus_timeouts == [.05] * 4
    assert ipc.requests == [{"cmd": "keyboard-layouts"}] + [{"cmd": "focused-window"}] * 4
    assert b.current() == 0 and not caplog.records and capsys.readouterr() == ("", "")


@pytest.mark.parametrize("invalidate", [False, True])
def test_closed_or_invalid_backend_never_grants_input_or_queries_focus(invalidate):
    ipc, watch = FocusIpc(), Watch()
    b = backend(ipc, watch)
    if invalidate:
        watch.callback(None)
    else:
        b.close()
    before = list(ipc.requests)
    assert b.input_locked() is True and ipc.requests == before
    assert not ipc.focus_timeouts


def test_backend_closed_while_focus_query_returns_cannot_grant_input(monkeypatch):
    ipc = FocusIpc()
    b = backend(ipc)

    def request(payload, timeout):
        assert payload == {"cmd": "focused-window"} and timeout == .05
        b.close()
        return {"ok": {"active": True}}

    monkeypatch.setattr(ipc, "request", request)
    assert b.input_locked() is True


def test_native_set_verifies_state_instead_of_optimistic_acknowledgment():
    ipc = Ipc()
    b = backend(ipc)
    assert b.layouts() == [LayoutSpec("us"), LayoutSpec("ru")]
    assert b.xkb_options() is None
    b.set(1)
    assert b.current() == 1 and b.wait_applied(1, .05)
    assert ipc.requests.count({"cmd": "action", "action": "keyboard-layout-next"}) == 1
    ipc.applies = False
    b.set(0)
    assert b.current() == 1 and not b.wait_applied(0, .02)
    assert ipc.requests.count({"cmd": "action", "action": "keyboard-layout-next"}) == 2


def test_confirmed_native_layout_is_requeried_without_blind_sleep(monkeypatch):
    ipc = Ipc()
    b = backend(ipc)
    b.set(1)
    before = list(ipc.requests)
    clock, slept = [0.0], []

    def sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(umbriel, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    assert b.wait_applied(1, .05)
    assert not slept
    assert ipc.requests == before + [{"cmd": "keyboard-layouts"}]


@pytest.mark.parametrize("index", [True, False, -1, 2, "1", None])
def test_invalid_target_never_sends_an_action(index):
    ipc = Ipc()
    b = backend(ipc)
    before = list(ipc.requests)
    with pytest.raises(BackendError):
        b.set(index)
    assert ipc.requests == before
    assert not b.wait_applied(index, .01)


@pytest.mark.parametrize("names", [["English (US)"], NAMES + ["German"]])
def test_native_backend_requires_two_groups_before_any_action(names):
    ipc = Ipc()
    ipc.names = names
    with pytest.raises(BackendError):
        backend(ipc)
    assert ipc.requests == [{"cmd": "keyboard-layouts"}]


def test_subscription_initial_snapshot_wins_the_earlier_query_race():
    ipc = Ipc()

    def watch(_ipc, callback):
        ipc.index = 1
        callback(event(1))
        return lambda: None

    assert backend(ipc, watch).current() == 1


def test_own_ack_then_external_transition_reports_the_external_change():
    ipc, watch = Ipc(), Watch()
    b = backend(ipc, watch)
    changes = []
    b.on_change(lambda *args: changes.append(args))
    b.set(1)
    watch.callback(event(1))
    ipc.index = 0
    watch.callback(event(0))
    assert changes == [(1, False), (0, True)]


@pytest.mark.parametrize("applies", [False, True])
def test_unexpected_event_during_switch_cannot_be_discarded_as_stale(applies):
    ipc, watch = Ipc(), Watch()
    ipc.applies = applies
    b = backend(ipc, watch)
    changes, reloads = [], []
    b.on_change(lambda *args: changes.append(args))
    b.on_layouts_changed = lambda: reloads.append(True)
    b.set(1)
    changes.clear()
    watch.callback(event(0))
    assert b.current() is None and changes == [(None, True)] and reloads == [True]
    assert not b.wait_applied(1, .01) and watch.closed == 1


@pytest.mark.parametrize("failure", [None, {"err": "private diagnostic"}, event(True),
                                      event(0, ["Russian", "English (US)"])])
def test_drop_malformed_state_and_map_change_invalidate_once(failure):
    ipc, watch = Ipc(), Watch()
    b = backend(ipc, watch)
    changes, reloads = [], []
    b.on_change(lambda *args: changes.append(args))
    b.on_layouts_changed = lambda: reloads.append(True)
    watch.callback(failure)
    watch.callback(failure)
    assert b.current() is None and changes == [(None, True)] and reloads == [True]
    assert not b.wait_applied(0, .01)
    with pytest.raises(BackendError):
        b.set(0)
    b.close()
    b.close()
    assert watch.closed == 1


def test_query_failure_invalidates_cached_current_without_keyboard_fallback():
    ipc = Ipc()
    b = backend(ipc)
    ipc.reply = OSError("connection reset")
    assert not b.wait_applied(0, .05) and b.current() is None


@pytest.mark.parametrize("reply", [{"ok": False}, {"err": "private diagnostic"}, None])
def test_action_error_invalidates_instead_of_advancing_state(reply):
    class RefusingIpc(Ipc):
        def request(self, payload, timeout=2.0):
            if payload == {"cmd": "action", "action": "keyboard-layout-next"}:
                return reply
            return super().request(payload, timeout)

    b = backend(RefusingIpc())
    with pytest.raises(BackendError):
        b.set(1)
    assert b.current() is None


def test_initial_subscription_drop_closes_the_owned_watch():
    closed = []

    def watch(_ipc, callback):
        callback(None)
        return lambda: closed.append(True)

    with pytest.raises(BackendError):
        backend(watch=watch)
    assert closed == [True]


def test_set_uses_a_fresh_query_and_does_not_cycle_an_already_selected_layout():
    ipc = Ipc()
    b = backend(ipc)
    ipc.index = 1  # A physical layout change arrived before its subscription event.
    b.set(1)
    assert b.current() == 1
    assert all(request == {"cmd": "keyboard-layouts"} for request in ipc.requests)


def test_query_map_change_invalidates_before_any_action():
    ipc = Ipc()
    b = backend(ipc)
    changes = []
    b.on_change(lambda *args: changes.append(args))
    ipc.names.reverse()
    with pytest.raises(BackendError):
        b.set(1)
    assert b.current() is None and changes == [(None, True)]
    assert all(request == {"cmd": "keyboard-layouts"} for request in ipc.requests)


def test_expired_set_deadline_cannot_start_an_action(monkeypatch):
    ipc = Ipc()
    b = backend(ipc)
    clock = iter([0.0, 3.0])
    monkeypatch.setattr(umbriel, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    with pytest.raises(BackendError):
        b.set(1)
    assert all(request == {"cmd": "keyboard-layouts"} for request in ipc.requests)


def test_close_and_late_events_cannot_resurrect_layout_state():
    ipc, watch = Ipc(), Watch()
    b = backend(ipc, watch)
    b.close()
    b.close()
    watch.callback(event(1))
    assert b.current() is None and watch.closed == 1
    with pytest.raises(BackendError):
        b.set(0)


@contextmanager
def unix_server(chunks):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX unsupported")
    with tempfile.TemporaryDirectory(prefix="ws-") as directory:
        path = os.path.join(directory, "i")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(path)
        except OSError as error:
            server.close()
            if (error.errno in {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT}
                    or getattr(error, "winerror", None) == 10047):
                pytest.skip("AF_UNIX unsupported by this OS")
            raise
        server.listen(1)
        server.settimeout(2)
        requests, errors = [], []

        def serve():
            try:
                with server.accept()[0] as client:
                    request = bytearray()
                    while not request.endswith(b"\n"):
                        chunk = client.recv(4096)
                        if not chunk:
                            raise ConnectionError("request ended before newline")
                        request.extend(chunk)
                    requests.append(json.loads(request))
                    for chunk in chunks:
                        client.sendall(chunk)
            except (OSError, ValueError) as error:
                errors.append(error)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield path, requests
        finally:
            thread.join(3)
            server.close()
            assert not thread.is_alive()
            assert not errors


def test_real_unix_request_handles_fragmented_utf8_and_emits_native_json():
    reply = state(names=["English (US)", "Русская"])
    encoded = json.dumps(reply, ensure_ascii=False).encode() + b"\n"
    split = encoded.index("Р".encode()) + 1
    with unix_server([encoded[:7], encoded[7:split], encoded[split:]]) as (path, requests):
        assert umbriel.UmbrielIpc(path).request({"cmd": "keyboard-layouts"}) == reply
    assert requests == [{"cmd": "keyboard-layouts"}]


@pytest.mark.parametrize("chunks", [[b'{"ok":'], [b'[]\n'], [b'\xff\n'],
                                    [b'{not json}\n'], [b'x' * 65537]])
def test_real_unix_request_eof_malformed_and_oversized_frames_fail_closed(chunks):
    with unix_server(chunks) as (path, _requests), pytest.raises((BackendError, OSError)):
        umbriel.UmbrielIpc(path).request({"cmd": "keyboard-layouts"})


@pytest.fixture
def glib_watch(monkeypatch):
    callbacks, removed = [], []

    def add_watch(fd, priority, conditions, callback):
        callbacks.append(callback)
        return 7

    glib = SimpleNamespace(io_add_watch=add_watch,
                           source_remove=lambda source: removed.append(source),
                           PRIORITY_DEFAULT=0, IO_IN=1, IO_HUP=2, IO_ERR=4, IO_NVAL=8,
                           SOURCE_CONTINUE=True, SOURCE_REMOVE=False)
    monkeypatch.setitem(sys.modules, "gi.repository", SimpleNamespace(GLib=glib))
    return callbacks, removed


@pytest.fixture
def watch_socket(glib_watch):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX unsupported")
    left, right = socket.socketpair()
    callbacks, removed = glib_watch
    ipc = SimpleNamespace(subscribe=lambda: right)
    left.sendall(json.dumps(event()).encode() + b"\n")
    try:
        yield left, right, ipc, callbacks, removed
    finally:
        left.close()
        right.close()


def test_real_subscribe_sends_native_family_and_consumes_initial_snapshot(glib_watch):
    callbacks, removed = glib_watch
    events = []
    with unix_server([json.dumps(event()).encode() + b"\n"]) as (path, requests):
        stop = umbriel._glib_watch(umbriel.UmbrielIpc(path), events.append)
        assert events == [event()]
        stop()
        stop()
    assert requests == [{"cmd": "subscribe", "events": ["keyboard_layout"]}]
    assert len(callbacks) == 1 and removed == [7]


def test_glib_watch_handles_partial_and_coalesced_frames_without_blocking(watch_socket):
    left, right, ipc, callbacks, removed = watch_socket
    events = []
    stop = umbriel._glib_watch(ipc, events.append)
    assert events == [event()] and right.gettimeout() == 0
    encoded = json.dumps(event(1)).encode() + b"\n"
    left.sendall(encoded[:9])
    assert callbacks[0](right.fileno(), 1) is True and len(events) == 1
    left.sendall(encoded[9:] + json.dumps(event(0)).encode() + b"\n")
    assert callbacks[0](right.fileno(), 1) is True
    assert events == [event(), event(1), event(0)]
    left.close()
    assert callbacks[0](right.fileno(), 2) is False and events[-1] is None
    stop()
    stop()
    assert removed == [7]


def test_glib_watch_rejects_overlong_partial_line_and_closes(watch_socket):
    left, right, ipc, callbacks, removed = watch_socket
    events = []
    stop = umbriel._glib_watch(ipc, events.append)
    # Keep writes below the OS socket buffer; each callback drains nonblocking.
    for _ in range(17):
        left.sendall(b'x' * 4096)
        keep = callbacks[0](right.fileno(), 1)
        if not keep:
            break
    assert keep is False and events[-1] is None and right.fileno() == -1
    stop()
    assert removed == [7]
