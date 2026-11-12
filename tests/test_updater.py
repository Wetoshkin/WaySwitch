"""Update sources and package-manager boundaries, without a live Linux host."""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_get_sh import shell as shell
from wayswitch import updater, updates


def runner(answers):
    def run(argv):
        answer = answers.get(tuple(argv), (1, "", "not found"))
        return subprocess.CompletedProcess(argv, *answer)
    return run


def test_owned_downstream_package_never_uses_github(tmp_path):
    module = tmp_path / "wayswitch" / "__init__.py"
    module.parent.mkdir()
    (module.parent / ".packaged").touch()
    (module.parent / ".update-source").write_text("github")
    run = runner({("dpkg-query", "-S", str(module)): (0, f"wayswitch: {module}\n", ""),
                  ("dpkg-query", "-W", "-f=${Version}", "wayswitch"): (0, "1:1.0.0-2", "")})
    source = updater.detect(module=module, env={}, run=run, os_release={"ID": "debian"})
    assert (source.kind, source.manager, source.package, source.installed) == (
        "system", "apt", "wayswitch", "1:1.0.0-2")


def test_unowned_installation_has_no_github_fallback(tmp_path):
    source = updater.detect(module=tmp_path / "__init__.py", env={}, run=runner({}),
                            os_release={"ID": "arch"})
    assert source.kind == "unknown"
    info = updater.check("1.1.4", source=source, run=runner({}))
    assert not info.available and not info.automatic and not info.command


def test_local_arch_package_is_not_assumed_to_be_aur(tmp_path):
    module = tmp_path / "__init__.py"
    run = runner({("pacman", "-Qoq", str(module)): (0, "wayswitch\n", ""),
                  ("pacman", "-Q", "wayswitch"): (0, "wayswitch 1:1.1.4-1\n", ""),
                  ("pacman", "-Qm", "wayswitch"): (0, "wayswitch 1:1.1.4-1\n", "")})
    source = updater.detect(module=module, env={}, run=run, os_release={"ID": "arch"})
    assert source.kind == "unknown" and source.package == "wayswitch"


def test_apt_candidate_includes_packaging_revision():
    source = updater.Source("system", "apt", "wayswitch", "1:1.1.4-1")
    run = runner({("apt-get", "-s", "install", "wayswitch"): (
        0, "Inst wayswitch [1:1.1.4-1] (1:1.1.4-2 repo [all])\n", "")})
    info = updater.check("1.1.4", source=source, run=run)
    assert info.available.version == "1:1.1.4-2"
    assert "apt-get" in info.command and "get.sh" not in info.command


def test_failed_repo_check_does_not_report_up_to_date():
    source = updater.Source("system", "apt", "wayswitch", "1.1.4")
    info = updater.check("1.1.4", source=source, run=runner({}))
    assert info.state == "error" and not info.available


def test_background_github_network_failure_is_not_up_to_date(monkeypatch, tmp_path):
    import urllib.request

    monkeypatch.setattr(updates, "cache_path", lambda: tmp_path / "updates.json")

    def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    info = updater.check("1.1.4", source=updater.Source("github", "apt", "wayswitch"))
    assert info.state == "error" and not info.available


def test_aur_updates_use_existing_helper_and_not_upstream_comparator():
    source = updater.Source("aur", "pacman", "wayswitch", "1:1.1.4-1")
    info = updater.check("1.1.4", source=source,
                         run=runner({("vercmp", "1:1.1.4-2", "1:1.1.4-1"): (0, "1", "")}),
                         which=lambda name: "/usr/bin/paru" if name == "paru" else None,
                         aur_fetch=lambda name: "1:1.1.4-2")
    assert info.available.version == "1:1.1.4-2"
    assert "paru -S aur/wayswitch" in info.command
    assert "--noconfirm" not in info.command and "get.sh" not in info.command


@pytest.mark.parametrize("candidate", ["1.1.3-1", "1.1.4-1"])
def test_aur_no_update_does_not_offer_install(candidate):
    source = updater.Source("aur", "pacman", "wayswitch", "1.1.4-1")
    info = updater.check("1.1.4", source=source,
                         run=runner({("vercmp", candidate, "1.1.4-1"): (0, "-1", "")}),
                         aur_fetch=lambda name: candidate)
    assert info.state == "current" and not info.available


def test_flatpak_never_attempts_host_execution():
    source = updater.detect(env={"FLATPAK_ID": "io.github.Wetoshkin.WaySwitch.Gui"},
                            run=lambda _: pytest.fail("host probe in sandbox"))
    info = updater.check("1.1.4", source=source, run=runner({}))
    assert source.kind == "flatpak" and not info.automatic
    assert "flatpak update" in info.command and "get.sh" not in info.command


def test_nix_installation_cannot_be_overwritten_by_installer():
    source = updater.detect(module=Path("/nix/store/abc/wayswitch/__init__.py"),
                            env={}, run=runner({}))
    assert source.kind == "nix"
    assert not updater.check("1.1.4", source=source).automatic


def test_download_plan_is_bound_to_release_and_original_format():
    source = updater.Source("github", "apt", "wayswitch", "1.1.3")
    command = updater.github_command(source, updates.Release("1.1.4", ""))
    assert "/releases/download/v1.1.4/" in command
    assert "wayswitch_1.1.4_all.deb" in command
    assert "SHA256SUMS" in command and "sha256sum -c" in command
    assert "get.sh" not in command and "git clone" not in command


def test_native_copy_command_rechecks_source_instead_of_freezing_shell_internals():
    info = updater.UpdateInfo(updater.Source("system", "apt", "wayswitch", "1.1.4-1"),
                               updates.Release("1.1.4-2", ""), "available", "native job", True)
    assert updater.manual_command(info) == "wayswitch update --install"


@pytest.mark.parametrize("version", ["1.1.4;id", "../main", "", "v1.1.4\nhello"])
def test_invalid_release_cannot_form_install_command(version):
    assert not updater.github_command(updater.Source("github", "apt"),
                                      updates.Release(version, ""))


@pytest.mark.parametrize("sums", ["correct", "missing", "empty", "duplicate", "corrupt"])
def test_download_integrity_controls_real_installation(shell, tmp_path, sums):
    asset = "wayswitch_1.1.4_all.deb"
    artifact = b"controlled test package"
    digest = hashlib.sha256(artifact).hexdigest()
    contents = {
        "correct": f"{digest}  {asset}\n",
        "missing": f"{digest}  other.deb\n",
        "empty": "",
        "duplicate": f"{digest}  {asset}\n{digest}  {asset}\n",
        "corrupt": f"{'0' * 64}  {asset}\n",
    }[sums]
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / asset).write_bytes(artifact)
    (fixture / "SHA256SUMS").write_text(contents)
    command = updater.github_command(updater.Source("github", "apt"),
                                      updates.Release("1.1.4", ""))
    prefix = f'''
fixture={fixture.as_posix()!r}
curl() {{
  for arg do
    case "$arg" in https://*) name=${{arg##*/}};; esac
  done
  cp "$fixture/$name" "$2"
}}
sudo() {{ printf '%s\\n' "$*" > installed; }}
'''
    result = subprocess.run([shell, "-c", prefix + command], cwd=tmp_path,
                            capture_output=True, text=True)
    assert (result.returncode == 0) is (sums == "correct"), result.stderr
    assert (tmp_path / "installed").exists() is (sums == "correct")


def test_appimage_update_requires_writable_absolute_regular_file(tmp_path):
    source = updater.Source("appimage", path="relative.AppImage")
    assert not updater.github_command(source, updates.Release("1.1.4", ""))
    target = tmp_path / "settings.AppImage"
    target.write_bytes(b"old")
    command = updater.github_command(updater.Source("appimage", path=str(target)),
                                      updates.Release("1.1.4", ""))
    assert "sha256sum -c" in command and "mv -f" in command
    assert "apt" not in command and "get.sh" not in command


def test_failed_install_keeps_gui_and_service_running(shell, tmp_path):
    info = updater.UpdateInfo(updater.Source("system", "apt", "wayswitch", "1.0"),
                               updates.Release("1.1", ""), "available", "exit 7", True)
    script = updater.install_script(info, app_id="test.app", pid=123)
    prefix = '''
flock() { :; }
systemctl() { echo "$*" >> systemctl; echo active; }
gapplication() { touch gui-closed; }
'''
    result = subprocess.run([shell, "-c", prefix + script], cwd=tmp_path,
                            input="\n", capture_output=True, text=True)
    assert result.returncode != 0
    assert not (tmp_path / "gui-closed").exists()
    assert "try-restart" not in (tmp_path / "systemctl").read_text()


@pytest.mark.parametrize("case,expected", [("new", "available"), ("none", "current"),
                                           ("failed", "error")])
def test_rpm_repo_candidates_are_taken_from_manager(case, expected):
    answer = {"new": (100, "wayswitch.noarch 1:1.1.4-2 distro\n", ""),
              "none": (0, "", ""), "failed": (1, "", "no metadata")}[case]
    source = updater.Source("system", "dnf", "wayswitch", "1:1.1.4-1")
    info = updater.check("1.1.4", source=source,
                         run=runner({("dnf", "--cacheonly", "check-update", "wayswitch"): answer}))
    assert info.state == expected
    assert "dnf upgrade" in info.command and "github" not in info.command


@pytest.mark.parametrize("manager", ["dnf", "zypper", "apt-rpm"])
@pytest.mark.parametrize("comparison,code,success", [("1", 0, True), ("0", 0, True),
                                                  ("-1", 0, False), ("", 0, False),
                                                  ("1", 1, False)])
def test_rpm_repository_update_checks_native_comparison_before_restart(
        shell, tmp_path, manager, comparison, code, success):
    info = updater.UpdateInfo(updater.Source("system", manager, "wayswitch"),
                              updates.Release("1:1.1.4-2", ""), "available", "true", True)
    script = updater.install_script(info, app_id="test.app", pid=0)
    prefix = f'''
flock() {{ :; }}
systemctl() {{ echo "$*" >> service-calls; echo active; }}
python3() {{ :; }}
rpm() {{
  case "$1" in
    -q) printf '1:1.1.5-1';;
    --eval) printf '%s' '{comparison}'; return {code};;
    *) return 1;;
  esac
}}
'''
    result = subprocess.run([shell, "-c", prefix + script], cwd=tmp_path,
                            input="\n", capture_output=True, text=True)
    assert (result.returncode == 0) is success, result.stderr
    assert ("try-restart" in (tmp_path / "service-calls").read_text()) is success


@pytest.mark.skipif(shutil.which("rpm") is None, reason="requires native RPM comparator")
@pytest.mark.parametrize("actual,success", [("1:1.1.4-2", True), ("1:1.1.5-1", True),
                                         ("1:1.1.4-10", True), ("1:1.1.4~rc1-1", False),
                                         ("0:9.0.0-1", False), ("2:0.1-1", True)])
def test_rpm_repository_verification_uses_real_evr_order(shell, tmp_path, actual, success):
    info = updater.UpdateInfo(updater.Source("system", "dnf", "wayswitch"),
                              updates.Release("1:1.1.4-2", ""), "available", "true", True)
    script = updater.install_script(info, app_id="test.app", pid=0)
    prefix = f'''
flock() {{ :; }}
systemctl() {{ echo inactive; }}
rpm() {{
  if [ "$1" = -q ]; then printf '%s' '{actual}'; else command rpm "$@"; fi
}}
'''
    result = subprocess.run([shell, "-c", prefix + script], cwd=tmp_path,
                            input="\n", capture_output=True, text=True)
    assert (result.returncode == 0) is success, result.stderr


@pytest.mark.parametrize("case", ["updated", "bad-xml", "empty"])
def test_zypper_checks_do_not_infer_success_from_invalid_response(case):
    output = {"updated": '<stream><update name="wayswitch" edition="1.1.4-2"/></stream>',
              "bad-xml": "error", "empty": "<stream/>"}[case]
    source = updater.Source("system", "zypper", "wayswitch", "1.1.4-1")
    info = updater.check("1.1.4", source=source, run=runner({
        ("zypper", "--xmlout", "list-updates", "--type", "package"): (0, output, "")}))
    assert info.state == {"updated": "available", "bad-xml": "error", "empty": "current"}[case]


def test_bundle_command_preserves_existing_scope_and_requires_checksum():
    source = updater.Source("github", "flatpak", updater.FLATPAK_ID)
    command = updater.github_command(source, updates.Release("1.1.5", ""))
    assert "/v1.1.5/WaySwitch-Settings.flatpak" in command
    assert 'flatpak install "$scope" --or-update' in command
    assert "sha256sum -c" in command and "get.sh" not in command


def test_cli_does_not_install_unknown_source(monkeypatch, capsys):
    from wayswitch.cli import main

    monkeypatch.setattr(updater, "check", lambda *a, **k: updater.UpdateInfo(
        updater.Source("unknown"), message="Use your original source"))
    monkeypatch.setattr(subprocess, "call", lambda *a, **k: pytest.fail("installer was run"))
    assert main(["update", "--install"]) == 2
    assert "original source" in capsys.readouterr().out


def test_bundle_environment_is_not_passed_to_host_programs():
    env = updater.host_environment({"WAYSWITCH_PACKAGE": "appimage", "APPDIR": "/bundle",
                                    "PYTHONHOME": "/bundle/usr", "LD_LIBRARY_PATH": "/bundle",
                                    "DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "test"})
    assert "PYTHONHOME" not in env and "LD_LIBRARY_PATH" not in env
    assert env["DISPLAY"] == ":0" and env["DBUS_SESSION_BUS_ADDRESS"] == "test"


def test_alt_package_uses_apt_rpm_and_keeps_rpm_format(tmp_path):
    module = tmp_path / "__init__.py"
    (tmp_path / ".update-source").write_text("github")
    run = runner({("rpm", "-qf", "--qf", "%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}",
                   str(module)): (0, "wayswitch\t1:1.1.4-1", "")})
    source = updater.detect(module=module, env={}, run=run, os_release={"ID": "altlinux"})
    assert source.manager == "apt-rpm"
    command = updater.github_command(source, updates.Release("1.1.5", ""))
    assert "wayswitch-1.1.5-1.noarch.rpm" in command and ".deb" not in command


@pytest.mark.parametrize("marker", ["system", "github", "aur"])
def test_staging_records_declared_source(shell, tmp_path, marker):
    root = Path(__file__).resolve().parents[1]
    destination = tmp_path / "stage"
    bash = shell if sys.platform == "win32" else shutil.which("bash")
    result = subprocess.run([bash, str(root / "tools" / "stage.sh"),
                             destination.as_posix(), "/usr/lib/wayswitch", marker],
                            capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    recorded = destination / "usr/lib/wayswitch/wayswitch/.update-source"
    assert recorded.read_text().strip() == marker


def test_arch_recipe_passes_aur_source_to_staging(shell, tmp_path):
    root = Path(__file__).resolve().parents[1]
    recipe = (root / "packaging/arch/PKGBUILD").read_text(encoding="utf-8")
    commit = re.search(r"^_commit=(\w+)$", recipe, re.M)[1]
    sources = tmp_path / f"WaySwitch-{commit}" / "tools"
    sources.mkdir(parents=True)
    (sources / "stage.sh").write_text(
        '#!/bin/bash\nprintf "%s" "${3:-system}" > "$1/source-marker"\n',
        encoding="utf-8", newline="\n")
    stage = tmp_path / "stage"
    stage.mkdir()
    local_recipe = tmp_path / "PKGBUILD"
    local_recipe.write_text(recipe, encoding="utf-8", newline="\n")
    bash = shell if sys.platform == "win32" else shutil.which("bash")
    result = subprocess.run([bash, "-c", 'pkgdir="$PWD/stage"; source ./PKGBUILD; package'],
                            cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert (stage / "source-marker").read_text() == "aur"


def test_aur_manual_build_requires_review_confirmation(shell, tmp_path):
    source = updater.Source("aur", "pacman", "wayswitch", "1.1.4-1")
    info = updater.check("1.1.4", source=source,
                         run=runner({("vercmp", "1.1.5-1", "1.1.4-1"): (0, "1", "")}),
                         which=lambda name: None, aur_fetch=lambda name: "1.1.5-1")
    prefix = '''
git() {
  for target do :; done
  case "$target" in https://*) target=wayswitch;; esac
  mkdir -p "$target"
  printf '# recipe\\n' > "$target/PKGBUILD"
}
makepkg() { touch "$original_dir/installed"; }
original_dir=$PWD
'''
    result = subprocess.run([shell, "-c", prefix + info.command], cwd=tmp_path,
                            input="no\n", capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0
    assert not (tmp_path / "installed").exists()


@pytest.mark.parametrize("service", ["active", "inactive"])
def test_successful_update_only_restarts_previously_running_service(shell, tmp_path, service):
    info = updater.UpdateInfo(updater.Source("system", "apt", "wayswitch", "1.1.4-1"),
                               updates.Release("1.1.4-2", ""), "available", "true", True)
    script = updater.install_script(info, app_id="test.app", pid=0)
    commands = tmp_path / "bin"
    commands.mkdir()
    query = commands / "dpkg-query"
    query.write_text("#!/bin/sh\nprintf 1.1.4-2\n", encoding="utf-8", newline="\n")
    query.chmod(0o755)
    prefix = f'''
PATH="$PWD/bin:$PATH"
flock() {{ :; }}
systemctl() {{ echo "$*" >> service-calls; echo {service}; }}
dpkg() {{ :; }}
python3() {{ :; }}
gapplication() {{ touch gui-closed; }}
'''
    result = subprocess.run([shell, "-c", prefix + script], cwd=tmp_path,
                            input="\n", capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert ("try-restart" in (tmp_path / "service-calls").read_text()) is (service == "active")
    assert not (tmp_path / "gui-closed").exists()


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("flock") is None,
                    reason="requires POSIX file descriptors and flock")
def test_reopened_gui_does_not_keep_update_lock(shell, tmp_path):
    info = updater.UpdateInfo(updater.Source("system", "apt", "wayswitch", "1.2.0-1"),
                               updates.Release("1.2.0-2", ""), "available", "true", True)
    script = updater.install_script(info, app_id="test.app", pid=123)
    gui = tmp_path / "wayswitch-gui"
    gui.write_text("#!/bin/sh\necho $$ > gui.pid\n"
                   "while [ ! -e stop ]; do sleep 0.05; done\n", encoding="utf-8")
    gui.chmod(0o755)
    query = tmp_path / "dpkg-query"
    query.write_text("#!/bin/sh\nprintf 1.2.0-2\n", encoding="utf-8")
    query.chmod(0o755)
    prefix = '''
PATH="$PWD:$PATH"
systemctl() { echo inactive; }
dpkg() { :; }
gapplication() { :; }
kill() { return 1; }
'''
    try:
        result = subprocess.run([shell, "-c", prefix + script], cwd=tmp_path,
                                env={**os.environ, "XDG_RUNTIME_DIR": str(tmp_path)},
                                input="\n", capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        deadline = time.monotonic() + 5
        while not (tmp_path / "gui.pid").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        os.kill(int((tmp_path / "gui.pid").read_text()), 0)
        lock = tmp_path / f"wayswitch-update-{os.getuid()}.lock"
        probe = subprocess.run(["flock", "-n", str(lock), "true"], capture_output=True)
        assert probe.returncode == 0, "reopened GUI inherited the update lock"
    finally:
        (tmp_path / "stop").touch()
