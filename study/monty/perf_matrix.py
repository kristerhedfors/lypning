"""Compute-throughput matrix over the cross-validated workloads.

Interleaved rounds (every engine runs each workload once per round, in
rotation) so machine load hits all arms alike; median of 5 rounds. Process
arms are wall-clock spawn-to-reap; in-process arms time the call alone.
"""
import os, statistics, subprocess, sys, time
sys.path.insert(0, "/home/user/lypning/src")
import pydantic_monty as pm
from lypning import engines as eng

ENV = dict(os.environ); ENV.update({"LYPNING_CAPTURE": "0", "PYTHONHASHSEED": "0"})
WORK = ["w_int", "w_float", "w_str", "w_list", "w_dict", "w_call"]
SRC = {w: open(w + ".py").read() for w in WORK}
ROUNDS = 5

def t_proc(cmd):
    t0 = time.perf_counter_ns()
    subprocess.run(cmd, capture_output=True, env=ENV)
    return (time.perf_counter_ns() - t0) / 1e6

pool = pm.Monty(); pool.__enter__()
eng.run_library("print(1)")  # warm the dlopen

def t_monty(src):
    t0 = time.perf_counter_ns()
    with pool.checkout() as s:
        s.feed_run(src, print_callback=pm.CollectStreams())
    return (time.perf_counter_ns() - t0) / 1e6

def t_lib(src):
    t0 = time.perf_counter_ns()
    eng.run_library(src, step_limit=10_000_000_000)
    return (time.perf_counter_ns() - t0) / 1e6

ARMS = [
    ("cpython",    lambda w: t_proc([sys.executable, w + ".py"])),
    ("lypning",    lambda w: t_proc(["/root/.lypning/bin/lypning", w + ".py"])),
    ("liblypning", lambda w: t_lib(SRC[w])),
    ("lypning-mp", lambda w: t_proc(["/root/.lypning/bin/lypning-mp-i386", w + ".py"])),
    ("monty-feed", lambda w: t_monty(SRC[w])),
    ("monty-cli",  lambda w: t_proc(["/usr/local/bin/monty", w + ".py"])),
]

res = {w: {a: [] for a, _ in ARMS} for w in WORK}
for r in range(ROUNDS):
    for w in WORK:
        for name, fn in ARMS:
            res[w][name].append(fn(w))
    print("round %d done" % (r + 1), flush=True)
pool.__exit__(None, None, None)

print("\n%-8s" % "workload" + "".join("%14s" % a for a, _ in ARMS))
for w in WORK:
    row = "%-8s" % w
    cpy = statistics.median(res[w]["cpython"])
    for name, _ in ARMS:
        m = statistics.median(res[w][name])
        row += "%9.0f ms  " % m if False else "%8.0f/%4.2fx" % (m, m / cpy)
    print(row)
print("\n(median of %d interleaved rounds; Nx = vs CPython wall)" % ROUNDS)
