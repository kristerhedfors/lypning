"""Family-macro reporting and development-only checkpoint selection, no GPU deps."""
from __future__ import annotations

from dataclasses import dataclass


def summarize(records):
    if not records:
        raise ValueError("cannot score an empty evaluation")
    def aggregate(rows):
        families = {r["family"] for r in rows}
        def macro(key):
            return sum(sum(r[key] for r in rows if r["family"] == f) /
                       sum(r["family"] == f for r in rows) for f in families) / len(families)
        return {"correct": macro("correct"), "correct_native": macro("native"),
                "cases": len(rows), "families": len(families)}
    return dict(aggregate(records), by_population={p: aggregate([r for r in records if r["population"] == p])
                for p in sorted({r["population"] for r in records})})


@dataclass
class CheckpointGate:
    baseline: dict
    patience: int
    best_step: int = 0
    stale: int = 0
    best: dict = None

    def __post_init__(self):
        self.best = self.baseline

    def observe(self, step, metrics):
        # Aggregate gains must not hide correctness loss on fallback controls
        # (or on the coverage population). Test data never enters this gate.
        eligible = all(metrics["by_population"].get(p, {}).get("correct", -1) >= s["correct"]
                       for p, s in self.baseline["by_population"].items())
        key = lambda m: (m["correct"], m["correct_native"])
        if eligible and key(metrics) > key(self.best):
            self.best, self.best_step, self.stale = metrics, step, 0
        else:
            self.stale += 1
        return self.stale >= self.patience

    def report(self):
        return dict(self.best, step=self.best_step, stale_checks=self.stale)
