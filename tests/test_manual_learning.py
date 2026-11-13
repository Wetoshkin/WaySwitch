import pytest

from tests.fakes import RU, US, FakeBackend, RecordingTypist, keys_for_text, make_keymap
from tests.test_review_fixes import type_word
from wayswitch.a11y import FocusSnapshot
from wayswitch.config import Config
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel
from wayswitch.learning import LearnedWords


class Probe:
    password = False
    field = ("editor", "/text")
    generation = 1

    def __init__(self, now):
        self.now = now

    def snapshot(self):
        return FocusSnapshot("editor", self.password, self.now[0],
                             field=self.field, generation=self.generation)

    def poke(self):
        pass


def environment(tmp_path, threshold=2):
    km, config, now = make_keymap(), Config(), [100.0]
    config.general.learn_after_converts = threshold
    config.general.auto_correct = False
    config.general.early_switch = False
    models = {lang: LanguageModel.load(lang) for lang in ("ru", "en")}
    store = LearnedWords(tmp_path / "learned_words.txt")
    backend, typist, probe = FakeBackend(current=RU), RecordingTypist(), Probe(now)
    ctrl = Controller(config, km, Detector(km, models), backend, typist,
                      clock=lambda: now[0], probe=probe, learned_words=store)
    return ctrl, store, probe, now


def convert(ctrl, word="ubuntu", target=US):
    source = RU if target == US else US
    ctrl.buffer.reset()
    ctrl._clear_context()
    ctrl.backend.set(source)
    text = ctrl.keymap.decode(keys_for_text(ctrl.keymap, word, target), source)
    type_word(ctrl, ctrl.keymap, text, source)
    assert ctrl.manual_word()


@pytest.mark.parametrize("threshold", [0, 1, 2, 5])
def test_only_threshold_successful_manual_conversions_learn(tmp_path, threshold):
    ctrl, store, probe, now = environment(tmp_path, threshold)
    for count in range(1, 6):
        convert(ctrl)
        assert store.path.exists() == (0 < threshold <= count)
    assert ctrl.stats["manual"] == 5
    assert ctrl.detector.models["en"].is_word("ubuntu") == (threshold > 0)


def test_exact_reverse_after_threshold_removes_created_word(tmp_path):
    ctrl, store, probe, now = environment(tmp_path)
    convert(ctrl)
    convert(ctrl)
    assert ("en", "ubuntu") in store.words
    assert ctrl.manual_word()
    assert not store.words
    assert not ctrl.detector.models["en"].is_word("ubuntu")
    convert(ctrl)
    assert ("en", "ubuntu") in store.words


def test_reverse_before_threshold_reverts_count(tmp_path):
    ctrl, store, probe, now = environment(tmp_path)
    convert(ctrl)
    assert ctrl.manual_word()
    convert(ctrl)
    assert not store.path.exists()


@pytest.mark.parametrize("private", ["password", "unknown", "missing_field"])
def test_private_or_unconfirmed_fields_never_learn(tmp_path, private):
    ctrl, store, probe, now = environment(tmp_path, 1)
    if private == "password":
        probe.password = True
    elif private == "unknown":
        ctrl.set_probe(None)
    else:
        probe.field = None
    convert(ctrl)
    assert not store.path.exists()


def test_exception_and_known_base_word_do_not_learn(tmp_path):
    ctrl, store, probe, now = environment(tmp_path, 1)
    ctrl.detector.exceptions.add("гигтег")
    convert(ctrl)
    assert not store.path.exists()
    ctrl.detector.exceptions.clear()
    convert(ctrl, "hello")
    assert not store.path.exists()


def test_reverse_does_not_remove_preexisting_personal_word(tmp_path):
    ctrl, store, probe, now = environment(tmp_path, 1)
    store.update(add=("en", "ubuntu"))
    convert(ctrl)
    assert ctrl.manual_word()
    assert store.words == {("en", "ubuntu")}


def test_failed_or_disrupted_manual_operation_never_learns(tmp_path):
    ctrl, store, probe, now = environment(tmp_path, 1)
    pending = []
    ctrl.schedule = pending.append
    convert(ctrl)
    probe.generation += 1
    pending.pop()()
    assert not store.path.exists()


def test_storage_failure_does_not_break_successful_typing(tmp_path, monkeypatch):
    ctrl, store, probe, now = environment(tmp_path, 1)

    def fail(**kwargs):
        raise OSError("do not log word")

    monkeypatch.setattr(store, "update", fail)
    convert(ctrl)
    assert ctrl.stats["manual"] == 1
    assert not ctrl.detector.models["en"].is_word("ubuntu")


def test_counts_survive_undo_window_but_expire_after_seven_days(tmp_path):
    ctrl, store, probe, now = environment(tmp_path)
    convert(ctrl)
    now[0] += 24 * 3600
    convert(ctrl)
    assert ("en", "ubuntu") in store.words
    ctrl, store, probe, now = environment(tmp_path / "expired")
    convert(ctrl)
    now[0] += 8 * 24 * 3600
    convert(ctrl)
    assert not store.path.exists()


def test_reverse_after_another_edit_cannot_delete_dictionary_word(tmp_path):
    ctrl, store, probe, now = environment(tmp_path, 1)
    convert(ctrl)
    ctrl.on_click()
    ctrl.backend.set(US)
    type_word(ctrl, ctrl.keymap, "ubuntu", US)
    ctrl.manual_word()
    assert ("en", "ubuntu") in store.words


def test_excluded_personal_target_blocks_all_automatic_rules(tmp_path):
    ctrl, store, probe, now = environment(tmp_path)
    store.update(add=("en", "ubuntu"))
    ctrl.detector.exceptions.add("ubuntu")
    ctrl.config.general.auto_correct = True
    ctrl.config.general.early_switch = True
    type_word(ctrl, ctrl.keymap, "гигтег ", RU)
    assert ctrl.typist.taps() == []
    assert ctrl.backend.current() == RU


def test_external_dictionary_edit_is_applied_on_next_word(tmp_path):
    ctrl, store, probe, now = environment(tmp_path)
    LearnedWords(store.path).update(add=("en", "ubuntu"))
    ctrl.config.general.auto_correct = True
    type_word(ctrl, ctrl.keymap, "гигтег ", RU)
    assert ctrl.backend.current() == US
    assert ctrl.typist.taps()


def test_automatic_and_phrase_corrections_do_not_learn(tmp_path):
    ctrl, store, probe, now = environment(tmp_path, 1)
    ctrl.backend.set(US)
    ctrl.config.general.auto_correct = True
    type_word(ctrl, ctrl.keymap, "ghbdtn ", US)
    assert ctrl.stats["corrections"] == 1
    assert not store.path.exists()
    ctrl.config.general.auto_correct = False
    ctrl.buffer.reset()
    ctrl._clear_context()
    ctrl.backend.set(RU)
    type_word(ctrl, ctrl.keymap, "гигтег ", RU)
    assert ctrl.manual_phrase()
    assert not store.path.exists()


def test_exact_reverse_after_external_edit_preserves_all_words(tmp_path):
    ctrl, store, probe, now = environment(tmp_path, 1)
    convert(ctrl)
    LearnedWords(store.path).update(add=("ru", "вайб"))
    assert ctrl.manual_word()
    assert store.words == {("en", "ubuntu"), ("ru", "вайб")}


def test_no_word_payload_in_learning_error_log(tmp_path, monkeypatch, caplog):
    ctrl, store, probe, now = environment(tmp_path, 1)

    def fail(**kwargs):
        raise OSError("ubuntu гигтег")

    monkeypatch.setattr(store, "update", fail)
    convert(ctrl)
    assert "ubuntu" not in caplog.text and "гигтег" not in caplog.text
    assert "OSError" in caplog.text


def test_focus_change_while_preparing_dictionary_write_cancels_learning(tmp_path, monkeypatch):
    ctrl, store, probe, now = environment(tmp_path, 1)
    original = store._read

    def changed():
        result = original()
        probe.generation += 1
        return result

    monkeypatch.setattr(store, "_read", changed)
    convert(ctrl)
    assert not store.path.exists()
    assert not ctrl.detector.models["en"].is_word("ubuntu")


def test_count_cache_is_bounded(tmp_path, monkeypatch):
    import wayswitch.controller as controller

    monkeypatch.setattr(controller, "COUNT_LIMIT", 2)
    ctrl, store, probe, now = environment(tmp_path, 5)
    for word in ("ubuntufoo", "ubuntubar", "ubuntubaz"):
        convert(ctrl, word)
    assert len(ctrl._learn_counts) == 2
    assert ("en", "ubuntufoo") not in ctrl._learn_counts


def test_learning_can_be_disabled_before_pending_manual_write(tmp_path):
    ctrl, store, probe, now = environment(tmp_path, 1)
    pending = []
    ctrl.schedule = pending.append
    convert(ctrl)
    ctrl.config.general.learn_after_converts = 0
    pending.pop()()
    assert not store.path.exists()


def test_learning_scope_rechecks_protection_of_the_same_snapshot(tmp_path, monkeypatch):
    ctrl, store, probe, now = environment(tmp_path, 1)
    safe = probe.snapshot()
    snapshots = iter([safe, safe._replace(password=True)])
    monkeypatch.setattr(probe, "snapshot", lambda: next(snapshots))
    assert ctrl._learning_scope() is None


def test_unrelated_reload_does_not_discard_learning_progress(tmp_path):
    import copy

    ctrl, store, probe, now = environment(tmp_path)
    convert(ctrl)
    config = copy.deepcopy(ctrl.config)
    config.general.sound_on_fix = True
    ctrl.reload_config(config)
    convert(ctrl)
    assert store.words == {("en", "ubuntu")}


@pytest.mark.parametrize("threshold", [1, 2])
def test_triple_shift_phrase_never_teaches_its_intermediate_word(tmp_path, threshold):
    ctrl, store, probe, now = environment(tmp_path, threshold)
    if threshold == 2:
        convert(ctrl)
    ctrl.buffer.reset()
    ctrl._clear_context()
    ctrl.backend.set(RU)
    type_word(ctrl, ctrl.keymap, "руддщ гигтег", RU)
    assert ctrl.manual_word(from_gesture=True)
    assert not store.words
    assert ctrl.manual_phrase(from_gesture=True)
    now[0] += 1.0
    ctrl._flush_pending_learning()
    assert not store.path.exists()
    convert(ctrl)
    assert store.words == {("en", "ubuntu")}


def test_double_shift_learning_waits_until_gesture_series_is_complete(tmp_path):
    ctrl, store, probe, now = environment(tmp_path, 1)
    pending = []
    ctrl.schedule_later = lambda delay, callback: pending.append(callback)
    type_word(ctrl, ctrl.keymap, "гигтег", RU)
    assert ctrl.manual_word(from_gesture=True)
    assert not store.path.exists()
    now[0] += 0.5
    pending.pop()()
    assert store.words == {("en", "ubuntu")}
