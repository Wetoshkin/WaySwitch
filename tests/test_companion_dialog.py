"""Ошибки помощника установки без GTK и запуска пакетного менеджера."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from wayswitch import companion
from wayswitch.gui import companion_dialog


class Widget:
    def __init__(self, **kwargs):
        self.label = kwargs.get("label", "")
        self.sensitive = kwargs.get("sensitive", True)
        self.callbacks = {}
        self.children = []

    def connect(self, signal, callback):
        self.callbacks[signal] = callback

    def emit(self, signal, *args):
        self.callbacks[signal](self, *args)

    def set_label(self, label):
        self.label = label

    def set_sensitive(self, value):
        self.sensitive = value

    def append(self, child):
        self.children.append(child)

    set_child = set_content = add_top_bar = append

    def add_css_class(self, _name):
        pass

    def present(self, _parent):
        pass

    def add_response(self, _name, _label):
        pass

    set_close_response = set_default_response = add_css_class


@pytest.mark.parametrize("action", ["refresh", "confirm"])
def test_failed_recheck_clears_previous_package_instructions(monkeypatch, en_locale, action):
    labels, buttons, alerts, pending, launched = [], [], [], [], []

    def tracked(collection):
        def create(**kwargs):
            widget = Widget(**kwargs)
            collection.append(widget)
            return widget
        return create

    repository = ModuleType("gi.repository")
    repository.Adw = SimpleNamespace(Dialog=Widget, ToolbarView=Widget, HeaderBar=Widget,
                                     AlertDialog=tracked(alerts))
    repository.Gtk = SimpleNamespace(
        ScrolledWindow=Widget, Box=Widget, Label=tracked(labels), Button=tracked(buttons),
        Orientation=SimpleNamespace(VERTICAL=0), PolicyType=SimpleNamespace(NEVER=0))
    repository.Gdk = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "gi", ModuleType("gi"))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    monkeypatch.setattr(companion_dialog, "run_in_thread", lambda work, done: pending.append(done))
    monkeypatch.setattr(companion, "handoff", lambda *a, **kw: launched.append(True))

    companion_dialog.show(object())
    info = companion.Package("Debian", "apt", candidate="1.1.4-1",
                             command="sudo apt-get install wayswitch",
                             automatic=True, message="Compatible package")
    pending.pop(0)(info)
    status, details, command = labels[:3]
    refresh, install, copy = buttons
    assert command.label == info.command and install.sensitive and copy.sensitive
    if action == "refresh":
        refresh.emit("clicked")
    else:
        install.emit("clicked")
        alerts[-1].emit("response", "install")
    pending.pop(0)(OSError("offline"))

    assert "Could not check" in status.label
    assert details.label == command.label == ""
    assert not install.sensitive and not copy.sensitive
    assert not launched
