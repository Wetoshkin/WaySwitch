import pytest

from tests.fakes import FakeBackend, RecordingTypist
from wayswitch import editable
from wayswitch.backends import gnome_shell


@pytest.mark.parametrize("version", [None, 1, 3])
def test_old_loaded_extension_uses_keyboard_and_native_spans(monkeypatch, version):
    monkeypatch.setattr(gnome_shell, "extension_version", lambda: version)
    keys = RecordingTypist()
    assert gnome_shell.make_typist(FakeBackend(), keys, "extension") is keys
    assert keys.replace_span is editable.try_replace


@pytest.mark.parametrize("version", [4, 5])
def test_ordered_extension_uses_shell_typist(monkeypatch, version):
    monkeypatch.setattr(gnome_shell, "extension_version", lambda: version)
    backend, keys = FakeBackend(), RecordingTypist()
    typist = gnome_shell.make_typist(backend, keys, "extension")
    assert isinstance(typist, gnome_shell.ShellTypist)
    assert typist._backend is backend and typist._keys is keys


def test_explicit_keyboard_mode_keeps_native_spans_without_version_query(monkeypatch):
    def forbidden():
        raise AssertionError("Explicit keyboard mode does not need extension typing")

    monkeypatch.setattr(gnome_shell, "extension_version", forbidden)
    keys = RecordingTypist()
    assert gnome_shell.make_typist(FakeBackend(), keys, "uinput") is keys
    assert keys.replace_span is editable.try_replace
