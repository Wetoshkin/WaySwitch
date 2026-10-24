"""Sway, Hyprland и среды на labwc/Wayfire: разбор IPC и конфигов без композитора."""

import json

import pytest

from tests.fakes import RecordingTypist
from wayswitch import keycodes as kc
from wayswitch.backends import compositor, hyprland, sway, xkbnames
from wayswitch.backends.base import BackendError
from wayswitch.keymap import LayoutSpec

RULES_XML = """<?xml version="1.0"?>
<xkbConfigRegistry>
  <layoutList>
    <layout>
      <configItem><name>us</name><description>English (US)</description></configItem>
      <variantList>
        <variant><configItem><name>dvorak</name><description>English (Dvorak)</description>
        </configItem></variant>
      </variantList>
    </layout>
    <layout>
      <configItem><name>ru</name><description>Russian</description></configItem>
      <variantList>
        <variant><configItem><name>phonetic</name>
          <description>Russian (phonetic)</description></configItem></variant>
      </variantList>
    </layout>
  </layoutList>
</xkbConfigRegistry>"""
RULES = xkbnames.parse_rules(RULES_XML)


def test_xkb_descriptions():
    assert RULES["Russian"] == LayoutSpec("ru")
    assert RULES["English (Dvorak)"] == LayoutSpec("us", "dvorak")
    assert xkbnames.parse_layout_list("us,ru", ",phonetic") == [LayoutSpec("us"),
                                                                 LayoutSpec("ru", "phonetic")]


# --- Sway ----------------------------------------------------------------------------

class FakeSway:
    def __init__(self, index=0, names=("English (US)", "Russian")):
        self.index = index
        self.names = list(names)
        self.commands = []

    def request(self, msg_type, payload=""):
        if msg_type == sway.GET_INPUTS:
            return [{"type": "pointer"},
                    {"type": "keyboard", "xkb_layout_names": self.names,
                     "xkb_active_layout_index": self.index}]
        if msg_type == sway.RUN_COMMAND:
            self.commands.append(payload)
            self.index = int(payload.rsplit(" ", 1)[1])
            return [{"success": True}]
        raise AssertionError(msg_type)


def make_sway(fake):
    events = {}
    backend = sway.SwayBackend(settle_ms=0, ipc=fake, rules=RULES,
                               watch=lambda _ipc, cb: events.setdefault("cb", cb) and None)
    return backend, events


def test_sway_reads_layouts_and_switches():
    fake = FakeSway(index=1)
    backend, _ = make_sway(fake)
    assert backend.layouts() == [LayoutSpec("us"), LayoutSpec("ru")]
    assert backend.current() == 1
    backend.set(0)
    assert fake.commands == ["input type:keyboard xkb_switch_layout 0"]
    assert backend.wait_applied(0, 0.5)


def test_sway_external_change_event():
    fake = FakeSway(index=0)
    backend, events = make_sway(fake)
    seen = []
    backend.on_change(lambda i, ext: seen.append((i, ext)))
    events["cb"]({"change": "xkb_layout", "input": {"xkb_active_layout_index": 1}})
    backend.set(0)
    events["cb"]({"change": "xkb_layout", "input": {"xkb_active_layout_index": 0}})
    assert seen == [(1, True), (0, False)]


def test_sway_needs_two_layouts():
    with pytest.raises(BackendError):
        make_sway(FakeSway(names=("English (US)",)))


def test_sway_ipc_framing():
    raw = sway.pack(sway.RUN_COMMAND, "abc")
    magic, length, msg_type = sway.HEADER.unpack(raw[:sway.HEADER.size])
    assert (magic, length, msg_type) == (b"i3-ipc", 3, 0) and raw.endswith(b"abc")


# --- Hyprland -------------------------------------------------------------------------

class FakeHypr:
    def __init__(self, active="English (US)", with_index=False):
        self.active = active
        self.with_index = with_index
        self.sent = []

    def json(self, command):
        if command.startswith("getoption"):
            key = command.split()[-1]
            return {"input:kb_layout": {"str": "us,ru"}, "input:kb_variant": {"str": "[[EMPTY]]"},
                    "input:kb_options": {"str": "grp:alt_shift_toggle"}}[key]
        if command == "devices":
            kb = {"name": "kbd", "main": True, "active_keymap": self.active}
            if self.with_index:
                kb["active_layout_index"] = 1
            return {"keyboards": [kb]}
        raise AssertionError(command)

    def request(self, command):
        self.sent.append(command)
        self.active = "Russian" if command.endswith("1") else "English (US)"
        return "ok"


def make_hypr(fake):
    events = {}
    backend = hyprland.HyprlandBackend(settle_ms=0, ipc=fake, rules=RULES,
                                       watch=lambda _ipc, cb: events.setdefault("cb", cb) and None)
    return backend, events


def test_hyprland_reads_layouts_and_switches():
    fake = FakeHypr()
    backend, _ = make_hypr(fake)
    assert backend.layouts() == [LayoutSpec("us"), LayoutSpec("ru")]
    assert backend.xkb_options() == ["grp:alt_shift_toggle"]
    assert backend.current() == 0
    backend.set(1)
    assert fake.sent == ["switchxkblayout all 1"]
    assert backend.wait_applied(1, 0.5)


def test_hyprland_prefers_layout_index_when_present():
    backend, _ = make_hypr(FakeHypr(with_index=True))
    assert backend.current() == 1


def test_hyprland_activelayout_event():
    backend, events = make_hypr(FakeHypr())
    seen = []
    backend.on_change(lambda i, ext: seen.append((i, ext)))
    events["cb"]("activelayout>>kbd,Russian")
    events["cb"]("workspace>>2")
    assert seen == [(1, True)]


class FakeHyprVirtualMain(FakeHypr):
    """Наша виртуальная клавиатура стала «main», у макропада одна раскладка."""

    def json(self, command):
        if command == "devices":
            return {"keyboards": [
                {"name": "wayswitch-virtual-keyboard", "main": True,
                 "active_keymap": "English (US)"},
                {"name": "at-kbd", "main": False, "active_keymap": self.active}]}
        return super().json(command)

    def request(self, command):
        super().request(command)
        return "layout idx out of range of 1"


def test_hyprland_ignores_own_virtual_keyboard():
    fake = FakeHyprVirtualMain(active="Russian")
    backend, events = make_hypr(fake)
    assert backend.current() == 1  # раскладка настоящей клавиатуры
    seen = []
    backend.on_change(lambda i, ext: seen.append((i, ext)))
    events["cb"]("activelayout>>wayswitch-virtual-keyboard,English (US)")
    assert seen == []
    events["cb"]("activelayout>>usb-external-keyboard,English (US)")
    assert seen == [(0, True)]


def test_hyprland_partial_switch_reply_is_not_failure():
    fake = FakeHyprVirtualMain()
    backend, _ = make_hypr(fake)
    backend.set(1)  # настоящая клавиатура переключилась — не BackendError
    assert backend.current() == 1


def test_hyprland_event_parse():
    assert hyprland.parse_event("activelayout>>at-kbd,English (US)") == ("at-kbd", "English (US)")
    assert hyprland.parse_event("openwindow>>x") is None


# --- labwc / Wayfire ------------------------------------------------------------------

def test_labwc_environment(tmp_path):
    (tmp_path / "labwc").mkdir()
    (tmp_path / "labwc" / "environment").write_text(
        "# раскладки\nXKB_DEFAULT_LAYOUT=us,ru\nXKB_DEFAULT_OPTIONS=grp:alt_shift_toggle\n",
        encoding="utf-8")
    specs, options, where = compositor.read_xkb({"XDG_CONFIG_HOME": str(tmp_path)})
    assert specs == [LayoutSpec("us"), LayoutSpec("ru")]
    assert options == ["grp:alt_shift_toggle"] and where == "labwc"


def test_wayfire_ini(tmp_path):
    (tmp_path / "wayfire.ini").write_text(
        "[input]\nxkb_layout = us,ru\nxkb_options = grp:win_space_toggle\n", encoding="utf-8")
    specs, options, where = compositor.read_xkb({"XDG_CONFIG_HOME": str(tmp_path)})
    assert where == "Wayfire" and len(specs) == 2
    assert compositor.toggle_combos(options) == [[kc.KEY_LEFTMETA, kc.KEY_SPACE]]


def test_compositor_backend_switches_with_toggle(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path), "XKB_DEFAULT_LAYOUT": "us,ru",
           "XKB_DEFAULT_OPTIONS": "grp:alt_shift_toggle", "XDG_RUNTIME_DIR": str(tmp_path)}
    typist = RecordingTypist()
    backend = compositor.CompositorBackend(typist, settle_ms=0, env=env)
    assert backend.supports_auto is False  # счётчику автоисправление не доверяем
    backend.set(1)
    pressed = [code for code, value in (e for e in typist.events if e != "syn") if value == 1]
    assert pressed == [kc.KEY_LEFTALT, kc.KEY_LEFTSHIFT]
    assert backend.current() == 1
    assert compositor.CompositorBackend(typist, env=env, track=True).supports_auto


def test_compositor_backend_explains_missing_config(tmp_path):
    with pytest.raises(BackendError, match="XKB_DEFAULT_LAYOUT"):
        compositor.CompositorBackend(RecordingTypist(), env={"XDG_CONFIG_HOME": str(tmp_path)})
    env = {"XDG_CONFIG_HOME": str(tmp_path), "XKB_DEFAULT_LAYOUT": "us,ru"}
    with pytest.raises(BackendError, match="grp:"):
        compositor.CompositorBackend(RecordingTypist(), env=env)


def test_json_roundtrip_of_sway_reply():
    # Ответ sway — JSON; парсер не должен спотыкаться о юникод в описаниях.
    assert json.loads(json.dumps({"xkb_layout_names": ["Русская"]}, ensure_ascii=False))


# --- экран блокировки ------------------------------------------------------------------

def test_locker_processes_detected():
    from wayswitch.session import locker_running

    assert locker_running(["bash\n", "swaylock\n"])
    assert locker_running(["hyprlock"])
    assert locker_running(["kscreenlocker_g"])
    assert not locker_running(["firefox", "sway", "Hyprland"])


def test_process_names_reads_own_comm(tmp_path):
    import os

    from wayswitch.session import process_names

    (tmp_path / "123").mkdir()
    (tmp_path / "123" / "comm").write_text("swaylock\n", encoding="utf-8")
    (tmp_path / "self").mkdir()
    uid = os.stat(tmp_path / "123").st_uid
    assert process_names(str(tmp_path), uid=uid) == ["swaylock\n"]


def test_compositor_toggle_any_order_and_side(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path), "XKB_DEFAULT_LAYOUT": "us,ru",
           "XKB_DEFAULT_OPTIONS": "grp:alt_shift_toggle", "XDG_RUNTIME_DIR": str(tmp_path)}
    backend = compositor.CompositorBackend(RecordingTypist(), settle_ms=0, env=env)
    # Shift, затем правый Alt — XKB переключает и так.
    backend.observe_physical(kc.KEY_LEFTSHIFT, 1)
    backend.observe_physical(kc.KEY_RIGHTALT, 1)
    backend.observe_physical(kc.KEY_RIGHTALT, 0)
    backend.observe_physical(kc.KEY_LEFTSHIFT, 0)
    assert backend.current() == 1
    # Перезапуск службы посреди сеанса: счётчик не сбрасывается в 0.
    again = compositor.CompositorBackend(RecordingTypist(), settle_ms=0, env=env)
    assert again.current() == 1
