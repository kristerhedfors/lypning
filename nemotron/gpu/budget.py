"""The spend guard. It runs on the box, it is not part of the trainer, and it wins.

WHY THIS IS A SEPARATE PROCESS. Eight H100s cost about $29.52/hour on spot —
$708 a day. The single most expensive failure mode in this whole project is not
a bad hyperparameter, it is an instance nobody turned off. Every control below
assumes the one above it has already failed:

  0. GCP `--max-run-duration` + `--instance-termination-action=DELETE`.
     Enforced by the control plane. Works even if the guest never boots.
  1. `shutdown -h +N` issued by the startup script BEFORE any work begins.
     Enforced by the kernel. Works if the driver crashes, hangs or is killed.
  2. This daemon: wall-clock spend against the cap, every 30 s, plus a
     dead-man's switch on the driver's heartbeat. Works if the driver is alive
     but wedged — an NCCL deadlock burns the same $29.52/hour as training does.
  3. The driver's own per-phase budgets, which skip a phase rather than eat the
     whole run.
  4. A cumulative ledger in GCS, checked before launch, so that "just one more
     try" cannot become $500 across runs.

Layers 0 and 1 are the ones that matter, because they are the only two that do
not depend on any code of mine still working.

This module is deliberately dependency-free and runs under the system python on
a bare GCP image.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

METADATA = "http://metadata.google.internal/computeMetadata/v1"
HEADERS = {"Metadata-Flavor": "Google"}


def _meta(path: str, default: str = "") -> str:
    try:
        req = urllib.request.Request(METADATA + path, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.read().decode().strip()
    except Exception:
        return default


def preempted() -> bool:
    """Spot preemption gives ~30 s. Polled every second; nothing else is fast enough."""
    return _meta("/instance/preempted", "FALSE").upper() == "TRUE"


def halt(reason: str, state_dir: Path) -> None:
    (state_dir / "HALT").write_text(reason + "\n", encoding="utf-8")
    sys.stderr.write("HALT: %s\n" % reason)
    sys.stderr.flush()
    # Best effort: let the driver flush a checkpoint, then go down regardless.
    for cmd in (["/usr/local/bin/ntx-flush"], ["shutdown", "-h", "now"]):
        try:
            subprocess.run(cmd, timeout=120)
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="spend guard; shuts the box down")
    ap.add_argument("--rate-per-hour", type=float, required=True)
    ap.add_argument("--cap-usd", type=float, required=True)
    ap.add_argument("--state-dir", default="/var/run/ntx")
    ap.add_argument("--heartbeat-timeout-s", type=float, default=900.0)
    ap.add_argument("--poll-s", type=float, default=1.0)
    ap.add_argument("--report-every-s", type=float, default=30.0)
    args = ap.parse_args()

    state = Path(args.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    started = time.time()
    hb = state / "heartbeat"
    last_report = 0.0

    while True:
        now = time.time()
        elapsed_h = (now - started) / 3600.0
        spend = elapsed_h * args.rate_per_hour

        if preempted():
            halt("spot preemption signalled", state)
            return 0
        if spend >= args.cap_usd:
            halt("spend cap reached: $%.2f >= $%.2f after %.1f min"
                 % (spend, args.cap_usd, (now - started) / 60), state)
            return 0
        # Dead-man's switch: a wedged trainer costs exactly as much as a working
        # one. Absent heartbeat file is tolerated for the first timeout window so
        # a slow model download does not trip it.
        age = (now - hb.stat().st_mtime) if hb.exists() else (now - started)
        if age > args.heartbeat_timeout_s:
            halt("no driver heartbeat for %.0f s (wedged?)" % age, state)
            return 0

        if now - last_report >= args.report_every_s:
            (state / "spend.json").write_text(json.dumps({
                "elapsed_s": round(now - started, 1),
                "spend_usd": round(spend, 3),
                "cap_usd": args.cap_usd,
                "rate_per_hour": args.rate_per_hour,
                "remaining_usd": round(args.cap_usd - spend, 3),
                "remaining_min": round((args.cap_usd - spend) / args.rate_per_hour * 60, 1),
                "heartbeat_age_s": round(age, 1),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }, indent=2) + "\n", encoding="utf-8")
            last_report = now
        time.sleep(args.poll_s)


if __name__ == "__main__":
    sys.exit(main())
