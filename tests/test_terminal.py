"""Обновление и удаление кнопкой: выбор терминала и команда."""

import shutil
import subprocess

import pytest

from tests.test_get_sh import shell as shell
from wayswitch.gui import terminal


def which_of(*names):
    return lambda n: f"/usr/bin/{n}" if n in names else None


def test_desktop_terminal_preferred():
    argv = terminal.terminal_argv("echo hi", "kde", which_of("gnome-terminal", "konsole"))
    assert argv == ["konsole", "-e", "sh", "-c", "echo hi"]


def test_gnome_console_and_fallbacks():
    assert terminal.terminal_argv("x", "gnome", which_of("kgx", "xterm"))[:2] == ["kgx", "--"]
    assert terminal.terminal_argv("x", "other", which_of("xterm")) == \
        ["xterm", "-e", "sh", "-c", "x"]
    assert terminal.terminal_argv("x", "gnome", which_of()) is None


def test_string_style_terminal_gets_one_quoted_argument():
    argv = terminal.terminal_argv("echo 'a b'", "", which_of("lxterminal"))
    assert argv[:2] == ["lxterminal", "-e"] and len(argv) == 3
    assert "sh -c" in argv[2]


def test_script_runs_get_sh_and_waits():
    update, remove = terminal.script(False), terminal.script(True)
    assert terminal.GET_SH_URL in update and "--uninstall" not in update
    assert "| sh -s -- --uninstall" in remove
    assert update.rstrip().endswith("read _")
    assert "--uninstall" in terminal.manual_command(True)


def test_failure_message_is_echoed_as_shell_source():
    """Текст ошибки должен попасть во внутренний sh как команда echo."""
    assert terminal.message_command("не удалось") == "\necho 'не удалось'"
    assert terminal.message_command("a; rm -rf x") == "\necho 'a; rm -rf x'"
    script = terminal.script(False)
    assert "|| echo " in script and "else echo " in script


@pytest.mark.skipif(shutil.which("sh") is None or shutil.which("dash") is None,
                    reason="нужен POSIX sh")
@pytest.mark.parametrize("partial", [False, True])
def test_curl_failure_prints_message_without_executing_it(tmp_path, monkeypatch, partial):
    marker = tmp_path / "executed"
    monkeypatch.setattr(terminal, "_", lambda message: (
        f"touch {marker}" if message == terminal.NO_NETWORK else message))
    prefix = "printf 'printf partial'; " if partial else ""
    script = "curl() { " + prefix + "return 1; }\n" + terminal.script(False)
    result = subprocess.run(["dash", "-c", script], input="\n", capture_output=True,
                            text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert str(marker) in result.stdout
    assert not marker.exists()


@pytest.mark.skipif(shutil.which("sh") is None or shutil.which("dash") is None,
                    reason="нужен POSIX sh")
def test_script_is_valid_posix_sh():
    for uninstall in (False, True):
        subprocess.run(["dash", "-n", "-c", terminal.script(uninstall)], check=True)


def test_ptyxis_preserves_shell_templates_and_interactive_input(shell):
    command = "read -r answer; printf '%s|%s' '${Version} — привет' \"$answer\"; exit 7"
    argv = terminal.terminal_argv(command, 'gnome', which_of('ptyxis'))
    # Ptyxis 50.1 expands braced environment names in command arguments.
    delivered = argv[-1].replace('${Version}', 'expanded-by-terminal')
    result = subprocess.run([shell, '-c', delivered], input=b'confirmed\n',
                            capture_output=True)
    assert result.stdout.decode('utf-8') == '${Version} — привет|confirmed'
    assert result.returncode == 7
