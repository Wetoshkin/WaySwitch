"""Чтение устройств ввода через evdev внутри GLib main loop.

Клавиатуры и мыши определяются по возможностям, не по имени. Hot-plug —
монитор каталога /dev/input. SYN_DROPPED → снимок зажатых клавиш.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from wayswitch import keycodes as kc
from wayswitch.actuator import UInputTypist

log = logging.getLogger("wayswitch")
INPUT_DIR = "/dev/input"
KEYBOARD_KEYS = {kc.KEY_A, kc.KEY_Z, kc.KEY_SPACE, kc.KEY_ENTER}
OPEN_DELAY_MS = 300  # udev успевает выставить права


def classify(name: str, key_caps: set[int]) -> str | None:
    if name == UInputTypist.NAME:
        return None
    if KEYBOARD_KEYS <= key_caps:
        return "keyboard"
    if kc.BTN_LEFT in key_caps:
        return "pointer"
    return None


class DeviceWatcher:
    def __init__(self, on_key: Callable[[int, int, int], None], on_click: Callable[[], None],
                 on_resync: Callable[[int, list[int]], None], on_gone: Callable[[int], None],
                 on_caps: Callable[[bool], None]):
        self.on_key, self.on_click = on_key, on_click
        self.on_resync, self.on_gone, self.on_caps = on_resync, on_gone, on_caps
        self._devices: dict[str, tuple] = {}  # путь → (dev, device_id, kind, source_id)
        self._next_id = 1
        self._monitor = None
        self._rescans: list[int] = []

    # --- перечисление -------------------------------------------------------------

    def devices(self) -> list[tuple[str, str, str]]:
        return [(path, d[0].name, d[2]) for path, d in self._devices.items()]

    def start(self) -> None:
        from gi.repository import Gio, GLib

        self.rescan()
        self._monitor = Gio.File.new_for_path(INPUT_DIR).monitor_directory(
            Gio.FileMonitorFlags.NONE, None)
        self._monitor.connect("changed", self._on_dir_changed)
        self._GLib = GLib

    def stop(self) -> None:
        for path in list(self._devices):
            self._close(path)

    def rescan(self) -> None:
        from evdev import list_devices

        for path in list_devices():
            if path not in self._devices:
                self._open(path)

    def schedule_rescans(self, delays_sec=(5, 15, 30)) -> None:
        """После пробуждения Bluetooth-клавиатуры появляются с задержкой."""
        from gi.repository import GLib

        for d in delays_sec:
            GLib.timeout_add_seconds(d, lambda: (self.rescan(), False)[1])

    def _on_dir_changed(self, _mon, file, _other, event_type) -> None:
        from gi.repository import Gio, GLib

        name = file.get_basename() or ""
        if not name.startswith("event"):
            return
        path = os.path.join(INPUT_DIR, name)
        if event_type == Gio.FileMonitorEvent.CREATED:
            GLib.timeout_add(OPEN_DELAY_MS, lambda: (self._open(path), False)[1])
        elif event_type == Gio.FileMonitorEvent.DELETED:
            self._close(path)

    # --- устройства -----------------------------------------------------------

    def _open(self, path: str) -> None:
        from evdev import InputDevice, ecodes
        from gi.repository import GLib

        if path in self._devices:
            return
        try:
            dev = InputDevice(path)
            caps = set(dev.capabilities().get(ecodes.EV_KEY, []))
        except OSError as e:
            log.debug("не открыть %s: %s", path, e)
            return
        kind = classify(dev.name, caps)
        if kind is None:
            dev.close()
            return
        device_id = self._next_id
        self._next_id += 1
        condition = GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR
        source = GLib.io_add_watch(dev.fd, GLib.PRIORITY_DEFAULT, condition,
                                   self._on_readable, path)
        self._devices[path] = (dev, device_id, kind, source)
        log.info("слушаю %s: %s (%s)", kind, dev.name, path)

    def _close(self, path: str) -> None:
        from gi.repository import GLib

        entry = self._devices.pop(path, None)
        if not entry:
            return
        dev, device_id, kind, source = entry
        GLib.source_remove(source)
        try:
            dev.close()
        except OSError:
            pass
        if kind == "keyboard":
            self.on_gone(device_id)
        log.info("устройство отключено: %s", path)

    def _on_readable(self, _fd, condition, path: str) -> bool:
        from evdev import ecodes
        from gi.repository import GLib

        entry = self._devices.get(path)
        if not entry:
            return False
        dev, device_id, kind, _ = entry
        if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
            self._close(path)
            return False
        try:
            events = list(dev.read())
        except OSError:
            self._close(path)
            return False
        for ev in events:
            if ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_DROPPED and kind == "keyboard":
                try:
                    self.on_resync(device_id, list(dev.active_keys()))
                except OSError:
                    self._close(path)
                    return False
                continue
            if ev.type != ecodes.EV_KEY:
                continue
            if kc.is_pointer_button(ev.code):
                if ev.value == 1:
                    self.on_click()
                continue
            if kind != "keyboard":
                continue
            self.on_key(ev.code, ev.value, device_id)
            if ev.code == kc.KEY_CAPSLOCK and ev.value == 0:
                try:
                    self.on_caps(ecodes.LED_CAPSL in dev.leds())
                except OSError:
                    pass
        return True
