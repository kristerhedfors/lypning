"""The decisive measurement run: every remaining number the paper claims.

Run on a quiet machine, serially, in one process, so the arms share conditions.
Sections:
  1. clean subset, with timeouts counted (a dropped program must be visible)
  2. cold start per engine, and PyPy's startup/execute split -- this decides
     whether PyPy's corpus loss is JIT warmup or fixed interpreter startup
  3. warm-pool baselines: pre-warmed CPython forking per program, and PyPy too
  4. exclusion bias: the static profile of the SKIPPED entries vs the retained
  5. self-hosting sensitivity: the sweep with lypning-importing entries removed
Emits JSON on stdout, a human report on stderr.
"""
from __future__ import annotations

import ast
import collections
import json
import os
import statistics
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


def spawn_ms(cmd, reps=40):
    xs = []
    for _ in range(reps):
        t0 = time.perf_counter_ns()
        subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, env=ENV)
        xs.append((time.perf_counter_ns() - t0) / 1e6)
    return {"median": round(statistics.median(xs), 3), "min": round(min(xs), 3)}


def clean_subset():
    """Programs CPython runs cleanly in a fresh temp cwd. Timeouts counted."""
    keep, stats = [], collections.Counter()
    for e in corpus.load_default():
        if conf.absolute_paths(e.program):
            stats["skip-abspath"] += 1
            continue
        if conf.is_nondeterministic(e):
            stats["skip-nondet"] += 1
            continue
        stats["graded"] += 1
        with tempfile.TemporaryDirectory() as cwd:
            try:
                p = subprocess.run([sys.executable, "-c", e.program], capture_output=True, stdin=subprocess.DEVNULL,
                                   timeout=20, cwd=cwd, env=ENV)
            except subprocess.TimeoutExpired:
                stats["ref-timeout"] += 1
                continue
        if p.returncode == 0:
            keep.append(e)
            stats["clean"] += 1
        else:
            stats["both-fail"] += 1
    return keep, stats


def sweep_cold(cmd, entries):
    t0 = time.perf_counter_ns()
    to = 0
    for e in entries:
        with tempfile.TemporaryDirectory() as cwd:
            try:
                subprocess.run(cmd + ["-c", e.program], capture_output=True, stdin=subprocess.DEVNULL,
                               timeout=20, cwd=cwd, env=ENV)
            except subprocess.TimeoutExpired:
                to += 1
    return (time.perf_counter_ns() - t0) / 1e6, to


def sweep_fork(entries, interp=None):
    """A pre-warmed interpreter forking per program: the warm-pool baseline.

    interp None means this process (CPython). The parent has already paid
    interpreter startup; each program costs fork + compile + exec + wait.
    """
    devnull = os.open(os.devnull, os.O_WRONLY)
    devnull_r = os.open(os.devnull, os.O_RDONLY)
    t0 = time.perf_counter_ns()
    for e in entries:
        cwd = tempfile.mkdtemp()
        pid = os.fork()
        if pid == 0:
            try:
                os.dup2(devnull, 1); os.dup2(devnull, 2)
                os.dup2(devnull_r, 0)          # same EOF stdin as the spawn arms
                os.chdir(cwd)
                exec(compile(e.program, "prog.py", "exec"),
                     {"__name__": "__main__", "__file__": "prog.py"})
            except BaseException:
                pass
            finally:
                os._exit(0)
        os.waitpid(pid, 0)
        subprocess.run(["rm", "-rf", cwd], capture_output=True)
    return (time.perf_counter_ns() - t0) / 1e6


def static_shape(programs):
    feat = collections.Counter()
    calls = collections.Counter()
    n = 0
    for src in programs:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        n += 1
        seen = {type(x).__name__ for x in ast.walk(tree)}
        for k, names in (("loop", {"For", "While"}),
                         ("comprehension", {"ListComp", "SetComp", "DictComp", "GeneratorExp"}),
                         ("function-def", {"FunctionDef"}), ("class-def", {"ClassDef"}),
                         ("try-except", {"Try"}), ("with", {"With"})):
            if names & seen:
                feat[k] += 1
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                calls[node.func.id] += 1
    if not n:
        return None
    return {"n": n, "features_pct": {k: round(100.0 * v / n, 1) for k, v in feat.items()},
            "open_sites_per_program": round(calls["open"] / n, 3),
            "top_calls": calls.most_common(8)}


def main() -> int:
    out = {}

    W("=== 1. clean subset ===\n")
    clean, stats = clean_subset()
    out["subset"] = dict(stats)
    W("  %s\n" % dict(stats))

    W("\n=== 2. cold start, print(1) ===\n")
    cold = {"cpython": spawn_ms([sys.executable, "-c", "print(1)"]),
            "lypning": spawn_ms([LYP, "-c", "print(1)"]),
            "lypning-mp": spawn_ms([MP, "-c", "print(1)"])}
    if PYPY:
        cold["pypy"] = spawn_ms([PYPY, "-c", "print(1)"])
    out["cold_start_ms"] = cold
    for k, v in cold.items():
        W("  %-12s median %7.3f ms   min %7.3f ms\n" % (k, v["median"], v["min"]))
    if PYPY:
        base = cold["pypy"]["median"] - cold["cpython"]["median"]
        W("  PyPy pays %+.2f ms MORE than CPython on an empty program:\n"
          "    that is fixed interpreter startup, present before any JIT warms.\n" % base)

    W("\n=== 3. warm pool vs cold spawn, over %d clean programs ===\n" % len(clean))
    arms = [("cold-cpython", lambda: sweep_cold([sys.executable], clean)[0]),
            ("fork-cpython (warm pool)", lambda: sweep_fork(clean)),
            ("cold-lypning-chain", lambda: sweep_cold([LYP, "run"], clean)[0]),
            ("cold-lypning-t1", lambda: sweep_cold([LYP], clean)[0])]
    if PYPY:
        arms.insert(1, ("cold-pypy", lambda: sweep_cold([PYPY], clean)[0]))
    best = {}
    for r in range(2):
        for name, fn in arms:
            ms = fn()
            best[name] = min(best.get(name, float("inf")), ms)
            W("  round %d %-26s %8.0f ms\n" % (r + 1, name, ms))
    out["sweep_best_ms"] = {k: round(v, 1) for k, v in best.items()}
    out["sweep_ms_per_program"] = {k: round(v / len(clean), 3) for k, v in best.items()}
    W("\n  BEST-OF-2, %d programs:\n" % len(clean))
    ref = best["cold-cpython"]
    for k, v in sorted(best.items(), key=lambda kv: kv[1]):
        W("    %-26s %8.0f ms  %6.2f ms/prog  %5.2fx vs cold CPython\n"
          % (k, v, v / len(clean), ref / v))

    W("\n=== 4. exclusion bias: skipped vs retained ===\n")
    skipped_abs, retained = [], []
    for e in corpus.load_default():
        if conf.absolute_paths(e.program):
            skipped_abs.append(e.program)
        elif not conf.is_nondeterministic(e):
            retained.append(e.program)
    out["exclusion_bias"] = {"skipped_abspath": static_shape(skipped_abs),
                             "retained": static_shape(retained)}
    for tag in ("skipped_abspath", "retained"):
        s = out["exclusion_bias"][tag]
        W("  %-18s n=%4d  open sites/program %.3f  features %s\n"
          % (tag, s["n"], s["open_sites_per_program"], s["features_pct"]))

    W("\n=== 5. self-hosting sensitivity (drop entries importing lypning) ===\n")
    def imports_lypning(src):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name.split(".")[0] == "lypning" for a in node.names):
                    return True
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] == "lypning":
                    return True
        return False

    counts = {"all": collections.Counter(), "no-self": collections.Counter()}
    for e in clean:
        self_host = imports_lypning(e.program)
        with tempfile.TemporaryDirectory() as cwd:
            env = dict(ENV); env["LYPNING_LOG"] = os.path.join(cwd, "c.jsonl")
            try:
                ref = subprocess.run([sys.executable, "-c", e.program], capture_output=True, stdin=subprocess.DEVNULL,
                                     text=True, errors="replace", timeout=20, cwd=cwd, env=env)
                got = subprocess.run([LYP, "-c", e.program], capture_output=True, stdin=subprocess.DEVNULL, text=True,
                                     errors="replace", timeout=20, cwd=cwd, env=env)
            except subprocess.TimeoutExpired:
                continue
        if ref.returncode != 0:
            continue
        if got.returncode == 90 and "unsupported:" in got.stderr:
            v = "refused"
        elif got.returncode == 0 and got.stdout == ref.stdout:
            v = "match"
        elif got.returncode == 0:
            v = "silent-diff"
        else:
            v = "loud-error"
        counts["all"][v] += 1
        if not self_host:
            counts["no-self"][v] += 1
    out["self_hosting"] = {k: dict(v) for k, v in counts.items()}
    for tag in ("all", "no-self"):
        c = counts[tag]
        tot = sum(c.values()) or 1
        W("  %-8s n=%4d  match %4d (%.1f%%)  refused %4d  silent-diff %d\n"
          % (tag, tot, c["match"], 100.0 * c["match"] / tot, c["refused"], c["silent-diff"]))

    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
