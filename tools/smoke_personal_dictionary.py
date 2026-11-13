"""Проверка настоящих GTK-виджетов словаря в отдельном профиле под Xvfb."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace


def main():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    from wayswitch import config
    from wayswitch.gui.dialogs import personal_word_dialog
    from wayswitch.gui.preferences import Preferences

    scratch = Path("build")
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dictionary-gui-", dir=scratch) as profile:
        os.environ["XDG_CONFIG_HOME"] = str(Path(profile).resolve())
        Adw.init()
        app = Adw.Application(application_id="io.github.Wetoshkin.WaySwitch.DictionaryTest")
        app.register(None)
        window = Adw.ApplicationWindow(application=app)
        window.present()
        calls = []
        proxy = SimpleNamespace(status={}, call=calls.append)
        config.config_dir().mkdir(parents=True)
        original = b'[meta]\nschema = 1\n[general]\nlearn_after_undos = 3\n'
        config.default_path().write_bytes(original)
        prefs = Preferences(SimpleNamespace(proxy=proxy, update_text=lambda: "Test profile"))
        prefs.present(window)
        assert prefs.cfg.general.learn_after_converts == 0
        prefs.step_positive.plus.emit("clicked")
        prefs.step_positive.plus.emit("clicked")
        prefs._flush()
        assert config.load().general.learn_after_converts == 2
        assert config.default_path().with_name(
            "config.toml.before-schema-2").read_bytes() == original

        def children(widget):
            yield widget
            child = widget.get_first_child()
            while child is not None:
                yield from children(child)
                child = child.get_next_sibling()

        def edit(entry, value):
            dialog = personal_word_dialog(
                prefs.dialog, entry,
                lambda new: prefs._change_personal(add=new, remove=entry))
            widgets = list(children(dialog))
            word = next(w for w in widgets if isinstance(w, Adw.EntryRow))
            save = next(w for w in widgets if isinstance(w, Gtk.Button)
                        and w.has_css_class("suggested-action"))
            word.set_text("two words")
            save.emit("clicked")
            assert ("en", "two words") not in prefs.personal_store.words
            word.set_text(value)
            save.emit("clicked")

        edit(None, "ubuntu")
        assert prefs.personal_store.words == {("en", "ubuntu")}
        edit(("en", "ubuntu"), "ubuntufoo")
        assert prefs.personal_store.words == {("en", "ubuntufoo")}
        prefs._exclude_personal("ubuntufoo")
        assert "ubuntufoo" in config.load_exceptions()
        prefs._change_personal(remove=("en", "ubuntufoo"))
        assert not prefs.personal_store.words
        assert calls.count("Reload") >= 4
        prefs.dialog.close()
        window.destroy()
    print("Personal dictionary GTK smoke passed")


if __name__ == "__main__":
    main()
