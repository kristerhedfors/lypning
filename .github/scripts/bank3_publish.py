"""Accumulate bank-v3 batches in the private dataset repo, and seed dedup from it.

Two modes, because growing a bank to five figures takes many runs and an Actions
artifact expires in 14 days (`HARVESTING.md`: "Retention is bounded, not a
promise"). The dataset repo is the durable home.

  --push  upload this run's accepted rows as a new immutable batch directory.
  --seed  write every task text already banked, so the generator can skip them.

This process runs no model-written code, which is why it may hold HF_TOKEN. The
job that executes candidates must not, and does not.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

#: What counts as a banked row, and why `unrepaired.jsonl` is on the list.
#: A refused row whose stratum is `ceiling` is the RIGHT answer — the task needs
#: something the engine cannot serve, so keeping the import and taking the
#: fallback is correct, and `PREREGISTRATION.md` §2 item (g) reserves 27 of 93 pool cases
#: for exactly that. Banking only native+repaired discarded the whole
#: counterweight: the run of 2026-09-18 asked for 105 ceiling task-calls and
#: banked none of them. A refused row whose stratum is `rewrite` is a genuine
#: failure and is still not banked; `ceiling_from_unrepaired` splits them.
ACCEPTED = ("native.jsonl", "repaired.jsonl", "ceiling.jsonl")
SOURCE_OF_CEILING = "unrepaired.jsonl"


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--push", type=Path, help="directory holding this run's outputs")
    ap.add_argument("--seed", type=Path, help="write banked task texts here")
    ap.add_argument("--batch", default=os.environ.get("GITHUB_RUN_ID", "local"))
    args = ap.parse_args()

    client = api()
    repo = repo_id(client)
    info = client.repo_info(repo, repo_type="dataset")
    if not info.private:
        print("refusing: %s is not private" % repo, file=sys.stderr)
        return 2

    if args.seed is not None:
        # Every task already banked, so a later run does not pay to regenerate
        # what it already has. Absence is normal on the first run and is not an
        # error, but it is reported rather than silently treated as empty.
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
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            tasks.add(json.loads(line)["task"])
                        except (ValueError, KeyError):
                            continue
        args.seed.parent.mkdir(parents=True, exist_ok=True)
        args.seed.write_text(
            "".join(json.dumps({"task": t}) + "\n" for t in sorted(tasks)), encoding="utf-8")
        print("seeded %d already-banked task(s) from %s" % (len(tasks), repo))
        return 0

    if args.push is None:
        print("one of --push or --seed is required", file=sys.stderr)
        return 2

    # Split the unrepaired file before counting: a ceiling row that refused is a
    # banked case, a rewrite row that refused is a repair we owe.
    source = args.push / SOURCE_OF_CEILING
    if source.is_file():
        kept, owed = [], 0
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("stratum") == "ceiling":
                kept.append(line)
            else:
                owed += 1
        (args.push / "ceiling.jsonl").write_text(
            "\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        print("ceiling rows kept %d; rewrite rows still owed a repair %d" % (len(kept), owed))

    counts, total = {}, 0
    for name in ACCEPTED:
        path = args.push / name
        rows = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] \
            if path.is_file() else []
        counts[name] = len(rows)
        total += len(rows)
    if not total:
        # A batch of nothing is not a batch. Uploading it would grow the
        # directory count while leaving the bank the size it was.
        print("no accepted rows in %s (%s) — nothing to publish"
              % (args.push, counts), file=sys.stderr)
        return 1

    target = "bank-v3/batches/%s" % args.batch
    client.upload_folder(repo_id=repo, repo_type="dataset", folder_path=str(args.push),
                         path_in_repo=target, allow_patterns=list(ACCEPTED),
                         commit_message="bank-v3 batch %s: %d accepted rows" % (args.batch, total))
    print(json.dumps({"repo": repo, "batch": target, "counts": counts, "total": total},
                     indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
