"""План исправления и его исполнение.

План строится без железа (тестируется), исполняется либо через uinput
(KeyTypist), либо через расширение GNOME Shell (TextTypist). Инварианты:

* на любом выходе из execute все клавиши, нажатые виртуальным устройством,
  отпущены;
* KeyTypist останавливается при наблюдаемом прерывании. После записи или
  использования нового режима физическим вводом результат — FAILED: часть
  слова могла измениться, восстанавливать прежний буфер и перепечатывать нельзя;
* проверка перед записью не атомарна: физическая клавиша может попасть между
  проверкой и записью. Это ограничение не устраняется задержкой;
* любая ошибка бэкенда или печати — FAILED, а не исключение наружу.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from wayswitch import keycodes as kc
from wayswitch.keymap import Keymap

if TYPE_CHECKING:
    from wayswitch.a11y import FocusSnapshot

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
    source_text: str | None = None
    focus: FocusSnapshot | None = None


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
                 hold: Callable[[], bool] | None, stop: Callable[[], bool],
                 before_write: Callable[[], bool] | None):
        self.backend = backend
        self._abort = abort
        self._stop = stop
        self._hold = hold
        self._before_write = before_write
        self.write_checked = before_write is None
        self._gave_up = False  # после точки невозврата ждём Shift один раз
        self.restore_group = restore_group
        self.switched = False
        self.caps_toggle: Callable[[], None] | None = None  # чем вернуть Caps Lock
        self.committed = False  # текст или новый режим уже использован, откат небезопасен
        self.native_done = False
        self.resolved_source: str | None = None
        self.resolved_target: str | None = None

    def aborted(self) -> bool:
        # _abort прокручивает GLib: и после первого Backspace нужны события lock/layout.
        return (self._abort() and not self.committed) or self._stop()

    def commit(self) -> None:
        self.committed = True

    def validate_write(self) -> bool:
        try:
            self.write_checked = self._before_write() is True
        finally:
            # Проверка может доставить физический ввод даже при отказе или ошибке.
            interrupted = self.native_aborted(before_write=True)
        return self.write_checked and not interrupted

    def native_aborted(self, *, before_write: bool = False) -> bool:
        interrupted = self._abort()
        stopped = self._stop()
        # Допечатанные символы уже используют новую раскладку или состояние Caps Lock.
        if interrupted and (self.switched or self.caps_toggle is not None) and not stopped:
            self.committed = True
        return (interrupted and (before_write or not self.committed)) or stopped

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
                self.caps_toggle = None
            except Exception as exc:  # noqa: BLE001 — откат по возможности
                log.warning("не удалось вернуть Caps Lock: %s", type(exc).__name__)
        if self.switched and not self.committed and self.restore_group is not None:
            try:
                self.backend.set(self.restore_group)
            except Exception as exc:  # noqa: BLE001 — откат по возможности
                log.warning("не удалось вернуть раскладку: %s", type(exc).__name__)


def execute(plan: Plan, typist, backend, abort: Callable[[], bool] = lambda: False,
            key_delay: float = 0.0, sleep=time.sleep, switch_timeout: float = 0.5,
            restore_group: int | None = None,
            hold: Callable[[], bool] | None = None,
            outcome: dict | None = None, stop: Callable[[], bool] = lambda: False,
            before_write: Callable[[], bool] | None = None) -> str:
    """before_write проверяется один раз перед первой записью, после ожиданий.

    outcome (если передан) получает caps_off: Caps Lock в итоге выключен нами.
    """
    run = _Run(backend, abort, restore_group, hold, stop, before_write)
    if isinstance(typist, TextTypist):
        result = _execute_text(plan, typist, run, switch_timeout)
    else:
        result = _execute_keys(plan, typist, run, key_delay, sleep, switch_timeout)
    if result != DONE and not stop():
        run.rollback()
    if outcome is not None:
        outcome["caps_off"] = run.caps_toggle is not None
        if run.native_done:
            outcome["native_span"] = True
            if run.resolved_source is not None:
                outcome["source_text"] = run.resolved_source
                outcome["target_text"] = run.resolved_target
    return result


def _execute_keys(plan: Plan, typist, run: _Run, key_delay: float, sleep,
                  switch_timeout: float) -> str:
    session = _Session(typist, key_delay, sleep)

    def interruption(check_hold: bool = False) -> str | None:
        blocked = False
        try:
            blocked = check_hold and run._hold is not None and run._hold() is not True
        finally:
            interrupted = run._abort()
            stopped = run._stop()
            # Физический ввод уже использовал новый режим, возвращать его нельзя.
            if interrupted and (run.switched or run.caps_toggle is not None
                                or set(session.pressed) & (kc.SHIFT_KEYS | {kc.KEY_CAPSLOCK})):
                run.commit()
        if blocked or interrupted or stopped:
            return FAILED if run.committed else ABORTED
        return None

    try:
        for step in plan.steps:
            if status := interruption():
                return status
            if isinstance(step, ReleaseModifiers):
                for code in ALL_MODIFIERS:
                    typist.key(code, 0)
                typist.syn()
            elif isinstance(step, Backspace):
                for _ in range(step.count):
                    if status := interruption(check_hold=True):
                        return status
                    if not run.write_checked:
                        if not run.validate_write():
                            return ABORTED
                        if status := interruption():
                            return status
                    run.committed = True
                    session.press(kc.KEY_BACKSPACE)
                    session.release(kc.KEY_BACKSPACE)
            elif isinstance(step, SwitchLayout):
                try:
                    applied = run.switch(step.group, switch_timeout)
                finally:
                    status = interruption()
                if status:
                    return status
                if not applied:
                    return FAILED
            elif isinstance(step, ToggleCaps):
                def tap_caps(s=session):
                    s.press(kc.KEY_CAPSLOCK)
                    s.release(kc.KEY_CAPSLOCK)
                try:
                    tap_caps()
                    run.caps_toggle = tap_caps
                finally:
                    status = interruption()
                if status:
                    return status
            elif isinstance(step, Type):
                for code, shift in step.keys:
                    if status := interruption(check_hold=True):
                        return status
                    if shift:
                        try:
                            session.press(kc.KEY_LEFTSHIFT)
                        finally:
                            status = interruption()
                        if status:
                            return status
                    if not run.write_checked:
                        if not run.validate_write():
                            return ABORTED
                        if status := interruption():
                            return status
                    run.committed = True
                    session.press(code)
                    session.release(code)
                    if shift:
                        session.release(kc.KEY_LEFTSHIFT)
        return interruption() or DONE
    except Exception as exc:  # noqa: BLE001 — OSError uinput, BackendError, GLib.Error
        log.error("исправление прервано ошибкой: %s", type(exc).__name__)
        return FAILED
    finally:
        session.release_all()


def _text_setup(step, typist, run: _Run, switch_timeout: float) -> bool:
    if isinstance(step, SwitchLayout):
        return run.switch(step.group, switch_timeout)
    if isinstance(step, ToggleCaps):
        toggle = getattr(typist, "toggle_caps", None)
        if toggle is not None and toggle() is not False:
            run.caps_toggle = toggle
    return True


def _execute_text(plan: Plan, typist: TextTypist, run: _Run, switch_timeout: float) -> str:
    step = None
    try:
        native = getattr(typist, "replace_span", None)
        snapshot = plan.focus
        if (native is not None and plan.source_text is not None
                and snapshot is not None and snapshot.app and snapshot.field
                and not snapshot.password and len(plan.steps) >= 2
                and isinstance(plan.steps[-2], Backspace) and isinstance(plan.steps[-1], Type)
                and all(isinstance(s, (ReleaseModifiers, SwitchLayout, ToggleCaps))
                        for s in plan.steps[:-2])):
            if len(plan.source_text) != plan.steps[-2].count:
                return ABORTED
            if run.blocked() or run.aborted():
                return ABORTED
            prepared = False

            def begin():
                nonlocal prepared
                for setup in plan.steps[:-2]:
                    if run.blocked() or run.native_aborted():
                        return False
                    try:
                        applied = _text_setup(setup, typist, run, switch_timeout)
                    finally:
                        interrupted = run.native_aborted()
                    if not applied:
                        raise RuntimeError("layout not applied")
                    if interrupted:
                        return False
                if run.native_aborted():
                    return False
                if not run.write_checked:
                    if (run.blocked() or run.native_aborted() or not run.validate_write()
                            or run.native_aborted(before_write=True)):
                        return False
                run.commit()
                prepared = True
                return True

            # Диапазон фиксируется до смены раскладки, допечатывание не сдвигает его.
            result = native(snapshot, plan.source_text, plan.steps[-1].text,
                            begin, run.native_aborted)
            if result is not None:
                run.committed |= result.committed
                if result.status == DONE and not prepared:
                    return FAILED
                if result.status == DONE:
                    source = getattr(result, "source_text", None)
                    target = getattr(result, "target_text", None)
                    if source is not None or target is not None:
                        if (not isinstance(source, str) or not isinstance(target, str)
                                or len(source) != len(plan.source_text)
                                or source.casefold() != plan.source_text.casefold()
                                or target.casefold() != plan.steps[-1].text.casefold()):
                            return FAILED
                        run.resolved_source, run.resolved_target = source, target
                run.native_done = result.status == DONE
                return result.status
            if run.committed or run.switched or run.caps_toggle is not None:
                return FAILED
            if run.blocked() or run.aborted():
                return ABORTED
        for step in plan.steps:
            if run.aborted():
                return ABORTED
            if isinstance(step, (Backspace, Type)) and (run.blocked() or run.aborted()):
                return ABORTED
            if isinstance(step, (Backspace, Type)) and not run.write_checked:
                if not run.validate_write() or run.native_aborted(before_write=True):
                    return ABORTED
            if isinstance(step, Backspace):
                run.committed = True
                typist.backspace(step.count)
            elif isinstance(step, Type):
                run.committed = True
                typist.type_text(step.text)
            elif not _text_setup(step, typist, run, switch_timeout):
                return FAILED
        return DONE
    except Exception as exc:  # noqa: BLE001 — GLib.Error таймаута D-Bus и прочее
        log.error("исправление прервано ошибкой: %s", type(exc).__name__)
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
