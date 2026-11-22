from types import SimpleNamespace

import pytest

from tests.fakes import (
    RU,
    US,
    FakeBackend,
    RecordingTextTypist,
    RecordingTypist,
    keys_for_text,
    make_keymap,
)
from tests.test_focus import make_env, type_keys
from wayswitch import a11y
from wayswitch import keycodes as kc
from wayswitch.a11y import FocusSnapshot
from wayswitch.actuator import (
    ABORTED,
    DONE,
    FAILED,
    Backspace,
    Plan,
    SwitchLayout,
    ToggleCaps,
    Type,
    execute,
    plan_fix,
)
from wayswitch.editable import SpanResult

FOCUS = FocusSnapshot("soffice", False, 100.0, ("libreoffice-writer",), (":1.5", "/field"))


class RecordingReplacer:
    """Явный native-обработчик без привязки к объекту typist."""

    def __init__(self, action=None):
        self.action = action
        self.calls = []

    def __call__(self, focus, source, target, commit, stopped):
        self.calls.append((focus, source, target))
        if self.action is not None:
            return self.action(commit, stopped)
        if commit() is False:
            return SpanResult(ABORTED)
        return SpanResult(DONE, True)


def native_plan(**overrides):
    values = {"source_text": "ghbdtn ", "focus": FOCUS}
    values.update(overrides)
    return Plan([SwitchLayout(RU), Backspace(7), Type("привет ", [])], **values)


@pytest.mark.parametrize("accepted", [False, True])
def test_native_transport_can_switch_layout_with_the_text_atomically(accepted):
    typist, backend = RecordingTextTypist(), FakeBackend(current=US)

    def replace(commit, stopped):
        assert stopped.defer_layout() == RU
        assert commit() is True
        assert backend.set_calls == []
        if accepted:
            backend.set(RU)
        return SpanResult(DONE, True) if accepted else SpanResult(ABORTED)

    result = execute(native_plan(), typist, backend, restore_group=US,
                     native_replacer=RecordingReplacer(replace))
    assert result == (DONE if accepted else ABORTED)
    assert backend.set_calls == ([RU] if accepted else [])


def key_plan(*, caps_off=False, target="привет "):
    plan = plan_fix(len("ghbdtn "), target, RU, make_keymap(), caps_on=False,
                    caps_off=caps_off)
    assert plan is not None
    plan.source_text = "ghbdtn "
    plan.focus = FOCUS
    return plan


def test_key_native_without_setup_honors_held_modifier_before_commit():
    typist = RecordingTypist()
    replacer = RecordingReplacer()
    plan = Plan([Backspace(7), Type("привет ", [])],
                source_text="ghbdtn ", focus=FOCUS)
    assert execute(plan, typist, FakeBackend(current=RU), hold=lambda: False,
                   native_replacer=replacer) == ABORTED
    assert typist.taps() == []


def test_key_typist_native_span_success_uses_no_printing_keys():
    typist, backend = RecordingTypist(), FakeBackend(current=US)
    replacer = RecordingReplacer()
    assert execute(key_plan(), typist, backend, restore_group=US, native_replacer=replacer) == DONE
    assert replacer.calls == [(FOCUS, "ghbdtn ", "привет ")]
    assert typist.taps() == []
    assert backend.set_calls == [RU]
    assert typist.stuck() == set()


def test_key_typist_unsupported_before_setup_uses_original_keys():
    plan = key_plan()
    typist, backend = RecordingTypist(), FakeBackend(current=US)
    replacer = RecordingReplacer(lambda *_: None)
    assert execute(plan, typist, backend, restore_group=US, native_replacer=replacer) == DONE
    expected = [(kc.KEY_BACKSPACE, False)] * 7 + plan.steps[-1].keys
    assert typist.taps() == expected
    assert backend.set_calls == [RU]
    assert len(replacer.calls) == 1


@pytest.mark.parametrize("failure", ["failed", "exception", "none"])
def test_key_typist_native_uncertainty_after_commit_never_falls_back(failure):
    def native(commit, _stopped):
        assert commit() is True
        if failure == "exception":
            raise RuntimeError("Timeout was reached")
        return SpanResult(FAILED, True) if failure == "failed" else None

    typist, backend = RecordingTypist(), FakeBackend(current=US)
    replacer = RecordingReplacer(native)
    assert execute(key_plan(), typist, backend, restore_group=US,
                   native_replacer=replacer) == FAILED
    assert typist.taps() == [] and len(replacer.calls) == 1
    assert backend.set_calls == [RU]


def test_key_typist_native_cancellation_before_commit_does_not_change_layout():
    typist, backend = RecordingTypist(), FakeBackend(current=US)
    replacer = RecordingReplacer(lambda *_: SpanResult(ABORTED))
    assert execute(key_plan(), typist, backend, restore_group=US,
                   native_replacer=replacer) == ABORTED
    assert typist.taps() == [] and replacer.calls
    assert backend.set_calls == []


def test_key_typist_focus_change_during_layout_prevents_native_edit():
    changed = []

    class Backend(FakeBackend):
        def wait_applied(self, index, timeout):
            changed.append(True)
            return super().wait_applied(index, timeout)

    typist, backend = RecordingTypist(), Backend(current=US)
    replacer = RecordingReplacer()
    assert execute(key_plan(), typist, backend, restore_group=US,
                   stop=lambda: bool(changed), native_replacer=replacer) == ABORTED
    assert typist.taps() == [] and len(replacer.calls) == 1
    assert backend.set_calls == [RU]


def test_key_typist_failed_layout_cannot_start_native_mutation():
    typist, backend = RecordingTypist(), FakeBackend(current=US, fail_switch=True)
    replacer = RecordingReplacer()
    assert execute(key_plan(), typist, backend, restore_group=US,
                   native_replacer=replacer) == FAILED
    assert typist.taps() == [] and len(replacer.calls) == 1
    assert backend.set_calls == [RU, US]


def test_key_typist_before_write_runs_after_layout_and_caps_once():
    order = []
    plan = key_plan(caps_off=True)
    typist, backend = RecordingTypist(), FakeBackend(current=US)
    replacer = RecordingReplacer()

    def native(commit, _stopped):
        order.append("native")
        assert commit() is True
        order.append("committed")
        return SpanResult(DONE, True)

    replacer.action = native
    outcome = {}

    def before_write():
        order.append("before_write")
        assert backend.current() == RU
        assert typist.taps() == [(kc.KEY_CAPSLOCK, False)]
        return True

    assert execute(plan, typist, backend, restore_group=US,
                   before_write=before_write, outcome=outcome, native_replacer=replacer) == DONE
    assert order == ["native", "before_write", "committed"]
    assert typist.taps() == [(kc.KEY_CAPSLOCK, False)]
    assert outcome["caps_off"] is True and outcome["native_span"] is True
    assert backend.set_calls == [RU]


def test_key_native_anchor_rejects_input_before_it_and_allows_append_after():
    physical = [False]
    seen = []

    def native(commit, stopped):
        physical[0] = True
        seen.append(stopped())
        assert stopped.reserve_anchor() is False
        seen.append(stopped())
        physical[0] = False
        assert stopped.reserve_anchor() is True
        physical[0] = True
        seen.append(stopped())
        seen.append(stopped(before_write=True))
        assert commit() is True
        return SpanResult(DONE, True)

    typist = RecordingTypist()
    replacer = RecordingReplacer(native)
    assert execute(key_plan(), typist, FakeBackend(current=US), restore_group=US,
                   abort=lambda: bool(physical[0]), native_replacer=replacer) == DONE
    assert seen == [True, True, False, True]
    assert typist.taps() == []


def test_key_native_fast_switch_skips_client_settle_for_accessibility_write():
    class Backend(FakeBackend):
        def __init__(self):
            super().__init__(current=US)
            self.waits = []

        def wait_applied(self, index, timeout):
            self.waits.append((index, timeout))
            return super().wait_applied(index, timeout)

    def native(commit, stopped):
        stopped.use_fast_switch()
        assert commit() is True
        return SpanResult(DONE, True)

    typist, backend = RecordingTypist(), Backend()
    replacer = RecordingReplacer(native)
    assert execute(key_plan(), typist, backend, restore_group=US, native_replacer=replacer) == DONE
    assert backend.set_calls == [RU]
    assert backend.waits == []
    assert typist.taps() == []


def test_key_native_default_switch_still_waits_for_client_settle():
    class Backend(FakeBackend):
        def __init__(self):
            super().__init__(current=US)
            self.waits = []

        def wait_applied(self, index, timeout):
            self.waits.append(index)
            return super().wait_applied(index, timeout)

    typist, backend = RecordingTypist(), Backend()
    replacer = RecordingReplacer()
    assert execute(key_plan(), typist, backend, restore_group=US, native_replacer=replacer) == DONE
    assert backend.set_calls == [RU]
    assert backend.waits == [RU]


def test_native_handler_receives_controller_focus_callback():
    snapshot = FocusSnapshot("soffice", False, 5.0, (), (":1.5", "/field"))
    seen = []

    def native(commit, stopped):
        seen.append(stopped.focus_snapshot())
        assert commit() is True
        return SpanResult(DONE, True)

    assert execute(native_plan(), RecordingTypist(), FakeBackend(current=US),
                   restore_group=US, native_focus=lambda: snapshot,
                   native_replacer=RecordingReplacer(native)) == DONE
    assert seen == [snapshot]


def test_native_handler_without_focus_callback_gets_no_proof():
    seen = []

    def native(commit, stopped):
        seen.append(stopped.focus_snapshot())
        assert commit() is True
        return SpanResult(DONE, True)

    assert execute(native_plan(), RecordingTypist(), FakeBackend(current=US),
                   restore_group=US, native_replacer=RecordingReplacer(native)) == DONE
    assert seen == [None]


def test_native_focus_callback_error_is_not_a_proof():
    seen = []

    def native(commit, stopped):
        seen.append(stopped.focus_snapshot())
        assert commit() is True
        return SpanResult(DONE, True)

    def broken():
        raise RuntimeError("at-spi query failed")

    assert execute(native_plan(), RecordingTypist(), FakeBackend(current=US),
                   restore_group=US, native_focus=broken,
                   native_replacer=RecordingReplacer(native)) == DONE
    assert seen == [None]


def test_key_native_anchor_cannot_override_focus_or_destructive_stop():
    physical = [False]
    stopped_now = [False]

    def native(commit, stopped):
        physical[0] = True
        stopped.reserve_anchor()
        stopped_now[0] = True
        assert stopped() is True
        assert commit() is False
        return SpanResult(ABORTED)

    typist = RecordingTypist()
    replacer = RecordingReplacer(native)
    assert execute(key_plan(), typist, FakeBackend(current=US), restore_group=US,
                   abort=lambda: bool(physical[0]),
                   stop=lambda: bool(stopped_now[0]), native_replacer=replacer) == ABORTED
    assert typist.taps() == []


def test_unsupported_key_native_preserves_original_hold_abort_sequence():
    typist, backend = RecordingTypist(), FakeBackend(current=US)
    replacer = RecordingReplacer(lambda *_: None)
    assert execute(key_plan(), typist, backend, restore_group=US,
                   hold=lambda: False, native_replacer=replacer) == ABORTED
    assert typist.taps() == [] and len(replacer.calls) == 1
    # Как без нативного обработчика: раскладка успела переключиться и откатилась.
    assert backend.set_calls == [RU, US]


def test_key_typist_without_span_metadata_keeps_plain_key_transport():
    plan = Plan([Backspace(2), Type("ab", [(kc.KEY_A, False), (kc.KEY_B, False)])])
    typist = RecordingTypist()
    replacer = RecordingReplacer(lambda *_: pytest.fail("shape without span must not use native"))
    assert execute(plan, typist, FakeBackend(current=US), native_replacer=replacer) == DONE
    assert replacer.calls == []
    assert typist.taps() == [(kc.KEY_BACKSPACE, False), (kc.KEY_BACKSPACE, False),
                             (kc.KEY_A, False), (kc.KEY_B, False)]


def test_native_span_reserves_source_before_layout_and_does_not_repeat_keys():
    backend = FakeBackend(current=US)

    def native(commit, stopped):
        assert backend.current() == US
        assert not stopped()
        assert commit() is True
        assert backend.current() == RU
        return SpanResult(DONE, True)

    typist = RecordingTextTypist()
    replacer = RecordingReplacer(native)
    assert execute(native_plan(), typist, backend, restore_group=US,
                   native_replacer=replacer) == DONE
    assert replacer.calls == [(FOCUS, "ghbdtn ", "привет ")]
    assert typist.calls == []
    assert backend.set_calls == [RU]


def test_unsupported_native_span_preserves_original_bulk_transport():
    typist = RecordingTextTypist()
    replacer = RecordingReplacer(lambda *_: None)
    assert execute(native_plan(), typist, FakeBackend(current=US), native_replacer=replacer) == DONE
    assert typist.calls == [("backspace", 7), ("type", "привет ")]


@pytest.mark.parametrize("failure", ["failed", "unsupported", "exception"])
def test_native_attempt_never_falls_back_or_retries_after_commit(failure):
    def native(commit, _stopped):
        commit()
        if failure == "exception":
            raise RuntimeError("Timeout was reached")
        return SpanResult(FAILED, True) if failure == "failed" else None

    typist, backend = RecordingTextTypist(), FakeBackend(current=US)
    replacer = RecordingReplacer(native)
    assert execute(native_plan(), typist, backend, restore_group=US,
                   native_replacer=replacer) == FAILED
    assert typist.calls == [] and len(replacer.calls) == 1
    assert backend.set_calls == [RU]


def test_native_cancellation_before_commit_does_not_change_layout():
    typist = RecordingTextTypist()
    replacer = RecordingReplacer(lambda *_: SpanResult(ABORTED))
    backend = FakeBackend(current=US)
    assert execute(native_plan(), typist, backend, restore_group=US,
                   native_replacer=replacer) == ABORTED
    assert typist.calls == [] and backend.set_calls == []


def test_append_during_layout_wait_uses_reserved_native_anchor():
    appended = []

    class Backend(FakeBackend):
        def wait_applied(self, index, timeout):
            appended.append(True)
            return super().wait_applied(index, timeout)

    typist, backend = RecordingTextTypist(), Backend(current=US)
    replacer = RecordingReplacer()
    assert execute(native_plan(), typist, backend, restore_group=US,
                   abort=lambda: bool(appended), native_replacer=replacer) == DONE
    assert len(replacer.calls) == 1 and typist.calls == []
    assert backend.set_calls == [RU]


def test_failed_layout_cannot_start_native_mutation():
    typist, backend = RecordingTextTypist(), FakeBackend(current=US, fail_switch=True)
    replacer = RecordingReplacer()
    assert execute(native_plan(), typist, backend, restore_group=US,
                   native_replacer=replacer) == FAILED
    assert typist.calls == []
    assert backend.set_calls == [RU, US]


@pytest.mark.parametrize("failure", ["false", "exception"])
def test_failed_layout_confirmation_keeps_mode_used_by_physical_append(failure):
    appended = []

    class Backend(FakeBackend):
        def wait_applied(self, index, timeout):
            appended.append(True)
            if failure == "exception":
                raise RuntimeError("layout acknowledgment timeout")
            return False

    typist, backend = RecordingTextTypist(), Backend(current=US)
    replacer = RecordingReplacer()
    assert execute(native_plan(), typist, backend, restore_group=US,
                   abort=lambda: bool(appended), native_replacer=replacer) == FAILED
    assert typist.calls == [] and backend.set_calls == [RU]
    assert backend.current() == RU


def test_append_after_caps_toggle_keeps_native_anchor_and_caps_state():
    appended = []

    class Typist(RecordingTextTypist):
        toggles = 0

        def toggle_caps(self):
            self.toggles += 1
            appended.append(True)
            return True

    typist = Typist()
    plan = Plan([ToggleCaps(), Backspace(7), Type("привет ", [])],
                source_text="GHBDTN ", focus=FOCUS)
    assert execute(plan, typist, FakeBackend(current=RU), abort=lambda: bool(appended),
                   native_replacer=RecordingReplacer()) == DONE
    assert typist.toggles == 1 and typist.calls == []


def test_focus_change_during_layout_prevents_native_insert_and_layout_rollback():
    changed = []

    class Backend(FakeBackend):
        def wait_applied(self, index, timeout):
            changed.append(True)
            return super().wait_applied(index, timeout)

    typist, backend = RecordingTextTypist(), Backend(current=US)
    replacer = RecordingReplacer()
    assert execute(native_plan(), typist, backend, restore_group=US,
                   stop=lambda: bool(changed), native_replacer=replacer) == ABORTED
    assert typist.calls == [] and backend.set_calls == [RU]


def test_unsupported_probe_rechecks_input_before_keyboard_fallback():
    interrupted = []

    def native(_commit, _stopped):
        interrupted.append(True)
        return None

    typist = RecordingTextTypist()
    replacer = RecordingReplacer(native)
    assert execute(native_plan(), typist, FakeBackend(current=US),
                   abort=lambda: bool(interrupted), native_replacer=replacer) == ABORTED
    assert typist.calls == []


def test_native_success_requires_a_committed_mutation():
    typist = RecordingTextTypist()
    replacer = RecordingReplacer(lambda *_: SpanResult(DONE))
    assert execute(native_plan(), typist, FakeBackend(current=US),
                   native_replacer=replacer) == FAILED
    assert typist.calls == []


@pytest.mark.parametrize("focus", [None, FocusSnapshot(), FOCUS._replace(password=True)])
def test_private_or_unverified_metadata_never_reaches_native_handler(focus):
    typist = RecordingTextTypist()
    replacer = RecordingReplacer(lambda *_: pytest.fail("native handler reached protected scope"))
    assert execute(native_plan(focus=focus), typist, FakeBackend(current=US),
                   native_replacer=replacer) == DONE
    assert replacer.calls == []
    assert typist.calls == [("backspace", 7), ("type", "привет ")]


def test_source_length_mismatch_cannot_delete_by_unverified_count():
    typist = RecordingTextTypist()
    replacer = RecordingReplacer()
    assert execute(native_plan(source_text="other"), typist, FakeBackend(current=US),
                   native_replacer=replacer) == ABORTED
    assert typist.calls == replacer.calls == []


def test_unannotated_text_plan_retains_bulk_calls():
    typist = RecordingTextTypist()
    replacer = RecordingReplacer(lambda *_: pytest.fail("native handler needs captured span"))
    plan = Plan([Backspace(2), Type("ab", [])])
    assert execute(plan, typist, FakeBackend(current=US), native_replacer=replacer) == DONE
    assert typist.calls == [("backspace", 2), ("type", "ab")]


def test_native_span_only_folds_the_final_adjacent_pair():
    typist = RecordingTextTypist()
    replacer = RecordingReplacer(lambda *_: pytest.fail("nonfinal pair must not use span metadata"))
    plan = native_plan()
    plan.steps.append(Type("!", []))
    assert execute(plan, typist, FakeBackend(current=US), native_replacer=replacer) == DONE
    assert typist.calls == [("backspace", 7), ("type", "привет "), ("type", "!")]


def controller_env(action=None, focus=FOCUS):
    probe = SimpleNamespace(snapshot=lambda: focus, poke=lambda: None)
    env = make_env(probe=probe)
    env["ctrl"].config.general.early_switch = False
    env["backend"].current_index = US
    env["ctrl"].typist = RecordingTextTypist()
    env["ctrl"].native_replacer = RecordingReplacer(action)
    return env


def test_controller_carries_original_word_tail_and_focus_to_native_span():
    env = controller_env()
    type_keys(env, keys_for_text(env["km"], "ghbdtn ", US))
    assert env["ctrl"].native_replacer.calls == [(FOCUS, "ghbdtn ", "привет ")]
    assert env["ctrl"].stats["corrections"] == 1


def test_controller_offers_native_handler_a_fresh_focus_snapshot():
    env = controller_env()
    ctrl = env["ctrl"]
    seen = []

    def native(commit, stopped):
        seen.append(stopped.focus_snapshot())
        assert commit() is True
        return SpanResult(DONE, True)

    ctrl.native_replacer.action = native
    type_keys(env, keys_for_text(env["km"], "ghbdtn ", US))
    assert seen == [FOCUS]
    assert ctrl.stats["corrections"] == 1


@pytest.mark.parametrize("snapshot", [
    FocusSnapshot(),
    FOCUS._replace(app=None),
    FOCUS._replace(field=None),
    FOCUS._replace(password=True),
    FOCUS._replace(taken_at=0.0),
    FOCUS._replace(taken_at=-1.0),
])
def test_controller_native_focus_snapshot_rejects_untrustworthy_scope(snapshot):
    env = controller_env(focus=snapshot)
    ctrl = env["ctrl"]
    ctrl.busy = True
    assert ctrl._native_focus_snapshot() is None


def test_controller_native_focus_snapshot_rejects_stale_or_null_probe():
    env = controller_env()
    ctrl = env["ctrl"]
    ctrl.busy = True
    ctrl.set_probe(a11y.NullProbe())
    assert ctrl._native_focus_snapshot() is None


def test_controller_native_focus_snapshot_requires_running_operation():
    env = controller_env()
    ctrl = env["ctrl"]
    assert not ctrl.busy
    assert ctrl._native_focus_snapshot() is None


def test_resolved_autocaps_case_is_kept_in_buffer_history_and_undo():
    env = controller_env()
    ctrl = env["ctrl"]
    corrected = []
    ctrl.on_corrected = lambda *args: corrected.append(args)

    def native(commit, _stopped):
        commit()
        return SpanResult(DONE, True, "Ghbdtn ", "Привет ")

    ctrl.native_replacer.action = native
    type_keys(env, keys_for_text(env["km"], "ghbdtn ", US))
    assert ctrl.keymap.decode(ctrl.buffer.phrase(), RU) == "Привет "
    assert corrected == [("Ghbdtn", "Привет", False)]
    ctrl.native_replacer.action = None
    assert ctrl.manual_word()
    assert ctrl.native_replacer.calls[-1][1:] == ("Привет ", "Ghbdtn ")
    assert ctrl.stats["undo"] == 1


@pytest.mark.parametrize("source,target", [
    ("private-unmatched ", "Привет "), ("Ghbdtn ", "private-target "),
    (None, "Привет "), ("Ghbdtn ", None),
])
def test_invalid_resolved_case_never_reaches_history(source, target, caplog):
    def native(commit, _stopped):
        commit()
        return SpanResult(DONE, True, source, target)

    outcome = {}
    assert execute(native_plan(), RecordingTextTypist(), FakeBackend(current=US),
                   outcome=outcome, native_replacer=RecordingReplacer(native)) == FAILED
    assert "source_text" not in outcome and "target_text" not in outcome
    assert "private-unmatched" not in caplog.text and "private-target" not in caplog.text


def test_dry_run_never_calls_native_span_or_keyboard():
    env = controller_env()
    env["ctrl"].dry_run = True
    type_keys(env, keys_for_text(env["km"], "ghbdtn ", US))
    assert env["ctrl"].native_replacer.calls == env["ctrl"].typist.calls == []


@pytest.mark.parametrize("event", ["click", "left", "backspace", "control", "resync", "gone"])
def test_disruptive_input_stops_mutation_after_native_commit(event):
    env = controller_env()
    ctrl = env["ctrl"]
    observed = []

    def native(commit, stopped):
        commit()
        if event == "click":
            ctrl.on_click()
        elif event == "resync":
            ctrl.on_resync(2, [])
        elif event == "gone":
            ctrl.on_device_gone(2)
        else:
            code = {"left": kc.KEY_LEFT, "backspace": kc.KEY_BACKSPACE,
                    "control": kc.KEY_LEFTCTRL}[event]
            ctrl.on_key(code, 1, 2)
        observed.append(stopped())
        return SpanResult(FAILED, True)

    ctrl.native_replacer.action = native
    type_keys(env, keys_for_text(env["km"], "ghbdtn ", US))
    assert observed == [True]
    assert ctrl.stats["corrections"] == 0
    assert ctrl.typist.calls == []


def test_physical_append_after_anchor_before_commit_is_replayed():
    env = controller_env()
    ctrl = env["ctrl"]

    def native(commit, stopped):
        stopped.reserve_anchor()
        ctrl.on_key(kc.KEY_T, 1, 2)
        ctrl.on_key(kc.KEY_T, 0, 2)
        assert not stopped()
        assert commit() is True
        return SpanResult(DONE, True)

    ctrl.native_replacer.action = native
    type_keys(env, keys_for_text(env["km"], "ghbdtn ", US))
    assert ctrl.stats["corrections"] == 1
    assert ctrl.typist.calls == []
    assert not ctrl._skip_word
    assert ctrl.keymap.decode(ctrl.buffer.current_word(), RU) == "е"
    assert list(ctrl.context) == []  # потерянный ввод сбросил контекст, replay его не вернул


def test_native_append_keeps_buffer_consistent_with_layout_used_by_real_field():
    env = controller_env()
    ctrl = env["ctrl"]
    actual_append = []

    def native(commit, stopped):
        reserve = getattr(stopped, "reserve_anchor", None)
        if reserve is not None:
            reserve()
        actual_append.append(ctrl.keymap.decode(
            keys_for_text(env["km"], "t", US), ctrl.backend.current()))
        ctrl.on_key(kc.KEY_T, 1, 2)
        ctrl.on_key(kc.KEY_T, 0, 2)
        if stopped():
            return SpanResult(ABORTED)
        assert commit() is True
        return SpanResult(DONE, True)

    ctrl.native_replacer.action = native
    type_keys(env, keys_for_text(env["km"], "ghbdtn ", US))
    assert ctrl.keymap.decode(ctrl.buffer.current_word(), ctrl.backend.current()) == "".join(
        actual_append)


def test_plain_append_does_not_abort_committed_native_span():
    env = controller_env()
    ctrl = env["ctrl"]

    def native(commit, stopped):
        commit()
        ctrl.on_key(kc.KEY_T, 1, 2)
        ctrl.on_key(kc.KEY_T, 0, 2)
        assert not stopped()
        return SpanResult(DONE, True)

    ctrl.native_replacer.action = native
    type_keys(env, keys_for_text(env["km"], "ghbdtn ", US))
    assert ctrl.stats["corrections"] == 1
    assert ctrl.typist.calls == []
    assert not ctrl._skip_word
    assert ctrl.keymap.decode(ctrl.buffer.current_word(), RU) == "е"


def test_native_early_append_remains_available_to_double_shift_undo():
    env = controller_env()
    ctrl = env["ctrl"]
    prefix = keys_for_text(env["km"], "ghbd", US)
    type_keys(env, prefix)

    def native(commit, stopped):
        assert commit() is True
        for code in (kc.KEY_T, kc.KEY_N, kc.KEY_SPACE):
            ctrl.on_key(code, 1, 2)
            ctrl.on_key(code, 0, 2)
        assert not stopped()
        return SpanResult(DONE, True)

    ctrl.native_replacer.action = native
    assert ctrl._apply_fix(prefix, 0, RU, "прив", manual=False,
                           reason="early", group_before=US)
    assert ctrl.keymap.decode(ctrl.buffer.phrase(), RU) == "привет "
    ctrl.native_replacer.action = None
    assert ctrl.manual_word()
    assert ctrl.native_replacer.calls[-1][1:] == ("привет ", "ghbdtn ")
    assert ctrl.stats["undo"] == 1
    assert ctrl.typist.calls == []


@pytest.mark.parametrize("unmodelled", ["repeat", "caps"])
def test_native_append_with_uncertain_count_or_case_keeps_buffer_guard(unmodelled):
    env = controller_env()
    ctrl = env["ctrl"]

    def native(commit, stopped):
        commit()
        if unmodelled == "repeat":
            ctrl.on_key(kc.KEY_T, 2, 2)
        else:
            ctrl.on_key(kc.KEY_T, 1, 2)
            ctrl.on_key(kc.KEY_T, 0, 2)
            ctrl.on_key(kc.KEY_CAPSLOCK, 1, 2)
            ctrl.on_key(kc.KEY_CAPSLOCK, 0, 2)
            ctrl.set_caps(True)
        assert not stopped()
        return SpanResult(DONE, True)

    ctrl.native_replacer.action = native
    type_keys(env, keys_for_text(env["km"], "ghbdtn ", US))
    assert ctrl.buffer.is_empty() and ctrl._skip_word


def test_command_modifier_gesture_alone_is_allowed_but_combination_stops():
    env = controller_env()
    ctrl = env["ctrl"]
    cfg = ctrl.config
    cfg.gesture.manual = "tap_key"
    cfg.gesture.tap_key = "leftctrl"
    ctrl.reload_config(cfg)
    observed = []

    def native(commit, stopped):
        commit()
        ctrl.on_key(kc.KEY_LEFTCTRL, 1, 2)
        observed.append(stopped())
        ctrl.on_key(kc.KEY_C, 1, 2)
        observed.append(stopped())
        return SpanResult(FAILED, True)

    ctrl.native_replacer.action = native
    type_keys(env, keys_for_text(env["km"], "ghbdtn ", US))
    assert observed == [False, True]


def test_execute_ignores_typist_replace_span_and_uses_explicit_replacer():
    typist = RecordingTextTypist()
    typist.replace_span = lambda *_: pytest.fail("monkeypatched replace_span must not run")
    replacer = RecordingReplacer()
    assert execute(native_plan(), typist, FakeBackend(current=US),
                   native_replacer=replacer) == DONE
    assert replacer.calls == [(FOCUS, "ghbdtn ", "привет ")]
    assert typist.calls == []


def test_controller_constructor_passes_declared_native_replacer():
    probe = SimpleNamespace(snapshot=lambda: FOCUS, poke=lambda: None)
    replacer = RecordingReplacer()
    env = make_env(probe=probe)
    ctrl = env["ctrl"]
    from wayswitch.controller import Controller

    fresh = Controller(ctrl.config, env["km"], env["det"], env["backend"],
                       RecordingTypist(), clock=env["clock"], probe=probe,
                       native_replacer=replacer)
    fresh.config.general.early_switch = False
    env["backend"].current_index = US
    env["ctrl"] = fresh
    type_keys(env, keys_for_text(env["km"], "ghbdtn ", US))
    assert replacer.calls == [(FOCUS, "ghbdtn ", "привет ")]
