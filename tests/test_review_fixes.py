"""Находки ревью PR #1: потеря текста, ложные срабатывания правил, утечки текста.

Ложные срабатывания проверяются на настоящих словарях (wayswitch/data):
на игрушечном словаре они не воспроизводятся.
"""

import pytest

from tests.fakes import RU, US, FakeBackend, RecordingTextTypist, RecordingTypist, make_keymap
from wayswitch import keycodes as kc
from wayswitch.a11y import FocusSnapshot
from wayswitch.actuator import (
    ABORTED,
    DONE,
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
    ctrl = Controller(config or Config(), km, det, backend, typist,
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


def test_abort_after_first_backspace_still_types_word():
    """Прерывание посреди Backspace-ов не оставляет стёртое слово без нового."""
    typist, backend = RecordingTypist(), FakeBackend(current=0)
    calls = {"n": 0}

    def abort():
        calls["n"] += 1
        return calls["n"] > 4  # «нажали клавишу» после первого Backspace

    assert execute(_plan(), typist, backend, abort=abort) == DONE
    taps = [c for c, _ in typist.taps()]
    assert taps.count(kc.KEY_BACKSPACE) == 3
    assert taps[-3:] == [kc.KEY_A, kc.KEY_B, kc.KEY_C]
    assert typist.stuck() == set()


def test_abort_before_backspace_restores_layout():
    typist, backend = RecordingTypist(), FakeBackend(current=0)
    calls = {"n": 0}

    def abort():
        calls["n"] += 1
        return calls["n"] == 3  # после SwitchLayout, до первого Backspace

    assert execute(_plan(), typist, backend, abort=abort, restore_group=0) == ABORTED
    assert backend.set_calls == [1, 0] and backend.current() == 0
    assert typist.taps() == []


def test_text_typist_error_after_backspace_retries_typing():
    class Flaky(RecordingTextTypist):
        failed = False

        def type_text(self, text):
            if not self.failed:
                self.failed = True
                raise RuntimeError("D-Bus timeout")
            super().type_text(text)

    typist = Flaky()
    assert execute(_plan(), typist, FakeBackend(current=0)) == DONE
    assert typist.calls == [("backspace", 3), ("type", "abc")]


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


# --- исключения и откат правил ---------------------------------------------------------

def test_exception_blocks_rules_too(real):
    ctrl, _b, _t, corrected = make_ctrl(real, group=RU)
    ctrl.detector.exceptions.add("чтото")
    type_word(ctrl, real[0], "чтото ", RU)
    assert corrected == []


def test_undo_of_split_fix_restores_original(real):
    """Откат «кем бы» возвращает «кембы» целиком, а не переворачивает «бы»."""
    ctrl, _backend, typist, corrected = make_ctrl(real, group=RU)
    km = real[0]
    type_word(ctrl, km, "кембы ", RU)
    assert corrected[-1][1] == "кем бы"
    before = len(typist.taps())
    assert ctrl.manual_word()
    assert corrected[-1] == ("кем бы", "кембы", True)
    after = typist.taps()[before:]
    new = [KeyPress(c, s) for c, s in after if c != kc.KEY_BACKSPACE]
    assert km.decode(new, RU) == "кембы "
    assert sum(1 for c, _ in after if c == kc.KEY_BACKSPACE) == len("кем бы ")
    assert "кембы" in ctrl.detector.exceptions


# --- утечки текста ---------------------------------------------------------------------

class PasswordProbe:
    def snapshot(self):
        return FocusSnapshot("app", True, 100.0)

    def poke(self):
        pass


def test_manual_fix_in_password_field_does_not_report_text(real):
    ctrl, _b, typist, corrected = make_ctrl(real, group=US, probe=PasswordProbe(),
                                            clock=lambda: 100.0)
    type_word(ctrl, real[0], "ghbdtn", US)
    assert ctrl.manual_word()
    assert typist.taps()  # исправление выполнено…
    assert corrected == []  # …но текст наружу не ушёл


def test_status_reports_layout_alphabet(real):
    ctrl, *_ = make_ctrl(real, group=RU)
    assert ctrl.status()["layout"] == "ru"
