"""Проверить язык первого запуска готового AppImage через AT-SPI под Xvfb."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path


def accessible_names(desktop) -> set[str]:
    names = set()
    pending = [desktop]
    visited = 0
    while pending and visited < 2000:
        node = pending.pop()
        visited += 1
        try:
            if node.name:
                names.add(node.name)
            pending.extend(node.getChildAtIndex(i) for i in range(node.childCount))
        except Exception:
            # Во время запуска приложения объекты AT-SPI могут исчезать.
            continue
    return names


def main() -> None:
    import pyatspi
    from gi.repository import GLib

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("appimage", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    artifact = args.appimage.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {"sha256": digest, "locale": "C", "language": "ru", "success": False}
    desktop = pyatspi.Registry.getDesktop(0)
    with tempfile.TemporaryDirectory(prefix="profile-", dir=output) as profile:
        environment = dict(os.environ, LC_ALL="C", LANG="C", LANGUAGE="ru",
                           GDK_BACKEND="x11", GTK_A11Y="atspi", GSETTINGS_BACKEND="memory")
        environment.pop("NO_AT_BRIDGE", None)
        for variable, directory in (("XDG_CONFIG_HOME", "config"),
                                    ("XDG_CACHE_HOME", "cache"),
                                    ("XDG_DATA_HOME", "data")):
            environment[variable] = str(Path(profile) / directory)
        with (output / "startup.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([str(artifact), "--appimage-extract-and-run"],
                                       cwd=profile, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                deadline = time.monotonic() + 60
                required = {"Welcome to WaySwitch", "Start"}
                observed = set()
                while time.monotonic() < deadline:
                    while GLib.MainContext.default().iteration(False):
                        pass
                    observed = accessible_names(desktop)
                    if required <= observed:
                        break
                    if process.poll() is not None:
                        raise RuntimeError(
                            f"AppImage exited before its window: {process.returncode}")
                    time.sleep(0.25)
                report["accessible_names"] = sorted(observed)
                assert required <= observed, f"Missing English UI: {sorted(required - observed)}"
                untranslated = [name for name in observed
                                if re.search("[А-Яа-яЁё]", re.sub("привет", "", name,
                                                                 flags=re.IGNORECASE))]
                assert not untranslated, f"Unexpected Russian UI: {untranslated}"
                subprocess.run(["scrot", str(output / "C.png")], check=True)
                report["success"] = True
            finally:
                (output / "report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
    print("AppImage under LC_ALL=C and LANGUAGE=ru: English UI confirmed")


if __name__ == "__main__":
    main()
