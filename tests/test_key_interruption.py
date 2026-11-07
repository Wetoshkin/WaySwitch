import pytest

from tests.fakes import RU, US, FakeBackend, make_keymap
from tests.test_controller import env as controller_env
from tests.test_controller import type_text
from wayswitch import keycodes as kc
from wayswitch.actuator import ABORTED, FAILED, Plan, ToggleCaps, execute, plan_fix


class Screen:
    def __init__(self, keymap, backend, text="Test ghbdtn "):
        self.keymap, self.backend, self.text = keymap, backend, text
        self.held = set()
        self.caps = False
        self.deleted = []
        self.typed = []
        self.injected = False
        self.on_event = lambda code, value: None
        self.on_physical = lambda code, value: None

    def key(self, code, value):
        if value:
            self.held.add(code)
            if code == kc.KEY_BACKSPACE:
                self.deleted.append(self.text[-1:])
                self.text = self.text[:-1]
            elif code == kc.KEY_CAPSLOCK:
                self.caps = not self.caps
            elif code not in kc.MODIFIER_KEYS:
                char = self.keymap.char(code, self.backend.current(),
                                       bool(self.held & kc.SHIFT_KEYS), self.caps)
                self.text += char
                self.typed.append(char)
        else:
            self.held.discard(code)
        self.on_event(code, value)

    def syn(self):
        pass

    def physical(self, code=kc.KEY_A):
        if self.injected:
            return
        self.injected = True
        self.text += self.keymap.char(code, self.backend.current(),
                                     bool(self.held & kc.SHIFT_KEYS), self.caps)
        self.on_physical(code, 1)
        self.on_physical(code, 0)


@pytest.fixture
def keyboard():
    keymap, backend = make_keymap(), FakeBackend(current=US)
    screen = Screen(keymap, backend)
    interrupted = [False]
    screen.on_physical = lambda code, value: interrupted.__setitem__(0, True)
    return keymap, backend, screen, lambda: interrupted[0]


def test_append_after_first_backspace_preserves_physical_character(keyboard):
    keymap, backend, screen, abort = keyboard
    screen.on_event = lambda code, value: (
        screen.physical() if code == kc.KEY_BACKSPACE and value == 0 else None)
    plan = plan_fix(7, "привет ", RU, keymap, False)
    assert execute(plan, screen, backend, abort=abort, restore_group=US) == FAILED
    assert screen.text == "Test ghbdtnф"
    assert screen.deleted == [" "] and screen.typed == [] and not screen.held
    assert backend.current() == RU


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("phase", ["backspace", "first_type", "last_type"])
def test_interrupted_key_fix_has_no_history_counter_or_buffer_replay(automatic, phase):
    state = controller_env.__wrapped__()
    ctrl = state["ctrl"]
    ctrl.config.general.auto_correct = automatic
    ctrl.config.general.early_switch = False
    screen = Screen(state["km"], state["backend"])
    screen.on_physical = lambda code, value: ctrl.on_key(code, value, 99)

    def event(code, value):
        if value:
            return
        if (phase == "backspace" and code == kc.KEY_BACKSPACE
                or phase == "first_type" and len(screen.typed) == 1
                or phase == "last_type" and len(screen.typed) == 7):
            screen.physical()

    screen.on_event = event
    ctrl.typist = screen
    type_text(state, "ghbdtn ")
    if not automatic:
        ctrl.manual_word()
    expected = {"backspace": "Test ghbdtnф", "first_type": "Test пф",
                "last_type": "Test привет ф"}
    assert screen.text == expected[phase] and not screen.held
    assert ctrl.buffer.is_empty() and ctrl._last_fix is None and ctrl._skip_word
    assert ctrl.stats == {"corrections": 0, "manual": 0, "undo": 0}
    assert state["corrected"] == [] and state["exceptions"] == []


@pytest.mark.parametrize("outcome", [True, False, "error"])
def test_append_during_layout_wait_preserves_new_mode_even_on_failure(keyboard, outcome):
    keymap, backend, screen, abort = keyboard

    def wait(index, timeout):
        screen.physical()
        if outcome == "error":
            raise RuntimeError("wait failed")
        return outcome

    backend.wait_applied = wait
    plan = plan_fix(7, "привет ", RU, keymap, False)
    assert execute(plan, screen, backend, abort=abort, restore_group=US) == FAILED
    assert screen.text == "Test ghbdtn ф" and backend.current() == RU
    assert screen.deleted == screen.typed == [] and not screen.held


def test_append_during_set_exception_preserves_applied_mode(keyboard):
    keymap, backend, screen, abort = keyboard
    original = backend.set

    def set_layout(index):
        original(index)
        screen.physical()
        raise RuntimeError("set failed after applying")

    backend.set = set_layout
    plan = plan_fix(7, "привет ", RU, keymap, False)
    assert execute(plan, screen, backend, abort=abort, restore_group=US) == FAILED
    assert screen.text == "Test ghbdtn ф" and backend.current() == RU
    assert screen.deleted == screen.typed == [] and not screen.held


@pytest.mark.parametrize("outcome", [False, "error"])
def test_failed_layout_wait_without_physical_append_still_rolls_back(keyboard, outcome):
    keymap, backend, screen, abort = keyboard

    def wait(index, timeout):
        if outcome == "error":
            raise RuntimeError("wait failed")
        return False

    backend.wait_applied = wait
    plan = plan_fix(7, "привет ", RU, keymap, False)
    assert execute(plan, screen, backend, abort=abort, restore_group=US) == FAILED
    assert screen.text == "Test ghbdtn " and backend.current() == US
    assert screen.deleted == screen.typed == [] and not screen.held


@pytest.mark.parametrize("failure", [False, True])
def test_append_after_caps_setup_preserves_mode_and_releases_on_error(keyboard, failure):
    _, backend, screen, abort = keyboard
    screen.caps = True

    def event(code, value):
        if code == kc.KEY_CAPSLOCK and value == 0:
            screen.physical()
            if failure:
                raise OSError("release failed after application")

    screen.on_event = event
    assert execute(Plan([ToggleCaps()]), screen, backend, abort=abort) == FAILED
    assert screen.text == "Test ghbdtn a" and not screen.caps and not screen.held
    assert screen.deleted == screen.typed == []


def test_append_under_synthetic_shift_is_uncertain_before_first_letter(keyboard):
    keymap, backend, screen, abort = keyboard
    screen.on_event = lambda code, value: (
        screen.physical(kc.KEY_B) if code == kc.KEY_LEFTSHIFT and value == 1 else None)
    plan = plan_fix(0, "A", US, keymap, False, switch=False)
    assert execute(plan, screen, backend, abort=abort) == FAILED
    assert screen.text == "Test ghbdtn B" and not screen.held
    assert screen.deleted == screen.typed == []


def test_synthetic_shift_error_still_preserves_mode_used_by_append(keyboard):
    keymap, backend, screen, abort = keyboard

    def event(code, value):
        if code == kc.KEY_LEFTSHIFT and value == 1:
            screen.physical(kc.KEY_B)
            raise OSError("shift failed after physical input")

    screen.on_event = event
    plan = plan_fix(0, "П", RU, keymap, False)
    assert execute(plan, screen, backend, abort=abort, restore_group=US) == FAILED
    assert screen.text == "Test ghbdtn И" and backend.current() == RU
    assert screen.deleted == screen.typed == [] and not screen.held


def test_failed_hold_after_commit_stops_remaining_deletion(keyboard):
    keymap, backend, screen, abort = keyboard
    plan = plan_fix(7, "привет ", RU, keymap, False)
    assert execute(plan, screen, backend, abort=abort,
                   hold=lambda: not screen.deleted, restore_group=US) == FAILED
    assert screen.text == "Test ghbdtn" and screen.deleted == [" "]
    assert not screen.typed and not screen.held and backend.current() == RU


@pytest.mark.parametrize("failure", [False, True])
def test_failed_hold_that_delivers_append_is_probed_before_rollback(keyboard, failure):
    keymap, backend, screen, abort = keyboard

    def hold():
        screen.physical()
        if failure:
            raise RuntimeError("hold failed after physical input")
        return False

    plan = plan_fix(7, "привет ", RU, keymap, False)
    assert execute(plan, screen, backend, abort=abort, hold=hold, restore_group=US) == FAILED
    assert screen.text == "Test ghbdtn ф" and backend.current() == RU
    assert screen.deleted == screen.typed == [] and not screen.held


def test_failed_hold_without_mode_or_text_change_remains_untouched_abort(keyboard):
    keymap, backend, screen, abort = keyboard
    plan = plan_fix(7, "hello ", US, keymap, False, switch=False)
    assert execute(plan, screen, backend, abort=abort, hold=lambda: False) == ABORTED
    assert screen.text == "Test ghbdtn " and backend.current() == US
    assert screen.deleted == screen.typed == [] and not screen.held
