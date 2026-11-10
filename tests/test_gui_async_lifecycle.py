"""Жизненный цикл асинхронного GUI: завершение приложения и онбординг."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest


class AppDefined(Exception):
    pass


def capture_app(monkeypatch):
    """Определить вложенный App из app.main, не запуская GTK."""
    from wayswitch.gui import app

    classes = []

    class Application:
        def __init_subclass__(cls):
            classes.append(cls)
            raise AppDefined

        def hold(self):
            self.holds = getattr(self, 'holds', 0) + 1

        def release(self):
            self.holds -= 1

        def do_startup(self):
            pass

        def do_shutdown(self):
            self.base_shutdown = True

    gi = types.ModuleType("gi")
    gi.require_version = lambda *args: None
    repository = types.ModuleType("gi.repository")
    repository.Adw = SimpleNamespace(Application=Application)
    repository.Gio = SimpleNamespace()
    repository.GLib = SimpleNamespace(SOURCE_REMOVE=False, timeouts=[])

    def timeout_add(ms, callback):
        repository.GLib.timeouts.append((ms, callback))
        return 1

    repository.GLib.timeout_add = timeout_add
    Application.glib = repository.GLib
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    monkeypatch.setattr(app.portable, "package_kind", lambda: "flatpak")
    with pytest.raises(AppDefined):
        app.main([])
    return classes[0]


def test_app_shutdown_closes_proxy(monkeypatch):
    app_class = capture_app(monkeypatch)
    instance = object.__new__(app_class)
    closed = []
    instance.proxy = SimpleNamespace(close=lambda: closed.append(True))

    app_class.do_shutdown(instance)

    assert closed == [True]
    assert instance.base_shutdown is True


def test_quit_stop_waits_for_pause_completion(monkeypatch):
    from wayswitch.gui.daemon_proxy import CALL_TIMEOUT_MS

    app_class = capture_app(monkeypatch)
    instance = object.__new__(app_class)
    instance._stopping = False
    pending = []

    def call(method, on_done=None):
        pending.append((method, on_done))
        return True

    instance.proxy = SimpleNamespace(call=call)
    quits = []
    instance.quit = lambda: quits.append(True)

    instance._quit_stop()

    assert instance._stopping is True
    assert [method for method, _ in pending] == ["Pause"]
    assert quits == []
    assert instance.holds == 1  # остаёмся живы, даже если окно закроют
    instance._quit_stop()  # повторное нажатие не дублирует Pause и hold
    assert len(pending) == 1
    assert app_class.glib.timeouts[0][0] == CALL_TIMEOUT_MS + 500

    pending[0][1](None)
    assert instance.holds == 0
    assert quits == [True]
    app_class.glib.timeouts[0][1]()  # страховка после завершения уже не срабатывает
    assert quits == [True]


def test_quit_stop_without_service_quits_immediately(monkeypatch):
    app_class = capture_app(monkeypatch)
    instance = object.__new__(app_class)
    instance._stopping = False
    instance.proxy = SimpleNamespace(call=lambda *_args, **_kwargs: False)
    quits = []
    instance.quit = lambda: quits.append(True)

    instance._quit_stop()

    assert quits == [True]


def test_quit_stop_transport_error_quits_once(monkeypatch):
    app_class = capture_app(monkeypatch)
    instance = object.__new__(app_class)
    instance._stopping = False
    quits = []

    def call(_method, on_done=None):
        on_done(None)  # синхронная ошибка отправки
        return False

    instance.proxy = SimpleNamespace(call=call)
    instance.quit = lambda: quits.append(True)

    instance._quit_stop()

    assert quits == [True]


class Label:
    def __init__(self):
        self.text = ""

    def set_label(self, text):
        self.text = text


class Button:
    def __init__(self, **_kwargs):
        self.sensitive = None
        self.connected = []

    def connect(self, name, callback):
        self.connected.append((name, callback))

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive


class Box:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)


class ActionRow:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.suffixes = []

    def add_suffix(self, widget):
        self.suffixes.append(widget)


def install_onboarding_widgets(monkeypatch):
    repository = types.ModuleType("gi.repository")
    repository.Adw = SimpleNamespace(ActionRow=ActionRow)
    repository.Gtk = SimpleNamespace(Button=Button,
                                     Align=SimpleNamespace(CENTER=0))
    monkeypatch.setitem(sys.modules, "gi", types.ModuleType("gi"))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)


def make_onboarding(monkeypatch, generation=1):
    from wayswitch.gui import onboarding

    install_onboarding_widgets(monkeypatch)
    instance = object.__new__(onboarding.Onboarding)
    instance._finished = False
    instance._checks_generation = generation
    instance.checks_hint = Label()
    instance.continue_button = Button()
    instance.checks_box = Box()
    instance.window = object()
    instance.app = SimpleNamespace(proxy=SimpleNamespace(refresh=lambda _cb: None))
    monkeypatch.setattr(onboarding, "clear_list", lambda box: box.rows.clear())
    return instance


def test_onboarding_stale_check_result_does_not_mutate_widgets(monkeypatch):
    instance = make_onboarding(monkeypatch, generation=2)
    instance.checks_hint.set_label("current")
    instance.continue_button.sensitive = False

    instance._render_portable_checks({"backend": "shell"}, 1)
    assert instance.checks_box.rows == []
    assert instance.checks_hint.text == "current"
    assert instance.continue_button.sensitive is False

    instance._render_portable_checks({"backend": "shell"}, 2)
    assert len(instance.checks_box.rows) == 1
    assert instance.checks_hint.text != "current"
    assert instance.continue_button.sensitive is True


def test_onboarding_closed_result_does_not_mutate_widgets(monkeypatch):
    instance = make_onboarding(monkeypatch, generation=2)
    instance._finished = True
    instance.checks_hint.set_label("closed")
    instance.continue_button.sensitive = False

    instance._render_portable_checks({"backend": "shell"}, 2)

    assert instance.checks_box.rows == []
    assert instance.checks_hint.text == "closed"
    assert instance.continue_button.sensitive is False


def test_onboarding_repeated_check_supersedes_pending_callback(monkeypatch):
    instance = make_onboarding(monkeypatch, generation=1)
    refreshes = []
    instance.app.proxy.refresh = refreshes.append

    instance._show_portable_checks()
    first = refreshes[0]
    instance._show_portable_checks()
    assert instance._checks_generation == 3
    assert len(refreshes) == 2

    first({"backend": "shell"})
    assert instance.checks_box.rows == []  # старый запрос уже неактуален

    refreshes[1]({"backend": "shell"})
    assert len(instance.checks_box.rows) == 1


def test_onboarding_navigation_away_invalidates_pending_check(monkeypatch):
    instance = make_onboarding(monkeypatch, generation=5)

    class Carousel:
        def get_n_pages(self):
            return 3

        def get_nth_page(self, index):
            return index

        def scroll_to(self, _page, _animate):
            pass

    instance.carousel = Carousel()
    instance._go(0)
    assert instance._checks_generation == 6


def test_quit_stop_owner_loss_uses_bounded_fallback(monkeypatch):
    app_class = capture_app(monkeypatch)
    instance = object.__new__(app_class)
    instance._stopping = False
    instance.proxy = SimpleNamespace(call=lambda *_args: True)
    quits = []
    instance.quit = lambda: quits.append(True)
    instance._quit_stop()
    assert instance.holds == 1
    assert quits == []
    assert app_class.glib.timeouts[0][1]() is False
    assert instance.holds == 0
    assert quits == [True]
