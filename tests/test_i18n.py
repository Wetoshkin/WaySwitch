"""Выбор языка, каталог и целостность переводов GUI.

Тесты не компилируют PO: на Windows GNU msgfmt может отсутствовать. Готовый
MO читается стандартным gettext.GNUTranslations, а соответствие PO/MO на Linux
проверяет отдельный тест через msgfmt.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import string
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from wayswitch import i18n
from wayswitch.gui import gesture_keys, onboarding, portable, terminal, texts

ROOT = Path(__file__).resolve().parents[1]
PO = ROOT / "wayswitch" / "locale" / "ru" / "LC_MESSAGES" / "wayswitch.po"
MO = PO.with_suffix(".mo")
QML = ROOT / "packaging" / "kde" / "io.github.Wetoshkin.wayswitch" / "contents" / "ui" / "main.qml"
LOCALE_VARIABLES = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")


def _catalog():
    with MO.open("rb") as stream:
        return i18n.GNUTranslations(stream)


def _has(catalog, message: str) -> bool:
    return message in catalog._catalog or (message, 0) in catalog._catalog


def _literal_msgids() -> set[str]:
    messages: set[str] = set()
    for path in (ROOT / "wayswitch").rglob("*.py"):
        if path.name == "i18n.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_" and node.args):
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    messages.add(first.value)
    return messages


def _table_msgids() -> set[str]:
    messages: set[str] = set()
    messages.update(texts.BACKEND_HUMAN.values())
    messages.update(texts.BACKEND_CHOICES)
    messages.update(texts.SENSITIVITY_CHOICES)
    messages.update(texts.MANUAL_CHOICES)
    messages.update(texts.LAYOUT_HUMAN.values())
    messages.update(texts.XKB_HUMAN.values())
    messages.add(texts.LAYOUT_SCOPE_TEXT)
    for row in texts.SELECTION_KEY_ROWS:
        messages.update(row[1:])
    for spec in texts.ONBOARDING_CHECKS.values():
        messages.update(item for item in spec[:3] if isinstance(item, str))
    messages.update(label for label, _fn in onboarding.ACTIONS.values())
    messages.update(gesture_keys._SIDES.values())
    messages.add(portable.PACKAGED_UPDATE_TEXT)
    messages.add(portable.FLATPAK_UPDATE_TEMPLATE)
    messages.add(portable.SETUP)
    messages.update({terminal.TITLE_UPDATE, terminal.TITLE_REMOVE, terminal.NO_NETWORK,
                     terminal.NO_CURL, terminal.PRESS_ENTER})
    return messages


def expected_msgids() -> set[str]:
    messages = _literal_msgids() | _table_msgids()
    messages.update({"word", "{n} time"})
    messages.update(re.findall(r'i18nd\("wayswitch",\s*"((?:[^"\\]|\\.)*)"',
                               QML.read_text(encoding="utf-8")))
    return messages


def _format_fields(message: str) -> set[str]:
    fields = set()
    for _literal, field, _spec, _conversion in string.Formatter().parse(message):
        if field is not None:
            fields.add(field)
    return fields


def _percent_fields(message: str) -> set[str]:
    return set(re.findall(r"%(\d+)", message))


def _markup_tags(message: str) -> Counter:
    return Counter(re.findall(r"</?([A-Za-z][\w-]*)", message))


# --- выбор языка -----------------------------------------------------------------

@pytest.mark.parametrize("environment,expected", [
    ({}, "en"),
    ({"LANG": "ru_RU.UTF-8"}, "ru"),
    ({"LANG": "ru_RU"}, "ru"),
    ({"LANG": "en_US.UTF-8"}, "en"),
    ({"LANG": "C"}, "en"),
    ({"LANG": "C.UTF-8"}, "en"),
    ({"LANG": "POSIX"}, "en"),
    ({"LANG": "de_DE.UTF-8"}, "en"),
    ({"LANG": "ru_RU.UTF-8", "LC_ALL": "en_US.UTF-8"}, "en"),
    ({"LANG": "ru_RU.UTF-8", "LC_MESSAGES": "en_US.UTF-8"}, "en"),
    ({"LANG": "ru_RU.UTF-8", "LC_ALL": ""}, "ru"),
    ({"LANG": "en_US.UTF-8", "LANGUAGE": "ru"}, "ru"),
    ({"LANG": "ru_RU.UTF-8", "LANGUAGE": "en:ru"}, "en"),
    ({"LANG": "en_US.UTF-8", "LANGUAGE": "C:ru"}, "en"),
    ({"LANG": "en_US.UTF-8", "LANGUAGE": "POSIX:ru"}, "en"),
    ({"LANG": "ru_RU.UTF-8", "LANGUAGE": "fr:ru"}, "ru"),
    ({"LANG": "C", "LANGUAGE": "ru"}, "en"),
    ({"LC_ALL": "C.UTF-8", "LANGUAGE": "ru"}, "en"),
    ({"LC_MESSAGES": "POSIX", "LANGUAGE": "ru", "LANG": "ru_RU.UTF-8"}, "en"),
    ({"LANGUAGE": "ru"}, "en"),
])
def test_select_language(environment, expected):
    assert i18n.select_language(environment) == expected


def test_translation_uses_environment_without_global_state(monkeypatch):
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    assert i18n.gettext("Settings") == "Параметры"
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert i18n.gettext("Settings") == "Settings"
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("LANGUAGE", "ru")
    assert i18n.gettext("Settings") == "Settings"


def test_c_locale_ignores_language_for_russian_catalog(monkeypatch):
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANGUAGE", "ru:en")
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    assert i18n.select_language() == "en"
    assert texts.status_view(None).title == "WaySwitch is not running"


def _fresh_environment(**values: str) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items()
                   if key not in LOCALE_VARIABLES and key not in ("PYTHONPATH", "PYTHONHOME")}
    environment.update(values)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


@pytest.mark.parametrize("environment,language,title", [
    ({"LANG": "ru_RU.UTF-8"}, "ru", "WaySwitch не запущен"),
    ({"LANG": "en_US.UTF-8"}, "en", "WaySwitch is not running"),
    ({"LANG": "C.UTF-8", "LANGUAGE": "ru"}, "en", "WaySwitch is not running"),
    ({"LC_ALL": "en_US.UTF-8", "LANG": "ru_RU.UTF-8"}, "en", "WaySwitch is not running"),
])
def test_fresh_process_import_uses_environment(environment, language, title):
    code = ("from wayswitch import i18n; "
            "from wayswitch.gui.texts import status_view; "
            "print(i18n.select_language()); print(status_view(None).title)")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                            env=_fresh_environment(**environment),
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [language, title]


# --- числа и множественные формы --------------------------------------------------

@pytest.mark.parametrize("n,form", [
    (1, "слово"), (2, "слова"), (5, "слов"), (11, "слов"), (12, "слов"),
    (14, "слов"), (21, "слово"), (22, "слова"), (25, "слов"),
])
def test_russian_word_plural(ru_locale, n, form):
    assert i18n.ngettext("word", "words", n) == form
    assert f"{n} {form}" in texts.tray_summary({"corrections_total": n})


@pytest.mark.parametrize("n,form", [
    (1, "word"), (2, "words"), (5, "words"), (11, "words"), (12, "words"),
    (14, "words"), (21, "words"), (22, "words"), (25, "words"),
])
def test_english_word_plural(en_locale, n, form):
    assert i18n.ngettext("word", "words", n) == form
    assert f"{n} {form}" in texts.tray_summary({"corrections_total": n})


@pytest.mark.parametrize("n,expected", [
    (1, "с первого раза"), (2, "2 раза"), (5, "5 раз"), (11, "11 раз"),
    (12, "12 раз"), (14, "14 раз"), (21, "21 раз"), (22, "22 раза"),
])
def test_russian_format_times(ru_locale, n, expected):
    assert texts.format_times(n) == expected


@pytest.mark.parametrize("n,expected", [
    (1, "first try"), (2, "2 times"), (5, "5 times"), (11, "11 times"),
    (12, "12 times"), (14, "14 times"), (21, "21 times"),
])
def test_english_format_times(en_locale, n, expected):
    assert texts.format_times(n) == expected


def test_plural_helper_uses_active_language(ru_locale):
    assert texts.plural(21, "слово", "слова", "слов") == "слово"
    assert texts.plural(22, "слово", "слова", "слов") == "слова"
    assert texts.plural(25, "слово", "слова", "слов") == "слов"


def test_plural_helper_uses_english_rules(en_locale):
    assert texts.plural(1, "word", "words", "words") == "word"
    assert texts.plural(2, "word", "words", "words") == "words"
    assert texts.plural(21, "word", "words", "words") == "words"


def test_numbers_and_layout_names_both_languages(ru_locale):
    assert texts.format_seconds(0.5) == "0,5 с"
    assert texts.format_seconds(0) == "нет"
    assert texts.format_ms(30) == "30 мс"
    assert texts.xkb_human("us, ru") == "English (US), Русская"


def test_numbers_and_layout_names_english(en_locale):
    assert texts.format_seconds(0.5) == "0.5 s"
    assert texts.format_seconds(0) == "none"
    assert texts.format_ms(30) == "30 ms"
    assert texts.xkb_human("us, ru") == "English (US), Russian"


# --- каталог --------------------------------------------------------------------

def test_catalog_covers_all_gui_messages(en_locale):
    catalog = _catalog()
    missing = sorted(message for message in expected_msgids()
                     if not _has(catalog, message))
    assert missing == []


def test_english_messages_contain_no_accidental_russian(en_locale):
    examples = ("ghbd", "Привет", "пРИВЕТ", "сто двадцать", "Privet")
    bad = sorted(message for message in expected_msgids()
                 if re.search("[А-Яа-яЁё]", message)
                 and not any(example in message for example in examples))
    assert bad == []


def test_russian_catalog_translates_key_ui_strings():
    catalog = _catalog()
    expected = {
        "Settings": "Параметры",
        "WaySwitch is not running": "WaySwitch не запущен",
        "Correction is on": "Исправление включено",
        "Pause": "Пауза",
        "Resume": "Возобновить",
        "Fix selection": "Исправить выделенное",
        "Gestures and keys": "Жесты и клавиши",
        "Cancel": "Отмена",
        "Quit": "Выйти",
    }
    for message, translation in expected.items():
        assert catalog.gettext(message) == translation


def test_russian_catalog_has_no_stale_gnome_only_selection_limit():
    catalog = _catalog()
    assert not _has(catalog, " (GNOME only for now)")


def test_placeholders_and_markup_survive_translation():
    catalog = _catalog()
    checked = 0
    for key, value in catalog._catalog.items():
        if isinstance(key, tuple):
            continue
        if not key:
            continue
        assert isinstance(value, str)
        assert _format_fields(key) == _format_fields(value), key
        assert _percent_fields(key) == _percent_fields(value), key
        assert _markup_tags(key) == _markup_tags(value), key
        checked += 1
    assert checked > 300
    for index in range(3):
        form = catalog._catalog[("word", index)]
        assert _format_fields(form) == set()
        form = catalog._catalog[("{n} time", index)]
        assert _format_fields(form) == {"n"}


def test_qml_uses_shared_wayswitch_domain():
    source = QML.read_text(encoding="utf-8")
    assert 'i18nd("wayswitch"' in source
    assert "import org.kde.ki18n" not in source


@pytest.mark.skipif(shutil.which("msgfmt") is None, reason="нужен GNU msgfmt")
def test_po_passes_msgfmt_and_matches_mo(tmp_path):
    output = tmp_path / "wayswitch.mo"
    result = subprocess.run(["msgfmt", "--check", "--check-format", "--no-hash",
                             "-o", str(output), str(PO)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with MO.open("rb") as stream:
        installed = i18n.GNUTranslations(stream)
    with output.open("rb") as stream:
        rebuilt = i18n.GNUTranslations(stream)
    installed_entries = {key: value for key, value in installed._catalog.items() if key}
    rebuilt_entries = {key: value for key, value in rebuilt._catalog.items() if key}
    assert rebuilt_entries == installed_entries
