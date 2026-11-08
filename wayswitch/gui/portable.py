"""Portable GUI package integration with the native host companion."""

from __future__ import annotations

import os
from pathlib import Path

from wayswitch.gui.terminal import manual_command

FLATPAK_ID = "io.github.Wetoshkin.WaySwitch.Gui"
APPIMAGE_ID = "io.github.Wetoshkin.WaySwitch.AppImage"
NATIVE_ID = "io.github.Wetoshkin.WaySwitch.Settings"
RELEASES_URL = "https://github.com/Wetoshkin/WaySwitch/releases"
PACKAGED_UPDATE_TEXT = "Обновления через пакетный менеджер"
SETUP = (
    "Это окно настроек. Исправление и жесты выполняет отдельная служба WaySwitch "
    "на основной системе. Установите нативный пакет из Releases или выполните "
    "в терминале основной системы:\n\n" + manual_command(False) +
    "\n\nУстановщик запросит пароль для правил клавиатуры. Затем выйдите из сеанса "
    "и войдите снова и запустите:\n\nsystemctl --user enable --now wayswitch.service"
    "\nwayswitch doctor\n\nНа Bazzite используется rpm-ostree; после установки нужен "
    "перезапуск. Не выдавайте переносимому окну права устройств через Flatseal."
)


def package_kind() -> str | None:
    if os.environ.get("FLATPAK_ID") == FLATPAK_ID:
        return "flatpak"
    if os.environ.get("WAYSWITCH_PACKAGE") == "appimage":
        return "appimage"
    return None


def is_system_packaged() -> bool:
    """True, если установлено через менеджер пакетов дистрибутива (RPM/deb/AUR)."""
    return (Path(__file__).resolve().parent.parent / ".packaged").exists()


def can_self_update() -> bool:
    """GUI сам проверяет обновления, только если это не пакетная установка."""
    return not is_system_packaged()


def application_id() -> str:
    return {"flatpak": FLATPAK_ID, "appimage": APPIMAGE_ID}.get(package_kind(), NATIVE_ID)


def instructions(action: str = "setup") -> str:
    if action == "update":
        gui = (("Для локального .flatpak: скачайте новый WaySwitch-Settings.flatpak и "
                "SHA256SUMS из того же релиза. В каталоге загрузки выполните:\n\n"
                "grep -E '^[0-9a-fA-F]{64} [ *]WaySwitch-Settings[.]flatpak$' SHA256SUMS |\n"
                "sha256sum -c - &&\n"
                "flatpak install --user --or-update ./WaySwitch-Settings.flatpak\n\n"
                f"Для установки из репозитория (например, Flathub): flatpak update {FLATPAK_ID}")
               if package_kind() == "flatpak" else
               "Скачайте новый AppImage из Releases, проверьте SHA256SUMS, "
               "закройте окно и замените файл AppImage.")
        return ("Обновление окна:\n\n" + gui + "\n\nОтдельно обновите нативную службу "
                "в терминале основной системы:\n\n" + manual_command(False) +
                "\n\nПосле обновления расширения GNOME войдите в сеанс заново.")
    if action == "remove":
        gui = (f"flatpak uninstall {FLATPAK_ID}" if package_kind() == "flatpak" else
               "Закройте окно и удалите файл AppImage.")
        return ("Удаление окна:\n\n" + gui + "\n\nСлужба продолжит исправлять слова. "
                "Чтобы отдельно удалить её, выполните в терминале основной системы:\n\n" +
                manual_command(True) + "\n\nОбщие настройки ~/.config/wayswitch сохраняются.")
    return SETUP


def connection_report(status: dict | None) -> str:
    if status is None:
        return "D-Bus: disconnected\n\n" + SETUP
    return (f"Package: {package_kind()}\nD-Bus: connected\n" +
            "\n".join(f"{k}: {v}" for k, v in sorted(status.items())) +
            "\n\nДиагностика нативной службы: wayswitch doctor\n"
            "Журнал: journalctl --user -u wayswitch.service -n 200 --no-pager")


def show_instructions(parent, action: str = "setup"):
    from gi.repository import Adw, Gdk, Gio

    text = instructions(action)
    dialog = Adw.AlertDialog(heading="WaySwitch: окно и нативная служба", body=text)
    dialog.add_response("close", "Закрыть")
    dialog.add_response("releases", "Releases")
    dialog.add_response("copy", "Скопировать инструкции")
    dialog.set_close_response("close")

    def response(_dialog, selected):
        if selected == "copy":
            Gdk.Display.get_default().get_clipboard().set(text)
        elif selected == "releases":
            Gio.AppInfo.launch_default_for_uri(RELEASES_URL, None)

    dialog.connect("response", response)
    dialog.present(parent)
    return dialog
