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
enable_extension wayswitch@siberia.ru
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
    start = source.index("drop_source_copy()")
    drop = source[start:source.index("\n}\n", start) + 3]
    if source_exists:
        (tmp_path / "uninstall.sh").write_text("")
    script = f"""
set -eu
SOURCE_UNINSTALL=uninstall.sh
TRAY_AUTOSTART=autostart.desktop
have() {{ return {0 if installed else 1}; }}
say() {{ :; }}
sudo() {{ :; }}
{drop}
{detection}
{"drop_source_copy" if migrate else ":"}
printf '%s' "$HAD"
"""
    result = subprocess.run([shell, "-c", script], cwd=tmp_path,
                            capture_output=True, text=True, check=True)
    assert result.stdout == str(expected)


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
def test_native_gui_refresh(shell, tmp_path, owner, native_args, session, stopped, restarted):
    source = (ROOT / "packaging/get.sh").read_text(encoding="utf-8")
    start = source.index("is_gnome()")
    helpers = source[start:source.index('[ "$(id -u)" = 0 ]', start)]
    detection = source[source.index("GUI_WAS=0"):source.index('step "Устанавливаю"')]
    refresh = source[source.index("enable_a11y || true") + len("enable_a11y || true"):
                     source.index("# --- Что дальше")]
    script = f"""
set -eu
cd "$1"
owner=$2
args=$3
session=$4
unset SSH_CONNECTION SSH_TTY DISPLAY
WAYLAND_DISPLAY=wayland-0
ATOMIC=0
[ "$session" != ssh ] || SSH_CONNECTION=remote
[ "$session" != atomic ] || ATOMIC=1
id() {{ echo 1000; }}
have() {{ command -v "$1" >/dev/null; }}
busctl() {{
    for name do :; done
    case "$name" in
      ru.siberia.WaySwitch.Settings) [ "$owner" != absent ] && [ ! -f killed ] || return 1
                                      printf '%s\\n' "$owner" ;;
      ru.siberia.WaySwitch.Gui) echo 'u 102' ;;
      ru.siberia.WaySwitch.AppImage) echo 'u 103' ;;
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
# Old command-line scan also finds both portable Python launchers.
pgrep() {{ echo 102; echo 103; }}
pkill() {{ printf '102\\n103\\n' >> killed; }}
setsid() {{ printf '%s' "$*" > started; }}
{helpers}
{detection}
{refresh}
wait
"""
    subprocess.run([shell, "-c", script, "test", ".", owner, native_args, session],
                   cwd=tmp_path, check=True, capture_output=True)
    killed = tmp_path / "killed"
    signals = killed.read_text().splitlines() if killed.exists() else []
    assert signals == (["101"] if stopped else [])
    started = tmp_path / "started"
    command = started.read_text() if started.exists() else ""
    assert command == ("wayswitch-gui --tray" if restarted else "")


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
