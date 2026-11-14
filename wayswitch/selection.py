"""«Исправить выделенное»: перевести выделенный текст в другую раскладку.

Как Shift+Pause в Punto Switcher: человек выделяет фразу, набранную не в той
раскладке, и одной командой получает её в правильной. Направление выбирается
по тому, букв какой раскладки в тексте больше (поровну — непонятно, не
трогаем). Каждый символ переводится через клавишу, которой он набран:
«ghbdtn? rfr ltkf&» → «привет, как дела?» («?» в английской — это «,» в
русской, и так далее). Символы, которых в исходной раскладке нет (цифры),
остаются как есть.

Модуль чистый: на входе текст и keymap, на выходе текст.
"""

from __future__ import annotations

import re

from wayswitch.buffer import KeyPress
from wayswitch.detector import LANG_OF_ALPHABET, TECH_MARKERS, TECH_PREFIXES

# Больше за раз не берём: это не текстовый процессор, а исправление фразы.
MAX_SELECTION = 2000


def _letter_group(keymap, ch: str, groups: tuple[int, int]) -> int | None:
    """В какой из двух раскладок этот символ — буква."""
    if not ch.isalpha():
        return None
    for group in groups:
        if keymap.key_for(ch, group) is not None or \
                keymap.key_for(ch.lower(), group) is not None:
            return group
    return None


def source_group(keymap, text: str, groups: tuple[int, int] = (0, 1)) -> int | None:
    """Раскладка, в которой текст набран: букв какой группы в нём больше."""
    counts = {g: 0 for g in groups}
    for ch in text:
        group = _letter_group(keymap, ch, groups)
        if group is not None:
            counts[group] += 1
    if not any(counts.values()) or counts[groups[0]] == counts[groups[1]]:
        return None
    return max(groups, key=lambda g: counts[g])


def convert(keymap, text: str, groups: tuple[int, int] = (0, 1)) -> tuple[str, int] | None:
    """(текст в другой раскладке, номер этой раскладки) или None — нечего менять."""
    if not text or len(text) > MAX_SELECTION:
        return None
    src = source_group(keymap, text, groups)
    if src is None:
        return None
    dst = groups[1] if src == groups[0] else groups[0]
    out = []
    for ch in text:
        hit = keymap.key_for(ch, src)
        upper = False
        if hit is None and ch.isalpha():
            hit = keymap.key_for(ch.lower(), src)
            upper = hit is not None
        if hit is None:
            out.append(ch)
            continue
        code, shift = hit
        converted = keymap.decode([KeyPress(code, shift or upper)], dst)
        out.append(converted if converted else ch)
    result = "".join(out)
    return (result, dst) if result != text else None


def mixed_layout(keymap, text: str, groups: tuple[int, int], models,
                 target_group: int | None) -> str | None:
    """Convert only unambiguous words to an explicit target; preserve separators."""
    if (type(target_group) is not int or target_group not in groups or not text
            or len(text) > MAX_SELECTION or not text.isprintable()
            or any(c.isdigit() or c in '_/\\@' for c in text)
            or text.lower().startswith(TECH_PREFIXES)
            or any(marker in text.lower() for marker in TECH_MARKERS)
            or any(not any(keymap.key_for(c, g) is not None for g in groups) for c in text)):
        return None
    model = models.get(LANG_OF_ALPHABET.get(keymap.alphabet(target_group)))
    if model is None:
        return None

    def word(token):
        letters = [c for c in token if c.isalpha()]
        if not letters:
            return token
        origins = {_letter_group(keymap, c, groups) for c in letters}
        if len(origins) != 1 or None in origins:
            return None
        source = origins.pop()
        if source == target_group:
            return token
        keys = [keymap.key_for(c, source) for c in token]
        if any(k is None for k in keys):
            return None
        result = keymap.decode([KeyPress(*k) for k in keys], target_group)
        return result if model.is_word(result) else None

    def token(match):
        value = match.group()
        direct = word(value)
        if direct != value and sum(c in ";'.,`[]" for c in value) > 1:
            raise ValueError('multiple punctuation positions')
        # Physical punctuation can encode Russian letters or separate two words.
        pieces = re.split(r"([;'.,`\[\]]+)", value)
        converted = [piece if i % 2 else word(piece) for i, piece in enumerate(pieces)]
        split = ''.join(converted) if all(p is not None for p in converted) else None
        candidates = {c for c in (direct, split) if c is not None}
        if len(candidates) != 1:
            raise ValueError('ambiguous token')
        return candidates.pop()

    try:
        result = re.sub(r"(?:[^\W\d_]|[;'.,`\[\]-])+", token, text)
    except ValueError:
        return None
    return result if result != text else None
