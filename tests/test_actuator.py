import pytest

from tests.fakes import (
    RU,
    US,
    FakeBackend,
    RecordingTextTypist,
    RecordingTypist,
    make_keymap,
)
from wayswitch import keycodes as kc
from wayswitch.actuator import (
    ABORTED,
    DONE,
    FAILED,
    Backspace,
    ReleaseModifiers,
    SwitchLayout,
    Type,
    execute,
    plan_fix,
)


def test_plan_fix_builds_steps_with_shift_and_caps():
    km = make_keymap()
    plan = plan_fix(7, "Привет, ", RU, km, caps_on=False)
    assert isinstance(plan.steps[0], ReleaseModifiers)
    assert plan.steps[1] == Backspace(7)
    assert plan.steps[2] == SwitchLayout(RU)
    t = plan.steps[3]
    assert isinstance(t, Type) and t.text == "Привет, "
    assert t.keys[0] == (kc.KEY_G, True)          # П — с Shift
    assert t.keys[1] == (kc.KEY_H, False)         # р
    assert t.keys[6] == (kc.KEY_SLASH, True)      # запятая в ru — Shift+/
    assert t.keys[7] == (kc.KEY_SPACE, False)
    caps = plan_fix(0, "Пр", RU, km, caps_on=True)
    assert caps.steps[-1].keys == [(kc.KEY_G, False), (kc.KEY_H, True)]


def test_plan_fix_without_switch_and_impossible_char():
    km = make_keymap()
    plan = plan_fix(3, "abc", US, km, caps_on=False, switch=False)
    assert not any(isinstance(s, SwitchLayout) for s in plan.steps)
    assert plan.steps[-1].keys[0] == (kc.KEY_A, False)
    assert plan_fix(1, "ж", US, km, caps_on=False) is None


def test_execute_full_sequence():
    km = make_keymap()
    typist, backend = RecordingTypist(), FakeBackend(current=US)
    plan = plan_fix(2, "пр ", RU, km, caps_on=False)
    assert execute(plan, typist, backend) == DONE
    assert backend.set_calls == [RU]
    assert typist.taps() == [(kc.KEY_BACKSPACE, False), (kc.KEY_BACKSPACE, False),
                             (kc.KEY_G, False), (kc.KEY_H, False), (kc.KEY_SPACE, False)]
    assert typist.stuck() == set()
    # первым делом отпущены все модификаторы
    assert typist.events[0] == (kc.KEY_LEFTSHIFT, 0)


def test_execute_aborts_and_releases_everything():
    km = make_keymap()
    plan = plan_fix(3, "Пр", RU, km, caps_on=False)
    calls = {"n": 0}

    def abort():
        calls["n"] += 1
        return calls["n"] >= 3

    typist = RecordingTypist()
    assert execute(plan, typist, FakeBackend(current=US), abort=abort) == ABORTED
    assert typist.stuck() == set()
    assert len(typist.taps()) < 5


def test_execute_fails_on_write_error_and_releases():
    km = make_keymap()
    plan = plan_fix(2, "Пр", RU, km, caps_on=False)
    for fail_at in range(1, 20):
        typist = RecordingTypist(fail_at=fail_at)
        result = execute(plan, typist, FakeBackend(current=US))
        assert result == FAILED
        assert typist.stuck() == set(), fail_at


def test_execute_fails_if_layout_not_applied():
    km = make_keymap()
    plan = plan_fix(1, "п", RU, km, caps_on=False)
    typist = RecordingTypist()
    assert execute(plan, typist, FakeBackend(current=US, fail_switch=True)) == FAILED
    assert typist.stuck() == set()
    assert (kc.KEY_G, 1) not in typist.events  # перепечатка не началась


def test_execute_with_text_typist():
    km = make_keymap()
    plan = plan_fix(4, "тест ", RU, km, caps_on=False)
    typist, backend = RecordingTextTypist(), FakeBackend(current=US)
    assert execute(plan, typist, backend) == DONE
    assert typist.calls == [("backspace", 4), ("type", "тест ")]
    assert backend.set_calls == [RU]


def test_key_delay_calls_sleep():
    km = make_keymap()
    plan = plan_fix(1, "п", RU, km, caps_on=False)
    slept = []
    execute(plan, RecordingTypist(), FakeBackend(current=US), key_delay=0.002,
            sleep=slept.append)
    assert slept and all(s == pytest.approx(0.002) for s in slept)
