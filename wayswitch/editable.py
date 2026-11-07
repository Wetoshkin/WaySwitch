"""Bounded native word replacement for LibreOffice Writer paragraphs."""

import time
from contextlib import contextmanager
from typing import NamedTuple

from wayswitch import a11y

EDITABLE = "org.a11y.atspi.EditableText"
MAX_SPAN = 512
KDE_APPS = frozenset({"soffice", "libreoffice", "soffice.bin"})
_OK, _ABORT, _UNSUPPORTED = "ok", "abort", "unsupported"


class SpanResult(NamedTuple):
    status: str
    committed: bool = False
    source_text: str | None = None
    target_text: str | None = None


def _writer(focus):
    return (isinstance(focus, a11y.FocusSnapshot)
            and any(app.casefold().removesuffix(".desktop") == "libreoffice-writer"
                    for app in focus.app_ids))


def try_replace(focus, source, target, commit, stopped) -> SpanResult | None:
    if (focus is not None and focus.app
            and (focus.app.casefold() not in {"soffice", "libreoffice"} or not _writer(focus))):
        return None
    if not _normal(focus):
        return SpanResult("aborted")
    connection = None
    attempted = False

    def begin():
        nonlocal attempted
        result = commit()
        if result is False:
            return False
        attempted = True
        return result

    try:
        connection = a11y._new_a11y_connection()
        with _text_changes(connection, focus.field) as events:
            return _replace(a11y._DbusTree(connection), focus, source, target,
                            begin, stopped, events)
    except Exception:  # noqa: BLE001 — metadata and transport errors must not expose text
        return SpanResult("failed", attempted)
    finally:
        if connection is not None:
            try:
                connection.close_sync(None)
            except Exception:  # noqa: BLE001 — never turn teardown into a keyboard fallback
                pass


def _normal(focus):
    return (_writer(focus) and focus.field is not None
            and focus.app is not None and focus.app.casefold() in {"soffice", "libreoffice"}
            and focus.protection(time.monotonic()) is False)


def _span(text):
    # LibreOffice's bridge exposes native UTF-16 positions; BMP spans share its units.
    return (isinstance(text, str) and 0 < len(text) <= MAX_SPAN
            and all(ord(ch) <= 0xFFFF and not 0xD800 <= ord(ch) <= 0xDFFF for ch in text))


def _case_target(source, target):
    if source.isupper():
        return target.upper()
    if source.islower():
        return target.lower()
    if source == source.capitalize():
        return target.capitalize()
    return None


def _edits(events, before, after, start, kind, length, count_before, count_after):
    if (not events or len(events) > 64 or type(count_before) is not int
            or type(count_after) is not int or count_before < 0 or count_after < 0):
        return False
    current_count = count_before
    for event in events:
        if (not isinstance(event, tuple) or len(event) != 3
                or event[0] not in {"insert", "delete"}
                or type(event[1]) is not int or type(event[2]) is not int
                or not 0 <= event[1] <= current_count or event[2] <= 0
                or event[0] == "delete" and event[1] + event[2] > current_count):
            return False
        current_count += event[2] if event[0] == "insert" else -event[2]
    if current_count != count_after:
        return False
    for index, (event_kind, offset, event_length) in enumerate(events):
        relative = offset - start
        larger, smaller = (after, before) if kind == "insert" else (before, after)
        if (event_kind != kind or event_length != length or relative < 0
                or relative + length > len(larger)
                or larger[:relative] + larger[relative + length:] != smaller):
            continue
        if all(i == index or k == "insert" and p >= start + len(
                before if i < index else after) for i, (k, p, _) in enumerate(events)):
            return True
    return False


def _replace(tree, focus, source, target, commit, stopped, events) -> SpanResult:
    if not _normal(focus) or not _span(source) or not _span(target):
        return SpanResult("aborted")
    node = focus.field
    try:
        context = a11y.query_shell_context()
    except Exception:  # noqa: BLE001 — unknown context is a failed native attempt, not a retry
        return SpanResult("failed")

    def safe():
        if stopped() or not _normal(focus):
            return False
        current = a11y.query_shell_context()
        if (context is None or current != context or context.protected()
                or not context.ime_focus or type(context.pid) is not int or context.pid <= 0
                or not focus.app_ids or context.app_ids != focus.app_ids):
            return False
        state = tree.state(node)
        interfaces = tree.interfaces(node)
        selections = tree.selection_count(node)
        pid = tree.pid(node)
        return (bool(state) and a11y._state_bit(state, a11y.EDITABLE)
                and a11y._state_bit(state, a11y.FOCUSED) and tree.role(node) in {73, 83}
                and interfaces is not None and a11y.TEXT in interfaces
                and EDITABLE in interfaces
                and tree.input_type(node) not in {None, "password"}
                and tree.app_of(node) == focus.app and a11y._same_application(pid, context.pid)
                and type(selections) is int and selections == 0
                and not stopped() and a11y.query_shell_context() == context)

    return _replace_checked(tree, focus, source, target, commit, stopped, events, safe)


def _replace_checked(tree, focus, source, target, commit, stopped, events, safe) -> SpanResult:
    attempted = False
    try:
        node = focus.field
        if not _span(source) or not _span(target):
            return SpanResult("aborted")

        def read(start, end):
            if not safe():
                return None
            text = tree.text_range(node, start, end)
            return text if isinstance(text, str) and len(text) == end - start and safe() else None

        if not safe():
            return SpanResult("aborted")
        caret = tree.caret(node)
        count = tree.character_count(node)
        if (type(caret) is not int or type(count) is not int
                or caret < len(source) or count < caret):
            return SpanResult("aborted")
        start = caret - len(source)
        actual = read(start, caret)
        case_changed = actual != source
        if case_changed:
            if (not _span(actual) or len(actual) != len(source)
                    or actual.casefold() != source.casefold()):
                return SpanResult("aborted")
            source, target = actual, _case_target(actual, target)
        if not _span(target) or source == target:
            return SpanResult("aborted")
        first = len(events)
        if not safe():
            return SpanResult("aborted")
        if commit() is False:
            return SpanResult("aborted")
        attempted = True
        if not safe():
            return SpanResult("failed", True)
        if tree.insert_text(node, start, target) is not True:
            return SpanResult("failed", True)
        combined = target + source
        if read(start, start + len(combined)) != combined:
            return SpanResult("failed", True)
        inserted_count = tree.character_count(node)
        if (type(inserted_count) is not int or not safe()
                or not _edits(events[first:], source, combined, start, "insert", len(target),
                              count, inserted_count)):
            return SpanResult("failed", True)
        # No recovery writes: uncertainty must retain the original rather than erase it.
        if read(start, start + len(combined)) != combined:
            return SpanResult("failed", True)
        second = len(events)
        delete_count = tree.character_count(node)
        if type(delete_count) is not int or not safe():
            return SpanResult("failed", True)
        if tree.delete_text(node, start + len(target), start + len(combined)) is not True:
            return SpanResult("failed", True)
        if read(start, start + len(target)) != target:
            return SpanResult("failed", True)
        final_count = tree.character_count(node)
        if (type(final_count) is not int or not safe()
                or not _edits(events[second:], combined, target, start, "delete", len(source),
                              delete_count, final_count)):
            return SpanResult("failed", True)
        return SpanResult("done", True, source if case_changed else None,
                          target if case_changed else None)
    except Exception:  # noqa: BLE001 — never log provider errors containing field contents
        return SpanResult("failed", attempted)


def _kde_identity(focus) -> bool:
    return (isinstance(focus, a11y.FocusSnapshot) and type(focus.app) is str
            and focus.app.casefold() in KDE_APPS)


def _kde_field(field) -> bool:
    return (type(field) is tuple and len(field) == 2
            and all(type(part) is str and part for part in field)
            and field[1] != "/org/a11y/atspi/null")


def _kde_focus(focus, now) -> bool:
    return (_kde_identity(focus) and _kde_field(focus.field)
            and focus.app_ids == ()
            and type(focus.taken_at) in (int, float)
            and not isinstance(focus.taken_at, bool)
            and 0 < focus.taken_at <= now
            and focus.password is False
            and focus.protection(now) is False)


def _kde_guard(tree, focus, context, stopped):
    """Live-проверка KDE-фокуса; ошибки метаданных не разрешают чтение текста."""
    stable_pid = None
    validated_focus = focus

    def check():
        nonlocal stable_pid, validated_focus
        try:
            if stopped():
                return _ABORT
            started = time.monotonic()
            if not _kde_focus(validated_focus, started):
                return _ABORT
            current = a11y.query_shell_context()
            if current is None or current != context or current.protected():
                return _ABORT
            live = a11y.find_focus(tree, a11y.ROOT)
            if (live.app != focus.app or live.field != focus.field
                    or live.password is not False):
                return _ABORT
            node = focus.field
            if tree.app_of(node) != focus.app:
                return _ABORT
            state = tree.state(node)
            interfaces = tree.interfaces(node)
            input_type = tree.input_type(node)
            selections = tree.selection_count(node)
            pid = tree.pid(node)
            role = tree.role(node)
            if not (bool(state) and a11y._state_bit(state, a11y.EDITABLE)
                    and a11y._state_bit(state, a11y.FOCUSED)):
                return _ABORT
            if interfaces is None or a11y.TEXT not in interfaces or EDITABLE not in interfaces:
                return _ABORT
            if type(input_type) is not str or input_type == "password":
                return _ABORT
            if type(selections) is not int or selections != 0:
                return _ABORT
            if type(pid) is not int or pid <= 0:
                return _ABORT
            if stable_pid is None:
                stable_pid = pid
            elif pid != stable_pid:
                return _ABORT
            if type(role) is not int or role not in {61, 73, 79, 83}:
                return _ABORT
            if stopped() or a11y.query_shell_context() != context:
                return _ABORT
            if not 0 <= time.monotonic() - started <= a11y.MAX_AGE_SEC:
                return _ABORT
            # Продлеваем доверие только полным живым подтверждением того же поля.
            validated_focus = focus._replace(taken_at=started)
            return _UNSUPPORTED if role in {61, 79} else _OK
        except Exception:  # noqa: BLE001 — unknown metadata cannot authorize a read
            return _ABORT

    return check


def try_replace_kde(focus, source, target, commit, stopped) -> SpanResult | None:
    """KDE-вход: нативный span только для подтверждённого Writer-параграфа."""
    if not isinstance(focus, a11y.FocusSnapshot):
        return SpanResult("aborted")
    if not _kde_identity(focus):
        # Не LibreOffice: обычный транспорт должен работать как раньше.
        return None if focus.app else SpanResult("aborted")
    if not _kde_focus(focus, time.monotonic()):
        return SpanResult("aborted")
    try:
        context = a11y.query_shell_context()
    except Exception:  # noqa: BLE001 — неизвестный контекст не разрешает попытку
        return SpanResult("aborted")
    if context != a11y.ShellContext():
        return SpanResult("aborted")
    connection = None
    attempted = False

    def begin():
        nonlocal attempted
        result = commit()
        if result is False:
            return False
        attempted = True
        return result

    try:
        connection = a11y._new_a11y_connection()
        if connection is None:
            return SpanResult("failed")
        tree = a11y._DbusTree(connection)
        check = _kde_guard(tree, focus, context, stopped)
        state = check()
        if state == _UNSUPPORTED:
            # Роль/компонент не поддерживается, но живой фокус уже доказан:
            # никакой подготовки и записей не было, можно вернуться к клавишам.
            return None
        if state != _OK:
            return SpanResult("aborted")
        with _text_changes(connection, focus.field) as events:
            return _replace_checked(tree, focus, source, target, begin, stopped, events,
                                    lambda: check() == _OK)
    except Exception:  # noqa: BLE001 — metadata and transport errors must not expose text
        return SpanResult("failed", attempted)
    finally:
        if connection is not None:
            try:
                connection.close_sync(None)
            except Exception:  # noqa: BLE001 — never turn teardown into a keyboard fallback
                pass


@contextmanager
def _text_changes(connection, node):
    from gi.repository import Gio, GLib

    events = []
    subscription = None
    registered = False
    registry = "org.a11y.atspi.Registry"
    event_name = "object:text-changed"

    def changed(_connection, _sender, _path, _interface, _member, params, _data):
        if len(events) > 64:
            return
        try:
            kind = params.get_child_value(0)
            start = params.get_child_value(1)
            length = params.get_child_value(2)
            if (kind.get_type_string() != "s" or start.get_type_string() != "i"
                    or length.get_type_string() != "i"):
                raise ValueError
            category = kind.get_string()
            if category not in {"insert", "delete"}:
                raise ValueError
            events.append((category, start.get_int32(), length.get_int32()))
        except Exception:  # noqa: BLE001 — payload beyond the three numeric/category fields is private
            events.append(("invalid", -1, -1))

    try:
        subscription = connection.signal_subscribe(
            node[0], "org.a11y.atspi.Event.Object", "TextChanged", node[1], None,
            Gio.DBusSignalFlags.NONE, changed, None)
        connection.call_sync(registry, "/org/a11y/atspi/registry", registry, "RegisterEvent",
                             GLib.Variant("(sass)", (event_name, [], "")),
                             GLib.VariantType("()"), Gio.DBusCallFlags.NONE, 1500, None)
        registered = True
        yield events
    finally:
        if subscription is not None:
            connection.signal_unsubscribe(subscription)
        if registered:
            try:
                connection.call_sync(registry, "/org/a11y/atspi/registry", registry,
                                     "DeregisterEvent", GLib.Variant("(s)", (event_name,)),
                                     GLib.VariantType("()"), Gio.DBusCallFlags.NONE, 400, None)
            except Exception:  # noqa: BLE001 — connection ownership also clears registrations
                pass
