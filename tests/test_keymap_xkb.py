import pytest

from wayswitch import keycodes as kc
from wayswitch.keymap import NUMPAD_KEYS, LayoutSpec, XkbKeymap

pytestmark = pytest.mark.skipif(not XkbKeymap.available(), reason="нет libxkbcommon")


def test_xkb_us_ru_matches_tables():
    from tests.fakes import RU_TABLE, US_TABLE

    km = XkbKeymap.from_names([LayoutSpec("us"), LayoutSpec("ru")], [])
    assert km.num_groups == 2
    for code, (lo, hi) in US_TABLE.items():
        assert km.char(code, 0) == lo, code
        assert km.char(code, 0, shift=True) == hi, code
    for code, (lo, hi) in RU_TABLE.items():
        assert km.char(code, 1) == lo, code
        assert km.char(code, 1, shift=True) == hi, code
    assert km.alphabet(0) == "latin" and km.alphabet(1) == "ru"
    assert km.key_for("б", 1) == (kc.KEY_COMMA, False)


def test_xkb_dvorak_variant():
    km = XkbKeymap.from_names([LayoutSpec("us", "dvorak"), LayoutSpec("ru")], [])
    assert km.char(kc.KEY_Q, 0) == "'"
    assert km.key_for("q", 0) == (kc.KEY_X, False)


@pytest.mark.parametrize("ch", [".", "/"])
def test_xkb_points_to_main_keyboard_not_numpad(ch):
    """Символы, которые есть и на цифровом блоке, печатаются основной клавишей."""
    km = XkbKeymap.from_names([LayoutSpec("us"), LayoutSpec("ru")], [])
    for group in (0, 1):
        result = km.key_for(ch, group)
        assert result is not None, (ch, group)
        code, _shift = result
        assert code not in NUMPAD_KEYS, (ch, group, code)
