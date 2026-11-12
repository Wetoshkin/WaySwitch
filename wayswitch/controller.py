"""Оркестрация без привязки к GLib: события клавиш → решения → действия.

Контроллер ничего не знает о evdev, D-Bus и главном цикле. Всё внешнее
приходит через вызовы on_*, всё наружу — через typist/backend и колбэки.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from wayswitch import a11y as focus
from wayswitch import keycodes as kc
from wayswitch.actuator import (
    ABORTED,
    DONE,
    FAILED,
    Plan,
    ReleaseModifiers,
    SwitchLayout,
    TextTypist,
    Type,
    execute,
    keys_for_typing,
    plan_fix,
)
from wayswitch.buffer import InputBuffer, KeyPress
from wayswitch.config import Config, apps_path
from wayswitch.detector import SENSITIVITY, Detector
from wayswitch.editable import _libreoffice_component
from wayswitch.gesture import ShiftGesture, TapKeyGesture
from wayswitch.selection import convert as convert_selection
from wayswitch.textops import number_words, transliterate

log = logging.getLogger("wayswitch")


_UNSET = object()  # «кэш ещё не заполнялся»: None значило бы «файла нет»
RELEASE_WAIT = 0.3  # сколько ждать отпускания физических клавиш перед печатью
SELECTION_RELEASE_WAIT = 2.0  # «Исправить выделенное» с сочетания: ждать отпускания
SELECTION_FOCUS_WAIT = 2.0  # после простоя ждём первый асинхронный снимок фокуса
# Готовый снимок фокуса после простоя опроса (тот же дедлайн, что у
# selection-команды): ждём не дольше, чем фоновый опрос успевает ответить.
SELECTION_MAX = 300  # больше — это уже не опечатка во фразе


def _word_unfinished(events: list[tuple[int, int, int]]) -> bool:
    """Последнее нажатие во время исправления — буква (слово ещё не закончено)."""
    for code, value, _dev in reversed(events):
        if value not in (1, 2) or code in kc.MODIFIER_KEYS or code in kc.LOCK_KEYS:
            continue
        return code != kc.KEY_SPACE and code not in kc.RESET_KEYS
    return False


def _clip(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
SPACE = KeyPress(kc.KEY_SPACE)


def apps_stamp() -> int | None:
    """Время изменения apps.txt в наносекундах (None — файла нет)."""
    path = apps_path()
    return path.stat().st_mtime_ns if path.exists() else None


@dataclass
class _WordGestureContext:
    """Что второй Shift-тап успел сделать с последним словом.

    Третьему тапу нужно знать выбранную группу и настоящий текст на экране:
    после удачного переворота фраза может быть смешанной (старые слова в
    прежней группе, последнее — уже в новой).
    """

    ok: bool
    changed: bool = False
    target_group: int = -1
    prefix: list[KeyPress] = field(default_factory=list)
    prefix_group: int = -1
    word_text: str = ""
    tail: int = 0


@dataclass
class LastFix:
    original_keys: list[KeyPress]
    # Что напечатано вместо слова, без хвостовых пробелов. У правил частиц и
    # слипшихся слов внутри бывает пробел («кем бы») — поэтому откат ищет эти
    # клавиши в хвосте фразы, а не сравнивает с последним словом.
    target_keys: list[KeyPress]
    group_before: int
    time: float
    # Где в буфере кончалось исправленное слово (без хвостовых пробелов).
    # Человек мог стереть его и набрать то же слово заново — это уже не
    # «откатить исправление», а новое слово.
    end: int = -1
    # Исправлено ранним триггером по началу слова: после него человек
    # допечатывает слово, и ручной переворот всего слова — это отмена.
    early: bool = False
    # Фактический исходный текст после разрешения регистра/раскладки: по нему
    # откат восстанавливает и собственные заглавные (например, `GHbdtn`).
    original_text: str | None = None
    # Для раннего триггера — всё слово (буквы в исходной раскладке), когда
    # его дописали: по нему потом сбрасывается счётчик отмен.
    word: str = ""
    secret: bool = False


class Controller:
    def __init__(self, config: Config, keymap, detector: Detector, backend, typist, *,
                 clock: Callable[[], float] = time.monotonic,
                 pump: Callable[[], None] = lambda: None,
                 screen_locked: Callable[[], bool] = lambda: False,
                 require_lock_confirmation: bool = False,
                 dry_run: bool = False,
                 on_corrected: Callable[[str, str, bool], None] | None = None,
                 on_fixed: Callable[[bool], None] | None = None,
                 on_status: Callable[[dict], None] | None = None,
                 persist_exception: Callable[[str], None] | None = None,
                 probe=None,
                 apps_provider: Callable[[], list[str]] = lambda: [],
                 sleep: Callable[[float], None] = time.sleep,
                 schedule: Callable[[Callable[[], None]], None] = lambda fn: fn()):
        """`schedule(fn)` — отложить исполнение вне диспатча источника событий;
        в демоне — GLib.idle_add. Источник evdev в GLib не реентрантен: пока
        выполняется его колбэк, события той же клавиатуры не доставляются,
        поэтому ожидание отпускания и прерывание физическим нажатием работают
        только если сам фикс исполняется уже после возврата из колбэка.
        Решение принимается в on_key, исполнение — в отложенном вызове, который
        сначала проверяет, что буфер не изменился. По умолчанию (тесты) —
        синхронно."""
        self.config = config
        self.keymap = keymap
        self.detector = detector
        self.backend = backend
        self.typist = typist
        self.clock = clock
        self.pump = pump  # прокрутить очередь событий главного цикла (для отмены)
        self.screen_locked = screen_locked
        self.dry_run = dry_run
        self.on_corrected = on_corrected
        # Исправление выполнено (без текста): звук — и для поля пароля тоже.
        self.on_fixed = on_fixed
        self.on_status = on_status
        self.persist_exception = persist_exception
        # Сведения о фокусе (приложение, поле пароля) — из at-spi, через снимок
        # в отдельном потоке: в пути ввода опрашивать доступность нельзя.
        self.probe = probe
        # Смена источника фокуса обнуляет привязку выделения даже при возврате
        # к тому же объекту probe (ABA): номер меняется на каждом set_probe.
        self._probe_epoch = 0
        self._buffer_focus = None
        # Последний известный владелец переживает временный провал метаданных.
        self._buffer_owner = None
        self._focus_boundary_at = 0.0
        self._buffer_private = False
        # Поле появилось уже после первых букв (вход в редактирование ячейки
        # Calc): буфер сохраняем, но исправление разрешаем только проверяемой
        # заменой диапазона, а не клавишами.
        self._buffer_window = None
        self._provisional_binding = False
        # Ожидание снимка после простоя: реентрантные клавиши из pump() копим
        # до конца текущей, иначе они обгонят ещё не учтённую клавишу.
        self._focus_waiting = False
        self._focus_wait_cancel = False
        # Клик/фикс во время ожидания меняет контекст: клавиша-триггер уже
        # относится к прежнему месту и не должна попасть в новый буфер.
        self._focus_wait_boundary = False
        self._focus_wait_queue: list[tuple[int, int, int]] = []
        self._key_depth = 0
        # Одна попытка пробуждения на эпизод простоя: у совсем упавшего
        # провайдера не должно быть дедлайна на каждую следующую клавишу.
        self._focus_wake_attempted = False
        # Список исключённых приложений нужен на каждом слове, а читать файл
        # каждый раз незачем: кэшируем по mtime, тогда правка apps.txt всё равно
        # подхватывается без перезапуска демона.
        self._apps_provider = apps_provider
        self._apps_cache: list[str] = []
        self._apps_stamp: object = _UNSET
        self.sleep = sleep
        self.schedule = schedule

        self.buffer = InputBuffer(keymap, config.general.phrase_timeout_sec,
                                  terminators=self._terminator_keys(config))
        self._rebuild_gesture(config)
        self.context: deque[str | None] = deque(maxlen=2)  # языки двух последних слов
        # Поколение контекста: любой сброс делает отложенный done устаревшим,
        # даже если между сбросом и done успел выполниться другой фикс.
        self._context_epoch = 0
        self._require_lock_confirmation = require_lock_confirmation
        self.lock_confirmation_required = require_lock_confirmation
        self.paused = require_lock_confirmation
        self.locked = False
        self.busy = False
        self._abort_requested = False
        self._operation_invalidated = False
        # Физическая буква, нажатая во время исправления, в буфер не попала:
        # слово на экране и в буфере разошлись. До конца этого слова ничего не
        # трогаем (ни авто, ни жестом) — иначе стёрли бы не столько, сколько надо.
        self._lost_input = False
        self._skip_word = False
        # Слово только что вернули жестом после раннего триггера: на пробеле
        # его не исправлять снова (иначе пинг-понг и обучение не наступит).
        self._hold_keys: list[KeyPress] | None = None
        # Нажатия, пришедшие во время исправления: если оно прервано до первого
        # Backspace (экран не тронут), их досылаем в буфер — слово не теряется.
        self._busy_keys: list[tuple[int, int, int]] = []
        self._busy_held: frozenset = frozenset()
        self._busy_reset = False  # клик/пауза/блокировка посреди исправления
        self._pending = 0  # отложенных (запланированных, но ещё не исполненных) фиксов
        self._pending_gesture: str | None = None  # жест, пришедший во время фикса
        self._pending_after: str | None = None  # второй жест той же серии (слово → фраза)
        self._word_gesture: _WordGestureContext | None = None
        self._last_fix: LastFix | None = None
        # Сколько раз подряд отменяли исправление слова: запоминаем слово как
        # исключение только с learn_after_undos-й отмены.
        self._undo_counts: dict[str, int] = {}
        # Время последней правки текста руками и последней ручной смены
        # раскладки: в это окно автоисправление не вмешивается.
        self._last_edit_at: float | None = None
        self._last_external_layout_at: float | None = None
        self._layout_shortcut_partial_at: float | None = None
        self.stats = {"corrections": 0, "manual": 0, "undo": 0}
        self._pause_key = kc.KEY_NAMES.get(config.gesture.pause_hotkey.lower()) \
            if config.gesture.pause_hotkey else None

    # --- конфигурация / статус ---------------------------------------------------

    @staticmethod
    def _terminator_keys(config: Config) -> set[int]:
        """Клавиши, завершающие слово помимо пробела.

        Tab и Enter (fix_on_tab / fix_on_enter) отключены: к моменту фикса
        Tab уже выбрал автодополнение, а Enter отправил сообщение, и
        Backspace-ы стёрли бы не то. Ключи остаются в конфиге, чтобы старые
        config.toml читались, но ничего не включают.
        """
        del config
        return set()

    def _rebuild_gesture(self, config: Config) -> None:
        """Жест по настройке: двойной Shift или одиночный тап своей клавиши."""
        # Клавиши действий над выделенным: «Исправить выделенное»,
        # транслитерация, число прописью — у каждой свой тап-жест.
        self._selection_gestures: list[tuple[TapKeyGesture, str]] = []
        for field_name, action in (("selection_key", "layout"), ("translit_key", "translit"),
                                   ("numbers_key", "numbers")):
            name = getattr(config.gesture, field_name).lower()
            if name in kc.KEY_NAMES:
                self._selection_gestures.append(
                    (TapKeyGesture(frozenset({kc.KEY_NAMES[name]})), action))
        if config.gesture.manual == "tap_key":
            # Неизвестное имя клавиши сюда не дойдёт (ловит валидация конфига),
            # но запасной вариант на случай правки конфига в обход неё.
            code = kc.KEY_NAMES.get(config.gesture.tap_key.lower(), kc.KEY_LEFTCTRL)
            self.gesture = TapKeyGesture(frozenset({code}))
            self._gesture_keys = self.gesture.keys
        else:
            # Shift-жест остаётся объектом и при выключенных жестах: on_key зовёт
            # его только для double_shift, но reset() из клика должен работать.
            self.gesture = ShiftGesture()
            self._gesture_keys = kc.SHIFT_KEYS

    def reload_config(self, config: Config) -> None:
        self.config = config
        self._rebuild_gesture(config)
        self._apps_stamp = _UNSET  # Reload — перечитать и список программ
        self.buffer.phrase_timeout = config.general.phrase_timeout_sec
        self.buffer.set_terminators(self._terminator_keys(config))
        self.detector.sensitivity = SENSITIVITY[config.general.sensitivity]
        self._pause_key = kc.KEY_NAMES.get(config.gesture.pause_hotkey.lower()) \
            if config.gesture.pause_hotkey else None
        self._emit_status()

    def status(self) -> dict:
        return {
            "active": self._auto_allowed(),
            "paused": self.paused,
            "locked": self.locked,
            "lock_confirmation_required": self.lock_confirmation_required,
            "busy": self.busy,
            "backend": getattr(self.backend, "name", "?"),
            # Наличие безопасного AT-SPI-механизма, а не старый флаг бэкенда:
            # возможность попытки команды, не утверждение о выделении сейчас.
            "selection_supported": self._selection_supported(),
            "layout_index": self.backend.current(),
            "layout": self._layout_alphabet(),
            "layout_supported": self._groups() is not None,
            "auto_correct": self.config.general.auto_correct,
            "corrections_total": self.stats["corrections"],
            "manual_total": self.stats["manual"],
            "undo_total": self.stats["undo"],
            "dry_run": self.dry_run,
        }

    def _layout_alphabet(self) -> str | None:
        """«ru» / «latin» для текущей раскладки — окну, чтобы писать её словами."""
        current = self.backend.current()
        if current is None:
            return None
        try:
            language = self.keymap.language(current)
            return {"ru": "ru", "en": "latin"}.get(language, "other")
        except (IndexError, ValueError):
            return None

    def _emit_status(self) -> None:
        if self.on_status:
            self.on_status(self.status())

    def pause(self) -> None:
        self.paused = True
        self._layout_shortcut_partial_at = None
        if self.busy:
            self._abort_requested = True  # пауза посреди фикса — не дописывать слово
            self._busy_reset = True
        self._skip_word = False
        self.buffer.reset()
        self._clear_context()
        self._buffer_owner = None
        self._last_fix = None
        self._hold_keys = None
        self._word_gesture = None
        self._pending_after = None
        self._emit_status()

    def resume(self, *, confirm_lock: bool = False) -> bool:
        if self.screen_locked() or self.locked:
            return False
        if self.lock_confirmation_required:
            if not confirm_lock:
                return False
            self.lock_confirmation_required = False
        self.paused = False
        self._emit_status()
        return True

    def set_locked(self, on: bool) -> None:
        self.locked = on
        if on:
            self._layout_shortcut_partial_at = None
            if self._require_lock_confirmation:
                self.lock_confirmation_required = self.paused = True
            self._busy_keys.clear()
            self._pending_gesture = None
            self._pending_after = None
            self._word_gesture = None
            self.gesture.reset()
            for gesture, _action in self._selection_gestures:
                gesture.reset()
        if on and self.busy:
            self._operation_invalidated = True
            self._abort_requested = True  # ничего не печатать на экране блокировки
        if self.busy:
            self._busy_reset = True
        self._skip_word = False
        self.buffer.reset()
        self._clear_context()
        self._buffer_owner = None
        self._last_fix = None
        self._hold_keys = None
        self._word_gesture = None
        self._pending_after = None
        self._emit_status()

    def set_caps(self, on: bool) -> None:
        self.buffer.set_caps(on)

    # --- входящие события -------------------------------------------------------

    def on_key(self, code: int, value: int, device_id: int) -> None:
        """Точка входа клавиатуры: во время ожидания фокуса события копим.

        pump() внутри ожидания прокручивает главный цикл, и нажатия приходят
        реентрантно. Обработать их сразу нельзя (предыдущая клавиша ещё не
        учтена в буфере), а терять нельзя — откладываем до конца текущей.
        """
        if self._focus_waiting:
            self._focus_wait_queue.append((code, value, device_id))
            if value != 0:
                self._focus_wait_cancel = True
            return
        self._key_depth += 1
        try:
            self._on_key(code, value, device_id)
        finally:
            self._key_depth -= 1
            if self._key_depth == 0:
                self._replay_focus_queue()

    def _replay_focus_queue(self) -> None:
        """Дослать отложенные за время ожидания клавиши в исходном порядке."""
        while self._focus_wait_queue:
            events, self._focus_wait_queue = self._focus_wait_queue, []
            for code, value, device_id in events:
                self.on_key(code, value, device_id)

    def _focus_wait_cancelled(self, probe, epoch: int) -> bool:
        """Ожидание снимка отменяется, как только меняется его источник или режим."""
        return (self.probe is not probe or self._probe_epoch != epoch
                or self.paused or self.locked or self.screen_locked()
                or self._focus_wait_cancel)

    def _wake_focus_after_idle(self):
        """Разбудить уснувший опрос и ограниченно дождаться свежего фокуса.

        AtspiProbe после IDLE_STOP_SEC очищает снимок и завершает поток: первая
        клавиша после простоя должна сама разбудить его и подождать ответ, иначе
        слово начнётся по пустому снимку, а пришедший посреди слова снимок того
        же поля будет принят за смену поля. Возвращает («woke» | «skip» |
        «cancelled» | «rejected», снимок). Дедлайн — общий с selection-командой.
        """
        if self.probe is None or isinstance(self.probe, focus.NullProbe):
            return "skip", None
        if not self._focus_guard_active():
            # Защиты и исключений нет: ждать AT-SPI только ради проверки
            # пароля не нужно. Снимок для истории/native проверяется отдельно.
            return "skip", None
        try:
            snap = self.probe.snapshot()
        except Exception as exc:  # noqa: BLE001 — не показывать payload провайдера
            log.warning("снимок фокуса недоступен (%s)", type(exc).__name__)
            return "rejected", None
        now = self.clock()
        if snap.protection(now) is False:
            # Фокус и так свежий и доверенный: заново будить нечего.
            self._focus_wake_attempted = False
            return "skip", None
        if snap.app is not None:
            return "skip", None
        recent_unknown = snap.age(now) <= focus.MAX_AGE_SEC
        if self._focus_wake_attempted:
            return "skip", None
        self._focus_wake_attempted = True
        probe, epoch = self.probe, self._probe_epoch
        self._focus_wait_cancel = False
        self._focus_wait_boundary = False
        self._focus_waiting = True
        try:
            fresh, why = self._wake_focus(
                probe, epoch,
                accept=lambda s, at: None if s.protection(at) is True
                else s.protection(at) is False,
                cancelled=lambda: self._focus_wait_cancelled(probe, epoch))
        finally:
            self._focus_waiting = False
            if self._key_depth == 0:
                self._replay_focus_queue()
        if fresh is not None:
            self._focus_wake_attempted = False
            return "woke", fresh
        if why == "cancelled":
            return "cancelled", None
        if recent_unknown:
            return "skip", None  # позднюю привязку по-прежнему разрешает только native-проверка
        return "rejected", None

    def _rebind_focus_after_wake(self, snap) -> bool:
        """Свежий снимок после пробуждения — новый исходный, а не смена поля.

        Поколение выросло из-за перезапуска потока опроса, но поле (или
        приложение) то же — буфер последнего слова ещё действителен. Если же
        подтверждено другое поле, идём обычной проверкой: она сбросит буфер.
        """
        owner = self._focus_owner(snap)
        app = self._scope_app(snap, owner)
        old = self._buffer_focus
        if old is not None:
            old_probe, _, old_field, old_app, old_protection = old
            if (old_protection is not None
                    and (old_probe is not self.probe
                         or (old_field, old_app) != (snap.field, app))):
                return self._sync_focus()
        self._buffer_focus = (self.probe, snap.generation, snap.field, app,
                              snap.protection(self.clock()))
        self._buffer_owner = (owner
                              if snap.protection(self.clock()) is False else None)
        self._buffer_window = snap.window
        return True

    def _on_key(self, code: int, value: int, device_id: int) -> None:
        if self.screen_locked() or self.lock_confirmation_required:
            if value == 0:
                self.buffer.note_release(code, device_id)
            elif value == 1 and code in kc.MODIFIER_KEYS:
                self.buffer.note_press(code, device_id)
            return
        now = self.clock()
        if (value == 1 and code in kc.COMMAND_MODIFIERS and self.buffer.current_word()
                and not (self.busy or self.paused or self.locked)):
            self._layout_shortcut_partial_at = now
        if value == 1 and code not in self._gesture_keys:
            # Любая другая клавиша рвёт серию Shift: результат второго тапа
            # больше не может управлять будущей фразой.
            self._word_gesture = None
        if value == 1 and code in kc.EDIT_KEYS:
            self._last_edit_at = now
        if value == 1 and self.probe is not None \
                and not (self.busy or self.paused or self.locked):
            # Будим опрос фокуса с первой буквы: после простоя поток спит, и
            # к пробелу снимок поля пароля должен быть уже свежим.
            self._poke_probe()
        if value == 1 and not (self.busy or self.paused or self.locked):
            # Первая клавиша после простоя: без ожидания снимок пуст, и слово
            # начнётся по неизвестному фокусу. Ждём ограниченно; при неудаче
            # защиту не ослабляем — слово просто не исправится.
            why, snap = self._wake_focus_after_idle()
            if self._focus_wait_boundary:
                # Клик/фикс во время ожидания: физически клавиша набрана до
                # смены места, в новый буфер её не переносим (лучше не
                # исправить, чем стереть чужой символ).
                self._focus_wait_boundary = False
                self._skip_word = True
                return
            if why == "woke":
                self._rebind_focus_after_wake(snap)
            elif why == "rejected":
                self._skip_word = True
        if not (self.paused or self.locked):
            # Смена поля на любом «печатном» нажатии/отпускании может оказаться
            # пересозданием редактора посреди слова; жесты и действия над
            # выделением — это уже смена контекста, буфер не переносим.
            keep_partial = (code not in self._gesture_keys
                            and all(code not in gesture.keys
                                    for gesture, _action in self._selection_gestures))
            self._sync_focus(typing_key=keep_partial)
        # Жест прокручиваем всегда, в том числе во время фикса: иначе третий
        # Shift-тап, пришедший при busy, не будет учтён, и на его отпускании
        # счётчик снова даст «word» — лишний переворот раскладки.
        gesture = self.gesture.on_key(code, value, now) \
            if self.config.gesture.manual in ("double_shift", "tap_key") else None
        # Тап клавиши выделения тоже видит все клавиши: Ctrl+C при выделении
        # на правом Ctrl — не жест.
        selection_taps = [(g.on_key(code, value, now), g, action)
                          for g, action in self._selection_gestures]
        if self.busy:
            # Физический ввод во время перепечатки — прервать; зажатые клавиши
            # учесть. Shift-тап не прерывает: он короткий и сам по себе ничего
            # не печатает, а тройной Shift во время фикса второго — штатный жест.
            self._busy_keys.append((code, value, device_id))
            commands_held = any(c in kc.COMMAND_MODIFIERS
                                for _, c in self.buffer.held_physical())
            if value in (1, 2) and (
                    code in (kc.EDIT_KEYS | kc.RESET_KEYS | kc.COMMAND_MODIFIERS)
                    and code not in self._gesture_keys
                    or commands_held and code not in kc.COMMAND_MODIFIERS):
                self._busy_reset = True
            if value == 1:
                self.buffer.note_press(code, device_id)
                if code not in self._gesture_keys:
                    self._abort_requested = True
                if code not in kc.MODIFIER_KEYS and code not in kc.LOCK_KEYS:
                    self._lost_input = True
            elif value == 2 and code not in self._gesture_keys:
                self._abort_requested = True  # зажатая клавиша печатает и сейчас
            elif value == 0:
                self.buffer.note_release(code, device_id)
            if gesture:
                self._queue_pending_gesture(gesture)
            return
        own = [(tap, action) for tap, g, action in selection_taps if code in g.keys]
        if own and not (self.paused or self.locked):
            # Своя клавиша действия над выделенным (selection_key, translit_key,
            # numbers_key): одиночное нажатие, как у жеста последнего слова.
            # В буфер не идёт.
            self._note_own_key(code, value, device_id)
            tap, action = own[0]
            if tap:
                self.schedule(lambda a=action: self.transform_selection(a))
            return
        if self.config.gesture.manual == "tap_key" and code in self._gesture_keys \
                and not (self.paused or self.locked):
            # Клавишу жеста целиком не отдаём буферу: она не модификатор (Ctrl,
            # ScrollLock), и feed() сбросил бы набранное слово — переворачивать
            # стало бы нечего. Но зажатой её учитываем: иначе Ctrl+V при жесте
            # на левом Ctrl лёг бы в буфер буквой «v», а Shift-жест терял бы
            # регистр заглавных.
            self._note_own_key(code, value, device_id)
            if gesture:
                self._dispatch_gesture(gesture)
            return
        if value == 1 and self._skip_word and (code in kc.RESET_KEYS or code in kc.META_KEYS):
            self._skip_word = False  # Enter, стрелки, Esc: каретка ушла, слово кончилось
        if value == 1 and self.buffer.is_empty():
            # Новое слово начинается с пустого буфера — прежний сомнительный
            # контекст больше не действует.
            self._provisional_binding = False
            self._buffer_private = False
        if value == 1 and code not in kc.MODIFIER_KEYS and not self.buffer.command_held():
            self._layout_shortcut_partial_at = None
        event = self.buffer.feed(code, value, device_id, now)
        if event is not None and event.reset_before:
            self._buffer_private = False
        if (event is not None and event.kind in ("letter", "word")
                and not self._focus_guard_active()):
            self._buffer_private |= self._secret_focus()
        if value == 1 and self._pause_key is not None and code == self._pause_key:
            (self.resume if self.paused else self.pause)()
            return
        if self.paused or self.locked:
            # Пауза/блокировка выключают и ручные жесты — только сам хоткей
            # паузы (обработан выше) должен продолжать работать.
            self.buffer.reset()
            return
        if value == 1 and self.config.gesture.manual == "pause_key" and code == kc.KEY_PAUSE:
            gesture = "word"
        if gesture:
            self._dispatch_gesture(gesture)
            return
        if self._skip_word:
            if value == 1 and code == kc.KEY_SPACE:
                self._skip_word = False
                self.buffer.reset()
                self._clear_context()
            return
        if event is None:
            return
        if event.kind == "reset":
            self._clear_context()
            self._skip_word = False
            self._hold_keys = None
            return
        if event.reset_before:
            self._clear_context()  # буфер очищен по таймауту перед этой клавишей
        if not self._auto_allowed():
            return
        if event.kind == "word":
            self._on_word(event.keys, device_id, code)
        elif event.kind == "letter":
            self._on_letter(event.keys, device_id, code)

    def _note_own_key(self, code: int, value: int, device_id: int) -> None:
        """Своя клавиша жеста: в буфер не идёт, но зажатой учитывается.

        Если это клавиша, которая сама по себе сдвигает каретку или уводит
        фокус (Esc, Insert, F-клавиши, Menu), набранное слово больше не на
        экране под курсором — буфер сбрасываем, как сделал бы feed().
        Модификаторы и «замки» (CapsLock, ScrollLock, NumLock, Pause) каретку
        не двигают.
        """
        if value == 1:
            self.buffer.note_press(code, device_id)
            if code not in kc.MODIFIER_KEYS and code not in kc.LOCK_KEYS:
                self.buffer.reset()
                self._clear_context()
        elif value == 0:
            self.buffer.note_release(code, device_id)

    def _dispatch_gesture(self, gesture: str) -> None:
        try:
            self._dispatch_gesture_unsafe(gesture)
        except Exception as exc:  # noqa: BLE001 — бэкенд/D-Bus: не ронять чтение клавиатуры
            log.error("ручное исправление не выполнено: %s", type(exc).__name__)
            self.busy = False

    def _queue_pending_gesture(self, gesture: str) -> None:
        """Запомнить жест до конца текущего/уже запланированного фикса."""
        if self._pending_gesture is None:
            self._pending_gesture = gesture
        elif self._pending_gesture == "word" and gesture == "phrase":
            # Третий тап не должен заменить слово: оно выбирает группу для фразы.
            self._pending_after = "phrase"
        else:
            self._pending_gesture = gesture

    def _dispatch_gesture_unsafe(self, gesture: str) -> None:
        if self._pending:
            # Фикс уже запланирован, но ещё не исполнен (третий тап пришёл
            # раньше idle): жест обработаем после него, на обновлённом буфере.
            self._queue_pending_gesture(gesture)
        elif gesture == "word":
            self.manual_word(from_gesture=True)
        else:
            self.manual_phrase(from_gesture=True)

    def on_click(self) -> None:
        self._focus_boundary_at = self.clock()
        self._layout_shortcut_partial_at = None
        self._buffer_focus = self._buffer_owner = None
        self._buffer_window = None
        self._word_gesture = None
        self._pending_after = None
        if self._focus_waiting:
            self._focus_wait_boundary = True
        if self.busy:
            self._abort_requested = True  # клик — физический ввод, как и нажатие клавиши
            self._busy_reset = True
            return
        self.buffer.click()
        self._clear_context()
        self._skip_word = False
        self._buffer_owner = None
        self._hold_keys = None
        # Клик посреди тапа жеста — не жест: иначе Ctrl+клик переворачивал бы слово.
        self._word_gesture = None
        self._pending_after = None
        self.gesture.reset()
        for g, _action in self._selection_gestures:
            g.reset()

    def on_scroll(self) -> None:
        """Колесо мыши: слово не трогаем, но тап-жест разрываем (Ctrl+колесо — масштаб)."""
        self._word_gesture = None
        self._pending_after = None
        self.gesture.reset()
        for g, _action in self._selection_gestures:
            g.reset()

    def on_layout_changed(self, index: int | None, external: bool) -> None:
        if external:
            self._word_gesture = None
            self._pending_after = None
            if self.busy:
                self._operation_invalidated = True
                self._abort_requested = True
                self._busy_reset = True
            # Пауза нужна только если смена раскладки прервала уже набранное
            # слово. Целиком новое слово из пустого буфера начинается после
            # смены и не может быть суффиксом прежнего.
            shortcut = self._layout_shortcut_partial_at
            had_partial_word = bool(self.buffer.current_word()) or (
                shortcut is not None
                and 0 <= self.clock() - shortcut < self.config.general.edit_guard_sec)
            self._layout_shortcut_partial_at = None
            self.buffer.reset()
            self._clear_context()
            self._skip_word = False
            self._last_external_layout_at = self.clock() if had_partial_word else None
        self._emit_status()

    def on_resync(self, device_id: int, held) -> None:
        if self.screen_locked() or self.locked or self.lock_confirmation_required:
            self.buffer.resync(device_id, (c for c in held if c in kc.MODIFIER_KEYS))
            return
        if self.busy:
            # Снимок зажатых клавиш на входе в исправление устарел: досылать
            # нажатия поверх него нельзя — сбросим буфер после исправления.
            self._busy_reset = True
        self.buffer.resync(device_id, held)

    def on_device_gone(self, device_id: int) -> None:
        if self.busy:
            self._busy_reset = True
        self.buffer.device_gone(device_id)

    # --- авторежим --------------------------------------------------------------

    def _caps_remapped(self) -> bool:
        """Caps Lock занят другим делом (переключение раскладки, Esc, Ctrl):
        нажимать его, чтобы выключить регистр, нельзя — и светодиод может
        означать вовсе не регистр."""
        try:
            options = self.backend.xkb_options()
        except Exception:  # noqa: BLE001 — бэкенд не знает опций
            return True
        if options is None:
            return True
        for opt in options:
            o = opt.lower()
            if o.startswith("caps:") or ("caps" in o and o.split(":")[0] in
                                         ("grp", "grp_led", "ctrl", "lv3", "lv5", "shift")):
                return True
        return False

    def _auto_allowed(self) -> bool:
        return (self.config.general.auto_correct and not self.paused and not self.locked
                and getattr(self.backend, "supports_auto", False) and self._groups() is not None)

    def _groups(self) -> tuple[int, int] | None:
        current = self.backend.current()
        other = self.keymap.opposite_group(current)
        if other is None:
            return None
        return current, other

    def _context_tuple(self) -> tuple[str | None, str | None]:
        """`self.context` — deque(maxlen=2), но в начале сессии/фразы в нём
        может быть 0 или 1 элемент; Detector.decide() ожидает ровно 2-tuple
        (читает context[-1]/context[-2]) — дополняем слева None."""
        return (None,) * (2 - len(self.context)) + tuple(self.context)

    def _clear_context(self) -> None:
        """Сбросить контекст и обесценить все ожидающие колбэки done."""
        self.context.clear()
        if self.buffer.is_empty():
            self._buffer_private = False
        self._context_epoch += 1

    def _context_done(self, target_group: int) -> Callable[[bool, bool], None]:
        """Добавить язык успешной коррекции, если её контекст ещё действителен."""
        lang = self.detector.lang_of_group(target_group)
        epoch = self._context_epoch

        def done(ok: bool, secret: bool) -> None:
            if ok and self._context_epoch == epoch:
                self.context.append(lang)

        return done

    def set_probe(self, probe) -> None:
        """Подменить источник сведений о фокусе (после смены настроек).

        Старую пробу закрываем без ожидания потока: Reload приходит и посреди
        исправления (через pump), ждать там секунду нельзя.
        """
        old, self.probe = self.probe, probe
        self._probe_epoch += 1
        self._layout_shortcut_partial_at = None
        self._buffer_owner = None
        close = getattr(old, "close", None)
        if close is not None:
            close()

    def _sync_focus(self, *, initial_binding: bool = False, typing_key: bool = False) -> bool:
        """Сохранять владение буфером, не принимая провал метаданных за смену поля."""
        snap = self._live_snapshot()
        protection = snap.protection(self.clock())
        guarded = self._focus_guard_active()
        old = self._buffer_focus
        old_owner = self._buffer_owner
        owner = self._focus_owner(snap if snap.age(self.clock()) <= focus.MAX_AGE_SEC
                                  else focus.FocusSnapshot())
        owner_changed = not self._same_focus_owner(old_owner, owner)
        if not guarded and old is not None and old_owner is not None:
            if (old_owner[2] is None and snap.field is None
                    and old[-1] is not None and protection is not None
                    and old[-1] != protection):
                owner_changed = True
        if not guarded and not owner_changed:
            if old_owner is not None:
                owner = (self.probe, *(new or previous for previous, new in
                                      zip(old_owner[1:], owner[1:], strict=True)))
            self._buffer_owner = owner if any(owner[1:]) else None
            # Не подменять последнего владельца пустым снимком или состоянием меню.
            if protection is None or (snap.password and snap.field is None
                                      and old_owner is not None and old_owner[2] is not None):
                if old is None:
                    self._buffer_focus = (self.probe, snap.generation, snap.field,
                                          self._scope_app(snap, owner), protection)
                return True
        app = self._scope_app(snap, owner)
        scope = (self.probe, snap.generation, snap.field, app, protection)
        self._buffer_focus = scope
        old_window, self._buffer_window = self._buffer_window, snap.window
        same_window = old_window == snap.window
        if (not guarded and old is not None and old[2] is not None
                and old[2] == snap.field and not owner_changed):
            same_window = True  # заголовок документа меняется при наборе
        if old is None or old == scope and same_window and not owner_changed:
            if protection is False:
                self._buffer_owner = owner
            return True
        old_probe, _, old_field, old_app, old_protection = old
        if (not self._lost_input and not owner_changed and old_probe is self.probe
                and old_field is not None and old_field == snap.field and old_app == app
                and old_protection is False and protection is False and same_window):
            # Обновление того же поля допустимо и между Backspace и перепечаткой.
            self._buffer_owner = owner
            return True
        partial_word = typing_key and bool(self.buffer.current_word()) and not self._lost_input
        generation_only = (not owner_changed and old_probe is self.probe and partial_word
                           and old_field == snap.field and old_app == app
                           and (old_protection == protection
                                or not guarded and old_field is not None) and same_window)
        same_app = old_app in (None, app) or not guarded and not owner_changed
        calc_rebind = (old_probe is self.probe and old_app == app and same_window
                       and _libreoffice_component(snap) == "calc")
        carry = (old_probe is self.probe and partial_word and app is not None
                 and protection is False and old_protection is not True
                 and same_app and (not owner_changed or calc_rebind)
                 and (same_window or old_window is None)
                 and (old_field is None or old_app is None or calc_rebind))
        if generation_only or carry:
            self._provisional_binding |= guarded or (old_field is not None
                                                     and old_field != snap.field)
            self._buffer_owner = owner
            return True
        changed_field = (old_probe is self.probe and old_protection is not None
                         and protection is not None and old_field is not None
                         and snap.field is not None and old_field != snap.field)
        if changed_field:
            self._skip_word = self._lost_input
        else:
            self._skip_word |= bool(self.buffer.current_word()) or self._lost_input
        self._provisional_binding = False
        self._layout_shortcut_partial_at = None
        self._buffer_owner = None
        self.buffer.reset()
        self._clear_context()
        self._hold_keys = None
        self._last_fix = None
        self._pending_gesture = None
        self._pending_after = None
        self._word_gesture = None
        if self.busy and not initial_binding:
            self._operation_invalidated = self._busy_reset = True
        return False

    def _password_focus(self) -> bool:
        """Свежий снимок говорит: курсор в поле пароля."""
        if self.probe is None:
            return False
        snap = self._live_snapshot()
        return snap.password and snap.age(self.clock()) <= focus.MAX_AGE_SEC

    def _secret_focus(self) -> bool:
        """Текст исправления нельзя отдавать наружу (D-Bus, окно, журнал).

        Отдаём текст только при свежем подтверждённом фокусе вне поля пароля.
        Ошибка провайдера — неизвестность, а не разрешение.
        """
        if self.probe is None or isinstance(self.probe, focus.NullProbe):
            return True
        snap = self._live_snapshot()
        return self._buffer_private or snap.protection(self.clock()) is not False

    def _live_snapshot(self) -> focus.FocusSnapshot:
        """Ошибочный снимок и кэш до клика не подтверждают текущее поле."""
        if self.probe is None:
            return focus.FocusSnapshot()
        try:
            snap = self.probe.snapshot()
        except Exception as exc:  # noqa: BLE001 — не показывать payload провайдера
            log.warning("снимок фокуса недоступен (%s)", type(exc).__name__)
            return focus.FocusSnapshot()
        if (not isinstance(snap, focus.FocusSnapshot)
                or snap.taken_at < self._focus_boundary_at):
            return focus.FocusSnapshot()
        return snap

    def _poke_probe(self) -> None:
        if self.probe is None:
            return
        try:
            self.probe.poke()
        except Exception as exc:  # noqa: BLE001 — провайдер не должен ронять ввод
            log.warning("опрос фокуса не разбужен (%s)", type(exc).__name__)

    def _focus_guard_active(self) -> bool:
        """Парольная защита и исключения требуют подтверждения фокуса."""
        return self.config.general.skip_password_fields or bool(self._apps())

    def _focus_owner(self, snap) -> tuple:
        return (self.probe, snap.app, snap.field, snap.app_ids, snap.window)

    def _scope_app(self, snap, owner):
        return (owner[3][0] if not self._focus_guard_active() and owner[3]
                else snap.app)

    @staticmethod
    def _same_focus_owner(old, new) -> bool:
        """Смену подтверждают разные известные значения, а не отсутствие данных."""
        if old is None or new is None:
            return True
        if old[0] is not new[0]:
            return False
        if old[3] and new[3] and not (set(old[3]) & set(new[3])):
            return False
        if not (old[3] and new[3]) and old[1] and new[1] and old[1] != new[1]:
            return False
        if old[2] is not None and new[2] is not None and old[2] != new[2]:
            return False
        same_field = old[2] is not None and old[2] == new[2]
        if not same_field and old[4] and new[4] and old[4] != new[4]:
            return False
        return True

    def _same_buffer_scope(self, captured, captured_owner) -> bool:
        """Тот же буфер: поколение может вырасти, владелец — только тот же."""
        if self._focus_guard_active():
            if self._buffer_focus == captured:
                return True
            return (captured is not None and captured[2] is not None
                    and captured[-1] is False and self._buffer_focus is not None
                    and self._buffer_focus[0] is captured[0]
                    and self._buffer_focus[2:] == captured[2:]
                    and self._same_focus_owner(captured_owner, self._buffer_owner))
        if captured is None:
            return True
        if self._buffer_focus is None:
            return False
        if self._buffer_focus[0] is not captured[0]:
            return False
        if captured_owner is not None and self._buffer_owner is None:
            return False  # подтверждённая смена уже сбросила буфер
        return self._same_focus_owner(captured_owner, self._buffer_owner)

    def _apps(self) -> list[str]:
        """Исключённые приложения с кэшем по времени изменения файла."""
        try:
            stamp = apps_stamp()
        except OSError:
            return self._apps_cache
        if stamp != self._apps_stamp:
            try:
                self._apps_cache = self._apps_provider()
            except (OSError, ValueError) as exc:  # UnicodeDecodeError — тоже ValueError
                log.warning("список программ не прочитан: %s", exc)
            self._apps_stamp = stamp
        return self._apps_cache

    def _focus_blocks(self) -> str | None:
        """Почему сейчас нельзя трогать текст: поле пароля или исключённая программа."""
        if self.probe is None or not self._focus_guard_active():
            return None
        try:
            return focus.skip_reason(self.probe, self._apps(),
                                     self.config.general.skip_password_fields, self.clock())
        except Exception as exc:  # noqa: BLE001 — неизвестность при защите запрещает
            log.warning("снимок фокуса недоступен (%s)", type(exc).__name__)
            return "фокус недоступен"

    def _edit_guard_active(self) -> bool:
        """Только что правили текст руками — не лезем с автозаменой."""
        window = self.config.general.edit_guard_sec
        if window <= 0:
            return False
        now = self.clock()
        for stamp in (self._last_edit_at, self._last_external_layout_at):
            if stamp is not None and now - stamp < window:
                return True
        return False

    def _remember_fix_word(self, keys: list[KeyPress]) -> None:
        last = self._last_fix
        if last is not None:
            def letters(source) -> str:
                return "".join(c for c in self.keymap.decode(source, last.group_before)
                               if c.isalpha() or c in "-'").lower()

            if last.early and not last.word and keys[:len(last.target_keys)] == last.target_keys:
                # Дописали слово, начало которого исправил ранний триггер:
                # запоминаем его целиком — его ещё могут вернуть жестом.
                last.word = letters(keys)
            elif self._undo_counts:
                # Начато следующее слово, а исправление не отменили — значит,
                # оно было верным: счётчик отмен для слова начинаем заново.
                self._undo_counts.pop(last.word or letters(last.original_keys), None)

    def _on_word(self, keys: list[KeyPress], device_id: int, code: int) -> None:
        held, self._hold_keys = self._hold_keys, None
        if held is not None and list(keys) == held:
            # То самое слово, которое вернули жестом: не исправлять снова.
            self.context.append(None)
            return
        self._remember_fix_word(keys)
        groups = self._groups()
        if groups is None:
            return
        current, other = groups
        self._poke_probe()  # пусть свежий снимок фокуса будет уже к делу
        if self._edit_guard_active():
            log.debug("слово пропущено: только что была правка текста")
            self.context.append(None)
            return
        blocked = self._focus_blocks()
        if blocked:
            # Человек печатает в поле пароля или в исключённой программе:
            # молчим, но слово запоминаем как «не наше». Само слово в журнал
            # не пишем — это может быть пароль.
            log.debug("слово пропущено: %s", blocked)
            self.context.append(None)
            return
        word = "".join(c for c in self.detector.decode(keys, current).lower()
                       if c.isalpha() or c in "-'")
        if word and word in self.detector.exceptions:
            # Исключение действует на все правила, а не только на смену
            # раскладки: откат «расклад-ка» добавил «раскладка» в исключения.
            self.context.append(None)
            return
        punct = self.detector.punct_fix(keys, current, other) \
            if self.config.general.punct_fix else None
        if punct:
            # Переписываем слово целиком в ТОЙ ЖЕ раскладке: _schedule_fix сверяет
            # снимок с буфером, и «только последняя буква» не совпала бы.
            stem = self.detector.decode(keys[:-1], current)
            self._schedule_fix(keys, 1, current, f"{stem}{punct} ", manual=False,
                               reason="punct", group_before=current, switch=False,
                               trigger=(device_id, code), done=self._context_done(current))
            return
        split = self.detector.split_words_fix(keys, current) \
            if self.config.general.split_words_fix else None
        if split:
            self._schedule_fix(keys, 1, current, split + " ", manual=False,
                               reason="split", group_before=current, switch=False,
                               trigger=(device_id, code), done=self._context_done(current))
            return
        particles = self.detector.particles_fix(keys, current) \
            if self.config.general.particles_fix else None
        if particles:
            self._schedule_fix(keys, 1, current, particles + " ", manual=False,
                               reason="particles", group_before=current, switch=False,
                               trigger=(device_id, code), done=self._context_done(current))
            return
        caps = self.detector.caps_lock_fix(keys, current, other) \
            if self.config.general.caps_fix and self.buffer.caps \
            and not self._caps_remapped() else None
        if caps:
            text, group = caps
            self._schedule_fix(keys, 1, group, text + " ", manual=False, reason="caps-lock",
                               group_before=current, switch=group != current,
                               trigger=(device_id, code), caps_off=True,
                               done=self._context_done(group))
            return
        capitals = self.detector.two_capitals_fix(keys, current) \
            if self.config.general.two_capitals_fix and not self.buffer.caps else None
        if capitals:
            self._schedule_fix(keys, 1, current, capitals + " ", manual=False,
                               reason="capitals", group_before=current, switch=False,
                               trigger=(device_id, code), done=self._context_done(current))
            return
        decision = self.detector.decide(
            keys, current, other, self._context_tuple(),
            two_capitals=self.config.general.two_capitals_fix and not self.buffer.caps)
        log.debug("решение: %s (%s)", decision.action, decision.reason)
        if decision.action == "fix":
            self._schedule_fix(keys, 1, other, decision.target_text + " ", manual=False,
                               reason=decision.reason, group_before=current,
                               trigger=(device_id, code), done=self._context_done(other))
            return
        self.context.append(decision.a.lang if decision.a.in_dict else None)

    def _on_letter(self, keys: list[KeyPress], device_id: int, code: int) -> None:
        groups = self._groups()
        if groups is None:
            return
        current, other = groups
        self._poke_probe()
        if self._edit_guard_active():
            return
        if self._focus_blocks():
            return
        target = self.detector.early_url_target(keys, current, other)
        if target:
            self._schedule_fix(keys, 0, other, target, manual=False, reason="url-early",
                               group_before=current, trigger=(device_id, code))
            return
        general = self.config.general
        if not general.early_switch or general.sensitivity == "conservative":
            return
        early = self.detector.early_word_target(keys, current, other)
        if early:
            self._schedule_fix(keys, 0, other, early, manual=False, reason="early",
                               group_before=current, trigger=(device_id, code))
            return

    # --- ручной режим -----------------------------------------------------------

    def _manual_allowed(self) -> bool:
        # Проверяем здесь, а не только в on_key: по D-Bus (FixLastWord/FixPhrase)
        # вызовы приходят мимо клавиатурного гейта — в том числе реентрантно
        # из pump() посреди исполняющегося фикса и на экране блокировки.
        return (not (self.busy or self.paused or self.locked or self.screen_locked())
                and not self._focus_waiting
                and self._groups() is not None)

    def _ready_for_manual_focus(self) -> bool:
        """Ручной жест после простоя: дождаться свежего фокуса или отказать.

        Без свежего снимка не трогаем текст: слово могло быть набрано в другом
        поле, а пауза/блокировка/смена probe во время ожидания — отказ.
        """
        why, snap = self._wake_focus_after_idle()
        if why in ("cancelled", "rejected"):
            return False
        if why == "woke":
            return self._rebind_focus_after_wake(snap)
        return True

    def _record_word_gesture(self, ok: bool, *, target_group: int, prefix: list[KeyPress],
                             prefix_group: int, word_text: str, tail: int) -> None:
        """Запомнить итог второго тапа для третьего (только жест, не D-Bus)."""
        self._word_gesture = _WordGestureContext(
            ok=ok, changed=bool(ok and target_group != prefix_group),
            target_group=target_group, prefix=list(prefix), prefix_group=prefix_group,
            word_text=word_text, tail=tail)

    def manual_word(self, *, from_gesture: bool = False) -> bool:
        """Исправить последнее слово (или откатить, или просто переключить раскладку).

        True — исправление запланировано/выполнено, False — нечего делать."""
        self._word_gesture = None
        if not self._manual_allowed() or not self._ready_for_manual_focus():
            return False
        if not self._sync_focus() or self._skip_word:
            return False
        groups = self._groups()
        if groups is None:
            return False
        current, other = groups
        # Без feed() (жест «Нажатие клавиши», вызов по D-Bus) таймаут фразы
        # иначе не проверяется: через час простоя перевернулось бы старое слово.
        self.buffer.expire(self.clock())
        word, tail = self.buffer.last_word_with_tail()
        if not word:
            applied = True
            if not self.dry_run:
                self.busy = True
                try:
                    self.backend.set(other)
                    applied = self.backend.wait_applied(other, 0.5)
                except Exception as exc:  # noqa: BLE001 — транспорт мог пропасть
                    log.warning("не удалось переключить раскладку: %s", type(exc).__name__)
                    applied = False
                finally:
                    self.busy = False
            if from_gesture:
                self._word_gesture = _WordGestureContext(ok=False)
            self._emit_status()
            return applied
        last = self._last_fix
        secret = self._secret_focus()
        undo_keys = self._undo_span(last)
        if undo_keys is not None:
            original = (last.original_text if last.original_text is not None
                        else self.keymap.decode(last.original_keys, last.group_before))
            phrase = self.buffer.phrase()
            prefix = phrase[:max(0, len(phrase) - tail - len(undo_keys))]

            def done(ok: bool, secret: bool) -> None:
                if not ok:
                    return
                self._last_fix = None
                self.stats["undo"] += 1
                letters = "".join(c for c in original if c.isalpha() or c in "-'").lower()
                if letters and not secret and not last.secret:
                    self._note_undo(letters)
                self._emit_status()

            def finish(ok: bool, secret: bool) -> None:
                done(ok, secret)
                if from_gesture:
                    self._record_word_gesture(
                        ok, target_group=last.group_before, prefix=prefix,
                        prefix_group=current, word_text=original, tail=tail)

            self._schedule_fix(undo_keys, tail, last.group_before, original + " " * tail,
                               manual=True, reason="undo", group_before=current, done=finish,
                               span=True, secret=secret or last.secret)
            return True
        text = self.keymap.decode(word, other)
        phrase = self.buffer.phrase()
        prefix = phrase[:max(0, len(phrase) - tail - len(word))]
        done = None
        start = len(phrase) - tail - len(word)
        if (last is not None and last.early and other == last.group_before
                and self.clock() - last.time <= self.config.general.undo_window_sec
                and start == last.end - len(last.target_keys)
                and word[:len(last.target_keys)] == last.target_keys):
            # Слово начали, WaySwitch переключил раскладку по началу, человек
            # дописал и вернул всё слово назад — это отмена раннего триггера.
            letters = "".join(c for c in text if c.isalpha() or c in "-'").lower()

            def done(ok: bool, secret: bool, before_space: bool = tail == 0) -> None:
                if ok and letters:
                    self._last_fix = None
                    self.stats["undo"] += 1
                    if not secret and not last.secret:
                        self._note_undo(letters)
                    if before_space:
                        # Запоминаем само слово: пропустим на пробеле только
                        # его, а не любое следующее.
                        self._hold_keys = list(self.buffer.last_word_with_tail()[0])

        def finish(ok: bool, secret: bool) -> None:
            if done is not None:
                done(ok, secret)
            if from_gesture:
                self._record_word_gesture(
                    ok, target_group=other, prefix=prefix, prefix_group=current,
                    word_text=text, tail=tail)

        self._schedule_fix(word, tail, other, text + " " * tail, manual=True,
                           reason="manual", group_before=current, done=finish,
                           secret=secret or bool(done and last and last.secret))
        return True

    def _note_undo(self, word: str) -> None:
        """Исправление слова отменили: на learn_after_undos-й раз — запомнить.

        Одна отмена бывает случайной (слово исправлено верно, но человек
        передумал), поэтому сразу в исключения не пишем.
        """
        if self._secret_focus():
            return
        count = self._undo_counts.get(word, 0) + 1
        if count < self.config.general.learn_after_undos:
            self._undo_counts[word] = count
            log.info("исправление отменено; слово запомнится после ещё %d отмен",
                     self.config.general.learn_after_undos - count)
            return
        self._undo_counts.pop(word, None)
        self.detector.exceptions.add(word)
        if self.persist_exception:
            self.persist_exception(word)

    def _undo_span(self, last: LastFix | None) -> list[KeyPress] | None:
        """Клавиши последнего автоисправления в хвосте буфера — если его ещё
        можно откатить (окно не истекло, после него ничего не набрано)."""
        if last is None or self.clock() - last.time > self.config.general.undo_window_sec:
            return None
        phrase = self.buffer.phrase()
        end = len(phrase)
        while end > 0 and phrase[end - 1] == SPACE:
            end -= 1
        n = len(last.target_keys)
        if last.end >= 0 and end != last.end:
            return None
        if n and end >= n and phrase[end - n:end] == last.target_keys:
            return list(last.target_keys)
        return None

    def fix_selection(self, text: str | None = None) -> bool:
        """Перевести выделенный текст в другую раскладку («Исправить выделенное»)."""
        return self.transform_selection("layout", text)

    def _group_for_text(self, text: str, groups: tuple[int, int]) -> int | None:
        """Раскладка, в которой печатать результат: по алфавиту его букв."""
        cyrillic = any("а" <= c.lower() <= "я" or c.lower() == "ё" for c in text)
        want = "ru" if cyrillic else "latin"
        for g in groups:
            if self.keymap.alphabet(g) == want:
                return g
        return None

    def _convert_selection(self, action: str, text: str,
                           groups: tuple[int, int]) -> tuple[str, int] | None:
        if action == "layout":
            return convert_selection(self.keymap, text, groups)
        new_text = transliterate(text) if action == "translit" else number_words(text)
        if new_text is None:
            return None
        target = self._group_for_text(new_text, groups)
        return (new_text, target) if target is not None else None

    def _selection_supported(self) -> bool:
        """Есть ли безопасный AT-SPI-механизм для команды над выделением."""
        return self.probe is not None and not isinstance(self.probe, focus.NullProbe)

    def _selection_snapshot_ok(self, snap, now: float) -> bool:
        """Снимок подтверждает конкретное незащищённое поле прямо сейчас."""
        if not isinstance(snap, focus.FocusSnapshot):
            return False
        if not snap.app or snap.field is None or snap.password:
            return False
        if type(snap.taken_at) not in (int, float) or not 0 < snap.taken_at <= now:
            return False
        if snap.age(now) > focus.MAX_AGE_SEC:
            return False
        return snap.protection(now) is False

    def _selection_snapshot(self):
        """Свежий доверенный снимок поля для чтения выделения (или None)."""
        if not self._selection_supported():
            return None
        try:
            snap = self.probe.snapshot()
        except Exception as exc:  # noqa: BLE001 — источник фокуса не отвечает
            log.warning("исправить выделенное: снимок фокуса недоступен (%s)",
                        type(exc).__name__)
            return None
        return snap if self._selection_snapshot_ok(snap, self.clock()) else None

    def _selection_valid(self, expected, probe, epoch: int) -> Callable[[], bool]:
        """Связать чтение с захваченными probe/поколением/полем/защитой.

        taken_at не сравнивается: фоновый опрос обновляет его, не меняя поле.
        Поколение ловит смену поля и ABA; epoch — подмену самого probe.
        """
        def valid() -> bool:
            if (self.probe is not probe or self._probe_epoch != epoch
                    or self.paused or self.locked or self.screen_locked()):
                return False
            if self._operation_invalidated or self._abort_requested or self._busy_reset:
                return False
            try:
                snap = probe.snapshot()
            except Exception:  # noqa: BLE001 — ошибка провайдера не разрешает запись
                return False
            return (self._selection_snapshot_ok(snap, self.clock())
                    and snap.generation == expected.generation
                    and snap.app == expected.app and snap.field == expected.field
                    and snap.password is False)
        return valid

    def _read_bound_selection(self, expected, probe, epoch: int):
        """Ровно одно чтение текущего выделения и проверка его формы."""
        valid = self._selection_valid(expected, probe, epoch)
        if not valid():
            return None
        try:
            selection = focus.read_selection(expected, valid, SELECTION_MAX)
        except Exception as exc:  # noqa: BLE001 — не показывать текст в журнале
            log.warning("исправить выделенное: чтение не удалось (%s)", type(exc).__name__)
            return None
        if not isinstance(selection, focus.TextSelection):
            return None
        start, end, text = selection
        if (type(start) is not int or type(end) is not int
                or not isinstance(text, str)
                or not 0 <= start < end
                or end - start != len(text)
                or not text
                or len(text) > SELECTION_MAX
                or "\n" in text or "\r" in text):
            log.info("исправить выделенное: некорректное или неоднострочное выделение")
            return None
        if not valid():
            return None
        return selection

    def _wake_focus(self, probe, epoch: int, accept, cancelled):
        """Разбудить опрос и ограниченно дождаться годного снимка фокуса.

        accept(снимок, время) -> True (годится) | False (ждать дальше) |
        None (ждать бессмысленно, например поле пароля). cancelled() — отмена
        по внешнему событию (пауза, блокировка, смена probe, ввод). Дедлайн —
        та же константа, что у явной selection-команды: новых таймеров нет.
        """
        try:
            probe.poke()
        except Exception as exc:  # noqa: BLE001 — провайдер не должен ронять команду
            log.warning("опрос фокуса не разбужен (%s)", type(exc).__name__)
            return None, "error"
        deadline = time.monotonic() + SELECTION_FOCUS_WAIT
        while True:
            if cancelled():
                return None, "cancelled"
            try:
                snap = probe.snapshot()
            except Exception as exc:  # noqa: BLE001 — не показывать payload провайдера
                log.warning("снимок фокуса недоступен (%s)", type(exc).__name__)
                return None, "error"
            if isinstance(snap, focus.FocusSnapshot):
                verdict = accept(snap, self.clock())
                if verdict is True:
                    return snap, "ok"
                if verdict is None:
                    return snap, "password"
            if time.monotonic() >= deadline:
                return None, "timeout"
            self.pump()
            self.sleep(0.01)

    def _selection_cancelled(self, probe, epoch: int) -> bool:
        """Отменена ли уже зарезервированная явная команда.

        busy выставлен до подготовки, а флаги прерывания обнулены тогда же:
        любое их значение сейчас — новое событие (клавиша, клик, пауза,
        блокировка, смена probe), его нельзя затирать.
        """
        return (self.probe is not probe or self._probe_epoch != epoch
                or self.paused or self.locked or self.screen_locked()
                or self._abort_requested or self._operation_invalidated
                or self._busy_reset)

    def _prepare_selection_focus(self, probe, epoch: int):
        """Разбудить опрос и дождаться свежего доверенного поля.

        После IDLE_STOP_SEC AtspiProbe очищает снимок и завершает поток, поэтому
        первая явная команда сама будит его через poke() и даёт ограниченное
        время на асинхронный ответ. busy уже зарезервирован: реентрантный вызов
        из poke/pump отклоняется, а пауза/блокировка/ввод остаются отменой.
        """
        def accept(snap, now):
            if snap.protection(now) is True:
                return None
            return self._selection_snapshot_ok(snap, now)

        fresh, why = self._wake_focus(
            probe, epoch, accept=accept,
            cancelled=lambda: self._selection_cancelled(probe, epoch))
        if why != "ok" or fresh is None:
            return None
        if self._selection_cancelled(probe, epoch):
            return None
        try:
            self._sync_focus(initial_binding=True)
        except Exception as exc:  # noqa: BLE001 — источник фокуса не разрешает чтение
            log.warning("исправить выделенное: фокус недоступен (%s)", type(exc).__name__)
            return None
        if self._selection_cancelled(probe, epoch):
            return None
        expected = self._selection_snapshot()
        if expected is None or self._selection_cancelled(probe, epoch):
            return None
        return expected

    def transform_selection(self, action: str, text: str | None = None) -> bool:
        """Действие над выделенным текстом: «layout» — другая раскладка,
        «translit» — транслитерация, «numbers» — число прописью.

        Читается текущее выделение конкретного поля через AT-SPI: PRIMARY и
        backend.get_selection устарели и не доказывают, что выделено сейчас.
        Текст печатается поверх выделения: Backspace и буфер обмена не нужны.
        Явный text (совместимость) лишь сверяется с прочитанным, не заменяя его.
        """
        if not self._manual_allowed() or not self._selection_supported():
            return False
        probe, epoch = self.probe, self._probe_epoch
        # busy — ДО poke/pump: реентрантный selection-запрос из провайдера или
        # pump, а также физический ввод должны видеть уже занятую операцию.
        self.busy = True
        self._abort_requested = self._operation_invalidated = False
        self._busy_reset = False
        self._lost_input = False
        self._busy_keys = []
        result = ABORTED
        try:
            expected = self._prepare_selection_focus(probe, epoch)
            if expected is None:
                log.info("исправить выделенное: фокус не подтверждён")
                return False
            groups = self._groups()
            if groups is None:
                return False
            try:
                secret = self._secret_focus()
            except Exception as exc:  # noqa: BLE001 — не показывать payload провайдера
                log.warning("исправить выделенное: снимок фокуса недоступен (%s)",
                            type(exc).__name__)
                return False
            if self._selection_cancelled(probe, epoch):
                return False
            selection = self._read_bound_selection(expected, probe, epoch)
            if selection is None:
                log.info("исправить выделенное: выделение не прочитано")
                return False
            if text is not None and text != selection.text:
                log.info("исправить выделенное: явный текст не совпадает с текущим")
                return False
            converted = self._convert_selection(action, selection.text, groups)
            if converted is None:
                log.info("выделенное: нечего менять (%s)", action)
                return False
            new_text, target = converted
            # Каждый символ должен быть в целевой раскладке: и uinput, и
            # расширение GNOME печатают клавишами этой раскладки, а символ без
            # клавиши (эмодзи, «—») пропал бы молча — выделение уже заменено.
            keys = keys_for_typing(new_text, target, self.keymap, self.buffer.caps)
            if keys is None:
                log.warning("исправить выделенное: есть символы, которые не напечатать")
                return False
            steps: list = [ReleaseModifiers(), SwitchLayout(target)]
            steps.append(Type(new_text, [] if isinstance(self.typist, TextTypist) else keys))
            if self.dry_run:
                log.info("[dry-run] выделенное → %d символов", len(new_text))
                return False
            # Вызов с системного сочетания (Ctrl+Alt+…) приходит, пока клавиши
            # ещё зажаты: буквы превратились бы в команды. Ждём отпускания всех.
            if not self._wait_all_released(SELECTION_RELEASE_WAIT):
                log.info("исправить выделенное: клавиши сочетания не отпущены")
                return False

            def before_write() -> bool:
                # Повторно читаем ТО ЖЕ выделение после ожиданий и подтверждения
                # раскладки: старый снимок не доказывает, что границы не сдвинулись.
                again = self._read_bound_selection(expected, probe, epoch)
                return again is not None and again == selection

            # Стирающих шагов нет: execute вызывает before_write перед первой
            # печатаемой клавишей; отказ не оставляет ни текста, ни статистики.
            result = execute(Plan(steps), self.typist, self.backend, abort=self._abort,
                             key_delay=self.config.typing.key_delay_ms / 1000.0,
                             sleep=self.sleep, restore_group=groups[0],
                             hold=self._wait_shift_released,
                             stop=lambda: self._stop() or not self._selection_valid(
                                 expected, probe, epoch)(),
                             before_write=before_write)
        except Exception as exc:  # noqa: BLE001 — не выпускать payload провайдера
            result = FAILED
            log.warning("исправить выделенное: ошибка (%s)", type(exc).__name__)
        finally:
            if (self._lost_input and _word_unfinished(self._busy_keys)) or result == FAILED:
                self._skip_word = True
            self.buffer.reset()
            self._clear_context()
            self._last_fix = None
            self.busy = False
            self._lost_input = False
            self._busy_keys = []
            self._busy_reset = False
        try:
            synced = self._sync_focus()
        except Exception as exc:  # noqa: BLE001 — не показывать payload провайдера
            log.warning("исправить выделенное: фокус недоступен (%s)", type(exc).__name__)
            synced = False
        if result != DONE or not synced or self._operation_invalidated:
            log.info("исправить выделенное: %s", result)
            self._emit_status()
            return False
        self.stats["manual"] += 1
        self._last_fix = None
        if self.on_fixed:
            self.on_fixed(True)
        report_safe = False
        if self.on_corrected and not secret:
            try:
                report_safe = not self._secret_focus()
            except Exception as exc:  # noqa: BLE001 — не показывать payload провайдера
                log.warning("исправить выделенное: снимок фокуса недоступен (%s)",
                            type(exc).__name__)
        if report_safe:
            # Наружу — только начало: сигнал видят все программы сеанса.
            self.on_corrected(_clip(selection.text), _clip(new_text), True)
        self._emit_status()
        return True

    def _wait_shift_released(self) -> bool:
        """Перед каждой синтетической клавишей: физический Shift отпущен."""
        deadline = time.monotonic() + RELEASE_WAIT
        while any(code in (kc.KEY_LEFTSHIFT, kc.KEY_RIGHTSHIFT)
                  for _, code in self.buffer.held_physical()):
            if time.monotonic() > deadline:
                return False
            self.pump()
            self.sleep(0.005)
        return True

    def _wait_all_released(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while self.buffer.held_physical():
            if time.monotonic() > deadline:
                return False
            self.pump()
            self.sleep(0.01)
        return True

    def _word_gesture_matches(self, context: _WordGestureContext,
                              keys: list[KeyPress]) -> bool:
        """Буфер всё ещё содержит ту же фразу и результат второго тапа."""
        if len(keys) != len(context.prefix) + len(context.word_text) + context.tail:
            return False
        if keys[:len(context.prefix)] != context.prefix:
            return False
        end = len(keys) - context.tail
        if context.tail and keys[end:] != [SPACE] * context.tail:
            return False
        middle = keys[len(context.prefix):end]
        return self.keymap.decode(middle, context.target_group) == context.word_text

    def _word_gesture_source(self, context: _WordGestureContext) -> str:
        """Текст, который после второго тапа реально виден на экране."""
        return (self.keymap.decode(context.prefix, context.prefix_group)
                + context.word_text + " " * context.tail)

    def manual_phrase(self, *, from_gesture: bool = False) -> bool:
        """Перевести всю фразу в другую раскладку или переписать в выбранной.

        Жест третьего Shift приходит после второго тапа: тот уже выбрал группу
        (или отменил правку в прежней). Прямой вызов по D-Bus такого выбора не
        делает и переводит фразу в противоположную группу.
        """
        if not self._manual_allowed():
            return False
        if self.buffer.expire(self.clock()):
            if from_gesture:
                self._word_gesture = None
            return False
        context = None
        if from_gesture:
            context = self._word_gesture
            self._word_gesture = None
            if context is None or not context.ok:
                return False  # второй тап не состоялся — серия не должна срабатывать
        if not self._ready_for_manual_focus():
            return False
        if self.buffer.expire(self.clock()):
            return False
        if not self._sync_focus() or self._skip_word:
            return False
        groups = self._groups()
        if groups is None:
            return False
        current, other = groups
        keys = self.buffer.phrase()
        if not keys:
            return False
        if from_gesture:
            if current != context.target_group or not self._word_gesture_matches(context, keys):
                return False  # серия устарела: буфер или раскладка уже другие
            source = self._word_gesture_source(context)
            if context.changed:
                # Второй тап уже выбрал эту группу; переписываем фразу в ней.
                target_group = current
                switch = False
            else:
                # Откат правки в прежней группе выбора не сделал — переводим.
                target_group = other
                switch = True
        else:
            target_group = other
            source = self.keymap.decode(keys, current)
            switch = True
        text = self.keymap.decode(keys, target_group)
        self._schedule_fix(keys, 0, target_group, text, manual=True, reason="phrase",
                           group_before=current, whole_phrase=True, switch=switch,
                           source_text=source)
        return True

    # --- исполнение -------------------------------------------------------------

    def _schedule_fix(self, keys: list[KeyPress], tail: int, target_group: int, text: str,
                      *, manual: bool, reason: str, group_before: int,
                      whole_phrase: bool = False, switch: bool = True,
                      trigger: tuple[int, int] | None = None,
                      done: Callable[[bool, bool], None] | None = None,
                      span: bool = False, caps_off: bool = False, secret: bool = False,
                      source_text: str | None = None) -> None:
        """Отложить _apply_fix через self.schedule.

        Решение принято по снимку буфера; к моменту исполнения пользователь
        мог продолжить печатать (роллинг, вторая клавиатура). Поэтому перед
        печатью сверяем, что буфер по-прежнему заканчивается теми же клавишами,
        и что другой фикс не исполняется прямо сейчас — иначе отказываемся.
        """
        snapshot = list(keys)
        capture_focus = self._buffer_focus
        capture_owner = self._buffer_owner
        secret = secret or capture_focus is None or capture_focus[-1] is not False \
            or self._secret_focus()
        self._pending += 1

        def run() -> None:
            self._pending -= 1
            ok = False
            outcome: dict = {}
            try:
                if self._focus_waiting:
                    self._focus_wait_boundary = True
                if self.busy:
                    log.info("исправление %s отменено: другое исправление в работе", reason)
                    return
                if whole_phrase and self.buffer.expire(self.clock()):
                    log.info("исправление %s отменено: фраза устарела", reason)
                    return
                if not self._sync_focus() or not self._same_buffer_scope(
                        capture_focus, capture_owner):
                    return
                if whole_phrase:
                    unchanged = self.buffer.phrase() == snapshot
                elif span:
                    # Откат исправления с пробелом внутри («кем бы»): сверяем
                    # хвост фразы целиком, а не последнее слово.
                    expected = snapshot + [SPACE] * tail
                    unchanged = self.buffer.phrase()[-len(expected):] == expected
                else:
                    unchanged = self.buffer.last_word_with_tail() == (snapshot, tail)
                if not unchanged:
                    log.info("исправление %s отменено: буфер изменился", reason)
                    return
                ok = self._apply_fix(snapshot, tail, target_group, text, manual=manual,
                                     reason=reason, group_before=group_before,
                                     whole_phrase=whole_phrase, switch=switch, trigger=trigger,
                                     span=span, caps_off=caps_off, secret=secret, outcome=outcome,
                                     source_text=source_text)
            finally:
                if done is not None:
                    done(ok, outcome.get("secret", secret))
                self._run_pending_gesture()

        self.schedule(run)

    def _run_pending_gesture(self) -> None:
        """Жест, пришедший во время фикса (или пока фикс ждал idle), — на свежем буфере.

        Гвард проверяем до извлечения жеста: если он не пройден (ещё остался
        другой отложенный фикс или конкурентный busy), жест должен остаться
        в self._pending_gesture, а не потеряться.
        """
        if (self._pending_gesture is None or self._pending
                or self.busy or self.paused or self.locked):
            return
        gesture, self._pending_gesture = self._pending_gesture, None
        follow = self._pending_after if gesture == "word" else None
        self._pending_after = None
        if follow is not None:
            # Слово должно сначала выполниться и запомнить выбранную группу;
            # фраза пойдёт из его done/после очереди.
            self._pending_gesture = follow
        if gesture == "word":
            self.manual_word(from_gesture=True)
        else:
            self.manual_phrase(from_gesture=True)
        if self._pending_gesture is not None and not self._pending and not self.busy:
            self._run_pending_gesture()

    def _abort(self) -> bool:
        self.pump()
        return self._abort_requested

    def _stop(self) -> bool:
        return (self._operation_invalidated or self._busy_reset
                or self.screen_locked() or self.locked
                or self._groups() is None
                or not self._sync_focus())

    def _refresh_focus_for_verified(self, expected, timeout: float = 0.6):
        """Свежий снимок фокуса для проверяемой native-замены.

        При входе в редактор ячейки кэш опроса может отставать; без свежего
        поля native-протокол откажется, и слово потеряется. Ждём ограниченно
        и только для неподтверждённой привязки.
        """
        probe = self.probe
        try:
            probe.poke()
        except Exception:  # noqa: BLE001 — опрос не должен ронять ввод
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (self.paused or self.locked or self.screen_locked()
                    or self._abort_requested or self._busy_reset
                    or self._operation_invalidated or self.probe is not probe):
                return None
            try:
                snap = probe.snapshot()
            except Exception:  # noqa: BLE001 — неизвестный снимок не доказывает фокус
                return None
            same = (expected is None
                    or expected.app is None
                    or snap.app == expected.app)
            if expected is not None and expected.window and snap.window:
                same = same and snap.window == expected.window
            if (snap.field is not None
                    and snap.protection(self.clock()) is False and same):
                return snap
            self.pump()
            self.sleep(0.02)
        return None

    def _native_append_count(self) -> int | None:
        if not self.busy or self._busy_reset or self._operation_invalidated:
            return None
        count = 0
        for code, value, _device in self._busy_keys:
            if value not in (0, 1):
                return None
            if code in kc.SHIFT_KEYS:
                continue
            if not (code in kc.WORD_EXTRA_KEYS or code == kc.KEY_SPACE
                    or self.keymap.is_letter_key(code)):
                return None
            count += value
        return count

    def _native_focus_snapshot(self):
        """Живой снимок фокуса для KDE-нативной замены; None — нет доверия.

        Полный обход a11y.find_focus в горячем пути не нужен: опрос фокуса уже
        идёт отдельным потоком и инвалидируется событиями смены фокуса, а
        _stop/_sync_focus отменяют операцию при смене поля. Здесь проверяется
        только то, что снимок свежий, принадлежит тому же полю и вне пароля.
        """
        if (not self.busy or self._busy_reset or self._operation_invalidated
                or self.probe is None or isinstance(self.probe, focus.NullProbe)):
            return None
        snap = self.probe.snapshot()
        if not isinstance(snap, focus.FocusSnapshot):
            return None
        now = self.clock()
        taken_at = snap.taken_at
        if (snap.app is None or snap.field is None or snap.password is not False
                or type(taken_at) not in (int, float) or isinstance(taken_at, bool)
                or not 0 < taken_at <= now
                or snap.age(now) > focus.MAX_AGE_SEC
                or snap.protection(now) is not False):
            return None
        return snap

    def _blocking_keys_held(self, trigger: tuple[int, int] | None) -> bool:
        """Есть ли зажатые клавиши, мешающие печати, кроме самого триггера.

        Триггер не считается: его key-up мог ещё не дойти, и его отдельно ждёт
        `_wait_trigger_release`. Shift блокирует: синтетическое отпускание не
        сбрасывает физически зажатый Shift в libinput, и печать ушла бы в
        верхнем регистре. Ctrl/Alt/Super не считаются: с ними буфер
        сбрасывается ещё в feed().
        """
        held = self.buffer.held_physical()
        if trigger is not None:
            held = held - {trigger}
        return bool({code for _, code in held} - kc.COMMAND_MODIFIERS)

    def _wait_release(self, trigger: tuple[int, int] | None = None) -> bool:
        # Дедлайн — по настоящим часам (time.monotonic), а не по self.clock:
        # self.clock в тестах подменяется управляемым вручную фейком и не тикает
        # сам по себе внутри этого цикла ожидания, поэтому дедлайн на нём
        # никогда не наступил бы. Здесь же мы ждём реальное железо (пока
        # физически отпустят клавиши), так что нужно настоящее время.
        deadline = time.monotonic() + RELEASE_WAIT
        while self._blocking_keys_held(trigger):
            if time.monotonic() > deadline:
                return False
            self.pump()
            self.sleep(0.005)
        return True

    def _wait_trigger_release(self, trigger: tuple[int, int] | None) -> bool:
        """Дождаться key-up клавиши-триггера.

        Пока она зажата, GNOME/libinput сливает синтетический press того же
        кода с физическим: нового логического нажатия нет, и завершающий
        пробел теряется (two_capitals, switch=False). Ждём ограниченно; не
        дождались — отменяем исправление до первой записи.
        """
        if trigger is None:
            return True
        deadline = time.monotonic() + RELEASE_WAIT
        while trigger in self.buffer.held_physical():
            if time.monotonic() > deadline:
                return False
            self.pump()
            self.sleep(0.005)
        return True

    def _apply_fix(self, keys: list[KeyPress], tail: int, target_group: int, text: str,
                   *, manual: bool, reason: str, group_before: int,
                   whole_phrase: bool = False, switch: bool = True,
                   trigger: tuple[int, int] | None = None, span: bool = False,
                   caps_off: bool = False, secret: bool = False,
                   outcome: dict | None = None, source_text: str | None = None) -> bool:
        # Бэкенд может ошибаться насчёт текущей раскладки (IBus на GNOME 46+).
        # Проверяем ДО плана: после Backspace-ов откатывать уже нечего.
        if self._groups() is None or self.backend.current() != group_before:
            return False
        verify = getattr(self.backend, "verify_layout", None)
        if verify is not None and not verify():
            log.error("исправление отменено: бэкенд %s ошибается насчёт раскладки "
                      "(включите расширение GNOME Shell)", getattr(self.backend, "name", "?"))
            return False
        switch = switch or bool(getattr(self.backend, "always_switch", False))
        plan = plan_fix(len(keys) + tail, text, target_group, self.keymap, self.buffer.caps,
                        switch=switch, caps_off=caps_off)
        if plan is None:
            log.warning("не могу набрать текст в группе %s", target_group)
            return False
        # До границы слова человек продолжает печатать: клавиатурная замена не атомарна.
        plan.require_verified = self._provisional_binding or reason in {"early", "url-early"}
        original = (source_text if source_text is not None
                    else self.keymap.decode(keys, group_before))
        if self.dry_run:
            log.info("[dry-run] %s: исправление текста", reason)
            return False
        plan.source_text = original + " " * tail
        plan.focus = self._live_snapshot() if self.probe is not None else None
        secret = secret or self._secret_focus()
        outcome = {} if outcome is None else outcome
        outcome["secret"] = secret
        expired_phrase = False

        def before_write() -> bool:
            # Между решением и первой стирающей клавишей прошли ожидание
            # отпускания и смена раскладки: фраза могла устареть.
            nonlocal expired_phrase
            if expired_phrase:
                return False
            if whole_phrase and (self.buffer.expire(self.clock())
                                 or self.buffer.phrase() != list(keys)):
                expired_phrase = True
                outcome["expired"] = True
                return False
            return True

        self.busy = True
        self._abort_requested = False
        self._operation_invalidated = False
        self._lost_input = False
        self._busy_keys = []
        self._busy_held = self.buffer.held_snapshot()
        self._busy_reset = False
        started = self.clock()
        try:
            if self._provisional_binding and self.probe is not None:
                fresh = self._refresh_focus_for_verified(plan.focus)
                if fresh is not None:
                    plan.focus = fresh
                    owner = self._focus_owner(fresh)
                    # Связка буфера должна указывать на то же поле, иначе _stop()
                    # в native-протоколе увидит «смену поля» и отменит исправление.
                    self._buffer_focus = (self.probe, fresh.generation, fresh.field,
                                          self._scope_app(fresh, owner),
                                          fresh.protection(self.clock()))
                    self._buffer_owner = (owner
                                          if fresh.protection(self.clock()) is False
                                          else None)
                    self._buffer_window = fresh.window
            if (self._abort_requested or self._busy_reset or self._operation_invalidated
                    or self.paused or self.locked or self.screen_locked()
                    or not self._wait_release(trigger)
                    or not self._wait_trigger_release(trigger)):
                log.info("исправление отменено: изменилось состояние ввода")
                result = ABORTED
            else:
                result = execute(plan, self.typist, self.backend, abort=self._abort,
                                 key_delay=self.config.typing.key_delay_ms / 1000.0,
                                 sleep=self.sleep,
                                 restore_group=group_before if switch else None,
                                 hold=self._wait_shift_released, outcome=outcome, stop=self._stop,
                                 native_append=self._native_append_count,
                                 native_focus=self._native_focus_snapshot,
                                 before_write=before_write if whole_phrase else None)
        except Exception as exc:  # noqa: BLE001 — execute сам ловит, это страховка
            log.error("исправление %s: ошибка %s", reason, type(exc).__name__)
            result = "failed"
        finally:
            self.busy = False
        if outcome.get("caps_off"):
            # Caps Lock нажат и не возвращён — выключен, чем бы ни кончилось
            # исправление (светодиод подтвердит событием клавиатуры).
            self.buffer.set_caps(False)
        if not self._sync_focus() or self._operation_invalidated:
            self._busy_reset = True
            result = ABORTED
        lost, self._lost_input = self._lost_input, False
        busy_keys, self._busy_keys = self._busy_keys, []
        interrupted, self._busy_reset = self._busy_reset, False
        native_done = result == DONE and outcome.get("native_span") and not interrupted
        replay = native_done and any(v == 1 for _, v, _ in busy_keys) and all(
            v == 0 or v == 1 and (
                c in (kc.WORD_EXTRA_KEYS | {kc.KEY_SPACE} | (kc.MODIFIER_KEYS - kc.META_KEYS))
                or self.keymap.is_letter_key(c)) for c, v, _ in busy_keys)
        if native_done and any(v == 2 for _, v, _ in busy_keys):
            lost = True
        if replay:
            if lost:
                self._clear_context()
            lost = False
        if result == ABORTED and not interrupted:
            if self._provisional_binding:
                # Буфер собран при неподтверждённой привязке. Если слово уже
                # закончилось пробелом (tail), пропускать следующее нельзя:
                # его наберут в подтверждённом поле. Для незаконченного слова
                # (жест без пробела) безопаснее дождаться границы.
                if tail == 0:
                    self._skip_word = True
                self._provisional_binding = False
                self.buffer.reset()
                self._clear_context()
                self._last_fix = None
            # Экран не тронут: на нём прежнее слово и то, что человек
            # допечатал. Досылаем эти нажатия в буфер с тем состоянием
            # зажатых клавиш, что было на входе, — как будто исправления не было.
            self.buffer.restore_held(self._busy_held)
            for code, value, device_id in busy_keys:
                self.buffer.feed(code, value, device_id, self.clock())
            log.info("исправление %s: %s", reason, result)
            self.gesture.reset()
            self._pending_gesture = None
            self._pending_after = None
            if not outcome.get("expired"):
                self._emit_status()
            return False
        if lost:
            self._last_fix = None
            if not interrupted and _word_unfinished(busy_keys):
                # Буква легла посреди перепечатки: на экране она есть, в
                # буфере — нет. Хвост этого слова не трогаем до пробела.
                self._skip_word = True
        if result != DONE:
            log.info("исправление %s: %s", reason, result)
            self.buffer.reset()
            self._clear_context()
            # Прервано физическим вводом — начатая серия Shift и отложенный
            # жест больше не имеют смысла (буфер пуст, жест дал бы лишний
            # переворот раскладки).
            self.gesture.reset()
            self._pending_gesture = None
            self._pending_after = None
            self._word_gesture = None
            self._emit_status()
            return False
        log.info("%s: исправлено за %.0f мс", reason, (self.clock() - started) * 1000)
        if "target_text" in outcome:
            text = outcome["target_text"]
            original = outcome["source_text"][:-tail] if tail else outcome["source_text"]
            actual_keys = keys_for_typing(original, group_before, self.keymap, False)
            if actual_keys is not None:
                keys = [KeyPress(code, shift) for code, shift in actual_keys]
        secret = secret or self._secret_focus()
        outcome["secret"] = secret
        new_keys = [KeyPress(code, shift) for code, shift in
                    keys_for_typing(text, target_group, self.keymap, False) or []]
        if whole_phrase:
            self.buffer.replace_all(new_keys)
        elif span:
            self.buffer.replace_tail(len(keys) + tail, new_keys)
        else:
            self.buffer.replace_last_word(new_keys[: len(new_keys) - tail] if tail else new_keys)
        word_keys = new_keys[: len(new_keys) - tail] if tail else new_keys
        fixed_end = len(self.buffer.phrase()) - tail
        if lost:
            self.buffer.reset()
            self._clear_context()
        if manual:
            self.stats["manual"] += 1
            if reason != "undo":
                self._last_fix = None
        else:
            self.stats["corrections"] += 1
            if not lost:
                self._last_fix = LastFix(list(keys), word_keys, group_before, self.clock(),
                                         end=fixed_end,
                                         early=reason == "early", secret=secret,
                                         original_text=original)
        if replay:
            # Текст уже в приложении; восстанавливаем только учёт нажатий и отмену.
            self.buffer.restore_held(self._busy_held)
            for code, value, device_id in busy_keys:
                event = self.buffer.feed(code, value, device_id, self.clock())
                if event is not None and event.kind == "word":
                    self._remember_fix_word(event.keys)
        if self.on_fixed:
            self.on_fixed(manual)
        if self.on_corrected and not secret and not self._secret_focus():
            # Из поля пароля текст наружу (D-Bus, окно) не отдаём.
            self.on_corrected(original, text.rstrip(), manual)
        self._emit_status()
        return True
