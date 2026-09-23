from pathlib import Path

import pytest

from wayswitch import config as cfgmod


def test_defaults_when_file_missing(tmp_path: Path):
    cfg = cfgmod.load(tmp_path / "nope.toml")
    assert cfg.general.auto_correct is True
    assert cfg.general.sensitivity == "normal"
    assert cfg.typing.settle_ms == 30
    assert cfg.backend.prefer == "auto"


def test_round_trip(tmp_path: Path):
    cfg = cfgmod.load(None if False else tmp_path / "c.toml")
    cfg.general.sensitivity = "aggressive"
    cfg.gesture.pause_hotkey = "scrolllock"
    cfg.typing.settle_ms = 45
    cfgmod.save(cfg, tmp_path / "c.toml")
    text = (tmp_path / "c.toml").read_text(encoding="utf-8")
    assert '[general]' in text and 'sensitivity = "aggressive"' in text
    again = cfgmod.load(tmp_path / "c.toml")
    assert again == cfg


def test_invalid_values_raise():
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.from_dict({"general": {"sensitivity": "wild"}})
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.from_dict({"typing": {"settle_ms": -1}})
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.from_dict({"gesture": {"pause_hotkey": "nosuchkey"}})
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.from_dict({"general": {"unknown_key": 1}})


def test_pause_hotkey_accepts_documented_spelling():
    cfg = cfgmod.from_dict({"gesture": {"pause_hotkey": "scroll_lock"}})
    assert cfg.gesture.pause_hotkey == "scroll_lock"


def test_unknown_section_ignored_but_unknown_key_rejected():
    cfg = cfgmod.from_dict({"future": {"x": 1}})
    assert cfg.general.auto_correct is True


def test_exceptions_file(tmp_path: Path):
    p = tmp_path / "exceptions.txt"
    assert cfgmod.load_exceptions(p) == set()
    cfgmod.add_exception("Ghbdtn", p)
    cfgmod.add_exception("hello", p)
    cfgmod.add_exception("hello", p)
    assert cfgmod.load_exceptions(p) == {"ghbdtn", "hello"}
    cfgmod.remove_exception("HELLO", p)
    assert cfgmod.load_exceptions(p) == {"ghbdtn"}
