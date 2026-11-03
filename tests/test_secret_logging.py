import logging

import pytest

from tests.fakes import US, make_keymap
from tests.test_review_fixes import PasswordProbe, make_ctrl, type_word
from wayswitch.detector import LanguageModel


@pytest.mark.parametrize("dry_run,failed_plan", [(True, False), (False, False), (False, True)])
def test_manual_fix_in_password_field_is_not_logged(caplog, monkeypatch, dry_run, failed_plan):
    real = make_keymap(), {lang: LanguageModel.load(lang) for lang in ("ru", "en")}
    ctrl, _backend, typist, corrected = make_ctrl(
        real, group=US, probe=PasswordProbe(), clock=lambda: 100.0, dry_run=dry_run,
    )
    if failed_plan:
        monkeypatch.setattr("wayswitch.controller.plan_fix", lambda *_a, **_kw: None)
    with caplog.at_level(logging.DEBUG, logger="wayswitch"):
        type_word(ctrl, real[0], "ghbdtn", US)
        assert ctrl._secret_focus()
        assert ctrl.manual_word()
    if dry_run or failed_plan:
        assert not typist.taps()
    assert corrected == []
    assert "ghbdtn" not in caplog.text
    assert "привет" not in caplog.text
