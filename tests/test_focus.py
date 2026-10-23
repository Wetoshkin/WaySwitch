"""Исключения по приложению и поля паролей: снимок фокуса из at-spi.

Проверяем и чистую логику решения, и то, что контроллер действительно молчит
в исключённой программе и в поле пароля.
"""

import time

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
    assert a11y.skip_reason(probe, [], True, clock()) == "поле пароля"


def test_skip_reason_password_can_be_disabled():
    clock = Clock()
    probe = FakeProbe(clock, app="firefox", password=True)
    assert a11y.skip_reason(probe, [], False, clock()) is None


def test_skip_reason_excluded_app():
    clock = Clock()
    probe = FakeProbe(clock, app="TelegramDesktop")
    assert a11y.skip_reason(probe, ["telegram"], True, clock()) == "приложение TelegramDesktop"


def test_skip_reason_stale_snapshot_does_not_block():
    """Данные протухли — лучше исправить, чем замереть навсегда."""
    clock = Clock()
    probe = FakeProbe(clock, app="TelegramDesktop", age=a11y.MAX_AGE_SEC + 1)
    assert a11y.skip_reason(probe, ["telegram"], True, clock()) is None


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


def test_controller_sets_terminators_from_config():
    config = Config()
    config.general.fix_on_tab = True
    env = make_env(config=config)
    assert kc.KEY_TAB in env["ctrl"].buffer._terminators
    config2 = Config()
    config2.general.fix_on_tab = False
    config2.general.fix_on_enter = True
    env["ctrl"].reload_config(config2)
    assert kc.KEY_TAB not in env["ctrl"].buffer._terminators
    assert kc.KEY_ENTER in env["ctrl"].buffer._terminators
