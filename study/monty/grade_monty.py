"""Monty against the lypning corpus, graded the way the battery grades a tier.

The CPython reference runs with the battery's own discipline: a temp cwd per
program, PYTHONHASHSEED=0, LC_ALL=C.UTF-8, capture disabled, absolute-path
programs skipped. Monty needs no sandbox — it cannot touch the filesystem
unless granted — which is exactly one of the rows the comparison table needs.
"""
import json, os, subprocess, sys, tempfile, time
sys.path.insert(0, "/home/user/lypning/src")
import pydantic_monty as pm
from lypning import corpus, conformance as conf

entries = corpus.load_default()
skipped_abs = 0
todo = []
for e in entries:
    if conf.absolute_paths(e.program):
        skipped_abs += 1
        continue
    if conf.is_nondeterministic(e):
        continue
    todo.append(e)

def run_cpython(prog):
    with tempfile.TemporaryDirectory() as cwd:
        env = dict(os.environ)
        env.update({"PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8",
                    "LYPNING_CAPTURE": "0", "LYPNING_LOG": os.path.join(cwd, "cap.jsonl")})
        try:
            p = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                               text=True, timeout=10, cwd=cwd, env=env)
            return p.returncode, p.stdout
        except subprocess.TimeoutExpired:
            return None, ""

counts = {"MATCH": 0, "BOTH-FAIL": 0, "UNSUPPORTED": 0, "LOUD-ERROR": 0,
          "SILENT-DIFF": 0, "MONTY-TIMEOUT": 0, "REF-TIMEOUT": 0, "CRASH": 0}
silent = []
lim = pm.ResourceLimits(max_duration_secs=8)
t0 = time.time()
with pm.Monty() as m:
    for i, e in enumerate(todo):
        rc, ref_out = run_cpython(e.program)
        if rc is None:
            counts["REF-TIMEOUT"] += 1
            continue
        col = pm.CollectStreams()
        exc = None
        try:
            with m.checkout(limits=lim) as sess:
                sess.feed_run(e.program, print_callback=col)
        except pm.MontySyntaxError as x:
            exc = ("syntax", str(x))
        except pm.MontyRuntimeError as x:
            exc = ("runtime", str(x))
        except pm.MontyError as x:
            exc = ("monty", str(x))
        except Exception as x:
            n = type(x).__name__
            if "Timeout" in n or "timed out" in str(x) or "duration" in str(x).lower():
                counts["MONTY-TIMEOUT"] += 1
                continue
            counts["CRASH"] += 1
            continue
        out = "".join(t for s_, t in col.output if s_ == "stdout")
        if exc is None:
            if rc == 0 and out == ref_out:
                counts["MATCH"] += 1
            elif rc != 0:
                counts["LOUD-ERROR"] += 1   # monty succeeded where CPython failed
            else:
                counts["SILENT-DIFF"] += 1
                if len(silent) < 12:
                    silent.append((e.id, ref_out[:60], out[:60]))
        else:
            kind, msg = exc
            if rc != 0:
                counts["BOTH-FAIL"] += 1
            elif kind == "syntax" or any(t in msg for t in (
                    "not supported", "NotImplemented", "No module named",
                    "no module", "is not defined", "has no attribute")):
                counts["UNSUPPORTED"] += 1
            else:
                counts["LOUD-ERROR"] += 1
        if (i+1) % 400 == 0:
            print("  ...%d/%d  %s" % (i+1, len(todo), counts), flush=True)

print("\ncorpus loaded: %d   abs-path skipped: %d   nondeterministic skipped: %d   graded: %d"
      % (len(entries), skipped_abs, len(entries)-skipped_abs-len(todo), len(todo)))
print("wall: %.0fs" % (time.time()-t0))
for k, v in counts.items():
    print("  %-13s %5d   (%.1f%%)" % (k, v, 100.0*v/max(1,len(todo))))
print("\nsample silent diffs:")
for s_ in silent:
    print("  %s\n    ref  %r\n    mnty %r" % s_)
