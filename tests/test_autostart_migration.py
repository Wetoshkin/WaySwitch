"""Autostart migration identifies only bounded WaySwitch desktop entries."""

import pytest

from wayswitch.gui import autostart


def entry(exec_line="wayswitch-gui --tray", extra=""):
    return ("[Desktop Entry]\nType=Application\nName=WaySwitch\n"
            f"Exec={exec_line}\nIcon=unrelated.example.icon\n{extra}")


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "autostart"
    path.mkdir()
    return path


@pytest.mark.parametrize("command", ["wayswitch-gui", "wayswitch-gui --tray",
                                    "/usr/bin/wayswitch-gui --tray",
                                    '"/usr/local/bin/wayswitch-gui" --tray'])
def test_migrates_by_own_content_without_old_domain(folder, command):
    old = folder / "org.example.WaySwitch.Settings.desktop"
    old.write_text(entry(command), encoding="utf-8")
    assert autostart.is_tray_enabled()
    autostart.set_tray_enabled(True)
    assert not old.exists()
    current = folder / autostart.DESKTOP_NAME
    assert current.read_text(encoding="utf-8") == autostart.tray_desktop_text()
    assert "unrelated.example.icon" not in current.read_text(encoding="utf-8")
    autostart.set_tray_enabled(True)
    assert list(folder.iterdir()) == [current]


@pytest.mark.parametrize("extra", ["Hidden=true\n", "X-GNOME-Autostart-enabled=false\n"])
def test_disabled_old_entry_does_not_enable_tray(folder, extra):
    old = folder / "org.example.WaySwitch.desktop"
    old.write_text(entry(extra=extra), encoding="utf-8")
    assert not autostart.is_tray_enabled()
    autostart.set_tray_enabled(False)
    assert not old.exists()
    assert not autostart.is_tray_enabled()
    assert not (folder / autostart.DESKTOP_NAME).exists()


def test_current_disabled_choice_wins_over_old_duplicate(folder):
    (folder / autostart.DESKTOP_NAME).write_text(entry(extra="Hidden=true\n"), encoding="utf-8")
    (folder / "org.example.WaySwitch.desktop").write_text(entry(), encoding="utf-8")
    assert not autostart.is_tray_enabled()
    autostart.set_tray_enabled(False)
    assert list(folder.iterdir()) == []


@pytest.mark.parametrize("contents", [
    "x", entry().replace("Type=Application", "Type=Link"),
    entry().replace("Name=WaySwitch", "Name=Other"),
    entry("other-program --tray"), entry("wayswitch-gui --unrecognized"),
    entry("/tmp/wayswitch-gui --tray"), entry("wayswitch-gui --tray ; other"),
    entry(extra="Hidden=maybe\n"), entry(extra="Exec=other\n"),
    entry() + "#" * 17000,
])
def test_invalid_and_unrelated_entries_are_untouched(folder, contents):
    path = folder / "org.example.WaySwitch.Settings.desktop"
    path.write_text(contents, encoding="utf-8")
    assert not autostart.is_tray_enabled()
    autostart.set_tray_enabled(False)
    assert path.read_text(encoding="utf-8") == contents


def test_file_without_product_name_is_not_ours(folder):
    path = folder / "other.desktop"
    path.write_text(entry(), encoding="utf-8")
    assert not autostart.is_tray_enabled()
    autostart.set_tray_enabled(False)
    assert path.exists()


def test_symlink_is_not_followed_or_removed(folder, tmp_path):
    target = tmp_path / "target.desktop"
    target.write_text(entry(), encoding="utf-8")
    link = folder / "org.example.WaySwitch.desktop"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    assert not autostart.is_tray_enabled()
    autostart.set_tray_enabled(False)
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == entry()


def test_unrecognized_current_path_is_not_overwritten(folder):
    current = folder / autostart.DESKTOP_NAME
    current.write_text("unrelated data", encoding="utf-8")
    old = folder / "org.example.WaySwitch.desktop"
    old.write_text(entry(), encoding="utf-8")
    with pytest.raises(FileExistsError):
        autostart.set_tray_enabled(True)
    assert current.read_text(encoding="utf-8") == "unrelated data"
    assert old.exists()


def test_failed_replace_keeps_current_choice_and_old_entry(folder, monkeypatch):
    current = folder / autostart.DESKTOP_NAME
    previous = entry(extra="Hidden=true\n")
    current.write_text(previous, encoding="utf-8")
    old = folder / "org.example.WaySwitch.desktop"
    old.write_text(entry(), encoding="utf-8")

    def fail_replace(*_args):
        raise OSError("synthetic disk error")

    monkeypatch.setattr(autostart.os, "replace", fail_replace)
    with pytest.raises(OSError):
        autostart.set_tray_enabled(True)
    assert current.read_text(encoding="utf-8") == previous
    assert old.read_text(encoding="utf-8") == entry()
    assert sorted(p.name for p in folder.iterdir()) == sorted([current.name, old.name])


def test_directory_entry_is_untouched(folder):
    directory = folder / "org.example.WaySwitch.desktop"
    directory.mkdir()
    assert not autostart.is_tray_enabled()
    autostart.set_tray_enabled(False)
    assert directory.is_dir()


@pytest.mark.parametrize("disabled", [False, True])
def test_startup_migration_preserves_old_preference(folder, disabled):
    old = folder / "org.example.WaySwitch.desktop"
    old.write_text(entry(extra="Hidden=true\n" if disabled else ""), encoding="utf-8")
    assert autostart.migrate_tray()
    assert not old.exists()
    assert autostart.is_tray_enabled() is not disabled
    assert not autostart.migrate_tray()


def test_startup_migration_preserves_canonical_disabled_preference(folder):
    current = folder / autostart.DESKTOP_NAME
    current.write_text(entry(extra="X-GNOME-Autostart-enabled=false\n"), encoding="utf-8")
    old = folder / "org.example.WaySwitch.desktop"
    old.write_text(entry(), encoding="utf-8")
    assert autostart.migrate_tray()
    assert not autostart.is_tray_enabled()
    assert not old.exists()


def test_startup_migration_noop_without_old_entry(folder):
    assert not autostart.migrate_tray()
    assert not list(folder.iterdir())


def test_startup_migration_collision_is_nonfatal_and_private(folder, caplog):
    current = folder / autostart.DESKTOP_NAME
    current.write_text("unrelated data", encoding="utf-8")
    old = folder / "org.example.WaySwitch.desktop"
    old.write_text(entry(), encoding="utf-8")
    assert not autostart.migrate_tray()
    assert current.read_text(encoding="utf-8") == "unrelated data"
    assert old.read_text(encoding="utf-8") == entry()
    assert str(folder) not in caplog.text


@pytest.mark.parametrize("command,extra", [
    ("wayswitch-gui", ""),
    ("/usr/local/bin/wayswitch-gui --tray", ""),
    ("wayswitch-gui --tray", "OnlyShowIn=KDE;\n"),
    ("wayswitch-gui --tray", "NotShowIn=GNOME;\n"),
    ("wayswitch-gui --tray", "TryExec=/opt/custom/wayswitch-gui\n"),
    ("wayswitch-gui --tray", "AutostartCondition=GSettings example enabled\n"),
    ("wayswitch-gui --tray", "Terminal=true\n"),
    ("wayswitch-gui --tray", "X-GNOME-Autostart-Delay=30\n"),
    ("wayswitch-gui --tray", "X-Custom-Startup=keep\n"),
])
def test_automatic_migration_preserves_custom_startup_behavior(folder, command, extra):
    custom = folder / "org.example.WaySwitch.desktop"
    original = entry(command, extra)
    custom.write_text(original, encoding="utf-8")
    assert not autostart.migrate_tray()
    assert custom.read_text(encoding="utf-8") == original
    assert list(folder.iterdir()) == [custom]


def test_custom_canonical_is_preserved_with_standard_legacy_duplicate(folder):
    current = folder / autostart.DESKTOP_NAME
    original = entry(extra="OnlyShowIn=KDE;\n")
    current.write_text(original, encoding="utf-8")
    old = folder / "org.example.WaySwitch.desktop"
    old.write_text(entry(), encoding="utf-8")
    assert not autostart.migrate_tray()
    assert current.read_text(encoding="utf-8") == original
    assert old.read_text(encoding="utf-8") == entry()
