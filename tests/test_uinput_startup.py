import sys
from types import ModuleType, SimpleNamespace

import pytest

from wayswitch import daemon
from wayswitch.actuator import UInputTypist


class UInputError(Exception):
    pass


@pytest.fixture
def evdev(monkeypatch):
    module = ModuleType("evdev")
    module.ecodes = SimpleNamespace(EV_KEY=1)
    uinput = ModuleType("evdev.uinput")
    uinput.UInputError = UInputError
    monkeypatch.setitem(sys.modules, "evdev", module)
    monkeypatch.setitem(sys.modules, "evdev.uinput", uinput)
    return module


def test_evdev_verification_error_is_an_io_failure(evdev):
    def denied(*args, **kwargs):
        raise UInputError('"/dev/uinput" cannot be opened for writing')

    evdev.UInput = denied
    with pytest.raises(OSError, match="/dev/uinput"):
        UInputTypist()


@pytest.mark.parametrize("error", [UInputError("access denied"), PermissionError(13, "denied"),
                                 OSError(19, "No such device")])
def test_startup_waits_for_uinput_and_continues_without_restart(
        evdev, monkeypatch, caplog, error):
    opened = []
    sleeps = []
    device = object()

    def open_device(events, *, name):
        assert events == {1: list(range(1, 128))}
        assert name == "WaySwitch Virtual Keyboard"
        opened.append(True)
        if len(opened) <= 15:
            raise error
        return device

    evdev.UInput = open_device
    monkeypatch.setattr(daemon.time, "sleep", sleeps.append)
    typist = daemon._open_uinput()
    assert typist._ui is device
    assert len(sleeps) == 15 and all(delay > 0 for delay in sleeps)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "/dev/uinput" in warnings[0].message


def test_startup_does_not_hide_programming_errors(evdev):
    def broken(*args, **kwargs):
        raise ValueError("invalid capabilities")

    evdev.UInput = broken
    with pytest.raises(ValueError, match="invalid capabilities"):
        daemon._open_uinput()


def test_waiting_for_uinput_can_be_interrupted(evdev, monkeypatch):
    def denied(*args, **kwargs):
        raise UInputError("access denied")

    def interrupt(_delay):
        raise KeyboardInterrupt

    evdev.UInput = denied
    monkeypatch.setattr(daemon.time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        daemon._open_uinput()


def test_run_waits_before_initializing_backend(evdev, monkeypatch):
    repository = ModuleType("gi.repository")
    repository.GLib = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "gi", ModuleType("gi"))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    monkeypatch.setattr(daemon.cfgmod, "load", lambda _: daemon.cfgmod.Config())
    device = object()
    available = False

    def open_device(*args, **kwargs):
        if not available:
            raise UInputError("access denied")
        return device

    def restore_access(_delay):
        nonlocal available
        available = True

    class BackendReached(Exception):
        pass

    def backend(_prefer, typist, _settle, **kwargs):
        assert typist._ui is device and available
        raise BackendReached

    evdev.UInput = open_device
    monkeypatch.setattr(daemon.time, "sleep", restore_access)
    monkeypatch.setattr(daemon, "choose_backend", backend)
    with pytest.raises(BackendReached):
        daemon.run(None, False, False)
