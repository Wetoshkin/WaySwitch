import pytest

from wayswitch import __version__
from wayswitch.cli import build_parser


def test_parser_subcommands():
    p = build_parser()
    ns = p.parse_args(["run", "--verbose", "--dry-run"])
    assert ns.command == "run" and ns.verbose and ns.dry_run
    assert p.parse_args(["dry-run"]).command == "dry-run"
    assert p.parse_args(["doctor"]).command == "doctor"
    assert p.parse_args(["fix"]).command == "fix"


def test_version(capsys):
    from wayswitch.cli import main

    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


@pytest.mark.parametrize("command", ["fix-selection", "translit", "number-words"])
@pytest.mark.parametrize("status", [{}, {"selection_supported": False}])
def test_selection_command_explains_unavailable_backend(command, status, monkeypatch, capsys):
    from wayswitch import cli

    calls = []

    def call(method):
        calls.append(method)
        return (status,) if method == "GetStatus" else (False,)

    monkeypatch.setattr(cli, "call_daemon", call)
    assert cli.main([command]) == 1
    assert calls == ["GetStatus"]
    assert "недоступно" in capsys.readouterr().out


@pytest.mark.parametrize("command,method", [
    ("fix-selection", "FixSelection"),
    ("translit", "TranslitSelection"),
    ("number-words", "NumberWordsSelection"),
])
def test_selection_command_uses_supported_backend(command, method, monkeypatch, capsys):
    from wayswitch import cli

    calls = []

    def call(name):
        calls.append(name)
        return ({"selection_supported": True},) if name == "GetStatus" else (True,)

    monkeypatch.setattr(cli, "call_daemon", call)
    assert cli.main([command]) == 0
    assert calls == ["GetStatus", method]
