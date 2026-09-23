"""Report Hugging Face job outcomes and what the artifact repo now holds.

The GitHub runner caps a job at its `timeout-minutes`, and `--follow` dies with
it. The HF job does not: it keeps running and still uploads its round directory.
So a cancelled workflow says nothing about the round, and this reads the side
that actually knows.

Free and read-only: it lists jobs and repo files and downloads nothing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "training"))
from pipeline.public_view import public_view  # noqa: E402


def main() -> int:
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    owner = api.whoami()["name"]
    repo = "%s/%s" % (owner, os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))

    print("== recent jobs")
    try:
        jobs = list(api.list_jobs())
    except Exception as exc:                                      # noqa: BLE001
        print("list_jobs failed: %s" % type(exc).__name__, file=sys.stderr)
        jobs = []
    for job in jobs[:12]:
        stage = getattr(getattr(job, "status", None), "stage", None)
        message = getattr(getattr(job, "status", None), "message", None)
        print("  %-26s %-10s %-10s %s"
              % (getattr(job, "id", "?"), getattr(job, "created_at", "") or "",
                 stage or "?", (message or "")[:70]))

    print("\n== %s" % repo)
    try:
        files = sorted(api.list_repo_files(repo, repo_type="dataset"))
    except Exception as exc:                                      # noqa: BLE001
        print("list_repo_files failed: %s" % type(exc).__name__, file=sys.stderr)
        return 1
    rounds, banks, other = [], [], []
    for f in files:
        (rounds if f.startswith("round-02/")
         else banks if f.startswith("bank-v3/") else other).append(f)
    print("  %d file(s): %d round artifacts, %d bank-v3, %d other"
          % (len(files), len(rounds), len(banks), len(other)))

    # The manifest is what a round writes last, on success or failure alike, so
    # it is the one file that says how the round ended.
    manifests = [f for f in rounds if f.endswith("manifest.json")]
    for m in manifests[-4:]:
        print("\n  -- %s" % m)
        try:
            from huggingface_hub import hf_hub_download

            path = hf_hub_download(repo, m, repo_type="dataset", token=token)
            data = public_view(json.loads(open(path, encoding="utf-8").read()))
            for key in ("status", "last_stage", "exit_code", "flavor", "steps",
                        "eval_draws", "seed", "pilot_bundle_digest",
                        "eval2_bundle_digest", "grpo_skipped"):
                if key in data:
                    print("     %-22s %s" % (key, data[key]))
        except Exception as exc:                                  # noqa: BLE001
            print("     could not read: %s" % type(exc).__name__)
    if not manifests:
        print("  no round manifest yet — the job has not reached its finish trap")
    batches = sorted({f.split("/")[2] for f in banks if f.count("/") >= 2})
    if batches:
        print("\n  bank-v3 batches: %s" % ", ".join(batches))
    return 0


if __name__ == "__main__":
    sys.exit(main())
