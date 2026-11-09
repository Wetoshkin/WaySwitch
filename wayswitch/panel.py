"""Индикатор для панелей без трея: модуль Waybar и короткая сводка состояния.

``wayswitch waybar`` печатает по строке JSON на каждое изменение — формат
custom-модуля Waybar с ``"return-type": "json"``: поля ``text``, ``tooltip``,
``class``. После исправления модуль на FLASH_SEC показывает, что было
исправлено (класс ``fixed``), после отката — ``undone``, затем возвращается
к раскладке. Цвета задаются CSS-классами, поэтому модуль подходит к любой
теме панели.

Модуль чистый: состояние → словарь. Поток событий D-Bus — в cli.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from wayswitch.gui.texts import BACKEND_HUMAN, layout_human, status_view
from wayswitch.i18n import gettext as _
from wayswitch.i18n import ngettext

# Сколько секунд модуль показывает последнее исправление.
FLASH_SEC = 1.5

LAYOUT_SHORT = {"ru": "RU", "latin": "EN"}

@dataclass(frozen=True)
class Flash:
    """Что показать поверх раскладки: исправление или откат."""

    kind: str          # "fixed" | "undone"
    original: str
    fixed: str


def tooltip(status: dict) -> str:
    backend = _(BACKEND_HUMAN.get(str(status.get("backend") or ""), ""))
    lines = ["WaySwitch — " + status_view(status).title]
    where = _("Layout: {layout}").format(layout=layout_human(status.get("layout")))
    if backend:
        where += " · " + backend
    lines.append(where)
    auto = int(status.get("corrections_total", 0) or 0)
    manual = int(status.get("manual_total", 0) or 0)
    lines.append(_("This session: {auto} automatic, {manual} manual").format(
        auto=auto, manual=manual))
    return "\n".join(lines)


def waybar_payload(status: dict | None, flash: Flash | None = None) -> dict:
    """Строка для Waybar по состоянию демона и последнему событию."""
    if status is None:
        return {"text": "WaySwitch", "tooltip": _("WaySwitch is not running"), "class": "off",
                "alt": "off"}
    layout = LAYOUT_SHORT.get(str(status.get("layout") or ""), "··")
    if status.get("paused"):
        return {"text": layout, "tooltip": tooltip(status), "class": "paused",
                "alt": "paused"}
    if flash is not None and flash.kind == "fixed":
        return {"text": f"{flash.original} → {flash.fixed}", "tooltip": tooltip(status),
                "class": "fixed", "alt": "fixed"}
    if flash is not None and flash.kind == "undone":
        return {"text": _("{word} — remembered").format(word=flash.fixed),
                "tooltip": tooltip(status),
                "class": "undone", "alt": "undone"}
    return {"text": layout, "tooltip": tooltip(status), "class": "on", "alt": "on"}


def status_line(status: dict | None) -> str:
    """Одна строка для `wayswitch status` по-человечески."""
    if status is None:
        return _("WaySwitch is not running")
    total = int(status.get("corrections_total", 0) or 0) + int(status.get("manual_total", 0) or 0)
    summary = ngettext("{count} correction this session", "{count} corrections this session",
                       total).format(count=total)
    where = _("Layout: {layout}").format(layout=layout_human(status.get("layout")))
    return f"WaySwitch: {status_view(status).title}; {where}; {summary}"


WAYBAR_CONFIG = """\
"custom/wayswitch": {
  "exec": "wayswitch waybar",
  "return-type": "json",
  "on-click": "wayswitch toggle",
  "on-click-right": "wayswitch gui"
}"""

WAYBAR_CSS = """\
#custom-wayswitch        { padding: 0 10px; border-radius: 7px; }
#custom-wayswitch.fixed  { background: #c46a32; color: #fff; }
#custom-wayswitch.undone { background: rgba(143,160,115,.3); }
#custom-wayswitch.paused { opacity: .5; }
#custom-wayswitch.off    { color: #ff7b63; }"""

HYPRLAND_SNIPPET = """\
# рабочие языки ru/en; другие раскладки можно оставить
input {
    kb_layout = us,ru
}
# пауза (последнее слово исправляет жест — двойной Shift)
bind = SUPER SHIFT, W, exec, wayswitch toggle
# окно параметров — плавающее
windowrule = float, class:^(io\\.github\\.Wetoshkin\\.WaySwitch\\.Settings)$
windowrule = size 640 740, class:^(io\\.github\\.Wetoshkin\\.WaySwitch\\.Settings)$"""

SWAY_SNIPPET = """\
# рабочие языки ru/en; другие раскладки можно оставить
input type:keyboard {
    xkb_layout us,ru
}
# пауза (последнее слово исправляет жест — двойной Shift)
bindsym $mod+Shift+w exec wayswitch toggle
# окно параметров — плавающее
for_window [app_id="io.github.Wetoshkin.WaySwitch.Settings"] floating enable, resize set 640 740"""
