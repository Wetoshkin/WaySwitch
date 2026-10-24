"""Обновление и удаление кнопкой: выбор терминала и команда."""

import shutil
import subprocess

import pytest

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


@pytest.mark.skipif(shutil.which("sh") is None or shutil.which("dash") is None,
                    reason="нужен POSIX sh")
def test_script_is_valid_posix_sh():
    for uninstall in (False, True):
        subprocess.run(["dash", "-n", "-c", terminal.script(uninstall)], check=True)
