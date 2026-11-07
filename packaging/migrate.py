"""Remove obsolete WaySwitch integration files identified by their own metadata."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

REPO_URL = "https://github.com/Wetoshkin/WaySwitch"
EXTENSION_UUID = "wayswitch@wetoshkin.github.io"
DESKTOP_NAME = "io.github.Wetoshkin.WaySwitch.Settings.desktop"
ICON_NAME = "io.github.Wetoshkin.WaySwitch"
PLUGIN_ID = "io.github.Wetoshkin.wayswitch"


def _text(path: Path, root: Path) -> str:
    if root.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("outside integration directory")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("not a regular file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("not a regular file")
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("integration metadata too large")
    return data.decode("utf-8")


def _metadata(folder: Path, root: Path) -> dict:
    try:
        if folder.is_symlink():
            return {}
        value = json.loads(_text(folder / "metadata.json", root))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError, RecursionError):
        return {}


def _desktop(path: Path, root: Path):
    try:
        if "wayswitch" not in path.name.lower():
            return None
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(_text(path, root))
        if parser.defaults():
            return None
        entry = parser["Desktop Entry"]
        command = shlex.split(entry.get("Exec", ""))
        if (entry.get("Type") == "Application" and entry.get("Name") == "WaySwitch"
                and command and command[0] in
                {"wayswitch-gui", "/usr/bin/wayswitch-gui", "/usr/local/bin/wayswitch-gui"}
                and command[1:] in ([], ["--tray"])):
            return entry
    except (OSError, UnicodeError, ValueError, configparser.Error, KeyError):
        pass
    return None


def disable_extension(uuid: str) -> None:
    try:
        subprocess.run(["gnome-extensions", "disable", uuid], check=False, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def clean_extensions(root: Path, *, uninstall: bool = False, disable: bool = True) -> None:
    if root.is_symlink():
        return
    for folder in root.glob("*"):
        meta = _metadata(folder, root)
        if (meta.get("name") != "WaySwitch" or meta.get("url") != REPO_URL
                or meta.get("uuid") != folder.name
                or re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+", folder.name) is None
                or not uninstall and folder.name == EXTENSION_UUID):
            continue
        if disable:
            disable_extension(folder.name)
        shutil.rmtree(folder)


def clean_prefix(prefix: Path) -> None:
    prefix = prefix.resolve()
    share = prefix / "share"
    if share.is_symlink() or not share.resolve().is_relative_to(prefix):
        return
    apps = prefix / "share/applications"
    for path in apps.glob("*.desktop"):
        if path.name == DESKTOP_NAME or (entry := _desktop(path, apps)) is None:
            continue
        icon = entry.get("Icon", "")
        if (icon != ICON_NAME and icon.endswith(".WaySwitch")
                and path.stem in (icon, f"{icon}.Settings")
                and re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", icon)):
            for kind, suffix in (("scalable", ""), ("symbolic", "-symbolic"),
                                 ("symbolic", "-paused-symbolic")):
                icons = prefix / f"share/icons/hicolor/{kind}/apps"
                target = icons / f"{icon}{suffix}.svg"
                if not icons.is_symlink() and target.resolve().is_relative_to(prefix.resolve()):
                    target.unlink(missing_ok=True)
        path.unlink()
    root = prefix / "share/plasma/plasmoids"
    if not root.is_symlink() and root.resolve().is_relative_to(prefix):
        for folder in root.glob("*"):
            meta = _metadata(folder, root).get("KPlugin", {})
            if (isinstance(meta, dict) and meta.get("Name") == "WaySwitch"
                    and meta.get("Website") == REPO_URL and meta.get("Id") == folder.name
                    and folder.name != PLUGIN_ID):
                shutil.rmtree(folder)
    root = prefix / "share/gnome-shell/extensions"
    if root.resolve().is_relative_to(prefix):
        clean_extensions(root, disable=False)


def clean_autostart(root: Path) -> None:
    for path in root.glob("*.desktop"):
        if _desktop(path, root) is not None:
            path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path)
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if args.prefix is not None:
        clean_prefix(args.prefix)
    else:
        data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        clean_extensions(data / "gnome-shell/extensions", uninstall=args.uninstall)
        if args.uninstall:
            config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
            clean_autostart(config / "autostart")


if __name__ == "__main__":
    main()
