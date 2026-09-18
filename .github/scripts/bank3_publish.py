"""Accumulate bank-v3 batches in the private dataset repo, and seed dedup from it.

Two modes, because growing a bank to five figures takes many runs and an Actions
artifact expires in 14 days (`HARVESTING.md`: "Retention is bounded, not a
promise"). The dataset repo is the durable home.

  --push  upload this run's `synth-adapt` output as a new immutable batch.
  --seed  write every task text already banked, so the generator can skip them.

This process runs no model-written code, which is why it may hold HF_TOKEN. The
job that executes candidates must not, and does not. It is transport: what a
row IS was decided in `pipeline.synth`, which already separated a banked
ceiling row from a rewrite row that still owes a repair.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

#: What a batch carries. `cases.jsonl` is the bank (schema-3, both populations);
#: `unrepaired.jsonl` is the capability request the next engine round reads;
#: `witnesses.jsonl` is engine bugs, kept because invariant 1 says a mismatch
#: is never discarded; `report.json` is the counts. Rejected rows stay in the
#: 14-day artifact: they are observations, not evidence anyone re-reads.
PUBLISHED = ("cases.jsonl", "unrepaired.jsonl", "witnesses.jsonl", "report.json")
BANK = "cases.jsonl"


def api():
    from huggingface_hub import HfApi

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        raise SystemExit(2)
    return HfApi(token=token)


def repo_id(client):
    return "%s/%s" % (client.whoami()["name"],
                      os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))


def rows_of(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--push", type=Path, help="the synth-adapt output directory")
    ap.add_argument("--seed", type=Path, help="write already-seen task texts here")
    ap.add_argument("--batch", default=os.environ.get("GITHUB_RUN_ID", "local"))
    args = ap.parse_args()

    client = api()
    repo = repo_id(client)
    info = client.repo_info(repo, repo_type="dataset")
    if not info.private:
        print("refusing: %s is not private" % repo, file=sys.stderr)
        return 2

    if args.seed is not None:
        # Every task already seen — banked, owed a repair, or a witness — so a
        # later run does not pay to regenerate it. Earlier batches used
        # native/repaired/ceiling files; every layout carries `task`. Absence is
        # normal on the first run and is reported rather than treated as empty.
        from huggingface_hub import snapshot_download

        tasks = set()
        try:
            local = snapshot_download(repo, repo_type="dataset",
                                      allow_patterns=["bank-v3/batches/**"],
                                      token=os.environ["HF_TOKEN"].strip())
        except Exception as exc:                                  # noqa: BLE001
            print("no banked batches yet (%s); seeding empty" % type(exc).__name__)
            local = None
        if local:
            for path in sorted(Path(local).rglob("*.jsonl")):
                for row in rows_of(path):
                    if isinstance(row, dict) and isinstance(row.get("task"), str):
                        tasks.add(row["task"])
                    elif isinstance(row, dict) and isinstance(row.get("row"), dict):
                        task = row["row"].get("task")      # a witness carries its row
                        if isinstance(task, str):
                            tasks.add(task)
        args.seed.parent.mkdir(parents=True, exist_ok=True)
        args.seed.write_text(
            "".join(json.dumps({"task": t}) + "\n" for t in sorted(tasks)), encoding="utf-8")
        print("seeded %d already-seen task(s) from %s" % (len(tasks), repo))
        return 0

    if args.push is None:
        print("one of --push or --seed is required", file=sys.stderr)
        return 2

    bank = args.push / BANK
    cases = rows_of(bank) if bank.is_file() else []
    if not cases:
        # A batch of nothing is not a batch. Uploading it would grow the
        # directory count while leaving the bank the size it was.
        print("no cases in %s — nothing to publish" % bank, file=sys.stderr)
        return 1
    populations = {}
    for row in cases:
        populations[row.get("population")] = populations.get(row.get("population"), 0) + 1
    counts = {name: (len(rows_of(args.push / name)) if name.endswith(".jsonl") else 1)
              for name in PUBLISHED if (args.push / name).is_file()}

    target = "bank-v3/batches/%s" % args.batch
    client.upload_folder(repo_id=repo, repo_type="dataset", folder_path=str(args.push),
                         path_in_repo=target, allow_patterns=list(PUBLISHED),
                         commit_message="bank-v3 batch %s: %d cases (%s)"
                         % (args.batch, len(cases),
                            ", ".join("%s %d" % kv for kv in sorted(populations.items()))))
    print(json.dumps({"repo": repo, "batch": target, "cases": len(cases),
                      "populations": populations, "files": counts}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
