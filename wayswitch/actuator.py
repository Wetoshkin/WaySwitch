"""План исправления и его исполнение.

План строится без железа (тестируется), исполняется либо через uinput
(KeyTypist), либо через расширение GNOME Shell (TextTypist). Инвариант:
на любом выходе из execute все клавиши, нажатые виртуальным устройством,
отпущены.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from wayswitch import keycodes as kc
from wayswitch.keymap import Keymap

DONE, ABORTED, FAILED = "done", "aborted", "failed"
ALL_MODIFIERS = (kc.KEY_LEFTSHIFT, kc.KEY_RIGHTSHIFT, kc.KEY_LEFTCTRL, kc.KEY_RIGHTCTRL,
                 kc.KEY_LEFTALT, kc.KEY_RIGHTALT, kc.KEY_LEFTMETA, kc.KEY_RIGHTMETA)


@dataclass(frozen=True)
class ReleaseModifiers:
    pass


@dataclass(frozen=True)
class Backspace:
    count: int


@dataclass(frozen=True)
class SwitchLayout:
    group: int


@dataclass(frozen=True)
class Type:
    text: str
    keys: list[tuple[int, bool]]


@dataclass
class Plan:
    steps: list = field(default_factory=list)


class KeyTypist(Protocol):
    def key(self, code: int, value: int) -> None: ...
    def syn(self) -> None: ...


@runtime_checkable
class TextTypist(Protocol):
    def backspace(self, count: int) -> None: ...
    def type_text(self, text: str) -> None: ...


def keys_for_typing(text: str, group: int, keymap: Keymap,
                    caps_on: bool) -> list[tuple[int, bool]] | None:
    """Символы → (код, Shift) в группе; при CapsLock Shift у букв инвертируется."""
    keys = []
    for ch in text:
        r = keymap.key_for(ch, group)
        if r is None:
            return None
        code, shift = r
        if caps_on and ch.isalpha():
            shift = not shift
        keys.append((code, shift))
    return keys


def plan_fix(delete_count: int, target_text: str, target_group: int, keymap: Keymap,
             caps_on: bool, switch: bool = True) -> Plan | None:
    """План: отпустить модификаторы, стереть, (переключить), набрать текст в target_group."""
    keys = keys_for_typing(target_text, target_group, keymap, caps_on)
    if keys is None:
        return None
    steps: list = [ReleaseModifiers()]
    if delete_count > 0:
        steps.append(Backspace(delete_count))
    if switch:
        steps.append(SwitchLayout(target_group))
    if target_text:
        steps.append(Type(target_text, keys))
    return Plan(steps)


class _Session:
    """Учёт нажатых виртуальных клавиш для гарантированного отпускания."""

    def __init__(self, typist: KeyTypist, key_delay: float, sleep):
        self.typist = typist
        self.key_delay = key_delay
        self.sleep = sleep
        self.pressed: list[int] = []

    def press(self, code: int) -> None:
        self.pressed.append(code)
        self.typist.key(code, 1)
        self.typist.syn()
        if self.key_delay > 0:
            self.sleep(self.key_delay)

    def release(self, code: int) -> None:
        self.typist.key(code, 0)
        self.typist.syn()
        if code in self.pressed:
            self.pressed.remove(code)
        if self.key_delay > 0:
            self.sleep(self.key_delay)

    def release_all(self) -> None:
        """Отпустить всё нажатое, по возможности; ошибка отдельной клавиши не прерывает цикл."""
        for code in reversed(list(self.pressed)):
            try:
                self.typist.key(code, 0)
                self.typist.syn()
            except OSError:
                pass
        self.pressed.clear()


def execute(plan: Plan, typist, backend, abort: Callable[[], bool] = lambda: False,
            key_delay: float = 0.0, sleep=time.sleep, switch_timeout: float = 0.5) -> str:
    if isinstance(typist, TextTypist):
        return _execute_text(plan, typist, backend, abort, switch_timeout)
    session = _Session(typist, key_delay, sleep)
    try:
        for step in plan.steps:
            if abort():
                return ABORTED
            if isinstance(step, ReleaseModifiers):
                for code in ALL_MODIFIERS:
                    typist.key(code, 0)
                typist.syn()
            elif isinstance(step, Backspace):
                for _ in range(step.count):
                    if abort():
                        return ABORTED
                    session.press(kc.KEY_BACKSPACE)
                    session.release(kc.KEY_BACKSPACE)
            elif isinstance(step, SwitchLayout):
                backend.set(step.group)
                if not backend.wait_applied(step.group, switch_timeout):
                    return FAILED
            elif isinstance(step, Type):
                for code, shift in step.keys:
                    if abort():
                        return ABORTED
                    if shift:
                        session.press(kc.KEY_LEFTSHIFT)
                    session.press(code)
                    session.release(code)
                    if shift:
                        session.release(kc.KEY_LEFTSHIFT)
        return DONE
    except OSError:
        return FAILED
    finally:
        session.release_all()


def _execute_text(plan: Plan, typist: TextTypist, backend, abort, switch_timeout: float) -> str:
    try:
        for step in plan.steps:
            if abort():
                return ABORTED
            if isinstance(step, Backspace):
                typist.backspace(step.count)
            elif isinstance(step, SwitchLayout):
                backend.set(step.group)
                if not backend.wait_applied(step.group, switch_timeout):
                    return FAILED
            elif isinstance(step, Type):
                typist.type_text(step.text)
        return DONE
    except OSError:
        return FAILED


class UInputTypist:
    """Виртуальная клавиатура через /dev/uinput."""

    NAME = "WaySwitch Virtual Keyboard"

    def __init__(self):
        from evdev import UInput, ecodes  # импорт здесь: модуль есть только на Linux

        self._ecodes = ecodes
        self._ui = UInput({ecodes.EV_KEY: list(range(1, 128))}, name=self.NAME)

    def key(self, code: int, value: int) -> None:
        self._ui.write(self._ecodes.EV_KEY, code, value)

    def syn(self) -> None:
        self._ui.syn()

    def close(self) -> None:
        self._ui.close()
