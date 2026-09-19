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

#: The eval-2 bank is frozen at 300 cases in `EVAL2.md` §11, but the binding
#: constraint is families, not cases: `data_loop --purpose benchmark` refuses
#: fewer than 18 independent semantic families, which a 9-family bank of 315
#: cases fails even though it clears the case target (measured 2026-09-18, job
#: 6aacca0ab1dc2b62dc590991, `benchmark needs at least 18 independent semantic
#: families`). Take whole families until BOTH are satisfied.
EVAL2_TARGET = 300
EVAL2_MIN_FAMILIES = 18


def main() -> int:
    root = Path(__file__).resolve().parent
    cases = [json.loads(l) for l in (root / "cases.jsonl").read_text(encoding="utf-8").splitlines() if l]

    by_family = defaultdict(list)
    for c in cases:
        by_family[c["family"]].append(c)

    # Split coverage by CAPABILITY, not by family. Families of one kind answer
    # the same shape of question and collide by the identical-stdout rule —
    # `file-firstline` matched `file-longestline`, `file-lastline`,
    # `file-sortlines` and `file-joinedcommas` across the split on 2026-09-18,
    # 96 pairs, because a first line is often also the longest. Keeping a whole
    # capability on one side removes the collision at its cause rather than
    # tuning a threshold.
    #
    # Controls are the exception: `fallback-control` is one capability, and
    # sending all of it to either side would leave the other with no control at
    # all. They are split by family, and only the ones whose output SHAPE is
    # distinctive go to the benchmark — a control printing a bare sum collides
    # with every sum-shaped training family.
    DISTINCTIVE = ("control-copy-roundtrip", "control-itertools-chain",
                   "control-statistics-median")
    by_capability = defaultdict(list)
    for fam, cs in by_family.items():
        by_capability[cs[0]["capabilities"][0]].append(fam)

    # Which capabilities, in order. Kinds that print a BARE INTEGER collide with
    # each other across the split by the identical-stdout rule whatever their
    # tasks say — `stdlib-collections-counter` matched `int-maximum` because the
    # most common value is often also the largest — so the benchmark is built
    # from the kinds whose answers are text, and the integer kinds stay in train
    # where a coincidence costs nothing.
    TEXTUAL_FIRST = ("string", "file", "text", "set-ops")
    caps = [c for c in by_capability if c != "fallback-control"]
    coverage_caps = ([c for c in TEXTUAL_FIRST if c in caps]
                     + sorted(c for c in caps if c not in TEXTUAL_FIRST))

    eval_families = [f for f in DISTINCTIVE if f in by_family]
    n = sum(len(by_family[f]) for f in eval_families)
    for cap in coverage_caps:
        if n >= EVAL2_TARGET and len(eval_families) >= EVAL2_MIN_FAMILIES:
            break
        for fam in sorted(by_capability[cap]):
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
    if report["eval2"]["families"] < EVAL2_MIN_FAMILIES:
        print("eval2 has %d families; the benchmark floor is %d"
              % (report["eval2"]["families"], EVAL2_MIN_FAMILIES), file=sys.stderr)
        return 1
    for side in ("train", "eval2"):
        if len(report[side]["populations"]) < 2:
            print("%s has only one population; controls must be on both sides" % side,
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
