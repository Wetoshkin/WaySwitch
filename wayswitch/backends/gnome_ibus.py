"""Раскладка через IBus: GNOME на Wayland ведёт xkb-раскладки как движки xkb:*."""

from __future__ import annotations

import time
from collections.abc import Callable

from wayswitch.backends import gnome_common
from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.keymap import LayoutSpec


class GnomeIbusBackend(LayoutBackend):
    name = "ibus"
    supports_auto = True

    def __init__(self, settle_ms: int = 30):
        import gi

        gi.require_version("IBus", "1.0")
        from gi.repository import GLib, IBus

        self._GLib = GLib
        self._settle = settle_ms / 1000.0
        self._specs = gnome_common.read_sources()
        IBus.init()  # документация IBus требует вызвать до создания IBus.Bus
        self._bus = IBus.Bus()
        if not self._bus.is_connected():
            raise BackendError("IBus не отвечает (ibus-daemon не запущен?)")
        self._bus.set_watch_ibus_signal(True)
        self._bus.connect("global-engine-changed", self._on_engine_changed)
        self._callbacks: list[Callable[[int, bool], None]] = []
        self._current: int | None = None
        self._expected: int | None = None  # индекс, который выставили мы
        # Список движков меняется только при (пере)установке ibus, кэшируем на
        # весь срок жизни бэкенда — иначе _engine_suffix() дёргал бы D-Bus на
        # каждое переключение раскладки.
        self._engine_names: list[str] = [desc.get_name() for desc in self._bus.list_engines()]
        self._refresh()

    def _refresh(self) -> None:
        desc = self._bus.get_global_engine()
        name = desc.get_name() if desc else ""
        self._current = gnome_common.index_of_engine(name, self._specs)

    def _on_engine_changed(self, _bus, name: str) -> None:
        index = gnome_common.index_of_engine(name, self._specs)
        external = index != self._expected
        self._expected = None
        self._current = index
        if index is not None:
            for cb in self._callbacks:
                cb(index, external)

    def layouts(self) -> list[LayoutSpec]:
        return list(self._specs)

    def xkb_options(self) -> list[str]:
        return gnome_common.read_xkb_options()

    def current(self) -> int | None:
        return self._current

    def set(self, index: int) -> None:
        self._expected = index
        try:
            engine = gnome_common.engine_name_for(self._specs[index]) + self._engine_suffix(index)
            if not self._bus.set_global_engine(engine):
                raise BackendError("IBus отказался переключить движок")
        except Exception:
            self._expected = None
            raise

    def _engine_suffix(self, index: int) -> str:
        """IBus требует полное имя (xkb:ru::rus); ищем его в кэше списка движков."""
        prefix = gnome_common.engine_name_for(self._specs[index])
        for name in self._engine_names:
            if name.startswith(prefix):
                return name[len(prefix):]
        raise BackendError(f"в IBus нет движка для раскладки {self._specs[index].xkb_id}")

    def wait_applied(self, index: int, timeout: float) -> bool:
        # Прокрутка главного цикла здесь может реентрантно доставить колбэки
        # (в т.ч. физические события клавиатуры выше по стеку). Это безопасно
        # только потому, что вызывающий контроллер держит self.busy на время
        # всего вызова set()/wait_applied() и глотает реентрантный ввод сам.
        ctx = self._GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while self._current != index:
            if time.monotonic() > deadline:
                return False
            ctx.iteration(False)
            time.sleep(0.001)
        # Сигнал пришёл от ibus-daemon; Shell применяет раскладку чуть позже.
        time.sleep(self._settle)
        return True

    def on_change(self, cb: Callable[[int, bool], None]) -> None:
        self._callbacks.append(cb)
