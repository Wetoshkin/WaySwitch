"""Human CLI localization and locale-independent machine contracts."""

import json
import sys

import pytest

from tests.fakes import make_keymap
from wayswitch import __version__, cli, doctor, explain, i18n


@pytest.mark.parametrize(
    "locale,russian",
    [
        ("ru_RU.UTF-8", True),
        ("en_US.UTF-8", False),
        ("C", False),
        ("C.UTF-8", False),
        ("xx_XX.UTF-8", False),
        ("", False),
    ],
)
def test_help_errors_doctor_and_explain(locale, russian, locale_env, monkeypatch, capsys):
    locale_env(LANG=locale)
    monkeypatch.setitem(sys.modules, "gi", None)
    with pytest.raises(SystemExit) as result:
        cli.main(["--help"])
    assert result.value.code == 0
    output = capsys.readouterr()
    assert ("Исправление раскладки" if russian else "Keyboard layout correction") in output.out
    assert "--help" in output.out and not output.err
    with pytest.raises(SystemExit) as result:
        cli.main(["doctor", "--delay", "-1"])
    assert result.value.code == 2
    assert ("ошибка:" if russian else "error:") in capsys.readouterr().err

    monkeypatch.setattr(doctor, "run_checks", lambda: [doctor.Check("test", True)])
    assert cli.main(["doctor"]) == 0
    assert (
        "Обязательные проверки пройдены" if russian else "Required checks passed"
    ) in capsys.readouterr().out

    monkeypatch.setattr(
        explain, "load_settings", lambda: explain.Settings("normal", frozenset(), True)
    )
    monkeypatch.setattr(explain, "load_keymap", make_keymap)
    assert cli.main(["explain", "ghbdtn"]) == 0
    text = capsys.readouterr().out
    assert ("Слово: ghbdtn" if russian else "Word: ghbdtn") in text
    assert "reason=dict-b" in text and "'привет'" in text
    assert ("Служба не опрашивается" if russian else "The service is not queried") in text


def test_status_explain_json_and_version_are_locale_independent(locale_env, monkeypatch, capsys):
    state = {"active": True, "backend": "shell", "layout": "ru", "corrections_total": 12}
    monkeypatch.setattr(cli, "call_daemon", lambda _method: (state,))
    monkeypatch.setattr(
        explain, "load_settings", lambda: explain.Settings("normal", frozenset(), True)
    )
    monkeypatch.setattr(explain, "load_keymap", make_keymap)
    outputs = []
    for locale in ("ru_RU.UTF-8", "en_US.UTF-8", "C", "xx_XX"):
        locale_env(LANG=locale)
        current = []
        for argv in (["status", "--json"], ["explain", "ghbdtn", "--json"], ["version"]):
            assert cli.main(argv) == 0
            current.append(capsys.readouterr().out)
        assert json.loads(current[0]) == state
        assert current[2] == f"wayswitch {__version__}\n"
        outputs.append(current)
    assert all(output == outputs[0] for output in outputs)


def test_missing_catalog_falls_back_to_english(tmp_path, locale_env, monkeypatch):
    locale_env(LANG="ru_RU.UTF-8")
    monkeypatch.setattr(i18n, "LOCALE_DIR", tmp_path)
    i18n._load.cache_clear()
    try:
        assert "Keyboard layout correction" in cli.build_parser().format_help()
    finally:
        i18n._load.cache_clear()
