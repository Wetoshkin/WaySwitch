import logging

import pytest

from tests.fakes import RU, US, RecordingTextTypist, make_keymap
from tests.test_focus import FakeProbe
from tests.test_review_fixes import PasswordProbe, make_ctrl, type_word
from wayswitch import a11y
from wayswitch import keycodes as kc
from wayswitch.detector import LanguageModel


@pytest.fixture(scope="module")
def real():
    return make_keymap(), {lang: LanguageModel.load(lang) for lang in ("ru", "en")}


@pytest.mark.parametrize("password", [False, True])
@pytest.mark.parametrize("mode", ["dry-run", "success", "failed-plan", "automatic"])
def test_typed_words_are_never_logged(real, caplog, monkeypatch, password, mode):
    ctrl, _backend, typist, corrected = make_ctrl(
        real, group=US, probe=FakeProbe(lambda: 100.0, app="editor", password=password),
        clock=lambda: 100.0, dry_run=mode == "dry-run",
    )
    ctrl.config.general.skip_password_fields = False
    if mode == "failed-plan":
        monkeypatch.setattr("wayswitch.controller.plan_fix", lambda *_a, **_kw: None)
    with caplog.at_level(logging.DEBUG, logger="wayswitch"):
        type_word(ctrl, real[0], "ghbdtn" + (" " if mode == "automatic" else ""), US)
        if mode != "automatic":
            assert ctrl.manual_word()
    if mode in ("dry-run", "failed-plan"):
        assert not typist.taps()
    else:
        assert typist.taps()
        assert bool(corrected) is not password
    assert "ghbdtn" not in caplog.text
    assert "привет" not in caplog.text


@pytest.mark.parametrize("probe", [None, a11y.NullProbe(), FakeProbe(lambda: 100.0),
                                  FakeProbe(lambda: 100.0, app="editor", age=10.0)])
@pytest.mark.parametrize("action", ["word", "phrase", "selection"])
def test_manual_fix_with_unknown_focus_does_not_report_text(real, probe, action):
    ctrl, _backend, typist, corrected = make_ctrl(
        real, group=US, probe=probe, clock=lambda: 100.0,
    )
    type_word(ctrl, real[0], "ghbdtn", US)
    if action == "word":
        assert ctrl.manual_word()
    elif action == "phrase":
        assert ctrl.manual_phrase()
    else:
        assert not ctrl.fix_selection("ghbdtn")
    assert bool(typist.taps()) is (action != "selection")
    assert corrected == []


@pytest.mark.parametrize("selection", [False, True])
def test_focus_becoming_password_during_typing_does_not_report_text(
        real, selection, monkeypatch):
    probe = FakeProbe(lambda: 100.0, app="editor")
    if selection:
        snapshot = probe.snapshot
        probe.snapshot = lambda: snapshot()._replace(field=(":test", "/field"))
        monkeypatch.setattr(a11y, "read_selection", lambda _expected, valid, _limit:
                            a11y.TextSelection(0, 6, "ghbdtn") if valid() else None)

    class FocusChangingTypist(RecordingTextTypist):
        def type_text(self, text):
            super().type_text(text)
            probe.password = True

    ctrl, _backend, typist, corrected = make_ctrl(
        real, group=US, probe=probe, clock=lambda: 100.0, typist=FocusChangingTypist(),
    )
    type_word(ctrl, real[0], "ghbdtn", US)
    if selection:
        assert not ctrl.fix_selection("ghbdtn")
    else:
        assert ctrl.manual_word()  # scheduled
    assert typist.calls
    assert corrected == []


def test_secret_correction_is_not_learned_after_focus_becomes_safe(real):
    probe = FakeProbe(lambda: 100.0, app="editor", password=True)
    saved = []
    ctrl, _backend, _typist, corrected = make_ctrl(
        real, group=RU, probe=probe, clock=lambda: 100.0, persist_exception=saved.append,
    )
    ctrl.config.general.skip_password_fields = False
    ctrl.config.general.learn_after_undos = 1
    type_word(ctrl, real[0], "кембы ", RU)
    assert ctrl.stats["corrections"] == 1
    probe.password = False
    assert not ctrl.manual_word()
    assert ctrl.stats["undo"] == 0
    assert "кембы" not in ctrl.detector.exceptions
    assert saved == []
    assert corrected == []


def test_undo_in_password_field_is_not_learned(real):
    probe = FakeProbe(lambda: 100.0, app="editor")
    saved = []
    ctrl, _backend, _typist, _corrected = make_ctrl(
        real, group=RU, probe=probe, clock=lambda: 100.0, persist_exception=saved.append,
    )
    ctrl.config.general.learn_after_undos = 1
    type_word(ctrl, real[0], "кембы ", RU)
    assert ctrl.stats["corrections"] == 1
    probe.password = True
    assert not ctrl.manual_word()
    assert ctrl.stats["undo"] == 0
    assert "кембы" not in ctrl.detector.exceptions
    assert saved == []


@pytest.mark.parametrize("early", [False, True])
@pytest.mark.parametrize("queued", [False, True])
def test_undo_cannot_replay_after_focus_changes(real, early, queued):
    probe = FakeProbe(lambda: 100.0, app="editor")

    saved = []
    ctrl, _backend, typist, corrected = make_ctrl(
        real, group=US if early else RU, probe=probe, clock=lambda: 100.0,
        persist_exception=saved.append,
    )
    ctrl.config.general.learn_after_undos = 1
    if early:
        ctrl.config.general.early_switch = True
        type_word(ctrl, real[0], "ghbd", US)
        type_word(ctrl, real[0], "ет", RU)
    else:
        type_word(ctrl, real[0], "кембы ", RU)
    assert ctrl.stats["corrections"] == 1
    before = typist.taps()
    if queued:
        pending = []
        ctrl.schedule = pending.append
        assert ctrl.manual_word()
        assert len(pending) == 1
        probe.password = True
        pending.pop()()
    else:
        probe.password = True
        assert not ctrl.manual_word()
    assert typist.taps() == before
    assert ctrl.stats["undo"] == 0
    assert len(corrected) == 1
    assert ctrl.detector.exceptions == set()
    assert saved == []


def test_typist_exception_does_not_log_typed_text(real, caplog):
    class BrokenTypist(RecordingTextTypist):
        def type_text(self, text):
            raise RuntimeError(text)

    ctrl, _backend, _typist, corrected = make_ctrl(
        real, group=US, probe=PasswordProbe(), clock=lambda: 100.0, typist=BrokenTypist(),
    )
    with caplog.at_level(logging.DEBUG, logger="wayswitch"):
        type_word(ctrl, real[0], "ghbdtn", US)
        assert ctrl.manual_word()
    assert corrected == []
    assert "RuntimeError" in caplog.text
    assert "ghbdtn" not in caplog.text
    assert "привет" not in caplog.text


@pytest.mark.parametrize("blocked", ["paused", "locked"])
def test_blocked_input_is_not_kept_for_later_manual_fix(real, blocked):
    ctrl, _backend, typist, corrected = make_ctrl(real, group=US)
    (ctrl.pause if blocked == "paused" else lambda: ctrl.set_locked(True))()
    type_word(ctrl, real[0], "ghbdtn", US)
    assert ctrl.buffer.is_empty()
    (ctrl.resume if blocked == "paused" else lambda: ctrl.set_locked(False))()
    ctrl.manual_word()
    assert typist.taps() == []
    assert corrected == []


def test_locked_key_release_updates_physical_modifier_state(real):
    ctrl, *_ = make_ctrl(real, group=US)
    ctrl.on_key(kc.KEY_LEFTSHIFT, 1, 1)
    assert (1, kc.KEY_LEFTSHIFT) in ctrl.buffer.held_physical()
    ctrl.set_locked(True)
    ctrl.on_key(kc.KEY_LEFTSHIFT, 0, 1)
    assert not ctrl.buffer.held_physical()
    ctrl.on_key(kc.KEY_CAPSLOCK, 1, 1)
    ctrl.on_key(kc.KEY_CAPSLOCK, 0, 1)
    ctrl.set_caps(True)  # DeviceWatcher читает состояние светодиода после отпускания.
    assert ctrl.buffer.caps
    assert ctrl.buffer.is_empty()


@pytest.mark.parametrize("blocked", ["paused", "locked"])
def test_resumed_word_does_not_undo_an_old_correction(real, blocked):
    saved = []
    ctrl, _backend, _typist, corrected = make_ctrl(
        real, group=RU, persist_exception=saved.append,
    )
    ctrl.config.general.learn_after_undos = 1
    type_word(ctrl, real[0], "кембы ", RU)
    assert corrected[-1][1] == "кем бы"
    (ctrl.pause if blocked == "paused" else lambda: ctrl.set_locked(True))()
    (ctrl.resume if blocked == "paused" else lambda: ctrl.set_locked(False))()
    type_word(ctrl, real[0], "кем бы ", RU)
    assert ctrl.manual_word()
    assert corrected[-1] == ("бы", ",s", True)
    assert ctrl.stats["undo"] == 0
    assert saved == []

@pytest.mark.parametrize("initial", ["protected", "unknown", "stale"])
@pytest.mark.parametrize("action", ["space", "word", "phrase", "queued-word"])
def test_buffered_private_word_never_moves_to_new_field(real, initial, action):
    probe = FakeProbe(lambda: 100.0, app=None if initial == "unknown" else "editor",
                      password=initial == "protected", age=10 if initial == "stale" else 0)
    ctrl, backend, typist, corrected = make_ctrl(real, group=US, probe=probe, clock=lambda: 100.0)
    type_word(ctrl, real[0], "ghbdtn", US)
    if action == "queued-word":
        queue = []
        ctrl.schedule = queue.append
        assert ctrl.manual_word()
    probe.app, probe.password, probe.age = "editor", False, 0
    if action == "queued-word":
        queue.pop()()
        ctrl.schedule = lambda fn: fn()
    elif action == "word":
        ctrl.manual_word()
    elif action == "phrase":
        ctrl.manual_phrase()
    else:
        type_word(ctrl, real[0], " ", US)
    assert not typist.taps()
    assert corrected == []
    assert not ctrl._undo_counts
    backend.current_index = US
    type_word(ctrl, real[0], " ghbdtn ", US)
    assert corrected == [("ghbdtn", "привет", False)]


def test_unknown_prefix_does_not_turn_into_a_correctable_suffix(real):
    probe = FakeProbe(lambda: 100.0)
    ctrl, _, typist, corrected = make_ctrl(real, group=US, probe=probe, clock=lambda: 100.0)
    type_word(ctrl, real[0], "xx", US)
    probe.app = "editor"
    type_word(ctrl, real[0], "ghbdtn", US)
    ctrl.manual_word()
    type_word(ctrl, real[0], " ", US)
    ctrl.manual_phrase()
    assert not typist.taps()
    assert corrected == []
    type_word(ctrl, real[0], "ghbdtn ", US)
    assert corrected == [("ghbdtn", "привет", False)]


def test_same_app_same_role_focus_event_invalidates_buffer(real):
    probe = a11y.AtspiProbe(clock=lambda: 100.0, query=lambda: ("editor", True))
    probe.poke = lambda: None
    probe._refresh()
    ctrl, _, typist, corrected = make_ctrl(real, group=US, probe=probe, clock=lambda: 100.0)
    type_word(ctrl, real[0], "ghbdtn", US)
    probe._invalidate()
    probe._refresh()
    ctrl.manual_word()
    assert not typist.taps()
    assert not corrected


def test_focus_change_after_backspace_cancels_retype(real):
    probe = FakeProbe(lambda: 100.0, app="editor", password=True)

    class Moved(RecordingTextTypist):
        def backspace(self, count):
            super().backspace(count)
            probe.password = False

    ctrl, _, typist, corrected = make_ctrl(real, group=US, probe=probe, clock=lambda: 100.0,
                                           typist=Moved())
    type_word(ctrl, real[0], "ghbdtn", US)
    ctrl.manual_word()
    assert not any(call[0] == "type" for call in typist.calls)
    assert not corrected

def test_failed_type_cannot_retry_in_another_field(real):
    probe = FakeProbe(lambda: 100.0, app="editor", password=True)

    class Moved(RecordingTextTypist):
        def type_text(self, text):
            super().type_text(text)
            probe.password = False
            raise RuntimeError("field lost")

    ctrl, _, typist, corrected = make_ctrl(real, group=US, probe=probe, clock=lambda: 100.0,
                                           typist=Moved())
    type_word(ctrl, real[0], "ghbdtn", US)
    ctrl.manual_word()
    assert [name for name, _ in typist.calls] == ["backspace", "type"]
    assert not corrected

def test_repeated_snapshots_preserve_buffer_only_for_the_same_field(real):
    clock = [100.0]
    node = [(":1.2", "/first")]
    probe = a11y.AtspiProbe(clock=lambda: clock[0],
                            query=lambda: a11y.FocusSnapshot("editor", field=node[0]))
    probe.poke = lambda: None
    probe._refresh()
    ctrl, backend, typist, corrected = make_ctrl(
        real, group=US, probe=probe, clock=lambda: clock[0])
    type_word(ctrl, real[0], "ghbd", US)
    clock[0] += 1
    probe._refresh()
    type_word(ctrl, real[0], "tn ", US)
    assert corrected == [("ghbdtn", "привет", False)]
    backend.current_index = US
    type_word(ctrl, real[0], "ghbdtn", US)
    before = typist.taps()
    node[0] = (":1.2", "/second")
    probe._refresh()
    assert not ctrl.manual_word()
    assert typist.taps() == before
    assert corrected == [("ghbdtn", "привет", False)]


def test_manual_purge_allows_first_word_in_confirmed_new_field(real):
    snap = a11y.FocusSnapshot("editor", password=True, field=(":1.2", "/password"))
    probe = a11y.AtspiProbe(clock=lambda: 100.0, query=lambda: snap)
    probe.poke = lambda: None
    probe._refresh()
    ctrl, _, typist, corrected = make_ctrl(real, group=US, probe=probe, clock=lambda: 100.0)
    type_word(ctrl, real[0], "ghbdtn", US)
    snap = snap._replace(password=False, field=(":1.2", "/plain"))
    probe._invalidate()
    probe._refresh()
    assert not ctrl.manual_word()
    assert not typist.taps()
    assert not corrected
    type_word(ctrl, real[0], "ghbdtn ", US)
    assert corrected == [("ghbdtn", "привет", False)]
