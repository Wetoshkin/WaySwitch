"""Integrity of build inputs and intentional source staging."""

import hashlib
import importlib.util
import io
import json
import re
import struct
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = (
    "https://snapshot.ubuntu.com/ubuntu/20261106T000000Z/pool/main/p/pcre2/"
    "libpcre2-8-0_10.42-4ubuntu2.2_amd64.deb"
)
ARCHIVE = (
    "https://archive.ubuntu.com/ubuntu/pool/main/p/pcre2/"
    "libpcre2-8-0_10.42-4ubuntu2.2_amd64.deb"
)
SECURITY = (
    "https://security.ubuntu.com/ubuntu/pool/main/p/pcre2/"
    "libpcre2-8-0_10.42-4ubuntu2.2_amd64.deb"
)


def request_url(request):
    return request if isinstance(request, str) else request.full_url


def builder():
    spec = importlib.util.spec_from_file_location("build_appimage",
                                                 ROOT / "tools/build_appimage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verified_download_rejects_corrupted_cache(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"verified artifact")
    cache = tmp_path / "cache"
    cache.write_bytes(b"corrupt")
    builder().fetch_verified(source.as_uri(), cache,
                             hashlib.sha256(source.read_bytes()).hexdigest())
    assert cache.read_bytes() == b"verified artifact"


def test_verified_download_rejects_wrong_digest(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"truncated")
    target = tmp_path / "cache"
    with pytest.raises(ValueError, match="SHA256"):
        builder().fetch_verified(source.as_uri(), target, "0" * 64)
    assert not target.exists()


def test_verified_download_retries_transient_http_errors(tmp_path):
    payload = b"runtime bytes"
    sha256 = hashlib.sha256(payload).hexdigest()
    calls = []

    def opener(url, timeout):
        calls.append((url, timeout))
        if len(calls) < 3:
            raise urllib.error.HTTPError(url, 504, "Gateway Time-out", {}, None)
        return io.BytesIO(payload)

    sleeps = []
    builder().fetch_verified("https://example.invalid/runtime", tmp_path / "cache", sha256,
                             opener=opener, sleep=sleeps.append, base_delay=0.5)
    assert (tmp_path / "cache").read_bytes() == payload
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]


def test_verified_download_does_not_retry_permanent_http_errors(tmp_path):
    calls = []

    def opener(url, timeout):
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    sleeps = []
    module = builder()
    target = tmp_path / "cache"
    with pytest.raises(module.DownloadError, match="404") as error:
        module.fetch_verified("https://example.invalid/runtime", target,
                              "0" * 64, opener=opener, sleep=sleeps.append)
    assert len(calls) == 1 and not sleeps
    assert "https://example.invalid/runtime" in str(error.value)
    assert str(target) in str(error.value)


def test_verified_download_reports_error_after_retries(tmp_path):
    calls = []

    def opener(url, timeout):
        calls.append(url)
        raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)

    sleeps = []
    module = builder()
    target = tmp_path / "cache"
    with pytest.raises(module.DownloadError, match="503") as error:
        module.fetch_verified("https://example.invalid/runtime", target,
                              "0" * 64, opener=opener, sleep=sleeps.append,
                              attempts=3, base_delay=0.25)
    assert len(calls) == 3 and sleeps == [0.25, 0.5]
    assert not target.exists()
    assert "https://example.invalid/runtime" in str(error.value)
    assert str(target) in str(error.value)


def test_verified_download_falls_back_to_api_asset(tmp_path, monkeypatch):
    payload = b"runtime bytes"
    sha256 = hashlib.sha256(payload).hexdigest()
    primary = "https://github.com/o/r/releases/download/tag/runtime-x86_64"
    asset = "https://api.github.com/repos/o/r/releases/assets/1"
    calls = []

    def opener(request, timeout):
        url = request if isinstance(request, str) else request.full_url
        headers = None if isinstance(request, str) else dict(request.headers)
        calls.append((url, headers))
        if url == primary:
            raise urllib.error.HTTPError(url, 504, "Gateway Time-out", {}, None)
        return io.BytesIO(payload)

    module = builder()
    monkeypatch.setattr(module, "_release_fallback", lambda url: (asset, {
        "Accept": "application/octet-stream"}))
    module.fetch_verified(primary, tmp_path / "cache", sha256, opener=opener,
                          sleep=lambda _delay: None, base_delay=0.1)
    assert (tmp_path / "cache").read_bytes() == payload
    assert [url for url, _ in calls] == [primary, asset]
    assert calls[1][1]["Accept"] == "application/octet-stream"


def test_ubuntu_fallbacks_only_for_strict_snapshot_pool():
    module = builder()
    assert module._ubuntu_fallbacks(SNAPSHOT) == (ARCHIVE, SECURITY)
    encoded = (
        "https://snapshot.ubuntu.com/ubuntu/20261106T000000Z/pool/main/x/xz-utils/"
        "liblzma5_5.6.1%2breally5.4.5-1ubuntu0.4_amd64.deb"
    )
    assert module._ubuntu_fallbacks(encoded)[0] == (
        "https://archive.ubuntu.com/ubuntu/pool/main/x/xz-utils/"
        "liblzma5_5.6.1%2breally5.4.5-1ubuntu0.4_amd64.deb"
    )


@pytest.mark.parametrize("url", [
    SNAPSHOT.replace("https://", "http://"),
    SNAPSHOT + "?download=1",
    SNAPSHOT.replace("snapshot.ubuntu.com", "mirror.example"),
    SNAPSHOT.replace("https://", "https://user@"),
    SNAPSHOT.replace("/pool/main/", "/pool/../main/"),
    SNAPSHOT.replace("/pool/main/", "/pool/restricted/"),
    SNAPSHOT.replace("/pool/main/", "/pool/%2e%2e/main/"),
    SNAPSHOT.replace("/pcre2/", "/pcre2/%2f/"),
])
def test_ubuntu_fallbacks_reject_unsafe_or_unknown_urls(url):
    assert builder()._ubuntu_fallbacks(url) == ()


def test_ubuntu_snapshot_transient_uses_archive_mirror(tmp_path):
    payload = b"ubuntu package bytes"
    sha256 = hashlib.sha256(payload).hexdigest()
    calls = []

    def opener(request, timeout):
        url = request_url(request)
        calls.append(url)
        if url == SNAPSHOT:
            raise urllib.error.HTTPError(url, 502, "Bad Gateway", {}, None)
        assert url == ARCHIVE
        return io.BytesIO(payload)

    sleeps = []
    target = tmp_path / "cache"
    builder().fetch_verified(SNAPSHOT, target, sha256, opener=opener,
                             sleep=sleeps.append, base_delay=0.5)
    assert target.read_bytes() == payload
    assert calls == [SNAPSHOT, ARCHIVE]
    assert sleeps == [0.5]
    assert not target.with_suffix(".partial").exists()


def test_ubuntu_mirror_404_tries_next_candidate(tmp_path):
    payload = b"ubuntu package bytes"
    sha256 = hashlib.sha256(payload).hexdigest()
    calls = []

    def opener(request, timeout):
        url = request_url(request)
        calls.append(url)
        if url == SNAPSHOT:
            raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)
        if url == ARCHIVE:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        assert url == SECURITY
        return io.BytesIO(payload)

    sleeps = []
    builder().fetch_verified(SNAPSHOT, tmp_path / "cache", sha256, opener=opener,
                             sleep=sleeps.append, base_delay=0.5)
    assert calls == [SNAPSHOT, ARCHIVE, SECURITY]
    assert sleeps == [0.5]


def test_ubuntu_fallback_is_bounded_and_retries_primary_once(tmp_path):
    calls = []

    def opener(request, timeout):
        url = request_url(request)
        calls.append(url)
        if url == SNAPSHOT:
            raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    sleeps = []
    module = builder()
    target = tmp_path / "cache"
    with pytest.raises(module.DownloadError, match="503") as error:
        module.fetch_verified(SNAPSHOT, target, "0" * 64, opener=opener,
                              sleep=sleeps.append, attempts=4, base_delay=0.25)
    assert calls == [SNAPSHOT, ARCHIVE, SECURITY, SNAPSHOT]
    assert sleeps == [0.25]
    assert not target.exists()
    assert not target.with_suffix(".partial").exists()
    assert SNAPSHOT in str(error.value)
    assert str(target) in str(error.value)


def test_snapshot_permanent_404_does_not_use_mirrors(tmp_path):
    calls = []

    def opener(url, timeout):
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    sleeps = []
    module = builder()
    with pytest.raises(module.DownloadError, match="404"):
        module.fetch_verified(SNAPSHOT, tmp_path / "cache", "0" * 64,
                              opener=opener, sleep=sleeps.append)
    assert calls == [SNAPSHOT]
    assert not sleeps


def test_non_ubuntu_url_never_uses_ubuntu_mirrors(tmp_path):
    calls = []

    def opener(url, timeout):
        calls.append(url)
        raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)

    module = builder()
    with pytest.raises(module.DownloadError, match="503"):
        module.fetch_verified("https://example.invalid/package.deb", tmp_path / "cache",
                              "0" * 64, opener=opener, sleep=lambda _delay: None,
                              attempts=2, base_delay=0.1)
    assert calls == ["https://example.invalid/package.deb"] * 2


def test_wrong_digest_from_mirror_fails_without_retry_or_fallback(tmp_path):
    calls = []

    def opener(request, timeout):
        url = request_url(request)
        calls.append(url)
        if url == SNAPSHOT:
            raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)
        return io.BytesIO(b"corrupt package")

    target = tmp_path / "cache"
    with pytest.raises(ValueError, match="SHA256"):
        builder().fetch_verified(SNAPSHOT, target, "0" * 64, opener=opener,
                                 sleep=lambda _delay: None)
    assert calls == [SNAPSHOT, ARCHIVE]
    assert not target.exists()
    assert not target.with_suffix(".partial").exists()


def test_verified_download_uses_valid_cache_without_network(tmp_path):
    payload = b"cached package"
    target = tmp_path / "cache"
    target.write_bytes(payload)

    def opener(_request, _timeout):
        pytest.fail("valid cache must not use the network")

    builder().fetch_verified(SNAPSHOT, target, hashlib.sha256(payload).hexdigest(),
                             opener=opener)


def test_common_licenses_have_a_verified_source():
    lock = json.loads((ROOT / "packaging/appimage/dependencies.json").read_text())
    source = lock["common_licenses"]
    assert source["file"].startswith("base-files_")
    assert source["url"].startswith("https://") and len(source["sha256"]) == 64


def test_appdir_has_png_directory_icon_matching_desktop(tmp_path, monkeypatch):
    module = builder()
    output = tmp_path / "build"
    appdir = output / "WaySwitch.AppDir"
    monkeypatch.setattr(sys, "argv", ["build_appimage.py", str(output)])
    monkeypatch.setattr(module, "fetch_verified", lambda _url, target, _hash:
                        target.write_bytes(b"verified dependency"))

    def run(command, **_kwargs):
        if command[0] == "dpkg-deb":
            target = Path(command[-1])
            if target != appdir:
                (target / "usr/share/common-licenses").mkdir(parents=True)
            else:
                for name in ("usr/lib/python3.12/urllib/request.py",
                             "usr/share/ca-certificates/mozilla/test.crt"):
                    path = target / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"dependency")
        elif command[0] == "mksquashfs":
            Path(command[2]).write_bytes(b"squashfs")

    monkeypatch.setattr(module.subprocess, "run", run)
    module.main()
    directory_icon = (appdir / ".DirIcon").read_bytes()
    assert directory_icon[:8] == b"\x89PNG\r\n\x1a\n"
    assert directory_icon[12:16] == b"IHDR"
    assert struct.unpack(">II", directory_icon[16:24]) == (256, 256)
    desktop = (appdir / f"{module.APPIMAGE_ID}.desktop").read_text()
    name = re.search(r"^Icon=(.+)$", desktop, re.M).group(1)
    assert (appdir / f"{name}.png").read_bytes() == directory_icon


def test_ubuntu_inputs_use_a_single_immutable_snapshot():
    lock = json.loads((ROOT / "packaging/appimage/dependencies.json").read_text())
    snapshots = set()
    for package in [*lock["packages"], lock["common_licenses"]]:
        match = re.fullmatch(
            r"https://snapshot\.ubuntu\.com/ubuntu/(\d{8}T\d{6}Z)/pool/.+\.deb",
            package["url"],
        )
        assert match, package["file"]
        snapshots.add(match[1])
    assert len(snapshots) == 1
