"""Dynamic profile — where the wall-clock of an agent's Python run actually goes.

For every CPython-clean corpus program, decomposes one `python3 prog.py` into
  spawn+interpreter-start | parse (compile()) | execute (exec())
by running an in-process timer inside the child and subtracting from the
parent's spawn-to-reap wall. Programs run in a fresh temp cwd (invariant 4).

Emits JSON on stdout, a human summary on stderr.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile

sys.path.insert(0, "src")
from lypning import corpus, conformance as conf  # noqa: E402

RUNNER = r'''
import json, os, sys, time
src = open(sys.argv[1], "rb").read().decode("utf-8", "replace")
out = sys.argv[2]
devnull = open(os.devnull, "w")
real_stdout, sys.stdout = sys.stdout, devnull
t0 = time.perf_counter_ns()
try:
    code = compile(src, "prog.py", "exec")
except BaseException:
    t1 = time.perf_counter_ns()
    json.dump({"parse_ns": t1 - t0, "exec_ns": 0, "ok": False}, open(out, "w"))
    raise SystemExit(0)
t1 = time.perf_counter_ns()
ok = True
try:
    exec(code, {"__name__": "__main__", "__file__": "prog.py"})
except BaseException:
    ok = False
t2 = time.perf_counter_ns()
sys.stdout = real_stdout
json.dump({"parse_ns": t1 - t0, "exec_ns": t2 - t1, "ok": ok}, open(out, "w"))
'''


def _stat(xs, scale=1.0):
    xs = sorted(x * scale for x in xs)
    n = len(xs)
    def q(p):
        return round(xs[min(n - 1, int(p * n))], 4)
    return {"n": n, "median": q(.5), "p75": q(.75), "p90": q(.9),
            "p99": q(.99), "max": round(xs[-1], 4),
            "mean": round(statistics.fmean(xs), 4)}


def main() -> int:
    env = dict(os.environ)
    env.update({"PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8", "LYPNING_CAPTURE": "0"})
    entries = [e for e in corpus.load_default()
               if not conf.absolute_paths(e.program) and not conf.is_nondeterministic(e)]
    sys.stderr.write("candidates: %d\n" % len(entries))

    with tempfile.TemporaryDirectory() as home:
        runner = os.path.join(home, "runner.py")
        open(runner, "w").write(RUNNER)
        parse, execute, overhead, wall = [], [], [], []
        clean = 0
        for e in entries:
            with tempfile.TemporaryDirectory() as cwd:
                prog = os.path.join(cwd, "prog.py")
                open(prog, "w").write(e.program)
                res = os.path.join(cwd, "_t.json")
                import time as _t
                t0 = _t.perf_counter_ns()
                try:
                    p = subprocess.run([sys.executable, runner, prog, res],
                                       capture_output=True, timeout=20, cwd=cwd, env=env)
                except subprocess.TimeoutExpired:
                    continue
                w_ms = (_t.perf_counter_ns() - t0) / 1e6
                if p.returncode != 0 or not os.path.exists(res):
                    continue
                try:
                    d = json.load(open(res))
                except Exception:
                    continue
            if not d.get("ok"):
                continue
            clean += 1
            pm, em = d["parse_ns"] / 1e6, d["exec_ns"] / 1e6
            parse.append(pm); execute.append(em); wall.append(w_ms)
            overhead.append(max(0.0, w_ms - pm - em))

    out = {"clean_programs": clean,
           "wall_ms": _stat(wall), "parse_ms": _stat(parse),
           "exec_ms": _stat(execute), "startup_overhead_ms": _stat(overhead),
           "totals_ms": {"wall": round(sum(wall), 1), "parse": round(sum(parse), 1),
                         "exec": round(sum(execute), 1),
                         "overhead": round(sum(overhead), 1)},
           "exec_under_1ms": sum(1 for x in execute if x < 1.0),
           "exec_under_10ms": sum(1 for x in execute if x < 10.0)}
    print(json.dumps(out, indent=1))
    w = sys.stderr.write
    t = out["totals_ms"]
    w("\nclean programs: %d\n" % clean)
    w("per-program medians: wall %.2f ms | parse %.3f ms | exec %.3f ms | startup+spawn %.2f ms\n"
      % (out["wall_ms"]["median"], out["parse_ms"]["median"],
         out["exec_ms"]["median"], out["startup_overhead_ms"]["median"]))
    w("aggregate share of total wall: startup+spawn %.1f%% | parse %.1f%% | exec %.1f%%\n"
      % (100 * t["overhead"] / t["wall"], 100 * t["parse"] / t["wall"],
         100 * t["exec"] / t["wall"]))
    w("programs whose EXEC is under 1 ms: %d (%.1f%%); under 10 ms: %d (%.1f%%)\n"
      % (out["exec_under_1ms"], 100 * out["exec_under_1ms"] / clean,
         out["exec_under_10ms"], 100 * out["exec_under_10ms"] / clean))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
