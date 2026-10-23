from wayswitch import keycodes as kc
from wayswitch.backends.gnome_common import (
    engine_name_for,
    index_of_engine,
    index_of_mru,
    parse_gnome_binding,
)
from wayswitch.backends.hotkey import HotkeyBackend
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


def test_hotkey_observe_physical_both_directions():
    specs = [LayoutSpec("us"), LayoutSpec("ru")]
    forward = [kc.KEY_LEFTMETA, kc.KEY_SPACE]
    backward = [kc.KEY_LEFTSHIFT, kc.KEY_LEFTMETA, kc.KEY_SPACE]
    backend = HotkeyBackend(typist=None, settle_ms=0, combos=[forward, backward], specs=specs)
    assert backend.current() == 0

    # Прямой хоткей: <Super>space — переключает на 1.
    backend.observe_physical(kc.KEY_LEFTMETA, 1)
    backend.observe_physical(kc.KEY_SPACE, 1)
    assert backend.current() == 1
    backend.observe_physical(kc.KEY_SPACE, 0)
    backend.observe_physical(kc.KEY_LEFTMETA, 0)

    # Обратный хоткей: <Shift><Super>space — с двумя раскладками снова переключает,
    # на этот раз обратно на 0.
    backend.observe_physical(kc.KEY_LEFTSHIFT, 1)
    backend.observe_physical(kc.KEY_LEFTMETA, 1)
    backend.observe_physical(kc.KEY_SPACE, 1)
    assert backend.current() == 0
    backend.observe_physical(kc.KEY_SPACE, 0)
    backend.observe_physical(kc.KEY_LEFTMETA, 0)
    backend.observe_physical(kc.KEY_LEFTSHIFT, 0)

    # Пробел с лишней посторонней зажатой клавишей не совпадает ни с одной
    # комбинацией целиком — переключения быть не должно.
    backend.observe_physical(kc.KEY_A, 1)
    backend.observe_physical(kc.KEY_LEFTMETA, 1)
    backend.observe_physical(kc.KEY_SPACE, 1)
    assert backend.current() == 0


def test_index_of_mru_points_to_current_layout():
    specs = [LayoutSpec("us"), LayoutSpec("ru")]
    assert index_of_mru([("xkb", "us"), ("xkb", "ru")], specs) == 0
    assert index_of_mru([("xkb", "ru"), ("xkb", "us")], specs) == 1
    # Вариант раскладки — тот же источник, сопоставляем по паре layout+variant.
    specs_v = [LayoutSpec("us", "dvorak"), LayoutSpec("ru")]
    assert index_of_mru([("xkb", "ru"), ("xkb", "us+dvorak")], specs_v) == 1
    assert index_of_mru([], specs) is None
    assert index_of_mru([("ibus", "mozc")], specs) is None
    assert index_of_mru([("xkb", "de")], specs) is None


def test_shell_wait_applied_waits_settle(monkeypatch):
    """После SetLayout композитору нужно время: пауза обязательна до печати."""
    from wayswitch.backends import gnome_shell

    backend = object.__new__(gnome_shell.GnomeShellBackend)
    backend._settle = 25
    backend._current = 1

    slept = []
    monkeypatch.setattr(gnome_shell.time, "sleep", lambda s: slept.append(s))

    assert backend.wait_applied(1, 2.0) is True
    assert slept == [0.025]

    slept.clear()
    assert backend.wait_applied(0, 2.0) is False
    assert slept == [0.025]
