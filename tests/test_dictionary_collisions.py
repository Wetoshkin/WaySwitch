import pytest

from tests.fakes import RU, US, keys_for_text, make_keymap
from wayswitch.detector import SENSITIVITY, Detector, LanguageModel


@pytest.fixture(scope="module")
def models():
    return {lang: LanguageModel.load(lang) for lang in ("ru", "en")}


@pytest.fixture
def detector(models):
    return Detector(make_keymap(), dict(models))


@pytest.mark.parametrize("text,target", [("nen", "тут"), ("Nen", "Тут"), ("nen!", "тут!")])
def test_rare_dictionary_collision_uses_frequent_target(detector, text, target):
    result = detector.decide(keys_for_text(make_keymap(), text, US), US, RU)
    assert (result.action, result.target_text, result.reason) == ("fix", target, "dict-frequency")


@pytest.mark.parametrize("context", [("en", "en"), (None, "en")])
def test_english_context_keeps_rare_english_token(detector, context):
    result = detector.decide(keys_for_text(make_keymap(), "nen", US), US, RU, context)
    assert result.action == "keep"


@pytest.mark.parametrize("word", ["tot", "the", "you", "her", "hello", "world", "cat", "dog",
                                  "run", "pen", "test", "python", "NEN"])
def test_normal_english_words_and_abbreviations_stay_unchanged(detector, word):
    result = detector.decide(keys_for_text(make_keymap(), word, US), US, RU)
    assert (result.action, result.target_text) == ("keep", word)


@pytest.mark.parametrize("protection", ["exception", "target-exception", "personal"])
def test_explicit_word_protections_override_collision_frequency(detector, protection):
    if protection == "personal":
        detector.set_personal_words({("en", "nen")})
    else:
        detector.exceptions.add("nen" if protection == "exception" else "тут")
    result = detector.decide(keys_for_text(make_keymap(), "nen", US), US, RU)
    assert result.action == "keep"


def test_other_personal_words_preserve_shipped_frequencies(detector):
    detector.set_personal_words({("en", "customtoken")})
    result = detector.decide(keys_for_text(make_keymap(), "nen", US), US, RU)
    assert (result.action, result.target_text) == ("fix", "тут")


def test_conservative_minimum_length_still_applies(detector):
    detector.sensitivity = SENSITIVITY["conservative"]
    result = detector.decide(keys_for_text(make_keymap(), "nen", US), US, RU)
    assert result.action == "keep"


def test_all_shipped_english_collisions_with_frequent_russian_words(models):
    km = make_keymap()
    detector = Detector(km, models)
    changed = {}
    for target in models["ru"].top:
        keys = keys_for_text(km, target, RU)
        source = detector.hypothesis(keys, US).core
        if not models["en"].is_word(source):
            continue
        result = detector.decide(keys, US, RU)
        if result.action == "fix":
            changed[source] = result.target_text
    assert changed == {"nen": "тут"}


@pytest.mark.parametrize("condition,want", [
    ("rare", "fix"), ("source-frequent", "keep"), ("source-top", "keep"),
    ("small-ratio", "keep"), ("no-counts", "keep"),
    ("ngram-loss", "keep"), ("ngram-floor", "keep"),
])
def test_collision_evidence_requires_every_guard(condition, want):
    source = LanguageModel.from_words(
        "en", [("abc", 10 if condition == "source-frequent" else 1), ("the", 10_000_000)],
        "abcdefghijklmnopqrstuvwxyz", top_n=2 if condition == "source-top" else 1)
    target = LanguageModel.from_words(
        "ru", [("фис", 100 if condition == "small-ratio" else 10_000_000), ("я", 10_000_000)],
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя", top_n=2)
    if condition == "no-counts":
        source = LanguageModel("en", source.words, source.top, source.model)
    if condition in {"ngram-loss", "ngram-floor"}:
        source.model = {"tri": {}, "ctx": {}, "floor": -1 if condition == "ngram-loss" else -10}
        target.model = {"tri": {}, "ctx": {}, "floor": -4 if condition == "ngram-loss" else -8}
    detector = Detector(make_keymap(), {"en": source, "ru": target})
    result = detector.decide(keys_for_text(make_keymap(), "abc", US), US, RU)
    assert result.action == want


def test_russian_dictionary_words_are_not_replaced_with_frequent_english(models):
    km = make_keymap()
    detector = Detector(km, models)
    changed = {}
    for target in models["en"].top:
        keys = keys_for_text(km, target, US)
        source = detector.hypothesis(keys, RU).core
        if models["ru"].is_word(source):
            result = detector.decide(keys, RU, US)
            if result.action == "fix":
                changed[source] = result.target_text
    assert changed == {}


@pytest.mark.parametrize("early", [False, True])
def test_controller_corrects_first_word_of_physical_phrase(models, early):
    from tests.test_controller import env, type_text

    state = env.__wrapped__()
    state["ctrl"].detector = Detector(state["km"], dict(models))
    state["ctrl"].config.general.early_switch = early
    type_text(state, "nen ghbdtn ", group=US)
    assert state["corrected"] == [("nen", "тут", False)]
    assert state["backend"].current() == RU
