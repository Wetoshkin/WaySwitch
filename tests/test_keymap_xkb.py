import pytest

from wayswitch import keycodes as kc
from wayswitch.keymap import LayoutSpec, XkbKeymap

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
