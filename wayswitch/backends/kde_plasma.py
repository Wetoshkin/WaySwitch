"""Раскладка в KDE Plasma на Wayland через D-Bus KWin.

KWin отдаёт раскладки в ``org.kde.keyboard`` (объект ``/Layouts``, интерфейс
``org.kde.KeyboardLayouts``):

- ``getLayout() -> u`` — индекс текущей раскладки;
- ``setLayout(u) -> b`` — переключить;
- ``getLayoutsList() -> a(sss)`` — (короткое имя, отображаемое, полное);
- сигнал ``layoutChanged(u)`` — раскладку сменили (в том числе руками).

Печать в KDE делает не расширение, а демон через uinput: сканкоды одинаково
работают во всех Wayland-средах, но требуют уже применённой раскладки —
отсюда пауза ``settle_ms`` перед печатью.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.keymap import LayoutSpec

SERVICE = "org.kde.keyboard"
OBJECT = "/Layouts"
INTERFACE = "org.kde.KeyboardLayouts"
def kxkbrc_path() -> Path:
    import os

    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "kxkbrc"


def parse_layouts_list(rows: list[tuple[str, str, str]],
                       variants: list[str] | None = None) -> list[LayoutSpec]:
    """a(sss) от KWin → LayoutSpec в порядке индексов KWin.

    KWin отдаёт только короткое имя раскладки («ru»), без варианта: «ru
    (phonetic)» и «us (dvorak)» превращались в обычные ru/us, и uinput
    печатал сканкоды по чужой карте. Варианты берём из kxkbrc (VariantList) —
    если их число совпадает с числом раскладок.
    """
    specs = [LayoutSpec.parse(short) for short, _display, _long in rows]
    if variants is not None and len(variants) == len(specs):
        specs = [LayoutSpec(spec.layout, variant.strip()) if variant.strip() else spec
                 for spec, variant in zip(specs, variants, strict=True)]
    return specs


def _kxkbrc_value(key: str, path: Path | None = None) -> str | None:
    target = kxkbrc_path() if path is None else path
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        name, sep, value = line.partition("=")
        if section == "Layout" and sep and name.strip() == key:
            return value
    return None


def read_kxkbrc_variants(path: Path | None = None) -> list[str] | None:
    """VariantList из kxkbrc: «,phonetic» → ["", "phonetic"]; нет ключа — None."""
    value = _kxkbrc_value("VariantList", path)
    if value is None:
        return None
    return value.split(",")


def read_kxkbrc_options(path: Path | None = None) -> list[str]:
    """Опции xkb из kxkbrc (``[Layout] Options=grp:alt_shift_toggle,...``).

    В KDE их нет в gsettings, а keymap их учитывает (compose, переключение
    групп). Файла нет — опций нет.
    """
    value = _kxkbrc_value("Options", path)
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


class KdePlasmaBackend(LayoutBackend):
    name = "kde"
    supports_auto = True

    def __init__(self, settle_ms: int = 120, caller=None, subscriber=None,
                 options_reader=read_kxkbrc_options):
        self._settle = settle_ms
        self._call_impl = caller if caller is not None else _dbus_caller()
        self._subscriber = subscriber if subscriber is not None else _dbus_subscriber()
        try:
            (rows,) = self._call_impl("getLayoutsList", None, "(a(sss))")
        except Exception as e:  # noqa: BLE001 — нет шины/службы: бэкенд недоступен
            raise BackendError(f"KWin не отдаёт org.kde.keyboard: {e}") from None
        self._specs = parse_layouts_list(rows, read_kxkbrc_variants())
        if len(self._specs) < 2:
            raise BackendError("в KDE настроена одна раскладка")
        self._callbacks: list[Callable[[int, bool], None]] = []
        self._expected: int | None = None
        self._current = self._query()
        self._options = list(options_reader())
        self._unsubscribe = self._subscriber(self._on_signal)

    # --- чтение состояния -------------------------------------------------------

    def _query(self) -> int | None:
        try:
            (index,) = self._call_impl("getLayout", None, "(u)")
        except Exception:  # noqa: BLE001 — состояние неизвестно, авторежим не наш
            return None
        return index if 0 <= index < len(self._specs) else None

    def layouts(self) -> list[LayoutSpec]:
        return list(self._specs)

    def xkb_options(self) -> list[str]:
        return list(self._options)

    def current(self) -> int | None:
        return self._current

    # --- переключение -----------------------------------------------------------

    def set(self, index: int) -> None:
        try:
            (ok,) = self._call_impl("setLayout", _variant_u(index), "(b)")
        except Exception as e:  # noqa: BLE001
            raise BackendError(f"setLayout({index}): {e}") from None
        if not ok:
            raise BackendError(f"KWin отказал в переключении на раскладку {index}")
        self._expected = index
        self._current = index

    def wait_applied(self, index: int, timeout: float) -> bool:
        # Переключение в KWin синхронное, но приложению нужно время принять
        # новый keymap, иначе теряется первая/последняя клавиша печати.
        if self._settle > 0:
            time.sleep(self._settle / 1000)
        self._current = self._query()
        return self._current == index

    def on_change(self, cb: Callable[[int, bool], None]) -> None:
        self._callbacks.append(cb)

    def _on_signal(self, index: int) -> None:
        if not 0 <= index < len(self._specs):
            return
        external = self._expected != index
        self._expected = None
        self._current = index
        for cb in self._callbacks:
            cb(index, external)

    def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()


def _variant_u(index: int):
    from gi.repository import GLib

    return GLib.Variant("(u)", (index,))


def _dbus_caller():
    """Функция вызова методов KWin по сессионной шине."""
    from gi.repository import Gio, GLib

    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(method: str, arg, reply_sig: str):
        return conn.call_sync(SERVICE, OBJECT, INTERFACE, method, arg,
                              GLib.VariantType(reply_sig), Gio.DBusCallFlags.NONE,
                              1500, None).unpack()

    return call


def _dbus_subscriber():
    from gi.repository import Gio

    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def subscribe(callback):
        def handler(_conn, _sender, _path, _iface, _signal, params):
            (index,) = params.unpack()
            callback(index)
            return None

        sub = conn.signal_subscribe(SERVICE, INTERFACE, "layoutChanged", OBJECT, None,
                                    Gio.DBusSignalFlags.NONE, handler)
        return lambda: conn.signal_unsubscribe(sub)

    return subscribe


def kde_available() -> bool:
    """Отвечает ли KWin на шине (KDE Plasma запущен)."""
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                          "org.freedesktop.DBus", "NameHasOwner",
                          GLib.Variant("(s)", (SERVICE,)), GLib.VariantType("(b)"),
                          Gio.DBusCallFlags.NONE, 1000, None)
    return bool(reply.unpack()[0])
