"""Score every generated program, and report the table the study exists to produce.

Nothing here asks a model anything. Each program is routed by lypning's own
parser, run on CPython, run on the Rust core, and the three answers are folded
into the same MATCH / UNSUPPORTED / MISMATCH vocabulary `lypning conformance`
uses. That is deliberate: a study that invented its own grading scheme could
not be compared against the repository's own numbers, and the whole point of a
prompting result is what it does to the numbers already on the table.

Run: python3 study/score.py            # score and write study/data/results.jsonl
     python3 study/score.py --report   # re-render the tables from what is scored
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as H  # noqa: E402

PROGRAMS = H.STUDY / "data" / "programs.jsonl"
RESULTS = H.STUDY / "data" / "results.jsonl"
TREATMENTS = json.loads((H.STUDY / "treatments.json").read_text(encoding="utf-8"))


def score_all() -> list:
    tasks = {t["id"]: t for t in H.load_tasks()}
    recs = [json.loads(ln) for ln in PROGRAMS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    before = H.git_snapshot()
    out = []
    for i, rec in enumerate(recs, 1):
        t = tasks[rec["task"]]
        scored = dict(rec)
        scored.update(H.score_one(rec["program"], t))
        out.append(scored)
        if i % 50 == 0:
            print("  %d/%d" % (i, len(recs)), file=sys.stderr)
    after = H.git_snapshot()
    changed = H.check_net(before, after)
    if changed:
        print("\nNET TRIPPED — the battery changed tracked files:\n  %s"
              % "\n  ".join(changed), file=sys.stderr)
    RESULTS.write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    return out


def load_results() -> list:
    return [json.loads(ln) for ln in RESULTS.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _pct(a: int, b: int) -> str:
    return "—" if not b else "%.1f%%" % (100.0 * a / b)


def report(rows: list) -> str:
    tasks = {t["id"]: t for t in H.load_tasks()}
    feasible = {tid for tid, t in tasks.items() if t["tier1_feasible"]}
    by_t = defaultdict(list)
    for r in rows:
        by_t[r["treatment"]].append(r)

    out = []
    order = sorted(by_t, key=lambda k: int(k[1:]))

    # --- the main table ------------------------------------------------------
    out.append("### Table 1 — what each prompt bought, over all %d tasks\n" % len(tasks))
    out.append("| id | treatment | n | routes tier 1 | **runs on tier 1** | of the %d feasible | correct | MISMATCH |"
               % len(feasible))
    out.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for tid in order:
        rs = by_t[tid]
        n = len(rs)
        routed = sum(1 for r in rs if r["route"] == "lypning")
        won = sum(1 for r in rs if r.get("tier1_win"))
        feas = [r for r in rs if r["task"] in feasible]
        won_f = sum(1 for r in feas if r.get("tier1_win"))
        correct = sum(1 for r in rs if r.get("correct"))
        mism = sum(1 for r in rs if r.get("verdict") == H.MISMATCH)
        out.append("| %s | %s | %d | %s | **%s** | %s | %s | %d |"
                   % (tid, TREATMENTS[tid]["label"], n, _pct(routed, n), _pct(won, n),
                      _pct(won_f, len(feas)), _pct(correct, n), mism))
    out.append("")

    # --- variance across replicates -----------------------------------------
    out.append("### Table 2 — spread across independent replicates\n")
    out.append("| id | treatment | replicates | tier-1 rate per replicate | mean | spread |")
    out.append("|---|---|---:|---|---:|---:|")
    for tid in order:
        per = defaultdict(list)
        for r in by_t[tid]:
            per[r["replicate"]].append(r)
        rates = []
        for rep in sorted(per):
            rs = per[rep]
            rates.append(100.0 * sum(1 for r in rs if r.get("tier1_win")) / len(rs))
        spread = (max(rates) - min(rates)) if len(rates) > 1 else 0.0
        out.append("| %s | %s | %d | %s | %.1f%% | %.1f pp |"
                   % (tid, TREATMENTS[tid]["label"], len(rates),
                      " · ".join("%.1f%%" % x for x in rates),
                      statistics.fmean(rates), spread))
    out.append("")

    # --- what stopped the rest ----------------------------------------------
    out.append("### Table 3 — what stopped the programs that did not reach tier 1\n")
    blockers = defaultdict(Counter)
    for r in rows:
        if r.get("tier1_win"):
            continue
        if r.get("verdict") == H.UNSUPPORTED and r.get("refusal_kind"):
            blockers[r["treatment"]]["run: " + r["refusal_kind"]] += 1
        elif r["route"] != "lypning":
            detail = (r.get("route_detail") or r.get("route_kind") or "?").strip()
            blockers[r["treatment"]]["route: " + detail] += 1
        elif not r.get("correct"):
            blockers[r["treatment"]]["wrong answer"] += 1
        else:
            blockers[r["treatment"]]["other"] += 1
    out.append("| id | treatment | the three commonest blockers |")
    out.append("|---|---|---|")
    for tid in order:
        top = ", ".join("`%s` ×%d" % (k, v) for k, v in blockers[tid].most_common(3))
        out.append("| %s | %s | %s |" % (tid, TREATMENTS[tid]["label"], top or "—"))
    out.append("")

    # --- per task ------------------------------------------------------------
    out.append("### Table 4 — per task: which ones prompting could move\n")
    out.append("| task | tempts | tier-1 feasible | control | cheapest static prompt that gets it | engine in the loop |")
    out.append("|---|---|---|---:|---:|---:|")
    static = [t for t in order if not TREATMENTS[t]["tools"]]
    tooled = [t for t in order if TREATMENTS[t]["tools"]]
    for tid_task in tasks:
        t = tasks[tid_task]
        rows_for = [r for r in rows if r["task"] == tid_task]
        def rate(ts):
            rs = [r for r in rows_for if r["treatment"] in ts]
            return _pct(sum(1 for r in rs if r.get("tier1_win")), len(rs))
        best = "—"
        best_v = -1.0
        for s in static:
            if s == "T0":
                continue
            rs = [r for r in rows_for if r["treatment"] == s]
            if not rs:
                continue
            v = 100.0 * sum(1 for r in rs if r.get("tier1_win")) / len(rs)
            if v > best_v:
                best_v, best = v, "%s (%.0f%%)" % (s, v)
        out.append("| `%s` | %s | %s | %s | %s | %s |"
                   % (tid_task, t.get("tempts") or "—",
                      "yes" if t["tier1_feasible"] else "**no**",
                      rate(["T0"]), best, rate(tooled) if tooled else "—"))
    out.append("")
    return "\n".join(out)


def main(argv) -> int:
    if "--report" not in argv:
        rows = score_all()
    else:
        rows = load_results()
    text = report(rows)
    (H.STUDY / "data" / "tables.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
