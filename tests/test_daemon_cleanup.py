"""Daemon owns the focus probe and releases it on both startup and loop exits."""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from wayswitch import daemon
from wayswitch.config import Config


class Probe:
    def __init__(self):
        self.pokes = 0
        self.closed = 0
        self.close_error = None

    def poke(self):
        self.pokes += 1

    def close(self):
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


class Typist:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class Backend:
    name = "fake"
    supports_auto = True

    def __init__(self):
        self.closed = 0
        self.callbacks = []

    def layouts(self):
        return []

    def xkb_options(self):
        return []

    def on_change(self, callback):
        self.callbacks.append(callback)

    def close(self):
        self.closed += 1


class Watcher:
    def __init__(self, devices):
        self._devices = devices
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def devices(self):
        return self._devices

    def stop(self):
        self.stopped += 1


class Controller:
    def __init__(self, config, *_args, **_kwargs):
        self.config = config
        self.buffer = SimpleNamespace(reset=lambda: None)
        self.on_layout_changed = lambda *_: None

    def on_key(self, *_args):
        pass

    def on_click(self, *_args):
        pass

    def on_resync(self, *_args):
        pass

    def on_device_gone(self, *_args):
        pass

    def set_caps(self, *_args):
        pass

    def on_scroll(self, *_args):
        pass

    def set_locked(self, *_args):
        pass

    def reload_config(self, _config):
        pass


class Guard:
    def __init__(self, *_args, **_kwargs):
        self.started = 0

    def start(self):
        self.started += 1

    def input_locked(self):
        return False


class Service:
    def __init__(self, *_args, **_kwargs):
        self.started = 0

    def start(self):
        self.started += 1


class Monitor:
    def connect(self, *_args):
        pass


@pytest.fixture
def environment(monkeypatch):
    probe = Probe()
    typist = Typist()
    backend = Backend()
    state = SimpleNamespace(devices=[("event0", "keyboard", "keyboard")],
                            probe=probe, typist=typist, backend=backend)
    watchers = []

    def watcher_factory(*_args, **_kwargs):
        watcher = Watcher(state.devices)
        watchers.append(watcher)
        return watcher

    def main_loop():
        return SimpleNamespace(run=lambda: None, quit=lambda: None)

    class MainContext:
        @staticmethod
        def default():
            return SimpleNamespace(pending=lambda: False, iteration=lambda _blocking: None)

    glib = SimpleNamespace(MainContext=MainContext, MainLoop=main_loop,
                           PRIORITY_HIGH=0, unix_signal_add=lambda *_args: 0)
    gio = SimpleNamespace(
        File=SimpleNamespace(new_for_path=lambda _path: SimpleNamespace(
            monitor_file=lambda *_args: Monitor())),
        FileMonitorFlags=SimpleNamespace(NONE=0),
        FileMonitorEvent=SimpleNamespace(CHANGED=1, CHANGES_DONE_HINT=2),
    )
    repository = ModuleType("gi.repository")
    repository.GLib = glib
    repository.Gio = gio
    monkeypatch.setitem(sys.modules, "gi", ModuleType("gi"))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)

    monkeypatch.setattr(daemon.cfgmod, "load", lambda _path: Config())
    monkeypatch.setattr(daemon.cfgmod, "load_exceptions", set)
    monkeypatch.setattr(daemon.cfgmod, "default_path", lambda: Path("config.toml"))
    monkeypatch.setattr(daemon.cfgmod_a11y, "make_probe", lambda enabled: probe)
    monkeypatch.setattr(daemon, "_open_uinput", lambda: typist)
    monkeypatch.setattr(daemon, "choose_backend", lambda *_args, **_kwargs: backend)
    monkeypatch.setattr(daemon, "detect_desktop", lambda: "gnome")
    monkeypatch.setattr(daemon, "XkbKeymap", SimpleNamespace(
        from_names=lambda *_args: SimpleNamespace(num_groups=0)))
    monkeypatch.setattr(daemon, "LanguageModel", SimpleNamespace(load=lambda _lang: object()))
    monkeypatch.setattr(daemon, "Detector", lambda *_args: object())
    monkeypatch.setattr(daemon, "Controller", Controller)
    monkeypatch.setattr(daemon, "DeviceWatcher", watcher_factory)
    monkeypatch.setattr(daemon, "SessionGuard", Guard)
    monkeypatch.setattr(daemon, "DaemonService", Service)
    return SimpleNamespace(state=state, watchers=watchers)


def test_startup_without_keyboards_closes_probe_and_other_resources(environment):
    environment.state.devices = []
    assert daemon.run(None, False, False) == 1
    assert environment.state.probe.closed == 1
    watcher = environment.watchers[0]
    assert watcher.stopped == 1
    assert environment.state.backend.closed == 1
    assert environment.state.typist.closed == 1


def test_ordinary_loop_exit_closes_probe_without_waiting(environment):
    assert daemon.run(None, False, False) == 0
    assert environment.state.probe.closed == 1
    watcher = environment.watchers[0]
    assert watcher.started == 1 and watcher.stopped == 1
    assert environment.state.backend.closed == 1
    assert environment.state.typist.closed == 1


@pytest.mark.parametrize("keyboard", [False, True])
def test_probe_close_failure_does_not_block_other_cleanup(environment, keyboard, caplog):
    environment.state.devices = [] if not keyboard else [("event0", "keyboard", "keyboard")]
    environment.state.probe.close_error = RuntimeError("private provider payload")
    assert daemon.run(None, False, False) == (0 if keyboard else 1)
    assert environment.state.probe.closed == 1
    watcher = environment.watchers[0]
    assert watcher.stopped == 1
    assert environment.state.backend.closed == 1
    assert environment.state.typist.closed == 1
    assert "private provider payload" not in caplog.text
