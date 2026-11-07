"""Umbriel newline-JSON IPC; normal keyboards and uinput must share layout names.

Protocol virtual keyboards/EIS retain their own maps. IPC omits XKB options and
device identity, so arbitrary per-device remaps cannot be verified here.
"""

from __future__ import annotations

import json
import os
import socket
import time

from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.backends.sway import specs_from_names

MAX_FRAME = 65536
TIMEOUT = 2.0
QUERY = {"cmd": "keyboard-layouts"}
NEXT = {"cmd": "action", "action": "keyboard-layout-next"}


def find_socket(env=None) -> str | None:
    env = os.environ if env is None else env
    if env.get("UMBRIEL_SOCKET"):
        return env["UMBRIEL_SOCKET"]
    runtime, display = env.get("XDG_RUNTIME_DIR"), env.get("WAYLAND_DISPLAY")
    if runtime and display and "/" not in display and "\\" not in display:
        return f"{runtime.rstrip('/')}/umbriel-{display}.sock"
    return None


def keyboard_state(reply) -> tuple[list[str], int]:
    """Validate the raw query reply without echoing arbitrary compositor payloads."""
    if not isinstance(reply, dict) or set(reply) != {"ok"}:
        raise BackendError("Umbriel: ответ состояния раскладки недоступен")
    data = reply["ok"]
    if not isinstance(data, dict):
        raise BackendError("Umbriel: неверное состояние раскладки")
    names, index = data.get("names"), data.get("current_index")
    if (not isinstance(names, list) or not 1 <= len(names) <= 8
            or any(not isinstance(n, str) or not n.strip() or len(n) > 256 for n in names)
            or len(set(names)) != len(names) or type(index) is not int
            or not 0 <= index < len(names)):
        raise BackendError("Umbriel: неверные имена или индекс раскладки")
    return list(names), index


def query_input_locked(ipc) -> bool:
    """Query only the compositor activity gate, without granting field access."""
    try:
        reply = ipc.request({"cmd": "focused-window"}, timeout=.05)
        return not (isinstance(reply, dict) and set(reply) == {"ok"}
                    and isinstance(reply["ok"], dict) and reply["ok"].get("active") is True)
    except Exception:  # noqa: BLE001 — unknown compositor focus must block input
        return True


def _decode(line):
    try:
        if not line or len(line) > MAX_FRAME:
            raise ValueError
        result = json.loads(line.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, RecursionError) as exc:
        raise BackendError("Umbriel: неверный или слишком большой JSON-ответ") from exc


def _read_initial(sock, timeout):
    deadline = time.monotonic() + timeout
    data = bytearray()
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Umbriel IPC timeout")
        sock.settimeout(remaining)
        chunk = sock.recv(4096)
        if not chunk:
            raise BackendError("Umbriel: соединение закрыто до полного ответа")
        data.extend(chunk)
        if len(data.split(b"\n", 1)[0]) > MAX_FRAME:
            raise BackendError("Umbriel: слишком большой ответ")
    line, rest = data.split(b"\n", 1)
    return _decode(line), bytearray(rest)


class UmbrielIpc:
    def __init__(self, path: str):
        self.path = path

    def _connect(self, payload, timeout):
        body = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
        if len(body) > MAX_FRAME or timeout <= 0:
            raise BackendError("Umbriel: недопустимый запрос")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            deadline = time.monotonic() + timeout
            sock.settimeout(timeout)
            sock.connect(self.path)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Umbriel IPC timeout")
            sock.settimeout(remaining)
            sock.sendall(body)
            return sock
        except Exception:
            sock.close()
            raise

    def request(self, payload: dict, timeout: float = TIMEOUT) -> dict:
        """Return the raw {ok: ...}/{err: ...} object, never an optimistic state."""
        deadline = time.monotonic() + timeout
        with self._connect(payload, timeout) as sock:
            result, rest = _read_initial(sock, max(0, deadline - time.monotonic()))
            if rest.strip():
                raise BackendError("Umbriel: лишние данные после ответа")
            return result

    def subscribe(self):
        return self._connect({"cmd": "subscribe", "events": ["keyboard_layout"]}, TIMEOUT)


class UmbrielBackend(LayoutBackend):
    name = "umbriel"
    supports_auto = True
    always_switch = True

    def __init__(self, *, ipc=None, watch=None, rules=None):
        self._callbacks = []
        self._expected = None
        self._unwatch = None
        self._closed = False
        self._valid = True
        if ipc is None:
            path = find_socket()
            if path is None:
                raise BackendError("Umbriel: сокет UMBRIEL_SOCKET не найден")
            ipc = UmbrielIpc(path)
        self._ipc = ipc
        try:
            self._names, self._current = keyboard_state(ipc.request(QUERY))
            if len(self._names) != 2:
                raise BackendError("Umbriel: нужны ровно две раскладки")
            self._specs = specs_from_names(self._names, rules)
            self._unwatch = (watch or _glib_watch)(ipc, self._on_event)
            if not self._valid:
                raise BackendError("Umbriel: подписка состояния недостоверна")
        except Exception as exc:
            self.close()
            if isinstance(exc, BackendError):
                raise
            raise BackendError("Umbriel: IPC или подписка недоступны") from exc

    def layouts(self):
        return list(self._specs)

    def xkb_options(self):
        return None

    def current(self):
        return self._current

    def input_locked(self) -> bool:
        """Fresh lock gate only; an active window never replaces the field safety check."""
        if not self._valid or self._closed:
            return True
        locked = query_input_locked(self._ipc)
        return locked or not self._valid or self._closed

    def on_change(self, callback):
        self._callbacks.append(callback)

    def _invalidate(self):
        if not self._valid or self._closed:
            return
        self._valid = False
        self._current = self._expected = None
        if self._unwatch is not None:
            self._unwatch()
            self._unwatch = None
        for callback in self._callbacks:
            callback(None, True)
        self.layouts_changed()

    def _accept(self, names, index, event=False):
        if names != self._names:
            self._invalidate()
            raise BackendError("Umbriel: карта раскладок изменилась")
        if event and self._expected is not None and index != self._expected:
            # IPC has no sequence number to distinguish an old event from a user switch.
            self._invalidate()
            return
        external = index != self._expected
        changed = index != self._current
        self._current = index
        if event and index == self._expected:
            self._expected = None
        if changed:
            for callback in self._callbacks:
                callback(index, external)

    def _refresh(self, timeout=TIMEOUT):
        if not self._valid or self._closed:
            raise BackendError("Umbriel: состояние раскладки недостоверно")
        try:
            names, index = keyboard_state(self._ipc.request(QUERY, timeout=timeout))
            self._accept(names, index)
            return index
        except Exception as exc:
            self._invalidate()
            raise BackendError("Umbriel: текущее состояние недоступно") from exc

    def _target(self, index):
        return type(index) is int and 0 <= index < len(self._names)

    def set(self, index):
        if not self._target(index):
            raise BackendError("Umbriel: неверный индекс раскладки")
        deadline = time.monotonic() + TIMEOUT
        if self._refresh() == index:
            return
        self._expected = index
        try:
            reply = self._ipc.request(NEXT, timeout=deadline - time.monotonic())
            if reply != {"ok": None}:
                raise BackendError("Umbriel: действие переключения отклонено")
            self._refresh(timeout=deadline - time.monotonic())
        except Exception:
            self._invalidate()
            raise BackendError("Umbriel: переключение не подтверждено") from None

    def wait_applied(self, index, timeout):
        if not self._target(index) or not self._valid or self._closed:
            return False
        deadline = time.monotonic() + max(0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._expected = None
                return False
            try:
                if self._refresh(timeout=min(TIMEOUT, remaining)) == index:
                    return True
            except BackendError:
                return False
            time.sleep(min(.01, max(0, deadline - time.monotonic())))

    def _on_event(self, payload):
        if self._closed or not self._valid:
            return
        try:
            if not isinstance(payload, dict) or payload.get("event") != "keyboard_layout":
                raise BackendError("Umbriel: подписка прервана")
            self._accept(*keyboard_state({"ok": payload.get("data")}), event=True)
        except BackendError:
            self._invalidate()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._valid = False
        self._current = self._expected = None
        if self._unwatch is not None:
            self._unwatch()
            self._unwatch = None


def _glib_watch(ipc, on_event):
    """Only setup waits for the initial snapshot; GLib callbacks never block."""
    from gi.repository import GLib

    sock = ipc.subscribe()
    source = None
    closed = False

    def stop():
        nonlocal closed
        if closed:
            return
        closed = True
        if source is not None:
            GLib.source_remove(source)
        sock.close()

    def failed():
        stop()
        on_event(None)
        return GLib.SOURCE_REMOVE

    try:
        initial, pending = _read_initial(sock, TIMEOUT)
        on_event(initial)
        sock.setblocking(False)

        def drain():
            for _ in range(64):
                if b"\n" not in pending:
                    if len(pending) > MAX_FRAME:
                        raise BackendError("Umbriel: слишком большое событие")
                    return
                line, rest = pending.split(b"\n", 1)
                pending[:] = rest
                on_event(_decode(line))
            raise BackendError("Umbriel: слишком много событий")

        drain()

        def readable(_fd, _condition):
            if closed:
                return GLib.SOURCE_REMOVE
            try:
                for _ in range(16):
                    try:
                        chunk = sock.recv(4096)
                    except BlockingIOError:
                        return GLib.SOURCE_CONTINUE
                    if not chunk:
                        return failed()
                    pending.extend(chunk)
                    drain()
                    if closed:
                        return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE
            except (OSError, BackendError):
                return failed()

        source = GLib.io_add_watch(sock.fileno(), GLib.PRIORITY_DEFAULT,
                                   GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR | GLib.IO_NVAL,
                                   readable)
        return stop
    except Exception:
        stop()
        raise
