from wayswitch import keycodes as kc
from wayswitch.backends.gnome_common import engine_name_for, index_of_engine, parse_gnome_binding
from wayswitch.keymap import LayoutSpec


def test_engine_names():
    assert engine_name_for(LayoutSpec("us")) == "xkb:us::"
    assert engine_name_for(LayoutSpec("us", "dvorak")) == "xkb:us:dvorak:"
    specs = [LayoutSpec("us"), LayoutSpec("ru")]
    assert index_of_engine("xkb:ru::rus", specs) == 1
    assert index_of_engine("xkb:us::eng", specs) == 0
    assert index_of_engine("xkb:us:dvorak:eng", specs) is None
    assert index_of_engine("anthy", specs) is None


def test_parse_binding():
    assert parse_gnome_binding("<Super>space") == [kc.KEY_LEFTMETA, kc.KEY_SPACE]
    assert parse_gnome_binding("<Shift><Alt>Shift_L") == [kc.KEY_LEFTSHIFT, kc.KEY_LEFTALT,
                                                          kc.KEY_LEFTSHIFT]
    assert parse_gnome_binding("<Control>grave") == [kc.KEY_LEFTCTRL, kc.KEY_GRAVE]
    assert parse_gnome_binding("") == []
