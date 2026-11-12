import pytest

from tests.test_controller import env as env
from tests.test_controller import tap, type_text, typed_text
from wayswitch import keycodes as kc
from wayswitch.a11y import FocusSnapshot


class ChangingProbe:
    def __init__(self, clock, **values):
        self.clock = clock
        self.values = values

    def snapshot(self):
        return FocusSnapshot(taken_at=self.clock(), **self.values)

    def poke(self):
        pass


@pytest.mark.parametrize("protected", [False, True])
def test_confirmed_application_change_without_editable_field_cancels(env, protected):
    ctrl = env["ctrl"]
    ctrl.config.general.skip_password_fields = False
    probe = ChangingProbe(env["clock"], app_ids=("first.app",))
    ctrl.set_probe(probe)
    type_text(env, "ghbdtn")

    probe.values.update(app_ids=("second.app",), password=protected)
    tap(env, kc.KEY_SPACE)

    assert ctrl.stats["corrections"] == 0
    assert env["typist"].taps() == []


def test_partial_metadata_gap_keeps_last_confirmed_field_owner(env):
    ctrl = env["ctrl"]
    ctrl.config.general.skip_password_fields = False
    probe = ChangingProbe(env["clock"], app="editor", app_ids=("editor",),
                          field=(":1.1", "/first"))
    ctrl.set_probe(probe)
    type_text(env, "ghb")

    probe.values["field"] = None
    type_text(env, "d")
    probe.values["field"] = (":1.1", "/second")
    type_text(env, "tn ")

    assert ctrl.stats["corrections"] == 0
    assert env["typist"].taps() == []


def test_confirmed_protected_field_change_still_cancels_when_protection_off(env):
    ctrl = env["ctrl"]
    ctrl.config.general.skip_password_fields = False
    probe = ChangingProbe(env["clock"], app="editor", app_ids=("editor",),
                          field=(":1.1", "/first"))
    ctrl.set_probe(probe)
    type_text(env, "ghbdtn")

    probe.values.update(password=True, field=(":1.1", "/password"))
    tap(env, kc.KEY_SPACE)

    assert ctrl.stats["corrections"] == 0
    assert env["typist"].taps() == []


def test_title_change_of_same_confirmed_field_keeps_word(env):
    ctrl = env["ctrl"]
    ctrl.config.general.skip_password_fields = False
    probe = ChangingProbe(env["clock"], app="editor", app_ids=("editor",),
                          field=(":1.1", "/field"), window="Document")
    ctrl.set_probe(probe)
    type_text(env, "g")

    probe.values["window"] = "*Document"
    type_text(env, "hbdtn ")

    assert ctrl.stats["corrections"] == 1


def test_first_word_after_click_survives_pre_click_cached_field(env):
    class CachedProbe(ChangingProbe):
        cached_at = None

        def snapshot(self):
            snap = super().snapshot()
            return snap if self.cached_at is None else snap._replace(taken_at=self.cached_at)

    ctrl = env["ctrl"]
    ctrl.config.general.skip_password_fields = False
    probe = CachedProbe(env["clock"], app="editor", app_ids=("editor",),
                        field=(":1.1", "/first"))
    ctrl.set_probe(probe)
    type_text(env, "x")
    probe.cached_at = env["clock"]()
    env["clock"].now += 0.05
    ctrl.on_click()
    type_text(env, "g")

    probe.cached_at = None
    probe.values["field"] = (":1.1", "/second")
    type_text(env, "hbdtn ")

    assert ctrl.stats["corrections"] == 1


def test_word_started_without_focus_is_never_reported_after_late_binding(env):
    ctrl = env["ctrl"]
    ctrl.config.general.skip_password_fields = False
    probe = ChangingProbe(env["clock"], app_ids=("editor",))
    ctrl.set_probe(probe)
    type_text(env, "g")
    probe.values.update(app="editor", field=(":1.1", "/field"))
    type_text(env, "hbdtn ")

    assert ctrl.stats["corrections"] == 1
    assert env["corrected"] == []


def test_two_capitals_are_corrected_without_accessibility_metadata(env):
    ctrl = env["ctrl"]
    ctrl.config.general.skip_password_fields = False
    ctrl.set_probe(ChangingProbe(env["clock"], app_ids=("editor",)))
    type_text(env, "GHbdtn ")

    assert ctrl.stats["corrections"] == 1
    assert typed_text(env).endswith("Привет ")
    assert env["corrected"] == []
