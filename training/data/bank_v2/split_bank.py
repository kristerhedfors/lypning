"""Split the authored bank into a training bank and a disjoint eval-2 benchmark.

The split is BY FAMILY and nothing else. Cases inside a family share a base task
and differ only in the parameter, so a family straddling the two banks would put
near-twins on both sides of the measurement — which is the leak `eval2-leaks`
exists to catch, and which would quietly inflate the benchmark.

Both banks keep both populations. A benchmark with no fallback-control cases
cannot tell "learned to serve more natively" from "learned to avoid imports",
which is the confusion the controls exist to prevent.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

#: The eval-2 bank is frozen at 300 in `EVAL2.md` §11; take the smallest set of
#: whole families that reaches it rather than trimming a family to hit a number.
EVAL2_TARGET = 300


def main() -> int:
    root = Path(__file__).resolve().parent
    cases = [json.loads(l) for l in (root / "cases.jsonl").read_text(encoding="utf-8").splitlines() if l]

    by_family = defaultdict(list)
    for c in cases:
        by_family[c["family"]].append(c)

    # Deterministic order, and controls first so the benchmark is guaranteed
    # some: they are the scarcer population and the one a greedy pass would
    # otherwise leave entirely in train.
    #
    # Which controls, though, is not arbitrary. A control that prints a bare sum
    # collides with every sum-shaped training family by the identical-stdout
    # rule — `control-array-sum` alone produced all 51 stdout leaks on
    # 2026-09-18 — so the benchmark takes the controls whose output SHAPE is
    # distinctive (a comma-joined list, a doubled list, a median that can be
    # fractional) and leaves the bare-integer ones in train, where a collision
    # costs nothing.
    DISTINCTIVE = ("control-copy-roundtrip", "control-itertools-chain",
                   "control-statistics-median")
    control = [f for f in DISTINCTIVE if f in by_family] + sorted(
        f for f, cs in by_family.items()
        if cs[0]["population"] == "fallback-control" and f not in DISTINCTIVE)
    coverage = sorted(f for f, cs in by_family.items()
                      if cs[0]["population"] == "coverage")

    eval_families, n = [], 0
    for fam in control[:3] + coverage:
        if n >= EVAL2_TARGET:
            break
        eval_families.append(fam)
        n += len(by_family[fam])

    eval_set = set(eval_families)
    eval_cases = [c for c in cases if c["family"] in eval_set]
    train_cases = [c for c in cases if c["family"] not in eval_set]

    for name, rows in (("train.jsonl", train_cases), ("eval2.jsonl", eval_cases)):
        (root / name).write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

    def describe(rows):
        return {"cases": len(rows), "families": len({r["family"] for r in rows}),
                "populations": dict(Counter(r["population"] for r in rows))}

    overlap = {r["family"] for r in train_cases} & {r["family"] for r in eval_cases}
    report = {"train": describe(train_cases), "eval2": describe(eval_cases),
              "family_overlap": sorted(overlap),
              "task_overlap": len({r["task"] for r in train_cases}
                                  & {r["task"] for r in eval_cases})}
    print(json.dumps(report, indent=2))
    if overlap or report["task_overlap"]:
        print("a family or task on both sides is a leak, not a split", file=sys.stderr)
        return 1
    for side in ("train", "eval2"):
        if len(report[side]["populations"]) < 2:
            print("%s has only one population; controls must be on both sides" % side,
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
