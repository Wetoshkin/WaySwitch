"""«Исправить выделенное»: перевод фразы в другую раскладку и её печать."""

from tests.fakes import RU, US, FakeBackend, RecordingTextTypist, RecordingTypist, make_keymap
from wayswitch import keycodes as kc
from wayswitch.buffer import KeyPress
from wayswitch.config import Config
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel
from wayswitch.selection import convert, source_group


def test_convert_latin_to_russian_with_punctuation():
    km = make_keymap()
    # Знаки тоже переводятся через клавишу: «?» в US — это «,» в русской.
    assert convert(km, "ghbdtn? rfr ltkf&") == ("привет, как дела?", RU)
    assert convert(km, "Ghbdtn") == ("Привет", RU)


def test_convert_russian_to_latin():
    km = make_keymap()
    assert convert(km, "руддщ цщкдв") == ("hello world", US)


def test_convert_keeps_digits_and_unknown_symbols():
    km = make_keymap()
    text, group = convert(km, "rjl 2026\n")
    assert text == "код 2026\n" and group == RU


def test_convert_nothing_to_do():
    km = make_keymap()
    assert convert(km, "") is None
    assert convert(km, "12345") is None
    assert source_group(km, "...") is None


class SelectingBackend(FakeBackend):
    def __init__(self, selection, **kw):
        super().__init__(**kw)
        self.selection = selection

    def get_selection(self):
        return self.selection


def make_ctrl(selection, typist=None, current=US):
    km = make_keymap()
    det = Detector(km, {
        "ru": LanguageModel.from_words("ru", [("привет", 900.0)],
                                       "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"),
        "en": LanguageModel.from_words("en", [("hello", 900.0)], "abcdefghijklmnopqrstuvwxyz"),
    })
    backend = SelectingBackend(selection, current=current)
    corrected = []
    ctrl = Controller(Config(), km, det, backend, typist or RecordingTypist(),
                      on_corrected=lambda o, f, m: corrected.append((o, f, m)))
    return ctrl, backend, corrected


def test_fix_selection_types_over_selection_without_backspace():
    typist = RecordingTypist()
    ctrl, backend, corrected = make_ctrl("ghbdtn vbh", typist)
    assert ctrl.fix_selection()
    taps = typist.taps()
    assert kc.KEY_BACKSPACE not in [c for c, _ in taps]
    assert make_keymap().decode([KeyPress(c, s) for c, s in taps], RU) == "привет мир"
    assert backend.current() == RU
    assert corrected == [("ghbdtn vbh", "привет мир", True)]


def test_fix_selection_with_extension_typist():
    typist = RecordingTextTypist()
    ctrl, _backend, _corrected = make_ctrl("руддщ", typist, current=RU)
    assert ctrl.fix_selection()
    assert typist.calls == [("type", "hello")]


def test_fix_selection_nothing_selected():
    ctrl, backend, corrected = make_ctrl(None)
    assert not ctrl.fix_selection()
    assert backend.set_calls == [] and corrected == []


def test_selection_key_from_config():
    """Клавиша «Исправить выделенное» — та, что выбрал человек, не Shift+Pause."""
    cfg = Config()
    cfg.gesture.selection_key = "rightctrl"
    typist = RecordingTypist()
    ctrl, _backend, corrected = make_ctrl("ghbdtn", typist)
    ctrl.reload_config(cfg)
    ctrl.on_key(kc.KEY_RIGHTCTRL, 1, 1)
    ctrl.on_key(kc.KEY_RIGHTCTRL, 0, 1)
    assert corrected and corrected[0][1] == "привет"


def test_selection_key_combo_is_not_a_tap():
    cfg = Config()
    cfg.gesture.selection_key = "rightctrl"
    ctrl, _backend, corrected = make_ctrl("ghbdtn")
    ctrl.reload_config(cfg)
    ctrl.on_key(kc.KEY_RIGHTCTRL, 1, 1)
    ctrl.on_key(kc.KEY_C, 1, 1)
    ctrl.on_key(kc.KEY_C, 0, 1)
    ctrl.on_key(kc.KEY_RIGHTCTRL, 0, 1)
    assert corrected == []


def test_selection_key_validation():
    import pytest

    from wayswitch import config as cfgmod

    cfg = cfgmod.Config()
    cfg.gesture.selection_key = "a"
    with pytest.raises(cfgmod.ConfigError):
        cfgmod._validate(cfg)
    cfg.gesture.selection_key = "leftctrl"
    cfg.gesture.manual = "tap_key"
    with pytest.raises(cfgmod.ConfigError):
        cfgmod._validate(cfg)


def test_fix_selection_pressed_twice_types_once():
    # Сочетание нажали второй раз, пока первый вызов ждёт отпускания клавиш:
    # второй вызов приходит через pump() и должен быть отклонён.
    typist = RecordingTypist()
    ctrl, _backend, _corrected = make_ctrl("ghbdtn", typist)
    ctrl.buffer.note_press(kc.KEY_LEFTCTRL, 1)
    second = []

    def pump():
        if not second:
            second.append(ctrl.fix_selection())
        ctrl.buffer.note_release(kc.KEY_LEFTCTRL, 1)

    ctrl.pump = pump
    assert ctrl.fix_selection()
    assert second == [False]
    letters = [c for c, _ in typist.taps() if c not in (kc.KEY_LEFTSHIFT,)]
    assert len(letters) == len("привет")
