"""`wayswitch explain`: разбор решения детектора без службы и устройств."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fakes import make_keymap
from wayswitch import cli, explain
from wayswitch import config as cfgmod
from wayswitch.keymap import XkbKeymap

pytestmark = pytest.mark.usefixtures("ru_locale")

ROOT = Path(__file__).resolve().parents[1]


def _settings(sensitivity: str = "normal", exceptions=(), two_capitals: bool = True):
    return explain.Settings(sensitivity, frozenset(exceptions), two_capitals)


@pytest.fixture(scope="module")
def models():
    return explain.load_models()


def _report(word: str, from_lang: str = "en", settings=None, models=None) -> dict:
    return explain.build_report(
        word, from_lang,
        settings=settings or _settings(),
        keymap=make_keymap(),
        models=models or explain.load_models(),
    )


def test_cli_explain_default_text_and_json_without_daemon(monkeypatch, capsys):
    monkeypatch.setattr(explain, "load_settings", lambda: _settings())
    monkeypatch.setattr(explain, "load_keymap", make_keymap)
    monkeypatch.setattr(cli, "call_daemon", lambda *_: pytest.fail("explain must not use D-Bus"))

    assert cli.main(["explain", "ghbdtn"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "en (us) → ru (ru)" in captured.out
    assert "по умолчанию --from en = us" in captured.out
    assert "'ghbdtn'" in captured.out and "'привет'" in captured.out
    assert "Решение: fix → 'привет' (reason=dict-b" in captured.out
    assert "Контекст: пустой" in captured.out
    assert "Оценки n-грамм: не вычислялись" in captured.out

    assert cli.main(["explain", "ghbdtn", "--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out.splitlines()[-1])
    assert data["from"] == "en" and data["to"] == "ru"
    assert data["layouts"] == {"source": "us", "target": "ru"}
    assert data["context"] == []
    assert data["a"]["text"] == "ghbdtn" and data["a"]["in_dictionary"] is False
    assert data["b"]["text"] == "привет" and data["b"]["in_dictionary"] is True
    assert data["decision"]["action"] == "fix"
    assert data["decision"]["target"] == "привет"
    assert data["decision"]["reason"] == "dict-b"
    assert "ngram_score" not in data["a"] and "ngram_score" not in data["b"]
    assert captured.err == ""


def test_explain_reverse_direction_ru_to_us(models):
    report = _report("привет", "ru", models=models)
    assert report["from"] == "ru" and report["to"] == "en"
    assert report["layouts"] == {"source": "ru", "target": "us"}
    assert report["a"]["text"] == "привет" and report["a"]["in_dictionary"] is True
    assert report["b"]["text"] == "ghbdtn" and report["b"]["in_dictionary"] is False
    assert report["decision"] == {
        "action": "keep", "target": "привет", "reason": "dict-a", "confidence": 1.0,
    }


@pytest.mark.parametrize(("word", "exceptions", "reason"), [
    ("hello", (), "dict-a"),
    ("hf", (), "short"),
    ("ghbdtn.com", (), "tech"),
    ("ghbdtn", ("ghbdtn",), "exception"),
    ("ghbdtn!", ("ghbdtn",), "exception"),
    ("123", (), "digits"),
])
def test_explain_known_exception_short_tech_and_invalid(models, word, exceptions, reason):
    report = _report(word, settings=_settings(exceptions=exceptions), models=models)
    assert report["decision"]["action"] == "keep"
    assert report["decision"]["reason"] == reason
    assert report["exception"] is bool(exceptions)


def test_explain_ngram_scores_only_when_computed(models):
    ngram = _report("qwerty", models=models)
    assert isinstance(ngram["a"]["ngram_score"], float)
    assert isinstance(ngram["b"]["ngram_score"], float)
    assert ngram["decision"]["reason"].startswith("ngram Δ=")

    dictionary = _report("ghbdtn", models=models)
    assert "ngram_score" not in dictionary["a"]
    assert "ngram_score" not in dictionary["b"]


def test_explain_sensitivity_override(monkeypatch, capsys):
    monkeypatch.setattr(explain, "load_settings", lambda: _settings())
    monkeypatch.setattr(explain, "load_keymap", make_keymap)
    assert cli.main(["explain", "ghbdtn", "--sensitivity", "conservative", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["settings"]["sensitivity"]["name"] == "conservative"
    assert data["settings"]["sensitivity"]["margin"] == 2.5


def test_explain_unmappable_chars_is_not_daemon_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(explain, "load_settings", lambda: _settings())
    monkeypatch.setattr(explain, "load_keymap", make_keymap)
    monkeypatch.setattr(cli, "call_daemon", lambda *_: pytest.fail("explain must not use D-Bus"))

    assert cli.main(["explain", "ghbdtn€"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "в раскладке us нельзя набрать" in captured.err
    assert "€" in captured.err
    assert "не отвечает" not in captured.err
    assert "D-Bus" not in captured.err


def test_explain_config_error_is_distinct(monkeypatch, capsys):
    def broken():
        raise cfgmod.ConfigError("sensitivity: допустимо ('normal',)")

    monkeypatch.setattr(explain.cfgmod, "load", broken)

    assert cli.main(["explain", "ghbdtn", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "не удалось прочитать конфигурацию" in captured.err
    assert "sensitivity" in captured.err
    assert "libxkbcommon" not in captured.err


def test_explain_missing_libxkbcommon_is_distinct(monkeypatch, capsys):
    monkeypatch.setattr(explain, "load_settings", lambda: _settings())
    monkeypatch.setattr(XkbKeymap, "available", classmethod(lambda _cls: False))

    assert cli.main(["explain", "ghbdtn"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "libxkbcommon недоступен" in captured.err
    assert "не отвечает" not in captured.err


def test_explain_import_path_has_no_platform_backends():
    code = (
        "import sys, wayswitch.explain\n"
        "blocked = ('gi', 'evdev', 'wayswitch.actuator', 'wayswitch.daemon',\n"
        "           'wayswitch.backends')\n"
        "print(','.join(name for name in blocked if name in sys.modules))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                            text=True, check=True)
    assert result.stdout.strip() == ""


@pytest.mark.skipif(not XkbKeymap.available(), reason="нет libxkbcommon")
def test_explain_with_real_us_ru_libxkbcommon(models):
    """Linux-проверка: явные us/ru из libxkbcommon дают тот же разбор."""
    report = explain.build_report("ghbdtn", "en", settings=_settings(),
                                  keymap=explain.load_keymap(), models=models)
    assert report["a"]["text"] == "ghbdtn"
    assert report["b"]["text"] == "привет"
    assert report["decision"]["action"] == "fix"
