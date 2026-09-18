"""Print what the private artifact repo holds, and read the small files in it.

Free and read-only: it lists files and downloads only JSON manifests and metric
files, never weights. `hf_status.py` prints counts, which is the right size for
a status line and the wrong size for deciding whether a rung has its inputs or
what a finished stage measured. This prints every path, then the contents of
the files small enough to be evidence, so both decisions are made against the
repository rather than against a remembered layout.
"""
from __future__ import annotations

import json
import os
import sys

# The inputs training/START_NEXT_ROUND.md names, and the round that owns each.
# A rung whose input is absent stays blocked; this only reports.
S0_JOB = "6aaa87465527934177ee9f34"
S0_RUN = "eval-20260916-063539"

# Read whole. Anything not matching stays a path in the listing: weights and
# rollout logs are evidence too, but not evidence that fits in a CI log.
SMALL = ("job-manifest.json", "metrics.json", "experiment.json", "bundle.json",
         "review.json", "best.json", "config.json", "plan-001.json", "seal.json")


def main() -> int:
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(token=token)
    owner = api.whoami()["name"]
    repo = "%s/%s" % (owner, os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))
    print("== %s" % repo)

    files = sorted(api.list_repo_files(repo, repo_type="dataset"))
    print("   %d file(s)" % len(files))
    for f in files:
        print("   %s" % f)

    print("\n== S0 inputs, by the names START_NEXT_ROUND.md uses")
    wanted = (
        ("probe rollouts (S0c)", [f for f in files if S0_JOB in f and "probe" in f]),
        ("eval2 rows (S0a/S0b)", [f for f in files if "eval2_rows" in f or S0_RUN in f]),
        ("attempts (preflight)", [f for f in files if f.endswith("attempts.jsonl")]),
        ("pinned lypning-l (S0b)", [f for f in files if f.endswith("lypning-l")]),
    )
    for name, hits in wanted:
        print("   %-24s %s" % (name, hits if hits else "ABSENT"))

    # The rows a stage wrote are the only record of what it measured. Counting
    # them costs one download each and settles "did the arm complete" without
    # reasoning about it.
    print("\n== line counts")
    for f in files:
        if not f.endswith(".jsonl") or f.endswith("adapter_model.safetensors"):
            continue
        try:
            path = hf_hub_download(repo, f, repo_type="dataset", token=token)
            with open(path, encoding="utf-8") as fh:
                print("   %-72s %d" % (f, sum(1 for _ in fh)))
        except Exception as exc:                                  # noqa: BLE001
            print("   %-72s unreadable: %s" % (f, type(exc).__name__))

    print("\n== small files, whole")
    for f in files:
        if not f.endswith(SMALL):
            continue
        print("\n  -- %s" % f)
        try:
            path = hf_hub_download(repo, f, repo_type="dataset", token=token)
            print(json.dumps(json.loads(open(path, encoding="utf-8").read()),
                             indent=2, sort_keys=True)[:4000])
        except Exception as exc:                                  # noqa: BLE001
            print("     could not read: %s" % type(exc).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
