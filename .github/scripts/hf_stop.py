"""Cancel Hugging Face jobs that are still running, by id or all of them.

WHY THIS EXISTS. A trainer that dies does not take its pool with it. On
2026-09-19 the round-02 trainer hit its wall clock and three `cpu-basic`
sandbox hosts plus a second job kept running with nothing left to serve. They
are cheap, which is exactly why nobody notices them, and a pool host left
running is also a host a later round may find busy.

Cancelling is not free of consequence, so it is not the default shape: ids are
named explicitly, `--all-running` needs saying, and every job is printed with
its age before anything is asked of it. A job that is already finished is
reported and skipped rather than treated as an error.

Free and read-mostly: it lists jobs and cancels the ones named. It uploads
nothing and reads no bank.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

#: Stages that are still consuming a machine.
LIVE = ("RUNNING", "UPDATING")


def age(created) -> str:
    if not created:
        return "?"
    now = dt.datetime.now(dt.timezone.utc)
    if getattr(created, "tzinfo", None) is None:
        return "?"
    minutes = int((now - created).total_seconds() // 60)
    return "%dh%02dm" % (minutes // 60, minutes % 60)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--job", action="append", default=[], help="job id to cancel (repeatable)")
    ap.add_argument("--all-running", action="store_true",
                    help="cancel every RUNNING job; say it deliberately")
    ap.add_argument("--keep", action="append", default=[],
                    help="job id to leave alone even under --all-running (repeatable)")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    jobs = list(api.list_jobs())
    live = [j for j in jobs
            if str(getattr(getattr(j, "status", None), "stage", "")) in LIVE]
    print("== %d job(s) listed, %d still live" % (len(jobs), len(live)))
    for job in live:
        print("   %-26s %-8s created %s" % (getattr(job, "id", "?"), age(getattr(job, "created_at", None)),
                                            getattr(job, "created_at", "")))

    keep = set(args.keep)
    if args.all_running:
        targets = [getattr(j, "id", "") for j in live if getattr(j, "id", "") not in keep]
    else:
        targets = [j for j in args.job if j not in keep]
    if not targets:
        # Nothing to do is a clean outcome, not a usage error: a rerun after a
        # successful stop should be quiet rather than red.
        print("no job to cancel")
        return 0

    live_ids = {getattr(j, "id", "") for j in live}
    failed = []
    for job_id in targets:
        if job_id not in live_ids:
            print("   %s is not running; skipping" % job_id)
            continue
        try:
            api.cancel_job(job_id=job_id)
            print("   cancelled %s" % job_id)
        except Exception as exc:                                  # noqa: BLE001
            print("   FAILED to cancel %s: %s" % (job_id, type(exc).__name__), file=sys.stderr)
            failed.append(job_id)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
