import os

import pytest

from wayswitch.learning import LearnedWords, normalize_word


def test_dictionary_is_created_only_on_explicit_add(tmp_path):
    store = LearnedWords(tmp_path / "learned_words.txt")
    assert not store.path.exists()
    assert store.words == frozenset()
    assert store.update(add=("en", "Ubuntu"))
    assert store.words == {("en", "ubuntu")}
    assert store.path.read_text(encoding="utf-8") == "en\tubuntu\n"
    assert not store.update(add=("en", "ubuntu"))


@pytest.mark.parametrize("lang,word", [
    ("fr", "bonjour"), ("en", ""), ("en", "a" * 65), ("en", "two words"),
    ("ru", "hello"), ("en", "hello\tworld"), ("en", "hello\n"),
    ("en", "-word"), ("en", "word-"), ("en", "word\x00"),
])
def test_invalid_personal_words_are_rejected(lang, word):
    with pytest.raises(ValueError):
        normalize_word(lang, word)


def test_words_are_nfc_and_keep_yo():
    assert normalize_word("ru", "Е\u0308ЛКА") == "ёлка"
    assert normalize_word("en", "Don't") == "don't"


def test_malformed_lines_are_skipped_but_never_silently_rewritten(tmp_path):
    path = tmp_path / "words"
    original = "# comment\nen\tubuntu\ninvalid\nru\tвайб\n"
    path.write_text(original, encoding="utf-8")
    store = LearnedWords(path)
    assert store.words == {("en", "ubuntu"), ("ru", "вайб")}
    with pytest.raises(ValueError):
        store.update(add=("en", "backend"))
    assert path.read_text(encoding="utf-8") == original


def test_writers_reread_and_merge_other_edits(tmp_path):
    path = tmp_path / "words"
    a, b = LearnedWords(path), LearnedWords(path)
    a.update(add=("en", "ubuntu"))
    b.update(add=("ru", "вайб"))
    assert b.words == {("en", "ubuntu"), ("ru", "вайб")}
    a.update(remove=("en", "ubuntu"), add=("en", "backend"))
    assert a.words == {("en", "backend"), ("ru", "вайб")}
    assert path.with_suffix(".bak").read_text(encoding="utf-8") == "en\tubuntu\nru\tвайб\n"


def test_reverse_refuses_to_delete_after_external_change(tmp_path):
    a = LearnedWords(tmp_path / "words")
    a.update(add=("en", "ubuntu"))
    digest = a.digest
    a.path.write_text("en\tubuntu\nru\tвайб\n", encoding="utf-8")
    with pytest.raises(ValueError):
        a.update(remove=("en", "ubuntu"), expected=digest)
    assert LearnedWords(a.path).words == {("en", "ubuntu"), ("ru", "вайб")}


def test_failed_replace_preserves_file_and_memory(tmp_path, monkeypatch):
    store = LearnedWords(tmp_path / "words")
    store.update(add=("en", "ubuntu"))

    def fail(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        store.update(add=("ru", "вайб"))
    assert store.words == {("en", "ubuntu")}
    assert LearnedWords(store.path).words == store.words
    assert not store.path.with_suffix(".lock").exists()


def test_external_change_between_read_and_replace_is_rejected(tmp_path, monkeypatch):
    store = LearnedWords(tmp_path / "words")
    store.update(add=("en", "ubuntu"))
    original = store._atomic_write

    def external_edit(path, data):
        original(path, data)
        if path == store.path.with_suffix(".bak"):
            store.path.write_text("en\tbackend\n", encoding="utf-8")

    monkeypatch.setattr(store, "_atomic_write", external_edit)
    with pytest.raises(ValueError):
        store.update(add=("ru", "вайб"))
    assert store.path.read_text(encoding="utf-8") == "en\tbackend\n"


def test_cooperating_writer_lock_prevents_overwrite(tmp_path):
    store = LearnedWords(tmp_path / "words")
    store.path.with_suffix(".lock").touch()
    with pytest.raises(FileExistsError):
        store.update(add=("en", "ubuntu"))
    assert not store.path.exists()


def test_refresh_preserves_last_valid_model_on_read_error(tmp_path, monkeypatch):
    store = LearnedWords(tmp_path / "words")
    store.update(add=("en", "ubuntu"))

    def denied():
        raise PermissionError("denied")

    monkeypatch.setattr(store, "_read", denied)
    assert not store.reload(force=True)
    assert store.words == {("en", "ubuntu")}
    assert store.error == "PermissionError"


def test_limit_and_fingerprint_include_content(tmp_path, monkeypatch):
    import wayswitch.learning as learning

    store = LearnedWords(tmp_path / "words")
    store.update(add=("en", "ubuntu"))
    stamp = store.path.stat()
    store.path.write_text("en\tfoobar\n", encoding="utf-8")
    os.utime(store.path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(ValueError):
        store.update(remove=("en", "ubuntu"), expected=store.digest)
    monkeypatch.setattr(learning, "MAX_WORDS", 1)
    with pytest.raises(ValueError):
        store.update(add=("ru", "вайб"))


def test_refresh_observes_external_edit_and_deletion(tmp_path):
    store = LearnedWords(tmp_path / "words")
    store.path.write_text("ru\tвайб\n", encoding="utf-8")
    assert store.reload()
    assert store.words == {("ru", "вайб")}
    store.path.unlink()
    assert store.reload()
    assert not store.words


def test_personal_words_rebuild_all_indices_without_changing_frequencies():
    from wayswitch.detector import LanguageModel
    from wayswitch.keymap import CYRILLIC

    base = LanguageModel.from_words("ru", [("дом", 1)], "".join(CYRILLIC))
    assert not base.has_prefix("ёл")
    assert not base.endings
    model = base.with_extra_words(["ёлка", "ёлками", "вайб"])
    assert model.is_word("елка") and model.has_prefix("елк")
    assert model.prefix_count("елк") == 2
    assert model.looks_like_word("вайбу")
    assert model.top == base.top and model.model is base.model
    assert not base.is_word("вайб")


def test_personal_target_overrides_dictionary_but_not_exceptions():
    from tests.fakes import RU, US, keys_for_text, make_keymap
    from wayswitch.detector import Detector, LanguageModel

    km = make_keymap()
    models = {lang: LanguageModel.load(lang) for lang in ("ru", "en")}
    models["ru"] = models["ru"].with_extra_words(["гигтег"])
    det = Detector(km, models)
    keys = keys_for_text(km, "ubuntu", US)
    assert det.hypothesis(keys, RU).in_dict
    det.set_personal_words({("en", "ubuntu")})
    d = det.decide(keys, RU, US)
    assert (d.action, d.target_text, d.reason) == ("fix", "ubuntu", "personal")
    assert not det.models["ru"].is_word("ubuntu")
    det.exceptions.add(km.decode(keys, RU))
    assert det.decide(keys, RU, US).action == "keep"
    det.exceptions.clear()
    det.set_personal_words(set())
    assert not det.models["en"].is_word("ubuntu")


def test_learning_setting_schema_and_validation(tmp_path):
    from wayswitch import config

    assert config.Config().general.learn_after_converts == 0
    for threshold in (0, 1, 2, 5):
        cfg = config.from_dict({"meta": {"schema": 1},
                                "general": {"learn_after_converts": threshold}})
        path = tmp_path / "config.toml"
        config.save(cfg, path)
        assert config.load(path).general.learn_after_converts == threshold
        assert config.to_dict(cfg)["meta"]["schema"] == config.CONFIG_SCHEMA
    for bad in (-1, 6, True, "2"):
        with pytest.raises(config.ConfigError):
            config.from_dict({"general": {"learn_after_converts": bad}})


def test_gui_edit_is_atomic_and_failure_does_not_reload_daemon(tmp_path, monkeypatch):
    from wayswitch.gui.preferences import Preferences

    store = LearnedWords(tmp_path / "words")
    store.update(add=("en", "ubuntu"))
    prefs = object.__new__(Preferences)
    prefs.personal_store = store
    calls, notices = [], []
    prefs.proxy = type("Proxy", (), {"call": lambda _self, name: calls.append(name)})()
    prefs._render_personal = lambda: None
    prefs.toast = notices.append
    assert prefs._change_personal(remove=("en", "ubuntu"), add=("en", "backend"))
    assert store.words == {("en", "backend")}


    assert calls == ["Reload"]

    def denied(**kwargs):
        raise PermissionError("do not print words")

    monkeypatch.setattr(store, "update", denied)
    assert not prefs._change_personal(remove=("en", "backend"))
    assert calls == ["Reload"] and notices
    assert store.words == {("en", "backend")}


def test_schema_upgrade_keeps_original_configuration_for_downgrade(tmp_path):
    from wayswitch import config

    path = tmp_path / "config.toml"
    original = b'[meta]\nschema = 1\n[general]\nlearn_after_undos = 3\n'
    path.write_bytes(original)
    cfg = config.load(path)
    cfg.general.learn_after_converts = 2
    config.save(cfg, path)
    backup = path.with_name(path.name + ".before-schema-2")
    assert backup.read_bytes() == original
    cfg.general.learn_after_converts = 5
    config.save(cfg, path)
    assert backup.read_bytes() == original
