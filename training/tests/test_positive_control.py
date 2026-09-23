from __future__ import annotations

import json
from pathlib import Path
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
    monkeypatch.setattr(pc, 'stratified_population', lambda cases, target, seed: cases)
    result = pc.plan(rows, 'Private spec', lambda msgs: [1] * (20 if 'Private spec' in msgs[0]['content'] else 10),
                     target_cases=2)
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
    monkeypatch.setattr(pc, 'stratified_population', lambda cases, target, seed: cases)
    with pytest.raises(TrainingError, match='nonempty'):
        pc.plan([], '', lambda msgs: [1])
    with pytest.raises(TrainingError, match='planning window'):
        pc.plan([], 'spec', lambda msgs: [1] * 32768, target_cases=1)


def test_confirmatory_selection_is_deterministic_balanced_and_keeps_every_family():
    cases = [dict(starter_cases()[0], case_id='%s-%d' % (family, i), family=family,
                  split='train')
             for family, count in [('a', 2), ('b', 5), ('c', 7)]
             for i in range(count)]
    selected = pc.stratified_population(cases, 9)
    again = pc.stratified_population(list(reversed(cases)), 9)
    assert [c['case_id'] for c in selected] == [c['case_id'] for c in again]
    counts = {family: sum(c['family'] == family for c in selected) for family in 'abc'}
    assert counts['a'] == 2 and sorted((counts['b'], counts['c'])) == [3, 4]


def test_public_plan_never_exports_private_bank_identifiers():
    result = pc.public_plan({'calls': 32, 'train_cases': 1, 'hub_bank_path': 'private/path',
                             'hub_bank_revision': 'a' * 40, 'bank_sha256': 'b' * 64,
                             'bank_file_sha256': 'c' * 64, 'case_set_sha256': 'd' * 64,
                             'future_private_field': 'secret'})
    assert result == {'calls': 32, 'train_cases': 1}


def load_script(name):
    import importlib.util
    from pathlib import Path
    import sys
    path = Path(__file__).resolve().parents[2] / '.github' / 'scripts' / name
    # As in CI, where `python .github/scripts/x.py` puts its own directory
    # first: the step2 scripts share `step2_shard` from there.
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
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
    monkeypatch.setattr(check, 'cases_from_env', lambda raw, environ=None: raw)
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


def test_grader_reuses_the_paid_admission_container_oracle():
    grade = load_script('step2_grade.py')
    identity = {'sha256': 'engine', 'oracle': 'controller Python', 'version': 'v'}
    admission = {'conformance': {'engine_sha256': 'engine'},
                 'references': {'python': 'pinned container Python'}}
    assert grade.admitted_identity(identity, admission) == {
        'sha256': 'engine', 'oracle': 'pinned container Python', 'version': 'v'}
    bad = {'conformance': {'engine_sha256': 'other'},
           'references': {'python': 'pinned container Python'}}
    with pytest.raises(TrainingError, match='grading engine differs'):
        grade.admitted_identity(identity, bad)


def test_grader_target_arms_default_to_the_reviewed_conditioned_source():
    grade = load_script('step2_grade.py')
    assert grade.target_arms(None) == ('subset-spec',)
    assert grade.target_arms('') == ('subset-spec',)
    assert grade.target_arms('bare') == ('bare',)
    assert grade.target_arms('bare,subset-spec') == ('bare', 'subset-spec')
    text = (Path(__file__).resolve().parents[2] / '.github' / 'workflows' /
            'step2-control-grade.yml').read_text()
    assert "STEP2_TARGET_ARMS: ${{ inputs.target_arms }}" in text
    assert "default: subset-spec" in text


def test_corpus_reuse_requires_identical_runtime_and_complete_green_evidence():
    import copy
    import hashlib
    reuse = load_script('step2_reuse_conformance.py')
    binary = b'engine'
    report = {'base_image': 'base@sha256:pin', 'engine_sha256': hashlib.sha256(binary).hexdigest(),
              'loaded': 10, 'skipped': 2, 'unbuilt': [], 'damage': [],
              'engines': {'lypning-l': {'counts': {'match': 3, 'unsupported': 5, 'total': 8, 'mismatch': 0},
                                       'mismatches': []}}}
    image = {'base_image': 'base@sha256:pin', 'source_commit': 'a' * 40}
    assert reuse.reusable(report, image, binary, image['base_image'], lambda _: True)
    assert not reuse.reusable(report, image, b'changed engine', image['base_image'], lambda _: True)
    assert not reuse.reusable(report, image, binary, 'other base', lambda _: True)
    assert not reuse.reusable(report, image, binary, image['base_image'], lambda _: False)
    for key, value in [('damage', ['changed file']), ('unbuilt', ['lypning-l']), ('skipped', 3)]:
        bad = copy.deepcopy(report)
        bad[key] = value
        assert not reuse.reusable(bad, image, binary, image['base_image'], lambda _: True)
    bad = copy.deepcopy(report)
    bad['engines']['lypning-l']['counts']['mismatch'] = 1
    assert not reuse.reusable(bad, image, binary, image['base_image'], lambda _: True)


def test_reference_workers_respect_container_memory_and_cpu_limits():
    check = load_script('step2_reference_check.py')
    assert check.worker_count(4, 16000) == 8
    assert check.worker_count(2, 7000) == 4
    assert check.worker_count(16, 4096) == 1
    assert check.worker_count(32, 64000) == 8


def test_reference_progress_requires_every_case_and_stops_on_failure():
    check = load_script('step2_reference_check.py')
    reports = []
    calls = []
    def score(case):
        calls.append(case)
        return 'failed'
    result = check.verify_cases([1, 2, 3], score, 1, reports.append)
    assert calls == [1]
    assert result['completed'] == 1
    assert not result['complete'] and result['stopped_early']
    assert reports[0]['completed'] == 0 and reports[-1] == result
    assert 'estimated_remaining_seconds' in result
    result = check.verify_cases([1, 2, 3], lambda _: 'correct-native', 2, reports.append)
    assert result['complete'] and result['completed'] == 3
    assert result['counts'] == {'correct-native': 3}


def test_reference_deadline_does_not_schedule_rest_or_admit_partial(monkeypatch):
    check = load_script('step2_reference_check.py')
    now = [0]
    monkeypatch.setattr(check.time, 'monotonic', lambda: now[0])
    def score(case):
        now[0] = 11
        return 'correct-native'
    result = check.verify_cases([1, 2, 3], score, 1, lambda _: None, deadline_s=10)
    assert result['completed'] == 1 and result['stopped_early']
    assert not result['complete']
