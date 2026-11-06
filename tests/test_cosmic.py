import os

import pytest

from wayswitch.backends.base import BackendError
from wayswitch.backends.cosmic import CosmicBackend, read_keymap


def test_keymap_fd_bounded_and_closed(tmp_path):
    path = tmp_path / "map"
    path.write_bytes(b"xkb\0")
    for fmt, size in [(0, 4), (1, 0), (1, 99), (1, 64 * 1024 * 1024)]:
        fd = os.open(path, os.O_RDONLY)
        with pytest.raises(BackendError):
            read_keymap(fmt, fd, size)
        with pytest.raises(OSError):
            os.fstat(fd)
    assert read_keymap(1, os.open(path, os.O_RDONLY), 4) == b"xkb"
    path.write_bytes(b"xkb")
    fd = os.open(path, os.O_RDONLY)
    os.lseek(fd, 3, os.SEEK_SET)
    assert read_keymap(1, fd, 3) == b"xkb"
    path.write_bytes(b"x\0b")
    with pytest.raises(BackendError):
        read_keymap(1, os.open(path, os.O_RDONLY), 3)


@pytest.mark.skipif(os.name != "posix", reason="Unix directory descriptor")
def test_directory_keymap_fd_is_closed(tmp_path):
    fd = os.open(tmp_path, os.O_RDONLY)
    with pytest.raises(BackendError):
        read_keymap(1, fd, 10)
    with pytest.raises(OSError):
        os.fstat(fd)


class Transport:
    def __init__(self):
        self.sent = []
        self.events = []

    def pump(self, timeout):
        if self.events:
            self.events.pop(0)()

    def set_group(self, group):
        self.sent.append(group)


def backend():
    from tests.fakes import make_keymap
    from wayswitch.keymap import LayoutSpec

    obj = CosmicBackend.__new__(CosmicBackend)
    obj._group, obj._pending, obj._callback, obj.error = 0, None, None, None
    obj.keymap, obj.transport = make_keymap(), Transport()
    obj.keymap.specs = [LayoutSpec("us"), LayoutSpec("ru")]
    return obj


def test_extra_groups_use_actual_map_indices_and_require_confirmation():
    from tests.fakes import RU_TABLE, US_TABLE
    from wayswitch.keymap import LayoutSpec, TableKeymap

    obj = backend()
    obj.keymap = TableKeymap([US_TABLE, RU_TABLE, US_TABLE],
                             [LayoutSpec("de"), LayoutSpec("ru"), LayoutSpec("us")])
    obj._group = 2
    assert obj.keymap.opposite_group(obj.current()) == 1
    obj.set(1)
    obj.transport.events.append(lambda: obj.group(1))
    assert obj.wait_applied(1, .1) and obj.transport.sent == [1]


def test_switch_needs_ack_external_change_is_reported():
    obj = backend()
    changes = []
    obj.on_change(lambda group, external: changes.append((group, external)))
    obj.set(1)
    assert obj.transport.sent == [1] and obj.current() is None
    assert not obj.wait_applied(0, 0)
    obj.transport.events.append(lambda: obj.group(1))
    assert obj.wait_applied(1, 0.1)
    obj.group(0)
    assert obj.current() == 0 and changes == [(1, False), (0, True)]


def test_timeout_then_rollback_cannot_reuse_stale_group():
    obj = backend()
    restarted = []
    obj.on_layouts_changed = lambda: restarted.append(True)
    obj.set(1)
    assert not obj.wait_applied(1, 0)
    with pytest.raises(BackendError):
        obj.set(0)
    obj.group(1)
    assert restarted == [True] and obj.current() is None
    assert not obj.wait_applied(0, 0)
    with pytest.raises(BackendError):
        obj.set(1)


def test_keymap_replacement_invalidates_cached_group(tmp_path):
    obj = backend()
    obj._raw_map = b"old"
    path = tmp_path / "map"
    for raw in (b"old", b"new"):
        path.write_bytes(raw + b"\0")
        obj.keyboard_map(1, os.open(path, os.O_RDONLY), 4)
        assert (obj.current() == 0) == (raw == b"old")


def test_multiple_keyboard_seats_and_hot_unplug_fail_closed():
    from wayswitch.backends.cosmic_wayland import CosmicTransport

    transport = CosmicTransport.__new__(CosmicTransport)
    transport.keyboard_seat = 42
    transport._seat_caps(43, 1)  # второй seat только с мышью
    with pytest.raises(BackendError, match="неоднозначен"):
        transport._seat_caps(43, 2)
    with pytest.raises(BackendError, match="потерял"):
        transport._seat_caps(42, 1)


@pytest.mark.parametrize("readable", [True, False])
def test_backpressure_never_calls_blocking_dispatch(monkeypatch, readable):
    import ctypes
    import errno
    from types import SimpleNamespace

    from wayswitch.backends import cosmic_wayland as wl

    calls = []
    def flush(_display):
        ctypes.set_errno(errno.EAGAIN)
        return -1

    def ready(read, write, _exc, timeout):
        assert read == write == [11] and timeout == 0
        return (read if readable else []), [], []

    transport = wl.CosmicTransport.__new__(wl.CosmicTransport)
    transport.fd, transport.display = 11, object()
    transport.lib = SimpleNamespace(
        wl_display_flush=flush, wl_display_prepare_read=lambda _: 0,
        wl_display_read_events=lambda _: calls.append("read") or 0,
        wl_display_dispatch_pending=lambda _: calls.append("dispatch") or 0,
        wl_display_cancel_read=lambda _: calls.append("cancel"))
    monkeypatch.setattr(wl.select, "select", ready)
    transport.pump(0)
    assert calls == (["read", "dispatch"] if readable else ["cancel"])
