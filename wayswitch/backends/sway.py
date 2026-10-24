"""Sway: раскладка через IPC композитора (протокол i3-ipc).

- GET_INPUTS — раскладки клавиатуры (описаниями XKB) и индекс текущей;
- COMMAND ``input type:keyboard xkb_switch_layout N`` — переключить;
- SUBSCRIBE ``["input"]`` — событие ``xkb_layout``, когда раскладку сменили.

Печать — через uinput, как в KDE: сканкоды ложатся по раскладке, которую
композитор уже применил. Расширения и IBus не нужны.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import socket
import struct
import time
from collections.abc import Callable

from wayswitch.backends import xkbnames
from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.keymap import LayoutSpec

MAGIC = b"i3-ipc"
HEADER = struct.Struct("=6sII")
RUN_COMMAND, SUBSCRIBE, GET_INPUTS = 0, 2, 100
EVENT_INPUT = 0x80000015
log = logging.getLogger("wayswitch")


def _alive(path: str) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(path)
        return True
    except OSError:
        return False


def find_socket(env=None, alive=_alive) -> str | None:
    """$SWAYSOCK, а если он устарел или служба запущена без него — живой сокет
    в XDG_RUNTIME_DIR (каталог 0700 — чужих сокетов там нет)."""
    env = os.environ if env is None else env
    if env.get("SWAYSOCK") and alive(env["SWAYSOCK"]):
        return env["SWAYSOCK"]
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    found = sorted(glob.glob(os.path.join(runtime, "sway-ipc.*.sock")), key=os.path.getmtime)
    for path in reversed(found):
        if alive(path):
            return path
    return None


def pack(msg_type: int, payload: str = "") -> bytes:
    body = payload.encode("utf-8")
    return HEADER.pack(MAGIC, len(body), msg_type) + body


def _recv_exact(sock, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("sway закрыл сокет")
        data += chunk
    return data


def read_message(sock) -> tuple[int, object]:
    magic, length, msg_type = HEADER.unpack(_recv_exact(sock, HEADER.size))
    if magic != MAGIC:
        raise BackendError("не i3-ipc: неверная сигнатура ответа sway")
    return msg_type, json.loads(_recv_exact(sock, length) or b"null")


class SwayIpc:
    def __init__(self, path: str):
        self.path = path

    def request(self, msg_type: int, payload: str = ""):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(self.path)
            sock.sendall(pack(msg_type, payload))
            _type, data = read_message(sock)
            return data

    def subscribe(self, events: list[str]):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.path)
        sock.sendall(pack(SUBSCRIBE, json.dumps(events)))
        _type, reply = read_message(sock)
        if not (isinstance(reply, dict) and reply.get("success")):
            sock.close()
            raise BackendError("sway не принял подписку на события input")
        return sock


VIRTUAL = "WaySwitch Virtual Keyboard"


def main_keyboard(inputs: list[dict], identifier: str | None = None) -> dict | None:
    """Клавиатура, за которой следим: заданная или первая настоящая с ≥2 раскладками."""
    for dev in inputs or []:
        if dev.get("type") != "keyboard" or dev.get("name") == VIRTUAL:
            continue
        if identifier is not None and dev.get("identifier") != identifier:
            continue
        if len(dev.get("xkb_layout_names") or []) >= 2:
            return dev
    return None


def keyboard_state(inputs: list[dict], identifier: str | None = None
                   ) -> tuple[list[str], int | None]:
    """Описания раскладок и индекс текущей у отслеживаемой клавиатуры."""
    dev = main_keyboard(inputs, identifier)
    if dev is None:
        return [], None
    return list(dev.get("xkb_layout_names") or []), dev.get("xkb_active_layout_index")


def specs_from_names(names: list[str], rules=None) -> list[LayoutSpec]:
    specs = []
    for name in names:
        spec = xkbnames.spec_for_description(name, rules)
        if spec is None:
            raise BackendError(f"раскладка «{name}» не найдена в базе XKB")
        specs.append(spec)
    return specs


class SwayBackend(LayoutBackend):
    name = "sway"
    supports_auto = True
    always_switch = True

    def __init__(self, settle_ms: int = 120, ipc=None, watch=None, rules=None):
        self._settle = settle_ms
        if ipc is None:
            path = find_socket()
            if path is None:
                raise BackendError("нет сокета sway (SWAYSOCK)")
            ipc = SwayIpc(path)
        self._ipc = ipc
        try:
            inputs = self._ipc.request(GET_INPUTS)
        except (OSError, ValueError) as e:
            raise BackendError(f"sway не отвечает: {e}") from None
        main = main_keyboard(inputs)
        self._kbd = main.get("identifier") if main else None
        names, index = keyboard_state(inputs, self._kbd)
        if len(names) < 2:
            raise BackendError("в sway у клавиатуры меньше двух раскладок (xkb_layout)")
        self._specs = specs_from_names(names, rules)
        self._current = index
        self._expected: int | None = None
        self._callbacks: list[Callable[[int, bool], None]] = []
        self._unwatch = (watch or _glib_watch)(self._ipc, self._on_event)

    def layouts(self) -> list[LayoutSpec]:
        return list(self._specs)

    def xkb_options(self) -> list[str]:
        return []

    def current(self) -> int | None:
        return self._current

    def _query(self) -> int | None:
        try:
            _names, index = keyboard_state(self._ipc.request(GET_INPUTS), self._kbd)
        except (OSError, ValueError):
            return None
        return index

    def set(self, index: int) -> None:
        self._expected = index
        try:
            reply = self._ipc.request(RUN_COMMAND,
                                      f"input type:keyboard xkb_switch_layout {int(index)}")
        except (OSError, ValueError) as e:
            self._expected = None
            raise BackendError(f"sway: переключение не удалось: {e}") from None
        if not (isinstance(reply, list) and reply and reply[0].get("success")):
            self._expected = None
            raise BackendError(f"sway отказал в переключении: {reply}")
        self._current = index

    def wait_applied(self, index: int, timeout: float) -> bool:
        if self._settle > 0:
            time.sleep(self._settle / 1000)
        self._current = self._query()
        return self._current == index

    def on_change(self, cb: Callable[[int, bool], None]) -> None:
        self._callbacks.append(cb)

    def _on_event(self, event: dict) -> None:
        dev = event.get("input") or {}
        if dev.get("identifier") == "*restart*" or (
                event.get("change") == "xkb_keymap" and dev.get("identifier") == self._kbd):
            self.layouts_changed()  # поменяли раскладки в конфиге sway
            return
        if event.get("change") != "xkb_layout":
            return
        if self._kbd is not None and dev.get("identifier") != self._kbd:
            return  # другая клавиатура (у неё может быть своя раскладка)
        index = dev.get("xkb_active_layout_index")
        if not isinstance(index, int) or index == self._current and self._expected is None:
            return
        external = index != self._expected
        self._expected = None
        self._current = index
        for cb in self._callbacks:
            cb(index, external)

    def close(self) -> None:
        if self._unwatch is not None:
            self._unwatch()


def _glib_watch(ipc: SwayIpc, on_event):
    """Подписка на события input в главном цикле GLib."""
    from gi.repository import GLib

    sock = ipc.subscribe(["input"])

    def readable(_fd, _cond):
        try:
            msg_type, data = read_message(sock)
        except (OSError, ValueError, BackendError) as exc:
            # sway перезапустили или закрыли сокет: состояние раскладки больше
            # не обновится — перезапуск службы подключится к новому сокету.
            log.warning("события sway больше не приходят: %s", exc)
            on_event({"change": "xkb_keymap", "input": {"identifier": "*restart*"}})
            return GLib.SOURCE_REMOVE
        if msg_type == EVENT_INPUT and isinstance(data, dict):
            on_event(data)
        return GLib.SOURCE_CONTINUE

    source = GLib.io_add_watch(sock.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, readable)

    def stop():
        GLib.source_remove(source)
        sock.close()

    return stop


def available() -> bool:
    return find_socket() is not None
