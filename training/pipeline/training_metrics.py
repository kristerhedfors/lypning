"""Family-macro reporting and development-only checkpoint selection, no GPU deps."""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from copy import deepcopy
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



@dataclass
class CheckpointGate:
    baseline: dict
    patience: int
    best_step: int = 0
    stale: int = 0
    best: dict = None

    def __post_init__(self):
        self.baseline = deepcopy(self.baseline)
        self.best = deepcopy(self.baseline)

    def observe(self, step, metrics):
        # Aggregate gains must not hide correctness loss on fallback controls
        # (or on the coverage population). Test data never enters this gate.
        eligible = all(metrics["by_population"].get(p, {}).get("correct", -1) >= s["correct"]
                       for p, s in self.baseline["by_population"].items())
        eligible = eligible and all(
            metrics.get("by_capability", {}).get(c, {}).get("correct", -1) >= score["correct"]
            for c, score in self.baseline.get("by_capability", {}).items())
        key = lambda m: (m["correct"], m["correct_native"])
        if eligible and key(metrics) > key(self.best):
            self.best, self.best_step, self.stale = metrics, step, 0
        else:
            self.stale += 1
        return self.stale >= self.patience

    def report(self):
        return dict(self.best, step=self.best_step, stale_checks=self.stale)
