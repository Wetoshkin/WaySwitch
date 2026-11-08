import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def migration():
    path = Path(__file__).resolve().parents[1] / "packaging" / "migrate.py"
    spec = importlib.util.spec_from_file_location("identifier_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extension(root, ident, *, url="https://github.com/Wetoshkin/WaySwitch", name="WaySwitch"):
    folder = root / ident
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps(
        {"uuid": ident, "name": name, "url": url}), encoding="utf-8")
    return folder


def test_extension_cleanup_uses_project_metadata_not_a_domain(migration, tmp_path, monkeypatch):
    old = extension(tmp_path, "wayswitch@example.invalid")
    current = extension(tmp_path, migration.EXTENSION_UUID)
    foreign = extension(tmp_path, "other@example.invalid", url="https://example.invalid/other")
    calls = []
    monkeypatch.setattr(migration, "disable_extension", calls.append)
    migration.clean_extensions(tmp_path)
    assert not old.exists() and current.exists() and foreign.exists()
    assert calls == ["wayswitch@example.invalid"]


def test_mismatched_extension_uuid_and_oversized_metadata_are_not_removed(migration, tmp_path):
    folder = extension(tmp_path, "wayswitch@example.invalid")
    (folder / "metadata.json").write_text(json.dumps({
        "uuid": "different@example.invalid", "name": "WaySwitch", "url": migration.REPO_URL}),
        encoding="utf-8")
    huge = extension(tmp_path, "large@example.invalid")
    (huge / "metadata.json").write_text(" " * 65537, encoding="utf-8")
    migration.clean_extensions(tmp_path)
    assert folder.exists() and huge.exists()


def test_prefix_cleanup_preserves_new_assets_and_foreign_desktops(migration, tmp_path):
    apps = tmp_path / "share/applications"
    apps.mkdir(parents=True)
    icons = tmp_path / "share/icons/hicolor/scalable/apps"
    icons.mkdir(parents=True)
    old = apps / "org.example.WaySwitch.Settings.desktop"
    old.write_text("[Desktop Entry]\nType=Application\nName=WaySwitch\n"
                   "Exec=wayswitch-gui\nIcon=org.example.WaySwitch\n", encoding="utf-8")
    icon = icons / "org.example.WaySwitch.svg"
    icon.write_text("test", encoding="utf-8")
    current = apps / migration.DESKTOP_NAME
    current.write_text(old.read_text(), encoding="utf-8")
    foreign = apps / "org.example.Other.desktop"
    foreign.write_text("[Desktop Entry]\nName=WaySwitch\nExec=other-app\n", encoding="utf-8")
    migration.clean_prefix(tmp_path)
    assert not old.exists() and not icon.exists()
    assert current.exists() and foreign.exists()


def test_icon_path_cannot_escape_prefix(migration, tmp_path):
    apps = tmp_path / "share/applications"
    apps.mkdir(parents=True)
    outside = tmp_path / "keep.svg"
    outside.write_text("keep", encoding="utf-8")
    (apps / "org.example.WaySwitch.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=WaySwitch\nExec=wayswitch-gui\n"
        "Icon=../../../../keep\n", encoding="utf-8")
    migration.clean_prefix(tmp_path)
    assert outside.read_text() == "keep"


def test_generic_icon_referenced_by_way_switch_is_not_owned(migration, tmp_path):
    apps = tmp_path / "share/applications"
    apps.mkdir(parents=True)
    icons = tmp_path / "share/icons/hicolor/scalable/apps"
    icons.mkdir(parents=True)
    icon = icons / "utilities-terminal.svg"
    icon.write_text("unrelated", encoding="utf-8")
    (apps / "org.example.WaySwitch.Settings.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=WaySwitch\nExec=wayswitch-gui\n"
        "Icon=utilities-terminal\n", encoding="utf-8")
    migration.clean_prefix(tmp_path)
    assert icon.read_text() == "unrelated"


def test_kde_cleanup_requires_exact_project_marker_and_matching_id(migration, tmp_path):
    root = tmp_path / "share/plasma/plasmoids"
    own = root / "org.example.wayswitch"
    own.mkdir(parents=True)
    metadata = {"KPlugin": {"Name": "WaySwitch", "Website": migration.REPO_URL,
                            "Id": own.name}}
    (own / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    foreign = root / "org.example.other"
    foreign.mkdir()
    (foreign / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    migration.clean_prefix(tmp_path)
    assert not own.exists() and foreign.exists()


def test_uninstall_only_removes_owned_autostart(migration, tmp_path):
    own = tmp_path / "org.example.WaySwitch.desktop"
    own.write_text("[Desktop Entry]\nType=Application\nName=WaySwitch\n"
                   "Exec=wayswitch-gui --tray\n", encoding="utf-8")
    foreign = tmp_path / "other.desktop"
    foreign.write_text("[Desktop Entry]\nName=Other\nExec=wayswitch-gui\n", encoding="utf-8")
    migration.clean_autostart(tmp_path)
    assert not own.exists() and foreign.exists()


def test_prefix_cleanup_does_not_follow_directory_links(migration, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    try:
        (prefix / "share").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    apps = outside / "applications"
    apps.mkdir()
    keep = apps / "org.example.WaySwitch.desktop"
    keep.write_text("[Desktop Entry]\nType=Application\nName=WaySwitch\n"
                    "Exec=wayswitch-gui\n", encoding="utf-8")
    migration.clean_prefix(prefix)
    assert keep.exists()
