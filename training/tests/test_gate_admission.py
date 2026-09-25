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
`summarize`'s shape, seeded (`test_the_simulated_metrics_are_summarize_s_own`
holds the shape to the real function). Anyone proposing to change the
selection rule has to keep the null half and report the power half, which is
the whole point of measuring them together.

The simulation is the one the gate actually faces, which the 2026-09-21
version of this file was not in two ways:

* **Draws are clustered by case.** Dev scores each case k=4 times
  (`train_verified.py --eval-draws`), and the k draws of one case share its
  difficulty. Each case gets a latent rate from a Beta around its family's
  observed rate; `KAPPA` is the Beta's concentration, so the within-case
  correlation is 1/(KAPPA+1). How clustered real dev draws are has not been
  measured (`s4prep` plan, "open"), so two values bracket it.
* **The baseline is redrawn.** Step 0 is an evaluation too, as noisy as the
  candidate. The earlier file held base at its observed counts, so only the
  candidate's noise was in play, and a margin sized on one arm's noise
  looked calibrated. Against a redrawn base that margin admitted noise about
  17% of the time (`test_the_old_one_arm_margin_over_admits_...` pins it).

The "+10pp" arm lifts each coverage case's native *parameter* by 10pp; the
realised gain is smaller, because native is capped at correct and three of the
five coverage families are already saturated. Controls are never lifted in the
native arms: selection reads the coverage population only.
"""
from __future__ import annotations

import math
import random

import pytest

from pipeline.training_metrics import (SELECTION_RULE, SELECTION_RULE_V1, SELECTION_RULE_V2,
                                       SELECTION_RULES, CheckpointGate, case_clusters,
                                       case_weighted_rate, macro_standard_error,
                                       paired_case_standard_error, paired_standard_error, summarize)
from pipeline.training_types import TrainingError

#: Dev baseline of seed 1111, from sft/best.json: draws, correct, correct-native.
#: Capabilities stand in for families (dev has seven of each).
DEV = {
    "base64": (180, 157, 0), "collections": (180, 169, 157), "enum": (180, 174, 174),
    "heapq": (180, 162, 151), "statistics": (144, 137, 137), "textwrap": (180, 159, 159),
    "unicodedata": (180, 165, 41),
}
CONTROL = {"base64", "unicodedata"}
K = 4
#: Beta concentrations bracketing within-case correlation: 1/3 and about 1/21.
KAPPAS = (2.0, 20.0)


def population(family):
    return "fallback-control" if family in CONTROL else "coverage"


def records_of(counts):
    """Rows `summarize` accepts, from per-case (draws, correct, native) counts."""
    rows = []
    for f, cases in counts.items():
        for j, (draws, correct, native) in enumerate(cases):
            for d in range(draws):
                rows.append(dict(family=f, case_id="%s-%02d" % (f, j), draw=d,
                                 population=population(f), correct=d < correct, native=d < native))
    return rows


_DIGEST = {}


def digest(families, counts):
    """The real `case_clusters` digest of this case set; the draws do not enter it."""
    key = tuple((f, len(counts[f])) for f in families)
    if key not in _DIGEST:
        _DIGEST[key] = case_clusters(records_of({f: [(1, 0, 0)] * n for f, n in key}))["digest"]
    return _DIGEST[key]


def metrics_of(counts):
    """A metrics dict in `summarize`'s shape, fast enough to simulate with."""
    per = {}
    for f, cases in counts.items():
        draws = sum(c[0] for c in cases)
        per[f] = {"correct": sum(c[1] for c in cases) / draws,
                  "correct_native": sum(c[2] for c in cases) / draws, "draws": draws}

    def over(families):
        return {"correct": sum(per[f]["correct"] for f in families) / len(families),
                "correct_native": sum(per[f]["correct_native"] for f in families) / len(families),
                "draws": sum(per[f]["draws"] for f in families)}

    def slice_(name):
        families = sorted(f for f in counts if population(f) == name)
        return dict(over(families), case_clusters={"digest": digest(families, counts), "families": [
            {"draws": [c[0] for c in counts[f]], "correct": [c[1] for c in counts[f]],
             "native": [c[2] for c in counts[f]]} for f in families]})

    return dict(over(list(counts)), by_family=per,
                by_population={p: slice_(p) for p in ("coverage", "fallback-control")})


def spread(n, correct, native):
    """Observed family counts as K-draw cases, as evenly as the counts allow."""
    cases = n // K
    c = [correct // cases + (j < correct % cases) for j in range(cases)]
    nn = [native // cases + (j < native % cases) for j in range(cases)]
    return [(K, cj, min(cj, nj)) for cj, nj in zip(c, nn)]


def observed(dev=DEV):
    return {f: spread(*v) for f, v in dev.items()}


def latent(rng, mean, kappa):
    if mean <= 0.0 or mean >= 1.0:
        return mean
    return rng.betavariate(mean * kappa, (1.0 - mean) * kappa)


def dev_cases(rng, kappa):
    """One dev split's cases: a latent (correct, native-given-correct) rate each."""
    return {f: [(latent(rng, c / n, kappa), latent(rng, nn / c, kappa)) for _ in range(n // K)]
            for f, (n, c, nn) in DEV.items()}


def redraw(rng, cases, lift_correct=0.0, lift_native=0.0, draws=K):
    """K draws of every case at its latent rates plus a uniform lift."""
    out = {}
    for f, latents in cases.items():
        rows = []
        for pc, share in latents:
            pn = pc * share
            pc = min(1.0, pc + lift_correct)
            pn = min(pc, pn + (lift_native if f not in CONTROL else 0.0))
            given = pn / pc if pc else 0.0
            cc = nc = 0
            for _ in range(draws):
                if rng.random() < pc:
                    cc += 1
                    nc += rng.random() < given
            rows.append((draws, cc, nc))
        out[f] = rows
    return out


def old_one_arm_margin_admits(base, candidate, z=1.2816):
    """The 2026-09-21 rule: all-family native, margin = z x the per-family
    binomial SE of base alone. Kept here only to show what it did."""
    families = base["by_family"].values()
    se = math.sqrt(sum(s["correct_native"] * (1 - s["correct_native"]) / s["draws"]
                       for s in families)) / len(base["by_family"])
    return (candidate["correct"] >= base["correct"] - 0.02
            and candidate["correct_native"] >= base["correct_native"] + z * se)


def admission_rate(lift_correct, lift_native, kappa, trials=2000, seed=7, draws=K, old=False,
                   rule=SELECTION_RULE):
    rng = random.Random(seed)
    admitted = 0
    for _ in range(trials):
        cases = dev_cases(rng, kappa)
        base = metrics_of(redraw(rng, cases, draws=draws))
        candidate = metrics_of(redraw(rng, cases, lift_correct, lift_native, draws=draws))
        if old:
            admitted += old_one_arm_margin_admits(base, candidate)
            continue
        gate = CheckpointGate(baseline=base, rule=rule)
        gate.observe(25, candidate)
        admitted += gate.best_step == 25
    return admitted / trials


def test_the_simulated_metrics_are_summarize_s_own():
    """The simulation's shortcut builder and the real `summarize` agree on
    every field the gate reads, so the rates below are about the real input."""
    counts = redraw(random.Random(3), dev_cases(random.Random(3), 2.0))
    real, fast = summarize(records_of(counts)), metrics_of(counts)
    for key in ("correct", "correct_native"):
        assert real[key] == pytest.approx(fast[key])
    for name in ("coverage", "fallback-control"):
        for key in ("correct", "correct_native", "draws"):
            assert real["by_population"][name][key] == pytest.approx(fast["by_population"][name][key])
        assert real["by_population"][name]["case_clusters"] == fast["by_population"][name]["case_clusters"]


@pytest.mark.parametrize("rule", SELECTION_RULES)
@pytest.mark.parametrize("kappa", KAPPAS)
def test_noise_is_admitted_at_the_stated_ten_percent_under_clustered_draws(kappa, rule):
    """The null half: a checkpoint that IS base -- same case latents, fresh
    draws for both arms -- clears the paired margin at the rule's 10%.

    Measured 2026-09-22 at 4,000 trials, seed 7, rule v1: 10.53% at KAPPA 2,
    9.23% at KAPPA 20. Rule v2 (case-weighted), measured 2026-09-25 on the
    same trials: 10.50% and 9.18%. The margin is the 90th percentile of the
    delta's own distribution, so the rate sits AT 10% by construction and a
    seeded estimate falls either side of it; asserting a bare `<= 0.10` would
    be a choice of seed. The bound is 10% plus three Monte Carlo standard
    errors (1.4pp at 4,000 trials): it fails a rule that over-admits -- the
    old one drew 17.4% here -- and not a seed that drew 10.5%."""
    trials = 4000
    null = admission_rate(0.0, 0.0, kappa, trials=trials, rule=rule)
    assert null <= 0.10 + 3 * math.sqrt(0.10 * 0.90 / trials), null


def test_the_old_one_arm_margin_over_admits_against_a_redrawn_baseline():
    """Why the margin changed. Near-independent draws (KAPPA 20) and a base
    as noisy as the candidate: the 2026-09-21 margin, sized on base's noise
    alone, admitted 17.40% of pure noise (measured 2026-09-22, 4,000 trials,
    seed 7; 13.25% at KAPPA 2, where clustering makes its per-draw error an
    overstatement). This pins that the null above is not a softer test."""
    assert admission_rate(0.0, 0.0, 20.0, trials=4000, old=True) > 0.13


def test_power_is_what_this_dev_split_can_show_and_draws_buy_it_back():
    """The power half, reported rather than tuned.

    The 2026-09-21 file asserted the +10pp native arm at >= 80% (84.85%
    measured). That held only against a noise-free base. With base redrawn,
    neither margin reaches 80% on this split at k=4. Measured 2026-09-22,
    2,000 trials, seed 7: +10pp native was admitted 38.0% of the time at
    KAPPA 2 and 63.5% at KAPPA 20; the old one-arm margin on the same draws,
    38.1% and 67.2%, while admitting 13-17% of noise. (+5pp correct with
    +10pp native: 88.9% and 100%.) Raising the lift does not help: native is
    capped at correct, so beyond +10pp the realised gain stops growing. More
    draws per case do: at k=16 the +10pp arm is admitted 75.3% (KAPPA 2) and
    97.7% (KAPPA 20) of the time (600 trials).
    Getting 80% by lowering z would be exactly the tuning `SELECTION_Z`
    forbids; the lever is `--eval-draws`, an operator decision.

    Those numbers are rule v1's. The assertions run the default rule, v2
    (case-weighted), which on the same trials (measured 2026-09-25) admitted
    +10pp native 38.95% (KAPPA 2) and 64.75% (KAPPA 20), +5pp correct with
    +10pp native 89.15% and 100%, and at k=16 76.0% and 97.7%: this split's
    coverage families are near-equal in size, so the two rules nearly agree.

    Asserted here: the floors this split does reach, so a change that loses
    power fails, and the k=16 recovery."""
    assert admission_rate(0.0, 0.10, 20.0) >= 0.55
    assert admission_rate(0.05, 0.10, 20.0) >= 0.95
    assert admission_rate(0.05, 0.10, 2.0) >= 0.80
    assert admission_rate(0.0, 0.10, 20.0, trials=600, draws=16) >= 0.80


@pytest.mark.parametrize("rule", SELECTION_RULES)
def test_the_margin_is_the_ten_percent_rule_and_not_a_tuned_constant(rule):
    """The margin of every observation is z case-clustered standard errors of
    the paired delta, recorded next to the delta it was compared with, and
    `best.json` names the rule version it ran under."""
    base = metrics_of(observed())
    better = observed({k: (n, c, c if k not in CONTROL else nn) for k, (n, c, nn) in DEV.items()})
    candidate = metrics_of(better)
    gate = CheckpointGate(baseline=base, rule=rule)
    gate.observe(25, candidate)
    seen = gate.report()["observed"][0]
    b, c = base["by_population"]["coverage"], candidate["by_population"]["coverage"]
    if rule == SELECTION_RULE_V1:
        error, delta = paired_standard_error(b, c), c["correct_native"] - b["correct_native"]
        metric = "by_population.coverage.correct_native"
    else:
        error, delta = paired_case_standard_error(b, c), case_weighted_rate(c) - case_weighted_rate(b)
        metric = "by_population.coverage.case_weighted_native"
    assert seen["standard_error"] == error
    assert seen["margin"] == gate.z * error
    assert seen["delta"] == pytest.approx(delta)
    assert gate.report()["rule"]["metric"] == metric
    assert gate.report()["rule"]["version"] == rule


def test_new_selections_default_to_the_case_weighted_rule_and_unknown_rules_are_refused():
    assert SELECTION_RULE == SELECTION_RULE_V2
    assert CheckpointGate(baseline=metrics_of(observed())).rule == SELECTION_RULE_V2
    with pytest.raises(TrainingError, match="unknown selection rule"):
        CheckpointGate(baseline=metrics_of(observed()), rule="coverage-best-guess/3")


def test_the_case_weighted_rate_is_summarize_s_own_case_weighted_native():
    counts = redraw(random.Random(5), dev_cases(random.Random(5), 2.0))
    coverage = summarize(records_of(counts))["by_population"]["coverage"]
    assert case_weighted_rate(coverage) == pytest.approx(coverage["case_weighted_native"])
    assert case_weighted_rate(coverage, "correct") == pytest.approx(coverage["case_weighted_correct"])
    assert paired_case_standard_error(coverage, coverage) == 0.0


# --- seed 1111 arm A: a dev split with a tiny family (amendment 2026-09-25) --

#: SHAPED like seed 1111's arm-A SFT selection (HF job 6ab52a686b030d633f68e503,
#: 2026-09-24), not its rows, which are private: seven dev families, one of
#: them a two-case coverage family, k = 16. Pooled coverage correct-and-native
#: rises steadily over 350 / 700 / 1,050 by roughly the +1.9 / +2.6 / +3.1pp
#: the operator read from that job, and the all-family correctness by roughly
#: -0.9 / 0.0 / +0.6pp; the tiny family alone swings up at 350 and down after.
ARM_A_BIG = ("alpha", "beta", "gamma", "delta")
ARM_A_TINY = "tiny"
ARM_A_CONTROLS = ("ctl-a", "ctl-b")
ARM_A_DRAWS = 16
#: Per-case native-draw deltas over base, cycled over the big families' cases.
ARM_A_LIFT = {350: [2, 1, 1, 1, 1, 1, -1], 700: [2, 2, 1, 1, 1, 1, 1, 1, 1, 1, -1],
              1050: [2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, -1]}
ARM_A_TINY_LIFT = {0: (0, 0), 350: (7, 5), 700: (-3, -1), 1050: (-3, -1)}


def arm_a_rows(step):
    """One dev evaluation of the shaped fixture at `step`, as `summarize` reads it."""
    rows = []

    def case(family, i, population, correct, native):
        for d in range(ARM_A_DRAWS):
            rows.append(dict(family=family, case_id="%s-%02d" % (family, i), draw=d,
                             population=population, correct=d < correct, native=d < native))

    for j in range(45 * len(ARM_A_BIG)):
        correct, native = 15, (5, 8, 11, 13)[j % 4]
        if step:
            lift = ARM_A_LIFT[step]
            native += lift[j % 25] if j % 25 < len(lift) else 0
        if step == 350 and j % 4 == 0:
            correct -= 1          # a small correctness cost at the first checkpoint
        if step == 1050 and j % 6 == 1:
            correct += 1          # and a small correctness gain at the last
        case(ARM_A_BIG[j // 45], j % 45, "coverage", correct, native)
    for i, lift in enumerate(ARM_A_TINY_LIFT[step]):
        case(ARM_A_TINY, i, "coverage", 15, 8 + lift)
    for family in ARM_A_CONTROLS:
        for i in range(45):
            case(family, i, "fallback-control", 14, 0)
    return rows


def test_a_tiny_dev_family_steers_the_family_macro_and_not_the_case_weighted_rule():
    """The amendment of 2026-09-25, on a fixture shaped like seed 1111's arm A.

    Rule v1 ranks the coverage FAMILY macro, in which the two-case family
    weighs as much as a 45-case one: its swing at step 350 carries the macro
    and 350 is selected, while every later checkpoint -- better on the pooled
    evidence -- is rejected for the margin. Rule v2 ranks the case-weighted
    rate, every case one unit, and selects 1,050, the checkpoint the pooled
    evidence favours; its null half is the test above."""
    metrics = {step: summarize(arm_a_rows(step)) for step in (0, 350, 700, 1050)}
    base = metrics[0]

    def pooled(step, key):
        return case_weighted_rate(metrics[step]["by_population"]["coverage"], key) \
            - case_weighted_rate(base["by_population"]["coverage"], key)
    shaped = {350: (0.019, -0.009), 700: (0.026, 0.0), 1050: (0.031, 0.006)}
    for step, (native, correct) in shaped.items():
        assert pooled(step, "correct_native") == pytest.approx(native, abs=0.005), step
        assert metrics[step]["correct"] - base["correct"] == pytest.approx(correct, abs=0.004), step

    selected = {}
    for rule in SELECTION_RULES:
        gate = CheckpointGate(baseline=base, rule=rule)
        for step in (350, 700, 1050):
            gate.observe(step, metrics[step])
        selected[rule] = gate.best_step
        report = gate.report()
        assert report["step"] == gate.best_step and report["rule"]["version"] == rule
    assert selected == {SELECTION_RULE_V1: 350, SELECTION_RULE_V2: 1050}


def test_the_standard_error_counts_cases_not_draws():
    """k draws of one case are one observation of that case.

    Two families of four cases at k=4. In `same`, every case is solved 2 of 4
    times: the cases do not differ, so the macro has no between-case noise
    and its clustered standard error is zero, where a per-draw binomial says
    0.125/sqrt(8). In `split`, half the cases always pass and half never do:
    the draws repeat each case exactly, so the family is four observations,
    not sixteen, and the clustered error is the per-case one."""
    same = {"a": [(4, 2, 2)] * 4, "b": [(4, 2, 2)] * 4}
    split = {"a": [(4, 4, 4), (4, 0, 0)] * 2, "b": [(4, 4, 4), (4, 0, 0)] * 2}
    coverage = lambda counts: summarize(records_of(counts))["by_population"]["coverage"]
    assert macro_standard_error(coverage(same)) == 0.0
    per_case = math.sqrt(4 / 3 * 0.25 / 4)       # sample SD of {1,0,1,0}, over sqrt(4) cases
    assert macro_standard_error(coverage(split)) == pytest.approx(math.sqrt(2) * per_case / 2)


def test_pairing_is_refused_across_different_cases():
    base = summarize(records_of({"a": [(4, 2, 2)] * 3}))["by_population"]["coverage"]
    other = summarize(records_of({"b": [(4, 2, 2)] * 3}))["by_population"]["coverage"]
    with pytest.raises(TrainingError, match="same cases"):
        paired_standard_error(base, other)


def test_selection_is_refused_without_a_coverage_population_or_its_clusters():
    only_controls = summarize([dict(family="d", case_id="d-0", draw=0, population="fallback-control",
                                    correct=True, native=False)])
    with pytest.raises(TrainingError, match="'coverage' population"):
        CheckpointGate(baseline=only_controls)
    hand_built = {"correct": 0.5, "correct_native": 0.5,
                  "by_population": {"coverage": {"correct": 0.5, "correct_native": 0.5, "draws": 8}}}
    with pytest.raises(TrainingError, match="case clusters"):
        CheckpointGate(baseline=hand_built)


def test_a_control_family_turning_native_is_not_selected():
    """`s4prep` plan item 1.4. Every correct control draw becomes native:
    the all-family correct-and-native macro jumps about 17pp, correctness is
    held exactly, and the old all-family rule would have selected it. But a
    control's right answer is the fallback -- `build_targets` rejects these
    very draws as `control-became-native` -- so coverage did not move and
    selection does not either."""
    base = metrics_of(observed())
    flipped = observed({k: (n, c, c if k in CONTROL else nn) for k, (n, c, nn) in DEV.items()})
    candidate = metrics_of(flipped)
    assert candidate["correct"] == base["correct"]
    assert candidate["correct_native"] - base["correct_native"] > 0.15
    assert old_one_arm_margin_admits(base, candidate)

    gate = CheckpointGate(baseline=base)
    gate.observe(25, candidate)
    assert gate.best_step == 0
    seen = gate.report()["observed"][0]
    # Every coverage case is unchanged, so the paired delta and its standard
    # error are both exactly zero: the checkpoint ties base, and a tie never
    # displaces the incumbent.
    assert (seen["delta"], seen["standard_error"]) == (0.0, 0.0)
    assert seen["rejected_for"] == ["not-best"]


def test_one_draw_of_noise_on_one_capability_no_longer_vetoes_a_native_gain():
    """The defect this file was written for, inverted.

    Every correct coverage draw becomes native: the largest native gain this
    dev split can show with correctness held exactly. Under the old
    lexicographic key it was admitted only while nothing anywhere regressed,
    so one fewer correct draw on one capability -- inside noise -- rejected
    it. Capabilities are now reported, not floors."""
    baseline = metrics_of(observed())
    better = {k: (n, c, c if k not in CONTROL else nn) for k, (n, c, nn) in DEV.items()}
    m = metrics_of(observed(better))
    assert m["correct"] == baseline["correct"], "correctness held exactly"
    assert m["correct_native"] > baseline["correct_native"]

    gate = CheckpointGate(baseline=baseline)
    gate.observe(25, m)
    assert gate.best_step == 25

    worse = dict(better)
    worse["enum"] = (180, 173, 173)
    gate = CheckpointGate(baseline=baseline)
    gate.observe(25, metrics_of(observed(worse)))
    assert gate.best_step == 25, "one draw on one capability is not a regression"


def test_a_control_collapse_still_vetoes_a_coverage_gain():
    """Retention survives the rewrite. SFT at step 50 of seed 1111 held its
    coverage correctness and lost 15.9pp of fallback-control correctness; a
    selector that reads only the coverage macro cannot see that at all. The
    retention rule does."""
    collapsed = {k: (n, c, c if k not in CONTROL else nn) for k, (n, c, nn) in DEV.items()}
    collapsed["base64"] = (180, 130, 0)
    collapsed["unicodedata"] = (180, 138, 41)
    baseline = metrics_of(observed())
    candidate = metrics_of(observed(collapsed))
    gate = CheckpointGate(baseline=baseline)
    # Gate A is held, so retention is the only thing standing between the
    # largest coverage gain this split can show and selection.
    gate.observe(25, dict(candidate, correct=baseline["correct"]))
    assert gate.best_step == 0
    assert gate.report()["observed"][0]["rejected_for"] == ["retention"]


def test_the_paired_error_is_the_noise_of_the_delta_not_of_either_arm():
    """Pairing, pinned without a simulation. Cases that differ wildly from one
    another but identically in both arms carry between-case spread into each
    arm's own error and none into the delta's: an evaluation paired with
    itself has a paired error of exactly zero, where two independent arms'
    errors would add in quadrature."""
    split = {"a": [(4, 4, 4), (4, 0, 0)] * 3, "b": [(4, 4, 2), (4, 1, 0)] * 3}
    base = summarize(records_of(split))["by_population"]["coverage"]
    assert macro_standard_error(base) > 0.1
    assert paired_standard_error(base, base) == 0.0
    one_more = dict(split, a=[(4, 4, 4), (4, 1, 1)] + split["a"][2:])
    candidate = summarize(records_of(one_more))["by_population"]["coverage"]
    unpaired = math.hypot(macro_standard_error(base), macro_standard_error(candidate))
    assert 0.0 < paired_standard_error(base, candidate) < unpaired / 4


def coupled_admission_rate(lift_native, kappa, trials, seed=9):
    """Base and candidate drawn from the SAME uniforms, case by case and draw by draw."""
    rng = random.Random(seed)
    admitted = 0
    for _ in range(trials):
        base, candidate = {}, {}
        for f, latents in dev_cases(rng, kappa).items():
            lift = 0.0 if f in CONTROL else lift_native
            rows_b, rows_c = [], []
            for pc, share in latents:
                pn = pc * share
                pn_c = min(pc, pn + lift)
                cb = nb = nc = 0
                for _ in range(K):
                    u, v = rng.random(), rng.random()
                    if u < pc:
                        cb += 1
                        nb += v < share
                        nc += v < (pn_c / pc)
                rows_b.append((K, cb, nb))
                rows_c.append((K, cb, nc))
            base[f], candidate[f] = rows_b, rows_c
        gate = CheckpointGate(baseline=metrics_of(base))
        gate.observe(25, metrics_of(candidate))
        admitted += gate.best_step == 25
    return admitted / trials


def test_draws_coupled_by_a_shared_sampling_seed_are_what_pairing_buys():
    """The simulation above draws base and candidate independently. Production
    does not: `verified_evaluation.chunk_seed` depends on the run seed and the
    chunk's case IDs and NOT on the step, so step 0 and every checkpoint sample
    under the same seeds, and a checkpoint that barely moved the policy mostly
    reproduces base's draws. That coupling is what the paired error exploits.

    At full coupling (every uniform shared) the +10pp native arm at k=4 is
    admitted 99.9% of the time at KAPPA 2 and 100% at KAPPA 20 (1,500 trials,
    seed 9, measured 2026-09-22), against 35.8% / 62.9% uncoupled. How
    coupled a trained LoRA's draws stay under a shared seed has not been
    measured; per-(case, draw) agreement between step 0 and step N in seed
    1111's `evaluations.jsonl` would bound it, and it decides whether k=4
    lacks power at all. An unpaired error -- each arm's own, in quadrature --
    would throw the coupling away, and this assertion with it.

    Measured the same day and NOT asserted: with coupled draws and a
    candidate whose case rates are exchangeable with base's but so close to
    them that only a handful of cases differ, the null rises to about 11.5-12%
    (8,000 trials, four seeds, per-case rate jitter SD 0.2-0.3pp, KAPPA 2). The
    normal quantile over a standard error estimated from so few discordant
    cases is optimistic; a t quantile or a sign-flip randomization test would
    close it, and either is a change to the rule, not to a constant."""
    assert coupled_admission_rate(0.10, 2.0, trials=300) >= 0.95
