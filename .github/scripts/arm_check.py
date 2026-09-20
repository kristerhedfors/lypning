"""Refuse to add a seed to an S4 result that is not the same arm as the others.

WHY THIS EXISTS. A complete S4 replicate is seeds 1111, 2222 and 3333, read
from three `job-manifest.json` files and never inferred from one. Nothing
related those three to each other: `s0_inventory` can PRINT a manifest, and no
code anywhere compared two. So three seeds run weeks apart, across an engine
fix or a re-cut bank, would be combined into one result by hand and nothing
would say they were different experiments.

That failure is silent, expensive and late. Each seed is about $27, the
mismatch is invisible in every individual manifest, and it is only discoverable
by someone remembering what changed between two dates.

WHAT DEFINES THE ARM, and what deliberately does not:

  bank_path                 the population being learned and measured
  space_revision            the verifier image that decides native vs refused
  qwen_revision             the base model
  steps, grpo_steps         the training dose
  eval_draws, eval_sequences  the evidence dose
  pool_sandboxes_per_host   DENSITY IS THE INSTRUMENT: `native` is host-load
                            dependent, so packing more sandboxes onto a host
                            changes what the label means

  seed                      the point of the exercise
  pool_max_hosts            a COST ceiling, not an instrument; cents on
                            `cpu-basic` beside an h200
  score_workers             throughput
  job id, timings           not the experiment

`commit` and `kernels` are reported but not enforced. A commit that fixes a
workflow does not change the arm and a commit that changes the engine does, and
this file cannot tell those apart -- so it shows the difference and lets a
reader judge, rather than refusing every round that landed an unrelated fix.
`kernels` is absent from manifests written before 2026-09-20 and is reported
as `unrecorded` there rather than silently compared against nothing.

Free and read-only: it lists manifests and compares them. It uploads nothing,
runs no model and reads no bank case.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

#: Fields that must agree for two seeds to be replicates of one experiment.
ARM_FIELDS = ("bank_path", "space_revision", "qwen_revision", "steps", "grpo_steps",
              "eval_draws", "eval_sequences", "pool_sandboxes_per_host")
#: Shown beside the arm, never enforced; see the module docstring.
REPORTED = ("commit", "kernels")
#: A manifest is evidence about an arm only if its round actually ran.
OK = ("ok", "succeeded", "success", "complete", "completed")


def arm_of(manifest):
    return tuple((f, manifest.get(f)) for f in ARM_FIELDS)


def differences(manifests):
    """Arm fields on which the given manifests disagree, as {field: {value: [seeds]}}."""
    out = {}
    for field in ARM_FIELDS:
        seen = {}
        for m in manifests:
            seen.setdefault(json.dumps(m.get(field), sort_keys=True), []).append(m.get("seed"))
        if len(seen) > 1:
            out[field] = seen
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bank", help="only consider rounds that read this bank")
    ap.add_argument("--expect", action="append", default=[],
                    help="field=value a NEW launch would carry (repeatable); "
                         "checked against the completed seeds before it is billed")
    ap.add_argument("--require-seeds", type=int, default=0,
                    help="also fail unless this many distinct seeds have completed")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(token=token)
    repo = "%s/%s" % (api.whoami()["name"],
                      os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))
    if not api.repo_info(repo, repo_type="dataset").private:
        raise SystemExit("refusing to read rounds from a public repository")

    paths = [f for f in api.list_repo_files(repo, repo_type="dataset")
             if f.endswith("/job-manifest.json")]
    manifests = []
    for path in sorted(paths):
        try:
            local = hf_hub_download(repo, path, repo_type="dataset", token=token)
            manifests.append(json.loads(open(local, encoding="utf-8").read()))
        except Exception as exc:                                  # noqa: BLE001
            print("   could not read %s: %s" % (path, type(exc).__name__), file=sys.stderr)

    done = [m for m in manifests if str(m.get("status", "")).lower() in OK]
    if args.bank:
        done = [m for m in done if m.get("bank_path") == args.bank]
    print("== %d manifest(s), %d completed%s"
          % (len(manifests), len(done), " on " + args.bank if args.bank else ""))
    for m in sorted(done, key=lambda m: (m.get("seed") or 0, m.get("job") or "")):
        print("   seed %-6s %-22s %s" % (m.get("seed"), m.get("job"), m.get("bank_path")))
        print("      %s" % "  ".join("%s=%s" % (f, m.get(f)) for f in ARM_FIELDS[1:]))
        print("      commit=%s  kernels=%s"
              % (str(m.get("commit"))[:12], m.get("kernels") or "unrecorded"))

    failures = []
    bad = differences(done)
    if bad:
        print("\nARMS DIFFER across completed seeds:")
        for field, seen in sorted(bad.items()):
            for value, seeds in sorted(seen.items()):
                print("   %-24s %-28s seeds %s"
                      % (field, value, ", ".join(str(s) for s in seeds)))
        failures.append("completed seeds are not replicates of one experiment")

    if args.expect:
        want = {}
        for pair in args.expect:
            if "=" not in pair:
                print("--expect needs field=value, got %r" % pair, file=sys.stderr)
                return 2
            field, value = pair.split("=", 1)
            if field not in ARM_FIELDS:
                print("--expect %s is not an arm field; one of: %s"
                      % (field, ", ".join(ARM_FIELDS)), file=sys.stderr)
                return 2
            want[field] = value
        for m in done:
            for field, value in sorted(want.items()):
                # Compared as text: the manifest holds 300 and a workflow holds
                # "300", and a launch refused over int-versus-str would be a
                # false alarm in the one place false alarms are most expensive.
                if str(m.get(field)) != str(value):
                    failures.append("a launch with %s=%s would not match seed %s (%s=%s)"
                                    % (field, value, m.get("seed"), field, m.get(field)))

    seeds = sorted({m.get("seed") for m in done})
    if args.require_seeds and len(seeds) < args.require_seeds:
        failures.append("%d distinct seed(s) completed, %d required: %s"
                        % (len(seeds), args.require_seeds, seeds))

    if failures:
        print("")
        for line in failures:
            print("ARM CHECK FAILED: %s" % line, file=sys.stderr)
        return 1
    print("\n   %d completed seed(s) %s share one arm" % (len(seeds), seeds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
