"""Исключения по приложению и поля паролей: снимок фокуса из at-spi.

Проверяем и чистую логику решения, и то, что контроллер действительно молчит
в исключённой программе и в поле пароля.
"""

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
    monkeypatch.setattr(a11y, "query_shell_app_ids", lambda: ("jetbrains-idea",))
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: (_ for _ in ()).throw(RuntimeError()))
    snap = a11y.query_focus()
    assert snap.app is None
    assert snap.app_ids == ("jetbrains-idea",)


@pytest.mark.parametrize("role,password", [(61, False), (40, True)])
def test_old_shell_extension_preserves_atspi_focus(monkeypatch, role, password):
    calls = []

    def call_sync(_bus, _path, _iface, method, *_args):
        calls.append(method)
        raise RuntimeError("org.freedesktop.DBus.Error.UnknownMethod: GetFocusedApp")

    repository = SimpleNamespace(
        Gio=SimpleNamespace(BusType=SimpleNamespace(SESSION=0),
                            DBusCallFlags=SimpleNamespace(NONE=0),
                            bus_get_sync=lambda *_: SimpleNamespace(call_sync=call_sync)),
        GLib=SimpleNamespace(VariantType=lambda signature: signature))
    monkeypatch.setitem(sys.modules, "gi", SimpleNamespace(repository=repository))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    tree = DictTree({
        a11y.ROOT: {"kids": ["editor"]},
        "editor": {"name": "Editor", "kids": ["window"]},
        "window": {"state": [a11y.ACTIVE], "kids": ["field"]},
        "field": {"state": [a11y.FOCUSED], "role": role},
    })
    monkeypatch.setattr(a11y, "_a11y_connection", lambda: object())
    monkeypatch.setattr(a11y, "_DbusTree", lambda _connection: tree)

    snap = a11y.query_focus()

    assert calls == ["GetFocusedApp"]
    assert snap == FocusSnapshot("Editor", password, field="field")
    probe = SimpleNamespace(snapshot=lambda: snap._replace(taken_at=100.0))
    assert a11y.skip_reason(probe, [], True, 100.0) == (
        "защищённый ввод" if password else None)
    assert a11y.skip_reason(probe, ["editor"], False, 100.0) == "приложение Editor"


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
    connections = []
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

    repository = SimpleNamespace(
        Gio=SimpleNamespace(DBusSignalFlags=SimpleNamespace(NONE=0),
                            DBusCallFlags=SimpleNamespace(NONE=0)),
        GLib=SimpleNamespace(Variant=lambda signature, values: (signature, values),
                             VariantType=lambda signature: signature, Error=RuntimeError))
    monkeypatch.setitem(sys.modules, "gi", SimpleNamespace(repository=repository))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    monkeypatch.setattr(a11y, "_new_a11y_connection", Connection, raising=False)
    return connections, registered


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

    def children(self, node):
        return self.nodes[node].get("kids", [])

    def state(self, node):
        bits = 0
        for bit in self.nodes[node].get("state", ()):
            bits |= 1 << bit
        return [bits]

    def role(self, node):
        return self.nodes[node].get("role")

    def app_name(self, node):
        return self.nodes[node].get("name", "")

    def app_of(self, node):
        return self.nodes[node].get("app")


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
    nodes["panel9"] = {"state": (a11y.FOCUSED, 25), "role": role}

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
             "field": {"state": (a11y.FOCUSED, a11y.SHOWING), "role": role}}
    nodes.update((item, {}) for item in items)
    assert a11y.find_focus(DictTree(nodes), "root") == FocusSnapshot(
        "Editor", protected, field="field")


@pytest.mark.parametrize("role,protected", [(40, True), (60, True), (79, False)])
def test_numeric_role_identifies_protected_input(role, protected):
    nodes = {"root": {"kids": ["app"]},
             "app": {"name": "Test App", "kids": ["window"]},
             "window": {"state": (a11y.ACTIVE,), "kids": ["entry"]},
             "entry": {"state": (a11y.FOCUSED,), "role": role}}
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
    nodes["node25"].update(state=(a11y.FOCUSED,), role=role)
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
