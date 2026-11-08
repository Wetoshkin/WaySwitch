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

import configparser
import logging
import os
import stat
import threading
import time
from collections.abc import Callable
from typing import NamedTuple, Protocol

log = logging.getLogger("wayswitch")

# Биты в маске состояний at-spi.
ACTIVE = 1
DEFUNCT = 6
EDITABLE = 7
FOCUSED = 12
SHOWING = 25

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
MENU_ROLES = frozenset({33, 34, 35, 41})
TEXT = "org.a11y.atspi.Text"
HYPERTEXT = "org.a11y.atspi.Hypertext"
HYPERLINK = "org.a11y.atspi.Hyperlink"

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
    # Имя активного окна: нужно, чтобы отличить компонент LibreOffice
    # (Writer от Calc) на KDE, где app_ids недоступны.
    window: str | None = None

    def age(self, now: float) -> float:
        return max(0.0, now - self.taken_at)


    def protection(self, now: float) -> bool | None:
        return self.password if self.app and self.age(now) <= MAX_AGE_SEC else None


class TextSelection(NamedTuple):
    start: int
    end: int
    text: str


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

    Опрос засыпает при простое; подписка доступности живёт до close().
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
        snap = self.snapshot()
        if alive and snap.app and snap.age(self._clock()) < POKE_FRESH_SEC:
            return
        self._ensure_thread()
        self._wake.set()

    def close(self, wait: bool = False) -> None:
        """Остановить поток. По умолчанию не ждём: поток — daemon и выйдет сам
        после текущего опроса, а ждать в главном цикле (Reload) нельзя."""
        self._stop.set()
        self._invalidate()
        thread = self._thread
        if thread is None or not thread.is_alive():
            self._drop_watcher()
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
            with self._lock:
                self._thread = None
            # Без слушателя AT-SPI новые Electron-окна могут не включить доступность.
            if self._stop.is_set():
                self._drop_watcher()
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


def _caret_field(tree, node, parents, remaining, depth):
    """Проверить тип ввода и владельца каретки, не читая содержимое поля."""
    ancestor = node
    uncertain = False
    while ancestor is not None:
        if _state_bit(tree.state(ancestor), DEFUNCT):
            return None
        role = tree.role(ancestor)
        if role in PROTECTED_ROLES:
            return node, True
        input_type = tree.input_type(ancestor)
        if input_type == "password":
            return node, True
        uncertain |= role in (None, 0, 67) or input_type is None
        ancestor = parents.get(ancestor)
    if uncertain:
        return None

    seen = set()
    while node not in seen and depth <= MAX_DEPTH:
        seen.add(node)
        state = tree.state(node)
        if _state_bit(state, DEFUNCT):
            return None
        role = tree.role(node)
        if role in PROTECTED_ROLES or tree.input_type(node) == "password":
            return node, True
        interfaces = tree.interfaces(node)
        if (role in (None, 0, 67) or not interfaces or TEXT not in interfaces
                or not _state_bit(state, EDITABLE)
                or tree.input_type(node) is None):
            return None
        caret = tree.caret(node)
        if type(caret) is not int or caret < 0:
            return None
        if HYPERTEXT not in interfaces:
            return node, False
        count = tree.link_count(node)
        if type(count) is not int or not 0 <= count <= remaining[0]:
            return None
        anchor = None
        for index in range(count):
            remaining[0] -= 1
            link = tree.link(node, index)
            offsets = tree.link_range(link) if link is not None else None
            if offsets is None or not 0 <= offsets[0] < offsets[1]:
                return None
            # Chromium GetLinkIndex может считать UTF-16 вместо символов Unicode.
            if offsets[0] <= caret < offsets[1]:
                anchor = tree.link_object(link)
                if anchor is None:
                    return None
                break
        if anchor is None:
            return node, False
        if remaining[0] <= 0:
            return None
        remaining[0] -= 1
        node = anchor
        depth += 1
    return None


def _flatpak_instance(pid):
    """Достоверная пара приложения и экземпляра, включая отдельный D-Bus proxy."""
    if type(pid) is not int or pid <= 0:
        return None
    root_fd = info_fd = None
    try:
        root_fd = os.open(f"/proc/{pid}/root", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        info_fd = os.open(".flatpak-info", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                          | os.O_CLOEXEC, dir_fd=root_fd)
        info = os.fstat(info_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            return None
        with os.fdopen(info_fd, "rb") as stream:
            info_fd = None
            data = stream.read(65537)
        if len(data) > 65536 or b"\0" in data:
            return None
        parser = configparser.ConfigParser(interpolation=None, default_section="")
        parser.optionxform = str
        parser.read_string(data.decode("utf-8"))
        app = parser.get("Application", "name").strip()
        instance = parser.get("Instance", "instance-id").strip()
        return (app, instance) if app and instance else None
    except (OSError, configparser.Error, UnicodeError, ValueError, AttributeError,
            NotImplementedError):
        return None
    finally:
        if info_fd is not None:
            os.close(info_fd)
        if root_fd is not None:
            os.close(root_fd)


def _same_application(pid, focused_pid):
    if (type(pid) is not int or type(focused_pid) is not int
            or pid <= 0 or focused_pid <= 0):
        return False
    if pid == focused_pid:
        return True
    instance = _flatpak_instance(pid)
    return instance is not None and instance == _flatpak_instance(focused_pid)


def find_focus(tree, root, focused_pid: int = 0) -> FocusSnapshot:
    """Обход подтверждённого окна: редактируемое поле либо защищённый ввод."""
    active = None
    scoped = None
    remaining = [MAX_NODES]
    for app in tree.children(root) or []:
        remaining[0] -= 1
        if remaining[0] <= 0:
            return FocusSnapshot()
        name = tree.app_name(app)
        if not name or name.lower() in SHELL_APPS:
            continue
        if focused_pid and not _same_application(tree.pid(app), focused_pid):
            continue
        if focused_pid:
            scoped = (app, name)
        for window in tree.children(app) or []:
            remaining[0] -= 1
            if remaining[0] <= 0:
                return FocusSnapshot()
            state = tree.state(window)
            if _state_bit(state, ACTIVE) and not _state_bit(state, DEFUNCT):
                active = (window, name)
                break
        if active:
            break
    active = active or scoped
    if active is None:
        return FocusSnapshot()
    window, app_name = active
    tree.prepare(window)
    queue = [(window, 0, None)]
    deferred = []
    web = []
    seen = set()
    roles = {}
    parents = {}
    complete = True
    native_phase = True
    unverified_focus = set()
    while (queue or deferred or web) and remaining[0] > 0:
        if not queue:
            if deferred:
                queue, deferred = deferred, []
                continue
            if not complete:
                return FocusSnapshot()
            if native_phase:
                hosts = set()
                for web_root, _, _ in web:
                    ancestor = parents.get(web_root)
                    while ancestor is not None:
                        hosts.add(ancestor)
                        ancestor = parents.get(ancestor)
                if unverified_focus - hosts:
                    return FocusSnapshot()
                native_phase = False
            queue, web = web, []
        node, depth, parent = queue.pop(0)
        if node in seen:
            continue
        # Сначала нативный интерфейс: браузеры сохраняют FOCUSED у веб-поля.
        if node not in roles:
            remaining[0] -= 1
            roles[node] = tree.role(node)
        role = roles[node]
        if role == 95 and native_phase:
            if node not in parents:
                parents[node] = parent
                web.append((node, depth, parent))
            continue
        seen.add(node)
        parents[node] = parent
        if role == 95 and not native_phase:
            focused_path = getattr(tree, "focused_path", None)
            path = focused_path(node) if callable(focused_path) else None
            if path and depth + len(path) <= MAX_DEPTH and len(path) < remaining[0]:
                # Collection находит редактор за большой историей без чтения её текста.
                remaining[0] -= len(path)
                ancestor = node
                for child in path:
                    parents[child], ancestor = ancestor, child
                queue.insert(0, (path[-1], depth + len(path), parents[path[-1]]))
        state = tree.state(node)
        if _state_bit(state, DEFUNCT):
            continue
        if not state or role in (None, 0, 67):
            complete = False
        if _state_focused(state):
            field = _caret_field(tree, node, parents, remaining, depth)
            if field is not None:
                window_name = None
                try:
                    name = tree._property(window, ACC, "Name")
                    window_name = name if isinstance(name, str) else None
                except Exception:  # noqa: BLE001 — имя окна лишь уточняет компонент
                    window_name = None
                return FocusSnapshot(app_name, field[1], field=field[0],
                                     window=window_name)
            if native_phase:
                unverified_focus.add(node)
        if depth < MAX_DEPTH:
            kids = tree.children(node)
            if kids is None:
                complete = False
            children = [(child, depth + 1, node) for child in kids or []]
            if _state_bit(state, SHOWING) and role not in MENU_ROLES:
                queue.extend(children)
            else:
                # Qt отмечает закрытые меню как SHOWING: их потомки проверяются позже.
                deferred.extend(children)
        else:
            complete = False
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

    from wayswitch.backends.gnome_shell import BUS_NAME, INTERFACE, OBJECT_PATH

    # Отдельное имя клиента не даёт старому probe удалить подписку нового при Reload.
    connection = _new_a11y_connection()
    registry = "org.a11y.atspi.Registry"
    path = "/org/a11y/atspi/registry"
    event = "object:state-changed:focused"
    live = threading.Event()
    live.set()
    subscriptions = []
    closed_handlers = []

    def changed(*_args):
        invalidate()

    def lost(*_args):
        live.clear()
        invalidate()

    def close():
        live.clear()
        for bus, subscription in subscriptions:
            bus.signal_unsubscribe(subscription)
        for bus, handler in closed_handlers:
            bus.disconnect(handler)
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
        closed_handlers.append((connection, connection.connect("closed", lost)))
        session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        closed_handlers.append((session, session.connect("closed", lost)))
        subscriptions.append((session, session.signal_subscribe(
            BUS_NAME, INTERFACE, "FocusedContextChanged", OBJECT_PATH, None,
            Gio.DBusSignalFlags.NONE, changed, None)))
        subscriptions.append((session, session.signal_subscribe(
            "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
            "/org/freedesktop/DBus", BUS_NAME, Gio.DBusSignalFlags.NONE, changed, None)))
        subscriptions.append((connection, connection.signal_subscribe(
            None, "org.a11y.atspi.Event.Object", "StateChanged", None, "focused",
            Gio.DBusSignalFlags.NONE, changed, None)))
        subscriptions.append((connection, connection.signal_subscribe(
            "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
            "/org/freedesktop/DBus", registry, Gio.DBusSignalFlags.NONE, lost, None)))
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
        return [(n, p) for n, p in kids[0] if n and p
                and p != "/org/a11y/atspi/null"] if kids else None

    def prepare(self, node):
        # Chromium включает полное дерево при первом запросе клиента доступности.
        self._call(node, ACC, "GetRelationSet", None, "(a(ua(so)))")

    def state(self, node):
        got = self._call(node, ACC, "GetState", None, "(au)")
        return got[0] if got else []

    def role(self, node) -> int | None:
        got = self._call(node, ACC, "GetRole", None, "(u)")
        return got[0] if got else None

    def interfaces(self, node):
        got = self._call(node, ACC, "GetInterfaces", None, "(as)")
        return got[0] if got else None

    def input_type(self, node):
        got = self._call(node, ACC, "GetAttributes", None, "(a{ss})")
        return got[0].get("text-input-type", "").lower() if got else None

    def _property(self, node, interface, name):
        got = self._call(node, "org.freedesktop.DBus.Properties", "Get",
                         self._GLib.Variant("(ss)", (interface, name)), "(v)")
        return got[0] if got else None

    def caret(self, node):
        return self._property(node, TEXT, "CaretOffset")

    def character_count(self, node):
        return self._property(node, TEXT, "CharacterCount")

    def selection_count(self, node):
        got = self._call(node, TEXT, "GetNSelections", None, "(i)")
        return got[0] if got else None

    def selection_range(self, node):
        got = self._call(node, TEXT, "GetSelection", self._GLib.Variant("(i)", (0,)), "(ii)")
        if (isinstance(got, (tuple, list)) and len(got) == 2
                and all(type(value) is int for value in got)):
            return got[0], got[1]
        return None

    def text_range(self, node, start, end):
        if (type(start) is not int or type(end) is not int
                or not 0 <= start <= end <= 0x7FFFFFFF or end - start > 1024):
            return None
        got = self._call(node, TEXT, "GetText", self._GLib.Variant("(ii)", (start, end)), "(s)")
        return got[0] if got else None

    def insert_text(self, node, start, text):
        if (type(start) is not int or not 0 <= start <= 0x7FFFFFFF
                or not isinstance(text, str) or not 0 < len(text) <= 512):
            return None
        application = self._call(node, ACC, "GetApplication", None, "((so))")
        if not application or application[0][0] != node[0]:
            return None
        toolkit = self._property(application[0], "org.a11y.atspi.Application", "ToolkitName")
        if not isinstance(toolkit, str) or not toolkit:
            return None
        # Qt resizes a QString using this length; other bridges use AT-SPI's UTF-8 bytes.
        size = len(text.encode("utf-16-le")) // 2 if toolkit.casefold() == "qt" else len(
            text.encode("utf-8"))
        got = self._call(node, "org.a11y.atspi.EditableText", "InsertText",
                         self._GLib.Variant("(isi)", (start, text, size)), "(b)")
        return got[0] if got else None

    def delete_text(self, node, start, end):
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= 0x7FFFFFFF or end - start > 512):
            return None
        got = self._call(node, "org.a11y.atspi.EditableText", "DeleteText",
                         self._GLib.Variant("(ii)", (start, end)), "(b)")
        return got[0] if got else None

    def link_count(self, node):
        got = self._call(node, HYPERTEXT, "GetNLinks", None, "(i)")
        return got[0] if got else None

    def _reference(self, node, interface, method, index):
        got = self._call(node, interface, method,
                         self._GLib.Variant("(i)", (index,)), "((so))")
        if got and got[0][0] and got[0][1] != "/org/a11y/atspi/null":
            return tuple(got[0])
        return None

    def link(self, node, index):
        return self._reference(node, HYPERTEXT, "GetLink", index)

    def link_range(self, node):
        start = self._property(node, HYPERLINK, "StartIndex")
        end = self._property(node, HYPERLINK, "EndIndex")
        return (start, end) if type(start) is int and type(end) is int else None

    def link_object(self, node):
        return self._reference(node, HYPERLINK, "GetObject", 0)

    def pid(self, node):
        got = self._call(("org.freedesktop.DBus", "/org/freedesktop/DBus"),
                         "org.freedesktop.DBus", "GetConnectionUnixProcessID",
                         self._GLib.Variant("(s)", (node[0],)), "(u)")
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

    def focused_path(self, root):
        rule = ([1 << FOCUSED, 0], 1, {}, 1, [0] * 5, 1, [], 1, False)
        got = self._call(root, "org.a11y.atspi.Collection", "GetMatches",
                         self._GLib.Variant("((aiia{ss}iaiiasib)uib)", (rule, 1, 2, True)),
                         "(a(so))")
        if not got or len(got[0]) != 1:
            return None
        node, path = got[0][0], []
        while node != root and len(path) < MAX_DEPTH:
            if (not isinstance(node, tuple) or len(node) != 2 or node[0] != root[0]
                    or not isinstance(node[1], str) or not node[1].startswith("/")
                    or node[1] == "/org/a11y/atspi/null" or node in path):
                return None
            path.append(node)
            node = self._property(node, ACC, "Parent")
        return list(reversed(path)) if node == root and path else None


ROOT = ("org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root")


class ShellContext(NamedTuple):
    app_ids: tuple[str, ...] = ()
    modal: bool = False
    ime_focus: bool = False
    purpose: int = 0
    hints: int = 0
    pid: int = 0

    def protected(self) -> bool:
        return self.modal or self.ime_focus and (self.purpose in (8, 12)
                                                 or bool(self.hints & (64 | 128)))


def query_shell_context() -> ShellContext | None:
    """Атомарные сведения GNOME; старое расширение отдаёт только идентификаторы."""
    try:
        from gi.repository import Gio, GLib

        from wayswitch.backends.gnome_shell import BUS_NAME, INTERFACE, OBJECT_PATH

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = bus.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, "GetFocusedContext", None,
                               GLib.VariantType("(asbbuuu)"), Gio.DBusCallFlags.NONE, 500, None)
        app_ids, modal, ime_focus, purpose, hints, pid = result.unpack()
        return ShellContext(tuple(app_ids), modal, ime_focus, purpose, hints, pid)
    except ImportError:
        return ShellContext(query_shell_app_ids())
    except Exception as exc:  # noqa: BLE001 — неизвестный контекст не разрешает ввод
        try:
            error = Gio.DBusError.get_remote_error(exc)
        except (AttributeError, TypeError):
            return None
        if error in {"org.freedesktop.DBus.Error.UnknownMethod",
                     "org.freedesktop.DBus.Error.UnknownInterface",
                     "org.freedesktop.DBus.Error.ServiceUnknown",
                     "org.freedesktop.DBus.Error.NameHasNoOwner"}:
            return ShellContext(query_shell_app_ids())
        return None


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
    context = query_shell_context()
    if context is None:
        return FocusSnapshot()
    if context.protected():
        return FocusSnapshot(context.app_ids[0] if context.app_ids else "gnome-shell", True,
                             app_ids=context.app_ids)
    try:
        snap = find_focus(_DbusTree(_a11y_connection()), ROOT, context.pid)
    except Exception:
        _CONN = None  # шина доступности перезапустилась — пересоздадим
        if not context.app_ids:
            raise
        snap = FocusSnapshot()
    current = query_shell_context()
    if current is None:
        return FocusSnapshot()
    if current.protected():
        return FocusSnapshot(current.app_ids[0] if current.app_ids else "gnome-shell", True,
                             app_ids=current.app_ids)
    if current != context:
        snap = FocusSnapshot()
    return snap._replace(app_ids=current.app_ids)


def _selection_ready(expected, valid, limit):
    now = time.monotonic()
    return (isinstance(expected, FocusSnapshot) and expected.field is not None
            and type(limit) is int and 0 < limit <= 1024
            and type(expected.taken_at) in (int, float) and 0 < expected.taken_at <= now
            and expected.protection(now) is False and valid() is True)


def _read_selection(tree, expected, valid, limit) -> TextSelection | None:
    try:
        if not _selection_ready(expected, valid, limit):
            return None
        context = query_shell_context()
        if (context is None or context.protected()
                or context.app_ids != expected.app_ids):
            return None

        def same_focus():
            if (not _selection_ready(expected, valid, limit)
                    or query_shell_context() != context):
                return False
            snap = find_focus(tree, ROOT, context.pid)
            return (snap.app == expected.app and snap.field == expected.field
                    and snap.password is False and query_shell_context() == context
                    and _selection_ready(expected, valid, limit))

        def metadata():
            selections = tree.selection_count(expected.field)
            if type(selections) is not int or selections != 1:
                return None
            count = tree.character_count(expected.field)
            bounds = tree.selection_range(expected.field)
            if (type(count) is not int or not 0 <= count <= 0x7FFFFFFF
                    or not isinstance(bounds, (tuple, list)) or len(bounds) != 2):
                return None
            start, end = bounds
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= count or end - start > limit):
                return None
            return count, start, end

        before = metadata()
        if before is None or not same_focus() or metadata() != before:
            return None
        _count, start, end = before
        text = tree.text_range(expected.field, start, end)
        if (type(text) is not str or len(text) != end - start
                or "\r" in text or "\n" in text):
            return None
        if metadata() != before or not same_focus():
            return None
        return TextSelection(start, end, text)
    except Exception:  # noqa: BLE001 — provider failures must not expose selected text
        return None


def read_selection(expected: FocusSnapshot, valid: Callable[[], bool],
                   limit: int) -> TextSelection | None:
    """Read one bounded selection; execution still needs its own fresh validation."""
    connection = None
    try:
        if not _selection_ready(expected, valid, limit):
            return None
        connection = _new_a11y_connection()
        if connection is None:
            return None
        return _read_selection(_DbusTree(connection), expected, valid, limit)
    except Exception:  # noqa: BLE001 — provider errors may contain document text
        return None
    finally:
        if connection is not None:
            try:
                connection.close_sync(None)
            except Exception:  # noqa: BLE001 — cleanup never exposes provider diagnostics
                pass


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
