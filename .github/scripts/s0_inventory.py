"""Print, in full, what the private artifact repo holds.

Free and read-only: it lists files and downloads nothing. `hf_status.py` prints
counts, which is the right size for a status line and the wrong size for
deciding whether rung S0 has its inputs. This prints every path, so that
decision is made against the repository rather than against a remembered layout.
"""
from __future__ import annotations

import os
import sys

# The inputs training/START_NEXT_ROUND.md names, and the round that owns each.
# A rung whose input is absent stays blocked; this only reports.
S0_JOB = "6aaa87465527934177ee9f34"
S0_RUN = "eval-20260916-063539"


def main() -> int:
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
