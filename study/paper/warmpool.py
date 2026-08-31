"""The warm-pool baseline — the honest competitor to a faster cold interpreter.

If the cost of an agent's Python is interpreter startup, the obvious answer is
not a new engine but a warm one: keep an interpreter alive and fork per
program. This measures that directly, so the paper cannot be accused of
beating a strawman.

Arms:
  cold-cpython   spawn `python3 -c prog`            (what harnesses do today)
  fork-cpython   pre-warmed CPython, fork per prog  (kernel-pool equivalent)
  cold-lypning   spawn `lypning run -c prog`        (the chain)
  lib-lypning    liblypning in-process, no spawn    (the library arm)
Every arm runs each program in a fresh temp cwd.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from lypning import corpus, conformance as conf, engines as eng  # noqa: E402

ENV = dict(os.environ)
ENV.update({"PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8", "LYPNING_CAPTURE": "0"})


def clean_programs():
    """The CPython-clean subset: programs that run cleanly in the sandbox."""
    out = []
    for e in corpus.load_default():
        if conf.absolute_paths(e.program) or conf.is_nondeterministic(e):
            continue
        with tempfile.TemporaryDirectory() as cwd:
            try:
                p = subprocess.run([sys.executable, "-c", e.program], capture_output=True,
                                   timeout=15, cwd=cwd, env=ENV)
            except subprocess.TimeoutExpired:
                continue
        if p.returncode == 0:
            out.append(e.program)
    return out


def cold(cmd, progs):
    t0 = time.perf_counter_ns()
    for prog in progs:
        with tempfile.TemporaryDirectory() as cwd:
            try:
                subprocess.run(cmd + ["-c", prog], capture_output=True, timeout=15,
                               cwd=cwd, env=ENV)
            except subprocess.TimeoutExpired:
                pass
    return (time.perf_counter_ns() - t0) / 1e6


def fork_server(progs):
    """A warm CPython: the interpreter is already up; fork isolates each run.

    This is the fair 'why not just keep an interpreter warm' baseline. The
    parent pays interpreter startup once; each program costs a fork, an exec
    of already-parsed source, and a wait.
    """
    devnull = os.open(os.devnull, os.O_WRONLY)
    # Pre-compile is NOT done here: a pool receives source at request time,
    # so parse cost stays inside the measured region, as it does for the
    # spawn arms.
    t0 = time.perf_counter_ns()
    for prog in progs:
        cwd = tempfile.mkdtemp()
        pid = os.fork()
        if pid == 0:                                  # child: isolated run
            try:
                os.dup2(devnull, 1); os.dup2(devnull, 2)
                os.chdir(cwd)
                exec(compile(prog, "prog.py", "exec"),
                     {"__name__": "__main__", "__file__": "prog.py"})
            except BaseException:
                pass
            finally:
                os._exit(0)
        os.waitpid(pid, 0)
        subprocess.run(["rm", "-rf", cwd], capture_output=True)
    return (time.perf_counter_ns() - t0) / 1e6


def library(progs):
    eng.run_library("print(1)")                       # warm the dlopen
    t0 = time.perf_counter_ns()
    served = 0
    for prog in progs:
        cwd = tempfile.mkdtemp()
        old = os.getcwd()
        try:
            os.chdir(cwd)
            r = eng.run_library(prog)
            if r is not None and getattr(r, "ok", False):
                served += 1
        except Exception:
            pass
        finally:
            os.chdir(old)
            subprocess.run(["rm", "-rf", cwd], capture_output=True)
    return (time.perf_counter_ns() - t0) / 1e6, served


def main() -> int:
    progs = clean_programs()
    sys.stderr.write("clean programs: %d\n" % len(progs))
    rounds = 2
    best = {}
    arms = [
        ("cold-cpython", lambda: cold([sys.executable], progs)),
        ("fork-cpython", lambda: fork_server(progs)),
        ("cold-lypning-chain", lambda: cold(["/root/.lypning/bin/lypning", "run"], progs)),
        ("cold-lypning-t1", lambda: cold(["/root/.lypning/bin/lypning"], progs)),
    ]
    for r in range(rounds):
        for name, fn in arms:
            ms = fn()
            best[name] = min(best.get(name, float("inf")), ms)
            sys.stderr.write("  round %d %-20s %8.0f ms\n" % (r + 1, name, ms))
    out = {"clean_programs": len(progs),
           "best_ms": {k: round(v, 1) for k, v in best.items()},
           "ms_per_program": {k: round(v / len(progs), 3) for k, v in best.items()}}
    print(json.dumps(out, indent=1))
    sys.stderr.write("\nBEST-OF-%d over %d programs:\n" % (rounds, len(progs)))
    base = best["cold-cpython"]
    for k, v in sorted(best.items(), key=lambda kv: kv[1]):
        sys.stderr.write("  %-20s %8.0f ms  %6.2f ms/prog  %5.2fx vs cold CPython\n"
                         % (k, v, v / len(progs), base / v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
