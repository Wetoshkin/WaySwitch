"""Живой прогон правил автоисправления на настоящей раскладке и словаре.

Печатающая машинка только записывает: видно, сколько backspace-ов ушло бы и
какая раскладка запрошена. Ничего не печатается в чужие окна и не меняется
системная раскладка — скрипт безопасно запускать на рабочей машине.

Нужна сессия, в которой `read_sources()` видит раскладки (GNOME или KDE на
Wayland), и установленный `libxkbcommon`. Запуск из корня репозитория:

    PYTHONPATH=. python3 tools/live_rules.py

Код возврата: 0 — все сценарии совпали с ожиданием, 1 — были расхождения.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.fakes import FakeBackend, RecordingTypist  # noqa: E402
from wayswitch import keycodes as kc  # noqa: E402
from wayswitch.backends.gnome_common import read_sources  # noqa: E402
from wayswitch.buffer import KeyPress  # noqa: E402
from wayswitch.config import Config  # noqa: E402
from wayswitch.controller import Controller  # noqa: E402
from wayswitch.detector import SENSITIVITY, Detector, LanguageModel  # noqa: E402
from wayswitch.keymap import XkbKeymap  # noqa: E402


def build() -> tuple[XkbKeymap, dict[str, int], Detector]:
    keymap = XkbKeymap.from_names(read_sources(), [])
    groups = {keymap.alphabet(g): g for g in range(keymap.num_groups)}
    models = {lang: LanguageModel.load(lang) for lang in ("ru", "en")}
    detector = Detector(keymap, models, set(), SENSITIVITY["normal"])
    return keymap, groups, detector


def run(keymap, detector, group: int, text: str, expect: str) -> bool:
    """Напечатать text клавишами группы group и сравнить результат с expect."""
    backend, typist = FakeBackend(current=group), RecordingTypist()
    controller = Controller(Config(), keymap, detector, backend, typist)
    for ch in text:
        code, shift = keymap.key_for(ch, group)
        # Регистр буфер берёт из зажатых клавиш, поэтому Shift нажимаем по-настоящему.
        if shift:
            controller.on_key(kc.KEY_LEFTSHIFT, 1, 1)
        controller.on_key(code, 1, 1)
        controller.on_key(code, 0, 1)
        if shift:
            controller.on_key(kc.KEY_LEFTSHIFT, 0, 1)
    typed_keys = [KeyPress(code, shift) for code, shift in typist.taps()
                  if code != kc.KEY_BACKSPACE]
    typed = keymap.decode(typed_keys, backend.current())
    backs = sum(1 for code, _shift in typist.taps() if code == kc.KEY_BACKSPACE)
    ok = typed == expect
    mark = "OK  " if ok else "FAIL"
    print(f"{mark} {text!r} → {typed!r}  (backspace {backs}, "
          f"переключений {len(backend.set_calls)}), ждали {expect!r}")
    return ok


def run_guard(keymap, detector, us_group: int) -> bool:
    """Сразу после Backspace автоисправление молчать обязано."""
    backend, typist = FakeBackend(current=us_group), RecordingTypist()
    controller = Controller(Config(), keymap, detector, backend, typist)
    controller.on_key(kc.KEY_BACKSPACE, 1, 1)
    controller.on_key(kc.KEY_BACKSPACE, 0, 1)
    for ch in "ghbdtn ":
        code, shift = keymap.key_for(ch, us_group)
        controller.on_key(code, 1, 1)
        controller.on_key(code, 0, 1)
    ok = typist.taps() == []
    print(f"{'OK  ' if ok else 'FAIL'} защита после Backspace: действий {len(typist.taps())}")
    return ok


def main() -> int:
    keymap, groups, detector = build()
    us, ru = groups["latin"], groups["ru"]
    results = [
        run(keymap, detector, ru, "руддщ ", "hello "),
        run(keymap, detector, us, "ghbdtn ", "привет "),
        run(keymap, detector, ru, "пирогб ", "пирог, "),
        run(keymap, detector, ru, "чтото ", "что-то "),
        run(keymap, detector, ru, "кембы ", "кем бы "),
        run(keymap, detector, ru, "сонатуБаха ", "сонату Баха "),
        run_guard(keymap, detector, us),
    ]
    print(f"\nитог: {sum(results)} из {len(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
