from __future__ import annotations

import json

import pytest

from pipeline import positive_control_grade as grade
from pipeline.training_types import Score, TrainingError


def cases():
    return [
        {'case_id': 'private-case-a', 'family': 'private-family-a', 'task': 'rewrite a',
         'split_group': 'ga', 'population': 'coverage',
         'capabilities': [], 'split': 'train', 'tests': [{}, {}, {}]},
        {'case_id': 'private-case-b', 'family': 'private-family-b', 'task': 'rewrite b',
         'split_group': 'gb', 'population': 'coverage',
         'capabilities': [], 'split': 'train', 'tests': [{}, {}, {}]},
    ]


def completions(rows, samples=2):
    out = []
    for case in rows:
        for draw in range(samples):
            for arm in ('bare', 'subset-spec'):
                out.append({'case_id': case['case_id'], 'draw': draw, 'arm': arm,
                            'seed': 1111 + draw, 'completion': '```python\nprint(1)\n```',
                            'finish_reason': 'stop',
                            'usage': {'completion_tokens': 8}})
    return out


class Verifier:
    def score(self, case, program):
        assert program == 'print(1)'
        return Score(1, 'correct-native', 3, 3)


def test_grade_requires_complete_pairs_and_emits_safe_public_report(tmp_path, monkeypatch):
    # The production order accepts up to sixteen; keep this unit fixture small.
    monkeypatch.setattr(grade, 'request_order', lambda rows, samples:
        ((case, draw, arm) for case in rows for draw in range(samples)
         for arm in ('bare', 'subset-spec')))
    rows = cases()
    progress = []
    result = grade.grade(rows, completions(rows), Verifier(), tmp_path / 'grade',
                         samples=2, workers=2, progress=progress.append)
    assert result['rows'] == 8 and result['families'] == 2
    assert result['comparison']['metrics']['native']['delta'] == 0
    public = (tmp_path / 'grade' / 'public-report.json').read_text()
    assert 'private-family-a' not in public and 'private-case-a' not in public
    assert result['targets']['rows'] == 2
    assert progress == [
        {'event': 'grade_progress', 'completed': 0, 'total': 8, 'workers': 2},
        {'event': 'grade_progress', 'completed': 8, 'total': 8, 'workers': 2},
    ]
    assert (tmp_path / 'grade' / 'sft.jsonl').is_file()
    private = json.loads((tmp_path / 'grade' / 'report.json').read_text())
    assert set(private['metrics']['bare']['by_family']) == {'private-family-a', 'private-family-b'}

    with __import__('pytest').raises(TrainingError, match='exactly cover'):
        grade.grade(rows, completions(rows)[:-1], Verifier(), tmp_path / 'bad',
                    samples=2, workers=2)


def _order(monkeypatch):
    monkeypatch.setattr(grade, 'request_order', lambda rows, samples:
        ((case, draw, arm) for case in rows for draw in range(samples)
         for arm in ('bare', 'subset-spec')))


def mixed_cases():
    # Two coverage families in separate split groups, and one control family
    # whose two cases sit in both groups: the only bridge between them.
    return [
        {'case_id': 'cov-a', 'family': 'cov-fam-a', 'task': 'a', 'split_group': 'g1',
         'population': 'coverage', 'split': 'train', 'tests': [{}, {}]},
        {'case_id': 'cov-b', 'family': 'cov-fam-b', 'task': 'b', 'split_group': 'g2',
         'population': 'coverage', 'split': 'train', 'tests': [{}, {}]},
        {'case_id': 'ctl-1', 'family': 'ctl-fam', 'task': 'c', 'split_group': 'g1',
         'population': 'fallback-control', 'split': 'train', 'tests': [{}, {}]},
        {'case_id': 'ctl-2', 'family': 'ctl-fam', 'task': 'd', 'split_group': 'g2',
         'population': 'fallback-control', 'split': 'train', 'tests': [{}, {}]},
    ]


def arm_completions(rows, samples=2):
    return [{'case_id': case['case_id'], 'draw': draw, 'arm': arm, 'seed': 1111 + draw,
             'completion': '```python\nprint(%r)\n```' % arm, 'finish_reason': 'stop',
             'usage': {'completion_tokens': 8}}
            for case in rows for draw in range(samples) for arm in ('bare', 'subset-spec')]


class ControlFlipVerifier:
    """Coverage is identical across arms; the conditioned arm runs every
    control case natively -- the behaviour the targets must never teach."""

    def score(self, case, program):
        if case['population'] == 'coverage':
            return Score(1, 'correct-native', 2, 2)
        if 'subset-spec' in program:
            return Score(1, 'correct-native', 2, 2)
        return Score(1, 'correct-control', 0, 2)


def test_step2_decision_is_computed_on_coverage_rows_only(tmp_path, monkeypatch):
    _order(monkeypatch)
    rows = mixed_cases()
    result = grade.grade(rows, arm_completions(rows), ControlFlipVerifier(),
                         tmp_path / 'grade', samples=2, workers=2)
    decision = result['decision']
    # Pooled, the control flip would read as a +1/3 native gain and earn the
    # distillation route; on coverage there is no gain at all.
    assert decision['population'] == 'coverage'
    assert decision['native_delta'] == 0 and decision['correct_delta'] == 0
    assert decision['distillation_route'] is False
    assert result['comparison']['population'] == 'coverage'
    assert result['comparison']['families'] == 2
    controls = result['control_comparison']
    assert controls['population'] == 'fallback-control' and controls['families'] == 1
    assert controls['metrics']['native']['delta'] == 1
    # The control family bridges the two coverage families' groups; slicing
    # must not split them into two falsely independent bootstrap units.
    assert result['comparison']['independent_clusters'] == 1
    private = json.loads((tmp_path / 'grade' / 'report.json').read_text())
    assert private['decision'] == decision
    assert private['control_comparison']['metrics'] == controls['metrics']
    # The written rows keep their real split groups.
    written = [json.loads(line) for line in
               (tmp_path / 'grade' / 'rows.jsonl').read_text().splitlines()]
    assert {r['split_group'] for r in written} == {'g1', 'g2'}
    assert result['targets']['arms'] == ['subset-spec']


def _generation(tmp_path, rows, **result):
    paid = tmp_path / 'paid'
    paid.mkdir()
    completions = arm_completions(rows)
    (paid / 'completions.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in completions))
    if result:
        (paid / 'result.json').write_text(json.dumps(result))
    return paid / 'completions.jsonl', len(completions)


def test_grading_files_needs_a_complete_generation_result(tmp_path, monkeypatch):
    _order(monkeypatch)
    rows = mixed_cases()
    path, planned = _generation(tmp_path, rows, complete=True, completed=16, planned=16,
                                reason=None, failure_types=[])
    public = grade.grade_files(rows, path, ControlFlipVerifier(), tmp_path / 'grade', samples=2)
    assert public['rows'] == planned == 16


@pytest.mark.parametrize('result', [
    None,
    # Every row present, but a later usage/identity check failed the run.
    {'complete': False, 'completed': 15, 'planned': 16,
     'reason': 'provider/usage failure; ambiguous reservations retained',
     'failure_types': ['provider-usage-contract']},
    {'complete': True, 'completed': 15, 'planned': 16, 'reason': None, 'failure_types': []},
    {'complete': True, 'completed': 16, 'planned': 16, 'reason': 'spend limit',
     'failure_types': []},
    {'complete': True, 'completed': 12, 'planned': 12, 'reason': None, 'failure_types': []},
])
def test_grading_refuses_an_incomplete_or_inconsistent_generation(tmp_path, monkeypatch, result):
    _order(monkeypatch)
    rows = mixed_cases()
    path, _ = _generation(tmp_path, rows, **(result or {}))
    with pytest.raises(TrainingError, match='result'):
        grade.grade_files(rows, path, ControlFlipVerifier(), tmp_path / 'grade', samples=2)
    assert not (tmp_path / 'grade').exists()


class NeverScored:
    def score(self, case, program):
        raise AssertionError('grading must not start on invalid target options')


@pytest.mark.parametrize('options,match', [
    ({'target_arms': ('tuned',)}, 'target arms'),
    ({'target_arms': 'bare'}, 'target arms'),
    ({'target_arms': ('bare', 'bare')}, 'target arms'),
    ({'token_count': len}, 'name its tokenizer'),
    ({'tokenizer': 'repo@' + 'a' * 40}, 'name its tokenizer'),
])
def test_invalid_target_options_are_refused_before_any_grading(tmp_path, monkeypatch,
                                                               options, match):
    # build_targets would refuse these too, but only after every completion
    # had been graded; the grader must refuse them before the first score.
    _order(monkeypatch)
    rows = mixed_cases()
    with pytest.raises(TrainingError, match=match):
        grade.grade(rows, arm_completions(rows), NeverScored(), tmp_path / 'grade',
                    samples=2, workers=2, **options)
    assert not (tmp_path / 'grade').exists()
