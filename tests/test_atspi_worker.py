"""Тесты постоянного AT-SPI worker (#58)."""

import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

from tests.fakes import RU, US, FakeBackend, RecordingTypist, make_keymap
from tests.test_focus import DictTree
from wayswitch import a11y, editable
from wayswitch.actuator import ABORTED, UNCERTAIN, Backspace, Plan, SwitchLayout, Type, execute
from wayswitch.atspi_worker import AtspiWorker, CancelToken, is_bus_closed_error
from wayswitch.config import Config
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel

FIELD = (":1.9", "/field")


def _fake_conn():
    return SimpleNamespace(is_closed=lambda: False, close_sync=lambda _c: None)


class WorkerTree(DictTree):
    def __init__(self, events):
        super().__init__({
            a11y.ROOT: {"kids": []},
            FIELD: {
                "role": 73,
                "state": (a11y.FOCUSED, a11y.EDITABLE),
                "interfaces": (a11y.TEXT, editable.EDITABLE),
                "input_type": "text",
                "caret": 11,
                "app_name": "soffice",
                "pid": 123,
            },
        })
        self.events = events
        self.text = "Test ghbdtn"
        self.position = len(self.text)
        self.selections = 0
        self.bounds = (5, 11)
        self.writes = []
        self.threads = []
        self.on_insert = None
        self.on_delete = None

    def app_of(self, node):
        self.threads.append(threading.get_ident())
        return self.nodes[node].get("app_name")

    def pid(self, node):
        return self.nodes[node].get("pid")

    def character_count(self, node):
        return len(self.text)

    def caret(self, node):
        return self.position

    def selection_count(self, node):
        return self.selections

    def selection_range(self, node):
        return self.bounds

    def text_range(self, node, start, end):
        self.reads.append((node, start, end))
        return self.text[start:end]

    def insert_text(self, node, start, value):
        self.threads.append(threading.get_ident())
        self.writes.append(("insert", start, value))
        if self.on_insert is not None:
            return self.on_insert(start, value)
        self.text = self.text[:start] + value + self.text[start:]
        self.position += len(value)
        self.events.append(("insert", start, len(value)))
        return True

    def delete_text(self, node, start, end):
        self.writes.append(("delete", start, end))
        if self.on_delete is not None:
            return self.on_delete(start, end)
        self.text = self.text[:start] + self.text[end:]
        self.position -= end - start
        self.events.append(("delete", start, end - start))
        return True


def make_focus(generation=1, app="soffice", app_ids=("libreoffice-writer",)):
    return a11y.FocusSnapshot(
        app, False, time.monotonic(), app_ids, FIELD,
        window="doc — LibreOffice Writer", generation=generation,
    )


def test_worker_reuses_single_connection_in_owner_thread_and_skips_bfs(monkeypatch):
    def forbidden_bfs(*_args, **_kwargs):
        raise AssertionError("direct selection check must not run full find_focus BFS")

    monkeypatch.setattr(a11y, "find_focus", forbidden_bfs)
    events = []
    tree = WorkerTree(events)
    tree.selections = 1
    tree.nodes[FIELD]["role"] = 61
    conns, conn_threads, closed = [], [], []

    def make_conn():
        conn_threads.append(threading.get_ident())
        conn = SimpleNamespace(
            is_closed=lambda: False,
            close_sync=lambda _c: closed.append(threading.get_ident()),
        )
        conns.append(conn)
        return conn

    @contextmanager
    def changes(_conn, _field):
        yield events

    ctx = a11y.ShellContext(("libreoffice-writer",), ime_focus=True, pid=123)
    worker = AtspiWorker(
        connection_factory=make_conn,
        tree_factory=lambda _c: tree,
        changes_factory=changes,
        context_provider=lambda: ctx,
        main_invoke=lambda fn: fn(),
    )
    try:
        focus = make_focus(generation=1)
        assert worker.read_selection(focus, lambda: True, 300) == (5, 11, "ghbdtn")
        assert worker.read_selection(focus, lambda: True, 300) == (5, 11, "ghbdtn")

        tree.selections = 0
        tree.nodes[FIELD]["role"] = 73
        res = worker.replace_span(focus, "ghbdtn", "привет", lambda: True, lambda: False)
        assert res == editable.SpanResult("done", True)
        assert tree.text == "Test привет"

        assert len(conns) == 1
        assert conn_threads[0] != threading.get_ident()
        assert set(tree.threads) == {conn_threads[0]}
    finally:
        worker.close(timeout=1.0)
    assert closed == conn_threads


def test_worker_provider_service_unknown_does_not_drop_bus_connection():
    events = []
    tree = WorkerTree(events)
    tree.selections = 1
    conns = []

    def make_conn():
        conn = _fake_conn()
        conns.append(conn)
        return conn

    def broken_range(*_args):
        raise RuntimeError("GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown: node gone")

    orig_range = tree.selection_range
    tree.selection_range = broken_range
    ctx = a11y.ShellContext(("libreoffice-writer",), ime_focus=True, pid=123)
    worker = AtspiWorker(
        connection_factory=make_conn,
        tree_factory=lambda _c: tree,
        context_provider=lambda: ctx,
        main_invoke=lambda fn: fn(),
    )
    try:
        focus = make_focus()
        assert worker.read_selection(focus, lambda: True, 300) is None
        tree.selection_range = orig_range
        assert worker.read_selection(focus, lambda: True, 300) == (5, 11, "ghbdtn")
        assert len(conns) == 1
        assert not is_bus_closed_error(RuntimeError("org.freedesktop.DBus.Error.ServiceUnknown"))
        assert is_bus_closed_error(RuntimeError("org.freedesktop.DBus.Error.Disconnected"))
    finally:
        worker.close(timeout=1.0)


def test_worker_reconnects_after_closed_bus_connection():
    events = []
    tree = WorkerTree(events)
    tree.selections = 1
    conns = []

    def make_conn():
        conn = SimpleNamespace(
            closed=False, is_closed=lambda: conn.closed, close_sync=lambda _c: None)
        conns.append(conn)
        return conn

    ctx = a11y.ShellContext(("libreoffice-writer",), ime_focus=True, pid=123)
    worker = AtspiWorker(
        connection_factory=make_conn,
        tree_factory=lambda _c: tree,
        context_provider=lambda: ctx,
        main_invoke=lambda fn: fn(),
    )
    try:
        focus = make_focus()
        assert worker.read_selection(focus, lambda: True, 300) == (5, 11, "ghbdtn")
        conns[0].closed = True
        assert worker.read_selection(focus, lambda: True, 300) == (5, 11, "ghbdtn")
        assert len(conns) == 2
    finally:
        worker.close(timeout=1.0)


def test_worker_stale_focused_state_without_matching_context_refuses_read(monkeypatch):
    def forbidden(*_a, **_kw):
        raise AssertionError("BFS forbidden")

    monkeypatch.setattr(a11y, "find_focus", forbidden)
    events = []
    tree = WorkerTree(events)
    tree.selections = 1
    ctx = a11y.ShellContext(("other.desktop",), pid=123)
    worker = AtspiWorker(
        connection_factory=_fake_conn,
        tree_factory=lambda _c: tree,
        context_provider=lambda: ctx,
        main_invoke=lambda fn: fn(),
    )
    try:
        assert worker.read_selection(make_focus(), lambda: True, 300) is None
        assert tree.reads == []
    finally:
        worker.close(timeout=1.0)


def test_worker_callbacks_run_on_main_invoke_and_cancelled_before_write_never_writes():
    events = []
    tree = WorkerTree(events)
    main_calls = []

    def main_invoke(fn):
        main_calls.append(threading.get_ident())
        fn()

    @contextmanager
    def changes(_conn, _field):
        yield events

    gate = threading.Event()

    def slow_commit():
        gate.wait(timeout=1.0)
        return True

    worker = AtspiWorker(
        connection_factory=_fake_conn,
        tree_factory=lambda _c: tree,
        changes_factory=changes,
        context_provider=lambda: a11y.ShellContext(
            ("libreoffice-writer",), ime_focus=True, pid=123),
        main_invoke=main_invoke,
    )
    try:
        res = worker.replace_span(
            make_focus(), "ghbdtn", "привет", slow_commit, lambda: False, timeout=0.06
        )
        assert res == editable.SpanResult(ABORTED)
        gate.set()
        time.sleep(0.05)
        assert tree.writes == []
        assert main_calls
    finally:
        gate.set()
        worker.close(timeout=1.0)


def test_worker_inflight_write_timeout_returns_uncertain_blocks_fallback_and_new_ops():
    events = []
    tree = WorkerTree(events)
    in_insert = threading.Event()
    release_insert = threading.Event()

    def blocking_insert(start, value):
        in_insert.set()
        release_insert.wait(timeout=1.0)
        return True

    tree.on_insert = blocking_insert

    @contextmanager
    def changes(_conn, _field):
        yield events

    worker = AtspiWorker(
        connection_factory=_fake_conn,
        tree_factory=lambda _c: tree,
        changes_factory=changes,
        context_provider=lambda: a11y.ShellContext(
            ("libreoffice-writer",), ime_focus=True, pid=123),
        main_invoke=lambda fn: fn(),
    )
    try:
        focus = make_focus(generation=7)
        plan = Plan([SwitchLayout(RU), Backspace(6), Type("привет", [])],
                    source_text="ghbdtn", focus=focus)
        typist, backend = RecordingTypist(), FakeBackend(current=US)
        result = execute(
            plan, typist, backend, restore_group=US,
            native_replacer=lambda *a: worker.replace_span(*a, timeout=0.08),
        )
        assert in_insert.is_set()
        assert result == UNCERTAIN
        assert typist.taps() == []
        assert worker.uncertain_active is True
        blocked = worker.replace_span(
            focus, "ghbdtn", "привет", lambda: True, lambda: False)
        assert blocked == editable.SpanResult(ABORTED)

        release_insert.set()
        time.sleep(0.05)
        tree.on_insert = None
        next_focus = make_focus(generation=8)
        done = worker.replace_span(
            next_focus, "ghbdtn", "привет", lambda: True, lambda: False)
        assert done == editable.SpanResult("done", True)
    finally:
        release_insert.set()
        worker.close(timeout=1.0)


def test_worker_queue_limit_and_cancel_removes_pending():
    token = CancelToken(1, 0, time.monotonic() + 1.0)
    assert token.cancel("test") is True
    assert token.begin_write() is False

    events = []
    tree = WorkerTree(events)
    gate = threading.Event()

    def slow_range(*_args):
        gate.wait(timeout=1.0)
        return (5, 11)

    tree.selections = 1
    tree.selection_range = slow_range
    worker = AtspiWorker(
        connection_factory=_fake_conn,
        tree_factory=lambda _c: tree,
        context_provider=lambda: a11y.ShellContext(
            ("libreoffice-writer",), ime_focus=True, pid=123),
        main_invoke=lambda fn: fn(),
        max_queue=1,
    )
    try:
        focus = make_focus()
        results = []
        t1 = threading.Thread(
            target=lambda: results.append(worker.read_selection(focus, lambda: True, 300)))
        t1.start()
        time.sleep(0.03)
        t2 = threading.Thread(
            target=lambda: results.append(worker.read_selection(focus, lambda: True, 300)))
        t2.start()
        time.sleep(0.03)
        assert worker.read_selection(focus, lambda: True, 300) is None
        gate.set()
        t1.join(timeout=1.0)
        t2.join(timeout=1.0)
    finally:
        gate.set()
        worker.close(timeout=1.0)


def test_controller_uses_injected_selection_reader_and_blocks_uncertain_generation():
    km = make_keymap()
    det = Detector(km, {
        "ru": LanguageModel.from_words(
            "ru", [("привет", 900.0)], "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"),
        "en": LanguageModel.from_words(
            "en", [("hello", 900.0)], "abcdefghijklmnopqrstuvwxyz"),
    })
    snap = make_focus(generation=3)
    probe = SimpleNamespace(snapshot=lambda: snap, poke=lambda: None)
    reads = []
    ctrl = Controller(
        Config(), km, det, FakeBackend(current=US), RecordingTypist(),
        probe=probe,
        selection_reader=lambda exp, _v, _l: (
            reads.append(exp) or a11y.TextSelection(0, 6, "ghbdtn")),
    )
    assert ctrl.fix_selection() is True
    assert len(reads) == 2
