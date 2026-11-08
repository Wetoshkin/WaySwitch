"""Build the GUI AppImage from integrity-checked Ubuntu 24.04 dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLATPAK_ID = "io.github.Wetoshkin.WaySwitch.Gui"
APPIMAGE_ID = "io.github.Wetoshkin.WaySwitch.AppImage"


# GitHub Releases иногда отвечает 5xx/таймаутом при загрузке runtime.
# Повторяем только временные сетевые ошибки; хэш проверяется после каждой попытки.
TRANSIENT_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})


def fetch_verified(url: str, target: Path, sha256: str, *, opener=urllib.request.urlopen,
                   sleep=time.sleep, attempts: int = 6, base_delay: float = 1.5) -> None:
    if target.exists():
        with target.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == sha256:
                return
    target.unlink(missing_ok=True)
    partial = target.with_suffix(".partial")
    last_error: Exception | None = None
    try:
        for attempt in range(attempts):
            partial.unlink(missing_ok=True)
            try:
                with opener(url, timeout=120) as source, partial.open("wb") as dest:
                    shutil.copyfileobj(source, dest)
            except urllib.error.HTTPError as exc:
                if exc.code not in TRANSIENT_HTTP:
                    raise
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            else:
                with partial.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != sha256:
                        raise ValueError(f"SHA256 mismatch: {url}")
                partial.replace(target)
                return
            if attempt + 1 < attempts:
                sleep(base_delay * (2 ** attempt))
        assert last_error is not None
        raise last_error
    finally:
        partial.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    appdir = output / "WaySwitch.AppDir"
    if appdir.exists():
        raise SystemExit(f"Refusing to replace existing build tree: {appdir}")
    cache = output / "downloads"
    cache.mkdir(exist_ok=True)
    lock = json.loads((ROOT / "packaging/appimage/dependencies.json").read_text())
    appdir.mkdir()

    def download(package):
        target = cache / package["file"]
        if target.parent != cache or not target.name.endswith(".deb"):
            raise ValueError("Invalid dependency filename")
        fetch_verified(package["url"], target, package["sha256"])

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(download, lock["packages"]))
    for package in lock["packages"]:
        target = cache / package["file"]
        subprocess.run(["dpkg-deb", "--extract", str(target), str(appdir)], check=True)
    common = lock["common_licenses"]
    download(common)
    with tempfile.TemporaryDirectory(prefix="licenses-", dir=output) as temporary:
        subprocess.run(["dpkg-deb", "--extract", str(cache / common["file"]), temporary],
                        check=True)
        shutil.copytree(Path(temporary) / "usr/share/common-licenses",
                        appdir / "usr/share/common-licenses")
    runtime = cache / "runtime-x86_64"
    fetch_verified(lock["runtime"]["url"], runtime, lock["runtime"]["sha256"])
    shutil.copytree(ROOT / "wayswitch", appdir / "usr/share/wayswitch/wayswitch",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("LICENSE", "DATA-LICENSE"):
        source = ROOT / ("LICENSE" if name == "LICENSE" else "wayswitch/data/LICENSE")
        dest = appdir / "usr/share/licenses/wayswitch" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    shutil.copyfile(ROOT / "packaging/appimage/runtime-LICENSE",
                    appdir / "usr/share/licenses/wayswitch/runtime-LICENSE")
    shutil.copyfile(ROOT / "packaging/appimage/AppRun", appdir / "AppRun")
    (appdir / "AppRun").chmod(0o755)
    icon = ROOT / "packaging/icons/io.github.Wetoshkin.WaySwitch.svg"
    shutil.copyfile(icon, appdir / f"{APPIMAGE_ID}.svg")
    desktop = (ROOT / f"packaging/flatpak/{FLATPAK_ID}.desktop").read_text()
    (appdir / f"{APPIMAGE_ID}.desktop").write_text(
        desktop.replace(FLATPAK_ID, APPIMAGE_ID).replace("Exec=wayswitch-gui", "Exec=AppRun"))
    icons = appdir / "usr/share/icons/hicolor/scalable/apps"
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(icon, icons / icon.name)
    shutil.copyfile(icon, icons / f"{APPIMAGE_ID}.svg")
    symbols = appdir / "usr/share/icons/hicolor/symbolic/apps"
    symbols.mkdir(parents=True, exist_ok=True)
    for symbol in (ROOT / "packaging/icons").glob("*-symbolic.svg"):
        shutil.copyfile(symbol, symbols / symbol.name)
    meta = appdir / "usr/share/metainfo"
    meta.mkdir(parents=True, exist_ok=True)
    metadata = (ROOT / f"packaging/flatpak/{FLATPAK_ID}.metainfo.xml").read_text()
    (meta / f"{APPIMAGE_ID}.metainfo.xml").write_text(metadata.replace(FLATPAK_ID, APPIMAGE_ID))
    shutil.copyfile(ROOT / "packaging/appimage/dependencies.json",
                    appdir / "usr/share/licenses/wayswitch/dependencies.json")
    # Python3.12's minimal package needs the complete standard library too (locked below).
    if not (appdir / "usr/lib/python3.12/urllib/request.py").is_file():
        raise SystemExit("Incomplete Python standard library")
    certs = sorted((appdir / "usr/share/ca-certificates/mozilla").glob("*.crt"))
    if not certs:
        raise SystemExit("Missing trusted CA certificates")
    (appdir / "usr/share/wayswitch/ca-certificates.crt").write_bytes(
        b"\n".join(cert.read_bytes() for cert in certs))
    libs = appdir / "usr/lib/x86_64-linux-gnu"
    subprocess.run([str(libs / "ld-linux-x86-64.so.2"), "--library-path", str(libs),
                    str(appdir / "usr/bin/glib-compile-schemas"),
                    str(appdir / "usr/share/glib-2.0/schemas")], check=True)
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "1793577600"))
    squashfs = output / "wayswitch.squashfs"
    env = dict(os.environ)
    env.pop("SOURCE_DATE_EPOCH", None)
    subprocess.run(["mksquashfs", str(appdir), str(squashfs), "-noappend", "-all-root",
                    "-no-xattrs", "-comp", "zstd", "-mkfs-time", str(epoch),
                    "-all-time", str(epoch)], check=True, env=env)
    artifact = output / "WaySwitch-Settings-x86_64.AppImage"
    with artifact.open("wb") as dest:
        for source in (runtime, squashfs):
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, dest)
    artifact.chmod(0o755)
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "SHA256SUMS").write_text(f"{digest}  {artifact.name}\n")
    print(artifact)


if __name__ == "__main__":
    main()
