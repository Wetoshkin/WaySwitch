from pathlib import Path

import pytest

from wayswitch import config as cfgmod


def test_defaults_when_file_missing(tmp_path: Path):
    cfg = cfgmod.load(tmp_path / "nope.toml")
    assert cfg.general.auto_correct is True
    assert cfg.general.sensitivity == "normal"
    assert cfg.typing.settle_ms == 120
    assert cfg.backend.prefer == "auto"
    assert cfg.backend.shell_typing == "extension"


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


def test_shell_typing_validation(tmp_path: Path):
    p = tmp_path / "c.toml"
    p.write_text('[backend]\nshell_typing = "uinput"\n', encoding="utf-8")
    assert cfgmod.load(p).backend.shell_typing == "uinput"

    p.write_text('[backend]\nshell_typing = "magic"\n', encoding="utf-8")
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.load(p)


def test_tap_key_validation(tmp_path: Path):
    p = tmp_path / "c.toml"
    p.write_text('[gesture]\nmanual = "tap_key"\ntap_key = "scrolllock"\n', encoding="utf-8")
    assert cfgmod.load(p).gesture.tap_key == "scrolllock"

    p.write_text('[gesture]\nmanual = "tap_key"\ntap_key = "нетакойклавиши"\n', encoding="utf-8")
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.load(p)


def test_old_default_settle_ms_is_migrated():
    from wayswitch.config import from_dict

    assert from_dict({"typing": {"settle_ms": 30}}).typing.settle_ms == 120
    assert from_dict({"typing": {"settle_ms": 200}}).typing.settle_ms == 200


def test_legacy_file_migrates_once_and_saves_marker(tmp_path: Path):
    path = tmp_path / "legacy.toml"
    path.write_text("[typing]\nsettle_ms = 30\n", encoding="utf-8")

    assert cfgmod.load(path).typing.settle_ms == 120
    assert cfgmod.load(path).typing.settle_ms == 120

    cfgmod.save(cfgmod.load(path), path)
    text = path.read_text(encoding="utf-8")
    assert "[meta]" in text and "schema = 1" in text
    assert cfgmod.load(path).typing.settle_ms == 120


@pytest.mark.parametrize("settle", [0, 30, 45, 119, 120, 2000])
def test_saved_settle_ms_survives_repeated_save_and_load(tmp_path: Path, settle: int):
    path = tmp_path / "c.toml"
    cfg = cfgmod.Config()
    cfg.typing.settle_ms = settle
    cfgmod.save(cfg, path)

    assert cfgmod.load(path).typing.settle_ms == settle
    cfgmod.save(cfgmod.load(path), path)
    assert cfgmod.load(path).typing.settle_ms == settle


def test_current_marker_keeps_default_and_explicit_values():
    assert cfgmod.from_dict({"typing": {}}).typing.settle_ms == 120
    marked = cfgmod.to_dict(cfgmod.Config())
    assert marked[cfgmod._SCHEMA_SECTION][cfgmod._SCHEMA_KEY] == cfgmod.CONFIG_SCHEMA
    assert cfgmod.from_dict(marked).typing.settle_ms == 120

    cfg = cfgmod.Config()
    cfg.typing.settle_ms = 30
    assert cfgmod.from_dict(cfgmod.to_dict(cfg)).typing.settle_ms == 30


def test_to_dict_roundtrip_and_unknown_top_level_section():
    cfg = cfgmod.Config()
    cfg.typing.settle_ms = 30
    data = cfgmod.to_dict(cfg)
    data["future"] = {"x": 1}
    assert cfgmod.from_dict(data) == cfg


@pytest.mark.parametrize("marker", [
    {"schema": 2},
    {"schema": 0},
    {"schema": -1},
    {"schema": "1"},
    {"schema": True},
    {},
    {"schema": 1, "extra": 1},
    [],
])
def test_invalid_or_future_marker_is_rejected(marker):
    with pytest.raises(cfgmod.ConfigSchemaError):
        cfgmod.from_dict({"meta": marker})


def test_save_does_not_overwrite_unknown_schema(tmp_path: Path):
    path = tmp_path / "future.toml"
    original = "[meta]\nschema = 2\n\n[typing]\nsettle_ms = 30\n"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(cfgmod.ConfigSchemaError):
        cfgmod.save(cfgmod.Config(), path)

    assert path.read_text(encoding="utf-8") == original


def test_lists_in_foreign_encoding_do_not_crash(tmp_path):
    from wayswitch.config import load_apps, load_exceptions

    path = tmp_path / "apps.txt"
    path.write_bytes("телеграм\n".encode("cp1251"))
    assert len(load_apps(path)) == 1
    assert len(load_exceptions(path)) == 1


def test_selection_key_conflict_matches_validation():
    from wayswitch.config import Gesture, selection_key_conflict

    g = Gesture()  # двойной Shift
    assert selection_key_conflict(g, "leftshift")
    assert selection_key_conflict(g, "rightctrl") is None
    assert selection_key_conflict(g, "pause", manual="pause_key")
    g.tap_key, g.pause_hotkey = "rightctrl", "scrolllock"
    assert selection_key_conflict(g, "rightctrl", manual="tap_key")
    assert selection_key_conflict(g, "scrolllock")


def test_save_preserves_config_when_existing_schema_cannot_be_read(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    original = "[meta]\nschema = 2\n"
    path.write_text(original, encoding="utf8")

    def denied(*args, **kwargs):
        raise PermissionError("unreadable config")

    monkeypatch.setattr(cfgmod, "open", denied, raising=False)
    with pytest.raises(PermissionError):
        cfgmod.save(cfgmod.Config(), path)
    assert path.read_text(encoding="utf8") == original
    assert not path.with_suffix(".tmp").exists()
