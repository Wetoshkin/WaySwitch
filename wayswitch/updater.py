"""Source-preserving update discovery. Probes are read-only and run outside GTK."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from wayswitch import updates
from wayswitch.i18n import gettext as _

FLATPAK_ID = "io.github.Wetoshkin.WaySwitch.Gui"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.:@-]{0,150}\Z")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.:~^-]{0,200}\Z")


def run_command(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=20,
                              encoding="utf-8", errors="replace",
                              env={**host_environment(), "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


@dataclass(frozen=True)
class Source:
    kind: str
    manager: str = ""
    package: str = ""
    installed: str = ""
    path: str = ""

    @property
    def label(self) -> str:
        if self.kind == "github":
            return _("GitHub Releases")
        if self.kind == "aur":
            return "AUR"
        if self.kind == "system":
            return _("System repositories ({manager})").format(manager=self.manager)
        if self.kind == "appimage":
            return "AppImage · GitHub Releases"
        if self.kind == "flatpak":
            return "Flatpak"
        if self.kind == "github-flatpak":
            return _("Downloaded Flatpak bundle · GitHub Releases")
        if self.kind == "nix":
            return "Nix"
        return _("Installation source could not be confirmed")


@dataclass(frozen=True)
class UpdateInfo:
    source: Source
    available: updates.Release | None = None
    state: str = "manual"
    command: str = ""
    automatic: bool = False
    message: str = ""


def detect(*, module: Path | None = None, env=None, run=run_command,
           os_release=None) -> Source:
    env = os.environ if env is None else env
    if env.get("FLATPAK_ID") == FLATPAK_ID:
        return Source("flatpak", package=FLATPAK_ID)
    if env.get("WAYSWITCH_PACKAGE") == "appimage":
        return Source("appimage", path=env.get("APPIMAGE", ""))
    module = module or Path(__file__).resolve().with_name("__init__.py")
    if "/nix/store/" in module.as_posix():
        return Source("nix")
    try:
        marker = (module.parent / ".update-source").read_text(encoding="utf-8").strip()
    except OSError:
        marker = ""
    except UnicodeError:
        return Source("unknown")
    if (module.parent / ".packaged").exists():
        marker = "system"
    if os_release is None:
        try:
            import platform
            os_release = platform.freedesktop_os_release()
        except OSError:
            os_release = {}
    distro = os_release.get("ID", "")
    for manager, query in (
        ("apt", ["dpkg-query", "-S", str(module)]),
        ("rpm", ["rpm", "-qf", "--qf", "%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}",
                 str(module)]),
        ("pacman", ["pacman", "-Qoq", str(module)]),
    ):
        result = run(query)
        if result.returncode:
            continue
        if manager == "apt":
            lines = result.stdout.strip().splitlines()
            if len(lines) != 1 or ": " not in lines[0]:
                continue
            package, owned = lines[0].rsplit(": ", 1)
            if owned != str(module) or not NAME.fullmatch(package):
                continue
            version = run(["dpkg-query", "-W", "-f=${Version}", package])
            installed = version.stdout.strip() if not version.returncode else ""
        elif manager == "rpm":
            fields = result.stdout.strip().split("\t")
            if len(fields) != 2:
                continue
            package, installed = fields
            if Path("/run/ostree-booted").exists():
                manager = "rpm-ostree"
            elif distro in {"altlinux", "alt"}:
                manager = "apt-rpm"
            elif distro in {"opensuse", "opensuse-tumbleweed", "opensuse-leap", "sles"}:
                manager = "zypper"
            else:
                manager = "dnf"
        else:
            package = result.stdout.strip()
            if not NAME.fullmatch(package):
                continue
            version = run(["pacman", "-Q", package])
            fields = version.stdout.strip().split()
            installed = fields[1] if not version.returncode and len(fields) == 2 else ""
            foreign = run(["pacman", "-Qm", package])
            if foreign.returncode == 0 and marker != "aur":
                return Source("unknown", manager, package, installed, str(module))
            if foreign.returncode not in {0, 1} or (
                    foreign.returncode and foreign.stderr.strip()):
                return Source("unknown", manager, package, installed, str(module))
        if not NAME.fullmatch(package) or not installed:
            return Source("unknown")
        kind = marker if marker in {"github", "aur"} else "system"
        if kind == "aur" and manager != "pacman":
            kind = "system"
        return Source(kind, manager, package, installed, str(module))
    if marker == "github" and "/usr/local/" in module.as_posix():
        return Source("github", "source", path=str(module))
    return Source("unknown")


def aur_version(package: str) -> str:
    import urllib.parse
    import urllib.request

    url = "https://aur.archlinux.org/rpc/v5/info?" + urllib.parse.urlencode({"arg[]": package})
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 — fixed HTTPS
        data = json.loads(response.read(1024 * 1024))
    results = data.get("results", []) if isinstance(data, dict) else []
    if (not isinstance(results, list) or len(results) != 1
            or not isinstance(results[0], dict) or results[0].get("Name") != package):
        raise updates.UpdateError("AUR package is unavailable")
    version = results[0].get("Version", "")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise updates.UpdateError("Invalid AUR version")
    return version


def _repository_version(source: Source, run) -> tuple[str, str]:
    package = source.package
    if source.manager in {"apt", "apt-rpm"}:
        result = run(["apt-get", "-s", "install", package])
        if result.returncode:
            return "error", ""
        match = re.search(r"^Inst " + re.escape(package) + r"(?: \[[^\]]+\])? \((\S+)",
                          result.stdout, re.M)
        return "available" if match else "current", match[1] if match else ""
    if source.manager == "pacman":
        result = run(["pacman", "-Qu", package])
        match = re.search(r"^" + re.escape(package) + r" \S+ -> (\S+)$",
                          result.stdout.strip(), re.M)
        if match:
            return "available", match[1]
        return ("current" if result.returncode in {0, 1} and not result.stderr.strip()
                else "error"), ""
    if source.manager == "dnf":
        result = run(["dnf", "--cacheonly", "check-update", package])
        if result.returncode == 0:
            return "current", ""
        if result.returncode == 100:
            match = re.search(r"^" + re.escape(package) + r"\.\S+\s+(\S+)\s+\S+\s*$",
                              result.stdout, re.M)
            if match:
                return "available", match[1]
        return "error", ""
    if source.manager == "zypper":
        import xml.etree.ElementTree as ET
        result = run(["zypper", "--xmlout", "list-updates", "--type", "package"])
        if result.returncode:
            return "error", ""
        try:
            root = ET.fromstring(result.stdout)
            for item in root.iter("update"):
                if item.get("name") == package and item.get("edition"):
                    return "available", item.get("edition")
            return "current", ""
        except ET.ParseError:
            return "error", ""
    return "manual", ""


def _manager_command(source: Source, which) -> str:
    package = shlex.quote(source.package)
    if source.kind == "aur":
        for helper in ("paru", "yay"):
            if which(helper):
                return f"{helper} -S aur/{package}"
        review = _("Review PKGBUILD and every other build file in the displayed directory. "
                   "Type yes to build and install, or anything else to cancel:")
        return ("(\nset -eu\nwork=$(mktemp -d)\n"
                f'git clone https://aur.archlinux.org/{package}.git "$work/src"\n'
                'cd "$work/src"\npwd\nls -la\ncat PKGBUILD\n'
                f"printf '%s ' {shlex.quote(review)}\n"
                'read -r answer\n[ "$answer" = yes ]\nmakepkg -si\n)')
    return {
        "apt": f"sudo apt-get update && sudo apt-get install {package}",
        "apt-rpm": f"sudo apt-get update && sudo apt-get install {package}",
        "dnf": f"sudo dnf upgrade --refresh {package}",
        "zypper": f"sudo zypper refresh && sudo zypper update {package}",
        "pacman": f"sudo pacman -Syu {package}",
    }.get(source.manager, "")


def check(current: str, *, force: bool = False, source: Source | None = None,
          run=run_command, which=shutil.which, aur_fetch=aur_version) -> UpdateInfo:
    source = source or detect(run=run)
    if source.kind == "unknown":
        return UpdateInfo(source, message=_("Use the installation method you originally chose. "
                                           "No other source will be selected automatically."))
    if source.kind == "nix":
        command = "sudo nixos-rebuild switch" if which("nixos-rebuild") else ""
        return UpdateInfo(source, command=command,
                          message=_("Update the WaySwitch package or pinned input in your Nix "
                                    "configuration first, then rebuild using your usual command."))
    if source.manager == "rpm-ostree" and source.kind != "github":
        return UpdateInfo(source, command="rpm-ostree upgrade",
                          message=_("This command updates the entire system deployment. "
                                    "Review it in the terminal; it is not run automatically.") +
                          "\n\n" + _("Update the deployment using your system updater. "
                                      "Layered local RPMs need a replacement from their "
                                      "original source. A reboot may be required."))
    if source.kind == "flatpak":
        return UpdateInfo(source, command=f"flatpak update {FLATPAK_ID}",
                          message=_("Run this command on the host for a repository installation. "
                                    "For a local bundle, use the verified bundle instructions "
                                    "below. Update the native service separately through its "
                                    "own installation source."))
    if source.kind in {"github", "appimage", "github-flatpak"}:
        try:
            # Discovery was already restricted to this source; do not apply the legacy marker.
            release = updates.check(current, force=force, strict=True)
        except (OSError, ValueError) as exc:
            return UpdateInfo(source, state="error", message=str(exc))
        command_source = (Source("github", "flatpak", source.package)
                          if source.kind == "github-flatpak" else source)
        command = github_command(command_source, release) if release else ""
        automatic = bool(command) and source.kind != "github-flatpak"
        if source.manager == "rpm-ostree":
            automatic = False
        if source.kind == "appimage" and release and not command:
            command = github_command(Source("github", "appimage-download"), release)
            automatic = False
        message = _("Packages are verified against the release's SHA256SUMS before installation.")
        if source.manager == "rpm-ostree":
            message += "\n\n" + _("This prepares a new deployment. Reboot after reviewing and "
                                     "applying the command; the running session is not replaced.")
        if source.kind == "appimage" and release and not automatic:
            message += "\n\n" + _("The original AppImage path is unavailable or not writable. "
                                     "The command downloads a verified new file into the current "
                                     "directory without overwriting an existing file. Close the "
                                     "old window and replace its file yourself.")
        return UpdateInfo(source, release, "available" if release else "current", command,
                          automatic, message)
    command = _manager_command(source, which)
    if not NAME.fullmatch(source.package):
        return UpdateInfo(source, state="error")
    if source.kind == "aur":
        try:
            version = aur_fetch(source.package)
            comparison = run(["vercmp", version, source.installed])
            if comparison.returncode:
                raise ValueError("Version comparison failed")
            state = "available" if int(comparison.stdout.strip()) > 0 else "current"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return UpdateInfo(source, state="error", command=command, message=str(exc))
    else:
        state, version = _repository_version(source, run)
    release = updates.Release(version, "", "") if state == "available" else None
    automatic = bool(command) and source.manager != "pacman"
    if source.kind == "aur":
        automatic = any(which(helper) for helper in ("paru", "yay"))
    message = (_("This uses your package manager's repository metadata. A new GitHub release "
                 "may not yet be packaged here. Package revisions are included.")
               if source.kind != "aur" else
               _("The AUR recipe must be updated by its maintainer. Review build-file changes "
                 "when the helper asks; the installation source stays AUR."))
    if source.manager == "pacman" and source.kind != "aur":
        message += "\n\n" + _("Arch repository updates include a system upgrade. Run the shown "
                                 "command yourself after reviewing the complete transaction.")
    return UpdateInfo(source, release, state, command, automatic, message)


def github_command(source: Source, release: updates.Release) -> str:
    """A frozen release, original format and mandatory checksum; no source fallback."""
    version = release.version
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?(?:[.-]?(?:a|b|rc)\.?\d+)?", version):
        return ""
    if source.kind not in {"github", "appimage"}:
        return ""
    if source.manager == "source":
        url = "https://github.com/Wetoshkin/WaySwitch.git"
        tag = "v" + version
        return ("(\nset -eu\nwork=$(mktemp -d)\ntrap 'rm -rf -- \"$work\"' EXIT\n"
                f"commit=$(git ls-remote {url} refs/tags/{tag} 'refs/tags/{tag}^{{}}' "
                "| tail -n 1 | cut -f1)\n"
                "[ ${#commit} -eq 40 ] && "
                "case $commit in *[!0-9a-f]*) exit 1;; esac\n"
                f"git clone --depth 1 --branch {tag} {url} \"$work/src\"\n"
                '[ "$(git -C "$work/src" rev-parse HEAD)" = "$commit" ]\n'
                'sudo env WAYSWITCH_UPDATE_SOURCE=github bash "$work/src/packaging/install.sh"\n)')
    package_version = re.sub(r"[.-]?(a|b|rc)\.?", r"~\1", version)
    if source.kind == "appimage":
        target = Path(source.path)
        if (not source.path or not target.is_absolute() or not target.is_file()
                or target.is_symlink() or not os.access(target.parent, os.W_OK)):
            return ""
        asset = "WaySwitch-Settings-x86_64.AppImage"
        install = (f"target={shlex.quote(str(target))}\n"
                   'replacement=$(mktemp "${target}.XXXXXX")\n'
                   'trap \'rm -rf -- "$work"; rm -f -- "$replacement"\' EXIT\n'
                   'cp "$work/$asset" "$replacement"\nchmod 755 "$replacement"\n'
                   f'[ "$("$replacement" --appimage-extract-and-run --version)" = '
                   f'{shlex.quote(version)} ]\n'
                   'mv -f -- "$replacement" "$target"')
    elif source.manager == "appimage-download":
        asset = "WaySwitch-Settings-x86_64.AppImage"
        install = ('[ ! -e "./$asset" ]\ncp "$work/$asset" "./$asset"\n'
                   'chmod 755 "./$asset"')
    elif source.package not in {"", "wayswitch"}:
        if source.manager != "flatpak" or source.package != FLATPAK_ID:
            return ""
        asset = "WaySwitch-Settings.flatpak"
        install = ('scope=\nfor candidate in --user --system; do\n'
                   f'if flatpak info "$candidate" {FLATPAK_ID} >/dev/null 2>&1; then\n'
                   '[ -z "$scope" ] || exit 1\nscope=$candidate\nfi\ndone\n'
                   '[ -n "$scope" ]\n'
                   'flatpak install "$scope" --or-update "$work/$asset"')
    elif source.manager == "apt":
        asset = f"wayswitch_{package_version}_all.deb"
        install = 'chmod 755 "$work"\nsudo apt-get install "$work/$asset"'
    elif source.manager in {"dnf", "zypper", "apt-rpm", "rpm-ostree"}:
        asset = f"wayswitch-{package_version}-1.noarch.rpm"
        install = {"dnf": 'sudo dnf install "$work/$asset"',
                   "apt-rpm": 'chmod 755 "$work"\nsudo apt-get install "$work/$asset"',
                   "rpm-ostree": 'sudo rpm-ostree install --uninstall=wayswitch "$work/$asset"',
                   "zypper": 'sudo zypper install --allow-unsigned-rpm "$work/$asset"'}[
                       source.manager]
    else:
        return ""
    base = f"https://github.com/Wetoshkin/WaySwitch/releases/download/v{version}"
    return ("(\nset -eu\nwork=$(mktemp -d)\ntrap 'rm -rf -- \"$work\"' EXIT\n"
            f"asset={asset}\n"
            f'curl -o "$work/$asset" --fail --location --proto =https {base}/{asset}\n'
            f'curl -o "$work/SHA256SUMS" --fail --location --proto =https {base}/SHA256SUMS\n'
            "digest=$(awk -v name=\"$asset\" '$2 == name {print $1}' \"$work/SHA256SUMS\")\n"
            '[ "${#digest}" -eq 64 ]\n'
            'case "$digest" in *[!0-9a-fA-F]*) exit 1;; esac\n'
            'printf "%s  %s\\n" "$digest" "$work/$asset" | sha256sum -c -\n'
            f"{install}\n)")


def install_script(info: UpdateInfo, *, app_id: str, pid: int) -> str:
    """Self-contained job survives replacement of the installed Python modules."""
    if not info.automatic or not info.command or not info.available:
        raise ValueError("No automatic update is available")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", app_id) or pid < 0:
        raise ValueError("Invalid application identity")
    source = info.source
    expected = info.available.version
    if not VERSION.fullmatch(expected):
        raise ValueError("Invalid package version")
    prefix = "/usr/local/bin/" if source.manager == "source" else "/usr/bin/"
    cli = prefix + "wayswitch" if source.path else "wayswitch"
    gui = prefix + "wayswitch-gui" if source.path else "wayswitch-gui"
    if source.kind == "appimage":
        verify = (f'actual=$({shlex.quote(source.path)} --appimage-extract-and-run --version)'
                  f' && [ "$actual" = {shlex.quote(expected)} ]')
        launch = shlex.join([source.path, "--appimage-extract-and-run"])
        before = "service_was=inactive"
        after = ""
    else:
        before = 'service_was=$(systemctl --user is-active wayswitch.service 2>/dev/null || :)'
        launch = shlex.quote(gui)
        if source.kind == "github":
            verify = (f"actual=$({shlex.quote(cli)} version) && "
                      f'[ "$actual" = {shlex.quote("wayswitch " + expected)} ]')
        elif source.manager == "apt":
            verify = (f"actual=$(dpkg-query -W '-f=${{Version}}' {shlex.quote(source.package)} "
                      "2>/dev/null) && "
                      f'dpkg --compare-versions "$actual" ge {shlex.quote(expected)}')
        elif source.manager == "pacman":
            verify = (f"actual=$(pacman -Q {shlex.quote(source.package)} | cut -d ' ' -f2) && "
                      f'[ "$(vercmp "$actual" {shlex.quote(expected)})" -ge 0 ]')
        else:
            # Отдельные компоненты EVR корректны и для rpm.vercmp до RPM 4.16.
            compare = ("%{lua:\n"
                       "local function parts(s)\n"
                       " local e,vr=s:match('^([0-9]+):(.*)$')\n"
                       " if not e then e,vr='0',s end\n"
                       " local v,r=vr:match('^(.*)-([^-]*)$')\n"
                       " return {e,v or vr,r or ''}\nend\n"
                       f"local a,b=parts('$actual'),parts('{expected}')\n"
                       "local c=0\nfor i=1,3 do\n"
                       " c=rpm.vercmp(a[i],b[i]); if c~=0 then break end\nend\n"
                       "print(c)}")
            verify = ("actual=$(rpm -q --qf '%{EPOCHNUM}:%{VERSION}-%{RELEASE}' "
                      f"{shlex.quote(source.package)}) &&\n"
                      'case "$actual" in ""|*[!A-Za-z0-9+_.:~^-]*) false;; *) :;; esac &&\n'
                      f'comparison=$(rpm --eval "{compare}") &&\n'
                      'case "$comparison" in 0|1) :;; *) false;; esac')
        health = (f"import json,subprocess,time; cli={cli!r}; "
                  "expected=subprocess.check_output([cli,'version'],text=True).strip()"
                  ".removeprefix('wayswitch '); "
                  "ok=False\n"
                  "for attempt in range(15):\n"
                  " try:\n"
                  "  r=subprocess.run([cli,'status','--json'],capture_output=True,"
                  "text=True,timeout=4)\n"
                  "  ok=r.returncode==0 and json.loads(r.stdout).get('version')==expected\n"
                  " except (OSError,ValueError,subprocess.TimeoutExpired): pass\n"
                  " if ok: break\n"
                  " time.sleep(0.3)\n"
                  "raise SystemExit(0 if ok else 1)")
        after = ('if [ "$service_was" = active ]; then\n'
                 "systemctl --user daemon-reload &&\n"
                 "systemctl --user try-restart wayswitch.service &&\n"
                 f"python3 -c {shlex.quote(health)} || return 1\nfi")
    success = _("Update installed and verified. Reopening WaySwitch.")
    failure = _("The update or its verification failed. The window was kept open. "
                "Review the terminal output before trying again.")
    manual = _("The update was installed. Close and reopen WaySwitch to load the new version.")
    enter = _("Press Enter to close this window.")
    return (
        "update_job() {\n"
        'command -v flock >/dev/null 2>&1 || return 1\n'
        f"{before}\n"
        f"{info.command}\nrc=$?\n[ \"$rc\" -eq 0 ] || return \"$rc\"\n"
        f"{verify} || return 1\n{after}\n"
        f'if [ {pid} -gt 0 ] && command -v gapplication >/dev/null 2>&1 && '
        f"gapplication action {shlex.quote(app_id)} quit-for-update; then\n"
        "attempt=0\n"
        f"while kill -0 {pid} 2>/dev/null; do\n"
        'attempt=$((attempt + 1))\n[ "$attempt" -lt 20 ] || return 1\nsleep 0.25\ndone\n'
        f"nohup {launch} >/dev/null 2>&1 </dev/null &\n"
        f"printf '%s\\n' {shlex.quote(success)}\n"
        f"else printf '%s\\n' {shlex.quote(manual)}\nfi\n}}\n"
        'lock_dir=${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}\n'
        '(flock -n 9 && update_job) 9>"$lock_dir/wayswitch-update-$(id -u).lock"\n'
        "rc=$?\n"
        f'[ "$rc" -eq 0 ] || printf \'%s\\n\' {shlex.quote(failure)}\n'
        f"printf '%s ' {shlex.quote(enter)}\nread -r reply\nexit \"$rc\""
    )


def host_environment(env=None) -> dict:
    """Do not load AppImage's bundled libraries into a host terminal/package manager."""
    result = dict(os.environ if env is None else env)
    if result.get("WAYSWITCH_PACKAGE") == "appimage":
        for key in ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH", "LD_PRELOAD",
                    "GI_TYPELIB_PATH", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR",
                    "GDK_PIXBUF_MODULEDIR", "GDK_PIXBUF_MODULE_FILE", "SSL_CERT_FILE",
                    "WAYSWITCH_PACKAGE", "APPDIR", "APPIMAGE"):
            result.pop(key, None)
        result["XDG_DATA_DIRS"] = "/usr/local/share:/usr/share"
    return result


def status_text(info: UpdateInfo) -> str:
    if info.state == "error":
        return _("Could not check {source}. No update result was confirmed.").format(
            source=info.source.label)
    if info.available:
        return _("Version {version} is available from {source}").format(
            version=info.available.version, source=info.source.label)
    if info.state == "current":
        return _("No newer package was found in {source}").format(source=info.source.label)
    return _("Updates through {source}: manual steps required").format(source=info.source.label)


def cli_update(*, install: bool = False) -> int:
    from wayswitch import __version__

    info = check(__version__, force=True)
    print(status_text(info))
    print(info.message)
    if info.available and info.available.notes:
        print(info.available.notes)
    if info.command:
        print("\n" + info.command)
    if info.state == "error":
        return 1
    if not install or info.state == "current":
        return 0
    if not info.automatic or not info.available:
        return 2
    # No GUI PID is assumed when invoked from a host terminal or portable client.
    script = install_script(info, app_id="io.github.Wetoshkin.WaySwitch.Settings", pid=0)
    try:
        return subprocess.call(["sh", "-c", script], env=host_environment())
    except OSError:
        return 1


def manual_command(info: UpdateInfo) -> str:
    if (info.source.kind == "appimage" and info.source.path
            and info.command and info.automatic):
        return shlex.join([info.source.path, "--appimage-extract-and-run", "--update"])
    if info.automatic and info.available:
        prefix = "/usr/local/bin/" if info.source.manager == "source" else "/usr/bin/"
        cli = prefix + "wayswitch" if info.source.path else "wayswitch"
        return shlex.join([cli, "update", "--install"])
    return info.command
