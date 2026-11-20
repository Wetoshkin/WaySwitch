"""Постоянный владелец соединения AT-SPI для чтения выделения и нативной замены."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from wayswitch import a11y, editable

log = logging.getLogger("wayswitch")

MAX_QUEUE = 8
DEFAULT_TIMEOUT_SEC = 2.0
SHUTDOWN_JOIN_SEC = 0.5
_BACKOFF_STEPS = (0.05, 0.1, 0.2, 0.5)

_PROVIDER_ERRORS = (
    "org.freedesktop.DBus.Error.ServiceUnknown",
    "org.freedesktop.DBus.Error.NameHasNoOwner",
    "org.freedesktop.DBus.Error.UnknownObject",
    "org.freedesktop.DBus.Error.UnknownInterface",
    "org.freedesktop.DBus.Error.UnknownMethod",
    "org.freedesktop.DBus.Error.UnknownProperty",
)
_BUS_CLOSED_MARKERS = (
    "org.freedesktop.DBus.Error.Disconnected",
    "connection is closed",
    "connection closed",
    "broken pipe",
)


def is_bus_closed_error(exc: Exception, connection=None) -> bool:
    """Отличить разрыв глобальной шины от ошибки конкретного провайдера/узла."""
    if connection is not None:
        try:
            if connection.is_closed():
                return True
        except Exception:  # noqa: BLE001
            return True
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, EOFError)):
        return True
    text = str(exc)
    if any(err in text for err in _PROVIDER_ERRORS):
        return False
    low = text.casefold()
    return any(marker.casefold() in low for marker in _BUS_CLOSED_MARKERS)


class CancelToken:
    """Потокобезопасный токен двухфазной авторизации и отмены операции."""

    def __init__(self, op_id: int, cancel_epoch: int, deadline: float,
                 clock: Callable[[], float] = time.monotonic):
        self.op_id = op_id
        self.cancel_epoch = cancel_epoch
        self.deadline = deadline
        self._clock = clock
        self._lock = threading.Lock()
        self._cancelled = False
        self._reason = ""
        self._in_write = False
        self._finished = False

    def is_active(self) -> bool:
        with self._lock:
            return not self._cancelled and self._clock() < self.deadline

    @property
    def in_write(self) -> bool:
        with self._lock:
            return self._in_write

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._finished

    def cancel(self, reason: str = "cancelled") -> bool:
        """Отменить операцию; True, если отмена произошла до начала записи."""
        with self._lock:
            if not self._cancelled:
                self._cancelled = True
                self._reason = reason
            return not self._in_write

    def begin_write(self) -> bool:
        """Атомарно разрешить запись, только если токен активен и срок не истёк."""
        with self._lock:
            if self._cancelled or self._clock() >= self.deadline:
                return False
            self._in_write = True
            return True

    def finish(self) -> None:
        with self._lock:
            self._finished = True


@dataclass(frozen=True)
class AtspiRequest:
    """Неизменяемый снимок запроса в AT-SPI worker."""

    op_id: int
    kind: str  # "read_selection" | "replace_span"
    focus: a11y.FocusSnapshot
    generation: int
    deadline: float
    cancel_epoch: int
    limit: int = 300
    source: str = ""
    target: str = ""
    shell: bool = False


class _QueuedItem:
    def __init__(self, request: AtspiRequest, token: CancelToken,
                 valid: Callable[[], bool] | None = None,
                 commit: Callable[[], bool | None] | None = None,
                 stopped: Callable[[], bool] | None = None):
        self.request = request
        self.token = token
        self.valid = valid
        self.commit = commit
        self.stopped = stopped
        self.done = threading.Event()
        self.result = None


def _default_main_invoke(fn: Callable[[], None]) -> None:
    from gi.repository import GLib

    GLib.idle_add(lambda: (fn(), False)[1])


class AtspiWorker:
    """Выделенный owner-поток с одним постоянным соединением к org.a11y.Bus."""

    def __init__(self, *,
                 connection_factory: Callable[[], object] | None = None,
                 tree_factory: Callable[[object], object] | None = None,
                 changes_factory=None,
                 shell_backend=None,
                 context_provider: Callable[[], a11y.ShellContext | None] | None = None,
                 main_invoke: Callable[[Callable[[], None]], None] | None = None,
                 main_pump: Callable[[], None] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 max_queue: int = MAX_QUEUE,
                 start: bool = True):
        self._connection_factory = connection_factory or a11y._new_a11y_connection
        self._tree_factory = tree_factory or a11y._DbusTree
        self._changes_factory = changes_factory or editable._text_changes
        self._shell_backend = shell_backend
        self._context_provider = context_provider or a11y.query_shell_context
        self._main_invoke = main_invoke or _default_main_invoke
        self._main_pump = main_pump
        self._clock = clock
        self._sleep = sleep
        self._max_queue = max(1, max_queue)

        self._cond = threading.Condition()
        self._main_wake = threading.Event()
        self._queue: deque[_QueuedItem] = deque()
        self._closing = False
        self._next_op_id = 0
        self._cancel_epoch = 0
        self._uncertain_active = False
        self._uncertain_generation: int | None = None

        self._conn = None
        self._tree = None
        self._owner_thread_id: int | None = None
        self._reconnect_failures = 0
        self._next_connect_at = 0.0
        self._active_item: _QueuedItem | None = None

        self._thread: threading.Thread | None = None
        if start:
            self._thread = threading.Thread(
                target=self._run, name="atspi-worker", daemon=True)
            self._thread.start()

    @property
    def uncertain_active(self) -> bool:
        with self._cond:
            return self._uncertain_active

    def clear_uncertain(self) -> None:
        with self._cond:
            self._uncertain_active = False
            self._uncertain_generation = None

    def cancel(self, op_id: int) -> None:
        """Отменить операцию с данным op_id и удалить её из очереди."""
        with self._cond:
            self._cancel_epoch += 1
            kept: deque[_QueuedItem] = deque()
            for item in self._queue:
                if item.request.op_id == op_id:
                    item.token.cancel("cancelled")
                    item.result = (editable.SpanResult(editable.ABORTED)
                                   if item.request.kind == "replace_span" else None)
                    item.done.set()
                else:
                    kept.append(item)
            self._queue = kept
            if self._active_item is not None and self._active_item.request.op_id == op_id:
                self._active_item.token.cancel("cancelled")
            self._cond.notify_all()
            self._main_wake.set()

    def _wait_item(self, item: _QueuedItem, deadline: float) -> bool:
        """Ждать завершения запроса, прокручивая main context при необходимости."""
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                return item.done.is_set()
            if self._main_pump is None:
                return item.done.wait(timeout=remaining)
            if item.done.is_set():
                return True
            self._main_wake.wait(timeout=min(0.005, remaining))
            self._main_wake.clear()
            if item.done.is_set():
                return True
            self._main_pump()

    def close(self, timeout: float = SHUTDOWN_JOIN_SEC) -> None:
        """Остановить приём, отменить ожидающие запросы и дождаться завершения."""
        with self._cond:
            self._closing = True
            self._cancel_epoch += 1
            while self._queue:
                item = self._queue.popleft()
                item.token.cancel("shutdown")
                item.result = (editable.SpanResult(editable.ABORTED)
                               if item.request.kind == "replace_span" else None)
                item.done.set()
            if self._active_item is not None:
                self._active_item.token.cancel("shutdown")
            self._cond.notify_all()
            self._main_wake.set()
        if self._thread is not None and self._thread.is_alive():
            if threading.get_ident() != self._thread.ident:
                self._thread.join(timeout=timeout)

    def read_selection(self, expected: a11y.FocusSnapshot,
                       valid: Callable[[], bool],
                       limit: int,
                       *, timeout: float = DEFAULT_TIMEOUT_SEC) -> a11y.TextSelection | None:
        """Прочитать выделение через постоянный AT-SPI worker."""
        if not a11y._selection_ready(expected, valid, limit):
            return None
        deadline = self._clock() + max(0.05, timeout)
        with self._cond:
            if self._closing or self._thread is None:
                return None
            if len(self._queue) >= self._max_queue:
                return None
            self._next_op_id += 1
            op_id = self._next_op_id
            req = AtspiRequest(
                op_id=op_id,
                kind="read_selection",
                focus=expected,
                generation=expected.generation,
                deadline=deadline,
                cancel_epoch=self._cancel_epoch,
                limit=limit,
            )
            token = CancelToken(op_id, req.cancel_epoch, deadline, clock=self._clock)
            item = _QueuedItem(req, token, valid=valid)
            self._queue.append(item)
            self._cond.notify_all()

        if not self._wait_item(item, deadline):
            token.cancel("timeout")
            self.cancel(op_id)
            return None
        return item.result

    def replace_deferred_field(self, focus, source: str, target: str,
                               commit: Callable[[], bool | None],
                               stopped: Callable[[], bool]) -> editable.SpanResult | None:
        """Расширенный диапазон допустим у обычного поля с адресной заменой."""
        if not editable._plain_field_ready(focus, source, target, stopped):
            return None
        return self.replace_span(focus, source, target, commit, stopped, shell=True)

    def replace_span(self, focus, source: str, target: str,
                     commit: Callable[[], bool | None],
                     stopped: Callable[[], bool],
                     *, timeout: float = DEFAULT_TIMEOUT_SEC,
                     shell: bool = False) -> editable.SpanResult | None:
        """Выполнить нативную замену через постоянный AT-SPI worker."""
        if not isinstance(focus, a11y.FocusSnapshot):
            return editable.SpanResult(editable.ABORTED)
        plain = bool(focus.app and focus.app.casefold() not in editable.KDE_APPS)
        if plain:
            if not editable._plain_field_ready(focus, source, target, stopped):
                return None
        elif focus.app_ids:
            if (focus.app and (focus.app.casefold() not in {"soffice", "libreoffice"}
                               or not (editable._writer(focus) or editable._calc(focus)))):
                return None
            if not editable._normal(focus):
                return editable.SpanResult(editable.ABORTED)
        else:
            if not editable._kde_identity(focus):
                return None if focus.app else editable.SpanResult(editable.ABORTED)
            if not editable._kde_focus(focus, self._clock()):
                return editable.SpanResult(editable.ABORTED)

        deadline = self._clock() + max(0.05, timeout)
        with self._cond:
            if self._closing or self._thread is None:
                return editable.SpanResult(editable.ABORTED)
            if self._uncertain_active:
                if self._uncertain_generation == focus.generation:
                    return editable.SpanResult(editable.ABORTED)
                self._uncertain_active = False
                self._uncertain_generation = None
            if len(self._queue) >= self._max_queue:
                return editable.SpanResult(editable.ABORTED)
            self._next_op_id += 1
            op_id = self._next_op_id
            req = AtspiRequest(
                op_id=op_id,
                kind="replace_span",
                focus=focus,
                generation=focus.generation,
                deadline=deadline,
                cancel_epoch=self._cancel_epoch,
                source=source,
                target=target,
                shell=shell,
            )
            token = CancelToken(op_id, req.cancel_epoch, deadline, clock=self._clock)
            item = _QueuedItem(req, token, commit=commit, stopped=stopped)
            self._queue.append(item)
            self._cond.notify_all()

        if not self._wait_item(item, deadline):
            was_before_write = token.cancel("timeout")
            if not was_before_write:
                with self._cond:
                    self._uncertain_active = True
                    self._uncertain_generation = focus.generation
                return editable.SpanResult(editable.UNCERTAIN, True)
            self.cancel(op_id)
            return editable.SpanResult(editable.ABORTED)
        return item.result

    # --- owner-thread ---

    def _run(self) -> None:
        self._owner_thread_id = threading.get_ident()
        ctx = None
        try:
            from gi.repository import GLib

            ctx_cls = getattr(GLib, "MainContext", None)
            if ctx_cls is not None:
                ctx = ctx_cls.new() if hasattr(ctx_cls, "new") else ctx_cls()
                if hasattr(ctx, "push_thread_default"):
                    ctx.push_thread_default()
                else:
                    ctx = None
        except Exception:  # noqa: BLE001
            ctx = None

        try:
            while True:
                item = self._next_item()
                if item is None:
                    break
                try:
                    self._process_item(item, ctx)
                finally:
                    with self._cond:
                        self._active_item = None
                    item.token.finish()
                    item.done.set()
                    self._main_wake.set()
        finally:
            self._close_connection()
            if ctx is not None:
                try:
                    ctx.pop_thread_default()
                except Exception:  # noqa: BLE001
                    pass

    def _next_item(self) -> _QueuedItem | None:
        with self._cond:
            while not self._closing and not self._queue:
                self._cond.wait()
            if self._closing:
                return None
            item = self._queue.popleft()
            self._active_item = item
            return item

    def _ensure_connection(self) -> tuple[object, object] | tuple[None, None]:
        assert threading.get_ident() == self._owner_thread_id
        if self._conn is not None:
            closed = False
            try:
                closed = bool(self._conn.is_closed())
            except Exception:  # noqa: BLE001
                closed = True
            if not closed and self._tree is not None:
                return self._conn, self._tree
            self._close_connection()

        now = self._clock()
        if now < self._next_connect_at:
            return None, None
        try:
            conn = self._connection_factory()
            if conn is None:
                raise RuntimeError("empty connection")
            tree = self._tree_factory(conn)
            tree._before_write = self._authorize_write
        except Exception:  # noqa: BLE001
            step = _BACKOFF_STEPS[min(self._reconnect_failures, len(_BACKOFF_STEPS) - 1)]
            self._reconnect_failures += 1
            self._next_connect_at = self._clock() + step
            return None, None
        self._conn = conn
        self._tree = tree
        self._reconnect_failures = 0
        self._next_connect_at = 0.0
        return conn, tree

    def _close_connection(self) -> None:
        conn, self._conn, self._tree = self._conn, None, None
        a11y._close_private_connection(conn)

    def _authorize_write(self) -> bool:
        assert threading.get_ident() == self._owner_thread_id
        item = self._active_item
        return item is not None and item.token.begin_write()

    def _call_main(self, token: CancelToken, fn: Callable[[], object], default):
        if not token.is_active():
            return default
        done = threading.Event()
        box = [default]

        def run():
            try:
                if token.is_active():
                    box[0] = fn()
            except Exception:  # noqa: BLE001
                box[0] = default
            finally:
                done.set()

        try:
            self._main_invoke(run)
            self._main_wake.set()
        except Exception:  # noqa: BLE001
            return default
        wait_for = max(0.0, token.deadline - self._clock())
        if not done.wait(timeout=wait_for):
            return default
        return box[0]

    def _process_item(self, item: _QueuedItem, ctx) -> None:
        req = item.request
        token = item.token
        if not token.is_active():
            item.result = (editable.SpanResult(editable.ABORTED)
                           if req.kind == "replace_span" else None)
            return

        conn, tree = self._ensure_connection()
        if conn is None or tree is None:
            item.result = (editable.SpanResult(editable.FAILED)
                           if req.kind == "replace_span" else None)
            return

        if req.kind == "read_selection":
            def valid_cb() -> bool:
                if not token.is_active() or item.valid is None:
                    return False
                return bool(self._call_main(token, item.valid, False))

            try:
                item.result = a11y._read_selection(
                    tree, req.focus, valid_cb, req.limit,
                    direct=True, context_provider=self._context_provider)
            except Exception as exc:  # noqa: BLE001
                if is_bus_closed_error(exc, conn):
                    self._close_connection()
                item.result = None
            return

        # replace_span
        item.result = self._run_replace(item, conn, tree, ctx)

    def _run_replace(self, item: _QueuedItem, conn, tree, ctx) -> editable.SpanResult | None:
        req = item.request
        token = item.token
        stopped_obj = item.stopped

        class _StoppedProxy:
            def __call__(self_proxy) -> bool:
                if not token.is_active():
                    return True
                if stopped_obj is None:
                    return False
                return bool(self._call_main(token, stopped_obj, True))

            def __getattr__(self_proxy, name: str):
                attr = getattr(stopped_obj, name)
                if not callable(attr):
                    return attr
                return lambda *args, **kw: self._call_main(
                    token, lambda: attr(*args, **kw), None)

        stopped_proxy = _StoppedProxy()

        def commit_proxy():
            if not token.is_active():
                return False
            if item.commit is not None:
                res = self._call_main(token, item.commit, False)
                if res is False:
                    return False
            return token.is_active()

        try:
            with self._changes_factory(conn, req.focus.field) as events:
                if ctx is not None:
                    while ctx.pending():
                        ctx.iteration(False)
                if req.focus.app and req.focus.app.casefold() not in editable.KDE_APPS:
                    result = editable._replace_field_with_tree(
                        tree, req.focus, req.source, req.target,
                        commit_proxy, stopped_proxy, events,
                        context_provider=self._context_provider)
                    if result is None and req.shell and self._shell_backend is not None:
                        result = editable._replace_shell_with_tree(
                            tree, req.focus, req.source, req.target, commit_proxy,
                            stopped_proxy, self._shell_backend,
                            context_provider=self._context_provider,
                            before_write=token.begin_write)
                elif req.focus.app_ids:
                    result = editable._replace(
                        tree, req.focus, req.source, req.target,
                        commit_proxy, stopped_proxy, events,
                        context_provider=self._context_provider)
                else:
                    result = editable._replace_kde_with_tree(
                        tree, req.focus, req.source, req.target,
                        commit_proxy, stopped_proxy, events,
                        context_provider=self._context_provider)
            if result is not None and result.status == editable.DONE:
                with self._cond:
                    if self._uncertain_generation == req.generation:
                        self._uncertain_active = False
                        self._uncertain_generation = None
            elif (result is not None and result.status == editable.FAILED
                  and token.in_write):
                with self._cond:
                    self._uncertain_active = True
                    self._uncertain_generation = req.generation
                return editable.SpanResult(editable.UNCERTAIN, True)
            if not token.is_active() and not token.in_write:
                return editable.SpanResult(editable.ABORTED)
            return result
        except Exception as exc:  # noqa: BLE001
            closed = is_bus_closed_error(exc, conn)
            if closed:
                self._close_connection()
            if token.in_write:
                with self._cond:
                    self._uncertain_active = True
                    self._uncertain_generation = req.generation
                return editable.SpanResult(editable.UNCERTAIN, True)
            return editable.SpanResult(editable.FAILED, False)
