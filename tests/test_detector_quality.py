"""Качество детектора на отложенной выборке из настоящих словарей.

10 % слов (по хешу) исключаются из словаря и из обучения n-грамм и играют
роль «неизвестных слов». Пороги из спецификации: полнота ≥ 95 % на словах
длиной ≥ 4, набранных не в той раскладке; ложных срабатываний ≤ 0.5 % на
словах, набранных правильно.
"""

import gzip
import hashlib
from pathlib import Path

import pytest

from tests.fakes import RU, US, keys_for_text, make_keymap
from wayswitch.detector import SENSITIVITY, Detector, LanguageModel

DATA = Path(__file__).resolve().parents[1] / "wayswitch" / "data"
ALPHABETS = {"ru": "абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "en": "abcdefghijklmnopqrstuvwxyz"}
GROUP = {"ru": RU, "en": US}

pytestmark = pytest.mark.skipif(not (DATA / "ru.words.gz").exists(), reason="нет данных")


def _held_out(word: str) -> bool:
    return hashlib.md5(word.encode()).digest()[0] % 10 == 0


def _rows(lang):
    with gzip.open(DATA / f"{lang}.words.gz", "rt", encoding="utf-8") as f:
        return [(w, int(c)) for w, c in (line.rstrip("\n").split("\t") for line in f)]


@pytest.fixture(scope="module")
def setup():
    km = make_keymap()
    models, held = {}, {}
    for lang in ("ru", "en"):
        rows = _rows(lang)
        train = [(w, float(c)) for w, c in rows if not _held_out(w)]
        held[lang] = [w for w, _ in rows if _held_out(w) and len(w) >= 4][:3000]
        models[lang] = LanguageModel.from_words(lang, train, ALPHABETS[lang])
    return km, Detector(km, models, sensitivity=SENSITIVITY["normal"]), held


def _rates(km, det, held, lang):
    other = "en" if lang == "ru" else "ru"
    g, og = GROUP[lang], GROUP[other]
    hits = misses = false_pos = total_fp = 0
    for word in held[lang]:
        try:
            keys = keys_for_text(km, word, g)
        except KeyError:
            continue
        # Слово набрано в чужой раскладке: на экране мусор, должно исправиться в word.
        d = det.decide(keys, og, g)
        if d.action == "fix" and d.target_text == word:
            hits += 1
        else:
            misses += 1
        # Слово набрано правильно: трогать нельзя.
        total_fp += 1
        if det.decide(keys, g, og).action == "fix":
            false_pos += 1
    return hits / (hits + misses), false_pos / total_fp


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_recall_and_false_positive_rate(setup, lang):
    km, det, held = setup
    recall, fpr = _rates(km, det, held, lang)
    print(f"\n{lang}: recall={recall:.3f} fpr={fpr:.4f}")
    assert recall >= 0.95, f"{lang}: полнота {recall:.3f}"
    assert fpr <= 0.005, f"{lang}: ложные {fpr:.4f}"
