"""How often the checkpoint selector admits a checkpoint that is actually better.

Seed 1111 on bank v3 (2026-09-20, job 6ab01cbb) selected step 0 for SFT and
for GRPO. That was read as "training did nothing" until the selector was
simulated: the old `CheckpointGate.observe` demanded no correctness regression
on every capability and every population -- nine constraints on ~180-draw
sub-metrics -- and then ranked on correctness before nativeness. Under that
rule a checkpoint with +10pp correct-and-native and correctness unchanged was
admitted about as often as pure noise. "Selected step 0" was a statement about
the selector.

This test runs the experiment against the REAL class with synthetic metrics in
`summarize`'s shape, seeded. `PLAN.md` Step 1.1 required the two rates to
swap places, and they have: noise is still rejected, and a real native gain is
now admitted. Anyone proposing to change the selection rule has to keep both
halves, which is the whole point of measuring them together.

The "+10pp" arm lifts the native *parameter* by 10pp; the realised macro gain
is smaller (about +1.8pp), because `redraw` caps native at correct and three
of the five coverage capabilities are already saturated. That is why the
selector needs a noise-scaled margin rather than a 3pp one: the effects this
dev split can show are of the same order as its own standard error.
"""
from __future__ import annotations

import random

from pipeline.training_metrics import CheckpointGate, macro_standard_error

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
    rate = lambda k, i: counts[k][i] / counts[k][0]
    def over(keys):
        return {"correct": macro([rate(k, 1) for k in keys]),
                "correct_native": macro([rate(k, 2) for k in keys]),
                "draws": sum(counts[k][0] for k in keys)}
    per_key = {k: over([k]) for k in counts}
    return dict(over(list(counts)), by_family=per_key, by_capability=per_key,
                by_population={"coverage": over([k for k in counts if k not in CONTROL]),
                               "fallback-control": over([k for k in counts if k in CONTROL])})


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


def admission_rate(lift_correct, lift_native, trials=4000, seed=7):
    rng = random.Random(seed)
    baseline = metrics_of(DEV)
    admitted = 0
    for _ in range(trials):
        gate = CheckpointGate(baseline=baseline)
        gate.observe(25, metrics_of(redraw(rng, lift_correct, lift_native)))
        admitted += gate.best_step == 25
    return admitted / trials


def test_the_selector_admits_the_effect_it_is_training_for_and_not_noise():
    """`PLAN.md` Step 1.1's acceptance rule, both halves at once.

    Measured 2026-09-21 at 4,000 trials, seed 7: null 9.17%, +10pp native
    84.85%, both 100%. The null rate is a *per-observation* rate; a stage that
    evaluates at three steps has three chances to draw it."""
    null = admission_rate(0.0, 0.0)
    native10 = admission_rate(0.0, 0.10)
    both = admission_rate(0.05, 0.10)

    assert null <= 0.10, null
    assert native10 >= 0.80, native10
    assert both > 0.90, both


def test_the_margin_is_the_ten_percent_rule_and_not_a_tuned_constant():
    """`null <= 0.10` above is not an accident of the seed: the margin is the
    90th percentile of the macro's own sampling distribution, so a checkpoint
    drawn from the null clears it about a tenth of the time by construction."""
    gate = CheckpointGate(baseline=metrics_of(DEV))
    assert gate.margin == gate.z * macro_standard_error(metrics_of(DEV))
    # Sized to this dev split: near one point, not the three the confirmatory
    # eval-2 endpoint asks for, which no checkpoint of this split could show.
    assert 0.005 < gate.margin < 0.015, gate.margin


def test_one_draw_of_noise_on_one_capability_no_longer_vetoes_a_native_gain():
    """The defect this file was written for, inverted.

    Every correct coverage draw becomes native: the largest native gain this
    dev split can show with correctness held exactly. Under the old
    lexicographic key it was admitted only while nothing anywhere regressed,
    so one fewer correct draw on one capability -- inside noise -- rejected
    it. Capabilities are now reported, not floors."""
    baseline = metrics_of(DEV)
    better = {k: (n, c, c if k not in CONTROL else nn) for k, (n, c, nn) in DEV.items()}
    m = metrics_of(better)
    assert m["correct"] == baseline["correct"], "correctness held exactly"
    assert m["correct_native"] > baseline["correct_native"]

    gate = CheckpointGate(baseline=baseline)
    gate.observe(25, m)
    assert gate.best_step == 25

    worse = dict(better)
    worse["enum"] = (180, 173, 173)
    gate = CheckpointGate(baseline=baseline)
    gate.observe(25, metrics_of(worse))
    assert gate.best_step == 25, "one draw on one capability is not a regression"


def test_a_control_collapse_still_vetoes_an_aggregate_gain():
    """Retention survives the rewrite. SFT at step 50 of seed 1111 held its
    coverage correctness and lost 15.9pp of fallback-control correctness; a
    selector that reads only the all-family macro dilutes that by the family
    count. This one does not."""
    baseline = metrics_of(DEV)
    collapsed = dict(DEV)
    collapsed["base64"] = (180, 130, 0)
    collapsed["unicodedata"] = (180, 138, 41)
    gate = CheckpointGate(baseline=baseline)
    # Gate A and the margin are both held, so retention is the only thing
    # standing between a 5pp aggregate native gain and selection.
    gate.observe(25, dict(metrics_of(collapsed), correct=baseline["correct"],
                          correct_native=baseline["correct_native"] + 0.05))
    assert gate.best_step == 0
    assert gate.report()["observed"][0]["rejected_for"] == ["retention"]
