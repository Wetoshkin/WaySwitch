"""Охрана сессии: блокировка экрана и сон.

Пока экран заблокирован, исправлять нельзя: evdev продолжает получать
нажатия, и пароль, набранный в экране блокировки (например, русское слово
латиницей), демон «исправил» бы прямо в поле пароля. Источников три,
блокировкой считается любой из них:

- служба хранителя экрана: org.gnome.ScreenSaver (GNOME) и
  org.freedesktop.ScreenSaver (KDE и другие) — сигнал ActiveChanged;
- logind: LockedHint и Active своей графической сессии, PrepareForSleep;
- процесс экрана блокировки: swaylock, hyprlock, gtklock, waylock и
  kscreenlocker_greet. Нужен для Sway, Hyprland, labwc и Wayfire — их
  экраны блокировки logind не сообщают.

Своя сессия logind ищется не по PID: служба systemd --user живёт вне
сессии, и GetSessionByPID для неё не находит ничего.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable

log = logging.getLogger("wayswitch")

# Имена процессов (/proc/PID/comm, до 15 символов) экранов блокировки Wayland.
LOCKERS = frozenset({"swaylock", "hyprlock", "gtklock", "waylock", "kscreenlocker_g",
                     "kscreenlocker_greet", "swaylock-effec", "nwg-lock", "chayang"})
SCREENSAVERS = (("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver"),
                ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
                 "org.freedesktop.ScreenSaver"))
POLL_MS = 1000


def locker_running(names: Iterable[str]) -> bool:
    """Есть ли среди имён процессов экран блокировки. Чистая функция."""
    return any(name.strip() in LOCKERS for name in names)


def process_names(proc: str = "/proc", uid: int | None = None) -> list[str]:
    """Имена процессов пользователя (comm)."""
    uid = os.getuid() if uid is None else uid
    names = []
    try:
        entries = os.listdir(proc)
    except OSError:
        return names
    for entry in entries:
        if not entry.isdigit():
            continue
        path = os.path.join(proc, entry)
        try:
            if os.stat(path).st_uid != uid:
                continue
            with open(os.path.join(path, "comm"), encoding="utf-8", errors="replace") as f:
                names.append(f.read())
        except OSError:
            continue
    return names


class SessionGuard:
    def __init__(self, on_locked: Callable[[bool], None], on_sleep: Callable[[bool], None]):
        self.on_locked = on_locked
        self.on_sleep = on_sleep
        self.locked = False
        self._screensaver: dict[str, bool] = {}
        self._logind_locked = False
        self._locker = False
        self._proxies = []

    def start(self) -> None:
        from gi.repository import Gio, GLib

        for name, path, iface in SCREENSAVERS:
            try:
                proxy = Gio.DBusProxy.new_for_bus_sync(
                    Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None, name,
                    path, iface, None)
                proxy.connect("g-signal", self._on_ss_signal, name)
                self._proxies.append(proxy)
                if proxy.get_name_owner():
                    self._screensaver[name] = bool(proxy.call_sync(
                        "GetActive", None, 0, 1000, None).unpack()[0])
            except Exception as e:  # noqa: BLE001 — службы может не быть в этой среде
                log.debug("%s недоступен: %s", name, e)
        try:
            system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            manager = Gio.DBusProxy.new_sync(system, Gio.DBusProxyFlags.NONE, None,
                                             "org.freedesktop.login1", "/org/freedesktop/login1",
                                             "org.freedesktop.login1.Manager", None)
            manager.connect("g-signal", self._on_manager_signal)
            path = self._find_session(system, manager)
            self._session = Gio.DBusProxy.new_sync(system, Gio.DBusProxyFlags.NONE, None,
                                                   "org.freedesktop.login1", path,
                                                   "org.freedesktop.login1.Session", None)
            self._session.connect("g-properties-changed", self._on_session_props)
            self._read_session()
        except Exception as e:  # noqa: BLE001
            log.warning("logind недоступен, охрана без него: %s", e)
        GLib.timeout_add(POLL_MS, self._poll_lockers)
        self._poll_lockers()
        self._update()

    @staticmethod
    def _find_session(system, manager) -> str:
        """Объект своей графической сессии в logind."""
        from gi.repository import Gio, GLib

        sid = os.environ.get("XDG_SESSION_ID")
        if sid:
            (path,) = manager.call_sync("GetSession", GLib.Variant("(s)", (sid,)),
                                        0, 1000, None).unpack()
            return path
        (user,) = manager.call_sync("GetUser", GLib.Variant("(u)", (os.getuid(),)),
                                    0, 1000, None).unpack()
        props = Gio.DBusProxy.new_sync(system, Gio.DBusProxyFlags.NONE, None,
                                       "org.freedesktop.login1", user,
                                       "org.freedesktop.login1.User", None)
        display = props.get_cached_property("Display")
        if display is not None:
            _sid, path = display.unpack()
            if path and path != "/":
                return path
        (path,) = manager.call_sync("GetSessionByPID", GLib.Variant("(u)", (os.getpid(),)),
                                    0, 1000, None).unpack()
        return path

    def _read_session(self) -> None:
        locked = self._session.get_cached_property("LockedHint")
        active = self._session.get_cached_property("Active")
        self._logind_locked = bool(locked and locked.unpack()) or bool(active is not None
                                                                       and not active.unpack())

    def _poll_lockers(self) -> bool:
        running = locker_running(process_names())
        if running != self._locker:
            self._locker = running
            self._update()
        return True

    def _on_ss_signal(self, _p, _s, signal: str, params, name: str) -> None:
        if signal == "ActiveChanged":
            self._screensaver[name] = bool(params.unpack()[0])
            self._update()

    def _on_session_props(self, _p, _changed, _invalidated) -> None:
        self._read_session()
        self._update()

    def _on_manager_signal(self, _p, _s, signal: str, params) -> None:
        if signal == "PrepareForSleep":
            self.on_sleep(bool(params.unpack()[0]))

    def _update(self) -> None:
        locked = any(self._screensaver.values()) or self._logind_locked or self._locker
        if locked != self.locked:
            self.locked = locked
            log.info("сессия %s", "заблокирована" if locked else "активна")
            self.on_locked(locked)
