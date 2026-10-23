"""Что сейчас в фокусе: приложение и поле пароля — через at-spi.

Зачем это нужно. Две самые частые боли пользователей Punto Switcher — «лезет
не в те программы» и «мешает вводить пароль». На Wayland у приложения нельзя
спросить композитор «какое окно активно», зато можно спросить мост
доступности (at-spi): он есть и в GNOME, и в KDE, и в любом стеке на GTK/Qt.

Как устроено. Опрос at-spi — это десятки D-Bus-вызовов, и делать их в пути
ввода нельзя: пока идёт опрос, события клавиатуры стоят в очереди. Поэтому
опрос живёт в отдельном потоке и раз в POLL_SEC обновляет снимок, а
controller читает этот снимок мгновенно. Данные о фокусе устаревают медленно
(окно и поле не меняются внутри слова), поэтому MAX_AGE_SEC = 3 с — разумный
компромисс между свежестью и отсутствием подвисаний.
"""

from __future__ import annotations

import threading
import time
from typing import NamedTuple, Protocol

# Бит 12 в маске состояний at-spi: STATE_FOCUSED.
FOCUSED = 12

# Снимок старше этого возраста считается недостоверным: лучше не мешать
# человеку (не исправлять), чем мешать по устаревшим данным.
MAX_AGE_SEC = 3.0

# Как часто поток обновляет снимок. Чаще нет смысла: фокус не скачет,
# а каждый опрос — это обход дерева доступности.
POLL_SEC = 1.5

# Пределы обхода: в браузере дерево доступности бывает на тысячи узлов,
# а поле ввода почти всегда не глубже десятка уровней.
MAX_NODES = 400
MAX_DEPTH = 12

# Столько секунд без ввода — и поток опроса останавливается, чтобы не крутить
# обход дерева доступности впустую. Разбудит его следующий poke().
IDLE_STOP_SEC = 30.0

# Роли at-spi, которыми обозначают поля ввода пароля.
PASSWORD_ROLES = frozenset({"password text"})

# Оболочки и оконные менеджеры: их узлы at-spi тоже бывают «в фокусе» (панель,
# обзор), но печатает человек не в них. Иначе при любом окне на экране
# активной программой оказывалась бы «gnome-shell».
SHELL_APPS = frozenset({"gnome-shell", "plasmashell", "kwin", "kwin_wayland", "mutter"})


class FocusSnapshot(NamedTuple):
    """Состояние фокуса на момент taken_at."""

    app: str | None = None
    password: bool = False
    taken_at: float = 0.0

    def age(self, now: float) -> float:
        return max(0.0, now - self.taken_at)


class Probe(Protocol):
    """Источник сведений о фокусе."""

    def snapshot(self) -> FocusSnapshot: ...

    def poke(self) -> None:
        """Попросить обновить снимок пораньше (не блокирует)."""
        ...


class NullProbe:
    """Заглушка: сведений о фокусе нет (at-spi недоступен, чужая среда)."""

    def snapshot(self) -> FocusSnapshot:
        return FocusSnapshot()

    def poke(self) -> None:
        return None


def app_matches(app: str | None, patterns: list[str] | tuple[str, ...]) -> bool:
    """Подходит ли приложение под список исключений.

    Сравнение по вхождению без регистра: в списке пишут «telegram», а at-spi
    отдаёт «TelegramDesktop» или «org.telegram.desktop».
    """
    if not app:
        return False
    name = app.lower()
    return any(p.strip().lower() in name for p in patterns if p.strip())


def skip_reason(probe: Probe, excluded: list[str] | tuple[str, ...],
                skip_password: bool, now: float) -> str | None:
    """Почему автоисправление сейчас трогать нельзя (или None).

    Возвращает короткую причину для журнала: «поле пароля» или
    «приложение telegram».
    """
    snap = probe.snapshot()
    if snap.age(now) > MAX_AGE_SEC:
        # Данных нет или они протухли: не выдумываем запрет.
        return None
    if skip_password and snap.password:
        return "поле пароля"
    if app_matches(snap.app, excluded):
        return f"приложение {snap.app}"
    return None


class AtspiProbe:
    """Настоящий опрос at-spi в отдельном потоке.

    Поток стартует лениво (при первом poke), чтобы демон не занимался
    доступностью, пока автоисправление выключено.
    """

    def __init__(self, poll_sec: float = POLL_SEC, clock=time.monotonic,
                 query=None) -> None:
        self._poll = poll_sec
        self._clock = clock
        # query() -> (имя приложения, признак поля пароля); подменяется в тестах.
        self._query = query or query_focus
        self._lock = threading.Lock()
        self._snap = FocusSnapshot()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._ok = True

    # --- публичное ----------------------------------------------------------

    def snapshot(self) -> FocusSnapshot:
        with self._lock:
            return self._snap

    def poke(self) -> None:
        self._ensure_thread()
        self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def healthy(self) -> bool:
        """Удался ли последний опрос (для doctor и страницы статуса)."""
        return self._ok

    # --- поток --------------------------------------------------------------

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="atspi-probe", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        last_poke = self._clock()
        while not self._stop.is_set():
            self._refresh()
            # Ждём либо таймаут, либо «опроси пораньше» от контроллера.
            if self._wake.wait(self._poll):
                self._wake.clear()
                last_poke = self._clock()
            elif self._clock() - last_poke > IDLE_STOP_SEC:
                # Ввода давно нет — гасим поток; следующий poke() поднимет снова.
                with self._lock:
                    self._snap = FocusSnapshot(None, False, self._clock())
                return

    def _refresh(self) -> None:
        try:
            app, password = self._query()
        except Exception as exc:  # at-spi может отсутствовать целиком
            self._ok = False
            with self._lock:
                self._snap = FocusSnapshot(None, False, self._clock())
            del exc
            return
        self._ok = True
        with self._lock:
            self._snap = FocusSnapshot(app, password, self._clock())


# --- настоящий опрос --------------------------------------------------------

ACC = "org.a11y.atspi.Accessible"


def _state_focused(words: list[int]) -> bool:
    """Разобрать маску состояний at-spi: бит FOCUSED."""
    if not words:
        return False
    return bool(words[0] >> FOCUSED & 1)


def query_focus() -> tuple[str | None, bool]:
    """Найти приложение в фокусе и признак поля пароля.

    Обход ограничен по числу узлов и глубине: нам нужно не всё дерево, а
    первый сфокусированный узел. Сфокусированных узлов в дереве ровно один,
    поэтому обход заканчивается, как только он найден.
    """
    from gi.repository import Gio, GLib  # локально: без GI модуль импортируется

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    res = bus.call_sync("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress",
                        None, GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 1500, None)
    conn = Gio.DBusConnection.new_for_address_sync(
        res.unpack()[0],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)

    def call(name: str, path: str, iface: str, method: str, arg, sig: str):
        try:
            return conn.call_sync(name, path, iface, method, arg, GLib.VariantType(sig),
                                  Gio.DBusCallFlags.NONE, 400, None).unpack()
        except GLib.Error:
            return None

    def label(name: str, path: str) -> str:
        got = call(name, path, "org.freedesktop.DBus.Properties", "Get",
                   GLib.Variant("(ss)", (ACC, "Name")), "(v)")
        return str(got[0]) if got else ""

    root = ("org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root")
    app_name: str | None = None
    password = False
    queue: list[tuple[str, str, int]] = [(root[0], root[1], 0)]
    seen: set[tuple[str, str]] = set()
    visited = 0
    while queue and visited < MAX_NODES:
        name, path, depth = queue.pop(0)
        if (name, path) in seen:
            continue
        seen.add((name, path))
        visited += 1

        role = call(name, path, ACC, "GetRoleName", None, "(s)")
        role = role[0] if role else ""

        state = call(name, path, ACC, "GetState", None, "(au)")
        if _state_focused(state[0] if state else []):
            # Нашли то, что в фокусе. Узнаём его приложение: если это сама
            # оболочка (панель, обзор), идём дальше — человек печатает не в ней.
            owner = None
            parent = call(name, path, ACC, "GetApplication", None, "(so)")
            if parent and parent[0]:
                owner = label(parent[0], parent[1]) or None
            if owner and owner.lower() in SHELL_APPS:
                continue
            password = role in PASSWORD_ROLES
            app_name = owner
            break

        if depth >= MAX_DEPTH:
            continue
        kids = call(name, path, ACC, "GetChildren", None, "(a(so))")
        for child_name, child_path in (kids[0] if kids else []):
            if child_name and child_path:
                queue.append((child_name, child_path, depth + 1))
    return app_name, password


def bridge_available() -> tuple[bool, str]:
    """Отвечает ли мост доступности — без требования, чтобы что-то было в фокусе.

    Проверять «есть ли приложение в фокусе» нельзя: когда ни одно окно не
    активно (сразу после входа, при заблокированном экране), ответ будет
    пустым, и doctor соврёт про неработающий at-spi.
    """
    try:
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = bus.call_sync("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress",
                            None, GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE,
                            1500, None)
        conn = Gio.DBusConnection.new_for_address_sync(
            res.unpack()[0], Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        got = conn.call_sync("org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root",
                             ACC, "GetChildren", None, GLib.VariantType("(a(so))"),
                             Gio.DBusCallFlags.NONE, 1500, None).unpack()
        apps = [n for n, _p in got[0] if n]
    except Exception as exc:  # noqa: BLE001 — любая беда = мост недоступен
        return False, f"at-spi не отвечает ({exc})"
    if not apps:
        return False, "at-spi отвечает, но не видит ни одного приложения"
    return True, ""


def make_probe(enabled: bool) -> Probe:
    """Включён ли сбор сведений о фокусе в этой среде."""
    return AtspiProbe() if enabled else NullProbe()
