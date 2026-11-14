"""Run real Gio dispatch on a private session bus, never the user's desktop bus."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("dbus-run-session"),
                    reason="requires Linux Gio and dbus-run-session")
def test_real_owner_events_payload_and_idle_dispatch():
    pytest.importorskip("gi")
    result = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(Path(__file__).with_name(
            "smoke_shell_context_dbus.py"))], capture_output=True, text=True,
        timeout=25, env={**os.environ, "WAYSWITCH_PRIVATE_BUS_TEST": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
