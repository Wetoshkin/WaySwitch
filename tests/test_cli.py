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
