"""Резерв: эмуляция системного хоткея смены раскладки через uinput.

Состояние ведём счётчиком, поэтому оно ненадёжно — авторежим выключен,
ручной жест работает. Физические нажатия того же хоткея учитываем.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from wayswitch.backends import gnome_common
from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.keymap import LayoutSpec


class HotkeyBackend(LayoutBackend):
    name = "hotkey"
    supports_auto = False

    def __init__(self, typist, settle_ms: int = 30, combo: list[int] | None = None,
                 specs: list[LayoutSpec] | None = None):
        self._typist = typist
        self._settle = max(settle_ms, 100) / 1000.0
        self._specs = specs or gnome_common.read_sources()
        self._combo = combo if combo is not None else gnome_common.read_switch_binding()
        if not self._combo:
            raise BackendError("системный хоткей смены раскладки не задан")
        self._current = 0  # при входе GNOME включает первый источник
        self._callbacks: list[Callable[[int, bool], None]] = []
        self._held: set[int] = set()

    def layouts(self) -> list[LayoutSpec]:
        return list(self._specs)

    def xkb_options(self) -> list[str]:
        return gnome_common.read_xkb_options()

    def current(self) -> int | None:
        return self._current

    def _advance(self, external: bool) -> None:
        self._current = (self._current + 1) % len(self._specs)
        for cb in self._callbacks:
            cb(self._current, external)

    def observe_physical(self, code: int, value: int) -> None:
        """Пользователь нажал системный хоткей сам — сдвинуть счётчик."""
        if value == 1:
            self._held.add(code)
            if code == self._combo[-1] and set(self._combo) <= self._held:
                self._advance(external=True)
        elif value == 0:
            self._held.discard(code)

    def set(self, index: int) -> None:
        while self._current != index:
            for code in self._combo:
                self._typist.key(code, 1)
            self._typist.syn()
            time.sleep(0.03)
            for code in self._combo:  # тот же порядок: модификатор отпускаем первым
                self._typist.key(code, 0)
            self._typist.syn()
            self._advance(external=False)
            time.sleep(self._settle)

    def wait_applied(self, index: int, timeout: float) -> bool:
        return self._current == index

    def on_change(self, cb: Callable[[int, bool], None]) -> None:
        self._callbacks.append(cb)
