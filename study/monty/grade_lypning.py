"""The same grading harness, pointed at lypning's tier 1 — so the comparison
table's two columns come from one instrument."""
import os, subprocess, sys, tempfile, time
sys.path.insert(0, "/home/user/lypning/src")
from lypning import corpus, conformance as conf

entries = corpus.load_default()
todo = [e for e in entries
        if not conf.absolute_paths(e.program) and not conf.is_nondeterministic(e)]

def run(cmd, prog, cwd, env):
    try:
        p = subprocess.run(cmd + ["-c", prog], capture_output=True, text=True,
                           timeout=10, cwd=cwd, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return None, "", ""

counts = {"MATCH": 0, "BOTH-FAIL": 0, "UNSUPPORTED": 0, "LOUD-ERROR": 0,
          "SILENT-DIFF": 0, "TIMEOUT": 0, "REF-TIMEOUT": 0}
t0 = time.time()
for i, e in enumerate(todo):
    with tempfile.TemporaryDirectory() as cwd:
        env = dict(os.environ)
        env.update({"PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8",
                    "LYPNING_CAPTURE": "0", "LYPNING_LOG": os.path.join(cwd, "c.jsonl")})
        rc, ref_out, _ = run([sys.executable], e.program, cwd, env)
        if rc is None:
            counts["REF-TIMEOUT"] += 1
            continue
        lrc, lout, lerr = run(["/root/.lypning/bin/lypning"], e.program, cwd, env)
    if lrc is None:
        counts["TIMEOUT"] += 1
    elif lrc == 90 and "unsupported:" in lerr:
        counts["UNSUPPORTED"] += 1 if rc == 0 else 0
        counts["BOTH-FAIL"] += 1 if rc != 0 else 0
    elif rc == 0 and lrc == 0 and lout == ref_out:
        counts["MATCH"] += 1
    elif rc != 0 and lrc != 0:
        counts["BOTH-FAIL"] += 1
    elif rc == 0 and lrc != 0:
        counts["LOUD-ERROR"] += 1
    elif rc == 0 and lout != ref_out:
        counts["SILENT-DIFF"] += 1
    else:
        counts["LOUD-ERROR"] += 1
    if (i+1) % 400 == 0:
        print("  ...%d/%d  %s" % (i+1, len(todo), counts), flush=True)
print("\ngraded %d   wall %.0fs" % (len(todo), time.time()-t0))
for k, v in counts.items():
    print("  %-13s %5d   (%.1f%%)" % (k, v, 100.0*v/len(todo)))
