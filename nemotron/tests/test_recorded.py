"""The golden pin: what was reported, and what still reproduces.

WHY THIS FILE EXISTS. An adversarial audit found 33 defects in this pipeline, and
fixing them MOVES NUMBERS that have already been reported. There are two honest
ways for a reported number to change — it was wrong and is now right, or the thing
it measured changed — and exactly one dishonest way, which is for it to move while
nobody is looking. This file makes the third impossible: every run's published
pass rate is pinned here as a literal, and a fix that moves one fails the suite
until the new value is written down in the same commit.

  runs/<id>/summary.as-reported.json   what was published. NEVER rewritten.
  runs/<id>/summary.json               derived, re-derived by every fix.

THE PIN IS FOLD-ONLY, AND THAT IS DELIBERATE. It re-aggregates the recorded
`passed` flags and never re-runs an acceptance test. Until the `-E` defect is
fixed (AUDIT.md, `sandbox/dash-E-defeats-pythonhashseed`) the grader is
hash-randomized, so a pin that re-graded would be a coin flip and would fail for
reasons that have nothing to do with the change under test. Re-grading is pinned
separately, by tests/test_replay.py, once the grader is deterministic.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from pipeline import split as splitmod
from pipeline.jsonio import read_json, read_jsonl, sha256_of

NTX = Path(__file__).resolve().parents[1]
RUNS = NTX / "runs"
DATA = NTX / "data"

# Published 2026-09-11. A change here must be a deliberate line in a diff.
AS_REPORTED = {
    "baseline-nemotron35-bf16-t12k": 0.35135135135135137,
    "baseline-nemotron35-bf16": 0.28378378378378377,
    "headroom-k16": 0.3597972972972973,
    "replay-check": 0.3581081081081081,
    "stock-nothinking": 0.35135135135135137,
}
BASELINE_RUN = "baseline-nemotron35-bf16-t12k"
HOLDOUT_MANIFEST = "80b2fc522202a0ece3b84b299791761c86dd07983596a46650f9bfcb733ce588"
N_CORPUS, N_HOLDOUT, N_TRAIN = 249, 74, 175


def _fold(run_dir: Path) -> "tuple[float, int, int]":
    """pass_rate exactly as summarize_run computes it, from recorded flags only."""
    per: "collections.defaultdict[str, list]" = collections.defaultdict(list)
    harness_errors = 0
    for a in read_jsonl(run_dir / "attempts.jsonl"):
        if a.get("harness_error"):
            harness_errors += 1
            continue
        per[a["case_id"]].append(1.0 if a.get("passed") else 0.0)
    scores = [sum(v) / len(v) for _, v in sorted(per.items())]
    return (sum(scores) / len(scores) if scores else float("nan")), len(scores), harness_errors


@pytest.mark.parametrize("run_id", sorted(AS_REPORTED))
def test_the_published_pass_rate_still_folds_out_of_the_attempts(run_id):
    run_dir = RUNS / run_id
    assert (run_dir / "attempts.jsonl").exists(), (
        "%s: the evidence for a published number is missing. runs/ is committed "
        "precisely so this cannot happen." % run_id)
    rate, n_cases, _ = _fold(run_dir)
    assert rate == pytest.approx(AS_REPORTED[run_id], abs=1e-12), (
        "%s moved: published %.6f, folds to %.6f.\n"
        "If that is intentional, say so in the same commit and update AS_REPORTED "
        "with the new value. If it is not, you have changed a number by accident."
        % (run_id, AS_REPORTED[run_id], rate))
    assert n_cases == N_HOLDOUT


@pytest.mark.parametrize("run_id", sorted(AS_REPORTED))
def test_as_reported_is_never_rewritten(run_id):
    """The frozen copy must keep agreeing with the constant above it."""
    p = RUNS / run_id / "summary.as-reported.json"
    assert p.exists(), "%s: the as-reported pin is missing" % run_id
    assert read_json(p)["pass_rate"] == pytest.approx(AS_REPORTED[run_id], abs=1e-12)


def test_the_baseline_names_a_run_whose_evidence_is_present():
    b = read_json(DATA / "baseline.json")
    assert b["run_id"] == BASELINE_RUN
    assert b["pass_rate"] == pytest.approx(AS_REPORTED[BASELINE_RUN], abs=1e-12)
    assert (RUNS / b["run_id"] / "attempts.jsonl").exists()


def test_the_frozen_split_still_verifies():
    ok, problems = splitmod.verify(DATA / "corpus.jsonl")
    assert ok, "the held-out split drifted: %s" % problems[:3]


def test_the_split_is_the_one_every_number_was_measured_on():
    lock = splitmod.load_lock(DATA)
    assert lock is not None
    assert lock["manifest_sha256"] == HOLDOUT_MANIFEST
    assert lock["n_holdout"] == N_HOLDOUT and lock["n_train"] == N_TRAIN
    assert len(read_jsonl(DATA / "corpus.jsonl")) == N_CORPUS


def test_every_graded_attempt_belongs_to_the_frozen_holdout():
    """A number folded over cases outside the lock is not a number about it."""
    held = {e["id"] for e in (splitmod.load_lock(DATA) or {})["holdout"]}
    for run_id in AS_REPORTED:
        ids = {a["case_id"] for a in read_jsonl(RUNS / run_id / "attempts.jsonl")}
        assert ids <= held, "%s graded %d case(s) outside the holdout" % (
            run_id, len(ids - held))


# --- what the current code derives, as distinct from what was published -------
#
# AS_REPORTED above is history and never moves. These are what the pipeline
# produces TODAY. A fix that moves one fails here until the new value is written
# down in the same commit — which is the only difference between correcting a
# number and losing track of one.
RE_DERIVED = {
    "baseline-nemotron35-bf16-t12k": 0.35135135135135137,
    "baseline-nemotron35-bf16": 0.28378378378378377,
    "headroom-k16": 0.36019144144144144,      # +0.04pp: 3 engine-mismatch attempts
    "replay-check": 0.3585022522522523,      # +0.04pp: same 3
    "stock-nothinking": 0.35135135135135137,
    # The corrected baseline. 35.1% -> 33.8%: one of the 26 passing held-out
    # attempts (ntx-3238ce407be8) reproduced the expected bytes with
    # print(repr('"a","b"\r\n')) and never computed them. The audit predicted
    # 25/74 by hand; the behavioural discriminator found the same attempt on its
    # own, and did NOT flag ntx-ca1a768f3164, the honest textwrap rewrite that
    # the syntactic guard convicted.
    "baseline-regraded": 0.33783783783783783,
}


@pytest.mark.parametrize("run_id", sorted(RE_DERIVED))
def test_the_current_code_still_derives_the_value_it_derived_last_commit(run_id):
    from pipeline.evaluate import summarize_run
    s = summarize_run(RUNS / run_id)
    assert s["pass_rate"] == pytest.approx(RE_DERIVED[run_id], abs=1e-9), (
        "%s: re-derived %.9f, expected %.9f. If your change was meant to move "
        "this, update RE_DERIVED in the same commit." % (
            run_id, s["pass_rate"], RE_DERIVED[run_id]))


def test_an_engine_mismatch_never_counts_against_the_model():
    """Invariant 1 in the metric: an engine bug is not the model's failure."""
    from pipeline.evaluate import summarize_run
    s = summarize_run(RUNS / "headroom-k16")
    assert s["engine_mismatches"] == 3
    assert s["n_attempts"] == 1184 - 3


def test_the_engines_own_reason_is_reported_beside_the_derived_label():
    """376 refusals were recorded as wrong-output; the reason field was right."""
    from pipeline.evaluate import summarize_run
    s = summarize_run(RUNS / "headroom-k16")
    assert s["failures_by_reason"]["refused"] == 376
    assert s["failures_by_reason"]["engine-mismatch"] == 3
