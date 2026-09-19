"""Publish the committed bank to the private dataset repo the pilot reads.

`round02_pilot.sh` takes its banks from `$BANK_PATH` inside `$WORK_REPO`, not
from the checkout: it wants `eval2.jsonl`, `train.jsonl` and any `evidence-*/`
snapshots, and prints `none (authored bank)` when there are none. The bank lives
in git for review and history; this copies it to where the job looks.

It has to happen here rather than on a laptop, because a GitHub Actions secret
is write-only — the token exists only inside the job.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from huggingface_hub import HfApi

BANK = Path("training/data/bank_v2")
REQUIRED = ("train.jsonl", "eval2.jsonl")

#: The ONE destination this script may write. It copies the committed bank, so
#: it may only publish the path that names the committed bank.
#:
#: It used to upload `BANK` to whatever `BANK_PATH` said. While those were the
#: same thing that was an idempotent re-publish; the moment `BANK_PATH` was
#: repointed at a bank cut from the batches by `bank-publish.yml`, bootstrap
#: overwrote that bank with this one — and the round then trained on bank v2
#: under bank v3's name, which cost a five-hour H200 job on 2026-09-19 and read
#: as a result until the numbers came back byte-identical to an older arm.
#:
#: A bank derived from the batches has no business being written from the
#: checkout. If `BANK_PATH` names anything else, that bank was published by
#: something else and this must not touch it.
OWNED_PATH = "banks/" + BANK.name.replace("bank_", "")


def main() -> int:
    # Before the token is read and before anything is asked of the Hub: having
    # nothing to do is not a reason to authenticate.
    bank_path = os.environ.get("BANK_PATH", OWNED_PATH).strip("/")
    if bank_path != OWNED_PATH:
        # Not an error: a round on a carved bank is the normal case now, and
        # this step simply has nothing to do. Saying so beats a silent skip,
        # because the step's name promises a publish.
        print("BANK_PATH is %s, which this script does not own (it publishes %s from %s). "
              "A bank cut from the batches is published by bank-publish.yml; leaving it "
              "alone." % (bank_path, OWNED_PATH, BANK))
        return 0

    token = os.environ["HF_TOKEN"]
    api = HfApi(token=token)
    owner = api.whoami()["name"]
    repo_id = "%s/%s" % (owner, os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))

    for name in REQUIRED:
        p = BANK / name
        if not p.is_file():
            print("not a file: %s — the bank must be committed, not rebuilt here" % p,
                  file=sys.stderr)
            return 2

    counts = {}
    for name in REQUIRED:
        rows = [json.loads(l) for l in (BANK / name).read_text(encoding="utf-8").splitlines() if l]
        fams = {r["family"] for r in rows}
        pops = sorted({r["population"] for r in rows})
        counts[name] = {"cases": len(rows), "families": len(fams), "populations": pops}
        # A bank with one population cannot tell "serves more natively" from
        # "avoids every import", which is the whole point of the controls.
        if len(pops) < 2:
            print("%s carries only %s; both populations are required" % (name, pops),
                  file=sys.stderr)
            return 1

    train_fams = {json.loads(l)["family"]
                  for l in (BANK / "train.jsonl").read_text(encoding="utf-8").splitlines() if l}
    eval_fams = {json.loads(l)["family"]
                 for l in (BANK / "eval2.jsonl").read_text(encoding="utf-8").splitlines() if l}
    overlap = sorted(train_fams & eval_fams)
    if overlap:
        print("families on both sides of the split: %s" % overlap, file=sys.stderr)
        return 1

    print(json.dumps({"repo": repo_id, "bank_path": bank_path, "banks": counts,
                      "family_overlap": overlap}, indent=2))

    info = api.repo_info(repo_id, repo_type="dataset")
    if not info.private:
        print("refusing to upload to a public destination: %s" % repo_id, file=sys.stderr)
        return 2

    api.upload_folder(repo_id=repo_id, repo_type="dataset", folder_path=str(BANK),
                      path_in_repo=bank_path, allow_patterns=list(REQUIRED),
                      commit_message="round-02 bank v2: authored, differential oracle")
    print("uploaded %s to %s/%s" % (", ".join(REQUIRED), repo_id, bank_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
