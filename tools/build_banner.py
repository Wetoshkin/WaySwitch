# ruff: noqa: E501 — строки CSS-анимации и SVG-разметки короче не сделать
"""Анимированные баннеры README: .github/assets/banner-{ru,en}.svg.

Шапка README — SVG, а не PNG, чтобы круги на фоне плавали, как в лава-лампе
(GitHub показывает CSS-анимацию в SVG-картинках). Текст переведён в контуры:
внешние шрифты в такой картинке не грузятся. Шрифты (OFL) скачиваются
отдельно и в репозиторий не кладутся:

    python tools/build_banner.py ПАПКА_СО_ШРИФТАМИ

В папке: Caprasimo.ttf, Figtree.ttf, Nunito.ttf, JBMono.ttf — файлы из
github.com/google/fonts (ofl/caprasimo/Caprasimo-Regular.ttf,
ofl/figtree/Figtree[wght].ttf, ofl/nunito/Nunito[wght].ttf,
ofl/jetbrainsmono/JetBrainsMono[wght].ttf). Макет — «GitHub Page» в дизайн-проекте.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parents[1]
W, H = 1280, 400
BG, CREAM, ACCENT, SAGE = "#201e1d", "#f5ead8", "#c67139", "#7a8a5e"
N300, N400, N500 = "#dcd3c4", "#c0b6a5", "#a19786"

TEXTS = {
    "ru": ("Современная замена Punto Switcher и Caramba Switcher для Linux на Wayland — "
           "во всех графических окружениях.", "ghbdtn", "привет"),
    "en": ("A modern Punto Switcher and Caramba Switcher alternative for Linux on Wayland — "
           "in every desktop environment.", "руддщ", "hello"),
}


class Face:
    def __init__(self, path: Path, weight: int | None = None):
        font = TTFont(path)
        if weight is not None and "fvar" in font:
            font = instantiateVariableFont(font, {"wght": weight})
        self.font = font
        self.upm = font["head"].unitsPerEm
        self.cmap = font.getBestCmap()
        self.glyphs = font.getGlyphSet()
        self.hmtx = font["hmtx"]
        hhea = font["hhea"]
        self.asc, self.desc = hhea.ascent, -hhea.descent

    def width(self, text: str, size: float) -> float:
        return sum(self.hmtx[self.cmap.get(ord(c), ".notdef")][0] for c in text) * size / self.upm

    def path(self, text: str, x: float, baseline: float, size: float) -> str:
        """Контур строки: одна d-строка для <path>."""
        scale = size / self.upm
        pen = SVGPathPen(self.glyphs)
        cursor = 0.0
        for ch in text:
            name = self.cmap.get(ord(ch))
            if name is None:
                # Вместо буквы был бы пустой квадрат — лучше упасть при сборке.
                raise SystemExit(f"в шрифте нет символа {ch!r}")
            tpen = TransformPen(pen, (scale, 0, 0, -scale, x + cursor * scale, baseline))
            self.glyphs[name].draw(tpen)
            cursor += self.hmtx[name][0]
        d = pen.getCommands()
        # Округление координат: файл в разы меньше, на вид не отличить.
        return re.sub(r"-?\d+\.\d+", lambda m: f"{float(m.group()):.1f}".rstrip("0").rstrip("."), d)

    def baseline(self, top: float, size: float, line_height: float) -> float:
        """Базовая линия в строке CSS с заданным line-height."""
        content = (self.asc + self.desc) * size / self.upm
        return top + (line_height - content) / 2 + self.asc * size / self.upm


def wrap(face: Face, text: str, size: float, max_width: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split(" "):
        probe = f"{cur} {word}".strip()
        if cur and face.width(probe, size) > max_width:
            lines.append(cur)
            cur = word
        else:
            cur = probe
    return lines + [cur]


def icon() -> str:
    """Значок приложения без XML-шапки — внутрь баннера."""
    svg = (ROOT / "packaging" / "icons" / "ru.siberia.WaySwitch.svg").read_text(encoding="utf-8")
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
    inner = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)
    s = 184 / 128
    return f'<g transform="translate(96 108) scale({s:.4f})">{inner.strip()}</g>'


STYLE = """
.b{animation:drift-a 26s ease-in-out infinite alternate;transform-box:fill-box;transform-origin:center}
.b2{animation-name:drift-b;animation-duration:33s}
.b3{animation-name:drift-c;animation-duration:21s}
@keyframes drift-a{0%{transform:translate(0,0) scale(1,1)}30%{transform:translate(-90px,60px) scale(1.08,.94)}60%{transform:translate(-40px,120px) scale(.94,1.06)}100%{transform:translate(-150px,30px) scale(1.04,1)}}
@keyframes drift-b{0%{transform:translate(0,0) scale(1,1)}35%{transform:translate(120px,-70px) scale(.92,1.08)}70%{transform:translate(220px,-20px) scale(1.1,.95)}100%{transform:translate(60px,-110px) scale(1,1.04)}}
@keyframes drift-c{0%{transform:translate(0,0) scale(1)}50%{transform:translate(-160px,-40px) scale(1.2)}100%{transform:translate(-60px,50px) scale(.9)}}
@media (prefers-reduced-motion:reduce){.b{animation:none}}
"""


def banner(lang: str, fonts: Path) -> str:
    title = Face(fonts / "Caprasimo.ttf")
    # В Figtree (шрифт макета) нет кириллицы — для русского баннера Nunito.
    family = "Nunito.ttf" if lang == "ru" else "Figtree.ttf"
    body = Face(fonts / family, 400)
    bold = Face(fonts / family, 700)
    mono = Face(fonts / "JBMono.ttf", 500)
    subtitle, wrong, right = TEXTS[lang]

    lines = wrap(body, subtitle, 28, 760)
    line_h = 28 * 1.35
    column = 104 + 18 + line_h * len(lines) + 18 + 6 + 48
    top = (H - column) / 2
    x = 336
    parts = [f'<path fill="{CREAM}" d="{title.path("WaySwitch", x, title.baseline(top, 104, 104), 104)}"/>']
    y = top + 104 + 18
    for line in lines:
        parts.append(f'<path fill="{N300}" d="{body.path(line, x, body.baseline(y, 28, line_h), 28)}"/>')
        y += line_h
    y += 18 + 6
    # «было» — серая плашка, зачёркнуто
    w1 = mono.width(wrong, 22) + 44
    parts.append(f'<rect x="{x}" y="{y:.1f}" width="{w1:.1f}" height="48" rx="24" '
                 f'fill="#ffffff" fill-opacity=".07"/>')
    base = mono.baseline(y, 22, 48)
    parts.append(f'<path fill="{N400}" d="{mono.path(wrong, x + 22, base, 22)}"/>')
    parts.append(f'<rect x="{x + 22}" y="{y + 24 - 1:.1f}" width="{w1 - 44:.1f}" height="2" '
                 f'fill="{N400}"/>')
    # стрелка
    ax = x + w1 + 14
    parts.append(f'<path d="M{ax + 3} {y + 24:.1f}h18m-7-7 7 7-7 7" fill="none" stroke="{N500}" '
                 f'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>')
    # «стало» — терракотовая плашка
    px = ax + 26 + 14
    w2 = bold.width(right, 24) + 48
    parts.append(f'<rect x="{px:.1f}" y="{y:.1f}" width="{w2:.1f}" height="48" rx="24" '
                 f'fill="{ACCENT}"/>')
    parts.append(f'<path fill="#fff" d="{bold.path(right, px + 24, bold.baseline(y, 24, 48), 24)}"/>')

    alt = ("WaySwitch — исправляет слова, набранные не в той раскладке" if lang == "ru"
           else "WaySwitch — fixes words typed in the wrong keyboard layout")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="{alt}">
<title>{alt}</title>
<style>{STYLE}</style>
<rect width="{W}" height="{H}" fill="{BG}"/>
<circle class="b" cx="{W + 140 - 300}" cy="{-200 + 300}" r="300" fill="{ACCENT}" fill-opacity=".16"/>
<circle class="b b2" cx="{W - 220 - 220}" cy="{H + 260 - 220}" r="220" fill="{SAGE}" fill-opacity=".2"/>
<circle class="b b3" cx="760" cy="40" r="90" fill="{ACCENT}" fill-opacity=".08"/>
{icon()}
{chr(10).join(parts)}
</svg>
"""


def main(argv: list[str]) -> int:
    fonts = Path(argv[1]) if len(argv) > 1 else Path("fonts")
    out = ROOT / ".github" / "assets"
    for lang in TEXTS:
        path = out / f"banner-{lang}.svg"
        path.write_text(banner(lang, fonts), encoding="utf-8", newline="\n")
        print(path, path.stat().st_size // 1024, "КБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
