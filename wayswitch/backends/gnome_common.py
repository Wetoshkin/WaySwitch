"""Общее для GNOME: источники ввода из gsettings и разбор системного хоткея."""

from __future__ import annotations

import re

from wayswitch import keycodes as kc
from wayswitch.backends.base import BackendError
from wayswitch.keymap import LayoutSpec

SCHEMA_SOURCES = "org.gnome.desktop.input-sources"
SCHEMA_WM = "org.gnome.desktop.wm.keybindings"

_MOD_CODES = {"super": kc.KEY_LEFTMETA, "alt": kc.KEY_LEFTALT, "shift": kc.KEY_LEFTSHIFT,
              "control": kc.KEY_LEFTCTRL, "primary": kc.KEY_LEFTCTRL, "meta": kc.KEY_LEFTALT}
_KEYSYM_NAMES = {"shift_l": kc.KEY_LEFTSHIFT, "shift_r": kc.KEY_RIGHTSHIFT,
                 "alt_l": kc.KEY_LEFTALT, "alt_r": kc.KEY_RIGHTALT,
                 "control_l": kc.KEY_LEFTCTRL, "control_r": kc.KEY_RIGHTCTRL,
                 "super_l": kc.KEY_LEFTMETA, "super_r": kc.KEY_RIGHTMETA,
                 "caps_lock": kc.KEY_CAPSLOCK, "iso_next_group": kc.KEY_RIGHTALT}


def engine_name_for(spec: LayoutSpec) -> str:
    """Префикс имени IBus-движка для xkb-раскладки: xkb:<layout>:<variant>:."""
    return f"xkb:{spec.layout}:{spec.variant}:"


def index_of_engine(engine: str, specs: list[LayoutSpec]) -> int | None:
    for i, spec in enumerate(specs):
        if engine.startswith(engine_name_for(spec)):
            return i
    return None


def parse_gnome_binding(binding: str) -> list[int]:
    """'<Super>space' → [KEY_LEFTMETA, KEY_SPACE]. Неизвестное имя → BackendError."""
    if not binding:
        return []
    codes = [_MOD_CODES[m.lower()] for m in re.findall(r"<([^>]+)>", binding)
             if m.lower() in _MOD_CODES]
    key = re.sub(r"<[^>]+>", "", binding).strip().lower()
    if key:
        code = _KEYSYM_NAMES.get(key) or kc.KEY_NAMES.get(key)
        if code is None:
            raise BackendError(f"неизвестная клавиша в системном хоткее: {binding!r}")
        codes.append(code)
    return codes


def _settings(schema: str):
    from gi.repository import Gio

    return Gio.Settings.new(schema)


def read_sources() -> list[LayoutSpec]:
    raw = _settings(SCHEMA_SOURCES).get_value("sources").unpack()
    specs = []
    for kind, ident in raw:
        if kind != "xkb":
            raise BackendError(f"источник ввода {kind}:{ident} — не xkb-раскладка")
        specs.append(LayoutSpec.parse(ident))
    if not specs:
        raise BackendError("в gsettings нет источников ввода")
    return specs


def read_xkb_options() -> list[str]:
    return list(_settings(SCHEMA_SOURCES).get_strv("xkb-options"))


def read_switch_binding() -> list[int]:
    values = _settings(SCHEMA_WM).get_strv("switch-input-source")
    return parse_gnome_binding(values[0]) if values else []
