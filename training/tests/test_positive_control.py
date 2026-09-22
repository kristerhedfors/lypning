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
