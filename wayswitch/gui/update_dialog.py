"""One native update window, including a copyable path when installation is manual."""

from __future__ import annotations

import os
import subprocess

from wayswitch import __version__, updater
from wayswitch.gui import portable, terminal
from wayswitch.gui.widgets import adapt_header
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
    from gi.repository import Adw, Gdk, Gtk, Pango

    dialog = Adw.Dialog(title=_("Updates"), content_width=720, content_height=640)
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    adapt_header(dialog, header)
    refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Check again"))
    refresh.add_css_class("flat")
    refresh.update_property([Gtk.AccessibleProperty.LABEL], [_("Check again")])
    header.pack_start(refresh)
    copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=_("Copy command"))
    copy.add_css_class("flat")
    copy.update_property([Gtk.AccessibleProperty.LABEL], [_("Copy command")])
    header.pack_start(copy)
    # Действия диалога — в заголовке: три длинные подписи в строке не
    # помещались в узкое окно. Установка — единственная основная кнопка.
    install = Gtk.Button(label=_("Install"))
    install.add_css_class("suggested-action")
    install.set_tooltip_text(_("Install in terminal"))
    install.update_property([Gtk.AccessibleProperty.LABEL], [_("Install in terminal")])
    header.pack_end(install)
    toolbar.add_top_bar(header)
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                  margin_top=20, margin_bottom=20, margin_start=24, margin_end=24)
    source_label = Gtk.Label(xalign=0, selectable=True, wrap=True,
                             wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=52)
    source_label.add_css_class("heading")
    box.append(source_label)
    status_label = Gtk.Label(xalign=0, selectable=True, wrap=True,
                             wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=52)
    box.append(status_label)
    message = Gtk.Label(xalign=0, selectable=True, wrap=True,
                        wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=52)
    box.append(message)
    # WORD_CHAR переносит и длинные версии/команды без пробелов. Обычные
    # Label вместо TextView не запрашивают ширину целой строки.
    notes = Gtk.Label(xalign=0, selectable=True, wrap=True,
                      wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=52)
    box.append(notes)
    command = Gtk.Label(xalign=0, selectable=True, wrap=True,
                        wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=52)
    command.add_css_class("monospace")
    command_details = Gtk.Expander()
    command_details.set_label_widget(Gtk.Label(
        label=_("Command for manual update"), xalign=0, wrap=True,
        wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=52))
    command_details.set_child(command)
    box.append(command_details)
    if portable.package_kind():
        version = (app.proxy.status or {}).get("version", _("unknown"))
        service = Gtk.Label(xalign=0, wrap=True, selectable=True,
                            wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=52,
                            label=_("Native service version: {version}. Update it separately "
                                    "on the host through its original source:").format(
                                        version=version) + "\n\nwayswitch update --install")
        box.append(service)
        service_copy = Gtk.Button(label=_("Copy"), tooltip_text=_("Copy service update command"),
                                  halign=Gtk.Align.START)
        service_copy.update_property([Gtk.AccessibleProperty.LABEL],
                                     [_("Copy service update command")])
        service_copy.connect("clicked", lambda *_: Gdk.Display.get_default().get_clipboard().set(
            "wayswitch update --install"))
        box.append(service_copy)
        instructions = Gtk.Button(label=_("Instructions"),
                                   tooltip_text=_("Window and service instructions"),
                                   halign=Gtk.Align.START)
        instructions.update_property([Gtk.AccessibleProperty.LABEL],
                                     [_("Window and service instructions")])
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
        refresh.set_sensitive(True)
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
        command.set_label(state["copy"])
        command_details.set_visible(bool(state["copy"]))
        copy.set_sensitive(bool(state["copy"]))
        notes.set_label(
            info.available.notes if info.available and info.available.notes else
            _("Release notes for repository packages are supplied by their maintainer. "
              "The package manager or AUR helper shows the proposed transaction."))

    def do_check(*_args):
        refresh.set_sensitive(False)
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

    refresh.connect("clicked", do_check)
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
