import pytest

from tests.fakes import RU, US, FakeBackend, RecordingTextTypist, RecordingTypist, make_keymap
from tests.test_native_execution import FOCUS
from wayswitch import keycodes as kc
from wayswitch.actuator import ABORTED, DONE, FAILED, Backspace, Plan, SwitchLayout, Type, execute
from wayswitch.editable import SpanResult


class Field:
    def __init__(self):
        self.text = "old"
        self.selected = True

    def write(self, text):
        self.text = ("" if self.selected else self.text) + text
        self.selected = False

    def delete(self, count):
        self.text = "" if self.selected else self.text[:-count]
        self.selected = False


def make_transport(kind):
    field, backend, keymap = Field(), FakeBackend(current=RU), make_keymap()

    class Keys(RecordingTypist):
        def key(self, code, value):
            super().key(code, value)
            if value and code == kc.KEY_BACKSPACE:
                field.delete(1)
            elif value and code not in kc.MODIFIER_KEYS:
                field.write(keymap.char(code, backend.current(),
                                        bool(self.stuck() & kc.SHIFT_KEYS), False))

    class Text(RecordingTextTypist):
        def backspace(self, count):
            super().backspace(count)
            field.delete(count)

        def type_text(self, text):
            super().type_text(text)
            field.write(text)

    class Native(Text):
        def replace_span(self, focus, source, target, commit, stopped):
            if not commit():
                return SpanResult(ABORTED)
            field.selected = True
            field.write(target)
            return SpanResult(DONE, True)

    typist = {"keys": Keys, "text": Text, "native": Native}[kind]()
    steps = [SwitchLayout(US)]
    if kind == "native":
        steps.append(Backspace(3))
    steps.append(Type("ab", [(kc.KEY_A, False), (kc.KEY_B, False)]))
    plan = Plan(steps, source_text="old" if kind == "native" else None,
                focus=FOCUS if kind == "native" else None)
    return field, backend, typist, plan


@pytest.fixture(params=["keys", "text", "native"])
def transport(request):
    return make_transport(request.param)


def test_selection_changed_during_layout_wait_is_not_overwritten(transport):
    field, backend, typist, plan = transport

    def wait(index, timeout):
        field.selected = False
        return True

    backend.wait_applied = wait
    assert execute(plan, typist, backend, restore_group=RU,
                   before_write=lambda: field.selected) == ABORTED
    assert field.text == "old" and backend.current() == RU


def test_selection_changed_during_modifier_wait_is_not_overwritten(transport):
    field, backend, typist, plan = transport

    def hold():
        field.selected = False
        return True

    assert execute(plan, typist, backend, restore_group=RU, hold=hold,
                   before_write=lambda: field.selected) == ABORTED
    assert field.text == "old" and backend.current() == RU


@pytest.mark.parametrize("raises", [False, True])
def test_rejected_or_failed_validation_never_changes_text(transport, raises, caplog):
    field, backend, typist, plan = transport

    def validate():
        if raises:
            raise RuntimeError("private selected text")
        return False

    assert execute(plan, typist, backend, restore_group=RU,
                   before_write=validate) == (FAILED if raises else ABORTED)
    assert field.text == "old" and backend.current() == RU
    assert "private selected text" not in caplog.text


def test_validation_runs_once_even_when_own_write_clears_selection(transport):
    field, backend, typist, plan = transport
    checks = []

    def validate():
        checks.append((backend.current(), field.selected))
        return field.selected

    assert execute(plan, typist, backend, before_write=validate) == DONE
    assert field.text == "ab" and checks == [(US, True)]


@pytest.mark.parametrize("raises", [False, True])
def test_validation_failure_preserves_mode_used_by_physical_input(transport, raises):
    field, backend, typist, plan = transport
    appended = []

    def validate():
        field.selected = False
        field.text += "x"
        appended.append(True)
        if raises:
            raise RuntimeError("validation failed after physical input")
        return False

    assert execute(plan, typist, backend, restore_group=RU,
                   abort=lambda: bool(appended), before_write=validate) == (
                       FAILED if raises else ABORTED)
    assert field.text == "oldx" and backend.current() == US


def test_validation_cannot_bypass_lock_delivered_by_its_callback(transport):
    field, backend, typist, plan = transport
    locked = []

    def validate():
        locked.append(True)
        return True

    assert execute(plan, typist, backend, stop=lambda: bool(locked),
                   before_write=validate) == ABORTED
    assert field.text == "old"


@pytest.mark.parametrize("kind", ["keys", "text"])
def test_validation_precedes_first_backspace(kind):
    field, backend = Field(), FakeBackend(current=US)

    class Keys(RecordingTypist):
        def key(self, code, value):
            super().key(code, value)
            if code == kc.KEY_BACKSPACE and value:
                field.delete(1)

    class Text(RecordingTextTypist):
        def backspace(self, count):
            field.delete(count)

    typist = Keys() if kind == "keys" else Text()
    assert execute(Plan([Backspace(3)]), typist, backend,
                   before_write=lambda: False) == ABORTED
    assert field.text == "old"


def test_native_physical_mode_commit_does_not_skip_validation():
    field, backend, typist, plan = make_transport("native")
    appended = []

    def wait(index, timeout):
        field.selected = False
        field.text += "x"
        appended.append(True)
        return True

    backend.wait_applied = wait
    assert execute(plan, typist, backend, restore_group=RU, abort=lambda: bool(appended),
                   before_write=lambda: field.selected) == ABORTED
    assert field.text == "oldx" and backend.current() == US


def test_partial_text_write_error_never_repeats_uncertain_output():
    field = Field()

    class Partial(RecordingTextTypist):
        def type_text(self, text):
            super().type_text(text)
            field.write(text[:1])
            raise RuntimeError("provider failed after partial delivery")

    typist = Partial()
    assert execute(Plan([Type("ab", [])]), typist, FakeBackend(current=US)) == FAILED
    assert field.text == "a" and typist.calls == [("type", "ab")]
