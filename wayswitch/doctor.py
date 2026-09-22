"""Проверка окружения перед первым запуском: что есть, чего не хватает, как починить."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Check:
    name: str
    ok: bool
    hint: str = ""
    required: bool = True


def _check(name: str, fn, hint: str, required: bool = True) -> Check:
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001 — любая ошибка = проверка не пройдена
        ok, detail = False, str(e)
    text = hint if not ok else ""
    if detail:
        text = f"{detail}. {text}".strip()
    return Check(name, ok, text, required)


def run_checks() -> list[Check]:
    checks = []

    def session():
        t = os.environ.get("XDG_SESSION_TYPE", "")
        d = os.environ.get("XDG_CURRENT_DESKTOP", "")
        return t == "wayland" and "gnome" in d.lower(), f"{t or '?'} / {d or '?'}"
    checks.append(_check("Сессия GNOME на Wayland", session,
                         "нужна графическая сессия GNOME Wayland"))

    def modules():
        import evdev  # noqa: F401
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        gi.require_version("IBus", "1.0")
        from gi.repository import Adw, Gtk, IBus  # noqa: F401
        return True, ""
    checks.append(_check("Python-модули evdev, Gtk4, Adw, IBus", modules,
                         "apt install python3-evdev python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 "
                         "gir1.2-ibus-1.0"))

    def xkb():
        from wayswitch.keymap import XkbKeymap
        return XkbKeymap.available(), ""
    checks.append(_check("libxkbcommon", xkb, "apt install libxkbcommon0"))

    def sources():
        from wayswitch.backends.gnome_common import read_sources
        from wayswitch.keymap import XkbKeymap
        specs = read_sources()
        km = XkbKeymap.from_names(specs, [])
        alphabets = sorted(km.alphabet(g) for g in range(km.num_groups))
        return alphabets == ["latin", "ru"], ", ".join(s.xkb_id for s in specs)
    checks.append(_check("Две раскладки: русская и латинская", sources,
                         "Настройки → Клавиатура → Источники ввода: оставьте ru и en"))

    def ibus():
        import gi
        gi.require_version("IBus", "1.0")
        from gi.repository import IBus
        bus = IBus.Bus()
        if not bus.is_connected():
            return False, "IBus не отвечает"
        desc = bus.get_global_engine()
        return True, f"текущий движок {desc.get_name() if desc else '?'}"
    checks.append(_check("IBus (состояние раскладки)", ibus,
                         "запустите ibus-daemon или установите расширение WaySwitch"))

    def shell():
        from wayswitch.backends.gnome_shell import shell_extension_present
        return shell_extension_present(), ""
    checks.append(_check("Расширение GNOME Shell", shell,
                         "необязательно: gnome-extensions enable wayswitch@siberia.ru",
                         required=False))

    def uinput():
        return os.access("/dev/uinput", os.W_OK), ""
    checks.append(_check("/dev/uinput доступен на запись", uinput,
                         "sudo packaging/install.sh (правило udev), затем перелогиньтесь"))

    def keyboards():
        from evdev import InputDevice, ecodes, list_devices

        from wayswitch.keys import classify
        found, denied = [], 0
        for path in list_devices():
            try:
                dev = InputDevice(path)
            except PermissionError:
                denied += 1
                continue
            kind = classify(dev.name, set(dev.capabilities().get(ecodes.EV_KEY, [])))
            if kind == "keyboard":
                found.append(dev.name)
            dev.close()
        return bool(found), f"{len(found)} клавиатур, без доступа {denied}"
    checks.append(_check("Клавиатуры читаются", keyboards,
                         "правило udev 60-wayswitch.rules + перелогин"))

    def udev_rule():
        return Path("/etc/udev/rules.d/60-wayswitch.rules").exists(), ""
    checks.append(_check("Правило udev установлено", udev_rule, "sudo packaging/install.sh"))

    def unit():
        p = Path.home() / ".config/systemd/user/wayswitch.service"
        return p.exists(), ""
    checks.append(_check("systemd user-сервис", unit, "sudo packaging/install.sh",
                         required=False))

    def tools():
        return shutil.which("gnome-extensions") is not None, ""
    checks.append(_check("gnome-extensions", tools, "нужно только для расширения",
                         required=False))
    return checks


def format_report(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        mark = "✓" if c.ok else "✗"
        line = f"{mark} {c.name}"
        if c.hint:
            line += f"\n    {c.hint}"
        lines.append(line)
    bad = [c for c in checks if not c.ok and c.required]
    lines.append("")
    lines.append("Всё готово." if not bad else f"Не пройдено обязательных проверок: {len(bad)}.")
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(not c.ok and c.required for c in checks) else 0
