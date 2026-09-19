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
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import bank_native as bank_native_mod
from . import engines as eng
from . import headroom as headroom_mod
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
    missing = stats.sampling_missing(samp)
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


def cmd_eval2_select(args: argparse.Namespace) -> int:
    """Select reverse-prompting candidates for eval-2 from data/classified.jsonl."""
    from . import eval2_select as e2s
    from .jsonio import write_jsonl
    source = Path(args.classified) if args.classified else DATA / "classified.jsonl"
    if not source.exists():
        print("no classified corpus: run `nt classify` first", file=sys.stderr)
        return 1
    sightings = Path(args.sightings) if args.sightings else ROOT.parent / "tests" / "corpus" / "sightings"
    result = e2s.select(read_jsonl(source), limit=args.limit, seed=args.seed,
                        sightings_dir=sightings, timeout_s=args.timeout, jobs=args.jobs)
    write_jsonl(args.output, result["candidates"])
    print(e2s.render(result))
    print("  -> %s" % args.output)
    return 0 if result["selected"] else 1


def cmd_eval2_legacy(args: argparse.Namespace) -> int:
    """Project a schema-3 eval-2 bank onto records `nt harvest --source jsonl:path=`
    accepts: one exact-stdout test per case, category unobserved, the bank's
    identity in the tags. The harvest's gates still run on what comes out."""
    from . import eval2_legacy
    from .jsonio import write_jsonl
    bank = Path(args.bank)
    if not bank.is_file():
        print("not a file: %s" % bank, file=sys.stderr)
        return 2
    result = eval2_legacy.project(read_jsonl(bank))
    write_jsonl(args.output, result["records"])
    print("eval2-legacy  projected %d of %d cases   skipped %d"
          % (len(result["records"]), result["cases"], len(result["skipped"])))
    for row in result["skipped"]:
        print("  skipped %s: %s" % (row["case_id"] or ("#%d" % row["index"]), row["reason"]))
    print("  -> %s" % args.output)
    print("  next: NTX_ROOT=<tree> nt harvest --source jsonl:path=%s && nt split --fraction 1.0"
          % args.output)
    return 0 if result["records"] else 1


def cmd_eval2_rows(args: argparse.Namespace) -> int:
    """A legacy run as `training_metrics` rows: correct off the run, native off a
    replay through the pinned engine, family and population off the case tags."""
    from . import eval2_rows, legality
    from .jsonio import write_jsonl
    from .training_metrics import summarize
    from .training_types import TrainingError
    engine = args.engine or eng.engine_path("lypning-l") or eng.engine_path("lypning")
    if not engine:
        print("no lypning binary on this machine: run `lypning build --rust`, "
              "or pass --engine", file=sys.stderr)
        return 1
    # `native` is read off this replay, and `status` is read off `native`, so the
    # binary here IS the population these rows define. An `--engine` that is not a
    # file is a truthy string that grades every program ERROR, which writes rows
    # whose every draw is non-native — a population, at exit 0, from a replay that
    # ran nothing. Same usage error, same exit 2, as `levers`.
    if not Path(engine).is_file():
        print("not a file: %s" % engine, file=sys.stderr)
        return 2
    path = RUNS / args.run_id / "attempts.jsonl"
    if not path.exists():
        print("no such run: %s" % args.run_id, file=sys.stderr)
        return 1
    attempts = list(read_jsonl(path))
    meta = _run_meta(args.run_id)
    seed = (meta.get("sampling") or {}).get("seed")
    cases, tests = _case_context(False)
    cache = (Path(args.cache) / ("%s.replay.json" % args.run_id)) if args.cache else None
    census = legality.replay(attempts, engine, tests=tests, workers=args.jobs, cache=cache)
    result = eval2_rows.rows(attempts, census["rows"], cases, seed=seed)
    # `is_file` above rejects a path; it cannot reject a regular file that will
    # not execute — no +x bit, wrong architecture, a text placeholder. Those
    # grade every program ERROR, which writes `native` False on every row and
    # every correct draw as `correct-fallback`: the same population from a replay
    # that ran nothing, one step further out. A hand-transferred binary losing
    # its execute bit is the ordinary way to arrive here. `levers` refuses an
    # ungraded replay; so does this, before any rows are written.
    errors = census["tally"].get("ERROR") or 0
    if errors:
        print("eval2-rows: %d of %d replayed program(s) did not grade (ERROR) through "
              "%s — an ungraded replay writes every draw non-native, which is a "
              "population, not a measurement."
              % (errors, result["replayed"], engine), file=sys.stderr)
        return 1
    write_jsonl(args.output, result["rows"])
    # The bytes that graded these rows, not the installed chain. `identity()`
    # fingerprints whatever `lypning-l`/`lypning` this host has, which for an
    # explicit historical `--engine` names binaries the replay never touched —
    # and this line is the only provenance the rows file carries, since
    # `eval2_rows.row_for` records a verdict and no identity.
    replay_identity = eng.binary_identity(engine)
    print("eval2-rows  %s   %d rows, %d replayed   @ engine sha256 %s   %s"
          % (args.run_id, len(result["rows"]), result["replayed"],
             replay_identity["sha256"] or "unreadable", replay_identity["version"]))
    if result.get("superseded"):
        print("  %d superseded attempt(s) folded: a resumed run redrew its harness errors"
              % result["superseded"])
    if result["unknown_cases"]:
        print("  %d attempts name cases this tree does not know: %s"
              % (len(result["unknown_cases"]), ", ".join(result["unknown_cases"][:5])),
              file=sys.stderr)
    if census["tally"].get("MISMATCH"):
        print("  MISMATCH %d — invariant 1: always a bug, never the model's."
              % census["tally"]["MISMATCH"], file=sys.stderr)
    print("  -> %s" % args.output)
    try:
        s = summarize(result["rows"]) if result["rows"] else None
    except TrainingError as exc:
        print("  rows written but not summarised: %s" % exc, file=sys.stderr)
        return 1
    if s is None:
        print("  no rows", file=sys.stderr)
        return 1
    print("  correct %s   correct-native %s   over %d cases, %d draws, %d families "
          "(family macro)" % (_pct(s["correct"]), _pct(s["correct_native"]),
                              s["cases"], s["draws"], s["families"]))
    for pop, ps in sorted(s["by_population"].items()):
        print("    %-18s correct %s   native %s   (%d cases)"
              % (pop, _pct(ps["correct"]), _pct(ps["correct_native"]), ps["cases"]))
    return 1 if census["tally"].get("MISMATCH") else 0


def cmd_eval2_bank(args: argparse.Namespace) -> int:
    """Assemble a schema-3 eval-2 bank from authored proposals, verified by execution.

    Exit 0 even when proposals were dropped (the drops are the report), 1 when
    nothing was admitted, 2 when the output directory already exists.
    """
    from . import eval2_bank
    from .training_types import TrainingError
    engine = args.engine or eng.engine_path("lypning-l") or os.environ.get("LYPNING_L_BIN")
    if not engine or not Path(engine).exists():
        print("no lypning-l on this machine: run `lypning build --rust`, set LYPNING_L_BIN, "
              "or pass --engine", file=sys.stderr)
        return 1
    if Path(args.output).exists():
        print("refusing to overwrite %s: a changed bank is a new directory" % args.output,
              file=sys.stderr)
        return 2
    try:
        inputs = eval2_bank.load_inputs(args.proposals, args.candidates, args.evidence or [])
        result = eval2_bank.build(inputs["batches"], inputs["candidates"], inputs["evidence"],
                                  engine, seed=args.seed, keep_disagreements=args.keep_disagreements,
                                  timeout_s=args.timeout)
        paths = eval2_bank.write_outputs(result, args.output)
    except (TrainingError, ValueError, OSError) as exc:
        print("eval2-bank: %s" % exc, file=sys.stderr)
        return 1
    print(eval2_bank.render(result["report"]))
    print("  -> %s" % paths["bank"])
    return 0 if result["cases"] else 1


def cmd_synth_generate(args: argparse.Namespace) -> int:
    """Ask the model for tasks and k programs each; execute nothing. Holds the key.

    Exit 2 when CEREBRAS_API_KEY is not in this process, 0 otherwise — a run that
    hit its bound is a normal outcome, and the next run resumes past what this
    one wrote.
    """
    import random
    from . import synth_generate as sg
    from .jsonio import iter_jsonl
    backend = sg.backend_from_env(timeout_s=args.timeout)
    if backend is None:
        print("CEREBRAS_API_KEY is not set; this runs on the trusted controller only",
              file=sys.stderr)
        return 2
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Resumable: what this file already holds, plus what the bank already has.
    seen = sg.seen_tasks(iter_jsonl(output))
    here = len(seen)
    for path in args.exclude_tasks or []:
        if not Path(path).is_file():
            print("not a file: %s" % path, file=sys.stderr)
            return 2
        seen |= sg.seen_tasks(iter_jsonl(path))
    print("resuming with %d task(s) in this file, %d already banked elsewhere"
          % (here, len(seen) - here))
    budget = sg.Budget(max_calls=args.max_calls, max_output_tokens=args.max_output_tokens,
                       max_seconds=args.max_minutes * 60.0)
    with output.open("a", encoding="utf-8") as sink:
        def write(row):
            sink.write(json.dumps(row, sort_keys=True) + "\n")
            sink.flush()
        summary = sg.generate(backend, write, budget=budget, seen=seen, samples=args.samples,
                              tasks_per_call=args.tasks_per_call,
                              rewrite_fraction=args.rewrite_fraction,
                              rng=random.Random(args.seed))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def cmd_synth_adapt(args: argparse.Namespace) -> int:
    """Execute every candidate, apply the oracle, route by what the engine does,
    repair the queue, and write schema-3 cases. Holds no provider key.

    Exit 2 for a usage error (the engine is not a file, the output exists), 1
    when nothing was admitted — a batch of nothing is a failed read, not a clean
    one — and 0 otherwise. Witnesses do not change the exit code: they are
    written and printed, and the cases beside them are still cases.
    """
    from . import synth
    from .training_types import VerificationBlocked
    if not Path(args.engine).is_file():
        print("not a file: %s" % args.engine, file=sys.stderr)
        return 2
    if not Path(args.candidates).is_file():
        print("not a file: %s" % args.candidates, file=sys.stderr)
        return 2
    if Path(args.output).exists():
        print("refusing to overwrite %s: a re-run is a new directory" % args.output,
              file=sys.stderr)
        return 2
    if args.offset < 0 or args.limit < 0:
        print("--offset and --limit are counts, not negatives", file=sys.stderr)
        return 2
    candidates = read_jsonl(args.candidates)
    total = len(candidates)
    candidates = candidates[args.offset:]
    if args.limit:
        candidates = candidates[:args.limit]
    if not candidates:
        print("no candidates in %s — nothing was triaged" % args.candidates, file=sys.stderr)
        return 1
    runner = synth.Runner(args.engine, timeout_s=args.timeout, mem_mb=args.memory_mb,
                          seed=args.seed)
    try:
        result = synth.run(candidates, runner, agree=args.agree, batch=args.batch)
    except VerificationBlocked as exc:
        # Ours: a sandbox that could not start, an engine that could not be
        # spawned. Nothing is written, because a partial batch that looks whole
        # is worse than none.
        print("synth-adapt blocked: %s" % exc, file=sys.stderr)
        return 1
    synth.write_outputs(result, args.output)
    print(synth.render(result["report"]))
    print("  slice [%d:%d] of %d candidate(s) in %s"
          % (args.offset, args.offset + len(candidates), total, args.candidates))
    print("  -> %s" % args.output)
    for row in result["witnesses"][:5]:
        print("  witness: %s" % row["why"], file=sys.stderr)
    return 0 if result["cases"] else 1


def _run_meta(run_id: str) -> Dict[str, Any]:
    p = RUNS / run_id / "meta.json"
    if not p.exists():
        return {}
    try:
        return read_json(p)
    except (ValueError, OSError):
        return {}


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
    if getattr(args, "top_k", None) is not None:
        inner += ["--top-k", str(args.top_k)]
    if args.label:
        inner += ["--label", args.label]
    if getattr(args, "system_file", None):
        # Absolute, because the inner command starts with `cd ROOT`.
        inner += ["--system-file", str(Path(args.system_file).resolve())]
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


def _system_file(path: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``(text, sha256 of the file's bytes)`` for `--system-file`, or two Nones.

    The stage 0b switch (`LADDER.md` 0b). The text goes to the model as a
    paragraph after `evaluate.SYSTEM_PROMPT` and into `prompt_sha`; the file's
    own sha256 is recorded beside it in meta.json so the arm names the exact
    bytes it was drawn with. An empty file is the bare prompt.
    """
    if not path:
        return None, None
    raw = Path(path).read_bytes()
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


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
    try:
        system_extra, system_file_sha256 = _system_file(getattr(args, "system_file", None))
    except (OSError, UnicodeDecodeError) as exc:
        print("cannot read --system-file: %s" % exc, file=sys.stderr)
        return 1
    ev = Evaluation(
        backend, cases, RUNS / run_id,
        samples=args.samples, temperature=args.temperature, top_p=args.top_p,
        max_tokens=args.max_tokens, enable_thinking=not args.no_thinking,
        top_k=getattr(args, "top_k", None),
        concurrency=args.concurrency, max_spend=args.max_spend,
        price_hour=args.price_hour,
        label=args.label or ("stock baseline" if args.baseline else ""),
        system_extra=system_extra, system_file_sha256=system_file_sha256,
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
    missing = stats.sampling_missing(samp)
    if missing:
        return None, "the declared sampling is missing %s" % ", ".join(missing)
    return dict(stats.sampling_block(samp), declared_by=source), ""


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
    evaluate = __import__("pipeline.evaluate", fromlist=["x"])
    local_sha = evaluate.prompt_signature()
    stated_sha = evaluate.canonical_prompt_sha(header.get("prompt_sha"))
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


def _csv_ints(text: str) -> List[int]:
    try:
        out = [int(x) for x in text.split(",") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("expected comma-separated integers, got %r" % text)
    if not out or any(n < 1 for n in out):
        raise argparse.ArgumentTypeError("expected positive integers, got %r" % text)
    return out


def _csv_floats(text: str) -> List[float]:
    try:
        return [float(x) for x in text.split(",") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("expected comma-separated numbers, got %r" % text)


def _eval2_rows_source(spec: str) -> Optional[Path]:
    """A `training_metrics` rows JSONL path, or a legacy run id whose
    `eval2_rows.jsonl` (`nt eval2-rows RUN --output runs/RUN/eval2_rows.jsonl`)
    sits in its run directory."""
    p = Path(spec)
    if p.is_file():
        return p
    q = RUNS / spec / "eval2_rows.jsonl"
    return q if q.is_file() else None


def _power_eval2(args: argparse.Namespace) -> int:
    """`EVAL2.md` §7: the power of the family-cluster rule against bank size,
    from a pilot's rows. Run before the bank is drawn; the pilot is spent."""
    if not args.rows:
        print("power --eval2 needs --rows <training_metrics rows JSONL, or a run id "
              "with runs/<id>/eval2_rows.jsonl>", file=sys.stderr)
        return 2
    src = _eval2_rows_source(args.rows)
    if src is None and (os.sep in args.rows or args.rows.endswith(".jsonl")):
        # A path the caller typed is a usage error, and rebuilding it is not the
        # remedy: `eval2-rows` re-derives `native` — hence `status`, hence the
        # population — from whichever engine it is given, so a rebuilt file is a
        # new population at a new identity. Only the run-id form gets the hint.
        print("not a file: %s" % args.rows, file=sys.stderr)
        return 2
    if src is None:
        print("no rows file at %s and no %s: write them with `nt eval2-rows %s "
              "--output runs/%s/eval2_rows.jsonl`"
              % (args.rows, RUNS / args.rows / "eval2_rows.jsonl", args.rows, args.rows),
              file=sys.stderr)
        return 1
    pilot = stats.pilot_from_rows(list(read_jsonl(src)))
    if not pilot:
        print("no rows in %s" % src, file=sys.stderr)
        return 1
    k = args.draws or max(len(e["scores"]) for e in pilot.values())
    curve = stats.power_curve_clustered(
        pilot, k, mde=args.mde, sizes=args.sizes, deltas=args.deltas,
        trials=args.trials, resamples=args.resamples)
    p = curve["pilot"]
    print("eval-2 power: family-cluster paired bootstrap, from %s" % src)
    print("  pilot %d cases in %d families (%d independent clusters), k=%d"
          % (p["cases"], p["families"], p["clusters"], curve["k"]))
    print("  base correct-and-native, macro over families: %s" % _pct(p["base_point"]))
    print("  rule: 95%% lower bound > %+.0fpp   %d banks per cell, %d resamples each"
          % (100 * curve["mde"], curve["trials"], curve["resamples"]))
    print()
    # Every cell carries the lift it REALISED beside its power, because the lift
    # it was ASKED for is not what it tested: the cap at 1 takes back whatever a
    # case has no room for. A table keyed on the nominal column alone reads as
    # blind to effects the rule detects (`EVAL2.md` section 7, ASSESSMENT.md
    # section 3.4). Realised here is the macro over families -- the units of the
    # section 4 rule, and so the units power is a property of.
    print("  each cell: realised macro lift (the rule's units), then power")
    print("  %-12s %6s   %s" % ("shape", "asked",
                                " ".join("%13s" % ("N=%d" % n) for n in curve["sizes"])))
    for shape in curve["shapes"]:
        for delta in curve["deltas"]:
            cells = [r for r in curve["rows"] if r["shape"] == shape and r["delta"] == delta]
            line = "  %-12s %+5.0fpp  %s" % (
                shape, 100 * delta,
                " ".join("%+6.1fpp%4.0f%%" % (100 * r["mean_macro_effect"], 100 * r["power"])
                         for r in cells))
            print(line + ("   <- false-positive rate" if delta == 0 else ""))
    print()
    # Two different things make a cell's realised macro miss its label, and only
    # one of them is the cap. `capped` is a fact about PER-CASE rates, so it is
    # reported in per-case units and ranked in them; it cannot certify or condemn
    # a macro label, which the family weighting moves on its own and in either
    # direction (a capped cell can still over-realise its label in the macro).
    # Hence: the shortfall the cap caused, named as the cap's; the macro read off
    # the table above for every row regardless.
    graded = [r for r in curve["rows"] if r["delta"] > 0]
    clipped = [r for r in graded if r["capped"]]
    if clipped:
        worst = min(clipped, key=lambda r: r["mean_effect"] - r["delta"])
        print("  the cap at 1 bit in %d of %d cells: those rows asked for more lift than"
              % (len(clipped), len(graded)))
        print("  the pilot's rates had room to take, so `asked` overstates what they")
        print("  tested. Worst per case: %s %+.0fpp asked at N=%d took %+.1fpp per case"
              % (worst["shape"], 100 * worst["delta"], worst["N"], 100 * worst["mean_effect"]))
        print("  (macro %+.1fpp). Read every row by its realised column above, capped or"
              % (100 * worst["mean_macro_effect"],))
        print("  not: the macro can miss `asked` in either direction without the cap.")
        print()
    print("  smallest N at 80% power, by the lift asked for (realised macro beside it,")
    print("  at that N -- they differ wherever the cap bit):")
    for shape in curve["shapes"]:
        for delta in curve["deltas"]:
            if delta <= curve["mde"]:
                continue
            n = curve["smallest_n"][shape][delta]
            hit = [r for r in curve["rows"]
                   if r["shape"] == shape and r["delta"] == delta and r["N"] == n]
            got = ("  (realised %+.1fpp)" % (100 * hit[0]["mean_macro_effect"])) if hit else ""
            print("    %-12s %+4.0fpp -> %s%s" % (shape, 100 * delta,
                                                  ("N=%d" % n) if n else "none on this grid", got))
    print()
    print("  uniform lifts every case by the effect; concentrated lifts the %.0f%% of"
          % (100 * curve["fraction"]))
    print("  cases with the lowest base rate to one target rate aimed at the same mean.")
    print("  Aimed at, not equal to: on a bank with no room the two shapes realise")
    print("  different effect SIZES at one `asked` value, so compare them by what they")
    print("  realised. And one realised lift is still not one power -- how the lift is")
    print("  spread between families moves it too, which is what the two shapes are for.")
    print("  An effect equal to the bar itself clears it in under half the banks however")
    print("  large N: size the bank at the effect expected, not the bar. The bank is the")
    print("  smallest N at which both shapes reach 80% power, or 300, whichever is larger")
    print("  (EVAL2.md section 7).")
    return 0


def cmd_power(args: argparse.Namespace) -> int:
    """What this held-out set can see, before anything is spent on making it move.

    A rule that cannot detect the effect being paid for reports "no win"
    whatever happens, and the money goes either way. Run this BEFORE the run:
    a rule chosen once the outcome is visible is not a rule.
    """
    if args.eval2:
        return _power_eval2(args)
    if not args.run_id:
        print("power needs a run id, or --eval2 --rows", file=sys.stderr)
        return 2
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
        sft_path = (Path(args.sft) if Path(args.sft).suffix == ".jsonl"
                    else Path(args.sft) / "sft.jsonl")
        # `read_jsonl` answers `[]` for a path that is not there, so a mistyped
        # `--sft`, a directory holding no `sft.jsonl` and an empty file all used
        # to reach "no training target passes a held-out case" and exit 0. This
        # is the one finding here that must stop a training run, so its all-clear
        # has to mean a comparison happened: a path that is not a file is the
        # caller's error, 2, and probing nothing is this command failing, 1.
        if not sft_path.is_file():
            print("not a file: %s" % sft_path, file=sys.stderr)
            return 2
        rows = list(read_jsonl(sft_path))
        r = splitmod.sft_solves_holdout(rows, [c for c in cases if c["id"] in held])
        if not r["programs_probed"] or not r["inputs_probed"]:
            print("leaks: %d SFT row(s) in %s, %d carrying a program, %d distinct "
                  "input(s) probed against %d held-out case(s) — nothing was run, so "
                  "nothing is clean."
                  % (r["rows"], sft_path, r["programs_probed"], r["inputs_probed"],
                     r["n_holdout"]), file=sys.stderr)
            return 1
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


def cmd_eval2_leaks(args: argparse.Namespace) -> int:
    """Training cases that are an eval-2 case in disguise, bank against bank.

    Exit 1 on any pair unless ``--allow``: a training bank that overlaps the
    eval-2 bank voids the number eval-2 exists to produce. Neither bank is
    written.
    """
    from . import eval2_leaks
    eval_path, train_path = Path(args.eval2), Path(args.train)
    for path in (eval_path, train_path):
        if not path.is_file():
            print("not a file: %s" % path, file=sys.stderr)
            return 2
    report = eval2_leaks.bank_leaks(read_jsonl(eval_path), read_jsonl(train_path),
                                    ceiling=args.ceiling,
                                    min_stdout_chars=args.min_stdout)
    # `is_file` above covers absence, not emptiness, and an all-filtered bank
    # reads the same. "0 pairs; 0 of 0 eval-2 cases leak" is a clean bill over a
    # comparison that did not happen, in the gate that stands between a training
    # bank and the number eval-2 exists to produce.
    if not report["n_eval2"] or not report["n_train"]:
        print("eval2-leaks: %d eval-2 and %d training case(s) — nothing was compared"
              % (report["n_eval2"], report["n_train"]), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(eval2_leaks.render(report))
    if report["pairs"] and not args.allow:
        return 1
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


def cmd_levers(args: argparse.Namespace) -> int:
    """Which lever can remove each refusal: the engine, the model, or neither.

    `nt refusals` ranks what the engine refuses; this asks the next question —
    of those refusals, which could be REMOVED, and by which lever. The model
    lever is a subset of the engine one, so this table is what divides a budget
    between them, and rung S0b of `STATUS.md` §10 is this same table run over
    the eval-2 correct-but-fallback draws instead of the local capture.

    The two failure exits split on whose mistake it was (root `CLAUDE.md`
    invariant 8): a path typed on the command line that is not a file is a usage
    error, 2, because argv is wrong and nothing was attempted; having read or
    graded nothing is this command failing, 1, because argv was right and the
    evidence was not there. `probe-vector` chose the same 1 for a
    present-but-empty input, so the two commands answer alike.
    """
    from . import levers

    today = time.strftime("%Y-%m-%d")
    notes: List[str] = []
    if (args.population_rows or args.require_engine_sha256 or args.expect_draws is not None) \
            and not args.run:
        print("levers: --population-rows, --require-engine-sha256 and --expect-draws "
              "apply only with --run", file=sys.stderr)
        return 2
    if args.expect_draws is not None and args.expect_draws <= 0:
        print("levers: --expect-draws must be positive", file=sys.stderr)
        return 2
    if args.run:
        # Rung S0b. Neither artifact carries the whole answer: the draw rows say
        # which draws are correct-but-fallback and how they cluster, the replay
        # says which refusal. `eval2_rows` already joins them to decide `native`;
        # this joins them to ask WHICH refusal, on the same key.
        from . import eval2_rows, legality
        engine = args.engine or eng.engine_path("lypning-l") or eng.engine_path("lypning")
        if not engine:
            print("no lypning binary on this machine: run `lypning build --rust`, "
                  "or pass --engine", file=sys.stderr)
            return 1
        # An `--engine` that is not a file passes `if not engine` — it is a
        # truthy string. Every program then grades ERROR, no draw carries a
        # refusal, and the vector prints empty at exit 0 while the considered
        # population silently grows, because `native` is re-derived from this
        # binary. A missing engine is a usage error, not a measurement.
        if not Path(engine).is_file():
            print("not a file: %s" % engine, file=sys.stderr)
            return 2
        replay_identity = eng.binary_identity(engine)
        if args.require_engine_sha256:
            if not re.fullmatch(r"[0-9a-f]{64}", args.require_engine_sha256):
                print("levers: --require-engine-sha256 must be 64 lowercase hex characters",
                      file=sys.stderr)
                return 2
            if replay_identity["sha256"] != args.require_engine_sha256:
                print("levers: replay engine sha256 %s, required %s"
                      % (replay_identity["sha256"] or "unreadable",
                         args.require_engine_sha256), file=sys.stderr)
                return 1
        attempts_path = RUNS / args.run / "attempts.jsonl"
        if not attempts_path.exists():
            print("no such run: %s" % args.run, file=sys.stderr)
            return 1
        attempts = list(read_jsonl(attempts_path))
        cases, tests = _case_context(False)
        if args.population_rows:
            population_path = Path(args.population_rows)
            if not population_path.is_file():
                print("not a file: %s" % population_path, file=sys.stderr)
                return 2
            drawn_rows = list(read_jsonl(population_path))
            if not drawn_rows:
                print("levers: no population rows in %s — nothing to join"
                      % population_path, file=sys.stderr)
                return 1
            # Replay only the frozen population.  A resumed legacy run can
            # carry several attempts for one draw; use the same winner rule as
            # eval2-rows before selecting by (corpus_id, draw).
            wanted = {(row.get("corpus_id") or row.get("case_id"), row.get("draw"))
                      for row in drawn_rows
                      if args.status is None or row.get("status") == args.status}
            attempts = [a for a in eval2_rows.one_attempt_per_draw(attempts)
                        if (a.get("case_id"), a.get("sample")) in wanted]
            source = "%s joined to %s" % (population_path, attempts_path)
        else:
            meta = _run_meta(args.run)
            seed = (meta.get("sampling") or {}).get("seed")
            drawn_rows = None
            source = "runs/%s" % args.run
        cache = (Path(args.cache) / ("%s.replay.json" % args.run)) if args.cache else None
        census = legality.replay(attempts, engine, tests=tests, workers=args.jobs,
                                 cache=cache)
        if drawn_rows is None:
            drawn_rows = eval2_rows.rows(attempts, census["rows"], cases, seed=seed)["rows"]
        programs = dict(((a.get("case_id"), int(a.get("sample") or 0)), a.get("program") or "")
                        for a in attempts)
        joined = levers.draw_refusals(drawn_rows, census["rows"],
                                      status=args.status, programs=programs)
        if args.expect_draws is not None and joined["considered"] != args.expect_draws:
            print("levers: %d draw(s) match status %s; expected exactly %d"
                  % (joined["considered"], args.status or "(any)", args.expect_draws),
                  file=sys.stderr)
            return 1
        result = levers.table(joined["records"], source=source,
                              unit="draw", independence="family",
                              loaded=len(drawn_rows))
        notes.append("@ replay engine sha256 %s   %s   oracle Python %s"
                     % (replay_identity["sha256"], replay_identity["version"],
                        replay_identity["oracle_python"]))
        notes.append("%d draw(s) match status %s; %d carried a refusal"
                     % (joined["considered"], args.status or "(any)",
                        len(joined["records"])))
        # Never a zero: a fallback draw with no refusal on record is a hole in
        # the evidence, and a hole is not a family with no mass.
        if joined["unmatched"] or joined["without_refusal"]:
            notes.append("UNRESOLVED: %d draw(s) have no replay row, %d have a replay "
                         "row carrying no refusal. Neither is counted anywhere above."
                         % (joined["unmatched"], joined["without_refusal"]))
        mismatches = census["tally"].get("MISMATCH") or 0
        if mismatches:
            print("  MISMATCH %d — invariant 1: always a bug, never the model's."
                  % mismatches, file=sys.stderr)
        # A program the replay could not run carries no refusal, so it leaves
        # the vector silently. Say so where MISMATCH is said: an ungraded
        # program is a hole in the evidence, not a family with no mass.
        errors = census["tally"].get("ERROR") or 0
        if errors:
            print("  ERROR %d — the replay could not grade these programs. Their "
                  "refusals are absent from the vector below, not zero." % errors,
                  file=sys.stderr)
        # And when NOTHING graded, there is no vector to print. An engine that
        # exists but cannot execute reaches here, so the `is_file` check above
        # is not enough on its own: it would leave the same empty vector at
        # exit 0, over a population this binary inflated by re-deriving
        # `native` from a replay that failed. That is the read of nothing this
        # command must not be able to publish.
        if mismatches or errors or joined["unmatched"] or joined["without_refusal"]:
            print("levers: incomplete replay — %d considered, %d MISMATCH, %d ERROR, "
                  "%d unmatched, %d without a refusal. This is not a refusal vector."
                  % (joined["considered"], mismatches, errors, joined["unmatched"],
                     joined["without_refusal"]), file=sys.stderr)
            return 1
    elif args.rows:
        if not args.replay:
            print("levers: --rows needs --replay: a draw row carries no refusal, and "
                  "the replay carries no population label. Use --run to build both.",
                  file=sys.stderr)
            return 2
        rows_path, replay_path = Path(args.rows), Path(args.replay)
        # Same hole as `probe-vector`: absent rows join to an empty vector at
        # exit 0, which reads as a measured absence of refusals.
        for path in (rows_path, replay_path):
            if not path.is_file():
                print("not a file: %s" % path, file=sys.stderr)
                return 2
        drawn = list(read_jsonl(rows_path))
        joined = levers.draw_refusals(drawn, read_jsonl(replay_path),
                                      status=args.status)
        result = levers.table(joined["records"], source=args.rows, unit="draw",
                              independence="family", loaded=len(drawn))
        notes.append("%d draw(s) match status %s; %d carried a refusal"
                     % (joined["considered"], args.status or "(any)",
                        len(joined["records"])))
        if joined["unmatched"] or joined["without_refusal"]:
            notes.append("UNRESOLVED: %d draw(s) have no replay row, %d have a replay "
                         "row carrying no refusal. Neither is counted anywhere above."
                         % (joined["unmatched"], joined["without_refusal"]))
            print("levers: incomplete join — %d considered, %d unmatched, %d without "
                  "a refusal. This is not a refusal vector."
                  % (joined["considered"], joined["unmatched"],
                     joined["without_refusal"]), file=sys.stderr)
            return 1
    else:
        if args.status:
            print("levers: --status applies to draw rows; use it with --run or --rows",
                  file=sys.stderr)
            return 2
        source = args.source or str(DATA / "classified.jsonl")
        rows = list(read_jsonl(Path(source)))
        result = levers.table(rows, source=source, loaded=len(rows))

    if result["unit"] == "draw":
        # A build order read off a benchmark is test-set steering, and this
        # command can be pointed at one. The banner is the same one `refusals`
        # carries, for the same reason.
        from . import refusals as _refusals
        notes.append(_refusals.HELD_OUT_BANNER)
        if args.rank:
            print("levers: refusing --rank on draw/held-out rows; use --vector for "
                  "descriptive by-family counts", file=sys.stderr)
            return 2
        # The backstop under the narrow ERROR guard above, which fires first and
        # names the failed replay because that is the more useful message. This
        # one asks the weaker question that covers the routes the other cannot
        # see: did ANY record back this vector? A present-but-empty
        # `attempts.jsonl` passes the `exists()` check; a `--status` that matches
        # nothing leaves `considered` at 0 and falsifies the other guard's second
        # term; and an engine that runs but is not lypning grades MISMATCH, not
        # ERROR, so `errors` is 0. Each of those printed a complete vector at
        # exit 0 over inputs that read or graded nothing. The vector is
        # publishable only if at least one record backs it.
        #
        # All four counts are on the line because stdout, which a refusal leaves
        # empty, is where the note that used to carry them went: without
        # `considered`, rows that never matched `--status` read as rows that
        # matched and carried no refusal; without the unmatched count, a join on
        # the wrong key reads as a census carrying no refusal at all. `joined` is
        # bound on both routes that label the unit `draw` — the local census is
        # the `entry` unit and never reaches here, and closing its own empty
        # reads (an absent or 0-byte `--source` still prints a table at exit 0)
        # would change what `--rank` and `--against` report over the repository
        # capture, which is not this guard's call to make.
        if not result["refusals"]:
            print("levers: no refusal to publish — %d draw row(s) loaded from %s; "
                  "%d match status %s; %d have no replay row; %d carried a "
                  "refusal. An empty vector over nothing is a read of nothing, "
                  "not a measured absence of refusals."
                  % (result["loaded"], result["source"], joined["considered"],
                     args.status or "(any)", joined["unmatched"],
                     result["refusals"]),
                  file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=sorted))
        return 0

    for note in notes:
        print(note)
    if notes:
        print("")

    failed = False
    if args.declared:
        print(levers.declared_report(levers.declared_rows(result, provenance=args.provenance)))
    elif args.undeclared:
        print(levers.undeclared_report(levers.undeclared_rows(result)))
    elif args.vector:
        print(levers.vector_report(levers.vector(result),
                                   independence=result["independence"],
                                   limit=args.limit))
    elif args.rank:
        print(levers.rank_report(levers.rank(result, bucket=args.bucket),
                                 bucket=args.bucket,
                                 independence=result["independence"],
                                 limit=args.limit))
    else:
        print(levers.report(result, today=today))
        if args.against:
            if not os.path.exists(args.against):
                print("levers: no such file: %s" % args.against, file=sys.stderr)
                return 2
            totals = levers.section4_totals(args.against)
            comparison = levers.compare(result, totals)
            print("")
            print(levers.compare_report(result, comparison))
            failed = failed or (args.strict and not comparison["agrees"])
    if args.strict:
        failed = failed or bool(result["undeclared"]) or bool(result["declared_unused"])
    return 1 if failed else 0


def cmd_probe_vector(args: argparse.Namespace) -> int:
    """S0c: a read-only per-case native-status comparison."""
    from . import probe_vector
    probe_path, base_path = Path(args.probe), Path(args.base)
    # `read_jsonl` answers `[]` for a path that is not there, and two empty
    # sides make `probe_only` empty, which is this command's only failure
    # signal. Absent inputs would therefore print a zeros table and exit 0 —
    # a rung that reads as "every probe case matched" when nothing was read.
    for path in (probe_path, base_path):
        if not path.is_file():
            print("not a file: %s" % path, file=sys.stderr)
            return 2
    try:
        result = probe_vector.compare(read_jsonl(probe_path),
                                      read_jsonl(base_path))
    except (OSError, ValueError) as exc:
        print("probe-vector: %s" % exc, file=sys.stderr)
        return 2
    # Present but empty is the same read of nothing as absent, one step later:
    # a probe stage that produced no rollouts did not run, and a zeros table
    # over it is not a comparison. `probe_only` cannot catch this either.
    if not result["probe_rows"]:
        print("probe-vector: no probe rows in %s — nothing to compare" % probe_path,
              file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(probe_vector.render(result))
    # The completed base pilot may include dev/test cases the train-only probe
    # deliberately lacks. A probe case with no base comparison is the hole.
    return 1 if result["probe_only"] else 0


def cmd_headroom(args: argparse.Namespace) -> int:
    """Whether this arm's population can host the pre-registered effect at all.

    A bank with no room reports "no win" whatever the adapter does, and the
    draws are paid for either way. Run it on the base arm before booking one.
    """
    path = Path(args.metrics)
    # An absent or unparsable metrics.json is a read of nothing, and a table
    # over it would be the S0b/S0c defect again: zeros at exit 0. Exit 2 names
    # the file, and `assess` exits 2 naming the field when one is missing.
    if not path.is_file():
        print("not a file: %s" % path, file=sys.stderr)
        return 2
    try:
        result = headroom_mod.assess(read_json(path), mde=args.mde, noise=args.noise)
    except (OSError, ValueError) as exc:
        print("headroom: %s: %s" % (path, exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(headroom_mod.render(result))
    # A saturated population is a finding, not a usage error: exit 1 so a script
    # that reads this before spending cannot ignore it by ignoring the prose.
    # `can_fire` is None when the file gives no way to tell whether its rate IS
    # EVAL2.md section 4's family macro, and that lands here too: a summary that
    # cannot answer the rule is not one that answers yes, and exit 0 would be
    # read as one. A file that shows its rate is the macro (headroom.
    # macro_decomposition) does get a verdict, and exit 0 when it clears.
    return 0 if result["can_fire"] else 1


def cmd_bank_carve(args: argparse.Namespace) -> int:
    """Carve a bank into a pilot and a held-out benchmark sharing no family.

    Writes nothing unless both banks are admissible — the pilot at every
    protocol seed, because a round is three seeds. A carve that does not
    validate is a finding about the bank, and writing it anyway would move the
    failure onto a metered job.
    """
    from . import bank_carve
    from .jsonio import read_jsonl, write_jsonl
    from .training_types import TrainingError

    path = Path(args.bank)
    if not path.is_file():
        print("not a file: %s" % path, file=sys.stderr)
        return 2
    cases = list(read_jsonl(path))
    if not cases:
        print("no cases in %s" % path, file=sys.stderr)
        return 2
    try:
        result = bank_carve.carve(cases, seed=args.seed,
                                  benchmark_families=args.benchmark_families)
    except TrainingError as exc:
        print("bank-carve: %s" % exc, file=sys.stderr)
        return 1
    print(bank_carve.render(result))
    if result["problems"]:
        return 1
    if args.output:
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        write_jsonl(out / "train.jsonl", result["pilot"])
        write_jsonl(out / "eval2.jsonl", result["benchmark"])
        print("  -> %s/train.jsonl, %s/eval2.jsonl" % (out, out))
    return 0


def cmd_stdlib_sft(args: argparse.Namespace) -> int:
    """The stdlib corpus as a separate SFT arm, with the contaminating units held back.

    A unit cannot be a bank case — `validate_cases` needs three discriminating
    tests and a unit has one fixed stdout — so this writes supervised rows
    instead, to be mixed into an SFT stage as an arm that a matched unmixed run
    is compared against. Nothing here trains, and nothing here is a benchmark.
    """
    from . import stdlib_sft
    from .jsonio import read_jsonl, write_jsonl
    from .training_types import TrainingError

    units_path = Path(args.units)
    if not units_path.is_file():
        print("not a file: %s" % units_path, file=sys.stderr)
        return 2
    units = list(read_jsonl(units_path))
    held_out: List[str] = list(args.held_out or [])
    if args.held_out_bank:
        bank = Path(args.held_out_bank)
        if not bank.is_file():
            print("not a file: %s" % bank, file=sys.stderr)
            return 2
        for case in read_jsonl(bank):
            held_out.extend(case.get("capabilities") or [])
            if case.get("family"):
                held_out.append(case["family"])
    try:
        result = stdlib_sft.sft_rows(units, held_out=held_out,
                                     allow_overlap=args.allow_overlap)
    except TrainingError as exc:
        print("stdlib-sft: %s" % exc, file=sys.stderr)
        return 1
    print(stdlib_sft.render(result, allow_overlap=args.allow_overlap))
    if args.output:
        write_jsonl(Path(args.output), result["rows"])
        print("  -> %s" % args.output)
    # An arm with no rows cannot be measured; that is a finding, not a usage error.
    return 0 if result["rows"] else 1


def cmd_bank_native(args: argparse.Namespace) -> int:
    """What fraction of a bank's own programs the pinned engine already runs.

    `EVAL2.md` section 9's falsifier before a draw is paid for: a bank whose
    programs the engine already serves cannot host a lift in the rate of
    serving them. Free, local, and read-only — it executes the bank's programs
    under the engine and nothing else.
    """
    from .synth import Runner
    from .training_types import VerificationBlocked
    path = Path(args.cases)
    # An absent file read as an empty bank would print a zeros table at exit 0,
    # the same read-of-nothing `probe-vector` and `headroom` each had to close.
    if not path.is_file():
        print("not a file: %s" % path, file=sys.stderr)
        return 2
    rows = read_jsonl(path)
    if not rows:
        print("bank-native: no rows in %s - nothing to measure" % path, file=sys.stderr)
        return 1
    if args.mix_only:
        # Free: reads the labels `pipeline.synth` already wrote and executes
        # nothing, so it needs no engine and answers before a draw is booked.
        mix = bank_native_mod.first_draft_mix(rows)
        print(json.dumps(mix, indent=2, sort_keys=True) if args.json
              else bank_native_mod.render_mix(mix, mde=args.mde, limit=args.limit))
        # A bank nobody can read this way is not a bank with no room.
        if mix["macro_delta_ceiling"] is None:
            return 1
        return 0 if mix["macro_delta_ceiling"] > args.mde else 1
    engine = args.engine or eng.engine_path("lypning-l")
    if not engine or not Path(engine).is_file():
        print("bank-native: no lypning-l engine (pass --engine, or set LYPNING_HOME "
              "and run `lypning build --rust`)", file=sys.stderr)
        return 2
    runner = Runner(engine, timeout_s=args.timeout)
    try:
        result = bank_native_mod.measure(rows, runner.engine_verdict, which=args.program)
    except (OSError, ValueError, VerificationBlocked) as exc:
        print("bank-native: %s" % exc, file=sys.stderr)
        return 2
    # legality.py: "the engine is part of the number ... quoted with its
    # fingerprint or it is not quoted". `binary_identity` names the bytes.
    result["engine"] = eng.binary_identity(engine)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(bank_native_mod.render(result, details=args.details))
    # A read that measured nothing is not a clean read: `--program original`
    # over a bank that carries no refused first drafts measures zero rows, and
    # a zeros table over it reads as "every draft was refused".
    if not result["measured"]:
        print("bank-native: no row carried a %s program" % args.program, file=sys.stderr)
        return 1
    # Invariant 1: a mismatch is a bug. Never exit 0 having seen one.
    return 1 if result["witness_verdicts"] else 0


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


def _stdlib_rows(files: List[str]) -> List[List[Dict[str, Any]]]:
    """Each --rows file's records, in the order the caller named the files.

    The order is the precedence `stdlib.assemble` de-duplicates by, so it is
    preserved rather than sorted here.
    """
    return [list(read_jsonl(f)) for f in files]


def cmd_stdlib_verify(args: argparse.Namespace) -> int:
    """CPython first, then each engine cheapest-first; emit rows and drops.

    Exit 1 on a MISMATCH — an engine that exited 0 with bytes CPython did not
    print is invariant 1's alarm and must not be merely logged. Every other drop
    is a ledger entry the repair stage reads: a candidate that refuses on every
    engine is a recorded gap, not a failed command. `--strict` widens the failure
    to every drop, which is what a check over the COMMITTED units wants — there,
    a malformed file is a defect rather than a candidate.
    """
    from . import stdlib

    cpython = stdlib.resolve_cpython(args.cpython)
    if not cpython:
        print("no cpython found: pass --cpython PATH", file=sys.stderr)
        return 1
    try:
        found, missing = stdlib.resolve_engines(args.engine or [])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if missing:
        print("engine not built: %s — run `lypning build --rust`, or pass "
              "--engine NAME=PATH. Labelling without a cheaper engine would "
              "understate every unit it would have run." % ", ".join(missing),
              file=sys.stderr)
        return 1

    paths: List[Path] = []
    for d in args.units:
        found_here = stdlib.unit_paths(d)
        if not found_here:
            print("no units under %s" % d, file=sys.stderr)
        paths.extend(found_here)
    if not paths:
        print("nothing to verify", file=sys.stderr)
        return 1

    result = stdlib.verify_units(paths, cpython, found,
                                 first_seen=args.first_seen,
                                 producer=args.producer,
                                 timeout_s=args.timeout)
    stdlib.write_rows(args.out, result.rows)
    stdlib.write_rows(args.drops, result.drops)
    print("cpython %s" % cpython)
    for name in stdlib.ENGINE_ORDER:
        if name in found:
            print("%-12s %s" % (name, found[name]))
    print("")
    print(stdlib.report(result.rows, result.drops))
    print("")
    print("%d rows -> %s" % (len(result.rows), args.out))
    print("%d drops -> %s" % (len(result.drops), args.drops))
    fatal = [d for d in result.drops
             if args.strict or d["reason"] in ("mismatch", "engine-missing")]
    if fatal:
        print("")
        print("%d unit(s) failed this check:" % len(fatal), file=sys.stderr)
        for d in fatal:
            print("  %s  %s  %s" % (d["name"], d["reason"], d["detail"]), file=sys.stderr)
        return 1
    return 0


def cmd_stdlib_assemble(args: argparse.Namespace) -> int:
    """Merge row files, de-duplicate by id, sort by name, render the report."""
    from . import stdlib

    groups = _stdlib_rows(args.rows)
    asm = stdlib.assemble(groups)
    stdlib.write_rows(args.out, asm.rows)
    text = stdlib.report(asm.rows)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)
    print("")
    print("%d rows in, %d after de-duplication -> %s"
          % (sum(len(g) for g in groups), len(asm.rows), args.out))
    print("report -> %s" % args.report)
    if asm.duplicates:
        print("%d duplicate id(s) dropped, first writer kept" % len(asm.duplicates))
    if asm.collisions:
        # One source under two names double-counts a corpus. Loud, and fatal.
        print("", file=sys.stderr)
        print("one id arrived under two names — the same unit is committed twice:",
              file=sys.stderr)
        for rid, kept, seen in asm.collisions:
            print("  %s  kept %s, also seen as %s" % (rid, kept, seen), file=sys.stderr)
        return 1
    return 0


def cmd_stdlib_report(args: argparse.Namespace) -> int:
    """Render a corpus that already exists. Reads, runs nothing, writes nothing."""
    from . import stdlib

    if not args.rows:
        print("nothing to render: pass --corpus FILE (or --rows FILE)",
              file=sys.stderr)
        return 2
    rows: List[Dict[str, Any]] = []
    for group in _stdlib_rows(args.rows):
        rows.extend(group)
    rows.sort(key=lambda r: (str(r.get("name") or ""), str(r.get("id") or "")))
    drops = list(read_jsonl(args.drops)) if args.drops else []
    print(stdlib.report(rows, drops))
    return 0


def _stdlib_resolved_on(args: argparse.Namespace) -> str:
    """The date stamped on the plan. From the CLI's clock, never the library's.

    `stdlib_generate` takes it as an argument for the same reason
    `stdlib.unit_row` takes `first_seen`: a library that read the clock would
    rewrite a plan that did not change, and a `--fake` run would stop being
    reproducible. The clock is the CLI's to read.
    """
    if args.resolved_on:
        return str(args.resolved_on)
    from datetime import date
    return date.today().isoformat()


def cmd_stdlib_plan(args: argparse.Namespace) -> int:
    """Resolve the model live, diff the targets against the committed units.

    Two things happen before anything is spent, and both can stop the dispatch:
    the targets file's closed list is checked against the engine's own set, and
    the model is resolved against the provider's `/v1/models` rather than taken
    from a constant. A run that fell back to the pin says so in the plan and on
    this screen, because a stale producer recorded on every row is the kind of
    thing nobody notices in a log.
    """
    from . import stdlib_generate as gen

    try:
        targets = gen.load_targets(args.targets)
    except gen.GenerateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    covered = gen.covered_names(args.units)
    resolved_on = _stdlib_resolved_on(args)
    resolution = gen.resolve_model(
        base_url=args.base_url or "", api_key=os.environ.get("NTX_API_KEY"),
        resolved_on=resolved_on, offline=bool(args.fake),
        override=args.model or os.environ.get("NTX_MODEL"))
    try:
        plan = gen.build_plan(
            targets, covered, resolution, resolved_on, batches=args.batches,
            per_batch=args.targets_per_batch, draws_per_target=args.draws_per_target,
            max_tokens=args.max_tokens, units_dir=args.units or "",
            targets_path=args.targets or "", price_in=args.price_in,
            price_out=args.price_out, currency=args.currency)
    except gen.GenerateError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(plan, indent=2, sort_keys=False,
                                         ensure_ascii=False) + "\n",
                              encoding="utf-8")
    print(gen.plan_report(plan))
    print("")
    print("plan -> %s" % args.out)
    if not plan["batches"]:
        # Not an error in itself, but a dispatch that would spend nothing and
        # produce nothing should not look like a successful generation run.
        print("", file=sys.stderr)
        print("every target in %s is already filled by a committed unit — nothing "
              "to generate" % args.targets, file=sys.stderr)
        return 1
    return 0


def cmd_stdlib_generate(args: argparse.Namespace) -> int:
    """One batch of the plan: draw, extract, gate, write the candidates and ledger.

    Nothing here judges a candidate: the verify stage runs CPython and the
    engines and is the only thing that may. A batch that wrote no candidate at
    all exits 1, because a generation job whose whole output is a ledger of
    refused completions is a job that needs looking at even though every
    individual draw was recorded correctly.
    """
    from . import stdlib_generate as gen

    try:
        plan = read_json(args.plan)
    except (OSError, ValueError) as exc:
        print("cannot read plan %s: %s" % (args.plan, exc), file=sys.stderr)
        return 1
    try:
        backend = gen.backend_for(plan, fake=bool(args.fake),
                                  api_key=os.environ.get("NTX_API_KEY"),
                                  timeout_s=args.request_timeout)
    except (gen.GenerateError, BackendError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        result = gen.generate_batch(plan, args.batch, args.out, backend,
                                    draws_per_target=args.draws_per_target,
                                    max_tokens=args.max_tokens)
    except gen.GenerateError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    draws = args.draws or str(Path(args.out) / "draws.jsonl")
    gen.write_draws(draws, result.draws)
    ident = backend.identity()
    print("model      %s" % ident.get("model"))
    print("base_url   %s" % ident.get("base_url"))
    print("batch      %s of %s" % (args.batch, args.plan))
    print("")
    print(gen.generation_report(result, "generate"))
    print("")
    print("%d candidate(s) -> %s" % (len(result.written), args.out))
    print("%d draw(s) -> %s" % (len(result.draws), draws))
    if not result.written:
        print("", file=sys.stderr)
        print("no candidate survived extraction in batch %s; the ledger says why "
              "for each draw" % args.batch, file=sys.stderr)
        return 1
    return 0


def cmd_stdlib_repair(args: argparse.Namespace) -> int:
    """ONE bounded round over what verify dropped. One call per job, no loop.

    The refusal lines are re-measured against the engines rather than
    reconstructed from the drop's wording, so what the model is shown is the
    sentence the engine actually wrote (invariant 9). With no engine built the
    round still runs on the recorded verdict alone, and this says so.
    """
    from . import stdlib
    from . import stdlib_generate as gen

    try:
        plan = read_json(args.plan)
        drops = list(read_jsonl(args.drops))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        found, missing = stdlib.resolve_engines(args.engine or [])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if missing:
        print("engine not built: %s — the round will feed back the recorded "
              "verdict without a measured refusal line" % ", ".join(missing),
              file=sys.stderr)
    try:
        jobs, skipped = gen.repair_jobs(drops, args.units, plan, found,
                                        timeout_s=args.timeout)
    except gen.GenerateError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for name, why in skipped:
        print("skipped %-28s %s" % (name, why))
    if not jobs:
        print("")
        print("nothing repairable in %s — %d drop(s) read, %d skipped"
              % (args.drops, len(drops), len(skipped)))
        return 0
    try:
        backend = gen.backend_for(plan, fake=bool(args.fake),
                                  api_key=os.environ.get("NTX_API_KEY"),
                                  timeout_s=args.request_timeout)
    except (gen.GenerateError, BackendError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = gen.repair_batch(jobs, args.out, backend, max_tokens=args.max_tokens)
    draws = args.draws or str(Path(args.out) / "draws.jsonl")
    gen.write_draws(draws, result.draws)
    print("")
    print("round 1 of %d — bounded, and there is no round 2" % gen.REPAIR_ROUNDS)
    print(gen.generation_report(result, "repair"))
    print("")
    print("%d candidate(s) -> %s" % (len(result.written), args.out))
    print("%d draw(s) -> %s" % (len(result.draws), draws))
    return 0


# ------------------------------------------------------------------ parser


def cmd_training_prepare(args) -> int:
    from .curriculum import starter_cases
    from .training import prepare, TrainingError
    from .jsonio import read_jsonl
    try:
        cases = starter_cases() if args.starter else read_jsonl(args.cases)
        if args.starter and args.purpose != "smoke":
            raise TrainingError("the starter curriculum is smoke-only")
        bundle = prepare(cases, args.engine, args.output, seed=args.seed,
                         timeout_s=args.timeout, memory_mb=args.memory_mb, purpose=args.purpose,
                         execution_image=args.execution_image, review_path=args.review,
                         execution_kind=args.execution_kind, execution_revision=args.execution_revision)
    except (TrainingError, OSError, subprocess.SubprocessError) as exc:
        print("training preparation blocked: %s" % exc, file=sys.stderr)
        return 1
    counts = {s: sum(c["split"] == s for c in bundle["cases"]) for s in ("train", "dev", "test")}
    print(json.dumps({"digest": bundle["digest"], "cases": counts,
                      "families": len({c["family"] for c in bundle["cases"]}),
                      "reference_scores": bundle["reference_scores"]}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nt", description="LoRA pipeline: corpus and eval")
    sub = p.add_subparsers(dest="cmd", required=True)

    tp = sub.add_parser("training-prepare", help="verify a NEW multi-input lypning-l experiment")
    source = tp.add_mutually_exclusive_group(required=True)
    source.add_argument("--starter", action="store_true", help="authored smoke tasks, not a benchmark")
    source.add_argument("--cases", type=Path, help="independently authored multi-input JSONL")
    tp.add_argument("--engine", type=Path, required=True, help="explicit compiled lypning-l binary")
    tp.add_argument("--output", type=Path, required=True, help="new directory; never overwrite")
    tp.add_argument("--seed", type=int, default=1111)
    tp.add_argument("--execution-image",
                    help="isolation image: an immutable local Docker image ID, or an "
                         "hf.co/spaces/<owner>/<name> image for --execution-kind hf-sandbox-pool; "
                         "required for pilot")
    tp.add_argument("--execution-kind", choices=("docker", "hf-sandbox-pool"), default="docker",
                    help="how generated code is isolated (default docker)")
    tp.add_argument("--execution-revision",
                    help="hf-sandbox-pool only: the Space's immutable 40-character commit")
    tp.add_argument("--review", type=Path, help="data_loop review.json bound to the exact cases/seed")
    tp.add_argument("--purpose", choices=("smoke", "pilot", "benchmark"), default="smoke",
                    help="benchmark: a reviewed bank stage eval measures whole (--eval-split all), never trains on")
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

    e2 = sub.add_parser("eval2-select", help="select reverse-prompting candidates for eval-2 (shape-stratified)")
    e2.add_argument("--output", type=Path, required=True, help="candidates JSONL to write")
    e2.add_argument("--classified", help="classified corpus (default data/classified.jsonl)")
    e2.add_argument("--sightings", help="per-session sightings dir (default tests/corpus/sightings)")
    e2.add_argument("--limit", type=int, default=0, help="stratified draw of this many; 0 keeps all")
    e2.add_argument("--seed", type=int, default=1111)
    e2.add_argument("--jobs", type=int, default=4)
    e2.add_argument("--timeout", type=float, default=10.0)
    e2.set_defaults(fn=cmd_eval2_select)

    e2l = sub.add_parser("eval2-legacy",
                         help="project a schema-3 eval-2 bank to records `harvest --source jsonl:` takes")
    e2l.add_argument("--bank", required=True, help="eval-2 bank (schema-3 cases JSONL)")
    e2l.add_argument("--output", required=True, help="records JSONL to write")
    e2l.set_defaults(fn=cmd_eval2_legacy)

    e2r = sub.add_parser("eval2-rows",
                         help="a legacy run's attempts as training_metrics rows (native off a replay)")
    e2r.add_argument("run_id")
    e2r.add_argument("--output", required=True, help="rows JSONL to write")
    e2r.add_argument("--engine", help="binary to replay through (default: lypning-l)")
    e2r.add_argument("--jobs", type=int, default=8)
    e2r.add_argument("--cache", help="directory of per-run replay verdicts (see `legality --cache`)")
    e2r.set_defaults(fn=cmd_eval2_rows)

    e2b = sub.add_parser("eval2-bank",
                         help="assemble a schema-3 eval-2 bank from authored proposals, verified by execution")
    e2b.add_argument("--proposals", required=True, help="JSON array of authoring batch results")
    e2b.add_argument("--candidates", required=True, help="eval2-select candidates JSONL")
    e2b.add_argument("--evidence", action="append", help="lypning.evidence snapshot dir (repeatable)")
    e2b.add_argument("--engine", help="lypning-l binary (default: lypning-l, then $LYPNING_L_BIN)")
    e2b.add_argument("--output", required=True, help="NEW directory for bank.jsonl, report.json, dropped.jsonl, witnesses.jsonl")
    e2b.add_argument("--seed", type=int, default=1111, help="hash seed of the second reference run")
    e2b.add_argument("--keep-disagreements", action="store_true",
                     help="keep (flagged) cases whose independent solution disagrees or is missing")
    e2b.add_argument("--timeout", type=float, default=10.0)
    e2b.set_defaults(fn=cmd_eval2_bank)

    sgn = sub.add_parser("synth-generate",
                         help="bank v3 step 1: ask Qwen on Cerebras for tasks and k programs each (runs nothing)")
    sgn.add_argument("--output", required=True, help="candidates JSONL, appended to; resumable")
    sgn.add_argument("--exclude-tasks", action="append",
                     help="JSONL whose task texts are never regenerated (repeatable)")
    sgn.add_argument("--tasks-per-call", type=int, default=8)
    sgn.add_argument("--samples", type=int, default=3,
                     help="independent programs per task; the self-consistency k")
    sgn.add_argument("--max-calls", type=int, default=200)
    sgn.add_argument("--max-output-tokens", type=int, default=1_500_000)
    sgn.add_argument("--max-minutes", type=float, default=50.0)
    sgn.add_argument("--timeout", type=float, default=120.0, help="per-request seconds")
    sgn.add_argument("--seed", type=int, default=1111)
    sgn.add_argument("--rewrite-fraction", type=float, default=66.0 / 93.0,
                     help="share of requests aimed at a REWRITABLE construct; the "
                          "preregistered pool is 66 rewrite to 27 ceiling (0.71)")
    sgn.set_defaults(fn=cmd_synth_generate)

    sad = sub.add_parser("synth-adapt",
                         help="bank v3 step 2: execute, judge, repair and write schema-3 cases (no provider key)")
    sad.add_argument("--candidates", required=True, help="synth-generate output")
    sad.add_argument("--engine", required=True, help="the pinned lypning-l binary that decides native versus refused")
    sad.add_argument("--output", required=True,
                     help="NEW directory for cases.jsonl, unrepaired.jsonl, rejected.jsonl, witnesses.jsonl, report.json")
    sad.add_argument("--agree", type=int, default=2, help="samples that must agree for a case to survive")
    sad.add_argument("--batch", default=os.environ.get("GITHUB_RUN_ID", "local"),
                     help="batch identity written into every case's provenance")
    sad.add_argument("--timeout", type=float, default=10.0)
    sad.add_argument("--memory-mb", type=int, default=1024,
                     help="child address-space cap; 0 explicitly disables it (macOS only)")
    sad.add_argument("--seed", type=int, default=1111, help="hash seed of the winner's second run")
    sad.add_argument("--limit", type=int, default=0, help="judge only the first N candidates; 0 keeps all")
    # A shard is `--offset`/`--limit`, not a pre-split file. Every shard job
    # downloads the whole candidates artifact anyway — `download-artifact` has
    # no partial fetch — so splitting the file first buys an extra job and a
    # second artifact and nothing else, while an offset is one integer that
    # composes with the limit already here. Each shard writes its own batch.
    sad.add_argument("--offset", type=int, default=0,
                     help="skip the first N candidates; with --limit this is one shard of a batch")
    sad.set_defaults(fn=cmd_synth_adapt)

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
    e.add_argument("--top-k", type=int, default=None,
                   help="top-k sampling, sent to vLLM/SGLang as a top-level key; "
                        "unset leaves it to the server (every run before 2026-09-16)")
    e.add_argument("--max-tokens", type=int, default=4096)
    e.add_argument("--max-spend", type=float, default=0.0, help="abort above this many dollars")
    e.add_argument("--price-hour", type=float, default=0.0,
                   help="dollars per GPU-hour, when you rent the box instead of the tokens")
    e.add_argument("--no-thinking", action="store_true")
    e.add_argument("--system-file", default=None,
                   help="stage 0b: a text file appended to the system prompt as its "
                        "own paragraph (training/prompts/subset-spec.md); moves "
                        "prompt_sha and is recorded as meta.system_file_sha256")
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
    pw.add_argument("run_id", nargs="?")
    pw.add_argument("--trials", type=int, default=200,
                    help="simulated runs per lift (default 200)")
    pw.add_argument("--eval2", action="store_true",
                    help="EVAL2.md section 7: family-cluster power against bank size, from --rows")
    pw.add_argument("--rows", help="training_metrics rows JSONL of the pilot, or a run id "
                                   "with runs/<id>/eval2_rows.jsonl")
    pw.add_argument("--mde", type=float, default=0.03, help="the rule's bar (default 0.03)")
    pw.add_argument("--sizes", type=_csv_ints, default=list(stats.CLUSTER_SIZES),
                    help="candidate bank sizes (default %s)" % ",".join(map(str, stats.CLUSTER_SIZES)))
    pw.add_argument("--deltas", type=_csv_floats, default=list(stats.CLUSTER_DELTAS),
                    help="true effects to simulate, 0 for the null (default %s)"
                         % ",".join(map(str, stats.CLUSTER_DELTAS)))
    pw.add_argument("--draws", type=int, default=0, help="k per case (default: the pilot's)")
    pw.add_argument("--resamples", type=int, default=400, help="bootstrap resamples per bank")
    pw.set_defaults(fn=cmd_power)

    lk = sub.add_parser("leaks", help="train cases that are the same question as a held-out one")
    lk.add_argument("--ceiling", type=float, default=splitmod.SIMILARITY_CEILING)
    lk.add_argument("--sft", default="", help="an sft.jsonl (or its directory): run every "
                                              "target against the held-out cases themselves")
    lk.add_argument("--verbose", action="store_true")
    lk.set_defaults(fn=cmd_leaks)

    el = sub.add_parser("eval2-leaks",
                        help="training cases that are an eval-2 case in disguise")
    el.add_argument("eval2", help="eval-2 bank (schema-3 cases JSONL)")
    el.add_argument("train", help="training bank in the same shape")
    el.add_argument("--ceiling", type=float, default=splitmod.SIMILARITY_CEILING,
                    help="task-text similarity at or above which a pair leaks")
    el.add_argument("--min-stdout", type=int, default=8,
                    help="expected stdouts shorter than this are ignored")
    el.add_argument("--allow", action="store_true",
                    help="report pairs but exit 0 anyway")
    el.add_argument("--json", action="store_true", help="print the report as JSON")
    el.set_defaults(fn=cmd_eval2_leaks)

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

    lv = sub.add_parser("levers", help="which lever can remove each refusal: engine, model, neither")
    lv.add_argument("--source", help="a `nt classify` jsonl (default: data/classified.jsonl)")
    lv.add_argument("--run", metavar="RUN_ID",
                    help="rung S0b: a run's draws joined to their replay verdicts")
    lv.add_argument("--engine", help="binary to replay through (with --run)")
    lv.add_argument("--require-engine-sha256",
                    help="with --run, refuse unless the explicit replay binary has this sha256")
    lv.add_argument("--cache", help="directory of <run>.replay.json caches (with --run)")
    lv.add_argument("--jobs", type=int, default=0, help="replay workers (with --run)")
    lv.add_argument("--population-rows",
                    help="with --run, freeze status/family/population to these eval2 rows; "
                         "the replay supplies refusal kinds only")
    lv.add_argument("--expect-draws", type=int,
                    help="refuse unless exactly N draw rows match --status")
    lv.add_argument("--rows", help="draw rows already written by `nt eval2-rows`")
    lv.add_argument("--replay", help="the legality rows to join them with (with --rows)")
    lv.add_argument("--status", help="keep only draws with this status, e.g. correct-fallback")
    lv_order = lv.add_mutually_exclusive_group()
    lv_order.add_argument("--rank", action="store_true",
                          help="the build order: independent x units")
    lv_order.add_argument("--vector", action="store_true",
                          help="descriptive by-family counts, safe for held-out draw rows")
    lv.add_argument("--bucket", default="engine-addressable", help="which bucket to rank")
    lv.add_argument("--limit", type=int, default=20, help="rows to show (0 for all)")
    lv.add_argument("--declared", action="store_true", help="every judgement call, for review")
    lv.add_argument("--provenance", choices=["s4", "new"],
                    help="with --declared: only rows from §4, or only new ones")
    lv.add_argument("--undeclared", action="store_true", help="the review queue")
    lv.add_argument("--against", nargs="?", const=str(ROOT / "ASSESSMENT.md"),
                    help="compare the four totals with ASSESSMENT.md §4's table")
    lv.add_argument("--json", action="store_true", help="the raw result")
    lv.add_argument("--strict", action="store_true",
                    help="exit 1 on an unreviewed family, an unused declaration or a delta")
    lv.set_defaults(fn=cmd_levers)

    pv = sub.add_parser("probe-vector", help="S0c: probe vs base native status per train case")
    pv.add_argument("--probe", required=True, help="probe/probe-rollouts.jsonl")
    pv.add_argument("--base", required=True, help="completed base-pilot rows JSONL")
    pv.add_argument("--json", action="store_true", help="machine-readable full table")
    pv.set_defaults(fn=cmd_probe_vector)

    hr = sub.add_parser("headroom",
                        help="whether an arm's population can host the pre-registered effect")
    hr.add_argument("metrics", help="a metrics.json from an eval arm")
    hr.add_argument("--mde", type=float, default=headroom_mod.PREREGISTERED_MDE,
                    help="the rule's bar as a fraction (PREREGISTRATION.md section 7b: "
                         "the CI lower bound must exceed +3pp, default %g)"
                         % headroom_mod.PREREGISTERED_MDE)
    hr.add_argument("--noise", type=float, default=headroom_mod.NOISE_FLOOR,
                    help="arm-to-arm noise the interval must clear the bar by "
                         "(default %g: %s); 0 asks the strict ceiling question instead"
                         % (headroom_mod.NOISE_FLOOR, headroom_mod.NOISE_PROVENANCE))
    hr.add_argument("--json", action="store_true", help="machine-readable full table")
    hr.set_defaults(fn=cmd_headroom)

    bc = sub.add_parser("bank-carve",
                        help="split a bank into a pilot and a held-out benchmark sharing no family")
    bc.add_argument("bank", help="a schema-3 JSONL bank (the union of every batch)")
    bc.add_argument("--output", help="directory to write train.jsonl and eval2.jsonl into")
    bc.add_argument("--seed", type=int, default=1111, help="which families the benchmark takes")
    bc.add_argument("--benchmark-families", type=int,
                    help="total families for the benchmark (default: the bank's own ratio, "
                         "clamped to the floors both banks have)")
    bc.set_defaults(fn=cmd_bank_carve)

    sl = sub.add_parser("stdlib-sft",
                        help="the stdlib corpus as a separate, measurable SFT arm")
    sl.add_argument("units", help="training/data/stdlib/stdlib.jsonl")
    sl.add_argument("--output", help="supervised rows JSONL to write")
    sl.add_argument("--held-out-bank",
                    help="a schema-3 bank whose families the benchmark reaches; units "
                         "filling those modules are held back")
    sl.add_argument("--held-out", action="append",
                    help="an extra module/capability the benchmark reaches (repeatable)")
    sl.add_argument("--allow-overlap", action="store_true",
                    help="mix the overlapping units anyway; the benchmark result must "
                         "then be read as contaminated, and said so in the report")
    sl.set_defaults(fn=cmd_stdlib_sft)

    bn = sub.add_parser("bank-native",
                        help="what fraction of a bank's own programs the engine already serves")
    bn.add_argument("cases", help="a schema-3 JSONL bank (cases.jsonl / train.jsonl)")
    bn.add_argument("--engine", help="lypning-l binary (default: $LYPNING_HOME/bin/lypning-l)")
    bn.add_argument("--program", choices=bank_native_mod.PROGRAMS, default="reference",
                    help="which program to judge: the shipped `reference`, or the refused "
                         "first draft under synth.original (default: reference)")
    bn.add_argument("--timeout", type=float, default=10.0, help="per-run wall clock, seconds")
    bn.add_argument("--details", action="store_true",
                    help="LOCAL ONLY: also print refusal details, which can echo an "
                         "identifier from the program that provoked them")
    bn.add_argument("--mix-only", action="store_true",
                    help="free: the per-family first-draft mix and the macro delta ceiling "
                         "it implies, off the labels, executing nothing and needing no engine")
    bn.add_argument("--mde", type=float, default=headroom_mod.PREREGISTERED_MDE,
                    help="the bar --mix-only compares its ceiling against (PREREGISTRATION.md "
                         "section 7b, default %g)" % headroom_mod.PREREGISTERED_MDE)
    bn.add_argument("--limit", type=int, default=0, help="--mix-only: families to print (0 = all)")
    bn.add_argument("--json", action="store_true", help="machine-readable full table")
    bn.set_defaults(fn=cmd_bank_native)

    sh = sub.add_parser("show", help="print the programs a run produced")
    sh.add_argument("run_id"); sh.add_argument("--case"); sh.add_argument("--limit", type=int, default=5)
    sh.add_argument("--failed-only", action="store_true"); sh.set_defaults(fn=cmd_show)

    # The stdlib corpus: units in, labelled rows out. `stdlib-plan`,
    # `stdlib-generate` and `stdlib-repair` are the generation half and are
    # registered separately.
    sv = sub.add_parser("stdlib-verify",
                        help="label each stdlib unit with the cheapest engine that runs it")
    sv.add_argument("--units", action="append", required=True, metavar="DIR",
                    help="a directory of unit files; repeatable")
    sv.add_argument("--out", required=True, metavar="FILE", help="labelled rows (jsonl)")
    sv.add_argument("--drops", required=True, metavar="FILE",
                    help="one row per unit that is NOT a corpus row, with the reason")
    sv.add_argument("--first-seen", required=True, metavar="DATE",
                    help="the date stamped on every row; passed in, never taken "
                         "from the clock, so a re-verify rewrites nothing")
    sv.add_argument("--cpython", help="the oracle (default: the real python3, past any shim)")
    sv.add_argument("--engine", action="append", metavar="NAME=PATH",
                    help="pin one engine binary; repeatable")
    sv.add_argument("--producer", default="authored",
                    help="provenance for every row: 'authored' for a committed "
                         "seed unit, or the model id that generated it")
    sv.add_argument("--timeout", type=float, default=30.0, metavar="S")
    sv.add_argument("--strict", action="store_true",
                    help="fail on ANY drop, not only on a mismatch — what a "
                         "check over the committed units wants")
    sv.set_defaults(fn=cmd_stdlib_verify)

    sa = sub.add_parser("stdlib-assemble",
                        help="merge row files, de-duplicate by id, render the report")
    sa.add_argument("--rows", action="append", required=True, metavar="FILE",
                    help="a labelled-rows file; repeatable, first writer wins")
    sa.add_argument("--out", required=True, metavar="FILE")
    sa.add_argument("--report", required=True, metavar="FILE")
    sa.set_defaults(fn=cmd_stdlib_assemble)

    sr = sub.add_parser("stdlib-report", help="render a stdlib corpus that already exists")
    # `--corpus` and `--rows` are the same slot under two names, because the
    # file is the same file at both ends of the pipeline: `stdlib-verify`
    # writes `--out` rows, `stdlib-assemble` merges them into a corpus, and a
    # reader rendering either should not have to know which one they hold.
    sr.add_argument("--rows", "--corpus", action="append", dest="rows",
                    metavar="FILE",
                    help="a rows or corpus file; repeatable, rendered as one")
    sr.add_argument("--drops", metavar="FILE")
    sr.set_defaults(fn=cmd_stdlib_report)

    # The generation half. The spend knobs are all here and all bounded:
    # `stdlib-plan` multiplies them into a stated ceiling and prints it, so a
    # dispatcher reads the worst case before authorising the run rather than
    # discovering it on an invoice.
    sp = sub.add_parser("stdlib-plan",
                        help="resolve the generation model and pick the targets "
                             "no committed unit fills yet")
    sp.add_argument("--units", metavar="DIR", default="training/stdlib/units",
                    help="committed units; their `# fills:` headers ARE the "
                         "coverage, so a target they cover is dropped")
    sp.add_argument("--targets", metavar="FILE", default="training/stdlib/targets.json")
    sp.add_argument("--out", required=True, metavar="FILE", help="the plan (json)")
    sp.add_argument("--batches", type=int, default=2, metavar="N",
                    help="how many batches this dispatch may run")
    sp.add_argument("--targets-per-batch", type=int, default=4, metavar="N")
    sp.add_argument("--draws-per-target", type=int, default=2, metavar="N",
                    help="a CEILING on retries, not a count: drawing stops at the "
                         "first structurally valid candidate")
    sp.add_argument("--max-tokens", type=int, default=6144, metavar="N",
                    help="per call; the run's output ceiling is this times the "
                         "call ceiling")
    sp.add_argument("--model", metavar="ID",
                    help="name the model instead of resolving it against "
                         "{base}/models; recorded as such in the plan")
    sp.add_argument("--base-url", metavar="URL", help="default: $NTX_BASE_URL")
    sp.add_argument("--resolved-on", metavar="DATE",
                    help="the date stamped on the plan (default: today)")
    sp.add_argument("--price-in", type=float, default=0.25, metavar="X",
                    help="published price per 1M prompt tokens, for the ceiling")
    sp.add_argument("--price-out", type=float, default=0.50, metavar="X")
    sp.add_argument("--currency", default="EUR")
    sp.add_argument("--fake", action="store_true",
                    help="the deterministic offline provider: resolve nothing, "
                         "fetch nothing, spend nothing")
    sp.set_defaults(fn=cmd_stdlib_plan)

    sg = sub.add_parser("stdlib-generate", help="one batch of a plan into candidate units")
    sg.add_argument("--plan", required=True, metavar="FILE")
    sg.add_argument("--batch", type=int, required=True, metavar="N")
    sg.add_argument("--out", required=True, metavar="DIR", help="candidate unit files")
    sg.add_argument("--draws", metavar="FILE",
                    help="the ledger of every raw completion, kept whether or not "
                         "it became a candidate (default: DIR/draws.jsonl)")
    sg.add_argument("--draws-per-target", type=int, default=0, metavar="N",
                    help="override the plan's ceiling")
    sg.add_argument("--max-tokens", type=int, default=0, metavar="N",
                    help="override the plan's per-call ceiling")
    sg.add_argument("--request-timeout", type=float, default=300.0, metavar="S")
    sg.add_argument("--fake", action="store_true")
    sg.set_defaults(fn=cmd_stdlib_generate)

    srp = sub.add_parser("stdlib-repair",
                         help="ONE bounded round over what verify dropped")
    srp.add_argument("--units", required=True, metavar="DIR",
                     help="where the rejected candidates are; a drop whose file is "
                          "gone is skipped, never regenerated from scratch")
    srp.add_argument("--drops", required=True, metavar="FILE")
    srp.add_argument("--plan", required=True, metavar="FILE")
    srp.add_argument("--out", required=True, metavar="DIR")
    srp.add_argument("--draws", metavar="FILE")
    srp.add_argument("--engine", action="append", metavar="NAME=PATH",
                     help="pin one engine binary; used to RE-MEASURE the refusal "
                          "line that is fed back")
    srp.add_argument("--max-tokens", type=int, default=6144, metavar="N")
    srp.add_argument("--timeout", type=float, default=30.0, metavar="S",
                     help="per engine run while re-measuring")
    srp.add_argument("--request-timeout", type=float, default=300.0, metavar="S")
    srp.add_argument("--fake", action="store_true")
    srp.set_defaults(fn=cmd_stdlib_repair)
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
