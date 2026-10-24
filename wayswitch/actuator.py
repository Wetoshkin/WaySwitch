"""План исправления и его исполнение.

План строится без железа (тестируется), исполняется либо через uinput
(KeyTypist), либо через расширение GNOME Shell (TextTypist). Инварианты:

* на любом выходе из execute все клавиши, нажатые виртуальным устройством,
  отпущены;
* первый Backspace — точка невозврата. До него прерывание (физическое
  нажатие, пауза) отменяет исправление и возвращает прежнюю раскладку; после
  него план доводится до конца, иначе человек остался бы со стёртым словом и
  без нового. Нажатая в это время клавиша всё равно попадёт в поле — после
  исправленного слова;
* любая ошибка бэкенда или печати — FAILED, а не исключение наружу.
"""

from __future__ import annotations

import logging
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
class ToggleCaps:
    """Нажать Caps Lock: выключить нечаянно включённый (правило «пРИВЕТ»)."""


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
             caps_on: bool, switch: bool = True, caps_off: bool = False) -> Plan | None:
    """План: отпустить модификаторы, (переключить раскладку), стереть, набрать.

    Переключение — ДО Backspace-ов: если бэкенд не смог сменить раскладку
    (IBus на GNOME 46+ меняет движок, но не раскладку компоузитора), execute
    вернёт FAILED, и слово останется на экране целым. В обратном порядке текст
    был бы уже стёрт без возможности его вернуть.
    """
    # caps_off: Caps Lock выключим до печати — набираем как без него.
    keys = keys_for_typing(target_text, target_group, keymap, caps_on and not caps_off)
    if keys is None:
        return None
    steps: list = [ReleaseModifiers()]
    if switch:
        steps.append(SwitchLayout(target_group))
    if caps_off:
        steps.append(ToggleCaps())
    if delete_count > 0:
        steps.append(Backspace(delete_count))
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


log = logging.getLogger("wayswitch")


class _Run:
    """Общее для обоих исполнителей: точка невозврата и возврат раскладки."""

    def __init__(self, backend, abort: Callable[[], bool], restore_group: int | None,
                 hold: Callable[[], bool] | None = None):
        self.backend = backend
        self._abort = abort
        self._hold = hold
        self._gave_up = False  # после точки невозврата ждём Shift один раз
        self.restore_group = restore_group
        self.switched = False
        self.caps_toggle: Callable[[], None] | None = None  # чем вернуть Caps Lock
        self.committed = False  # первый Backspace ушёл — назад дороги нет

    def aborted(self) -> bool:
        return not self.committed and self._abort()

    def blocked(self) -> bool:
        """Перед стирающей или печатающей клавишей: зажат физический Shift.

        Модификаторы в Mutter/KWin общие для всех устройств сеанса — буквы
        вышли бы заглавными. hold() ждёт отпускания; не дождались до точки
        невозврата — прерываемся, после неё печатаем как есть.
        """
        if self._hold is None or self._gave_up or self._hold():
            return False
        if self.committed:
            # Текст уже меняется: дописываем как есть, не замирая на каждой
            # клавише ещё на RELEASE_WAIT.
            self._gave_up = True
            return False
        return True

    def switch(self, group: int, timeout: float) -> bool:
        self.switched = True
        self.backend.set(group)
        return bool(self.backend.wait_applied(group, timeout))

    def rollback(self) -> None:
        """Слово не тронуто, а раскладку уже переключили — вернуть прежнюю."""
        if self.caps_toggle is not None and not self.committed:
            try:
                self.caps_toggle()  # Caps Lock выключили зря — включить обратно
            except Exception as exc:  # noqa: BLE001 — откат по возможности
                log.warning("не удалось вернуть Caps Lock: %s", exc)
        if self.switched and not self.committed and self.restore_group is not None:
            try:
                self.backend.set(self.restore_group)
            except Exception as exc:  # noqa: BLE001 — откат по возможности
                log.warning("не удалось вернуть раскладку: %s", exc)


def execute(plan: Plan, typist, backend, abort: Callable[[], bool] = lambda: False,
            key_delay: float = 0.0, sleep=time.sleep, switch_timeout: float = 0.5,
            restore_group: int | None = None,
            hold: Callable[[], bool] | None = None) -> str:
    run = _Run(backend, abort, restore_group, hold)
    if isinstance(typist, TextTypist):
        result = _execute_text(plan, typist, run, switch_timeout)
    else:
        result = _execute_keys(plan, typist, run, key_delay, sleep, switch_timeout)
    if result != DONE:
        run.rollback()
    return result


def _execute_keys(plan: Plan, typist, run: _Run, key_delay: float, sleep,
                  switch_timeout: float) -> str:
    session = _Session(typist, key_delay, sleep)
    try:
        for step in plan.steps:
            if run.aborted():
                return ABORTED
            if isinstance(step, ReleaseModifiers):
                for code in ALL_MODIFIERS:
                    typist.key(code, 0)
                typist.syn()
            elif isinstance(step, Backspace):
                for _ in range(step.count):
                    # blocked() — первым: пока ждём Shift, pump() мог принести
                    # нажатие, и aborted() должен его увидеть.
                    if run.blocked() or run.aborted():
                        return ABORTED
                    run.committed = True
                    session.press(kc.KEY_BACKSPACE)
                    session.release(kc.KEY_BACKSPACE)
            elif isinstance(step, SwitchLayout):
                if not run.switch(step.group, switch_timeout):
                    return FAILED
            elif isinstance(step, ToggleCaps):
                def tap_caps(s=session):
                    s.press(kc.KEY_CAPSLOCK)
                    s.release(kc.KEY_CAPSLOCK)
                tap_caps()
                run.caps_toggle = tap_caps
            elif isinstance(step, Type):
                for code, shift in step.keys:
                    if run.blocked() or run.aborted():
                        return ABORTED
                    # Первая напечатанная буква меняет экран, как и Backspace
                    # (у «Исправить выделенное» Backspace нет вовсе): дальше
                    # фразу не обрываем.
                    run.committed = True
                    if shift:
                        session.press(kc.KEY_LEFTSHIFT)
                    session.press(code)
                    session.release(code)
                    if shift:
                        session.release(kc.KEY_LEFTSHIFT)
        return DONE
    except Exception as exc:  # noqa: BLE001 — OSError uinput, BackendError, GLib.Error
        log.error("исправление прервано ошибкой: %s", exc)
        return FAILED
    finally:
        session.release_all()


def _execute_text(plan: Plan, typist: TextTypist, run: _Run, switch_timeout: float) -> str:
    step = None
    try:
        for step in plan.steps:
            if run.aborted():
                return ABORTED
            if isinstance(step, (Backspace, Type)) and (run.blocked() or run.aborted()):
                return ABORTED
            if isinstance(step, Backspace):
                run.committed = True
                typist.backspace(step.count)
            elif isinstance(step, SwitchLayout):
                if not run.switch(step.group, switch_timeout):
                    return FAILED
            elif isinstance(step, ToggleCaps):
                # Печать текстом от Caps Lock не зависит; выключаем, если
                # исполнитель умеет (у расширения GNOME — через uinput).
                toggle = getattr(typist, "toggle_caps", None)
                if toggle is not None:
                    toggle()
                    run.caps_toggle = toggle
            elif isinstance(step, Type):
                run.committed = True
                typist.type_text(step.text)
        return DONE
    except Exception as exc:  # noqa: BLE001 — GLib.Error таймаута D-Bus и прочее
        log.error("исправление прервано ошибкой: %s", exc)
        timed_out = "timed out" in str(exc).lower() or "timeout" in str(exc).lower()
        if run.committed and isinstance(step, Type) and not timed_out:
            # По таймауту не повторяем: расширение могло напечатать слово, а
            # ответ просто не успел — повтор задвоил бы его.
            # Слово уже стёрто: одна повторная попытка напечатать лучше, чем
            # оставить пустое место (таймаут D-Bus бывает разовым).
            try:
                typist.type_text(step.text)
                return DONE
            except Exception as again:  # noqa: BLE001
                log.error("повторная печать не удалась: %s", again)
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
