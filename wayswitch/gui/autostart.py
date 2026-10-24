"""Автозапуск и управление службой: systemd user-сервис демона и XDG-autostart
значка в панели."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger("wayswitch.gui")

SERVICE = "wayswitch.service"
# Имя файла автозапуска совпадает с записью приложения: так GNOME считает это
# тем же приложением, а не отдельной записью.
DESKTOP_NAME = "ru.siberia.WaySwitch.Settings.desktop"
# Так файл назывался до 2.0.0a2: у обновившихся он остался в autostart, и без
# миграции включение значка давало два трея.
LEGACY_DESKTOP_NAMES = ("ru.siberia.WaySwitch.desktop",)
# systemctl обычно отвечает за доли секунды; зависший менеджер не должен
# подвешивать окно.
TIMEOUT_SEC = 10


def _autostart_dir() -> Path:
    import os

    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def tray_desktop_text() -> str:
    return "\n".join([
        "[Desktop Entry]", "Type=Application", "Name=WaySwitch",
        "Comment=Значок WaySwitch в верхней панели", "Exec=wayswitch-gui --tray",
        "Icon=ru.siberia.WaySwitch", "Terminal=false", "X-GNOME-Autostart-enabled=true",
        "X-GNOME-Autostart-Delay=3", "",
    ])


def _systemctl(*args: str) -> subprocess.CompletedProcess | None:
    # systemctl может отсутствовать (не systemd-система) или зависнуть — это не
    # повод ронять GUI.
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                              timeout=TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("systemctl %s: %s", " ".join(args), e)
        return None


def is_service_enabled() -> bool:
    r = _systemctl("is-enabled", SERVICE)
    return r is not None and r.stdout.strip() == "enabled"


def set_service_enabled(on: bool) -> None:
    _systemctl("enable" if on else "disable", "--now", SERVICE)


def start_service() -> bool:
    r = _systemctl("start", SERVICE)
    return r is not None and r.returncode == 0


def stop_service() -> bool:
    r = _systemctl("stop", SERVICE)
    return r is not None and r.returncode == 0


def is_tray_enabled() -> bool:
    folder = _autostart_dir()
    return any((folder / name).exists() for name in (DESKTOP_NAME, *LEGACY_DESKTOP_NAMES))


def set_tray_enabled(on: bool) -> None:
    folder = _autostart_dir()
    for name in LEGACY_DESKTOP_NAMES:
        legacy = folder / name
        if legacy.exists():
            legacy.unlink()
    path = folder / DESKTOP_NAME
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tray_desktop_text(), encoding="utf-8")
    elif path.exists():
        path.unlink()
