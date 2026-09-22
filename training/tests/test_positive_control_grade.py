from __future__ import annotations

import json

from pipeline import positive_control_grade as grade
from pipeline.training_types import Score, TrainingError


def cases():
    return [
        {'case_id': 'private-case-a', 'family': 'private-family-a', 'split_group': 'ga', 'population': 'coverage',
         'capabilities': [], 'split': 'train', 'tests': [{}, {}, {}]},
        {'case_id': 'private-case-b', 'family': 'private-family-b', 'split_group': 'gb', 'population': 'coverage',
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
    result = grade.grade(rows, completions(rows), Verifier(), tmp_path / 'grade',
                         samples=2, workers=2)
    assert result['rows'] == 8 and result['families'] == 2
    assert result['comparison']['metrics']['native']['delta'] == 0
    public = (tmp_path / 'grade' / 'public-report.json').read_text()
    assert 'private-family-a' not in public and 'private-case-a' not in public
    private = json.loads((tmp_path / 'grade' / 'report.json').read_text())
    assert set(private['metrics']['bare']['by_family']) == {'private-family-a', 'private-family-b'}

    with __import__('pytest').raises(TrainingError, match='exactly cover'):
        grade.grade(rows, completions(rows)[:-1], Verifier(), tmp_path / 'bad',
                    samples=2, workers=2)
