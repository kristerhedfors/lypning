"""Print what the private artifact repo holds, and read the small files in it.

`hf_status.py` prints counts, which is the right size for a status line and the
wrong size for deciding whether a rung has its inputs or what a finished stage
measured. This prints every path, then the contents of the files small enough to
be evidence, so both decisions are made against the repository rather than
against a remembered layout.

Downloading and printing are different boundaries, and only the second one is
tight. No weights are fetched, but the line-counts loop downloads **every**
`.jsonl` in the repository into the runner — bank rows and case files included —
and returns only the count. What may be *printed* is the `SMALL` tuple below,
and that is the list that keeps the held-out benchmark out of a public log.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "training"))
from pipeline.public_view import public_view  # noqa: E402

# The inputs training/START_NEXT_ROUND.md names, and the round that owns each.
# A rung whose input is absent stays blocked; this only reports.
S0_JOB = "6aaa87465527934177ee9f34"
S0_RUN = "eval-20260916-063539"

# Read whole. This repository is PUBLIC, so an Actions log is world-readable and
# every name here is a publication decision, not a convenience. Aggregates only:
# a manifest, a metric, a seal. `bundle.json` is deliberately ABSENT — it carries
# the cases, and the cases are the held-out benchmark. Printing them publishes
# eval-2 to anyone who scrapes a log, which cannot be undone by deleting the run.
# Never widen this to anything that holds task text, a reference program or an
# expected stdout; count its lines instead, or compute the statistic in the job
# and print only the statistic. What is printed of them passes `public_view`:
# `metrics.json` and `best.json` carry per-case `case_clusters` counts, which
# stay private (operator decision, 2026-09-23).
SMALL = ("job-manifest.json", "metrics.json", "experiment.json",
         "best.json", "config.json", "plan-001.json", "seal.json")


def main() -> int:
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(token=token)
    owner = api.whoami()["name"]

    # A second repository has been asserted to hold the S0c probe rollouts. The
    # assertion is worth one lookup: a rung reported blocked twice for a file
    # that was in the next repository along is the expensive kind of wrong.
    print("== the asserted second home for the S0c probe")
    for name in ("%s/lypning-round02-work" % owner,):
        try:
            hits = [f for f in api.list_repo_files(name, repo_type="dataset")
                    if "probe" in f or "eval2_rows" in f or f.endswith("attempts.jsonl")]
            print("   %s: %s" % (name, hits if hits else "exists, holds none of the S0 inputs"))
        except Exception as exc:                                  # noqa: BLE001
            print("   %s: %s" % (name, type(exc).__name__))

    repo = "%s/%s" % (owner, os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))
    print("\n== %s" % repo)

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
            print(json.dumps(public_view(json.loads(open(path, encoding="utf-8").read())),
                             indent=2, sort_keys=True)[:4000])
        except Exception as exc:                                  # noqa: BLE001
            print("     could not read: %s" % type(exc).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
