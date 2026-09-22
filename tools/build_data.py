#!/usr/bin/env python3
"""Сборка словарей и триграмм из открытых частотных списков.

Источник: FrequencyWords (hermitdave), списки по 50 000 слов на основе
субтитров OpenSubtitles 2018, лицензия CC-BY-SA 4.0 (см. wayswitch/data/LICENSE).
Результат детерминирован: одинаковый вход → одинаковые файлы.
"""

from __future__ import annotations

import gzip
import math
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wayswitch import ngram  # noqa: E402

BASE = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018"
SOURCES = {
    "ru": (f"{BASE}/ru/ru_50k.txt", "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"),
    "en": (f"{BASE}/en/en_50k.txt", "abcdefghijklmnopqrstuvwxyz"),
}
DATA = Path(__file__).resolve().parents[1] / "wayswitch" / "data"


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8")


def parse(text: str, alphabet: str) -> list[tuple[str, int]]:
    letters = set(alphabet)
    rows = []
    seen = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        word, count = parts[0].lower(), int(parts[1])
        # Только слова целиком из букв алфавита; дубликаты по регистру схлопываем.
        if not word or set(word) - letters or word in seen:
            continue
        seen.add(word)
        rows.append((word, count))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    for lang, (url, alphabet) in SOURCES.items():
        print(f"{lang}: загрузка {url}")
        rows = parse(fetch(url), alphabet)
        print(f"{lang}: {len(rows)} слов")
        with gzip.open(DATA / f"{lang}.words.gz", "wt", encoding="utf-8") as f:
            for word, count in rows:
                f.write(f"{word}\t{count}\n")
        # Тот же вес, что и в LanguageModel.from_words: log(1 + частота).
        model = ngram.train(((w, math.log1p(c)) for w, c in rows), alphabet)
        ngram.save(model, DATA / f"{lang}.ngrams.json.gz")
        print(f"{lang}: триграмм {len(model['tri'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
