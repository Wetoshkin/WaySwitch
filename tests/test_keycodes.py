from wayswitch import keycodes as kc


def test_letter_codes_match_linux_headers():
    # Коды букв в input-event-codes.h не идут по алфавиту — проверяем точечно.
    assert kc.KEY_Q == 16 and kc.KEY_A == 30 and kc.KEY_Z == 44 and kc.KEY_M == 50
    assert kc.KEY_SPACE == 57 and kc.KEY_BACKSPACE == 14 and kc.KEY_LEFTMETA == 125


def test_sets_are_disjoint_where_expected():
    assert not (kc.SHIFT_KEYS & kc.COMMAND_MODIFIERS)
    assert kc.KEY_ENTER in kc.RESET_KEYS and kc.KEY_F12 in kc.RESET_KEYS
    assert kc.KEY_SPACE not in kc.RESET_KEYS


def test_pointer_buttons():
    assert kc.is_pointer_button(kc.BTN_LEFT)
    assert kc.is_pointer_button(0x117)
    assert not kc.is_pointer_button(kc.KEY_A)


def test_key_names_round_trip():
    assert kc.KEY_NAMES["space"] == kc.KEY_SPACE
    assert kc.KEY_NAMES["pause"] == kc.KEY_PAUSE
    assert kc.key_name(kc.KEY_LEFTSHIFT) == "leftshift"
