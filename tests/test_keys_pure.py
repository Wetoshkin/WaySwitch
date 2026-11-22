import sys
from types import SimpleNamespace

import pytest

from wayswitch import keycodes as kc
from wayswitch.keys import DeviceWatcher, classify

KB = {kc.KEY_A, kc.KEY_Z, kc.KEY_SPACE, kc.KEY_ENTER, kc.KEY_LEFTSHIFT}


def test_classify():
    assert classify("AT Translated Set 2 keyboard", KB) == "keyboard"
    assert classify("Logitech USB Mouse", {kc.BTN_LEFT, kc.BTN_RIGHT}) == "pointer"
    assert classify("Video Bus", {kc.KEY_F1}) is None
    assert classify("WaySwitch Virtual Keyboard", KB) is None
    # Гибрид (клавиатура с кнопками) — клавиатура важнее.
    assert classify("Combo", KB | {kc.BTN_LEFT}) == "keyboard"


@pytest.fixture
def input_api(monkeypatch):
    ecodes = SimpleNamespace(EV_SYN=0, EV_KEY=1, EV_REL=2, EV_LED=17,
                             SYN_DROPPED=3, LED_CAPSL=1)
    glib = SimpleNamespace(IOCondition=SimpleNamespace(IN=1, HUP=8, ERR=16),
                           PRIORITY_DEFAULT=0, io_add_watch=lambda *_args: 1)
    monkeypatch.setitem(sys.modules, "evdev", SimpleNamespace(ecodes=ecodes))
    monkeypatch.setitem(sys.modules, "gi", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "gi.repository", SimpleNamespace(GLib=glib))
    return ecodes


def manager(caps, keys=None):
    return DeviceWatcher(
        lambda *args: keys.append((args, caps[-1])) if keys is not None else None,
        lambda: None, lambda *_args: None, lambda *_args: None, caps.append)


@pytest.mark.parametrize("kind", ["keyboard", "pointer"])
@pytest.mark.parametrize("old", [False, True])
def test_caps_led_event_supersedes_stale_state_at_key_release(input_api, kind, old):
    e = input_api
    caps, keys = [], []
    events = [SimpleNamespace(type=e.EV_KEY, code=kc.KEY_CAPSLOCK, value=0),
              SimpleNamespace(type=e.EV_LED, code=e.LED_CAPSL, value=int(not old)),
              SimpleNamespace(type=e.EV_KEY, code=kc.KEY_A, value=1)]
    dev = SimpleNamespace(read=lambda: events, leds=lambda: [e.LED_CAPSL] if old else [])
    reader = manager(caps, keys)
    # Establish the old state without requiring any physical key event.
    caps.append(old)
    reader._devices["keyboard"] = (dev, 1, kind, 1)
    assert reader._on_readable(None, 1, "keyboard") is True
    assert caps[-1] is (not old if kind == "keyboard" else old)
    if kind == "keyboard":
        assert keys[-1] == ((kc.KEY_A, 1, 1), not old)


def test_open_keyboard_reads_existing_caps_led(input_api, monkeypatch):
    e = input_api
    dev = SimpleNamespace(name="Keyboard", fd=5,
                          info=SimpleNamespace(vendor=1, product=1),
                          capabilities=lambda: {e.EV_KEY: list(KB), e.EV_LED: [e.LED_CAPSL]},
                          leds=lambda: [e.LED_CAPSL])
    monkeypatch.setattr(sys.modules["evdev"], "InputDevice", lambda _path: dev, raising=False)
    caps = []
    reader = manager(caps)
    reader._open("keyboard")
    assert caps == [True]


@pytest.mark.parametrize("name,vendor,product,want", [
    ("WaySwitch Virtual Keyboard", 1, 1, None),
    ("handy-keys passthrough: WaySwitch Virtual Keyboard", 0, 0, None),
    ("renamed output", 0x0FAC, 1, None),
    ("AT Translated Set 2 keyboard", 1, 1, "keyboard"),
    ("handy-keys passthrough: AT Translated Set 2 keyboard", 1, 1, "keyboard"),
    ("keyd virtual keyboard", 0x0FAC, 0x0ADE, "keyboard"),
    ("handy-keys passthrough: keyd virtual keyboard", 0x0FAC, 0x0ADE, "keyboard"),
    ("Other keyboard", 0x0FAC, 2, "keyboard"),
    ("Other keyboard", 2, 1, "keyboard"),
])
def test_classify_republished_output_without_excluding_input(name, vendor, product, want):
    assert classify(name, KB, vendor=vendor, product=product) == want


def test_classify_handy_output_without_device_ids():
    assert classify("handy-keys passthrough: WaySwitch Virtual Keyboard", KB) is None


@pytest.mark.parametrize("name,vendor,product,want", [
    ("renamed output", 0x0FAC, 1, False),
    ("handy-keys passthrough: WaySwitch Virtual Keyboard", 0x0FAC, 1, False),
    ("AT Translated Set 2 keyboard", 1, 1, True),
    ("handy-keys passthrough: AT Translated Set 2 keyboard", 1, 1, True),
    ("keyd virtual keyboard", 0x0FAC, 0x0ADE, True),
])
def test_watcher_filters_own_output_and_delivers_input(
        input_api, monkeypatch, name, vendor, product, want):
    e = input_api
    closed = []
    dev = SimpleNamespace(
        name=name, fd=5, info=SimpleNamespace(vendor=vendor, product=product),
        capabilities=lambda: {e.EV_KEY: list(KB)}, close=lambda: closed.append(True),
        read=lambda: [SimpleNamespace(type=e.EV_KEY, code=kc.KEY_A, value=1)])
    monkeypatch.setattr(sys.modules["evdev"], "InputDevice", lambda _path: dev, raising=False)
    keys = []
    reader = manager([False], keys)
    reader._open("keyboard")
    if want:
        assert reader.devices() == [("keyboard", name, "keyboard")]
        assert reader._on_readable(None, 1, "keyboard") is True
        assert keys == [((kc.KEY_A, 1, 1), False)]
        assert closed == []
    else:
        assert reader.devices() == []
        assert keys == []
        assert closed == [True]
