import pytest

from tests.fakes import RU, US, K, keys_for_text, make_keymap
from wayswitch import keycodes as kc
from wayswitch.detector import SENSITIVITY, Detector, LanguageModel, Sensitivity

RU_ALPHABET = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
EN_ALPHABET = "abcdefghijklmnopqrstuvwxyz"

RU_WORDS = [("я", 1000.0), ("не", 900.0), ("это", 800.0), ("привет", 500.0), ("мы", 700.0),
            ("буква", 100.0), ("книга", 120.0), ("эту", 300.0), ("пока", 200.0),
            ("спасибо", 250.0), ("который", 150.0), ("сон", 60.0), ("дом", 90.0),
            ("дорогой", 80.0), ("игорь", 40.0)]
EN_WORDS = [("the", 1000.0), ("hello", 500.0), ("world", 400.0), ("book", 300.0),
            ("letter", 200.0), ("this", 350.0), ("that", 340.0), ("a", 900.0), ("i", 950.0),
            ("with", 330.0), ("son", 50.0)]


@pytest.fixture
def det():
    km = make_keymap()
    models = {
        "ru": LanguageModel.from_words("ru", RU_WORDS, RU_ALPHABET, top_n=5),
        "en": LanguageModel.from_words("en", EN_WORDS, EN_ALPHABET, top_n=5),
    }
    return Detector(km, models, exceptions=set(), sensitivity=SENSITIVITY["normal"])


def typed(text, group):
    return keys_for_text(make_keymap(), text, group)


# --- исправления -------------------------------------------------------------

@pytest.mark.parametrize("typed_text,expected", [
    ("ghbdtn", "привет"),   # словарное слово
    (",erdf", "буква"),     # ведущая запятая = б
    ("'nj", "это"),         # апостроф = э
    ("'ne", "эту"),
])
def test_russian_word_typed_in_latin_layout_is_fixed(det, typed_text, expected):
    d = det.decide(typed(typed_text, US), US, RU)
    assert d.action == "fix", d.reason
    assert d.target_text == expected


def test_case_and_tail_punctuation_follow_target_layout(det):
    keys = [K(kc.KEY_G, shift=True), K(kc.KEY_H), K(kc.KEY_B), K(kc.KEY_D), K(kc.KEY_T),
            K(kc.KEY_N), K(kc.KEY_SLASH, shift=True)]
    d = det.decide(keys, US, RU)
    assert d.action == "fix"
    assert d.target_text == "Привет,"


def test_english_word_typed_in_russian_layout_is_fixed(det):
    d = det.decide(typed("руддщ", RU), RU, US)
    assert d.action == "fix" and d.target_text == "hello"


def test_unknown_but_plausible_word_fixed_by_ngrams(det):
    # «книгой» нет в словаре, но триграммы русские; латинская гипотеза — мусор.
    d = det.decide(typed("rybujq", US), US, RU)
    assert d.action == "fix" and d.target_text == "книгой"


def test_url_typed_in_russian_layout_is_fixed(det):
    d = det.decide(typed("реезыЖ..ышеуюсщь", RU), RU, US)
    assert d.action == "fix" and d.target_text == "https://site.com"


def test_short_word_needs_russian_context(det):
    keys = typed("z", US)
    assert det.decide(keys, US, RU, context=(None, None)).action == "fix"
    assert det.decide(keys, US, RU, context=("ru", "ru")).action == "fix"
    assert det.decide(keys, US, RU, context=("en", "en")).action == "keep"


def test_early_url_trigger(det):
    assert det.early_url_target(typed("реезыЖ..", RU), RU, US) == "https://"
    assert det.early_url_target(typed("реезыЖ.", RU), RU, US) is None
    assert det.early_url_target(typed("https://", US), US, RU) is None


# --- сохранение --------------------------------------------------------------

@pytest.mark.parametrize("text,group", [
    ("hello", US), ("the", US), ("github.com", US), ("https", US), ("abc123", US),
    ("user@host", US), ("привет", RU), ("Привет", RU), ("эту", RU), ("сон", RU),
    ("camelCase", US), ("NASA", US), ("ab", US),
])
def test_real_words_and_tech_tokens_are_kept(det, text, group):
    other = RU if group == US else US
    d = det.decide(typed(text, group), group, other)
    assert d.action == "keep", (text, d.reason, d.target_text)


def test_exception_list_blocks_fix(det):
    det.exceptions.add("ghbdtn")
    assert det.decide(typed("ghbdtn", US), US, RU).action == "keep"


def test_context_doubles_margin_for_ngram_path(det):
    # То же слово («книгой»), но margin подобран так, что при удвоении для
    # контекста (a.lang, a.lang) решение меняется с fix на keep.
    keys = typed("rybujq", US)
    a = det.hypothesis(keys, US)
    b = det.hypothesis(keys, RU)
    assert a.valid and not a.in_dict
    assert b.valid and not b.in_dict
    delta = det.models[b.lang].score(b.core) - det.models[a.lang].score(a.core)
    margin = delta * 0.75  # delta >= margin, но delta < 2 * margin
    sens = Sensitivity(min_length=3, margin=margin, floor=-10.0)
    d2 = Detector(det.keymap, det.models, exceptions=set(), sensitivity=sens)
    assert d2.decide(keys, US, RU, context=(None, None)).action == "fix"
    assert d2.decide(keys, US, RU, context=("ru", "ru")).action == "fix"
    assert d2.decide(keys, US, RU, context=("en", "en")).action == "keep"


def test_word_language(det):
    assert det.word_language("привет") == "ru"
    assert det.word_language("Hello") == "en"
    assert det.word_language("ghbdtn") is None
