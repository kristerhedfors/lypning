"""The deployment-shape metric: total wall to run every CPython-clean corpus
program on each system in its natural shape. This is what an agent session
pays; it is spawn-bound by construction, which is the population's own shape.
"""
import os, subprocess, sys, tempfile, time
sys.path.insert(0, "/home/user/lypning/src")
import pydantic_monty as pm
from lypning import corpus, conformance as conf

ENV = dict(os.environ); ENV.update({"LYPNING_CAPTURE": "0", "PYTHONHASHSEED": "0",
                                    "LC_ALL": "C.UTF-8"})
entries = [e for e in corpus.load_default()
           if not conf.absolute_paths(e.program) and not conf.is_nondeterministic(e)]
# The CPython-clean subset: programs that ran cleanly in the sandbox.
clean = []
for e in entries:
    with tempfile.TemporaryDirectory() as cwd:
        try:
            p = subprocess.run([sys.executable, "-c", e.program], capture_output=True,
                               text=True, timeout=10, cwd=cwd, env=ENV)
        except subprocess.TimeoutExpired:
            continue
    if p.returncode == 0:
        clean.append(e.program)
print("clean subset:", len(clean), flush=True)

def total_proc(cmd_prefix):
    t0 = time.perf_counter_ns()
    for prog in clean:
        with tempfile.TemporaryDirectory() as cwd:
            subprocess.run(cmd_prefix + [prog], capture_output=True, timeout=20,
                           cwd=cwd, env=ENV)
    return (time.perf_counter_ns() - t0) / 1e6

def total_monty():
    t0 = time.perf_counter_ns()
    with pm.Monty() as pool:
        for prog in clean:
            try:
                with pool.checkout(limits=pm.ResourceLimits(max_duration_secs=15)) as s:
                    s.feed_run(prog, print_callback=pm.CollectStreams())
            except Exception:
                pass
    return (time.perf_counter_ns() - t0) / 1e6

# Interleave the arms across 3 rounds; report the best round (least-loaded).
arms = {
    "cpython -c":        lambda: total_proc([sys.executable, "-c"]),
    "lypning run -c":    lambda: total_proc(["/root/.lypning/bin/lypning", "run", "-c"]),
    "lypning -c (tier1)": lambda: total_proc(["/root/.lypning/bin/lypning", "-c"]),
    "monty warm pool":   total_monty,
}
best = {k: float("inf") for k in arms}
for r in range(2):  # rounds; raise for tighter medians
    for k, fn in arms.items():
        ms = fn()
        best[k] = min(best[k], ms)
        print("  round %d %-18s %8.0f ms" % (r+1, k, ms), flush=True)
print("\nBEST-OF-ROUNDS, %d programs:" % len(clean))
for k, v in best.items():
    print("  %-18s %8.0f ms   %6.2f ms/program" % (k, v, v/len(clean)))
