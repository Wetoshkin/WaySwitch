"""Автозапуск: systemd user-сервис демона и XDG-autostart трея."""

from __future__ import annotations

import subprocess
from pathlib import Path

SERVICE = "wayswitch.service"
DESKTOP_NAME = "ru.siberia.WaySwitch.desktop"


def _autostart_dir() -> Path:
    import os

    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def tray_desktop_text() -> str:
    return "\n".join([
        "[Desktop Entry]", "Type=Application", "Name=WaySwitch",
        "Comment=Значок WaySwitch в трее", "Exec=wayswitch-gui --tray",
        "Icon=input-keyboard", "Terminal=false", "X-GNOME-Autostart-enabled=true",
        "X-GNOME-Autostart-Delay=3", "",
    ])


def is_service_enabled() -> bool:
    r = subprocess.run(["systemctl", "--user", "is-enabled", SERVICE],
                       capture_output=True, text=True)
    return r.stdout.strip() == "enabled"


def set_service_enabled(on: bool) -> None:
    subprocess.run(["systemctl", "--user", "enable" if on else "disable", "--now", SERVICE],
                   check=False)


def is_tray_enabled() -> bool:
    return (_autostart_dir() / DESKTOP_NAME).exists()


def set_tray_enabled(on: bool) -> None:
    path = _autostart_dir() / DESKTOP_NAME
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tray_desktop_text(), encoding="utf-8")
    elif path.exists():
        path.unlink()
