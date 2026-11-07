"""Проверки установщика без установки пакетов и обращения к сети."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("desktop,gnome", [
    ("COSMIC", False), ("COSMIC:GNOME", False), ("GNOME:COSMIC", False),
    ("cosmic:GNOME", False), ("GNOME", True), ("ubuntu:GNOME", True),
])
def test_cosmic_session_does_not_enable_gnome_extensions(shell, tmp_path, desktop, gnome):
    source = (ROOT / "packaging/get.sh").read_text(encoding="utf-8")
    helper = ""
    if "is_gnome()" in source:
        start = source.index("is_gnome()")
        helper = source[start:source.index("\n}\n", start) + 3]
    start = source.index("enable_extension()")
    extension = source[start:source.index("\n}\n", start) + 3]
    command = tmp_path / "gnome-extensions"
    command.write_text("#!/bin/sh\nprintf enabled > enabled.txt\n", encoding="utf-8")
    command.chmod(0o755)
    script = f"""
XDG_CURRENT_DESKTOP=$1
PATH="$PWD:$PATH"
{helper}
{extension}
enable_extension wayswitch@wetoshkin.github.io
"""
    subprocess.run([shell, "-c", script, "test", desktop], cwd=tmp_path,
                   capture_output=True, text=True, check=True)
    assert (tmp_path / "enabled.txt").exists() is gnome


@pytest.mark.parametrize("source_exists,installed,migrate,expected", [
    (True, True, False, 1),
    (True, True, True, 0),
    (False, True, True, 1),
    (False, False, False, 0),
    (True, False, False, 0),
])
def test_update_detection_distinguishes_source_update_from_migration(
    shell, tmp_path, source_exists, installed, migrate, expected,
):
    source = (ROOT / "packaging/get.sh").read_text(encoding="utf-8")
    detection = next(line for line in source.splitlines() if line.startswith("HAD=0"))
    start = source.index("preserve_source_tray()")
    drop = source[start:source.index("\naur_helper()", start)]
    if source_exists:
        (tmp_path / "uninstall.sh").write_text("")
    script = f"""
set -eu
SOURCE_UNINSTALL=uninstall.sh
TRAY_AUTOSTART=autostart.desktop
TMP="$PWD"
python3() {{ "$TEST_PYTHON" "$@"; }}
have() {{ return {0 if installed else 1}; }}
say() {{ :; }}
sudo() {{ :; }}
{drop}
{detection}
{"drop_source_copy" if migrate else ":"}
printf '%s' "$HAD"
"""
    env = dict(os.environ, TEST_PYTHON=Path(sys.executable).as_posix())
    result = subprocess.run([shell, "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, check=True)
    assert result.stdout == str(expected)


@pytest.mark.parametrize("basename", [
    "org.example.WaySwitch.Settings.desktop", "io.github.Wetoshkin.WaySwitch.Settings.desktop",
])
@pytest.mark.parametrize("extra", ["", "Hidden=true\n", "X-GNOME-Autostart-enabled=false\n"])
def test_source_migration_preserves_legacy_autostart_before_old_uninstall(
    shell, tmp_path, basename, extra,
):
    folder = tmp_path / "autostart"
    folder.mkdir()
    legacy = folder / basename
    original = ("[Desktop Entry]\nType=Application\nName=WaySwitch\n"
                "Exec=wayswitch-gui --tray\n" + extra).encode()
    legacy.write_bytes(original)
    _drop_source(shell, tmp_path, 'rm -f "$TRAY_FOLDER"/*.desktop')
    assert legacy.read_bytes() == original
    assert not list(tmp_path.glob("tray.*"))


def test_source_migration_restores_multiple_entries_and_private_snapshot(shell, tmp_path):
    folder = tmp_path / "autostart"
    folder.mkdir()
    original = b"[Desktop Entry]\nType=Application\nName=WaySwitch\nExec=wayswitch-gui\n"
    for name in ["org.example.WaySwitch.desktop", "org.example.WaySwitch.Settings.desktop"]:
        (folder / name).write_bytes(original)
    private_checks = """
for private in "$TMP"/tray.*; do
    [ "$(ls -ld "$private" | cut -c1-10)" = drwx------ ] || return 96
    for saved in "$private"/*.desktop; do
        [ "$(ls -l "$saved" | cut -c1-10)" = -rw------- ] || return 96
    done
done
""" if os.name != "nt" else ""
    uninstall = private_checks + 'rm -f "$TRAY_FOLDER"/*.desktop'
    _drop_source(shell, tmp_path, uninstall)
    assert len(list(folder.iterdir())) == 2
    assert all(path.read_bytes() == original for path in folder.iterdir())


def test_source_migration_restores_entries_even_when_old_uninstall_fails(shell, tmp_path):
    folder = tmp_path / "autostart"
    folder.mkdir()
    legacy = folder / "org.example.WaySwitch.desktop"
    original = b"[Desktop Entry]\nType=Application\nName=WaySwitch\nExec=wayswitch-gui\n"
    legacy.write_bytes(original)
    result = _drop_source(shell, tmp_path, 'rm -f "$TRAY_FOLDER"/*.desktop; return 37',
                          fails=True)
    assert result.returncode == 37
    assert legacy.read_bytes() == original


@pytest.mark.parametrize("contents", [
    "unrelated", "[Desktop Entry]\nType=Application\nName=Other\nExec=wayswitch-gui\n",
    "[Desktop Entry]\nType=Application\nName=WaySwitch\nExec=other-program\n",
    "[Desktop Entry]\nType=Application\nName=WaySwitch\nExec=wayswitch-gui --other\n",
    "[Desktop Entry]\nType=Application\nName=WaySwitch\nExec=wayswitch-gui\n" + "#" * 17000,
])
def test_source_migration_does_not_restore_unrecognized_entries(shell, tmp_path, contents):
    folder = tmp_path / "autostart"
    folder.mkdir()
    legacy = folder / "org.example.WaySwitch.desktop"
    legacy.write_text(contents, encoding="utf-8")
    _drop_source(shell, tmp_path, 'rm -f "$TRAY_FOLDER"/*.desktop')
    assert not legacy.exists()


def test_source_migration_does_not_snapshot_symlink(shell, tmp_path):
    folder = tmp_path / "autostart"
    folder.mkdir()
    target = tmp_path / "target.desktop"
    target.write_text("[Desktop Entry]\nType=Application\nName=WaySwitch\n"
                      "Exec=wayswitch-gui --tray\n", encoding="utf-8")
    legacy = folder / "org.example.WaySwitch.desktop"
    try:
        legacy.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    _drop_source(shell, tmp_path, 'rm -f "$TRAY_FOLDER"/*.desktop')
    assert not legacy.exists() and not legacy.is_symlink()
    assert target.exists()


def test_source_migration_never_overwrites_changed_autostart(shell, tmp_path):
    folder = tmp_path / "autostart"
    folder.mkdir()
    legacy = folder / "org.example.WaySwitch.desktop"
    legacy.write_text("[Desktop Entry]\nType=Application\nName=WaySwitch\n"
                      "Exec=wayswitch-gui --tray\n", encoding="utf-8")
    _drop_source(shell, tmp_path, 'printf changed > "$TRAY_FOLDER/org.example.WaySwitch.desktop"')
    assert legacy.read_text() == "changed"


def test_source_tray_remove_requires_owned_content_and_needs_no_backup(shell, tmp_path):
    folder = tmp_path / "autostart"
    folder.mkdir()
    owned = "[Desktop Entry]\nType=Application\nName=WaySwitch\nExec=wayswitch-gui --tray\n"
    legacy = folder / "org.example.WaySwitch.Settings.desktop"
    legacy.write_text(owned + "Hidden=true\n", encoding="utf-8")
    canonical = folder / "io.github.Wetoshkin.WaySwitch.Settings.desktop"
    canonical.write_text("unrelated current file", encoding="utf-8")
    foreign = folder / "other.desktop"
    foreign.write_text(owned, encoding="utf-8")
    source = (ROOT / "packaging/get.sh").read_text(encoding="utf-8")
    helper = source[source.index("preserve_source_tray()"):source.index("drop_source_copy()")]
    script = f"""
set -eu
unset TMP
TRAY_AUTOSTART="$PWD/autostart/io.github.Wetoshkin.WaySwitch.Settings.desktop"
python3() {{ "$TEST_PYTHON" "$@"; }}
{helper}
preserve_source_tray remove
"""
    subprocess.run([shell, "-c", script], cwd=tmp_path, check=True, capture_output=True,
                   env=dict(os.environ, TEST_PYTHON=Path(sys.executable).as_posix()))
    assert not legacy.exists()
    assert canonical.read_text() == "unrelated current file"
    assert foreign.read_text() == owned
    assert not list(tmp_path.glob("tray.*"))


def _drop_source(shell, tmp_path, uninstall, *, fails=False):
    source = (ROOT / "packaging/get.sh").read_text(encoding="utf-8")
    start = source.find("preserve_source_tray()")
    if start < 0:
        start = source.index("drop_source_copy()")
    drop = source[start:source.index("\naur_helper()", start)]
    (tmp_path / "uninstall.sh").write_text("", encoding="utf-8")
    script = f"""
set -eu
SOURCE_UNINSTALL=uninstall.sh
TMP="$PWD"
TRAY_FOLDER="$PWD/autostart"
TRAY_AUTOSTART="$TRAY_FOLDER/io.github.Wetoshkin.WaySwitch.Settings.desktop"
python3() {{ "$TEST_PYTHON" "$@"; }}
say() {{ :; }}
sudo() {{
    [ "$1" = WAYSWITCH_MIGRATE=1 ] && [ "$2" = bash ] && \\
        [ "$3" = "$SOURCE_UNINSTALL" ] || return 97
    {uninstall}
}}
{drop}
drop_source_copy
[ "$HAD" = 0 ]
"""
    return subprocess.run([shell, "-c", script], cwd=tmp_path, check=not fails,
                          capture_output=True,
                          env=dict(os.environ, TEST_PYTHON=Path(sys.executable).as_posix()))


@pytest.mark.parametrize("bus_name", [
    "io.github.Wetoshkin.WaySwitch.Settings", "org.example.WaySwitch.Settings",
])
@pytest.mark.parametrize("owner,native_args,session,stopped,restarted", [
    ("u 101", "/usr/bin/python3 -c from wayswitch.gui.app import main; main() --tray",
     "local", True, True),
    ("u 101", "/usr/bin/python3 /usr/bin/wayswitch-gui", "local", True, False),
    ("u 101", "/usr/local/bin/wayswitch-gui --tray", "local", True, True),
    ("u 101", "/usr/bin/python3 -m wayswitch gui --tray", "local", True, True),
    ("absent", "", "local", False, False),
    ("u 101 garbage", "", "local", False, False),
    ("u 0", "", "local", False, False),
    ("u 101", "wayswitch-gui --tray", "foreign", False, False),
    ("u 101", "wayswitch-gui --tray", "ssh", False, False),
    ("u 101", "wayswitch-gui --tray", "atomic", False, False),
])
def test_native_gui_refresh(
    shell, tmp_path, bus_name, owner, native_args, session, stopped, restarted,
):
    source = (ROOT / "packaging/get.sh").read_text(encoding="utf-8")
    start = source.index("is_gnome()")
    helpers = source[start:source.index('[ "$(id -u)" = 0 ]', start)]
    detection = source[source.index("GUI_WAS=0"):source.index('step "Устанавливаю"')]
    refresh = source[source.index("enable_a11y || true") + len("enable_a11y || true"):
                     source.index("# --- Что дальше")]
    script = f"""
set -eu
cd "$1"
pid_reply=$2
args=$3
session=$4
bus_name=$5
unset SSH_CONNECTION SSH_TTY DISPLAY
WAYLAND_DISPLAY=wayland-0
ATOMIC=0
[ "$session" != ssh ] || SSH_CONNECTION=remote
[ "$session" != atomic ] || ATOMIC=1
id() {{ echo 1000; }}
have() {{ command -v "$1" >/dev/null; }}
busctl() {{
    case "$6 ${{8:-}}" in
      'ListNames ') printf 'as 3 "%s" "%s" "%s"\\n' "$bus_name" \\
          io.github.Wetoshkin.WaySwitch.Gui io.github.Wetoshkin.WaySwitch.AppImage ;;
      "GetNameOwner $bus_name") [ "$pid_reply" != absent ] && [ ! -f killed ] || return 1
                               echo 's ":1.101"' ;;
      'GetConnectionUnixProcessID :1.101') printf '%s\\n' "$pid_reply" ;;
      *) return 1 ;;
    esac
}}
ps() {{
    case "$*" in
      *uid=*101*) [ "$session" = foreign ] && echo 2000 || echo 1000 ;;
      *args=*101*) printf '%s\\n' "$args" ;;
      *) return 1 ;;
    esac
}}
kill() {{ printf '%s\\n' "$*" >> killed; }}
pgrep() {{ exit 99; }}
pkill() {{ exit 99; }}
setsid() {{ printf '%s' "$*" > started; }}
{helpers}
{detection}
{refresh}
wait
"""
    subprocess.run([shell, "-c", script, "test", ".", owner, native_args, session, bus_name],
                   cwd=tmp_path, check=True, capture_output=True)
    killed = tmp_path / "killed"
    signals = killed.read_text().splitlines() if killed.exists() else []
    assert signals == (["101"] if stopped else [])
    started = tmp_path / "started"
    command = started.read_text() if started.exists() else ""
    assert command == ("wayswitch-gui --tray" if restarted else "")


@pytest.mark.parametrize("scenario,expected", [
    ("multiple", ["101", "104"]),
    ("aliases", ["101"]),
    ("foreign", ["101"]),
    ("reused-pid", []),
    ("changed-pid", []),
    ("changed-uid", []),
    ("bad-owner", []),
    ("bad-owner-number", []),
    ("malformed-list", []),
    ("portable-only", []),
    ("no-display", []),
    ("ssh-tty", []),
    ("x11", ["101"]),
    ("replacement", ["101"]),
    ("stubborn", ["101"]),
])
def test_native_gui_stop_rechecks_bus_identity(shell, tmp_path, scenario, expected):
    source = (ROOT / "packaging/get.sh").read_text(encoding="utf-8")
    start = source.index("is_gnome()")
    helpers = source[start:source.index('[ "$(id -u)" = 0 ]', start)]
    script = f"""
set -eu
scenario=$1
unset SSH_CONNECTION SSH_TTY DISPLAY WAYLAND_DISPLAY
WAYLAND_DISPLAY=wayland-0
ATOMIC=0
[ "$scenario" != no-display ] || unset WAYLAND_DISPLAY
[ "$scenario" != ssh-tty ] || SSH_TTY=/dev/pts/1
if [ "$scenario" = x11 ]; then unset WAYLAND_DISPLAY; DISPLAY=:0; fi
id() {{ echo 1000; }}
busctl() {{
    method=$6
    name=${{8:-}}
    case "$method" in
      ListNames)
        case "$scenario" in
          portable-only) echo 'as 2 "org.example.WaySwitch.Gui" "org.example.WaySwitch.AppImage"' ;;
          malformed-list) echo 'as 2 "org.example.WaySwitch.Settings"' ;;
          multiple|aliases|foreign) printf 'as 4 "%s" "%s" "%s" "%s"\\n' \\
              io.github.Wetoshkin.WaySwitch.Settings org.example.WaySwitch.Settings \\
              org.example.WaySwitch.Gui org.example.WaySwitch.AppImage ;;
          *) echo 'as 1 "org.example.WaySwitch.Settings"' ;;
        esac ;;
      GetNameOwner)
        case "$name" in
          *.WaySwitch.Settings) ;;
          *) exit 98 ;;
        esac
        if [ -f "killed-$name" ]; then
          if [ "$scenario" = replacement ]; then echo 's ":1.999"'; return 0; fi
          return 1
        fi
        count=0
        [ ! -f "count-$name" ] || read -r count < "count-$name"
        count=$((count + 1)); printf '%s\\n' "$count" > "count-$name"
        if [ "$scenario" = bad-owner ]; then echo 's ":1.101" garbage'
        elif [ "$scenario" = bad-owner-number ]; then echo 's ":1.1:1"'
        elif [ "$scenario" = reused-pid ] && [ "$count" -ge 3 ]; then echo 's ":1.999"'
        elif [ "$name" = io.github.Wetoshkin.WaySwitch.Settings ]; then echo 's ":1.101"'
        else echo 's ":1.104"'; fi ;;
      GetConnectionUnixProcessID)
        case "$name" in
          :1.101) echo 'u 101' ;;
          :1.104)
            if [ "$scenario" = aliases ]; then echo 'u 101'
            elif [ "$scenario" = changed-pid ] && \\
                [ "$(cat count-org.example.WaySwitch.Settings)" -ge 2 ]; then echo 'u 105'
            elif [ "$scenario" = multiple ] || [ "$scenario" = foreign ]; then echo 'u 104'
            else echo 'u 101'; fi ;;
          :1.999) echo 'u 101' ;;
          *) exit 98 ;;
        esac ;;
      *) exit 98 ;;
    esac
}}
ps() {{
    for pid do :; done
    if [ "$scenario" = foreign ] && [ "$pid" = 104 ]; then echo 2000
    elif [ "$scenario" = changed-uid ] && \\
        [ "$(cat count-org.example.WaySwitch.Settings)" -ge 2 ]; then echo 2000
    else echo 1000; fi
}}
kill() {{
    printf '%s\\n' "$*" >> killed
    [ "$scenario" != stubborn ] || return 0
    touch killed-org.example.WaySwitch.Settings
    [ "$scenario" != multiple ] || [ "$1" != 101 ] || rm killed-org.example.WaySwitch.Settings
    [ "$1" != 101 ] || touch killed-io.github.Wetoshkin.WaySwitch.Settings
}}
sleep() {{ :; }}
{helpers}
if stop_gui; then echo 0 > status; else echo 1 > status; fi
"""
    subprocess.run([shell, "-c", script, "test", scenario], cwd=tmp_path,
                   check=True, capture_output=True)
    killed = tmp_path / "killed"
    assert (killed.read_text().splitlines() if killed.exists() else []) == expected
    if scenario in {"multiple", "aliases", "foreign", "replacement", "x11"}:
        assert (tmp_path / "status").read_text().strip() == "0"
    elif scenario == "stubborn":
        assert (tmp_path / "status").read_text().strip() == "1"


@pytest.fixture
def shell():
    executable = shutil.which("sh")
    if executable is None:
        # Git for Windows поставляет sh, но не добавляет его в PATH.
        executable = str(Path(shutil.which("git")).parent.parent / "bin/bash.exe")
    return executable


@pytest.mark.parametrize(
    ("version", "apt_status", "expected_status", "message"),
    [
        ("1.5.0-1", 0, 0, "ready"),
        ("1:1.8.0-2", 0, 0, "ready"),
        ("1.4.3", 0, 1, "too old"),
        ("(none)", 0, 1, "sudo apt update"),
        ("", 0, 1, "sudo apt update"),
        ("", 100, 1, "sudo apt update"),
        ("1.5.0-1", 100, 1, "sudo apt update"),
    ],
)
def test_adwaita_preflight_under_russian_locale(
    shell, tmp_path, version, apt_status, expected_status, message,
):
    source = (ROOT / "packaging/get.sh").read_text(encoding="utf-8")
    start = source.index("check_adwaita_deb()")
    check = source[start:source.index("\n}\n", start) + 3]
    # apt-cache — внешняя граница: его заголовки зависят от локали пользователя.
    commands = {
        "apt-cache": f"""#!/bin/sh
    [ "$1 $2" = 'policy gir1.2-adw-1' ] || exit 99
    if [ "${{LC_ALL:-}}" = C ]; then label=Candidate; else label=Кандидат; fi
    [ -z '{version}' ] || printf '  %s: %s\\n' "$label" '{version}'
    exit {apt_status}
""",
        "dpkg": """#!/bin/sh
    [ "$1" = --compare-versions ] && [ "$3 $4" = 'ge 1.5' ] || exit 98
    [ "$2" != 1.4.3 ]
""",
    }
    for name, command in commands.items():
        executable = tmp_path / name
        executable.write_text(command, encoding="utf-8", newline="\n")
        executable.chmod(0o755)
    script = f"""
set -eu
export LANG=ru_RU.UTF-8
unset LC_ALL
export PATH="$PWD:$PATH"
have() {{ return 0; }}
die() {{ printf '%s\\n' "$2"; exit 1; }}
{check}
check_adwaita_deb
echo ready
"""
    result = subprocess.run([shell, "-c", script], cwd=tmp_path, capture_output=True, text=True,
                            encoding="utf-8")
    assert result.returncode == expected_status, result.stdout + result.stderr
    assert message in result.stdout
    if message == "sudo apt update":
        assert "too old" not in result.stdout

@pytest.mark.parametrize('case,expected', [
    ('hostile_json_cwd', 0), ('hostile_json_path', 0),
    ('hostile_hash_cwd', 1), ('hostile_hash_path', 1),
    ('valid', 0), ('mismatch', 1), ('missing', 1), ('malformed', 1),
    ('duplicate', 1), ('absent', 2), ('network', 1), ('truncated', 1),
    ('badjson', 1), ('wrong_url', 1), ('download_failure', 1),
    ('badsize', 1), ('null_assets', 1), ('duplicate_key', 1), ('bad_release', 1),
])
def test_fetch_requires_release_integrity(shell, tmp_path, case, expected):
    source = (ROOT / 'packaging/get.sh').read_text(encoding='utf-8')
    fetch = source[source.index('fetch()'):source.index('\nget_package()')]
    data = b'package bytes'
    asset = {'name': 'wayswitch.deb', 'size': len(data),
             'digest': 'sha256:' + hashlib.sha256(data).hexdigest(),
             'browser_download_url': 'https://github.com/Wetoshkin/WaySwitch/releases/download/v1/wayswitch.deb'}
    child_env = os.environ.copy()
    if case.startswith('hostile_'):
        directory = tmp_path if case.endswith('cwd') else tmp_path / 'modules'
        directory.mkdir(exist_ok=True)
        if case.endswith('path'):
            child_env['PYTHONPATH'] = str(directory)
        marker = "from pathlib import Path\nPath('executed').touch()\n"
        if 'hash' in case:
            payload = ("class sha256:\n    def update(self, chunk): pass\n"
                       f"    def hexdigest(self): return '{hashlib.sha256(data).hexdigest()}'\n")
            (directory / 'hashlib.py').write_text(marker + payload)
        else:
            (directory / 'json.py').write_text(marker)
    if case == 'missing':
        asset.pop('digest')
    if case == 'malformed':
        asset['digest'] = 'sha256:nope'
    if case == 'badsize':
        asset['size'] = True
    if case == 'wrong_url':
        asset['browser_download_url'] = 'https://evil.example/wayswitch.deb'
    assets = [] if case == 'absent' else [asset]
    if case == 'null_assets':
        assets = None
    if case == 'duplicate':
        assets.append(asset.copy())
    (tmp_path / 'api.json').write_text('{' if case == 'badjson' else json.dumps(
        [{'tag_name': 'v1', 'assets': assets}]), encoding='utf-8')
    if case == 'bad_release':
        (tmp_path / 'api.json').write_text('[null]', encoding='utf-8')
    if case == 'duplicate_key':
        path = tmp_path / 'api.json'
        path.write_text(path.read_text().replace('"tag_name":', '"tag_name": "wrong", "tag_name":'))
    payload = b'bad' if case == 'truncated' else data
    if case == 'mismatch' or 'hostile_hash' in case:
        payload = b'changed bytes'
    (tmp_path / 'package').write_bytes(payload)
    script = f'''
set -eu
TMP="$PWD"
python3() {{ "$TEST_PYTHON" "$@"; }}
API=api
REPO=Wetoshkin/WaySwitch
have() {{ command -v "$1" >/dev/null 2>&1; }}
say() {{ printf '%s\\n' "$2" >&2; }}
curl() {{
    [ '{case}' != network ] || return 22
    out=""; url=""
    while [ "$#" -gt 0 ]; do
        case "$1" in -o) shift; out=$1 ;; -*) ;; *) url=$1 ;; esac
        shift
    done
    printf '%s\\n' "$url" >> requests
    if [ "$url" = api ]; then
        if [ -n "$out" ]; then cat api.json > "$out"; else cat api.json; fi
    else
        [ '{case}' != download_failure ] || return 22
        cat package > "$out"
    fi
}}
{fetch}
fetch deb && rc=0 || rc=$?
exit "$rc"
'''
    child_env['TEST_PYTHON'] = Path(sys.executable).as_posix()
    result = subprocess.run([shell, '-c', script], cwd=tmp_path, env=child_env,
                            capture_output=True, text=True, encoding='utf-8')
    assert not (tmp_path / 'executed').exists()
    assert result.returncode == expected, result.stdout + result.stderr
    if expected == 0:
        assert Path(result.stdout).name in ('wayswitch.deb', 'package.deb')
        assert (tmp_path / 'requests').read_text().splitlines() == [
            'api', asset['browser_download_url'],
        ]


@pytest.mark.parametrize('status,expected', [(0, 'install'), (1, 'stopped'), (2, 'source')])
def test_get_package_never_falls_back_after_integrity_failure(shell, tmp_path, status, expected):
    source = (ROOT / 'packaging/get.sh').read_text(encoding='utf-8')
    function = source[source.index('get_package()'):source.index('\nfrom_source()')]
    script = f'''
set -eu
ATOMIC=0
fetch() {{ printf package; return {status}; }}
warn() {{ :; }}
die() {{ printf stopped; exit 1; }}
{function}
get_package deb
if [ -n "$PKG" ]; then printf install; else printf source; fi
'''
    result = subprocess.run([shell, '-c', script], cwd=tmp_path, capture_output=True, text=True)
    assert result.stdout == expected
    assert result.returncode == (1 if status == 1 else 0)


def test_fetch_explains_python_requirement(shell, tmp_path):
    source = (ROOT / 'packaging/get.sh').read_text(encoding='utf-8')
    fetch = source[source.index('fetch()'):source.index('\nget_package()')]
    result = subprocess.run([shell, '-c', f'''
have() {{ return 1; }}
say() {{ printf '%s\\n%s\\n' "$1" "$2"; }}
{fetch}
fetch deb
'''], cwd=tmp_path, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 1
    assert 'Python 3 is required' in result.stderr
    assert 'Нужен Python 3' in result.stderr


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("settings_tray,legacy_tray,foreign,expected", [
    (False, True, False, True), (True, False, False, True),
    (True, True, False, True), (False, False, False, False),
    (False, True, True, False), (True, True, True, True),
])
def test_refresh_preserves_any_valid_tray_across_old_and_new_gui(
    shell, tmp_path, reverse, settings_tray, legacy_tray, foreign, expected,
):
    source = (ROOT / "packaging/get.sh").read_text(encoding="utf-8")
    helpers = source[source.index("native_gui_pid()"):source.index('[ "$(id -u)" = 0 ]')]
    start = source.index("GUI_WAS=0")
    detection = source[start:source.index('step "', start)]
    refresh = source[source.index('if stop_gui && [ "$GUI_WAS" = 1 ]'):
                     source.index("# --- Что дальше")]
    names = ["io.github.Wetoshkin.WaySwitch.Settings", "org.example.WaySwitch.Settings"]
    if reverse:
        names.reverse()
    script = f'''set -eu
unset SSH_CONNECTION SSH_TTY DISPLAY
WAYLAND_DISPLAY=wayland-0
ATOMIC=0
id() {{ echo 1000; }}
have() {{ command -v "$1" >/dev/null; }}
busctl() {{
 case "$6 ${{8:-}}" in
 'ListNames ') echo 'as 2 "{names[0]}" "{names[1]}"' ;;
 'GetNameOwner io.github.Wetoshkin.WaySwitch.Settings')
   [ ! -f killed-101 ] && echo 's ":1.101"' ;;
 'GetNameOwner org.example.WaySwitch.Settings')
   [ ! -f killed-104 ] && echo 's ":1.104"' ;;
 'GetConnectionUnixProcessID :1.101') echo 'u 101' ;;
 'GetConnectionUnixProcessID :1.104') echo 'u 104' ;;
 *) return 1;;
 esac
}}
ps() {{
 case "$*" in
 *uid=*104*) echo {2000 if foreign else 1000} ;;
 *uid=*) echo 1000 ;;
 *args=*101*) echo '/usr/bin/wayswitch-gui{" --tray" if settings_tray else ""}' ;;
 *args=*104*) echo '/usr/bin/wayswitch-gui{" --tray" if legacy_tray else ""}' ;;
 *) return 1;;
 esac
}}
kill() {{ touch "killed-$1"; printf '%s\\n' "$1" >> signals; }}
setsid() {{ printf '%s' "$*" > started; }}
sleep() {{ :; }}
{helpers}
{detection}
{refresh}
wait
'''
    subprocess.run([shell, "-c", script], cwd=tmp_path, check=True, capture_output=True)
    signaled = (tmp_path / "signals").read_text().splitlines()
    assert sorted(signaled) == (["101"] if foreign else ["101", "104"])
    started = tmp_path / "started"
    assert started.exists() is expected
    if expected:
        assert started.read_text() == "wayswitch-gui --tray"
