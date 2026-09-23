"""Перевод версии Python (PEP 440) в версию Debian.

Python-предрелизы (`a`, `b`, `rc`) не сортируются в Debian корректно без
разделителя `~` — без него, например, `2.0.0rc1` оказался бы «больше»
`2.0.0`. Модуль умеет только тот частный случай, который встречается в
`pyproject.toml` этого проекта: `<release>[a|b|rc]<N>`.
"""

from __future__ import annotations

import re
import sys

_PRERELEASE_RE = re.compile(r"^(?P<release>\d+(?:\.\d+)*)(?P<kind>a|b|rc)(?P<num>\d+)$")


def to_debian(version: str) -> str:
    """Преобразовать версию Python в версию Debian.

    `2.0.0a1` -> `2.0.0~a1`, `2.0.0b2` -> `2.0.0~b2`, `2.0.0rc1` -> `2.0.0~rc1`,
    обычная версия без суффикса возвращается без изменений.
    """
    match = _PRERELEASE_RE.match(version)
    if match is None:
        return version
    return f"{match.group('release')}~{match.group('kind')}{match.group('num')}"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: debversion.py <python-version>", file=sys.stderr)
        return 2
    print(to_debian(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
