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

from . import engines as eng
from . import split as splitmod
from . import stats
from .adapters import parse_source
from .backends import BackendError, ChatBackend
from .evaluate import (Evaluation, load_holdout, score_verdict, scored_attempts,
                       summarize_run)
from .harvest import harvest
from .jsonio import read_json, read_jsonl, write_json

ROOT = Path(os.environ.get("NTX_ROOT") or Path(__file__).resolve().parents[1])
DATA = ROOT / "data"
RUNS = ROOT / "runs"
BASELINE = DATA / "baseline.json"


def _pct(x: float) -> str:
    return "n/a" if x != x else "%5.1f%%" % (100.0 * x)


def _progress(run_id: str) -> Dict[str, Any]:
    """A run's progress record, or an empty one — an absent file states nothing."""
    p = RUNS / run_id / "progress.json"
    if not p.exists():
        return {}
    try:
        return read_json(p)
    except (ValueError, OSError):
        return {}


def _backend_of(run_id: str) -> Dict[str, Any]:
    """What meta.json says answered this run; summary.json keeps only the model name."""
    p = RUNS / run_id / "meta.json"
    if not p.exists():
        return {}
    try:
        return read_json(p).get("backend") or {}
    except (ValueError, OSError):
        return {}


def _armed(summary: Dict[str, Any]) -> Dict[str, Any]:
    """A summary with the backend identity its run directory still remembers.

    Copied rather than mutated: these dicts come straight off disk and nothing
    here is entitled to write a recorded measurement back.
    """
    if not summary or summary.get("backend"):
        return summary
    b = _backend_of(summary.get("run_id") or "")
    return dict(summary, backend=b) if b else summary


def _summary_of(run_id: str) -> Optional[Dict[str, Any]]:
    p = RUNS / run_id / "summary.json"
    if not p.exists():
        return None
    try:
        return _armed(read_json(p))
    except (ValueError, OSError):
        return None


def _arm(summary: Dict[str, Any]) -> str:
    b = summary.get("backend") or {}
    return "%s @ %s" % (summary.get("model") or "model not recorded",
                        b.get("base_url") or b.get("source") or "endpoint not recorded")


def _val(v: Any) -> str:
    """One side of a difference, short enough to read. A trailing … marks a cut."""
    s = "%s" % v
    if len(s) > 16 and all(ch in "0123456789abcdef" for ch in s.lower()):
        return s[:12] + "…"
    return s if len(s) <= 48 else s[:45] + "…"


def _render_incomparable(recs: List[Dict[str, Any]],
                         left: str = "baseline", right: str = "run") -> List[str]:
    """Name the field and both its values — and never call an unknown a difference.

    "Unrecorded on both sides" when only one side is unrecorded is the same
    category of error as the silent subtraction this guard exists to stop.
    """
    out = []
    for d in recs:
        pair = "%s %s, %s %s" % (left, _val(d["baseline"]), right, _val(d["run"]))
        if not d.get("established"):
            missing = [name for name, side in ((left, "baseline"), (right, "run"))
                       if d[side] == stats.UNRECORDED]
            if len(missing) > 1:
                out.append("%s recorded by neither (%s)" % (d["field"], pair))
            else:
                out.append("%s not recorded by %s (%s)" % (d["field"], missing[0], pair))
        elif d.get("same_model_name"):
            out.append("different %s under one model name %s (%s)"
                       % (d["field"], d["same_model_name"], pair))
        else:
            out.append("different %s (%s)" % (d["field"], pair))
    return out


def _render_incomplete(recs: List[Dict[str, Any]]) -> List[str]:
    out = []
    for d in recs:
        if d["field"] == "cases_evaluated":
            out.append("partial run: %s of %s cases evaluated"
                       % (d["actual"], d["expected"]))
        elif d["field"] == "harness_errors":
            out.append("%s of %s cases evaluated; every missing one is inside the %s "
                       "attempt(s) excluded as harness errors — server or sandbox, "
                       "not the model"
                       % (d["actual"], d["expected"], d.get("harness_errors")))
        elif d["field"] == "aborted":
            out.append("aborted: %s" % d["actual"])
        else:
            out.append("run state %s, not done" % d["actual"])
    return out


def _promotion_bars(s: Dict[str, Any]) -> List[str]:
    """Why a run may not become the denominator of every later claim.

    A baseline is subtracted from for the life of the project, so the two things
    a candidate can never recover from once promoted are checked here: a number
    that does not cover the whole split, and a run that never wrote down how it
    sampled — which makes every later run n/c against it, with no way out but
    promoting something else.
    """
    why = _render_incomplete(stats.blocking(
        stats.completeness(s, _progress(s.get("run_id") or ""))))
    samp = s.get("sampling")
    samp = samp if isinstance(samp, dict) else {}
    missing = [k for k in stats.SAMPLING_KEYS if k not in samp]
    if missing:
        why.append("sampling not recorded (missing %s): every later run would be "
                   "not-comparable against it" % ", ".join(missing))
    return why


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
        # The same bar `nt promote` applies. A run that aborted on its spend cap
        # still reaches this line with a summary in hand, and the flag that asks
        # for a baseline must not be a way around the check that guards one.
        bars = _promotion_bars(_armed(summary))
        if bars:
            print("\nNOT written as the baseline:", file=sys.stderr)
            for w in bars:
                print("  %s" % w, file=sys.stderr)
            print("the run itself is recorded; fix what is named above and promote it "
                  "with `nt promote %s`" % summary["run_id"], file=sys.stderr)
            return 1
        write_json(BASELINE, summary)
        print("\nbaseline written to %s" % BASELINE)
        print("every later run is measured against pass_rate = %.4f" % summary["pass_rate"])
    return 0


def _ids(names: Optional[List[str]], limit: int = 8) -> str:
    """Name them, but never let one line become the whole report."""
    names = names or []
    head = ", ".join(names[:limit])
    return head if len(names) <= limit else "%s (+%d more)" % (head, len(names) - limit)


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
    if s.get("engine_mismatches"):
        # The denominator shrank; say so and say which cases, or the loudest
        # finding a run can produce becomes an unexplained gap in the count.
        out.append("MISMATCH   %d attempt(s) excluded — an engine disagreed with CPython, "
                   "which is a bug here, not the model: %s"
                   % (s["engine_mismatches"], _ids(s.get("engine_mismatch_cases"))))
    if s.get("non_genuine_passes"):
        out.append("recited    %d attempt(s) reproduced the expected output without "
                   "computing it, scored as not-genuine: %s"
                   % (s["non_genuine_passes"], _ids(s.get("non_genuine_cases"))))
    if s.get("authorship_undecided"):
        out.append("undecided  %d pass(es) the discriminator could not judge — scored as "
                   "they stand: %s"
                   % (s["authorship_undecided"], _ids(s.get("authorship_undecided_cases"))))
    if s.get("failures_by_category"):
        out.append("failures   " + "  ".join(
            "%s=%d" % (k, v) for k, v in sorted(s["failures_by_category"].items(),
                                                key=lambda kv: -kv[1])))
    out.append("cost       $%.4f   %d output tokens%s"
               % (s.get("spend_usd", 0.0), s.get("tokens_out", 0),
                  ("   wall %s" % _dur(elapsed)) if elapsed else ""))
    return "\n".join(out)


SAMPLING_HELP = (
    "state it one of three ways: --sampling '{\"temperature\": 1.0, ...}' (or a path "
    "to such a file) as an operator declaration; a header line {\"sampling\": {...}} "
    "with no case_id at the top of the completions file; or a \"sampling\" block on "
    "every completion record. Required keys: " + ", ".join(stats.SAMPLING_KEYS))


def _split_header(comps: List[Dict[str, Any]]) -> Tuple[Dict[str, Any],
                                                        List[Dict[str, Any]]]:
    """A leading record with no case_id is the file's header, not a completion."""
    if comps and "case_id" not in comps[0]:
        return comps[0], comps[1:]
    return {}, comps


def _declared_sampling(header: Dict[str, Any], comps: List[Dict[str, Any]],
                       override: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """How these completions were sampled, from the operator or from the file.

    Returns the sampling block and an empty reason, or None and the reason it
    could not be established. Only the box that generated the completions knows
    how it sampled them, and inventing a record here would let a run differing
    from the baseline in nothing but max_tokens be subtracted from it as if it
    were a model. An operator declaration is accepted because someone who knows
    can always assert it; it is recorded as a declaration so the summary says
    which of the two it was.
    """
    if override:
        try:
            text = override if override.lstrip().startswith("{") else \
                Path(override).read_text(encoding="utf-8")
            samp = json.loads(text)
        except (ValueError, OSError) as exc:
            return None, "--sampling is not readable JSON (%s)" % exc
        source = "operator"
    elif isinstance(header.get("sampling"), dict):
        samp, source = header["sampling"], "completions header"
    else:
        stated = [c["sampling"] for c in comps if isinstance(c.get("sampling"), dict)]
        if not stated or len(stated) != len(comps):
            return None, "the completions file does not state how it was sampled"
        if any(s != stated[0] for s in stated[1:]):
            return None, "the completions file states more than one sampling config"
        samp, source = stated[0], "completions records"
    if isinstance(samp, dict) and isinstance(samp.get("sampling"), dict):
        samp = samp["sampling"]
    if not isinstance(samp, dict):
        return None, "the declared sampling is not a JSON object"
    missing = [k for k in stats.SAMPLING_KEYS if k not in samp]
    if missing:
        return None, "the declared sampling is missing %s" % ", ".join(missing)
    return dict(samp, declared_by=source), ""


def cmd_grade(args: argparse.Namespace) -> int:
    """Grade completions produced elsewhere. The GPU box generates; this grades.

    The acceptance tests need the lypning engines and a sandbox, and both are
    CPU work. Renting eight H100s to run them would cost roughly twelve times
    what the same seconds cost here, so the box writes completions to a file and
    stops. Re-grading afterwards — after an engine rebuild, after a fixed test —
    then costs nothing and needs no GPU at all.
    """
    from .acceptance import run_test
    from .extract import extract_program
    from .jsonio import append_jsonl

    header, comps = _split_header(read_jsonl(args.completions))
    if not comps:
        print("no completions in %s" % args.completions, file=sys.stderr)
        return 1
    # Refused rather than warned, because a warning here buys a run that can
    # never be compared with anything: the arm and the decode budget are the two
    # facts the grader cannot recover from the completions. Re-grading is free
    # (that is the whole point of this command), so the cost of refusing is one
    # command, and the cost of accepting is a GPU run nobody may subtract.
    sampling, why = _declared_sampling(header, comps, args.sampling)
    if sampling is None:
        print("refusing to grade %s: %s" % (args.completions, why), file=sys.stderr)
        print("a run whose sampling is unrecorded can never be compared with the "
              "baseline — " + SAMPLING_HELP, file=sys.stderr)
        return 1
    model = args.model or header.get("model")
    if not model:
        print("refusing to grade %s: --model is required — it names the weights that "
              "produced these completions, and a run that cannot name its arm cannot "
              "be one" % args.completions, file=sys.stderr)
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
    # base_url is written only when someone states it. Left absent it is an
    # unknown that withholds a delta; filled in with the replay path it would be
    # a false endpoint that makes two grades of one arm look like two arms.
    backend = {"model": model, "source": "replay:%s" % args.completions}
    endpoint = args.endpoint or header.get("base_url")
    if endpoint:
        backend["base_url"] = endpoint
    meta = {"run_id": args.run_id, "label": args.label, "backend": backend,
            "prompt_sha": __import__("pipeline.evaluate", fromlist=["x"]).prompt_signature(),
            "holdout_manifest_sha256": (splitmod.load_lock(DATA) or {}).get("manifest_sha256", ""),
            "n_cases": len(cases), "sampling": dict(sampling, replayed=True),
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
            # The same scorer the eval runs, so that a replay of stored
            # completions cannot report a different pass rate than the run did.
            rec.update(score_verdict(case, program, run_test(case["test"], program)))
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
    disc = rep.get("discrimination") or {}
    if disc:
        print("kept draws by discrimination:   %s"
              % ", ".join("%s %d" % kv for kv in disc["kept_draws_by_verdict"].items()))
        print("SFT rows on textual evidence alone (no input to perturb)   %d of %d"
              % (disc["sft_rows_on_textual_evidence_only"], rep["sft_examples"]))
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
    s = _armed(read_json(run_dir / "summary.json"))
    bars = _promotion_bars(s)
    if bars:
        print("refusing to promote %s:" % args.run_id, file=sys.stderr)
        for w in bars:
            print("  %s" % w, file=sys.stderr)
        print("a baseline is the denominator of every later claim — re-run or re-grade "
              "this one, do not widen the check", file=sys.stderr)
        return 1
    write_json(BASELINE, s)
    print("baseline := %s   pass@1 %s   arm %s"
          % (args.run_id, _pct(s["pass_rate"]), _arm(s)))
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
    base = _armed(read_json(BASELINE)) if BASELINE.exists() else None
    rows: List[Dict[str, Any]] = []
    for p in sorted(RUNS.glob("*/summary.json")):
        try:
            rows.append(_armed(read_json(p)))
        except (ValueError, OSError):
            continue
    if not rows:
        print("no finished runs yet")
        return 0
    # Every number on this board is measured against the baseline, so a baseline
    # that does not cover the split makes every delta wrong rather than one.
    base_bars = _render_incomplete(stats.blocking(stats.completeness(
        base, _progress(base.get("run_id") or "")))) if base else []
    bp = base["pass_rate"] if (base and not base_bars) else None
    for r in rows:
        # A delta is only a delta if both numbers came from the same held-out
        # set, the same prompt and the same sampling config, from arms the record
        # can tell apart, and if both runs finished. Otherwise it is two
        # different measurements subtracted, which is worse than no number.
        r["_vs_base"] = (_render_incomparable(stats.comparability(base, r))
                         if (base and not base_bars) else [])
        own = stats.completeness(r, _progress(r.get("run_id") or ""))
        r["_bars"] = _render_incomplete(stats.blocking(own))
        r["_notes"] = _render_incomplete([d for d in own if not d.get("blocks")])
        r["_why"] = "; ".join(r["_vs_base"] + r["_bars"])
        ok = not r["_why"]
        r["_delta"] = (r["pass_rate"] - bp) if (bp is not None and ok) else 0.0
        r["_win"] = bool(bp is not None and ok and stats.beats(r, bp))
    rows.sort(key=lambda r: (bool(r["_why"]), -r["_delta"]))
    if base:
        print("baseline   %s   run %s   (a win needs CI-low above this)"
              % (_pct(base["pass_rate"]), base["run_id"]))
        print("           arm %s" % _arm(base))
    for w in base_bars:
        print("baseline   UNUSABLE — %s" % w)
    if base_bars:
        print("           no delta on this board means anything until the baseline "
              "is re-run and re-promoted")
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
    for r in rows:
        for w in r["_bars"]:
            print("  n/c %s: %s" % (r["run_id"], w))
        for w in r["_vs_base"]:
            print("  n/c %s: not comparable to the baseline — %s" % (r["run_id"], w))
        for w in r["_notes"]:
            print("  note %s: %s" % (r["run_id"], w))
    if any(r["_why"] for r in rows):
        print("  a withheld delta is not a zero: those runs measured something else.")
    print("\narms (as requested; the model name is free text and two endpoints here "
          "answer to one)")
    for r in rows:
        print("  %-26s %s" % (r["run_id"][:26], _arm(r)))
    comparable = [r for r in rows if not r["_why"]]
    if bp is not None and comparable and not any(r["_win"] for r in comparable):
        print("\nno run clears the bar: a win needs its CI lower bound above %s."
              % _pct(bp))
    return 0


def _per_case(run_id: str) -> Dict[str, float]:
    attempts = scored_attempts(read_jsonl(RUNS / run_id / "attempts.jsonl"))
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
    b_sum, a_sum = _summary_of(args.before), _summary_of(args.after)
    bars: List[str] = []
    for run_id, s in ((args.before, b_sum), (args.after, a_sum)):
        if s is not None:
            bars.extend("%s: %s" % (run_id, w) for w in _render_incomplete(
                stats.blocking(stats.completeness(s, _progress(run_id)))))
    if b_sum is not None and a_sum is not None:
        bars.extend(_render_incomparable(stats.comparability(b_sum, a_sum),
                                         left="before", right="after"))
    print("before %s   after %s" % (args.before, args.after))
    if bars and not args.anyway:
        print("refusing to compare %s with %s — they did not measure the same thing:"
              % (args.before, args.after), file=sys.stderr)
        for w in bars:
            print("  %s" % w, file=sys.stderr)
        print("  --anyway prints the arithmetic, which is then the difference between "
              "two runs and not a delta attributable to the model", file=sys.stderr)
        return 1
    for w in bars:
        print("WARNING  %s" % w)
    dropped = d["dropped_before_only"] + d["dropped_after_only"]
    print("paired delta %+.1fpp   95%% CI [%+.1f, %+.1f]pp   over %d cases%s"
          % (100 * d["delta"], 100 * d["ci95"]["lo"], 100 * d["ci95"]["hi"], d["n_pairs"],
             ("   (%d case(s) dropped: %d only in %s, %d only in %s)"
              % (dropped, d["dropped_before_only"], args.before,
                 d["dropped_after_only"], args.after)) if dropped else ""))
    # Separability is a property of the numbers and holds either way; which model
    # it is a verdict ON is exactly what an incomparable pair does not establish.
    if bars:
        verdict = ("separable from noise, but between runs that differ as above"
                   if d["significant"] else "not separable from noise")
    else:
        verdict = {"improvement": "SIGNIFICANT IMPROVEMENT",
                   "regression": "SIGNIFICANT REGRESSION",
                   "none": "not separable from noise"}[d["direction"]]
    print("gained %d   lost %d   McNemar p=%.4f   %s"
          % (d["gained"], d["lost"], d["mcnemar_p"], verdict))
    if args.ids:
        print("gained:", ", ".join(d["gained_ids"]))
        print("lost  :", ", ".join(d["lost_ids"]))
    base = _armed(read_json(BASELINE)) if BASELINE.exists() else None
    if base is None or a_sum is None:
        return 0
    # The unpaired rule is a second comparison — after against the BASELINE, not
    # against before — so it carries its own refusal. `nt results` and this line
    # ran off the same files and printed opposite verdicts until it did.
    stop = _render_incomparable(stats.comparability(base, a_sum), right="after")
    stop += ["%s: %s" % (args.after, w) for w in _render_incomplete(
        stats.blocking(stats.completeness(a_sum, _progress(args.after))))]
    stop += ["baseline %s: %s" % (base.get("run_id"), w) for w in _render_incomplete(
        stats.blocking(stats.completeness(base, _progress(base.get("run_id") or ""))))]
    if stop:
        print("unpaired rule: n/c against the baseline — %s" % "; ".join(stop))
    else:
        print("unpaired rule: CI-low %s vs baseline point %s -> %s"
              % (_pct(a_sum["ci95"]["lo"]), _pct(base["pass_rate"]),
                 "WIN" if stats.beats(a_sum, base["pass_rate"]) else "no"))
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
    attempts = scored_attempts(read_jsonl(run_dir / "attempts.jsonl"))
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
        # Ordered by case id, as summarize_run orders it. The bootstrap draws
        # from this sequence with a fixed seed, so an order that follows thread
        # completion made the published interval depend on which attempt landed
        # first — the one thing stats.py's docstring promises it does not.
        scores = [c[0] / c[1] for _, c in sorted(per_case.items())]
        draws = sum(c[1] for c in per_case.values())
        b = stats.summarize(scores)
        pk = stats.pass_at_k([(c[0], c[1]) for c in per_case.values()])
        print("%-26s %5d %6d %9s [%s, %s] %8s %9s"
              % (key, len(scores), draws, _pct(b["pass_rate"]),
                 _pct(b["ci95"]["lo"]).strip(), _pct(b["ci95"]["hi"]).strip(),
                 _pct(pk["pass_at_k"]) if pk["k"] > 1 else "-",
                 ("%+.1fpp" % (100 * pk["headroom"])) if pk["k"] > 1 else "-"))
        # "pass@k" names the largest k any case got. When the draws are ragged
        # the short cases had fewer chances and the column overstates nothing
        # about them — it just is not one k.
        if pk["ragged"]:
            print("%-26s   draws per case run %d..%d, so the pass@k column is not "
                  "one k" % ("", pk["k_min"], pk["k"]))
    return 0


def cmd_refusals(args: argparse.Namespace) -> int:
    """Rank what the engine still refuses by how much of the corpus it blocks.

    This is the $0 instrument between eval runs. `eval` measures the model
    against a fixed engine; this measures the ENGINE against a fixed corpus, and
    the two move the same number from opposite ends — a construct delivered here
    retires the cases that asked the model to route around it.
    """
    from . import refusals
    engine = args.engine or eng.engine_path("lypning-l") or eng.engine_path("lypning")
    if not engine:
        print("no lypning binary on this machine: run `lypning build --rust`, "
              "or pass --engine", file=sys.stderr)
        return 1
    if args.run:
        attempts = list(read_jsonl(RUNS / args.run / "attempts.jsonl"))
        result = refusals.on_policy(attempts, engine)
        print(refusals.on_policy_report(result, limit=args.limit or 12))
        return 1 if result["tally"].get("MISMATCH") else 0
    cases = list(read_jsonl(DATA / "corpus.jsonl"))
    if args.held_out or args.train:
        held = {c["id"] for c in read_jsonl(DATA / "holdout.jsonl")}
        cases = [c for c in cases if (c["id"] in held) == bool(args.held_out)]
    print(refusals.report(refusals.census(cases, engine),
                          limit=args.limit, show_details=args.details))
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
    gr.add_argument("--sampling", default="",
                    help="JSON, or a path to it, stating %s for these completions"
                         % ", ".join(stats.SAMPLING_KEYS))
    gr.add_argument("--endpoint", default="",
                    help="where the completions were generated; recorded as the arm")
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
    cp.add_argument("--anyway", action="store_true",
                    help="print the arithmetic for two runs that measured different "
                         "things; it is then not a delta attributable to the model")
    cp.set_defaults(fn=cmd_compare)

    sl = sub.add_parser("slices", help="pass rate per stratum for a run")
    sl.add_argument("run_id"); sl.add_argument("--fine", action="store_true",
                                               help="every refusal kind, not just the group")
    sl.set_defaults(fn=cmd_slices)

    rf = sub.add_parser("refusals", help="rank what the engine still refuses, by corpus weight")
    rf.add_argument("--engine", help="binary to probe (default: the widest built variant)")
    rf.add_argument("--details", action="store_true", help="the exact refusal line under each kind")
    rf.add_argument("--limit", type=int, default=0, help="show only the top N kinds")
    rf.add_argument("--run", metavar="RUN_ID",
                    help="census the programs a MODEL wrote in this run, not the corpus "
                         "negatives — the population `conformance` cannot see. Exits 1 on "
                         "any MISMATCH.")
    g = rf.add_mutually_exclusive_group()
    g.add_argument("--held-out", action="store_true", help="only the frozen held-out split")
    g.add_argument("--train", action="store_true", help="only the train split")
    rf.set_defaults(fn=cmd_refusals)

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
