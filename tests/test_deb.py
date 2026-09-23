"""Тесты Debian-упаковки: всё, что можно проверить без Linux/dpkg."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.debversion import to_debian

ROOT = Path(__file__).resolve().parents[1]
DEB_DIR = ROOT / "packaging" / "deb"

DEPENDS = [
    "python3 (>= 3.11)",
    "python3-evdev",
    "python3-gi",
    "gir1.2-gtk-4.0",
    "gir1.2-adw-1",
    "gir1.2-ibus-1.0",
    "gir1.2-glib-2.0",
    "libxkbcommon0",
    "udev",
]


# --- debversion.to_debian --------------------------------------------------


@pytest.mark.parametrize(
    ("py_version", "expected"),
    [
        ("2.0.0a1", "2.0.0~a1"),
        ("2.0.0rc1", "2.0.0~rc1"),
        ("2.0.0", "2.0.0"),
        ("2.1.0b3", "2.1.0~b3"),
    ],
)
def test_to_debian(py_version: str, expected: str):
    assert to_debian(py_version) == expected


def test_debversion_cli(capsys):
    from tools import debversion

    assert debversion.main(["2.0.0a1"]) == 0
    assert capsys.readouterr().out.strip() == "2.0.0~a1"


# --- control.in --------------------------------------------------------


def test_control_in_has_required_fields():
    text = (DEB_DIR / "control.in").read_text(encoding="utf-8")
    assert "Architecture: all" in text
    assert "Version: @VERSION@" in text
    for pkg in DEPENDS:
        assert pkg in text, pkg


def test_control_in_is_lf():
    raw = (DEB_DIR / "control.in").read_bytes()
    assert b"\r\n" not in raw


# --- maintainer-скрипты и build_deb.sh ------------------------------------


@pytest.mark.parametrize(
    ("path", "shebang"),
    [
        (DEB_DIR / "postinst", b"#!/bin/sh"),
        (DEB_DIR / "postrm", b"#!/bin/sh"),
        (ROOT / "tools" / "build_deb.sh", b"#!/bin/bash"),
    ],
)
def test_scripts_are_lf_and_correct_shebang(path: Path, shebang: bytes):
    raw = path.read_bytes()
    assert b"\r\n" not in raw, path
    assert raw.startswith(shebang), path


@pytest.mark.parametrize(
    "relpath",
    ["packaging/deb/postinst", "packaging/deb/postrm", "tools/build_deb.sh"],
)
def test_scripts_are_executable_in_git_index(relpath: str):
    try:
        out = subprocess.run(
            ["git", "ls-files", "-s", relpath],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("git недоступен")
    assert out.strip(), relpath
    mode = out.split()[0]
    assert mode == "100755", f"{relpath}: mode={mode}"


def test_build_deb_rewrites_exec_start():
    text = (ROOT / "tools" / "build_deb.sh").read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/bin/wayswitch run" in text
    assert "ExecStart=/usr/bin/wayswitch run" in text
