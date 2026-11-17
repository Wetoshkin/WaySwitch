"""Ограниченный поток клавиш GNOME; evdev и контроллер остаются общими."""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field

from wayswitch import keycodes as kc

MAX_QUEUE = 256
MAX_DEVICES = 32
MAX_RATE = 1024
CAPABILITIES = frozenset({"compositor-key-events", "device-node", "key-repeat",
                          "new-device-state", "uinput-only"})
log = logging.getLogger("wayswitch")


@dataclass
class _Device:
    sequence: int = 0
    held: set[int] = field(default_factory=set)
    blocked: bool = False
    discard_word: bool = False


def _uint(value, minimum=0):
    return type(value) is int and minimum <= value <= 0xFFFFFFFF


def _device_id(value):
    return type(value) is int and -0x80000000 <= value < 0


class CompositorStream:
    def __init__(self, on_key, on_resync, on_gone, on_caps, on_click, on_scroll,
                 schedule, *, clock=time.monotonic):
        self.on_key, self.on_resync, self.on_gone = on_key, on_resync, on_gone
        self.on_caps, self.on_click, self.on_scroll = on_caps, on_click, on_scroll
        self.schedule, self.clock = schedule, clock
        self.owner = None
        self.generation = 0
        self._epoch = 0
        self._devices: dict[int, _Device] = {}
        self._queue = deque()
        self._scheduled = False
        self._fault = False
        self._rate_at, self._rate = clock(), 0

    @property
    def input_locked(self):
        return self._fault or any(device.blocked or device.discard_word
                                 for device in self._devices.values())

    def reset(self, owner=None, generation=0):
        self._epoch += 1
        self._queue.clear()
        self._scheduled = False
        for device_id in self._devices:
            self.on_gone(device_id)
        self._devices.clear()
        self._fault = False
        self.owner, self.generation = owner, generation
        self._rate_at, self._rate = self.clock(), 0

    def _invalidate(self):
        self._epoch += 1
        self._queue.clear()
        self._scheduled = False
        for device_id, device in self._devices.items():
            device.blocked = True
            device.discard_word = True
            self.on_resync(device_id, sorted(device.held))
        self.on_click()

    def receive(self, sender, name, args):
        if not self.owner or sender != self.owner:
            return
        if name == "CompositorKey":
            self._key(args)
        elif name == "CompositorResync":
            self._resync(args)
        elif name == "CompositorDeviceGone":
            if (len(args) == 3 and _device_id(args[0]) and args[1] == 1
                    and args[2] == self.generation):
                device_id = args[0]
                self._devices.pop(device_id, None)
                self._epoch += 1
                self._queue.clear()
                self._scheduled = False
                for other_id, device in self._devices.items():
                    device.discard_word = True
                    self.on_resync(other_id, sorted(device.held))
                self.on_gone(device_id)
        elif name == "CompositorBoundary":
            if (len(args) == 2 and args[0] == self.generation and args[1] in (0, 1)):
                self._queue.clear()
                for device_id, device in self._devices.items():
                    self.on_resync(device_id, sorted(device.held))
                (self.on_click if args[1] == 0 else self.on_scroll)()
        elif (name == "CompositorFault" and len(args) == 1 and _uint(args[0], 1)
              and args[0] >= self.generation):
            self.generation = args[0]
            self._fault = True
            self._invalidate()

    def _key(self, args):
        if self._fault:
            return
        if len(args) == 7 and _uint(args[4]) and args[4] < self.generation:
            return
        if (len(args) != 7 or not all(_uint(args[i]) for i in (0, 1, 3, 4, 5))
                or not 1 <= args[0] <= 127 or args[1] not in (0, 1, 2)
                or not _device_id(args[2]) or args[3] != 1
                or args[4] != self.generation or args[5] == 0 or type(args[6]) is not bool):
            self._invalidate()
            return
        code, value, device_id, _seat, _generation, sequence, _caps = args
        device = self._devices.get(device_id)
        if device is None:
            if len(self._devices) >= MAX_DEVICES:
                self._invalidate()
                return
            device = self._devices[device_id] = _Device()
        valid = (sequence == device.sequence + 1 and (
            value == 1 and code not in device.held
            or value in (0, 2) and code in device.held))
        device.sequence = sequence
        if value == 0:
            device.held.discard(code)
        else:
            device.held.add(code)
        now = self.clock()
        if now - self._rate_at >= 1:
            self._rate_at, self._rate = now, 0
        self._rate += 1
        if not valid or self._rate > MAX_RATE or len(self._queue) >= MAX_QUEUE:
            self._invalidate()
            return
        if device.blocked:
            return
        if device.discard_word:
            if value != 1 or code not in (kc.KEY_TAB, kc.KEY_ENTER, kc.KEY_SPACE):
                return
            device.discard_word = False
            self.on_resync(device_id, sorted(device.held - {code}))
        self._queue.append(tuple(args))
        if not self._scheduled:
            self._scheduled = True
            epoch = self._epoch
            self.schedule(lambda: self._drain(epoch))

    def _resync(self, args):
        if (len(args) != 6 or not _device_id(args[0]) or args[1] != 1
                or not _uint(args[2], 1) or not _uint(args[3])
                or not isinstance(args[4], (list, tuple)) or len(args[4]) > 127
                or not all(type(code) is int and 1 <= code <= 127 for code in args[4])
                or type(args[5]) is not bool):
            self._invalidate()
            return
        device_id, _seat, _generation, sequence, held, complete = args
        if _generation < self.generation:
            return
        if _generation > self.generation:
            self.generation = _generation
            self._invalidate()
        device = self._devices.get(device_id)
        if device is None:
            return
        if sequence < device.sequence:
            return
        if (device.blocked or sequence != device.sequence
                or set(held) != device.held or not complete):
            self._queue = deque(event for event in self._queue if event[2] != device_id)
            device.sequence, device.held = sequence, set(held)
            device.blocked = bool(held) or not complete
            device.discard_word = True
            self.on_resync(device_id, sorted(device.held))

    def _drain(self, epoch):
        if epoch != self._epoch:
            return
        count = 0
        while self._queue and epoch == self._epoch and count < 64:
            count += 1
            code, value, device_id, _seat, _generation, _sequence, caps = self._queue.popleft()
            device = self._devices.get(device_id)
            if device is not None and not device.blocked:
                self.on_caps(caps)
                self.on_key(code, value, device_id)
        if epoch == self._epoch:
            self._scheduled = bool(self._queue)
            if self._queue:
                self.schedule(lambda: self._drain(epoch))


class ShellInput:
    """Подписка на один проверенный owner расширения в той же сессионной шине."""

    def __init__(self, stream, watcher, own_node):
        self.stream, self.watcher, self.own_node = stream, watcher, own_node
        self._proxy = self._connection = None
        self._handlers = []
        self.active = False

    def start(self):
        from gi.repository import Gio, GLib

        from wayswitch.backends.gnome_shell import BUS_NAME, INTERFACE, OBJECT_PATH

        self._proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            BUS_NAME, OBJECT_PATH, INTERFACE, None)
        self._connection = self._proxy.get_connection()
        owner, = self._connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "GetNameOwner", GLib.Variant("(s)", ("io.github.Wetoshkin.WaySwitch",)),
            GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 1000, None).unpack()
        if owner != self._connection.get_unique_name():
            raise RuntimeError("Daemon name is not owned by this connection")
        self._handlers = [(self._proxy, self._proxy.connect("g-signal", self._signal)),
                          (self._proxy, self._proxy.connect("notify::g-name-owner", self._owner)),
                          (self._connection, self._connection.connect("closed", self._closed))]
        return self._enable()

    def _call(self, name, args=None):
        return self._proxy.call_sync(name, args, 0, 1000, None).unpack()

    def _enable(self):
        from gi.repository import GLib

        owner = self._proxy.get_name_owner()
        self.active = False
        self.stream.reset()
        if not owner:
            return False
        capabilities, = self._call("GetInputCapabilities")
        if not CAPABILITIES <= set(capabilities):
            return False
        self.watched_changed()
        generation, = self._call("EnableCompositorInput", GLib.Variant("(s)", (self.own_node,)))
        if (not _uint(generation, 1) or self._proxy.get_name_owner() != owner
                or self._connection.is_closed()):
            return False
        self.stream.reset(owner, generation)
        self.active = True
        return True

    def watched_changed(self):
        from gi.repository import GLib

        paths = [path for path, _name, _kind in self.watcher.devices()]
        self._call("SetWatchedDevices", GLib.Variant("(as)", (paths,)))

    def _signal(self, proxy, sender, name, params):
        if self.active and proxy is self._proxy and proxy.get_connection() is self._connection:
            if name == "CompositorFault" and sender == self.stream.owner:
                log.warning("источник ввода GNOME не подтверждён; требуется перезапуск службы "
                            "и переподключение удалённой клавиатуры")
            self.stream.receive(sender, name, params.unpack())

    def _owner(self, _proxy, _property):
        self.active = False
        self.stream.reset()
        # Новый owner требует повторной явной авторизации через перезапуск службы.

    def _closed(self, *_args):
        self.active = False
        self.stream.reset()

    def close(self):
        self.active = False
        self.stream.reset()
        if self._proxy is not None:
            try:
                self._call("DisableCompositorInput")
            except Exception:  # noqa: BLE001 — отключение уже могло удалить owner
                pass
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers.clear()
