"""What the prompt bought, in milliseconds.

Tier-1 coverage is a proxy. The thing a user gets is a session that costs less,
and the two are not the same number: a program that stays in the subset runs
**in the dispatcher's own process** with no second spawn, while one that leaves
it pays a wasted classification plus the full CPython price. So this times the
same 26 programs, per treatment, through the mixture and through CPython, and
reports what the difference actually is.

Min of ``--repeat`` runs per program, arms interleaved per program so a load
spike lands on both. Every run gets its own temp cwd with the task's fixtures,
same as :mod:`study.harness` — a timing pass over agent-written programs is
still a corpus run, and CLAUDE.md invariant 4 does not stop applying because
the question changed.

This is a wall-clock benchmark on a shared machine: quote the ratio, re-run
rather than remember the milliseconds, and do not put it in CI.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as H  # noqa: E402

OUT = H.STUDY / "data" / "timing.json"


def time_one(program: str, task: dict, engine: str, repeat: int) -> float:
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        H.run_program(program, task, engine)
        best = min(best, (time.perf_counter() - t0) * 1000.0)
    return best


def main(argv) -> int:
    repeat = 3
    for i, a in enumerate(argv):
        if a == "--repeat" and i + 1 < len(argv):
            repeat = int(argv[i + 1])
    tasks = {t["id"]: t for t in H.load_tasks()}
    rows = [json.loads(ln) for ln in
            (H.STUDY / "data" / "results.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]

    before = H.git_snapshot()
    per_cell: dict = defaultdict(lambda: {"mixture": 0.0, "cpython": 0.0, "n": 0})
    for i, r in enumerate(rows, 1):
        t = tasks[r["task"]]
        key = (r["treatment"], r["replicate"])
        # Interleaved: the mixture and CPython see the same machine.
        mix = time_one(r["program"], t, "mixture", repeat)
        cpy = time_one(r["program"], t, "cpython", repeat)
        per_cell[key]["mixture"] += mix
        per_cell[key]["cpython"] += cpy
        per_cell[key]["n"] += 1
        if i % 100 == 0:
            print("  %d/%d" % (i, len(rows)), file=sys.stderr)
    changed = H.check_net(before, H.git_snapshot())
    if changed:
        print("\nNET TRIPPED — the timing pass changed tracked files:\n  %s"
              % "\n  ".join(changed), file=sys.stderr)

    by_t: dict = defaultdict(list)
    for (tid, rep), v in per_cell.items():
        by_t[tid].append(v)
    out = {}
    for tid, cells in by_t.items():
        mix = statistics.fmean(c["mixture"] for c in cells)
        cpy = statistics.fmean(c["cpython"] for c in cells)
        out[tid] = {"cells": len(cells), "programs_per_cell": cells[0]["n"],
                    "mixture_ms": round(mix, 1), "cpython_ms": round(cpy, 1),
                    "ratio": round(mix / cpy, 3) if cpy else None}
    OUT.write_text(json.dumps({"repeat": repeat, "treatments": out}, indent=2) + "\n",
                   encoding="utf-8")

    print("\n| id | treatment | programs | mixture | cpython | vs cpython |")
    print("|---|---|---:|---:|---:|---:|")
    treatments = json.loads((H.STUDY / "treatments.json").read_text(encoding="utf-8"))
    for tid in sorted(out, key=lambda k: int(k[1:])):
        v = out[tid]
        print("| %s | %s | %d | %.1f ms | %.1f ms | %.3fx |"
              % (tid, treatments[tid]["label"], v["programs_per_cell"],
                 v["mixture_ms"], v["cpython_ms"], v["ratio"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
