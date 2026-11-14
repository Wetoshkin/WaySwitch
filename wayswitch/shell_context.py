"""GNOME context discovery bound to one bus connection and service owner."""

from __future__ import annotations

import threading
import time
from typing import NamedTuple

BUS_NAME = "io.github.Wetoshkin.WaySwitch.Shell"
OBJECT_PATH = "/io/github/Wetoshkin/WaySwitch/Shell"
INTERFACE = BUS_NAME
PAYLOAD_CAPABILITY = "focused_context_payload_v2"
MAX_AGE_SEC = 3.0


class ShellContext(NamedTuple):
    app_ids: tuple[str, ...] = ()
    modal: bool = False
    ime_focus: bool = False
    purpose: int = 0
    hints: int = 0
    pid: int = 0

    def protected(self) -> bool:
        return self.modal or self.ime_focus and (self.purpose in (8, 12)
                                                 or bool(self.hints & (64 | 128)))


def _context(values) -> ShellContext | None:
    if not isinstance(values, (tuple, list)) or len(values) != 6:
        return None
    ids, modal, ime, purpose, hints, pid = values
    if (not isinstance(ids, (tuple, list)) or len(ids) > 16
            or any(type(s) is not str or len(s) > 256 for s in ids)
            or type(modal) is not bool or type(ime) is not bool
            or any(type(n) is not int or not 0 <= n <= 0xFFFFFFFF
                   for n in (purpose, hints, pid))):
        return None
    return ShellContext(tuple(ids), modal, ime, purpose, hints, pid)


class ShellWatch:
    """Subscriptions dispatch on the daemon's continuously served default context."""

    def __init__(self, bus, invalidate, *, clock=time.monotonic):
        from gi.repository import Gio, GLib

        self._bus, self._gio, self._glib = bus, Gio, GLib
        self._invalidate, self._clock = invalidate, clock
        self._lock = threading.Lock()
        self._closed = False
        self._owner = None  # None: unknown, "": confirmed absent
        self._generation = 0
        self._capabilities = None
        self._legacy = False
        self._payload = None
        self._received = 0.0
        self._subscriptions = []
        self._handler = None
        try:
            self._handler = bus.connect("closed", self._lost)
            for sender, iface, member, path, arg in [
                ("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                 "/org/freedesktop/DBus", BUS_NAME),
                (BUS_NAME, INTERFACE, "FocusedContextChanged", OBJECT_PATH, None),
                (BUS_NAME, INTERFACE, "FocusedContextChanged2", OBJECT_PATH, None),
            ]:
                self._subscriptions.append(bus.signal_subscribe(
                    sender, iface, member, path, arg, Gio.DBusSignalFlags.NONE,
                    self._changed, None))
            generation = self.generation
            owner = self._lookup_owner()
            with self._lock:
                if self._generation == generation and not self._closed:
                    self._owner = owner
        except Exception:
            self.close()
            raise

    @property
    def generation(self):
        with self._lock:
            return self._generation

    def _error(self, exc):
        try:
            return self._gio.DBusError.get_remote_error(exc)
        except (AttributeError, TypeError):
            return None

    def _lookup_owner(self):
        try:
            return self._bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "GetNameOwner", self._glib.Variant("(s)", (BUS_NAME,)),
                self._glib.VariantType("(s)"), self._gio.DBusCallFlags.NONE, 500, None).unpack()[0]
        except Exception as exc:
            return "" if self._error(exc) in {
                "org.freedesktop.DBus.Error.NameHasNoOwner",
                "org.freedesktop.DBus.Error.ServiceUnknown"} else None

    def _lost(self, *_args):
        with self._lock:
            self._owner = None
            self._payload = None
            self._capabilities = None
            self._generation += 1
            self._closed = True
        self._invalidate()

    def _changed(self, _bus, sender, _path, _iface, member, parameters, _data):
        with self._lock:
            if self._closed:
                return
            if member == "NameOwnerChanged":
                name, _old, owner = parameters.unpack()
                if name != BUS_NAME:
                    return
                self._owner = owner
                self._capabilities = None
                self._legacy = False
            elif self._owner is not None and (not self._owner or sender != self._owner):
                return
            self._generation += 1
            self._payload = None
            if (member == "FocusedContextChanged2" and self._owner
                    and self._capabilities is not None):
                try:
                    context = _context(parameters.unpack())
                except (AttributeError, TypeError, ValueError):
                    context = None
                if (PAYLOAD_CAPABILITY in self._capabilities
                        and context is not None and context.ime_focus):
                    self._payload = context
                    self._received = self._clock()
        self._invalidate()

    def _call(self, owner, method, signature):
        return self._bus.call_sync(
            owner or BUS_NAME, OBJECT_PATH, INTERFACE, method, None,
            self._glib.VariantType(signature), self._gio.DBusCallFlags.NONE,
            500, None).unpack()

    def query(self, *, fresh=False) -> ShellContext | None:
        with self._lock:
            if self._closed:
                return None
            owner, generation = self._owner, self._generation
            capabilities, legacy = self._capabilities, self._legacy
            if owner == "":
                return ShellContext()
            if (not fresh and self._payload is not None
                    and 0 <= self._clock() - self._received <= MAX_AGE_SEC):
                return self._payload
        if owner is None:
            recovered = self._lookup_owner()
            with self._lock:
                if self._closed or self._generation != generation or recovered is None:
                    return None
                self._owner = recovered
                self._generation += 1
            self._invalidate()
            return self.query(fresh=fresh)
        if owner and capabilities is None:
            try:
                values = self._call(owner, "GetCapabilities", "(as)")[0]
                capabilities = tuple(values) if (isinstance(values, (tuple, list))
                    and len(values) <= 32 and all(type(s) is str and len(s) <= 128
                                                 for s in values)) else ()
            except Exception as exc:
                if self._error(exc) not in {
                    "org.freedesktop.DBus.Error.UnknownMethod",
                    "org.freedesktop.DBus.Error.UnknownInterface"}:
                    return None
                capabilities = ()
            with self._lock:
                if self._closed or self._generation != generation:
                    return None
                self._capabilities = capabilities
        try:
            if legacy:
                ids = self._call(owner, "GetFocusedApp", "(as)")[0]
                result = _context((ids, False, False, 0, 0, 0))
            else:
                result = _context(self._call(owner, "GetFocusedContext", "(asbbuuu)"))
        except Exception as exc:
            error = self._error(exc)
            if error in {"org.freedesktop.DBus.Error.UnknownMethod",
                         "org.freedesktop.DBus.Error.UnknownInterface"} and not legacy:
                try:
                    ids = self._call(owner, "GetFocusedApp", "(as)")[0]
                    result = _context((ids, False, False, 0, 0, 0))
                    legacy = True
                except Exception as legacy_error:
                    result = ShellContext() if self._error(legacy_error) in {
                        "org.freedesktop.DBus.Error.UnknownMethod",
                        "org.freedesktop.DBus.Error.UnknownInterface"} else None
                    legacy = result is not None
            elif legacy and error in {"org.freedesktop.DBus.Error.UnknownMethod",
                                       "org.freedesktop.DBus.Error.UnknownInterface"}:
                result = ShellContext()
            elif error in {"org.freedesktop.DBus.Error.ServiceUnknown",
                           "org.freedesktop.DBus.Error.NameHasNoOwner"}:
                # The owner signal, not a failed call to an old unique name, proves absence.
                result = ShellContext() if owner is None else None
            else:
                result = None
        with self._lock:
            if self._closed or self._generation != generation:
                return None
            self._legacy = legacy
            return result

    def close(self):
        with self._lock:
            self._closed = True
            self._payload = None
            self._generation += 1
        for subscription in self._subscriptions:
            self._bus.signal_unsubscribe(subscription)
        self._subscriptions.clear()
        if self._handler is not None:
            self._bus.disconnect(self._handler)
            self._handler = None
