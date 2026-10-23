"""Оформление окна: акцент «терракота», «шалфей» для успешных состояний и
несколько своих классов (карточка состояния, клавиша, зачёркнутое слово).

AdwStyleManager не умеет задать приложению свой акцент, поэтому цвет
задаётся стилями. В libadwaita 1.6+ виджеты берут цвета из CSS-переменных
(--accent-bg-color), в 1.5 — из именованных цветов (@accent_bg_color);
пишем оба варианта. Светлая и тёмная темы различаются оттенками, поэтому
стиль пересобирается по сигналу notify::dark.
"""

from __future__ import annotations

PALETTE = {
    False: {  # светлая тема
        "accent_bg": "#b8622c", "accent": "#9a4f1f", "accent_tint": "rgba(198,113,57,.16)",
        "success": "#56633f", "success_tint": "rgba(122,138,94,.2)",
    },
    True: {   # тёмная тема
        "accent_bg": "#c46a32", "accent": "#f3a46f", "accent_tint": "rgba(214,127,72,.22)",
        "success": "#b3c795", "success_tint": "rgba(143,160,115,.26)",
    },
}

_COMMON = """
.ws-status-icon { min-width: 44px; min-height: 44px; border-radius: 999px; padding: 0; }
.ws-status-icon.on { background: @ws_success_tint; color: @ws_success; }
.ws-status-icon.paused, .ws-status-icon.manual, .ws-status-icon.locked {
  background: alpha(currentColor, .1); }
.ws-status-title { font-size: 17px; font-weight: 700; }
.ws-stat { padding: 12px 16px; }
.ws-stat-number { font-size: 20px; font-weight: 700; font-feature-settings: "tnum"; }
.ws-stats separator { background: alpha(currentColor, .08); }
.ws-keycap { padding: 2px 9px; border-radius: 6px; background: alpha(currentColor, .1);
  box-shadow: inset 0 -2px alpha(black, .2); font-size: 13px; font-weight: 600; }
.ws-keycap.large { padding: 8px 18px; border-radius: 10px; font-size: 18px; font-weight: 700; }
.ws-strike { text-decoration-line: line-through; font-family: monospace; opacity: .6; }
.ws-mono { font-family: monospace; }
.ws-success { color: @ws_success; }
.ws-accent-text { color: @ws_accent; font-weight: 600; }
.ws-highlight { background: @ws_accent_tint; color: @ws_accent; border-radius: 4px; }
.ws-done-icon { min-width: 72px; min-height: 72px; border-radius: 999px;
  background: @ws_success_tint; color: @ws_success; }
.ws-fail-row { background: alpha(@error_color, .06); }
.ws-test-entry.fixed { box-shadow: inset 0 0 0 2px @ws_accent_bg; }
"""


def css_for(dark: bool, modern: bool) -> str:
    """Таблица стилей для темы. modern — libadwaita 1.6+ (CSS-переменные)."""
    p = PALETTE[bool(dark)]
    defs = [f"@define-color ws_{name} {value};" for name, value in p.items()]
    if modern:
        accent = (f":root {{ --accent-bg-color: {p['accent_bg']}; "
                  f"--accent-color: {p['accent']}; --accent-fg-color: white; }}")
    else:
        accent = (f"@define-color accent_bg_color {p['accent_bg']};\n"
                  f"@define-color accent_color {p['accent']};\n"
                  "@define-color accent_fg_color white;")
    return "\n".join(defs) + "\n" + accent + "\n" + _COMMON


def install() -> None:
    """Подключить стиль к дисплею и следить за сменой темы."""
    from gi.repository import Adw, Gdk, Gtk

    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    manager = Adw.StyleManager.get_default()
    modern = (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 6)

    def apply(*_):
        text = css_for(manager.get_dark(), modern)
        # load_from_string появился в GTK 4.12; на старом — load_from_data.
        if hasattr(provider, "load_from_string"):
            provider.load_from_string(text)
        else:
            provider.load_from_data(text, -1)

    apply()
    manager.connect("notify::dark", apply)
    # Запуск из исходников: значки ещё не установлены в hicolor — берём их
    # прямо из packaging/icons.
    from pathlib import Path

    icons = Path(__file__).resolve().parents[2] / "packaging" / "icons"
    if icons.is_dir():
        Gtk.IconTheme.get_for_display(display).add_search_path(str(icons))
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
