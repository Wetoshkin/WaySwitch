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

import logging
import threading
import time
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

    def close(self, wait: bool = False) -> None:
        """Остановить поток. По умолчанию не ждём: поток — daemon и выйдет сам
        после текущего опроса, а ждать в главном цикле (Reload) нельзя."""
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if wait and thread is not None and thread.is_alive():
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
                # Снимок помечаем старым (taken_at=0): «свежее» пустое значение
                # три секунды выдавало бы себя за «ограничений нет».
                with self._lock:
                    self._snap = FocusSnapshot()
                return

    def _refresh(self) -> None:
        try:
            app, password = self._query()
        except Exception:  # noqa: BLE001 — at-spi может отсутствовать целиком
            if self._ok:
                # Первый сбой после успеха — в журнал с трассировкой, дальше
                # молча: иначе без at-spi журнал забивался бы раз в 1,5 с.
                log.warning("опрос at-spi не удался", exc_info=True)
            self._ok = False
            with self._lock:
                self._snap = FocusSnapshot(None, False, self._clock())
            return
        self._ok = True
        with self._lock:
            self._snap = FocusSnapshot(app, password, self._clock())


# --- настоящий опрос --------------------------------------------------------

ACC = "org.a11y.atspi.Accessible"


def _state_bit(words: list[int], bit: int) -> bool:
    """Разобрать маску состояний at-spi (первое 32-битное слово)."""
    if not words:
        return False
    return bool(words[0] >> bit & 1)


def _state_focused(words: list[int]) -> bool:
    return _state_bit(words, FOCUSED)


def find_focus(tree, root) -> tuple[str | None, bool]:
    """Чистый обход: приложение в фокусе и признак поля пароля.

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
            return owner, tree.role(node) in PASSWORD_ROLES
        if depth < MAX_DEPTH:
            queue.extend((child, depth + 1) for child in tree.children(node))
    # Фокус не нашли, но активное окно известно — приложение всё равно знаем.
    return app_name, False


_CONN = None


def _a11y_connection():
    """Соединение с шиной доступности; переиспользуется между опросами."""
    from gi.repository import Gio, GLib

    global _CONN
    if _CONN is not None and not _CONN.is_closed():
        return _CONN
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    res = bus.call_sync("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress",
                        None, GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 1500, None)
    _CONN = Gio.DBusConnection.new_for_address_sync(
        res.unpack()[0],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    return _CONN


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

    def role(self, node) -> str:
        got = self._call(node, ACC, "GetRoleName", None, "(s)")
        return got[0] if got else ""

    def app_name(self, node) -> str:
        got = self._call(node, "org.freedesktop.DBus.Properties", "Get",
                         self._GLib.Variant("(ss)", (ACC, "Name")), "(v)")
        return str(got[0]).strip() if got else ""

    def app_of(self, node) -> str | None:
        got = self._call(node, ACC, "GetApplication", None, "(so)")
        if got and got[0]:
            return self.app_name(got) or None
        return None


ROOT = ("org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root")


def query_focus() -> tuple[str | None, bool]:
    """Приложение в фокусе и признак поля пароля (блокирует на D-Bus)."""
    global _CONN
    try:
        return find_focus(_DbusTree(_a11y_connection()), ROOT)
    except Exception:
        _CONN = None  # шина доступности перезапустилась — пересоздадим
        raise


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
