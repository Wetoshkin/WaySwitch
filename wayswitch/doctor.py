"""Проверка окружения перед первым запуском: что есть, чего не хватает, как починить.

Проверки зависят от среды: в GNOME раскладку отдаёт наше расширение Shell, в
KDE — D-Bus KWin, в прочих средах прочитать её неоткуда (только ручной жест).
Поддерживается только Wayland.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

from wayswitch.backends.desktop import (
    CINNAMON,
    COMPOSITOR_DESKTOPS,
    COSMIC,
    DESKTOP_NAMES,
    GNOME,
    HYPRLAND,
    KDE,
    SWAY,
    UMBRIEL,
    detect_desktop,
    is_wayland,
)
from wayswitch.backends.gnome_shell import TYPING_PROTOCOL_VERSION
from wayswitch.i18n import gettext as _


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


# Состояние расширения GNOME Shell → (ok, ключ проверки для окна, обязательна
# ли, текст). Один источник и для `wayswitch doctor`, и для первого запуска.
EXTENSION_UUID = "wayswitch@wetoshkin.github.io"
RELOGIN = "sign out and back in so GNOME loads the new version"
EXTENSION_STATES = {
    "ok": (True, "extension", True, ""),
    "missing": (
        False,
        "extension_missing",
        True,
        "extension is not installed: reinstall WaySwitch using the original installation command",
    ),
    "disabled": (
        False,
        "extension",
        True,
        "extension is disabled: enable it with the Enable button in WaySwitch or "
        "gnome-extensions enable wayswitch@wetoshkin.github.io",
    ),
    # Включено, но GNOME его ещё не загрузил (поставили в этом сеансе) или
    # держит старую копию после обновления: кнопкой «Включить» тут ничего не
    # изменить, нужен новый вход. Не блокируем: старая копия умеет главное.
    "not_loaded": (
        False,
        "extension_relogin",
        False,
        "extension is enabled but GNOME will only load it after signing in: " + RELOGIN,
    ),
    "outdated": (False, "extension_relogin", False, "an older extension is running: " + RELOGIN),
}


def extension_state(installed: bool, enabled: bool, running: bool, version: int | None) -> str:
    """Одно из EXTENSION_STATES по фактам о расширении. Чистая функция."""
    if running:
        return "outdated" if version is not None and version < TYPING_PROTOCOL_VERSION else "ok"
    if not installed:
        return "missing"
    return "not_loaded" if enabled else "disabled"


def _extension_installed() -> bool:
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    home = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
    return any(
        (Path(d) / "gnome-shell/extensions" / EXTENSION_UUID / "metadata.json").exists()
        for d in [home, *data_dirs]
        if d
    )


def _extension_enabled() -> bool:
    import subprocess

    def get(key: str) -> str:
        try:
            return subprocess.run(
                ["gsettings", "get", "org.gnome.shell", key],
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    return EXTENSION_UUID in get("enabled-extensions") and EXTENSION_UUID not in get(
        "disabled-extensions"
    )


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
        return False, (
            _(
                "access denied to /dev/uinput: uaccess requires an active local "
                "session; a separate RDP session may lack device access"
            )
        )
    except OSError as e:
        return False, _(
            "cannot open /dev/uinput ({error}) — is the uinput module loaded?"
        ).format(error=e.strerror or e)
    os.close(fd)
    return True, ""


def toolkit_accessibility() -> tuple[bool, str]:
    """Включён ли мост доступности в GTK-программах.

    Без него at-spi видит только оболочку: защищённое автоисправление ждёт
    достоверного фокуса, исключения в GNOME также используют идентификаторы окна.
    """
    import subprocess

    try:
        out = (
            subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "toolkit-accessibility"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            .stdout.strip()
            .lower()
        )
    except (OSError, subprocess.SubprocessError):
        return False, _("this setting is unavailable in the current environment")
    if out == "true":
        return True, ""
    return False, (
        _(
            "gsettings set org.gnome.desktop.interface toolkit-accessibility true and "
            "restart applications"
        )
    )


def atspi_status() -> tuple[bool, str]:
    """Отвечает ли мост доступности: без него не узнать приложение и поле пароля.

    Без достоверного фокуса автоисправление с защитой полей паролей ждёт,
    ручное исправление остаётся доступным.
    """
    from wayswitch.a11y import bridge_available

    return bridge_available()


def focus_status() -> tuple[bool, str]:
    """Свежие идентификаторы приложения без заголовков окна и текста поля."""
    from wayswitch.a11y import query_focus

    if detect_desktop() == UMBRIEL:
        from wayswitch.backends import umbriel

        path = umbriel.find_socket()
        if path is None or umbriel.query_input_locked(umbriel.UmbrielIpc(path)):
            return False, _(
                "Umbriel: an active regular window could not be confirmed; input is locked"
            )
    snap = query_focus()
    names = ", ".join(snap.app_ids)
    detail = f"AT-SPI: {snap.app or _('application unknown')}"
    if names:
        detail += _("; window ID: {names}").format(names=names)
    if not snap.app:
        return False, detail + (
            _("; with password protection enabled, auto-correction waits for verified focus")
        )
    if snap.password:
        return False, detail + _("; protected input (such as a password or terminal)")
    return True, detail + _("; regular input field recognized")


def daemon_status() -> tuple[bool, str]:
    from wayswitch.cli import call_daemon
    from wayswitch.config import BACKEND_PREFS

    try:
        (status,) = call_daemon("GetStatus")
    except Exception:  # noqa: BLE001 — в отчёт не попадают сообщения чужой службы D-Bus
        return False, _("Daemon is unavailable over D-Bus; auto-correction state is unknown")
    if not isinstance(status, dict):
        return False, _("Invalid daemon response; auto-correction state is unknown")

    backend = status.get("backend")
    if backend not in BACKEND_PREFS:
        backend = _("unknown")
    layouts = {"ru": _("Russian"), "latin": _("Latin"), "other": _("other")}
    layout = layouts.get(str(status.get("layout")), _("undetermined"))
    details = [_("Backend: {backend}, layout: {layout}").format(backend=backend, layout=layout)]
    auto = status.get("auto_correct")
    if auto is False:
        details.append(_("Auto-correction is disabled in settings (auto_correct = false)"))
    elif auto is True:
        details.append(_("Auto-correction is enabled in settings"))

    flags = (
        "auto_correct",
        "active",
        "paused",
        "locked",
        "lock_confirmation_required",
        "dry_run",
        "layout_supported",
    )
    complete = all(isinstance(status.get(flag), bool) for flag in flags)
    blockers = [
        message
        for key, value, message in (
            ("paused", True, _("daemon is paused")),
            ("locked", True, _("session is locked")),
            ("lock_confirmation_required", True, _("resuming after unlock requires confirmation")),
            ("dry_run", True, _("observation mode (dry-run); corrections are not typed")),
            ("layout_supported", False, _("current layout is unsupported or undetermined")),
        )
        if status.get(key) is value
    ]
    if not complete:
        blockers.append(_("incomplete daemon response; auto-correction state is unknown"))
    elif auto and not blockers and not status["active"]:
        blockers.append(_("backend does not support automatic correction"))
    details.extend(blockers)
    return auto is True and not blockers, ". ".join(details)


def run_checks() -> list[Check]:
    checks = []
    desktop = detect_desktop()
    try:
        nixos = platform.freedesktop_os_release().get("ID") == "nixos"
    except OSError:
        nixos = False
    nixos_hint = _(
        "on NixOS, enable the WaySwitch module from docs/nixos.md and run sudo nixos-rebuild switch"
    )

    def session():
        wayland = is_wayland()
        return wayland, DESKTOP_NAMES[desktop]

    checks.append(
        _check(
            _("Wayland session"),
            session,
            _(
                "X11 graphics mode is active; WaySwitch requires Wayland: on the login"
                " screen, choose a session without Xorg using the gear menu"
            ),
            key="wayland",
        )
    )

    checks.append(
        _check(
            _("Application and password field recognition (GTK / GNOME setting)"),
            toolkit_accessibility,
            _(
                "without verified focus, auto-correction waits when password "
                "protection is enabled; the manual gesture remains available"
            ),
            required=False,
        )
    )
    checks.append(
        _check(
            _("Application and password field recognition (at-spi service)"),
            atspi_status,
            _(
                "without verified focus, auto-correction waits when password "
                "protection is enabled; the manual gesture remains available"
            ),
            required=False,
        )
    )
    focus_hint = _(
        "after running wayswitch doctor --delay 5, focus a regular input field in the "
        "desired app and keep it focused until checking finishes"
    )
    if desktop == GNOME:
        focus_hint += _(
            "; GNOME window IDs require updating the extension and signing out and back in"
        )
    checks.append(
        _check(
            _("Active application (without text or window title)"),
            focus_status,
            focus_hint,
            required=False,
            key="focus",
        )
    )
    checks.append(
        _check(
            _("Auto-correction in the running daemon"),
            daemon_status,
            "",
            required=False,
            key="daemon",
        )
    )

    cosmic_map = None
    umbriel_specs = None
    if desktop == COSMIC:

        def cosmic():
            nonlocal cosmic_map
            from wayswitch.backends.cosmic import CosmicBackend

            backend = CosmicBackend(watch=False)
            try:
                cosmic_map = backend.keymap
                return True, (
                    _(
                        "group {group}; after startup and unlock, confirm "
                        "input with the WaySwitch switch or wayswitch resume"
                    ).format(group=backend.current())
                )
            finally:
                backend.close()

        checks.append(
            _check(
                _("COSMIC layouts (Wayland)"),
                cosmic,
                _("sign into COSMIC; zcosmic_keyboard_layout_manager_v1 is required"),
                key="switch",
            )
        )

    if desktop == GNOME:
        # На GNOME 46+ без расширения автоисправления нет: IBus меняет движок,
        # но не раскладку компоузитора. Поэтому проверка обязательная — кроме
        # случаев, когда помочь может только новый вход в систему.
        try:
            from wayswitch.backends.gnome_shell import extension_version, shell_extension_present

            running = shell_extension_present()
            state = extension_state(
                _extension_installed(),
                _extension_enabled(),
                running,
                extension_version() if running else None,
            )
        except Exception as exc:  # noqa: BLE001 — нет D-Bus сеанса
            state, ok_override = "missing", str(exc)
        else:
            ok_override = ""
        ok, key, required, text = EXTENSION_STATES[state]
        checks.append(Check(_("GNOME Shell extension"), ok, ok_override or _(text), required, key))

        def ibus():
            import gi

            gi.require_version("IBus", "1.0")
            from gi.repository import IBus

            bus = IBus.Bus()
            if not bus.is_connected():
                return False, _("IBus is not responding")
            desc = bus.get_global_engine()
            return True, _("current engine {engine}").format(
                engine=desc.get_name() if desc else "?"
            )

        checks.append(
            _check(
                _("Fallback layout switching (IBus)"),
                ibus,
                _("optional: only needed without the WaySwitch extension"),
                required=False,
            )
        )

    if desktop == KDE:

        def kde_service():
            from wayswitch.backends.kde_plasma import kde_available

            return kde_available(), "org.kde.keyboard"

        checks.append(
            _check(
                _("KWin exposes layouts (org.kde.keyboard)"),
                kde_service,
                _("KDE Plasma with at least two layouts is required"),
            )
        )

    if desktop == SWAY:

        def sway_ipc():
            from wayswitch.backends import sway

            path = sway.find_socket()
            if path is None:
                return False, _("sway socket not found")
            sway.SwayIpc(path).request(sway.GET_INPUTS)
            return True, "Sway IPC"

        checks.append(
            _check(
                _("Layout switching (Sway IPC)"),
                sway_ipc,
                _(
                    "start the service from sway: add exec systemctl --user "
                    "import-environment SWAYSOCK to the sway config"
                ),
                key="switch",
            )
        )

    if desktop == HYPRLAND:

        def hypr_ipc():
            from wayswitch.backends import hyprland

            directory = hyprland.find_dir()
            if directory is None:
                return False, _("Hyprland socket not found")
            hyprland.HyprIpc(directory).request("version")
            return True, "Hyprland IPC"

        checks.append(
            _check(
                _("Layout switching (Hyprland IPC)"),
                hypr_ipc,
                _("start the service from Hyprland (uwsm or exec-once)"),
                key="switch",
            )
        )

    if desktop == UMBRIEL:

        def umbriel_ipc():
            nonlocal umbriel_specs
            from wayswitch.backends import sway, umbriel

            path = umbriel.find_socket()
            if path is None:
                return False, _("Umbriel socket not found")
            names, index = umbriel.keyboard_state(
                umbriel.UmbrielIpc(path).request({"cmd": "keyboard-layouts"})
            )
            umbriel_specs = sway.specs_from_names(names)
            return True, _("Umbriel IPC; group {index}").format(index=index)

        checks.append(
            _check(
                _("Layout switching (Umbriel IPC)"),
                umbriel_ipc,
                _(
                    "start the service from Umbriel; pass UMBRIEL_SOCKET and "
                    "WAYLAND_DISPLAY to the user systemd environment"
                ),
                key="switch",
            )
        )

    if desktop in COMPOSITOR_DESKTOPS:

        def compositor_xkb():
            from wayswitch.backends import compositor

            specs, options, where = compositor.read_xkb()
            if not specs:
                return False, _("compositor layouts not found")
            if not compositor.toggle_combos(options):
                return False, _("no layout switching shortcut in xkb_options ({where})").format(
                    where=where
                )
            if not compositor.cycle_supported(options, len(specs)):
                return False, (
                    _(
                        "the counter cannot confirm layout switching with these "
                        "sources and shortcuts; the service starts passively. For 3–4 "
                        "layouts, use grp:alt_shift_toggle without remapping Shift"
                    )
                )
            return True, f"{where}: {', '.join(s.xkb_id for s in specs)}"

        checks.append(
            _check(
                _("Layout switching shortcut (labwc / Wayfire)"),
                compositor_xkb,
                _(
                    "set XKB_DEFAULT_LAYOUT=us,ru and "
                    "XKB_DEFAULT_OPTIONS=grp:alt_shift_toggle in "
                    "~/.config/labwc/environment (or xkb_layout / xkb_options in "
                    "wayfire.ini [input])"
                ),
                required=False,
                key="switch",
            )
        )

    def modules():
        import evdev  # noqa: F401
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401

        return True, ""

    checks.append(
        _check(
            _("Required libraries (evdev, GTK 4, libadwaita)"),
            modules,
            nixos_hint
            if nixos
            else "apt install python3-evdev python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 "
            "(dnf: python3-evdev python3-gobject gtk4 libadwaita, "
            "pacman: python-evdev python-gobject gtk4 libadwaita)",
        )
    )

    def xkb():
        from wayswitch.keymap import XkbKeymap

        return XkbKeymap.available(), ""

    checks.append(
        _check(
            _("Keyboard layout library (libxkbcommon)"),
            xkb,
            nixos_hint
            if nixos
            else "apt install libxkbcommon0 / dnf install libxkbcommon / pacman -S libxkbcommon",
        )
    )

    def sources():
        """Наличие ru/en без ограничения на остальные источники."""
        from wayswitch.keymap import XkbKeymap

        if cosmic_map is not None:
            ready = any(
                cosmic_map.opposite_group(g) is not None for g in range(cosmic_map.num_groups)
            )
            return ready, ", ".join(s.xkb_id for s in cosmic_map.specs)
        if desktop == UMBRIEL:
            specs = umbriel_specs
        elif desktop == KDE:
            from wayswitch.backends.kde_plasma import KdePlasmaBackend

            backend = KdePlasmaBackend()
            try:
                specs = backend.layouts()
            finally:
                backend.close()
        elif desktop in (GNOME, CINNAMON):
            from wayswitch.backends.gnome_common import read_sources

            specs = read_sources()
        elif desktop == SWAY:
            from wayswitch.backends import sway

            path = sway.find_socket()
            if path is None:
                return False, _("sway is not responding (socket not found)")
            names, _i = sway.keyboard_state(sway.SwayIpc(path).request(sway.GET_INPUTS))
            specs = sway.specs_from_names(names)
        elif desktop == HYPRLAND:
            from wayswitch.backends import hyprland, xkbnames

            directory = hyprland.find_dir()
            if directory is None:
                return False, _("Hyprland is not responding (socket not found)")
            ipc = hyprland.HyprIpc(directory)
            specs = xkbnames.parse_layout_list(
                hyprland._clean(ipc.json("getoption input:kb_layout").get("str", "")),
                hyprland._clean(ipc.json("getoption input:kb_variant").get("str", "")),
            )
        else:
            from wayswitch.backends import compositor

            specs, _o, _w = compositor.read_xkb()
            if not specs:
                return False, _("compositor layouts not found")
        km = XkbKeymap.from_names(specs, [])
        ready = any(km.opposite_group(g) is not None for g in range(km.num_groups))
        return ready, ", ".join(s.xkb_id for s in specs)

    if (desktop != COSMIC or cosmic_map is not None) and (
        desktop != UMBRIEL or umbriel_specs is not None
    ):
        checks.append(
            _check(
                _("Russian and English layouts"),
                sources,
                _(
                    "add Russian and English layouts; other layouts can stay. "
                    "Corrections require the ru/en pair"
                ),
                required=False,
                key="layouts",
            )
        )
    from wayswitch.keymap import LAYOUT_SCOPE

    checks.append(
        Check(
            _("WaySwitch working languages"), True, LAYOUT_SCOPE, required=False, key="layout_scope"
        )
    )

    def uinput():
        return uinput_status()

    checks.append(
        _check(
            _("Correction typing (/dev/uinput)"),
            uinput,
            _(
                "restart the computer; if that does not help, reinstall WaySwitch "
                "using the original installation command"
            ),
            key="uinput",
        )
    )

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
        return bool(found), _("keyboards found: {count}, access denied: {denied}").format(
            count=len(found), denied=denied
        )

    checks.append(
        _check(
            _("Keyboard access"),
            keyboards,
            _("restart the computer (keyboard access is granted at login)"),
            key="keyboards",
        )
    )

    def udev_rule():
        # install.sh кладёт правило в /etc, .deb — в /usr/lib.
        found = [
            p
            for p in (
                "/etc/udev/rules.d/60-wayswitch.rules",
                "/usr/lib/udev/rules.d/60-wayswitch.rules",
            )
            if Path(p).exists()
        ]
        return bool(found), found[0] if found else ""

    checks.append(
        _check(
            _("Keyboard access configured (udev rule)"),
            udev_rule,
            nixos_hint
            if nixos
            else _("reinstall WaySwitch using the original installation command"),
        )
    )

    def unit():
        # .deb кладёт юнит системно, install.sh — в профиль пользователя.
        candidates = (
            Path("/usr/lib/systemd/user/wayswitch.service"),
            Path("/etc/systemd/user/wayswitch.service"),
            Path.home() / ".config/systemd/user/wayswitch.service",
        )
        found = [str(c) for c in candidates if c.exists()]
        return bool(found), found[0] if found else ""

    checks.append(
        _check(
            _("Autostart installed"),
            unit,
            nixos_hint
            if nixos
            else _("optional: reinstall WaySwitch using the original installation command"),
            required=False,
        )
    )

    def service_running():
        import subprocess

        try:
            out = subprocess.run(
                ["systemctl", "--user", "is-active", "wayswitch"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            return False, _("systemctl unavailable ({exc})").format(exc=exc)
        return out == "active", out

    # Sway, Hyprland без uwsm и сеансы на labwc/Wayfire не поднимают
    # graphical-session.target — включённая служба там сама не стартует.
    if desktop == UMBRIEL:
        hint = _("systemctl --user start wayswitch; add wayswitch run to Umbriel autostart")
    elif desktop in (SWAY, HYPRLAND):
        hint = _("systemctl --user start wayswitch; for autostart, add to the config: ") + (
            "exec-once = wayswitch run" if desktop == HYPRLAND else "exec wayswitch run"
        )
    else:
        hint = _("systemctl --user enable --now wayswitch; logs: journalctl --user -u wayswitch")
    checks.append(
        _check(_("WaySwitch service running"), service_running, hint, required=False, key="service")
    )

    if desktop == GNOME:

        def tools():
            return shutil.which("gnome-extensions") is not None, ""

        checks.append(
            _check("gnome-extensions", tools, _("only needed for the extension"), required=False)
        )
    return checks


def format_report(checks: list[Check]) -> str:
    lines = []
    daemon_line = ""
    for c in checks:
        # Необязательное — не крестиком: на исправной системе их бывает
        # несколько, и человек решил бы, что всё сломано.
        mark = "✓" if c.ok else ("✗" if c.required else "○")
        line = f"{mark} {c.name}"
        if c.hint:
            line += f"\n    {c.hint}"
        if c.key == "daemon":
            daemon_line = line
        else:
            lines.append(line)
    bad = [c for c in checks if not c.ok and c.required]
    lines.append("")
    if bad:
        lines.append(_("Required checks failed: {count}.").format(count=len(bad)))
    else:
        lines.append(_("Required checks passed."))
        if any(c.key == "focus" and not c.ok for c in checks):
            lines.append(
                _(
                    "With password protection enabled, auto-correction is unconfirmed:"
                    " verified input focus is required."
                )
            )
    if daemon_line:
        lines.append(daemon_line)
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(not c.ok and c.required for c in checks) else 0
