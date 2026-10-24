"""Решение «слово набрано не в той раскладке?» по двум гипотезам.

A — то, что сейчас на экране (декодировка в текущей группе), B — то же
нажатия в другой группе. Порядок проверок повторяет раздел 5.4 спецификации.
"""

from __future__ import annotations

import gzip
import math
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property
from importlib import resources
from pathlib import Path

from wayswitch import ngram
from wayswitch.keymap import CYRILLIC, LATIN, Keymap

LANG_OF_ALPHABET = {"ru": "ru", "latin": "en"}
LETTERS = {"ru": CYRILLIC, "en": LATIN}
INNER_MARKS = {"-", "'"}
URL_SCHEMES = ("http://", "https://", "ftp://")
TECH_PREFIXES = URL_SCHEMES + ("ssh://", "www.", "git@")
TECH_MARKERS = ("://", "@", "--", ".com", ".ru", ".org", ".net", ".io", ".dev")
# Признаки пути проверяются только внутри слова (между первой и последней
# буквой), а не по всему тексту: в ru точка — это KEY_SLASH, и «привет.»,
# набранное в US, выглядит как «ghbdtn/»; Ё — Shift+grave, и «Ёлка» — «~krf».
# Хвостовой «/» и ведущий «~» — не путь, а пунктуация/буква другой раскладки.
PATH_MARKERS = ("/", "\\", "~")
TECH_WORDS = {"http", "https", "ftp", "www", "ssh", "git", "sudo", "apt", "dnf"}

# Знаки, которые в чужой раскладке оказываются буквой: в русской запятая живёт
# на клавише «б», точка — на «ю». «пирогб» → «пирог,», «улю» → «ул.».
# «;» (клавиша «ж») не берём: «стаж», «морж», «метраж» стали бы «ста;».
PUNCT_FROM_OTHER = ",."
# Перед «ю»-точкой — только согласная: окончания «-ую», «-ью», «-аю» слишком
# часты («дую» → «ду.», «сталью» → «сталь.»), а сокращения с точкой
# («руб.», «ул.», «г.») кончаются согласной.
_NOT_BEFORE_DOT = frozenset("аеёиоуыэюяьъй")
# Сокращения, после которых точка ставится всегда.
ABBREVIATIONS = frozenset({"руб", "коп", "ул", "г", "гг", "др", "стр", "см", "тыс", "млн",
                           "млрд", "т", "пр", "проч", "д", "кв", "обл", "р", "ед", "шт",
                           "мин", "сек", "мес", "англ", "рус", "напр", "доп", "тел"})

# Частицы. По правилам «-то», «-либо», «-нибудь», «-ка», «-таки» пишутся через
# дефис, «бы», «же», «ли» — раздельно. Но «-ка», «-то», «бы», «же» совпадают с
# окончаниями обычных слов («раскладка», «нетто», «колбы», «строже»), а в
# словаре нет многих словоформ. Поэтому у каждой частицы — свой закрытый
# список основ, после которых она точно частица.
_PRONOUNS = ("что", "кто", "где", "куда", "откуда", "как", "когда", "какой", "какая",
             "какое", "какие", "каких", "каким", "какую", "чей", "чья", "чьё", "чьи",
             "почему", "зачем", "отчего", "сколько", "чего", "чему", "чем", "кого", "кому",
             "кем", "ком", "чём")
_PERSONAL = ("я", "ты", "он", "она", "оно", "мы", "вы", "они")
PARTICLE_STEMS = {
    "то": frozenset(_PRONOUNS),
    "либо": frozenset(_PRONOUNS),
    "нибудь": frozenset(_PRONOUNS),
    "ка": frozenset(("давай", "давайте", "дай", "дайте", "смотри", "смотрите", "ну",
                     "иди", "идите", "скажи", "скажите", "глянь", "погоди", "подожди",
                     "посмотри", "покажи", "попробуй", "принеси", "сходи", "налей",
                     "расскажи", "подумай", "постой", "пойди", "напиши", "позвони")),
    "таки": frozenset(("всё", "все", "так", "опять", "наконец", "довольно", "есть")),
    "же": frozenset(_PRONOUNS + _PERSONAL + (
        "тот", "та", "те", "так", "там", "тут", "туда", "сюда", "здесь", "сейчас", "тогда",
        "всё", "все", "это", "этот", "эта", "эти", "такой", "такая", "такое", "такие",
        "сразу", "опять", "ведь", "нет", "да", "его", "её", "их", "ему", "ей", "им",
        "меня", "тебя", "нас", "вас")),
}
# «бы» и «ли» — после местоимений и глаголов: прошедшее время на «-л(а/о/и)»,
# у «ли» ещё настоящее время. «столбы», «колбы», «вербы» не подходят: основа
# короткая или не глагольная.
_BY_WORDS = frozenset(_PRONOUNS + _PERSONAL + (
    "был", "была", "было", "были", "мог", "могла", "могли", "смог", "если", "хотя",
    "тогда", "лучше", "хорошо", "можно", "нужно", "надо"))
_BY_VERB_ENDINGS = ("ал", "ял", "ел", "ил", "ыл", "ул", "ла", "ло", "ли")
_LI_VERB_ENDINGS = ("ешь", "ишь", "ете", "ите", "ет", "ит", "ут", "ют", "ат", "ят", "ть",
                    "ся", "сь")
_LI_WORDS = frozenset(("то", "есть", "был", "была", "было", "были", "так", "верно",
                       "можно", "нужно", "надо", "много", "мало", "скоро", "давно"))
# Первые части составных названий со строчной: «мегаФон», «сберБанк».
BRAND_PREFIXES = frozenset((
    "мега", "сбер", "авто", "мото", "фото", "видео", "теле", "радио", "супер", "мини",
    "макси", "евро", "гига", "кило", "нано", "вело", "аэро", "агро", "био", "эко", "инфо",
    "медиа", "пром", "газ", "тех", "спец", "рос", "мос", "яндекс", "вкус"))


@dataclass(frozen=True)
class Sensitivity:
    min_length: int
    margin: float
    floor: float


SENSITIVITY = {
    "conservative": Sensitivity(4, 2.5, -5.5),
    "normal": Sensitivity(3, 1.5, -6.0),
    "aggressive": Sensitivity(3, 0.8, -7.0),
}


class LanguageModel:
    def __init__(self, lang: str, words: Iterable[str], top: Iterable[str], model: dict):
        self.lang = lang
        self.words = frozenset(words)
        self.top = frozenset(top)
        self.model = model

    @classmethod
    def from_words(cls, lang: str, rows: list[tuple[str, float]], alphabet: str,
                   top_n: int = 300) -> LanguageModel:
        ordered = sorted(rows, key=lambda r: -r[1])
        # Вес слова — log(1 + частота): частые важнее, но не подавляют остальные.
        weighted = [(w, math.log1p(c)) for w, c in ordered]
        return cls(lang, (w for w, _ in ordered), (w for w, _ in ordered[:top_n]),
                   ngram.train(weighted, alphabet))

    @classmethod
    def load(cls, lang: str, data_dir: Path | None = None, top_n: int = 300) -> LanguageModel:
        base = data_dir or Path(str(resources.files("wayswitch") / "data"))
        words = []
        with gzip.open(base / f"{lang}.words.gz", "rt", encoding="utf-8") as f:
            for line in f:
                words.append(line.split("\t", 1)[0])
        return cls(lang, words, words[:top_n], ngram.load(base / f"{lang}.ngrams.json.gz"))

    def is_word(self, word: str) -> bool:
        return word.lower() in self.words

    @cached_property
    def endings(self) -> frozenset[str]:
        """Обычные окончания языка: две последние буквы хотя бы трёх слов словаря.

        Порог отсекает случайные слова («кгб», «люб»), иначе «пирогб» и
        «сделаюб» не исправлялись бы.
        """
        from collections import Counter

        counts = Counter(w[-2:] for w in self.words if len(w) >= 3)
        return frozenset(e for e, n in counts.items() if n >= 3)

    @cached_property
    def _sorted_words(self) -> list[str]:
        """Слова в алфавитном порядке — для поиска по префиксу (bisect)."""
        return sorted(self.words)

    def has_prefix(self, prefix: str) -> bool:
        """Есть ли в словаре слово, начинающееся с prefix.

        Нужно для падежных форм: «сонату» в словаре нет, а «соната» есть.
        """
        prefix = prefix.lower()
        if not prefix:
            return False
        i = bisect_left(self._sorted_words, prefix)
        return i < len(self._sorted_words) and self._sorted_words[i].startswith(prefix)

    def looks_like_word(self, word: str) -> bool:
        """Слово или его форма: точное совпадение либо основа в словаре.

        Отбрасываем до двух последних букв — так ловятся падежи и склонения
        («сонату» → «сонат…» → «соната»), но не абракадабра.
        """
        if self.is_word(word):
            return True
        for cut in (1, 2):
            if len(word) - cut >= 3 and self.has_prefix(word[:-cut]):
                return True
        return False

    def score(self, text: str) -> float:
        return ngram.score(self.model, text)


@dataclass
class Hypothesis:
    text: str
    core: str
    tail: str
    lang: str | None
    valid: bool
    in_dict: bool
    score: float | None = None


@dataclass
class Decision:
    action: str
    target_text: str
    confidence: float
    reason: str
    a: Hypothesis
    b: Hypothesis


def _split_tail(text: str, letters: set[str]) -> tuple[str, str]:
    """Отделить хвостовую пунктуацию: символы в конце, не являющиеся буквами языка."""
    i = len(text)
    while i > 0 and text[i - 1].lower() not in letters and text[i - 1] not in INNER_MARKS:
        i -= 1
    return text[:i], text[i:]


def _inner(core: str, letters: set[str]) -> str:
    """Часть core от первой буквы языка (хвост уже отрезан в _split_tail)."""
    i = 0
    while i < len(core) and core[i].lower() not in letters:
        i += 1
    return core[i:]


def _is_camel(core: str) -> bool:
    return any(c.isupper() for c in core[1:]) and any(c.islower() for c in core)


def _mixed_alphabets(core: str) -> bool:
    low = set(core.lower())
    return bool(low & CYRILLIC) and bool(low & LATIN)


class Detector:
    def __init__(self, keymap: Keymap, models: dict[str, LanguageModel],
                 exceptions: set[str] | None = None,
                 sensitivity: Sensitivity = SENSITIVITY["normal"]):
        self.keymap = keymap
        self.models = models
        self.exceptions = exceptions if exceptions is not None else set()
        self.sensitivity = sensitivity

    # --- вспомогательное ---------------------------------------------------

    def decode(self, keys, group: int) -> str:
        return self.keymap.decode(keys, group)

    def lang_of_group(self, group: int) -> str | None:
        return LANG_OF_ALPHABET.get(self.keymap.alphabet(group))

    def word_language(self, text: str) -> str | None:
        """Язык словарного слова или None, если слово никому не известно."""
        core = text.strip().lower()
        for lang, model in self.models.items():
            if core and model.is_word(core):
                return lang
        return None

    def hypothesis(self, keys, group: int) -> Hypothesis:
        text = self.decode(keys, group)
        lang = self.lang_of_group(group)
        if lang is None or lang not in self.models:
            return Hypothesis(text, text, "", None, False, False)
        letters = LETTERS[lang]
        core, tail = _split_tail(text, letters)
        low = core.lower()
        valid = bool(low) and all(c in letters or c in INNER_MARKS for c in low) \
            and low[0] not in INNER_MARKS and low[-1] not in INNER_MARKS
        in_dict = valid and self.models[lang].is_word(low)
        return Hypothesis(text, core, tail, lang, valid, in_dict)

    # --- решения -----------------------------------------------------------

    def early_url_target(self, keys, current_group: int, other_group: int) -> str | None:
        """Слово в другой раскладке — ровно схема URL: исправлять, не дожидаясь пробела."""
        if self.lang_of_group(other_group) != "en":
            return None
        b = self.decode(keys, other_group)
        return b if b.lower() in URL_SCHEMES else None

    def punct_fix(self, keys, current_group: int, other_group: int) -> str | None:
        """Знак препинания набран буквой из другой раскладки.

        «пирогб» → «пирог,». Чиним только когда без последней буквы получается
        известное слово, а с ней — неизвестное: иначе «гриб» стал бы «гри,».
        """
        if len(keys) < 3:
            return None
        lang = self.lang_of_group(current_group)
        # Только из русской раскладки: в английской хвостовой «/» (точка в
        # русской) — это почти всегда путь в терминале: «build/», «docs/».
        if lang != "ru" or lang not in self.models:
            return None
        last = keys[-1]
        if last.shift:  # со Shift в другой раскладке знак был бы другим
            return None
        mark = self.keymap.decode([last], other_group)
        if mark not in PUNCT_FROM_OTHER:
            return None
        letters = LETTERS[lang]
        model = self.models[lang]
        stem = self.keymap.decode(keys[:-1], current_group).lower()
        full = self.keymap.decode(keys, current_group).lower()
        if not stem or any(c not in letters for c in stem):
            return None
        if model.is_word(full):
            return None
        if mark == "." and stem in ABBREVIATIONS:
            return mark  # «рубю» → «руб.», «улю» → «ул.»
        if mark == "." and stem[-1] in _NOT_BEFORE_DOT:
            return None
        if not model.is_word(stem) or len(stem) < 3:
            return None  # «изб» → «из,» — двухбуквенная основа слишком случайна
        # Последние две буквы — частое окончание настоящих слов («ромб»,
        # «горб», «мелю», «парю»): тогда это слово, которого просто нет в
        # словаре, а не знак из другой раскладки. «пирогб» («гб») и
        # «сделаюб» («юб») так не кончается ни одно слово.
        if full[-2:] in model.endings:
            return None
        return mark

    def particles_fix(self, keys, current_group: int) -> str | None:
        """Слипшиеся частицы: «чтото» → «что-то», «кембы» → «кем бы».

        Срабатывает, только если слова целиком нет в словаре, а основа — из
        списка тех, после которых частица точно частица (PARTICLE_STEMS,
        _BY_*, _LI_*): иначе «раскладка» стала бы «расклад-ка».
        """
        lang = self.lang_of_group(current_group)
        if lang != "ru" or lang not in self.models:
            return None
        letters = LETTERS[lang]
        model = self.models[lang]
        text = self.decode(keys, current_group).lower()
        if len(text) < 4 or any(c not in letters for c in text) or model.is_word(text):
            return None
        for particle, stems in PARTICLE_STEMS.items():
            stem = text[: -len(particle)]
            if text.endswith(particle) and stem in stems:
                sep = " " if particle == "же" else "-"
                return f"{stem}{sep}{particle}"
        if text.endswith("бы"):
            stem = text[:-2]
            if stem in _BY_WORDS or (len(stem) >= 5 and stem.endswith(_BY_VERB_ENDINGS)
                                     and model.is_word(stem)):
                return f"{stem} бы"
        if text.endswith("ли"):
            stem = text[:-2]
            if stem in _LI_WORDS or (len(stem) >= 4 and stem.endswith(_LI_VERB_ENDINGS)
                                     and model.is_word(stem)):
                return f"{stem} ли"
        return None

    def two_capitals_fix(self, keys, current_group: int) -> str | None:
        """Две заглавные в начале: «ПРивет» → «Привет», «HEllo» → «Hello».

        Shift отпустили на букву позже. Чиним, только если дальше все буквы
        строчные (не аббревиатура «ООО», не «iPhone») и слово с одной
        заглавной есть в словаре.
        """
        lang = self.lang_of_group(current_group)
        if lang is None or lang not in self.models:
            return None
        text = self.decode(keys, current_group)
        letters = LETTERS[lang]
        if len(text) < 3 or any(c.lower() not in letters for c in text):
            return None
        if not (text[0].isupper() and text[1].isupper() and text[2:].islower()):
            return None
        if len(text) < 4:
            return None  # «IDs», «PCs», «TVs» — сокращение во множественном числе
        fixed = text[0] + text[1:].lower()
        if not self.models[lang].is_word(fixed.lower()):
            return None
        return fixed

    def split_words_fix(self, keys, current_group: int) -> str | None:
        """Слипшиеся слова: «сонатуБаха» → «сонату Баха».

        Признак — строчная буква, за которой сразу идёт заглавная. Правило
        срабатывает только если обе половины есть в словаре, а целое слово —
        нет, и только когда слово начинается со строчной: иначе «WaySwitch»
        превратился бы в «Way Switch».
        """
        lang = self.lang_of_group(current_group)
        # Только русский: в английском «useState», «getName», «forEach» — это
        # camelCase в коде, а не потерянный пробел.
        if lang != "ru" or lang not in self.models:
            return None
        letters = LETTERS[lang]
        model = self.models[lang]
        text = self.decode(keys, current_group)
        if len(text) < 6 or not text[0].islower():
            return None
        # LETTERS хранит строчные буквы, а регистр здесь как раз важен.
        if any(c.lower() not in letters for c in text):
            return None
        if model.is_word(text.lower()):
            return None
        # Ровно одна граница «строчная → заглавная».
        cuts = [i for i in range(1, len(text) - 1)
                if text[i - 1].islower() and text[i].isupper()]
        if len(cuts) != 1:
            return None
        cut = cuts[0]
        left, right = text[:cut], text[cut:]
        if len(left) < 3 or len(right) < 3:
            return None
        if left.lower() in BRAND_PREFIXES:
            return None  # «мегаФон», «сберБанк» — названия, а не потерянный пробел
        if not model.looks_like_word(left.lower()) or not model.looks_like_word(right.lower()):
            return None
        # Хотя бы одна половина — словарное слово, а не только «похожа на слово».
        if not (model.is_word(left.lower()) or model.is_word(right.lower())):
            return None
        return f"{left} {right}"

    def decide(self, keys, current_group: int, other_group: int,
               context: tuple[str | None, str | None] = (None, None)) -> Decision:
        a = self.hypothesis(keys, current_group)
        b = self.hypothesis(keys, other_group)
        sens = self.sensitivity

        def keep(reason: str, conf: float = 0.0) -> Decision:
            return Decision("keep", a.text, conf, reason, a, b)

        def fix(reason: str, conf: float) -> Decision:
            return Decision("fix", b.core + b.tail, conf, reason, a, b)

        if a.lang is None or b.lang is None:
            return keep("unknown-alphabet")

        # 2. Технический токен в B: URL, набранный не в той раскладке.
        bl = b.text.lower()
        if (bl.startswith(TECH_PREFIXES) or "://" in bl) and not a.in_dict \
                and b.lang == "en":
            return Decision("fix", b.text, 1.0, "url", a, b)

        # 3. Стоп-правила по тому, что на экране.
        if any(c.isdigit() for c in a.text):
            return keep("digits")
        if _mixed_alphabets(a.core):
            return keep("mixed")
        if _is_camel(a.core):
            return keep("camel")
        al = a.text.lower()
        inner = _inner(a.core, LETTERS[a.lang])
        if any(m in al for m in TECH_MARKERS) or any(m in inner for m in PATH_MARKERS) \
                or al.startswith(TECH_PREFIXES) or a.core.lower() in TECH_WORDS:
            return keep("tech")
        if 2 <= len(a.core) < 5 and a.core.isupper():
            return keep("abbrev")
        if a.core.lower() in self.exceptions:
            return keep("exception")

        # 4. Валидность.
        if not a.valid and not b.valid:
            return keep("invalid")

        # 5. Словарь.
        if a.valid and a.in_dict:
            return keep("dict-a", 1.0)
        short = len(b.core) < sens.min_length
        if b.valid and b.in_dict and not short:
            return fix("dict-b", 1.0)

        # 8. Короткие слова — только частотные и только в контексте своего языка.
        if short:
            if b.valid and b.core.lower() in self.models[b.lang].top and not a.in_dict \
                    and context[-1] in (None, b.lang):
                return fix("dict-short", 0.8)
            return keep("short")

        # 6–7. N-граммы.
        if not b.valid:
            return keep("b-invalid")
        if len(b.core) <= 3:
            # Три буквы триграммам не по силам: «cfg», «wtf», «xkb» выглядели
            # бы русскими словами. Короткое слово — только по словарю (выше).
            return keep("short-ngram")
        b.score = self.models[b.lang].score(b.core)
        a.score = self.models[a.lang].score(a.core) if a.valid else None
        margin = sens.margin * (2.0 if context == (a.lang, a.lang) else 1.0)
        if b.score < sens.floor:
            return keep(f"ngram-floor {b.score:.2f}")
        if a.score is None:
            return fix(f"ngram-only {b.score:.2f}", 0.7)
        delta = b.score - a.score
        if delta >= margin:
            return fix(f"ngram Δ={delta:.2f}", min(1.0, delta / (2 * margin)))
        return keep(f"ngram Δ={delta:.2f} < {margin:.2f}")
