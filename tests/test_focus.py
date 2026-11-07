"""Исключения по приложению и поля паролей: снимок фокуса из at-spi.

Проверяем и чистую логику решения, и то, что контроллер действительно молчит
в исключённой программе и в поле пароля.
"""

import os
import stat
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from tests.fakes import RU, US, FakeBackend, RecordingTypist, keys_for_text, make_keymap
from wayswitch import a11y
from wayswitch import config as cfgmod
from wayswitch import keycodes as kc
from wayswitch.a11y import FocusSnapshot
from wayswitch.buffer import InputBuffer
from wayswitch.config import Config
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeProbe:
    """Подставной источник фокуса: отдаёт заданный снимок и считает poke()."""

    def __init__(self, clock, app=None, password=False, age=0.0):
        self.clock = clock
        self.app = app
        self.password = password
        self.age = age
        self.pokes = 0

    def snapshot(self) -> FocusSnapshot:
        return FocusSnapshot(self.app, self.password, self.clock() - self.age)

    def poke(self) -> None:
        self.pokes += 1


def make_env(probe=None, apps=("telegram",), config=None):
    km = make_keymap()
    det = Detector(km, {
        "ru": LanguageModel.from_words("ru", [("привет", 900.0), ("пирог", 800.0)],
                                       "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"),
        "en": LanguageModel.from_words("en", [("hello", 900.0)], "abcdefghijklmnopqrstuvwxyz"),
    })
    backend, typist, clock = FakeBackend(current=RU), RecordingTypist(), Clock()
    ctrl = Controller(config or Config(), km, det, backend, typist, clock=clock,
                      probe=probe, apps_provider=lambda: list(apps))
    return dict(km=km, det=det, ctrl=ctrl, backend=backend, typist=typist, clock=clock)


def type_keys(env, keys):
    for key in keys:
        env["ctrl"].on_key(key.code, 1, 1)
        env["ctrl"].on_key(key.code, 0, 1)


def typed_chars(env) -> str:
    from wayswitch.buffer import KeyPress

    codes = [(c, s) for c, s in env["typist"].taps() if c != kc.KEY_BACKSPACE]
    return env["km"].decode([KeyPress(c, s) for c, s in codes], env["backend"].current())


# --- сопоставление приложений -----------------------------------------------

@pytest.mark.parametrize("app,patterns,expected", [
    ("TelegramDesktop", ["telegram"], True),
    ("org.telegram.desktop", ["Telegram"], True),
    ("code", ["telegram", "code"], True),
    ("TelegramDesktop", ["code"], False),
    ("TelegramDesktop", [], False),
    (None, ["telegram"], False),
    ("code", ["  code  "], True),      # пробелы вокруг не мешают
    ("code", [""], False),             # пустая строка не исключает всё
])
def test_app_matches(app, patterns, expected):
    assert a11y.app_matches(app, patterns) is expected


# --- решение «трогать или нет» ----------------------------------------------

def test_skip_reason_password_field():
    clock = Clock()
    probe = FakeProbe(clock, app="firefox", password=True)
    assert a11y.skip_reason(probe, [], True, clock()) == "защищённый ввод"


def test_skip_reason_password_can_be_disabled():
    clock = Clock()
    probe = FakeProbe(clock, app="firefox", password=True)
    assert a11y.skip_reason(probe, [], False, clock()) is None


def test_skip_reason_excluded_app():
    clock = Clock()
    probe = FakeProbe(clock, app="TelegramDesktop")
    assert a11y.skip_reason(probe, ["telegram"], True, clock()) == "приложение TelegramDesktop"


def test_skip_reason_stale_snapshot_blocks_protected_input():
    clock = Clock()
    probe = FakeProbe(clock, app="TelegramDesktop", age=a11y.MAX_AGE_SEC + 1)
    assert a11y.skip_reason(probe, ["telegram"], True, clock()) is not None


def test_skip_reason_unknown_focus_blocks_protected_input():
    clock = Clock()
    assert a11y.skip_reason(FakeProbe(clock), [], True, clock()) is not None
    assert a11y.skip_reason(FakeProbe(clock), [], False, clock()) is None


def test_skip_reason_uses_shell_app_id_without_atspi_name():
    class Probe:
        def snapshot(self):
            return FocusSnapshot(None, False, 100.0, ("jetbrains-idea",))

    assert "jetbrains-idea" in a11y.skip_reason(Probe(), ["idea"], True, 100.0)


def test_shell_identity_does_not_claim_password_detection(monkeypatch):
    monkeypatch.setattr(a11y, "query_shell_context",
                        lambda: a11y.ShellContext(("jetbrains-idea",)))
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: (_ for _ in ()).throw(RuntimeError()))
    snap = a11y.query_focus()
    assert snap.app is None
    assert snap.app_ids == ("jetbrains-idea",)


@pytest.mark.parametrize("role,password", [(61, False), (40, True)])
@pytest.mark.parametrize("app_ids", [(), ("editor.desktop",)])
def test_old_shell_extension_preserves_atspi_focus(monkeypatch, role, password, app_ids):
    calls = []

    def call_sync(_bus, _path, _iface, method, *_args):
        calls.append(method)
        if method == "GetFocusedApp" and app_ids:
            return SimpleNamespace(unpack=lambda: (list(app_ids),))
        raise RuntimeError(f"org.freedesktop.DBus.Error.UnknownMethod: {method}")

    repository = SimpleNamespace(
        Gio=SimpleNamespace(BusType=SimpleNamespace(SESSION=0),
                            DBusCallFlags=SimpleNamespace(NONE=0),
                            DBusError=SimpleNamespace(
                                get_remote_error=lambda e: str(e).split(":")[0]),
                            bus_get_sync=lambda *_: SimpleNamespace(call_sync=call_sync)),
        GLib=SimpleNamespace(VariantType=lambda signature: signature))
    monkeypatch.setitem(sys.modules, "gi", SimpleNamespace(repository=repository))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    tree = DictTree({
        a11y.ROOT: {"kids": ["editor"]},
        "editor": {"name": "Editor", "kids": ["window"]},
        "window": {"state": [a11y.ACTIVE], "kids": ["field"]},
        "field": {"state": [a11y.FOCUSED, 7], "role": role,
                  "interfaces": ("org.a11y.atspi.Text",), "caret": 0},
    })
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: object())
    monkeypatch.setattr(a11y, "_DbusTree", lambda _connection: tree)

    snap = a11y.query_focus()

    assert calls == ["GetFocusedContext", "GetFocusedApp",
                     "GetFocusedContext", "GetFocusedApp"]
    assert snap == FocusSnapshot("Editor", password, app_ids=app_ids, field="field")
    probe = SimpleNamespace(snapshot=lambda: snap._replace(taken_at=100.0))
    assert a11y.skip_reason(probe, [], True, 100.0) == (
        "защищённый ввод" if password else None)
    assert a11y.skip_reason(probe, ["editor"], False, 100.0) == "приложение Editor"


def shell_bus(monkeypatch, results):
    calls = []

    def call_sync(_bus, _path, _iface, method, *_args):
        calls.append(method)
        result = results.pop(0) if len(results) > 1 else results[0]
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(unpack=lambda: result)

    repository = SimpleNamespace(
        Gio=SimpleNamespace(BusType=SimpleNamespace(SESSION=0),
                            DBusCallFlags=SimpleNamespace(NONE=0),
                            DBusError=SimpleNamespace(
                                get_remote_error=lambda e: str(e).split(":")[0]),
                            bus_get_sync=lambda *_: SimpleNamespace(call_sync=call_sync)),
        GLib=SimpleNamespace(Variant=lambda signature, values: (signature, values),
                             VariantType=lambda signature: signature))
    monkeypatch.setitem(sys.modules, "gi", SimpleNamespace(repository=repository))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    return calls


@pytest.mark.parametrize("modal,ime,purpose,hints", [
    (True, False, 0, 0), (False, True, 8, 0), (False, True, 12, 0),
    (False, True, 0, 64), (False, True, 0, 128),
])
def test_shell_positive_protection_overrides_stale_safe_accessibility(
        monkeypatch, modal, ime, purpose, hints):
    shell_bus(monkeypatch, [(["editor.desktop"], modal, ime, purpose, hints, 123)])
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: object())
    monkeypatch.setattr(a11y, "_DbusTree", lambda _: editable_tree())
    snap = a11y.query_focus()
    assert snap.password
    assert snap.app_ids == ("editor.desktop",)


def test_shell_modal_with_no_app_still_protects_manual_correction(monkeypatch):
    shell_bus(monkeypatch, [([], True, False, 0, 0, 0)])
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: object())
    monkeypatch.setattr(a11y, "_DbusTree", lambda _: DictTree({a11y.ROOT: {"kids": []}}))
    snap = a11y.query_focus()
    assert snap.protection(0.0) is True


def test_stale_ime_purpose_without_current_focus_does_not_protect_normal_field(monkeypatch):
    shell_bus(monkeypatch, [(["editor.desktop"], False, False, 8, 192, 123)])
    tree = editable_tree()
    tree.nodes[a11y.ROOT] = tree.nodes.pop("root")
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: object())
    monkeypatch.setattr(a11y, "_DbusTree", lambda _: tree)
    snap = a11y.query_focus()
    assert snap.app == "Editor"
    assert not snap.password


def test_shell_context_change_during_accessibility_query_discards_old_field(monkeypatch):
    shell_bus(monkeypatch, [(["editor.desktop"], False, True, 0, 0, 123),
                            (["browser.desktop"], False, True, 0, 0, 456)])
    tree = editable_tree()
    tree.nodes[a11y.ROOT] = tree.nodes.pop("root")
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: object())
    monkeypatch.setattr(a11y, "_DbusTree", lambda _: tree)
    assert a11y.query_focus().app is None


@pytest.mark.parametrize("error", ["org.freedesktop.DBus.Error.NoReply",
                                  "org.freedesktop.DBus.Error.Timeout",
                                  "org.freedesktop.DBus.Error.Failed"])
@pytest.mark.parametrize("when", ["before", "after"])
def test_transient_shell_context_error_never_downgrades_to_legacy_safe_focus(
        monkeypatch, error, when):
    results = [RuntimeError(error), (["editor.desktop"],),
               RuntimeError(error), (["editor.desktop"],)]
    if when == "after":
        results.insert(0, (["editor.desktop"], False, False, 0, 0, 0))
    calls = shell_bus(monkeypatch, results)
    tree = editable_tree()
    tree.nodes[a11y.ROOT] = tree.nodes.pop("root")
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: object())
    monkeypatch.setattr(a11y, "_DbusTree", lambda _: tree)
    assert a11y.query_focus().app is None
    assert "GetFocusedApp" not in calls


def test_trusted_shell_pid_scopes_frameless_application(monkeypatch):
    shell_bus(monkeypatch, [(["editor.desktop"], False, True, 0, 0, 123)])
    tree = editable_tree()
    tree.nodes[a11y.ROOT] = tree.nodes.pop("root")
    tree.nodes["window"]["state"] = ()
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: object())
    monkeypatch.setattr(a11y, "_DbusTree", lambda _: tree)
    assert a11y.query_focus() == FocusSnapshot("Editor", app_ids=("editor.desktop",),
                                             field="field")


def test_trusted_shell_pid_does_not_accept_other_active_application(monkeypatch):
    shell_bus(monkeypatch, [(["other.desktop"], False, True, 0, 0, 456)])
    tree = editable_tree()
    tree.nodes[a11y.ROOT] = tree.nodes.pop("root")
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: object())
    monkeypatch.setattr(a11y, "_DbusTree", lambda _: tree)
    assert a11y.query_focus().app is None


@pytest.fixture
def proc_metadata(tmp_path, monkeypatch):
    files = {}
    roots = {}
    closed = []
    reads = []
    flags = SimpleNamespace(O_RDONLY=0, O_DIRECTORY=0x10000000,
                            O_NOFOLLOW=0x20000000, O_NONBLOCK=0x40000000, O_CLOEXEC=0)

    def openat(path, requested, *, dir_fd=None):
        if dir_fd is None:
            assert requested & flags.O_DIRECTORY
            assert path.startswith("/proc/") and path.endswith("/root")
            pid = int(path.split("/")[2])
            if pid not in files:
                raise FileNotFoundError
            descriptor = 100000 + len(roots)
            roots[descriptor] = files[pid]
            return descriptor
        assert path == ".flatpak-info"
        assert requested & flags.O_NOFOLLOW and requested & flags.O_NONBLOCK
        source = roots[dir_fd]
        if isinstance(source, Exception):
            raise source
        return os.open(source, os.O_RDONLY | getattr(os, "O_BINARY", 0))

    def close(descriptor):
        if descriptor in roots:
            closed.append(descriptor)
        else:
            os.close(descriptor)

    def fdopen(descriptor, mode):
        reads.append(descriptor)
        return os.fdopen(descriptor, mode)

    fake_os = SimpleNamespace(**vars(flags), open=openat, close=close,
                              fstat=os.fstat, fdopen=fdopen)
    monkeypatch.setattr(a11y, "os", fake_os, raising=False)

    def write(pid, contents):
        path = tmp_path / f"{pid}-{len(list(tmp_path.iterdir()))}.flatpak-info"
        path.write_bytes(contents if isinstance(contents, bytes) else contents.encode("utf-8"))
        files[pid] = path
        return path

    return SimpleNamespace(write=write, files=files, roots=roots, closed=closed,
                           reads=reads, os=fake_os)


FLATPAK_INFO = "[Application]\nname=org.example.Editor\n[Instance]\ninstance-id=123456\n"


def proxy_focus_tree():
    tree = editable_tree()
    tree.nodes["app"]["pid"] = 456
    return tree


def test_flatpak_proxy_pid_matches_focused_instance_despite_distinct_files(proc_metadata):
    proxy = proc_metadata.write(456, FLATPAK_INFO)
    app = proc_metadata.write(123, FLATPAK_INFO)
    assert proxy.stat().st_ino != app.stat().st_ino
    assert a11y.find_focus(proxy_focus_tree(), "root", 123) == FocusSnapshot(
        "Editor", field="field")
    assert len(proc_metadata.closed) == 2


@pytest.mark.parametrize("metadata", [
    "", "[Application]\nname=org.example.Editor\n", "[Instance]\ninstance-id=123456\n",
    "[Application]\nname=\n[Instance]\ninstance-id=123456\n",
    "[Application]\nname=org.example.Editor\n[Instance]\ninstance-id= \n",
    "[Application]\nname=org.example.Other\n[Instance]\ninstance-id=123456\n",
    "[Application]\nname=org.example.Editor\n[Instance]\ninstance-id=654321\n",
    FLATPAK_INFO + "[Application]\nname=org.example.Other\n",
    FLATPAK_INFO + "broken ini data\n", b"\xff\xfe", FLATPAK_INFO + "#" * 65536,
    "[DEFAULT]\nname=org.example.Editor\ninstance-id=123456\n[Application]\n[Instance]\n",
    FLATPAK_INFO.replace("name=", "NAME="), FLATPAK_INFO + "\0",
], ids=["empty", "no-instance", "no-app", "empty-app", "empty-instance", "other-app",
        "other-instance", "duplicate-section", "invalid-ini", "invalid-utf8", "oversize",
        "inherited-defaults", "wrong-key-case", "nul"])
def test_flatpak_pid_mismatch_does_not_accept_incomplete_or_other_instance(proc_metadata, metadata):
    proc_metadata.write(456, metadata)
    proc_metadata.write(123, FLATPAK_INFO)
    assert a11y.find_focus(proxy_focus_tree(), "root", 123) == FocusSnapshot()


@pytest.mark.parametrize("failure", [FileNotFoundError(), PermissionError(), OSError("symlink")])
def test_flatpak_metadata_open_error_does_not_accept_pid_mismatch(proc_metadata, failure):
    proc_metadata.files[456] = failure
    proc_metadata.write(123, FLATPAK_INFO)
    assert a11y.find_focus(proxy_focus_tree(), "root", 123) == FocusSnapshot()
    assert len(proc_metadata.closed) == 1


def test_flatpak_metadata_nonregular_file_is_not_read(proc_metadata):
    proc_metadata.write(456, FLATPAK_INFO)
    proc_metadata.write(123, FLATPAK_INFO)
    proc_metadata.os.fstat = lambda _fd: SimpleNamespace(st_mode=stat.S_IFIFO, st_size=0)
    assert a11y.find_focus(proxy_focus_tree(), "root", 123) == FocusSnapshot()
    assert not proc_metadata.reads


def test_matching_native_pid_does_not_open_flatpak_metadata(proc_metadata):
    tree = editable_tree()
    assert a11y.find_focus(tree, "root", 123) == FocusSnapshot("Editor", field="field")
    assert not proc_metadata.roots


def test_flatpak_metadata_read_uses_the_already_opened_root(proc_metadata):
    expected = proc_metadata.write(456, FLATPAK_INFO)
    replacement = proc_metadata.write(789, FLATPAK_INFO.replace("123456", "654321"))
    proc_metadata.write(123, FLATPAK_INFO)
    openat = proc_metadata.os.open

    def changed_process(path, flags, *, dir_fd=None):
        descriptor = openat(path, flags, dir_fd=dir_fd)
        if path == "/proc/456/root":
            proc_metadata.files[456] = replacement
        return descriptor

    proc_metadata.os.open = changed_process
    assert a11y.find_focus(proxy_focus_tree(), "root", 123) == FocusSnapshot(
        "Editor", field="field")
    assert expected in proc_metadata.roots.values()


def test_flatpak_pid_scope_distinguishes_simultaneous_instances(proc_metadata):
    proc_metadata.write(456, FLATPAK_INFO.replace("123456", "654321"))
    proc_metadata.write(789, FLATPAK_INFO)
    proc_metadata.write(123, FLATPAK_INFO)
    tree = proxy_focus_tree()
    tree.nodes["root"]["kids"] = ["other-app", "app"]
    tree.nodes["other-app"] = {"name": "Other instance", "pid": 456, "kids": ["other-window"]}
    tree.nodes["other-window"] = {"state": (1,), "kids": ["other-field"]}
    tree.nodes["other-field"] = {"state": (12,), "role": 40}
    tree.nodes["app"]["pid"] = 789
    assert a11y.find_focus(tree, "root", 123) == FocusSnapshot("Editor", field="field")


def test_real_dbus_metadata_adapter_accepts_editable_paragraph_without_reading_text(monkeypatch):
    shell_bus(monkeypatch, [(["writer.desktop"], False, False, 0, 0, 0)])
    tree = a11y._DbusTree(object())
    root, app, window, field = a11y.ROOT, (":1.5", "/app"), (":1.5", "/window"), (":1.5", "/field")

    def call(node, iface, method, args, signature):
        if method == "GetChildren":
            return ({root: [app], app: [window], window: [field], field: []}[node],)
        if method == "GetState":
            return ([{window: 1 << 1, field: (1 << 12) | (1 << 7)}.get(node, 0)],)
        if method == "GetRole":
            return (73 if node == field else 23,)
        if method == "GetInterfaces":
            return (["org.a11y.atspi.Text"],)
        if method == "GetAttributes":
            return ({"text-input-type": "text", "private-secret": "never-return-this"},)
        if method == "GetRelationSet":
            return ([],)
        if method == "Get":
            assert args in [("(ss)", (a11y.ACC, "Name")),
                            ("(ss)", ("org.a11y.atspi.Text", "CaretOffset"))]
            if args[1][1] == "Name":
                assert node == app, "field/ancestor Name must never be read"
                return ("Writer",)
            return (0,)
        pytest.fail(f"Unexpected content access: {iface}.{method}")

    tree._call = call
    monkeypatch.setattr(a11y, "_DbusTree", lambda _: tree)
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: object())
    assert a11y.query_focus() == FocusSnapshot("Writer", app_ids=("writer.desktop",), field=field)


@pytest.mark.parametrize("broken", [None, "caret", "interfaces", "attrs", "count", "link",
                                   "range", "range-bool", "object", "ancestor-role",
                                   "children", "pid"])
def test_dbus_unicode_embedded_password_and_metadata_failures(monkeypatch, broken):
    shell_bus(monkeypatch, [(["browser.desktop"], False, False, 0, 0, 777)])
    tree = a11y._DbusTree(object())
    root = a11y.ROOT
    app, window, field, link, anchor = [(":1.5", f"/{name}")
                                      for name in ("app", "window", "field", "link", "anchor")]

    def call(node, iface, method, args, signature):
        if method == "GetConnectionUnixProcessID":
            assert node == ("org.freedesktop.DBus", "/org/freedesktop/DBus")
            assert args == ("(s)", (":1.5",))
            assert signature == "(u)"
            return None if broken == "pid" else (777,)
        if method == "GetChildren":
            if broken == "children" and node == window:
                return None
            return ({root: [app], app: [window], window: [field], field: []}[node],)
        if method == "GetState":
            return ([{window: 1 << 1, field: (1 << 12) | (1 << 7)}.get(node, 0)],)
        if method == "GetRole":
            if broken == "ancestor-role" and node == window:
                return None
            return ({field: 85, anchor: 11}.get(node, 23),)
        if method == "GetInterfaces":
            return None if broken == "interfaces" else (["org.a11y.atspi.Text",
                                                          "org.a11y.atspi.Hypertext"],)
        if method == "GetAttributes":
            if broken == "attrs" and node == field:
                return None
            return ({"text-input-type": "password" if node == anchor else "text",
                     "private-secret": "must-be-discarded"},)
        if method == "GetRelationSet":
            assert signature == "(a(ua(so)))"
            return ([],)
        if method == "GetNLinks":
            assert iface == "org.a11y.atspi.Hypertext" and signature == "(i)"
            return None if broken == "count" else (1,)
        if method in ("GetLink", "GetObject"):
            assert args == ("(i)", (0,)) and signature == "((so))"
            if method == "GetLink":
                assert iface == "org.a11y.atspi.Hypertext"
                return None if broken == "link" else (link,)
            assert iface == "org.a11y.atspi.Hyperlink"
            return None if broken == "object" else (anchor,)
        if method == "Get":
            interface, name = args[1]
            if name == "Name":
                assert node == app and interface == a11y.ACC
                return ("Browser",)
            if name == "CaretOffset":
                assert interface == "org.a11y.atspi.Text"
                return (-1 if broken == "caret" else 1,)
            assert interface == "org.a11y.atspi.Hyperlink"
            assert name in ("StartIndex", "EndIndex")
            if broken == "range-bool":
                return (False if name == "StartIndex" else 2,)
            return None if broken == "range" else (1 if name == "StartIndex" else 2,)
        pytest.fail(f"Unexpected content access: {iface}.{method}")

    tree._call = call
    monkeypatch.setattr(a11y, "_DbusTree", lambda _: tree)
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: object())
    snap = a11y.query_focus()
    assert snap.app_ids == ("browser.desktop",)
    if broken is None:
        assert snap == FocusSnapshot("Browser", True, app_ids=("browser.desktop",), field=anchor)
    else:
        assert snap.app is None


def test_skip_reason_no_probe_never_blocks():
    clock = Clock()
    assert a11y.skip_reason(a11y.NullProbe(), ["telegram"], True, clock()) is None


# --- сам поток опроса -------------------------------------------------------

def test_probe_updates_snapshot_from_query():
    calls = []

    def query():
        calls.append(1)
        return "code", False

    clock = Clock()
    probe = a11y.AtspiProbe(poll_sec=0.01, clock=clock, query=query)
    try:
        probe.poke()
        deadline = time.monotonic() + 2.0
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls, "поток ни разу не опросил фокус"
        assert probe.healthy()
        assert probe.snapshot().app == "code"
    finally:
        probe.close()


def test_probe_survives_failing_query():
    def query():
        raise RuntimeError("at-spi недоступен")

    probe = a11y.AtspiProbe(poll_sec=0.01, query=query)
    try:
        probe.poke()
        deadline = time.monotonic() + 2.0
        while probe.healthy() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert probe.healthy() is False
        assert probe.snapshot().app is None
    finally:
        probe.close()


def test_failed_probe_does_not_publish_fresh_focus():
    def query():
        raise RuntimeError("at-spi недоступен")

    probe = a11y.AtspiProbe(clock=Clock(), query=query)
    probe._refresh()
    assert probe.snapshot().age(100.0) > a11y.MAX_AGE_SEC


@pytest.fixture
def focus_bus(monkeypatch):
    state = {"connection": object(), "callbacks": [], "closed": []}

    def watch(connection, callback):
        state["callbacks"].append(callback)
        live = threading.Event()
        live.set()
        return a11y._FocusWatch(connection, live,
                                lambda: state["closed"].append(connection))

    monkeypatch.setattr(a11y, "_a11y_connection", lambda: state["connection"])
    monkeypatch.setattr(a11y, "_watch_focus_events", watch, raising=False)
    return state


def test_focus_event_invalidates_young_safe_snapshot(monkeypatch, focus_bus):
    monkeypatch.setattr(a11y, "query_focus", lambda: ("Editor", False))
    probe = a11y.AtspiProbe(clock=Clock())
    probe._refresh()
    assert probe.snapshot().app == "Editor"
    assert focus_bus["callbacks"], "опрос начат без подписки на смену фокуса"
    focus_bus["callbacks"][0]()
    assert a11y.skip_reason(probe, [], True, 100.0) is not None


def test_focus_event_during_query_cannot_republish_old_safe_focus(monkeypatch, focus_bus):
    def query():
        focus_bus["callbacks"][0]()
        return "Editor", False

    monkeypatch.setattr(a11y, "query_focus", query)
    probe = a11y.AtspiProbe(clock=Clock())
    probe._refresh()
    assert focus_bus["callbacks"], "опрос начат без подписки на смену фокуса"
    assert probe.snapshot().app is None
    assert a11y.skip_reason(probe, [], True, 100.0) is not None


def test_registration_loss_before_query_generation_stays_blocked(monkeypatch, focus_bus):
    monkeypatch.setattr(a11y, "query_focus", lambda: ("Editor", False))
    probe = a11y.AtspiProbe(clock=Clock())
    live = threading.Event()
    live.set()
    is_set = live.is_set

    def checked_then_lost():
        was_live = is_set()
        live.clear()
        probe._invalidate()
        monkeypatch.setattr(live, "is_set", is_set)
        return was_live

    monkeypatch.setattr(live, "is_set", checked_then_lost)
    monkeypatch.setattr(a11y, "_watch_focus_events", lambda connection, _callback:
                        a11y._FocusWatch(connection, live, lambda: None))
    probe._refresh()
    assert probe.snapshot().app is None
    assert a11y.skip_reason(probe, [], True, 100.0) is not None


def test_unavailable_focus_watcher_does_not_publish_safe_focus(monkeypatch, focus_bus):
    def unavailable(*_args):
        raise RuntimeError("реестр событий недоступен")

    monkeypatch.setattr(a11y, "_watch_focus_events", unavailable)
    monkeypatch.setattr(a11y, "query_focus", lambda: ("Editor", False))
    probe = a11y.AtspiProbe(clock=Clock())
    probe._refresh()
    assert not probe.healthy()
    assert probe.snapshot().app is None


def test_close_during_query_does_not_publish_late_result():
    entered, release = threading.Event(), threading.Event()

    def query():
        entered.set()
        assert release.wait(2.0)
        return "Editor", False

    probe = a11y.AtspiProbe(clock=Clock(), query=query)
    probe.poke()
    thread = probe._thread
    try:
        assert entered.wait(1.0)
        probe.close()
    finally:
        release.set()
        thread.join(2.0)
    assert not thread.is_alive()
    assert probe.snapshot().app is None


def test_close_during_registration_cleans_late_subscription(monkeypatch, focus_bus):
    entered, release, cleaned = threading.Event(), threading.Event(), threading.Event()

    def watch(*_args):
        entered.set()
        assert release.wait(2.0)
        live = threading.Event()
        live.set()
        return a11y._FocusWatch(focus_bus["connection"], live, cleaned.set)

    monkeypatch.setattr(a11y, "_watch_focus_events", watch)
    monkeypatch.setattr(a11y, "query_focus", lambda: ("Editor", False))
    probe = a11y.AtspiProbe(clock=Clock())
    probe.poke()
    thread = probe._thread
    try:
        assert entered.wait(1.0), "подписка не устанавливается до опроса"
        probe.close()
    finally:
        release.set()
        thread.join(2.0)
    assert cleaned.is_set()
    assert probe.snapshot().app is None


def test_probe_replaces_subscription_when_bus_reconnects(monkeypatch, focus_bus):
    monkeypatch.setattr(a11y, "query_focus", lambda: ("Editor", False))
    probe = a11y.AtspiProbe(clock=Clock())
    probe._refresh()
    previous = focus_bus["connection"]
    focus_bus["connection"] = object()
    probe._refresh()
    assert focus_bus["closed"] == [previous]
    assert probe.snapshot().app == "Editor"


@pytest.fixture
def event_connections(monkeypatch):
    class Connections(list):
        pass

    connections = Connections()
    registered = set()

    class Connection:
        def __init__(self):
            self.callbacks = {}
            self.closed = False
            self.owner = ":1.42"
            connections.append(self)

        def signal_subscribe(self, _sender, _iface, member, _path, arg0, _flags, callback, _data):
            assert (member, arg0) in [("StateChanged", "focused"),
                                       ("NameOwnerChanged", "org.a11y.atspi.Registry")]
            self.callbacks[member] = callback
            return member

        def signal_unsubscribe(self, subscription):
            del self.callbacks[subscription]

        def connect(self, signal, callback):
            assert signal == "closed"
            self.callbacks[signal] = callback
            return signal

        def disconnect(self, signal):
            del self.callbacks[signal]

        def call_sync(self, _bus, _path, _iface, method, args, *_rest):
            if method == "StartServiceByName":
                assert args == ("(su)", ("org.a11y.atspi.Registry", 0))
                self.owner = self.owner or ":1.42"
                return None
            if method == "GetNameOwner":
                if self.owner is None:
                    raise RuntimeError("NameHasNoOwner")
                return SimpleNamespace(unpack=lambda: (self.owner,))
            assert method == "RegisterEvent"
            assert args == ("(sass)", ("object:state-changed:focused", [], ""))
            registered.add(self)

        def close_sync(self, _cancellable):
            self.closed = True
            registered.discard(self)

    class Session:
        def __init__(self):
            self.callbacks = {}
            self.handlers = {}
            self.closed = False
            self.next_id = 0

        def signal_subscribe(self, sender, iface, member, path, arg0, _flags, callback, _data):
            from wayswitch.backends.gnome_shell import BUS_NAME, INTERFACE, OBJECT_PATH

            assert (sender, iface, member, path, arg0) in [
                (BUS_NAME, INTERFACE, "FocusedContextChanged", OBJECT_PATH, None),
                ("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                 "/org/freedesktop/DBus", BUS_NAME)]
            self.next_id += 1
            self.callbacks[self.next_id] = (member, callback)
            return self.next_id

        def signal_unsubscribe(self, subscription):
            del self.callbacks[subscription]

        def connect(self, signal, callback):
            assert signal == "closed"
            self.next_id += 1
            self.handlers[self.next_id] = callback
            return self.next_id

        def disconnect(self, handler):
            del self.handlers[handler]

        def close_sync(self, _cancellable):
            self.closed = True

    connections.session = Session()

    repository = SimpleNamespace(
        Gio=SimpleNamespace(DBusSignalFlags=SimpleNamespace(NONE=0),
                            DBusCallFlags=SimpleNamespace(NONE=0),
                            BusType=SimpleNamespace(SESSION=0),
                            bus_get_sync=lambda *_: connections.session),
        GLib=SimpleNamespace(Variant=lambda signature, values: (signature, values),
                             VariantType=lambda signature: signature, Error=RuntimeError))
    monkeypatch.setitem(sys.modules, "gi", SimpleNamespace(repository=repository))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    monkeypatch.setattr(a11y, "_new_a11y_connection", Connection, raising=False)
    return connections, registered


@pytest.mark.parametrize("signal", ["FocusedContextChanged", "NameOwnerChanged"])
def test_shell_context_event_invalidates_same_accessibility_field_without_payload(
        event_connections, signal):
    connections, _registered = event_connections
    probe = a11y.AtspiProbe(clock=Clock(),
                            query=lambda: FocusSnapshot("Browser", field=(":1.5", "/same")))
    watch = a11y._watch_focus_events(object(), probe._invalidate)
    probe._refresh()
    assert probe.snapshot().app == "Browser"

    class PrivatePayload:
        def unpack(self):
            pytest.fail("focus event payload must never be read")

    matching = [callback for member, callback in connections.session.callbacks.values()
                if member == signal]
    for callback in matching:
        callback(None, None, None, None, None, PrivatePayload(), None)
    assert probe.snapshot().app is None
    assert watch.live.is_set()
    watch.close()


def test_shared_session_watch_cleanup_preserves_other_probe_and_bus(event_connections):
    connections, _registered = event_connections
    old = a11y._watch_focus_events(object(), lambda: None)
    new = a11y._watch_focus_events(object(), lambda: None)
    assert len(connections.session.callbacks) == 4
    old.close()
    assert len(connections.session.callbacks) == 2
    assert not connections.session.closed
    assert new.live.is_set()
    new.close()
    assert not connections.session.callbacks
    assert not connections.session.handlers
    assert not connections.session.closed


def test_session_bus_loss_invalidates_snapshot_and_registration(event_connections):
    connections, _registered = event_connections
    invalidations = []
    watch = a11y._watch_focus_events(object(), lambda: invalidations.append(True))
    for callback in list(connections.session.handlers.values()):
        callback(None, None, None)
    assert not watch.live.is_set()
    assert invalidations
    watch.close()


def test_shell_event_during_query_cannot_republish_safe_focus(event_connections):
    connections, _registered = event_connections

    def query():
        for member, callback in connections.session.callbacks.values():
            if member == "FocusedContextChanged":
                callback(None, None, None, None, None, object(), None)
        return FocusSnapshot("Browser", field=(":1.5", "/same"))

    probe = a11y.AtspiProbe(clock=Clock(), query=query)
    watch = a11y._watch_focus_events(object(), probe._invalidate)
    probe._refresh()
    assert probe.snapshot().app is None
    watch.close()


def test_session_registration_failure_closes_owned_accessibility_connection(
        monkeypatch, event_connections):
    connections, registered = event_connections

    def failed(*_args):
        raise RuntimeError("session bus unavailable")

    monkeypatch.setattr(sys.modules["gi.repository"].Gio, "bus_get_sync", failed)
    with pytest.raises(RuntimeError, match="session bus unavailable"):
        a11y._watch_focus_events(object(), lambda: None)
    assert connections[0].closed
    assert not connections[0].callbacks
    assert not registered
    assert not connections.session.closed


def test_overlapping_probe_cleanup_keeps_new_registration(event_connections):
    connections, registered = event_connections
    old = a11y._watch_focus_events(object(), lambda: None)
    new = a11y._watch_focus_events(object(), lambda: None)
    assert len(connections) == 2
    old.close()
    assert registered == {connections[1]}
    assert new.live.is_set()
    new.close()
    assert not registered


def test_focus_watch_activates_registry_without_existing_owner(monkeypatch, event_connections):
    connections, registered = event_connections
    connection_type = a11y._new_a11y_connection
    initialize = connection_type.__init__

    def cold(self):
        initialize(self)
        self.owner = None

    monkeypatch.setattr(connection_type, "__init__", cold)
    watch = a11y._watch_focus_events(object(), lambda: None)
    assert watch.live.is_set()
    assert registered == {connections[0]}
    watch.close()


@pytest.mark.parametrize("signal", ["closed", "NameOwnerChanged"])
def test_lost_focus_registration_invalidates_cached_safe_focus(event_connections, signal):
    connections, _registered = event_connections
    invalidations = []
    watch = a11y._watch_focus_events(object(), lambda: invalidations.append(True))
    assert watch.live.is_set()
    connections[0].callbacks[signal](None, None, None, None, None, None, None)
    assert not watch.live.is_set()
    assert invalidations
    watch.close()


def test_registry_restart_during_registration_does_not_restore_live_flag(
        monkeypatch, event_connections):
    connections, registered = event_connections
    connection_type = a11y._new_a11y_connection
    call = connection_type.call_sync

    def restart(self, bus, path, iface, method, args, *rest):
        result = call(self, bus, path, iface, method, args, *rest)
        if method == "RegisterEvent":
            self.owner = ":1.43"
            self.callbacks["NameOwnerChanged"](None, None, None, None, None, None, None)
        return result

    monkeypatch.setattr(connection_type, "call_sync", restart)
    with pytest.raises(RuntimeError):
        a11y._watch_focus_events(object(), lambda: None)
    assert connections[0].closed
    assert not registered
    assert not connections.session.callbacks
    assert not connections.session.handlers


def test_focus_loss_and_gain_both_invalidate_without_reading_event_payload(event_connections):
    connections, _registered = event_connections
    probe = a11y.AtspiProbe(clock=Clock(), query=lambda: ("Editor", False))
    watch = a11y._watch_focus_events(object(), probe._invalidate)
    for enabled in (0, 1):
        probe._refresh()
        connections[0].callbacks["StateChanged"](
            None, None, None, None, None, ("focused", enabled, 0, object(), {}), None)
        assert a11y.skip_reason(probe, [], True, 100.0) is not None
        assert watch.live.is_set()
    watch.close()


def test_idle_worker_releases_subscription(monkeypatch, focus_bus):
    monkeypatch.setattr(a11y, "IDLE_STOP_SEC", 0.0)
    monkeypatch.setattr(a11y, "query_focus", lambda: ("Editor", False))
    probe = a11y.AtspiProbe(poll_sec=0.01)
    try:
        probe.poke()
        deadline = time.monotonic() + 1.0
        while not focus_bus["closed"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert focus_bus["closed"]
        assert probe.snapshot().app is None
    finally:
        probe.close(wait=True)


def test_make_probe_respects_flag():
    assert isinstance(a11y.make_probe(False), a11y.NullProbe)
    probe = a11y.make_probe(True)
    assert isinstance(probe, a11y.AtspiProbe)
    probe.close()


# --- контроллер молчит там, где надо ----------------------------------------

def test_controller_skips_excluded_app():
    clock = Clock()
    env = make_env(probe=FakeProbe(clock, app="TelegramDesktop"), apps=["telegram"])
    type_keys(env, keys_for_text(env["km"], "ghbdtn", US))
    env["ctrl"].on_key(kc.KEY_SPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_SPACE, 0, 1)
    assert env["typist"].taps() == []
    assert env["backend"].set_calls == []
    assert env["ctrl"].context[-1] is None


def test_controller_skips_password_field():
    clock = Clock()
    env = make_env(probe=FakeProbe(clock, app="firefox", password=True), apps=[])
    type_keys(env, keys_for_text(env["km"], "ghbdtn", US))
    env["ctrl"].on_key(kc.KEY_SPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_SPACE, 0, 1)
    assert env["typist"].taps() == []


def test_controller_fixes_in_other_app():
    """В обычной программе автоисправление работает как обычно."""
    clock = Clock()
    env = make_env(probe=FakeProbe(clock, app="gedit"), apps=["telegram"])
    type_keys(env, keys_for_text(env["km"], "руддщ", RU))
    env["ctrl"].on_key(kc.KEY_SPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_SPACE, 0, 1)
    assert env["backend"].set_calls == [US]


def test_controller_pokes_probe_on_word():
    clock = Clock()
    probe = FakeProbe(clock, app="gedit")
    env = make_env(probe=probe)
    type_keys(env, keys_for_text(env["km"], "ghbdtn", US))
    env["ctrl"].on_key(kc.KEY_SPACE, 1, 1)
    env["ctrl"].on_key(kc.KEY_SPACE, 0, 1)
    assert probe.pokes >= 1


# --- кэш списка приложений --------------------------------------------------

def test_apps_list_is_cached_by_mtime(tmp_path, monkeypatch):
    """Файл не перечитывается на каждом слове, но правка подхватывается."""
    from wayswitch import controller as ctrl_mod

    path = tmp_path / "apps.txt"
    path.write_text("telegram\n", encoding="utf-8")
    monkeypatch.setattr(ctrl_mod, "apps_path", lambda: path)

    calls = []

    def provider():
        calls.append(1)
        return cfgmod.load_apps(path)

    clock = Clock()
    env = make_env(probe=FakeProbe(clock, app="gedit"), apps=[])
    env["ctrl"]._apps_provider = provider

    assert env["ctrl"]._apps() == ["telegram"]
    assert env["ctrl"]._apps() == ["telegram"]
    assert len(calls) == 1, "второй вызов должен прийти из кэша"

    # файл изменился — кэш обязан перечитаться
    path.write_text("telegram\ncode\n", encoding="utf-8")
    import os
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    assert env["ctrl"]._apps() == ["telegram", "code"]
    assert len(calls) == 2


# --- файл исключённых приложений --------------------------------------------

def test_apps_file_roundtrip(tmp_path):
    path = tmp_path / "apps.txt"
    assert cfgmod.load_apps(path) == []
    assert cfgmod.add_app("telegram", path) is True
    assert cfgmod.add_app("Telegram", path) is False   # дубль без регистра
    assert cfgmod.add_app("code", path) is True
    assert cfgmod.load_apps(path) == ["telegram", "code"]
    assert cfgmod.remove_app("TELEGRAM", path) is True
    assert cfgmod.load_apps(path) == ["code"]
    assert cfgmod.remove_app("нет-такого", path) is False


def test_apps_file_ignores_comments(tmp_path):
    path = tmp_path / "apps.txt"
    path.write_text("# мои исключения\ntelegram\n\n  code  \n", encoding="utf-8")
    assert cfgmod.load_apps(path) == ["telegram", "code"]


@pytest.mark.parametrize("name,expected", [("skip_password_fields", True),
                                           ("fix_on_tab", False),
                                           ("fix_on_enter", False)])
def test_focus_defaults(name, expected):
    assert getattr(Config().general, name) is expected


# --- Tab/Enter как конец слова ----------------------------------------------

def make_buffer(**kwargs):
    return InputBuffer(make_keymap(), 8.0, **kwargs)


def test_tab_resets_word_by_default():
    buf = make_buffer()
    for key in keys_for_text(make_keymap(), "ghbdtn", US):
        buf.feed(key.code, 1, 1, 0.0)
        buf.feed(key.code, 0, 1, 0.0)
    event = buf.feed(kc.KEY_TAB, 1, 1, 0.0)
    assert event is not None and event.kind == "reset"


def test_tab_ends_word_when_enabled():
    km = make_keymap()
    buf = make_buffer(terminators={kc.KEY_TAB})
    for key in keys_for_text(km, "ghbdtn", US):
        buf.feed(key.code, 1, 1, 0.0)
        buf.feed(key.code, 0, 1, 0.0)
    event = buf.feed(kc.KEY_TAB, 1, 1, 0.0)
    assert event is not None and event.kind == "word"
    assert len(event.keys) == 6


def test_enter_ends_word_when_enabled():
    km = make_keymap()
    buf = make_buffer(terminators={kc.KEY_ENTER})
    for key in keys_for_text(km, "ghbdtn", US):
        buf.feed(key.code, 1, 1, 0.0)
        buf.feed(key.code, 0, 1, 0.0)
    event = buf.feed(kc.KEY_ENTER, 1, 1, 0.0)
    assert event is not None and event.kind == "word"


def test_tab_enter_terminators_are_disabled():
    """fix_on_tab/fix_on_enter читаются (старые конфиги), но ничего не включают:
    к моменту фикса Tab уже выбрал автодополнение, Enter отправил сообщение."""
    config = Config()
    config.general.fix_on_tab = True
    config.general.fix_on_enter = True
    env = make_env(config=config)
    assert env["ctrl"].buffer._terminators == frozenset()


# --- обход дерева доступности ---------------------------------------------------------

class DictTree:
    """Дерево доступности в памяти: узел -> (дети, состояние, роль, имя)."""

    def __init__(self, nodes):
        self.nodes = nodes
        self.prepared = []
        self.reads = []

    def children(self, node):
        return self.nodes[node].get("kids", [])

    def state(self, node):
        if self.nodes[node].get("state", ()) is None:
            return []
        bits = 0
        for bit in self.nodes[node].get("state", ()):
            bits |= 1 << bit
        return [bits]

    def role(self, node):
        return self.nodes[node].get("role", 39)

    def interfaces(self, node):
        return self.nodes[node].get("interfaces", ())

    def caret(self, node):
        return self.nodes[node].get("caret")

    def input_type(self, node):
        return self.nodes[node].get("input_type", "")

    def link_count(self, node):
        return self.nodes[node].get("link_count", len(self.nodes[node].get("links", [])))

    def link(self, node, index):
        return self.nodes[node]["links"][index]

    def link_range(self, node):
        return self.nodes[node].get("range")

    def link_object(self, node):
        return self.nodes[node].get("object")

    def pid(self, node):
        return self.nodes[node].get("pid")

    def prepare(self, node):
        self.prepared.append(node)
        self.nodes[node].update(self.nodes[node].get("on_prepare", {}))

    def app_name(self, node):
        return self.nodes[node].get("name", "")

    def app_of(self, node):
        return self.nodes[node].get("app")


def editable_tree(**field):
    nodes = {"root": {"kids": ["app"]},
             "app": {"name": "Editor", "kids": ["window"], "pid": 123},
             "window": {"state": (1,), "kids": ["field"]},
             "field": {"state": (12, 7), "role": 79,
                       "interfaces": ("org.a11y.atspi.Text",), "caret": 0}}
    nodes["field"].update(field)
    return DictTree(nodes)


@pytest.mark.parametrize("role", [61, 79, 73, 83, 85, 110])
def test_editable_text_accepts_writer_and_browser_container_roles(role):
    tree = editable_tree(role=role)
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", field="field")


def test_defunct_paragraph_cannot_confirm_cached_focus():
    tree = editable_tree(role=73, state=(6, a11y.FOCUSED, a11y.EDITABLE))
    assert a11y.find_focus(tree, "root") == FocusSnapshot()


def test_cached_calc_paragraph_under_defunct_editor_does_not_hide_live_editor():
    tree = editable_tree(role=73)
    tree.nodes["window"]["kids"] = ["old-editor", "current-editor"]
    tree.nodes.update({
        "old-editor": {"state": (6,), "kids": ["old-paragraph"]},
        "old-paragraph": {"role": 73, "state": (a11y.FOCUSED, a11y.EDITABLE),
                          "interfaces": (a11y.TEXT,), "caret": 7},
        "current-editor": {"kids": ["field"]},
    })
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", field="field")


def test_parent_destroyed_during_focus_query_cannot_confirm_live_paragraph():
    tree = editable_tree(role=73)
    state = tree.state

    def changed(node):
        if node == "field":
            tree.nodes["window"]["state"] = (6,)
        return state(node)

    tree.state = changed
    assert a11y.find_focus(tree, "root") == FocusSnapshot()


def test_defunct_active_window_does_not_hide_current_window():
    tree = editable_tree(role=73)
    tree.nodes["app"]["kids"] = ["old-window", "window"]
    tree.nodes["old-window"] = {"state": (6, a11y.ACTIVE), "kids": ["old-field"]}
    tree.nodes["old-field"] = dict(tree.nodes["field"])
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", field="field")


@pytest.mark.parametrize("field", [
    {"state": (12,)}, {"state": (7,)}, {"interfaces": ()},
    {"interfaces": None}, {"caret": -1}, {"caret": None},
    {"caret": False}, {"caret": True},
    {"role": None}, {"role": 0}, {"role": 67}, {"input_type": None},
])
def test_incomplete_or_readonly_text_does_not_confirm_safe_input(field):
    assert a11y.find_focus(editable_tree(**field), "root") == FocusSnapshot()


@pytest.mark.parametrize("role", [40, 60])
def test_protected_role_does_not_need_editable_or_text_interface(role):
    tree = editable_tree(role=role, state=(12,), interfaces=(), caret=None)
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", True, field="field")


@pytest.mark.parametrize("parent", [{"role": 40}, {"role": 60},
                                  {"input_type": "password"}])
def test_protected_ancestor_overrides_editable_child(parent):
    tree = editable_tree()
    tree.nodes["window"].update(parent)
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", True, field="field")


@pytest.mark.parametrize("parent", [{"role": None}, {"input_type": None}])
def test_failed_ancestor_metadata_does_not_confirm_safe_input(parent):
    tree = editable_tree()
    tree.nodes["window"].update(parent)
    assert a11y.find_focus(tree, "root") == FocusSnapshot()


def test_password_input_type_survives_aria_role_combobox():
    tree = editable_tree(role=11, input_type="password")
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", True, field="field")


def embedded_tree(caret=0, anchor=None):
    tree = editable_tree(role=85, caret=caret,
                         interfaces=("org.a11y.atspi.Text", "org.a11y.atspi.Hypertext"),
                         links=["link"])
    tree.nodes["link"] = {"range": (0, 1), "object": "anchor"}
    tree.nodes["anchor"] = {"role": 40} if anchor is None else anchor
    return tree


@pytest.mark.parametrize("caret,start,end", [(0, 0, 1), (1, 1, 2)])
def test_embedded_password_at_unicode_caret_is_protected(caret, start, end):
    tree = embedded_tree(caret)
    tree.nodes["link"]["range"] = (start, end)
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", True, field="anchor")


def test_caret_after_embedded_password_is_normal_parent_text():
    assert a11y.find_focus(embedded_tree(caret=1), "root") == FocusSnapshot(
        "Editor", field="field")


def test_defunct_embedded_editor_cannot_confirm_safe_input():
    tree = embedded_tree(anchor={"role": 79, "state": (6, a11y.EDITABLE),
                                 "caret": 0, "interfaces": (a11y.TEXT,)})
    assert a11y.find_focus(tree, "root") == FocusSnapshot()


@pytest.mark.parametrize("broken", ["count", "range", "object", "cycle", "budget"])
def test_embedded_caret_with_incomplete_metadata_is_unknown(broken):
    tree = embedded_tree()
    if broken == "count":
        tree.nodes["field"]["link_count"] = None
    elif broken == "range":
        tree.nodes["link"]["range"] = None
    elif broken == "object":
        tree.nodes["link"]["object"] = None
    elif broken == "cycle":
        tree.nodes["link"]["object"] = "field"
    else:
        tree.nodes["field"]["link_count"] = 401
    assert a11y.find_focus(tree, "root") == FocusSnapshot()


def test_embedded_caret_follows_nested_editable_objects():
    tree = embedded_tree(anchor={"role": 85, "state": (7,), "caret": 0,
                                 "interfaces": ("org.a11y.atspi.Text",
                                                "org.a11y.atspi.Hypertext"),
                                 "links": ["nested-link"]})
    tree.nodes["nested-link"] = {"range": (0, 1), "object": "password"}
    tree.nodes["password"] = {"role": 11, "input_type": "password"}
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", True, field="password")


def test_embedded_objects_share_the_tree_depth_limit():
    tree = embedded_tree()
    tree.nodes["window"]["kids"] = ["panel1"]
    for depth in range(1, 31):
        tree.nodes[f"panel{depth}"] = {"kids": [f"panel{depth + 1}"]}
    tree.nodes["panel31"] = {"kids": ["field"]}
    assert a11y.find_focus(tree, "root") == FocusSnapshot()


def test_embedded_links_share_the_node_budget_with_tree_traversal():
    tree = embedded_tree()
    tree.nodes["field"]["link_count"] = 398
    assert a11y.find_focus(tree, "root") == FocusSnapshot()


def test_unknown_embedded_anchor_is_not_assumed_editable():
    tree = embedded_tree(anchor={"role": 79, "state": (7,), "caret": -1,
                                 "interfaces": ("org.a11y.atspi.Text",)})
    assert a11y.find_focus(tree, "root") == FocusSnapshot()


def test_boolean_link_count_does_not_confirm_safe_input():
    tree = embedded_tree()
    tree.nodes["field"]["link_count"] = False
    assert a11y.find_focus(tree, "root") == FocusSnapshot()


def test_no_active_window_never_reuses_stale_other_application_focus():
    tree = editable_tree()
    tree.nodes["window"]["state"] = ()
    assert a11y.find_focus(tree, "root") == FocusSnapshot()


def test_chromium_frame_is_prepared_before_reading_its_children():
    tree = editable_tree()
    tree.nodes["window"].update(kids=[], on_prepare={"kids": ["field"]})
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", field="field")


@pytest.mark.parametrize("webrole", [79, 40])
def test_native_browser_entry_takes_precedence_over_stale_web_focus(webrole):
    tree = editable_tree()
    tree.nodes["window"]["kids"] = ["web", "native-panel"]
    tree.nodes["web"] = {"role": 95, "kids": ["stale"]}
    tree.nodes["stale"] = {"role": webrole, "state": (12, 7), "caret": 0,
                            "interfaces": ("org.a11y.atspi.Text",)}
    tree.nodes["native-panel"] = {"kids": ["field"]}
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", field="field")


def test_duplicate_web_child_cannot_resume_before_native_password_search_finishes():
    tree = editable_tree()
    tree.nodes["window"]["kids"] = ["web", "web", "panel1"]
    tree.nodes["web"] = {"role": 95, "kids": ["field"]}
    tree.nodes["panel1"] = {"kids": ["panel2"]}
    tree.nodes["panel2"] = {"kids": ["native-password"]}
    tree.nodes["native-password"] = {"role": 40, "state": (12,)}
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", True,
                                                        field="native-password")


def test_web_field_is_still_found_when_browser_has_no_native_focus():
    tree = editable_tree(role=73)
    tree.nodes["window"]["kids"] = ["web"]
    tree.nodes["web"] = {"role": 95, "kids": ["field"]}
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", field="field")


def test_failed_native_branch_cannot_prove_stale_web_field_is_current():
    tree = editable_tree()
    tree.nodes["window"]["kids"] = ["web", "native-panel"]
    tree.nodes["web"] = {"role": 95, "kids": ["field"]}
    tree.nodes["native-panel"] = {"kids": None}
    assert a11y.find_focus(tree, "root") == FocusSnapshot()


@pytest.mark.parametrize("native", [
    {"role": 40, "state": None}, {"role": None, "state": ()},
    {"role": 79, "state": (12,), "interfaces": ("org.a11y.atspi.Text",), "caret": 0},
    {"role": 11, "state": (12, 7), "interfaces": ("org.a11y.atspi.Text",),
     "caret": 0, "input_type": None},
])
def test_unknown_or_unverified_native_focus_blocks_stale_web_field(native):
    tree = editable_tree()
    tree.nodes["window"]["kids"] = ["web", "native"]
    tree.nodes["web"] = {"role": 95, "kids": ["field"]}
    tree.nodes["native"] = native
    assert a11y.find_focus(tree, "root") == FocusSnapshot()


def test_focused_generic_web_host_does_not_block_verified_descendant_field():
    tree = editable_tree()
    tree.nodes["window"]["kids"] = ["host"]
    tree.nodes["host"] = {"role": 39, "state": (12,), "kids": ["web"]}
    tree.nodes["web"] = {"role": 95, "kids": ["field"]}
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", field="field")


def test_web_document_as_scope_root_is_bounded_and_reaches_its_field():
    tree = editable_tree()
    tree.nodes["window"]["role"] = 95
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", field="field")


def test_deferred_web_documents_share_the_first_inspection_budget():
    tree = editable_tree()
    documents = [f"document{i}" for i in range(a11y.MAX_NODES + 20)]
    tree.nodes["window"]["kids"] = documents
    tree.nodes.update((node, {"role": 95}) for node in documents)
    reads = []
    role = tree.role

    def counted_role(node):
        reads.append(node)
        return role(node)

    tree.role = counted_role
    assert a11y.find_focus(tree, "root") == FocusSnapshot()
    assert len(reads) <= a11y.MAX_NODES
    assert len(set(reads)) <= a11y.MAX_NODES


def test_resuming_a_web_document_does_not_spend_its_node_budget_twice(monkeypatch):
    monkeypatch.setattr(a11y, "MAX_NODES", 5)
    tree = editable_tree()
    tree.nodes["window"]["kids"] = ["web"]
    tree.nodes["web"] = {"role": 95, "kids": ["field"]}
    assert a11y.find_focus(tree, "root") == FocusSnapshot("Editor", field="field")


def test_find_focus_goes_straight_to_active_window():
    """Десятки «шумных» приложений не съедают бюджет обхода: ищем в активном окне."""
    from wayswitch import a11y

    nodes = {"root": {"kids": [f"noise{i}" for i in range(30)] + ["firefox"]}}
    for i in range(30):
        nodes[f"noise{i}"] = {"name": f"app{i}", "kids": [f"w{i}"]}
        nodes[f"w{i}"] = {"kids": [f"w{i}c{j}" for j in range(20)]}
        for j in range(20):
            nodes[f"w{i}c{j}"] = {}
    nodes["firefox"] = {"name": "Firefox", "kids": ["ffwin"]}
    chain = [f"ff{d}" for d in range(11)]
    nodes["ffwin"] = {"state": (a11y.ACTIVE,), "kids": [chain[0]]}
    for a, b in zip(chain, chain[1:], strict=False):
        nodes[a] = {"kids": [b]}
    nodes[chain[-1]] = {"state": (a11y.FOCUSED,), "role": 40}
    assert a11y.find_focus(DictTree(nodes), "root")[:2] == ("Firefox", True)


def test_find_focus_skips_shell():
    from wayswitch import a11y

    nodes = {"root": {"kids": ["shell", "term"]},
             "shell": {"name": "gnome-shell", "kids": ["sw"]},
             "sw": {"state": (a11y.ACTIVE, a11y.FOCUSED)},
             "term": {"name": "Ptyxis", "kids": ["tw"]},
             "tw": {"state": (a11y.ACTIVE,), "kids": ["entry"]},
             "entry": {"state": (a11y.FOCUSED,), "role": 60}}
    assert a11y.find_focus(DictTree(nodes), "root")[:2] == ("Ptyxis", True)


@pytest.mark.parametrize("role,protected", [(61, False), (40, True)])
def test_visible_editor_branch_is_reached_before_hidden_menu_budget(role, protected):
    menus = [f"menu{i}" for i in range(a11y.MAX_NODES)]
    nodes = {"root": {"kids": ["app"]},
             "app": {"name": "Editor", "kids": ["window"]},
             "window": {"state": (a11y.ACTIVE, 25), "kids": ["menus", "panel0"]},
             "menus": {"kids": menus}}
    nodes.update((menu, {}) for menu in menus)
    for depth in range(9):
        nodes[f"panel{depth}"] = {"state": (25,), "kids": [f"panel{depth + 1}"]}
    nodes["panel9"] = {"state": (a11y.FOCUSED, 7, 25), "role": role,
                       "interfaces": ("org.a11y.atspi.Text",), "caret": 0}

    snap = a11y.find_focus(DictTree(nodes), "root")

    assert snap == FocusSnapshot("Editor", protected, field="panel9")


def test_visible_branches_do_not_exclude_unmarked_password_field():
    nodes = {"root": {"kids": ["app"]},
             "app": {"name": "Editor", "kids": ["window"]},
             "window": {"state": (a11y.ACTIVE, 25), "kids": ["visible", "unmarked"]},
             "visible": {"state": (25,), "kids": ["label"]},
             "label": {"state": (25,), "role": 129},
             "unmarked": {"kids": ["password"]},
             "password": {"state": (a11y.FOCUSED,), "role": 40}}
    assert a11y.find_focus(DictTree(nodes), "root") == FocusSnapshot(
        "Editor", True, field="password")


@pytest.mark.parametrize("role,protected", [(61, False), (40, True)])
def test_visible_pane_does_not_hide_focused_sibling_with_its_children(role, protected):
    items = [f"item{i}" for i in range(a11y.MAX_NODES)]
    nodes = {"root": {"kids": ["app"]},
             "app": {"name": "Editor", "kids": ["window"]},
             "window": {"state": (a11y.ACTIVE, a11y.SHOWING), "kids": ["pane", "field"]},
             "pane": {"state": (a11y.SHOWING,), "kids": items},
             "field": {"state": (a11y.FOCUSED, 7, a11y.SHOWING), "role": role,
                       "interfaces": ("org.a11y.atspi.Text",), "caret": 0}}
    nodes.update((item, {}) for item in items)
    assert a11y.find_focus(DictTree(nodes), "root") == FocusSnapshot(
        "Editor", protected, field="field")


@pytest.mark.parametrize("role,protected", [(40, True), (60, True), (79, False)])
def test_numeric_role_identifies_protected_input(role, protected):
    nodes = {"root": {"kids": ["app"]},
             "app": {"name": "Test App", "kids": ["window"]},
             "window": {"state": (a11y.ACTIVE,), "kids": ["entry"]},
             "entry": {"state": (a11y.FOCUSED, 7), "role": role,
                       "interfaces": ("org.a11y.atspi.Text",), "caret": 0}}
    assert a11y.find_focus(DictTree(nodes), "root")[:2] == ("Test App", protected)


@pytest.mark.parametrize("role,protected", [(40, True), (79, False)])
def test_browser_focused_containers_do_not_hide_deep_text_field(role, protected):
    nodes = {"root": {"kids": ["app"]},
             "app": {"name": "Browser", "kids": ["window"]},
             "window": {"state": (a11y.ACTIVE,), "kids": ["node1"]}}
    for depth in range(1, 26):
        nodes[f"node{depth}"] = {"kids": [f"node{depth + 1}"] if depth < 25 else []}
    nodes["node19"].update(state=(a11y.FOCUSED,), role=39)
    nodes["node23"].update(state=(a11y.FOCUSED,), role=95)
    nodes["node25"].update(state=(a11y.FOCUSED, 7), role=role,
                           interfaces=("org.a11y.atspi.Text",), caret=0)
    assert a11y.find_focus(DictTree(nodes), "root")[:2] == ("Browser", protected)


def test_text_field_beyond_depth_limit_remains_unverified():
    nodes = {"root": {"kids": ["app"]},
             "app": {"name": "Browser", "kids": ["window"]},
             "window": {"state": (a11y.ACTIVE,), "kids": ["node1"]}}
    for depth in range(1, 34):
        nodes[f"node{depth}"] = {"kids": [f"node{depth + 1}"] if depth < 33 else []}
    nodes["node33"].update(state=(a11y.FOCUSED,), role=79)
    assert a11y.find_focus(DictTree(nodes), "root")[:2] == (None, False)


@pytest.mark.parametrize("role", [43, 67])
def test_focused_nontext_or_unknown_role_does_not_confirm_safe_input(role):
    nodes = {"root": {"kids": ["app"]},
             "app": {"name": "Browser", "kids": ["window"]},
             "window": {"state": (a11y.ACTIVE,), "kids": ["button"]},
             "button": {"state": (a11y.FOCUSED,), "role": role}}
    assert a11y.find_focus(DictTree(nodes), "root")[:2] == (None, False)


def test_terminal_blocks_auto_but_manual_fix_is_private_and_not_learned():
    nodes = {"root": {"kids": ["app"]},
             "app": {"name": "Terminal", "kids": ["window"]},
             "window": {"state": (a11y.ACTIVE,), "kids": ["entry"]},
             "entry": {"state": (a11y.FOCUSED,), "role": 60}}
    probe = a11y.AtspiProbe(clock=Clock(), query=lambda: a11y.find_focus(DictTree(nodes), "root"))
    probe._refresh()
    corrected = []
    env = make_env(probe=probe, apps=[])
    env["backend"].current_index = US
    env["ctrl"].on_corrected = lambda *args: corrected.append(args)
    try:
        type_keys(env, keys_for_text(env["km"], "ghbdtn ", US))
        assert env["typist"].taps() == []
        assert env["ctrl"].manual_word()
        assert env["typist"].taps()
        assert env["ctrl"].manual_word()
        assert corrected == []
        assert env["ctrl"]._undo_counts == {}
    finally:
        probe.close(wait=True)


def test_dbus_app_of_unpacks_accessible_reference():
    tree = object.__new__(a11y._DbusTree)

    def call(node, iface, method, arg, sig):
        assert method == "GetApplication"
        assert sig == "((so))"
        return ((":1.42", "/org/a11y/atspi/accessible/root"),)

    tree._call = call

    def app_name(node):
        assert node == (":1.42", "/org/a11y/atspi/accessible/root")
        return "IntelliJ IDEA"

    tree.app_name = app_name
    assert tree.app_of((":1.42", "/field")) == "IntelliJ IDEA"


def test_dbus_password_uses_numeric_role_when_name_is_generic():
    tree = object.__new__(a11y._DbusTree)
    calls = []

    def call(node, iface, method, arg, sig):
        calls.append((method, sig))
        return (40,) if method == "GetRole" else ("text box",)

    tree._call = call
    assert tree.role((":1.42", "/password")) == 40
    assert calls == [("GetRole", "(u)")]


@pytest.mark.parametrize("entry", [{}, {"state": (a11y.FOCUSED,), "role": None},
                                  {"state": (a11y.FOCUSED,), "role": 0}])
def test_active_app_without_verified_field_does_not_imply_safe_input(entry):
    nodes = {"root": {"kids": ["app"]},
             "app": {"name": "Browser", "kids": ["window"]},
             "window": {"state": (a11y.ACTIVE,), "kids": ["entry"]},
             "entry": entry}
    assert a11y.find_focus(DictTree(nodes), "root")[:2] == (None, False)


def test_idle_stop_leaves_stale_snapshot():
    """Поток уснул — снимок «старый», а не свежее «ограничений нет»."""
    from wayswitch import a11y

    assert a11y.FocusSnapshot().age(1000.0) > a11y.MAX_AGE_SEC


def test_manual_fix_with_stale_focus_is_not_reported():
    # Снимок фокуса устарел (поток опроса только проснулся): не знаем, не поле
    # ли это пароля, — текст исправления наружу не отдаём.
    clock = Clock()
    corrected = []
    env = make_env(probe=FakeProbe(clock, app="firefox", age=a11y.MAX_AGE_SEC + 5))
    env["ctrl"].on_corrected = lambda o, f, m: corrected.append((o, f, m))
    env["ctrl"].clock = env["clock"] = clock
    env["backend"].current_index = US
    type_keys(env, keys_for_text(env["km"], "ghbdtn", US))
    assert env["ctrl"].manual_word()
    assert corrected == []
    assert env["ctrl"].probe.pokes > 0  # клавиши будят опрос
