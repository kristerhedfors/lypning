"""Move a locally generated candidates file to the private work repo, and back.

Generation needs the provider key; adaptation executes model-written code. The
bank-v3 workflow keeps those in two jobs so the key-holder never runs candidate
code, and that split is the safety property, not a convenience. When the
provider refuses the CI runner but answers the developer's own shell, the split
has to survive the move: generation runs locally, adaptation still runs on a
disposable VM with no provider key, and this is the channel between them.

The repository is public, so the candidates never touch it: they carry model
written task text and programs. They go to the private dataset repo instead.

  put  (local, needs HF_TOKEN in the shell)  upload candidates.jsonl
  get  (in CI, needs the HF_TOKEN secret)    download it for synth-adapt
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

#: Candidates are an intermediate, not a bank. They live beside the round
#: artifacts in the work repo rather than in the artifact repo, which holds
#: things a report cites.
WORK_REPO = "lypning-round02-work"
PREFIX = "bank-v3/candidates"


def client(token):
    from huggingface_hub import HfApi

    return HfApi(token=token)


def repo_of(api):
    return "%s/%s" % (api.whoami()["name"], os.environ.get("WORK_REPO_NAME", WORK_REPO))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("put", "get"))
    ap.add_argument("path", type=Path, help="candidates.jsonl to upload, or to write")
    ap.add_argument("--batch", default=os.environ.get("GITHUB_RUN_ID", "local"),
                    help="names the object in the repo; reuse it to overwrite")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    api = client(token)
    repo = repo_of(api)
    target = "%s/%s/candidates.jsonl" % (PREFIX, args.batch)

    if args.action == "put":
        if not args.path.is_file():
            print("no such file: %s" % args.path, file=sys.stderr)
            return 2
        rows = sum(1 for _ in args.path.open(encoding="utf-8"))
        if not rows:
            print("refusing to upload an empty candidates file", file=sys.stderr)
            return 1
        api.upload_file(path_or_fileobj=str(args.path), path_in_repo=target,
                        repo_id=repo, repo_type="dataset",
                        commit_message="bank-v3 candidates %s: %d rows" % (args.batch, rows))
        print("uploaded %d rows to %s %s" % (rows, repo, target))
        return 0

    from huggingface_hub import hf_hub_download

    got = hf_hub_download(repo, target, repo_type="dataset", token=token)
    args.path.parent.mkdir(parents=True, exist_ok=True)
    args.path.write_bytes(Path(got).read_bytes())
    print("fetched %d rows from %s %s"
          % (sum(1 for _ in args.path.open(encoding="utf-8")), repo, target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
