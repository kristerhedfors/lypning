"""Fetch a Hugging Face job's own log, which is the side that knows why it died.

WHY THIS EXISTS. `launch.py --follow` streams the job into a GitHub runner, and
a GitHub runner is capped at 360 minutes while a pilot is submitted for 480, so
the follower expires first BY DESIGN. Worse, GitHub will not serve an
in-progress job's log at all. Twice on 2026-09-19 a round's cause of death was
readable only from the Hub and the follower had captured everything except it.

Free and read-only: it asks for one job's log and prints it. It uploads
nothing, runs nothing and touches no bank.

The log is the JOB's stdout, which for `round02_pilot.sh` is stage banners,
counts and tracebacks — never a bank row and never a candidate program, because
the script prints neither. Tail rather than head by default: a failure is at
the end.
"""
from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job", help="the HF job id")
    ap.add_argument("--tail", type=int, default=120,
                    help="last N lines; 0 prints the whole log")
    ap.add_argument("--grep", help="only lines containing this substring")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    try:
        info = api.inspect_job(job_id=args.job)
        stage = getattr(getattr(info, "status", None), "stage", "?")
        message = getattr(getattr(info, "status", None), "message", None)
        print("== job %s  stage=%s%s" % (args.job, stage, "  %s" % message if message else ""))
    except Exception as exc:                                      # noqa: BLE001
        print("could not inspect %s: %s" % (args.job, type(exc).__name__), file=sys.stderr)

    lines = []
    try:
        for entry in api.fetch_job_logs(job_id=args.job):
            text = entry if isinstance(entry, str) else getattr(entry, "data", str(entry))
            lines.extend(str(text).splitlines())
    except Exception as exc:                                      # noqa: BLE001
        print("could not fetch logs for %s: %s" % (args.job, type(exc).__name__), file=sys.stderr)
        return 1

    if args.grep:
        lines = [line for line in lines if args.grep in line]
    if args.tail:
        lines = lines[-args.tail:]
    print("== %d line(s)" % len(lines))
    for line in lines:
        print(line)
    # An empty log is a failed read, not a job that said nothing.
    return 0 if lines else 1


if __name__ == "__main__":
    sys.exit(main())
