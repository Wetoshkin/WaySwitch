"""Что сейчас в фокусе: приложение и защищённый ввод — через at-spi.

Зачем это нужно. Две самые частые боли пользователей Punto Switcher — «лезет
не в те программы» и «мешает вводить пароль». На Wayland у приложения нельзя
спросить композитор «какое окно активно», зато можно спросить мост
доступности (at-spi): он есть и в GNOME, и в KDE, и в любом стеке на GTK/Qt.

Как устроено. Опрос at-spi — это десятки D-Bus-вызовов, и делать их в пути
ввода нельзя: пока идёт опрос, события клавиатуры стоят в очереди. Поэтому
опрос живёт в отдельном потоке и раз в POLL_SEC обновляет снимок, а
controller читает этот снимок мгновенно. События смены фокуса немедленно
сбрасывают снимок, а результат начатого до события опроса отбрасывается.
MAX_AGE_SEC ограничивает срок действия снимка при отсутствии событий.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import NamedTuple, Protocol

log = logging.getLogger("wayswitch")

# Биты в маске состояний at-spi: STATE_ACTIVE (активное окно) и STATE_FOCUSED.
ACTIVE = 1
FOCUSED = 12

# Снимок старше этого возраста считается недостоверным: лучше не мешать
# человеку (не исправлять), чем мешать по устаревшим данным.
MAX_AGE_SEC = 3.0

# Как часто поток обновляет снимок. Чаще нет смысла: фокус не скачет,
# а каждый опрос — это обход дерева доступности.
POLL_SEC = 1.5
# poke() на каждой клавише: снимок моложе этого — не будим поток, иначе
# обход дерева доступности шёл бы с частотой набора.
POKE_FRESH_SEC = 1.0

# Веб-поля бывают глубже 20 уровней; бюджет узлов ограничивает стоимость обхода.
MAX_NODES = 400
MAX_DEPTH = 32

# Столько секунд без ввода — и поток опроса останавливается, чтобы не крутить
# обход дерева доступности впустую. Разбудит его следующий poke().
IDLE_STOP_SEC = 30.0

# ATSPI_ROLE_PASSWORD_TEXT / TERMINAL: VTE не сообщает о скрытом вводе getpass.
PROTECTED_ROLES = frozenset({40, 60})
# ATSPI_ROLE_TEXT / ENTRY: сфокусированный контейнер не подтверждает тип ввода.
TEXT_ROLES = frozenset({61, 79})

# Оболочки и оконные менеджеры: их узлы at-spi тоже бывают «в фокусе» (панель,
# обзор), но печатает человек не в них. Иначе при любом окне на экране
# активной программой оказывалась бы «gnome-shell».
SHELL_APPS = frozenset({"gnome-shell", "plasmashell", "kwin", "kwin_wayland", "mutter"})


class FocusSnapshot(NamedTuple):
    """Состояние фокуса; имя password сохранено для совместимости, включая терминалы."""

    app: str | None = None
    password: bool = False
    taken_at: float = 0.0
    app_ids: tuple[str, ...] = ()
    field: tuple[str, str] | None = None
    generation: int = 0

    def age(self, now: float) -> float:
        return max(0.0, now - self.taken_at)


    def protection(self, now: float) -> bool | None:
        return self.password if self.app and self.age(now) <= MAX_AGE_SEC else None


class _FocusWatch(NamedTuple):
    query_connection: object
    live: threading.Event
    close: Callable[[], None]


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

    Возвращает короткую причину для журнала: «защищённый ввод» или
    «приложение telegram».
    """
    if isinstance(probe, NullProbe):
        return None
    snap = probe.snapshot()
    if snap.age(now) > MAX_AGE_SEC:
        return "фокус устарел" if skip_password or excluded else None
    if skip_password and snap.password:
        return "защищённый ввод"
    for app in (snap.app, *snap.app_ids):
        if app_matches(app, excluded):
            return f"приложение {app}"
    if not snap.app and (skip_password or excluded):
        return "фокус недоступен"
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
        # В тестах query() также может вернуть пару (приложение, защищённый ввод).
        self._query = query or query_focus
        self._watch_real_focus = query is None
        self._watcher: _FocusWatch | None = None
        self._lock = threading.Lock()
        self._snap = FocusSnapshot()
        self._generation = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._ok = True
        self._last_activity = 0.0

    # --- публичное ----------------------------------------------------------

    def snapshot(self) -> FocusSnapshot:
        with self._lock:
            return self._snap

    def poke(self) -> None:
        if self._stop.is_set():
            return
        # Активность отмечаем всегда, даже если будить не стали: иначе поток
        # погас бы по простою посреди набора.
        self._last_activity = self._clock()
        alive = self._thread is not None and self._thread.is_alive()
        if alive and self.snapshot().age(self._clock()) < POKE_FRESH_SEC:
            return
        self._ensure_thread()
        self._wake.set()

    def close(self, wait: bool = False) -> None:
        """Остановить поток. По умолчанию не ждём: поток — daemon и выйдет сам
        после текущего опроса, а ждать в главном цикле (Reload) нельзя."""
        self._stop.set()
        self._invalidate()
        thread = self._thread
        if wait and thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def healthy(self) -> bool:
        """Удался ли последний опрос (для doctor и страницы статуса)."""
        return self._ok

    # --- поток --------------------------------------------------------------

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._stop.is_set() or (self._thread is not None and self._thread.is_alive()):
                return
            self._thread = threading.Thread(target=self._run, name="atspi-probe", daemon=True)
            self._thread.start()

    def _invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            self._snap = FocusSnapshot(generation=self._generation)
        self._wake.set()

    def _drop_watcher(self) -> None:
        watcher, self._watcher = self._watcher, None
        if watcher is not None:
            watcher.close()

    def _run(self) -> None:
        self._last_activity = self._clock()
        try:
            while not self._stop.is_set():
                self._refresh()
                if self._wake.wait(self._poll):
                    self._wake.clear()
                elif self._clock() - self._last_activity > IDLE_STOP_SEC:
                    with self._lock:
                        self._generation += 1
                        self._snap = FocusSnapshot(generation=self._generation)
                    return
        finally:
            # Регистрация могла закончиться уже после close(wait=False).
            self._drop_watcher()
            with self._lock:
                self._thread = None
            if self._wake.is_set() and not self._stop.is_set():
                self._ensure_thread()

    def _refresh(self) -> None:
        started = self._clock()
        try:
            if self._watch_real_focus:
                connection = _a11y_connection()
                if (self._watcher is None or self._watcher.query_connection is not connection
                        or not self._watcher.live.is_set()):
                    self._drop_watcher()
                    self._watcher = _watch_focus_events(connection, self._invalidate)
                if not self._watcher.live.is_set():
                    raise RuntimeError("подписка at-spi потеряна")
            if self._stop.is_set():
                return
            with self._lock:
                generation = self._generation
            result = self._query()
        except Exception:  # noqa: BLE001 — at-spi может отсутствовать целиком
            if self._ok:
                # Первый сбой после успеха — в журнал с трассировкой, дальше
                # молча: иначе без at-spi журнал забивался бы раз в 1,5 с.
                log.warning("опрос at-spi не удался", exc_info=True)
            self._ok = False
            with self._lock:
                if self._snap.app:
                    self._generation += 1
                self._snap = FocusSnapshot(generation=self._generation)
            return
        self._ok = True
        with self._lock:
            if (generation == self._generation and not self._stop.is_set()
                    and (not self._watch_real_focus or self._watcher.live.is_set())):
                snap = result if isinstance(result, FocusSnapshot) else FocusSnapshot(*result)
                old = self._snap
                if ((old.app, old.field, old.password) != (snap.app, snap.field, snap.password)
                        or old.app and old.age(started) > MAX_AGE_SEC):
                    self._generation += 1
                self._snap = snap._replace(taken_at=started, generation=self._generation)


# --- настоящий опрос --------------------------------------------------------

ACC = "org.a11y.atspi.Accessible"


def _state_bit(words: list[int], bit: int) -> bool:
    """Разобрать маску состояний at-spi (первое 32-битное слово)."""
    if not words:
        return False
    return bool(words[0] >> bit & 1)


def _state_focused(words: list[int]) -> bool:
    return _state_bit(words, FOCUSED)


def find_focus(tree, root) -> FocusSnapshot:
    """Чистый обход: приложение в фокусе и признак защищённого ввода.

    ``tree`` — объект с методами children(node), state(node), role(node),
    app_name(node), app_of(node); в query_focus он ходит в D-Bus, в тестах —
    в словарь.

    Сначала ищем активное окно (дети приложений, глубина 2, это дёшево), и
    уже внутри него — сфокусированный узел. Обход всего дерева в ширину делил
    бюджет MAX_NODES на все приложения, и до поля ввода в браузере (глубже
    десятка уровней) он не доходил. Заодно снимается вопрос «два узла в
    фокусе сразу» — ищем только в активном окне.
    """
    active = None
    for app in tree.children(root):
        name = tree.app_name(app)
        if name and name.lower() in SHELL_APPS:
            continue
        for window in tree.children(app):
            if _state_bit(tree.state(window), ACTIVE):
                active = (window, name or None)
                break
        if active:
            break
    if active is not None:
        start, app_name = [(active[0], 0)], active[1]
    else:
        # Тулкит не отмечает активное окно — прежний обход от корня.
        start, app_name = [(root, 0)], None
    queue = list(start)
    seen = set()
    visited = 0
    while queue and visited < MAX_NODES:
        node, depth = queue.pop(0)
        if node in seen:
            continue
        seen.add(node)
        visited += 1
        if _state_focused(tree.state(node)):
            owner = app_name or tree.app_of(node)
            if owner and owner.lower() in SHELL_APPS:
                continue
            role = tree.role(node)
            if role in PROTECTED_ROLES:
                return FocusSnapshot(owner, True, field=node)
            if role in TEXT_ROLES:
                return FocusSnapshot(owner, False, field=node)
        if depth < MAX_DEPTH:
            queue.extend((child, depth + 1) for child in tree.children(node))
    # Активное окно без доступного поля не подтверждает, что ввод не секретный.
    return FocusSnapshot()


_CONN = None


def _a11y_connection():
    """Соединение с шиной доступности; переиспользуется между опросами."""
    global _CONN
    if _CONN is None or _CONN.is_closed():
        _CONN = _new_a11y_connection()
    return _CONN


def _new_a11y_connection():
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    res = bus.call_sync("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress",
                        None, GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 1500, None)
    return Gio.DBusConnection.new_for_address_sync(
        res.unpack()[0],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


def _watch_focus_events(query_connection, invalidate) -> _FocusWatch:
    from gi.repository import Gio, GLib

    # Отдельное имя клиента не даёт старому probe удалить подписку нового при Reload.
    connection = _new_a11y_connection()
    registry = "org.a11y.atspi.Registry"
    path = "/org/a11y/atspi/registry"
    event = "object:state-changed:focused"
    live = threading.Event()
    live.set()
    subscriptions = []
    closed_handler = None

    def changed(*_args):
        invalidate()

    def lost(*_args):
        live.clear()
        invalidate()

    def close():
        live.clear()
        for subscription in subscriptions:
            connection.signal_unsubscribe(subscription)
        if closed_handler is not None:
            connection.disconnect(closed_handler)
        try:
            connection.close_sync(None)
        except GLib.Error:
            pass

    def owner():
        return connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "GetNameOwner", GLib.Variant("(s)", (registry,)), GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NONE, 1500, None).unpack()[0]

    try:
        closed_handler = connection.connect("closed", lost)
        subscriptions.append(connection.signal_subscribe(
            None, "org.a11y.atspi.Event.Object", "StateChanged", None, "focused",
            Gio.DBusSignalFlags.NONE, changed, None))
        subscriptions.append(connection.signal_subscribe(
            "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
            "/org/freedesktop/DBus", registry, Gio.DBusSignalFlags.NONE, lost, None))
        connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "StartServiceByName", GLib.Variant("(su)", (registry, 0)), GLib.VariantType("(u)"),
            Gio.DBusCallFlags.NONE, 1500, None)
        registered_owner = owner()
        connection.call_sync(registered_owner, path, registry, "RegisterEvent",
                             GLib.Variant("(sass)", (event, [], "")), GLib.VariantType("()"),
                             Gio.DBusCallFlags.NONE, 1500, None)
        if owner() != registered_owner or not live.is_set():
            raise RuntimeError("реестр at-spi сменился во время подписки")
    except Exception:
        close()
        raise

    return _FocusWatch(query_connection, live, close)


class _DbusTree:
    """Узлы дерева доступности — пары (имя на шине, путь объекта)."""

    def __init__(self, conn):
        from gi.repository import Gio, GLib

        self._conn, self._Gio, self._GLib = conn, Gio, GLib

    def _call(self, node, iface: str, method: str, arg, sig: str):
        GLib, Gio = self._GLib, self._Gio
        try:
            return self._conn.call_sync(node[0], node[1], iface, method, arg,
                                        GLib.VariantType(sig), Gio.DBusCallFlags.NONE, 400,
                                        None).unpack()
        except GLib.Error:
            return None

    def children(self, node):
        kids = self._call(node, ACC, "GetChildren", None, "(a(so))")
        return [(n, p) for n, p in (kids[0] if kids else []) if n and p]

    def state(self, node):
        got = self._call(node, ACC, "GetState", None, "(au)")
        return got[0] if got else []

    def role(self, node) -> int | None:
        got = self._call(node, ACC, "GetRole", None, "(u)")
        return got[0] if got else None

    def app_name(self, node) -> str:
        got = self._call(node, "org.freedesktop.DBus.Properties", "Get",
                         self._GLib.Variant("(ss)", (ACC, "Name")), "(v)")
        return str(got[0]).strip() if got else ""

    def app_of(self, node) -> str | None:
        got = self._call(node, ACC, "GetApplication", None, "((so))")
        if got and got[0][0]:
            return self.app_name(got[0]) or None
        return None


ROOT = ("org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root")


def query_shell_app_ids() -> tuple[str, ...]:
    """Идентификаторы активного окна GNOME; старое расширение не обязано их отдавать."""
    try:
        from gi.repository import Gio, GLib

        from wayswitch.backends.gnome_shell import BUS_NAME, INTERFACE, OBJECT_PATH

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = bus.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, "GetFocusedApp", None,
                               GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE, 500, None)
        return tuple(result.unpack()[0])
    except Exception:  # noqa: BLE001 — расширение необязательно вне GNOME
        return ()


def query_focus() -> FocusSnapshot:
    """Приложение в фокусе и признак защищённого ввода (блокирует на D-Bus)."""
    global _CONN
    app_ids = query_shell_app_ids()
    try:
        snap = find_focus(_DbusTree(_a11y_connection()), ROOT)
    except Exception:
        _CONN = None  # шина доступности перезапустилась — пересоздадим
        if not app_ids:
            raise
        snap = FocusSnapshot()
    return snap._replace(app_ids=app_ids)


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


def list_applications() -> list[str]:
    """Имена программ, которые сейчас видит at-spi (для «Добавить приложение»).

    Один уровень дерева: дети корня — это приложения. Оболочки отброшены.
    Блокирует на D-Bus — звать из потока.
    """
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    res = bus.call_sync("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress",
                        None, GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 1500, None)
    conn = Gio.DBusConnection.new_for_address_sync(
        res.unpack()[0], Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    kids = conn.call_sync("org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root",
                          ACC, "GetChildren", None, GLib.VariantType("(a(so))"),
                          Gio.DBusCallFlags.NONE, 1500, None).unpack()[0]
    names: list[str] = []
    for bus_name, path in kids:
        try:
            got = conn.call_sync(bus_name, path, "org.freedesktop.DBus.Properties", "Get",
                                 GLib.Variant("(ss)", (ACC, "Name")), GLib.VariantType("(v)"),
                                 Gio.DBusCallFlags.NONE, 400, None).unpack()
        except GLib.Error:
            continue
        name = str(got[0]).strip()
        if name and name.lower() not in SHELL_APPS and name not in names:
            names.append(name)
    return names


def make_probe(enabled: bool) -> Probe:
    """Включён ли сбор сведений о фокусе в этой среде."""
    return AtspiProbe() if enabled else NullProbe()
