import pytest

from tests.fakes import RU, US, FakeBackend, RecordingTypist, make_keymap
from wayswitch import controller as controller_mod
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


# --- исполнение вне диспатча evdev (schedule) -------------------------------

def deferred(env):
    """Подменяет schedule на очередь: решения принимаются сразу, печать — по drain()."""
    queue: list = []
    env["ctrl"].schedule = queue.append
    return queue


def drain(queue):
    while queue:
        queue.pop(0)()


def test_fix_is_scheduled_not_executed_in_dispatch(env):
    queue = deferred(env)
    type_text(env, "ghbdtn ")
    # Решение принято, но ничего не напечатано и раскладка не тронута.
    assert env["typist"].events == [] and env["backend"].set_calls == []
    assert env["corrected"] == [] and len(queue) == 1
    drain(queue)
    assert typed_text(env) == "\b" * 7 + "привет "
    assert env["corrected"] == [("ghbdtn", "привет", False)]
    assert list(env["ctrl"].context) == ["ru"]


def test_scheduled_fix_skipped_when_buffer_changed(env):
    queue = deferred(env)
    type_text(env, "ghbdtn ")
    type_text(env, "x")  # пользователь продолжил печатать до idle
    drain(queue)
    assert env["typist"].events == [] and env["backend"].set_calls == []
    assert env["corrected"] == []
    assert list(env["ctrl"].context) == []


def test_scheduled_early_url_and_manual_fixes_are_deferred_too(env):
    queue = deferred(env)
    env["backend"].current_index = RU
    type_text(env, "реезыЖ..")
    assert env["typist"].events == [] and len(queue) == 1
    drain(queue)
    assert typed_text(env) == "\b" * 8 + "https://"
    env["typist"].events.clear()
    env["backend"].current_index = US
    env["ctrl"].buffer.reset()
    type_text(env, "ghbdtn")
    assert env["ctrl"].manual_word() is True
    assert env["typist"].events == []
    drain(queue)
    assert typed_text(env) == "\b" * 6 + "привет"


def test_third_shift_tap_before_scheduled_word_fix_runs_becomes_phrase(env):
    """Третий тап пришёл, пока фикс второго ещё ждал idle: жест не теряется и
    не даёт лишнего переворота раскладки — фраза перепечатывается после слова."""
    env["ctrl"].config.general.auto_correct = False
    queue = deferred(env)
    type_text(env, "ghbdtn vbh")
    double_shift(env, times=3)
    assert len(queue) == 1  # фраза ждёт исполнения слова
    drain(queue)
    assert typed_text(env).endswith("привет мир")
    assert env["km"].decode(env["ctrl"].buffer.phrase(), RU) == "привет мир"
    assert env["backend"].set_calls == [RU]
    assert env["typist"].stuck() == set()


def test_pending_gesture_survives_guard_failure_while_other_fix_still_queued(env):
    """Жест, пришедший при self._pending == 2 (два фикса ещё не исполнены),
    не должен теряться, когда исполнение первого фикса не проходит гвард
    _run_pending_gesture (второй фикс всё ещё в очереди): жест обязан
    дождаться исполнения второго и сработать тогда."""
    ctrl = env["ctrl"]
    queue = deferred(env)
    type_text(env, "ghbdtn ")
    type_text(env, "vbh ")
    assert ctrl._pending == 2 and len(queue) == 2

    double_shift(env)  # второй фикс ещё не исполнен → жест уходит в _pending_gesture
    assert ctrl._pending_gesture == "word"

    drain_one = queue.pop(0)
    drain_one()  # первый фикс исполнен, но второй ещё в очереди — гвард не пройден
    assert ctrl._pending == 1
    assert ctrl._pending_gesture == "word", "жест не должен теряться при неудачном гварде"

    drain_one = queue.pop(0)
    drain_one()  # второй фикс исполнен, гвард пройден — жест применяется
    assert ctrl._pending_gesture is None
    assert ctrl._pending == 1  # manual_word() из жеста запланировал третий фикс
    assert len(queue) == 1
    drain(queue)
    assert ctrl._pending == 0
    # Первый фикс ("ghbdtn") отменён самим run() — буфер успел уйти вперёд
    # (набрано "vbh ") к моменту его исполнения; это не связано с багом
    # гварда и ожидаемо. Важно, что жест таки применился, а не потерялся:
    # второй (авто) фикс "vbh"→"мир" исполнился, а следом отработал жест
    # (мануальный, из _pending_gesture), откатив его обратно на "vbh".
    assert [c[2] for c in env["corrected"]] == [False, True]
    assert env["corrected"][0] == ("vbh", "мир", False)
    assert env["corrected"][1][1] == "vbh"  # жест вернул слово обратно


# --- гонки во время исполнения (I2/I3/I4) -------------------------------------

def reenter_during_fix(env, action, at_event=6):
    """Typist, который при записи at_event-го события вызывает action() —
    имитация события, доставленного pump()'ом посреди перепечатки."""
    class Reentrant(RecordingTypist):
        fired = False

        def key(self, code, value):
            super().key(code, value)
            if not self.fired and len(self.events) >= at_event:
                self.fired = True
                action()
    env["typist"] = Reentrant()
    env["ctrl"].typist = env["typist"]


def test_shift_press_during_fix_does_not_abort(env):
    ctrl = env["ctrl"]

    def tap_shift():
        ctrl.on_key(kc.KEY_LEFTSHIFT, 1, 1)
        ctrl.on_key(kc.KEY_LEFTSHIFT, 0, 1)

    reenter_during_fix(env, tap_shift)
    type_text(env, "ghbdtn ")
    assert typed_text(env) == "\b" * 7 + "привет "
    assert env["corrected"] == [("ghbdtn", "привет", False)]
    assert env["typist"].stuck() == set()


def test_shift_held_across_fix_end_is_still_tracked(env):
    ctrl = env["ctrl"]
    reenter_during_fix(env, lambda: ctrl.on_key(kc.KEY_LEFTSHIFT, 1, 1))
    type_text(env, "ghbdtn ")
    assert env["corrected"] == [("ghbdtn", "привет", False)]
    assert ctrl.buffer.shift_held()  # нажат во время фикса, не отпущен — зажат


def test_third_shift_tap_during_word_fix_execution_yields_phrase(env):
    ctrl = env["ctrl"]
    ctrl.config.general.auto_correct = False

    def tap_shift():
        ctrl.on_key(kc.KEY_RIGHTSHIFT, 1, 1)
        ctrl.on_key(kc.KEY_RIGHTSHIFT, 0, 1)

    reenter_during_fix(env, tap_shift)
    type_text(env, "ghbdtn vbh")
    double_shift(env)  # второй тап запускает фикс слова; третий приходит во время него
    assert typed_text(env).endswith("привет мир")
    assert env["km"].decode(ctrl.buffer.phrase(), RU) == "привет мир"
    assert env["backend"].set_calls == [RU]
    assert env["typist"].stuck() == set()


def test_abort_by_click_resets_gesture(env):
    ctrl = env["ctrl"]
    ctrl.config.general.auto_correct = False
    reenter_during_fix(env, ctrl.on_click)
    type_text(env, "ghbdtn")
    double_shift(env)  # фикс слова прерван кликом (сам клик серию Shift не трогает)
    assert ctrl.buffer.is_empty() and env["backend"].set_calls == []
    # Серия начата заново: свежий двойной Shift — обычный переворот раскладки,
    # а не «третий/четвёртый тап» старой серии (которые дали бы пустую фразу).
    double_shift(env)
    assert env["backend"].set_calls == [RU]


def test_manual_fix_reentrant_during_fix_is_rejected(env):
    ctrl = env["ctrl"]
    results = []

    def pump():
        if ctrl.busy:
            results.append(ctrl.manual_word())
            results.append(ctrl.manual_phrase())

    ctrl.pump = pump
    type_text(env, "ghbdtn ")
    assert results and set(results) == {False}
    assert typed_text(env) == "\b" * 7 + "привет "  # напечатано ровно один раз
    assert env["corrected"] == [("ghbdtn", "привет", False)]


def test_manual_fix_rejected_while_paused_or_locked(env):
    ctrl = env["ctrl"]
    type_text(env, "ghbdtn")
    ctrl.pause()
    assert ctrl.manual_word() is False and ctrl.manual_phrase() is False
    ctrl.resume()
    ctrl.set_locked(True)
    assert ctrl.manual_word() is False and ctrl.manual_phrase() is False
    # На экране блокировки даже раскладка не переключается (буфер пуст).
    assert env["typist"].events == [] and env["backend"].set_calls == []


def test_click_during_fix_aborts(env):
    reenter_during_fix(env, env["ctrl"].on_click)
    type_text(env, "ghbdtn ")
    assert env["typist"].stuck() == set()
    assert env["ctrl"].buffer.is_empty()
    assert env["corrected"] == []


def test_pause_during_fix_aborts(env):
    reenter_during_fix(env, env["ctrl"].pause)
    type_text(env, "ghbdtn ")
    assert env["typist"].stuck() == set()
    assert env["ctrl"].buffer.is_empty()
    assert env["corrected"] == [] and env["ctrl"].paused


def test_held_physical_shift_cancels_fix(env, monkeypatch):
    monkeypatch.setattr(controller_mod, "RELEASE_WAIT", 0.02)
    type_text(env, "ghbdtn")
    env["ctrl"].on_key(kc.KEY_LEFTSHIFT, 1, 2)  # Shift зажат на второй клавиатуре
    type_text(env, " ")
    assert env["typist"].events == [] and env["backend"].set_calls == []


def test_fix_waits_for_physical_shift_release(env):
    ctrl = env["ctrl"]
    type_text(env, "ghbdtn")
    ctrl.on_key(kc.KEY_LEFTSHIFT, 1, 2)
    ctrl.pump = lambda: ctrl.on_key(kc.KEY_LEFTSHIFT, 0, 2)  # отпускание приходит в ожидании
    type_text(env, " ")
    assert typed_text(env) == "\b" * 7 + "привет "
