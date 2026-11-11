"""Переходы модификаторов между обычным вводом, сочетаниями и исправлением."""

import pytest

from tests.fakes import US, make_keymap
from tests.test_buffer import make, tap
from tests.test_controller import double_shift, type_text, typed_text
from tests.test_controller import env as env
from wayswitch import keycodes as kc
from wayswitch.buffer import KeyPress


@pytest.mark.parametrize("shift", sorted(kc.SHIFT_KEYS))
@pytest.mark.parametrize("caps", [False, True])
def test_shift_release_and_caps_xor(shift, caps):
    buf = make()
    buf.set_caps(caps)
    buf.feed(shift, 1, 1, 0)
    tap(buf, kc.KEY_G)
    buf.feed(shift, 0, 1, 0)
    tap(buf, kc.KEY_H)
    assert buf.current_word() == [KeyPress(kc.KEY_G, True, caps),
                                  KeyPress(kc.KEY_H, False, caps)]
    assert make_keymap().decode(buf.current_word(), US) == ("gH" if caps else "Gh")


@pytest.mark.parametrize("shift", sorted(kc.SHIFT_KEYS))
def test_shift_held_across_word_and_punctuation(shift):
    buf = make()
    buf.feed(shift, 1, 1, 0)
    tap(buf, kc.KEY_G)
    assert tap(buf, kc.KEY_SPACE).keys == [KeyPress(kc.KEY_G, True)]
    tap(buf, kc.KEY_H)
    tap(buf, kc.KEY_COMMA)
    assert all(key.shift for key in buf.current_word())
    buf.feed(shift, 0, 1, 0)
    tap(buf, kc.KEY_B)
    assert buf.current_word()[-1] == KeyPress(kc.KEY_B)


@pytest.mark.parametrize("modifier", [kc.KEY_LEFTCTRL, kc.KEY_RIGHTCTRL, kc.KEY_LEFTALT])
@pytest.mark.parametrize("shift", sorted(kc.SHIFT_KEYS))
@pytest.mark.parametrize("command_first", [False, True])
def test_command_shift_order_does_not_reuse_old_text(modifier, shift, command_first):
    buf = make()
    tap(buf, kc.KEY_G)
    order = (modifier, shift) if command_first else (shift, modifier)
    for code in order:
        buf.feed(code, 1, 1, 0)
    assert tap(buf, kc.KEY_C).kind == "reset"
    for code in reversed(order):
        buf.feed(code, 0, 1, 0)
    tap(buf, kc.KEY_H)
    assert buf.current_word() == [KeyPress(kc.KEY_H)]
    assert not buf.command_held() and not buf.shift_held()


@pytest.mark.parametrize("modifier", [*sorted(kc.SHIFT_KEYS), kc.KEY_LEFTCTRL,
                                      kc.KEY_RIGHTCTRL, kc.KEY_LEFTALT])
def test_bare_modifier_preserves_word(modifier):
    buf = make()
    tap(buf, kc.KEY_G)
    tap(buf, modifier)
    tap(buf, kc.KEY_H)
    assert buf.current_word() == [KeyPress(kc.KEY_G), KeyPress(kc.KEY_H)]


@pytest.mark.parametrize("shift", sorted(kc.SHIFT_KEYS))
def test_busy_tracking_keeps_second_keyboard_shift(shift):
    buf = make()
    buf.note_press(shift, 1)
    buf.note_press(shift, 2)
    buf.note_release(shift, 1)
    tap(buf, kc.KEY_G)
    assert buf.current_word() == [KeyPress(kc.KEY_G, True)]
    buf.note_release(shift, 2)
    tap(buf, kc.KEY_H)
    assert buf.current_word()[-1] == KeyPress(kc.KEY_H)
    assert not buf.held_physical()


@pytest.mark.parametrize("modifier", [kc.KEY_LEFTCTRL, kc.KEY_RIGHTCTRL, kc.KEY_LEFTALT])
@pytest.mark.parametrize("shift", sorted(kc.SHIFT_KEYS))
def test_releasing_command_while_shift_stays_held_starts_new_word(modifier, shift):
    buf = make()
    tap(buf, kc.KEY_G)
    buf.feed(shift, 1, 1, 0)
    buf.feed(modifier, 1, 1, 0)
    assert tap(buf, kc.KEY_C).kind == "reset"
    buf.feed(modifier, 0, 1, 0)
    tap(buf, kc.KEY_H)
    assert buf.current_word() == [KeyPress(kc.KEY_H, True)]
    buf.feed(shift, 0, 1, 0)
    tap(buf, kc.KEY_COMMA)
    assert buf.current_word()[-1] == KeyPress(kc.KEY_COMMA)


@pytest.mark.parametrize("shift", sorted(kc.SHIFT_KEYS))
def test_released_shift_word_boundary_and_tab(shift):
    buf = make()
    buf.feed(shift, 1, 1, 0)
    tap(buf, kc.KEY_G)
    buf.feed(shift, 0, 1, 0)
    assert tap(buf, kc.KEY_SPACE).keys == [KeyPress(kc.KEY_G, True)]
    tap(buf, kc.KEY_H)
    assert tap(buf, kc.KEY_TAB).kind == "reset"
    assert buf.is_empty()


def test_real_caps_is_not_normalized_as_two_initial_capitals(env):
    env["ctrl"].buffer.set_caps(True)
    type_text(env, "ghbdtn ")
    assert all(fixed != "Привет" for _original, fixed, _manual in env["corrected"])
    assert env["ctrl"].buffer.caps is True


def test_two_initial_capitals_correction_and_undo(env):
    type_text(env, "GHbdtn ")
    assert env["corrected"] == [("GHbdtn", "Привет", False)]
    double_shift(env)
    assert typed_text(env).endswith("GHbdtn ")


@pytest.mark.parametrize("modifier", [kc.KEY_LEFTCTRL, kc.KEY_RIGHTCTRL, kc.KEY_LEFTALT])
def test_controller_does_not_correct_text_from_before_command(env, modifier):
    type_text(env, "ghb")
    ctrl = env["ctrl"]
    ctrl.on_key(modifier, 1, 1)
    ctrl.on_key(kc.KEY_C, 1, 1)
    ctrl.on_key(kc.KEY_C, 0, 1)
    ctrl.on_key(modifier, 0, 1)
    type_text(env, "hello ")
    assert not env["corrected"] and not env["typist"].events


@pytest.mark.parametrize("shift", sorted(kc.SHIFT_KEYS))
def test_held_shift_cancels_pending_correction_without_changing_caps(env, shift):
    ctrl = env["ctrl"]
    pending = []
    ctrl.schedule = pending.append
    type_text(env, "ghbdtn ")
    assert pending
    ctrl.on_key(shift, 1, 2)
    for work in pending:
        work()
    assert not env["corrected"] and not env["typist"].events
    assert ctrl.buffer.caps is False
    ctrl.on_key(shift, 0, 2)
    assert not ctrl.buffer.shift_held()
