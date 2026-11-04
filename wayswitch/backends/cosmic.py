"""Текущая группа COSMIC, общая для устройств одного клавиатурного seat."""

import os
import stat
import time

from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.backends.cosmic_wayland import MANAGER, CosmicTransport
from wayswitch.keymap import XkbKeymap


def read_keymap(fmt, fd, size):
    try:
        info = os.fstat(fd)
        if (fmt != 1 or not 1 < size <= 4 * 1024 * 1024
                or not stat.S_ISREG(info.st_mode) or info.st_size < size):
            raise BackendError("неподдерживаемая карта wl_keyboard")
        if hasattr(os, "pread"):
            # fd может иметь общий offset с композитором и другими клиентами.
            data = os.pread(fd, size, 0)
        else:  # Windows: обычный файл в тесте
            os.lseek(fd, 0, os.SEEK_SET)
            data = os.read(fd, size)
        if len(data) != size or b"\0" in data[:-1]:
            raise BackendError("повреждённая карта wl_keyboard")
        # Smithay присылает TEXT_V1 без NUL, Mutter — со знаком конца.
        return data.removesuffix(b"\0")
    finally:
        os.close(fd)


class CosmicBackend(LayoutBackend):
    name = "cosmic"
    supports_auto = True

    def __init__(self, *, watch=True):
        from gi.repository import GLib

        self._group = self._pending = self._callback = self.error = None
        self.keymap = self._raw_map = self._source = None
        self.transport = CosmicTransport(self)
        try:
            deadline = time.monotonic() + 2
            while self.current() is None:
                if self.error:
                    raise BackendError(self.error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BackendError(f"нет {MANAGER.decode()} или карты и группы клавиатуры")
                self.transport.pump(remaining)
            if watch:
                self._source = GLib.io_add_watch(
                    self.transport.fd, GLib.PRIORITY_DEFAULT,
                    GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP, self._readable)
        except Exception:
            self.close()
            raise

    def keyboard_map(self, fmt, fd, size):
        data = read_keymap(fmt, fd, size)
        if self.error or data == self._raw_map:
            return
        if self.keymap is not None:
            self.invalidate("изменилась карта wl_keyboard")
            return
        self.keymap = XkbKeymap.from_buffer(data)
        self._raw_map = data
        if self._group is not None and self._group >= self.keymap.num_groups:
            self.invalidate("группа вне карты wl_keyboard")

    def group(self, index):
        if self.error:
            return
        if self.keymap and index >= self.keymap.num_groups:
            self.invalidate("группа вне карты wl_keyboard")
            return
        previous, self._group = self._group, index
        if self._pending is not None and self._pending != index:
            self.invalidate("группа изменилась во время переключения")
            return
        external = self._pending != index
        self._pending = None
        if self._callback and previous != index:
            self._callback(index, external)

    def invalidate(self, reason):
        if self.error is None:
            self.error = reason
            self.supports_auto = False
            if self._callback:
                self._callback(None, True)
            self.layouts_changed()

    def _pump(self, timeout):
        try:
            self.transport.pump(timeout)
        except (OSError, BackendError) as exc:
            self.invalidate(str(exc))

    def _readable(self, *_args):
        self._pump(0)
        if self.error:
            self._source = None
            return False
        return True

    def current(self):
        if self.error or self.keymap is None or self._pending is not None:
            return None
        return self._group

    def set(self, index):
        if not self.error:
            previous = self._group
            self._pump(0)
            if previous != self._group and self._group != index:
                raise BackendError("раскладка изменилась перед переключением")
        if self.error or self._group is None or not 0 <= index < self.keymap.num_groups:
            raise BackendError(self.error or "неизвестная группа COSMIC")
        if self._pending is not None:
            if self._pending == index:
                return
            self.invalidate("предыдущее переключение ещё не подтверждено")
            raise BackendError(self.error)
        if self._group != index:
            self._pending = index
            try:
                self.transport.set_group(index)
            except (OSError, BackendError) as exc:
                self.invalidate(str(exc))
                raise

    def wait_applied(self, index, timeout):
        deadline = time.monotonic() + timeout
        while not self.error and self._pending == index:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.invalidate("COSMIC не подтвердил переключение")
                return False
            self._pump(remaining)
        return self._pending is None and self.current() == index

    def on_change(self, cb):
        self._callback = cb

    def layouts(self):
        return self.keymap.specs

    def xkb_options(self):
        return None  # карта содержит символы, но не имена RMLVO-опций

    def close(self):
        if self._source is not None:
            from gi.repository import GLib

            GLib.source_remove(self._source)
            self._source = None
        self.transport.close()
        self.error = "соединение COSMIC закрыто"
