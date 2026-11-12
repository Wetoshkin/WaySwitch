import subprocess

import pytest

from tests.test_get_sh import shell as shell
from wayswitch import companion


def probe(responses=None, **kwargs):
    calls = []

    def run(argv):
        calls.append(argv)
        code, output = (responses or {}).get(tuple(argv), (1, ""))
        return subprocess.CompletedProcess(argv, code, output, "")

    result = companion.check(
        "1.1.4",
        env={},
        os_release={"ID": "ubuntu"},
        which=lambda name: "/usr/bin/" + name if name != "wayswitch" else None,
        run=run,
        atomic=False,
        **kwargs,
    )
    return result, calls


def test_apt_candidate_is_pinned_and_only_read_only_queries_run():
    info, calls = probe(
        {("apt-cache", "policy", "wayswitch"): (0, "wayswitch:\n  Candidate: 1:1.1.4-2\n")}
    )
    assert info.candidate == "1:1.1.4-2" and info.automatic
    assert info.command == "sudo apt-get install -- wayswitch=1:1.1.4-2"
    assert calls == [["apt-cache", "policy", "wayswitch"]]


@pytest.mark.parametrize("candidate", ["1.1.3-1", "1.1.5-1", "1.1.4rc1-1", "$(id)"])
def test_incompatible_or_invalid_candidate_cannot_be_installed(candidate):
    info, _ = probe({("apt-cache", "policy", "wayswitch"): (0, f"Candidate: {candidate}\n")})
    assert not info.automatic and not info.command


def test_missing_candidate_has_manual_refresh_instructions():
    info, _ = probe()
    assert not info.automatic and "apt" in info.message
    assert not info.command


@pytest.mark.parametrize(
    "env,distro,atomic,manager",
    [
        ({"FLATPAK_ID": "io.github.Wetoshkin.WaySwitch.Gui"}, "ubuntu", False, "flatpak"),
        ({}, "nixos", False, "nix"),
        ({}, "fedora", True, "rpm-ostree"),
    ],
)
def test_isolated_and_declarative_environments_never_start_installers(env, distro, atomic, manager):
    info = companion.check(
        "1.1.4",
        env=env,
        os_release={"ID": distro},
        atomic=atomic,
        which=lambda _: pytest.fail("must not search executables"),
        run=lambda _: pytest.fail("must not run host probes"),
    )
    assert info.manager == manager and not info.automatic and not info.command


def test_existing_installation_keeps_its_source():
    calls = []

    def run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "wayswitch 1.1.3\n", "")

    info = companion.check(
        "1.1.4",
        env={},
        os_release={"ID": "arch"},
        atomic=False,
        which=lambda name: "/usr/bin/" + name,
        run=run,
    )
    assert info.installed == "1.1.3" and not info.automatic
    assert info.command == "/usr/bin/wayswitch update --install"
    assert calls == [["/usr/bin/wayswitch", "version"]]


def test_missing_package_manager_does_not_run_queries():
    info = companion.check("1.1.4", env={}, os_release={"ID": "ubuntu"}, atomic=False,
                           which=lambda _: None,
                           run=lambda _: pytest.fail("manager is missing"))
    assert not info.automatic and not info.command


def test_host_probe_removes_appimage_python_and_library_variables(monkeypatch):
    from wayswitch import updater

    monkeypatch.setenv("WAYSWITCH_PACKAGE", "appimage")
    monkeypatch.setenv("PYTHONHOME", "/bundle/python")
    monkeypatch.setenv("LD_PRELOAD", "/bundle/library")
    calls = []
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **kw: calls.append(kw))
    updater.run_command(["apt-cache", "policy", "wayswitch"])
    assert "PYTHONHOME" not in calls[0]["env"] and "LD_PRELOAD" not in calls[0]["env"]
    assert calls[0]["env"]["LC_ALL"] == "C"


def test_cancel_does_not_start_terminal(monkeypatch):
    info, _ = probe({("apt-cache", "policy", "wayswitch"): (0, "Candidate: 1.1.4-1\n")})
    monkeypatch.setattr(companion.subprocess, "Popen", lambda *a, **k: pytest.fail("cancelled"))
    assert not companion.handoff(info, confirmed=False)


def test_handoff_uses_desktop_preferred_host_terminal(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(companion, "host_which", lambda name:
                        f"/usr/bin/{name}" if name in {"gnome-terminal", "konsole"} else None)
    launched = []
    monkeypatch.setattr(companion.subprocess, "Popen", lambda argv, **kw: launched.append(argv))
    info = companion.Package("Debian", "apt", command="true", automatic=True)
    assert companion.handoff(info, confirmed=True)
    assert launched[0][0] == "konsole"


def test_ci_transport_preserves_desktop_probe_subprocess(monkeypatch):
    from types import SimpleNamespace

    from tools import smoke_companion_install
    from wayswitch.backends import desktop

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    info = companion.Package("Debian", "apt", candidate="1.1.4-1",
                             command="true", automatic=True)
    monkeypatch.setattr(companion, "check", lambda *a, **kw: info)
    launched = []

    def popen(argv, **kwargs):
        launched.append(argv)
        if argv == ["desktop-probe"]:
            assert kwargs["stdout"] == subprocess.PIPE
        return SimpleNamespace(communicate=lambda **kw: None)

    def detect():
        subprocess.Popen(["desktop-probe"], stdout=subprocess.PIPE)
        return "gnome"

    monkeypatch.setattr(companion.subprocess, "Popen", popen)
    monkeypatch.setattr(desktop, "detect_desktop", detect)
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: info.candidate)
    monkeypatch.setattr(smoke_companion_install, "Path",
                        lambda path: SimpleNamespace(is_file=lambda: True))
    smoke_companion_install.main()
    assert len(launched) == 2
    assert launched[1][:2] == ["sh", "-c"]


def test_terminal_failure_is_reported_without_installation_claim(monkeypatch):
    info, _ = probe({("apt-cache", "policy", "wayswitch"): (0, "Candidate: 1.1.4-1\n")})
    monkeypatch.setattr(companion.terminal, "terminal_argv", lambda *a, **k: None)
    assert not companion.handoff(info, confirmed=True)


def test_confirmed_handoff_sanitizes_host_environment(monkeypatch, en_locale):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    info, _ = probe({("apt-cache", "policy", "wayswitch"): (0, "Candidate: 1.1.4-1\n")})
    monkeypatch.setenv("WAYSWITCH_PACKAGE", "appimage")
    monkeypatch.setenv("PYTHONPATH", "/tmp/bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/bundle/lib")
    monkeypatch.setattr(
        companion.terminal, "terminal_argv", lambda script, **kw: ["terminal", script]
    )
    calls = []
    monkeypatch.setattr(companion.subprocess, "Popen", lambda *a, **kw: calls.append((a, kw)))
    assert companion.handoff(info, confirmed=True)
    assert len(calls) == 1
    script = calls[0][0][0][1]
    assert info.command in script and "wayswitch doctor" in script
    assert "no rollback is promised" in script
    assert "PYTHONPATH" not in calls[0][1]["env"]
    assert "LD_LIBRARY_PATH" not in calls[0][1]["env"]


@pytest.mark.parametrize("distro,query,output,expected,automatic", [
    ("fedora", ("dnf", "--cacheonly", "list", "--available", "wayswitch"),
     "wayswitch.noarch 1.1.4-1 updates\n", "sudo dnf install -- wayswitch-1.1.4-1", True),
    ("altlinux", ("apt-cache", "policy", "wayswitch"), "Candidate: 1.1.4-alt1\n",
     "sudo apt-get install -- wayswitch=1.1.4-alt1", True),
    ("arch", ("pacman", "-Si", "wayswitch"), "Version : 1.1.4-1\n",
     "sudo pacman -Syu wayswitch", False),
    ("arch", ("paru", "-Si", "aur/wayswitch"), "Version : 1.1.4-1\n",
     "paru -S aur/wayswitch", False),
    ("opensuse-tumbleweed", ("zypper", "--no-refresh", "--xmlout", "search", "--match-exact",
                            "--type", "package", "--details", "wayswitch"),
     '<stream><solvable name="wayswitch" edition="1.1.4-1"/></stream>',
     "sudo zypper install -- wayswitch=1.1.4-1", True),
])
def test_native_managers(distro, query, output, expected, automatic):
    def run(argv):
        return subprocess.CompletedProcess(argv, 0 if tuple(argv) == query else 1,
                                           output if tuple(argv) == query else "", "")

    info = companion.check("1.1.4", env={}, os_release={"ID": distro}, atomic=False,
                           run=run, which=lambda name: name if name != "wayswitch" else None)
    assert info.command == expected and info.automatic == automatic


@pytest.mark.parametrize("success", [True, False])
def test_terminal_transaction_reports_result_without_fake_rollback(
        shell, monkeypatch, success, en_locale):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    info, _ = probe({("apt-cache", "policy", "wayswitch"): (0, "Candidate: 1.1.4-1\n")})
    scripts = []
    monkeypatch.setattr(companion.terminal, "terminal_argv",
                        lambda script, **kw: scripts.append(script) or ["terminal"])
    monkeypatch.setattr(companion.subprocess, "Popen", lambda *a, **kw: None)
    assert companion.handoff(info, confirmed=True)
    prefix = f"sudo() {{ return {0 if success else 1}; }}\n"
    prefix += "wayswitch() { echo doctor-called; }\n"
    # Popen was replaced only for handoff; use the real runner in this check.
    monkeypatch.undo()
    result = subprocess.run([shell, "-c", prefix + scripts[0]], input="\n",
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0
    if success:
        assert "reported success" in result.stdout and "doctor-called" in result.stdout
    else:
        assert "failed or was cancelled" in result.stdout
        assert "no rollback is promised" in result.stdout
        assert "doctor-called" not in result.stdout
