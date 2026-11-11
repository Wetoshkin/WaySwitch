"""AppImage native-service setup through the host package manager."""

from __future__ import annotations

from wayswitch import __version__, companion
from wayswitch.gui.widgets import run_in_thread
from wayswitch.i18n import gettext as _


def show(parent):
    from gi.repository import Adw, Gdk, Gtk

    dialog = Adw.Dialog(title=_("Native service installation"), content_width=680,
                        content_height=600)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                  margin_top=20, margin_bottom=20, margin_start=24, margin_end=24)
    status = Gtk.Label(label=_("Checking…"), xalign=0, wrap=True, selectable=True)
    status.add_css_class("heading")
    details = Gtk.Label(xalign=0, wrap=True, selectable=True)
    command = Gtk.Label(xalign=0, wrap=True, selectable=True)
    command.add_css_class("monospace")
    buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    refresh = Gtk.Button(label=_("Check again"))
    install = Gtk.Button(label=_("Install in terminal"), sensitive=False)
    install.add_css_class("suggested-action")
    copy = Gtk.Button(label=_("Copy command"), sensitive=False)
    for widget in (refresh, install, copy):
        buttons.append(widget)
    for widget in (status, details, command, buttons):
        box.append(widget)
    box.append(Gtk.Label(xalign=0, wrap=True, selectable=True, label=_(
        "After the package transaction, check again here and run wayswitch doctor. "
        "To activate the service when ready: systemctl --user enable --now wayswitch. "
        "Installing a package does not confirm a running, correctly configured service.")))
    scroll.set_child(box)
    toolbar.set_content(scroll)
    dialog.set_child(toolbar)
    state = {"info": None, "closed": False, "generation": 0}
    dialog.connect("closed", lambda *_: state.update(closed=True))

    def render(result, generation):
        if state["closed"] or generation != state["generation"]:
            return
        refresh.set_sensitive(True)
        install.set_sensitive(False)
        copy.set_sensitive(False)
        state["info"] = None
        if isinstance(result, Exception):
            status.set_label(_("Could not check native packages; no installation was started."))
            return
        state["info"] = result
        status.set_label(_("{distro}\nPackage manager: {manager}\nGUI: {gui}\n"
                           "Native installed: {installed}\n"
                           "Repository candidate: {candidate}").format(
                               distro=result.distro, manager=result.manager or _("unknown"),
                               gui=__version__, installed=result.installed or _("not found"),
                               candidate=result.candidate or _("unknown")))
        details.set_label(result.message)
        command.set_label(result.command)
        copy.set_sensitive(bool(result.command))
        install.set_sensitive(result.automatic)

    def check(*_args):
        state["generation"] += 1
        generation = state["generation"]
        state["info"] = None
        refresh.set_sensitive(False)
        install.set_sensitive(False)
        copy.set_sensitive(False)
        status.set_label(_("Checking…"))
        run_in_thread(lambda: companion.check(__version__),
                      lambda result: render(result, generation))

    def ask_install(*_args):
        info = state["info"]
        if info is None or not info.automatic:
            return
        install.set_sensitive(False)
        confirmation = Adw.AlertDialog(heading=_("Open the native package manager?"),
                                       body=info.command + "\n\n" + _(
                                           "Review the transaction in the terminal and approve "
                                           "authentication there. Cancellation or failure may "
                                           "leave package-manager changes; use its recovery "
                                           "instructions if needed."))
        confirmation.add_response("cancel", _("Cancel"))
        confirmation.add_response("install", _("Install in terminal"))
        confirmation.set_close_response("cancel")
        confirmation.set_default_response("cancel")

        def response(_dialog, selected):
            if selected != "install" or state["closed"] or state["info"] != info:
                if not state["closed"] and state["info"] == info:
                    install.set_sensitive(info.automatic)
                return
            install.set_sensitive(False)
            refresh.set_sensitive(False)
            generation = state["generation"]

            def ready(result):
                if state["closed"] or generation != state["generation"]:
                    return
                render(result, generation)
                if result != info:
                    details.set_label(_("Package information changed. "
                                        "Review it and confirm again."))
                elif companion.handoff(info, confirmed=True):
                    install.set_sensitive(False)
                    details.set_label(_("The package manager was opened in a terminal. Read its "
                                        "result there, then check again; installation and service "
                                        "activation have not been confirmed by this window."))
                else:
                    details.set_label(_("Could not open a terminal. Copy the command and run "
                                        "it in your terminal."))

            run_in_thread(lambda: companion.check(__version__), ready)

        confirmation.connect("response", response)
        confirmation.present(parent)

    refresh.connect("clicked", check)
    install.connect("clicked", ask_install)
    copy.connect("clicked", lambda *_: Gdk.Display.get_default().get_clipboard().set(
        state["info"].command if state["info"] is not None else ""))
    dialog.present(parent)
    check()
    return dialog
