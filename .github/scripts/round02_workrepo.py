"""Ensure the artifact destination exists and is private, before anything runs.

`launch.py` refuses to submit into a repository that exists and is not a private
dataset, and the job checks again before it uploads. Neither of them creates it,
and neither flips visibility on an existing repository — that refusal is the
point, so this script creates a private one and otherwise only reads.
"""
from __future__ import annotations

import os
import sys

from huggingface_hub import HfApi


def main() -> int:
    token = os.environ["HF_TOKEN"]
    api = HfApi(token=token)
    owner = api.whoami()["name"]
    repo_id = "%s/%s" % (owner, os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))

    try:
        info = api.repo_info(repo_id, repo_type="dataset")
    except Exception:                                          # noqa: BLE001
        print("creating %s as a private dataset" % repo_id)
        api.create_repo(repo_id=repo_id, repo_type="dataset", private=True, exist_ok=True)
        info = api.repo_info(repo_id, repo_type="dataset")

    print("%s  private=%s" % (repo_id, info.private))
    if not info.private:
        # Deliberately not a flip: a repository someone made public is a
        # decision this script is not entitled to reverse silently.
        print("refusing: %s is not private. Make it private or choose another "
              "destination; this script will not change visibility." % repo_id,
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
