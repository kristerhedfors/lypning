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
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import engines as eng
from . import sample as sample_mod
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


def _engine_of(run_id: str) -> Dict[str, Any]:
    """Which engine graded this run, from meta.json. Absent for runs graded
    before the field existed, and an absent engine is an unknown, never a
    difference — `stats._engine` says why."""
    p = RUNS / run_id / "meta.json"
    if not p.exists():
        return {}
    try:
        return read_json(p).get("engine") or {}
    except (ValueError, OSError):
        return {}


def _armed(summary: Dict[str, Any]) -> Dict[str, Any]:
    """A summary with the backend and engine identities its run directory still
    remembers.

    Copied rather than mutated: these dicts come straight off disk and nothing
    here is entitled to write a recorded measurement back.
    """
    if not summary:
        return summary
    out = summary
    if not out.get("backend"):
        b = _backend_of(out.get("run_id") or "")
        if b:
            out = dict(out, backend=b)
    if not out.get("engine"):
        e = _engine_of(out.get("run_id") or "")
        if e:
            out = dict(out, engine=e)
    return out


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


#: What `nt harvest` runs when told nothing. It used to be `["study"]` alone,
#: which is 26 cases; the corpus it replaces was 223 `lypning` cases and 26
#: `study` ones, so the bare command silently destroyed 90% of the corpus and
#: broke the held-out lock (2026-09-13, recovered from git). A default that
#: cannot rebuild what is on disk is not a default, it is a trap.
DEFAULT_SOURCES = ["lypning", "study"]


def cmd_harvest(args: argparse.Namespace) -> int:
    try:
        sources = [parse_source(s) for s in (args.source or DEFAULT_SOURCES)]
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

    try:
        ledger = harvest(sources, out_dir=DATA, jobs=args.jobs,
                         allow_no_witness=args.allow_no_witness,
                         allow_holdout_loss=args.allow_holdout_loss,
                         progress=progress)
    except ValueError as exc:
        print("%s" % exc, file=sys.stderr)
        return 1
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
    # The generating box states which prompt it rendered; this box stamps the run
    # with the prompt IT would render. Left uncompared, a GPU job running an older
    # template produces a run whose recorded prompt_sha is a prompt it never sent,
    # and `stats.comparability`'s prompt-drift guard reads the stamp, so it sees
    # nothing. Both arms drift together, so the paired delta survives -- but it is
    # then a delta on an unrecorded prompt, and every comparison against a run
    # graded at another template is wrong while claiming to be checked.
    local_sha = __import__("pipeline.evaluate", fromlist=["x"]).prompt_signature()
    stated_sha = header.get("prompt_sha")
    if stated_sha and stated_sha != local_sha:
        print("refusing to grade %s: it was generated from prompt %s and this tree "
              "renders %s.\n  The run would be stamped with a prompt it never saw. "
              "Grade at the tree that generated it, or regenerate."
              % (args.completions, stated_sha, local_sha), file=sys.stderr)
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
    # F2, and the reason it is a PRECONDITION rather than a note. Under a
    # correctness endpoint a moving engine is a confound the recorded
    # fingerprint can flag afterwards. Under a legality endpoint it is
    # DEFINITIONAL — SLR is measured relative to what this binary accepts — so
    # an arm graded by a different engine is not a comparable arm, and finding
    # that out after the grade means re-grading both arms or throwing the
    # comparison away. Pinning costs one flag; not pinning has already cost this
    # repository two features chosen off a held-out ranking.
    if args.require_fingerprint:
        live = eng.identity()["fingerprint"]
        if live != args.require_fingerprint:
            print("refusing to grade: this box's engine is %s, and the run was "
                  "pinned to %s. Rebuild that engine or drop the pin — do not "
                  "compare two arms across two engines."
                  % (live, args.require_fingerprint), file=sys.stderr)
            return 1
    backend = {"model": model, "source": "replay:%s" % args.completions}
    endpoint = args.endpoint or header.get("base_url")
    if endpoint:
        backend["base_url"] = endpoint
    meta = {"run_id": args.run_id, "label": args.label, "backend": backend,
            "prompt_sha": local_sha,
            "holdout_manifest_sha256": (splitmod.load_lock(DATA) or {}).get("manifest_sha256", ""),
            # Re-grading is free, so it happens often, and every re-grade is a
            # different engine until someone proves otherwise. This is the proof.
            "engine": eng.identity(),
            "n_cases": len(cases), "sampling": dict(sampling, replayed=True),
            "started_at": __import__("pipeline.evaluate", fromlist=["x"])._now()}
    write_json(run_dir / "meta.json", meta)

    # A completion that spent the whole decode budget was CUT, not finished, and
    # the replay path has no `finish_reason` to say so — only the token count and
    # the budget, which together say it exactly. Recorded because the cap is not
    # neutral between arms and the asymmetry runs the same way the treatment
    # does: a fine-tune that makes the model terser hits the cap less often, so
    # scoring a truncation as `syntax-error` credits the tuned arm for the
    # control's verbosity. Measured 2026-09-14 over the three graded arms: base
    # 51/1184 (4.3%), tuned 23/1184 (1.9%), hinted 16/1184 (1.4%), and NONE of
    # the 90 passed. The verdict is unchanged — a program that does not fit the
    # budget did fail — but it is counted under its own name so the rate is on
    # the face of the summary instead of buried in two other categories.
    cap = int(sampling.get("max_tokens") or 0)

    for i, c in enumerate(comps, 1):
        case = cases[c["case_id"]]
        program, how = extract_program(c.get("text") or "", c.get("reasoning"))
        used = int(c.get("completion_tokens") or 0)
        truncated = bool(cap and used >= cap)
        rec = {"run_id": args.run_id, "case_id": c["case_id"], "sample": c.get("sample", 0),
               "how": how, "program": program or "",
               "completion_tokens": used, "truncated": truncated,
               "cost_usd": 0.0, "ts": meta["started_at"]}
        if program is None:
            rec.update(passed=False, reason="no-code", detail="", failure_category="no-code")
        else:
            # The same scorer the eval runs, so that a replay of stored
            # completions cannot report a different pass rate than the run did.
            rec.update(score_verdict(case, program, run_test(case["test"], program)))
        if truncated and not rec.get("passed"):
            was = rec.get("failure_category") or "?"
            rec["failure_category"] = "truncated"
            rec["detail"] = "cut at the %d-token budget; would have scored %s" % (cap, was)
        append_jsonl(run_dir / "attempts.jsonl", rec)
        if i % 200 == 0:
            print("  graded %d/%d" % (i, len(comps)), flush=True)

    print(render_summary(summarize_run(run_dir)))
    return 0


def _mixture(census: Dict[str, int]) -> str:
    """A population census, always in the same order, never as a bare total."""
    return "  ".join("%s %d" % (name, census.get(name, 0))
                     for name in ("rewrite", "ceiling", "unobserved"))


def cmd_sample(args: argparse.Namespace) -> int:
    """Rejection-sample verified SFT targets from the TRAIN split only.

    The pool is printed before a dollar is spent, because the mixture is the
    experiment: three populations live in this corpus and only one of them is
    the task (`sample.population`).
    """
    from .sample import sample_targets, sampling_pool
    engine = args.engine or eng.engine_path("lypning-l") or eng.engine_path("lypning")
    if not engine:
        print("no lypning binary: run `lypning build --rust`, or pass --engine",
              file=sys.stderr)
        return 1
    try:
        pool = sampling_pool(DATA, engine)
    except (ValueError, AssertionError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    cases = pool["cases"]
    print("train %d cases  %s" % (pool["train"], _mixture(pool["train_census"])))
    for label, ids in (("the same question as a held-out case", pool["dropped_leaking"]),
                       ("not the task (see sample.TRAINABLE)", pool["dropped_population"]),
                       ("degenerate: the given program passes as-is", pool["dropped_degenerate"]),
                       ("unsatisfiable: nothing passes it", pool["dropped_unsatisfiable"])):
        if ids:
            print("  -%-4d %s" % (len(ids), label))
    print("pool  %d cases  %s   (engine %s)"
          % (len(cases), _mixture(pool["census"]), pool["engine"]))
    if not cases:
        print("nothing left to sample", file=sys.stderr)
        return 1
    if args.limit:
        cases = cases[: args.limit]
    try:
        backend = _backend(args)
    except BackendError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    # The cap that is not a cap. `ChatBackend.cost()` multiplies by NTX_PRICE_IN
    # and NTX_PRICE_OUT; unset they are 0.0, every draw records cost_usd 0.0, and
    # `--max-spend` can never trip -- which is how the first SFT set was drawn
    # (`PREREGISTRATION.md` §2(f)). A cap the operator believes in and that
    # cannot fire is worse than no cap, so it is an error rather than a warning.
    if args.max_spend and not (backend.price_in or backend.price_out or args.price_hour):
        print("--max-spend $%.2f cannot fire: NTX_PRICE_IN/NTX_PRICE_OUT are unset and "
              "--price-hour is 0, so every draw costs a recorded $0.00.\n"
              "  export NTX_PRICE_IN and NTX_PRICE_OUT (per 1M tokens), or pass "
              "--price-hour, or drop --max-spend and say out loud that the run is "
              "unbounded." % args.max_spend, file=sys.stderr)
        return 2
    hints_by_case = None
    if args.hinted:
        from . import hints as hints_mod
        recipes = hints_mod.load_cookbook()
        engine_name = Path(engine).name
        hints_by_case, why = {}, {}
        for case in cases:
            hint, reason = hints_mod.hint_for(case, engine, engine_name, recipes)
            if hint:
                hints_by_case[case["id"]] = hint
            why[reason] = why.get(reason, 0) + 1
        print("hinted sampling: %d of %d cases carry a live refusal from %s "
              "(%d recipes in the cookbook)"
              % (len(hints_by_case), len(cases), engine_name, len(recipes)))
        for reason, n in sorted(why.items(), key=lambda kv: -kv[1])[:8]:
            print("  %5d  %s" % (n, reason))
        print("  the hint reaches the DRAW only — the SFT row and the eval keep the "
              "bare prompt, which is what makes this context distillation")
        if not hints_by_case:
            print("no case has a refusal to quote: --hinted would change nothing",
                  file=sys.stderr)
            return 1

    out_dir = DATA / "sft" / (args.name or "v1")
    prior = out_dir / "draws.jsonl"
    n_prior = sum(1 for _ in open(prior, encoding="utf-8")) if prior.exists() else 0
    print("sampling %d cases x k=%d  (held-out %d cases are excluded by the lock)"
          % (len(cases), args.k, len((splitmod.load_lock(DATA) or {}).get("holdout", []))))
    if n_prior and not args.resume:
        print("%s already holds %d draws.\n"
              "  fold_draws reads ALL of them, so sampling here mixes them into this "
              "run's sft.jsonl and into its yield report.\n"
              "  --resume to continue that run (same model only), or --name something "
              "else to start a new one." % (prior, n_prior), file=sys.stderr)
        return 1
    if args.dry_run:
        planned = len(cases) * args.k
        print("DRY RUN: %d draws would be sent to %s (%s); nothing has been spent"
              % (planned, backend.base_url, backend.model))
        if backend.price_in or backend.price_out:
            # max_tokens is a ceiling, so this is the ceiling too, and is labelled
            # as one rather than offered as an estimate nobody can hold anyone to.
            ceiling = planned * (backend.price_out * args.max_tokens) / 1e6
            print("        at NTX_PRICE_OUT=%s that is at most $%.2f of completion "
                  "tokens (every draw hitting --max-tokens %d), plus prompt tokens"
                  % (backend.price_out, ceiling, args.max_tokens))
        else:
            print("        cost unknown: NTX_PRICE_IN/NTX_PRICE_OUT are unset")
        return 0

    def progress(done: int, total: int, rec: Dict[str, Any]) -> None:
        if done % 100 == 0:
            print("  %d/%d" % (done, total), flush=True)

    try:
        rep = sample_targets(
            backend, cases, out_dir, k=args.k, keep=args.keep,
            ceiling_keep=args.ceiling_keep, temperature=args.temperature,
            top_p=args.top_p, max_tokens=args.max_tokens,
            enable_thinking=not args.no_thinking, concurrency=args.concurrency,
            price_hour=args.price_hour, max_spend=args.max_spend, resume=args.resume,
            hints_by_case=hints_by_case,
            progress=progress)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print()
    if rep.get("resumed_draws"):
        print("resumed: %d draws were already recorded, %d were drawn now"
              % (rep["resumed_draws"], rep.get("drawn_this_run") or 0))
    print("cases with a verified solution  %d / %d   (%.1f%%)"
          % (rep["cases_with_a_verified_solution"], rep["cases"], 100 * rep["yield_rate"]))
    print("SFT examples written            %d" % rep["sft_examples"])
    print("   of them ON TASK (rewrite)    %d   <- the number the abandon "
          "threshold is about" % rep["sft_examples_on_task"])
    for name, e in sorted(rep.get("by_population", {}).items()):
        print("   %-26s %3d rows from %d/%d cases"
              % (name, e["rows"], e["solved"], e["cases"]))
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


def _denominators(engine: Optional[str]) -> List[Tuple[str, List[str], bool]]:
    """`(label, excluded ids, is_primary)` — the pre-registered denominators.

    Computed from the engine, never a hardcoded list of ids: the criterion is
    mechanical (`refusals.usable_cases`) precisely so that which cases count is
    not a thing anyone chooses after seeing a score.
    """
    from . import refusals
    cases = list(read_jsonl(DATA / "holdout.jsonl"))
    if not engine:
        return [("all cases", [], True)]
    r = refusals.usable_cases(cases, engine)
    deg = list(r["degenerate"])
    unsat = [row["id"] for row in r["unsatisfiable"]]
    return [
        ("all %d (secondary)" % len(cases), [], False),
        ("%d non-degenerate (PRIMARY)" % (len(cases) - len(deg)), deg, True),
        ("%d usable" % len(r["usable"]), deg + unsat, False),
    ]


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
    # And a THIRD engine, which is easy to miss because it is not in either run:
    # `_denominators` asks the live binary which held-out cases are degenerate,
    # so a box whose engine has moved since the runs were graded defines the
    # PRIMARY denominator with one engine and fills it with scores from another.
    if not args.no_rule:
        live_id = eng.identity()
        live = (live_id.get("fingerprint") if any(
            v.get("found") for v in (live_id.get("chain") or {}).values()) else "")
        for run_id, s in ((args.before, b_sum), (args.after, a_sum)):
            fp = ((s or {}).get("engine") or {}).get("fingerprint")
            if live and fp and fp != live:
                bars.append(
                    "%s was graded by engine %s, and the denominators below are "
                    "computed by %s, the engine on this box — re-grade against "
                    "one engine, or pass --no-rule and lose the rule"
                    % (run_id, fp, live))
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
    # THE PRE-REGISTERED RULE, over every denominator it names. It lived only in
    # PREREGISTRATION.md until 2026-09-12 and was applied by hand each time,
    # which is the shape of a rule nobody can be held to.
    if not args.no_rule:
        engine = eng.engine_path("lypning-l") or eng.engine_path("lypning")
        if not engine:
            print("\nrule: n/c — no lypning binary, so the denominators cannot "
                  "be computed (`lypning build --rust`)")
        else:
            print()
            print("the pre-registered rule — a win needs BOTH legs "
                  "(PREREGISTRATION.md §3)")
            print("  %-28s %4s %8s %8s %9s %-18s %8s  %s"
                  % ("denominator", "n", "before", "after", "delta", "95% CI",
                     "McNemar", "verdict"))
            for label, drop, primary in _denominators(engine):
                v = stats.decide(before, after, exclude=drop)
                if not v.get("n_pairs"):
                    print("  %-28s %s" % (label, v.get("why")))
                    continue
                print("  %-28s %4d %7.2f%% %7.2f%% %+8.2fpp [%+.2f,%+.2f]%s %7.4f  %s%s"
                      % (label, v["n_pairs"], 100 * v["before_point"],
                         100 * v["after_point"], 100 * v["delta"],
                         100 * v["ci95"]["lo"], 100 * v["ci95"]["hi"], " " * 3,
                         v["mcnemar_p"],
                         "WIN" if v["fires"] else "no",
                         "  <-- primary" if primary else ""))
                if primary and not v["fires"]:
                    print("  %-28s   %s" % ("", v["why"]))
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


def _programs_of(name: str) -> Optional[Path]:
    """Where the recorded programs of ``name`` are — a run, a draw pool, or a file.

    The train pool is not a run and never was: it is the rejection sampler's own
    draws, sixteen per case, written beside the SFT set they were filtered into.
    Reachability is the first question asked of BOTH populations, so resolving
    both here is what stops the answer for one of them being a second command
    that drifts.
    """
    for path in (RUNS / name / "attempts.jsonl", DATA / "sft" / name / "draws.jsonl",
                 Path(name)):
        if path.is_file():
            return path
    return None


def _case_context(no_context: bool) -> Tuple[Dict[str, Dict[str, Any]],
                                             Optional[Dict[str, Dict[str, Any]]]]:
    """Every case this tree knows, and the per-case test context to replay under."""
    cases: Dict[str, Dict[str, Any]] = {}
    for path in (DATA / "holdout.jsonl", DATA / "train.jsonl", DATA / "corpus.jsonl"):
        if path.exists():
            for c in read_jsonl(path):
                cases.setdefault(c["id"], c)
    if no_context:
        return cases, None
    return cases, {cid: c.get("test") or {} for cid, c in cases.items()}


def cmd_legality(args: argparse.Namespace) -> int:
    """Subset-legality rate between two runs: what fraction of what a model writes runs.

    Costs CPU and nothing else. Every program was already generated and paid
    for; this replays them through the pinned engine and asks the question the
    correctness endpoint cannot isolate — not "is it right" but "will it run".

    ``--pass-at-k`` asks the prior question of one population instead of the
    delta between two: not how often a draw is legal, but whether ANY draw is —
    which is the ceiling on every reward-based stage and costs the same nothing.
    """
    from . import legality
    engine = args.engine or eng.engine_path("lypning-l") or eng.engine_path("lypning")
    if not engine:
        print("no lypning binary on this machine: run `lypning build --rust`, "
              "or pass --engine", file=sys.stderr)
        return 1
    cases, tests = _case_context(args.no_context)
    if args.pass_at_k:
        return _reachability(args, engine, cases, tests)
    if not args.after:
        print("legality needs two runs to compare; one run is `--pass-at-k`",
              file=sys.stderr)
        return 2
    arms = {}
    for run_id in (args.before, args.after):
        path = RUNS / run_id / "attempts.jsonl"
        if not path.exists():
            print("no such run: %s" % run_id, file=sys.stderr)
            return 1
        cache = (Path(args.cache) / ("%s.replay.json" % run_id)) if args.cache else None
        arms[run_id] = legality.arm(list(read_jsonl(path)), engine, tests=tests,
                                    workers=args.jobs, cache=cache)
    try:
        cmp = legality.compare(arms[args.before], arms[args.after],
                               (None if args.no_context else (cases or None)),
                               engine=engine, gate_a=args.gate_a,
                               gate_b=args.gate_b, gate_c=args.gate_c)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(legality.report(cmp, before=args.before, after=args.after,
                          limit=args.limit, mde=args.mde))
    if args.json:
        blob = dict(cmp)
        blob.pop("gates_pass", None)
        Path(args.json).write_text(
            json.dumps(blob, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return 0 if (cmp["gates_pass"] and not cmp["mismatch"]) else 1


def _reachability(args: argparse.Namespace, engine: str,
                  cases: Dict[str, Dict[str, Any]],
                  tests: Optional[Dict[str, Dict[str, Any]]]) -> int:
    """Stage 0a: how much of a pool the model can already reach, by refusal kind.

    Every population named on the command line gets its own table. They are not
    differenced and deliberately so — the train pool and the held-out set have
    different case mixes, and a delta between two mixes is a statement about the
    mixes.
    """
    from . import legality
    held = set()
    lock = splitmod.load_lock(DATA)
    if lock:
        held = {e["id"] for e in lock.get("holdout", [])}
    kinds = {cid: c.get("category") or "unknown" for cid, c in cases.items()}
    worst = 0
    for i, name in enumerate([n for n in (args.before, args.after) if n]):
        path = _programs_of(name)
        if path is None:
            print("no run, draw pool or file called %s" % name, file=sys.stderr)
            return 1
        draws = [d for d in read_jsonl(path) if d.get("program")]
        if not draws:
            print("%s: no programs recorded" % name, file=sys.stderr)
            return 1
        cache = (Path(args.cache) / ("%s.replay.json" % name)) if args.cache else None
        r = legality.reachability(draws, engine, tests=tests, kinds=kinds,
                                  workers=args.jobs, cache=cache)
        if i:
            print()
        label = name if str(path) == name else "%s (%s)" % (name, path.name)
        print(legality.reachability_report(
            r, label=label, limit=args.limit,
            floor=args.floor,
            held_out=bool(held) and {d.get("case_id") for d in draws} <= held))
        if args.json:
            out = Path(args.json)
            if len([n for n in (args.before, args.after) if n]) > 1:
                # A population can be named by a path, and a path is not a
                # filename component: `with_name` refuses one, after the replay
                # it was going to record has already been paid for.
                slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(name).name) or "arm"
                out = out.with_name("%s.%s%s" % (out.stem, slug, out.suffix))
            blob = dict(r)
            out.write_text(json.dumps(blob, indent=1, sort_keys=True, default=str),
                           encoding="utf-8")
        # A pool below the floor is the stop this stage exists to raise, and a
        # stop the caller cannot see in an exit code is a stop nobody scripts on.
        if r["mismatch"] or r["reachable"] < args.floor:
            worst = 1
    return worst


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


def cmd_power(args: argparse.Namespace) -> int:
    """What this held-out set can see, before anything is spent on making it move.

    A rule that cannot detect the effect being paid for reports "no win"
    whatever happens, and the money goes either way. Run this BEFORE the run:
    a rule chosen once the outcome is visible is not a rule.
    """
    run_dir = RUNS / args.run_id
    attempts = scored_attempts(read_jsonl(run_dir / "attempts.jsonl"))
    per: Dict[str, List[int]] = {}
    for a in attempts:
        c = per.setdefault(a["case_id"], [0, 0])
        c[1] += 1
        if a.get("passed"):
            c[0] += 1
    scores = [p / n for _, (p, n) in sorted(per.items())]
    if not scores:
        print("no scored attempts in %s" % args.run_id, file=sys.stderr)
        return 1
    k = max(n for _, n in per.values())
    curve = stats.power_curve(scores, k, trials=args.trials)

    print("power of the held-out set, from %s (%d cases, k=%d, %d simulated runs per row)"
          % (args.run_id, curve["n_cases"], curve["k"], curve["trials"]))
    print("  baseline point %.4f   never passes %d   always passes %d   movable %d"
          % (curve["baseline_point"], curve["never_passes"], curve["always_passes"],
             curve["n_cases"] - curve["never_passes"] - curve["always_passes"]))
    print()
    print("  %-10s %9s   %9s %9s %9s" % ("true lift", "unpaired",
                                          "boot-leg", "McNemar", "PRIMARY"))
    for row in curve["rows"]:
        print("  %+8.0fpp %9.0f%%   %9.0f%% %9.0f%% %9.0f%%"
              % (100 * row["lift"], 100 * row["unpaired_power"],
                 100 * row["bootstrap_power"], 100 * row["mcnemar_power"],
                 100 * row["paired_power"]))
    print()
    for rule in ("unpaired", "paired"):
        mde = stats.minimum_detectable(curve, rule)
        print("  %-9s reaches 80%% power at %s"
              % ("PRIMARY" if rule == "paired" else rule,
                 ("%+.0fpp" % (100 * mde)) if mde is not None else
                 "no lift on this grid — it cannot see one"))
    floor = curve.get("min_gained_if_none_lost")
    if floor:
        print()
        print("  AND A DISCRETE FLOOR THE PERCENTAGE HIDES. The primary rule is the")
        print("  CONJUNCTION of both legs, and exact McNemar over b gained and c lost")
        print("  is 2/2**b when nothing is lost: p = 0.0625 at five cases, %.5f at %d."
              % (stats._mcnemar(floor, 0), floor))
        print("  So a fine-tune that flips FEWER THAN %d cases and loses none cannot" % floor)
        print("  fire this rule at any effect size. The question the rule really asks")
        print("  is how many CASES moved, not how far the mean did.")
    print()
    print("  The lift is applied uniformly to every case, which is the most")
    print("  favourable shape an improvement can take, so these are an UPPER")
    print("  bound on power rather than an estimate of it.")
    return 0


def cmd_leaks(args: argparse.Namespace) -> int:
    """Train cases that are the same question as a held-out one.

    Disjoint ids are not an independence proof. Run this before sampling, and
    read it as a property of the CORPUS rather than of the splitter: a
    capture-derived corpus contains the same wall hit twice.
    """
    cases = list(read_jsonl(DATA / "corpus.jsonl"))
    lock = splitmod.load_lock(DATA)
    if lock is None:
        print("the split is not frozen", file=sys.stderr)
        return 1
    held = {e["id"] for e in lock["holdout"]}
    if args.sft:
        # The direct question, asked of the targets themselves rather than of
        # the cases they came from: does a program we are about to train on
        # already pass a held-out case? Exit 1 if any does — it is the one
        # finding here that must stop a training run.
        rows = list(read_jsonl(Path(args.sft) if Path(args.sft).suffix == ".jsonl"
                               else Path(args.sft) / "sft.jsonl"))
        r = splitmod.sft_solves_holdout(rows, [c for c in cases if c["id"] in held])
        print("%d SFT rows against %d held-out cases (%d distinct inputs probed)"
              % (r["rows"], r["n_holdout"], r["inputs_probed"]))
        if not r["solved"]:
            print("no training target passes a held-out case")
            return 0
        print("CONTAMINATED: %d of %d rows are verified solutions to %d held-out cases"
              % (r["rows_solving"], r["rows"], len(r["holdout_cases_solved"])))
        for pair in sorted(set((h["holdout_id"], h["row_case_id"]) for h in r["solved"])):
            print("  held-out %s  is solved by a target sampled for %s" % pair)
        return 1
    report = splitmod.cross_split_leaks([c for c in cases if c["id"] not in held],
                                        [c for c in cases if c["id"] in held],
                                        ceiling=args.ceiling)
    print("train %d, held-out %d, similarity ceiling %.2f"
          % (report["n_train"], report["n_holdout"], report["ceiling"]))
    print("%d train cases leak; %d are clean and may be sampled"
          % (len(report["leaks"]), len(report["clean"])))
    if args.verbose:
        print()
        for row in sorted(report["leaks"], key=lambda r: -r["similarity"]):
            print("  %s  ~  %s   %s" % (row["id"], row["twin"], row["why"]))
    return 0


def cmd_usable(args: argparse.Namespace) -> int:
    """Which held-out cases can still measure a model, and which cannot.

    Run before a comparison, never after: a case dropped once a score is visible
    is a case dropped for its score.
    """
    from . import refusals
    engine = args.engine or eng.engine_path("lypning-l") or eng.engine_path("lypning")
    if not engine:
        print("no lypning binary: run `lypning build --rust`, or pass --engine",
              file=sys.stderr)
        return 1
    cases = list(read_jsonl(DATA / ("holdout.jsonl" if not args.train else "train.jsonl")))
    r = refusals.usable_cases(cases, engine)
    print("%d cases   %d usable   %d degenerate   %d unsatisfiable"
          % (r["n"], len(r["usable"]), len(r["degenerate"]), len(r["unsatisfiable"])))
    if r["degenerate"]:
        print()
        print("DEGENERATE — the engine now runs the program the case asks the model to")
        print("rewrite, so returning the input unchanged passes:")
        for cid in r["degenerate"]:
            print("  %s" % cid)
    if r["unsatisfiable"]:
        print()
        print("UNSATISFIABLE — the case's own negative does not reproduce the expected")
        print("output, so nothing passes it: not the model, not a human, not CPython:")
        for row in r["unsatisfiable"]:
            print("  %s  %s" % (row["id"], row["verdict"]))
            print("      %s" % row["detail"])
    print()
    print("%d of %d cases can measure a model." % (len(r["usable"]), r["n"]))
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
    if args.held_out:
        print(refusals.HELD_OUT_BANNER)
        print("")
    print(refusals.report(refusals.census(cases, engine),
                          limit=args.limit, show_details=args.details,
                          held_out=bool(args.held_out)))
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


def cmd_training_prepare(args) -> int:
    from .curriculum import starter_cases
    from .training import prepare, TrainingError
    from .jsonio import read_jsonl
    try:
        cases = starter_cases() if args.starter else read_jsonl(args.cases)
        bundle = prepare(cases, args.engine, args.output, seed=args.seed,
                         timeout_s=args.timeout, memory_mb=args.memory_mb)
    except (TrainingError, OSError, subprocess.SubprocessError) as exc:
        print("training preparation blocked: %s" % exc, file=sys.stderr)
        return 1
    counts = {s: sum(c["split"] == s for c in bundle["cases"]) for s in ("train", "dev", "test")}
    print(json.dumps({"digest": bundle["digest"], "cases": counts,
                      "families": len({c["family"] for c in bundle["cases"]}),
                      "reference_scores": bundle["reference_scores"]}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nt", description="Nemotron LoRA pipeline: corpus and eval")
    sub = p.add_subparsers(dest="cmd", required=True)

    tp = sub.add_parser("training-prepare", help="verify a NEW multi-input lypning-l experiment")
    source = tp.add_mutually_exclusive_group(required=True)
    source.add_argument("--starter", action="store_true", help="authored smoke tasks, not a benchmark")
    source.add_argument("--cases", type=Path, help="independently authored multi-input JSONL")
    tp.add_argument("--engine", type=Path, required=True, help="explicit compiled lypning-l binary")
    tp.add_argument("--output", type=Path, required=True, help="new directory; never overwrite")
    tp.add_argument("--seed", type=int, default=1111)
    tp.add_argument("--timeout", type=float, default=5.0)
    tp.add_argument("--memory-mb", type=int, default=1024,
                    help="child address-space cap; 0 explicitly disables it (macOS smoke only)")
    tp.set_defaults(fn=cmd_training_prepare)

    h = sub.add_parser("harvest", help="step 1: build the corpus from failing cases")
    h.add_argument("--source", action="append",
                   help="adapter spec, repeatable; default %s. "
                        "lypning[:cache=PATH] (refusals from `nt classify`, the "
                        "source of most of this corpus) | study | "
                        "evalfail:attempts=PATH | jsonl:path=PATH[,map=dst->src]"
                        % " ".join(DEFAULT_SOURCES))
    h.add_argument("--allow-holdout-loss", action="store_true",
                   help="write a corpus that drops frozen held-out cases. Voids "
                        "every baseline graded on them; you will need to "
                        "re-freeze and re-establish one")
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
    sp.add_argument("--ceiling-keep", type=int, default=sample_mod.CEILING_KEEP,
                    help="max targets kept per CEILING case; the counterweight is "
                         "anchored by how many such cases there are, not by how "
                         "many near-identical targets each one yields")
    sp.add_argument("--engine", help="the binary the pool is measured against")
    sp.add_argument("--name", default="v1")
    sp.add_argument("--limit", type=int, default=0,
                    help="sample only the first N cases of the pool; --limit 1 --k 1 "
                         "--name smoke is one row for the price of one row")
    sp.add_argument("--dry-run", action="store_true",
                    help="print the pool, the draw count and the ceiling cost, send nothing")
    sp.add_argument("--resume", action="store_true",
                    help="continue an interrupted run: draws already recorded for a "
                         "(case, draw) pair are not redrawn")
    sp.add_argument("--concurrency", type=int, default=16)
    sp.add_argument("--temperature", type=float, default=1.0)
    sp.add_argument("--top-p", type=float, default=0.95)
    sp.add_argument("--max-tokens", type=int, default=2048)
    # THE DEFAULT IS NO THINKING, because the arm this SFT set is built to beat
    # was drawn with `enable_thinking: false` and rejection sampling is on-policy
    # or it is nothing. Until 2026-09-13 this flag defaulted the other way, which
    # is the opposite of `sample.sample_targets`'s own default and cost a
    # projected $25.05 against a $6 budget before the first minute was out.
    # `--thinking` is still reachable, deliberately, for an arm drawn that way.
    sp.add_argument("--no-thinking", dest="no_thinking", action="store_true",
                    default=True)
    sp.add_argument("--thinking", dest="no_thinking", action="store_false",
                    help="draw with enable_thinking=true; only for an arm that "
                         "was itself sampled that way")
    sp.add_argument("--hinted", action="store_true",
                    help="context distillation: draw WITH the engine's own refusal "
                         "line and a matching COOKBOOK recipe in the user turn, and "
                         "train on the bare prompt. The run of record sampled and "
                         "trained on a prompt that never mentioned the subset, and "
                         "its legality delta was a null; see pipeline/hints.py")
    sp.add_argument("--price-hour", type=float, default=0.0)
    sp.add_argument("--max-spend", type=float, default=0.0)
    sp.add_argument("--base-url"); sp.add_argument("--model")
    sp.set_defaults(fn=cmd_sample)

    gr = sub.add_parser("grade", help="grade completions generated elsewhere (no GPU needed)")
    gr.add_argument("--require-fingerprint", default="",
                    help="refuse unless this box's engine fingerprint is exactly this. "
                         "Pin every arm of one comparison to one engine BEFORE grading: "
                         "a legality number is defined relative to what the engine accepts")
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
    cp.add_argument("--no-rule", action="store_true",
                    help="skip the pre-registered denominators (they need the engine)")
    cp.add_argument("--ids", action="store_true", help="list the cases that moved")
    cp.add_argument("--anyway", action="store_true",
                    help="print the arithmetic for two runs that measured different "
                         "things; it is then not a delta attributable to the model")
    cp.set_defaults(fn=cmd_compare)

    lg = sub.add_parser("legality", help="subset-legality rate between two runs (no GPU, no spend)")
    lg.add_argument("before"); lg.add_argument("after", nargs="?")
    lg.add_argument("--pass-at-k", action="store_true",
                    help="reachability instead of a delta: per case, did ANY draw land "
                         "in the subset, and did any land there correct. Takes one or "
                         "two populations — a run id, an SFT draw pool under data/sft, "
                         "or a path to a JSONL of draws")
    lg.add_argument("--floor", type=float, default=0.60,
                    help="reachability floor for --pass-at-k; below it a reward-based "
                         "stage has nothing to reinforce and the exit code says so")
    lg.add_argument("--engine", help="binary to judge legality with (default: lypning-l)")
    lg.add_argument("--limit", type=int, default=12, help="refusal-kind rows to print")
    lg.add_argument("--mde", type=float, default=0.0,
                    help="pre-registered minimum detectable effect, as a fraction "
                         "(0.03 = the CI lower bound must exceed +3pp)")
    lg.add_argument("--gate-a", type=float, default=0.02,
                    help="correctness may not fall more than this (default 2pp)")
    lg.add_argument("--gate-b", type=float, default=0.80,
                    help="supported-import retention floor, as a ratio of the base arm")
    lg.add_argument("--gate-c", type=float, default=0.20,
                    help="mean completion tokens may not grow more than this")
    lg.add_argument("--no-context", action="store_true",
                    help="run each program without its case's argv/stdin/files; this "
                         "UNDERSTATES refusals and disables gate B")
    lg.add_argument("--jobs", type=int, default=8,
                    help="parallel sandbox runs (the work is subprocess-bound)")
    lg.add_argument("--cache", help="directory of per-run replay verdicts. Reused only "
                                    "when the engine fingerprint still matches, so the "
                                    "aggregation can be re-cut without re-running 2,368 "
                                    "programs")
    lg.add_argument("--json", help="write the full comparison here")
    lg.set_defaults(fn=cmd_legality)

    sl = sub.add_parser("slices", help="pass rate per stratum for a run")
    sl.add_argument("run_id"); sl.add_argument("--fine", action="store_true",
                                               help="every refusal kind, not just the group")
    sl.set_defaults(fn=cmd_slices)

    pw = sub.add_parser("power", help="what effect size this held-out set could detect")
    pw.add_argument("run_id")
    pw.add_argument("--trials", type=int, default=200,
                    help="simulated runs per lift (default 200)")
    pw.set_defaults(fn=cmd_power)

    lk = sub.add_parser("leaks", help="train cases that are the same question as a held-out one")
    lk.add_argument("--ceiling", type=float, default=splitmod.SIMILARITY_CEILING)
    lk.add_argument("--sft", default="", help="an sft.jsonl (or its directory): run every "
                                              "target against the held-out cases themselves")
    lk.add_argument("--verbose", action="store_true")
    lk.set_defaults(fn=cmd_leaks)

    us = sub.add_parser("usable", help="which held-out cases can still measure a model")
    us.add_argument("--engine")
    us.add_argument("--train", action="store_true", help="the train split instead")
    us.set_defaults(fn=cmd_usable)

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
