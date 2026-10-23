"""«Исправить выделенное»: перевести выделенный текст в другую раскладку.

Как Shift+Pause в Punto Switcher: человек выделяет фразу, набранную не в той
раскладке, и одной командой получает её в правильной. Направление выбирается
по тому, букв какой раскладки в тексте больше. Каждый символ переводится
через клавишу, которой он набран: «ghbdtn, rfr ltkf?» → «привет, как дела?»
(запятая — это «б» в русской, и так далее). Символы, которых в исходной
раскладке нет (цифры, перевод строки), остаются как есть.

Модуль чистый: на входе текст и keymap, на выходе текст.
"""

from __future__ import annotations

from wayswitch.buffer import KeyPress

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
    if not any(counts.values()):
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
