"""Read-only native package discovery and explicit package-manager handoff."""

from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from wayswitch import updater
from wayswitch.gui import terminal
from wayswitch.i18n import gettext as _

HOST_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def host_which(name: str) -> str | None:
    return shutil.which(name, path=HOST_PATH)


@dataclass(frozen=True)
class Package:
    distro: str
    manager: str
    candidate: str = ""
    installed: str = ""
    command: str = ""
    automatic: bool = False
    message: str = ""


def compatible(package: str, gui: str) -> bool:
    # No wider protocol compatibility contract exists yet; revisions may differ.
    match = re.fullmatch(r"(?:\d+:)?(\d+\.\d+\.\d+)(?:[-+_][A-Za-z0-9.+_~^-]+)?", package)
    return bool(match and match[1] == gui)


def check(gui: str, *, env=None, os_release=None, run=updater.run_command,
          which=host_which, atomic: bool | None = None) -> Package:
    env = os.environ if env is None else env
    if env.get("FLATPAK_ID"):
        return Package("Flatpak", "flatpak", message=_(
            "Install the native service in a host terminal. Flatpak remains isolated; "
            "do not grant it keyboard device access."))
    if os_release is None:
        try:
            os_release = platform.freedesktop_os_release()
        except OSError:
            os_release = {}
    distro = os_release.get("PRETTY_NAME", os_release.get("ID", "Linux"))[:200]
    families = {os_release.get("ID", ""), *os_release.get("ID_LIKE", "").split()}
    if "nixos" in families:
        return Package(distro, "nix", message=_(
            "Enable the WaySwitch NixOS module from docs/nixos.md and rebuild the system. "
            "The module configures the native service and device permissions."))
    if atomic if atomic is not None else Path("/run/ostree-booted").exists():
        return Package(distro, "rpm-ostree", message=_(
            "Use the distribution's rpm-ostree layering instructions for the native "
            "WaySwitch package, then reboot. Check the configured repository and package "
            "version first; this window does not install into /usr."))
    manager = ("apt-rpm" if families & {"alt", "altlinux"} else
               "apt" if families & {"debian", "ubuntu"} else
               "pacman" if families & {"arch", "manjaro", "cachyos"} else
               "zypper" if families & {"opensuse", "opensuse-tumbleweed", "opensuse-leap", "sles"}
               else "dnf" if families & {"fedora", "rhel", "centos"} else "")
    native = which("wayswitch")
    if native:
        result = run([native, "version"])
        version = result.stdout.strip().removeprefix("wayswitch ") if not result.returncode else ""
        version = version if updater.VERSION.fullmatch(version) else ""
        return Package(distro, manager, installed=version or _("unknown"),
                       command=shlex.join([native, "update", "--install"]), message=_(
                           "A native installation already exists. Keep its original source: "
                           "update it with its package manager or wayswitch update --install. "
                           "Older services may not support that command. GUI and native "
                           "versions must match before using this installation assistant."))
    executable = "apt-get" if manager in {"apt", "apt-rpm"} else manager
    if not executable or not which(executable):
        return Package(distro, manager, message=_(
            "No supported native package manager was found. Follow your distribution's "
            "WaySwitch packaging instructions; no installation was started."))
    candidate = ""
    refresh = ""
    aur_helper = ""
    if manager in {"apt", "apt-rpm"}:
        result = run(["apt-cache", "policy", "wayswitch"])
        match = re.search(r"^\s*Candidate:\s*(\S+)\s*$", result.stdout, re.M)
        candidate = match[1] if not result.returncode and match else ""
        refresh = "sudo apt-get update; apt-cache policy wayswitch"
    elif manager == "dnf":
        result = run(["dnf", "--cacheonly", "list", "--available", "wayswitch"])
        match = re.search(r"^wayswitch\.\S+\s+(\S+)\s+\S+\s*$", result.stdout, re.M)
        candidate = match[1] if not result.returncode and match else ""
        refresh = "dnf --refresh info wayswitch"
    elif manager == "pacman":
        result = run(["pacman", "-Si", "wayswitch"])
        match = re.search(r"^Version\s*:\s*(\S+)\s*$", result.stdout, re.M)
        candidate = match[1] if not result.returncode and match else ""
        refresh = "pacman -Si wayswitch; yay -Si aur/wayswitch"
        if not candidate:
            for helper in ("paru", "yay"):
                if which(helper):
                    result = run([helper, "-Si", "aur/wayswitch"])
                    match = re.search(r"^Version\s*:\s*(\S+)\s*$", result.stdout, re.M)
                    if not result.returncode and match:
                        candidate, aur_helper = match[1], helper
                        break
    elif manager == "zypper":
        import xml.etree.ElementTree as ET

        result = run(["zypper", "--no-refresh", "--xmlout", "info", "wayswitch"])
        try:
            root = ET.fromstring(result.stdout)
            editions = {item.get("edition", "") for item in root.iter("solvable")
                        if item.get("name") == "wayswitch"}
            candidate = editions.pop() if not result.returncode and len(editions) == 1 else ""
        except ET.ParseError:
            pass
        refresh = "sudo zypper refresh; zypper info wayswitch"
    if not candidate or not updater.VERSION.fullmatch(candidate):
        return Package(distro, manager, message=_(
            "No native package version was confirmed in the configured repositories. "
            "Check the repository or AUR maintainer's instructions, refresh metadata "
            "in your terminal and check again:\n\n{command}").format(command=refresh))
    if not compatible(candidate, gui):
        return Package(distro, manager, candidate=candidate, message=_(
            "The native candidate and GUI versions do not match. Obtain matching versions "
            "through their respective sources; installation is disabled."))
    if aur_helper:
        return Package(distro, "aur", candidate=candidate,
                       command=shlex.join([aur_helper, "-S", "aur/wayswitch"]), message=_(
                           "The AUR candidate has the same upstream version as the GUI. "
                           "Review PKGBUILD and all build files before installing with your "
                           "helper. Run the displayed command yourself; no source is changed."))
    command = {"apt": f"sudo apt-get install -- wayswitch={candidate}",
               "apt-rpm": f"sudo apt-get install -- wayswitch={candidate}",
               "dnf": f"sudo dnf install -- wayswitch-{candidate}",
               "zypper": f"sudo zypper install -- wayswitch={candidate}",
               "pacman": "sudo pacman -Syu wayswitch"}[manager]
    message = _("The upstream versions match. The native package supplies the service, "
                "systemd unit, keyboard permissions and desktop integration. Review the "
                "package manager's transaction and authentication prompt. After installation, "
                "sign out and back in for udev and GNOME extension changes, then run "
                "wayswitch doctor. Service activation remains a separate action.")
    if manager == "pacman":
        message += "\n\n" + _("Arch installation includes a full system upgrade. Review and "
                              "run the command yourself. For AUR, review the maintainer's "
                              "PKGBUILD and use your existing helper.")
    return Package(distro, manager, candidate=candidate, command=command,
                   automatic=manager != "pacman", message=message)


def handoff(info: Package, *, confirmed: bool) -> bool:
    if not confirmed or not info.automatic or not info.command:
        return False
    success = _("The package manager reported success. This does not confirm an active "
                "service. Sign out and back in, then run wayswitch doctor and check "
                "systemctl --user status wayswitch.")
    failure = _("The package manager failed or was cancelled. It may have made changes. "
                "Read its error and use its recovery instructions; no rollback is promised.")
    script = (f"if {info.command}; then printf '%s\\n' {shlex.quote(success)}; "
              f"wayswitch doctor; else printf '%s\\n' {shlex.quote(failure)}; fi\n"
              f"printf '%s ' {shlex.quote(_(terminal.PRESS_ENTER))}; read answer")
    argv = terminal.terminal_argv(script, which=host_which)
    if argv is None:
        return False
    environment = updater.host_environment()
    environment["PATH"] = HOST_PATH
    try:
        subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
    except OSError:
        return False
    return True
