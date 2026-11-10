"""Build the GUI AppImage from integrity-checked Ubuntu 24.04 dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
RELEASE_DOWNLOAD = re.compile(
    r"https://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/]+)")

# Snapshot Service иногда отвечает 5xx на закреплённый pool-файл. Такой же
# неизменный файл лежит на официальных archive/security-зеркалах Ubuntu.
SNAPSHOT_POOL = re.compile(
    r"https://snapshot\.ubuntu\.com/ubuntu/\d{8}T\d{6}Z/pool/(main|universe)/"
    r"(?P<path>[A-Za-z0-9][A-Za-z0-9._+~%:/-]*\.deb)")
UBUNTU_MIRRORS = ("https://archive.ubuntu.com/ubuntu",
                  "https://security.ubuntu.com/ubuntu")


def _release_fallback(url: str) -> tuple[str, dict[str, str]] | None:
    """Прямая ссылка API на тот же asset, если браузерная 5xx.

    GitHub отдаёт файлы релиза и через github.com/releases/download, и через
    API assets с Accept: application/octet-stream. Второй маршрут иногда живёт,
    когда первый отвечает 504. Содержимое всё равно проверяется по sha256.
    """
    match = RELEASE_DOWNLOAD.fullmatch(url)
    if match is None:
        return None
    owner, repo, tag, name = match.groups()
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "wayswitch-appimage-build"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            release = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    for asset in release.get("assets", []):
        if asset.get("name") == name and isinstance(asset.get("url"), str):
            return asset["url"], {"Accept": "application/octet-stream",
                                  "User-Agent": "wayswitch-appimage-build"}
    return None


class DownloadError(RuntimeError):
    """Загрузка не удалась; сообщение содержит URL, цель и последнюю ошибку."""


def _ubuntu_fallbacks(url: str) -> tuple[str, ...]:
    """Зеркала официального архива для строго закреплённого snapshot pool-файла.

    Принимаем только неизменяемый путь snapshot. Query, userinfo, чужие хосты,
    traversal и закодированные разделители не переписываются на зеркала.
    """
    match = SNAPSHOT_POOL.fullmatch(url)
    if match is None:
        return ()
    path = match["path"]
    if "//" in path or "\\" in path:
        return ()
    for segment in path.split("/"):
        if segment in {".", ".."} or re.search(r"%(?:2e|2f|3f|23|5c)", segment,
                                               re.IGNORECASE):
            return ()
    return tuple(f"{mirror}/pool/{match[1]}/{path}" for mirror in UBUNTU_MIRRORS)


def _download_error(url: str, target: Path, last_url: str,
                    error: Exception) -> DownloadError:
    last = f" (last URL: {last_url})" if last_url != url else ""
    return DownloadError(f"download failed: {url} -> {target}{last}: {error}")


def fetch_verified(url: str, target: Path, sha256: str, *, opener=urllib.request.urlopen,
                   sleep=time.sleep, attempts: int = 6, base_delay: float = 1.5) -> None:
    if target.exists():
        with target.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == sha256:
                return
    target.unlink(missing_ok=True)
    partial = target.with_suffix(".partial")
    last_error: Exception | None = None
    last_url = url
    current_url, headers = url, None
    fallback_checked = False
    ubuntu_candidates: tuple[str, ...] = ()
    ubuntu_index = -1
    ubuntu_active = False
    try:
        for attempt in range(attempts):
            partial.unlink(missing_ok=True)
            last_url = current_url
            try:
                request = (urllib.request.Request(current_url, headers=headers)
                           if headers else current_url)
                with opener(request, timeout=120) as source, partial.open("wb") as dest:
                    shutil.copyfileobj(source, dest)
            except urllib.error.HTTPError as exc:
                if exc.code not in TRANSIENT_HTTP:
                    if (ubuntu_active and exc.code == 404
                            and ubuntu_index + 1 < len(ubuntu_candidates)):
                        last_error = exc
                        ubuntu_index += 1
                        current_url = ubuntu_candidates[ubuntu_index]
                        headers = None
                        continue
                    raise _download_error(url, target, current_url, exc) from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            else:
                with partial.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != sha256:
                        raise ValueError(f"SHA256 mismatch: {current_url} -> {target}")
                partial.replace(target)
                return
            if not fallback_checked:
                fallback_checked = True
                fallback = _release_fallback(url)
                if fallback is not None:
                    current_url, headers = fallback
                else:
                    mirrors = _ubuntu_fallbacks(url)
                    if mirrors:
                        # Сначала официальные зеркала, затем ограниченный
                        # повтор исходного snapshot в пределах общих попыток.
                        ubuntu_candidates = (*mirrors, url)
                        ubuntu_active = True
                        ubuntu_index = 0
                        current_url = ubuntu_candidates[0]
                        headers = None
            elif ubuntu_active:
                if ubuntu_index + 1 < len(ubuntu_candidates):
                    ubuntu_index += 1
                    current_url = ubuntu_candidates[ubuntu_index]
                else:
                    ubuntu_active = False
                    current_url = url
                headers = None
            if attempt + 1 < attempts:
                sleep(base_delay * (2 ** attempt))
        assert last_error is not None
        raise _download_error(url, target, last_url, last_error) from last_error
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
    # Python читает каталог из пакета, а стандартный share/locale нужен для
    # единообразного пути с KI18n и другими сборками.
    locale = appdir / "usr/share/locale/ru/LC_MESSAGES"
    locale.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "wayswitch/locale/ru/LC_MESSAGES/wayswitch.mo",
                    locale / "wayswitch.mo")
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
    png = icon.with_suffix(".png")
    shutil.copyfile(png, appdir / f"{APPIMAGE_ID}.png")
    shutil.copyfile(png, appdir / ".DirIcon")
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
