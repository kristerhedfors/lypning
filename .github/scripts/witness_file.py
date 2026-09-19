"""Turn a batch's witnesses into `engine-mismatches.jsonl` rows. Two halves, two steps.

A witness is a program CPython ran clean whose engine run disagreed --
invariant 1 says that is a bug and never a data point, and
`training/data/engine-mismatches.jsonl` is where such bugs are filed, program
text included, so the engine round can reproduce them. The witnesses live on
the private Hub under `bank-v3/batches/<batch>/witnesses.jsonl`; this is how
they get from there to the file, without ever appearing in a public log.

  fetch      needs HF_TOKEN; downloads the witness files; executes NOTHING.
  reproduce  needs NO token; re-runs each witness under both interpreters and
             writes the mismatch rows to --output, an artifact path.

The split is the safety property from `bank-v3.yml`: the step holding a
credential never executes model-written code. Do not merge the two.
Stdout carries counts, the mismatch kind and the input index only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def rows_of(path: Path):
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def fetch(batches, out_dir: Path) -> int:
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi, hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError

    api = HfApi(token=token)
    repo = "%s/%s" % (api.whoami()["name"], os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for batch in batches:
        name = "bank-v3/batches/%s/witnesses.jsonl" % batch
        try:
            got = hf_hub_download(repo, name, repo_type="dataset", token=token)
        except EntryNotFoundError:
            print("batch %s: no witnesses file" % batch)
            continue
        dest = out_dir / ("%s.jsonl" % batch)
        dest.write_bytes(Path(got).read_bytes())
        n = len(rows_of(dest))
        total += n
        print("batch %s: %d witness(es)" % (batch, n))
    print("fetched %d witness(es) into %s" % (total, out_dir))
    return 0


def reproduce(in_dir: Path, engine: str, output: Path, found_by: str) -> int:
    from pipeline.synth import Runner, case_id_for
    from pipeline.training_types import VerificationBlocked

    runner = Runner(engine)
    filed, unreproduced = [], 0
    for path in sorted(in_dir.glob("*.jsonl")):
        for w in rows_of(path):
            row = w.get("row") or {}
            program, tests = row.get("program"), row.get("tests") or []
            if not program or not tests:
                unreproduced += 1
                continue
            try:
                verdict, kinds = runner.engine_verdict(program, tests)
            except VerificationBlocked as exc:
                print("%s: harness blocked: %s" % (path.stem, exc), file=sys.stderr)
                unreproduced += 1
                continue
            if verdict not in ("mismatch", "crash", "bad-refusal"):
                # The engine no longer disagrees: the witness has been fixed
                # since the batch ran, or never reproduced. Either way, not filed.
                print("%s: %s -- no longer a witness (engine says %s)" % (path.stem, w.get("why"), verdict))
                unreproduced += 1
                continue
            # The input the disagreement was seen on, and both outputs from it.
            idx = 0
            for i, test in enumerate(tests):
                native = runner._exec(program, test, native=True)
                if native.exit_code != 0 or native.stdout != test["stdout"]:
                    idx = i
                    break
            native = runner._exec(program, tests[idx], native=True)
            filed.append({
                "case_id": case_id_for(row["task"], tests),
                "found_by": found_by,
                "program": program,
                "expected": tests[idx]["stdout"],
                "native_stdout": native.stdout,
                "kind": "%s on input %d (%s)" % (verdict, idx, kinds[0] if kinds else "no detail"),
            })
            print("%s: filed %s on input %d" % (path.stem, verdict, idx))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for r in filed:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("filed %d mismatch row(s) to %s; %d not reproduced" % (len(filed), output, unreproduced))
    # No witness reproduced is a failed read, not a clean one: the batch said
    # there was one.
    return 0 if filed or not any(in_dir.glob("*.jsonl")) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--batch", action="append", required=True)
    f.add_argument("--dir", type=Path, required=True, help="RUNNER_TEMP only")
    r = sub.add_parser("reproduce")
    r.add_argument("--dir", type=Path, required=True)
    r.add_argument("--engine", required=True)
    r.add_argument("--output", type=Path, required=True, help="artifact path, never the log")
    r.add_argument("--found-by", required=True)
    args = ap.parse_args()
    if args.cmd == "fetch":
        return fetch(args.batch, args.dir)
    return reproduce(args.dir, args.engine, args.output, args.found_by)


if __name__ == "__main__":
    sys.exit(main())
