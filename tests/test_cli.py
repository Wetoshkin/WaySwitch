import pytest

from wayswitch import __version__
from wayswitch.cli import build_parser

SELECTION_COMMANDS = [
    ("fix-selection", "FixSelection", "исправлено"),
    ("translit", "TranslitSelection", "готово"),
    ("number-words", "NumberWordsSelection", "готово"),
]


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


@pytest.mark.parametrize("args,seconds", [([], 0), (["--delay", "0"], 0), (["--delay", "5"], 5)])
def test_doctor_waits_before_sampling_and_prints_version(args, seconds, monkeypatch, capsys):
    import time

    from wayswitch import cli, doctor

    events = []

    def wait(delay):
        assert "курсор" in capsys.readouterr().err
        events.append(delay)

    monkeypatch.setattr(time, "sleep", wait)
    monkeypatch.setattr(doctor, "run_checks", lambda: events.append("checks") or [])
    assert cli.main(["doctor", *args]) == 0
    assert events == ([seconds, "checks"] if seconds else ["checks"])
    assert f"WaySwitch {__version__}" in capsys.readouterr().out


def test_doctor_rejects_negative_delay():
    from wayswitch import cli

    with pytest.raises(SystemExit) as error:
        cli.main(["doctor", "--delay", "-1"])
    assert error.value.code == 2


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


@pytest.mark.parametrize("command,method,message", SELECTION_COMMANDS)
def test_selection_command_uses_supported_backend(command, method, message, monkeypatch, capsys):
    from wayswitch import cli

    calls = []

    def call(name):
        calls.append(name)
        return ({"selection_supported": True},) if name == "GetStatus" else (True,)

    monkeypatch.setattr(cli, "call_daemon", call)
    assert cli.main([command]) == 0
    captured = capsys.readouterr()
    assert calls == ["GetStatus", method]
    assert captured.out.strip() == message
    assert captured.err == ""


@pytest.mark.parametrize("reply", [(False,), None, (), (True, False), (None,)])
@pytest.mark.parametrize("command,method", [(c, m) for c, m, _ in SELECTION_COMMANDS])
def test_selection_command_does_not_claim_success(command, method, reply, monkeypatch, capsys):
    from wayswitch import cli

    calls = []

    def call(name):
        calls.append(name)
        return ({"selection_supported": True},) if name == "GetStatus" else reply

    monkeypatch.setattr(cli, "call_daemon", call)
    assert cli.main([command]) == 1
    captured = capsys.readouterr()
    out = captured.out
    assert calls == ["GetStatus", method]
    assert "Не удалось подтвердить" in out
    assert "Проверьте" in out
    assert "одну строку" in out
    assert out.count("Не удалось подтвердить") == 1
    assert "Traceback" not in out + captured.err
    assert "нечего" not in out
    assert "выделите" not in out.lower()
    assert captured.err == ""


@pytest.mark.parametrize("reply", [None, (), ("x",), (None,)])
def test_selection_command_handles_bad_status_reply(reply, monkeypatch, capsys):
    from wayswitch import cli

    calls = []

    def call(name):
        calls.append(name)
        return reply

    monkeypatch.setattr(cli, "call_daemon", call)
    assert cli.main(["fix-selection"]) == 1
    captured = capsys.readouterr()
    assert calls == ["GetStatus"]
    assert "недоступно" in captured.out
    assert "Traceback" not in captured.out + captured.err
    assert captured.err == ""
