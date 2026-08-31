"""Dump all 39 PyPy silent diffs for manual classification, then run the
CPython 3.13 arm: cold start, and full grading against the 3.11 reference."""
import json, os, statistics, subprocess, sys, tempfile, time
sys.path.insert(0, "src")
from lypning import corpus, conformance as conf

PYPY = os.environ["PYPY_BIN"]; P313 = os.environ["P313_BIN"]
ENV = dict(os.environ); ENV.update({"PYTHONHASHSEED":"0","LC_ALL":"C.UTF-8","LYPNING_CAPTURE":"0"})
D = subprocess.DEVNULL
def run(cmd, prog, cwd):
    t0=time.perf_counter_ns()
    try:
        p=subprocess.run(cmd+["-c",prog],capture_output=True,stdin=D,text=True,
                         errors="replace",timeout=20,cwd=cwd,env=ENV)
        return p.returncode,p.stdout,p.stderr,(time.perf_counter_ns()-t0)/1e6
    except subprocess.TimeoutExpired:
        return None,"","",0.0

entries=[e for e in corpus.load_default()
         if not conf.absolute_paths(e.program) and not conf.is_nondeterministic(e)]

diffs=[]; counts={"MATCH":0,"BOTH-FAIL":0,"LOUD":0,"SILENT":0,"REFOK":0}
wall313=0.0
for e in entries:
    with tempfile.TemporaryDirectory() as cwd:
        rc,so,se,_=run([sys.executable],e.program,cwd)
        prc,pso,pse,_=run([PYPY],e.program,cwd) if rc==0 else (1,"","",0)
        trc,tso,tse,tms=run([P313],e.program,cwd)
    wall313+=tms
    if rc==0 and prc==0 and pso!=so:
        diffs.append({"id":e.id[:12],"program":e.program[:400],"cpy":so[:200],"pypy":pso[:200]})
    if rc is None or trc is None: continue
    if rc!=0:
        counts["BOTH-FAIL" if trc!=0 else "REFOK"]+=1
    elif trc!=0: counts["LOUD"]+=1
    elif tso==so: counts["MATCH"]+=1
    else:
        counts["SILENT"]+=1
        if counts["SILENT"]<=15:
            diffs.append({"id":"313-"+e.id[:10],"program":e.program[:400],"cpy":so[:200],"pypy":"[3.13] "+tso[:200]})
print(json.dumps({"pypy_diff_count":sum(1 for d in diffs if not d["id"].startswith("313")),
                  "cp313_grading":counts,"cp313_wall_ms":round(wall313,1)},indent=1))
json.dump(diffs,open(os.environ["OUT"],"w"),indent=1)

xs=[]
for _ in range(40):
    t0=time.perf_counter_ns()
    subprocess.run([P313,"-c","print(1)"],capture_output=True,stdin=D,env=ENV)
    xs.append((time.perf_counter_ns()-t0)/1e6)
print("cp313 cold start: median %.3f ms  min %.3f ms" % (statistics.median(xs),min(xs)))
