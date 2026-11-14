import json

import pytest

from experiments.model_store import compare


@pytest.mark.parametrize('diverge,golden_failed', [(False, False), (True, False), (False, True)])
def test_comparison_exit_status_and_saved_differences(monkeypatch, tmp_path,
                                                     diverge, golden_failed):
    def child(command, **_):
        variant = command[command.index('--variant')+1]
        kind = command[command.index('--child')+1]
        data = {'load_ns': 1, 'ready_ns': 2}
        if kind == 'decisions':
            value = 'changed' if diverge and variant == 'native' else 'keep'
            data.update({'decisions': [[value]], 'decide_ns': {}, 'batch_cpu_ns': {},
                         'work_items': 1})
        return json.dumps(data)
    output = tmp_path / 'report.json'
    monkeypatch.setattr(compare.platform, 'platform', lambda: 'test-platform')
    monkeypatch.setattr(compare.subprocess, 'check_output', child)
    monkeypatch.setattr(compare, 'golden', lambda *_: {
        'differences': [], 'python_statuses': {'pass': 1, 'fail': 0, 'error': 0},
        'native_statuses': {'pass': int(not golden_failed),
                            'fail': int(golden_failed), 'error': 0}})
    monkeypatch.setattr(compare.sys, 'argv', ['compare', '--packs', str(tmp_path / 'packs'),
                                             '--output', str(output),
                                             '--golden-root', str(tmp_path)])
    assert compare.main() == int(diverge or golden_failed)
    report = json.loads(output.read_text())
    assert len(report['differences']['native']) == int(diverge)
