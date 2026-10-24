from wayswitch import keycodes as kc
from wayswitch.gesture import ShiftGesture, TapKeyGesture


def press_release(g, code, t):
    r1 = g.on_key(code, 1, t)
    r2 = g.on_key(code, 0, t + 0.05)
    return r1 or r2


def test_double_shift_on_release_of_second():
    g = ShiftGesture(window=0.4)
    assert press_release(g, kc.KEY_LEFTSHIFT, 0.0) is None
    assert g.on_key(kc.KEY_RIGHTSHIFT, 1, 0.2) is None
    assert g.on_key(kc.KEY_RIGHTSHIFT, 0, 0.25) == "word"


def test_triple_shift_is_phrase():
    g = ShiftGesture()
    press_release(g, kc.KEY_LEFTSHIFT, 0.0)
    assert press_release(g, kc.KEY_LEFTSHIFT, 0.2) == "word"
    assert press_release(g, kc.KEY_LEFTSHIFT, 0.4) == "phrase"
    assert press_release(g, kc.KEY_LEFTSHIFT, 0.6) is None  # серия закончена


def test_other_key_breaks_series():
    g = ShiftGesture()
    press_release(g, kc.KEY_LEFTSHIFT, 0.0)
    g.on_key(kc.KEY_A, 1, 0.1)
    g.on_key(kc.KEY_A, 0, 0.15)
    assert press_release(g, kc.KEY_LEFTSHIFT, 0.2) is None


def test_slow_taps_do_not_count():
    g = ShiftGesture(window=0.4)
    press_release(g, kc.KEY_LEFTSHIFT, 0.0)
    assert press_release(g, kc.KEY_LEFTSHIFT, 1.0) is None


def test_shift_held_for_capital_letter_is_not_a_tap():
    g = ShiftGesture()
    g.on_key(kc.KEY_LEFTSHIFT, 1, 0.0)
    g.on_key(kc.KEY_H, 1, 0.1)
    g.on_key(kc.KEY_H, 0, 0.15)
    g.on_key(kc.KEY_LEFTSHIFT, 0, 0.2)
    assert press_release(g, kc.KEY_LEFTSHIFT, 0.3) is None


def ctrl_tap(g, t, hold=0.1):
    g.on_key(kc.KEY_LEFTCTRL, 1, t)
    return g.on_key(kc.KEY_LEFTCTRL, 0, t + hold)


def test_ctrl_tap_returns_word():
    g = TapKeyGesture()
    assert ctrl_tap(g, 0.0) == "word"


def test_ctrl_tap_with_other_key_is_not_gesture():
    g = TapKeyGesture()
    g.on_key(kc.KEY_LEFTCTRL, 1, 0.0)
    g.on_key(kc.KEY_C, 1, 0.05)     # Ctrl+C — сокращение, а не жест
    g.on_key(kc.KEY_C, 0, 0.1)
    assert g.on_key(kc.KEY_LEFTCTRL, 0, 0.15) is None


def test_ctrl_hold_is_not_gesture():
    g = TapKeyGesture(window=0.5)
    assert ctrl_tap(g, 0.0, hold=0.9) is None


def test_ctrl_tap_two_times_in_a_row():
    g = TapKeyGesture()
    assert ctrl_tap(g, 0.0) == "word"
    assert ctrl_tap(g, 1.0) == "word"


def test_ctrl_reset_breaks_tap():
    g = TapKeyGesture()
    g.on_key(kc.KEY_LEFTCTRL, 1, 0.0)
    g.reset()  # клик мышью посреди тапа
    assert g.on_key(kc.KEY_LEFTCTRL, 0, 0.1) is None
