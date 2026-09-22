"""Grade a complete paired positive control and emit private and public reports."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path

from .jsonio import read_jsonl, sha256_of, write_json, write_jsonl
from .positive_control_generate import request_order
from .positive_control_targets import build_targets
from .training import Verifier, program_from_completion
from .training_metrics import paired_comparison, summarize
from .training_types import TrainingError


def _expected(cases, samples):
    return {(case['case_id'], draw, arm) for case, draw, arm in request_order(cases, samples)}


def grade(cases, completions, verifier, output, *, samples, workers=8, run_id='',
          lineage=None):
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

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, completions))
    rows.sort(key=lambda r: (r['arm'], r['case_id'], r['draw']))
    write_jsonl(output / 'rows.jsonl', rows)
    arms = {arm: [r for r in rows if r['arm'] == arm]
            for arm in ('bare', 'subset-spec')}
    metrics = {arm: summarize(values) for arm, values in arms.items()}
    comparison = paired_comparison(arms['bare'], arms['subset-spec'])
    native = comparison['metrics']['native']
    correct = comparison['metrics']['correct']
    decision = {
        'distillation_route': native['delta'] >= .10 and correct['delta'] >= -.02,
        'confirmatory_signal': native['delta'] >= .08 and native['ci95'][0] > .03
                              and correct['delta'] >= -.02,
        'native_delta': native['delta'], 'native_ci95': native['ci95'],
        'correct_delta': correct['delta'], 'correct_ci95': correct['ci95'],
    }
    report = {'schema': 1, 'cases': len(cases), 'samples_per_arm': samples,
              'case_set_sha256': sha256_of(sorted(by_case)), 'rows': len(rows),
              'metrics': metrics, 'comparison': comparison, 'decision': decision}
    write_json(output / 'report.json', report)
    targets, target_report = build_targets(cases, completions, rows, samples=samples,
                                           run_id=run_id, lineage=lineage)
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
        'comparison': comparison, 'decision': decision,
        'targets': {k: target_report[k] for k in
                    ('rows', 'cases_with_targets', 'families_with_targets', 'populations',
                     'eligible_before_cap', 'rejected', 'prompt_policy', 'selection_policy')},
    }
    write_json(output / 'public-report.json', public)
    return public


def grade_files(cases, completions_path, verifier, output, *, samples, workers=8, run_id='',
                lineage=None):
    return grade(cases, read_jsonl(completions_path), verifier, output,
                 samples=samples, workers=workers, run_id=run_id, lineage=lineage)
