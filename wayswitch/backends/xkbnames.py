"""Названия раскладок XKB: «Russian» → ru, «English (Dvorak)» → us(dvorak).

Sway и Hyprland отдают текущую раскладку описанием из базы XKB, а не
идентификатором. Описания берём из того же файла, по которому их строит
libxkbcommon: /usr/share/X11/xkb/rules/evdev.xml.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from wayswitch.keymap import LayoutSpec

RULES_PATHS = (Path("/usr/share/X11/xkb/rules/evdev.xml"),
               Path("/usr/share/xkeyboard-config-2/rules/evdev.xml"),
               Path("/usr/local/share/X11/xkb/rules/evdev.xml"))


def parse_rules(xml_text: str) -> dict[str, LayoutSpec]:
    """Описание → LayoutSpec для всех раскладок и вариантов из evdev.xml."""
    root = ET.fromstring(xml_text)
    out: dict[str, LayoutSpec] = {}
    for layout in root.iter("layout"):
        item = layout.find("configItem")
        if item is None:
            continue
        name = (item.findtext("name") or "").strip()
        desc = (item.findtext("description") or "").strip()
        if name and desc:
            out.setdefault(desc, LayoutSpec(name))
        for variant in layout.iter("variant"):
            vitem = variant.find("configItem")
            if vitem is None:
                continue
            vname = (vitem.findtext("name") or "").strip()
            vdesc = (vitem.findtext("description") or "").strip()
            if name and vname and vdesc:
                out.setdefault(vdesc, LayoutSpec(name, vname))
    return out


@lru_cache(maxsize=1)
def _system_rules() -> dict[str, LayoutSpec]:
    for path in RULES_PATHS:
        try:
            return parse_rules(path.read_text(encoding="utf-8"))
        except (OSError, ET.ParseError):
            continue
    return {}


def spec_for_description(desc: str,
                         rules: dict[str, LayoutSpec] | None = None) -> LayoutSpec | None:
    table = _system_rules() if rules is None else rules
    return table.get(desc.strip())


def parse_layout_list(layouts: str, variants: str = "") -> list[LayoutSpec]:
    """«us,ru» + «,phonetic» → [us, ru(phonetic)] (формат конфигов композиторов)."""
    names = [p.strip() for p in layouts.split(",") if p.strip()]
    vars_ = [p.strip() for p in variants.split(",")] if variants else []
    vars_ += [""] * (len(names) - len(vars_))
    return [LayoutSpec(n, v) for n, v in zip(names, vars_, strict=False)]
