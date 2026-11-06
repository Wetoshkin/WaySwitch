"""Additional system sources must never become correction languages."""

from types import SimpleNamespace

import pytest

from tests.fakes import RU_TABLE, US_TABLE, FakeBackend, RecordingTypist, SafeProbe
from wayswitch.config import Config
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel
from wayswitch.keymap import LayoutSpec, TableKeymap, XkbKeymap


def environment(ids=("de", "ru", "us"), current=2, schedule=None):
    specs = [LayoutSpec.parse(i) for i in ids]
    km = TableKeymap([RU_TABLE if s.layout == "ru" else US_TABLE for s in specs], specs)
    backend, typist = FakeBackend(layouts=specs, current=current), RecordingTypist()
    clock = SimpleNamespace(now=100.0)
    def now():
        return clock.now
    detector = Detector(km, {
        "ru": LanguageModel.from_words("ru", [("привет", 100)], "привет"),
        "en": LanguageModel.from_words("en", [("hello", 100)], "hello"),
    })
    corrected = []
    ctrl = Controller(Config(), km, detector, backend, typist, clock=now,
                      probe=SafeProbe(now), schedule=schedule or (lambda work: work()),
                      on_corrected=lambda *args: corrected.append(args))
    backend.on_change(ctrl.on_layout_changed)
    return SimpleNamespace(km=km, backend=backend, typist=typist, ctrl=ctrl,
                           clock=clock, corrected=corrected)


def type_text(env, text):
    from wayswitch import keycodes as kc

    for ch in text:
        code, shift = env.km.key_for(ch, env.backend.current())
        if shift:
            env.ctrl.on_key(kc.KEY_LEFTSHIFT, 1, 1)
        env.ctrl.on_key(code, 1, 1)
        env.ctrl.on_key(code, 0, 1)
        if shift:
            env.ctrl.on_key(kc.KEY_LEFTSHIFT, 0, 1)
        env.clock.now += .05


@pytest.mark.parametrize("current,opposite", [(0, None), (1, 2), (2, 1), (3, 1),
                                               (4, 2), (None, None), (-1, None), (5, None)])
def test_pair_keeps_system_indices_and_current_variant(current, opposite):
    env = environment(("de", "ru", "us", "us+dvorak", "ru+phonetic"))
    assert env.km.opposite_group(current) == opposite


@pytest.mark.parametrize("source", ["de", "fr", "pl", "uk", "be", "bg"])
def test_other_languages_are_not_recognized_by_alphabet(source):
    env = environment((source, "ru", "us"))
    assert env.km.language(0) is None


def test_language_metadata_overrides_layout_country_and_inherits_variants(monkeypatch):
    from wayswitch.backends import xkbnames

    xml = '''<xkbConfigRegistry><layoutList><layout>
      <configItem><name>us</name><languageList><iso639Id>eng</iso639Id></languageList></configItem>
      <variantList><variant><configItem><name>dvorak</name></configItem></variant>
      <variant><configItem><name>haw</name><languageList><iso639Id>haw</iso639Id>
      </languageList></configItem></variant></variantList></layout></layoutList></xkbConfigRegistry>'''
    metadata = xkbnames.parse_languages(xml)
    monkeypatch.setattr(xkbnames, "_system_languages", lambda: metadata)
    assert xkbnames.language_for_spec(LayoutSpec("us", "dvorak")) == "en"
    assert xkbnames.language_for_spec(LayoutSpec("us", "haw")) is None


def test_gnome_sources_preserve_ime_position(monkeypatch):
    from wayswitch.backends import gnome_common

    raw = [("xkb", "de"), ("ibus", "mozc-jp"), ("xkb", "ru"), ("xkb", "us")]
    settings = SimpleNamespace(get_value=lambda _: SimpleNamespace(unpack=lambda: raw))
    monkeypatch.setattr(gnome_common, "_settings", lambda _: settings)
    specs = gnome_common.read_sources()
    assert len(specs) == 4 and specs[1].kind == "ibus"
    assert gnome_common.index_of_engine("xkb:us::eng", specs) == 3
    assert gnome_common.index_of_mru([("ibus", "mozc-jp")], specs) == 1


def test_unknown_ibus_engine_revokes_the_previous_layout():
    from wayswitch.backends.gnome_ibus import GnomeIbusBackend

    backend = object.__new__(GnomeIbusBackend)
    backend._specs = [LayoutSpec("us"), LayoutSpec("ru")]
    backend._current, backend._expected = 1, None
    changes = []
    backend._callbacks = [lambda *args: changes.append(args)]
    backend._on_engine_changed(None, "unknown-input-method")
    assert backend.current() is None and changes == [(None, True)]


def test_named_keymap_keeps_opaque_slots_without_compiling_other_languages(monkeypatch):
    from wayswitch import keymap

    compiled = []

    def native_compile(_context, pointer, _flags):
        name = pointer._obj.layout.decode()
        compiled.append(name)
        if name not in ("us", "ru"):
            raise RuntimeError("unsupported custom source")
        return name

    def compile_map(cls, lib, build, specs):
        name = build(None)
        return cls([US_TABLE if name == "us" else RU_TABLE], specs)

    monkeypatch.setattr(keymap, "_load_lib", lambda: SimpleNamespace(
        xkb_keymap_new_from_names=native_compile))
    monkeypatch.setattr(XkbKeymap, "_compile", classmethod(compile_map))
    specs = [LayoutSpec("custom-extra"), LayoutSpec("us"), LayoutSpec("ru"),
             LayoutSpec("de"), LayoutSpec("mozc", kind="ibus")]
    km = XkbKeymap.from_names(specs, [])
    assert compiled == ["us", "ru"]
    assert km.num_groups == 5 and km.opposite_group(1) == 2
    assert km.char(34, 1) == "g" and km.char(34, 2) == "п"
    assert km._table(0) == km._table(3) == km._table(4) == {}


def test_hotkey_counter_with_extra_sources_is_not_an_authority():
    from wayswitch.backends.hotkey import HotkeyBackend

    typist = RecordingTypist()
    backend = HotkeyBackend(typist, specs=[LayoutSpec("us"), LayoutSpec("de"), LayoutSpec("ru")],
                            combos=[[125, 57]])
    assert backend.current() is None
    with pytest.raises(Exception, match="расклад"):
        backend.set(2)
    assert typist.events == []


def test_correction_and_undo_use_actual_pair_indices():
    env = environment()
    type_text(env, "ghbdtn ")
    assert env.backend.set_calls == [1]
    assert env.corrected == [("ghbdtn", "привет", False)]
    assert env.ctrl.manual_word()
    assert env.backend.set_calls == [1, 2]
    assert env.ctrl.status()["undo_total"] == 1


def test_extra_layout_is_passive_and_return_resumes_without_resume():
    env = environment(current=0)
    type_text(env, "ghbdtn ")
    assert env.typist.events == [] and not env.ctrl.manual_word()
    assert not env.ctrl.manual_phrase()
    assert env.ctrl.status()["active"] is False
    assert env.ctrl.status()["layout"] == "other"
    assert not env.ctrl.transform_selection("translit", "hello")
    env.backend.emit_external_change(2)
    assert env.ctrl.status()["active"] is True
    env.clock.now += 1
    type_text(env, "ghbdtn ")
    assert env.corrected == [("ghbdtn", "привет", False)]


def test_external_switch_cancels_pending_correction():
    queue = []
    env = environment(schedule=queue.append)
    type_text(env, "ghbdtn ")
    assert queue
    env.backend.emit_external_change(0)
    for work in queue:
        work()
    assert env.typist.events == [] and env.corrected == []


def test_unknown_or_unannounced_source_change_cancels_pending_correction():
    for current in (0, None):
        queue = []
        env = environment(schedule=queue.append)
        type_text(env, "ghbdtn ")
        env.backend.current_index = current
        for work in queue:
            work()
        assert env.typist.events == [] and env.corrected == []


def test_selected_text_conversion_uses_nonzero_pair():
    env = environment()
    assert env.ctrl.fix_selection("ghbdtn")
    assert env.backend.set_calls == [1]
    assert env.corrected == [("ghbdtn", "привет", True)]


@pytest.mark.parametrize("desktop", ["gnome", "kde", "sway", "hyprland", "cosmic",
                                     "umbriel", "xfce", "cinnamon"])
@pytest.mark.parametrize("ids", [("de", "ru", "us"), ("us",), ("de", "fr", "pl", "ru", "us")])
def test_doctor_layout_policy_does_not_block_setup(desktop, ids, monkeypatch):
    from wayswitch import doctor
    from wayswitch.backends import (
        compositor,
        cosmic,
        gnome_common,
        hyprland,
        kde_plasma,
        sway,
        umbriel,
    )
    from wayswitch.gui import texts

    env = environment(ids, current=0)
    specs = env.backend.layouts()
    real_check = doctor._check

    def check(name, fn, hint, required=True, key=""):
        if key in ("layouts", "switch", "layout_scope"):
            return real_check(name, fn, hint, required, key)
        return doctor.Check(name, True, "", required, key)

    monkeypatch.setattr(doctor, "detect_desktop", lambda: desktop)
    monkeypatch.setattr(doctor, "_check", check)
    monkeypatch.setattr(gnome_common, "read_sources", lambda: specs)
    monkeypatch.setattr(kde_plasma, "KdePlasmaBackend", lambda: env.backend)
    monkeypatch.setattr(compositor, "read_xkb", lambda: (specs, ["grp:alt_shift_toggle"], "test"))
    monkeypatch.setattr(XkbKeymap, "from_names", lambda *_: env.km)
    monkeypatch.setattr(cosmic, "CosmicBackend", lambda **_: SimpleNamespace(
        keymap=SimpleNamespace(specs=specs, num_groups=len(specs),
                               language=env.km.language, opposite_group=env.km.opposite_group),
        current=lambda: 0, close=lambda: None))
    monkeypatch.setattr(sway, "find_socket", lambda: "/fake")
    monkeypatch.setattr(sway, "SwayIpc", lambda _: SimpleNamespace(request=lambda *_: []))
    monkeypatch.setattr(sway, "keyboard_state", lambda _: ([s.layout for s in specs], 0))
    monkeypatch.setattr(sway, "specs_from_names", lambda _: specs)
    monkeypatch.setattr(umbriel, "find_socket", lambda: "/fake")
    monkeypatch.setattr(umbriel, "UmbrielIpc", lambda _: SimpleNamespace(request=lambda *_: {}))
    monkeypatch.setattr(umbriel, "keyboard_state", lambda _: ([], 0))
    monkeypatch.setattr(hyprland, "find_dir", lambda: "/fake")
    monkeypatch.setattr(hyprland, "HyprIpc", lambda _: SimpleNamespace(
        request=lambda *_: "ok", json=lambda *_: {"str": ",".join(ids)}))
    checks = doctor.run_checks()
    layout = next(c for c in checks if c.key == "layouts")
    assert layout.ok is (len(ids) > 1) and layout.required is False
    rows = texts.onboarding_rows([layout])
    assert not texts.onboarding_blocking(rows)
    scope = next(c for c in checks if c.key == "layout_scope")
    assert not scope.required
    assert any("русск" in r.subtitle and "английск" in r.subtitle
               for r in texts.onboarding_rows([scope]))


def test_gui_does_not_offer_manual_edits_on_unsupported_source():
    from wayswitch.gui import texts

    env = environment(current=0)
    view = texts.status_view(env.ctrl.status())
    assert "вручную" not in view.title.lower()
    assert "русск" in view.subtitle and "английск" in view.subtitle


def test_cinnamon_diagnostics_use_the_same_sources_as_its_backend(monkeypatch):
    from wayswitch import doctor
    from wayswitch.backends import compositor, gnome_common

    env = environment()
    real_check = doctor._check

    def check(name, fn, hint, required=True, key=""):
        if key == "layouts":
            return real_check(name, fn, hint, required, key)
        return doctor.Check(name, True, "", required, key)

    monkeypatch.setattr(doctor, "_check", check)
    monkeypatch.setattr(doctor, "detect_desktop", lambda: "cinnamon")
    monkeypatch.setattr(compositor, "read_xkb", lambda: ([], [], ""))
    monkeypatch.setattr(gnome_common, "read_sources", env.backend.layouts)
    monkeypatch.setattr(XkbKeymap, "from_names", lambda *_: env.km)
    checks = doctor.run_checks()
    assert next(c for c in checks if c.key == "layouts").ok


@pytest.mark.parametrize("current,allowed", [(None, True), (0, False)])
def test_ibus_start_on_unknown_source_does_not_permanently_disable_ru_en(current, allowed,
                                                                       monkeypatch):
    import sys
    from types import ModuleType

    from wayswitch import daemon

    class StartupReached(RuntimeError):
        pass

    def stop():
        raise StartupReached

    env = environment(("us", "ru"), current=0)
    env.backend.name = "ibus"
    env.backend.current_index = current
    env.backend.verify_layout = lambda: False
    repository = ModuleType("gi.repository")
    repository.GLib = SimpleNamespace(MainContext=SimpleNamespace(default=stop))
    monkeypatch.setitem(sys.modules, "gi", ModuleType("gi"))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    monkeypatch.setattr(daemon, "UInputTypist", lambda: env.typist)
    monkeypatch.setattr(daemon, "choose_backend", lambda *_, **__: env.backend)
    monkeypatch.setattr(daemon.cfgmod, "load", lambda _: Config())
    monkeypatch.setattr(daemon.cfgmod, "load_exceptions", set)
    monkeypatch.setattr(daemon.XkbKeymap, "from_names", lambda *_: env.km)
    monkeypatch.setattr(daemon.LanguageModel, "load", lambda _: object())
    with pytest.raises(StartupReached):
        daemon.run(None, False, False)
    assert env.backend.supports_auto is allowed


@pytest.mark.skipif(not XkbKeymap.available(), reason="нет libxkbcommon")
def test_real_xkb_five_sources_keep_last_group_and_ime_slot():
    specs = [LayoutSpec("de"), LayoutSpec("fr"), LayoutSpec("pl"),
             LayoutSpec("mozc", kind="ibus"), LayoutSpec("ru"), LayoutSpec("us", "dvorak")]
    km = XkbKeymap.from_names(specs, [])
    assert km.num_groups == 6
    assert [km.language(g) for g in range(6)] == [None, None, None, None, "ru", "en"]
    assert km.opposite_group(5) == 4
    assert km.key_for("q", 5) == (45, False)
    assert km._table(3) == {}
