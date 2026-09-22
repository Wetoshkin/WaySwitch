"""Раскладки: соответствие «код клавиши → символ» по группам xkb.

Источник правды на живой системе — libxkbcommon (XkbKeymap), собранный из тех
же раскладок, что использует GNOME. TableKeymap — то же самое из явных таблиц,
для тестов. Обе реализации строят таблицы один раз; в горячем пути только
словари.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property

CYRILLIC = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")
LATIN = set("abcdefghijklmnopqrstuvwxyz")
# Буквенные клавиши, по которым определяем алфавит группы.
_ALPHABET_PROBE = (30, 31, 32, 33, 34, 35, 36, 37, 38)  # KEY_A..KEY_L


@dataclass(frozen=True)
class LayoutSpec:
    layout: str
    variant: str = ""

    @classmethod
    def parse(cls, source_id: str) -> LayoutSpec:
        """'us+dvorak' (формат gsettings) → LayoutSpec."""
        layout, _, variant = source_id.partition("+")
        return cls(layout, variant)

    @property
    def xkb_id(self) -> str:
        return f"{self.layout}+{self.variant}" if self.variant else self.layout


class Keymap(ABC):
    """Абстрактная раскладка из нескольких групп."""

    @property
    @abstractmethod
    def num_groups(self) -> int: ...

    @abstractmethod
    def _table(self, group: int) -> dict[int, tuple[str, str]]:
        """Код клавиши → (без Shift, с Shift) для группы."""

    def char(self, code: int, group: int, shift: bool = False, caps: bool = False) -> str:
        pair = self._table(group).get(code)
        if pair is None:
            return ""
        ch = pair[1] if shift else pair[0]
        # CapsLock меняет регистр только у букв; с Shift — обратно в строчную.
        if caps and ch.isalpha():
            ch = ch.swapcase()
        return ch

    @cached_property
    def _reverse(self) -> list[dict[str, tuple[int, bool]]]:
        result = []
        for group in range(self.num_groups):
            rev: dict[str, tuple[int, bool]] = {}
            table = self._table(group)
            # Сначала символы с Shift, затем без — без Shift перекрывает.
            for code, (_lo, hi) in table.items():
                if hi:
                    rev.setdefault(hi, (code, True))
            for code, (lo, _hi) in table.items():
                if lo:
                    rev[lo] = (code, False)
            result.append(rev)
        return result

    def key_for(self, ch: str, group: int) -> tuple[int, bool] | None:
        return self._reverse[group].get(ch)

    @cached_property
    def _letter_keys(self) -> frozenset[int]:
        keys = set()
        for group in range(self.num_groups):
            for code, (lo, hi) in self._table(group).items():
                if lo.isalpha() or hi.isalpha():
                    keys.add(code)
        return frozenset(keys)

    def is_letter_key(self, code: int) -> bool:
        """Клавиша даёт букву хотя бы в одной группе (так `;` попадает в слово: в ru это ж)."""
        return code in self._letter_keys

    def alphabet(self, group: int) -> str:
        chars = {self.char(code, group).lower() for code in _ALPHABET_PROBE}
        chars.discard("")
        if chars and chars <= CYRILLIC:
            return "ru"
        if chars and chars <= LATIN:
            return "latin"
        return "other"

    def decode(self, keys: Iterable, group: int) -> str:
        return "".join(self.char(k.code, group, k.shift, k.caps) for k in keys)


class TableKeymap(Keymap):
    def __init__(self, tables: list[dict[int, tuple[str, str]]]):
        self._tables = tables

    @property
    def num_groups(self) -> int:
        return len(self._tables)

    def _table(self, group: int) -> dict[int, tuple[str, str]]:
        return self._tables[group]


# ---- libxkbcommon через ctypes -------------------------------------------------

class _XkbRuleNames(ctypes.Structure):
    _fields_ = [
        ("rules", ctypes.c_char_p), ("model", ctypes.c_char_p), ("layout", ctypes.c_char_p),
        ("variant", ctypes.c_char_p), ("options", ctypes.c_char_p),
    ]


_lib = None


def _load_lib():
    global _lib
    if _lib is None:
        name = ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0"
        lib = ctypes.CDLL(name)
        lib.xkb_context_new.restype = ctypes.c_void_p
        lib.xkb_context_new.argtypes = [ctypes.c_int]
        lib.xkb_context_unref.argtypes = [ctypes.c_void_p]
        lib.xkb_keymap_new_from_names.restype = ctypes.c_void_p
        lib.xkb_keymap_new_from_names.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XkbRuleNames),
                                                  ctypes.c_int]
        lib.xkb_keymap_unref.argtypes = [ctypes.c_void_p]
        lib.xkb_keymap_num_layouts.restype = ctypes.c_uint32
        lib.xkb_keymap_num_layouts.argtypes = [ctypes.c_void_p]
        lib.xkb_keymap_mod_get_index.restype = ctypes.c_uint32
        lib.xkb_keymap_mod_get_index.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.xkb_state_new.restype = ctypes.c_void_p
        lib.xkb_state_new.argtypes = [ctypes.c_void_p]
        lib.xkb_state_unref.argtypes = [ctypes.c_void_p]
        lib.xkb_state_update_mask.restype = ctypes.c_int
        lib.xkb_state_update_mask.argtypes = [ctypes.c_void_p] + [ctypes.c_uint32] * 6
        lib.xkb_state_key_get_utf8.restype = ctypes.c_int
        lib.xkb_state_key_get_utf8.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
                                               ctypes.c_size_t]
        _lib = lib
    return _lib


class XkbKeymap(Keymap):
    """Раскладка, скомпилированная libxkbcommon из имён xkb (как в GNOME)."""

    EVDEV_OFFSET = 8  # xkb keycode = evdev code + 8
    MAX_CODE = 255

    def __init__(self, tables: list[dict[int, tuple[str, str]]], specs: list[LayoutSpec]):
        self._tables = tables
        self.specs = specs

    @staticmethod
    def available() -> bool:
        try:
            _load_lib()
            return True
        except OSError:
            return False

    @classmethod
    def from_names(cls, specs: list[LayoutSpec], options: list[str]) -> XkbKeymap:
        lib = _load_lib()
        names = _XkbRuleNames(
            rules=b"evdev", model=b"pc105",
            layout=",".join(s.layout for s in specs).encode(),
            variant=",".join(s.variant for s in specs).encode(),
            options=",".join(options).encode(),
        )
        ctx = lib.xkb_context_new(0)
        if not ctx:
            raise RuntimeError("xkb_context_new")
        try:
            keymap = lib.xkb_keymap_new_from_names(ctx, ctypes.byref(names), 0)
            if not keymap:
                raise RuntimeError(f"xkb: не удалось собрать раскладку {names.layout!r}")
            try:
                groups = lib.xkb_keymap_num_layouts(keymap)
                shift_idx = lib.xkb_keymap_mod_get_index(keymap, b"Shift")
                tables = [cls._build_table(lib, keymap, g, 1 << shift_idx) for g in range(groups)]
            finally:
                lib.xkb_keymap_unref(keymap)
        finally:
            lib.xkb_context_unref(ctx)
        return cls(tables, specs)

    @classmethod
    def _build_table(cls, lib, keymap, group: int, shift_mask: int) -> dict[int, tuple[str, str]]:
        table: dict[int, tuple[str, str]] = {}
        buf = ctypes.create_string_buffer(16)
        for shift in (False, True):
            state = lib.xkb_state_new(keymap)
            try:
                lib.xkb_state_update_mask(state, shift_mask if shift else 0, 0, 0, 0, 0, group)
                for code in range(1, cls.MAX_CODE + 1):
                    n = lib.xkb_state_key_get_utf8(state, code + cls.EVDEV_OFFSET, buf, 16)
                    ch = buf.value[:n].decode("utf-8", "replace") if n > 0 else ""
                    lo, hi = table.get(code, ("", ""))
                    table[code] = (lo, ch) if shift else (ch, hi)
            finally:
                lib.xkb_state_unref(state)
        # Управляющие символы (Enter → \r, Tab → \t, Esc) словом не являются.
        return {c: (lo, hi) for c, (lo, hi) in table.items()
                if (lo and lo.isprintable()) or (hi and hi.isprintable())}

    @property
    def num_groups(self) -> int:
        return len(self._tables)

    def _table(self, group: int) -> dict[int, tuple[str, str]]:
        return self._tables[group]
