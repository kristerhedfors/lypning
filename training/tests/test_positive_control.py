from __future__ import annotations

import json
import pytest
from pipeline import positive_control as pc
from pipeline.curriculum import starter_cases
from pipeline.training_types import TrainingError


def test_population_is_actual_training_split_not_heldout(monkeypatch):
    cases = starter_cases()
    # Exercise selection without replacing the production schema validation.
    assigned = [dict(c, split=('train' if i % 3 == 0 else 'dev' if i % 3 == 1 else 'test'))
                for i, c in enumerate(cases)]
    monkeypatch.setattr(pc, 'split_cases', lambda rows, seed: assigned)
    monkeypatch.setattr(pc, 'validate_pilot', lambda rows: None)
    selected = pc.population(cases)
    assert selected and all(c['split'] == 'train' for c in selected)
    assert {c['case_id'] for c in selected}.isdisjoint({c['case_id'] for c in assigned if c['split'] != 'train'})
    with pytest.raises(TrainingError, match='raw bank'):
        pc.population(assigned)


def test_prompts_include_task_only_and_spec_changes_only_system():
    case = starter_cases()[0]
    bare = pc.arm_messages(case)
    spec = pc.arm_messages(case, 'Use the subset.')
    assert bare[1] == spec[1] == {'role': 'user', 'content': case['task']}
    assert spec[0]['content'] == bare[0]['content'] + '\n\nUse the subset.'
    assert bare == pc.arm_messages(case)
    assert set(bare[1]) == {'role', 'content'}


def test_costs_count_both_arms_and_all_sixteen_draws_without_private_content(monkeypatch):
    rows = starter_cases()[:2]
    monkeypatch.setattr(pc, 'population', lambda rows, seed: rows)
    result = pc.plan(rows, 'Private spec', lambda msgs: [1] * (20 if 'Private spec' in msgs[0]['content'] else 10))
    assert result['calls'] == 64
    assert result['arms']['bare']['input_tokens'] == 320
    assert result['arms']['subset-spec']['input_tokens'] == 640
    assert result['input_only_usd'] == round(960 * .99 / 1e6, 4)
    assert result['cost_scenarios_usd']['512'] == round((960 * .99 + 64 * 512 * 1.49) / 1e6, 4)
    public = json.dumps(result)
    for case in rows:
        assert case['task'] not in public
        assert case['reference'] not in public
        assert case['case_id'] not in public
    assert 'Private spec' not in public


def test_empty_spec_and_oversized_prompt_refused(monkeypatch):
    monkeypatch.setattr(pc, 'population', lambda rows, seed: starter_cases()[:1])
    with pytest.raises(TrainingError, match='nonempty'):
        pc.plan([], '', lambda msgs: [1])
    with pytest.raises(TrainingError, match='planning window'):
        pc.plan([], 'spec', lambda msgs: [1] * 32768)


def test_public_plan_never_exports_private_bank_identifiers():
    result = pc.public_plan({'calls': 32, 'train_cases': 1, 'hub_bank_path': 'private/path',
                             'hub_bank_revision': 'a' * 40, 'bank_sha256': 'b' * 64,
                             'bank_file_sha256': 'c' * 64, 'case_set_sha256': 'd' * 64,
                             'future_private_field': 'secret'})
    assert result == {'calls': 32, 'train_cases': 1}


def load_script(name):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / '.github' / 'scripts' / name
    spec = importlib.util.spec_from_file_location('positive_control_check', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('mismatches,unbuilt,damage,expected', [
    ([], [], [], 0), (['public-corpus-id'], [], [], 1),
    ([], ['lypning-l'], [], 1), ([], [], ['changed file'], 1),
])
def test_candidate_check_fails_closed_without_printing_programs(tmp_path, monkeypatch, capsys,
                                                               mismatches, unbuilt, damage, expected):
    from pathlib import Path
    from types import SimpleNamespace
    check = load_script('step2_candidate_check.py')
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'bin' / 'lypning-l').write_bytes(b'compiled-engine-fixture')
    monkeypatch.setenv('LYPNING_HOME', str(tmp_path))
    monkeypatch.setenv('CHECK_BASE_IMAGE', 'pinned-base')
    monkeypatch.setattr(check, 'Path', lambda p: tmp_path / 'candidate.json' if str(p) == '/result/candidate.json' else Path(p))
    failures = [SimpleNamespace(entry_id=i, expected_stdout='PRIVATE OUTPUT', actual_stdout='WRONG') for i in mismatches]
    arm = SimpleNamespace(match=3, unsupported=4, mismatch=len(failures), total=7,
                          failures=lambda: failures)
    report = SimpleNamespace(damage=damage, total=9, skipped=[1, 2], unbuilt=unbuilt,
                             engines={} if unbuilt else {'lypning-l': arm})
    monkeypatch.setattr(check.corpus, 'load_default', lambda: [])
    monkeypatch.setattr(check.conformance, 'run', lambda **kw: report)
    assert check.main() == expected
    assert 'PRIVATE OUTPUT' not in capsys.readouterr().out
    saved = json.loads((tmp_path / 'candidate.json').read_text())
    assert saved['loaded'] == 9
    assert saved['skipped'] == 2


def test_reference_admission_uses_container_runner_and_hides_failure_text(tmp_path, monkeypatch, capsys):
    from pipeline.training_types import Score
    check = load_script('step2_reference_check.py')
    rows = starter_cases()[:2]
    (tmp_path / 'step2-bank').mkdir()
    (tmp_path / 'step2-bank' / 'train.jsonl').write_text('\n'.join(json.dumps(c) for c in rows))
    (tmp_path / 'step2-result').mkdir()
    monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    monkeypatch.setenv('LYPNING_HOME', str(tmp_path))
    monkeypatch.setenv('CHECK_BASE_IMAGE', 'pinned-base')
    monkeypatch.setenv('CANDIDATE_IMAGE', 'sha256:' + '1' * 64)
    monkeypatch.setattr(check, 'population', lambda raw: raw)
    monkeypatch.setattr(check, 'engine_identity', lambda binary: {'oracle': 'host Python', 'sha256': 'engine'})
    monkeypatch.setattr(check.subprocess, 'check_output', lambda command, **kw: 'pinned Python\n')
    boundary = object()
    def runner(image, expected):
        assert image == 'sha256:' + '1' * 64
        assert expected == {'oracle': 'pinned Python', 'sha256': 'engine'}
        return boundary
    monkeypatch.setattr(check, 'ContainerRunner', runner)
    class Verifier:
        def __init__(self, binary, runner):
            assert runner is boundary
        def score(self, case, program):
            if case['case_id'] == rows[1]['case_id']:
                raise TrainingError('PRIVATE EXPECTED STDOUT')
            return Score(1.0, 'correct-native', len(case['tests']), len(case['tests']))
    monkeypatch.setattr(check, 'Verifier', Verifier)
    assert check.main() == 1
    assert 'PRIVATE EXPECTED STDOUT' not in capsys.readouterr().out
    result = json.loads((tmp_path / 'step2-result' / 'references.json').read_text())
    assert result['counts'] == {'correct-native': 1, 'failed': 1}
    assert result['python'] == 'pinned Python'
