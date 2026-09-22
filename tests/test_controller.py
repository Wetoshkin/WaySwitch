import pytest

from tests.fakes import RU, US, FakeBackend, RecordingTypist, make_keymap
from wayswitch import keycodes as kc
from wayswitch.config import Config
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel

RU_WORDS = [("я", 1000.0), ("не", 900.0), ("это", 800.0), ("привет", 500.0), ("мир", 400.0),
            ("буква", 100.0), ("книга", 120.0), ("пока", 200.0), ("спасибо", 250.0)]
EN_WORDS = [("the", 1000.0), ("hello", 500.0), ("world", 400.0), ("book", 300.0),
            ("this", 350.0), ("a", 900.0), ("i", 950.0)]


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def env():
    km = make_keymap()
    det = Detector(km, {
        "ru": LanguageModel.from_words("ru", RU_WORDS, "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"),
        "en": LanguageModel.from_words("en", EN_WORDS, "abcdefghijklmnopqrstuvwxyz"),
    })
    backend, typist, clock = FakeBackend(current=US), RecordingTypist(), Clock()
    corrected, exceptions = [], []
    ctrl = Controller(Config(), km, det, backend, typist, clock=clock,
                      on_corrected=lambda o, f, m: corrected.append((o, f, m)),
                      persist_exception=exceptions.append)
    return dict(km=km, ctrl=ctrl, backend=backend, typist=typist, clock=clock,
                corrected=corrected, exceptions=exceptions)


def type_text(env, text, group=None, dev=1):
    """Печатает текст физическими нажатиями в текущей раскладке бэкенда."""
    group = env["backend"].current() if group is None else group
    for ch in text:
        code, shift = env["km"].key_for(ch, group)
        if shift:
            env["ctrl"].on_key(kc.KEY_LEFTSHIFT, 1, dev)
        env["ctrl"].on_key(code, 1, dev)
        env["ctrl"].on_key(code, 0, dev)
        if shift:
            env["ctrl"].on_key(kc.KEY_LEFTSHIFT, 0, dev)
        env["clock"].now += 0.05


def double_shift(env, times=2):
    for _ in range(times):
        env["ctrl"].on_key(kc.KEY_RIGHTSHIFT, 1, 1)
        env["ctrl"].on_key(kc.KEY_RIGHTSHIFT, 0, 1)
        env["clock"].now += 0.1


def typed_text(env):
    """Что реально напечатал виртуальный демон (после Backspace-ов), в целевой раскладке.

    Декодирует ВСЕ нажатия в ФИНАЛЬНОЙ (на момент вызова) раскладке бэкенда —
    если по ходу теста было больше одного исправления с переключением
    раскладки туда-обратно, ранние нажатия декодируются неверно. Сравнение
    результата с полной строкой имеет смысл только для сценариев с одним
    исправлением; для остальных используйте endswith()/частичные проверки.
    """
    taps = env["typist"].taps()
    out = []
    for code, shift in taps:
        if code == kc.KEY_BACKSPACE:
            out.append("\b")
        else:
            out.append(env["km"].char(code, env["backend"].current(), shift))
    return "".join(out)


def test_auto_fix_on_space(env):
    type_text(env, "ghbdtn ")
    assert env["backend"].set_calls == [RU]
    assert typed_text(env) == "\b" * 7 + "привет "
    assert env["corrected"] == [("ghbdtn", "привет", False)]
    word, tail = env["ctrl"].buffer.last_word_with_tail()
    assert env["km"].decode(word, RU) == "привет" and tail == 1
    assert env["ctrl"].status()["corrections_total"] == 1


def test_dictionary_word_kept(env):
    type_text(env, "hello ")
    assert env["typist"].events == [] and env["backend"].set_calls == []


def test_manual_double_shift_without_space(env):
    type_text(env, "ghbdtn")
    double_shift(env)
    assert typed_text(env) == "\b" * 6 + "привет"
    assert env["corrected"][-1][2] is True


def test_manual_double_shift_with_empty_buffer_just_switches(env):
    double_shift(env)
    assert env["backend"].set_calls == [RU] and env["typist"].taps() == []


def test_triple_shift_fixes_phrase(env):
    env["ctrl"].config.general.auto_correct = False  # вся фраза набрана в одной неверной раскладке
    type_text(env, "ghbdtn vbh")
    double_shift(env, times=3)
    # второй Shift исправил «мир» и переключил раскладку, третий — переписал
    # всю фразу в уже переключённой раскладке; в итоге напечатано «привет мир»
    assert typed_text(env).endswith("привет мир")
    assert env["km"].decode(env["ctrl"].buffer.phrase(), RU) == "привет мир"
    assert env["backend"].current() == RU


def test_triple_shift_after_auto_fix_keeps_fixed_word(env):
    # «ghbdtn » авто-исправляется на «привет », раскладка переключается на RU.
    type_text(env, "ghbdtn ")
    # «vbh» набирается физическими клавишами US-букв, пока раскладка уже RU —
    # на экране это «мир», без необходимости исправления.
    type_text(env, "vbh", group=US)
    # Три Shift подряд (в пределах окна жеста): второй переворачивает последнее
    # слово («мир» → «vbh», раскладка переключается на US), третий переписывает
    # всю фразу в уже переключённой (US) группе: «привет» → «ghbdtn», «vbh» как есть.
    double_shift(env, times=3)
    assert typed_text(env).endswith("ghbdtn vbh")
    assert env["backend"].current() == US


def test_undo_by_gesture_adds_exception(env):
    type_text(env, "ghbdtn ")
    env["typist"].events.clear()
    double_shift(env)
    assert typed_text(env) == "\b" * 7 + "ghbdtn "
    assert env["exceptions"] == ["ghbdtn"]
    assert env["ctrl"].status()["undo_total"] == 1
    assert env["backend"].current() == US


def test_gesture_after_undo_window_is_plain_manual(env):
    type_text(env, "ghbdtn ")
    env["clock"].now += 10
    double_shift(env)
    assert env["exceptions"] == []


def test_early_url_trigger(env):
    env["backend"].current_index = RU
    type_text(env, "реезыЖ..")
    assert typed_text(env) == "\b" * 8 + "https://"
    assert env["backend"].current() == US


def test_abort_on_physical_key_during_fix(env):
    class Interrupting(RecordingTypist):
        def key(self, code, value):
            super().key(code, value)
            if len(self.events) == 6:
                env["ctrl"].on_key(kc.KEY_A, 1, 1)  # пользователь нажал клавишу
    env["typist"] = Interrupting()
    env["ctrl"].typist = env["typist"]
    type_text(env, "ghbdtn ")
    assert env["typist"].stuck() == set()
    assert env["ctrl"].buffer.is_empty()
    assert env["corrected"] == []


def test_click_and_external_layout_change_reset(env):
    type_text(env, "ghbdtn")
    env["ctrl"].on_click()
    assert env["ctrl"].buffer.is_empty()
    type_text(env, "ghbdtn")
    env["backend"].emit_external_change(RU)
    env["ctrl"].on_layout_changed(RU, True)
    assert env["ctrl"].buffer.is_empty()


def test_pause_and_lock_disable_auto(env):
    env["ctrl"].pause()
    type_text(env, "ghbdtn ")
    assert env["typist"].events == []
    env["ctrl"].resume()
    env["ctrl"].set_locked(True)
    type_text(env, "ghbdtn ")
    assert env["typist"].events == []


def test_dry_run_does_not_touch_anything(env):
    env["ctrl"].dry_run = True
    type_text(env, "ghbdtn ")
    assert env["typist"].events == [] and env["backend"].set_calls == []


def test_held_non_modifier_key_cancels_fix(env):
    env["ctrl"].on_key(kc.KEY_Q, 1, 2)  # вторая клавиатура держит клавишу
    env["clock"].now += 1
    type_text(env, "ghbdtn ")
    assert env["typist"].events == []


def test_context_languages_tracked(env):
    type_text(env, "hello world ")
    assert list(env["ctrl"].context) == ["en", "en"]
    type_text(env, "ghbdtn ")
    assert list(env["ctrl"].context) == ["en", "ru"]


def test_timeout_clears_context(env):
    type_text(env, "hello world ")
    assert list(env["ctrl"].context) == ["en", "en"]
    env["clock"].now += 20  # пауза дольше phrase_timeout
    type_text(env, "ghbdtn ")
    assert list(env["ctrl"].context) == ["ru"]


def test_layout_change_by_daemon_does_not_reset(env):
    type_text(env, "ghbdtn ")
    env["ctrl"].on_layout_changed(RU, False)
    assert not env["ctrl"].buffer.is_empty()


def test_manual_word_plain_switch_busy_guard_swallows_reentrant_key(env):
    """wait_applied может реентрантно прокрутить главный цикл и доставить колбэки;
    busy должен быть выставлен уже на пустом plain-switch пути, иначе физическое
    нажатие клавиши, пришедшее прямо из wait_applied, попадёт в обычную обработку.
    """
    ctrl = env["ctrl"]

    class ReentrantBackend(FakeBackend):
        def __init__(self, ctrl):
            super().__init__(current=US)
            self._ctrl = ctrl

        def wait_applied(self, index: int, timeout: float) -> bool:
            self._ctrl.on_key(kc.KEY_A, 1, 1)  # реентрантное физическое нажатие
            return super().wait_applied(index, timeout)

    backend = ReentrantBackend(ctrl)
    ctrl.backend = backend
    env["backend"] = backend

    assert ctrl.manual_word() is True
    assert ctrl.buffer.is_empty()
    assert backend.set_calls == [RU]


def test_first_short_word_does_not_crash(env):
    """В начале сессии context пуст (deque() без элементов), а Detector.decide()
    для коротких слов читает context[-1] — без дополнения до 2-tuple это IndexError."""
    type_text(env, "z ")
    assert typed_text(env) == "\b" * 2 + "я "


def test_gestures_blocked_while_paused_or_locked(env):
    type_text(env, "ghbdtn")
    env["ctrl"].pause()
    double_shift(env)
    assert env["typist"].events == [] and env["backend"].set_calls == []
    env["ctrl"].resume()
    env["ctrl"].set_locked(True)
    type_text(env, "ghbdtn")  # буфер пуст после lock — печатаем заново
    double_shift(env)
    assert env["typist"].events == [] and env["backend"].set_calls == []


def test_failed_fix_leaves_context_empty(env):
    env["backend"] = FakeBackend(current=US, fail_switch=True)
    env["ctrl"].backend = env["backend"]
    type_text(env, "ghbdtn ")
    assert list(env["ctrl"].context) == []
    assert env["ctrl"].buffer.is_empty()
