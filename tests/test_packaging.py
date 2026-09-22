from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "packaging"


def test_udev_rules_use_uaccess():
    text = (ROOT / "60-wayswitch.rules").read_text(encoding="utf-8")
    assert 'KERNEL=="uinput"' in text and 'TAG+="uaccess"' in text
    assert 'ENV{ID_INPUT_KEYBOARD}=="1"' in text and 'ENV{ID_INPUT_MOUSE}=="1"' in text
    assert "GROUP=" not in text  # без групп: права даёт uaccess


def test_service_unit():
    text = (ROOT / "wayswitch.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/bin/wayswitch run" in text
    assert "After=graphical-session.target" in text
    assert "WantedBy=default.target" in text


def test_scripts_are_lf_and_executable_shebang():
    for name in ("install.sh", "uninstall.sh"):
        raw = (ROOT / name).read_bytes()
        assert b"\r\n" not in raw, name
        assert raw.startswith(b"#!/bin/bash"), name
