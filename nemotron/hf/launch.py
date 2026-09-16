"""Submit a round-02 stage to Hugging Face Jobs and follow it. Runs on the operator's machine.

Two stages: `smoke` (round02_smoke.sh, the plumbing on the starter) and `pilot`
(round02_pilot.sh, the first real round on reviewed banks). A pilot names the
bank with --bank-path, a directory in the private --work-repo that holds
eval2.jsonl, train.jsonl and the evidence-*/ snapshots they cite; --steps,
--eval-draws and --seed travel to the job as environment, as the bank path does.

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
import shlex
import sys
import time

#: One digest for the trainer job and the verifier image. Change both together.
BASE_IMAGE = "python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"
REPO_URL = "https://github.com/kristerhedfors/lypning"
STAGES = {"smoke": "nemotron/hf/round02_smoke.sh", "pilot": "nemotron/hf/round02_pilot.sh"}
#: The stages that read reviewed banks from the private dataset repo (--bank-path).
BANKED = ("pilot",)
DEFAULT_STEPS, DEFAULT_EVAL_DRAWS, DEFAULT_SEED = 20, 16, 1111
DEFAULT_EVAL_SEQUENCES, DEFAULT_SCORE_WORKERS = 128, 16
TERMINAL = ("COMPLETED", "ERROR", "CANCELED")


def bootstrap(stage, branch, commit):
    """The job's shell line; every operator-supplied word is quoted, never interpolated."""
    return ("set -euo pipefail; apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null; "
            "git clone -q --branch %s %s /work/lypning && cd /work/lypning && git checkout -q %s && "
            "bash %s" % (shlex.quote(branch), shlex.quote(REPO_URL), shlex.quote(commit), shlex.quote(STAGES[stage])))


def job_env(args):
    """The job's environment. The smoke keys are fixed; a banked stage adds the bank and its knobs."""
    env = {"SPACE_REPO": args.space, "SPACE_REV": args.space_revision, "QWEN_REV": args.qwen_revision,
           "WORK_REPO": args.work_repo}
    if args.stage in BANKED:
        env.update({"BANK_PATH": args.bank_path, "STEPS": str(args.steps),
                    "EVAL_DRAWS": str(args.eval_draws), "SEED": str(args.seed),
                    "EVAL_SEQUENCES": str(args.eval_sequences), "SCORE_WORKERS": str(args.score_workers),
                    "BUNDLES_FROM": args.bundles_from or ""})
    return env


def private_dataset(api, repo_id):
    """The artifact destination must be private BEFORE anything is submitted.

    `create_repo(private=True, exist_ok=True)` neither checks nor changes an
    existing repository's visibility, so an existing public repository would
    receive the round's artifacts. Refuse it; never flip visibility silently.
    Returns True when the repository exists (or was created) private.
    """
    if not api.repo_exists(repo_id, repo_type="dataset"):
        api.create_repo(repo_id, repo_type="dataset", private=True)
        return True
    return getattr(api.repo_info(repo_id, repo_type="dataset"), "private", None) is True


def final_status(api, job_id, wait_s=120, sleep=time.sleep):
    """The log stream closes a beat before the Hub flips the status; wait for a terminal stage."""
    deadline = time.monotonic() + wait_s
    while True:
        job = api.inspect_job(job_id=job_id)
        if job.status.stage in TERMINAL or time.monotonic() >= deadline:
            return job.status
        sleep(3)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=sorted(STAGES))
    p.add_argument("--branch", required=True)
    p.add_argument("--commit", required=True, help="40-character commit on --branch")
    p.add_argument("--space", required=True, help="verifier Space, owner/name")
    p.add_argument("--space-revision", required=True, help="the Space's 40-character commit")
    p.add_argument("--qwen-revision", required=True, help="approved immutable Qwen3.8-27B commit")
    p.add_argument("--work-repo", required=True, help="private dataset repo for artifacts")
    p.add_argument("--bank-path", help="pilot: directory in --work-repo holding eval2.jsonl, train.jsonl, evidence-*/")
    p.add_argument("--steps", type=int, default=DEFAULT_STEPS, help="pilot: SFT and GRPO optimizer steps")
    p.add_argument("--eval-draws", type=int, default=DEFAULT_EVAL_DRAWS, help="pilot: draws per case on the eval-2 benchmark")
    p.add_argument("--eval-sequences", type=int, default=DEFAULT_EVAL_SEQUENCES,
                   help="pilot: sequences per generate call in evaluation")
    p.add_argument("--score-workers", type=int, default=DEFAULT_SCORE_WORKERS,
                   help="pilot: concurrent verifier scorings")
    p.add_argument("--bundles-from", default="",
                   help="pilot: reuse the pilot/ and eval2/ bundles under this directory of --work-repo")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="pilot: review, preparation and training seed")
    p.add_argument("--flavor", default="a10g-small")
    p.add_argument("--timeout", default="75m")
    p.add_argument("--yes", action="store_true", help="actually submit (billed)")
    p.add_argument("--follow", action="store_true", help="stream logs until the job ends")
    args = p.parse_args(argv)
    for name, value in (("commit", args.commit), ("space revision", args.space_revision), ("Qwen revision", args.qwen_revision)):
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            print("%s must be a 40-character commit" % name, file=sys.stderr)
            return 2
    if args.stage in BANKED and not (args.bank_path or "").strip("/"):
        print("%s needs --bank-path: a directory in --work-repo holding eval2.jsonl, train.jsonl and evidence-*/"
              % args.stage, file=sys.stderr)
        return 2
    if min(args.steps, args.eval_draws) <= 0:
        print("--steps and --eval-draws must be positive", file=sys.stderr)
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
    if args.stage in BANKED:
        plan.update({"bank_path": args.bank_path, "steps": args.steps, "eval_draws": args.eval_draws, "seed": args.seed})
    print(json.dumps(plan, indent=2))
    if not args.yes:
        print("dry run: pass --yes to submit", file=sys.stderr)
        return 0
    if not private_dataset(api, args.work_repo):
        print("refusing to submit: %s exists and is not a private dataset repository" % args.work_repo, file=sys.stderr)
        return 2
    job = api.run_job(
        image=BASE_IMAGE, command=["bash", "-c", bootstrap(args.stage, args.branch, args.commit)],
        env=job_env(args),
        secrets={"HF_TOKEN": token}, flavor=args.flavor, timeout=args.timeout,
        name="lypning-round02-" + args.stage, labels={"lypning-round": "02", "lypning-stage": args.stage})
    print(json.dumps({"job_id": job.id, "url": job.url}))
    if args.follow:
        for line in api.fetch_job_logs(job_id=job.id, follow=True):
            print(line)
        status = final_status(api, job.id)
        print(json.dumps({"job_id": job.id, "stage": status.stage, "message": status.message}))
        return 0 if status.stage == "COMPLETED" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
