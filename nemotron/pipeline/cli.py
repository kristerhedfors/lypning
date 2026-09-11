"""The surface. Every command is one line, and nothing long-running blocks.

Two rules shape this file. First, anything that can take minutes — an eval, later
a training run — is launched detached under tmux and returns immediately with the
run id; `status` and `results` are the only two commands you need after that.
Second, library code returns data and the CLI renders it: exit codes are 0 ok,
1 this command failed, 2 usage. That is the same split the host repository uses,
for the same reason — a module that prints cannot be tested for what it decided.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import split as splitmod
from . import stats
from .adapters import parse_source
from .backends import BackendError, ChatBackend
from .evaluate import Evaluation, load_holdout, summarize_run
from .harvest import harvest
from .jsonio import read_json, read_jsonl, write_json

ROOT = Path(os.environ.get("NTX_ROOT") or Path(__file__).resolve().parents[1])
DATA = ROOT / "data"
RUNS = ROOT / "runs"
BASELINE = DATA / "baseline.json"


def _pct(x: float) -> str:
    return "n/a" if x != x else "%5.1f%%" % (100.0 * x)


def _dur(s: Optional[float]) -> str:
    if s is None:
        return "?"
    s = int(s)
    if s < 90:
        return "%ds" % s
    if s < 5400:
        return "%dm%02ds" % (s // 60, s % 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


# ------------------------------------------------------------------ step 1


def cmd_harvest(args: argparse.Namespace) -> int:
    try:
        sources = [parse_source(s) for s in (args.source or ["study"])]
    except ValueError as exc:
        print("usage: %s" % exc, file=sys.stderr)
        return 2
    for s in sources:
        if s["name"] == "study":
            s.setdefault("path", str(ROOT.parent / "study" / "tasks.jsonl"))
        if s["name"] == "evalfail":
            s.setdefault("corpus", str(DATA / "corpus.jsonl"))

    def progress(done: int, total: int, cid: str, gate: str) -> None:
        if args.verbose:
            print("  [%3d/%3d] %s %s" % (done, total, cid, gate), file=sys.stderr)

    ledger = harvest(sources, out_dir=DATA, jobs=args.jobs,
                     allow_no_witness=args.allow_no_witness, progress=progress)
    print("harvest  kept %d   dropped %d   (candidates %d)"
          % (ledger["kept"], ledger["dropped"], ledger["candidates"]))
    if ledger["drops_by_gate"]:
        print("  dropped by gate:")
        for gate, n in sorted(ledger["drops_by_gate"].items(), key=lambda kv: -kv[1]):
            print("    %-14s %4d" % (gate, n))
    print("  by category:")
    for cat, n in sorted(ledger["kept_by_category"].items(), key=lambda kv: -kv[1]):
        print("    %-14s %4d" % (cat, n))
    print("  with reference %d   with a recorded failure %d"
          % (ledger["with_reference"], ledger["with_negative"]))
    print("  -> %s" % (DATA / "corpus.jsonl"))
    if ledger["kept"] == 0:
        print("\nnothing was kept. see data/drops.jsonl for the gate that rejected each "
              "candidate.", file=sys.stderr)
        return 1
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    """Run the real corpus through CPython and the engines, once, and write it down."""
    import concurrent.futures
    from .jsonio import append_jsonl
    from .lypning_source import classify_entry

    corpus_paths = args.corpus or [
        str(ROOT.parent / "src" / "lypning" / "assets" / "corpus" / "corpus.jsonl"),
        str(ROOT.parent / "src" / "lypning" / "assets" / "corpus" / "seed-corpus.jsonl"),
    ]
    entries: List[Dict[str, Any]] = []
    for cp in corpus_paths:
        entries.extend(read_jsonl(cp))
    if args.limit:
        entries = entries[: args.limit]
    out = DATA / "classified.jsonl"
    done = {r["entry"]["id"] for r in read_jsonl(out)} if out.exists() else set()
    todo = [e for e in entries if e.get("id") not in done]
    print("corpus %d entries loaded from %d file(s); %d already classified, %d to do"
          % (len(entries), len(corpus_paths), len(done), len(todo)))

    counts: Dict[str, int] = {}
    lock = __import__("threading").Lock()

    def one(entry: Dict[str, Any]) -> Dict[str, Any]:
        outcome, info = classify_entry(entry, timeout_s=args.timeout)
        return {"entry": entry, "outcome": outcome, "info": info}

    n = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for rec in pool.map(one, todo):
            with lock:
                append_jsonl(out, rec)
                counts[rec["outcome"]] = counts.get(rec["outcome"], 0) + 1
                n += 1
                if n % 250 == 0:
                    print("  %d/%d  %s" % (n, len(todo), counts), flush=True)
    allrecs = read_jsonl(out)
    tally: Dict[str, int] = {}
    kinds: Dict[str, int] = {}
    for r in allrecs:
        tally[r["outcome"]] = tally.get(r["outcome"], 0) + 1
        if r["outcome"] == "refused":
            k = r["info"]["kind"]
            kinds[k] = kinds.get(k, 0) + 1
    print("\nclassified %d entries" % len(allrecs))
    for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
        print("  %-10s %5d" % (k, v))
    print("\nrefusals by kind (the stratification key):")
    for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print("  %-14s %5d" % (k, v))
    print("\n-> %s" % out)
    return 0


def cmd_split(args: argparse.Namespace) -> int:
    corpus = DATA / "corpus.jsonl"
    if not corpus.exists():
        print("no corpus: run `nt harvest` first", file=sys.stderr)
        return 1
    existed = (DATA / splitmod.LOCK_NAME).exists()
    try:
        lock = splitmod.freeze(corpus, fraction=args.fraction, refreeze=args.refreeze)
        counts = splitmod.materialize(corpus)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    verb = "re-frozen" if (existed and args.refreeze) else ("already frozen" if existed else "frozen")
    print("split %s at %.0f%%   train %d   held-out %d"
          % (verb, 100 * lock["fraction"], counts["train"], counts["holdout"]))
    print("  manifest %s" % lock["manifest_sha256"][:16])
    print("  %-14s %8s %8s" % ("category", "total", "held-out"))
    for cat, s in sorted(lock["strata"].items()):
        print("  %-14s %8d %8d" % (cat, s["total"], s["holdout"]))
    if existed and args.refreeze:
        print("\nthe held-out set moved: every baseline and every delta measured "
              "against one is now void.", file=sys.stderr)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    ok, problems = splitmod.verify(DATA / "corpus.jsonl")
    if ok:
        lock = splitmod.load_lock(DATA) or {}
        print("holdout OK   %d cases   manifest %s"
              % (lock.get("n_holdout", 0), (lock.get("manifest_sha256") or "")[:16]))
        return 0
    print("holdout FAILED", file=sys.stderr)
    for p in problems:
        print("  " + p, file=sys.stderr)
    return 1


# ------------------------------------------------------------------ step 2


def _backend(args: argparse.Namespace) -> ChatBackend:
    return ChatBackend.from_env(
        base_url=getattr(args, "base_url", None), model=getattr(args, "model", None)
    )


def cmd_probe(args: argparse.Namespace) -> int:
    try:
        info = _backend(args).probe()
    except BackendError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("backend OK   %s   %.2fs   reply %r"
          % (info["model"], info["latency_s"], info["reply"]))
    return 0


def _run_id(prefix: str) -> str:
    return "%s-%s" % (prefix, time.strftime("%Y%m%d-%H%M%S"))


def cmd_eval(args: argparse.Namespace) -> int:
    """Launch detached unless --foreground. Returns as soon as tmux has it."""
    run_id = args.run_id or _run_id("baseline" if args.baseline else "eval")
    if args.foreground:
        return _eval_foreground(args, run_id)
    if not shutil.which("tmux"):
        print("tmux not found: re-run with --foreground, or install tmux", file=sys.stderr)
        return 1
    inner = [sys.executable, "-m", "pipeline.cli", "eval", "--foreground",
             "--run-id", run_id, "--samples", str(args.samples),
             "--concurrency", str(args.concurrency),
             "--temperature", str(args.temperature), "--top-p", str(args.top_p),
             "--max-tokens", str(args.max_tokens), "--max-spend", str(args.max_spend),
             "--price-hour", str(args.price_hour)]
    if args.baseline:
        inner.append("--baseline")
    if args.no_thinking:
        inner.append("--no-thinking")
    if args.label:
        inner += ["--label", args.label]
    if args.base_url:
        inner += ["--base-url", args.base_url]
    if args.model:
        inner += ["--model", args.model]
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log = run_dir / "console.log"
    session = "ntx-" + run_id
    # `; sleep` keeps a failed session alive long enough for its log to be read.
    shell = "cd %s && %s 2>&1 | tee -a %s" % (
        _q(str(ROOT)), " ".join(_q(a) for a in inner), _q(str(log)))
    subprocess.run(["tmux", "new-session", "-d", "-s", session, "bash", "-lc", shell], check=True)
    print("launched %s   (tmux %s)" % (run_id, session))
    print("  nt status        # progress, spend, ETA")
    print("  nt results       # leaderboard once it lands")
    return 0


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _eval_foreground(args: argparse.Namespace, run_id: str) -> int:
    try:
        cases = load_holdout(DATA)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not cases:
        print("held-out split is empty", file=sys.stderr)
        return 1
    try:
        backend = _backend(args)
    except BackendError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    lock = splitmod.load_lock(DATA) or {}
    ev = Evaluation(
        backend, cases, RUNS / run_id,
        samples=args.samples, temperature=args.temperature, top_p=args.top_p,
        max_tokens=args.max_tokens, enable_thinking=not args.no_thinking,
        concurrency=args.concurrency, max_spend=args.max_spend,
        price_hour=args.price_hour,
        label=args.label or ("stock baseline" if args.baseline else ""),
    )
    t0 = time.time()

    def progress(done: int, total: int, rec: Dict[str, Any]) -> None:
        mark = "." if rec.get("passed") else ("!" if rec.get("harness_error") else "x")
        print("[%3d/%3d] %s %s %s" % (done, total, mark, rec["case_id"],
                                      rec.get("failure_category") or ""), flush=True)

    summary = ev.run(lock.get("manifest_sha256", ""), progress=progress)
    print()
    print(render_summary(summary, elapsed=time.time() - t0))
    if args.baseline:
        write_json(BASELINE, summary)
        print("\nbaseline written to %s" % BASELINE)
        print("every later run is measured against pass_rate = %.4f" % summary["pass_rate"])
    return 0


def render_summary(s: Dict[str, Any], elapsed: Optional[float] = None) -> str:
    out = [
        "run        %s  %s" % (s["run_id"], s.get("label") or ""),
        "model      %s" % s.get("model"),
        "pass@1     %s   95%% CI [%s, %s]  (bootstrap, %d resamples)"
        % (_pct(s["pass_rate"]), _pct(s["ci95"]["lo"]), _pct(s["ci95"]["hi"]),
           s["ci95"].get("resamples") or 0),
        "           %s   95%% CI [%s, %s]  (Wilson, cross-check)"
        % (" " * 6, _pct(s["ci95_wilson"]["lo"]), _pct(s["ci95_wilson"]["hi"])),
        "cases      %d evaluated of %s planned   attempts %d"
        % (s["cases_evaluated"], s.get("cases_planned"), s["n_attempts"]),
    ]
    if s.get("harness_errors"):
        out.append("harness    %d attempt(s) excluded — server or sandbox, not the model"
                   % s["harness_errors"])
    if s.get("failures_by_category"):
        out.append("failures   " + "  ".join(
            "%s=%d" % (k, v) for k, v in sorted(s["failures_by_category"].items(),
                                                key=lambda kv: -kv[1])))
    out.append("cost       $%.4f   %d output tokens%s"
               % (s.get("spend_usd", 0.0), s.get("tokens_out", 0),
                  ("   wall %s" % _dur(elapsed)) if elapsed else ""))
    return "\n".join(out)


def cmd_grade(args: argparse.Namespace) -> int:
    """Grade completions produced elsewhere. The GPU box generates; this grades.

    The acceptance tests need the lypning engines and a sandbox, and both are
    CPU work. Renting eight H100s to run them would cost roughly twelve times
    what the same seconds cost here, so the box writes completions to a file and
    stops. Re-grading afterwards — after an engine rebuild, after a fixed test —
    then costs nothing and needs no GPU at all.
    """
    from .acceptance import run_test
    from .classify import classify
    from .extract import extract_program
    from .jsonio import append_jsonl

    comps = read_jsonl(args.completions)
    if not comps:
        print("no completions in %s" % args.completions, file=sys.stderr)
        return 1
    try:
        cases = {c["id"]: c for c in load_holdout(DATA)}
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    unknown = {c["case_id"] for c in comps} - set(cases)
    if unknown:
        print("completions reference %d case(s) not in the frozen held-out split: %s"
              % (len(unknown), sorted(unknown)[:3]), file=sys.stderr)
        return 1

    run_dir = RUNS / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "attempts.jsonl").unlink(missing_ok=True)
    meta = {"run_id": args.run_id, "label": args.label,
            "backend": {"model": args.model or "(generated elsewhere)",
                        "base_url": "replay:%s" % args.completions},
            "prompt_sha": __import__("pipeline.evaluate", fromlist=["x"]).prompt_signature(),
            "holdout_manifest_sha256": (splitmod.load_lock(DATA) or {}).get("manifest_sha256", ""),
            "n_cases": len(cases), "sampling": {"replayed": True},
            "started_at": __import__("pipeline.evaluate", fromlist=["x"])._now()}
    write_json(run_dir / "meta.json", meta)

    for i, c in enumerate(comps, 1):
        case = cases[c["case_id"]]
        program, how = extract_program(c.get("text") or "", c.get("reasoning"))
        rec = {"run_id": args.run_id, "case_id": c["case_id"], "sample": c.get("sample", 0),
               "how": how, "program": program or "",
               "completion_tokens": c.get("completion_tokens", 0),
               "cost_usd": 0.0, "ts": meta["started_at"]}
        if program is None:
            rec.update(passed=False, reason="no-code", detail="", failure_category="no-code")
        else:
            v = run_test(case["test"], program)
            if v.harness_error:
                rec.update(harness_error=v.harness_error, passed=False)
            elif v.passed:
                rec.update(passed=True, reason="pass", detail="", failure_category="")
            else:
                rec.update(passed=False, reason=v.reason, detail=v.detail[:500],
                           failure_category=classify(v, had_code=True))
        append_jsonl(run_dir / "attempts.jsonl", rec)
        if i % 200 == 0:
            print("  graded %d/%d" % (i, len(comps)), flush=True)

    print(render_summary(summarize_run(run_dir)))
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    """Rejection-sample verified SFT targets from the TRAIN split only."""
    from .sample import sample_targets, train_cases
    try:
        cases = train_cases(DATA)
    except (ValueError, AssertionError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.limit:
        cases = cases[: args.limit]
    try:
        backend = _backend(args)
    except BackendError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    out_dir = DATA / "sft" / (args.name or "v1")
    print("sampling %d train cases x k=%d  (held-out %d cases are excluded by the lock)"
          % (len(cases), args.k, len((splitmod.load_lock(DATA) or {}).get("holdout", []))))

    def progress(done: int, total: int, rec: Dict[str, Any]) -> None:
        if done % 100 == 0:
            print("  %d/%d" % (done, total), flush=True)

    rep = sample_targets(
        backend, cases, out_dir, k=args.k, keep=args.keep,
        temperature=args.temperature, top_p=args.top_p, max_tokens=args.max_tokens,
        enable_thinking=not args.no_thinking, concurrency=args.concurrency,
        price_hour=args.price_hour, max_spend=args.max_spend, progress=progress)
    print()
    print("cases with a verified solution  %d / %d   (%.1f%%)"
          % (rep["cases_with_a_verified_solution"], rep["cases"], 100 * rep["yield_rate"]))
    print("SFT examples written            %d" % rep["sft_examples"])
    print("per-draw pass rate              %.1f%%" % (100 * rep["draw_pass_rate"]))
    print("rejected by reason:")
    for r, n in list(rep["rejected_by_reason"].items())[:8]:
        print("   %-18s %5d" % (r, n))
    print("\nyield by category:")
    for cat, e in list(rep["by_category"].items())[:12]:
        print("   %-26s %2d/%-2d  %5.0f%%" % (cat, e["solved"], e["cases"], 100 * e["yield"]))
    print("\n-> %s" % (out_dir / "sft.jsonl"))
    if rep.get("aborted"):
        print("ABORTED: %s" % rep["aborted"], file=sys.stderr)
        return 1
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    run_dir = RUNS / args.run_id
    if not run_dir.exists():
        print("no such run: %s" % args.run_id, file=sys.stderr)
        return 1
    print(render_summary(summarize_run(run_dir)))
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    run_dir = RUNS / args.run_id
    if not (run_dir / "summary.json").exists():
        print("run has no summary: %s" % args.run_id, file=sys.stderr)
        return 1
    s = read_json(run_dir / "summary.json")
    write_json(BASELINE, s)
    print("baseline := %s   pass@1 %s" % (args.run_id, _pct(s["pass_rate"])))
    return 0


# ------------------------------------------------------- status / results


def _progresses() -> List[Dict[str, Any]]:
    out = []
    for p in sorted(RUNS.glob("*/progress.json")):
        try:
            out.append(read_json(p))
        except (ValueError, OSError):
            continue
    return out


def cmd_status(args: argparse.Namespace) -> int:
    lock = splitmod.load_lock(DATA)
    corpus = read_jsonl(DATA / "corpus.jsonl")
    print("corpus     %d cases   held-out %s   manifest %s"
          % (len(corpus),
             (lock or {}).get("n_holdout", "unfrozen"),
             ((lock or {}).get("manifest_sha256") or "-")[:12]))
    if BASELINE.exists():
        b = read_json(BASELINE)
        print("baseline   pass@1 %s  CI [%s, %s]  run %s"
              % (_pct(b["pass_rate"]), _pct(b["ci95"]["lo"]), _pct(b["ci95"]["hi"]),
                 b["run_id"]))
    else:
        print("baseline   not measured yet")
    rows = _progresses()
    live = [r for r in rows if r.get("state") == "running"]
    spend = sum(r.get("spend_usd", 0.0) for r in rows)
    print("spend      $%.2f across %d run(s)" % (spend, len(rows)))
    if not rows:
        print("runs       none")
        return 0
    print("%-26s %-8s %9s %8s %9s" % ("run", "state", "progress", "spend", "eta"))
    for r in sorted(rows, key=lambda r: r["run_id"])[-args.limit:]:
        prog = "%d/%d" % (r.get("done", 0), r.get("total", 0))
        print("%-26s %-8s %9s %8s %9s"
              % (r["run_id"][:26], r.get("state", "?"), prog,
                 "$%.2f" % r.get("spend_usd", 0.0), _dur(r.get("eta_s"))))
        if r.get("aborted"):
            print("    aborted: %s" % r["aborted"])
    if live:
        print("\n%d running." % len(live))
    return 0


def cmd_results(args: argparse.Namespace) -> int:
    base = read_json(BASELINE) if BASELINE.exists() else None
    rows: List[Dict[str, Any]] = []
    for p in sorted(RUNS.glob("*/summary.json")):
        try:
            rows.append(read_json(p))
        except (ValueError, OSError):
            continue
    if not rows:
        print("no finished runs yet")
        return 0
    bp = base["pass_rate"] if base else None
    base_manifest = (base or {}).get("holdout_manifest_sha256")
    base_prompt = (base or {}).get("prompt_sha")
    incomparable: List[Dict[str, Any]] = []
    for r in rows:
        # A delta is only a delta if both numbers came from the same held-out
        # set and the same prompt. Otherwise it is two different measurements
        # subtracted, which is worse than no number at all.
        why = []
        if base_manifest and r.get("holdout_manifest_sha256") != base_manifest:
            why.append("different held-out split")
        if base_prompt and r.get("prompt_sha") != base_prompt:
            why.append("different prompt")
        r["_why"] = "; ".join(why)
        r["_delta"] = (r["pass_rate"] - bp) if (bp is not None and not why) else 0.0
        r["_win"] = bool(bp is not None and not why and stats.beats(r, bp))
        if why:
            incomparable.append(r)
    rows.sort(key=lambda r: (bool(r["_why"]), -r["_delta"]))
    if base:
        print("baseline   %s   run %s   (a win needs CI-low above this)"
              % (_pct(base["pass_rate"]), base["run_id"]))
    print("%-26s %8s %-18s %8s %6s %8s"
          % ("run", "pass@1", "95% CI", "delta", "win", "cost"))
    for r in rows:
        print("%-26s %8s %-18s %8s %6s %8s"
              % (r["run_id"][:26], _pct(r["pass_rate"]),
                 "[%s,%s]" % (_pct(r["ci95"]["lo"]).strip(), _pct(r["ci95"]["hi"]).strip()),
                 ("n/c" if r["_why"] else
                  ("%+.1fpp" % (100 * r["_delta"])) if bp is not None else "-"),
                 "yes" if r["_win"] else "",
                 "$%.2f" % r.get("spend_usd", 0.0)))
    for r in incomparable:
        print("  n/c %s: %s — not comparable to the baseline" % (r["run_id"], r["_why"]))
    comparable = [r for r in rows if not r["_why"]]
    if bp is not None and comparable and not any(r["_win"] for r in comparable):
        print("\nno run clears the bar: a win needs its CI lower bound above %s."
              % _pct(bp))
    return 0


def _per_case(run_id: str) -> Dict[str, float]:
    attempts = [a for a in read_jsonl(RUNS / run_id / "attempts.jsonl")
                if not a.get("harness_error")]
    agg: Dict[str, List[float]] = {}
    for a in attempts:
        agg.setdefault(a["case_id"], []).append(1.0 if a.get("passed") else 0.0)
    return {k: sum(v) / len(v) for k, v in agg.items()}


def cmd_compare(args: argparse.Namespace) -> int:
    """Paired delta between two runs over the cases both measured."""
    before, after = _per_case(args.before), _per_case(args.after)
    d = stats.paired_delta(before, after)
    if not d.get("n_pairs"):
        print("no cases in common", file=sys.stderr)
        return 1
    print("before %s   after %s" % (args.before, args.after))
    print("paired delta %+.1fpp   95%% CI [%+.1f, %+.1f]pp   over %d cases"
          % (100 * d["delta"], 100 * d["ci95"]["lo"], 100 * d["ci95"]["hi"], d["n_pairs"]))
    print("gained %d   lost %d   McNemar p=%.4f   %s"
          % (d["gained"], d["lost"], d["mcnemar_p"],
             "SIGNIFICANT" if d["significant"] else "not separable from noise"))
    if args.ids:
        print("gained:", ", ".join(d["gained_ids"]))
        print("lost  :", ", ".join(d["lost_ids"]))
    base = read_json(BASELINE) if BASELINE.exists() else None
    if base:
        a = read_json(RUNS / args.after / "summary.json")
        print("unpaired rule: CI-low %s vs baseline point %s -> %s"
              % (_pct(a["ci95"]["lo"]), _pct(base["pass_rate"]),
                 "WIN" if stats.beats(a, base["pass_rate"]) else "no"))
    return 0


def cmd_slices(args: argparse.Namespace) -> int:
    """Pass rate per stratum. The blended number hides where the headroom is.

    This corpus mixes three populations with wildly different ceilings: rewrite
    cases the model mostly fails, ceiling cases it should and does pass by
    falling back, and a saturated task bank. One average over the three is not a
    number anyone can act on.

    The bootstrap resamples CASES, never attempts. With k draws per case the
    attempts are not independent — k samples of one easy case are one easy case
    seen k times — and resampling them would report an interval several times
    too narrow. Each case contributes its mean over its own draws, and that mean
    is the unit that gets resampled.
    """
    from .classify import stratum
    run_dir = RUNS / args.run_id
    attempts = [a for a in read_jsonl(run_dir / "attempts.jsonl") if not a.get("harness_error")]
    corpus = {c["id"]: c for c in read_jsonl(DATA / "corpus.jsonl")}

    counts: Dict[str, Dict[str, List[int]]] = {}
    for a in attempts:
        case = corpus.get(a["case_id"])
        if case is None:
            continue
        key = case["category"] if args.fine else stratum(case["category"])
        c = counts.setdefault(key, {}).setdefault(a["case_id"], [0, 0])
        c[1] += 1
        if a.get("passed"):
            c[0] += 1

    print("run %s   (bootstrap resamples cases, not attempts)" % args.run_id)
    print("%-26s %5s %6s %9s %-20s %8s %9s"
          % ("slice", "cases", "draws", "pass@1", "95% CI (bootstrap)", "pass@k", "headroom"))
    for key, per_case in sorted(counts.items(), key=lambda kv: -len(kv[1])):
        scores = [c[0] / c[1] for c in per_case.values()]
        draws = sum(c[1] for c in per_case.values())
        b = stats.summarize(scores)
        pk = stats.pass_at_k([(c[0], c[1]) for c in per_case.values()])
        print("%-26s %5d %6d %9s [%s, %s] %8s %9s"
              % (key, len(scores), draws, _pct(b["pass_rate"]),
                 _pct(b["ci95"]["lo"]).strip(), _pct(b["ci95"]["hi"]).strip(),
                 _pct(pk["pass_at_k"]) if pk["k"] > 1 else "-",
                 ("%+.1fpp" % (100 * pk["headroom"])) if pk["k"] > 1 else "-"))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Inspect what actually happened on a case: the program and why it failed."""
    run_dir = RUNS / args.run_id
    attempts = [a for a in read_jsonl(run_dir / "attempts.jsonl")
                if not args.case or a["case_id"] == args.case]
    if args.failed_only:
        attempts = [a for a in attempts if not a.get("passed")]
    for a in attempts[: args.limit]:
        print("=" * 72)
        print("%s sample %s  %s  %s"
              % (a["case_id"], a["sample"], "PASS" if a.get("passed") else "FAIL",
                 a.get("failure_category") or ""))
        if a.get("detail"):
            print("detail: %s" % a["detail"][:400])
        print("-" * 72)
        print(a.get("program") or "(no program extracted)")
    return 0


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nt", description="Nemotron LoRA pipeline: corpus and eval")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("harvest", help="step 1: build the corpus from failing cases")
    h.add_argument("--source", action="append",
                   help="adapter spec, repeatable: study | evalfail:attempts=PATH | "
                        "jsonl:path=PATH[,map=dst->src]")
    h.add_argument("--jobs", type=int, default=4)
    h.add_argument("--allow-no-witness", action="store_true",
                   help="keep cases with neither a reference nor a recorded failure "
                        "(their tests cannot be shown to work)")
    h.add_argument("-v", "--verbose", action="store_true")
    h.set_defaults(fn=cmd_harvest)

    cl = sub.add_parser("classify", help="run the real corpus through CPython and the engines")
    cl.add_argument("--corpus", action="append")
    cl.add_argument("--jobs", type=int, default=8)
    cl.add_argument("--timeout", type=float, default=10.0)
    cl.add_argument("--limit", type=int, default=0)
    cl.set_defaults(fn=cmd_classify)

    s = sub.add_parser("split", help="freeze the 70/30 stratified held-out split")
    s.add_argument("--fraction", type=float, default=splitmod.DEFAULT_FRACTION)
    s.add_argument("--refreeze", action="store_true",
                   help="move the held-out set; voids every baseline")
    s.set_defaults(fn=cmd_split)

    v = sub.add_parser("verify", help="check the held-out split against its lock")
    v.set_defaults(fn=cmd_verify)

    pr = sub.add_parser("probe", help="one cheap request; check the backend is reachable")
    pr.add_argument("--base-url"); pr.add_argument("--model")
    pr.set_defaults(fn=cmd_probe)

    e = sub.add_parser("eval", help="step 2: pass@1 over the held-out split (detached)")
    e.add_argument("--baseline", action="store_true", help="record the result as THE baseline")
    e.add_argument("--label", default="")
    e.add_argument("--run-id")
    e.add_argument("--samples", type=int, default=1)
    e.add_argument("--concurrency", type=int, default=4)
    e.add_argument("--temperature", type=float, default=1.0)
    e.add_argument("--top-p", type=float, default=0.95)
    e.add_argument("--max-tokens", type=int, default=4096)
    e.add_argument("--max-spend", type=float, default=0.0, help="abort above this many dollars")
    e.add_argument("--price-hour", type=float, default=0.0,
                   help="dollars per GPU-hour, when you rent the box instead of the tokens")
    e.add_argument("--no-thinking", action="store_true")
    e.add_argument("--base-url"); e.add_argument("--model")
    e.add_argument("--foreground", action="store_true", help="used by the tmux launcher")
    e.set_defaults(fn=cmd_eval)

    sp = sub.add_parser("sample", help="rejection-sample verified SFT targets (train split only)")
    sp.add_argument("--k", type=int, default=16)
    sp.add_argument("--keep", type=int, default=2, help="max targets kept per case")
    sp.add_argument("--name", default="v1")
    sp.add_argument("--limit", type=int, default=0)
    sp.add_argument("--concurrency", type=int, default=16)
    sp.add_argument("--temperature", type=float, default=1.0)
    sp.add_argument("--top-p", type=float, default=0.95)
    sp.add_argument("--max-tokens", type=int, default=2048)
    sp.add_argument("--no-thinking", action="store_true")
    sp.add_argument("--price-hour", type=float, default=0.0)
    sp.add_argument("--max-spend", type=float, default=0.0)
    sp.add_argument("--base-url"); sp.add_argument("--model")
    sp.set_defaults(fn=cmd_sample)

    gr = sub.add_parser("grade", help="grade completions generated elsewhere (no GPU needed)")
    gr.add_argument("run_id"); gr.add_argument("--completions", required=True)
    gr.add_argument("--label", default=""); gr.add_argument("--model", default="")
    gr.set_defaults(fn=cmd_grade)

    sm = sub.add_parser("summarize", help="recompute a run's summary from its attempts")
    sm.add_argument("run_id"); sm.set_defaults(fn=cmd_summarize)

    pm = sub.add_parser("promote", help="make a finished run the baseline")
    pm.add_argument("run_id"); pm.set_defaults(fn=cmd_promote)

    st = sub.add_parser("status", help="one screen: corpus, baseline, running, spend")
    st.add_argument("--limit", type=int, default=10); st.set_defaults(fn=cmd_status)

    rs = sub.add_parser("results", help="leaderboard sorted by delta vs baseline")
    rs.set_defaults(fn=cmd_results)

    cp = sub.add_parser("compare", help="paired delta between two runs")
    cp.add_argument("before"); cp.add_argument("after")
    cp.add_argument("--ids", action="store_true", help="list the cases that moved")
    cp.set_defaults(fn=cmd_compare)

    sl = sub.add_parser("slices", help="pass rate per stratum for a run")
    sl.add_argument("run_id"); sl.add_argument("--fine", action="store_true",
                                               help="every refusal kind, not just the group")
    sl.set_defaults(fn=cmd_slices)

    sh = sub.add_parser("show", help="print the programs a run produced")
    sh.add_argument("run_id"); sh.add_argument("--case"); sh.add_argument("--limit", type=int, default=5)
    sh.add_argument("--failed-only", action="store_true"); sh.set_defaults(fn=cmd_show)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
