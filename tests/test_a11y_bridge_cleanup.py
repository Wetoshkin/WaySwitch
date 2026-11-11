"""Private AT-SPI connections from bridge/list calls must not outlive the call."""

import sys
from types import SimpleNamespace

import pytest

from wayswitch import a11y


class ProviderError(RuntimeError):
    pass


class _GLibError(Exception):
    pass


class Session:
    def __init__(self, address_error=None):
        self.address_error = address_error
        self.closed = 0

    def call_sync(self, bus, path, iface, method, *_args):
        assert (bus, path, iface, method) == (
            "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress")
        if self.address_error is not None:
            raise self.address_error
        return SimpleNamespace(unpack=lambda: ("unix:path=/at-spi",))

    def close_sync(self, _cancellable):
        self.closed += 1


class PrivateConnection:
    def __init__(self, calls=None, close_error=None, names=(), call_error=None):
        self.calls = [] if calls is None else calls
        self.close_error = close_error
        self.names = names
        self.call_error = call_error
        self.closed = 0

    def call_sync(self, bus, path, iface, method, *_args):
        self.calls.append((bus, path, iface, method))
        if self.call_error is not None:
            raise self.call_error
        if method == "GetChildren":
            kids = tuple((name, name) for name in self.names)
            return SimpleNamespace(unpack=lambda: (kids,))
        if method == "Get":
            name = path
            return SimpleNamespace(unpack=lambda: (name,))
        raise AssertionError(method)

    def close_sync(self, _cancellable):
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


@pytest.fixture
def bridge(monkeypatch):
    session = Session()
    connections = []

    def private_factory(address, flags, _cancellable, _callback):
        assert address == "unix:path=/at-spi"
        assert flags == 3  # AUTHENTICATION_CLIENT | MESSAGE_BUS_CONNECTION
        return connections[0]

    gio = SimpleNamespace(
        BusType=SimpleNamespace(SESSION=1),
        DBusCallFlags=SimpleNamespace(NONE=0),
        DBusConnectionFlags=SimpleNamespace(AUTHENTICATION_CLIENT=1,
                                            MESSAGE_BUS_CONNECTION=2),
        bus_get_sync=lambda *_args: session,
        DBusConnection=SimpleNamespace(new_for_address_sync=private_factory),
    )
    glib = SimpleNamespace(VariantType=lambda signature: signature,
                           Variant=lambda signature, values: (signature, values),
                           Error=_GLibError)
    repository = SimpleNamespace(Gio=gio, GLib=glib)
    monkeypatch.setitem(sys.modules, "gi", SimpleNamespace(repository=repository))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    return SimpleNamespace(session=session, connections=connections)


def test_bridge_available_closes_private_connection_on_success(bridge):
    bridge.connections.append(PrivateConnection(names=("Editor",)))
    assert a11y.bridge_available() == (True, "")
    assert bridge.connections[0].closed == 1
    assert bridge.session.closed == 0


def test_list_applications_closes_private_connection_on_success(bridge):
    bridge.connections.append(PrivateConnection(
        names=("Editor", "gnome-shell", "Editor", "Browser")))
    assert a11y.list_applications() == ["Editor", "Browser"]
    assert bridge.connections[0].closed == 1
    assert bridge.session.closed == 0


@pytest.mark.parametrize("close_error", [None, ProviderError("cleanup provider payload")])
def test_close_failure_does_not_change_success(bridge, close_error, caplog):
    bridge.connections.append(PrivateConnection(names=("Editor",), close_error=close_error))
    assert a11y.bridge_available() == (True, "")
    assert bridge.connections[0].closed == 1
    assert bridge.session.closed == 0
    assert "cleanup provider payload" not in caplog.text


def test_bridge_call_failure_closes_connection_and_keeps_original_error(bridge):
    bridge.connections.append(PrivateConnection(
        call_error=ProviderError("primary at-spi failure"),
        close_error=ProviderError("cleanup provider payload")))
    ok, detail = a11y.bridge_available()
    assert not ok
    assert "primary at-spi failure" in detail
    assert "cleanup provider payload" not in detail
    assert bridge.connections[0].closed == 1
    assert bridge.session.closed == 0


def test_list_call_failure_closes_connection_and_keeps_original_error(bridge, caplog):
    bridge.connections.append(PrivateConnection(
        call_error=ProviderError("primary at-spi failure"),
        close_error=ProviderError("cleanup provider payload")))
    with pytest.raises(ProviderError, match="primary at-spi failure"):
        a11y.list_applications()
    assert bridge.connections[0].closed == 1
    assert bridge.session.closed == 0
    assert "cleanup provider payload" not in caplog.text


@pytest.mark.parametrize("call", [a11y.bridge_available, a11y.list_applications])
def test_failure_before_private_connection_is_assigned_is_preserved(bridge, call):
    bridge.session.address_error = ProviderError("session bus failure")
    if call is a11y.bridge_available:
        ok, detail = call()
        assert not ok and "session bus failure" in detail
    else:
        with pytest.raises(ProviderError, match="session bus failure"):
            call()
    assert bridge.connections == []
    assert bridge.session.closed == 0
