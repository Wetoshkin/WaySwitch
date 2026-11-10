"""Контрольные пары е/ё и сокращений на настоящих словарях.

Проверяем ровно то, что видит пользователь: слово, набранное не в той
раскладке, исправляется с сохранением набранной буквы, а правильный ввод,
имена, технические токены и пути не трогаются. Пороги проверяются на всех
трёх настройках чувствительности.
"""

from pathlib import Path

import pytest

from tests.fakes import RU, US, make_keymap
from wayswitch.buffer import KeyPress
from wayswitch.detector import EN_CONTRACTIONS, SENSITIVITY, Detector, LanguageModel

DATA = Path(__file__).resolve().parents[1] / "wayswitch" / "data"
SENS = ("conservative", "normal", "aggressive")
RU_E_YO = ("небоскреб", "небоскрёб", "улет", "улёт", "все")
EN_REQUIRED = ("I'm", "I'd", "won't", "don't", "can't")
EN_NAMES_TECH = (
    "GitHub", "OpenAI", "iPhone", "macOS", "WaySwitch", "React", "useState",
    "build/", "docs/", "src/main.py", "/usr/bin", "https://example.com",
    "user@host", r"C:\Users\me", "v1.2.3", "npm", "API", "README.md",
)
RU_NAMES_TECH = ("Москва", "Привет", "Ёлка", "СберБанкОнлайн", "мегаФон", "Игорь")

pytestmark = pytest.mark.skipif(not (DATA / "ru.words.gz").exists(), reason="нет данных")


@pytest.fixture(scope="module")
def setup():
    km = make_keymap()
    models = {lang: LanguageModel.load(lang) for lang in ("ru", "en")}
    return km, models


def _det(setup, sensitivity):
    km, models = setup
    return Detector(km, models, set(), SENSITIVITY[sensitivity])


def _keys(km, text, group):
    keys = []
    for ch in text:
        found = km.key_for(ch, group) or km.key_for(ch.lower(), group)
        assert found is not None, (text, ch)
        code, shift = found
        keys.append(KeyPress(code, shift=bool(shift or ch.isupper())))
    return keys


def _decide(setup, sensitivity, word, typed_group, current_group):
    km, _ = setup
    keys = _keys(km, word, typed_group)
    other_group = RU if current_group == US else US
    return _det(setup, sensitivity).decide(keys, current_group, other_group)


# --- русский: поиск е/ё ------------------------------------------------------

def test_ru_lookup_folds_yo_but_keeps_dictionary_spelling(setup):
    _, models = setup
    ru = models["ru"]
    assert ru.is_word("небоскрёб") and ru.is_word("небоскреб")
    assert "небоскрёб" in ru.words and "небоскреб" not in ru.words
    assert ru.has_prefix("небоскрё") and ru.has_prefix("небоскре")
    assert ru.prefix_count("небоскрё") == ru.prefix_count("небоскре") > 0
    assert ru.looks_like_word("небоскребу")


@pytest.mark.parametrize("sensitivity", SENS)
@pytest.mark.parametrize("word", RU_E_YO)
def test_ru_e_yo_wrong_layout_fixed_with_entered_spelling(setup, sensitivity, word):
    decision = _decide(setup, sensitivity, word, RU, US)
    assert (decision.action, decision.target_text) == ("fix", word), decision.reason
    assert decision.b.in_dict


@pytest.mark.parametrize("sensitivity", SENS)
@pytest.mark.parametrize("word", RU_E_YO + ("всё",))
def test_ru_e_yo_correct_source_kept(setup, sensitivity, word):
    decision = _decide(setup, sensitivity, word, RU, RU)
    assert decision.action == "keep", (word, decision.reason, decision.target_text)


@pytest.mark.parametrize("sensitivity", SENS)
def test_ru_vsyo_wrong_layout_keeps_dc_technical_collision(setup, sensitivity):
    """«всё» в US — это «dc~»; после хвоста «~» остаётся словарное «dc».

    Короткое слово-источник важнее догадки, иначе пострадает настоящий
    технический токен «dc». Ограничение действует на всех чувствительностях.
    """
    decision = _decide(setup, sensitivity, "всё", RU, US)
    assert decision.a.core == "dc"
    assert (decision.action, decision.reason) == ("keep", "dict-a")


# --- английский: закрытый набор сокращений -----------------------------------

def test_contraction_table_is_validated_against_shipped_dictionary(setup):
    _, models = setup
    en = models["en"]
    for form, key in EN_CONTRACTIONS.items():
        assert key in en.words, (form, key)
        assert en.is_word(form), form


@pytest.mark.parametrize("sensitivity", ("normal", "aggressive"))
@pytest.mark.parametrize("word", EN_REQUIRED)
def test_en_contractions_wrong_layout_fixed(setup, sensitivity, word):
    decision = _decide(setup, sensitivity, word, US, RU)
    assert (decision.action, decision.target_text) == ("fix", word), decision.reason


@pytest.mark.parametrize("word", ("won't", "don't", "can't"))
def test_en_long_contractions_fixed_even_on_conservative(setup, word):
    decision = _decide(setup, "conservative", word, US, RU)
    assert (decision.action, decision.target_text) == ("fix", word), decision.reason


@pytest.mark.parametrize("word", ("I'm", "I'd"))
def test_en_short_contractions_kept_on_conservative(setup, word):
    """conservative не трогает трёхбуквенные слова вне top-списка."""
    decision = _decide(setup, "conservative", word, US, RU)
    assert decision.action == "keep"
    assert decision.reason == "short"


@pytest.mark.parametrize("sensitivity", SENS)
@pytest.mark.parametrize("word", EN_REQUIRED)
def test_en_contractions_correct_source_kept(setup, sensitivity, word):
    decision = _decide(setup, sensitivity, word, US, US)
    assert decision.action == "keep", (word, decision.reason, decision.target_text)


# --- имена, технические токены и пути ---------------------------------------

@pytest.mark.parametrize("sensitivity", SENS)
@pytest.mark.parametrize("word", EN_NAMES_TECH)
def test_correct_english_names_tech_and_paths_kept(setup, sensitivity, word):
    decision = _decide(setup, sensitivity, word, US, US)
    assert decision.action == "keep", (word, decision.reason, decision.target_text)


@pytest.mark.parametrize("sensitivity", SENS)
@pytest.mark.parametrize("word", RU_NAMES_TECH)
def test_correct_russian_names_kept(setup, sensitivity, word):
    decision = _decide(setup, sensitivity, word, RU, RU)
    assert decision.action == "keep", (word, decision.reason, decision.target_text)
