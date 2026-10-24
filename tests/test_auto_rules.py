"""Правила, взятые из практики Punto Switcher: пунктуация, частицы, защита от правки.

Проверяем и детектор (чистые решения), и контроллер (что реально печатается).
"""

import pytest

from tests.fakes import RU, US, FakeBackend, RecordingTypist, keys_for_text, make_keymap
from wayswitch import keycodes as kc
from wayswitch.config import Config
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel

RU_WORDS = [("пирог", 900.0), ("гриб", 800.0), ("что", 1000.0), ("кем", 700.0),
            ("привет", 500.0), ("мир", 400.0), ("сонату", 300.0), ("баха", 300.0),
            ("исполнили", 200.0)]
EN_WORDS = [("hello", 900.0), ("world", 500.0)]


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def make_env(config: Config | None = None):
    km = make_keymap()
    det = Detector(km, {
        "ru": LanguageModel.from_words("ru", RU_WORDS, "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"),
        "en": LanguageModel.from_words("en", EN_WORDS, "abcdefghijklmnopqrstuvwxyz"),
    })
    backend, typist, clock = FakeBackend(current=RU), RecordingTypist(), Clock()
    ctrl = Controller(config or Config(), km, det, backend, typist, clock=clock)
    return dict(km=km, det=det, ctrl=ctrl, backend=backend, typist=typist, clock=clock)


def keys_with_shift(km, text, group):
    """Клавиши текста, где заглавные буквы нажаты с Shift.

    В тестовой таблице клавиш нет заглавных символов, поэтому верхний регистр
    ставим вручную — как это сделал бы человек.
    """
    from wayswitch.buffer import KeyPress

    keys = []
    for ch in text:
        base = keys_for_text(km, ch.lower(), group)[0]
        keys.append(KeyPress(base.code, shift=ch.isupper(), caps=False))
    return keys


def type_keys(env, keys):
    for key in keys:
        env["ctrl"].on_key(key.code, 1, 1)
        env["ctrl"].on_key(key.code, 0, 1)


def typed_chars(env) -> str:
    """Что напечатала печатающая машинка (без Backspace-ов), в группе бэкенда."""
    from wayswitch.buffer import KeyPress

    codes = [(code, shift) for code, shift in env["typist"].taps()
             if code != kc.KEY_BACKSPACE]
    return env["km"].decode([KeyPress(code, shift) for code, shift in codes],
                            env["backend"].current())


def backspaces(env) -> int:
    return sum(1 for code, _shift in env["typist"].taps() if code == kc.KEY_BACKSPACE)


# --- пунктуация из другой раскладки -----------------------------------------

def test_punct_fix_comma_typed_as_russian_letter():
    env = make_env()
    keys = keys_for_text(env["km"], "пирогб", RU)
    assert env["det"].punct_fix(keys, RU, US) == ","


def test_punct_fix_skips_real_word():
    """«гриб» — настоящее слово, запятой там нет."""
    env = make_env()
    keys = keys_for_text(env["km"], "гриб", RU)
    assert env["det"].punct_fix(keys, RU, US) is None


def test_punct_fix_skips_short_word():
    env = make_env()
    keys = keys_for_text(env["km"], "б", RU)
    assert env["det"].punct_fix(keys, RU, US) is None


def test_punct_fix_applied_without_layout_switch():
    """Меняется только последняя буква, раскладку не трогаем."""
    env = make_env()
    type_keys(env, keys_for_text(env["km"], "пирогб", RU))
    env["ctrl"].on_key(kc.KEY_SPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_SPACE, 0, 1)
    assert backspaces(env) == 7  # «пирогб» + пробел
    assert typed_chars(env) == "пирог, "
    assert env["backend"].set_calls == []


# --- частицы ----------------------------------------------------------------

def test_particles_hyphen():
    env = make_env()
    keys = keys_for_text(env["km"], "чтото", RU)
    assert env["det"].particles_fix(keys, RU) == "что-то"


def test_particles_space():
    env = make_env()
    keys = keys_for_text(env["km"], "кембы", RU)
    assert env["det"].particles_fix(keys, RU) == "кем бы"


def test_particles_skips_known_word():
    env = make_env()
    keys = keys_for_text(env["km"], "пирог", RU)
    assert env["det"].particles_fix(keys, RU) is None


def test_particles_skips_unknown_stem():
    env = make_env()
    keys = keys_for_text(env["km"], "абракадабрато", RU)
    assert env["det"].particles_fix(keys, RU) is None


def test_particles_applied_in_controller():
    env = make_env()
    type_keys(env, keys_for_text(env["km"], "чтото", RU))
    env["ctrl"].on_key(kc.KEY_SPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_SPACE, 0, 1)
    assert typed_chars(env) == "что-то "
    assert backspaces(env) == 6  # «чтото» + пробел
    assert env["backend"].set_calls == []


# --- слипшиеся слова --------------------------------------------------------

def test_split_words_on_case_boundary():
    env = make_env()
    keys = keys_with_shift(env["km"], "сонатуБаха", RU)
    assert env["det"].split_words_fix(keys, RU) == "сонату Баха"


def test_split_words_skips_camel_case_brand():
    """«WaySwitch» — не ошибка: слово начинается с заглавной."""
    env = make_env()
    keys = keys_for_text(env["km"], "WaySwitch", US)
    assert env["det"].split_words_fix(keys, US) is None


def test_split_words_skips_without_boundary():
    env = make_env()
    keys = keys_for_text(env["km"], "приветмир", RU)
    assert env["det"].split_words_fix(keys, RU) is None


def test_split_words_skips_known_word():
    env = make_env()
    keys = keys_for_text(env["km"], "привет", RU)
    assert env["det"].split_words_fix(keys, RU) is None


def test_split_words_requires_both_parts_in_dictionary():
    env = make_env()
    keys = keys_with_shift(env["km"], "абракадабруБаха", RU)
    assert env["det"].split_words_fix(keys, RU) is None


def test_split_words_applied_in_controller():
    env = make_env()
    # Shift нажимаем по-настоящему: буфер берёт регистр из зажатых клавиш,
    # а не из отметки в самой букве.
    type_keys(env, keys_for_text(env["km"], "сонату", RU))
    env["ctrl"].on_key(kc.KEY_LEFTSHIFT, 1, 1)
    type_keys(env, keys_for_text(env["km"], "б", RU))
    env["ctrl"].on_key(kc.KEY_LEFTSHIFT, 0, 1)
    type_keys(env, keys_for_text(env["km"], "аха", RU))
    env["ctrl"].on_key(kc.KEY_SPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_SPACE, 0, 1)
    assert typed_chars(env) == "сонату Баха "
    assert env["backend"].set_calls == []


# --- защита после правки текста ---------------------------------------------

def test_guard_skips_fix_after_backspace():
    """После Backspace автоисправление молчит: человек правит текст руками."""
    env = make_env()
    env["backend"].current_index = US  # на экране «ghbdtn», то есть «привет» в RU
    env["ctrl"].on_key(kc.KEY_BACKSPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_BACKSPACE, 0, 1)
    type_keys(env, keys_for_text(env["km"], "ghbdtn", US))
    env["ctrl"].on_key(kc.KEY_SPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_SPACE, 0, 1)
    assert env["typist"].taps() == []
    assert env["backend"].set_calls == []


def test_guard_expires_and_fix_works_again():
    env = make_env()
    env["backend"].current_index = US
    env["ctrl"].on_key(kc.KEY_BACKSPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_BACKSPACE, 0, 1)
    env["clock"].now += 2.0  # окно защиты прошло
    type_keys(env, keys_for_text(env["km"], "ghbdtn", US))
    env["ctrl"].on_key(kc.KEY_SPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_SPACE, 0, 1)
    assert backspaces(env) == 7
    assert env["backend"].set_calls == [RU]


def test_guard_can_be_disabled():
    config = Config()
    config.general.edit_guard_sec = 0.0
    env = make_env(config)
    env["backend"].current_index = US
    env["ctrl"].on_key(kc.KEY_BACKSPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_BACKSPACE, 0, 1)
    type_keys(env, keys_for_text(env["km"], "ghbdtn", US))
    env["ctrl"].on_key(kc.KEY_SPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_SPACE, 0, 1)
    assert backspaces(env) == 7


def test_guard_after_manual_layout_switch():
    """Сменил раскладку руками — секунду не лезем с автозаменой."""
    env = make_env()
    env["backend"].current_index = US
    env["ctrl"].on_layout_changed(US, external=True)
    type_keys(env, keys_for_text(env["km"], "ghbdtn", US))
    env["ctrl"].on_key(kc.KEY_SPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_SPACE, 0, 1)
    assert env["typist"].taps() == []


@pytest.mark.parametrize("name,expected", [("edit_guard_sec", 1.0),
                                           ("punct_fix", True),
                                           ("particles_fix", True)])
def test_new_defaults(name, expected):
    config = Config()
    assert getattr(config.general, name) == expected
