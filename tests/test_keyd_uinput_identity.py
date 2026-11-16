"""Regression for issue #91: keyd must not recycle WaySwitch's own uinput keys.

keyd 2.6.0+ treats any uinput device with vendor 0x0FAC as its own virtual
device and skips it before wildcard matching. Older keyd (2.4.3/2.5.0) only
skips the exact name ``keyd virtual keyboard`` and still grabs a WaySwitch
uinput device, so the manual [ids] exclusion stays documented.
"""
import sys
from types import ModuleType, SimpleNamespace

import pytest

from tests.fakes import RecordingTypist
from tests.test_controller import env as controller_env
from tests.test_controller import type_text, typed_text
from wayswitch import keycodes as kc
from wayswitch.actuator import UInputTypist

KEYD_VIRTUAL_VENDOR = 0x0FAC
KEYD_VKBD_NAME = "keyd virtual keyboard"


class FakeUInputError(Exception):
    pass


class FakeUInput:
    instances = []

    def __init__(self, events, **kwargs):
        self.events = events
        self.kwargs = kwargs
        self.writes = []
        self.closed = False
        FakeUInput.instances.append(self)

    def write(self, event_type, code, value):
        self.writes.append((event_type, code, value))

    def syn(self):
        self.writes.append(("syn",))

    def close(self):
        self.closed = True


@pytest.fixture
def fake_evdev(monkeypatch):
    FakeUInput.instances = []
    module = ModuleType("evdev")
    module.ecodes = SimpleNamespace(EV_KEY=1)
    module.UInput = FakeUInput
    uinput = ModuleType("evdev.uinput")
    uinput.UInputError = FakeUInputError
    monkeypatch.setitem(sys.modules, "evdev", module)
    monkeypatch.setitem(sys.modules, "evdev.uinput", uinput)
    return module


def keyd_26_skips(kwargs) -> bool:
    """Model of keyd v2.6.0 src/device.c: is_virtual = vendor == 0x0FAC."""
    return kwargs.get("vendor", 0x1) == KEYD_VIRTUAL_VENDOR


def keyd_25_skips(kwargs) -> bool:
    """Model of keyd 2.4.3/2.5.0 src/device.c: is_virtual = exact keyd name."""
    return kwargs.get("name") == KEYD_VKBD_NAME


class LoopbackTypist(RecordingTypist):
    """Replays uinput writes through keyd's virtual keyboard when grabbed."""

    ECHO_DEVICE = 2
    PHYSICAL_DEVICE = 1

    def __init__(self, ctrl, *, mirrored):
        super().__init__()
        self._ctrl = ctrl
        self.mirrored = mirrored
        self.pending: list[tuple[int, int, int]] = []

    def key(self, code, value):
        super().key(code, value)
        if self.mirrored:
            self.pending.append((self.ECHO_DEVICE, code, value))

    def physical(self, code, value):
        self.pending.append((self.PHYSICAL_DEVICE, code, value))

    def pump(self):
        pending, self.pending = self.pending, []
        for device, code, value in pending:
            self._ctrl.on_key(code, value, device)


def test_uinput_identity_is_skipped_by_keyd_26_and_keeps_public_identity(fake_evdev):
    typist = UInputTypist()
    kwargs = typist._ui.kwargs
    assert typist._ui.events == {1: list(range(1, 128))}
    assert kwargs["name"] == "WaySwitch Virtual Keyboard"
    assert kwargs["vendor"] == KEYD_VIRTUAL_VENDOR
    assert kwargs["product"] == 0x1
    # The previous library default 0001:0001 is matched by keyd's wildcard.
    assert keyd_26_skips({"vendor": 0x1, "product": 0x1}) is False
    assert keyd_26_skips(kwargs) is True
    # 2.4.3/2.5.0 exclude only their own exact name; this stays a manual step.
    assert keyd_25_skips(kwargs) is False


@pytest.mark.parametrize("old_identity", [True, False])
def test_keyd_26_no_longer_replays_first_backspace_into_correction(
        fake_evdev, caplog, old_identity):
    caplog.set_level("INFO", logger="wayswitch")
    real = UInputTypist()
    if old_identity:
        real._ui.kwargs["vendor"] = 0x1
    mirrored = not keyd_26_skips(real._ui.kwargs)
    state = controller_env.__wrapped__()
    ctrl = state["ctrl"]
    ctrl.config.general.early_switch = False
    typist = LoopbackTypist(ctrl, mirrored=mirrored)
    state["typist"] = ctrl.typist = typist
    ctrl.pump = typist.pump

    type_text(state, "ghbdtn ")

    # Before the identity change this replay aborted after the first Backspace
    # with reason=dict-b, failed: one trailing character erased, no replacement.
    if old_identity:
        assert ctrl.stats["corrections"] == 0
        assert typed_text(state) == "\b"
        assert "dict-b: failed" in caplog.text
    else:
        assert ctrl.stats["corrections"] == 1
        assert typed_text(state) == "\b" * 7 + "привет "


def test_physical_backspace_during_correction_still_aborts(fake_evdev):
    real = UInputTypist()
    assert keyd_26_skips(real._ui.kwargs)

    class Interrupting(LoopbackTypist):
        def __init__(self, ctrl):
            super().__init__(ctrl, mirrored=False)
            self._injected = False

        def key(self, code, value):
            super().key(code, value)
            if code == kc.KEY_BACKSPACE and value == 0 and not self._injected:
                self._injected = True
                self.physical(kc.KEY_BACKSPACE, 1)
                self.physical(kc.KEY_BACKSPACE, 0)

    state = controller_env.__wrapped__()
    ctrl = state["ctrl"]
    ctrl.config.general.early_switch = False
    typist = Interrupting(ctrl)
    state["typist"] = ctrl.typist = typist
    ctrl.pump = typist.pump

    type_text(state, "ghbdtn ")

    # A real Backspace from the user still stops reprinting after one deletion.
    assert ctrl.stats["corrections"] == 0
    assert state["corrected"] == []
    assert typed_text(state) == "\b"
