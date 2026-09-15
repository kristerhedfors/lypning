"""Submit a round-02 stage to Hugging Face Jobs and follow it. Runs on the operator's machine.

The job image is the SAME CPython base digest as the verifier Space, so the
identity handshake's `sys.version` matches by construction. The job clones the
repository at one commit, then runs the stage script from that clone; nothing
private is baked into an image. The HF token travels as a job secret, held by
the trainer only; candidates run in pooled sandboxes on another VM without it.

Cost is bounded by the flavor and the timeout, and both are printed before
submission. This never launches without an explicit --yes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

#: One digest for the trainer job and the verifier image. Change both together.
BASE_IMAGE = "python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"
REPO_URL = "https://github.com/kristerhedfors/lypning"
STAGES = {"smoke": "nemotron/hf/round02_smoke.sh"}


def bootstrap(stage, branch, commit):
    return ("set -euo pipefail; apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null; "
            "git clone -q --branch %s %s /work/lypning && cd /work/lypning && git checkout -q %s && "
            "bash %s" % (branch, REPO_URL, commit, STAGES[stage]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=sorted(STAGES))
    p.add_argument("--branch", required=True)
    p.add_argument("--commit", required=True, help="40-character commit on --branch")
    p.add_argument("--space", required=True, help="verifier Space, owner/name")
    p.add_argument("--space-revision", required=True, help="the Space's 40-character commit")
    p.add_argument("--qwen-revision", required=True, help="approved immutable Qwen3.8-27B commit")
    p.add_argument("--work-repo", required=True, help="private dataset repo for artifacts")
    p.add_argument("--flavor", default="a10g-small")
    p.add_argument("--timeout", default="75m")
    p.add_argument("--yes", action="store_true", help="actually submit (billed)")
    p.add_argument("--follow", action="store_true", help="stream logs until the job ends")
    args = p.parse_args(argv)
    for name, value in (("commit", args.commit), ("space revision", args.space_revision), ("Qwen revision", args.qwen_revision)):
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            print("%s must be a 40-character commit" % name, file=sys.stderr)
            return 2
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    hardware = {h.name: h for h in api.list_jobs_hardware()}
    if args.flavor not in hardware:
        print("unknown flavor %s" % args.flavor, file=sys.stderr)
        return 2
    plan = {"stage": args.stage, "image": BASE_IMAGE, "flavor": args.flavor, "timeout": args.timeout,
            "hourly_usd": round(hardware[args.flavor].unit_cost_usd * (60 if hardware[args.flavor].unit_label == "minute" else 1), 2),
            "branch": args.branch, "commit": args.commit, "space": args.space, "space_revision": args.space_revision,
            "qwen_revision": args.qwen_revision, "work_repo": args.work_repo}
    print(json.dumps(plan, indent=2))
    if not args.yes:
        print("dry run: pass --yes to submit", file=sys.stderr)
        return 0
    api.create_repo(args.work_repo, repo_type="dataset", private=True, exist_ok=True)
    job = api.run_job(
        image=BASE_IMAGE, command=["bash", "-c", bootstrap(args.stage, args.branch, args.commit)],
        env={"SPACE_REPO": args.space, "SPACE_REV": args.space_revision, "QWEN_REV": args.qwen_revision,
             "WORK_REPO": args.work_repo},
        secrets={"HF_TOKEN": token}, flavor=args.flavor, timeout=args.timeout,
        name="lypning-round02-" + args.stage, labels={"lypning-round": "02", "lypning-stage": args.stage})
    print(json.dumps({"job_id": job.id, "url": job.url}))
    if args.follow:
        for line in api.fetch_job_logs(job_id=job.id, follow=True):
            print(line)
        final = api.inspect_job(job_id=job.id)
        print(json.dumps({"job_id": job.id, "stage": final.status.stage, "message": final.status.message}))
        return 0 if final.status.stage == "COMPLETED" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
