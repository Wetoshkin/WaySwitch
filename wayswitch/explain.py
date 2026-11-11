"""Разбор решения детектора для одного слова без запуска службы.

`wayswitch explain` отвечает на вопрос «почему это слово исправлено или
оставлено», используя те же Detector и LanguageModel, что и демон. Слово
задаёт пользователь; раскладки — явные us/ru. Служба не опрашивается, фокус
не читается, клавиши не печатаются, конфиг и раскладка не меняются.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from functools import lru_cache

from wayswitch import config as cfgmod
from wayswitch.buffer import KeyPress
from wayswitch.detector import SENSITIVITY, Detector, LanguageModel
from wayswitch.keymap import LayoutSpec, XkbKeymap

# Порядок групп в собранной карте: 0 — us, 1 — ru. Он же задаёт --from en|ru.
SOURCE_LAYOUTS = {"en": "us", "ru": "ru"}
GROUP_OF_SOURCE = {"en": 0, "ru": 1}
LAYOUT_SPECS = [LayoutSpec("us"), LayoutSpec("ru")]
DEFAULT_FROM = "en"

LIMITATIONS = (
    "Только us/ru: варианты раскладок, AltGr, dead keys и XKB-опции не читаются.",
    "Контекст пустой: не учитываются предыдущее слово, приложение, поле, "
    "раскладка в системе и edit_guard.",
    "Вызывается только Detector.decide: punct/split/particles/caps, early-триггер "
    "и тройной Shift не проверяются.",
    "Служба не опрашивается, клавиши не отправляются, раскладка и конфиг не меняются.",
)


class ExplainError(RuntimeError):
    """Ошибка разбора: неверный ввод, конфиг, словари или libxkbcommon."""


@dataclass(frozen=True)
class Settings:
    sensitivity: str
    exceptions: frozenset[str]
    two_capitals_fix: bool


def load_settings() -> Settings:
    """Чувствительность и исключения из обычного config.toml (или умолчаний)."""
    try:
        config = cfgmod.load()
        exceptions = cfgmod.load_exceptions()
    except (cfgmod.ConfigError, OSError, UnicodeError) as exc:
        raise ExplainError(f"не удалось прочитать конфигурацию: {exc}") from exc
    return Settings(config.general.sensitivity, frozenset(exceptions),
                    config.general.two_capitals_fix)


def load_keymap() -> XkbKeymap:
    """Собрать явные us/ru через libxkbcommon; вариант и опции не угадываем."""
    if not XkbKeymap.available():
        raise ExplainError("libxkbcommon недоступен: нужен Linux с libxkbcommon0; "
                           "раскладки us/ru не собраны")
    try:
        return XkbKeymap.from_names(LAYOUT_SPECS, [])
    except (OSError, RuntimeError) as exc:
        raise ExplainError(f"не удалось собрать раскладки us/ru: {exc}") from exc


@lru_cache(maxsize=1)
def load_models() -> dict[str, LanguageModel]:
    """Обе поставленные модели; кэш нужен только для повторных вызовов в процессе."""
    try:
        return {lang: LanguageModel.load(lang) for lang in ("en", "ru")}
    except (OSError, ValueError, EOFError, KeyError) as exc:
        raise ExplainError(f"не удалось загрузить словари: {exc}") from exc


def _keys_for(word: str, keymap, group: int, layout: str) -> list[KeyPress]:
    keys = []
    missing = []
    for ch in word:
        pair = keymap.key_for(ch, group)
        if pair is None:
            missing.append(ch)
            continue
        keys.append(KeyPress(pair[0], shift=pair[1]))
    if missing:
        chars = "".join(dict.fromkeys(missing))
        raise ExplainError(f"в раскладке {layout} нельзя набрать: {chars!r}")
    return keys


def _hypothesis(h) -> dict:
    out = {
        "text": h.text,
        "core": h.core,
        "tail": h.tail,
        "language": h.lang,
        "valid": h.valid,
        "in_dictionary": h.in_dict,
    }
    # Ноль не подставляем: score есть только когда n-граммы действительно считали.
    if h.score is not None:
        out["ngram_score"] = h.score
    return out


def build_report(word: str, from_lang: str, *, settings: Settings, keymap,
                 models: dict[str, LanguageModel]) -> dict:
    if from_lang not in SOURCE_LAYOUTS:
        raise ExplainError(f"неизвестная исходная раскладка: {from_lang!r}; допустимо en, ru")
    if not word:
        raise ExplainError("слово не задано")
    if any(ch.isspace() for ch in word):
        raise ExplainError("нужно одно слово без пробелов")
    sensitivity = SENSITIVITY.get(settings.sensitivity)
    if sensitivity is None:
        raise ExplainError(f"неизвестная чувствительность: {settings.sensitivity!r}")

    source_group = GROUP_OF_SOURCE[from_lang]
    target_group = 1 - source_group
    source_layout = SOURCE_LAYOUTS[from_lang]
    target_lang = "ru" if from_lang == "en" else "en"
    target_layout = SOURCE_LAYOUTS[target_lang]
    expected = ("latin", "ru") if from_lang == "en" else ("ru", "latin")
    actual = (keymap.alphabet(source_group), keymap.alphabet(target_group))
    if actual != expected:
        raise ExplainError("раскладки собраны не в порядке us/ru: "
                           f"получено {actual[0]}/{actual[1]}, нужно {expected[0]}/{expected[1]}")

    keys = _keys_for(word, keymap, source_group, source_layout)
    decoded = keymap.decode(keys, source_group)
    if decoded != word:
        raise ExplainError(f"раскладка {source_layout} не позволяет однозначно набрать {word!r}")

    detector = Detector(keymap, models, set(settings.exceptions), sensitivity)
    decision = detector.decide(keys, source_group, target_group, (None, None),
                               two_capitals=settings.two_capitals_fix)
    return {
        "word": word,
        "from": from_lang,
        "to": target_lang,
        "default_from": DEFAULT_FROM,
        "layouts": {"source": source_layout, "target": target_layout},
        "context": [],
        "exception": decision.a.core.lower() in settings.exceptions,
        "settings": {
            "sensitivity": {
                "name": settings.sensitivity,
                "min_length": sensitivity.min_length,
                "margin": sensitivity.margin,
                "floor": sensitivity.floor,
            },
            "two_capitals_fix": settings.two_capitals_fix,
        },
        "a": _hypothesis(decision.a),
        "b": _hypothesis(decision.b),
        "decision": {
            "action": decision.action,
            "target": decision.target_text,
            "reason": decision.reason,
            "confidence": decision.confidence,
        },
        "limitations": list(LIMITATIONS),
    }


def _yes(value: bool) -> str:
    return "да" if value else "нет"


def _format_hypothesis(label: str, layout: str, data: dict) -> str:
    line = (f"{label} ({layout}): {data['text']!r}; core={data['core']!r}; "
            f"tail={data['tail']!r}; valid={_yes(data['valid'])}; "
            f"in_dict={_yes(data['in_dictionary'])}")
    if "ngram_score" in data:
        line += f"; ngram_score={data['ngram_score']:.2f}"
    return line


def format_report(report: dict) -> str:
    source = f"{report['from']} ({report['layouts']['source']})"
    target = f"{report['to']} ({report['layouts']['target']})"
    settings = report["settings"]
    sensitivity = settings["sensitivity"]
    lines = [
        f"Слово: {report['word']}",
        f"Раскладки: {source} → {target} "
        f"(по умолчанию --from {report['default_from']} = us)",
        f"Настройки: sensitivity={sensitivity['name']} "
        f"(min_length={sensitivity['min_length']}, margin={sensitivity['margin']:.2f}, "
        f"floor={sensitivity['floor']:.2f}), "
        f"two_capitals_fix={'вкл' if settings['two_capitals_fix'] else 'выкл'}, "
        f"исключение={_yes(report['exception'])}",
        "Контекст: пустой — реальный фокус приложения, поле и edit_guard не опрашиваются",
        _format_hypothesis("A", report["layouts"]["source"], report["a"]),
        _format_hypothesis("B", report["layouts"]["target"], report["b"]),
    ]
    scores = [f"{label.upper()}={report[key]['ngram_score']:.2f}"
              for label, key in (("a", "a"), ("b", "b"))
              if "ngram_score" in report[key]]
    if scores:
        suffix = "" if len(scores) == 2 else " (у другой гипотезы не вычислялась)"
        lines.append("Оценки n-грамм: " + ", ".join(scores) + suffix)
    else:
        lines.append("Оценки n-грамм: не вычислялись — решение принято раньше")
    decision = report["decision"]
    lines.append(f"Решение: {decision['action']} → {decision['target']!r} "
                 f"(reason={decision['reason']}, confidence={decision['confidence']:.2f})")
    lines.append("Ограничения:")
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines)


def run_explain(word: str, from_lang: str, sensitivity: str | None = None, *,
                json_output: bool = False) -> int:
    settings = load_settings()
    if sensitivity is not None:
        settings = replace(settings, sensitivity=sensitivity)
    keymap = load_keymap()
    report = build_report(word, from_lang, settings=settings, keymap=keymap,
                          models=load_models())
    if json_output:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(format_report(report))
    return 0
