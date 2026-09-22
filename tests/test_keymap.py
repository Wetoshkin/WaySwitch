from dataclasses import dataclass

from tests.fakes import RU, US, make_keymap
from wayswitch import keycodes as kc
from wayswitch.keymap import LayoutSpec


@dataclass
class K:
    code: int
    shift: bool = False
    caps: bool = False


def test_layout_spec_parse():
    assert LayoutSpec.parse("us+dvorak") == LayoutSpec("us", "dvorak")
    assert LayoutSpec.parse("ru").xkb_id == "ru"
    assert LayoutSpec("us", "dvorak").xkb_id == "us+dvorak"


def test_char_by_group_and_shift():
    km = make_keymap()
    assert km.char(kc.KEY_G, US) == "g"
    assert km.char(kc.KEY_G, RU) == "п"
    assert km.char(kc.KEY_G, RU, shift=True) == "П"
    assert km.char(kc.KEY_SLASH, RU) == "."
    assert km.char(kc.KEY_SLASH, RU, shift=True) == ","


def test_caps_inverts_only_letters():
    km = make_keymap()
    assert km.char(kc.KEY_A, US, caps=True) == "A"
    assert km.char(kc.KEY_A, US, shift=True, caps=True) == "a"
    assert km.char(kc.KEY_1, US, caps=True) == "1"


def test_key_for_prefers_unshifted():
    km = make_keymap()
    assert km.key_for("п", RU) == (kc.KEY_G, False)
    assert km.key_for("П", RU) == (kc.KEY_G, True)
    assert km.key_for(",", RU) == (kc.KEY_SLASH, True)
    assert km.key_for(",", US) == (kc.KEY_COMMA, False)
    assert km.key_for("ж", US) is None
    assert km.key_for(" ", US) == (kc.KEY_SPACE, False)


def test_letter_keys_are_union_of_groups():
    km = make_keymap()
    # ; в us — не буква, но в ru это ж → клавиша считается буквенной.
    assert km.is_letter_key(kc.KEY_SEMICOLON)
    assert km.is_letter_key(kc.KEY_GRAVE)
    assert km.is_letter_key(kc.KEY_A)
    assert not km.is_letter_key(kc.KEY_1)
    assert not km.is_letter_key(kc.KEY_SPACE)


def test_alphabet():
    km = make_keymap()
    assert km.alphabet(US) == "latin"
    assert km.alphabet(RU) == "ru"


def test_decode():
    km = make_keymap()
    keys = [K(kc.KEY_G, shift=True), K(kc.KEY_H), K(kc.KEY_B), K(kc.KEY_SLASH, shift=True)]
    assert km.decode(keys, US) == "Ghb?"
    assert km.decode(keys, RU) == "При,"
