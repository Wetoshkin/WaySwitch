"""Оркестрация без привязки к GLib: события клавиш → решения → действия.

Контроллер ничего не знает о evdev, D-Bus и главном цикле. Всё внешнее
приходит через вызовы on_*, всё наружу — через typist/backend и колбэки.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from wayswitch import a11y as focus
from wayswitch import keycodes as kc
from wayswitch.actuator import (
    ABORTED,
    DONE,
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
from wayswitch.gesture import ShiftGesture, TapKeyGesture
from wayswitch.selection import convert as convert_selection
from wayswitch.textops import number_words, transliterate

log = logging.getLogger("wayswitch")


_UNSET = object()  # «кэш ещё не заполнялся»: None значило бы «файла нет»
RELEASE_WAIT = 0.3  # сколько ждать отпускания физических клавиш перед печатью
SELECTION_RELEASE_WAIT = 2.0  # «Исправить выделенное» с сочетания: ждать отпускания
SELECTION_MAX = 300  # больше — это уже не опечатка во фразе


def _word_unfinished(events: list[tuple[int, int, int]]) -> bool:
    """Последнее нажатие во время исправления — буква (слово ещё не закончено)."""
    for code, value, _dev in reversed(events):
        if value != 1 or code in kc.MODIFIER_KEYS or code in kc.LOCK_KEYS:
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
    # Для раннего триггера — всё слово (буквы в исходной раскладке), когда
    # его дописали: по нему потом сбрасывается счётчик отмен.
    word: str = ""


class Controller:
    def __init__(self, config: Config, keymap, detector: Detector, backend, typist, *,
                 clock: Callable[[], float] = time.monotonic,
                 pump: Callable[[], None] = lambda: None,
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
        self.dry_run = dry_run
        self.on_corrected = on_corrected
        # Исправление выполнено (без текста): звук — и для поля пароля тоже.
        self.on_fixed = on_fixed
        self.on_status = on_status
        self.persist_exception = persist_exception
        # Сведения о фокусе (приложение, поле пароля) — из at-spi, через снимок
        # в отдельном потоке: в пути ввода опрашивать доступность нельзя.
        self.probe = probe
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
        self.paused = False
        self.locked = False
        self.busy = False
        self._abort_requested = False
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
        self._last_fix: LastFix | None = None
        # Сколько раз подряд отменяли исправление слова: запоминаем слово как
        # исключение только с learn_after_undos-й отмены.
        self._undo_counts: dict[str, int] = {}
        # Время последней правки текста руками и последней ручной смены
        # раскладки: в это окно автоисправление не вмешивается.
        self._last_edit_at: float | None = None
        self._last_external_layout_at: float | None = None
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
            "busy": self.busy,
            "backend": getattr(self.backend, "name", "?"),
            "layout_index": self.backend.current(),
            "layout": self._layout_alphabet(),
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
            return self.keymap.alphabet(current)
        except (IndexError, ValueError):
            return None

    def _emit_status(self) -> None:
        if self.on_status:
            self.on_status(self.status())

    def pause(self) -> None:
        self.paused = True
        if self.busy:
            self._abort_requested = True  # пауза посреди фикса — не дописывать слово
            self._busy_reset = True
        self._skip_word = False
        self.buffer.reset()
        self._emit_status()

    def resume(self) -> None:
        self.paused = False
        self._emit_status()

    def set_locked(self, on: bool) -> None:
        self.locked = on
        if on and self.busy:
            self._abort_requested = True  # ничего не печатать на экране блокировки
        if self.busy:
            self._busy_reset = True
        self._skip_word = False
        self.buffer.reset()
        self.context.clear()
        self._emit_status()

    def set_caps(self, on: bool) -> None:
        self.buffer.set_caps(on)

    # --- входящие события -------------------------------------------------------

    def on_key(self, code: int, value: int, device_id: int) -> None:
        now = self.clock()
        if value == 1 and code in kc.EDIT_KEYS:
            self._last_edit_at = now
        if value == 1 and self.probe is not None \
                and not (self.busy or self.paused or self.locked):
            # Будим опрос фокуса с первой буквы: после простоя поток спит, и
            # к пробелу снимок поля пароля должен быть уже свежим.
            self.probe.poke()
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
                self._pending_gesture = gesture
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
        event = self.buffer.feed(code, value, device_id, now)
        if value == 1 and self._pause_key is not None and code == self._pause_key:
            (self.resume if self.paused else self.pause)()
            return
        if self.paused or self.locked:
            # Пауза/блокировка выключают и ручные жесты — только сам хоткей
            # паузы (обработан выше) должен продолжать работать.
            return
        if value == 1 and self.config.gesture.manual == "pause_key" and code == kc.KEY_PAUSE:
            gesture = "word"
        if gesture:
            self._dispatch_gesture(gesture)
            return
        if event is None:
            return
        if event.kind == "reset":
            self.context.clear()
            self._skip_word = False
            self._hold_keys = None
            return
        if event.reset_before:
            self.context.clear()  # буфер очищен по таймауту перед этой клавишей
            self._skip_word = False
        if self._skip_word:
            # Хвост слова, начало которого напечатано во время исправления.
            if event.kind == "word":
                self._skip_word = False
                self.buffer.reset()
                self.context.clear()
            return
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
                self.context.clear()
        elif value == 0:
            self.buffer.note_release(code, device_id)

    def _dispatch_gesture(self, gesture: str) -> None:
        try:
            self._dispatch_gesture_unsafe(gesture)
        except Exception as exc:  # noqa: BLE001 — бэкенд/D-Bus: не ронять чтение клавиатуры
            log.error("ручное исправление не выполнено: %s", exc)
            self.busy = False

    def _dispatch_gesture_unsafe(self, gesture: str) -> None:
        if self._pending:
            # Фикс уже запланирован, но ещё не исполнен (третий тап пришёл
            # раньше idle): жест обработаем после него, на обновлённом буфере.
            self._pending_gesture = gesture
        elif gesture == "word":
            self.manual_word()
        else:
            self.manual_phrase()

    def on_click(self) -> None:
        if self.busy:
            self._abort_requested = True  # клик — физический ввод, как и нажатие клавиши
            self._busy_reset = True
            return
        self.buffer.click()
        self.context.clear()
        self._skip_word = False
        self._hold_keys = None
        # Клик посреди тапа жеста — не жест: иначе Ctrl+клик переворачивал бы слово.
        self.gesture.reset()
        for g, _action in self._selection_gestures:
            g.reset()

    def on_scroll(self) -> None:
        """Колесо мыши: слово не трогаем, но тап-жест разрываем (Ctrl+колесо — масштаб)."""
        self.gesture.reset()
        for g, _action in self._selection_gestures:
            g.reset()

    def on_layout_changed(self, index: int, external: bool) -> None:
        if external:
            self.buffer.reset()
            self.context.clear()
            self._skip_word = False
            self._last_external_layout_at = self.clock()
        self._emit_status()

    def on_resync(self, device_id: int, held) -> None:
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
            return False
        for opt in options:
            o = opt.lower()
            if o.startswith("caps:") or ("caps" in o and o.split(":")[0] in
                                         ("grp", "grp_led", "ctrl", "lv3", "lv5", "shift")):
                return True
        return False

    def _auto_allowed(self) -> bool:
        return (self.config.general.auto_correct and not self.paused and not self.locked
                and getattr(self.backend, "supports_auto", False))

    def _groups(self) -> tuple[int, int] | None:
        current = self.backend.current()
        if current is None or len(self.backend.layouts()) != 2:
            return None
        return current, 1 - current

    def _context_tuple(self) -> tuple[str | None, str | None]:
        """`self.context` — deque(maxlen=2), но в начале сессии/фразы в нём
        может быть 0 или 1 элемент; Detector.decide() ожидает ровно 2-tuple
        (читает context[-1]/context[-2]) — дополняем слева None."""
        return (None,) * (2 - len(self.context)) + tuple(self.context)

    def set_probe(self, probe) -> None:
        """Подменить источник сведений о фокусе (после смены настроек).

        Старую пробу закрываем без ожидания потока: Reload приходит и посреди
        исправления (через pump), ждать там секунду нельзя.
        """
        old, self.probe = self.probe, probe
        close = getattr(old, "close", None)
        if close is not None:
            close()

    def _password_focus(self) -> bool:
        """Свежий снимок говорит: курсор в поле пароля."""
        if self.probe is None:
            return False
        snap = self.probe.snapshot()
        return snap.password and snap.age(self.clock()) <= focus.MAX_AGE_SEC

    def _secret_focus(self) -> bool:
        """Текст исправления нельзя отдавать наружу (D-Bus, окно, журнал).

        Поле пароля — или неизвестно, не оно ли: опрос at-spi есть, но снимок
        устарел (поток только проснулся после простоя).
        """
        if self.probe is None or isinstance(self.probe, focus.NullProbe):
            return False
        snap = self.probe.snapshot()
        return snap.password or snap.age(self.clock()) > focus.MAX_AGE_SEC

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
        if self.probe is None:
            return None
        return focus.skip_reason(self.probe, self._apps(),
                                 self.config.general.skip_password_fields, self.clock())

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

    def _on_word(self, keys: list[KeyPress], device_id: int, code: int) -> None:
        held, self._hold_keys = self._hold_keys, None
        if held is not None and list(keys) == held:
            # То самое слово, которое вернули жестом: не исправлять снова.
            self.context.append(None)
            return
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
        groups = self._groups()
        if groups is None:
            return
        current, other = groups
        if self.probe is not None:
            self.probe.poke()  # пусть свежий снимок фокуса будет уже к делу
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
                               trigger=(device_id, code))
            return
        split = self.detector.split_words_fix(keys, current) \
            if self.config.general.split_words_fix else None
        if split:
            self._schedule_fix(keys, 1, current, split + " ", manual=False,
                               reason="split", group_before=current, switch=False,
                               trigger=(device_id, code))
            return
        particles = self.detector.particles_fix(keys, current) \
            if self.config.general.particles_fix else None
        if particles:
            self._schedule_fix(keys, 1, current, particles + " ", manual=False,
                               reason="particles", group_before=current, switch=False,
                               trigger=(device_id, code))
            return
        caps = self.detector.caps_lock_fix(keys, current, other) \
            if self.config.general.caps_fix and self.buffer.caps \
            and not self._caps_remapped() else None
        if caps:
            text, group = caps
            self._schedule_fix(keys, 1, group, text + " ", manual=False, reason="caps-lock",
                               group_before=current, switch=group != current,
                               trigger=(device_id, code), caps_off=True)
            return
        capitals = self.detector.two_capitals_fix(keys, current) \
            if self.config.general.two_capitals_fix and not self.buffer.caps else None
        if capitals:
            self._schedule_fix(keys, 1, current, capitals + " ", manual=False,
                               reason="capitals", group_before=current, switch=False,
                               trigger=(device_id, code))
            return
        decision = self.detector.decide(keys, current, other, self._context_tuple())
        log.debug("слово %r → %s (%s)", decision.a.text, decision.action, decision.reason)
        if decision.action == "fix":
            lang = decision.b.lang

            def done(ok: bool) -> None:
                if ok:
                    self.context.append(lang)

            self._schedule_fix(keys, 1, other, decision.target_text + " ", manual=False,
                               reason=decision.reason, group_before=current,
                               trigger=(device_id, code), done=done)
            return
        self.context.append(decision.a.lang if decision.a.in_dict else None)

    def _on_letter(self, keys: list[KeyPress], device_id: int, code: int) -> None:
        groups = self._groups()
        if groups is None:
            return
        current, other = groups
        if self.probe is not None:
            self.probe.poke()
        if self._edit_guard_active():
            return
        if self._focus_blocks():
            return
        target = self.detector.early_url_target(keys, current, other)
        if target:
            self._schedule_fix(keys, 0, other, target, manual=False, reason="url-early",
                               group_before=current, trigger=(device_id, code))
            return
        if not self.config.general.early_switch or self.config.general.sensitivity == "careful":
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
        return not (self.busy or self.paused or self.locked)

    def manual_word(self) -> bool:
        """Исправить последнее слово (или откатить, или просто переключить раскладку).

        True — исправление запланировано/выполнено, False — нечего делать."""
        if not self._manual_allowed():
            return False
        groups = self._groups()
        if groups is None:
            return False
        current, other = groups
        # Без feed() (жест «Нажатие клавиши», вызов по D-Bus) таймаут фразы
        # иначе не проверяется: через час простоя перевернулось бы старое слово.
        self.buffer.expire(self.clock())
        word, tail = self.buffer.last_word_with_tail()
        if word and self._skip_word:
            # Слово на экране длиннее, чем в буфере: перевернули бы не всё.
            log.info("ручное исправление пропущено: слово набрано во время исправления")
            return False
        if not word:
            if not self.dry_run:
                self.busy = True
                try:
                    self.backend.set(other)
                    self.backend.wait_applied(other, 0.5)
                finally:
                    self.busy = False
            self._emit_status()
            return True
        last = self._last_fix
        undo_keys = self._undo_span(last)
        if undo_keys is not None:
            original = self.keymap.decode(last.original_keys, last.group_before)

            def done(ok: bool) -> None:
                if not ok:
                    return
                self._last_fix = None
                self.stats["undo"] += 1
                letters = "".join(c for c in original if c.isalpha() or c in "-'").lower()
                if letters:
                    self._note_undo(letters)
                self._emit_status()

            self._schedule_fix(undo_keys, tail, last.group_before, original + " " * tail,
                               manual=True, reason="undo", group_before=current, done=done,
                               span=True)
            return True
        text = self.keymap.decode(word, other)
        done = None
        start = len(self.buffer.phrase()) - tail - len(word)
        if (last is not None and last.early and other == last.group_before
                and self.clock() - last.time <= self.config.general.undo_window_sec
                and start == last.end - len(last.target_keys)
                and word[:len(last.target_keys)] == last.target_keys):
            # Слово начали, WaySwitch переключил раскладку по началу, человек
            # дописал и вернул всё слово назад — это отмена раннего триггера.
            letters = "".join(c for c in text if c.isalpha() or c in "-'").lower()

            def done(ok: bool, before_space: bool = tail == 0) -> None:
                if ok and letters:
                    self._last_fix = None
                    self.stats["undo"] += 1
                    self._note_undo(letters)
                    if before_space:
                        # Запоминаем само слово: пропустим на пробеле только
                        # его, а не любое следующее.
                        self._hold_keys = list(self.buffer.last_word_with_tail()[0])

        self._schedule_fix(word, tail, other, text + " " * tail, manual=True,
                           reason="manual", group_before=current, done=done)
        return True

    def _note_undo(self, word: str) -> None:
        """Исправление слова отменили: на learn_after_undos-й раз — запомнить.

        Одна отмена бывает случайной (слово исправлено верно, но человек
        передумал), поэтому сразу в исключения не пишем.
        """
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
            return convert_selection(self.keymap, text, (0, 1))
        new_text = transliterate(text) if action == "translit" else number_words(text)
        if new_text is None:
            return None
        target = self._group_for_text(new_text, groups)
        return (new_text, target) if target is not None else None

    def transform_selection(self, action: str, text: str | None = None) -> bool:
        """Действие над выделенным текстом: «layout» — другая раскладка,
        «translit» — транслитерация, «numbers» — число прописью.

        Текст берётся у бэкенда (расширение GNOME Shell читает PRIMARY —
        выделение без Ctrl+C) и печатается поверх выделения: в любом поле ввода
        набор заменяет выделенный текст, Backspace не нужен — значит, и стереть
        лишнее нечем.
        """
        if not self._manual_allowed():
            return False
        if text is None:
            getter = getattr(self.backend, "get_selection", None)
            try:
                text = getter() if getter is not None else None
            except Exception as exc:  # noqa: BLE001 — бэкенд/расширение не ответили
                log.warning("выделение не прочитать: %s", exc)
                return False
        if not text:
            log.info("исправить выделенное: ничего не выделено")
            return False
        if "\n" in text or "\r" in text or len(text) > SELECTION_MAX:
            # Перевод строки ушёл бы клавишей Enter — в мессенджере это
            # «отправить» посреди текста. Длинный текст печатался бы минутами.
            log.info("исправить выделенное: выделено больше одной строки или "
                     "больше %d символов", SELECTION_MAX)
            return False
        groups = self._groups()
        if groups is None:
            return False
        converted = self._convert_selection(action, text, groups)
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
        secret = self._secret_focus()
        # busy — до ожидания: пока ждём отпускания, pump() доставляет и
        # повторный вызов (нажали сочетание дважды), он напечатал бы ещё раз.
        self.busy = True
        try:
            # Вызов с системного сочетания (Ctrl+Alt+…) приходит, пока клавиши
            # ещё зажаты: буквы превратились бы в команды. Ждём отпускания всех.
            if not self._wait_all_released(SELECTION_RELEASE_WAIT):
                log.info("исправить выделенное: клавиши сочетания не отпущены")
                return False
            # Прерывать нечего: стирающих шагов нет, а наполовину набранная
            # фраза хуже целой.
            result = execute(Plan(steps), self.typist, self.backend,
                             key_delay=self.config.typing.key_delay_ms / 1000.0,
                             sleep=self.sleep, restore_group=groups[0],
                             hold=self._wait_shift_released)
        finally:
            self.busy = False
            self._lost_input = False
            self._busy_keys = []
            self._busy_reset = False
        self.buffer.reset()
        self.context.clear()
        if result != DONE:
            log.info("исправить выделенное: %s", result)
            self._emit_status()
            return False
        self.stats["manual"] += 1
        self._last_fix = None
        if self.on_fixed:
            self.on_fixed(True)
        if self.on_corrected and not secret:
            # Наружу — только начало: сигнал видят все программы сеанса.
            self.on_corrected(_clip(text), _clip(new_text), True)
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

    def manual_phrase(self) -> bool:
        if not self._manual_allowed() or self._skip_word:
            return False
        current = self.backend.current()
        keys = self.buffer.phrase()
        if current is None or not keys:
            return False
        # Раскладка уже переключена вторым Shift; декодируем всё в текущей группе.
        text = self.keymap.decode(keys, current)
        self._schedule_fix(keys, 0, current, text, manual=True, reason="phrase",
                           group_before=current, whole_phrase=True, switch=False)
        return True

    # --- исполнение -------------------------------------------------------------

    def _schedule_fix(self, keys: list[KeyPress], tail: int, target_group: int, text: str,
                      *, manual: bool, reason: str, group_before: int,
                      whole_phrase: bool = False, switch: bool = True,
                      trigger: tuple[int, int] | None = None,
                      done: Callable[[bool], None] | None = None,
                      span: bool = False, caps_off: bool = False) -> None:
        """Отложить _apply_fix через self.schedule.

        Решение принято по снимку буфера; к моменту исполнения пользователь
        мог продолжить печатать (роллинг, вторая клавиатура). Поэтому перед
        печатью сверяем, что буфер по-прежнему заканчивается теми же клавишами,
        и что другой фикс не исполняется прямо сейчас — иначе отказываемся.
        """
        snapshot = list(keys)
        self._pending += 1

        def run() -> None:
            self._pending -= 1
            ok = False
            try:
                if self.busy:
                    log.info("исправление %s отменено: другое исправление в работе", reason)
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
                                     span=span, caps_off=caps_off)
            finally:
                if done is not None:
                    done(ok)
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
        (self.manual_word if gesture == "word" else self.manual_phrase)()

    def _abort(self) -> bool:
        self.pump()
        return self._abort_requested

    def _blocking_keys_held(self, trigger: tuple[int, int] | None) -> bool:
        """Есть ли физически зажатые клавиши, мешающие печати.

        Клавиша-триггер (`trigger`) сюда не считается: buffer.feed() кладёт её
        в _held ещё до генерации события «слово»/«буква», а её отпускание могло
        ещё не дойти до нас (фикс исполняется в ближайшем idle после нажатия),
        так что оно не «зависшее», а просто ещё не доставлено. Любая ДРУГАЯ
        зажатая клавиша (с другого устройства или нажатая раньше) блокирует
        печать. Shift тоже блокирует: синтетическое отпускание из
        ReleaseModifiers не сбрасывает физически зажатый Shift в libinput
        (release с чужого устройства отбрасывается), и перепечатка ушла бы в
        верхнем регистре; Shift отпускают за миллисекунды, а жест и так
        срабатывает на отпускании. Ctrl/Alt/Super не считаются: с ними буфер
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

    def _apply_fix(self, keys: list[KeyPress], tail: int, target_group: int, text: str,
                   *, manual: bool, reason: str, group_before: int,
                   whole_phrase: bool = False, switch: bool = True,
                   trigger: tuple[int, int] | None = None, span: bool = False,
                   caps_off: bool = False) -> bool:
        # Бэкенд может ошибаться насчёт текущей раскладки (IBus на GNOME 46+).
        # Проверяем ДО плана: после Backspace-ов откатывать уже нечего.
        verify = getattr(self.backend, "verify_layout", None)
        if verify is not None and not verify():
            log.error("исправление отменено: бэкенд %s ошибается насчёт раскладки "
                      "(включите расширение GNOME Shell)", getattr(self.backend, "name", "?"))
            return False
        switch = switch or bool(getattr(self.backend, "always_switch", False))
        plan = plan_fix(len(keys) + tail, text, target_group, self.keymap, self.buffer.caps,
                        switch=switch, caps_off=caps_off)
        if plan is None:
            log.warning("не могу набрать %r в группе %s", text, target_group)
            return False
        original = self.keymap.decode(keys, group_before)
        if self.dry_run:
            log.info("[dry-run] %s: %r → %r", reason, original, text)
            return False
        secret = self._secret_focus()
        self.busy = True
        self._abort_requested = False
        self._lost_input = False
        self._busy_keys = []
        self._busy_held = self.buffer.held_snapshot()
        self._busy_reset = False
        outcome: dict = {}
        started = self.clock()
        try:
            if not self._wait_release(trigger):
                log.info("исправление отменено: зажата физическая клавиша")
                result = ABORTED
            else:
                result = execute(plan, self.typist, self.backend, abort=self._abort,
                                 key_delay=self.config.typing.key_delay_ms / 1000.0,
                                 sleep=self.sleep,
                                 restore_group=group_before if switch else None,
                                 hold=self._wait_shift_released, outcome=outcome)
        except Exception as exc:  # noqa: BLE001 — execute сам ловит, это страховка
            log.error("исправление %s: ошибка %s", reason, exc)
            result = "failed"
        finally:
            self.busy = False
        if outcome.get("caps_off"):
            # Caps Lock нажат и не возвращён — выключен, чем бы ни кончилось
            # исправление (светодиод подтвердит событием клавиатуры).
            self.buffer.set_caps(False)
        lost, self._lost_input = self._lost_input, False
        busy_keys, self._busy_keys = self._busy_keys, []
        interrupted, self._busy_reset = self._busy_reset, False
        if result == ABORTED and not interrupted:
            # Экран не тронут: на нём прежнее слово и то, что человек
            # допечатал. Досылаем эти нажатия в буфер с тем состоянием
            # зажатых клавиш, что было на входе, — как будто исправления не было.
            self.buffer.restore_held(self._busy_held)
            for code, value, device_id in busy_keys:
                self.buffer.feed(code, value, device_id, self.clock())
            log.info("исправление %s: %s", reason, result)
            self.gesture.reset()
            self._pending_gesture = None
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
            self.context.clear()
            # Прервано физическим вводом — начатая серия Shift и отложенный
            # жест больше не имеют смысла (буфер пуст, жест дал бы лишний
            # переворот раскладки).
            self.gesture.reset()
            self._pending_gesture = None
            self._emit_status()
            return False
        # Сам текст — только в отладочный журнал: в обычном журнале (journald
        # хранит его неделями) исправленным словам делать нечего. text — через
        # %r как есть: по нему видно, ушёл ли пробел в конце.
        log.info("%s: исправлено за %.0f мс", reason, (self.clock() - started) * 1000)
        if not secret:
            log.debug("%s: %r → %r", reason, original, text)
        new_keys = [KeyPress(code, shift) for code, shift in
                    keys_for_typing(text, target_group, self.keymap, False) or []]
        if whole_phrase:
            self.buffer.replace_all(new_keys)
        elif span:
            self.buffer.replace_tail(len(keys) + tail, new_keys)
        else:
            self.buffer.replace_last_word(new_keys[: len(new_keys) - tail] if tail else new_keys)
        word_keys = new_keys[: len(new_keys) - tail] if tail else new_keys
        if lost:
            self.buffer.reset()
            self.context.clear()
        if manual:
            self.stats["manual"] += 1
            if reason != "undo":
                self._last_fix = None
        else:
            self.stats["corrections"] += 1
            if not lost:
                self._last_fix = LastFix(list(keys), word_keys, group_before, self.clock(),
                                         end=len(self.buffer.phrase()) - tail,
                                         early=reason == "early")
        if self.on_fixed:
            self.on_fixed(manual)
        if self.on_corrected and not secret:
            # Из поля пароля текст наружу (D-Bus, окно) не отдаём.
            self.on_corrected(original, text.rstrip(), manual)
        self._emit_status()
        return True
