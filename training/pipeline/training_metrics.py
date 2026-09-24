"""Family-macro reporting and development-only checkpoint selection, no GPU deps."""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import random

from .mismatch_policy import engine_mismatches
from .training_types import TrainingError


BENCHMARK_MIN_FAMILY_CASES = 5


def family_eligibility(records, minimum):
    """Count distinct cases before filtering; repeated draws never buy admission."""
    if type(minimum) is not int or minimum < 1:
        raise TrainingError("minimum family cases must be a positive integer")
    families = {}
    for i, row in enumerate(records):
        if minimum > 1 and "case_id" not in row:
            raise TrainingError("family-size admission needs case IDs")
        families.setdefault(row["family"], set()).add(row.get("case_id", str(i)))
    return {f for f, cases in families.items() if len(cases) >= minimum}


def summarize(records, *, min_family_cases=1):
    if not records:
        raise ValueError("cannot score an empty evaluation")
    def aggregate(rows):
        families = {r["family"] for r in rows}
        def macro(key):
            return sum(sum(r[key] for r in rows if r["family"] == f) /
                       sum(r["family"] == f for r in rows) for f in families) / len(families)
        return {"correct": macro("correct"), "correct_native": macro("native"),
                "case_weighted_correct": sum(r["correct"] for r in rows) / len(rows),
                "case_weighted_native": sum(r["native"] for r in rows) / len(rows),
                "cases": len({r.get("case_id", str(i)) for i, r in enumerate(rows)}),
                "draws": len(rows), "families": len(families),
                "truncation_rate": sum(r.get("truncated", False) for r in rows) / len(rows),
                "mean_completion_tokens": sum(r.get("completion_tokens", 0) for r in rows) / len(rows),
                "statuses": dict(Counter(r.get("status", "unreported") for r in rows)),
                # Always present, zero included: an engine-mismatch draw is an
                # engine bug, and its count is reported even when it is none
                # (`mismatch_policy`). Such a draw is neither correct nor native.
                "engine_mismatches": engine_mismatches(rows)}
    if all("case_id" in r for r in records):
        draws = Counter(r["case_id"] for r in records)
        if len(set(draws.values())) != 1:
            raise TrainingError("evaluation must have equal draw counts per case")
        keys = [(r["case_id"], r.get("draw", 0)) for r in records]
        if len(keys) != len(set(keys)):
            raise TrainingError("duplicate evaluation case/draw")
    eligible = family_eligibility(records, min_family_cases)
    primary = [r for r in records if r["family"] in eligible]
    if not primary:
        raise TrainingError("no families meet the primary macro minimum case count")
    return dict(aggregate(primary),
        macro_rule={"min_family_cases": min_family_cases, "scope": "all-population families",
                    "excluded_cases": len({r["case_id"] for r in records if r["family"] not in eligible}),
                    "excluded_families": len({r["family"] for r in records} - eligible),
                    "population_slices": "unfiltered descriptive family macro and case-weighted rates"},
        by_family={f: aggregate([r for r in records if r["family"] == f])
                   for f in sorted({r["family"] for r in records})},
        by_population={p: dict(aggregate(rows), case_clusters=case_clusters(rows))
                       for p in sorted({r["population"] for r in records})
                       for rows in [[r for r in records if r["population"] == p]]},
        by_capability={c: aggregate([r for r in records if c in r.get("capabilities", [])])
                       for c in sorted({c for r in records for c in r.get("capabilities", [])})})


def case_clusters(rows):
    """Per-case draw, correct and native counts, family by family, in a fixed order.

    A case's k draws share its difficulty, so they are not k independent
    observations; the unit of replication is the case. These counts are what
    a case-clustered standard error, and a paired one, are computed from
    (`paired_standard_error`). Counts rather than case IDs, and `digest`
    names the (family, case) set so two evaluations can be proved to pair
    without either artifact holding the other's IDs. The counts are PRIVATE
    (operator decision, 2026-09-23): the artifacts keep them, and every printer
    that reaches a public Actions log strips them with `pipeline.public_view`.
    Families and cases are sorted, so the same set gives the same order in
    every evaluation. A row without a case ID is its own case, as in
    `summarize`'s `cases` count.
    """
    families = {}
    for i, r in enumerate(rows):
        counts = families.setdefault(r["family"], {}).setdefault(r.get("case_id", str(i)), [0, 0, 0])
        counts[0] += 1
        counts[1] += int(bool(r["correct"]))
        counts[2] += int(bool(r["native"]))
    identity = [[f, sorted(cases)] for f, cases in sorted(families.items())]
    return {"digest": hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest(),
            "families": [{name: [families[f][c][i] for c in cases]
                          for i, name in enumerate(("draws", "correct", "native"))}
                         for f, cases in identity]}


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


def paired_comparison(base, candidate, *, seed=1111, resamples=2000, min_family_cases=1):
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
    families = sorted(family_eligibility(list(a.values()), min_family_cases))
    if not families:
        raise TrainingError("no families meet the primary macro minimum case count")
    # Build links BEFORE exclusion: an ineligible family can still connect
    # two eligible families through their source groups.
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
            "min_family_cases": min_family_cases,
            "method": "paired source/family-component percentile bootstrap; exploratory with few clusters",
            "metrics": results}



#: Gate A, `PREREGISTRATION.md` §7c: the tuned correctness rate may not fall
#: more than this below base. One tolerance on the family macro -- not a floor
#: per capability and per population, which is what seed 1111 selected under.
GATE_A_TOLERANCE = 0.02

#: One-sided normal quantile for the selection margin; 1.2816 is the 90th
#: percentile. The constant IS the rule "a checkpoint no better than base is
#: selected at most 10% of the time", not a number tuned until a test passed.
#: It holds only when the standard error it multiplies is the noise of the
#: quantity compared -- the paired delta, clustered by case -- which is what
#: `paired_standard_error` supplies.
SELECTION_Z = 1.2816

#: Population retention is a collapse detector, not a significance test: three
#: standard errors, so a slice of a few hundred draws wobbling within noise
#: never vetoes a gain, and the 16pp control loss SFT took in seed 1111 does.
RETENTION_Z = 3.0


#: The population selection ranks on. A fallback-control family is one whose
#: right answer is to fall back to CPython; its draws turning native are not
#: the gain being trained for, and `positive_control_targets.build_targets`
#: rejects exactly those draws as `control-became-native`. Controls enter the
#: gate only through Gate A and correctness retention.
SELECTION_POPULATION = "coverage"

_COUNT = {"correct": "correct", "correct_native": "native"}


def _clustered_variance(families):
    """Variance of a family macro, from per-case values grouped by family.

    A family's rate is a mean over its cases, so its variance is the
    between-case sample variance over the case count: the case is the cluster,
    and k correlated draws of one case count once. A family of one case has no
    between-case spread to measure; its term is then the draws' own binomial
    variance (`single`), which assumes the independence one case cannot test.
    The macro weights families equally, hence the squared family count.
    """
    variance = 0.0
    for values, single in families:
        n = len(values)
        if n < 2:
            variance += single
            continue
        mean = sum(values) / n
        variance += sum((v - mean) ** 2 for v in values) / (n - 1) / n
    return variance / len(families) ** 2


def _clusters(stats):
    clusters = stats.get("case_clusters") if isinstance(stats, dict) else None
    if not clusters or not clusters.get("families"):
        raise TrainingError("checkpoint selection needs the case clusters `summarize` reports")
    return clusters


def _binomial(fam, count):
    draws = sum(fam["draws"])
    if draws <= 0:
        raise TrainingError("a family with no draws cannot enter the macro")
    rate = sum(fam[count]) / draws
    return rate * (1.0 - rate) / draws


def macro_standard_error(stats, key="correct_native"):
    """Case-clustered standard error of one slice's family macro.

    `stats` is a `summarize` slice carrying `case_clusters` (a `by_population`
    entry). The per-family `p(1-p)/draws` this replaced treated a case's k
    draws as k independent observations. They share the case's difficulty, so
    the draws of a case that is always solved add no information, and the
    spread between cases -- which the draws repeat -- is the noise that counts.
    """
    count = _COUNT[key]
    return math.sqrt(_clustered_variance(
        [([x / d for x, d in zip(fam[count], fam["draws"])], _binomial(fam, count))
         for fam in _clusters(stats)["families"]]))


def paired_standard_error(base, candidate, key="correct_native"):
    """Case-clustered standard error of the paired candidate-minus-base macro delta.

    Selection asks whether a checkpoint beats base, and base is a measurement
    too, as noisy as the candidate. A margin sized on base's noise alone and
    tested against a base held fixed admits a checkpoint no better than base
    more often than the 10% it states (`tests/test_gate_admission.py` measures
    it). Both evaluations score the same dev cases, so the delta is taken case
    by case: difficulty a case carries into both arms cancels, and what remains
    -- both arms' draw noise, and any case-level shift the checkpoint made --
    is exactly what the spread of the per-case deltas measures.
    """
    count = _COUNT[key]
    b, c = _clusters(base), _clusters(candidate)
    if b["digest"] != c["digest"] or len(b["families"]) != len(c["families"]):
        raise TrainingError("paired selection needs the same cases in both evaluations")
    return math.sqrt(_clustered_variance(
        [([xc / dc - xb / db for xb, db, xc, dc in
           zip(fb[count], fb["draws"], fc[count], fc["draws"])],
          _binomial(fb, count) + _binomial(fc, count))
         for fb, fc in zip(b["families"], c["families"])]))


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
       family macro of the `population` slice (coverage), whose paired gain
       over base must clear `z` case-clustered standard errors of that delta
       (`paired_standard_error`). Without that margin a pure argmax admits
       pure noise about half the time, because the best of several noisy draws
       is biased upward. Controls are not ranked: a control family flipping
       from fallback to native is not the effect being trained for, so
       controls count only through rules 1 and 2.

    Step 0 is the incumbent and stays selectable: nothing displaces it unless
    it clears the bar. Every observation is kept in `report()`, admitted or
    not, with its delta, standard error and margin -- the margin depends on
    the candidate now, since it is the noise of a paired delta -- so the
    selection can be re-read, and re-made, offline.
    """
    baseline: dict
    tolerance: float = GATE_A_TOLERANCE
    z: float = SELECTION_Z
    population: str = SELECTION_POPULATION
    best_step: int = 0
    best: dict = None
    observations: list = field(default_factory=list)

    def __post_init__(self):
        self.baseline = deepcopy(self.baseline)
        self.best = deepcopy(self.baseline)
        self._selected(self.baseline)   # refuse, at step 0, a baseline selection cannot read

    def _selected(self, metrics):
        """The slice selection ranks on: refused when absent, never read as zero."""
        stats = metrics.get("by_population", {}).get(self.population)
        if stats is None:
            raise TrainingError("checkpoint selection needs the %r population" % self.population)
        _clusters(stats)
        return stats

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
        base, candidate = self._selected(self.baseline), self._selected(metrics)
        score = candidate["correct_native"]
        delta = score - base["correct_native"]
        error = paired_standard_error(base, candidate)
        margin = self.z * error
        lost = self._retention(metrics)
        reasons = []
        if metrics["correct"] < self.baseline["correct"] - self.tolerance:
            reasons.append("gate-a")
        if lost:
            reasons.append("retention")
        if delta < margin:
            reasons.append("margin")
        if not reasons and score <= self._selected(self.best)["correct_native"]:
            reasons.append("not-best")
        self.observations.append({"step": step, "correct": metrics["correct"],
                                  "correct_native": metrics["correct_native"],
                                  "selection_correct_native": score, "delta": delta,
                                  "standard_error": error, "margin": margin,
                                  "selected": not reasons, "rejected_for": reasons,
                                  "retention_lost": lost})
        if not reasons:
            self.best, self.best_step = metrics, step

    def report(self):
        base = self._selected(self.baseline)
        return dict(self.best, step=self.best_step, rule={
            "metric": "by_population.%s.correct_native" % self.population,
            "selection_population": self.population, "gate_a_tolerance": self.tolerance,
            "selection_z": self.z, "retention_z": RETENTION_Z,
            "margin": "selection_z x the case-clustered standard error of the paired "
                      "delta over base; per observation",
            "baseline_standard_error": macro_standard_error(base),
            "baseline_correct": self.baseline["correct"],
            "baseline_correct_native": base["correct_native"],
            "selection_is_post_hoc": True, "early_stopping": False,
        }, observed=list(self.observations))
