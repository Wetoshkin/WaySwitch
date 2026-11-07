"""Selection belongs to the captured field until the first synthetic write."""

import pytest

from tests.fakes import RecordingTextTypist, RecordingTypist, SafeProbe
from tests.test_selection import make_ctrl
from wayswitch import a11y
from wayswitch import keycodes as kc


class SelectionProbe(SafeProbe):
    password = False
    generation = 1
    field = (":test", "/field")

    def snapshot(self):
        return a11y.FocusSnapshot("editor", self.password, self.clock(),
                                  field=self.field, generation=self.generation)


def selection_env(monkeypatch, typist):
    ctrl, backend, corrected = make_ctrl("ghbdtn", typist)
    probe = SelectionProbe(ctrl.clock)
    ctrl.set_probe(probe)
    backend.name = "kde"
    backend.supports_selection = True
    state = {"start": 0, "end": 6, "text": "ghbdtn"}
    reads = []
    clipboard = []

    def read(expected, valid, limit):
        reads.append(expected)
        assert limit == 300
        return a11y.TextSelection(**state) if valid() else None

    def forbidden():
        clipboard.append(True)
        raise AssertionError("KDE must read the current AT-SPI selection")

    monkeypatch.setattr(a11y, "read_selection", read, raising=False)
    backend.get_selection = forbidden
    return ctrl, backend, corrected, probe, state, reads, clipboard


@pytest.mark.parametrize("typist_class", [RecordingTypist, RecordingTextTypist])
def test_kde_selection_reads_current_range_twice_without_clipboard(monkeypatch, typist_class):
    typist = typist_class()
    ctrl, _backend, corrected, _probe, _state, reads, clipboard = selection_env(
        monkeypatch, typist)
    assert ctrl.fix_selection()
    assert len(reads) == 2 and reads[0] == reads[1]
    assert clipboard == []
    assert ctrl.stats["manual"] == 1
    assert corrected == [("ghbdtn", "привет", True)]


@pytest.mark.parametrize("typist_class", [RecordingTypist, RecordingTextTypist])
@pytest.mark.parametrize("changed", ["range", "text"])
def test_selection_change_during_layout_wait_prevents_first_write(
        monkeypatch, typist_class, changed):
    typist = typist_class()
    ctrl, backend, corrected, _probe, state, reads, _clipboard = selection_env(
        monkeypatch, typist)

    def wait(index, timeout):
        if changed == "range":
            state.update(start=1, end=7)
        else:
            state["text"] = "rfrltk"
        return True

    backend.wait_applied = wait
    assert not ctrl.fix_selection()
    assert len(reads) == 2
    if isinstance(typist, RecordingTextTypist):
        assert typist.calls == []
    else:
        assert typist.taps() == []
    assert corrected == [] and ctrl.stats["manual"] == 0


def test_nested_selection_request_is_refused_before_reading_again(monkeypatch):
    typist = RecordingTextTypist()
    ctrl, _backend, _corrected, _probe, state, _reads, _clipboard = selection_env(
        monkeypatch, typist)
    nested = []
    calls = []

    def read(expected, valid, limit):
        calls.append(expected)
        nested.append(ctrl.fix_selection())
        return a11y.TextSelection(**state) if valid() else None

    monkeypatch.setattr(a11y, "read_selection", read)
    assert ctrl.fix_selection()
    assert nested == [False, False] and len(calls) == 2
    assert ctrl.stats["manual"] == 1


@pytest.mark.parametrize("kind", ["password", "unknown", "stale", "no-field"])
def test_selection_refuses_untrusted_focus_before_reading(monkeypatch, kind):
    typist = RecordingTextTypist()
    ctrl, _backend, _corrected, probe, _state, reads, clipboard = selection_env(
        monkeypatch, typist)
    snapshot = probe.snapshot()
    if kind == "password":
        snapshot = snapshot._replace(password=True)
    elif kind == "unknown":
        snapshot = a11y.FocusSnapshot()
    elif kind == "stale":
        snapshot = snapshot._replace(taken_at=ctrl.clock() - 10)
    else:
        snapshot = snapshot._replace(field=None)
    probe.snapshot = lambda: snapshot
    ctrl.config.general.skip_password_fields = False
    assert not ctrl.fix_selection("ghbdtn")
    assert reads == [] and clipboard == [] and typist.calls == []


def test_gnome_uses_current_selection_without_reading_stale_primary(monkeypatch):
    typist = RecordingTextTypist()
    ctrl, backend, corrected, _probe, _state, reads, _clipboard = selection_env(
        monkeypatch, typist)
    backend.name = "shell"
    clipboard = []
    backend.get_selection = lambda: clipboard.append(True) or "other application"
    assert ctrl.fix_selection()
    assert len(reads) == 2 and clipboard == []
    assert corrected == [("ghbdtn", "привет", True)]


def test_selection_focus_aba_does_not_reuse_same_field(monkeypatch):
    typist = RecordingTextTypist()
    ctrl, backend, corrected, probe, _state, _reads, _clipboard = selection_env(
        monkeypatch, typist)
    reads = []

    def read(expected, valid, limit):
        reads.append(expected)
        if len(reads) == 2:
            probe.generation += 2
        return a11y.TextSelection(start=0, end=6, text="ghbdtn") if valid() else None

    monkeypatch.setattr(a11y, "read_selection", read)
    assert not ctrl.fix_selection()
    assert len(reads) == 2 and typist.calls == [] and corrected == []


def test_selection_change_during_key_release_wait_prevents_first_write(monkeypatch):
    typist = RecordingTextTypist()
    ctrl, _backend, corrected, _probe, state, reads, _clipboard = selection_env(
        monkeypatch, typist)
    ctrl.buffer.note_press(kc.KEY_LEFTCTRL, 1)

    def pump():
        state.update(start=1, end=7)
        ctrl.buffer.note_release(kc.KEY_LEFTCTRL, 1)

    ctrl.pump = pump
    assert not ctrl.fix_selection()
    assert len(reads) == 2
    assert typist.calls == [] and corrected == [] and ctrl.stats["manual"] == 0
    assert ctrl.busy is False


def test_selection_probe_change_prevents_first_write(monkeypatch):
    typist = RecordingTextTypist()
    ctrl, _backend, corrected, _probe, _state, reads, _clipboard = selection_env(
        monkeypatch, typist)
    other = SelectionProbe(ctrl.clock)

    def read(expected, valid, limit):
        reads.append(expected)
        if len(reads) == 2:
            ctrl.set_probe(other)
        return a11y.TextSelection(start=0, end=6, text="ghbdtn") if valid() else None

    monkeypatch.setattr(a11y, "read_selection", read)
    assert not ctrl.fix_selection()
    assert len(reads) == 2
    assert typist.calls == [] and corrected == [] and ctrl.stats["manual"] == 0
    assert ctrl.busy is False


@pytest.mark.parametrize("during", ["read", "wait"])
@pytest.mark.parametrize("how", ["pause", "lock"])
def test_pause_or_lock_prevents_first_write(monkeypatch, during, how):
    typist = RecordingTextTypist()
    ctrl, backend, corrected, _probe, state, reads, _clipboard = selection_env(
        monkeypatch, typist)

    def stop_now():
        if how == "pause":
            ctrl.pause()
        else:
            ctrl.set_locked(True)

    if during == "read":
        def read(expected, valid, limit):
            reads.append(expected)
            if len(reads) == 2:
                stop_now()
            return a11y.TextSelection(**state) if valid() else None

        monkeypatch.setattr(a11y, "read_selection", read)
    else:
        def wait(index, timeout):
            stop_now()
            return True

        backend.wait_applied = wait
    assert not ctrl.fix_selection()
    # При паузе/блокировке во время ожидания execute замечает прерывание до
    # повторного чтения; при прерывании внутри чтения — before_write уже вызван.
    assert len(reads) == (2 if during == "read" else 1)
    assert typist.calls == [] and corrected == [] and ctrl.stats["manual"] == 0
    assert ctrl.busy is False


@pytest.mark.parametrize("kind", ["none", "tuple", "multiline", "short", "raises"])
def test_invalid_reader_response_refused_without_write(monkeypatch, caplog, kind):
    typist = RecordingTextTypist()
    ctrl, backend, corrected, _probe, _state, reads, _clipboard = selection_env(
        monkeypatch, typist)

    def read(expected, valid, limit):
        reads.append(expected)
        if kind == "none":
            return None
        if kind == "tuple":
            return (0, 6, "ghbdtn")
        if kind == "multiline":
            return a11y.TextSelection(0, 7, "ghbd\ntn")
        if kind == "short":
            return a11y.TextSelection(0, 6, "abc")
        raise RuntimeError("private selected text payload")

    monkeypatch.setattr(a11y, "read_selection", read)
    assert not ctrl.fix_selection()
    assert len(reads) == 1
    assert typist.calls == [] and corrected == [] and backend.set_calls == []
    assert ctrl.stats["manual"] == 0 and ctrl.busy is False
    if kind == "raises":
        assert "private selected text payload" not in caplog.text


@pytest.mark.parametrize("explicit", ["ghbdtn", "другой"])
def test_explicit_text_only_matches_confirmed_selection(monkeypatch, explicit):
    typist = RecordingTextTypist()
    ctrl, backend, corrected, _probe, _state, reads, _clipboard = selection_env(
        monkeypatch, typist)
    assert ctrl.fix_selection(explicit) is (explicit == "ghbdtn")
    assert len(reads) == (2 if explicit == "ghbdtn" else 1)
    if explicit == "ghbdtn":
        assert typist.calls == [("type", "привет")]
        assert corrected == [("ghbdtn", "привет", True)]
    else:
        assert typist.calls == [] and corrected == [] and backend.set_calls == []
    assert ctrl.stats["manual"] == (1 if explicit == "ghbdtn" else 0)


@pytest.mark.parametrize("action,source,expected", [
    ("translit", "Привет", "Privet"),
    ("numbers", "1 250", "одна тысяча двести пятьдесят"),
])
def test_all_transformations_use_the_bound_reader(monkeypatch, action, source, expected):
    typist = RecordingTextTypist()
    ctrl, _backend, corrected, _probe, state, reads, clipboard = selection_env(
        monkeypatch, typist)
    state.update(start=0, end=len(source), text=source)
    assert ctrl.transform_selection(action)
    assert len(reads) == 2 and reads[0] == reads[1] and clipboard == []
    assert typist.calls == [("type", expected)]
    assert ctrl.stats["manual"] == 1
    assert corrected == [(source, expected, True)]


def test_selection_status_reflects_safe_probe_not_backend_flag(monkeypatch):
    ctrl, backend, *_ = selection_env(monkeypatch, RecordingTextTypist())
    assert ctrl.status()["selection_supported"] is True
    backend.supports_selection = False
    assert ctrl.status()["selection_supported"] is True
    ctrl.set_probe(a11y.NullProbe())
    assert ctrl.status()["selection_supported"] is False
    ctrl.set_probe(None)
    assert ctrl.status()["selection_supported"] is False



def test_capture_source_change_is_rejected_before_any_selection_read(monkeypatch):
    typist = RecordingTextTypist()
    ctrl, _backend, corrected, probe, _state, reads, _clipboard = selection_env(
        monkeypatch, typist)
    replacement = SelectionProbe(ctrl.clock)
    original = probe.snapshot
    calls = 0

    def snapshot():
        nonlocal calls
        calls += 1
        value = original()
        if calls == 2:
            ctrl.set_probe(replacement)
        return value

    probe.snapshot = snapshot
    assert not ctrl.fix_selection()
    assert reads == [] and typist.calls == [] and corrected == []
    assert not ctrl.busy


def test_malformed_focus_is_refused_without_exception_or_text_read(monkeypatch):
    typist = RecordingTextTypist()
    ctrl, _backend, corrected, probe, _state, reads, _clipboard = selection_env(
        monkeypatch, typist)
    probe.snapshot = lambda: object()
    assert not ctrl.fix_selection()
    assert reads == [] and typist.calls == [] and corrected == []
    assert not ctrl.busy


@pytest.mark.parametrize("change", ["pause", "lock", "probe", "generation"])
def test_invalidation_while_releasing_keys_cannot_start_writing(monkeypatch, change):
    typist = RecordingTextTypist()
    ctrl, _backend, corrected, probe, _state, _reads, _clipboard = selection_env(
        monkeypatch, typist)

    def wait(_timeout):
        if change == "pause":
            ctrl.pause()
        elif change == "lock":
            ctrl.set_locked(True)
        elif change == "probe":
            ctrl.set_probe(SelectionProbe(ctrl.clock))
        else:
            probe.generation += 2
        return True

    monkeypatch.setattr(ctrl, "_wait_all_released", wait)
    assert not ctrl.fix_selection()
    assert typist.calls == [] and corrected == [] and ctrl.stats["manual"] == 0
    assert not ctrl.busy


@pytest.mark.parametrize("change", ["pause", "lock"])
def test_invalidation_inside_reader_does_not_write(monkeypatch, change):
    typist = RecordingTextTypist()
    ctrl, _backend, corrected, _probe, state, _reads, _clipboard = selection_env(
        monkeypatch, typist)

    def read(_expected, _valid, _limit):
        ctrl.pause() if change == "pause" else ctrl.set_locked(True)
        return a11y.TextSelection(**state)

    monkeypatch.setattr(a11y, "read_selection", read)
    assert not ctrl.fix_selection()
    assert typist.calls == [] and corrected == [] and ctrl.stats["manual"] == 0
    assert not ctrl.busy


@pytest.mark.parametrize("value", [a11y.TextSelection(-1, 5, "ghbdtn"),
                                   a11y.TextSelection(0, 301, "x" * 301)])

def test_bad_selection_reply_is_refused_without_writing(monkeypatch, value):
    typist = RecordingTextTypist()
    ctrl, _backend, corrected, _probe, _state, _reads, _clipboard = selection_env(
        monkeypatch, typist)
    monkeypatch.setattr(a11y, "read_selection", lambda *_args: value)
    assert not ctrl.fix_selection()
    assert typist.calls == [] and corrected == [] and ctrl.stats["manual"] == 0
    assert not ctrl.busy




def test_failed_selection_discards_old_word_after_physical_input(monkeypatch):
    typist = RecordingTextTypist()
    ctrl, _backend, corrected, _probe, _state, _reads, _clipboard = selection_env(
        monkeypatch, typist)
    ctrl.config.general.auto_correct = False
    for code in (kc.KEY_G, kc.KEY_H, kc.KEY_B, kc.KEY_D, kc.KEY_T, kc.KEY_N):
        ctrl.on_key(code, 1, 1)
        ctrl.on_key(code, 0, 1)

    def read(*_args):
        ctrl.on_key(kc.KEY_X, 1, 1)
        ctrl.on_key(kc.KEY_X, 0, 1)
        return None

    monkeypatch.setattr(a11y, "read_selection", read)
    assert not ctrl.fix_selection()
    ctrl.manual_word()
    assert typist.calls == [] and corrected == [] and ctrl.stats["manual"] == 0
    assert not ctrl.busy
