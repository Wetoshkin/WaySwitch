"""Буфер набранных нажатий.

Хранятся коды клавиш с состоянием Shift/CapsLock, а не символы: какой символ
получился, зависит от раскладки, а её мы узнаём отдельно. Пробелы хранятся
как разделители — по ним фраза делится на слова.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from wayswitch import keycodes as kc
from wayswitch.keymap import Keymap


@dataclass(frozen=True)
class KeyPress:
    code: int
    shift: bool = False
    caps: bool = False


@dataclass
class BufferEvent:
    kind: str  # "word" — слово завершено пробелом; "letter" — слово растёт; "reset"
    keys: list[KeyPress] = field(default_factory=list)
    reset_before: bool = False  # буфер был очищен по таймауту перед обработкой этой клавиши


class InputBuffer:
    def __init__(self, keymap: Keymap, phrase_timeout: float = 8.0, max_keys: int = 512):
        self._keymap = keymap
        self.phrase_timeout = phrase_timeout
        self.max_keys = max_keys
        self._keys: list[KeyPress] = []
        self._held: set[tuple[int, int]] = set()  # (device_id, code)
        self.caps = False
        self._last_time: float | None = None

    # --- состояние клавиш ------------------------------------------------------

    def set_caps(self, on: bool) -> None:
        self.caps = on

    def held_physical(self) -> set[tuple[int, int]]:
        return set(self._held)

    def _held_codes(self) -> set[int]:
        return {code for _, code in self._held}

    def shift_held(self) -> bool:
        return bool(self._held_codes() & kc.SHIFT_KEYS)

    def command_held(self) -> bool:
        return bool(self._held_codes() & kc.COMMAND_MODIFIERS)

    def held_non_modifier(self) -> bool:
        return bool(self._held_codes() - kc.MODIFIER_KEYS)

    def note_press(self, code: int, device_id: int) -> None:
        """Нажатие, пришедшее пока демон печатает: в буфер не кладём, но зажатые
        обновляем — иначе Shift, зажатый во время фикса и удерживаемый после
        него, был бы для нас «не нажат», и следующие буквы легли бы в буфер без
        регистра."""
        self._held.add((device_id, code))

    def note_release(self, code: int, device_id: int) -> None:
        """Отпускание, пришедшее пока демон печатает: буфер не трогаем, зажатые обновляем."""
        self._held.discard((device_id, code))

    # --- сброс ----------------------------------------------------------------

    def is_empty(self) -> bool:
        return not self._keys

    def reset(self) -> bool:
        had = bool(self._keys)
        self._keys.clear()
        return had

    def _reset_event(self) -> BufferEvent | None:
        return BufferEvent("reset") if self.reset() else None

    def click(self) -> None:
        self.reset()

    def resync(self, device_id: int, held_codes: Iterable[int]) -> None:
        """После SYN_DROPPED: заменить зажатые клавиши устройства снимком, буфер сбросить."""
        self._held = {(d, c) for d, c in self._held if d != device_id}
        self._held |= {(device_id, c) for c in held_codes}
        self.reset()

    def device_gone(self, device_id: int) -> None:
        self._held = {(d, c) for d, c in self._held if d != device_id}
        self.reset()

    # --- ввод -----------------------------------------------------------------

    def _is_word_key(self, code: int) -> bool:
        return self._keymap.is_letter_key(code) or code in kc.WORD_EXTRA_KEYS

    def _append(self, key: KeyPress) -> None:
        self._keys.append(key)
        if len(self._keys) > self.max_keys:
            del self._keys[: len(self._keys) - self.max_keys]

    def feed(self, code: int, value: int, device_id: int, now: float) -> BufferEvent | None:
        timed_out = False
        if self._last_time is not None and self._keys \
                and now - self._last_time > self.phrase_timeout:
            self._keys.clear()
            timed_out = True
        self._last_time = now

        if value == 0:
            self._held.discard((device_id, code))
            return None
        if value == 2:
            # Автоповтор: сколько символов выдаст компоситор — неизвестно.
            if self._is_word_key(code) or code in (kc.KEY_SPACE, kc.KEY_BACKSPACE):
                event = self._reset_event()
            else:
                event = None
            if timed_out and event is None:
                return BufferEvent("reset")
            return event

        self._held.add((device_id, code))
        if code in kc.META_KEYS:
            event = self._reset_event()  # Super открывает обзор — каретка ушла
        elif code in kc.MODIFIER_KEYS or code == kc.KEY_CAPSLOCK:
            event = None
        elif self.command_held():
            event = self._reset_event()  # Ctrl+X, Alt+Tab и т. п.
        elif code in kc.RESET_KEYS:
            event = self._reset_event()
        elif code == kc.KEY_BACKSPACE:
            if self._keys:
                self._keys.pop()
                event = None
            else:
                event = self._reset_event()  # стёрли то, чего не видели
        elif code == kc.KEY_SPACE:
            word = self.current_word()
            self._append(KeyPress(kc.KEY_SPACE))
            event = BufferEvent("word", word) if word else None
        elif self._is_word_key(code):
            self._append(KeyPress(code, self.shift_held(), self.caps))
            event = BufferEvent("letter", self.current_word())
        else:
            event = self._reset_event()  # цифровой блок, мультимедиа и прочее

        if timed_out and event is None:
            return BufferEvent("reset")
        if timed_out and event is not None and event.kind != "reset":
            event.reset_before = True
        return event

    # --- чтение ---------------------------------------------------------------

    def current_word(self) -> list[KeyPress]:
        i = len(self._keys)
        while i > 0 and self._keys[i - 1].code != kc.KEY_SPACE:
            i -= 1
        return self._keys[i:]

    def last_word_with_tail(self) -> tuple[list[KeyPress], int]:
        """Последнее слово и число пробелов после него."""
        end = len(self._keys)
        while end > 0 and self._keys[end - 1].code == kc.KEY_SPACE:
            end -= 1
        start = end
        while start > 0 and self._keys[start - 1].code != kc.KEY_SPACE:
            start -= 1
        return self._keys[start:end], len(self._keys) - end

    def phrase(self) -> list[KeyPress]:
        return list(self._keys)

    def replace_last_word(self, keys: list[KeyPress]) -> None:
        end = len(self._keys)
        while end > 0 and self._keys[end - 1].code == kc.KEY_SPACE:
            end -= 1
        start = end
        while start > 0 and self._keys[start - 1].code != kc.KEY_SPACE:
            start -= 1
        self._keys[start:end] = list(keys)

    def replace_all(self, keys: list[KeyPress]) -> None:
        self._keys = list(keys)
