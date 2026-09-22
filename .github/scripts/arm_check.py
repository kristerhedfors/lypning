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
  split_seed                which cases of it are train, dev and test
  space_revision            the verifier image that decides native vs refused
  qwen_revision             the base model
  steps, grpo_steps         the training dose
  sft_target_run, sft_sha256  WHAT IS TRAINED ON: authored references and a
                            graded rejection-target set are two arms at the
                            same bank and dose, and nothing else says so
  sft_learning_rate, grpo_learning_rate  the effective rates the trainer
                            resolved; seed 1111 of 2026-09-21 ran at 2e-5
  kernels                   the gated-delta-net implementation; a swap on
                            identical weights moved dSLR by +1.57pp
                            (`STATUS.md` §2), so it is an arm, not a detail
  eval_draws, eval_sequences  the evidence dose
  pool_sandboxes_per_host   DENSITY IS THE INSTRUMENT: `native` is host-load
                            dependent, so packing more sandboxes onto a host
                            changes what the label means

  seed                      the point of the exercise
  pool_max_hosts            a COST ceiling, not an instrument; cents on
                            `cpu-basic` beside an h200
  score_workers             throughput
  job id, timings           not the experiment

Without the target, rates and kernel this check could not tell S4 arm A from
seed 1111's configuration at the same bank and steps, and PLAN.md's "arm_check
green before the second seed is billed" would have passed on the wrong arm --
exactly the old-config seed PLAN forbids.

OLD MANIFESTS STAY READABLE, explicitly (`arm_value`). A field a manifest
predates is `unrecorded`, and `unrecorded` is a value: two old manifests agree
on it, and an old and a new one never do, which is the safe direction -- the
check cannot vouch for what was not written down. `split_seed` is the one
exception with a known answer: before 2026-09-22 `round02_pilot.sh` reviewed
and prepared at the training seed, so an absent split seed means the split
followed the seed, and old replicates keep agreeing with each other.

`commit` is reported but not enforced. A commit that fixes a workflow does not
change the arm and a commit that changes the engine does, and this file cannot
tell those apart -- so it shows the difference and lets a reader judge, rather
than refusing every round that landed an unrelated fix.

Free and read-only: it lists manifests and compares them. It uploads nothing,
runs no model and reads no bank case.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

#: Fields that must agree for two seeds to be replicates of one experiment.
ARM_FIELDS = ("bank_path", "split_seed", "space_revision", "qwen_revision", "steps",
              "grpo_steps", "sft_target_run", "sft_sha256", "sft_learning_rate",
              "grpo_learning_rate", "kernels", "eval_draws", "eval_sequences",
              "pool_sandboxes_per_host")
#: Shown beside the arm, never enforced; see the module docstring.
REPORTED = ("commit",)
#: A manifest is evidence about an arm only if its round actually ran.
OK = ("ok", "succeeded", "success", "complete", "completed")
#: What a field a manifest predates reads as. A value, not a wildcard.
UNRECORDED = "unrecorded"
#: The split seed of a manifest written before the split was decoupled.
SPLIT_FOLLOWS_SEED = "follows-seed"


def arm_value(manifest, field):
    """One arm field of one manifest, with absence made explicit (docstring)."""
    if field in manifest:
        return manifest[field]
    if field == "split_seed":
        return SPLIT_FOLLOWS_SEED
    return UNRECORDED


def arm_of(manifest):
    return tuple((f, arm_value(manifest, f)) for f in ARM_FIELDS)


def differences(manifests):
    """Arm fields on which the given manifests disagree, as {field: {value: [seeds]}}."""
    out = {}
    for field in ARM_FIELDS:
        seen = {}
        for m in manifests:
            seen.setdefault(json.dumps(arm_value(m, field), sort_keys=True), []).append(m.get("seed"))
        if len(seen) > 1:
            out[field] = seen
    return out


def parse_pairs(pairs, flag):
    """field=value pairs over arm fields, or a usage message naming the bad one."""
    want = {}
    for pair in pairs:
        if "=" not in pair:
            return None, "%s needs field=value, got %r" % (flag, pair)
        field, value = pair.split("=", 1)
        if field not in ARM_FIELDS:
            return None, "%s %s is not an arm field; one of: %s" % (flag, field, ", ".join(ARM_FIELDS))
        want[field] = value
    return want, None


def selected(manifests, want):
    """The manifests of ONE arm, named by its fields.

    Needed since the target run became an arm field: seed 1111's round of
    2026-09-21 and S4 arm A share a bank, so without a selection the first
    completed arm A seed and that round are compared and reported as
    differing arms on every later preflight -- true, and not the question.
    Compared as text, as --expect is.
    """
    return [m for m in manifests
            if all(str(arm_value(m, f)) == str(v) for f, v in want.items())]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bank", help="only consider rounds that read this bank")
    ap.add_argument("--expect", action="append", default=[],
                    help="field=value a NEW launch would carry (repeatable); "
                         "checked against the completed seeds before it is billed")
    ap.add_argument("--select", action="append", default=[],
                    help="field=value naming the arm to check (repeatable), e.g. "
                         "sft_target_run=<run>; other completed rounds are listed and skipped")
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
    select, problem = parse_pairs(args.select, "--select")
    if problem:
        print(problem, file=sys.stderr)
        return 2
    if select:
        skipped = [m for m in done if m not in selected(done, select)]
        done = selected(done, select)
        for m in skipped:
            print("   not this arm: seed %s %s" % (m.get("seed"), m.get("job")))
    print("== %d manifest(s), %d completed%s"
          % (len(manifests), len(done), " on " + args.bank if args.bank else ""))
    for m in sorted(done, key=lambda m: (m.get("seed") or 0, m.get("job") or "")):
        print("   seed %-6s %-22s %s" % (m.get("seed"), m.get("job"), m.get("bank_path")))
        print("      %s" % "  ".join("%s=%s" % (f, arm_value(m, f)) for f in ARM_FIELDS[1:]))
        print("      commit=%s" % str(m.get("commit"))[:12])

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
        want, problem = parse_pairs(args.expect, "--expect")
        if problem:
            print(problem, file=sys.stderr)
            return 2
        for m in done:
            for field, value in sorted(want.items()):
                # Compared as text: the manifest holds 300 and a workflow holds
                # "300", and a launch refused over int-versus-str would be a
                # false alarm in the one place false alarms are most expensive.
                if str(arm_value(m, field)) != str(value):
                    failures.append("a launch with %s=%s would not match seed %s (%s=%s)"
                                    % (field, value, m.get("seed"), field, arm_value(m, field)))

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
