"""Read one job's timeout and timestamps; never print environment, secrets or logs."""
from __future__ import annotations

import json
import os
import re
import sys
from urllib.request import Request, urlopen


def safe_summary(data):
    out = {}
    # JobInfo in the pinned SDK drops timeout fields. Read the raw GET response,
    # then admit only known numeric fields and ISO timestamps to the public log.
    for key in ("timeout", "timeoutSeconds"):
        value = data.get(key)
        out[key] = value if type(value) in (int, float) else None
    for key in ("createdAt", "startedAt", "finishedAt"):
        value = data.get(key)
        out[key] = value if isinstance(value, str) and re.fullmatch(r"[0-9T:.+Z-]+", value) else None
    stage = (data.get("status") or {}).get("stage")
    out["stage"] = stage if stage in ("COMPLETED", "ERROR", "CANCELED", "RUNNING", "PENDING") else "unknown"
    durations = data.get("durations") or {}
    out["durations"] = {k: v for k, v in durations.items()
                        if k in ("scheduling", "running", "total", "schedulingSecs", "runningSecs", "totalSecs")
                        and type(v) in (int, float)}
    return out


def main(job):
    if not re.fullmatch(r"[a-f0-9]{24}", job):
        raise ValueError("invalid job ID")
    from huggingface_hub import HfApi
    token = os.environ["HF_TOKEN"]
    owner = HfApi(token=token).whoami()["name"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", owner):
        raise ValueError("invalid owner")
    req = Request("https://huggingface.co/api/jobs/%s/%s" % (owner, job),
                  headers={"Authorization": "Bearer " + token})
    with urlopen(req, timeout=30) as response:
        result = safe_summary(json.load(response))
    print(json.dumps(dict(result, job=job), sort_keys=True))


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as exc:
        # Error payloads may contain private data; the exception type is enough.
        print("timeout audit failed: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
