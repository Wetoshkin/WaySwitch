"""Проверка окружения перед первым запуском: что есть, чего не хватает, как починить.

Проверки зависят от среды: в GNOME раскладку отдаёт наше расширение Shell, в
KDE — D-Bus KWin, в прочих средах прочитать её неоткуда (только ручной жест).
Поддерживается только Wayland.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from wayswitch.backends.desktop import (
    COMPOSITOR_DESKTOPS,
    DESKTOP_NAMES,
    GNOME,
    HYPRLAND,
    KDE,
    SWAY,
    detect_desktop,
    is_wayland,
)


@dataclass
class Check:
    name: str
    ok: bool
    hint: str = ""
    required: bool = True
    # Стабильный идентификатор для GUI (первый запуск): имя проверки — текст,
    # его можно переписать, а по key окно находит «свою» проверку.
    key: str = ""


def _check(name: str, fn, hint: str, required: bool = True, key: str = "") -> Check:
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001 — любая ошибка = проверка не пройдена
        ok, detail = False, str(e)
    text = hint if not ok else ""
    if detail:
        text = f"{detail}. {text}".strip()
    return Check(name, ok, text, required, key)


def uinput_status() -> tuple[bool, str]:
    """Открывается ли /dev/uinput на самом деле.

    os.access() тут обманывает: права (ACL от udev) могут быть выданы, а
    модуля uinput в ядре нет — тогда open() падает с ENODEV, и печатать
    нечем. Так бывает сразу после обновления ядра, когда модули старого
    ядра уже удалены: нужна перезагрузка.
    """
    try:
        # getattr: на Windows (разработка, тесты) O_NONBLOCK нет.
        fd = os.open("/dev/uinput", os.O_WRONLY | getattr(os, "O_NONBLOCK", 0))
    except PermissionError:
        return False, "нет прав на /dev/uinput"
    except OSError as e:
        return False, f"/dev/uinput не открывается ({e.strerror or e}) — модуль uinput не загружен?"
    os.close(fd)
    return True, ""


def toolkit_accessibility() -> tuple[bool, str]:
    """Включён ли мост доступности в GTK-программах.

    Без него at-spi видит только оболочку: исключения по программам и защита
    полей паролей не работают (автоисправление при этом работает).
    """
    import subprocess

    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface",
                              "toolkit-accessibility"], capture_output=True, text=True,
                             timeout=3).stdout.strip().lower()
    except (OSError, subprocess.SubprocessError):
        return False, "в этой среде настройка недоступна"
    if out == "true":
        return True, ""
    return False, ("gsettings set org.gnome.desktop.interface toolkit-accessibility true "
                   "и перезапустить программы")


def atspi_status() -> tuple[bool, str]:
    """Отвечает ли мост доступности: без него не узнать приложение и поле пароля.

    Проверка необязательная: без at-spi автоисправление работает, просто не
    исключает программы и не распознаёт поля паролей.
    """
    from wayswitch.a11y import bridge_available

    return bridge_available()


def run_checks() -> list[Check]:
    checks = []
    desktop = detect_desktop()

    def session():
        wayland = is_wayland()
        return wayland, DESKTOP_NAMES[desktop]
    checks.append(_check("Сессия Wayland", session,
                         "включён старый режим графики X11, а WaySwitch работает только "
                         "в Wayland: на экране входа нажмите на шестерёнку и выберите "
                         "сеанс без «Xorg»", key="wayland"))

    checks.append(_check("Распознавание программ и полей пароля (настройка)",
                         toolkit_accessibility,
                         "необязательно: без неё не работают исключения по "
                         "программам и защита полей паролей", required=False))
    checks.append(_check("Распознавание программ и полей пароля (служба at-spi)",
                         atspi_status,
                         "необязательно: без него не работают исключения по "
                         "программам и защита полей паролей", required=False))

    if desktop == GNOME:
        def gnome_tools():
            from wayswitch.backends.gnome_shell import extension_version, shell_extension_present
            if not shell_extension_present():
                return False, "расширение не запущено"
            version = extension_version()
            if version is not None and version < 2:
                # Shell держит загруженной старую копию до перелогина.
                return False, ("работает старая версия расширения — выйдите из сеанса "
                               "и войдите снова")
            return True, ""
        # На GNOME 46+ без расширения автоисправления нет: IBus меняет движок,
        # но не раскладку компоузитора. Поэтому проверка обязательная.
        checks.append(_check("Расширение GNOME Shell", gnome_tools,
                             "gnome-extensions enable wayswitch@siberia.ru; если не помогает — "
                             "выйдите из сеанса и войдите снова",
                             key="extension"))

        def ibus():
            import gi
            gi.require_version("IBus", "1.0")
            from gi.repository import IBus
            bus = IBus.Bus()
            if not bus.is_connected():
                return False, "IBus не отвечает"
            desc = bus.get_global_engine()
            return True, f"текущий движок {desc.get_name() if desc else '?'}"
        checks.append(_check("Запасной способ переключения (IBus)", ibus,
                             "необязательно: нужен только без расширения WaySwitch",
                             required=False))

    if desktop == KDE:
        def kde_service():
            from wayswitch.backends.kde_plasma import kde_available
            return kde_available(), "org.kde.keyboard"
        checks.append(_check("KWin отдаёт раскладки (org.kde.keyboard)", kde_service,
                             "нужен KDE Plasma с двумя и более раскладками"))

    if desktop == SWAY:
        def sway_ipc():
            from wayswitch.backends import sway
            path = sway.find_socket()
            if path is None:
                return False, "сокет sway не найден"
            sway.SwayIpc(path).request(sway.GET_INPUTS)
            return True, "Sway IPC"
        checks.append(_check("Переключение раскладки (Sway IPC)", sway_ipc,
                             "запустите службу из сеанса sway: exec systemctl --user "
                             "import-environment SWAYSOCK в конфиге sway", key="switch"))

    if desktop == HYPRLAND:
        def hypr_ipc():
            from wayswitch.backends import hyprland
            directory = hyprland.find_dir()
            if directory is None:
                return False, "сокет Hyprland не найден"
            hyprland.HyprIpc(directory).request("version")
            return True, "Hyprland IPC"
        checks.append(_check("Переключение раскладки (Hyprland IPC)", hypr_ipc,
                             "запустите службу из сеанса Hyprland (uwsm или exec-once)",
                             key="switch"))

    if desktop in COMPOSITOR_DESKTOPS:
        def compositor_xkb():
            from wayswitch.backends import compositor
            specs, options, where = compositor.read_xkb()
            if len(specs) < 2:
                return False, "раскладки композитора не найдены"
            if not compositor.toggle_combos(options):
                return False, f"нет сочетания переключения в xkb_options ({where})"
            return True, f"{where}: {', '.join(s.xkb_id for s in specs)}"
        checks.append(_check("Сочетание переключения раскладки (labwc / Wayfire)",
                             compositor_xkb,
                             "задайте в ~/.config/labwc/environment: XKB_DEFAULT_LAYOUT=us,ru и "
                             "XKB_DEFAULT_OPTIONS=grp:alt_shift_toggle (или xkb_layout / "
                             "xkb_options в [input] wayfire.ini)", key="switch"))

    def modules():
        import evdev  # noqa: F401
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401
        return True, ""
    checks.append(_check("Нужные программные библиотеки (evdev, GTK 4, libadwaita)", modules,
                         "apt install python3-evdev python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 "
                         "(dnf: python3-evdev python3-gobject gtk4 libadwaita, "
                         "pacman: python-evdev python-gobject gtk4 libadwaita)"))

    def xkb():
        from wayswitch.keymap import XkbKeymap
        return XkbKeymap.available(), ""
    checks.append(_check("Библиотека раскладок (libxkbcommon)", xkb,
                         "apt install libxkbcommon0 / dnf install libxkbcommon / "
                         "pacman -S libxkbcommon"))

    def sources():
        """Две раскладки: ru и латинская — по данным среды."""
        from wayswitch.keymap import XkbKeymap
        if desktop == KDE:
            from wayswitch.backends.kde_plasma import KdePlasmaBackend
            specs = KdePlasmaBackend().layouts()
        elif desktop == GNOME:
            from wayswitch.backends.gnome_common import read_sources
            specs = read_sources()
        elif desktop == SWAY:
            from wayswitch.backends import sway
            path = sway.find_socket()
            if path is None:
                return False, "sway не отвечает (сокет не найден)"
            names, _i = sway.keyboard_state(sway.SwayIpc(path).request(sway.GET_INPUTS))
            specs = sway.specs_from_names(names)
        elif desktop == HYPRLAND:
            from wayswitch.backends import hyprland, xkbnames
            directory = hyprland.find_dir()
            if directory is None:
                return False, "Hyprland не отвечает (сокет не найден)"
            ipc = hyprland.HyprIpc(directory)
            specs = xkbnames.parse_layout_list(
                hyprland._clean(ipc.json("getoption input:kb_layout").get("str", "")),
                hyprland._clean(ipc.json("getoption input:kb_variant").get("str", "")))
        else:
            from wayswitch.backends import compositor
            specs, _o, _w = compositor.read_xkb()
            if not specs:
                return False, "раскладки композитора не найдены"
        km = XkbKeymap.from_names(specs, [])
        alphabets = sorted(km.alphabet(g) for g in range(km.num_groups))
        return alphabets == ["latin", "ru"], ", ".join(s.xkb_id for s in specs)
    checks.append(_check("Две раскладки: русская и английская", sources,
                         "оставьте в системе ровно две раскладки: русскую и английскую",
                         key="layouts"))

    def uinput():
        return uinput_status()
    checks.append(_check("Можно печатать исправления (/dev/uinput)", uinput,
                         "перезагрузите компьютер; если не помогло — переустановите "
                         "WaySwitch той же командой, которой устанавливали",
                         key="uinput"))

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
        return bool(found), f"клавиатур найдено: {len(found)}, без доступа: {denied}"
    checks.append(_check("Клавиатура доступна", keyboards,
                         "перезагрузите компьютер (доступ к клавиатуре выдаётся при "
                         "входе в систему)", key="keyboards"))

    def udev_rule():
        # install.sh кладёт правило в /etc, .deb — в /usr/lib.
        found = [p for p in ("/etc/udev/rules.d/60-wayswitch.rules",
                             "/usr/lib/udev/rules.d/60-wayswitch.rules") if Path(p).exists()]
        return bool(found), found[0] if found else ""
    checks.append(_check("Доступ к клавиатуре настроен (правило udev)", udev_rule,
                         "переустановите WaySwitch той же командой, которой устанавливали"))

    def unit():
        # .deb кладёт юнит системно, install.sh — в профиль пользователя.
        candidates = (Path("/usr/lib/systemd/user/wayswitch.service"),
                      Path.home() / ".config/systemd/user/wayswitch.service")
        found = [str(c) for c in candidates if c.exists()]
        return bool(found), found[0] if found else ""
    checks.append(_check("Автозапуск установлен", unit,
                         "необязательно: переустановите WaySwitch той же командой, "
                         "которой устанавливали", required=False))

    def service_running():
        import subprocess
        try:
            out = subprocess.run(["systemctl", "--user", "is-active", "wayswitch"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"systemctl недоступен ({exc})"
        return out == "active", out
    # Sway, Hyprland без uwsm и сеансы на labwc/Wayfire не поднимают
    # graphical-session.target — включённая служба там сама не стартует.
    hint = ("systemctl --user start wayswitch; для автозапуска добавьте в конфиг "
            + ("exec-once = wayswitch run" if desktop == HYPRLAND else "exec wayswitch run")
            if desktop in (SWAY, HYPRLAND) else
            "systemctl --user enable --now wayswitch; журнал: journalctl --user -u wayswitch")
    checks.append(_check("Служба WaySwitch работает", service_running, hint,
                         required=False, key="service"))

    if desktop == GNOME:
        def tools():
            return shutil.which("gnome-extensions") is not None, ""
        checks.append(_check("gnome-extensions", tools, "нужно только для расширения",
                             required=False))
    return checks


def format_report(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        # Необязательное — не крестиком: на исправной системе их бывает
        # несколько, и человек решил бы, что всё сломано.
        mark = "✓" if c.ok else ("✗" if c.required else "○")
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
