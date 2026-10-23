"""Значок в трее по протоколу StatusNotifierItem + com.canonical.dbusmenu.

На GNOME виден при расширении AppIndicator (в Ubuntu включено). Реализованы
только те методы, которые вызывает хост: свойства SNI, Activate, меню.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import NamedTuple

from wayswitch.gui import texts

log = logging.getLogger("wayswitch.gui")

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <method name="Activate">
      <arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/>
    </method>
    <method name="ContextMenu">
      <arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/>
    </method>
    <method name="Scroll">
      <arg type="i" name="delta" direction="in"/><arg type="s" name="orientation" direction="in"/>
    </method>
    <signal name="NewIcon"/><signal name="NewTitle"/><signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>
</node>
"""
MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="Status" type="s" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/><arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/><arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/><arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/><arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/><arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/><arg type="i" name="parent"/>
    </signal>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/><arg type="a(ias)" name="removedProps"/>
    </signal>
  </interface>
</node>
"""
MENU_PATH = "/ru/siberia/WaySwitch/Menu"
ITEM_PATH = "/StatusNotifierItem"


class MenuItem(NamedTuple):
    ident: str
    label: str = ""
    enabled: bool = True
    kind: str = "standard"          # "standard" | "separator"
    toggle: bool | None = None      # галочка у пункта-переключателя
    icon: str = ""


def menu_layout(status: dict | None, update: str | None = None) -> list[MenuItem]:
    """Пункты меню значка. Зависят от состояния демона и наличия обновления."""
    running = status is not None
    auto = bool(status and status.get("auto_correct"))
    paused = bool(status and status.get("paused"))
    items = [
        MenuItem("summary", "WaySwitch — " + texts.tray_summary(status), False),
        MenuItem("sep1", kind="separator"),
        MenuItem("auto", "Автоисправление", running, toggle=auto),
        MenuItem("pause", "Возобновить" if paused else "Пауза", running,
                 icon="media-playback-start-symbolic" if paused
                 else "media-playback-pause-symbolic"),
        MenuItem("fix_selection", "Исправить выделенное", running,
                 icon="edit-select-all-symbolic"),
        MenuItem("sep2", kind="separator"),
    ]
    if update:
        items.append(MenuItem("update", f"Вышла версия {update}…",
                              icon="software-update-available-symbolic"))
    items += [
        MenuItem("settings", "Открыть WaySwitch", icon="window-new-symbolic"),
        MenuItem("quit", "Выйти", icon="application-exit-symbolic"),
    ]
    return items


# Постоянные номера пунктов dbusmenu (0 — корень).
MENU_IDS = {ident: n for n, ident in enumerate(
    ("summary", "sep1", "auto", "pause", "fix_selection", "sep2", "update",
     "settings", "quit"), start=1)}

# Имена значков из packaging/icons: ставятся установщиком в hicolor.
INSTALLED_ICON = "ru.siberia.WaySwitch"
TRAY_ICON = "ru.siberia.WaySwitch-symbolic"
TRAY_PAUSED_ICON = "ru.siberia.WaySwitch-paused-symbolic"


class StatusNotifier:
    def __init__(self, actions: dict[str, Callable[[], None]]):
        # ident пункта меню → действие; «settings» ещё и щелчок по значку.
        self._actions = actions
        self._status: dict | None = None
        self._update: str | None = None
        self._revision = 1
        self._conn = None

    def start(self) -> None:
        from gi.repository import Gio

        self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._conn.register_object(ITEM_PATH, sni, self._sni_call, self._sni_get, None)
        self._conn.register_object(MENU_PATH, menu, self._menu_call, self._menu_get, None)
        name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._name = name
        Gio.bus_own_name_on_connection(self._conn, name, Gio.BusNameOwnerFlags.NONE,
                                       lambda *_: self._register(name), None)
        # Панель (plasmashell, waybar, расширение AppIndicator) могла стартовать
        # позже нас или перезапуститься — регистрируемся при каждом появлении
        # StatusNotifierWatcher, иначе значок пропал бы до перезапуска WaySwitch.
        Gio.bus_watch_name_on_connection(self._conn, "org.kde.StatusNotifierWatcher",
                                         Gio.BusNameWatcherFlags.NONE,
                                         lambda *_: self._register(self._name), None)

    def _register(self, name: str) -> None:
        from gi.repository import Gio, GLib

        try:
            self._conn.call_sync("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                                 "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                                 GLib.Variant("(s)", (name,)), None, Gio.DBusCallFlags.NONE,
                                 2000, None)
        except Exception as e:  # noqa: BLE001
            log.info("трей недоступен (нет StatusNotifierWatcher): %s", e)

    # --- SNI ---------------------------------------------------------------------

    def _icon(self) -> str:
        """Имя значка: монохромный свой, если тема его знает, иначе системный.

        Цветной значок приложения в панели GNOME теряется — там все значки
        symbolic, поэтому для панели есть отдельная монохромная пара.
        """
        if self._status is None or self._status.get("paused"):
            return (TRAY_PAUSED_ICON if self._theme_has(TRAY_PAUSED_ICON)
                    else "media-playback-pause-symbolic")
        return TRAY_ICON if self._theme_has(TRAY_ICON) else "input-keyboard-symbolic"

    @staticmethod
    def _theme_has(name: str) -> bool:
        """Есть ли значок в теме: не у всех он установлен (запуск из исходников)."""
        try:
            from gi.repository import Gdk, Gtk

            display = Gdk.Display.get_default()
            if display is None:
                return False
            theme = Gtk.IconTheme.get_for_display(display)
            return theme.has_icon(name)
        except Exception:  # noqa: BLE001 — без GTK просто берём системный значок
            return False

    def _sni_get(self, _c, _s, _p, _i, prop: str):
        from gi.repository import GLib

        tooltip = texts.tray_summary(self._status, self._update)
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "wayswitch"),
            "Title": GLib.Variant("s", "WaySwitch"),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", self._icon()),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], "WaySwitch", tooltip)),
            "Menu": GLib.Variant("o", MENU_PATH),
            "ItemIsMenu": GLib.Variant("b", True),
        }
        return values.get(prop)

    def _sni_call(self, _c, _s, _p, _i, method: str, _params, invocation) -> None:
        if method == "Activate":
            self._activate("settings")
        invocation.return_value(None)

    # --- dbusmenu ----------------------------------------------------------------

    def _items(self):
        from gi.repository import GLib

        out = []
        for item in menu_layout(self._status, self._update):
            i = MENU_IDS[item.ident]
            if item.kind == "separator":
                props = {"type": GLib.Variant("s", "separator")}
            else:
                props = {"label": GLib.Variant("s", item.label),
                         "enabled": GLib.Variant("b", item.enabled)}
                if item.toggle is not None:
                    props["toggle-type"] = GLib.Variant("s", "checkmark")
                    props["toggle-state"] = GLib.Variant("i", 1 if item.toggle else 0)
                if item.icon:
                    props["icon-name"] = GLib.Variant("s", item.icon)
            out.append((i, item.ident, props))
        return out

    def _activate(self, ident: str) -> None:
        action = self._actions.get(ident)
        if action is not None:
            action()

    def _menu_get(self, _c, _s, _p, _i, prop: str):
        from gi.repository import GLib

        return {"Version": GLib.Variant("u", 3),
                "Status": GLib.Variant("s", "normal")}.get(prop)

    def _menu_call(self, _c, _s, _p, _i, method: str, params, invocation) -> None:
        from gi.repository import GLib

        items = self._items()
        if method == "GetLayout":
            children = [GLib.Variant("(ia{sv}av)", (i, props, [])) for i, _, props in items]
            root_props = {"children-display": GLib.Variant("s", "submenu")}
            root = GLib.Variant("(ia{sv}av)", (0, root_props, children))
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("u", self._revision), root))
        elif method == "GetGroupProperties":
            ids = set(params.unpack()[0])
            props = [(i, p) for i, _, p in items if not ids or i in ids]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (props,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            for i, _, p in items:
                if i == item_id and name in p:
                    invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("v", p[name])))
                    return
            invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", name)
        elif method == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id == "clicked":
                for i, ident, _ in items:
                    if i == item_id:
                        self._activate(ident)
            invocation.return_value(None)
        elif method == "EventGroup":
            for item_id, event_id, _d, _t in params.unpack()[0]:
                if event_id == "clicked":
                    for i, ident, _ in items:
                        if i == item_id:
                            self._activate(ident)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def update(self, status: dict | None, update: str | None = None) -> None:
        from gi.repository import GLib

        self._status = status
        self._update = update
        self._revision += 1
        if self._conn:
            self._conn.emit_signal(None, MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                                   GLib.Variant("(ui)", (self._revision, 0)))
            self._conn.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewIcon", None)
            self._conn.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip",
                                   None)
