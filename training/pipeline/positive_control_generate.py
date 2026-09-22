"""Bounded hosted draws for the positive control. Executes no generated code.

A fresh output directory is required. Each outbound request reserves the price
of an entire provider context plus the output allowance BEFORE it is sent.
Successful, well-formed usage releases the unused reservation; failed or
ambiguous requests retain it. Transport retries are forbidden. The caller must
supply the reviewed population, provider, runtime admission and dollar ceiling.
This module does not launch jobs, upload data, or authorize its own budget.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from decimal import Decimal
import hashlib
import re
from pathlib import Path
import threading
import time

from .jsonio import append_jsonl, sha256_of, write_json
from .positive_control import MODEL, MAX_TOKENS, PRICE_IN, PRICE_OUT, SAMPLES, arm_messages
from .training_types import TrainingError

# Reserve the full published context for input, even though planned prompts
# are much shorter. One request is in flight per worker; unused cost is released
# only after a response with valid usage. This also bounds ambiguous failures.
# https://inference-docs.cerebras.ai/models/overview (2026-09-22): paid 128k.
CONTEXT_TOKENS = 131072
PROVIDER = 'https://api.cerebras.ai/v1'


class SpendLimit(TrainingError):
    pass


class Budget:
    def __init__(self, ceiling, ledger):
        self.ceiling = Decimal(str(ceiling))
        if not self.ceiling.is_finite() or self.ceiling <= 0:
            raise TrainingError('a positive finite operator dollar ceiling is required')
        self.ledger = ledger
        self.charged = Decimal(0)
        self.lock = threading.Lock()
        self.pending = set()
        self.seen = set()
        self.reservation = (Decimal(CONTEXT_TOKENS) * Decimal(str(PRICE_IN)) +
                            Decimal(MAX_TOKENS) * Decimal(str(PRICE_OUT))) / Decimal(1000000)

    def reserve(self, key):
        with self.lock:
            if key in self.seen:
                raise TrainingError('request has already been reserved; no implicit retries')
            if self.charged + self.reservation > self.ceiling:
                raise SpendLimit('remaining ceiling cannot cover another request reservation')
            # Durable before network access. An interrupted call is never
            # silently retried by a resume path (there is no implicit resume).
            self.ledger(dict(event='reserved', request=key, usd=str(self.reservation)))
            self.charged += self.reservation
            self.pending.add(key)
            self.seen.add(key)

    def settle(self, key, usage):
        prompt = usage.get('prompt_tokens')
        output = usage.get('completion_tokens')
        if (type(prompt) is not int or type(output) is not int or
                not 0 < prompt <= CONTEXT_TOKENS or not 0 <= output <= MAX_TOKENS):
            raise TrainingError('missing or out-of-contract provider usage; reservation retained')
        cost = (Decimal(prompt) * Decimal(str(PRICE_IN)) +
                Decimal(output) * Decimal(str(PRICE_OUT))) / Decimal(1000000)
        with self.lock:
            if key not in self.pending:
                raise TrainingError('request has no outstanding reservation')
            self.ledger(dict(event='settled', request=key, usd=str(cost),
                             prompt_tokens=prompt, completion_tokens=output))
            self.charged -= self.reservation - cost
            self.pending.remove(key)
        return float(cost)


def request_order(cases, samples=SAMPLES):
    """Paired, interleaved arms; order is fixed before any model outcome exists."""
    if samples != SAMPLES or not cases:
        raise TrainingError('positive control requires cases and exactly sixteen draws')
    ids = [c['case_id'] for c in cases]
    if len(ids) != len(set(ids)) or any(c.get('split') != 'train' for c in cases):
        raise TrainingError('positive control requires unique train-only cases')
    ordered = sorted(cases, key=lambda c: sha256_of([1111, c['case_id']]))
    for draw in range(samples):
        for case in ordered:
            arms = ('bare', 'subset-spec')
            if int(hashlib.sha256((case['case_id'] + ':' + str(draw)).encode()).hexdigest(), 16) % 2:
                arms = tuple(reversed(arms))
            for arm in arms:
                yield case, draw, arm


def validate_admission(admission, cases):
    """Read the private proof emitted by step2_reference_check, never a flag."""
    try:
        candidate = admission['conformance']
        references = admission['references']
        arm = candidate['engines']['lypning-l']['counts']
        fingerprints = admission['case_fingerprints']
        hashes = (candidate['engine_sha256'], references['engine_sha256'])
        if (not re.fullmatch('[0-9a-f]{40}', admission['source_commit']) or
                not re.fullmatch('sha256:[0-9a-f]{64}', admission['candidate_image']) or
                hashes[0] != hashes[1] or not re.fullmatch('[0-9a-f]{64}', hashes[0]) or
                candidate['base_image'] != references['base_image'] or
                '@sha256:' not in candidate['base_image'] or
                candidate['unbuilt'] or candidate['damage'] or arm['mismatch'] != 0 or
                arm['total'] <= 0 or candidate['loaded'] <= 0 or
                references['counts'].get('failed', 0) != 0 or
                references['cases'] != len(fingerprints) or
                sum(references['counts'].values()) != references['cases'] or
                not set(references['counts']) <= {'correct-native', 'correct-control'} or
                any(fingerprints.get(c['case_id']) != sha256_of(c) for c in cases)):
            raise ValueError('incomplete, failed or mismatched admission')
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise TrainingError('verified runtime admission does not cover this population') from exc


def generate(cases, spec, backend, output, *, ceiling_usd, admission, workers=4,
             max_seconds=3000, requests_per_minute=60):
    """Generate a reviewed shard; admission is supplied by the orchestrator.

    Completion completeness is separate from spend: hitting the cap or any
    provider failure writes a partial result, never a successful comparison.
    Four in-flight requests at most; all have reservations before dispatch.
    Raw completions and the ledger are PRIVATE evidence, not Actions artifacts.
    """
    if (backend.base_url.rstrip('/') != PROVIDER or backend.model != MODEL or
            backend.max_retries != 0 or not 0 < backend.timeout_s <= 120):
        raise TrainingError('fixed Cerebras model, zero transport retries and timeout <=120s required')
    if type(workers) is not int or not 1 <= workers <= 4:
        raise TrainingError('workers must be in 1..4')
    if not 0 < max_seconds <= 18000 or not 0 < requests_per_minute <= 120:
        raise TrainingError('bounded wall time and request rate required')
    if not spec.strip():
        raise TrainingError('subset spec is required')
    validate_admission(admission, cases)
    # Materialize before creating output or making a call, so no late data
    # validation error can turn half a paid run into a malformed experiment.
    requests = list(request_order(cases))
    output = Path(output)
    if output.exists():
        raise TrainingError('output exists; preserve partial work and choose a new directory')
    output.mkdir(parents=True)
    budget = Budget(ceiling_usd, lambda row: append_jsonl(output / 'spend.jsonl', row))
    write_json(output / 'manifest.json', {
        'schema': 1, 'provider': backend.identity(), 'admission': admission,
        'case_set_sha256': sha256_of(cases), 'spec_sha256': hashlib.sha256(spec.encode()).hexdigest(),
        'requests': len(requests), 'samples': SAMPLES, 'ceiling_usd': str(budget.ceiling),
        'dispatch_seconds': max_seconds, 'inflight_timeout_seconds': backend.timeout_s,
        'max_retries': 0, 'workers': workers,
        'sampling': {'temperature': .7, 'top_p': .8, 'max_tokens': MAX_TOKENS,
                     'reasoning_effort': 'none', 'seed': '1111 + draw'},
    })
    started = time.monotonic()
    completed = 0
    failure = None
    next_request_at = started

    def one(case, draw, arm):
        key = '%s/%d/%s' % (case['case_id'], draw, arm)
        # Reserve happens in the controller before submission to this worker.
        done = backend.complete(arm_messages(case, spec if arm == 'subset-spec' else None),
                                temperature=.7, top_p=.8, max_tokens=MAX_TOKENS,
                                seed=1111 + draw, reasoning_effort='none')
        usage = done.raw.get('usage') or {}
        row = dict(case_id=case['case_id'], draw=draw, arm=arm, seed=1111 + draw,
                   completion=done.text, finish_reason=done.finish_reason,
                   usage=usage, model=done.raw.get('model'))
        return key, row, done

    futures = {}
    iterator = iter(requests)
    exhausted = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while futures or not exhausted:
            while not exhausted and failure is None and len(futures) < workers:
                if time.monotonic() - started >= max_seconds:
                    failure = 'wall-time limit'
                    break
                delay = next_request_at - time.monotonic()
                if delay > 0:
                    # Sleep at most one second, then service completed requests.
                    if futures:
                        break
                    time.sleep(min(delay, 1))
                    continue
                try:
                    case, draw, arm = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                key = '%s/%d/%s' % (case['case_id'], draw, arm)
                try:
                    budget.reserve(key)
                except SpendLimit:
                    failure = 'spend limit'
                    break
                futures[pool.submit(one, case, draw, arm)] = key
                next_request_at = time.monotonic() + 60.0 / requests_per_minute
            if failure:
                exhausted = True
            if not futures:
                continue
            ready, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
            for future in ready:
                key = futures.pop(future)
                try:
                    _, row, done = future.result()
                    # Save what was paid for before inspecting model/usage.
                    append_jsonl(output / 'completions.jsonl', row)
                    if row['model'] != MODEL:
                        raise TrainingError('provider model identity differs')
                    message = (done.raw.get('choices') or [{}])[0].get('message') or {}
                    if done.reasoning or message.get('reasoning') or message.get('reasoning_content'):
                        raise TrainingError('thinking-off contract violated')
                    budget.settle(key, row['usage'])
                    completed += 1
                except Exception as exc:
                    # Provider exception strings may echo a prompt or key.
                    append_jsonl(output / 'errors.jsonl', {'request': key, 'type': type(exc).__name__})
                    failure = 'provider/usage failure; ambiguous reservations retained'
    result = {'complete': completed == len(requests) and failure is None,
              'completed': completed, 'planned': len(requests), 'reason': failure,
              'charged_or_reserved_usd': str(budget.charged), 'ceiling_usd': str(budget.ceiling)}
    write_json(output / 'result.json', result)
    return result
