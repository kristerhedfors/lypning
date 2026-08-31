"""The experiments the hostile review said were owed and cheap. One serial run.

1. Exact zero check: match/walrus/async raw counts (the paper claims exact zeros)
2. False-refusal sweep: does any graded program exit 90 under plain CPython?
3. PyPy divergence family split: the three families, with per-family counts
4. Per-program paired walls: how many of the 745 are SLOWER under the chain
   than under cold CPython, and by how much (the missing table)
5. Tier-2 ablation: the chain with the MicroPython binary moved aside
"""
from __future__ import annotations

import ast
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from lypning import corpus, conformance as conf  # noqa: E402

PYPY = os.environ.get("PYPY_BIN", "")
LYP = "/root/.lypning/bin/lypning"
MP = "/root/.lypning/bin/lypning-mp-i386"
ENV = dict(os.environ)
ENV.update({"PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8", "LYPNING_CAPTURE": "0"})
W = sys.stderr.write
DEVNULL = subprocess.DEVNULL


def run(cmd, prog, cwd, timeout=20):
    t0 = time.perf_counter_ns()
    try:
        p = subprocess.run(cmd + ["-c", prog], capture_output=True, stdin=DEVNULL,
                           text=True, errors="replace", timeout=timeout, cwd=cwd, env=ENV)
        return p.returncode, p.stdout, p.stderr, (time.perf_counter_ns() - t0) / 1e6
    except subprocess.TimeoutExpired:
        return None, "", "", (time.perf_counter_ns() - t0) / 1e6


def main() -> int:
    out = {}
    entries = [e for e in corpus.load_default()
               if not conf.absolute_paths(e.program) and not conf.is_nondeterministic(e)]

    W("=== 1. exact zeros ===\n")
    zeros = collections.Counter()
    for e in corpus.load_default():
        try:
            tree = ast.parse(e.program)
        except SyntaxError:
            continue
        seen = {type(x).__name__ for x in ast.walk(tree)}
        for k, names in (("match", {"Match"}), ("walrus", {"NamedExpr"}),
                         ("async", {"AsyncFunctionDef", "Await", "AsyncFor", "AsyncWith"})):
            if names & seen:
                zeros[k] += 1
    out["exact_zeros"] = dict(zeros)
    W("  programs containing: %s (absent keys are exact zeros)\n" % (dict(zeros) or "{}"))

    W("\n=== 2. false-refusal sweep: CPython exiting 90 ===\n")
    fr = {"exit90": 0, "exit90_with_marker": 0}
    clean = []
    ref_out_by_id = {}
    ref_ms_by_id = {}
    for e in entries:
        with tempfile.TemporaryDirectory() as cwd:
            rc, so, se, ms = run([sys.executable], e.program, cwd)
        if rc == 90:
            fr["exit90"] += 1
            if "unsupported:" in se and so == "":
                fr["exit90_with_marker"] += 1
        if rc == 0:
            clean.append(e)
            ref_out_by_id[e.id] = so
            ref_ms_by_id[e.id] = ms
    out["false_refusal"] = fr
    out["clean_n"] = len(clean)
    W("  of %d graded: exit-90 %d, full refusal signature %d\n"
      % (len(entries), fr["exit90"], fr["exit90_with_marker"]))

    W("\n=== 3. PyPy divergence families over %d clean ===\n" % len(clean))
    fams = collections.Counter()
    if PYPY:
        for e in clean:
            with tempfile.TemporaryDirectory() as cwd:
                rc, so, se, _ = run([sys.executable], e.program, cwd)
                prc, pso, pse, _ = run([PYPY], e.program, cwd)
            if rc != 0 or prc != 0 or pso == so:
                continue
            src = e.program
            if "open(" in src and ("read" in src or "load" in src or "md5" in src
                                   or "sha" in src or "getsize" in src):
                fams["file-finalization"] += 1
            elif ("set(" in src or "{" in src) and (pso and so and sorted(pso) == sorted(so)):
                fams["iteration-order"] += 1
            elif "=" in src and ("chars=" in src or "keepends=" in src or "maxsplit=" in src
                                 or "kw" in src):
                fams["kwargs-accepted"] += 1
            else:
                fams["other"] += 1
    out["pypy_families"] = dict(fams)
    W("  %s   total %d\n" % (dict(fams), sum(fams.values())))

    W("\n=== 4. per-program paired walls: chain vs cold CPython ===\n")
    slower = []
    chain_ms = {}
    for e in clean:
        with tempfile.TemporaryDirectory() as cwd:
            rc, so, se, ms = run([LYP, "run"], e.program, cwd)
        chain_ms[e.id] = ms
    # pair against the reference milliseconds captured in pass 2
    deltas = []
    for e in clean:
        d = chain_ms[e.id] - ref_ms_by_id[e.id]
        deltas.append(d)
        if d > 0:
            slower.append((e.id, round(d, 2)))
    deltas.sort()
    n = len(deltas)
    out["paired"] = {
        "n": n,
        "chain_slower_count": len(slower),
        "chain_slower_pct": round(100.0 * len(slower) / n, 1),
        "delta_ms_median": round(deltas[n // 2], 2),
        "delta_ms_p90": round(deltas[int(.9 * n)], 2),
        "delta_ms_worst": round(deltas[-1], 2),
        "delta_ms_best": round(deltas[0], 2),
        "worst_10": sorted(slower, key=lambda t: -t[1])[:10],
    }
    W("  chain slower than CPython on %d of %d (%.1f%%)\n"
      % (len(slower), n, 100.0 * len(slower) / n))
    W("  delta ms (chain - cpython): median %.2f  p90 %.2f  worst +%.2f  best %.2f\n"
      % (out["paired"]["delta_ms_median"], out["paired"]["delta_ms_p90"],
         out["paired"]["delta_ms_worst"], out["paired"]["delta_ms_best"]))

    W("\n=== 5. tier-2 ablation: chain with MicroPython moved aside ===\n")
    aside = MP + ".aside"
    shutil.move(MP, aside)
    try:
        t0 = time.perf_counter_ns()
        counts = collections.Counter()
        for e in clean:
            with tempfile.TemporaryDirectory() as cwd:
                rc, so, se, _ = run([LYP, "run"], e.program, cwd)
            if rc == 0 and so == ref_out_by_id[e.id]:
                counts["match"] += 1
            elif rc == 0:
                counts["silent-diff"] += 1
            else:
                counts["fail"] += 1
        wall = (time.perf_counter_ns() - t0) / 1e6
    finally:
        shutil.move(aside, MP)
    out["ablation_no_tier2"] = {"counts": dict(counts), "wall_ms": round(wall, 1),
                                "ms_per_program": round(wall / len(clean), 3)}
    W("  without tier 2: %s  wall %.0f ms  %.2f ms/prog\n"
      % (dict(counts), wall, wall / len(clean)))
    W("  (with tier 2, same subset, earlier best-of-2: 8476 ms, 11.38 ms/prog)\n")

    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
