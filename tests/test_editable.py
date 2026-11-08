"""Bounded native word edits never use a keyboard fallback after an attempt."""

import sys
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from wayswitch import a11y, editable


def test_span_result_contract():
    failed = editable.SpanResult("failed")
    assert failed.status == "failed" and failed.committed is False
    assert failed.source_text is None and failed.target_text is None
    done = editable.SpanResult("done", True)
    assert done.committed is True and done.source_text is None and done.target_text is None


def test_non_libreoffice_has_no_native_attempt(monkeypatch):
    def forbidden():
        raise AssertionError("No native connection for another app")

    monkeypatch.setattr(a11y, "_new_a11y_connection", forbidden)
    focus = a11y.FocusSnapshot("chrome", taken_at=time.monotonic(), field=(":1.2", "/field"))
    assert editable.try_replace(focus, "hello", "руддщ", forbidden, lambda: False) is None


@pytest.mark.parametrize("component", ["libreoffice-calc", "libreoffice-impress"])
def test_writer_native_path_does_not_open_other_libreoffice_components(
        native, monkeypatch, component):
    calls = []
    monkeypatch.setattr(a11y, "_new_a11y_connection", lambda: calls.append(True))
    focus = native.focus._replace(app_ids=(component, "org.libreoffice"))
    result = editable.try_replace(focus, SOURCE, TARGET, native.commit, native.stopped)
    assert calls == []
    assert result is None


def test_direct_native_range_requires_writer_component(native, monkeypatch):
    native.focus = native.focus._replace(app_ids=("libreoffice-calc", "org.libreoffice"))
    native.context = native.context._replace(app_ids=native.focus.app_ids)
    monkeypatch.setattr(a11y, "query_shell_context", lambda: native.context)
    assert replace(native) == editable.SpanResult("aborted")
    assert native.tree.reads == native.tree.writes == native.committed == []


FIELD = (":1.2", "/field")
SOURCE = "ghbdtn "
TARGET = "привет "


class Tree:
    def __init__(self, events):
        self.text = "Test " + SOURCE
        self.position = len(self.text)
        self.events = events
        self.reads = []
        self.writes = []
        self.editable = True
        self.focused = True
        self.kind = ""
        self.selections = 0
        self.insert_reply = self.delete_reply = True
        self.noop = False
        self.insert_error = None
        self.delete_error = None
        self.after_insert = lambda: None
        self.after_delete = lambda: None
        self.insert_event_start = self.delete_event_start = None

    def state(self, node):
        assert node == FIELD
        return [(1 << a11y.EDITABLE if self.editable else 0)
                | (1 << a11y.FOCUSED if self.focused else 0)]

    def role(self, node):
        return 73

    def interfaces(self, node):
        return [a11y.TEXT, "org.a11y.atspi.EditableText"]

    def input_type(self, node):
        return self.kind

    def app_of(self, node):
        return "soffice"

    def pid(self, node):
        return 1234

    def caret(self, node):
        return self.position

    def selection_count(self, node):
        return self.selections

    def character_count(self, node):
        return len(self.text)

    def text_range(self, node, start, end):
        assert node == FIELD and 0 <= start <= end and end - start <= 1024
        self.reads.append((start, end))
        return self.text[start:end]

    def insert_text(self, node, start, text):
        assert node == FIELD
        self.writes.append(("insert", start, text))
        if not self.noop:
            self.text = self.text[:start] + text + self.text[start:]
            if self.position >= start:
                self.position += len(text)
            offset = start if self.insert_event_start is None else self.insert_event_start
            self.events.append(("insert", offset, len(text)))
        self.after_insert()
        if self.insert_error:
            raise self.insert_error
        return self.insert_reply

    def delete_text(self, node, start, end):
        assert node == FIELD
        self.writes.append(("delete", start, end))
        if self.delete_error:
            raise self.delete_error
        self.text = self.text[:start] + self.text[end:]
        self.position = self.position - (end - start) if self.position >= end else min(
            self.position, start)
        offset = start if self.delete_event_start is None else self.delete_event_start
        self.events.append(("delete", offset, end - start))
        self.after_delete()
        return self.delete_reply

    def append(self, text):
        start = self.position
        self.text = self.text[:start] + text + self.text[start:]
        self.position += len(text)
        self.events.append(("insert", start, len(text)))


@pytest.fixture
def native(monkeypatch):
    events = []
    tree = Tree(events)
    context = a11y.ShellContext(("libreoffice-writer",), False, True, 0, 0, 1234)
    monkeypatch.setattr(a11y, "query_shell_context", lambda: context)
    focus = a11y.FocusSnapshot("soffice", False, time.monotonic(), context.app_ids, FIELD)
    committed = []
    return SimpleNamespace(tree=tree, events=events, context=context, focus=focus,
                           commit=lambda: committed.append(True), committed=committed,
                           stopped=lambda: False)


def replace(native, source=SOURCE, target=TARGET):
    return editable._replace(native.tree, native.focus, source, target, native.commit,
                             native.stopped, native.events)


def test_insert_first_exact_replace_preserves_physical_append(native):
    native.tree.after_insert = lambda: native.tree.append("X")
    assert replace(native) == editable.SpanResult("done", True)
    assert native.tree.text == "Test привет X"
    assert native.tree.position == 13
    assert native.tree.writes == [("insert", 5, TARGET), ("delete", 12, 19)]
    assert native.committed == [True]


@pytest.mark.parametrize("change", [
    lambda n: setattr(n, "focus", n.focus._replace(password=True)),
    lambda n: setattr(n, "focus", n.focus._replace(app=None)),
    lambda n: setattr(n, "focus", n.focus._replace(taken_at=0)),
    lambda n: setattr(n.tree, "editable", False),
    lambda n: setattr(n.tree, "focused", False),
    lambda n: setattr(n.tree, "kind", "password"),
    lambda n: setattr(n.tree, "kind", None),
    lambda n: setattr(n.tree, "selections", 1),
    lambda n: setattr(n, "stopped", lambda: True),
])
def test_unsafe_metadata_never_reads_or_writes(native, change):
    change(native)
    assert replace(native).status in {"aborted", "failed"}
    assert not native.tree.reads and not native.tree.writes and not native.committed


def test_source_mismatch_is_local_and_never_logged(native, caplog, capsys):
    native.tree.text = "Test secret!"
    assert replace(native) == editable.SpanResult("aborted")
    assert not native.tree.writes
    assert "secret" not in caplog.text + str(capsys.readouterr())


@pytest.mark.parametrize("source,target", [("a" * 513, "a"), ("a", "b" * 513),
                                           ("a😀", "test"), (SOURCE, "😀")])
def test_oversized_or_unproven_utf16_spans_never_read(native, source, target):
    assert replace(native, source, target).status in {"aborted", "failed"}
    assert not native.tree.reads and not native.tree.writes


@pytest.mark.parametrize("mode", ["noop", "false", "timeout", "error"])
def test_uncertain_insert_preserves_original_without_retry(native, mode):
    if mode == "noop":
        native.tree.noop = True
    elif mode == "false":
        native.tree.insert_reply = False
    else:
        native.tree.insert_error = RuntimeError("Timeout" if mode == "timeout" else "private")
    assert replace(native) == editable.SpanResult("failed", True)
    assert SOURCE in native.tree.text
    assert native.tree.writes == [("insert", 5, TARGET)]


@pytest.mark.parametrize("mode", ["focus", "protected", "selection", "edit", "source"])
def test_disruption_after_insertion_never_deletes_original(native, mode):
    def disrupt():
        if mode == "focus":
            native.tree.focused = False
        elif mode == "protected":
            native.tree.kind = "password"
        elif mode == "selection":
            native.tree.selections = 1
        elif mode == "edit":
            native.stopped = lambda: True
        else:
            native.tree.text = native.tree.text[:12] + "CHANGED"

    native.tree.after_insert = disrupt
    if mode == "edit":
        # The closure observes the new stopped function after the mutation.
        def stopped():
            return native.stopped()
    else:
        stopped = native.stopped
    result = editable._replace(native.tree, native.focus, SOURCE, TARGET, native.commit,
                               stopped, native.events)
    assert result == editable.SpanResult("failed", True)
    assert native.tree.writes == [("insert", 5, TARGET)]


def test_equivalent_diff_positions_in_duplicate_text(native):
    native.tree.text = "Test " + TARGET
    native.tree.position = len(native.tree.text)
    native.tree.insert_event_start = 12
    native.tree.delete_event_start = 5
    assert replace(native, TARGET, TARGET * 2) == editable.SpanResult("done", True)
    assert native.tree.text == "Test " + TARGET * 2


def test_unrelated_insert_event_cannot_confirm_attempt(native):
    native.tree.insert_event_start = 0
    assert replace(native) == editable.SpanResult("failed", True)
    assert native.tree.writes == [("insert", 5, TARGET)]


def test_delete_timeout_does_not_repeat_or_fall_back(native):
    native.tree.delete_error = RuntimeError("Timeout with secret payload")
    assert replace(native) == editable.SpanResult("failed", True)
    assert native.tree.writes == [("insert", 5, TARGET), ("delete", 12, 19)]
    assert SOURCE in native.tree.text


def test_shell_context_change_before_attempt_never_reads(native, monkeypatch):
    monkeypatch.setattr(a11y, "query_shell_context",
                        lambda: native.context._replace(purpose=8))
    assert replace(native).status in {"aborted", "failed"}
    assert not native.tree.reads and not native.tree.writes


def test_invalid_selection_metadata_is_not_zero_selections(native):
    native.tree.selections = False
    assert replace(native).status in {"aborted", "failed"}
    assert not native.tree.reads and not native.tree.writes


def test_commit_callback_error_does_not_attempt_native_mutation(native):
    def commit():
        raise RuntimeError("layout prelude failed before commitment")

    result = editable._replace(native.tree, native.focus, SOURCE, TARGET, commit,
                               native.stopped, native.events)
    assert result == editable.SpanResult("failed")
    assert not native.tree.writes


def test_cancelled_commit_prelude_never_inserts(native):
    result = editable._replace(native.tree, native.focus, SOURCE, TARGET, lambda: False,
                               native.stopped, native.events)
    assert result == editable.SpanResult("aborted")
    assert native.tree.text == "Test " + SOURCE
    assert not native.tree.writes


def test_commit_prelude_append_does_not_move_the_reserved_word_anchor(native):
    def commit():
        native.committed.append(True)
        native.tree.append("X")
        return True

    result = editable._replace(native.tree, native.focus, SOURCE, TARGET, commit,
                               native.stopped, native.events)
    assert result == editable.SpanResult("done", True)
    assert native.tree.text == "Test " + TARGET + "X"
    assert native.tree.position == 13
    assert native.tree.writes == [("insert", 5, TARGET), ("delete", 12, 19)]


@pytest.mark.parametrize("protection", ["focus", "password", "shell-password"])
def test_unsafe_focus_after_commit_prelude_never_inserts_or_reads(native, monkeypatch, protection):
    def commit():
        if protection == "focus":
            native.tree.focused = False
        elif protection == "password":
            native.tree.kind = "password"
        else:
            monkeypatch.setattr(a11y, "query_shell_context",
                                lambda: native.context._replace(purpose=8))
        return True

    result = editable._replace(native.tree, native.focus, SOURCE, TARGET, commit,
                               native.stopped, native.events)
    assert result == editable.SpanResult("failed", True)
    assert native.tree.reads == [(5, 12)]
    assert not native.tree.writes


@pytest.mark.parametrize("failed", [False, "raise"])
def test_outer_commit_failure_keeps_uncommitted_result(native, monkeypatch, failed):
    connection = SimpleNamespace(close_sync=lambda _cancellable: None)
    monkeypatch.setattr(a11y, "_new_a11y_connection", lambda: connection)
    monkeypatch.setattr(a11y, "_DbusTree", lambda _conn: native.tree)

    @contextmanager
    def changes(_connection, _field):
        yield native.events

    monkeypatch.setattr(editable, "_text_changes", changes)

    def commit():
        if failed == "raise":
            raise RuntimeError("prelude failed")
        return False

    result = editable.try_replace(native.focus, SOURCE, TARGET, commit, native.stopped)
    assert result == editable.SpanResult("failed" if failed == "raise" else "aborted")
    assert not native.tree.writes


def test_outer_cleanup_failure_cannot_forget_attempt(native, monkeypatch):
    def failed_close(_cancellable):
        raise RuntimeError("bus has closed")

    connection = SimpleNamespace(close_sync=failed_close)
    monkeypatch.setattr(a11y, "_new_a11y_connection", lambda: connection)
    monkeypatch.setattr(a11y, "_DbusTree", lambda _conn: native.tree)

    @contextmanager
    def failed_cleanup(_conn, _field):
        yield native.events
        raise RuntimeError("provider disconnected during unsubscribe")

    monkeypatch.setattr(editable, "_text_changes", failed_cleanup)
    assert editable.try_replace(native.focus, SOURCE, TARGET, native.commit,
                                native.stopped) == editable.SpanResult("failed", True)
    assert native.tree.text == "Test " + TARGET


def test_context_becomes_password_after_insert_never_reads_again(native, monkeypatch):
    def protected():
        monkeypatch.setattr(a11y, "query_shell_context",
                            lambda: native.context._replace(purpose=8))

    native.tree.after_insert = protected
    assert replace(native) == editable.SpanResult("failed", True)
    assert native.tree.reads == [(5, 12)]
    assert native.tree.writes == [("insert", 5, TARGET)]


@pytest.mark.parametrize("bad_event", [("insert", False, 7), ("insert", 5, True),
                                        ("insert", 5, 0), ("delete", 5, -1),
                                        ("private", 5, 7), ("insert", -1, 7)])
def test_malformed_events_do_not_authorize_source_deletion(native, bad_event):
    native.tree.after_insert = lambda: native.events.append(bad_event)
    assert replace(native) == editable.SpanResult("failed", True)
    assert native.tree.writes == [("insert", 5, TARGET)]


def test_maximum_span_read_is_bounded(native):
    source, target = "a" * 512, "б" * 512
    native.tree.text = "Test " + source
    native.tree.position = len(native.tree.text)
    assert replace(native, source, target) == editable.SpanResult("done", True)
    assert max(end - start for start, end in native.tree.reads) == 1024


@pytest.mark.parametrize("toolkit,size", [("gtk", 13), ("Qt", 7)])
def test_dbus_native_methods_use_toolkit_insert_units_and_provider_range_offsets(toolkit, size):
    tree = object.__new__(a11y._DbusTree)
    tree._GLib = SimpleNamespace(Variant=lambda signature, args: (signature, args))
    calls = []

    def call(node, interface, method, args, reply):
        assert node == ((FIELD[0], "/app") if method == "Get" and args[1][1] == "ToolkitName"
                        else FIELD)
        calls.append((interface, method, args, reply))
        if method == "GetApplication":
            return ((FIELD[0], "/app"),)
        if method == "Get" and args[1][1] == "ToolkitName":
            return (toolkit,)
        return {"GetText": (TARGET,), "InsertText": (True,), "DeleteText": (True,),
                "GetNSelections": (0,), "Get": (12,)}[method]

    tree._call = call
    assert tree.text_range(FIELD, 5, 12) == TARGET
    assert tree.insert_text(FIELD, 5, TARGET) is True
    assert tree.delete_text(FIELD, 12, 19) is True
    assert tree.selection_count(FIELD) == 0
    assert tree.character_count(FIELD) == 12
    assert calls == [
        (a11y.TEXT, "GetText", ("(ii)", (5, 12)), "(s)"),
        (a11y.ACC, "GetApplication", None, "((so))"),
        ("org.freedesktop.DBus.Properties", "Get",
         ("(ss)", ("org.a11y.atspi.Application", "ToolkitName")), "(v)"),
        (editable.EDITABLE, "InsertText", ("(isi)", (5, TARGET, size)), "(b)"),
        (editable.EDITABLE, "DeleteText", ("(ii)", (12, 19)), "(b)"),
        (a11y.TEXT, "GetNSelections", None, "(i)"),
        ("org.freedesktop.DBus.Properties", "Get",
         ("(ss)", (a11y.TEXT, "CharacterCount")), "(v)"),
    ]


@pytest.mark.parametrize("toolkit,size", [("Qt", 3), ("gtk", 6)])
def test_dbus_insert_non_bmp_uses_utf16_units_only_for_qt(toolkit, size):
    tree = object.__new__(a11y._DbusTree)
    tree._GLib = SimpleNamespace(Variant=lambda signature, args: (signature, args))
    tree._property = lambda *_args: toolkit
    calls = []

    def call(node, interface, method, args, reply):
        calls.append((method, args))
        return ((FIELD[0], "/app"),) if method == "GetApplication" else (True,)

    tree._call = call
    assert tree.insert_text(FIELD, 0, "😀я") is True
    assert calls[-1] == ("InsertText", ("(isi)", (0, "😀я", size)))


@pytest.mark.parametrize("application,toolkit", [
    (None, "Qt"), (((":other", "/app"),), "Qt"),
    (((FIELD[0], "/app"),), None), (((FIELD[0], "/app"),), ""),
])
def test_dbus_insert_without_proven_toolkit_does_not_write(application, toolkit):
    tree = object.__new__(a11y._DbusTree)
    tree._GLib = SimpleNamespace(Variant=lambda signature, args: (signature, args))
    tree._property = lambda *_args: toolkit
    calls = []

    def call(node, interface, method, args, reply):
        calls.append(method)
        return application if method == "GetApplication" else (True,)

    tree._call = call
    assert tree.insert_text(FIELD, 0, TARGET) is None
    assert "InsertText" not in calls


@pytest.mark.parametrize("start,end", [(0, 1025), (-1, 5), (5, 4), (False, 5)])
def test_dbus_text_bounds_do_not_reach_provider(start, end):
    tree = object.__new__(a11y._DbusTree)

    def forbidden(*_args):
        raise AssertionError("Malformed range reached provider")

    tree._call = forbidden
    assert tree.text_range(FIELD, start, end) is None


class Parameter:
    def __init__(self, value, signature):
        self.value, self.signature = value, signature

    def get_type_string(self):
        return self.signature

    def get_string(self):
        assert self.signature == "s"
        return self.value

    def get_int32(self):
        assert self.signature == "i"
        return self.value


class NumericOnlyEvent:
    def __init__(self, kind="insert", start=5, length=7):
        self.children = [Parameter(kind, "s"), Parameter(start, "i"), Parameter(length, "i")]

    def get_child_value(self, index):
        assert index < 3, "Event text/application payload must never be read"
        return self.children[index]

    def unpack(self):
        raise AssertionError("Complete event payload must never be unpacked")


@pytest.fixture
def event_bus(monkeypatch):
    calls = []
    connection = SimpleNamespace()

    def subscribe(sender, interface, member, path, detail, flags, callback, data):
        assert sender == FIELD[0] and path == FIELD[1] and member == "TextChanged"
        connection.callback = callback
        calls.append("subscribe")
        return 5

    connection.signal_subscribe = subscribe
    connection.signal_unsubscribe = lambda ident: calls.append(("unsubscribe", ident))
    connection.call_sync = lambda *args: calls.append(args[3])
    gio = SimpleNamespace(DBusSignalFlags=SimpleNamespace(NONE=0),
                          DBusCallFlags=SimpleNamespace(NONE=0))
    glib = SimpleNamespace(Variant=lambda signature, args: (signature, args), VariantType=str)
    monkeypatch.setitem(sys.modules, "gi.repository", SimpleNamespace(Gio=gio, GLib=glib))
    return connection, calls


def deliver(connection, params):
    connection.callback(connection, FIELD[0], FIELD[1], "org.a11y.atspi.Event.Object",
                        "TextChanged", params, None)


def test_subscription_reads_only_numeric_category_and_always_closes(event_bus):
    connection, calls = event_bus
    with pytest.raises(RuntimeError), editable._text_changes(connection, FIELD) as events:
        deliver(connection, NumericOnlyEvent())
        assert events == [("insert", 5, 7)]
        raise RuntimeError("transaction interrupted")
    assert calls == ["subscribe", "RegisterEvent", ("unsubscribe", 5), "DeregisterEvent"]


def test_subscription_malformed_prefix_and_event_flood_are_bounded(event_bus):
    connection, _calls = event_bus
    with editable._text_changes(connection, FIELD) as events:
        malformed = NumericOnlyEvent()
        malformed.children[2].signature = "v"
        deliver(connection, malformed)
        assert events == [("invalid", -1, -1)]
        for _ in range(1000):
            deliver(connection, NumericOnlyEvent())
        assert len(events) == 65


def test_failed_registration_still_removes_subscription(event_bus):
    connection, calls = event_bus

    def failed(*_args):
        raise RuntimeError("registry unavailable")

    connection.call_sync = failed
    with pytest.raises(RuntimeError), editable._text_changes(connection, FIELD):
        pytest.fail("No transaction without a registered listener")
    assert calls == ["subscribe", ("unsubscribe", 5)]


def test_non_bmp_prefix_uses_the_same_providers_raw_offsets(native):
    class UTF16Tree(Tree):
        def character_count(self, node):
            return len(self.text.encode("utf-16-le")) // 2

        def text_range(self, node, start, end):
            self.reads.append((start, end))
            return self.text.encode("utf-16-le")[start * 2:end * 2].decode("utf-16-le")

        def insert_text(self, node, start, text):
            self.writes.append(("insert", start, text))
            encoded = self.text.encode("utf-16-le")
            self.text = (encoded[:start * 2] + text.encode("utf-16-le")
                         + encoded[start * 2:]).decode("utf-16-le")
            self.position += len(text)
            self.events.append(("insert", start, len(text)))
            return True

        def delete_text(self, node, start, end):
            self.writes.append(("delete", start, end))
            encoded = self.text.encode("utf-16-le")
            self.text = (encoded[:start * 2] + encoded[end * 2:]).decode("utf-16-le")
            self.position -= end - start
            self.events.append(("delete", start, end - start))
            return True

    native.tree = UTF16Tree(native.events)
    native.tree.text = "😀Test " + SOURCE
    native.tree.position = 14
    assert replace(native) == editable.SpanResult("done", True)
    assert native.tree.text == "😀Test " + TARGET
    assert native.tree.position == 14
    assert native.tree.writes == [("insert", 7, TARGET), ("delete", 14, 21)]
    assert all(start >= 7 for start, _ in native.tree.reads)


def test_unknown_event_category_never_keeps_arbitrary_text(event_bus):
    connection, _calls = event_bus
    with editable._text_changes(connection, FIELD) as events:
        deliver(connection, NumericOnlyEvent(kind="private-field-contents"))
        assert events == [("invalid", -1, -1)]


@pytest.mark.parametrize("position", [20, 999999])
def test_append_outside_sequential_document_length_does_not_authorize_deletion(native, position):
    def impossible_append():
        native.tree.append("X")
        native.events[-1] = ("insert", position, 1)

    native.tree.after_insert = impossible_append
    assert replace(native) == editable.SpanResult("failed", True)
    assert native.tree.text == "Test " + TARGET + SOURCE + "X"
    assert native.tree.writes == [("insert", 5, TARGET)]


@pytest.mark.parametrize("events,before,after", [
    ([("insert", 5, 7), ("insert", 999999, 1)], 12, 20),
    ([("insert", 5, 7), ("delete", 18, 2)], 12, 17),
    ([("insert", 5, 7)], -1, 6),
    ([("insert", 5, 7)], True, 8),
    ([("delete", 5, 7)], 12, -1),
])
def test_numeric_edit_evidence_rejects_impossible_document_coordinates(events, before, after):
    assert not editable._edits(events, SOURCE, TARGET + SOURCE, 5, "insert", 7, before, after)


def test_native_libreoffice_accepts_its_verified_flatpak_proxy(native, monkeypatch):
    native.tree.pid = lambda _node: 456
    identities = {1234: ("org.libreoffice.LibreOffice", "987654"),
                  456: ("org.libreoffice.LibreOffice", "987654")}
    monkeypatch.setattr(a11y, "_flatpak_instance", identities.get)
    assert replace(native) == editable.SpanResult("done", True)
    assert native.tree.text == "Test " + TARGET


@pytest.mark.parametrize("proxy", [None, ("org.libreoffice.LibreOffice", "111111"),
                                     ("org.example.Other", "987654")])
def test_native_other_or_unknown_flatpak_instance_never_reads(native, monkeypatch, proxy):
    native.tree.pid = lambda _node: 456
    identities = {1234: ("org.libreoffice.LibreOffice", "987654"), 456: proxy}
    monkeypatch.setattr(a11y, "_flatpak_instance", identities.get)
    assert replace(native) == editable.SpanResult("aborted")
    assert not native.tree.reads and not native.tree.writes


def test_native_direct_pid_match_never_opens_flatpak_metadata(native, monkeypatch):
    def forbidden(_pid):
        raise AssertionError("Native PID match must not consult Flatpak metadata")

    monkeypatch.setattr(a11y, "_flatpak_instance", forbidden)
    assert replace(native) == editable.SpanResult("done", True)


@pytest.mark.parametrize("pid,focused_pid", [(True, 1), (1, True), (0, 1), (1, 0),
                                            (-1, 1), (1, -1), ("1", 1), (1, "1"), (None, 1)])
def test_same_application_rejects_invalid_pid_before_metadata(monkeypatch, pid, focused_pid):
    def forbidden(_pid):
        raise AssertionError("Invalid PID must not consult process metadata")

    monkeypatch.setattr(a11y, "_flatpak_instance", forbidden)
    assert not a11y._same_application(pid, focused_pid)


def test_focus_scope_rejects_boolean_pid_equal_to_one():
    nodes = {"root": ["app"], "app": ["window"], "window": ["field"], "field": []}
    tree = SimpleNamespace(
        children=nodes.get, app_name=lambda _node: "soffice", pid=lambda _node: True,
        state=lambda node: [(1 << a11y.ACTIVE) if node == "window"
                            else (1 << a11y.FOCUSED) | (1 << a11y.EDITABLE)],
        role=lambda node: 73 if node == "field" else 23,
        prepare=lambda _node: None, interfaces=lambda _node: [a11y.TEXT],
        input_type=lambda _node: "", caret=lambda _node: 0, app_of=lambda _node: "soffice")
    assert a11y.find_focus(tree, "root", 1) == a11y.FocusSnapshot()


@pytest.mark.parametrize("source,actual,target,resolved", [
    (SOURCE, "Ghbdtn ", TARGET, "Привет "),
    (TARGET, "Привет ", SOURCE, "Ghbdtn "),
    (SOURCE, "GHBDTN ", TARGET, "ПРИВЕТ "),
    ("GHBDTN ", SOURCE, "ПРИВЕТ ", TARGET),
    ("ghbdtn vbh ", "Ghbdtn vbh ", "привет мир ", "Привет мир "),
    ("привет ", "Привет ", "GHbdtn ", "GHbdtn "),
])
def test_case_equivalent_displayed_source_preserves_actual_case(
        native, source, actual, target, resolved):
    native.tree.text = "Test " + actual
    native.tree.position = len(native.tree.text)
    assert replace(native, source, target) == editable.SpanResult("done", True, actual, resolved)
    assert native.tree.text == "Test " + resolved
    assert native.tree.position == len(native.tree.text)
    assert native.tree.writes[0] == ("insert", 5, resolved)


@pytest.mark.parametrize("source,target", [("пРИВЕТ ", "Привет "), ("ПРивет ", "Привет "),
                                          ("GHBDTN ", TARGET)])
def test_exact_source_preserves_planned_caps_and_two_capitals_fix(native, source, target):
    native.tree.text = "Test " + source
    native.tree.position = len(native.tree.text)
    assert replace(native, source, target) == editable.SpanResult("done", True)
    assert native.tree.text == "Test " + target


@pytest.mark.parametrize("actual,source,target", [(SOURCE, SOURCE, SOURCE),
                                                  ("Ghbdtn ", SOURCE, "Ghbdtn "),
                                                  ("Привет ", SOURCE, TARGET)])
def test_already_correct_displayed_word_never_mutates(native, actual, source, target):
    native.tree.text = "Test " + actual
    native.tree.position = len(native.tree.text)
    assert replace(native, source, target) == editable.SpanResult("aborted")
    assert not native.tree.writes and not native.committed


def test_mixed_case_formatter_mismatch_stays_private_and_is_not_guessed(native):
    native.tree.text = "Test gHbDtN "
    native.tree.position = len(native.tree.text)
    assert replace(native) == editable.SpanResult("aborted")
    assert not native.tree.writes and not native.committed


def test_case_adjustment_rechecks_expanded_target_bounds(native):
    native.tree.text = "Test ẞ"
    native.tree.position = len(native.tree.text)
    assert replace(native, "ß", "ß" * 512) == editable.SpanResult("aborted")
    assert native.tree.reads == [(5, 6)]
    assert not native.tree.writes and not native.committed


def test_case_adjusted_source_is_not_returned_after_failed_insertion(native):
    native.tree.text = "Test Ghbdtn "
    native.tree.position = len(native.tree.text)
    native.tree.insert_error = RuntimeError("provider error containing private field data")
    result = replace(native)
    assert result == editable.SpanResult("failed", True)
    assert result.source_text is None and result.target_text is None


def test_case_adjusted_source_is_not_returned_after_focus_becomes_protected(native):
    native.tree.text = "Test Ghbdtn "
    native.tree.position = len(native.tree.text)
    native.tree.after_insert = lambda: setattr(native.tree, "kind", "password")
    assert replace(native) == editable.SpanResult("failed", True)


# --- KDE: нативный параграф Writer через живой AT-SPI -------------------------

KDE_APP = (":1.2", "/app")
KDE_WINDOW = (":1.2", "/window")


class KdeTree(Tree):
    def __init__(self, events):
        super().__init__(events)
        self.app = "soffice.bin"
        self.field_role = 73
        self.owner_pid = 1234
        self.pid_values = None
        self.pid_calls = 0

    def children(self, node):
        return {a11y.ROOT: [KDE_APP], KDE_APP: [KDE_WINDOW],
                KDE_WINDOW: [FIELD]}.get(node, [])

    def app_name(self, node):
        return self.app if node == KDE_APP else ""

    def prepare(self, _node):
        pass

    def state(self, node):
        if node == FIELD:
            return super().state(node)
        return [(1 << a11y.ACTIVE) if node == KDE_WINDOW else 0]

    def role(self, node):
        return self.field_role if node == FIELD else 23

    def interfaces(self, node):
        return super().interfaces(node) if node == FIELD else [a11y.TEXT]

    def input_type(self, node):
        return self.kind if node == FIELD else ""

    def app_of(self, node):
        return self.app

    def pid(self, node):
        self.pid_calls += 1
        if self.pid_values is not None:
            return self.pid_values[min(self.pid_calls - 1, len(self.pid_values) - 1)]
        return self.owner_pid


@pytest.fixture
def kde_native(monkeypatch):
    events = []
    tree = KdeTree(events)
    context = a11y.ShellContext()
    monkeypatch.setattr(a11y, "query_shell_context", lambda: context)
    focus = a11y.FocusSnapshot("soffice.bin", False, time.monotonic(), (), FIELD)
    connection = SimpleNamespace(close_sync=lambda _cancellable: None)

    @contextmanager
    def changes(_connection, _field):
        yield events

    monkeypatch.setattr(a11y, "_new_a11y_connection", lambda: connection)
    monkeypatch.setattr(a11y, "_DbusTree", lambda _conn: tree)
    monkeypatch.setattr(editable, "_text_changes", changes)
    committed = []
    native = SimpleNamespace(tree=tree, events=events, context=context, focus=focus,
                             connection=connection, committed=committed, frozen=None,
                             commit=lambda: committed.append(True))
    # Снимок контроллера: свежий на каждый вызов, пока опрос не остановлен.
    native.focus_snapshot = lambda: (native.frozen if native.frozen is not None
                                     else native.focus._replace(
                                         taken_at=time.monotonic()))
    native.stopped = KdeStop(native.focus_snapshot)
    return native


def kde_replace(native, source=SOURCE, target=TARGET):
    return editable.try_replace_kde(native.focus, source, target, native.commit,
                                    native.stopped)


class KdeStop:
    """Подставной стоп-колбэк KDE-native: снимок фокуса, якорь и счётчик ввода."""

    def __init__(self, live_snapshot):
        self._live_snapshot = live_snapshot
        self.aborted = False
        self.anchor = False
        self.prepare_result = True
        self.on_prepare = lambda: None
        self.append = lambda: None
        self.fast_switch = False

    def __call__(self, *, before_write=False):
        return self.aborted and (before_write or not self.anchor)

    def prepare(self):
        self.on_prepare()
        return self.prepare_result

    def reserve_anchor(self):
        self.anchor = True
        return True

    def use_fast_switch(self):
        self.fast_switch = True

    def append_count(self):
        return self.append()

    def focus_snapshot(self):
        return self._live_snapshot()


def test_kde_native_prepare_requests_fast_layout_switch(kde_native):
    assert kde_replace(kde_native) == editable.SpanResult("done", True)
    assert kde_native.stopped.fast_switch is True


@pytest.mark.parametrize("failure", [None, "no-counter", "wrong-counter", "wrong-caret",
                                    "missing-own-event", "inside-event"])
def test_kde_missing_append_event_requires_independent_physical_proof(kde_native, failure):
    physical = KdeStop(kde_native.focus_snapshot)
    count = [0]
    physical.append = lambda: None if failure == "no-counter" else count[0]
    kde_native.stopped = physical

    def append_silently(letter):
        tree = kde_native.tree
        pos = tree.position
        tree.text = tree.text[:pos] + letter + tree.text[pos:]
        tree.position += 1
        count[0] += 1

    def after_insert():
        append_silently("X")
        if failure == "wrong-counter":
            count[0] += 1
        elif failure == "wrong-caret":
            kde_native.tree.position -= 1
        elif failure == "missing-own-event":
            kde_native.events.clear()
        elif failure == "inside-event":
            kde_native.events.append(("insert", 0, 1))

    kde_native.tree.after_insert = after_insert
    kde_native.tree.after_delete = lambda: append_silently("Y")
    result = kde_replace(kde_native)
    if failure is None:
        assert result == editable.SpanResult("done", True)
        assert kde_native.tree.text == "Test " + TARGET + "XY"
    else:
        assert result == editable.SpanResult("failed", True)
        assert all(operation[0] != "delete" for operation in kde_native.tree.writes)
        assert SOURCE in kde_native.tree.text


def test_kde_append_during_recheck_after_anchor_is_preserved(kde_native):
    physical = KdeStop(kde_native.focus_snapshot)
    provider = kde_native.focus_snapshot
    scans = []

    def append_on_second_scan():
        scans.append(True)
        live = provider()
        if len(scans) == 2:
            kde_native.tree.append("X")
            physical.aborted = True
        return live

    physical._live_snapshot = append_on_second_scan
    assert editable.try_replace_kde(
        kde_native.focus, SOURCE, TARGET, kde_native.commit, physical
    ) == editable.SpanResult("done", True)
    assert kde_native.tree.text == "Test " + TARGET + "X"
    assert kde_native.tree.writes == [("insert", 5, TARGET), ("delete", 12, 19)]
    assert kde_native.committed == [True]


def test_kde_unrecorded_append_before_insert_still_balances_counts(kde_native, monkeypatch):
    kde_native.tree.text = "Test ghbd"
    kde_native.tree.position = len(kde_native.tree.text)

    @contextmanager
    def changes(_connection, _field):
        # Печать пришла до регистрации TextChanged: в события её нет.
        kde_native.tree.append("ет ")
        kde_native.events.clear()
        yield kde_native.events

    monkeypatch.setattr(editable, "_text_changes", changes)
    assert kde_replace(kde_native, "ghbd", "прив") == editable.SpanResult("done", True)
    assert kde_native.tree.text == "Test привет "
    assert kde_native.tree.writes == [("insert", 5, "прив"), ("delete", 9, 13)]


def test_kde_pre_anchor_append_cancels_before_reads_or_writes(kde_native):
    physical = KdeStop(kde_native.focus_snapshot)
    provider = kde_native.focus_snapshot

    def append_before_anchor():
        live = provider()
        kde_native.tree.append("X")
        physical.aborted = True
        return live

    physical._live_snapshot = append_before_anchor
    assert editable.try_replace_kde(
        kde_native.focus, SOURCE, TARGET, kde_native.commit, physical
    ) == editable.SpanResult("aborted")
    assert not physical.anchor
    assert kde_native.tree.reads == kde_native.tree.writes == []
    assert not kde_native.committed


def test_kde_live_snapshot_replaces_full_focus_scans(kde_native, monkeypatch):
    scans = []

    def forbidden(*_args):
        scans.append(True)
        raise AssertionError("a11y.find_focus остаётся вне горячего пути")

    monkeypatch.setattr(a11y, "find_focus", forbidden)
    assert kde_replace(kde_native) == editable.SpanResult("done", True)
    assert scans == []
    assert kde_native.tree.text == "Test " + TARGET


@pytest.mark.parametrize("proof", [
    lambda now: None,
    lambda now: a11y.FocusSnapshot(),
    lambda now: a11y.FocusSnapshot(None, False, now, (), FIELD),
    lambda now: a11y.FocusSnapshot("soffice.bin", False, now - a11y.MAX_AGE_SEC - .1,
                                   (), FIELD),
    lambda now: a11y.FocusSnapshot("other.bin", False, now, (), FIELD),
    lambda now: a11y.FocusSnapshot("soffice.bin", False, now, (), (":1.2", "/other")),
    lambda now: a11y.FocusSnapshot("soffice.bin", True, now, (), FIELD),
    lambda now: a11y.FocusSnapshot("soffice.bin", False, now, (), FIELD, generation=7),
])
def test_kde_live_focus_proof_must_be_same_fresh_unprotected_field(kde_native, proof):
    kde_native.stopped = KdeStop(lambda: proof(time.monotonic()))
    assert kde_replace(kde_native) == editable.SpanResult("aborted")
    assert kde_native.tree.reads == kde_native.tree.writes == kde_native.committed == []


def test_kde_physical_press_between_anchor_and_prepare_aborts(kde_native):
    physical = KdeStop(kde_native.focus_snapshot)
    count = [0]
    physical.append = lambda: count[0]

    def press_during_prepare():
        # Нажатие до подтверждения раскладки набралось бы в старой раскладке.
        count[0] += 1

    physical.on_prepare = press_during_prepare
    kde_native.stopped = physical
    assert kde_replace(kde_native) == editable.SpanResult("aborted")
    assert kde_native.tree.text == "Test " + SOURCE
    assert kde_native.tree.reads == kde_native.tree.writes == kde_native.committed == []


def test_kde_physical_press_during_prepare_refuses_anchor(kde_native):
    physical = KdeStop(kde_native.focus_snapshot)
    physical.prepare_result = False
    kde_native.stopped = physical
    assert kde_replace(kde_native) == editable.SpanResult("aborted")
    assert not physical.anchor
    assert kde_native.tree.reads == kde_native.tree.writes == kde_native.committed == []


def test_kde_native_paragraph_success(kde_native):
    assert kde_replace(kde_native) == editable.SpanResult("done", True)
    assert kde_native.tree.text == "Test " + TARGET
    assert kde_native.tree.writes == [("insert", 5, TARGET), ("delete", 12, 19)]
    assert kde_native.committed == [True]


def test_kde_long_edit_uses_fresh_live_focus_proofs(kde_native, monkeypatch):
    clock = [kde_native.focus.taken_at]
    monkeypatch.setattr(editable.time, "monotonic", lambda: clock[0])
    read = kde_native.tree.text_range

    def delayed_read(*args):
        clock[0] += .8
        return read(*args)

    kde_native.tree.text_range = delayed_read
    assert kde_replace(kde_native) == editable.SpanResult("done", True)
    assert clock[0] - kde_native.focus.taken_at > a11y.MAX_AGE_SEC
    assert kde_native.tree.text == "Test " + TARGET


def test_kde_gap_between_live_proofs_retains_original(kde_native, monkeypatch):
    clock = [kde_native.focus.taken_at]
    monkeypatch.setattr(editable.time, "monotonic", lambda: clock[0])

    def stop_refreshing():
        clock[0] += a11y.MAX_AGE_SEC + .1
        kde_native.frozen = kde_native.focus  # опрос фокуса перестал обновлять снимок

    kde_native.tree.after_insert = stop_refreshing
    assert kde_replace(kde_native) == editable.SpanResult("failed", True)
    assert kde_native.tree.text == "Test " + TARGET + SOURCE
    assert kde_native.tree.writes == [("insert", 5, TARGET)]


def test_kde_slow_live_proof_cannot_renew_trust(kde_native, monkeypatch):
    clock = [kde_native.focus.taken_at]
    monkeypatch.setattr(editable.time, "monotonic", lambda: clock[0])
    input_type = kde_native.tree.input_type

    def delayed_type(node):
        clock[0] += a11y.MAX_AGE_SEC + .1
        return input_type(node)

    kde_native.tree.input_type = delayed_type
    check = editable._kde_guard(kde_native.tree, kde_native.focus, kde_native.context,
                                kde_native.stopped)
    assert check() == "abort"
    assert kde_native.tree.reads == kde_native.tree.writes == []


@pytest.mark.parametrize("app", ["soffice", "libreoffice", "soffice.bin"])
def test_kde_accepts_only_recognized_libreoffice_identity(kde_native, app):
    kde_native.tree.app = app
    kde_native.focus = kde_native.focus._replace(app=app)
    assert kde_replace(kde_native) == editable.SpanResult("done", True)


def test_kde_displayed_case_source_is_resolved(kde_native):
    kde_native.tree.text = "Test Ghbdtn "
    kde_native.tree.position = len(kde_native.tree.text)
    assert kde_replace(kde_native) == editable.SpanResult(
        "done", True, "Ghbdtn ", "Привет ")
    assert kde_native.tree.text == "Test Привет "


def test_kde_prefix_suffix_and_physical_append_are_preserved(kde_native):
    kde_native.tree.text = "Lead Test " + SOURCE + " tail"
    kde_native.tree.position = len("Lead Test " + SOURCE)
    kde_native.tree.after_insert = lambda: kde_native.tree.append("X")
    assert kde_replace(kde_native) == editable.SpanResult("done", True)
    assert kde_native.tree.text == "Lead Test " + TARGET + "X tail"
    assert kde_native.tree.writes == [("insert", 10, TARGET), ("delete", 17, 24)]


@pytest.mark.parametrize("change", [
    lambda n: setattr(n, "focus", n.focus._replace(password=True)),
    lambda n: setattr(n, "focus", n.focus._replace(app=None)),
    lambda n: setattr(n, "focus", n.focus._replace(taken_at=0)),
    lambda n: setattr(n, "focus", n.focus._replace(field=None)),
    lambda n: setattr(n.tree, "editable", False),
    lambda n: setattr(n.tree, "focused", False),
    lambda n: setattr(n.tree, "kind", "password"),
    lambda n: setattr(n.tree, "kind", None),
    lambda n: setattr(n.tree, "selections", 1),
    lambda n: setattr(n.tree, "owner_pid", 0),
    lambda n: setattr(n.tree, "owner_pid", True),
    lambda n: setattr(n, "stopped", lambda: True),
    lambda n: setattr(n.tree, "field_role", None),
])
def test_kde_unsafe_metadata_never_reads_or_writes(kde_native, change):
    change(kde_native)
    assert kde_replace(kde_native).status == "aborted"
    assert not kde_native.tree.reads and not kde_native.tree.writes
    assert not kde_native.committed


def test_kde_moved_field_is_cancelled_before_any_read(kde_native):
    kde_native.focus = kde_native.focus._replace(field=(":1.2", "/other"))
    assert kde_replace(kde_native).status == "aborted"
    assert not kde_native.tree.reads and not kde_native.tree.writes
    assert not kde_native.committed


def test_kde_wrong_application_owner_identity_is_cancelled(kde_native):
    kde_native.tree.app_of = lambda _node: "other.bin"
    assert kde_replace(kde_native).status == "aborted"
    assert not kde_native.tree.reads and not kde_native.tree.writes


def test_kde_changed_owner_pid_is_cancelled_before_read(kde_native):
    kde_native.tree.pid_values = [1234, 5678]
    assert kde_replace(kde_native).status == "aborted"
    assert not kde_native.tree.reads and not kde_native.tree.writes
    assert not kde_native.committed


def test_kde_changed_shell_context_is_cancelled_before_read(kde_native, monkeypatch):
    contexts = [kde_native.context, kde_native.context._replace(app_ids=("other",))]
    monkeypatch.setattr(a11y, "query_shell_context",
                        lambda: contexts.pop(0) if contexts else
                        kde_native.context._replace(app_ids=("other",)))
    assert kde_replace(kde_native).status == "aborted"
    assert not kde_native.tree.reads and not kde_native.tree.writes


@pytest.mark.parametrize("context", [
    a11y.ShellContext(("libreoffice-writer",), True, True, 8, 0, 1234),
    a11y.ShellContext(("libreoffice-writer",), False, True, 0, 64, 1234),
])
def test_kde_protected_shell_context_is_never_accepted(kde_native, monkeypatch, context):
    monkeypatch.setattr(a11y, "query_shell_context", lambda: context)
    assert kde_replace(kde_native).status == "aborted"
    assert not kde_native.tree.reads and not kde_native.tree.writes


def test_kde_safe_unsupported_role_returns_none_before_subscription(kde_native, monkeypatch):
    kde_native.tree.field_role = 61
    entered = []

    @contextmanager
    def changes(_connection, _field):
        entered.append(True)
        yield kde_native.events

    monkeypatch.setattr(editable, "_text_changes", changes)
    assert kde_replace(kde_native) is None
    assert not entered
    assert not kde_native.tree.reads and not kde_native.tree.writes
    assert not kde_native.committed


@pytest.mark.parametrize("change", [
    lambda n: setattr(n.tree, "kind", "password"),
    lambda n: setattr(n.tree, "owner_pid", 0),
    lambda n: setattr(n.tree, "selections", 1),
    lambda n: setattr(n, "focus", n.focus._replace(taken_at=0)),
])
def test_kde_unsupported_role_only_when_live_metadata_is_safe(kde_native, change):
    kde_native.tree.field_role = 61
    change(kde_native)
    assert kde_replace(kde_native).status == "aborted"
    assert not kde_native.tree.reads and not kde_native.tree.writes


def test_kde_non_libreoffice_returns_none_without_native_connection(kde_native, monkeypatch):
    def forbidden():
        raise AssertionError("No native connection for another KDE app")

    monkeypatch.setattr(a11y, "_new_a11y_connection", forbidden)
    kde_native.focus = kde_native.focus._replace(app="chrome")
    assert kde_replace(kde_native) is None
    assert not kde_native.tree.reads and not kde_native.tree.writes


def test_gnome_entry_still_requires_writer_app_id_for_kde_style_focus(monkeypatch):
    calls = []
    monkeypatch.setattr(a11y, "_new_a11y_connection", lambda: calls.append(True))
    focus = a11y.FocusSnapshot("soffice.bin", False, time.monotonic(), (), FIELD)
    assert editable.try_replace(focus, SOURCE, TARGET, lambda: True, lambda: False) is None
    assert calls == []


@pytest.mark.parametrize("role", [0, 40, 60, 67, 999])
def test_kde_role_changed_after_live_focus_never_enables_key_fallback(kde_native, role):
    # Снимок фокуса контроллера подтверждает поле, но роль узла уже не та.
    kde_native.tree.field_role = role
    assert kde_replace(kde_native) == editable.SpanResult("aborted")
    assert kde_native.tree.reads == kde_native.tree.writes == kde_native.committed == []


@pytest.mark.parametrize("context", [
    a11y.ShellContext(("other.desktop",), False, True, 0, 0, 9999),
    a11y.ShellContext((), False, True, 0, 0, 1234),
    a11y.ShellContext((), False, False, 0, 0, 1234),
])
def test_kde_rejects_unprotected_context_from_another_session(
        kde_native, monkeypatch, context):
    monkeypatch.setattr(a11y, "query_shell_context", lambda: context)
    assert kde_replace(kde_native) == editable.SpanResult("aborted")
    assert kde_native.tree.reads == kde_native.tree.writes == kde_native.committed == []


def test_kde_rejects_snapshot_with_conflicting_shell_identity(kde_native):
    kde_native.focus = kde_native.focus._replace(app_ids=("libreoffice-writer",))
    assert kde_replace(kde_native) == editable.SpanResult("aborted")
    assert kde_native.tree.reads == kde_native.tree.writes == kde_native.committed == []
