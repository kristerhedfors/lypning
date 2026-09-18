"""Execute the generated candidates, apply the oracle, and route by what the engine does.

Runs in a job with **no provider key**: it executes model-written code, and
`HARVESTING.md` says the model-facing execution boundary must never hold the
upstream secret. `cerebras_gen.py` holds the key and executes nothing; this holds
no key and executes everything. The split is the point.

THE ORACLE. A task is answered k times independently by `cerebras_gen.py`. Here
each sample runs on every input and a task survives only when at least `--agree`
samples produce byte-identical stdout on all of them, with exit 0 and empty
stderr. That majority string becomes the expected output.

Self-consistency is real evidence and it is not independence: the samples share
a model and a prompt, so they share a misreading of an ambiguous task. Rows carry
`oracle_basis: self-consistency`, and a report must keep this tier separate from
`bank_v2`, whose expectations were derived from the task by a different author
than the reference.

Determinism is checked, not assumed: the winning program runs twice and a row
whose own output moves between two clean runs is dropped, not scored. That is
the `unstable` policy (v3, 2026-09-16) applied at authoring time.

ROUTING, which is the `ORCHESTRATION.md` verified-outcome table:
  correct and native  -> a coverage case, ready to train on
  correct but refused -> the repair queue, tagged with the refusal kind
  incorrect/unstable  -> retained as an observation, never a training row
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

LIMIT_S = 10.0


def run(python, program, spec, workdir, *, timeout=LIMIT_S):
    for name, text in (spec.get("files") or {}).items():
        if "/" in name or name.startswith("."):
            return None
        (workdir / name).write_text(text, encoding="utf-8")
    try:
        proc = subprocess.run(
            [python, "-I", "-c", program] + list(spec.get("argv") or []),
            input=(spec.get("stdin") or ""), capture_output=True, text=True,
            cwd=str(workdir), timeout=timeout)
    except (subprocess.TimeoutExpired, OSError, ValueError, UnicodeError):
        return None
    return proc


def outputs_for(python, program, inputs, workdir):
    """Every input's stdout, or None if the program failed anywhere."""
    got = []
    for spec in inputs:
        for p in workdir.iterdir():
            p.unlink()
        proc = run(python, program, spec, workdir)
        if proc is None or proc.returncode != 0 or proc.stderr.strip():
            return None
        got.append(proc.stdout)
    return got


def engine_verdict(engine, program, inputs, workdir):
    """What the pinned engine does with this program: native, or a refusal kind."""
    refusals = set()
    for spec in inputs:
        for p in workdir.iterdir():
            p.unlink()
        proc = run(engine, program, spec, workdir)
        if proc is None:
            return "error", None
        if proc.returncode == 90:
            line = (proc.stderr or "").strip().splitlines()[-1:] or [""]
            # `<engine>: unsupported: <kind>: <detail>` — keep kind and detail,
            # drop the engine name so the bucket is about the construct.
            parts = line[0].split("unsupported:", 1)
            refusals.add(parts[1].strip() if len(parts) > 1 else line[0])
            continue
        if proc.returncode != 0:
            return "error", None
    if refusals:
        return "refused", sorted(refusals)
    return "native", None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidates", required=True, type=Path)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--out-native", required=True, type=Path)
    ap.add_argument("--out-repair", required=True, type=Path)
    ap.add_argument("--out-rejected", required=True, type=Path)
    ap.add_argument("--agree", type=int, default=2)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not Path(args.engine).is_file():
        print("not a file: %s" % args.engine, file=sys.stderr)
        return 2
    rows = [json.loads(l) for l in args.candidates.read_text(encoding="utf-8").splitlines() if l]
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print("no candidates in %s — nothing was triaged" % args.candidates, file=sys.stderr)
        return 1

    native, repair, rejected = [], [], []
    tally = Counter()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for row in rows:
            inputs, programs = row["inputs"], row["programs"]
            results = [(p, outputs_for(args.python, p, inputs, workdir)) for p in programs]
            usable = [(p, o) for p, o in results if o is not None]
            tally["samples_failed"] += len(results) - len(usable)
            if len(usable) < args.agree:
                tally["no_quorum"] += 1
                rejected.append({**row, "why": "fewer than %d samples ran clean" % args.agree})
                continue

            votes = Counter(tuple(o) for _, o in usable)
            best, count = votes.most_common(1)[0]
            if count < args.agree:
                # Samples ran but disagreed: the task is ambiguous or the model
                # is guessing. Either way there is no expected output to keep.
                tally["disagreed"] += 1
                rejected.append({**row, "why": "no %d samples agreed" % args.agree})
                continue
            winner = next(p for p, o in usable if tuple(o) == best)

            again = outputs_for(args.python, winner, inputs, workdir)
            if again is None or tuple(again) != best:
                tally["unstable"] += 1
                rejected.append({**row, "why": "output moved between two clean runs"})
                continue
            if all(not s.strip() for s in best):
                tally["empty_output"] += 1
                rejected.append({**row, "why": "every expected output is blank"})
                continue

            verdict, kinds = engine_verdict(args.engine, winner, inputs, workdir)
            # Carry the stratum through. Without it a refused CEILING case is
            # indistinguishable from a rewrite case no rule could fix, and the
            # two are opposites: the first is the right answer keeping its
            # import, the second is a failure. `PREREGISTRATION.md` §2g reserves
            # 27 of 93 pool cases for the first, so losing them loses the
            # counterweight that stops an arm scoring well by avoiding imports.
            record = {"task": row["task"], "domain": row["domain"], "inputs": inputs,
                      "program": winner, "expected": list(best),
                      "agreement": count, "samples": len(programs), "model": row["model"],
                      "stratum": row.get("stratum"),
                      "target_construct": row.get("target_construct")}
            if verdict == "native":
                tally["native"] += 1
                native.append(record)
            elif verdict == "refused":
                tally["refused"] += 1
                repair.append({**record, "refusals": kinds})
            else:
                tally["engine_error"] += 1
                rejected.append({**row, "why": "engine could not grade the program"})

    for path, rows_out in ((args.out_native, native), (args.out_repair, repair),
                           (args.out_rejected, rejected)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows_out),
                        encoding="utf-8")

    kinds = Counter(k for r in repair for k in r["refusals"])
    print(json.dumps({
        "candidates": len(rows), "native": len(native), "repair_queue": len(repair),
        "rejected": len(rejected), "tally": dict(tally),
        "top_refusal_kinds": kinds.most_common(15),
    }, indent=2))
    # A triage that kept nothing is a failed read, not a clean one.
    return 0 if (native or repair) else 1


if __name__ == "__main__":
    sys.exit(main())
