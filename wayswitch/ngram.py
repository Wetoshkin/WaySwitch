"""Символьная триграммная модель с аддитивным сглаживанием.

Оценка слова — средняя логвероятность следующего символа при двух
предыдущих, с паддингом начала/конца. Модель — обычный dict, сериализуется
в gzip+json.
"""

from __future__ import annotations

import gzip
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

PAD_START = "^"
PAD_END = "$"
K = 0.1  # добавка сглаживания


def train(words: Iterable[tuple[str, float]], alphabet: str) -> dict:
    tri: dict[str, float] = defaultdict(float)
    ctx: dict[str, float] = defaultdict(float)
    for word, weight in words:
        s = PAD_START * 2 + word.lower() + PAD_END
        for i in range(2, len(s)):
            tri[s[i - 2:i] + s[i]] += weight
            ctx[s[i - 2:i]] += weight
    vocab = len(alphabet) + 1  # плюс символ конца слова
    model = {
        "alphabet": alphabet,
        "tri": {abc: math.log((n + K) / (ctx[abc[:2]] + K * vocab)) for abc, n in tri.items()},
        "ctx": {ab: math.log(K / (n + K * vocab)) for ab, n in ctx.items()},
        "floor": math.log(1.0 / vocab),
    }
    return model


def score(model: dict, text: str) -> float:
    """Средняя логвероятность на символ; чем выше, тем правдоподобнее слово."""
    tri, ctx, floor = model["tri"], model["ctx"], model["floor"]
    s = PAD_START * 2 + text.lower() + PAD_END
    total, n = 0.0, 0
    for i in range(2, len(s)):
        abc = s[i - 2:i] + s[i]
        lp = tri.get(abc)
        if lp is None:
            lp = ctx.get(abc[:2], floor)
        total += lp
        n += 1
    return total / n if n else floor


def save(model: dict, path: Path) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)
