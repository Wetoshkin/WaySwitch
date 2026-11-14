"""Selection text is read only from a freshly verified normal field."""

import time
from types import SimpleNamespace

import pytest

from tests.test_focus import DictTree
from wayswitch import a11y

APP, WINDOW, PANEL = "app", "window", "panel"
FIELD = (":1.2", "/field")


class SelectionTree(DictTree):
    def __init__(self):
        super().__init__({
            a11y.ROOT: {"kids": [APP]},
            APP: {"name": "kate", "pid": 123, "kids": [WINDOW]},
            WINDOW: {"role": 23, "state": (a11y.ACTIVE,), "kids": [PANEL]},
            PANEL: {"role": 20, "state": (a11y.SHOWING,), "kids": [FIELD]},
            FIELD: {"role": 61, "state": (a11y.FOCUSED, a11y.EDITABLE),
                    "interfaces": (a11y.TEXT,), "caret": 11},
        })
        self.count = 16
        self.selections = 1
        self.bounds = (5, 11)
        self.reply = "ghbdtn"
        self.after_range = lambda: None
        self.after_focus = lambda: None
        self.after_read = lambda: None

    def prepare(self, node):
        super().prepare(node)
        self.after_focus()

    def character_count(self, node):
        assert node == FIELD
        return self.count

    def selection_count(self, node):
        assert node == FIELD
        return self.selections

    def selection_range(self, node):
        assert node == FIELD
        bounds = self.bounds
        self.after_range()
        return bounds

    def text_range(self, node, start, end):
        self.reads.append((node, start, end))
        reply = self.reply
        self.after_read()
        return reply


@pytest.fixture
def selection(monkeypatch):
    case = SimpleNamespace(
        tree=SelectionTree(), valid=True, context=a11y.ShellContext(),
        expected=a11y.FocusSnapshot("kate", taken_at=time.monotonic(), field=FIELD,
                                   generation=9))
    monkeypatch.setattr(a11y, "query_shell_context", lambda *, fresh=False: case.context)
    return case


def read(case, limit=300):
    return a11y._read_selection(case.tree, case.expected, lambda: case.valid, limit)


def test_selection_epoch_change_during_read_rejects_same_context(selection, monkeypatch):
    watch = SimpleNamespace(generation=1)
    monkeypatch.setattr(a11y, "_SHELL_WATCH", watch)
    selection.tree.after_read = lambda: setattr(watch, "generation", 3)
    assert read(selection) is None


@pytest.mark.parametrize("gnome", [False, True])
def test_reads_only_selected_range_of_verified_field(selection, gnome):
    if gnome:
        selection.context = a11y.ShellContext(("org.kde.kate",), pid=123)
        selection.expected = selection.expected._replace(app_ids=("org.kde.kate",))
    result = read(selection)
    assert isinstance(result, a11y.TextSelection)
    assert result == (5, 11, "ghbdtn")
    assert selection.tree.reads == [(FIELD, 5, 11)]


@pytest.mark.parametrize("change", [
    {"state": (a11y.FOCUSED,)}, {"state": (a11y.DEFUNCT, a11y.FOCUSED, a11y.EDITABLE)},
    {"role": 40}, {"role": 0}, {"interfaces": ()}, {"caret": None},
    {"input_type": "password"}, {"input_type": None},
])
def test_unsafe_field_has_zero_text_reads(selection, change):
    selection.tree.nodes[FIELD].update(change)
    assert read(selection) is None
    assert selection.tree.reads == []


@pytest.mark.parametrize("node,change", [
    (WINDOW, {"state": ()}),
    (WINDOW, {"state": (a11y.ACTIVE, a11y.DEFUNCT)}),
    (PANEL, {"state": (a11y.DEFUNCT,)}),
    (PANEL, {"role": 40}), (PANEL, {"role": 0}),
    (PANEL, {"input_type": "password"}),
])
def test_ancestor_or_inactive_window_cannot_authorize_leaf(selection, node, change):
    selection.tree.nodes[node].update(change)
    assert read(selection) is None
    assert selection.tree.reads == []


@pytest.mark.parametrize("change", [
    {"app": None}, {"app": "other"}, {"password": True}, {"field": None},
    {"field": (":1.2", "/other")}, {"taken_at": 0},
    {"taken_at": float("nan")}, {"taken_at": float("inf")},
    {"taken_at": 1}, {"app_ids": ("stale.desktop",)},
])
def test_untrusted_expected_snapshot_has_zero_text_reads(selection, change):
    selection.expected = selection.expected._replace(**change)
    assert read(selection) is None
    assert selection.tree.reads == []


@pytest.mark.parametrize("context", [None, a11y.ShellContext(modal=True),
                                        a11y.ShellContext(ime_focus=True, purpose=8),
                                        a11y.ShellContext(("another.desktop",)),
                                        a11y.ShellContext(pid=999)])
def test_unknown_protected_or_mismatched_context_has_zero_text_reads(selection, context):
    selection.context = context
    assert read(selection) is None
    assert selection.tree.reads == []


@pytest.mark.parametrize("value", [None, False, True, 0, 2, 1.0, "1"])
def test_requires_exactly_one_selection(selection, value):
    selection.tree.selections = value
    assert read(selection) is None
    assert selection.tree.reads == []


@pytest.mark.parametrize("value", [None, True, -1, 0, 1.0, "16", 0x80000000])
def test_bad_character_count_has_zero_text_reads(selection, value):
    selection.tree.count = value
    assert read(selection) is None
    assert selection.tree.reads == []


@pytest.mark.parametrize("value", [
    None, (), (1,), (0, 1, 2), (5, 5), (11, 5), (-1, 5), (5, 17),
    (True, 11), (5, False), (5.0, 11), (5, "11"), 11,
])
def test_bad_range_has_zero_text_reads(selection, value):
    selection.tree.bounds = value
    assert read(selection) is None
    assert selection.tree.reads == []


@pytest.mark.parametrize("limit", [None, True, False, 0, -1, 5, 1.0])
def test_invalid_or_exceeded_limit_has_zero_text_reads(selection, limit):
    assert read(selection, limit) is None
    assert selection.tree.reads == []


def test_selection_longer_than_limit_is_not_read(selection):
    selection.tree.count = 400
    selection.tree.bounds = (0, 301)
    assert read(selection) is None
    assert selection.tree.reads == []


@pytest.mark.parametrize("text", [None, b"ghbdtn", 42, "short", "gh\nbdt", "gh\rbdt"])
def test_malformed_or_multiline_text_is_not_returned(selection, text):
    selection.tree.reply = text
    assert read(selection) is None
    assert selection.tree.reads == [(FIELD, 5, 11)]


def test_unicode_codepoint_offsets_accept_non_bmp(selection):
    selection.tree.bounds = (5, 7)
    selection.tree.reply = "🙂a"
    assert read(selection) == (5, 7, "🙂a")


def test_utf16_length_mismatch_is_not_returned(selection):
    selection.tree.bounds = (5, 8)
    selection.tree.reply = "🙂a"
    assert read(selection) is None


@pytest.mark.parametrize("change", ["field", "nofocus", "app", "password", "readonly",
                                    "count", "range", "multiple", "generation", "context",
                                    "context_ids", "context_unknown", "context_pid"])
def test_post_read_changes_never_return_selection(selection, change):
    def mutate():
        if change == "field":
            other = (":1.2", "/other")
            selection.tree.nodes[other] = dict(selection.tree.nodes[FIELD])
            selection.tree.nodes[PANEL]["kids"] = [other]
        elif change == "nofocus":
            selection.tree.nodes[PANEL]["kids"] = []
        elif change == "app":
            selection.tree.nodes[APP]["name"] = "other"
        elif change == "password":
            selection.tree.nodes[PANEL]["input_type"] = "password"
        elif change == "readonly":
            selection.tree.nodes[FIELD]["state"] = (a11y.FOCUSED,)
        elif change == "count":
            selection.tree.count += 1
        elif change == "range":
            selection.tree.bounds = (6, 12)
        elif change == "multiple":
            selection.tree.selections = 2
        elif change == "generation":
            selection.valid = False
        elif change == "context_ids":
            selection.context = a11y.ShellContext(("other.desktop",))
        elif change == "context_unknown":
            selection.context = None
        elif change == "context_pid":
            selection.context = a11y.ShellContext(pid=999)
        else:
            selection.context = a11y.ShellContext(modal=True)

    selection.tree.after_read = mutate
    assert read(selection) is None
    assert selection.tree.reads == [(FIELD, 5, 11)]


def test_focus_change_during_metadata_is_caught_before_text(selection):
    selection.tree.after_range = lambda: selection.tree.nodes[PANEL].update(role=40)
    assert read(selection) is None
    assert selection.tree.reads == []


@pytest.mark.parametrize("change", ["count", "range", "multiple"])
def test_selection_change_during_focus_revalidation_prevents_text_read(selection, change):
    def mutate():
        if change == "count":
            selection.tree.count += 1
        elif change == "range":
            selection.tree.bounds = (6, 12)
        else:
            selection.tree.selections = 2

    selection.tree.after_focus = mutate
    assert read(selection) is None
    assert selection.tree.reads == []


def test_fresh_other_normal_field_is_rejected_before_text(selection):
    other = (":1.2", "/other")
    selection.tree.nodes[other] = dict(selection.tree.nodes[FIELD])
    selection.tree.nodes[PANEL]["kids"] = [other]
    assert read(selection) is None
    assert selection.tree.reads == []


def test_generation_change_during_metadata_prevents_text_read(selection):
    selection.tree.after_range = lambda: setattr(selection, "valid", False)
    assert read(selection) is None
    assert selection.tree.reads == []


@pytest.mark.parametrize("value", [False, None, 1, "yes"])
def test_generation_callback_requires_true_before_text(selection, value):
    selection.valid = value
    assert read(selection) is None
    assert selection.tree.reads == []


def test_provider_exception_is_not_logged(selection, caplog):
    def fail():
        raise RuntimeError("private provider payload")

    selection.tree.after_range = fail
    assert read(selection) is None
    assert selection.tree.reads == []
    assert "private provider payload" not in caplog.text


def test_generation_callback_exception_is_not_logged(selection, caplog):
    def fail():
        raise RuntimeError("private provider payload")

    assert a11y._read_selection(selection.tree, selection.expected, fail, 300) is None
    assert selection.tree.reads == []
    assert "private provider payload" not in caplog.text


def test_get_selection_uses_one_index_and_pair_reply():
    tree = object.__new__(a11y._DbusTree)
    tree._GLib = SimpleNamespace(Variant=lambda sig, data: (sig, data))
    calls = []
    tree._call = lambda *args: calls.append(args) or (5, 11)
    assert tree.selection_range(FIELD) == (5, 11)
    assert calls == [(FIELD, a11y.TEXT, "GetSelection", ("(i)", (0,)), "(ii)")]


@pytest.mark.parametrize("reply", [None, (1,), ("1", 2), (True, 2), (1, False),
                                   (1, 2, 3), 1])
def test_get_selection_rejects_malformed_pair(reply):
    tree = object.__new__(a11y._DbusTree)
    tree._GLib = SimpleNamespace(Variant=lambda sig, data: (sig, data))
    tree._call = lambda *args: reply
    assert tree.selection_range(FIELD) is None


@pytest.mark.parametrize("failure", [None, "tree", "provider", "read"])
def test_public_reader_closes_owned_connection_and_never_uses_global(
        selection, monkeypatch, failure, caplog):
    closed = []
    connection = SimpleNamespace(close_sync=lambda unused: closed.append(True))
    monkeypatch.setattr(a11y, "_new_a11y_connection", lambda: connection)

    def global_forbidden():
        raise AssertionError("global connection forbidden")

    def tree_factory(conn):
        assert conn is connection
        if failure == "tree":
            raise RuntimeError("private provider payload")
        return selection.tree

    monkeypatch.setattr(a11y, "_a11y_connection", global_forbidden)
    monkeypatch.setattr(a11y, "_DbusTree", tree_factory)
    if failure == "provider":
        selection.tree.selections = None
    if failure == "read":
        selection.tree.after_read = lambda: (_ for _ in ()).throw(
            RuntimeError("private provider payload"))
    result = a11y.read_selection(selection.expected, lambda: True, 300)
    assert result == ((5, 11, "ghbdtn") if failure is None else None)
    assert closed == [True]
    assert "private provider payload" not in caplog.text


@pytest.mark.parametrize("change", [{"taken_at": 0}, {"password": True}, {"field": None}])
def test_public_reader_rejects_unsafe_capture_before_connecting(selection, monkeypatch, change):
    opened = []
    monkeypatch.setattr(a11y, "_new_a11y_connection", lambda: opened.append(True))
    expected = selection.expected._replace(**change)
    assert a11y.read_selection(expected, lambda: True, 300) is None
    assert opened == []
