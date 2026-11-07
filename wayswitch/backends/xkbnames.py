"""Названия раскладок XKB: «Russian» → ru, «English (Dvorak)» → us(dvorak).

Sway и Hyprland отдают текущую раскладку описанием из базы XKB, а не
идентификатором. Описания берём из того же файла, по которому их строит
libxkbcommon: /usr/share/X11/xkb/rules/evdev.xml.
"""

from __future__ import annotations

import os
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
    root = os.environ.get("XKB_CONFIG_ROOT")
    paths = (Path(root) / "rules/evdev.xml", *RULES_PATHS) if root else RULES_PATHS
    for path in paths:
        try:
            return parse_rules(path.read_text(encoding="utf-8"))
        except (OSError, ET.ParseError):
            continue
    return {}


def spec_for_description(desc: str,
                         rules: dict[str, LayoutSpec] | None = None) -> LayoutSpec | None:
    table = _system_rules() if rules is None else rules
    return table.get(desc.strip())


def parse_languages(xml_text: str) -> dict[LayoutSpec, frozenset[str]]:
    """Языки XKB, с наследованием языка базовой раскладки для вариантов."""
    def languages(item):
        return frozenset(n.text.strip() for n in item.findall("languageList/iso639Id")
                         if n.text and n.text.strip())

    out = {}
    for layout in ET.fromstring(xml_text).iter("layout"):
        item = layout.find("configItem")
        if item is None:
            continue
        name = (item.findtext("name") or "").strip()
        base = languages(item)
        out[LayoutSpec(name)] = base
        for variant in layout.iter("variant"):
            item = variant.find("configItem")
            if item is not None:
                vname = (item.findtext("name") or "").strip()
                out[LayoutSpec(name, vname)] = languages(item) or base
    return out


@lru_cache(maxsize=1)
def _system_languages() -> dict[LayoutSpec, frozenset[str]]:
    root = os.environ.get("XKB_CONFIG_ROOT")
    paths = (Path(root) / "rules/evdev.xml", *RULES_PATHS) if root else RULES_PATHS
    for path in paths:
        try:
            return parse_languages(path.read_text(encoding="utf-8"))
        except (OSError, ET.ParseError):
            continue
    return {}


def language_for_spec(spec: LayoutSpec) -> str | None:
    if spec.kind != "xkb":
        return None
    spec = spec_for_description(spec.layout) or spec
    languages = _system_languages()
    if languages:
        codes = languages.get(spec, frozenset())
        descriptions = [desc for desc, candidate in _system_rules().items() if candidate == spec]
        for code, language, name in (("rus", "ru", "Russian"), ("eng", "en", "English")):
            if codes == {code} or code in codes and any(d.startswith(name) for d in descriptions):
                return language
        return None
    # Чистые таблицы/неполная установка XKB: только известные ru/en, без догадок по алфавиту.
    return {"ru": "ru", "us": "en", "gb": "en"}.get(spec.layout)


def parse_layout_list(layouts: str, variants: str = "") -> list[LayoutSpec]:
    """«us,ru» + «,phonetic» → [us, ru(phonetic)] (формат конфигов композиторов)."""
    names = [p.strip() for p in layouts.split(",") if p.strip()]
    vars_ = [p.strip() for p in variants.split(",")] if variants else []
    vars_ += [""] * (len(names) - len(vars_))
    return [LayoutSpec(n, v) for n, v in zip(names, vars_, strict=False)]
