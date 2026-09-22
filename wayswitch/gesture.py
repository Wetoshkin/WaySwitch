"""Жест ручного исправления: двойной (слово) и тройной (фраза) Shift.

Срабатывает на отпускании, чтобы синтетические символы не ушли с зажатым
физическим Shift. Любая другая клавиша между нажатиями разрывает серию.
"""

from __future__ import annotations

from wayswitch import keycodes as kc


class ShiftGesture:
    def __init__(self, window: float = 0.4):
        self.window = window
        self._count = 0
        self._last_press: float | None = None
        self._broken = False  # между нажатием и отпусканием была другая клавиша

    def reset(self) -> None:
        """Забыть начатую серию — например, после прерванного исправления."""
        self._count = 0
        self._last_press = None
        self._broken = False

    def on_key(self, code: int, value: int, now: float) -> str | None:
        if code in kc.SHIFT_KEYS:
            if value == 1:
                if self._last_press is not None and now - self._last_press <= self.window:
                    self._count += 1
                else:
                    self._count = 1
                self._last_press = now
                self._broken = False
            elif value == 0:
                if self._broken:
                    self._count = 0
                    self._last_press = None
                elif self._count == 2:
                    return "word"
                elif self._count >= 3:
                    self._count = 0
                    self._last_press = None
                    return "phrase"
            return None
        if value == 1:
            self._count = 0
            self._last_press = None
            self._broken = True
        return None
