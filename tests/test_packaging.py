from pathlib import Path

import pytest

from tests.test_get_sh import shell as shell

ROOT = Path(__file__).resolve().parents[1] / "packaging"


def test_udev_rules_use_uaccess():
    text = (ROOT / "60-wayswitch.rules").read_text(encoding="utf-8")
    assert 'KERNEL=="uinput"' in text and 'TAG+="uaccess"' in text
    assert 'ENV{ID_INPUT_KEYBOARD}=="1"' in text and 'ENV{ID_INPUT_MOUSE}=="1"' in text
    assert "GROUP=" not in text  # без групп: права даёт uaccess


def test_service_unit():
    text = (ROOT / "wayswitch.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/bin/wayswitch run" in text
    assert "After=graphical-session.target" in text
    assert "PartOf=graphical-session.target" in text
    # default.target поднял бы сервис до графической сессии (и при SSH-логине).
    assert "WantedBy=graphical-session.target" in text
    assert "WantedBy=default.target" not in text


def test_scripts_are_lf_and_executable_shebang():
    for name in ("install.sh", "uninstall.sh"):
        raw = (ROOT / name).read_bytes()
        assert b"\r\n" not in raw, name
        assert raw.startswith(b"#!/bin/bash"), name


def test_uninstall_has_sudo_user_validation():
    text = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
    assert "SUDO_USER" in text and "graphical-session.target.wants" in text


def test_install_has_xdg_runtime_dir():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "XDG_RUNTIME_DIR" in text


def test_install_updates_apt_index_before_install():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert text.index("apt-get update") < text.index("apt-get install")


def test_install_supports_arch_pacman():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "command -v pacman" in text
    assert "pacman -S --needed --noconfirm" in text
    # Имена пакетов в Arch другие, чем в apt/dnf.
    assert "python-evdev" in text and "libadwaita" in text


def test_install_warns_when_uinput_module_missing():
    # После обновления ядра модули старого ядра удалены, modprobe падает —
    # установка не должна обрываться, но обязана сказать про перезагрузку.
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "modprobe uinput" in text
    assert "перезагрузк" in text


def test_scripts_use_env_for_xdg_runtime_dir():
    for name in ("install.sh", "uninstall.sh"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "env XDG_RUNTIME_DIR=" in text, name


def test_install_pins_absolute_python():
    """Обёртки не должны зависеть от PATH: venv без PyGObject ломает запуск."""
    from pathlib import Path

    script = (Path(__file__).resolve().parent.parent / "packaging" / "install.sh").read_text(
        encoding="utf-8")
    assert "PY=/usr/bin/python3" in script
    assert "exec $PY -P -m wayswitch" in script
    assert "exec python3 -m wayswitch" not in script


@pytest.mark.parametrize("name,marker", [("wayswitch", "TRUSTED_CLI"),
                                         ("wayswitch-gui", "TRUSTED_GUI")])
def test_generated_source_wrapper_ignores_caller_checkout(shell, tmp_path, name, marker):
    import subprocess
    import sys

    binary = tmp_path / "bin"
    library = tmp_path / "installed"
    hostile = tmp_path / "checkout"
    binary.mkdir()
    (library / "wayswitch/gui").mkdir(parents=True)
    hostile.mkdir()
    (library / "wayswitch/__init__.py").write_text("")
    (library / "wayswitch/__main__.py").write_text("print('TRUSTED_CLI')\n")
    (library / "wayswitch/gui/__init__.py").write_text("")
    (library / "wayswitch/gui/app.py").write_text("def main():\n    print('TRUSTED_GUI')\n")
    (hostile / "wayswitch.py").write_text("raise SystemExit(67)\n")
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    start = source.index('cat > "$BIN/wayswitch" <<EOF')
    end = source.index('chmod +x "$BIN/wayswitch"', start)
    generation = 'BIN=$1\nLIB=$2\nPY=$3\n' + source[start:end]
    subprocess.run([shell, "-c", generation, "test", binary.as_posix(), library.as_posix(),
                    Path(sys.executable).as_posix()], check=True, capture_output=True)
    result = subprocess.run([shell, (binary / name).as_posix()], cwd=hostile,
                            capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == marker, result.stderr


# --- ярлык и значок ---------------------------------------------------------

def _desktop_text() -> str:
    return (ROOT / "io.github.Wetoshkin.WaySwitch.Settings.desktop").read_text(encoding="utf-8")


def _app_id() -> str:
    """Экспортированный ID нативного окна; GTK импортируется только при запуске."""
    from wayswitch.gui.app import APP_ID
    from wayswitch.gui.portable import NATIVE_ID

    assert APP_ID == NATIVE_ID == "io.github.Wetoshkin.WaySwitch.Settings"
    return APP_ID


def test_desktop_file_name_matches_app_id():
    """Имя ярлыка обязано совпадать с APP_ID окна.

    Иначе GNOME не связывает запущенное окно с записью: в доке и в списке
    приложений рисуется заглушка «нет значка», а иногда появляется второй значок.
    """
    assert (ROOT / "io.github.Wetoshkin.WaySwitch.Settings.desktop").exists()
    assert (ROOT / f"{_app_id()}.desktop").exists()


def test_desktop_entry_points_to_own_icon():
    text = _desktop_text()
    assert "Icon=io.github.Wetoshkin.WaySwitch" in text
    assert "StartupWMClass=" + _app_id() in text
    assert (ROOT / "icons" / "io.github.Wetoshkin.WaySwitch.svg").exists()


def test_icon_is_scalable_svg():
    text = (ROOT / "icons" / "io.github.Wetoshkin.WaySwitch.svg").read_text(encoding="utf-8")
    assert text.lstrip().startswith("<svg")
    assert 'viewBox="0 0 128 128"' in text
    # Буквы — контурами: текст в SVG без нужного шрифта разваливается.
    assert "<text" not in text


def test_symbolic_tray_icons_use_fill_only():
    """GTK и AppIndicator перекрашивают symbolic-значки по fill; обводка
    осталась бы тёмной на тёмной панели."""
    for name in ("io.github.Wetoshkin.WaySwitch-symbolic.svg",
                 "io.github.Wetoshkin.WaySwitch-paused-symbolic.svg"):
        text = (ROOT / "icons" / name).read_text(encoding="utf-8")
        assert 'viewBox="0 0 16 16"' in text
        assert "stroke" not in text and "<text" not in text


def test_installer_installs_icon_and_removes_old_entry():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "share/icons/hicolor" in text and "scalable/apps" in text
    assert "symbolic/apps" in text
    assert "io.github.Wetoshkin.WaySwitch.svg" in text
    # старое имя ярлыка (до 2.0) не должно оставаться вторым пунктом в меню
    assert '"$SRC/packaging/migrate.py" --prefix /usr/local' in text


def test_autostart_uses_same_name_as_desktop_entry():
    from wayswitch.gui import autostart

    assert autostart.DESKTOP_NAME == "io.github.Wetoshkin.WaySwitch.Settings.desktop"
    assert "Icon=io.github.Wetoshkin.WaySwitch" in autostart.tray_desktop_text()


def test_versions_agree():
    """Версия одна на всех: pyproject, пакет, расширение (version-name)."""
    import json
    import re

    from wayswitch import __version__

    repo = ROOT.parent
    pyproject = (repo / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1) == __version__
    meta = json.loads((repo / "extension" / "wayswitch@wetoshkin.github.io" / "metadata.json")
                      .read_text(encoding="utf-8"))
    assert meta["version-name"] == __version__
    plasmoid = json.loads((repo / "packaging/kde/io.github.Wetoshkin.wayswitch/metadata.json")
                          .read_text(encoding="utf-8"))
    assert plasmoid["KPlugin"]["Version"] == __version__
    pkgbuild = (repo / "packaging" / "arch" / "PKGBUILD").read_text(encoding="utf-8")
    assert f"pkgver={__version__}" in pkgbuild


def test_package_epoch_survives_version_renumbering():
    from tools.aur_update import srcinfo

    repo = ROOT.parent
    assert "Version: 1:@VERSION@" in (ROOT / "deb/control.in").read_text(encoding="utf-8")
    assert "Epoch:          1" in (ROOT / "rpm/wayswitch.spec").read_text(encoding="utf-8")
    pkgbuild = (ROOT / "arch/PKGBUILD").read_text(encoding="utf-8")
    assert "\tepoch = 1\n" in srcinfo(pkgbuild)
    assert (repo / "packaging/arch/.SRCINFO").read_text(encoding="utf-8") == srcinfo(pkgbuild)
