"""Собрать заметки GitHub-релиза из CHANGELOG.md для точного тега.

    python tools/release_notes.py v1.1.3 > notes.md
    python tools/release_notes.py --self-test

Раздел ищется по точной версии тега (префикс ``v`` снимается). Если раздела
нет или он пуст, команда завершается ошибкой: релиз не должен публиковать
заметки другой версии. Ссылка на docs/PROGRESS.md собирается из того же тега.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
PROGRESS_URL = "https://github.com/Wetoshkin/WaySwitch/blob/{tag}/docs/PROGRESS.md"

_HEADING = re.compile(r"^##[ \t]+\[([^\]\n]+)\](?:[ \t]+.*)?$", re.MULTILINE)

_TEMPLATE = """\
## Установка и обновление

Для новой нативной установки из GitHub откройте терминал, вставьте команду
сочетанием Ctrl+Shift+V и нажмите Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/Wetoshkin/WaySwitch/main/packaging/get.sh | sh
```

Скрипт сам определит дистрибутив, покажет план и спросит подтверждение.
Для GitHub-установки той же командой можно обновить старую версию; настройки
сохраняются. Если пакет установлен из AUR или стороннего репозитория,
обновляйте через его менеджер и сопровождающего: источник не меняется.

После обновления расширения GNOME может потребоваться выйти из сеанса и
войти снова; atomic-системам может требоваться перезагрузка.

Вручную: скачайте файл ниже — `.deb` для Ubuntu 24.04+ и Debian 13+,
`.rpm` для Fedora и openSUSE — и выполните в терминале:
`sudo apt install "$(xdg-user-dir DOWNLOAD)"/wayswitch_*.deb` (Ubuntu, Debian),
`sudo dnf install "$(xdg-user-dir DOWNLOAD)"/wayswitch-*.rpm` (Fedora) или
`sudo zypper install --allow-unsigned-rpm "$(xdg-user-dir DOWNLOAD)"/wayswitch-*.rpm`
(openSUSE).

`.flatpak` и `.AppImage` содержат только окно настроек и трей.
Для исправления раскладки нужна также нативная служба WaySwitch,
установленная через `.deb`, `.rpm` или пакет дистрибутива.
AppImage предназначен для x86_64 с glibc 2.39+ (Ubuntu 24.04+).
Файл `SHA256SUMS` содержит контрольные суммы всех четырёх форматов.

В COSMIC после запуска службы или блокировки разблокируйте экран
и явно включите исправление в окне/трее либо командой `wayswitch resume`.
Автоматического возобновления нет.

## Возможности

- Автоматическое исправление русско-английской раскладки.
- Ручные жесты, отмена исправления и обучение на исключениях.
- Окно настроек, значок панели и интеграции с рабочими столами Wayland.

Поддержка окружений и ограничения описаны в README. Актуальные проверки
и ограничения этого тега — в [состоянии проекта]({progress_url}).
"""

_SELF_TEST_CHANGELOG = """\
# История изменений

## [Не опубликовано]
- Это изменение ещё не входит ни в один выпуск.

## [9.9.1] - 2026-01-01
- Изменение только синтетического тега 9.9.1.

## [9.9.2] - 2026-01-02
- Изменение только синтетического тега 9.9.2.
"""


class ReleaseNotesError(ValueError):
    """Заметки для запрошенного тега нельзя собрать."""


def tag_version(tag: str) -> tuple[str, str]:
    """Вернуть исходный тег и версию без префикса ``v``."""
    actual = tag.strip()
    version = actual[1:] if actual.startswith("v") else actual
    if not version or any(char.isspace() for char in actual):
        raise ReleaseNotesError("тег пуст или содержит пробелы")
    if version.casefold() == "unreleased":
        raise ReleaseNotesError("раздел [Не опубликовано] нельзя выпускать как тег")
    return actual, version


def sections(text: str) -> dict[str, str]:
    """Разобрать разделы второго уровня вида ``## [версия] - дата``."""
    headings = list(_HEADING.finditer(text))
    result: dict[str, str] = {}
    for index, heading in enumerate(headings):
        name = heading.group(1).strip()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        if name in result:
            raise ReleaseNotesError(f"раздел CHANGELOG повторяется: {name}")
        result[name] = text[heading.end():end].strip()
    return result


def notes_for(text: str, tag: str) -> str:
    """Собрать заметки для тега из текста CHANGELOG."""
    actual_tag, version = tag_version(tag)
    section = sections(text).get(version)
    if section is None:
        raise ReleaseNotesError(f"в CHANGELOG.md нет раздела для тега {actual_tag}")
    if not section:
        raise ReleaseNotesError(f"раздел CHANGELOG для тега {actual_tag} пуст")
    progress = PROGRESS_URL.format(tag=actual_tag)
    return (f"## Изменения в {version}\n\n{section}\n\n"
            f"{_TEMPLATE.format(progress_url=progress)}").rstrip() + "\n"


def render(tag: str, changelog: Path = CHANGELOG) -> str:
    """Прочитать CHANGELOG и собрать заметки для тега."""
    path = Path(changelog)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseNotesError(f"не удалось прочитать {path}: {exc}") from exc
    return notes_for(text, tag)


def self_test() -> None:
    """Проверить два синтетических тега с разными заметками и ссылками."""
    first = notes_for(_SELF_TEST_CHANGELOG, "v9.9.1")
    second = notes_for(_SELF_TEST_CHANGELOG, "v9.9.2")
    assert "Изменение только синтетического тега 9.9.1." in first
    assert "Изменение только синтетического тега 9.9.2." not in first
    assert "Изменение только синтетического тега 9.9.2." in second
    assert "Изменение только синтетического тега 9.9.1." not in second
    assert "Это изменение ещё не входит" not in first
    assert PROGRESS_URL.format(tag="v9.9.1") in first
    assert PROGRESS_URL.format(tag="v9.9.2") in second
    assert "## Изменения в 9.9.1" in first
    assert "## Изменения в 9.9.2" in second
    assert first != second
    try:
        notes_for(_SELF_TEST_CHANGELOG, "v9.9.3")
    except ReleaseNotesError:
        pass
    else:
        raise AssertionError("отсутствующий тег должен завершаться ошибкой")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?", help="тег релиза, например v1.1.3")
    parser.add_argument("--changelog", type=Path, default=CHANGELOG)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        print("release_notes: self-test passed")
        return 0
    if args.tag is None:
        parser.error("укажите тег (например, v1.1.3) или --self-test")
    try:
        sys.stdout.write(render(args.tag, args.changelog))
    except ReleaseNotesError as exc:
        print(f"release_notes: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
