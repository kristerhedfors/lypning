"""Grade a complete paired positive control and emit private and public reports."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import json
from pathlib import Path

from .jsonio import read_jsonl, sha256_of, write_json, write_jsonl
from .positive_control_generate import request_order
from .positive_control_targets import DEFAULT_ARMS, build_targets, normalise_arms
from .training import Verifier, program_from_completion
from .training_metrics import paired_comparison, split_components, summarize
from .training_types import TrainingError


def _expected(cases, samples):
    return {(case['case_id'], draw, arm) for case, draw, arm in request_order(cases, samples)}


def population_comparison(rows, population):
    """Paired bare-vs-conditioned comparison over one population, or None.

    The Step 2 decision is about coverage: a control case run natively is
    not a gain -- the target builder rejects exactly those draws as
    control-became-native -- so pooling controls into the decision deltas
    credited the conditioned arm for the behaviour it must not learn. Controls
    are compared separately as retention evidence.

    Families are linked through split groups; a control family can be the
    only bridge between two coverage families. Each row's split group is
    replaced by its component over ALL rows before slicing, so the bootstrap
    resamples the same independent units the pooled comparison did, never a
    finer (and falsely narrower) partition.
    """
    component = split_components({(r['family'], r['split_group']) for r in rows})
    sliced = {arm: [dict(r, split_group=component[r['family']]) for r in rows
                    if r['arm'] == arm and r['population'] == population]
              for arm in ('bare', 'subset-spec')}
    if not sliced['bare']:
        return None
    return dict(paired_comparison(sliced['bare'], sliced['subset-spec']),
                population=population)


def grade(cases, completions, verifier, output, *, samples, workers=8, run_id='',
          lineage=None, progress=None, target_arms=DEFAULT_ARMS, token_count=None,
          tokenizer=None):
    # The target options are only used after every completion is graded --
    # an hour of containers on the confirmatory rung -- so refuse a bad arm
    # set or an unnamed token count here, before any of that work starts.
    target_arms = normalise_arms(target_arms)
    if (token_count is None) != (tokenizer is None):
        raise TrainingError('a token count must name its tokenizer, and only then')
    output = Path(output)
    if output.exists():
        raise TrainingError('grade output exists; preserve it and choose a new directory')
    output.mkdir(parents=True)
    expected = _expected(cases, samples)
    by_case = {case['case_id']: case for case in cases}
    observed = {(row.get('case_id'), row.get('draw'), row.get('arm')) for row in completions}
    if len(observed) != len(completions) or observed != expected:
        raise TrainingError('completions do not exactly cover the paired case/draw/arm plan')

    def one(row):
        case = by_case[row['case_id']]
        program = program_from_completion(row.get('completion'))
        score = verifier.score(case, program)
        result = {
            'case_id': case['case_id'], 'draw': row['draw'], 'arm': row['arm'],
            'seed': row['seed'], 'family': case['family'],
            'split_group': case.get('split_group', case['family']),
            'population': case['population'], 'capabilities': case.get('capabilities', []),
            'correct': score.correct, 'native': score.native, 'status': score.status,
            'completion_tokens': int((row.get('usage') or {}).get('completion_tokens') or 0),
            'truncated': row.get('finish_reason') == 'length',
            'score': asdict(score),
        }
        return result

    rows = []
    total = len(completions)
    if progress:
        progress({'event': 'grade_progress', 'completed': 0, 'total': total,
                  'workers': workers})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, row) for row in completions]
        for completed, future in enumerate(as_completed(futures), 1):
            rows.append(future.result())
            if progress and (completed == total or completed % 32 == 0):
                progress({'event': 'grade_progress', 'completed': completed,
                          'total': total, 'workers': workers})
    rows.sort(key=lambda r: (r['arm'], r['case_id'], r['draw']))
    write_jsonl(output / 'rows.jsonl', rows)
    arms = {arm: [r for r in rows if r['arm'] == arm]
            for arm in ('bare', 'subset-spec')}
    metrics = {arm: summarize(values) for arm, values in arms.items()}
    comparison = population_comparison(rows, 'coverage')
    if comparison is None:
        raise TrainingError('the Step 2 decision needs coverage rows')
    control_comparison = population_comparison(rows, 'fallback-control')
    native = comparison['metrics']['native']
    correct = comparison['metrics']['correct']
    decision = {
        'distillation_route': native['delta'] >= .10 and correct['delta'] >= -.02,
        'confirmatory_signal': native['delta'] >= .08 and native['ci95'][0] > .03
                              and correct['delta'] >= -.02,
        'native_delta': native['delta'], 'native_ci95': native['ci95'],
        'correct_delta': correct['delta'], 'correct_ci95': correct['ci95'],
        'population': 'coverage',
    }
    report = {'schema': 1, 'cases': len(cases), 'samples_per_arm': samples,
              'case_set_sha256': sha256_of(sorted(by_case)), 'rows': len(rows),
              'metrics': metrics, 'comparison': comparison,
              'control_comparison': control_comparison, 'decision': decision}
    write_json(output / 'report.json', report)
    targets, target_report = build_targets(cases, completions, rows, samples=samples,
                                           run_id=run_id, lineage=lineage, arms=target_arms,
                                           token_count=token_count, tokenizer=tokenizer)
    write_jsonl(output / 'sft.jsonl', targets)
    write_json(output / 'sft-report.json', target_report)
    public = {
        'schema': 1, 'cases': len(cases), 'families': comparison['families'],
        'independent_clusters': comparison['independent_clusters'],
        'samples_per_arm': samples, 'rows': len(rows),
        'arms': {arm: {k: metrics[arm][k] for k in
                       ('correct', 'correct_native', 'case_weighted_correct',
                        'case_weighted_native', 'cases', 'draws', 'families',
                        'truncation_rate', 'mean_completion_tokens', 'statuses')}
                 for arm in metrics},
        'comparison': comparison, 'control_comparison': control_comparison,
        'decision': decision,
        'targets': {k: target_report[k] for k in
                    ('rows', 'cases_with_targets', 'families_with_targets', 'populations',
                     'eligible_before_cap', 'rejected', 'prompt_policy', 'selection_policy',
                     'arms', 'length_policy')},
    }
    write_json(output / 'public-report.json', public)
    return public


def generation_complete(completions_path):
    """Refuse a paid run whose own result says it did not finish cleanly.

    Exact case/draw/arm coverage is not enough: a completion is persisted
    BEFORE its model identity, thinking-off contract and usage are checked,
    so a run can hold every row and still have failed on one of them. The
    sibling result.json is the generator's verdict and is read, not inferred.
    """
    path = Path(completions_path).with_name('result.json')
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise TrainingError('grading needs the generation result.json beside the completions') from exc
    if (not isinstance(result, dict) or result.get('complete') is not True or
            result.get('reason') is not None or result.get('failure_types') or
            type(result.get('completed')) is not int or
            result.get('completed') != result.get('planned')):
        raise TrainingError('generation result is not complete; an incomplete paid run is never graded')
    return result


def grade_files(cases, completions_path, verifier, output, *, samples, workers=8, run_id='',
                lineage=None, progress=None, target_arms=DEFAULT_ARMS, token_count=None,
                tokenizer=None):
    result = generation_complete(completions_path)
    completions = read_jsonl(completions_path)
    if result['planned'] != len(completions):
        raise TrainingError('generation result and completions disagree on the request count')
    return grade(cases, completions, verifier, output,
                 samples=samples, workers=workers, run_id=run_id, lineage=lineage,
                 progress=progress, target_arms=target_arms, token_count=token_count,
                 tokenizer=tokenizer)
