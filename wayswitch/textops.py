"""Преобразования выделенного текста: транслитерация и число прописью.

Модуль чистый: на входе строка, на выходе строка (или None — нечего менять).
Печатает результат контроллер, как и «Исправить выделенное».
"""

from __future__ import annotations

# --- транслитерация -----------------------------------------------------------
# Читаемая латиница, как пишут в адресах и никах: «Щукин» → «Shchukin»,
# «Юля» → «Yulya». Ъ и Ь пропадают — обратно они не восстанавливаются.

RU_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Обратно — сначала длинные сочетания: «shch» раньше «sh», «sh» раньше «s».
LAT_TO_RU = [
    ("shch", "щ"), ("sch", "щ"), ("yo", "ё"), ("yu", "ю"), ("ya", "я"), ("ye", "е"),
    ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"), ("sh", "ш"),
    ("a", "а"), ("b", "б"), ("v", "в"), ("g", "г"), ("d", "д"), ("e", "е"),
    ("z", "з"), ("i", "и"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"),
    ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"),
    ("f", "ф"), ("h", "х"), ("c", "к"), ("q", "к"), ("w", "в"), ("x", "кс"),
    ("j", "дж"),
]
_RU_VOWELS = set("аеёиоуыэюя")
MAX_TEXT = 2000


def _has_cyrillic(text: str) -> bool:
    return any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in text)


def _has_latin(text: str) -> bool:
    return any("a" <= ch.lower() <= "z" for ch in text)


def _match_case(piece: str, source: str, word_upper: bool) -> str:
    """Регистр перевода — по исходной букве: «Ж» → «Zh», в слове капсом — «ZH»."""
    if not source[0].isupper():
        return piece
    return piece.upper() if word_upper else piece[:1].upper() + piece[1:]


def _word_is_upper(text: str, i: int) -> bool:
    """Слово вокруг позиции i набрано капсом (больше одной буквы, все заглавные)."""
    start = i
    while start > 0 and text[start - 1].isalpha():
        start -= 1
    end = i
    while end < len(text) and text[end].isalpha():
        end += 1
    word = text[start:end]
    return len(word) > 1 and word.isupper()


def to_latin(text: str) -> str:
    out = []
    for i, ch in enumerate(text):
        piece = RU_TO_LAT.get(ch.lower())
        if piece is None:
            out.append(ch)
        elif piece:
            out.append(_match_case(piece, ch, _word_is_upper(text, i)))
    return "".join(out)


def to_cyrillic(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        upper = _word_is_upper(text, i)
        for lat, ru in LAT_TO_RU:
            chunk = text[i:i + len(lat)]
            if chunk.lower() == lat:
                out.append(_match_case(ru, chunk, upper))
                i += len(lat)
                break
        else:
            ch = text[i]
            if ch == "'" and 0 < i < len(text) - 1 and text[i - 1].isalpha() \
                    and text[i + 1].isalpha():
                out.append("ь")  # «Ol'ga» → «Ольга»; кавычки вокруг слов — как есть
            elif ch.lower() == "y":
                # После гласной — «й» («Sergey» → «Сергей»), иначе «ы» («ty» → «ты»).
                prev = out[-1][-1].lower() if out and out[-1] else ""
                out.append(_match_case("й" if prev in _RU_VOWELS else "ы", ch, upper))
            else:
                out.append(ch)
            i += 1
    return "".join(out)


def transliterate(text: str) -> str | None:
    """Кириллица → латиница, иначе латиница → кириллица; None — нечего менять."""
    if not text or len(text) > MAX_TEXT:
        return None
    if _has_cyrillic(text):
        result = to_latin(text)
    elif _has_latin(text):
        result = to_cyrillic(text)
    else:
        return None
    return result if result != text else None


# --- число прописью --------------------------------------------------------------

_UNITS_M = ("", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_UNITS_F = ("", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_TEENS = ("десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
          "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать")
_TENS = ("", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят",
         "семьдесят", "восемьдесят", "девяносто")
_HUNDREDS = ("", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот",
             "семьсот", "восемьсот", "девятьсот")
# (одна, две–четыре, пять и больше), женский ли род
_SCALES = ((("", "", ""), False),
           (("тысяча", "тысячи", "тысяч"), True),
           (("миллион", "миллиона", "миллионов"), False),
           (("миллиард", "миллиарда", "миллиардов"), False))
MAX_NUMBER = 10 ** 12 - 1


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    if 11 <= n % 100 <= 14:
        return forms[2]
    if n % 10 == 1:
        return forms[0]
    if 2 <= n % 10 <= 4:
        return forms[1]
    return forms[2]


def _triplet(n: int, feminine: bool) -> list[str]:
    words = [_HUNDREDS[n // 100]]
    rest = n % 100
    if 10 <= rest <= 19:
        words.append(_TEENS[rest - 10])
    else:
        words.append(_TENS[rest // 10])
        words.append((_UNITS_F if feminine else _UNITS_M)[rest % 10])
    return [w for w in words if w]


def number_to_words(n: int) -> str:
    """Целое число по-русски: 1250 → «одна тысяча двести пятьдесят»."""
    if abs(n) > MAX_NUMBER:
        raise ValueError("слишком большое число")
    if n == 0:
        return "ноль"
    words = ["минус"] if n < 0 else []
    n = abs(n)
    parts = []
    scale = 0
    while n:
        n, triplet = divmod(n, 1000)
        if triplet:
            forms, feminine = _SCALES[scale]
            chunk = _triplet(triplet, feminine)
            if scale:
                chunk.append(_plural(triplet, forms))
            parts.append(chunk)
        scale += 1
    for chunk in reversed(parts):
        words.extend(chunk)
    return " ".join(words)


def number_words(text: str) -> str | None:
    """Выделенное число («1 250», «-15») → прописью; не число — None."""
    raw = text.strip()
    # Пробелы, неразрывные пробелы и апостроф — разделители разрядов.
    digits = raw.replace(" ", "").replace(" ", "").replace(" ", "").replace("'", "")
    negative = digits.startswith(("-", "−"))
    if negative:
        digits = digits[1:]
    if not digits.isdigit() or not digits.isascii():
        return None
    value = int(digits)
    if value > MAX_NUMBER:
        return None
    words = number_to_words(-value if negative else value)
    # Пробелы вокруг выделения сохраняем: выделили «123 » — получим «… три ».
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return lead + words + trail
