"""Подготовить пакет AUR к выпуску: версия, контрольная сумма, .SRCINFO.

    python tools/aur_update.py            — взять версию из pyproject.toml
    python tools/aur_update.py --check    — только сверить .SRCINFO с PKGBUILD

Делает то же, что `updpkgsums && makepkg --printsrcinfo > .SRCINFO` на Arch,
но работает где угодно: архив релиза скачивается с GitHub, .SRCINFO
собирается из простых присваиваний PKGBUILD (у нас других нет).
"""

from __future__ import annotations

import hashlib
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "packaging" / "arch"
PKGBUILD = ARCH / "PKGBUILD"
SRCINFO = ARCH / ".SRCINFO"

# Поля .SRCINFO в порядке, в котором их пишет makepkg --printsrcinfo.
PKGBASE_FIELDS = ("pkgdesc", "pkgver", "pkgrel", "url", "install", "arch", "license",
                  "depends", "optdepends", "source", "sha256sums")


def parse(text: str) -> dict[str, list[str]]:
    """Простые присваивания PKGBUILD: name=value и name=(...).

    Массивы разбираются shlex-ом до закрывающей скобки вне кавычек: в
    описаниях бывают свои скобки («звук (pw-play)»).
    """
    import shlex

    out: dict[str, list[str]] = {}
    for m in re.finditer(r"^(\w+)=", text, re.M):
        name, rest = m.group(1), text[m.end():]
        if rest.startswith("("):
            quote, depth, i = None, 0, 0
            for i, ch in enumerate(rest):  # noqa: B007 — i нужен после цикла
                if quote:
                    quote = None if ch == quote else quote
                elif ch in "'\"":
                    quote = ch
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
            out[name] = shlex.split(rest[1:i], comments=True)
        else:
            out[name] = [rest.splitlines()[0].strip().strip("'\"")]
    return out


def expand(value: str, fields: dict[str, list[str]]) -> str:
    def sub(m):
        key = m.group(1) or m.group(2)
        return fields.get(key, [""])[0]
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", sub, value)


def srcinfo(text: str) -> str:
    f = parse(text)
    name = f["pkgname"][0]
    lines = [f"pkgbase = {name}"]
    for key in PKGBASE_FIELDS:
        for value in f.get(key, []):
            lines.append(f"\t{key} = {expand(value, f)}")
    lines += ["", f"pkgname = {name}", ""]
    return "\n".join(lines)


def tarball_sha256(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 — https GitHub
        return hashlib.sha256(resp.read()).hexdigest()


def main(argv: list[str]) -> int:
    text = PKGBUILD.read_text(encoding="utf-8")
    if "--check" in argv:
        ok = SRCINFO.exists() and SRCINFO.read_text(encoding="utf-8") == srcinfo(text)
        print(".SRCINFO актуален" if ok else ".SRCINFO устарел: python tools/aur_update.py")
        return 0 if ok else 1
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml")
                        .read_text(encoding="utf-8"), re.M).group(1)
    text = re.sub(r"^pkgver=.*$", f"pkgver={version}", text, flags=re.M)
    url = expand(parse(text)["source"][0].split("::", 1)[-1], parse(text))
    digest = tarball_sha256(url)
    text = re.sub(r"^sha256sums=\(.*\)$", f"sha256sums=('{digest}')", text, flags=re.M)
    PKGBUILD.write_text(text, encoding="utf-8", newline="\n")
    SRCINFO.write_text(srcinfo(text), encoding="utf-8", newline="\n")
    print(f"PKGBUILD и .SRCINFO: {version}, sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
