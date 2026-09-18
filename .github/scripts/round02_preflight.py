"""Round-02 preflight: read the account, the price and the model revision.

Free. Submits nothing, creates nothing, writes nothing to the Hub. It exists
because three facts decide whether a round can run at all, and none of them is
knowable from the checkout: whether this token can see Jobs hardware, what an
hour of the chosen flavor costs, and whether the approved model revision
resolves. Ledger row T3 records the last attempt dying on a provider 402 after
the job was already submitted; this asks first.

Every failure here is reported and then swallowed into `jobs_ok=false` rather
than raised, so the run ends green with a readable answer instead of a
traceback: a preflight that cannot reach the Hub has not found a problem with
the round, it has found a problem with itself.
"""
from __future__ import annotations

import json
import os
import sys

from huggingface_hub import HfApi


def out(**kw):
    """Emit step outputs; also echo them so the log alone is readable."""
    path = os.environ.get("GITHUB_OUTPUT")
    for key, value in kw.items():
        print("%-12s %s" % (key, value))
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("%s=%s\n" % (key, value))


def main() -> int:
    token = os.environ.get("HF_TOKEN", "")
    if not token:
        print("HF_TOKEN is not set in this job", file=sys.stderr)
        out(jobs_ok="false", whoami="", qwen_rev="", hourly_usd="")
        return 0
    api = HfApi(token=token)

    print("== identity")
    try:
        me = api.whoami()
    except Exception as exc:                                  # noqa: BLE001
        print("whoami failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        out(jobs_ok="false", whoami="", qwen_rev="", hourly_usd="")
        return 0
    name = me.get("name") or ""
    # Never print the token; the scopes tell us whether a write will work.
    scopes = (me.get("auth") or {}).get("accessToken", {}).get("role") or me.get("type")
    print("  account  %s  (%s)" % (name, scopes))

    print("== jobs hardware and price")
    flavor = os.environ.get("FLAVOR", "a10g-small")
    hourly = ""
    jobs_ok = "false"
    try:
        hardware = {h.name: h for h in api.list_jobs_hardware()}
        jobs_ok = "true"
        print("  %d flavor(s) visible" % len(hardware))
        if flavor in hardware:
            h = hardware[flavor]
            hourly = "%.2f" % (h.unit_cost_usd * (60 if h.unit_label == "minute" else 1))
            print("  %s: $%s/hour" % (flavor, hourly))
        else:
            print("  %s NOT offered to this account; visible: %s"
                  % (flavor, ", ".join(sorted(hardware))), file=sys.stderr)
            jobs_ok = "false"
    except Exception as exc:                                  # noqa: BLE001
        # This is the 402 route, and the one worth naming precisely: a token
        # that reads models fine can still have no Jobs entitlement or credit.
        print("  list_jobs_hardware failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)

    print("== approved model revision")
    model = os.environ.get("QWEN_MODEL", "Qwen/Qwen3.8-27B")
    qwen_rev = ""
    try:
        info = api.model_info(model)
        qwen_rev = info.sha or ""
        print("  %s -> %s" % (model, qwen_rev))
        if len(qwen_rev) != 40:
            print("  refusing a revision that is not a 40-character commit", file=sys.stderr)
            qwen_rev = ""
    except Exception as exc:                                  # noqa: BLE001
        print("  model_info failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        print("  the round pins an APPROVED immutable commit; `main` is not one "
              "and an unreachable model is not one either", file=sys.stderr)

    print("== artifact destination")
    owner = name
    work = "%s/%s" % (owner, os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))
    try:
        info = api.repo_info(work, repo_type="dataset")
        print("  %s exists, private=%s" % (work, info.private))
        if not info.private:
            print("  the launcher refuses a destination that is not private, and "
                  "never flips visibility itself", file=sys.stderr)
    except Exception:                                         # noqa: BLE001
        print("  %s does not exist yet; the submit job creates it private" % work)

    if not qwen_rev:
        jobs_ok = "false"
    out(jobs_ok=jobs_ok, whoami=name, qwen_rev=qwen_rev, hourly_usd=hourly)
    print("\n== preflight verdict: %s" % ("ready to submit" if jobs_ok == "true"
                                          else "NOT ready — see the lines above"))
    print(json.dumps({"account": name, "jobs_ok": jobs_ok, "flavor": flavor,
                      "hourly_usd": hourly, "qwen_rev": qwen_rev}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
