"""Нечаянный Caps Lock, ранний триггер по началу слова, действия над выделенным."""

import pytest

from tests.fakes import RU, US, FakeBackend, RecordingTypist, keys_for_text, make_keymap
from wayswitch import config as cfgmod
from wayswitch import keycodes as kc
from wayswitch.buffer import KeyPress
from wayswitch.config import Config
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel


@pytest.fixture(scope="module")
def real():
    km = make_keymap()
    return km, {lang: LanguageModel.load(lang) for lang in ("ru", "en")}


class SelBackend(FakeBackend):
    selection = ""

    def get_selection(self):
        return self.selection


def make(real, group, config=None, selection=""):
    km, models = real
    backend = SelBackend(current=group)
    backend.selection = selection
    typist, corrected = RecordingTypist(), []
    ctrl = Controller(config or Config(), km, Detector(km, models), backend, typist,
                      on_corrected=lambda o, f, m: corrected.append((o, f, m)))
    return ctrl, backend, typist, corrected


def press(ctrl, code, shift=False):
    if shift:
        ctrl.on_key(kc.KEY_LEFTSHIFT, 1, 1)
    ctrl.on_key(code, 1, 1)
    ctrl.on_key(code, 0, 1)
    if shift:
        ctrl.on_key(kc.KEY_LEFTSHIFT, 0, 1)


def type_caps_word(ctrl, km, word, group):
    """Слово с включённым Caps Lock: первая буква с Shift (выходит строчной)."""
    for i, key in enumerate(keys_for_text(km, word.lower(), group)):
        press(ctrl, key.code, shift=(i == 0))
    press(ctrl, kc.KEY_SPACE)


def typed(km, typist, group):
    return km.decode([KeyPress(c, s) for c, s in typist.taps()
                      if c not in (kc.KEY_BACKSPACE, kc.KEY_CAPSLOCK)], group)


# --- нечаянный Caps Lock ------------------------------------------------------

def test_caps_lock_word_fixed_and_caps_turned_off(real):
    km = real[0]
    ctrl, _b, typist, corrected = make(real, RU)
    ctrl.set_caps(True)
    type_caps_word(ctrl, km, "привет", RU)
    assert corrected == [("пРИВЕТ", "Привет", False)]
    codes = [c for c, _ in typist.taps()]
    assert codes.count(kc.KEY_CAPSLOCK) == 1  # Caps Lock выключен одним нажатием
    assert codes.index(kc.KEY_CAPSLOCK) < codes.index(kc.KEY_BACKSPACE)
    assert typed(km, typist, RU) == "Привет "
    assert not ctrl.buffer.caps


def test_caps_lock_word_in_wrong_layout(real):
    km = real[0]
    ctrl, backend, typist, corrected = make(real, US)
    ctrl.set_caps(True)
    type_caps_word(ctrl, km, "ghbdtn", US)  # «gHBDTN»
    assert corrected[-1][1] == "Привет"
    assert backend.current() == RU


def test_caps_lock_shouting_is_left_alone(real):
    km = real[0]
    ctrl, _b, typist, corrected = make(real, RU)
    ctrl.set_caps(True)
    for key in keys_for_text(km, "привет", RU):  # «ПРИВЕТ» без Shift — нарочно
        press(ctrl, key.code)
    press(ctrl, kc.KEY_SPACE)
    assert corrected == []


def test_caps_fix_can_be_turned_off(real):
    km = real[0]
    cfg = Config()
    cfg.general.caps_fix = False
    ctrl, _b, _t, corrected = make(real, RU, cfg)
    ctrl.set_caps(True)
    type_caps_word(ctrl, km, "привет", RU)
    assert all(f != "Привет" for _o, f, _m in corrected)


def test_caps_toggle_rolled_back_when_aborted():
    from wayswitch.actuator import ABORTED, execute, plan_fix

    km = make_keymap()
    typist = RecordingTypist()
    plan = plan_fix(3, "Мир", RU, km, caps_on=True, switch=False, caps_off=True)
    result = execute(plan, typist, FakeBackend(current=RU), abort=lambda: True)
    assert result == ABORTED
    # Прервали до первого Backspace — Caps Lock не трогали (или вернули).
    assert [c for c, _ in typist.taps()].count(kc.KEY_CAPSLOCK) in (0, 2)


# --- ранний триггер ----------------------------------------------------------

def test_early_switch_on_fourth_letter(real):
    km = real[0]
    ctrl, backend, typist, corrected = make(real, US)
    for key in keys_for_text(km, "ghbd", US):
        press(ctrl, key.code)
    assert corrected == [("ghbd", "прив", False)]
    assert backend.current() == RU
    # Дальше человек допечатывает уже по-русски — слово не трогаем.
    for key in keys_for_text(km, "ет ", RU):
        press(ctrl, key.code)
    assert len(corrected) == 1
    assert typed(km, typist, RU).endswith("прив")


def test_early_switch_respects_setting_and_careful_mode(real):
    km = real[0]
    for tweak in ({"early_switch": False}, {"sensitivity": "careful"}):
        cfg = Config()
        for k, v in tweak.items():
            setattr(cfg.general, k, v)
        ctrl, _b, _t, corrected = make(real, US, cfg)
        for key in keys_for_text(km, "ghbd", US):
            press(ctrl, key.code)
        assert corrected == [], tweak


def test_early_switch_leaves_real_words_alone(real):
    km = real[0]
    ctrl, _b, _t, corrected = make(real, US)
    for word in ("hello ", "world ", "github ", "kubectl "):
        for key in keys_for_text(km, word, US):
            press(ctrl, key.code)
    assert corrected == []


# --- действия над выделенным -------------------------------------------------

def test_translit_selection(real):
    km = real[0]
    ctrl, backend, typist, corrected = make(real, RU, selection="Привет, мир")
    assert ctrl.transform_selection("translit")
    assert backend.current() == US
    assert typed(km, typist, US) == "Privet, mir"
    assert corrected[-1] == ("Привет, мир", "Privet, mir", True)


def test_number_words_selection(real):
    km = real[0]
    ctrl, backend, typist, _c = make(real, US, selection="1 250")
    assert ctrl.transform_selection("numbers")
    assert backend.current() == RU
    assert typed(km, typist, RU) == "одна тысяча двести пятьдесят"


def test_number_words_rejects_non_number(real):
    ctrl, *_ = make(real, RU, selection="двенадцать")
    assert not ctrl.transform_selection("numbers")


def test_own_translit_key_triggers_action(real):
    km = real[0]
    cfg = Config()
    cfg.gesture.translit_key = "f8"
    calls = []
    ctrl, *_ = make(real, RU, cfg, selection="Мир")
    ctrl.schedule = lambda fn: fn()
    ctrl.transform_selection = lambda action, text=None: calls.append(action)
    press(ctrl, kc.KEY_F8)
    assert calls == ["translit"]
    assert km  # раскладка не нужна, но фикстура общая


@pytest.mark.parametrize("gesture", [
    {"selection_key": "f8", "translit_key": "f8"},
    {"translit_key": "f9", "numbers_key": "f9"},
    {"manual": "double_shift", "numbers_key": "leftshift"},
    {"translit_key": "f7", "pause_hotkey": "f7"},
])
def test_selection_keys_conflicts(gesture):
    cfg = cfgmod.Config()
    for key, value in gesture.items():
        setattr(cfg.gesture, key, value)
    with pytest.raises(cfgmod.ConfigError):
        cfgmod._validate(cfg)
