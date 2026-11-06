"""Portable clients must not inspect sandbox devices or manage host services."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_get_sh import shell as shell
from wayswitch import updates
from wayswitch.gui import portable


def test_package_mode_is_explicit(monkeypatch):
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.delenv("WAYSWITCH_PACKAGE", raising=False)
    assert portable.package_kind() is None
    assert portable.application_id() == "ru.siberia.WaySwitch.Settings"
    monkeypatch.setenv("FLATPAK_ID", "ru.siberia.WaySwitch.Gui")
    assert portable.package_kind() == "flatpak"
    assert portable.application_id() == "ru.siberia.WaySwitch.Gui"
    monkeypatch.delenv("FLATPAK_ID")
    monkeypatch.setenv("WAYSWITCH_PACKAGE", "appimage")
    assert portable.package_kind() == "appimage"
    assert portable.application_id() == "ru.siberia.WaySwitch.AppImage"


def test_diagnostics_reports_connection_not_sandbox_devices(monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", "ru.siberia.WaySwitch.Gui")
    missing = portable.connection_report(None)
    running = portable.connection_report({"backend": "shell", "paused": True})
    assert "systemctl --user enable --now wayswitch.service" in missing
    assert "wayswitch doctor" in missing
    assert "D-Bus: connected" in running and "shell" in running
    assert "/dev/input" not in missing + running


def test_lifecycle_instructions_keep_native_companion_separate(monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", "ru.siberia.WaySwitch.Gui")
    assert "flatpak update ru.siberia.WaySwitch.Gui" in portable.instructions("update")
    assert "flatpak install --user --or-update ./WaySwitch-Settings.flatpak" in \
        portable.instructions("update")
    assert "SHA256SUMS" in portable.instructions("update")
    remove = portable.instructions("remove")
    assert "flatpak uninstall ru.siberia.WaySwitch.Gui" in remove
    assert "--uninstall" in remove and "get.sh" in remove
    monkeypatch.delenv("FLATPAK_ID")
    monkeypatch.setenv("WAYSWITCH_PACKAGE", "appimage")
    assert "AppImage" in portable.instructions("update")
    assert "AppImage" in portable.instructions("remove")


def test_flatpak_permissions_only_allow_client_contract():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "packaging/flatpak/ru.siberia.WaySwitch.Gui.json")
                          .read_text(encoding="utf-8"))
    args = manifest["finish-args"]
    assert "--talk-name=ru.siberia.WaySwitch" in args
    assert "--filesystem=xdg-config/wayswitch:create" in args
    assert not any("device=" in a or "flatpak" in a or "filesystem=host" in a
                   or "filesystem=home" in a for a in args)


def test_flatpak_tray_owns_only_application_namespace(monkeypatch):
    from wayswitch.gui.tray import notifier_name

    monkeypatch.setenv("FLATPAK_ID", "ru.siberia.WaySwitch.Gui")
    assert notifier_name(123) == "ru.siberia.WaySwitch.Gui.Tray"
    monkeypatch.delenv("FLATPAK_ID")
    assert notifier_name(123) == "org.kde.StatusNotifierItem-123-1"


def test_flatpak_launcher_respects_custom_host_config():
    root = Path(__file__).resolve().parents[1]
    wrapper = (root / "packaging/flatpak/wayswitch-gui").read_text(encoding="utf-8")
    assert '${HOST_XDG_CONFIG_HOME:-$HOME/.config}' in wrapper


@pytest.mark.parametrize("launcher", ["packaging/flatpak/wayswitch-gui",
                                      "packaging/appimage/AppRun"])
def test_packaged_python_ignores_callers_cwd(tmp_path, launcher):
    root = Path(__file__).resolve().parents[1]
    script = (root / launcher).read_text(encoding="utf-8")
    flags = re.search(r'(?:exec python3|python3\.12")\s+((?:-\w+\s+)*)-c', script)[1].split()
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    (trusted / "wayswitch.py").write_text("VALUE = 'trusted'\n")
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "wayswitch.py").write_text("raise SystemExit(67)\n")
    result = subprocess.run([sys.executable, *flags, "-c",
                             "import wayswitch;print(wayswitch.VALUE)"],
                            cwd=hostile, env=dict(os.environ, PYTHONPATH=str(trusted)),
                            capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "trusted"


def test_flatpak_bundle_declares_official_runtime_source():
    root = Path(__file__).resolve().parents[1]
    script = (root / "tools/build_flatpak.sh").read_text(encoding="utf-8")
    assert "--runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo" in script


def test_shared_portable_metadata_does_not_claim_sandbox_confinement():
    root = Path(__file__).resolve().parents[1]
    meta = (root / "packaging/flatpak/ru.siberia.WaySwitch.Gui.metainfo.xml").read_text()
    assert "this sandbox" not in meta


@pytest.mark.parametrize("case", ["valid", "corrupt", "missing-record"])
def test_displayed_flatpak_update_verifies_only_target_and_gates_install(
        shell, tmp_path, monkeypatch, case):
    import hashlib

    monkeypatch.setenv("FLATPAK_ID", portable.FLATPAK_ID)
    commands = portable.instructions("update").split("выполните:\n\n", 1)[1].split("\n\n", 1)[0]
    payload = b"verified local bundle"
    bundle = tmp_path / "WaySwitch-Settings.flatpak"
    bundle.write_bytes(b"corrupt bundle" if case == "corrupt" else payload)
    record = ("WaySwitch-Settings.flatpak.bak" if case == "missing-record"
              else "WaySwitch-Settings.flatpak")
    manifest = "\n".join(f"{hashlib.sha256(payload).hexdigest()}  {name}" for name in
                         ["wayswitch.deb", record, "wayswitch.rpm", "WaySwitch.AppImage"])
    (tmp_path / "SHA256SUMS").write_text(manifest + "\n", newline="\n")
    result = subprocess.run([shell, "-c",
                             'flatpak() { printf "%s\\n" "$*" > installed.txt; }\n' + commands],
                            cwd=tmp_path, capture_output=True, text=True)
    assert (result.returncode == 0) is (case == "valid"), result.stdout + result.stderr
    assert (tmp_path / "installed.txt").exists() is (case == "valid")
    if case == "valid":
        assert "FAILED" not in result.stdout + result.stderr
        assert (tmp_path / "installed.txt").read_text().strip() == \
            "install --user --or-update ./WaySwitch-Settings.flatpak"


def test_is_system_packaged_detects_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(
        portable, "__file__", str(tmp_path / "wayswitch" / "gui" / "portable.py")
    )
    assert portable.is_system_packaged() is False
    (tmp_path / "wayswitch").mkdir()
    (tmp_path / "wayswitch" / ".packaged").write_text("")
    assert portable.is_system_packaged() is True


def test_check_skips_when_system_packaged(monkeypatch, tmp_path):
    monkeypatch.setattr(updates.portable, "is_system_packaged", lambda: True)
    calls = []
    assert updates.check("1.0.0", path=tmp_path / "c.json",
                         fetch=lambda *a, **k: calls.append(1)) is None
    assert calls == []


def test_can_self_update_follows_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(
        portable, "__file__", str(tmp_path / "wayswitch" / "gui" / "portable.py")
    )
    assert portable.can_self_update() is True
    (tmp_path / "wayswitch").mkdir()
    (tmp_path / "wayswitch" / ".packaged").write_text("")
    assert portable.can_self_update() is False
