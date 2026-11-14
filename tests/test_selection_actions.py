import pytest

from tests.fakes import RU, US, make_keymap
from wayswitch.detector import LanguageModel
from wayswitch.selection import mixed_layout
from wayswitch.textops import cycle_case


@pytest.mark.parametrize('text,expected', [
    ('привет мир', 'ПРИВЕТ МИР'), ('ПРИВЕТ МИР', 'Привет Мир'),
    ('Привет Мир', 'привет мир'), ("I'M", "I'm"), ("I'M fine", "i'm fine"),
    ("O'NEILL", "O'neill"), ('ЖУК-ПЁТР', 'Жук-Пётр'), ('e\u0301', 'E\u0301'),
    ('ß', 'SS'), ('SS', 'Ss'), ('Ss', 'ss'), ('123 😀', None), ('', None),
    ('HELLO_WORLD2TEST', 'Hello_World2Test'),
])
def test_cycle_case_unicode_boundaries(text, expected):
    assert cycle_case(text) == expected


@pytest.fixture
def models():
    return {'ru': LanguageModel('ru', ['привет', 'мир'], [], {}),
            'en': LanguageModel('en', ['hello', 'world'], [], {})}


@pytest.mark.parametrize('text,target,expected', [
    ('привет vbh', RU, 'привет мир'), ('ghbdtn vbh', RU, 'привет мир'),
    ('ghbdtn, vbh!', RU, 'привет, мир!'), ('Привет Vbh', RU, 'Привет Мир'),
    ('hello руддщ', US, 'hello hello'), ('  привет  vbh ', RU, '  привет  мир '),
    ('привет abc', RU, None), ('привet', RU, None), ('hello123', RU, None),
    ('https://test.com', US, None), ('привет 😀', RU, None), ('привет\nvbh', RU, None),
    ('привет', RU, None), ('vbh', None, None), ('vbh', 5, None),
])
def test_mixed_layout_requires_explicit_target_and_known_words(models, text, target, expected):
    assert mixed_layout(make_keymap(), text, (US, RU), models, target) == expected


def test_mixed_layout_requires_target_model():
    assert mixed_layout(make_keymap(), 'vbh', (US, RU), {}, RU) is None


@pytest.mark.parametrize('action,original,expected,target', [
    ('case', 'hello', 'HELLO', None), ('case', 'ПРИВЕТ', 'Привет', None),
    ('mixed', 'привет vbh', 'привет мир', 'ru'), ('mixed', 'руддщ hello', 'hello hello', 'en'),
])
def test_bound_actions_recheck_selection_and_use_no_clipboard(
        monkeypatch, action, original, expected, target):
    from tests.fakes import RecordingTextTypist
    from tests.test_selection import make_ctrl
    from wayswitch import a11y
    typist = RecordingTextTypist()
    current = RU if original == 'ПРИВЕТ' else US
    ctrl, backend, corrected = make_ctrl(original, typist, current=current)
    ctrl.detector.models['ru'] = ctrl.detector.models['ru'].with_extra_words(['мир'])
    reads = []
    def read(snap, valid, limit):
        assert valid() and limit == 300
        reads.append(snap)
        return a11y.TextSelection(0, len(original), original)
    monkeypatch.setattr(a11y, 'read_selection', read)
    assert ctrl.transform_selection(action, target=target)
    assert len(reads) == 2 and reads[0] == reads[1]
    assert typist.calls == [('type', expected)]
    assert corrected == [(original, expected, True)]
    if expected == 'HELLO':
        assert backend.set_calls == []


@pytest.mark.parametrize('action', ['case', 'mixed'])
@pytest.mark.parametrize('failure', ['password', 'changed', 'lost', 'unprintable'])
def test_new_actions_refuse_before_write(monkeypatch, action, failure):
    from tests.fakes import RecordingTextTypist
    from tests.test_bound_selection import selection_env
    typist = RecordingTextTypist()
    ctrl, _backend, corrected, probe, state, reads, _clipboard = selection_env(monkeypatch, typist)
    state.update(text='hello' if action == 'case' else 'ghbdtn', start=0,
                 end=5 if action == 'case' else 6)
    if failure == 'password':
        probe.password = True
    elif failure == 'unprintable':
        state.update(text='hello 😀', end=7)
    else:
        def pump():
            if reads:
                if failure == 'changed':
                    state['text'] = 'changed'
                else:
                    probe.field = (':other', '/field')
        ctrl.pump = pump
    assert not ctrl.transform_selection(action, target='ru' if action == 'mixed' else None)
    assert typist.calls == [] and corrected == []


def test_new_config_schema_backs_up_exact_previous_file(tmp_path):
    from wayswitch import config
    path = tmp_path / 'config.toml'
    previous = b'[meta]\nschema = 2\n[typing]\nsettle_ms = 30\n'
    path.write_bytes(previous)
    cfg = config.load(path)
    cfg.gesture.case_key = 'f8'
    cfg.gesture.mixed_key = 'f9'
    cfg.gesture.mixed_target = 'ru'
    config.save(cfg, path)
    assert path.with_name('config.toml.before-schema-3').read_bytes() == previous
    assert config.load(path).gesture == cfg.gesture
    assert config.load(path).typing.settle_ms == 30
    config.save(cfg, path)
    assert path.with_name('config.toml.before-schema-3').read_bytes() == previous


@pytest.mark.parametrize('field,other', [('case_key', 'mixed_key'), ('mixed_key', 'selection_key')])
def test_new_key_conflicts_share_existing_alias_validation(field, other):
    from wayswitch import config
    with pytest.raises(config.ConfigError):
        config.from_dict({'gesture': {field: 'scroll_lock', other: 'scrolllock'}})


def test_new_cli_requires_target_and_sends_additive_method(monkeypatch, capsys):
    from wayswitch import cli
    calls = []
    def call(method, target=None):
        calls.append((method, target))
        return ({'selection_supported': True},) if method == 'GetStatus' else (True,)
    monkeypatch.setattr(cli, 'call_daemon', call)
    with pytest.raises(SystemExit):
        cli.main(['mixed-layout'])
    assert cli.main(['mixed-layout', '--to', 'ru']) == 0
    assert calls == [('GetStatus', None), ('MixedLayoutSelection', 'ru')]
    calls.clear()
    assert cli.main(['change-case']) == 0
    assert calls == [('GetStatus', None), ('ChangeCaseSelection', None)]


@pytest.mark.parametrize('original,current', [('hello', RU), ('ПРИВЕТ', US), ('ß', US)])
def test_case_preserves_active_layout_and_rejects_unsupported_source(
        monkeypatch, original, current):
    from tests.fakes import RecordingTypist
    from tests.test_selection import make_ctrl
    typist = RecordingTypist()
    ctrl, backend, _ = make_ctrl(original, typist, current=current, monkeypatch=monkeypatch)
    assert not ctrl.transform_selection('case')
    assert backend.current() == current and backend.set_calls == [] and typist.events == []


@pytest.mark.parametrize('original,current', [('hello', RU), ('ПРИВЕТ', US)])
def test_text_transport_refuses_unrepresentable_active_group(monkeypatch, original, current):
    from tests.fakes import RecordingTextTypist
    from tests.test_selection import make_ctrl
    typist = RecordingTextTypist()
    ctrl, backend, _ = make_ctrl(original, typist, current=current, monkeypatch=monkeypatch)
    assert not ctrl.transform_selection('case')
    assert backend.current() == current and backend.set_calls == []
    assert typist.calls == []


@pytest.mark.parametrize('trustworthy', [True, False])
def test_same_group_honors_virtual_keyboard_and_layout_verification(monkeypatch, trustworthy):
    from tests.fakes import RecordingTextTypist
    from tests.test_selection import make_ctrl
    typist = RecordingTextTypist()
    ctrl, backend, _ = make_ctrl('hello', typist, monkeypatch=monkeypatch)
    backend.always_switch = True
    backend.verify_layout = lambda: trustworthy
    assert ctrl.transform_selection('case') is trustworthy
    assert backend.set_calls == ([US] if trustworthy else [US, US])
    assert typist.calls == ([('type', 'HELLO')] if trustworthy else [])


def test_multiple_physical_punctuation_positions_are_not_guessed(models):
    models['ru'] = models['ru'].with_extra_words(['ак', 'ар', 'акбар'])
    assert mixed_layout(make_keymap(), 'fr,fh.vbh', (US, RU), models, RU) is None


@pytest.mark.parametrize('value,target,expected', [
    ('proxy-руддщ', US, None), ('привет-vbh', RU, None),
    ('привет - vbh', RU, 'привет - мир'),
])
def test_mixed_compound_needs_separate_optional_policy(models, value, target, expected):
    assert mixed_layout(make_keymap(), value, (US, RU), models, target) == expected


def test_direct_mixed_call_requires_explicit_goal(monkeypatch):
    from tests.test_selection import make_ctrl
    ctrl, backend, _ = make_ctrl('ghbdtn', current=RU, monkeypatch=monkeypatch)
    assert not ctrl.transform_selection('mixed')
    assert backend.set_calls == []


def test_mixed_hotkey_captures_goal_before_idle(monkeypatch):
    from tests.test_selection import make_ctrl
    from wayswitch import keycodes as kc
    ctrl, backend, _ = make_ctrl('ghbdtn', current=RU, monkeypatch=monkeypatch)
    ctrl.config.gesture.mixed_key = 'f9'
    ctrl._rebuild_gesture(ctrl.config)
    pending, calls = [], []
    ctrl.schedule = pending.append
    ctrl.transform_selection = lambda action, **kw: calls.append((action, kw))
    ctrl.on_key(kc.KEY_F9, 1, 1)
    ctrl.on_key(kc.KEY_F9, 0, 1)
    ctrl.config.gesture.mixed_target = 'en'
    backend.set(US)
    assert len(pending) == 1
    pending[0]()
    assert calls == [('mixed', {'target': 'ru'})]


def test_mixed_hotkey_with_unknown_layout_keeps_no_guess(monkeypatch):
    from tests.test_selection import make_ctrl
    from wayswitch import keycodes as kc
    ctrl, backend, _ = make_ctrl('ghbdtn', monkeypatch=monkeypatch)
    ctrl.config.gesture.mixed_key = 'f9'
    ctrl._rebuild_gesture(ctrl.config)
    pending = []
    ctrl.schedule = pending.append
    backend.set(None)
    ctrl.on_key(kc.KEY_F9, 1, 1)
    ctrl.on_key(kc.KEY_F9, 0, 1)
    assert len(pending) == 1
    backend.set(RU)
    assert pending[0]() is False


def test_text_transport_refuses_unrepresentable_source(monkeypatch):
    from tests.fakes import RecordingTextTypist
    from tests.test_selection import make_ctrl
    typist = RecordingTextTypist()
    ctrl, backend, _ = make_ctrl('ß', typist, monkeypatch=monkeypatch)
    assert not ctrl.transform_selection('case')
    assert backend.set_calls == [] and typist.calls == []
