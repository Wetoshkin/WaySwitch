from wayswitch import keycodes as kc
from wayswitch.keys import classify

KB = {kc.KEY_A, kc.KEY_Z, kc.KEY_SPACE, kc.KEY_ENTER, kc.KEY_LEFTSHIFT}


def test_classify():
    assert classify("AT Translated Set 2 keyboard", KB) == "keyboard"
    assert classify("Logitech USB Mouse", {kc.BTN_LEFT, kc.BTN_RIGHT}) == "pointer"
    assert classify("Video Bus", {kc.KEY_F1}) is None
    assert classify("WaySwitch Virtual Keyboard", KB) is None
    # Гибрид (клавиатура с кнопками) — клавиатура важнее.
    assert classify("Combo", KB | {kc.BTN_LEFT}) == "keyboard"
