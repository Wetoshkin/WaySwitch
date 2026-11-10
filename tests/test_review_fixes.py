"""Находки ревью PR #1: потеря текста, ложные срабатывания правил, утечки текста.

Ложные срабатывания проверяются на настоящих словарях (wayswitch/data):
на игрушечном словаре они не воспроизводятся.
"""

from time import monotonic

import pytest

from tests.fakes import (
    RU,
    US,
    FakeBackend,
    RecordingTextTypist,
    RecordingTypist,
    SafeProbe,
    make_keymap,
)
from wayswitch import keycodes as kc
from wayswitch.a11y import FocusSnapshot
from wayswitch.actuator import (
    FAILED,
    Backspace,
    Plan,
    ReleaseModifiers,
    SwitchLayout,
    Type,
    execute,
)
from wayswitch.buffer import KeyPress
from wayswitch.config import Config
from wayswitch.controller import Controller
from wayswitch.detector import SENSITIVITY, Detector, LanguageModel


@pytest.fixture(scope="module")
def real():
    km = make_keymap()
    models = {lang: LanguageModel.load(lang) for lang in ("ru", "en")}
    return km, models


def make_ctrl(real, group=RU, config=None, typist=None, **kw):
    km, models = real
    det = Detector(km, models, set(), SENSITIVITY["normal"])
    backend = FakeBackend(current=group)
    typist = typist or RecordingTypist()
    corrected = []
    config = config or Config()
    # Слова здесь печатаются без пробела ради ручных жестов: ранний триггер
    # исправил бы их на четвёртой букве. У него свои тесты (test_punto_extras).
    config.general.early_switch = False
    kw.setdefault("probe", SafeProbe(kw.get("clock", monotonic)))
    ctrl = Controller(config, km, det, backend, typist,
                      on_corrected=lambda o, f, m: corrected.append((o, f, m)), **kw)
    return ctrl, backend, typist, corrected


def press(ctrl, code, shift=False, device=1):
    if shift:
        ctrl.on_key(kc.KEY_LEFTSHIFT, 1, device)
    ctrl.on_key(code, 1, device)
    ctrl.on_key(code, 0, device)
    if shift:
        ctrl.on_key(kc.KEY_LEFTSHIFT, 0, device)


def type_word(ctrl, km, text, group):
    for ch in text:
        code, shift = km.key_for(ch.lower(), group) or km.key_for(ch, group)
        press(ctrl, code, shift or ch.isupper())


# --- точка невозврата и ошибки исполнения ---------------------------------------------

def _plan():
    return Plan([ReleaseModifiers(), SwitchLayout(1), Backspace(3),
                 Type("abc", [(kc.KEY_A, False), (kc.KEY_B, False), (kc.KEY_C, False)])])


def test_key_after_first_backspace_stops_further_changes():
    class InterruptedTypist(RecordingTypist):
        interrupted = False

        def key(self, code, value):
            super().key(code, value)
            if code == kc.KEY_BACKSPACE and value == 0:
                self.interrupted = True

    typist, backend = InterruptedTypist(), FakeBackend(current=0)
    assert execute(_plan(), typist, backend, abort=lambda: typist.interrupted,
                   restore_group=0) == FAILED
    taps = [c for c, _ in typist.taps()]
    assert taps == [kc.KEY_BACKSPACE]
    assert backend.current() == 1 and backend.set_calls == [1]
    assert typist.stuck() == set()


def test_key_after_layout_change_preserves_mode_before_backspace():
    typist, backend = RecordingTypist(), FakeBackend(current=0)
    assert execute(_plan(), typist, backend, abort=lambda: bool(backend.set_calls),
                   restore_group=0) == FAILED
    assert backend.set_calls == [1] and backend.current() == 1
    assert typist.taps() == []


def test_text_typist_error_after_backspace_does_not_repeat_uncertain_write():
    class Flaky(RecordingTextTypist):
        failed = False

        def type_text(self, text):
            if not self.failed:
                self.failed = True
                raise RuntimeError("расширение перезапускалось")
            super().type_text(text)

    typist = Flaky()
    assert execute(_plan(), typist, FakeBackend(current=0)) == FAILED
    assert typist.failed and typist.calls == [("backspace", 3)]


def test_timeout_is_not_retried_to_avoid_double_typing():
    class Slow(RecordingTextTypist):
        def type_text(self, text):
            super().type_text(text)
            raise RuntimeError("Timeout was reached")

    typist = Slow()
    assert execute(_plan(), typist, FakeBackend(current=0)) == FAILED
    assert typist.calls == [("backspace", 3), ("type", "abc")]  # без повтора


def test_any_backend_exception_is_failed_not_raised():
    class Broken(FakeBackend):
        def set(self, index):
            raise RuntimeError("BackendError")

    assert execute(_plan(), RecordingTypist(), Broken(current=0)) == FAILED


# --- жест «Нажатие клавиши» -------------------------------------------------------------

def test_tap_key_ctrl_v_resets_buffer(real):
    """Ctrl+V при жесте на левом Ctrl не кладёт «v» в слово."""
    cfg = Config()
    cfg.gesture.manual = "tap_key"
    ctrl, *_ = make_ctrl(real, group=US, config=cfg)
    type_word(ctrl, real[0], "ghbdtn", US)
    ctrl.on_key(kc.KEY_LEFTCTRL, 1, 1)
    press(ctrl, kc.KEY_V)
    ctrl.on_key(kc.KEY_LEFTCTRL, 0, 1)
    assert ctrl.buffer.is_empty()


# --- ложные срабатывания правил на настоящих словарях -------------------------------

@pytest.mark.parametrize("word", [
    "раскладка", "вкладка", "настройка", "сборка", "закладка", "приставка", "мойка", "рамка",
    "нетто", "брутто", "колбы", "горбы", "вербы", "столбы", "строже", "бородато", "богато",
    "дую", "сую", "стаж", "морж", "метраж", "сталью", "площадью", "печалью", "контратаки",
    "мегаФон", "сберБанк", "СбербанкОнлайн",
])
def test_rules_do_not_touch_normal_russian(real, word):
    ctrl, _b, _t, corrected = make_ctrl(real, group=RU)
    type_word(ctrl, real[0], word + " ", RU)
    assert corrected == [], corrected


@pytest.mark.parametrize("word", ["build/", "docs/", "home/", "useState", "getName", "forEach",
                                  "parseInt", "iPhone", "macOS"])
def test_rules_do_not_touch_code_and_paths(real, word):
    ctrl, _b, _t, corrected = make_ctrl(real, group=US)
    type_word(ctrl, real[0], word + " ", US)
    assert corrected == [], corrected


@pytest.mark.parametrize("word,expected", [
    ("чтото", "что-то"), ("кембы", "кем бы"), ("хорошобы", "хорошо бы"),
    ("давайка", "давай-ка"), ("всётаки", "всё-таки"), ("знаешьли", "знаешь ли"),
    ("сделалбы", "сделал бы"), ("когданибудь", "когда-нибудь"), ("тыже", "ты же"),
    ("пирогб", "пирог,"), ("сонатуБаха", "сонату Баха"),
])
def test_rules_still_fix_real_cases(real, word, expected):
    ctrl, _b, _t, corrected = make_ctrl(real, group=RU)
    type_word(ctrl, real[0], word + " ", RU)
    assert corrected and corrected[0][1] == expected


# --- две заглавные -------------------------------------------------------------------

@pytest.mark.parametrize("word,group,expected", [
    ("ПРивет", RU, "Привет"), ("HEllo", US, "Hello"),
    ("ООО", RU, None), ("США", RU, None), ("ПРИВЕТ", RU, None), ("iPhone", US, None),
])
def test_two_capitals(real, word, group, expected):
    ctrl, _b, _t, corrected = make_ctrl(real, group=group)
    type_word(ctrl, real[0], word + " ", group)
    got = corrected[0][1] if corrected else None
    assert got == expected


def tap_shift(ctrl, times=1):
    for _ in range(times):
        ctrl.on_key(kc.KEY_RIGHTSHIFT, 1, 1)
        ctrl.on_key(kc.KEY_RIGHTSHIFT, 0, 1)


def test_third_shift_after_same_group_undo_converts_phrase(real):
    """Второй тап отменил правку в той же раскладке и группу не сменил.
    Третий тап должен перевести всю фразу в противоположную группу, а не
    перепечатать её в прежней."""
    ctrl, backend, _typist, corrected = make_ctrl(real, group=RU)
    type_word(ctrl, real[0], "ПРивет ", RU)
    assert corrected[0] == ("ПРивет", "Привет", False)
    tap_shift(ctrl, 2)  # откат правки «две заглавные»; группа осталась RU
    assert backend.current() == RU
    tap_shift(ctrl, 1)  # третий тап: фраза должна уйти в US
    assert backend.current() == US
    assert real[0].decode(ctrl.buffer.phrase(), US) == "GHbdtn "
    assert corrected[-1] == ("ПРивет ", "GHbdtn", True)


@pytest.mark.parametrize("typed_text,expected", [
    ("{Kt,", "Хлеб"),
    ("K>,jdm", "Любовь"),
    (":Bpym", "Жизнь"),
    ("<>hj", "Бюро"),
    (">Hbq", "Юрий"),
])
def test_two_capitals_in_opposite_layout(real, typed_text, expected):
    ctrl, _b, _t, corrected = make_ctrl(real, group=US)
    type_word(ctrl, real[0], typed_text + " ", US)
    assert corrected == [(typed_text, expected, False)]


def test_two_capitals_in_opposite_layout_obeys_setting(real):
    cfg = Config()
    cfg.general.two_capitals_fix = False
    ctrl, _b, _t, corrected = make_ctrl(real, group=US, config=cfg)
    type_word(ctrl, real[0], "{Kt, ", US)
    assert corrected == []
