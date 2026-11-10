"""Заметки релиза из CHANGELOG и лицензионные метаданные пакета."""

from __future__ import annotations

import tomllib

import pytest

from tools import release_notes

ROOT = release_notes.ROOT
PROGRESS = "https://github.com/Wetoshkin/WaySwitch/blob/{}/docs/PROGRESS.md"

SYNTHETIC = """\
# История изменений

## [Не опубликовано]
- Это изменение ещё не входит ни в один выпуск.

## [9.9.1] - 2026-01-01
- Изменение только синтетического тега 9.9.1.

## [9.9.2] - 2026-01-02
- Изменение только синтетического тега 9.9.2.
"""


def test_two_synthetic_tags_use_their_own_notes_and_links():
    first = release_notes.notes_for(SYNTHETIC, "v9.9.1")
    second = release_notes.notes_for(SYNTHETIC, "v9.9.2")

    assert "## Изменения в 9.9.1" in first
    assert "Изменение только синтетического тега 9.9.1." in first
    assert "Изменение только синтетического тега 9.9.2." not in first
    assert "## Изменения в 9.9.2" in second
    assert "Изменение только синтетического тега 9.9.2." in second
    assert "Изменение только синтетического тега 9.9.1." not in second
    assert "Это изменение ещё не входит" not in first + second
    assert PROGRESS.format("v9.9.1") in first
    assert PROGRESS.format("v9.9.2") in second
    assert first != second


def test_missing_and_duplicate_sections_are_rejected():
    with pytest.raises(release_notes.ReleaseNotesError, match="9.9.3"):
        release_notes.notes_for(SYNTHETIC, "v9.9.3")
    duplicate = "## [9.9.1]\n- one\n## [9.9.1]\n- two\n"
    with pytest.raises(release_notes.ReleaseNotesError, match="повторяется"):
        release_notes.notes_for(duplicate, "v9.9.1")


def test_committed_changelog_has_published_1_1_3_and_unreleased():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Не опубликовано]" in text
    assert "## [1.1.3] - 2026-11-09" in text
    notes = release_notes.notes_for(text, "v1.1.3")
    assert "## Изменения в 1.1.3" in notes
    assert "AppImage" in notes
    assert PROGRESS.format("v1.1.3") in notes


def test_committed_changelog_has_prepared_1_1_4_notes():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    notes = release_notes.notes_for(text, "v1.1.4")
    assert "## Изменения в 1.1.4" in notes
    assert notes.strip()
    assert "Новых изменений после 1.1.4" not in notes
    assert "AppImage добавлены PNG" not in notes
    assert PROGRESS.format("v1.1.4") in notes


def test_release_workflow_uses_the_tag_notes_and_keeps_package_checks():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'python3 tools/release_notes.py "$GITHUB_REF_NAME" > notes.md' in workflow
    assert "python3 tools/release_notes.py --self-test" in workflow
    assert "Изменения в 1.1.3" not in workflow
    assert ('case "$GITHUB_REF_NAME" in *a[0-9]*|*b[0-9]*|*rc[0-9]*) '
            'pre="--prerelease" ;; esac') in workflow
    for command in ("bash tools/build_deb.sh", "bash tools/build_rpm.sh",
                    "bash tools/build_flatpak.sh build/flatpak",
                    "python3 tools/build_appimage.py build/appimage"):
        assert command in workflow, command
    assert "sha256sum *.deb *.rpm" in workflow
    assert "sha256sum --check SHA256SUMS" in workflow


def test_license_metadata_covers_code_and_packaged_dictionaries():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["build-system"]["requires"] == ["setuptools>=68"]
    assert project["project"]["license"] == {"text": "MIT AND CC-BY-SA-4.0"}
    package_data = project["tool"]["setuptools"]["package-data"]["wayswitch"]
    assert "data/LICENSE" in package_data

    data_license = (ROOT / "wayswitch" / "data" / "LICENSE").read_text(encoding="utf-8")
    assert "MIT" in data_license and "CC-BY-SA 4.0" in data_license

    rpm = (ROOT / "packaging" / "rpm" / "wayswitch.spec").read_text(encoding="utf-8")
    assert "License:        MIT AND CC-BY-SA-4.0" in rpm
    arch = (ROOT / "packaging" / "arch" / "PKGBUILD").read_text(encoding="utf-8")
    assert "license=('MIT' 'CC-BY-SA-4.0')" in arch
    srcinfo = (ROOT / "packaging" / "arch" / ".SRCINFO").read_text(encoding="utf-8")
    assert "\tlicense = MIT\n" in srcinfo
    assert "\tlicense = CC-BY-SA-4.0\n" in srcinfo
    flatpak = (ROOT / "packaging" / "flatpak" /
               "io.github.Wetoshkin.WaySwitch.Gui.metainfo.xml").read_text(encoding="utf-8")
    assert "<project_license>MIT AND CC-BY-SA-4.0</project_license>" in flatpak
