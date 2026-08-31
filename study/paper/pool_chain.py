"""The pool-backstopped chain: the composition the measurements pointed at.

docs/PAPER.md §5.4 projected it arithmetically and declined to claim it: tier 1
serves its share far below the warm pool's per-program cost, so a chain whose
BACKSTOP is the pool rather than a cold CPython spawn should beat both. This
measures it instead of estimating it.

Arms, all over the same CPython-clean subset, each program in a fresh temp cwd,
graded against a per-program CPython reference by one instrument:

  cold-cpython    spawn python3 -c            the baseline a harness pays today
  warm-pool       the pool alone              correct by construction
  cold-chain      lypning run -c              what we ship
  pool-chain      liblypning, pool on refusal the composition
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from lypning import corpus, conformance as conf, engines as eng, pool as poolmod  # noqa: E402

ENV = dict(os.environ)
ENV.update({"PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8", "LYPNING_CAPTURE": "0"})
D = subprocess.DEVNULL
LYP = "/root/.lypning/bin/lypning"
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

    tmp = tempfile.mkdtemp()
    sock = os.path.join(tmp, "pool.sock")
    server = poolmod.Server(sock)
    failed = server.warm()
    server.open()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.3)
    W("pool warm (preload failures: %s)\n" % (failed or "none"))
    client = poolmod.Client(sock)
    eng.run_library("print(1)")                       # warm the dlopen

    def a_cold_cpython(prog, ref, cwd):
        rc, so = cpython(prog, cwd)
        return "match" if (rc == 0 and so == ref) else "diff"

    def _uni(t):
        return t.replace("\r\n", "\n").replace("\r", "\n")

    def b_warm_pool(prog, ref, cwd):
        r = client.run(prog, cwd=cwd, env=ENV)
        return "match" if (r["returncode"] == 0 and _uni(r["stdout"]) == ref) else "diff"

    def c_cold_chain(prog, ref, cwd):
        try:
            p = subprocess.run([LYP, "run", "-c", prog], capture_output=True, stdin=D,
                               text=True, errors="replace", timeout=20, cwd=cwd, env=ENV)
        except subprocess.TimeoutExpired:
            return "timeout"
        return "match" if (p.returncode == 0 and p.stdout == ref) else "diff"

    def d_pool_chain(prog, ref, cwd):
        r = eng.run_library(prog, cwd=cwd)
        if r.returncode == 90 and "unsupported:" in r.stderr:
            got = client.run(prog, cwd=cwd, env=ENV)
            return "match-via-pool" if (got["returncode"] == 0 and _uni(got["stdout"]) == ref) \
                else "diff-via-pool"
        if r.returncode == 0 and r.stdout == ref:
            return "match-tier1"
        return "diff-tier1"

    arms = [("cold-cpython", a_cold_cpython), ("warm-pool", b_warm_pool),
            ("cold-chain", c_cold_chain), ("pool-chain", d_pool_chain)]
    best, verdicts = {}, {}
    for rnd in range(3):
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
            W("  round %d %-14s %8.0f ms  %s\n" % (rnd + 1, name, ms, dict(cnt)))
    client.shutdown()

    n = len(clean)
    ref_ms = best["cold-cpython"]
    out = {"clean_n": n, "best_ms": {k: round(v, 1) for k, v in best.items()},
           "ms_per_program": {k: round(v / n, 3) for k, v in best.items()},
           "speedup_vs_cold_cpython": {k: round(ref_ms / v, 3) for k, v in best.items()},
           "verdicts": verdicts}
    print(json.dumps(out, indent=1))
    W("\nBEST-OF-3 over %d programs:\n" % n)
    for k in ("pool-chain", "warm-pool", "cold-chain", "cold-cpython"):
        matched = sum(v for key, v in verdicts[k].items() if key.startswith("match"))
        W("  %-14s %8.0f ms  %6.2f ms/prog  %5.2fx   correct %d/%d  %s\n"
          % (k, best[k], best[k] / n, ref_ms / best[k], matched, n, verdicts[k]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
