"""Жизненный цикл асинхронного GUI: завершение приложения и онбординг."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest


class AppDefined(Exception):
    pass


def capture_app(monkeypatch, argv=None, package_kind="flatpak"):
    """Определить вложенный App из app.main, не запуская GTK."""
    from wayswitch.gui import app

    classes = []

    class SimpleAction:
        def __init__(self, name, _parameter_type):
            self.name = name

        @staticmethod
        def new(name, parameter_type):
            return SimpleAction(name, parameter_type)

        def connect(self, _signal, _callback):
            pass

    class Application:
        def __init_subclass__(cls):
            classes.append(cls)
            raise AppDefined

        def __init__(self, **_kwargs):
            self.holds = 0
            self.actions = {}
            self.accels = {}

        def add_action(self, action):
            self.actions[action.name] = action

        def set_accels_for_action(self, action, accels):
            self.accels[action] = accels

        def hold(self):
            self.holds = getattr(self, 'holds', 0) + 1

        def release(self):
            self.holds -= 1

        def do_startup(self):
            self.base_started = True

        def do_shutdown(self):
            self.base_shutdown = True

        def quit(self):
            self.quit_called = True

    gi = types.ModuleType("gi")
    gi.require_version = lambda *args: None
    repository = types.ModuleType("gi.repository")
    repository.Adw = SimpleNamespace(Application=Application)
    repository.Gio = SimpleNamespace(
        SimpleAction=SimpleAction,
        ApplicationFlags=SimpleNamespace(FLAGS_NONE=0))
    repository.Gdk = SimpleNamespace(Display=SimpleNamespace(get_default=lambda: None))
    repository.Gtk = SimpleNamespace()
    repository.GLib = SimpleNamespace(SOURCE_REMOVE=False, timeouts=[])

    def timeout_add(ms, callback):
        repository.GLib.timeouts.append((ms, callback))
        return 1

    repository.GLib.timeout_add = timeout_add
    repository.GLib.timeout_add_seconds = lambda *_args: 1
    Application.glib = repository.GLib
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    monkeypatch.setattr(app.portable, "package_kind",
                        lambda kind=package_kind: kind)
    monkeypatch.setattr(app.autostart, "migrate_tray", lambda: False)
    with pytest.raises(AppDefined):
        app.main([] if argv is None else argv)
    return classes[0]


class FakeWindow:
    def __init__(self, request_close):
        self.request_close = request_close
        self.visible = False
        self.present_count = 0
        self.close_results = []

    def set_visible(self, visible):
        self.visible = visible

    def present(self):
        self.visible = True
        self.present_count += 1

    def close(self):
        result = self.request_close()
        self.close_results.append(result)
        return result


class FakeMain:
    def __init__(self, request_close):
        self.window = FakeWindow(request_close)

    def present(self):
        self.window.present()


def make_app(app_class, monkeypatch):
    """Создать App с заглушками GI и окна; окно создаётся при показе."""
    from wayswitch.gui import onboarding

    instance = app_class()
    starts = []
    closes = []
    instance.proxy = SimpleNamespace(
        start=lambda: starts.append("proxy"),
        close=lambda: closes.append("proxy"),
        call=lambda *_args, **_kwargs: True,
        status=None,
    )
    instance.tray = SimpleNamespace(
        start=lambda: starts.append("tray"),
        update=lambda *_args: None,
    )

    def ensure_main():
        if instance._main is None:
            instance._main = FakeMain(lambda: app_class._on_close(instance))
        return instance._main

    instance._ensure_main = ensure_main
    instance.starts = starts
    instance.closes = closes
    monkeypatch.setattr(onboarding, "needed", lambda: False)
    return instance

def test_package_update_check_uses_source_worker_and_publishes_candidate(monkeypatch):
    from wayswitch import updater, updates
    from wayswitch.gui import app, widgets

    app_class = capture_app(monkeypatch)
    instance = object.__new__(app_class)
    instance._main = None
    instance.proxy = SimpleNamespace(status={})
    rendered = []
    instance.tray = SimpleNamespace(update=lambda status, version: rendered.append(version))
    instance._notify_update = lambda result: None
    monkeypatch.setattr(app.cfgmod, "load", app.cfgmod.Config)
    info = updater.UpdateInfo(updater.Source("system", "apt", "wayswitch", "1.1.4-1"),
                               updates.Release("1.1.4-2", ""), "available")
    monkeypatch.setattr(updater, "check", lambda *a, **kw: info)
    monkeypatch.setattr(updates, "should_notify", lambda *a: False)
    pending = []
    monkeypatch.setattr(widgets, "run_in_thread", lambda work, done: pending.append((work, done)))
    results = []
    instance.check_updates(force=True, done=results.append)
    assert not results and not rendered
    work, done = pending.pop()
    done(work())
    assert results == [info] and rendered == ["1.1.4-2"]
    assert instance.update_info is info and instance.update.version == "1.1.4-2"


def test_gui_version_query_needs_no_gtk(monkeypatch, capsys):
    from wayswitch import __version__
    from wayswitch.gui import app

    monkeypatch.setitem(sys.modules, "gi", None)
    assert app.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_late_update_result_does_not_touch_closed_gui(monkeypatch):
    from wayswitch import updater
    from wayswitch.gui import app, widgets

    app_class = capture_app(monkeypatch)
    instance = object.__new__(app_class)
    monkeypatch.setattr(app.cfgmod, "load", app.cfgmod.Config)
    pending = []
    monkeypatch.setattr(widgets, "run_in_thread", lambda work, done: pending.append(done))
    instance.check_updates(force=True, done=lambda _: pytest.fail("closed dialog updated"))
    instance._update_closed = True
    pending.pop()(updater.UpdateInfo(updater.Source("system", "apt", "wayswitch")))
    assert not instance._checking_updates


def test_unexpected_check_failure_does_not_keep_old_success_status(monkeypatch):
    from wayswitch import updater
    from wayswitch.gui import app, widgets

    app_class = capture_app(monkeypatch)
    instance = object.__new__(app_class)
    instance.update_info = updater.UpdateInfo(updater.Source("system", "apt", "wayswitch"),
                                               state="current")
    instance.update = None
    instance._main = None
    instance.proxy = SimpleNamespace(status={})
    instance.tray = SimpleNamespace(update=lambda *args: None)
    monkeypatch.setattr(app.cfgmod, "load", app.cfgmod.Config)
    monkeypatch.setattr(widgets, "run_in_thread", lambda work, done: done(OSError("failure")))
    instance.check_updates(force=True)
    assert instance.update_info.state == "error" and not instance._update_checked


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


def test_native_default_holds_application_and_closes_to_tray(monkeypatch):
    app_class = capture_app(monkeypatch, package_kind=None)
    instance = make_app(app_class, monkeypatch)
    history = instance.history
    proxy = instance.proxy

    app_class.do_startup(instance)
    assert instance.holds == 1
    assert instance.starts == ["proxy", "tray"]

    app_class.do_activate(instance)
    main = instance._main
    assert main.window.visible is True

    assert app_class._on_close(instance) is True
    assert instance._main is main
    assert main.window.visible is False
    assert instance.history is history
    assert instance.proxy is proxy
    assert instance.closes == []
    assert getattr(instance, "base_shutdown", False) is False

    app_class.do_activate(instance)
    assert instance._main is main
    assert main.window.visible is True


def test_native_tray_activation_restores_retained_window(monkeypatch):
    app_class = capture_app(monkeypatch, argv=["--tray"], package_kind=None)
    instance = make_app(app_class, monkeypatch)

    app_class.do_startup(instance)
    assert instance.holds == 1
    app_class.do_activate(instance)
    assert instance._main is None  # первая активация --tray окно не открывает

    app_class.do_activate(instance)
    main = instance._main
    assert main.window.visible is True
    assert app_class._on_close(instance) is True
    assert instance._main is main
    assert main.window.visible is False

    app_class.do_activate(instance)
    assert instance._main is main
    assert main.window.visible is True


@pytest.mark.parametrize("kind", ["appimage", "flatpak"])
def test_portable_default_window_close_keeps_only_window_lifetime(monkeypatch, kind):
    app_class = capture_app(monkeypatch, package_kind=kind)
    instance = make_app(app_class, monkeypatch)

    app_class.do_startup(instance)
    assert instance.holds == 0
    app_class.do_activate(instance)
    assert instance._main.window.visible is True

    assert app_class._on_close(instance) is False
    assert instance._main is None
    assert getattr(instance, "base_shutdown", False) is False


@pytest.mark.parametrize("kind", ["appimage", "flatpak"])
def test_portable_tray_keeps_window_after_close(monkeypatch, kind):
    app_class = capture_app(monkeypatch, argv=["--tray"], package_kind=kind)
    instance = make_app(app_class, monkeypatch)

    app_class.do_startup(instance)
    assert instance.holds == 1
    app_class.do_activate(instance)
    assert instance._main is None
    app_class.do_activate(instance)
    main = instance._main

    assert app_class._on_close(instance) is True
    assert instance._main is main
    assert main.window.visible is False

    app_class.do_activate(instance)
    assert instance._main is main
    assert main.window.visible is True


def test_native_background_close_hides_window_without_shutdown(monkeypatch):
    app_class = capture_app(monkeypatch, package_kind=None)
    instance = make_app(app_class, monkeypatch)
    app_class.do_startup(instance)
    app_class.do_activate(instance)
    main = instance._main

    instance._quit_background()

    assert main.window.close_results == [True]
    assert instance._main is main
    assert main.window.visible is False
    assert instance.closes == []
    assert getattr(instance, "base_shutdown", False) is False


@pytest.mark.parametrize("kind", ["appimage", "flatpak"])
def test_portable_background_close_ends_window(monkeypatch, kind):
    app_class = capture_app(monkeypatch, package_kind=kind)
    instance = make_app(app_class, monkeypatch)
    app_class.do_startup(instance)
    app_class.do_activate(instance)
    main = instance._main

    instance._quit_background()

    assert main.window.close_results == [False]
    assert instance._main is None


def test_native_explicit_stop_stops_service_and_quits(monkeypatch):
    from wayswitch.gui import autostart

    app_class = capture_app(monkeypatch, package_kind=None)
    instance = make_app(app_class, monkeypatch)
    stopped = []
    quits = []
    monkeypatch.setattr(autostart, "stop_service", lambda: stopped.append(True))
    instance._stopping = False
    instance.quit = lambda: quits.append(True)

    app_class._quit_stop(instance)

    assert stopped == [True]
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
