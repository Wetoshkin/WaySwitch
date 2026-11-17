from types import SimpleNamespace

import pytest

from tests.fakes import US, FakeBackend, RecordingTypist, SafeProbe, keys_for_text, make_keymap
from wayswitch import keycodes as kc
from wayswitch.a11y import FocusSnapshot
from wayswitch.compositor_input import MAX_QUEUE, CompositorStream, ShellInput
from wayswitch.config import Config, ConfigError, from_dict, to_dict
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel


def stream_env():
    events, pending = [], []
    stream = CompositorStream(
        lambda *args: events.append(("key", *args)),
        lambda *args: events.append(("resync", *args)),
        lambda *args: events.append(("gone", *args)),
        lambda caps: events.append(("caps", caps)),
        lambda: events.append(("click",)), lambda: events.append(("scroll",)),
        pending.append, clock=lambda: 100.0)
    stream.reset(":shell", 7)
    return stream, events, pending


def key(value, sequence, code=kc.KEY_A, device=-1):
    return code, value, device, 1, 7, sequence, False


def drain(pending):
    while pending:
        pending.pop(0)()


def test_compositor_input_is_opt_in_and_requires_uinput_without_changing_typing_protocol():
    cfg = Config()
    assert cfg.backend.compositor_input is False
    with pytest.raises(ConfigError, match="shell_typing"):
        from_dict({"backend": {"compositor_input": True}})
    cfg.backend.compositor_input = True
    cfg.backend.shell_typing = "uinput"
    assert from_dict(to_dict(cfg)) == cfg
    from wayswitch.backends.gnome_shell import TYPING_PROTOCOL_VERSION

    assert TYPING_PROTOCOL_VERSION == 4


def test_sender_is_cached_and_queue_delivers_press_repeat_release_in_order():
    stream, events, pending = stream_env()
    stream.receive(":forged", "CompositorKey", key(1, 1))
    assert pending == []
    for value, sequence in ((1, 1), (2, 2), (0, 3)):
        stream.receive(":shell", "CompositorKey", key(value, sequence))
    stream.receive(":shell", "CompositorResync", (-1, 1, 7, 3, [], True))
    assert events == []
    drain(pending)
    assert events == [("caps", False), ("key", kc.KEY_A, 1, -1),
                      ("caps", False), ("key", kc.KEY_A, 2, -1),
                      ("caps", False), ("key", kc.KEY_A, 0, -1)]


@pytest.mark.parametrize("index,value", [(0, 0), (0, True), (1, 3), (2, 1), (3, 2), (6, 1)])
def test_invalid_envelope_cannot_deliver_queued_keys(index, value):
    stream, events, pending = stream_env()
    stream.receive(":shell", "CompositorKey", key(1, 1))
    invalid = list(key(0, 2))
    invalid[index] = value
    stream.receive(":shell", "CompositorKey", invalid)
    drain(pending)
    assert stream.input_locked
    assert not any(event[0] == "key" for event in events)


@pytest.mark.parametrize("fault", ["gap", "repeat", "duplicate", "overflow"])
def test_loss_blocks_input_until_complete_empty_resync_without_replay(fault):
    stream, events, pending = stream_env()
    stream.receive(":shell", "CompositorKey", key(1, 1))
    sequence = 2
    if fault == "gap":
        sequence = 3
        stream.receive(":shell", "CompositorKey", key(0, sequence))
    elif fault == "repeat":
        stream.receive(":shell", "CompositorKey", key(2, sequence, code=kc.KEY_B))
    elif fault == "duplicate":
        stream.receive(":shell", "CompositorKey", key(1, sequence))
    else:
        for sequence in range(2, MAX_QUEUE + 2):
            stream.receive(":shell", "CompositorKey", key(0 if sequence % 2 == 0 else 1, sequence))
    assert stream.input_locked is True
    drain(pending)
    assert not any(event[0] == "key" for event in events)
    stream.receive(":shell", "CompositorResync", (-1, 1, 7, sequence, [], False))
    assert stream.input_locked is True
    stream.receive(":shell", "CompositorResync", (-1, 1, 7, sequence, [], True))
    assert stream.input_locked is True  # остаток повреждённого слова ещё не завершён
    stream.receive(":shell", "CompositorKey", key(1, sequence + 1, kc.KEY_SPACE))
    stream.receive(":shell", "CompositorKey", key(0, sequence + 2, kc.KEY_SPACE))
    drain(pending)
    assert stream.input_locked is False
    sequence += 2
    events.clear()
    stream.receive(":shell", "CompositorKey", key(1, sequence + 1))
    drain(pending)
    assert events == [("caps", False), ("key", kc.KEY_A, 1, -1)]


def test_owner_change_unplug_and_click_discard_pending_events():
    stream, events, pending = stream_env()
    stream.receive(":shell", "CompositorKey", key(1, 1))
    stream.receive(":shell", "CompositorDeviceGone", (-1, 1, 7))
    drain(pending)
    assert events == [("gone", -1)]
    stream.receive(":shell", "CompositorKey", key(1, 1, device=-2))
    stream.reset(":new-shell", 8)
    drain(pending)
    assert events[-1] == ("gone", -2)
    stream.receive(":shell", "CompositorKey", key(0, 2))
    assert not pending


def test_boundary_drops_old_word_and_restores_current_held_state():
    stream, events, pending = stream_env()
    stream.receive(":shell", "CompositorKey", key(1, 1, kc.KEY_LEFTSHIFT))
    stream.receive(":shell", "CompositorBoundary", (7, 0))
    drain(pending)
    assert events == [("resync", -1, [kc.KEY_LEFTSHIFT]), ("click",)]


def test_new_generation_resync_discards_old_keys_and_requires_new_word_boundary():
    stream, events, pending = stream_env()
    stream.receive(":shell", "CompositorKey", key(1, 1))
    stream.receive(":shell", "CompositorResync", (-1, 1, 8, 2, [], True))
    stream.receive(":shell", "CompositorKey", key(0, 2))
    drain(pending)
    assert stream.generation == 8 and stream.input_locked
    assert not any(event[0] == "key" for event in events)
    stream.receive(":shell", "CompositorKey", (kc.KEY_SPACE, 1, -1, 1, 8, 3, False))
    stream.receive(":shell", "CompositorKey", (kc.KEY_SPACE, 0, -1, 1, 8, 4, False))
    drain(pending)
    assert not stream.input_locked


def test_unsupported_source_locks_even_without_registered_devices_until_restart():
    stream, events, pending = stream_env()
    stream.receive(":shell", "CompositorFault", (8,))
    assert stream.input_locked
    stream.receive(":shell", "CompositorKey", (kc.KEY_SPACE, 1, -1, 1, 8, 1, False))
    drain(pending)
    assert not any(event[0] == "key" for event in events)
    stream.reset(":shell", 9)
    assert not stream.input_locked


def test_dbus_callback_uses_connection_and_sender_without_owner_lookup_per_key():
    stream, events, pending = stream_env()
    connection = object()

    def forbidden():
        raise AssertionError("No owner query on each key")

    proxy = SimpleNamespace(get_connection=lambda: connection, get_name_owner=forbidden)
    client = ShellInput(stream, None, "/dev/input/event9")
    client._proxy, client._connection, client.active = proxy, connection, True
    params = SimpleNamespace(unpack=lambda: key(1, 1))
    client._signal(object(), ":shell", "CompositorKey", params)
    client._signal(proxy, ":forged", "CompositorKey", params)
    assert pending == []
    client._signal(proxy, ":shell", "CompositorKey", params)
    client._owner(proxy, None)
    drain(pending)
    assert events == [("gone", -1)]
    assert not client.active


@pytest.mark.parametrize("mode", ["normal", "pause", "password"])
def test_remote_word_uses_same_controller_correction_and_protection(mode):
    km, cfg = make_keymap(), Config()
    cfg.general.early_switch = False
    detector = Detector(km, {
        "ru": LanguageModel.from_words("ru", [("привет", 500.0)],
                                       "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"),
        "en": LanguageModel.from_words("en", [("hello", 500.0)], "abcdefghijklmnopqrstuvwxyz"),
    })
    pending, corrected = [], []
    probe = SafeProbe(lambda: 100.0)
    if mode == "password":
        probe.snapshot = lambda: FocusSnapshot("editor", True, 100.0)
    typist = RecordingTypist()
    controller = Controller(
        cfg, km, detector, FakeBackend(current=US), typist, probe=probe, clock=lambda: 100.0,
        schedule=pending.append, on_corrected=lambda *args: corrected.append(args))
    if mode == "pause":
        controller.pause()
    stream = CompositorStream(
        controller.on_key, controller.on_resync, controller.on_device_gone, controller.set_caps,
        controller.on_click, controller.on_scroll, pending.append, clock=lambda: 100.0)
    stream.reset(":shell", 7)
    controller.screen_locked = lambda: stream.input_locked
    sequence = 0
    for press in keys_for_text(km, "ghbdtn ", US):
        for value in (1, 0):
            sequence += 1
            stream.receive(":shell", "CompositorKey", key(value, sequence, press.code))
        stream.receive(":shell", "CompositorResync", (-1, 1, 7, sequence, [], True))
        drain(pending)
    assert corrected == ([("ghbdtn", "привет", False)] if mode == "normal" else [])
    if mode != "normal":
        assert typist.events == []
