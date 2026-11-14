import pytest

from wayswitch import config


@pytest.mark.parametrize('name', ['a', '1', 'space', 'enter', 'esc', 'insert', 'printscreen',
                                  'leftctrl', *[f'f{i}' for i in range(1, 25)]])
def test_pause_hotkey_rejects_typing_and_command_keys(name):
    with pytest.raises(config.ConfigError, match='pause_hotkey'):
        config.from_dict({'gesture': {'pause_hotkey': name}})


@pytest.mark.parametrize('name', ['pause', 'scrolllock', 'scroll_lock', 'SCROLL_LOCK', ''])
def test_pause_hotkey_accepts_only_existing_pause_keys(name):
    assert config.from_dict({'gesture': {'pause_hotkey': name}}).gesture.pause_hotkey == name


def test_pause_key_gesture_cannot_share_pause_toggle(tmp_path):
    with pytest.raises(config.ConfigError, match='pause'):
        config.from_dict({'gesture': {'manual': 'pause_key', 'pause_hotkey': 'pause'}})
    path = tmp_path / 'config.toml'
    original = b'[gesture]\npause_hotkey="a"\n'
    path.write_bytes(original)
    with pytest.raises(config.ConfigError):
        config.load(path)
    assert path.read_bytes() == original


def test_invalid_startup_config_has_no_traceback(monkeypatch, capsys):
    import sys
    from types import SimpleNamespace

    from wayswitch.cli import main
    def run(*_):
        raise config.ConfigError('pause_hotkey: pause, scrolllock')
    monkeypatch.setitem(sys.modules, 'wayswitch.daemon', SimpleNamespace(run=run))
    assert main(['run']) == 2
    assert 'pause_hotkey' in capsys.readouterr().err


def test_gui_rejects_pause_gesture_conflict_without_clearing_binding():
    from types import SimpleNamespace

    from wayswitch.gui.preferences import Preferences
    prefs = object.__new__(Preferences)
    prefs.cfg = config.Config()
    prefs.cfg.gesture.pause_hotkey = 'pause'
    prefs._loading = False
    selected, visible, messages = [], [], []
    prefs.cb_manual = SimpleNamespace(set_selected=selected.append)
    prefs.row_key = SimpleNamespace(set_visible=visible.append)
    prefs.toast = messages.append
    prefs._set_manual('pause_key')
    assert prefs.cfg.gesture.manual == 'double_shift'
    assert selected == [config.MANUAL_MODES.index('double_shift')]
    assert messages
