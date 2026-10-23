from pathlib import Path

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
    assert "exec $PY -m wayswitch" in script
    assert "exec python3 -m wayswitch" not in script


# --- ярлык и значок ---------------------------------------------------------

def _desktop_text() -> str:
    return (ROOT / "ru.siberia.WaySwitch.Settings.desktop").read_text(encoding="utf-8")


def _app_id() -> str:
    """APP_ID окна без импорта GUI: импорт тянет GTK, которого в CI нет."""
    import re

    text = (ROOT.parent / "wayswitch" / "gui" / "app.py").read_text(encoding="utf-8")
    return re.search(r'APP_ID = "([^"]+)"', text).group(1)


def test_desktop_file_name_matches_app_id():
    """Имя ярлыка обязано совпадать с APP_ID окна.

    Иначе GNOME не связывает запущенное окно с записью: в доке и в списке
    приложений рисуется заглушка «нет значка», а иногда появляется второй значок.
    """
    assert (ROOT / "ru.siberia.WaySwitch.Settings.desktop").exists()
    assert (ROOT / f"{_app_id()}.desktop").exists()


def test_desktop_entry_points_to_own_icon():
    text = _desktop_text()
    assert "Icon=ru.siberia.WaySwitch" in text
    assert "StartupWMClass=" + _app_id() in text
    assert (ROOT / "icons" / "ru.siberia.WaySwitch.svg").exists()


def test_icon_is_scalable_svg():
    text = (ROOT / "icons" / "ru.siberia.WaySwitch.svg").read_text(encoding="utf-8")
    assert text.lstrip().startswith("<svg")
    assert 'viewBox="0 0 128 128"' in text
    assert "<text" in text and "А" in text and "A" in text


def test_installer_installs_icon_and_removes_old_entry():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "share/icons/hicolor/scalable/apps" in text
    assert "ru.siberia.WaySwitch.svg" in text
    # старое имя ярлыка (до 2.0) не должно оставаться вторым пунктом в меню
    assert "rm -f /usr/local/share/applications/ru.siberia.WaySwitch.desktop" in text


def test_autostart_uses_same_name_as_desktop_entry():
    from wayswitch.gui import autostart

    assert autostart.DESKTOP_NAME == "ru.siberia.WaySwitch.Settings.desktop"
    assert "Icon=ru.siberia.WaySwitch" in autostart.tray_desktop_text()
