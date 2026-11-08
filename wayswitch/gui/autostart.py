"""Автозапуск и управление службой: systemd user-сервис демона и XDG-autostart
значка в панели."""

from __future__ import annotations

import configparser
import logging
import os
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("wayswitch.gui")

SERVICE = "wayswitch.service"
# Имя файла автозапуска совпадает с записью приложения: так GNOME считает это
# тем же приложением, а не отдельной записью.
DESKTOP_NAME = "io.github.Wetoshkin.WaySwitch.Settings.desktop"
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
        "Icon=io.github.Wetoshkin.WaySwitch", "Terminal=false", "X-GNOME-Autostart-enabled=true",
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


def _tray_entry(path: Path, *, generated_only: bool = False) -> bool | None:
    """None means an unrelated or unsafe file; False is our disabled entry."""
    if "wayswitch" not in path.name.lower() or path.suffix != ".desktop":
        return None
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            data = stream.read(16385)
        if len(data) > 16384:
            return None
        desktop = configparser.ConfigParser(interpolation=None)
        desktop.optionxform = str
        desktop.read_string(data.decode("utf-8"))
        if desktop.defaults():
            return None
        values = desktop["Desktop Entry"]
        command = shlex.split(values.get("Exec", ""))
        if (values.get("Type") != "Application" or values.get("Name") != "WaySwitch"
                or not command or command[0] not in
                {"wayswitch-gui", "/usr/bin/wayswitch-gui", "/usr/local/bin/wayswitch-gui"}
                or command[1:] not in ([], ["--tray"])):
            return None
        if generated_only:
            keys = {"Type", "Name", "Comment", "Exec", "Icon", "Terminal", "Hidden",
                    "X-GNOME-Autostart-enabled", "X-GNOME-Autostart-Delay"}
            if (set(values) - keys or desktop.sections() != ["Desktop Entry"]
                    or command != ["wayswitch-gui", "--tray"]
                    or values.get("Terminal", "false") != "false"
                    or values.get("X-GNOME-Autostart-Delay", "3") != "3"):
                return None
        hidden = values.get("Hidden", "false")
        enabled = values.get("X-GNOME-Autostart-enabled", "true")
        if hidden not in {"true", "false"} or enabled not in {"true", "false"}:
            return None
        return hidden == "false" and enabled == "true"
    except (OSError, UnicodeError, configparser.Error, KeyError, ValueError):
        return None


def is_tray_enabled() -> bool:
    folder = _autostart_dir()
    current = _tray_entry(folder / DESKTOP_NAME)
    if current is not None:
        return current
    return any(_tray_entry(path) is True for path in folder.glob("*.desktop"))


def set_tray_enabled(on: bool) -> None:
    folder = _autostart_dir()
    path = folder / DESKTOP_NAME
    if on:
        folder.mkdir(parents=True, exist_ok=True)
        # Never overwrite a foreign entry or follow a link at our current name.
        if (path.exists() or path.is_symlink()) and _tray_entry(path) is None:
            raise FileExistsError(path)
        fd, temporary = tempfile.mkstemp(dir=folder, prefix=".wayswitch-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(tray_desktop_text())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    for candidate in folder.glob("*.desktop"):
        if (not on or candidate != path) and _tray_entry(candidate) is not None:
            candidate.unlink()


def migrate_tray() -> bool:
    """Migrate recognized old entries; report conflicts without breaking startup."""
    try:
        entries = [path for path in _autostart_dir().glob("*.desktop")
                   if _tray_entry(path) is not None]
        if not any(path.name != DESKTOP_NAME for path in entries):
            return False
        # Условия пользовательского запуска нельзя заменить стандартным значком.
        if any(_tray_entry(path, generated_only=True) is None for path in entries):
            return False
        set_tray_enabled(is_tray_enabled())
        return True
    except OSError as error:
        log.warning("WaySwitch autostart migration failed (%s)", type(error).__name__)
        return False
