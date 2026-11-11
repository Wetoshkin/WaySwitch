"""Portable GUI package integration with the native host companion."""

from __future__ import annotations

import os
from pathlib import Path

from wayswitch.gui.terminal import manual_command
from wayswitch.i18n import gettext as _

FLATPAK_ID = "io.github.Wetoshkin.WaySwitch.Gui"
APPIMAGE_ID = "io.github.Wetoshkin.WaySwitch.AppImage"
NATIVE_ID = "io.github.Wetoshkin.WaySwitch.Settings"
RELEASES_URL = "https://github.com/Wetoshkin/WaySwitch/releases"
# Английский исходный текст; перевод берётся при показе.
PACKAGED_UPDATE_TEXT = "Updates through the package manager"
# Команды и регулярное выражение не переводятся: {64} внутри значения не
# должно попасть в str.format шаблона перевода.
FLATPAK_UPDATE_COMMAND = (
    "grep -E '^[0-9a-fA-F]{64} [ *]WaySwitch-Settings[.]flatpak$' SHA256SUMS |\n"
    "sha256sum -c - &&\n"
    "flatpak install --user --or-update ./WaySwitch-Settings.flatpak"
)
FLATPAK_UPDATE_TEMPLATE = (
    "For a local .flatpak: download the new WaySwitch-Settings.flatpak and "
    "SHA256SUMS from the same release. In the download folder run:\n\n"
    "{command}\n\n"
    "For a repository install (for example Flathub): flatpak update {id}"
)
SETUP = (
    "This is the settings window. Correction and gestures are performed by a separate "
    "WaySwitch service on the host system. Install the native package from Releases "
    "or run in a terminal on the host system:\n\n" + manual_command(False) +
    "\n\nThe installer will ask for the password for keyboard rules. Then sign out and "
    "sign in again, and run:\n\nsystemctl --user enable --now wayswitch.service"
    "\nwayswitch doctor\n\nOn Bazzite, rpm-ostree is used; a reboot is required after "
    "installation. Do not grant the portable window device access through Flatseal."
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
        gui = _(FLATPAK_UPDATE_TEMPLATE).format(
            command=FLATPAK_UPDATE_COMMAND, id=FLATPAK_ID) \
            if package_kind() == "flatpak" else \
            _("Download the new AppImage from Releases, verify SHA256SUMS, close the "
              "window and replace the AppImage file.")
        return (_("Window update:\n\n") + gui +
                _("\n\nUpdate the native service separately in a terminal on the host "
                  "system through its original source:\n\n") + "wayswitch update --install" +
                _("\n\nIf the installed service does not support this command yet, use its "
                  "original package manager or installation method.") +
                _("\n\nAfter updating the GNOME extension, sign in again."))
    if action == "remove":
        gui = (_("flatpak uninstall {id}").format(id=FLATPAK_ID)
               if package_kind() == "flatpak" else
               _("Close the window and delete the AppImage file."))
        return (_("Window removal:\n\n") + gui +
                _("\n\nThe service will keep correcting words. To remove it separately, "
                  "run in a terminal on the host system:\n\n") +
                manual_command(True) +
                _("\n\nShared settings in ~/.config/wayswitch are kept."))
    return _(SETUP)


def connection_report(status: dict | None) -> str:
    if status is None:
        return "D-Bus: disconnected\n\n" + _(SETUP)
    return (f"Package: {package_kind()}\nD-Bus: connected\n" +
            "\n".join(f"{k}: {v}" for k, v in sorted(status.items())) +
            _("\n\nNative service diagnostics: wayswitch doctor\n"
              "Log: journalctl --user -u wayswitch.service -n 200 --no-pager"))


def show_instructions(parent, action: str = "setup"):
    if action == "setup" and package_kind() == "appimage":
        from wayswitch.gui.companion_dialog import show

        return show(parent)
    from gi.repository import Adw, Gdk, Gio

    text = instructions(action)
    dialog = Adw.AlertDialog(heading=_("WaySwitch: window and native service"), body=text)
    dialog.add_response("close", _("Close"))
    dialog.add_response("releases", "Releases")
    dialog.add_response("copy", _("Copy instructions"))
    dialog.set_close_response("close")

    def response(_dialog, selected):
        if selected == "copy":
            Gdk.Display.get_default().get_clipboard().set(text)
        elif selected == "releases":
            Gio.AppInfo.launch_default_for_uri(RELEASES_URL, None)

    dialog.connect("response", response)
    dialog.present(parent)
    return dialog
