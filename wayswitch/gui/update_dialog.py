"""One native update window, including a copyable path when installation is manual."""

from __future__ import annotations

import os
import subprocess

from wayswitch import __version__, updater
from wayswitch.gui import portable, terminal
from wayswitch.i18n import gettext as _


def launch(info: updater.UpdateInfo, app_id: str) -> bool:
    from wayswitch.backends.desktop import detect_desktop

    script = updater.install_script(info, app_id=app_id, pid=os.getpid())
    argv = terminal.terminal_argv(script, detect_desktop())
    if argv is None:
        return False
    try:
        subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=updater.host_environment())
        return True
    except OSError:
        return False


def show(app):
    from gi.repository import Adw, Gdk, Gtk

    dialog = Adw.Dialog(title=_("Updates"), content_width=720, content_height=640)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                  margin_top=20, margin_bottom=20, margin_start=24, margin_end=24)
    source_label = Gtk.Label(xalign=0, selectable=True, wrap=True)
    source_label.add_css_class("heading")
    box.append(source_label)
    status_label = Gtk.Label(xalign=0, selectable=True, wrap=True)
    box.append(status_label)
    message = Gtk.Label(xalign=0, selectable=True, wrap=True)
    box.append(message)
    buttons = Gtk.Box(spacing=12)
    check = Gtk.Button(label=_("Check again"))
    install = Gtk.Button(label=_("Install in terminal"))
    install.add_css_class("suggested-action")
    copy = Gtk.Button(label=_("Copy command"))
    for button in (check, install, copy):
        buttons.append(button)
    box.append(buttons)
    notes = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
    notes.set_hexpand(True)
    box.append(notes)
    command = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                           wrap_mode=Gtk.WrapMode.WORD_CHAR)
    command_details = Gtk.Expander(label=_("Command for manual update"))
    command_details.set_child(command)
    box.append(command_details)
    if portable.package_kind():
        version = (app.proxy.status or {}).get("version", _("unknown"))
        service = Gtk.Label(xalign=0, wrap=True, selectable=True,
                            label=_("Native service version: {version}. Update it separately "
                                    "on the host through its original source:").format(
                                        version=version) + "\n\nwayswitch update --install")
        box.append(service)
        service_copy = Gtk.Button(label=_("Copy service update command"), halign=Gtk.Align.START)
        service_copy.connect("clicked", lambda *_: Gdk.Display.get_default().get_clipboard().set(
            "wayswitch update --install"))
        box.append(service_copy)
        instructions = Gtk.Button(label=_("Window and service instructions"),
                                   halign=Gtk.Align.START)
        instructions.connect("clicked", lambda *_: portable.show_instructions(
            app._parent(), "update"))
        box.append(instructions)
        if portable.package_kind() == "flatpak" and not portable.is_system_packaged():
            bundle = Gtk.Button(label=_("I installed a downloaded .flatpak bundle…"),
                                 halign=Gtk.Align.START)

            def choose_bundle(*_args):
                app._update_source_override = updater.Source(
                    "github-flatpak", "flatpak", portable.FLATPAK_ID)
                do_check()

            bundle.connect("clicked", choose_bundle)
            box.append(bundle)
    scroll.set_child(box)
    toolbar.set_content(scroll)
    dialog.set_child(toolbar)
    state = {"info": None, "closed": False, "copy": ""}
    dialog.connect("closed", lambda *_: state.update(closed=True))

    def render(_result=None):
        if state["closed"]:
            return
        info = getattr(app, "update_info", None)
        state["info"] = info
        check.set_sensitive(True)
        if info is None:
            status_label.set_label(_("Could not check the installation source"))
            return
        source_label.set_label(_("Source: {source}\nInstalled: {version} (window {gui})").format(
            source=info.source.label, version=info.source.installed or __version__,
            gui=__version__))
        status_label.set_label(updater.status_text(info))
        message.set_label(info.message + "\n\n" + _("Installation runs visibly in a terminal; "
                          "confirm the transaction and enter your password if requested. "
                          "GNOME extension updates may require signing out and back in."))
        install.set_sensitive(bool(info.available and info.automatic))
        state["copy"] = updater.manual_command(info)
        command.get_buffer().set_text(state["copy"])
        command_details.set_visible(bool(state["copy"]))
        copy.set_sensitive(bool(state["copy"]))
        notes.get_buffer().set_text(
            info.available.notes if info.available and info.available.notes else
            _("Release notes for repository packages are supplied by their maintainer. "
              "The package manager or AUR helper shows the proposed transaction."))

    def do_check(*_args):
        check.set_sensitive(False)
        install.set_sensitive(False)
        copy.set_sensitive(False)
        status_label.set_label(_("Checking…"))
        app.check_updates(force=True, done=render)

    def do_install(*_args):
        info = state["info"]
        if info is None or not info.automatic or not info.available:
            return
        if launch(info, portable.application_id()):
            install.set_sensitive(False)
            status_label.set_label(_("Installation is running in the terminal. "
                                    "Its result will be verified before restarting."))
        else:
            status_label.set_label(_("Could not open a terminal. Copy the command and run "
                                    "it in your terminal."))

    check.connect("clicked", do_check)
    copy.connect("clicked", lambda *_: Gdk.Display.get_default().get_clipboard().set(state["copy"]))
    install.connect("clicked", do_install)
    dialog.present(app._parent())
    install.set_sensitive(False)
    copy.set_sensitive(False)
    if getattr(app, "update_info", None) is not None:
        render()
    else:
        do_check()
    return dialog
