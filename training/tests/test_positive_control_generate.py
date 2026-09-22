from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest
from pipeline import positive_control_generate as gen
from pipeline.backends import ChatBackend, BackendError
from pipeline.training_types import TrainingError


def admission(cases):
    return {
        'source_commit': 'a' * 40, 'candidate_image': 'sha256:' + 'b' * 64,
        'case_fingerprints': {c['case_id']: gen.sha256_of(c) for c in cases},
        'conformance': {'engine_sha256': 'c' * 64, 'base_image': 'python@sha256:' + 'd' * 64,
                        'unbuilt': [], 'damage': [], 'loaded': 10,
                        'engines': {'lypning-l': {'counts': {'mismatch': 0, 'total': 10}}}},
        'references': {'engine_sha256': 'c' * 64, 'base_image': 'python@sha256:' + 'd' * 64,
                       'cases': len(cases), 'counts': {'correct-native': len(cases)}},
    }


def test_concurrent_reservations_cannot_overspend():
    rows = []
    budget = gen.Budget('.30', rows.append)
    def reserve(i):
        try:
            budget.reserve(str(i))
            return True
        except gen.SpendLimit:
            return False
    with ThreadPoolExecutor(max_workers=16) as pool:
        assert sum(pool.map(reserve, range(100))) == 2
    assert budget.charged <= budget.ceiling
    assert len(rows) == 2


def test_failed_usage_and_duplicate_settlement_cannot_release_money():
    budget = gen.Budget(1, lambda row: None)
    budget.reserve('request')
    reserved = budget.charged
    for usage in ({}, {'prompt_tokens': 10, 'completion_tokens': -1},
                  {'prompt_tokens': True, 'completion_tokens': 1},
                  {'prompt_tokens': 999999999, 'completion_tokens': 1}):
        with pytest.raises(TrainingError):
            budget.settle('request', usage)
        assert budget.charged == reserved
    budget.settle('request', {'prompt_tokens': 100, 'completion_tokens': 50})
    assert budget.charged == Decimal('0.0001735')
    with pytest.raises(TrainingError):
        budget.settle('request', {'prompt_tokens': 1, 'completion_tokens': 1})
    with pytest.raises(TrainingError):
        budget.reserve('request')
    assert budget.charged == Decimal('0.0001735')


def test_request_order_is_paired_train_only_and_deterministic():
    cases = [{'case_id': 'a', 'split': 'train'}, {'case_id': 'b', 'split': 'train'}]
    first = list(gen.request_order(cases))
    assert first == list(gen.request_order(list(reversed(cases))))
    assert len(first) == 64
    assert len({(c['case_id'], d, a) for c, d, a in first}) == 64
    for i in range(0, len(first), 2):
        assert first[i][:2] == first[i+1][:2]
        assert {first[i][2], first[i+1][2]} == {'bare', 'subset-spec'}
    with pytest.raises(TrainingError):
        list(gen.request_order([{'case_id': 'a', 'split': 'test'}]))


def test_transport_failure_is_paid_once_retained_and_partial(tmp_path):
    calls = []
    backend = ChatBackend(gen.PROVIDER, gen.MODEL, max_retries=0, timeout_s=120)
    def fail(messages, **kwargs):
        calls.append(kwargs)
        assert (tmp_path / 'out' / 'spend.jsonl').is_file()
        raise BackendError('PRIVATE PROMPT AND TOKEN')
    backend.complete = fail
    cases = [{'case_id': 'a', 'split': 'train', 'task': 'Print input'}]
    result = gen.generate(cases,
                          'spec', backend, tmp_path / 'out', ceiling_usd=1,
                          admission=admission(cases), workers=1)
    assert len(calls) == 1
    assert calls[0]['reasoning_effort'] == 'none'
    assert 'enable_thinking' not in calls[0]  # Cerebras uses reasoning_effort.
    assert result['complete'] is False and result['completed'] == 0
    assert Decimal(result['charged_or_reserved_usd']) > 0
    assert 'PRIVATE PROMPT' not in (tmp_path / 'out' / 'errors.jsonl').read_text()
    with pytest.raises(TrainingError, match='output exists'):
        gen.generate(cases, 'spec', backend, tmp_path / 'out',
                     ceiling_usd=1, admission=admission(cases))


def test_insufficient_reservation_makes_no_network_call(tmp_path):
    backend = ChatBackend(gen.PROVIDER, gen.MODEL, max_retries=0, timeout_s=120)
    backend.complete = lambda *a, **kw: pytest.fail('must not call provider')
    cases = [{'case_id': 'a', 'split': 'train'}]
    result = gen.generate(cases, 'spec', backend, tmp_path / 'out',
                          ceiling_usd=.01, admission=admission(cases))
    assert result['reason'] == 'spend limit'
    assert result['complete'] is False
    assert result['charged_or_reserved_usd'] == '0'


@pytest.mark.parametrize('ceiling', [0, -1, 'NaN', 'Infinity'])
def test_bad_ceiling_refused(ceiling):
    with pytest.raises(TrainingError):
        gen.Budget(ceiling, lambda row: None)


def test_no_retry_and_runtime_admission_required(tmp_path):
    with pytest.raises(TrainingError, match='zero transport retries'):
        gen.generate([], 'spec', ChatBackend(gen.PROVIDER, gen.MODEL), tmp_path / 'out',
                     ceiling_usd=1, admission={'ready': True})
    with pytest.raises(TrainingError, match='runtime admission'):
        gen.generate([], 'spec', ChatBackend(gen.PROVIDER, gen.MODEL, max_retries=0, timeout_s=120),
                     tmp_path / 'out', ceiling_usd=1, admission={})


def test_runtime_proof_is_bound_to_cases_and_rejects_red_conformance():
    import copy
    cases = [{'case_id': 'a', 'split': 'train', 'task': 'original'}]
    proof = admission(cases)
    gen.validate_admission(proof, cases)
    with pytest.raises(TrainingError, match='runtime admission'):
        gen.validate_admission(proof, [dict(cases[0], task='edited')])
    broken = copy.deepcopy(proof)
    broken['conformance']['engines']['lypning-l']['counts']['mismatch'] = 1
    with pytest.raises(TrainingError, match='runtime admission'):
        gen.validate_admission(broken, cases)
    with pytest.raises(TrainingError, match='runtime admission'):
        gen.validate_admission({'ready': True}, cases)


def test_complete_interleaved_run_records_every_draw_without_executing_it(tmp_path, monkeypatch):
    ticks = iter(i * .6 for i in range(10000))
    monkeypatch.setattr(gen, 'time', SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None))
    backend = ChatBackend(gen.PROVIDER, gen.MODEL, max_retries=0, timeout_s=120)
    def complete(messages, **kwargs):
        return SimpleNamespace(text='raise RuntimeError("never execute here")', finish_reason='stop',
                               reasoning=None, raw={'model': gen.MODEL,
                               'usage': {'prompt_tokens': 100, 'completion_tokens': 50},
                               'choices': [{'message': {'content': 'answer'}}]})
    backend.complete = complete
    cases = [{'case_id': 'a', 'split': 'train', 'task': 'task'}]
    result = gen.generate(cases, 'spec', backend, tmp_path / 'out', ceiling_usd=1,
                          admission=admission(cases), workers=2)
    assert result['complete'] and result['completed'] == result['planned'] == 32
    assert Decimal(result['charged_or_reserved_usd']) == Decimal('0.0055520')
    rows = [json.loads(line) for line in (tmp_path / 'out' / 'completions.jsonl').read_text().splitlines()]
    assert len({(r['case_id'], r['draw'], r['arm']) for r in rows}) == 32
    assert all(r['seed'] == 1111 + r['draw'] for r in rows)
