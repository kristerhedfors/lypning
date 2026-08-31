"""Five Python implementations, one instrument, one agent corpus.

CPython | PyPy | MicroPython | Monty | lypning (tier 1 and full chain)

Every program is run ONCE under the CPython reference, then under each engine,
in the same fresh temp cwd with the same environment, so compatibility and
wall-clock come from a single sweep. Per-program engine wall is recorded so
the paper can compute total agent wall-clock under a fallback policy.

Usage: python3 study/paper/bench5.py [--limit N] > results.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from lypning import corpus, conformance as conf  # noqa: E402

PYPY = os.environ.get("PYPY_BIN", "")
LYP = "/root/.lypning/bin/lypning"
MP = "/root/.lypning/bin/lypning-mp-i386"
TIMEOUT = 15

try:
    import pydantic_monty as pm
except ImportError:
    pm = None


def run_proc(cmd, prog, cwd, env):
    """Returns (rc, stdout, stderr, wall_ms). rc None on timeout."""
    t0 = time.perf_counter_ns()
    try:
        p = subprocess.run(cmd + ["-c", prog], capture_output=True, stdin=subprocess.DEVNULL, text=True,
                           errors="replace", timeout=TIMEOUT, cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        return None, "", "", (time.perf_counter_ns() - t0) / 1e6
    return p.returncode, p.stdout, p.stderr, (time.perf_counter_ns() - t0) / 1e6


def run_monty(pool, prog):
    """Monty has no process shape; time its in-process feed_run."""
    t0 = time.perf_counter_ns()
    try:
        col = pm.CollectStreams()
        with pool.checkout(limits=pm.ResourceLimits(max_duration_secs=TIMEOUT)) as s:
            s.feed_run(prog, print_callback=col)
        out = "".join(t for st, t in col.output if st == "stdout")
        rc = 0
    except Exception as exc:                       # any Monty error is a loud failure
        out, rc = "", type(exc).__name__
    return rc, out, "", (time.perf_counter_ns() - t0) / 1e6


def classify(ref_rc, ref_out, rc, out, err, engine):
    """One verdict vocabulary for every engine."""
    if rc is None:
        return "TIMEOUT"
    refused = (rc == 90 and "unsupported:" in err) if engine != "monty" else False
    if refused:
        return "BOTH-FAIL" if ref_rc != 0 else "UNSUPPORTED"
    failed = (rc != 0) if engine != "monty" else (rc != 0)
    if ref_rc != 0:
        return "BOTH-FAIL" if failed else "REF-FAIL-ENGINE-OK"
    if failed:
        return "LOUD-ERROR"
    return "MATCH" if out == ref_out else "SILENT-DIFF"


def main() -> int:
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    entries = [e for e in corpus.load_default()
               if not conf.absolute_paths(e.program) and not conf.is_nondeterministic(e)]
    if limit:
        entries = entries[:limit]
    sys.stderr.write("grading %d programs\n" % len(entries))

    engines = [("cpython", [sys.executable]),
               ("lypning-t1", [LYP]),
               ("lypning-chain", [LYP, "run"]),
               ("micropython", [MP])]
    if PYPY and os.path.exists(PYPY):
        engines.insert(1, ("pypy", [PYPY]))
    else:
        sys.stderr.write("PyPy not found (set PYPY_BIN) — skipping that arm\n")
    have_monty = pm is not None
    names = [n for n, _ in engines] + (["monty"] if have_monty else [])

    counts = {n: {} for n in names}
    walls = {n: [] for n in names}
    ref_walls = []
    diffs = {n: [] for n in names}
    pool = pm.Monty() if have_monty else None
    if pool:
        pool.__enter__()
        run_monty(pool, "print(1)")            # warm the pool out of the measurement

    t_start = time.time()
    for i, e in enumerate(entries):
        with tempfile.TemporaryDirectory() as cwd:
            env = dict(os.environ)
            env.update({"PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8",
                        "LYPNING_CAPTURE": "0",
                        "LYPNING_LOG": os.path.join(cwd, "capture.jsonl")})
            ref_rc, ref_out, _, ref_ms = run_proc([sys.executable], e.program, cwd, env)
            if ref_rc is None:
                continue
            ref_walls.append(ref_ms)
            for name, cmd in engines:
                if name == "cpython":
                    rc, out, err, ms = ref_rc, ref_out, "", ref_ms
                else:
                    rc, out, err, ms = run_proc(cmd, e.program, cwd, env)
                v = classify(ref_rc, ref_out, rc, out, err, name)
                counts[name][v] = counts[name].get(v, 0) + 1
                walls[name].append(ms)
                if v == "SILENT-DIFF" and len(diffs[name]) < 12:
                    diffs[name].append({"id": e.id[:12], "program": e.program[:200],
                                        "ref": ref_out[:120], "got": out[:120]})
            if have_monty:
                rc, out, err, ms = run_monty(pool, e.program)
                v = classify(ref_rc, ref_out, rc, out, err, "monty")
                counts["monty"][v] = counts["monty"].get(v, 0) + 1
                walls["monty"].append(ms)
                if v == "SILENT-DIFF" and len(diffs["monty"]) < 12:
                    diffs["monty"].append({"id": e.id[:12], "program": e.program[:200],
                                           "ref": ref_out[:120], "got": out[:120]})
        if (i + 1) % 250 == 0:
            sys.stderr.write("  ...%d/%d (%.0fs)\n" % (i + 1, len(entries), time.time() - t_start))
    if pool:
        pool.__exit__(None, None, None)

    graded = len(ref_walls)
    out = {"graded": graded, "corpus_entries": len(corpus.load_default()),
           "engines": names, "counts": counts, "sample_silent_diffs": diffs,
           "total_wall_ms": {n: round(sum(walls[n]), 1) for n in names},
           "median_wall_ms": {n: round(sorted(walls[n])[len(walls[n]) // 2], 3)
                              for n in names if walls[n]},
           "wall_ms_per_program": {n: walls[n] for n in names},
           "sweep_seconds": round(time.time() - t_start, 1)}
    print(json.dumps(out))

    w = sys.stderr.write
    w("\ngraded %d programs in %.0fs\n\n" % (graded, out["sweep_seconds"]))
    keys = ["MATCH", "BOTH-FAIL", "UNSUPPORTED", "LOUD-ERROR", "SILENT-DIFF",
            "TIMEOUT", "REF-FAIL-ENGINE-OK"]
    w("%-15s" % "engine" + "".join("%12s" % k[:11] for k in keys) + "%12s\n" % "wall(s)")
    for n in names:
        w("%-15s" % n + "".join("%12d" % counts[n].get(k, 0) for k in keys)
          + "%12.1f\n" % (out["total_wall_ms"][n] / 1000))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
