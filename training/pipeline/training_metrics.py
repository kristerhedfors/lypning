"""Family-macro reporting and development-only checkpoint selection, no GPU deps."""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter
from copy import deepcopy
import math
import random

from .training_types import TrainingError


def summarize(records):
    if not records:
        raise ValueError("cannot score an empty evaluation")
    def aggregate(rows):
        families = {r["family"] for r in rows}
        def macro(key):
            return sum(sum(r[key] for r in rows if r["family"] == f) /
                       sum(r["family"] == f for r in rows) for f in families) / len(families)
        return {"correct": macro("correct"), "correct_native": macro("native"),
                "cases": len({r.get("case_id", str(i)) for i, r in enumerate(rows)}),
                "draws": len(rows), "families": len(families),
                "truncation_rate": sum(r.get("truncated", False) for r in rows) / len(rows),
                "mean_completion_tokens": sum(r.get("completion_tokens", 0) for r in rows) / len(rows),
                "statuses": dict(Counter(r.get("status", "unreported") for r in rows))}
    if all("case_id" in r for r in records):
        draws = Counter(r["case_id"] for r in records)
        if len(set(draws.values())) != 1:
            raise TrainingError("evaluation must have equal draw counts per case")
        keys = [(r["case_id"], r.get("draw", 0)) for r in records]
        if len(keys) != len(set(keys)):
            raise TrainingError("duplicate evaluation case/draw")
    return dict(aggregate(records),
        by_family={f: aggregate([r for r in records if r["family"] == f])
                   for f in sorted({r["family"] for r in records})},
        by_population={p: aggregate([r for r in records if r["population"] == p])
                       for p in sorted({r["population"] for r in records})},
        by_capability={c: aggregate([r for r in records if c in r.get("capabilities", [])])
                       for c in sorted({c for r in records for c in r.get("capabilities", [])})})


def split_components(links):
    """Families and split groups as connected components: a family that spans
    groups links them, a group holding two families links those, and one
    component is one independent unit. Returns family -> component key, the
    key being the component's first family in sorted order.

    Before 2026-09-16 a family spanning groups was refused outright; the eval-2
    bank v1 has 18 such families (one across 23 source groups), so the refusal
    would have voided every comparison on the bank."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for family, group in links:
        a, b = find(("f", str(family))), find(("g", str(group)))
        if a != b:
            parent[a] = b
    members = {}
    for kind, name in list(parent):
        if kind == "f":
            members.setdefault(find((kind, name)), []).append(name)
    key = {root: min(names) for root, names in members.items()}
    return {name: key[find(("f", name))] for names in members.values() for name in names}


def paired_comparison(base, candidate, *, seed=1111, resamples=2000):
    """Paired split-component bootstrap; linked families are not independent.
    A component is every family and split group reachable from one another
    (`split_components`); components are resampled, the statistic is the macro
    over the families the drawn components carry."""
    def index(rows):
        summarize(rows)
        return {(r["case_id"], r["draw"]): r for r in rows}
    a, b = index(base), index(candidate)
    if a.keys() != b.keys():
        raise TrainingError("paired evaluation needs identical case/draw IDs")
    for key in a:
        if any(a[key].get(k) != b[key].get(k) for k in ("family", "population", "capabilities", "seed", "split_group")):
            raise TrainingError("paired evaluation metadata/seed mismatch")
    if resamples < 100:
        raise TrainingError("need at least 100 bootstrap resamples")
    families = sorted({r["family"] for r in a.values()})
    component = split_components({(r["family"], r.get("split_group", r["family"])) for r in a.values()})
    clusters = {}
    for f in families:
        clusters.setdefault(component[f], []).append(f)
    results = {}
    for metric in ("correct", "native"):
        delta = {f: sum(float(b[k][metric]) - float(a[k][metric]) for k in a if a[k]["family"] == f) /
                 sum(a[k]["family"] == f for k in a) for f in families}
        rng = random.Random(seed)
        samples = []
        groups = list(clusters.values())
        for _ in range(resamples):
            sampled = [f for _ in groups for f in rng.choice(groups)]
            samples.append(sum(delta[f] for f in sampled) / len(sampled))
        samples.sort()
        results[metric] = {"delta": sum(delta.values()) / len(delta),
            "ci95": [samples[int(.025 * resamples)], samples[min(resamples - 1, int(.975 * resamples))]]}
    return {"families": len(families), "independent_clusters": len(clusters), "resamples": resamples, "seed": seed,
            "method": "paired source/family-component percentile bootstrap; exploratory with few clusters",
            "metrics": results}



#: Gate A, `PREREGISTRATION.md` §7c: the tuned correctness rate may not fall
#: more than this below base. One tolerance on the family macro -- not a floor
#: per capability and per population, which is what seed 1111 selected under.
GATE_A_TOLERANCE = 0.02

#: One-sided normal quantile for the selection margin; 1.2816 is the 90th
#: percentile. The constant IS the rule "a checkpoint no better than base is
#: selected at most 10% of the time", not a number tuned until a test passed.
SELECTION_Z = 1.2816

#: Population retention is a collapse detector, not a significance test: three
#: standard errors, so a slice of a few hundred draws wobbling within noise
#: never vetoes a gain, and the 16pp control loss SFT took in seed 1111 does.
RETENTION_Z = 3.0


def macro_standard_error(metrics, key="correct_native"):
    """Standard error of a family macro of per-family rates, from `summarize`.

    The macro weights families equally, so its variance is the sum of the
    per-family binomial variances over the squared family count -- not
    `p(1-p)/draws` over the pooled draws, which on seed 1111's dev split
    overstates it by about 1.6x because the per-family rates are extreme
    (one family at 0.0, three saturated) while their average is near 0.5.
    """
    families = metrics.get("by_family")
    if not families:
        raise TrainingError("checkpoint selection needs per-family metrics")
    variance = 0.0
    for stats in families.values():
        rate, draws = float(stats[key]), int(stats["draws"])
        if draws <= 0:
            raise TrainingError("a family with no draws cannot enter the macro")
        variance += rate * (1.0 - rate) / draws
    return math.sqrt(variance) / len(families)


def pooled_standard_error(stats, key="correct"):
    """Binomial standard error over a slice's pooled draws.

    Used for population slices, where `summarize` reports the slice macro and
    its draw count but not the families inside it. It overstates a
    heterogeneous slice's noise, which widens the retention tolerance -- the
    conservative direction for a check whose job is not to cry wolf.
    """
    rate, draws = float(stats[key]), int(stats.get("draws", 0))
    if draws <= 0:
        return 0.0
    return math.sqrt(rate * (1.0 - rate) / draws)


@dataclass
class CheckpointGate:
    """Post-hoc selection on the metric being trained for. Never stops training.

    Three rules, in place of seed 1111's nine hard floors and its correctness
    -first lexicographic key (`PLAN.md` Step 1.1-1.2, and the admission
    simulation in `tests/test_gate_admission.py`):

    1. **Gate A.** The all-family correctness macro may not fall more than
       `tolerance` below base. A tolerance, because a floor on a noisy
       sub-metric vetoes on one draw of noise.
    2. **Retention.** No population slice's correctness macro may fall more
       than `RETENTION_Z` standard errors -- at least `tolerance` -- below
       base. An aggregate gain must not hide a fallback-control collapse.
    3. **Selection.** Among the eligible, the largest correct-and-native
       family macro, and it must clear base by `margin` = `z` standard errors.
       Without that margin a pure argmax admits pure noise about half the
       time, because the best of several noisy draws is biased upward.

    Step 0 is the incumbent and stays selectable: nothing displaces it unless
    it clears the bar. Every observation is kept in `report()`, admitted or
    not, so the selection can be re-read -- and re-made -- offline.
    """
    baseline: dict
    tolerance: float = GATE_A_TOLERANCE
    z: float = SELECTION_Z
    best_step: int = 0
    best: dict = None
    margin: float = 0.0
    observations: list = field(default_factory=list)

    def __post_init__(self):
        self.baseline = deepcopy(self.baseline)
        self.best = deepcopy(self.baseline)
        self.margin = self.z * macro_standard_error(self.baseline)

    def _retention(self, metrics):
        """Population slices whose correctness fell further than their noise."""
        lost = []
        for name, base in self.baseline.get("by_population", {}).items():
            allowed = max(self.tolerance, RETENTION_Z * pooled_standard_error(base))
            observed = metrics.get("by_population", {}).get(name, {}).get("correct")
            if observed is None or float(observed) < float(base["correct"]) - allowed:
                lost.append({"population": name, "allowed_drop": allowed,
                             "baseline": base["correct"], "observed": observed})
        return lost

    def observe(self, step, metrics):
        """Record a checkpoint and maybe select it. Returns nothing on purpose.

        Stopping is not selection: a registered dose trains to completion and
        the selection is read afterwards. The old return value was an early
        stop that cut seed 1111's GRPO off at step 15 of a registered 20.
        """
        floor = self.baseline["correct"] - self.tolerance
        bar = self.baseline["correct_native"] + self.margin
        lost = self._retention(metrics)
        reasons = []
        if metrics["correct"] < floor:
            reasons.append("gate-a")
        if lost:
            reasons.append("retention")
        if metrics["correct_native"] < bar:
            reasons.append("margin")
        if not reasons and metrics["correct_native"] <= self.best["correct_native"]:
            reasons.append("not-best")
        self.observations.append({"step": step, "correct": metrics["correct"],
                                  "correct_native": metrics["correct_native"],
                                  "selected": not reasons, "rejected_for": reasons,
                                  "retention_lost": lost})
        if not reasons:
            self.best, self.best_step = metrics, step

    def report(self):
        return dict(self.best, step=self.best_step, rule={
            "metric": "correct_native", "gate_a_tolerance": self.tolerance,
            "selection_z": self.z, "retention_z": RETENTION_Z, "margin": self.margin,
            "baseline_correct": self.baseline["correct"],
            "baseline_correct_native": self.baseline["correct_native"],
            "selection_is_post_hoc": True, "early_stopping": False,
        }, observed=list(self.observations))
