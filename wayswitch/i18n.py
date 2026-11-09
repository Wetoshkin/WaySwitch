"""Выбор языка интерфейса и чтение каталога gettext.

Каталог ``wayswitch.mo`` лежит внутри пакета (``wayswitch/locale``) и попадает
в wheel, AppImage и Flatpak вместе с остальным кодом. Для KI18n тот же файл
устанавливается в стандартный ``share/locale``.

Выбор языка — по переменным окружения в порядке gettext: LANGUAGE, LC_ALL,
LC_MESSAGES, LANG. Эффективная локаль C/POSIX (в том числе C.UTF-8) всегда
даёт английский: LANGUAGE в ней не должен включать русский каталог. Английский
— исходный язык, поэтому для него каталог не нужен.

Модуль чистый: только стандартная библиотека, без gi/evdev и без изменения
глобальной локали процесса. Язык вычисляется при каждом обращении, поэтому
тесты могут проверять оба языка в одном процессе без утечки состояния.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import cache
from gettext import GNUTranslations, NullTranslations
from pathlib import Path

DOMAIN = "wayswitch"
LOCALE_DIR = Path(__file__).resolve().parent / "locale"
SOURCE_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("ru",)

# Порядок категорий LC_MESSAGES по POSIX/gettext: LC_ALL перекрывает
# LC_MESSAGES, а та — LANG. Пустые значения считаются незаданными.
_LOCALE_VARIABLES = ("LC_ALL", "LC_MESSAGES", "LANG")


def _value(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    return value.strip() if isinstance(value, str) else ""


def _language_of(locale_name: str) -> str:
    """``ru_RU.UTF-8@euro`` → ``ru``; ``C.UTF-8`` → ``C``."""
    base = locale_name.split("@", 1)[0].split(".", 1)[0]
    if not base:
        return ""
    if base in ("C", "POSIX"):
        return base
    return base.split("_", 1)[0].lower()


def base_locale(environ: Mapping[str, str] | None = None) -> str:
    """Эффективная локаль LC_MESSAGES или пустая строка."""
    environ = os.environ if environ is None else environ
    for name in _LOCALE_VARIABLES:
        value = _value(environ, name)
        if value:
            return value
    return ""


def locale_candidates(environ: Mapping[str, str] | None = None) -> list[str]:
    """Локали для поиска каталога в порядке gettext.

    LANGUAGE идёт первой, затем эффективная LC_MESSAGES/LANG. Для C, POSIX и
    C.UTF-8 список пуст: исходный английский используется без каталога.
    """
    environ = os.environ if environ is None else environ
    base = base_locale(environ)
    if not base or _language_of(base) in ("C", "POSIX"):
        return []
    candidates = [item.strip() for item in _value(environ, "LANGUAGE").split(":")
                  if item.strip()]
    candidates.append(base)
    return candidates


def select_language(environ: Mapping[str, str] | None = None) -> str:
    """Язык интерфейса: ``ru`` или ``en``.

    Берётся первый поддерживаемый кандидат. Английский — исходный язык, но
    явно указанный ``en`` в LANGUAGE останавливает поиск: человек попросил
    английский, и русский хвост списка его не переопределяет.
    """
    for candidate in locale_candidates(environ):
        language = _language_of(candidate)
        if language == SOURCE_LANGUAGE or language in SUPPORTED_LANGUAGES:
            return language
    return SOURCE_LANGUAGE


def catalog_path(language: str) -> Path:
    return LOCALE_DIR / language / "LC_MESSAGES" / f"{DOMAIN}.mo"


@cache
def _load(language: str) -> NullTranslations:
    path = catalog_path(language)
    if language == SOURCE_LANGUAGE or not path.is_file():
        return NullTranslations()
    with path.open("rb") as stream:
        return GNUTranslations(stream)


def translation(environ: Mapping[str, str] | None = None) -> NullTranslations:
    """Каталог для текущего окружения; для английского — пустой."""
    return _load(select_language(environ))


def language(environ: Mapping[str, str] | None = None) -> str:
    return select_language(environ)


def gettext(message: str, environ: Mapping[str, str] | None = None) -> str:
    if not message:
        return message
    return translation(environ).gettext(message)


def ngettext(singular: str, plural: str, n: int,
             environ: Mapping[str, str] | None = None) -> str:
    return translation(environ).ngettext(singular, plural, n)


def init(environ: Mapping[str, str] | None = None) -> str:
    """Загрузить каталог до создания окон; вернуть выбранный язык."""
    selected = select_language(environ)
    _load(selected)
    return selected
