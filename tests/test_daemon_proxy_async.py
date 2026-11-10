"""Асинхронный DaemonProxy на фейковом Gio — без GTK и настоящего D-Bus."""

from __future__ import annotations

import sys
import types

import pytest

from wayswitch.gui.daemon_proxy import DaemonProxy


class FakeReply:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


class FakeCall:
    def __init__(self, proxy, method, timeout, cancellable, callback, user_data):
        self.proxy = proxy
        self.method = method
        self.timeout = timeout
        self.cancellable = cancellable
        self.callback = callback
        self.user_data = user_data

    def complete(self, value) -> None:
        self.callback(self.proxy, FakeReply(value), self.user_data)

    def fail(self, error: Exception) -> None:
        self.callback(self.proxy, error, self.user_data)


class FakeProxy:
    def __init__(self, owner: str | None):
        self.owner = owner
        self.handlers = {}
        self.requests: list[FakeCall] = []
        self.finished = 0
        self.disconnected = []
        self._handler_ids = {}
        self._next_handler = 1

    def connect(self, name, callback):
        handler = self._next_handler
        self._next_handler += 1
        self.handlers[name] = callback
        self._handler_ids[handler] = (name, callback)
        return handler

    def disconnect(self, handler) -> None:
        self.disconnected.append(handler)
        item = self._handler_ids.pop(handler, None)
        if item is not None and self.handlers.get(item[0]) is item[1]:
            del self.handlers[item[0]]

    def get_name_owner(self):
        return self.owner

    def call(self, method, parameters, flags, timeout, cancellable, callback,
             user_data) -> None:
        assert parameters is None
        self.requests.append(FakeCall(self, method, timeout, cancellable,
                                      callback, user_data))

    def call_finish(self, result):
        self.finished += 1
        if isinstance(result, BaseException):
            raise result
        return result

    def set_owner(self, owner: str | None) -> None:
        old, self.owner = self.owner, owner
        if old != owner:
            self.handlers["notify::g-name-owner"](self, None)

    def signal(self, name: str, value, sender: str | None = None) -> None:
        sender = self.owner if sender is None else sender
        self.handlers["g-signal"](self, sender, name, FakeReply(value))


def install_fake_gi(monkeypatch):
    fake = types.SimpleNamespace(pending_start=None, start_args=None,
                                 cancellable=None)

    class Cancellable:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    class DBusProxy:
        @staticmethod
        def new_for_bus(bus_type, flags, info, name, object_path, interface,
                        cancellable, callback, user_data):
            fake.start_args = (bus_type, name, object_path, interface)
            fake.cancellable = cancellable
            fake.pending_start = (callback, user_data)

        @staticmethod
        def new_for_bus_finish(result):
            if isinstance(result, BaseException):
                raise result
            return result

    class Notification:
        def __init__(self, title):
            self.title = title
            self.body = None

        @staticmethod
        def new(title):
            return Notification(title)

        def set_body(self, body):
            self.body = body

    fake.DBusProxy = DBusProxy
    fake.Cancellable = Cancellable
    fake.BusType = types.SimpleNamespace(SESSION=0)
    fake.DBusProxyFlags = types.SimpleNamespace(NONE=0)
    fake.DBusCallFlags = types.SimpleNamespace(NONE=0)
    fake.Notification = Notification

    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    repository.Gio = fake
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    return fake


def finish_start(fake, proxy) -> None:
    callback, user_data = fake.pending_start
    fake.pending_start = None
    callback(None, proxy, user_data)


def make_proxy(monkeypatch, owner: str | None = ":1.7", status: dict | None = None):
    fake = install_fake_gi(monkeypatch)
    statuses, corrected = [], []
    proxy = DaemonProxy(statuses.append, corrected.append)
    proxy.start()
    daemon = FakeProxy(owner)
    finish_start(fake, daemon)
    initial = None
    if status is not None:
        initial = daemon.requests.pop(0)
        assert initial.method == "GetStatus"
        initial.complete((status,))
    return fake, proxy, daemon, statuses, corrected


def test_start_and_get_status_do_not_wait_for_the_bus(monkeypatch):
    fake = install_fake_gi(monkeypatch)
    statuses = []
    proxy = DaemonProxy(statuses.append, lambda *_: None)

    proxy.start()
    assert fake.pending_start is not None
    assert proxy.status is None
    assert statuses == []

    daemon = FakeProxy(":1.7")
    finish_start(fake, daemon)
    request = daemon.requests[0]
    assert request.method == "GetStatus"
    # Запрос отправлен, но окно уже живо: статус ещё не подтверждён.
    assert statuses == []

    request.complete(({"active": True},))
    assert statuses == [{"active": True}]
    assert proxy.status == {"active": True}


def test_absent_service_reports_none_without_a_dbus_call(monkeypatch):
    fake = install_fake_gi(monkeypatch)
    statuses = []
    done = []
    proxy = DaemonProxy(statuses.append, lambda *_: None)
    proxy.start()
    daemon = FakeProxy(None)
    finish_start(fake, daemon)

    assert statuses == [None]
    assert daemon.requests == []
    proxy.refresh(done.append)
    assert done == [None]


def test_stale_status_reply_does_not_overwrite_newer_signal(monkeypatch):
    _, proxy, daemon, statuses, _ = make_proxy(monkeypatch, status={"n": 1})
    proxy.refresh()
    stale = daemon.requests.pop(0)

    daemon.signal("StatusChanged", ({"n": 2},))
    stale.complete(({"n": 1},))

    assert proxy.status == {"n": 2}
    assert statuses == [{"n": 1}, {"n": 2}]
    # Ответ всё равно разобран, даже если его результат отброшен.
    assert daemon.finished == 2


def test_newer_refresh_wins_over_older_pending_refresh(monkeypatch):
    _, proxy, daemon, statuses, _ = make_proxy(monkeypatch, status={"n": 1})
    done = []
    proxy.refresh(done.append)
    older = daemon.requests.pop(0)
    proxy.refresh()
    newer = daemon.requests.pop(0)

    older.complete(({"n": 2},))
    newer.complete(({"n": 3},))

    assert proxy.status == {"n": 3}
    assert statuses == [{"n": 1}, {"n": 3}]
    # Устаревший refresh не зависает: колбэк получает применённый на тот
    # момент статус, но не затирает более новый ответ.
    assert done == [{"n": 1}]


def test_owner_disappears_and_reappears_discards_old_reply(monkeypatch):
    _, proxy, daemon, statuses, _ = make_proxy(monkeypatch, status={"n": 1})
    proxy.refresh()
    old_reply = daemon.requests.pop(0)

    daemon.set_owner(None)
    assert proxy.status is None
    old_reply.complete(({"n": 2},))
    assert proxy.status is None

    daemon.set_owner(":1.8")
    new_reply = daemon.requests.pop(0)
    assert new_reply.method == "GetStatus"
    new_reply.complete(({"n": 3},))

    assert proxy.status == {"n": 3}
    assert statuses == [{"n": 1}, None, {"n": 3}]


def test_signal_from_previous_owner_is_ignored(monkeypatch):
    _, proxy, daemon, statuses, _ = make_proxy(monkeypatch, status={"n": 1})
    daemon.set_owner(":1.8")

    daemon.signal("StatusChanged", ({"n": 2},), sender=":1.7")
    assert proxy.status is None
    assert statuses == [{"n": 1}, None]


def test_direct_owner_replacement_clears_status_promptly(monkeypatch):
    _, proxy, daemon, statuses, _ = make_proxy(monkeypatch, status={"n": 1})
    daemon.set_owner(":1.8")

    assert proxy.status is None
    assert statuses == [{"n": 1}, None]
    new_reply = daemon.requests.pop(0)
    new_reply.complete(({"n": 3},))
    assert proxy.status == {"n": 3}
    assert statuses == [{"n": 1}, None, {"n": 3}]


def test_delayed_command_over_owner_replacement_is_discarded(monkeypatch):
    _, proxy, daemon, _, _ = make_proxy(monkeypatch, status={"n": 1})
    results = []
    proxy.call("FixSelection", results.append)
    delayed = daemon.requests.pop(0)

    daemon.set_owner(None)
    daemon.set_owner(":1.8")
    replacement = daemon.requests.pop(0)
    delayed.complete((False,))

    assert results == []
    assert replacement.method == "GetStatus"
    assert daemon.finished == 2  # начальный статус и отброшенный ответ команды
    replacement.complete(({"n": 2},))
    assert proxy.status == {"n": 2}


def test_delayed_callbacks_after_close_are_ignored(monkeypatch):
    fake, proxy, daemon, _, _ = make_proxy(monkeypatch, status={"n": 1})
    results = []
    proxy.call("FixSelection", results.append)
    command = daemon.requests.pop(0)
    done = []
    proxy.refresh(done.append)
    status_reply = daemon.requests.pop(0)

    proxy.close()

    assert fake.cancellable.cancelled is True
    assert daemon.handlers == {}
    assert len(daemon.disconnected) == 2
    command.complete((False,))
    status_reply.complete(({"n": 2},))
    assert results == []
    assert done == []
    assert daemon.finished == 3  # все поздние ответы всё равно разобраны

    assert proxy.call("Pause") is False
    proxy.refresh(done.append)
    assert done == []


def test_close_while_proxy_construction_is_pending(monkeypatch):
    fake = install_fake_gi(monkeypatch)
    statuses = []
    proxy = DaemonProxy(statuses.append, lambda *_: None)
    proxy.start()
    proxy.close()

    daemon = FakeProxy(":1.7")
    finish_start(fake, daemon)

    assert fake.cancellable.cancelled is True
    assert statuses == []
    assert daemon.handlers == {}
    assert proxy.call("Pause") is False


def test_command_callbacks_are_independent_for_rapid_calls(monkeypatch):
    _, proxy, daemon, statuses, _ = make_proxy(monkeypatch, status={"n": 1})
    results = []

    assert proxy.call("FixSelection", results.append) is True
    assert proxy.call("FixSelection", results.append) is True
    first, second = daemon.requests
    assert first.method == second.method == "FixSelection"
    assert results == []  # до завершения уведомления нет

    # Ответы пришли не по порядку: каждый колбэк получает свой результат.
    second.complete((False,))
    first.complete((True,))
    assert results == [(False,), (True,)]
    assert proxy.status == {"n": 1}
    assert statuses == [{"n": 1}]


def test_call_without_owner_is_not_reported_before_completion(monkeypatch):
    _, proxy, daemon, _, _ = make_proxy(monkeypatch, status={"n": 1})
    daemon.set_owner(None)
    results = []

    assert proxy.call("FixSelection", results.append) is False
    assert results == []


def test_command_failure_and_error_are_real_completions(monkeypatch):
    _, proxy, daemon, _, _ = make_proxy(monkeypatch, status={"n": 1})
    results = []
    proxy.call("FixSelection", results.append)
    daemon.requests.pop(0).complete((False,))
    proxy.call("FixSelection", results.append)
    daemon.requests.pop(0).fail(RuntimeError("owner vanished"))

    assert results == [(False,), None]


def test_selection_feedback_only_after_actual_dbus_result(monkeypatch):
    install_fake_gi(monkeypatch)
    from wayswitch.gui.app import notify_selection_result

    _, proxy, daemon, _, _ = make_proxy(monkeypatch, status={"n": 1})
    sent = []
    proxy.call("FixSelection",
               lambda result: notify_selection_result(lambda ident, note:
                                                      sent.append((ident, note)), result))
    assert sent == []

    daemon.requests.pop(0).complete((True,))
    assert sent == []

    proxy.call("FixSelection",
               lambda result: notify_selection_result(lambda ident, note:
                                                      sent.append((ident, note)), result))
    daemon.requests.pop(0).complete((False,))
    assert len(sent) == 1
    assert sent[0][0] == "selection-failed"
    assert sent[0][1].title == "Could not confirm the correction"


def test_proxy_construction_failure_is_reported_without_blocking(monkeypatch):
    fake = install_fake_gi(monkeypatch)
    statuses = []
    proxy = DaemonProxy(statuses.append, lambda *_: None)
    proxy.start()
    callback, user_data = fake.pending_start
    fake.pending_start = None
    callback(None, RuntimeError("no session bus"), user_data)

    assert proxy.status is None
    assert statuses == [None]
    assert proxy.call("Pause") is False


def test_fire_and_forget_call_does_not_need_a_callback(monkeypatch):
    _, proxy, daemon, _, _ = make_proxy(monkeypatch, status={"n": 1})
    assert proxy.call("Reload") is True
    request = daemon.requests.pop(0)
    assert request.method == "Reload"
    request.complete(None)


@pytest.mark.parametrize("method", ["Pause", "Resume", "Reload"])
def test_fire_and_forget_methods_keep_the_wire_api(monkeypatch, method):
    _, proxy, daemon, _, _ = make_proxy(monkeypatch, status={"n": 1})
    proxy.call(method)
    request = daemon.requests.pop(0)
    assert request.method == method
    assert request.timeout == 3000


def test_daemon_proxy_has_no_synchronous_gio_calls():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "wayswitch" / "gui"
              / "daemon_proxy.py").read_text(encoding="utf-8")
    assert "Gio.DBusProxy.new_for_bus(" in source
    assert "new_for_bus_sync(" not in source
    assert "call_sync(" not in source
