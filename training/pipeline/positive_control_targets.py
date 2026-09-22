"""Fold graded conditioned draws into a private rejection-sampling SFT set.

The positive-control grader, rather than this module, executes programs.  This
module accepts only its exactly paired rows, joins them back to the raw private
completions, keeps conditioned programs with the population's required verdict,
and renders the original bare task prompt as the training input.  The result is
context distillation: the subset specification selected the target, but is not
present when the target is learned or evaluated.
"""
from __future__ import annotations

from collections import Counter
import hashlib

from .jsonio import sha256_of
from .positive_control_generate import request_order
from .training import messages, program_from_completion
from .training_types import TrainingError


def _keys(rows):
    return {(row.get("case_id"), row.get("draw"), row.get("arm")) for row in rows}


def build_targets(cases, completions, graded, *, samples, coverage_keep=4,
                  control_keep=1, run_id=""):
    """Return ``(sft_rows, report)`` from a complete paired private run.

    Coverage cases contribute only correct-native conditioned programs.  The
    fallback-control population contributes correct-control programs, preserving
    examples where refusing native execution is the right behavior.  Programs
    are de-duplicated within case before the fixed per-case retention cap.
    """
    if (type(coverage_keep) is not int or coverage_keep < 1 or
            type(control_keep) is not int or control_keep < 1):
        raise TrainingError("target retention caps must be positive integers")
    by_case = {case["case_id"]: case for case in cases}
    if len(by_case) != len(cases):
        raise TrainingError("target cases must have unique ids")
    expected = {(c["case_id"], draw, arm)
                for c, draw, arm in request_order(cases, samples)}
    if (len(completions) != len(expected) or len(graded) != len(expected) or
            _keys(completions) != expected or _keys(graded) != expected):
        raise TrainingError("targets require complete, exactly paired generation and grade rows")
    completion_by_key = {(row["case_id"], row["draw"], row["arm"]): row
                         for row in completions}
    grade_by_key = {(row["case_id"], row["draw"], row["arm"]): row
                    for row in graded}
    if len(completion_by_key) != len(completions) or len(grade_by_key) != len(graded):
        raise TrainingError("duplicate generation or grade key")

    eligible = Counter()
    rejected = Counter()
    selected = []
    for case in sorted(cases, key=lambda row: row["case_id"]):
        population = case["population"]
        want = "correct-native" if population == "coverage" else "correct-control"
        limit = coverage_keep if population == "coverage" else control_keep
        seen = set()
        for draw in range(samples):
            key = (case["case_id"], draw, "subset-spec")
            score = grade_by_key[key]
            raw = completion_by_key[key]
            if score.get("status") != want:
                rejected[str(score.get("status") or "missing-status")] += 1
                continue
            if population == "fallback-control" and score.get("native"):
                rejected["control-became-native"] += 1
                continue
            program = program_from_completion(raw.get("completion"))
            if not program:
                raise TrainingError("a passing grade has no unambiguous program: %s" % (key,))
            digest = hashlib.sha256(program.encode("utf-8")).hexdigest()
            if digest in seen:
                rejected["duplicate-program"] += 1
                continue
            seen.add(digest)
            eligible[population] += 1
            if len(seen) > limit:
                rejected["over-retention-cap"] += 1
                continue
            selected.append({
                "case_id": case["case_id"],
                "messages": messages(case) + [{
                    "role": "assistant",
                    "content": "```python\n" + program.rstrip() + "\n```",
                }],
                "population": population,
                "family": case["family"],
                "source": {"run_id": run_id, "arm": "subset-spec", "draw": draw,
                           "program_sha256": digest},
            })
    selected.sort(key=lambda row: (row["case_id"], row["source"]["draw"]))
    populations = Counter(row["population"] for row in selected)
    report = {
        "schema": 1,
        "run_id": run_id,
        "cases": len(cases),
        "samples_per_arm": samples,
        "coverage_keep": coverage_keep,
        "control_keep": control_keep,
        "rows": len(selected),
        "cases_with_targets": len({row["case_id"] for row in selected}),
        "families_with_targets": len({row["family"] for row in selected}),
        "populations": dict(sorted(populations.items())),
        "eligible_before_cap": dict(sorted(eligible.items())),
        "rejected": dict(sorted(rejected.items())),
        "case_set_sha256": sha256_of(sorted(by_case)),
        "sft_sha256": sha256_of(selected),
        "prompt_policy": "bare training prompt; conditioned subset specification absent",
        "selection_policy": {
            "coverage": "conditioned correct-native",
            "fallback-control": "conditioned correct-control with zero fully-native outcomes",
        },
    }
    return selected, report
