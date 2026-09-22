"""Статическая проверка: Variant/VariantType живут в GLib, а не в Gio.

`Gio.Variant` не существует в PyGObject (AttributeError), но выясняется это
только на живой системе — GI-код в юнит-тестах не выполняется. Поэтому
запрещаем такое обращение по тексту всех модулей пакета.
"""

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "wayswitch"
FORBIDDEN = re.compile(r"\bGio\.Variant")


def test_no_gio_variant_in_package():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN.search(line):
                offenders.append(f"{path.relative_to(PACKAGE.parent)}:{lineno}: {line.strip()}")
    assert not offenders, "Gio.Variant не существует — используйте GLib.Variant:\n" + \
        "\n".join(offenders)
