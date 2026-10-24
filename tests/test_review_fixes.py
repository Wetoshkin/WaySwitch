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
    config = config or Config()
    # Слова здесь печатаются без пробела ради ручных жестов: ранний триггер
    # исправил бы их на четвёртой букве. У него свои тесты (test_punto_extras).
    config.general.early_switch = False
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
                raise RuntimeError("расширение перезапускалось")
            super().type_text(text)

    typist = Flaky()
    assert execute(_plan(), typist, FakeBackend(current=0)) == DONE
    assert typist.calls == [("backspace", 3), ("type", "abc")]


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


# --- исключения и откат правил ---------------------------------------------------------

def test_exception_blocks_rules_too(real):
    ctrl, _b, _t, corrected = make_ctrl(real, group=RU)
    ctrl.detector.exceptions.add("чтото")
    type_word(ctrl, real[0], "чтото ", RU)
    assert corrected == []


def test_undo_of_split_fix_restores_original(real):
    """Откат «кем бы» возвращает «кембы» целиком, а не переворачивает «бы»."""
    cfg = Config()
    cfg.general.learn_after_undos = 1  # запомнить с первой отмены
    ctrl, _backend, typist, corrected = make_ctrl(real, group=RU, config=cfg)
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


# --- второе ревью: правила на настоящих словарях ---------------------------------------

@pytest.mark.parametrize("word", [
    "ямб", "изб", "ромб", "ухаб", "горб", "судеб", "мелю", "мылю", "жарю", "парю", "пилю",
    "делю", "колю", "мирю", "роню",
])
def test_punct_rule_leaves_real_word_endings(real, word):
    ctrl, _b, _t, corrected = make_ctrl(real, group=RU)
    type_word(ctrl, real[0], word + " ", RU)
    assert corrected == [], corrected


@pytest.mark.parametrize("word,expected", [("пирогб", "пирог,"), ("домю", "дом."),
                                           ("рубю", "руб."), ("улю", "ул.")])
def test_punct_rule_still_fixes(real, word, expected):
    ctrl, _b, _t, corrected = make_ctrl(real, group=RU)
    type_word(ctrl, real[0], word + " ", RU)
    assert corrected and corrected[0][1] == expected


@pytest.mark.parametrize("word", ["wtf", "brb", "cfg", "dst", "ctx", "ptr", "xkb"])
def test_three_letter_latin_not_flipped_by_trigrams(real, word):
    ctrl, _b, _t, corrected = make_ctrl(real, group=US)
    type_word(ctrl, real[0], word + " ", US)
    assert corrected == [], corrected


@pytest.mark.parametrize("word", ["IDs", "DJs", "CDs", "TVs", "PCs"])
def test_two_capitals_skips_plural_abbreviations(real, word):
    ctrl, _b, _t, corrected = make_ctrl(real, group=US)
    type_word(ctrl, real[0], word + " ", US)
    assert corrected == [], corrected


# --- второе ревью: ввод, жесты, конфликты ---------------------------------------------

def test_touchpad_tap_counts_as_click():
    assert kc.is_pointer_button(kc.BTN_TOUCH)
    from wayswitch.keys import classify
    assert classify("Touchpad", {kc.BTN_TOUCH}) == "pointer"


def test_scroll_breaks_tap_gesture(real):
    cfg = Config()
    cfg.gesture.manual = "tap_key"
    ctrl, _b, typist, corrected = make_ctrl(real, group=US, config=cfg)
    type_word(ctrl, real[0], "ghbdtn", US)
    ctrl.on_key(kc.KEY_LEFTCTRL, 1, 1)
    ctrl.on_scroll()  # Ctrl+колесо — масштаб, а не жест
    ctrl.on_key(kc.KEY_LEFTCTRL, 0, 1)
    assert corrected == []


def test_tap_key_respects_phrase_timeout(real):
    now = {"t": 100.0}
    cfg = Config()
    cfg.gesture.manual = "tap_key"
    ctrl, _b, _t, corrected = make_ctrl(real, group=US, config=cfg, clock=lambda: now["t"])
    type_word(ctrl, real[0], "ghbdtn", US)
    now["t"] += 3600
    ctrl.on_key(kc.KEY_LEFTCTRL, 1, 1)
    ctrl.on_key(kc.KEY_LEFTCTRL, 0, 1)
    assert corrected == []  # через час старое слово не переворачиваем


def test_own_escape_key_resets_buffer(real):
    cfg = Config()
    cfg.gesture.manual = "tap_key"
    cfg.gesture.tap_key = "esc"
    ctrl, *_ = make_ctrl(real, group=US, config=cfg)
    type_word(ctrl, real[0], "ghbdtn", US)
    ctrl.on_key(kc.KEY_ESC, 1, 1)
    ctrl.on_key(kc.KEY_ESC, 0, 1)
    assert ctrl.buffer.is_empty()


@pytest.mark.parametrize("gesture", [
    {"manual": "double_shift", "selection_key": "leftshift"},
    {"manual": "pause_key", "selection_key": "pause"},
    {"manual": "tap_key", "tap_key": "f8", "selection_key": "f8"},
    {"manual": "double_shift", "selection_key": "f9", "pause_hotkey": "f9"},
    {"manual": "tap_key", "tap_key": "f9", "pause_hotkey": "f9"},
])
def test_key_conflicts_rejected(gesture):
    from wayswitch import config as cfgmod

    cfg = cfgmod.Config()
    for key, value in gesture.items():
        setattr(cfg.gesture, key, value)
    with pytest.raises(cfgmod.ConfigError):
        cfgmod._validate(cfg)


def test_retyped_word_is_not_undone(real):
    """Стёрли исправленное «привет» и набрали его заново — это не откат."""
    ctrl, _b, _t, corrected = make_ctrl(real, group=US)
    km = real[0]
    type_word(ctrl, km, "ghbdtn ", US)
    assert corrected[-1][1] == "привет"
    type_word(ctrl, km, "мир привет ", RU)
    ctrl.manual_word()  # это ручной переворот нового слова, а не откат
    assert ctrl.stats["undo"] == 0
    assert "ghbdtn" not in ctrl.detector.exceptions


def test_multiline_selection_is_refused(real):
    class Sel(FakeBackend):
        def get_selection(self):
            return "ghbdtn\nvbh"

    km, models = real
    det = Detector(km, models, set(), SENSITIVITY["normal"])
    ctrl = Controller(Config(), km, det, Sel(current=US), RecordingTypist())
    assert not ctrl.fix_selection()
