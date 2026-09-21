"""How often the checkpoint selector admits a checkpoint that is actually better.

Seed 1111 on bank v3 (2026-09-20, job 6ab01cbb) selected step 0 for SFT and
for GRPO. That was read as "training did nothing" until the selector was
simulated: `CheckpointGate.observe` demands no correctness regression on every
capability and every population -- nine constraints on ~180-draw sub-metrics
-- and then ranks on correctness before nativeness. Under that rule a
checkpoint with +10pp correct-and-native and correctness unchanged is admitted
about as often as pure noise. "Selected step 0" is a statement about the
selector.

This test runs the experiment against the REAL class with synthetic metrics in
`summarize`'s shape, seeded, and pins the blindness as a known defect:
`PLAN.md` Step 1.1 flips the two marked assertions when the selector is fixed
(null admitted <= 10%, +10pp native admitted >= 80%). Until then, anyone who
reads a step-0 selection as a result has this file to answer to.
"""
from __future__ import annotations

import random

from pipeline.training_metrics import CheckpointGate

#: Dev baseline of seed 1111, from sft/best.json: draws, correct, correct-native.
#: Capabilities stand in for families (dev has seven of each).
DEV = {
    "base64": (180, 157, 0), "collections": (180, 169, 157), "enum": (180, 174, 174),
    "heapq": (180, 162, 151), "statistics": (144, 137, 137), "textwrap": (180, 159, 159),
    "unicodedata": (180, 165, 41),
}
CONTROL = {"base64", "unicodedata"}


def macro(values):
    return sum(values) / len(values)


def metrics_of(counts):
    """A metrics dict in `summarize`'s shape from per-capability counts."""
    cov = [k for k in counts if k not in CONTROL]
    ctl = [k for k in counts if k in CONTROL]
    rate = lambda k, i: counts[k][i] / counts[k][0]
    return {
        "correct": macro([rate(k, 1) for k in counts]),
        "correct_native": macro([rate(k, 2) for k in counts]),
        "by_capability": {k: {"correct": rate(k, 1), "correct_native": rate(k, 2)} for k in counts},
        "by_population": {
            "coverage": {"correct": macro([rate(k, 1) for k in cov]),
                         "correct_native": macro([rate(k, 2) for k in cov])},
            "fallback-control": {"correct": macro([rate(k, 1) for k in ctl]),
                                 "correct_native": macro([rate(k, 2) for k in ctl])},
        },
    }


def redraw(rng, lift_correct=0.0, lift_native=0.0):
    """Re-sample every capability at its observed rate plus a uniform lift."""
    out = {}
    for k, (n, c, nn) in DEV.items():
        pc = min(1.0, c / n + lift_correct)
        pn = min(pc, nn / n + (lift_native if k not in CONTROL else 0.0))
        cc = sum(rng.random() < pc for _ in range(n))
        nc = sum(rng.random() < (pn / pc if pc else 0.0) for _ in range(cc))
        out[k] = (n, cc, nc)
    return out


def admission_rate(lift_correct, lift_native, trials=1500, seed=7):
    rng = random.Random(seed)
    baseline = metrics_of(DEV)
    admitted = 0
    for _ in range(trials):
        gate = CheckpointGate(baseline=baseline, patience=3)
        gate.observe(25, metrics_of(redraw(rng, lift_correct, lift_native)))
        admitted += gate.best_step == 25
    return admitted / trials


def test_the_selector_is_blind_to_native_gains_as_of_seed_1111():
    """Pins the defect. PLAN.md Step 1.1 inverts the two marked lines."""
    null = admission_rate(0.0, 0.0)
    native10 = admission_rate(0.0, 0.10)
    both = admission_rate(0.05, 0.10)

    assert null < 0.10, "noise must not be admitted often; this half is right already"
    # DEFECT, pinned: a +10pp native gain is admitted about as often as noise.
    assert native10 < 0.10, native10                      # Step 1.1 -> >= 0.80
    assert abs(native10 - null) < 0.05, (native10, null)  # Step 1.1 -> remove
    # The gate is not simply dead: a large gain on BOTH axes does get through.
    assert both > 0.90, both


def test_a_native_gain_cannot_win_the_key_while_correctness_ranks_first():
    """The second half of the blindness, separately: even an ELIGIBLE checkpoint
    that lifts native by 10pp loses to the incumbent unless correctness also
    rose, because the key is lexicographic on correctness."""
    baseline = metrics_of(DEV)
    # Every correct coverage draw becomes native: the largest native gain this
    # dev split can show with correctness held exactly. Three coverage
    # capabilities are already saturated (native == correct), so the macro
    # moves less than the per-draw picture suggests -- which is itself part of
    # why the key's correctness-first ordering is so hard to beat.
    better = {k: (n, c, c if k not in CONTROL else nn) for k, (n, c, nn) in DEV.items()}
    m = metrics_of(better)
    assert m["correct"] == baseline["correct"], "correctness held exactly"
    assert m["correct_native"] > baseline["correct_native"]
    gate = CheckpointGate(baseline=baseline, patience=3)
    stop = gate.observe(25, m)
    # Eligible (no regression anywhere) and strictly better on native: admitted.
    assert gate.best_step == 25 and not stop
    # But one fewer correct draw on ONE capability -- inside noise -- and it is out.
    worse = dict(better)
    worse["enum"] = (180, 173, 173)
    gate = CheckpointGate(baseline=baseline, patience=3)
    gate.observe(25, metrics_of(worse))
    assert gate.best_step == 0, "one draw of noise on one capability vetoes a +10pp native gain"
