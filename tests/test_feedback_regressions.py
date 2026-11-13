"""Смешанный ввод и варианты XKB из обсуждения первого релиза."""

import pytest

from tests.fakes import RU, US, FakeBackend, RecordingTypist, keys_for_text, make_keymap
from tests.test_review_fixes import press, type_word
from wayswitch import keycodes as kc
from wayswitch.a11y import FocusSnapshot
from wayswitch.buffer import KeyPress
from wayswitch.config import Config
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel
from wayswitch.keymap import LayoutSpec, XkbKeymap


class EditorProbe:
    def snapshot(self):
        return FocusSnapshot("test-editor", False, 100.0)

    def poke(self):
        pass


@pytest.fixture(scope="module")
def models():
    return {lang: LanguageModel.load(lang) for lang in ("ru", "en")}


def make_controller(km, models, group):
    backend, typist = FakeBackend(current=group), RecordingTypist()
    config = Config()
    config.general.early_switch = False
    ctrl = Controller(config, km, Detector(km, models), backend, typist,
                      clock=lambda: 100.0, probe=EditorProbe())
    return ctrl, backend, typist


def test_mixed_russian_word_then_ubuntu(models):
    km = make_keymap()
    ctrl, backend, typist = make_controller(km, models, US)
    type_word(ctrl, km, "lbcnhb,enbd ", US)
    assert backend.current() == RU
    assert km.decode([KeyPress(c, s) for c, s in typist.taps()
                      if c != kc.KEY_BACKSPACE], RU) == "дистрибутив "
    typist.events.clear()
    backend.set(US)
    ctrl.on_layout_changed(US, external=True)
    type_word(ctrl, km, "Ubuntu ", US)
    assert typist.taps() == []
    assert backend.current() == US


def test_window_click_drops_previous_word(models):
    km = make_keymap()
    ctrl, backend, typist = make_controller(km, models, US)
    type_word(ctrl, km, "ghbdtn", US)
    ctrl.on_click()
    press(ctrl, kc.KEY_SPACE)
    assert typist.taps() == []
    assert backend.current() == US


def test_russian_word_then_english_colon_is_not_replaced_with_zhe(models):
    km = make_keymap()
    now = [100.0]
    backend, typist = FakeBackend(current=RU), RecordingTypist()
    config = Config()
    config.general.early_switch = False
    ctrl = Controller(config, km, Detector(km, models), backend, typist,
                      clock=lambda: now[0], probe=EditorProbe())
    type_word(ctrl, km, "уточнить", RU)
    backend.set(US)
    ctrl.on_layout_changed(US, external=True)
    now[0] += 2.0  # окно edit_guard_sec уже истекло
    type_word(ctrl, km, ": ", US)
    assert typist.taps() == []
    assert backend.current() == US


@pytest.mark.parametrize("word", ["enjxybnm", "ghbkj;tybz", "ltqcndbq", "ytckj;yjt"])
@pytest.mark.parametrize("uppercase", [False, True])
@pytest.mark.parametrize("tail", [":", ":/"])
def test_wrong_layout_word_with_colon_is_not_retyped(models, word, uppercase, tail):
    km = make_keymap()
    ctrl, backend, typist = make_controller(km, models, US)
    type_word(ctrl, km, (word.upper() if uppercase else word) + tail + " ", US)
    assert typist.taps() == []
    assert backend.current() == US


@pytest.mark.parametrize("source,target", [
    ("GHBDTN", "ПРИВЕТ"), ("GHBDTN/", "ПРИВЕТ."), ("GHBDTN?", "ПРИВЕТ,"),
    (":bpym", "Жизнь"), (";t", "же"), ("'nj", "это"),
])
def test_colon_guard_preserves_unambiguous_layout_corrections(models, source, target):
    km = make_keymap()
    d = Detector(km, models).decide(keys_for_text(km, source, US), US, RU, two_capitals=True)
    assert (d.action, d.target_text) == ("fix", target)


@pytest.mark.skipif(not XkbKeymap.available(), reason="нет libxkbcommon")
@pytest.mark.parametrize("source,target,word", [(US, RU, "привет"), (RU, US, "hello")])
def test_dvorak_correction_through_controller(models, source, target, word):
    km = XkbKeymap.from_names([LayoutSpec("us", "dvorak"), LayoutSpec("ru")], [])
    ctrl, backend, typist = make_controller(km, models, source)
    # Физические клавиши целевого слова набраны в ошибочной исходной группе.
    for key in keys_for_text(km, word, target):
        press(ctrl, key.code, key.shift)
    press(ctrl, kc.KEY_SPACE)
    assert backend.current() == target
    taps = typist.taps()
    assert sum(code == kc.KEY_BACKSPACE for code, _ in taps) == len(word) + 1
    printed = [KeyPress(code, shift) for code, shift in taps if code != kc.KEY_BACKSPACE]
    assert km.decode(printed, target) == word + " "
