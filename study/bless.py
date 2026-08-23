"""Derive each task's expected stdout from its reference solution, and check
which tasks the Rust subset can serve at all.

A task's ``expect_stdout`` is not typed by hand — it is what CPython printed
when it ran the reference program, in the same temp cwd, with the same stdin and
argv every generated program will get. Typing it by hand is how a battery
acquires a task that nothing can pass.

``tier1_feasible`` is measured the same way: the reference is run on the Rust
core, and the task is feasible if *that* run matched. It is a property of the
task and the engine, never of a prompt, and it is what turns a raw percentage
into a ceiling a prompt could actually reach.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as H  # noqa: E402


def main() -> int:
    tasks = H.load_tasks()
    out = []
    bad = 0
    for t in tasks:
        ref = t["reference"]
        cp = H.run_program(ref, t, "cpython")
        if cp["exit"] != 0:
            print("FAIL %-20s reference did not run under CPython: %s"
                  % (t["id"], cp["stderr"].strip().splitlines()[-1:]))
            bad += 1
            continue
        t["expect_stdout"] = cp["stdout"]
        got = H.run_program(ref, t, "lypning")
        v = H.verdict_for(cp, got)
        t["tier1_feasible"] = v == H.MATCH
        t["reference_verdict"] = v
        r = H.route(ref)
        t["reference_route"] = r.get("engine", "?")
        note = ""
        if v == H.UNSUPPORTED:
            note = (got["stderr"].strip().splitlines() or [""])[-1]
        elif v == H.MISMATCH:
            note = "MISMATCH — lypning exit %s" % got["exit"]
            bad += 1
        print("%-20s %-9s route=%-10s %s" % (t["id"], v, t["reference_route"], note))
        out.append(t)
    H.TASKS.write_text("".join(json.dumps(t) + "\n" for t in out), encoding="utf-8")
    feasible = sum(1 for t in out if t["tier1_feasible"])
    print("\n%d tasks blessed — %d tier-1 feasible (%.1f%% ceiling), %d not"
          % (len(out), feasible, 100.0 * feasible / len(out), len(out) - feasible))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
