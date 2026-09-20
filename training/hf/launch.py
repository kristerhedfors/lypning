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
STAGES = {"smoke": "training/hf/round02_smoke.sh", "pilot": "training/hf/round02_pilot.sh"}
#: The stages that read reviewed banks from the private dataset repo (--bank-path).
BANKED = ("pilot",)
DEFAULT_STEPS, DEFAULT_GRPO_STEPS, DEFAULT_EVAL_DRAWS, DEFAULT_SEED = 250, 20, 16, 1111
# 12 scorers, not 16: the default pool is 4 x 4 = 16 slots, and 16 scorers in
# 16 slots is the exact-fit shape that has no room for a sandbox winding down.
DEFAULT_EVAL_SEQUENCES, DEFAULT_SCORE_WORKERS = 128, 12
DEFAULT_POOL_SANDBOXES_PER_HOST, DEFAULT_POOL_MAX_HOSTS = 4, 4
#: The density ceiling a banked launch may not exceed, in sandboxes on one host
#: of this flavor (`pipeline/hf_sandbox_runner.FLAVOR`). `native` is a
#: host-load-dependent endpoint — the same property that keeps the native-timeout
#: abort — so sandboxes per host is an instrument parameter, and two arms scored
#: at different densities are not comparable; `pipeline/training_contract.py`
#: makes that argument for k already. The number is four *at* `cpu-basic` and
#: nowhere else: a different pool flavor is a different host, which voids it and
#: has to be decided again rather than carried over.
#:
#: HOSTS ARE NOT DENSITY, and only one of the two is the instrument. Sandboxes
#: per host stays at four because that is what `native` is sensitive to: a
#: contended host makes a served program time out and be scored non-native, so
#: raising it changes what the label MEANS. The host count is a cost ceiling
#: (`--pool-max-hosts` says so), and `cpu-basic` hosts are cents beside an h200
#: at $5/h. Raised 4 -> 16 on 2026-09-20 to buy throughput without touching the
#: endpoint: preparation ran at ~4 s/case through 16 scorers and is the stage
#: that ended two rounds at the wall.
POOL_FLAVOR, MAX_POOL_SANDBOXES_PER_HOST, MAX_POOL_HOSTS = "cpu-basic", 4, 16
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
                    "GRPO_STEPS": str(args.grpo_steps),
                    "EVAL_DRAWS": str(args.eval_draws), "SEED": str(args.seed),
                    "EVAL_SEQUENCES": str(args.eval_sequences), "SCORE_WORKERS": str(args.score_workers),
                    "NTX_POOL_SANDBOXES_PER_HOST": str(args.pool_sandboxes_per_host),
                    "NTX_POOL_MAX_HOSTS": str(args.pool_max_hosts),
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
    p.add_argument("--steps", type=int, default=DEFAULT_STEPS, help="pilot: SFT optimizer steps")
    p.add_argument("--grpo-steps", type=int, default=DEFAULT_GRPO_STEPS,
                   help="pilot: GRPO optimizer steps (default: 20)")
    p.add_argument("--eval-draws", type=int, default=DEFAULT_EVAL_DRAWS, help="pilot: draws per case on the eval-2 benchmark")
    p.add_argument("--eval-sequences", type=int, default=DEFAULT_EVAL_SEQUENCES,
                   help="pilot: sequences per generate call in evaluation")
    p.add_argument("--score-workers", type=int, default=DEFAULT_SCORE_WORKERS,
                   help="pilot: concurrent verifier scorings")
    p.add_argument("--pool-sandboxes-per-host", type=int,
                   default=DEFAULT_POOL_SANDBOXES_PER_HOST,
                   help="pilot: verifier concurrency per CPU host (default and maximum: 4)")
    p.add_argument("--pool-max-hosts", type=int, default=DEFAULT_POOL_MAX_HOSTS,
                   help="pilot: verifier CPU-host cost ceiling (default 4, maximum 16)")
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
    if min(args.steps, args.grpo_steps, args.eval_draws, args.eval_sequences, args.score_workers,
           args.pool_sandboxes_per_host, args.pool_max_hosts) <= 0:
        print("training, evaluation and pool limits must be positive", file=sys.stderr)
        return 2
    # Capacity must cover the scorers, and a MULTI-HOST pool must additionally
    # keep one host of slack. The pool RAISES rather than waits once every host
    # is full, and a round's stages are separate processes that adopt the
    # previous stage's warm hosts at their true occupancy -- so scorers exactly
    # equal to capacity dies the moment one sandbox is still winding down. That
    # is how 6ab01391 was lost on 2026-09-20, at its second stage, after the
    # pilot bundle was already built. A single-host pool has no such packing and
    # is the smoke/test shape, so the slack would only refuse legal work.
    capacity = args.pool_sandboxes_per_host * args.pool_max_hosts
    slack = args.pool_sandboxes_per_host if args.pool_max_hosts > 1 else 0
    if capacity < args.score_workers + slack:
        # Name the knob that still has room, not the knob that matches the stage.
        # Both knobs are capped for a banked stage, so "increase --pool-max-hosts"
        # was unfollowable whenever hosts were already at the ceiling — the same
        # two-refusal dead end the density fix removed, moved one knob out — and
        # above their product no knob reaches at all, which is a number the
        # operator has to be told rather than left to find by bisection.
        room = []
        if args.stage not in BANKED or args.pool_sandboxes_per_host < MAX_POOL_SANDBOXES_PER_HOST:
            room.append("--pool-sandboxes-per-host")
        if args.stage not in BANKED or args.pool_max_hosts < MAX_POOL_HOSTS:
            room.append("--pool-max-hosts")
        if room:
            print("pool capacity must cover --score-workers: increase %s"
                  % " or ".join(room), file=sys.stderr)
        else:
            print("a banked launch tops out at %d scorers (%d per host x %d hosts at %s, "
                  "less one host of slack); lower --score-workers"
                  % (MAX_POOL_SANDBOXES_PER_HOST * (MAX_POOL_HOSTS - 1),
                     MAX_POOL_SANDBOXES_PER_HOST, MAX_POOL_HOSTS, POOL_FLAVOR),
                  file=sys.stderr)
        return 2
    # The check above is a product, and a product is blind to density: sixteen
    # sandboxes on one host clears it, and that is the shape round-02 actually
    # ran. The two together force the scorers across hosts, which is the shape
    # that was decided; separately, neither does. Only a banked stage carries the
    # pool knobs into the job (`job_env`), so a smoke is unaffected by
    # construction, and there is deliberately no floor on --score-workers, on
    # --pool-max-hosts or on total capacity: no eval-2 arm has ever completed, so
    # a throughput threshold would be set against a forward estimate, and low
    # concurrency is the safe direction for a load-dependent endpoint. This binds
    # the launcher only — the job reads NTX_POOL_SANDBOXES_PER_HOST from its
    # environment (`pipeline/hf_sandbox_runner.py`), which checks positivity and
    # nothing else, and an absent knob is legal there and must stay legal.
    if args.stage in BANKED and args.pool_sandboxes_per_host > MAX_POOL_SANDBOXES_PER_HOST:
        print("--pool-sandboxes-per-host must not exceed %d at %s: per-host density is part of the "
              "instrument, so spread the scorers with --pool-max-hosts instead"
              % (MAX_POOL_SANDBOXES_PER_HOST, POOL_FLAVOR), file=sys.stderr)
        return 2
    # And the cost envelope is bounded in the other direction for the same
    # reason the density is: both ceilings are conditioned on a banked stage,
    # because only a banked stage carries these knobs into the job at all, so
    # refusing them on a smoke would be refusing a value that does nothing.
    if args.stage in BANKED and args.pool_max_hosts > MAX_POOL_HOSTS:
        print("pool cost ceiling is %d CPU hosts" % MAX_POOL_HOSTS, file=sys.stderr)
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
        plan.update({"bank_path": args.bank_path, "steps": args.steps,
                     "grpo_steps": args.grpo_steps,
                     "eval_draws": args.eval_draws, "eval_sequences": args.eval_sequences,
                     "score_workers": args.score_workers,
                     "pool_sandboxes_per_host": args.pool_sandboxes_per_host,
                     "pool_max_hosts": args.pool_max_hosts, "seed": args.seed})
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
