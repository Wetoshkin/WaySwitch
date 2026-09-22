from tests.fakes import make_keymap
from wayswitch import keycodes as kc
from wayswitch.buffer import InputBuffer, KeyPress


def make():
    return InputBuffer(make_keymap(), phrase_timeout=8.0, max_keys=512)


def tap(buf, code, t=0.0, dev=1, shift=False):
    ev = None
    if shift:
        buf.feed(kc.KEY_LEFTSHIFT, 1, dev, t)
    ev = buf.feed(code, 1, dev, t) or ev
    buf.feed(code, 0, dev, t)
    if shift:
        buf.feed(kc.KEY_LEFTSHIFT, 0, dev, t)
    return ev


def test_letters_then_space_emit_word():
    b = make()
    assert tap(b, kc.KEY_G).kind == "letter"
    tap(b, kc.KEY_H)
    ev = tap(b, kc.KEY_B)
    assert ev.kind == "letter" and [k.code for k in ev.keys] == [kc.KEY_G, kc.KEY_H, kc.KEY_B]
    ev = tap(b, kc.KEY_SPACE)
    assert ev.kind == "word" and len(ev.keys) == 3
    assert b.current_word() == []
    word, tail = b.last_word_with_tail()
    assert len(word) == 3 and tail == 1


def test_shift_and_caps_recorded():
    b = make()
    tap(b, kc.KEY_G, shift=True)
    b.set_caps(True)
    tap(b, kc.KEY_H)
    keys = b.current_word()
    assert keys[0] == KeyPress(kc.KEY_G, True, False)
    assert keys[1] == KeyPress(kc.KEY_H, False, True)


def test_backspace_pops_and_crosses_space():
    b = make()
    tap(b, kc.KEY_G)
    tap(b, kc.KEY_SPACE)
    tap(b, kc.KEY_BACKSPACE)
    assert len(b.current_word()) == 1  # пробел стёрт, слово снова текущее
    tap(b, kc.KEY_BACKSPACE)
    assert b.is_empty()
    assert tap(b, kc.KEY_BACKSPACE) is None and b.is_empty()  # стираем чужое → пусто


def test_reset_keys_and_modifiers():
    b = make()
    for code in (kc.KEY_ENTER, kc.KEY_TAB, kc.KEY_LEFT, kc.KEY_HOME, kc.KEY_F5, kc.KEY_DELETE):
        tap(b, kc.KEY_G)
        assert tap(b, code).kind == "reset", code
        assert b.is_empty()
    tap(b, kc.KEY_G)
    b.feed(kc.KEY_LEFTCTRL, 1, 1, 0.0)
    assert b.feed(kc.KEY_C, 1, 1, 0.0).kind == "reset"
    b.feed(kc.KEY_C, 0, 1, 0.0)
    b.feed(kc.KEY_LEFTCTRL, 0, 1, 0.0)
    tap(b, kc.KEY_G)
    assert b.feed(kc.KEY_LEFTMETA, 1, 1, 0.0).kind == "reset"


def test_click_timeout_resync_gone():
    b = make()
    tap(b, kc.KEY_G)
    b.click()
    assert b.is_empty()
    tap(b, kc.KEY_G, t=0.0)
    tap(b, kc.KEY_H, t=9.0)  # пауза больше phrase_timeout — старое забыто
    assert len(b.current_word()) == 1
    b.feed(kc.KEY_LEFTSHIFT, 1, 2, 9.0)
    assert b.shift_held()
    b.resync(2, [])
    assert not b.shift_held() and b.is_empty()
    b.feed(kc.KEY_LEFTSHIFT, 1, 2, 9.0)
    b.device_gone(2)
    assert not b.shift_held()


def test_autorepeat_resets():
    b = make()
    tap(b, kc.KEY_G)
    assert b.feed(kc.KEY_G, 2, 1, 0.0).kind == "reset"


def test_backspace_autorepeat_resets():
    b = make()
    tap(b, kc.KEY_C)
    tap(b, kc.KEY_A)
    tap(b, kc.KEY_T)
    tap(b, kc.KEY_BACKSPACE)  # buffer is [c, a]
    assert len(b.current_word()) == 2
    ev = b.feed(kc.KEY_BACKSPACE, 2, 1, 0.0)
    assert ev.kind == "reset"
    assert b.is_empty()


def test_held_tracking_across_devices():
    b = make()
    b.feed(kc.KEY_LEFTSHIFT, 1, 1, 0.0)
    b.feed(kc.KEY_LEFTSHIFT, 1, 2, 0.0)
    b.feed(kc.KEY_LEFTSHIFT, 0, 1, 0.0)
    assert b.shift_held()
    b.note_release(kc.KEY_LEFTSHIFT, 2)
    assert not b.shift_held()
    b.feed(kc.KEY_G, 1, 1, 0.0)
    assert b.held_non_modifier()
    b.feed(kc.KEY_G, 0, 1, 0.0)
    assert not b.held_non_modifier()


def test_replace_last_word_and_phrase():
    b = make()
    for code in (kc.KEY_G, kc.KEY_H, kc.KEY_SPACE, kc.KEY_B, kc.KEY_SPACE):
        tap(b, code)
    b.replace_last_word([KeyPress(kc.KEY_A), KeyPress(kc.KEY_S)])
    word, tail = b.last_word_with_tail()
    assert [k.code for k in word] == [kc.KEY_A, kc.KEY_S] and tail == 1
    assert [k.code for k in b.phrase()] == [kc.KEY_G, kc.KEY_H, kc.KEY_SPACE, kc.KEY_A,
                                            kc.KEY_S, kc.KEY_SPACE]
    b.replace_all([KeyPress(kc.KEY_Q)])
    assert [k.code for k in b.phrase()] == [kc.KEY_Q]


def test_max_keys_drops_oldest():
    b = InputBuffer(make_keymap(), max_keys=3)
    for code in (kc.KEY_G, kc.KEY_H, kc.KEY_B, kc.KEY_D):
        tap(b, code)
    assert [k.code for k in b.phrase()] == [kc.KEY_H, kc.KEY_B, kc.KEY_D]


def test_ru_punctuation_keys_are_word_keys():
    b = make()
    tap(b, kc.KEY_COMMA)
    tap(b, kc.KEY_SEMICOLON)
    tap(b, kc.KEY_APOSTROPHE)
    assert len(b.current_word()) == 3


def test_timeout_signals_reset():
    b = make()
    tap(b, kc.KEY_G, t=0.0)
    ev = b.feed(kc.KEY_H, 1, 1, 9.0)
    assert ev.kind == "letter"
    assert ev.reset_before is True
    assert len(ev.keys) == 1
    # Separately test that timeout before non-word key returns reset
    b2 = make()
    tap(b2, kc.KEY_G, t=0.0)
    ev2 = b2.feed(kc.KEY_LEFTSHIFT, 1, 1, 9.0)
    assert ev2.kind == "reset"
