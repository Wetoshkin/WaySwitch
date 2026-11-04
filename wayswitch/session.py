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
from pathlib import Path

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
    def __init__(self, on_locked: Callable[[bool], None], on_sleep: Callable[[bool], None],
                 *, cosmic: bool = False):
        self.on_locked = on_locked
        self.on_sleep = on_sleep
        self.locked = False
        self._screensaver: dict[str, bool] = {}
        self._logind_locked = False
        self._locker = False
        self._proxies = []
        self._cosmic = cosmic
        self._marker: Path | None = None
        self._marker_locked = False
        self._awaiting_marker = False
        self._greeter_ready = False

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
            self._proxies.append(manager)
            path = self._find_session(system, manager, display_only=self._cosmic)
            self._session = Gio.DBusProxy.new_sync(system, Gio.DBusProxyFlags.NONE, None,
                                                   "org.freedesktop.login1", path,
                                                   "org.freedesktop.login1.Session", None)
            self._session.connect("g-properties-changed", self._on_session_props)
            if self._cosmic:
                self._session.connect("g-signal", self._on_cosmic_signal)
                self._session.connect("notify::g-name-owner", self._lost_cosmic_session)
                self._set_cosmic_session(self._session.get_cached_property("Id").unpack())
            self._read_session()
        except Exception as e:  # noqa: BLE001
            log.warning("logind недоступен, охрана без него: %s", e)
        GLib.timeout_add(POLL_MS, self._poll_lockers)
        self._poll_lockers()
        self._update()

    @staticmethod
    def _find_session(system, manager, *, display_only=False) -> str:
        """Объект своей графической сессии в logind."""
        from gi.repository import Gio, GLib

        sid = os.environ.get("XDG_SESSION_ID")
        if sid and not display_only:
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
        if display_only:
            raise RuntimeError("logind не указал графический сеанс COSMIC")
        (path,) = manager.call_sync("GetSessionByPID", GLib.Variant("(u)", (os.getpid(),)),
                                    0, 1000, None).unpack()
        return path

    def _set_cosmic_session(self, session_id):
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if runtime and session_id and session_id.isalnum():
            self._marker = Path(runtime) / f"cosmic-greeter-{session_id}.lock"

    def input_locked(self) -> bool:
        """cosmic-greeter ставит этот файл до блокировки, но не меняет LockedHint."""
        if self._cosmic:
            locked = True
            if self._marker is not None and self._greeter_ready:
                try:
                    self._marker.lstat()
                    self._awaiting_marker = False
                except FileNotFoundError:
                    if not os.access(self._marker.parent, os.W_OK | os.X_OK):
                        self._awaiting_marker = True
                    locked = self._awaiting_marker
                except OSError:
                    self._awaiting_marker = True
            if self._marker_locked != locked:
                self._marker_locked = locked
                self._update()
        return self.locked

    def _read_session(self) -> None:
        locked = self._session.get_cached_property("LockedHint")
        active = self._session.get_cached_property("Active")
        self._logind_locked = (bool(locked and locked.unpack())
                               or (active is None and self._cosmic)
                               or (active is not None and not active.unpack()))

    def _poll_lockers(self) -> bool:
        names = process_names()
        if self._cosmic:
            self._greeter_ready = "cosmic-greeter" in {n.strip() for n in names}
        self.input_locked()
        running = locker_running(names)
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

    def _on_cosmic_signal(self, _proxy, _sender, signal, _params) -> None:
        if signal == "Lock":
            self._awaiting_marker = True
            self.input_locked()

    def _lost_cosmic_session(self, *_args) -> None:
        # Новый владелец logind мог пропустить Lock / сменить соответствие сеансов.
        self._marker = None
        self.input_locked()

    def _on_manager_signal(self, _p, _s, signal: str, params) -> None:
        if signal == "PrepareForSleep":
            if self._cosmic and params.unpack()[0]:
                self._on_cosmic_signal(None, None, "Lock", None)
            self.on_sleep(bool(params.unpack()[0]))

    def _update(self) -> None:
        locked = (any(self._screensaver.values()) or self._logind_locked
                  or self._locker or self._marker_locked)
        if locked != self.locked:
            self.locked = locked
            log.info("сессия %s", "заблокирована" if locked else "активна")
            self.on_locked(locked)
