"""Оркестрация без привязки к GLib: события клавиш → решения → действия.

Контроллер ничего не знает о evdev, D-Bus и главном цикле. Всё внешнее
приходит через вызовы on_*, всё наружу — через typist/backend и колбэки.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from wayswitch import keycodes as kc
from wayswitch.actuator import DONE, execute, keys_for_typing, plan_fix
from wayswitch.buffer import InputBuffer, KeyPress
from wayswitch.config import Config
from wayswitch.detector import SENSITIVITY, Detector
from wayswitch.gesture import ShiftGesture

log = logging.getLogger("wayswitch")
RELEASE_WAIT = 0.3  # сколько ждать отпускания физических клавиш перед печатью


@dataclass
class LastFix:
    original_keys: list[KeyPress]
    target_keys: list[KeyPress]
    group_before: int
    time: float


class Controller:
    def __init__(self, config: Config, keymap, detector: Detector, backend, typist, *,
                 clock: Callable[[], float] = time.monotonic,
                 pump: Callable[[], None] = lambda: None,
                 dry_run: bool = False,
                 on_corrected: Callable[[str, str, bool], None] | None = None,
                 on_status: Callable[[dict], None] | None = None,
                 persist_exception: Callable[[str], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.config = config
        self.keymap = keymap
        self.detector = detector
        self.backend = backend
        self.typist = typist
        self.clock = clock
        self.pump = pump  # прокрутить очередь событий главного цикла (для отмены)
        self.dry_run = dry_run
        self.on_corrected = on_corrected
        self.on_status = on_status
        self.persist_exception = persist_exception
        self.sleep = sleep

        self.buffer = InputBuffer(keymap, config.general.phrase_timeout_sec)
        self.gesture = ShiftGesture()
        self.context: deque[str | None] = deque(maxlen=2)  # языки двух последних слов
        self.paused = False
        self.locked = False
        self.busy = False
        self._abort_requested = False
        self._last_fix: LastFix | None = None
        self.stats = {"corrections": 0, "manual": 0, "undo": 0}
        self._pause_key = kc.KEY_NAMES.get(config.gesture.pause_hotkey.lower()) \
            if config.gesture.pause_hotkey else None

    # --- конфигурация / статус ---------------------------------------------------

    def reload_config(self, config: Config) -> None:
        self.config = config
        self.buffer.phrase_timeout = config.general.phrase_timeout_sec
        self.detector.sensitivity = SENSITIVITY[config.general.sensitivity]
        self._pause_key = kc.KEY_NAMES.get(config.gesture.pause_hotkey.lower()) \
            if config.gesture.pause_hotkey else None
        self._emit_status()

    def status(self) -> dict:
        return {
            "active": self._auto_allowed(),
            "paused": self.paused,
            "locked": self.locked,
            "busy": self.busy,
            "backend": getattr(self.backend, "name", "?"),
            "layout_index": self.backend.current(),
            "auto_correct": self.config.general.auto_correct,
            "corrections_total": self.stats["corrections"],
            "manual_total": self.stats["manual"],
            "undo_total": self.stats["undo"],
            "dry_run": self.dry_run,
        }

    def _emit_status(self) -> None:
        if self.on_status:
            self.on_status(self.status())

    def pause(self) -> None:
        self.paused = True
        self.buffer.reset()
        self._emit_status()

    def resume(self) -> None:
        self.paused = False
        self._emit_status()

    def set_locked(self, on: bool) -> None:
        self.locked = on
        self.buffer.reset()
        self.context.clear()
        self._emit_status()

    def set_caps(self, on: bool) -> None:
        self.buffer.set_caps(on)

    # --- входящие события -------------------------------------------------------

    def on_key(self, code: int, value: int, device_id: int) -> None:
        if self.busy:
            # Физический ввод во время перепечатки — прервать; зажатые клавиши учесть.
            if value == 1:
                self._abort_requested = True
            elif value == 0:
                self.buffer.note_release(code, device_id)
            return
        now = self.clock()
        gesture = self.gesture.on_key(code, value, now) \
            if self.config.gesture.manual == "double_shift" else None
        event = self.buffer.feed(code, value, device_id, now)
        if value == 1 and self._pause_key is not None and code == self._pause_key:
            (self.resume if self.paused else self.pause)()
            return
        if self.paused or self.locked:
            # Пауза/блокировка выключают и ручные жесты — только сам хоткей
            # паузы (обработан выше) должен продолжать работать.
            return
        if value == 1 and self.config.gesture.manual == "pause_key" and code == kc.KEY_PAUSE:
            self.manual_word()
            return
        if gesture == "word":
            self.manual_word()
            return
        if gesture == "phrase":
            self.manual_phrase()
            return
        if event is None:
            return
        if event.kind == "reset":
            self.context.clear()
            return
        if event.reset_before:
            self.context.clear()  # буфер очищен по таймауту перед этой клавишей
        if not self._auto_allowed():
            return
        if event.kind == "word":
            self._on_word(event.keys, device_id, code)
        elif event.kind == "letter":
            self._on_letter(event.keys, device_id, code)

    def on_click(self) -> None:
        self.buffer.click()
        self.context.clear()

    def on_layout_changed(self, index: int, external: bool) -> None:
        if external:
            self.buffer.reset()
            self.context.clear()
        self._emit_status()

    def on_resync(self, device_id: int, held) -> None:
        self.buffer.resync(device_id, held)

    def on_device_gone(self, device_id: int) -> None:
        self.buffer.device_gone(device_id)

    # --- авторежим --------------------------------------------------------------

    def _auto_allowed(self) -> bool:
        return (self.config.general.auto_correct and not self.paused and not self.locked
                and getattr(self.backend, "supports_auto", False))

    def _groups(self) -> tuple[int, int] | None:
        current = self.backend.current()
        if current is None or len(self.backend.layouts()) != 2:
            return None
        return current, 1 - current

    def _context_tuple(self) -> tuple[str | None, str | None]:
        """`self.context` — deque(maxlen=2), но в начале сессии/фразы в нём
        может быть 0 или 1 элемент; Detector.decide() ожидает ровно 2-tuple
        (читает context[-1]/context[-2]) — дополняем слева None."""
        return (None,) * (2 - len(self.context)) + tuple(self.context)

    def _on_word(self, keys: list[KeyPress], device_id: int, code: int) -> None:
        groups = self._groups()
        if groups is None:
            return
        current, other = groups
        decision = self.detector.decide(keys, current, other, self._context_tuple())
        log.debug("слово %r → %s (%s)", decision.a.text, decision.action, decision.reason)
        if decision.action == "fix":
            if self._apply_fix(keys, 1, other, decision.target_text + " ", manual=False,
                               reason=decision.reason, group_before=current,
                               trigger=(device_id, code)):
                self.context.append(decision.b.lang)
            return
        self.context.append(decision.a.lang if decision.a.in_dict else None)

    def _on_letter(self, keys: list[KeyPress], device_id: int, code: int) -> None:
        groups = self._groups()
        if groups is None:
            return
        current, other = groups
        target = self.detector.early_url_target(keys, current, other)
        if target:
            self._apply_fix(keys, 0, other, target, manual=False, reason="url-early",
                            group_before=current, trigger=(device_id, code))

    # --- ручной режим -----------------------------------------------------------

    def manual_word(self) -> bool:
        groups = self._groups()
        if groups is None:
            return False
        current, other = groups
        word, tail = self.buffer.last_word_with_tail()
        if not word:
            if not self.dry_run:
                self.busy = True
                try:
                    self.backend.set(other)
                    self.backend.wait_applied(other, 0.5)
                finally:
                    self.busy = False
            self._emit_status()
            return True
        last = self._last_fix
        undo = (last is not None and self.clock() - last.time <= self.config.general.undo_window_sec
                and word == last.target_keys)
        if undo:
            original = self.keymap.decode(last.original_keys, last.group_before)
            ok = self._apply_fix(word, tail, last.group_before, original + " " * tail,
                                 manual=True, reason="undo", group_before=current)
            if ok:
                self._last_fix = None
                self.stats["undo"] += 1
                letters = "".join(c for c in original if c.isalpha() or c in "-'").lower()
                if letters:
                    self.detector.exceptions.add(letters)
                    if self.persist_exception:
                        self.persist_exception(letters)
            return ok
        text = self.keymap.decode(word, other)
        return self._apply_fix(word, tail, other, text + " " * tail, manual=True,
                               reason="manual", group_before=current)

    def manual_phrase(self) -> bool:
        current = self.backend.current()
        keys = self.buffer.phrase()
        if current is None or not keys:
            return False
        # Раскладка уже переключена вторым Shift; декодируем всё в текущей группе.
        text = self.keymap.decode(keys, current)
        return self._apply_fix(keys, 0, current, text, manual=True, reason="phrase",
                               group_before=current, whole_phrase=True, switch=False)

    # --- исполнение -------------------------------------------------------------

    def _abort(self) -> bool:
        self.pump()
        return self._abort_requested

    def _blocking_keys_held(self, trigger: tuple[int, int] | None) -> bool:
        """Есть ли физически зажатые небезразличные клавиши, мешающие печати.

        Клавиша-триггер (`trigger`) сюда не считается: buffer.feed() кладёт её
        в _held ещё до генерации события «слово»/«буква», а мы сейчас как раз
        синхронно внутри обработки её же нажатия — отпускание этой самой
        клавиши физически не могло прийти раньше, чем мы вернёмся из этого
        вызова, так что оно не «зависшее», а просто ещё не доставлено.
        Любая ДРУГАЯ зажатая клавиша (с другого устройства или нажатая раньше)
        по-прежнему блокирует печать.
        """
        held = self.buffer.held_physical()
        if trigger is not None:
            held = held - {trigger}
        return bool({code for _, code in held} - kc.MODIFIER_KEYS)

    def _wait_release(self, trigger: tuple[int, int] | None = None) -> bool:
        # Дедлайн — по настоящим часам (time.monotonic), а не по self.clock:
        # self.clock в тестах подменяется управляемым вручную фейком и не тикает
        # сам по себе внутри этого цикла ожидания, поэтому дедлайн на нём
        # никогда не наступил бы. Здесь же мы ждём реальное железо (пока
        # физически отпустят клавиши), так что нужно настоящее время.
        deadline = time.monotonic() + RELEASE_WAIT
        while self._blocking_keys_held(trigger):
            if time.monotonic() > deadline:
                return False
            self.pump()
            self.sleep(0.005)
        return True

    def _apply_fix(self, keys: list[KeyPress], tail: int, target_group: int, text: str,
                   *, manual: bool, reason: str, group_before: int,
                   whole_phrase: bool = False, switch: bool = True,
                   trigger: tuple[int, int] | None = None) -> bool:
        plan = plan_fix(len(keys) + tail, text, target_group, self.keymap, self.buffer.caps,
                        switch=switch)
        if plan is None:
            log.warning("не могу набрать %r в группе %s", text, target_group)
            return False
        original = self.keymap.decode(keys, group_before)
        if self.dry_run:
            log.info("[dry-run] %s: %r → %r", reason, original, text)
            return False
        self.busy = True
        self._abort_requested = False
        started = self.clock()
        try:
            if not self._wait_release(trigger):
                log.info("исправление отменено: зажата физическая клавиша")
                return False
            result = execute(plan, self.typist, self.backend, abort=self._abort,
                             key_delay=self.config.typing.key_delay_ms / 1000.0,
                             sleep=self.sleep)
        finally:
            self.busy = False
        if result != DONE:
            log.info("исправление %s: %s", reason, result)
            self.buffer.reset()
            self.context.clear()
            self._emit_status()
            return False
        log.info("%s: %r → %r за %.0f мс", reason, original, text.rstrip(),
                 (self.clock() - started) * 1000)
        new_keys = [KeyPress(code, shift) for code, shift in
                    keys_for_typing(text, target_group, self.keymap, False) or []]
        if whole_phrase:
            self.buffer.replace_all(new_keys)
        else:
            self.buffer.replace_last_word(new_keys[: len(new_keys) - tail] if tail else new_keys)
        word_keys = new_keys[: len(new_keys) - tail] if tail else new_keys
        if manual:
            self.stats["manual"] += 1
            if reason != "undo":
                self._last_fix = None
        else:
            self.stats["corrections"] += 1
            self._last_fix = LastFix(list(keys), word_keys, group_before, self.clock())
        if self.on_corrected:
            self.on_corrected(original, text.rstrip(), manual)
        self._emit_status()
        return True
