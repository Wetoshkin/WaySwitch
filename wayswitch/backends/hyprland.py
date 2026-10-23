"""Hyprland: раскладка через IPC композитора.

- ``.socket.sock`` — запросы: ``j/devices`` (клавиатуры и текущая раскладка),
  ``j/getoption input:kb_layout`` (список раскладок), ``switchxkblayout all N``;
- ``.socket2.sock`` — события строками, нам нужно ``activelayout>>клавиатура,описание``.

Печать — через uinput, как в KDE и Sway.
"""

from __future__ import annotations

import glob
import json
import os
import socket
import time
from collections.abc import Callable

from wayswitch.backends import xkbnames
from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.keymap import LayoutSpec


def find_dir(env=None) -> str | None:
    """Каталог сокетов текущего экземпляра Hyprland."""
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    sig = env.get("HYPRLAND_INSTANCE_SIGNATURE")
    if sig:
        path = os.path.join(runtime, "hypr", sig)
        return path if os.path.isdir(path) else None
    # Служба systemd могла стартовать без переменной: берём самый свежий экземпляр.
    found = sorted(glob.glob(os.path.join(runtime, "hypr", "*", ".socket.sock")),
                   key=os.path.getmtime)
    return os.path.dirname(found[-1]) if found else None


class HyprIpc:
    def __init__(self, directory: str):
        self.directory = directory

    def request(self, command: str) -> str:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(os.path.join(self.directory, ".socket.sock"))
            sock.sendall(command.encode("utf-8"))
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def json(self, command: str):
        return json.loads(self.request("j/" + command))

    def events(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(os.path.join(self.directory, ".socket2.sock"))
        return sock


def main_keyboard(devices: dict) -> dict | None:
    keyboards = devices.get("keyboards") or []
    for kb in keyboards:
        if kb.get("main"):
            return kb
    return keyboards[0] if keyboards else None


def index_of(spec: LayoutSpec | None, specs: list[LayoutSpec]) -> int | None:
    if spec is None:
        return None
    for i, s in enumerate(specs):
        if s == spec:
            return i
    for i, s in enumerate(specs):  # вариант мог не совпасть по написанию
        if s.layout == spec.layout:
            return i
    return None


def parse_event(line: str) -> tuple[str, str] | None:
    """«activelayout>>kbd,Russian» → ("kbd", "Russian")."""
    name, sep, data = line.partition(">>")
    if name != "activelayout" or not sep:
        return None
    keyboard, _, layout = data.partition(",")
    return keyboard, layout


class HyprlandBackend(LayoutBackend):
    name = "hyprland"
    supports_auto = True

    def __init__(self, settle_ms: int = 120, ipc=None, watch=None, rules=None):
        self._settle = settle_ms
        self._rules = rules
        if ipc is None:
            directory = find_dir()
            if directory is None:
                raise BackendError("нет сокета Hyprland (HYPRLAND_INSTANCE_SIGNATURE)")
            ipc = HyprIpc(directory)
        self._ipc = ipc
        try:
            layout = ipc.json("getoption input:kb_layout").get("str", "")
            variant = ipc.json("getoption input:kb_variant").get("str", "")
            options = ipc.json("getoption input:kb_options").get("str", "")
        except (OSError, ValueError, AttributeError) as e:
            raise BackendError(f"Hyprland не отвечает: {e}") from None
        self._specs = xkbnames.parse_layout_list(_clean(layout), _clean(variant))
        if len(self._specs) < 2:
            raise BackendError("в Hyprland меньше двух раскладок (input:kb_layout)")
        self._options = [o for o in _clean(options).split(",") if o]
        self._expected: int | None = None
        self._callbacks: list[Callable[[int, bool], None]] = []
        self._current = self._query()
        self._unwatch = (watch or _glib_watch)(self._ipc, self._on_line)

    def _index_of_desc(self, desc: str) -> int | None:
        return index_of(xkbnames.spec_for_description(desc, self._rules), self._specs)

    def _query(self) -> int | None:
        try:
            kb = main_keyboard(self._ipc.json("devices"))
        except (OSError, ValueError):
            return None
        if kb is None:
            return None
        if isinstance(kb.get("active_layout_index"), int):
            return kb["active_layout_index"]
        return self._index_of_desc(str(kb.get("active_keymap") or ""))

    def layouts(self) -> list[LayoutSpec]:
        return list(self._specs)

    def xkb_options(self) -> list[str]:
        return list(self._options)

    def current(self) -> int | None:
        return self._current

    def set(self, index: int) -> None:
        self._expected = index
        try:
            reply = self._ipc.request(f"switchxkblayout all {int(index)}")
        except OSError as e:
            self._expected = None
            raise BackendError(f"Hyprland: переключение не удалось: {e}") from None
        if reply.strip() != "ok":
            self._expected = None
            raise BackendError(f"Hyprland отказал в переключении: {reply.strip()}")
        self._current = index

    def wait_applied(self, index: int, timeout: float) -> bool:
        if self._settle > 0:
            time.sleep(self._settle / 1000)
        self._current = self._query()
        return self._current == index

    def on_change(self, cb: Callable[[int, bool], None]) -> None:
        self._callbacks.append(cb)

    def _on_line(self, line: str) -> None:
        parsed = parse_event(line)
        if parsed is None:
            return
        index = self._index_of_desc(parsed[1])
        if index is None or index == self._current and self._expected is None:
            return
        external = index != self._expected
        self._expected = None
        self._current = index
        for cb in self._callbacks:
            cb(index, external)

    def close(self) -> None:
        if self._unwatch is not None:
            self._unwatch()


def _clean(value: str) -> str:
    # getoption отдаёт "[[EMPTY]]" для пустых строковых опций.
    return "" if value.strip() in ("[[EMPTY]]", "") else value.strip()


def _glib_watch(ipc: HyprIpc, on_line):
    from gi.repository import GLib

    sock = ipc.events()
    buf = {"data": b""}

    def readable(_fd, _cond):
        try:
            chunk = sock.recv(65536)
        except OSError:
            return GLib.SOURCE_REMOVE
        if not chunk:
            return GLib.SOURCE_REMOVE
        buf["data"] += chunk
        *lines, buf["data"] = buf["data"].split(b"\n")
        for line in lines:
            on_line(line.decode("utf-8", errors="replace"))
        return GLib.SOURCE_CONTINUE

    source = GLib.io_add_watch(sock.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, readable)

    def stop():
        GLib.source_remove(source)
        sock.close()

    return stop


def available() -> bool:
    return find_dir() is not None
