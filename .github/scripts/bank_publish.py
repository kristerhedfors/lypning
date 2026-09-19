"""Publish a carved bank as `banks/<name>/{train,eval2}.jsonl`, or refuse.

`bank3_publish.py --push` banks one immutable BATCH, which is the record of
what a run judged. This publishes the thing a ROUND reads: the union of every
batch, carved into a pilot and a benchmark that share no family, under the
`--bank-path` layout `training/hf/launch.py` expects.

The two are different objects and the distinction is load-bearing. A batch is
append-only evidence and is never rewritten; a bank is derived from every batch
that exists when it is cut, so it is dated, named and replaced rather than
edited. Overwriting `banks/v3` under a round that is running would change the
bank out from under it, so a name that already exists is refused unless
`--replace` says otherwise.

Nothing is uploaded unless `bank_carve` admits both halves — the pilot at every
protocol seed. Writing a bank a round cannot use moves the failure onto a
metered job, which is the whole shape this tree keeps removing.

Prints counts, families and digests. No task text, no program, no expected
output: the repository is public and an Actions log is world-readable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path[:0] = ["src", "training"]

from pipeline import bank_carve                                    # noqa: E402
from pipeline.jsonio import sha256_of                              # noqa: E402
from pipeline.training_types import TrainingError                  # noqa: E402

#: What `launch.py --bank-path` expects to find in the directory.
TRAIN, EVAL2 = "train.jsonl", "eval2.jsonl"

#: Written beside them, and the reason a round can trust what it downloaded.
#: A bank is a path in a shared repository, and on 2026-09-19 another job
#: uploaded a different bank over this one: the round read 1,689 cases where
#: 8,370 were published, trained on the wrong population and reported a result.
#: Nothing in the bank said which bank it was. This does — and because a
#: foreign writer overwrites the JSONL without knowing to update the manifest,
#: the mismatch is what a round refuses on.
MANIFEST = "bank.json"


def rows_of(path: Path):
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write(path: Path, rows) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    path.write_text(body, encoding="utf-8")
    return sha256_of(body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("union", type=Path, help="the normalised union of every batch")
    ap.add_argument("--name", required=True, help="bank name, e.g. v3-20260919")
    ap.add_argument("--out", type=Path, required=True, help="staging dir, RUNNER_TEMP only")
    ap.add_argument("--seed", type=int, default=1111, help="which families the benchmark takes")
    ap.add_argument("--benchmark-families", type=int)
    ap.add_argument("--max-cases-per-family", type=int,
                    help="trim each family to at most this many cases; family COUNT is "
                         "untouched, and preparation costs about four seconds a case")
    ap.add_argument("--push", action="store_true", help="upload; otherwise stage and report only")
    ap.add_argument("--replace", action="store_true",
                    help="allow overwriting a bank name that already exists")
    args = ap.parse_args()

    if not args.union.is_file():
        print("not a file: %s" % args.union, file=sys.stderr)
        return 2
    cases = rows_of(args.union)
    if not cases:
        print("no cases in %s" % args.union, file=sys.stderr)
        return 2

    try:
        carved = bank_carve.carve(cases, seed=args.seed,
                                  benchmark_families=args.benchmark_families,
                                  max_cases_per_family=args.max_cases_per_family)
    except TrainingError as exc:
        print("bank-publish: %s" % exc, file=sys.stderr)
        return 1
    print(bank_carve.render(carved))
    if carved["problems"]:
        return 1

    stage = args.out / args.name
    digests = {TRAIN: write(stage / TRAIN, carved["pilot"]),
               EVAL2: write(stage / EVAL2, carved["benchmark"])}
    summary = {
        "bank": args.name, "seed": args.seed,
        "max_cases_per_family": args.max_cases_per_family,
        "train": {"cases": len(carved["pilot"]), "families": len(carved["plan"]["pilot"]),
                  "sha256": digests[TRAIN], **carved["plan"]["pilot_counts"]},
        "eval2": {"cases": len(carved["benchmark"]), "families": len(carved["plan"]["benchmark"]),
                  "sha256": digests[EVAL2], **carved["plan"]["benchmark_counts"]},
    }
    (stage / MANIFEST).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                  encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not args.push:
        print("staged only; pass --push to upload")
        return 0

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    repo = "%s/%s" % (api.whoami()["name"],
                      os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))
    info = api.repo_info(repo, repo_type="dataset")
    if not info.private:
        print("refusing: %s is not private" % repo, file=sys.stderr)
        return 2
    target = "banks/%s" % args.name
    existing = [f for f in api.list_repo_files(repo, repo_type="dataset")
                if f.startswith(target + "/")]
    if existing and not args.replace:
        # A round reads a bank by name. Replacing one while a round is running
        # would change the bank under it, and the round's own manifest would
        # still name it.
        print("refusing: %s already exists (%d file(s)); a bank is dated and replaced, "
              "not edited. Pass --replace only if no round is reading it."
              % (target, len(existing)), file=sys.stderr)
        return 1
    api.upload_folder(repo_id=repo, repo_type="dataset", folder_path=str(stage),
                      path_in_repo=target, allow_patterns=[TRAIN, EVAL2, MANIFEST],
                      commit_message="bank %s: %d pilot / %d benchmark cases, no shared family"
                      % (args.name, len(carved["pilot"]), len(carved["benchmark"])))
    print("pushed %s to %s" % (target, repo))
    print("set BANK_PATH to: %s" % target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
