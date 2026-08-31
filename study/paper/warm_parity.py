"""Warm-shape parity: the Monty stack against the lypning stack, symmetrically.

The review's one asymmetry: COMPARISON.md gave Monty its warm in-process shape
but lypning only its cold-spawn shape, though a warm lypning arm (the C ABI)
exists. This grades AND times four deployment shapes over the same clean subset
with one instrument, two interleaved rounds:

  monty-warm       pydantic_monty pool, fresh checkout+feed per program
  liblyp-warm      liblypning in-process, per-program temp cwd
  warm-chain       liblypning first; on refusal, one cold CPython spawn
  cold-cpython     the reference shape

Correctness is graded against a fresh CPython reference run per program.
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from lypning import corpus, conformance as conf, engines as eng  # noqa: E402

import pydantic_monty as pm  # noqa: E402

ENV = dict(os.environ)
ENV.update({"PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8", "LYPNING_CAPTURE": "0"})
D = subprocess.DEVNULL
W = sys.stderr.write


def cpython(prog, cwd):
    try:
        p = subprocess.run([sys.executable, "-c", prog], capture_output=True, stdin=D,
                           text=True, errors="replace", timeout=20, cwd=cwd, env=ENV)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return None, ""


def main() -> int:
    entries = [e for e in corpus.load_default()
               if not conf.absolute_paths(e.program) and not conf.is_nondeterministic(e)]
    clean = []
    for e in entries:
        with tempfile.TemporaryDirectory() as cwd:
            rc, so = cpython(e.program, cwd)
        if rc == 0:
            clean.append((e.program, so))
    W("clean subset: %d\n" % len(clean))

    eng.run_library("print(1)")                      # warm the dlopen
    pool = pm.Monty(); pool.__enter__()
    try:
        with pool.checkout() as s:                   # warm the pool
            s.feed_run("print(1)", print_callback=pm.CollectStreams())

        def monty_arm(prog, ref, cwd):
            try:
                col = pm.CollectStreams()
                with pool.checkout(limits=pm.ResourceLimits(max_duration_secs=20)) as s:
                    s.feed_run(prog, print_callback=col)
                out = "".join(t for st, t in col.output if st == "stdout")
                return "match" if out == ref else "silent-diff"
            except Exception:
                return "loud-error"

        def liblyp_arm(prog, ref, cwd):
            r = eng.run_library(prog, cwd=cwd)
            if r.returncode == 90 and "unsupported:" in r.stderr:
                return "refused"
            if r.returncode == 0 and r.stdout == ref:
                return "match"
            return "silent-diff" if r.returncode == 0 else "loud-error"

        def warm_chain_arm(prog, ref, cwd):
            r = eng.run_library(prog, cwd=cwd)
            if r.returncode == 90 and "unsupported:" in r.stderr:
                rc, so = cpython(prog, cwd)          # in-stack fallback, one spawn
                return "match" if (rc == 0 and so == ref) else "fallback-diff"
            if r.returncode == 0 and r.stdout == ref:
                return "match"
            return "silent-diff" if r.returncode == 0 else "loud-error"

        def cold_cpython_arm(prog, ref, cwd):
            rc, so = cpython(prog, cwd)
            return "match" if (rc == 0 and so == ref) else "diff"

        arms = [("monty-warm", monty_arm), ("liblyp-warm", liblyp_arm),
                ("warm-chain", warm_chain_arm), ("cold-cpython", cold_cpython_arm)]
        best = {}
        verdicts = {}
        for rnd in range(2):
            for name, fn in arms:
                cnt = collections.Counter()
                t0 = time.perf_counter_ns()
                for prog, ref in clean:
                    with tempfile.TemporaryDirectory() as cwd:
                        cnt[fn(prog, ref, cwd)] += 1
                ms = (time.perf_counter_ns() - t0) / 1e6
                if ms < best.get(name, float("inf")):
                    best[name] = ms
                    verdicts[name] = dict(cnt)
                W("  round %d %-13s %8.0f ms  %s\n" % (rnd + 1, name, ms, dict(cnt)))
    finally:
        pool.__exit__(None, None, None)

    n = len(clean)
    out = {"clean_n": n,
           "best_ms": {k: round(v, 1) for k, v in best.items()},
           "ms_per_program": {k: round(v / n, 3) for k, v in best.items()},
           "verdicts": verdicts}
    print(json.dumps(out, indent=1))
    W("\nBEST-OF-2 over %d programs (temp-cwd churn included in every arm):\n" % n)
    ref = best["cold-cpython"]
    for k in ("liblyp-warm", "warm-chain", "monty-warm", "cold-cpython"):
        W("  %-13s %8.0f ms  %6.2f ms/prog  %5.2fx vs cold CPython   %s\n"
          % (k, best[k], best[k] / n, ref / best[k], verdicts[k]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
