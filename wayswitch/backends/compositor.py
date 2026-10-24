"""Xfce, MATE, Budgie, LXQt и другие среды на labwc / Wayfire.

В Wayland эти окружения работают поверх отдельного композитора (labwc,
Wayfire). Раскладки задаются в его настройках через XKB:

- переменные XKB_DEFAULT_LAYOUT / _VARIANT / _OPTIONS (их читают оба);
- labwc: ~/.config/labwc/environment;
- Wayfire: ~/.config/wayfire.ini, секция [input], ключи xkb_layout и др.

Узнать текущую раскладку у этих композиторов нельзя — протокола нет.
Поэтому переключаем тем же сочетанием, что задано в xkb_options
(grp:alt_shift_toggle и т. п.), а текущую раскладку ведём счётчиком и
учитываем физические нажатия сочетания. Счётчик ошибается, если раскладку
сменили мышью; поэтому автоисправление здесь — по желанию (backend.track_hotkey).
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

from wayswitch import keycodes as kc
from wayswitch.backends import xkbnames
from wayswitch.backends.base import BackendError
from wayswitch.backends.hotkey import HotkeyBackend
from wayswitch.keymap import LayoutSpec

# xkb_options → клавиши, которыми XKB переключает группу.
TOGGLES = {
    "grp:alt_shift_toggle": [kc.KEY_LEFTALT, kc.KEY_LEFTSHIFT],
    "grp:lalt_lshift_toggle": [kc.KEY_LEFTALT, kc.KEY_LEFTSHIFT],
    "grp:ctrl_shift_toggle": [kc.KEY_LEFTCTRL, kc.KEY_LEFTSHIFT],
    "grp:lctrl_lshift_toggle": [kc.KEY_LEFTCTRL, kc.KEY_LEFTSHIFT],
    "grp:ctrl_alt_toggle": [kc.KEY_LEFTCTRL, kc.KEY_LEFTALT],
    "grp:win_space_toggle": [kc.KEY_LEFTMETA, kc.KEY_SPACE],
    "grp:alt_space_toggle": [kc.KEY_LEFTALT, kc.KEY_SPACE],
    "grp:caps_toggle": [kc.KEY_CAPSLOCK],
    "grp:shift_caps_toggle": [kc.KEY_LEFTSHIFT, kc.KEY_CAPSLOCK],
    "grp:alt_caps_toggle": [kc.KEY_LEFTALT, kc.KEY_CAPSLOCK],
    "grp:toggle": [kc.KEY_RIGHTALT],
    "grp:rctrl_toggle": [kc.KEY_RIGHTCTRL],
    "grp:lwin_toggle": [kc.KEY_LEFTMETA],
    "grp:menu_toggle": [kc.KEY_COMPOSE],
    "grp:alt_shift_toggle_bidir": [kc.KEY_LEFTALT, kc.KEY_LEFTSHIFT],
    "grp:ctrl_shift_toggle_bidir": [kc.KEY_LEFTCTRL, kc.KEY_LEFTSHIFT],
    "grp:lalt_toggle": [kc.KEY_LEFTALT],
    "grp:rshift_toggle": [kc.KEY_RIGHTSHIFT],
    "grp:lshift_toggle": [kc.KEY_LEFTSHIFT],
    "grp:shifts_toggle": [kc.KEY_LEFTSHIFT, kc.KEY_RIGHTSHIFT],
    "grp:alts_toggle": [kc.KEY_LEFTALT, kc.KEY_RIGHTALT],
    "grp:ctrls_toggle": [kc.KEY_LEFTCTRL, kc.KEY_RIGHTCTRL],
}
# Опции, где левая и правая клавиши равноправны («Alt+Shift» любой стороной).
_SIDE_AGNOSTIC = {"grp:alt_shift_toggle", "grp:ctrl_shift_toggle", "grp:ctrl_alt_toggle",
                  "grp:win_space_toggle", "grp:alt_space_toggle", "grp:shift_caps_toggle",
                  "grp:alt_caps_toggle", "grp:alt_shift_toggle_bidir",
                  "grp:ctrl_shift_toggle_bidir"}
_LEFT = {kc.KEY_RIGHTALT: kc.KEY_LEFTALT, kc.KEY_RIGHTSHIFT: kc.KEY_LEFTSHIFT,
         kc.KEY_RIGHTCTRL: kc.KEY_LEFTCTRL, kc.KEY_RIGHTMETA: kc.KEY_LEFTMETA}


def state_path(env=None) -> Path:
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "wayswitch" / "group"


def toggle_combos(options: list[str]) -> list[list[int]]:
    return [TOGGLES[o] for o in options if o in TOGGLES]


def _config_home(env) -> Path:
    return Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE построчно (формат ~/.config/labwc/environment)."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if sep:
            out[name.strip()] = value.strip().strip('"').strip("'")
    return out


def read_wayfire(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return {}
    if not parser.has_section("input"):
        return {}
    sec = parser["input"]
    return {"XKB_DEFAULT_LAYOUT": sec.get("xkb_layout", ""),
            "XKB_DEFAULT_VARIANT": sec.get("xkb_variant", ""),
            "XKB_DEFAULT_OPTIONS": sec.get("xkb_options", "")}


def read_xkb(env=None) -> tuple[list[LayoutSpec], list[str], str]:
    """(раскладки, xkb_options, откуда взято) по переменным и конфигам композиторов."""
    env = os.environ if env is None else env
    home = _config_home(env)
    sources = [("переменные окружения", dict(env)),
               ("labwc", read_env_file(home / "labwc" / "environment")),
               ("Wayfire", read_wayfire(home / "wayfire.ini"))]
    for where, values in sources:
        layout = values.get("XKB_DEFAULT_LAYOUT", "")
        if "," in layout:
            specs = xkbnames.parse_layout_list(layout, values.get("XKB_DEFAULT_VARIANT", ""))
            options = [o.strip() for o in values.get("XKB_DEFAULT_OPTIONS", "").split(",")
                       if o.strip()]
            return specs, options, where
    return [], [], ""


class CompositorBackend(HotkeyBackend):
    """Сочетание XKB-переключения раскладки, раскладки — из конфига композитора."""

    name = "compositor"

    def __init__(self, typist, settle_ms: int = 120, track: bool = False, env=None):
        specs, options, where = read_xkb(env)
        if len(specs) < 2:
            raise BackendError("раскладки не найдены: задайте XKB_DEFAULT_LAYOUT=us,ru "
                               "в ~/.config/labwc/environment или xkb_layout в wayfire.ini")
        combos = toggle_combos(options)
        if not combos:
            raise BackendError("в xkb_options нет сочетания переключения раскладки "
                               "(например, grp:alt_shift_toggle)")
        super().__init__(typist, settle_ms, combos=combos, specs=specs)
        self._options = options
        self.source = where
        self._agnostic = any(o in _SIDE_AGNOSTIC for o in options)
        # Счётчик живёт до конца сеанса (XDG_RUNTIME_DIR очищается при
        # выходе): перезапуск службы посреди сеанса не сбрасывает его в 0.
        self._state = state_path(env)
        try:
            saved = int(self._state.read_text(encoding="ascii"))
            if 0 <= saved < len(self._specs):
                self._current = saved
        except (OSError, ValueError):
            pass
        # Счётчик раскладки ненадёжен (смена мышью ему не видна) — доверять ли
        # ему автоисправление, решает человек.
        self.supports_auto = bool(track)

    def xkb_options(self) -> list[str]:
        return list(self._options)

    def _norm(self, codes) -> set[int]:
        return {_LEFT.get(c, c) for c in codes} if self._agnostic else set(codes)

    def observe_physical(self, code: int, value: int) -> None:
        """XKB переключает группу на любом порядке нажатия и любой стороной
        (Shift→Alt так же, как Alt→Shift; правый Alt — как левый)."""
        if value == 1:
            self._held.add(code)
            held = self._norm(self._held)
            for combo in self._combos:
                if self._norm([code]) <= self._norm(combo) and held == self._norm(combo):
                    self._advance(external=True)
                    break
        elif value == 0:
            self._held.discard(code)

    def _advance(self, external: bool) -> None:
        super()._advance(external)
        try:
            self._state.parent.mkdir(parents=True, exist_ok=True)
            self._state.write_text(str(self._current), encoding="ascii")
        except OSError:
            pass
