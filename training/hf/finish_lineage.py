"""A finish job evaluates the pilot job's own SFT adapter, or it refuses.

`round02_finish.sh` finishes a pilot job that completed SFT and then died --
seed 1111's arm-A pilot, HF job 6ab52a686b030d633f68e503, died in its base
test arm on one engine mismatch (2026-09-24) -- by running that pilot's steps
7f and 7g from the adapters it saved, instead of training again. A second job
measures the pilot's arm only if it evaluates the pilot's adapter on the
pilot's bundles through the pilot's engine and verifier, so before any weight
is loaded this compares what the finish job holds with what the pilot job
recorded, and refuses, NAMING every field that differs:

  job                      the manifest is the pilot job it was asked for
  sft_completed            the pilot's last stage is after SFT and its
                           best.json observed the full registered dose
  grpo_steps               arm A: the pilot ran no GRPO, so the finish's two
                           arms (base, SFT) are all of it
  selected_step            an evaluated, non-zero checkpoint, and the adapter
                           directory is that step's SFT adapter
  selection_override       a step other than the rule's is registered in
                           `OVERRIDES` with its dated amendment
  seal                     the adapter's files are the ones it was sealed with
  pilot_bundle_digest      the pilot bundle's own digest recomputes, matches the
                           manifest, and is the bundle the adapter trained on
  eval2_bundle_digest      the eval-2 bundle's digest recomputes and matches
  sft_sha256               the adapter trained on the manifest's target set
  verifier_sha256          THIS code's verifier modules hash to what both
                           bundles recorded, so `load_bundle` accepts them
  engine                   the downloaded lypning-l is both bundles' engine and
                           the one the adapter's targets were graded by
  space, space_revision    the verifier image is the pilot's, and the Space's
                           current head is still that revision (the pool can
                           only serve the head: `hf_sandbox_runner`)
  qwen_revision            the base model is the pilot's and the adapter's
  seed, split_seed, eval_draws, eval_sequences, pool_sandboxes_per_host
                           the evaluation's own arm fields are the pilot's
  commit                   this checkout descends from the pilot's commit

`code_sha256` is ALLOWED to differ, unlike `split_eval2`: the finish runs newer
evaluation code by design (#126 counts an engine-mismatch draw instead of
aborting). Both digests, the files that moved and every commit between the
two are written to `lineage.json` as `finish_lineage`, so a reader sees
exactly what code measured what.

`problems` is pure and takes everything as arguments; `main` gathers them from
disk, stages the verified bundles and adapter into the round directory and
writes `lineage.json`. Nothing case-level is printed: the rule-v2 re-reading of
the pilot's dev evaluations below records steps and aggregates only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT / "training") not in sys.path:
    sys.path.insert(0, str(ROOT / "training"))

JOB_ID = re.compile(r"[0-9a-f]{24}")
#: Fields the finish job shares with its pilot job (`split_eval2.PAIR_FIELDS`);
#: the finish manifest copies them so `arm_check` can see the pair agree.
PAIR_FIELDS = ("seed", "eval_draws", "eval_sequences", "pool_sandboxes_per_host",
               "space_revision", "qwen_revision", "bank_path", "split_seed")
#: Stages `round02_pilot.sh` reaches only once SFT has written best.json and
#: its selected adapter has been checked for a seal. `sft` itself is not one:
#: a job that died there may have died mid-dose.
POST_SFT_STAGES = ("sft-dev-reload", "grpo-gate", "probe", "grpo", "test", "eval2-deferred",
                   "eval2", "report", "done")
#: What a pre-versioned best.json was selected under.
LEGACY_RULE = "coverage-family-macro/1"
#: A finish may evaluate a step the pilot's rule did not select ONLY when that
#: step is registered here, with its date, its reason and the amendment that
#: records it. A dispatch input cannot choose a checkpoint on its own.
OVERRIDES = {
    "6ab52a686b030d633f68e503": {
        "step": 1050, "date": "2026-09-25",
        "reason": ("the dev selector's coverage family macro is fragile when dev families "
                   "include tiny slices: rule v1 selected 350, while pooled dev correct-and-"
                   "native over base was +1.9 / +2.6 / +3.1pp at 350 / 700 / 1,050 and "
                   "correctness -0.9 / 0.0 / +0.6pp; the operator chose 1,050 on the pooled "
                   "evidence and to finish from the saved adapters rather than re-train"),
        "amendment": "training/EVAL2.md section 4, amendment of 2026-09-25; training/PLAN.md Step 4"},
}


def problems(manifest, best, adapter_experiment, seal_ok, pilot_bundle, pilot_digest,
             eval2_bundle, eval2_digest, here, job, step):
    """Every identity refusal, each naming its field; empty when the finish is the pilot's arm.

    `here` is what this job holds: space, space_head, qwen_revision,
    engine_identity, verifier_sha256, seed, split_seed, eval_draws,
    eval_sequences, pool_sandboxes_per_host, commit_descends.
    `pilot_digest` and `eval2_digest` are the bundles' recomputed digests.
    """
    out = []

    def refuse(field, detail):
        out.append("%s: %s" % (field, detail))

    if not JOB_ID.fullmatch(job or ""):
        refuse("job", "the pilot job id must be 24 lower-case hex characters")
    elif manifest.get("job") != job:
        refuse("job", "the manifest is job %s, not %s" % (manifest.get("job"), job))
    observed = sorted(o.get("step") for o in best.get("observed") or []
                      if isinstance(o.get("step"), int))
    if manifest.get("last_stage") not in POST_SFT_STAGES:
        refuse("sft_completed", "the pilot job's last stage is %r, not one after SFT"
               % manifest.get("last_stage"))
    if not isinstance(best.get("step"), int) or not observed or observed[-1] != manifest.get("steps"):
        refuse("sft_completed", "sft/best.json did not observe the registered %s steps"
               % manifest.get("steps"))
    if manifest.get("grpo_steps") != 0:
        refuse("grpo_steps", "the pilot ran GRPO; the finish carries the base and SFT arms only")
    if not isinstance(step, int) or step <= 0 or step not in observed:
        refuse("selected_step", "step %r is not an evaluated SFT checkpoint of the pilot" % (step,))
    if adapter_experiment.get("checkpoint_step") != step or adapter_experiment.get("stage") != "sft":
        refuse("selected_step", "the adapter directory is not SFT step %r's" % (step,))
    if step != best.get("step") and (OVERRIDES.get(job) or {}).get("step") != step:
        refuse("selection_override", "step %r is not the rule's step %r and no dated amendment "
                                     "registers it (finish_lineage.OVERRIDES)" % (step, best.get("step")))
    if not seal_ok:
        refuse("seal", "the selected adapter's files do not match its seal.json")
    if (pilot_digest != pilot_bundle.get("digest") or pilot_bundle.get("purpose") != "pilot"
            or {manifest.get("pilot_bundle_digest"), adapter_experiment.get("bundle_digest")} != {pilot_digest}):
        refuse("pilot_bundle_digest", "the pilot bundle is not the one the pilot job prepared and "
                                      "the adapter trained on")
    if (eval2_digest != eval2_bundle.get("digest") or eval2_bundle.get("purpose") != "benchmark"
            or manifest.get("eval2_bundle_digest") != eval2_digest):
        refuse("eval2_bundle_digest", "the eval-2 bundle is not the one the pilot job prepared")
    targets = adapter_experiment.get("sft_targets") or {}
    if targets.get("sft_sha256") != manifest.get("sft_sha256"):
        refuse("sft_sha256", "the adapter was not trained on the manifest's target set")
    engine = here["engine_identity"]
    for name, bundle in (("pilot", pilot_bundle), ("eval-2", eval2_bundle)):
        identity = bundle.get("identity") or {}
        if identity.get("verifier_sha256") != here["verifier_sha256"]:
            refuse("verifier_sha256", "this code's verifier modules are not the ones the %s bundle "
                                      "was prepared by; load_bundle would refuse it" % name)
        if identity != engine:
            moved = sorted(k for k in set(identity) | set(engine) if identity.get(k) != engine.get(k))
            refuse("engine", "the %s bundle's engine identity differs in %s" % (name, ", ".join(moved)))
    if (targets.get("lineage") or {}).get("engine_sha256") != engine.get("sha256"):
        refuse("engine", "the adapter's targets were graded by another engine")
    if manifest.get("space") != here["space"]:
        refuse("space", "the pilot job verified through another Space")
    revision = manifest.get("space_revision")
    execution = {"kind": "hf-sandbox-pool", "image": "hf.co/spaces/" + str(here["space"]),
                 "revision": revision}
    if pilot_bundle.get("execution") != execution or eval2_bundle.get("execution") != execution:
        refuse("space_revision", "the bundles were not prepared at the pilot's verifier revision")
    if here["space_head"] != revision:
        refuse("space_revision", "the Space's head is %s, the pilot's revision is %s; the pool "
                                 "serves only the head" % (here["space_head"], revision))
    if {manifest.get("qwen_revision"), adapter_experiment.get("revision")} != {here["qwen_revision"]}:
        refuse("qwen_revision", "the pilot job's base model is not this job's")
    if manifest.get("seed") != here["seed"] or adapter_experiment.get("seed") != here["seed"]:
        refuse("seed", "the pilot ran seed %s, the adapter %s, this job %s"
               % (manifest.get("seed"), adapter_experiment.get("seed"), here["seed"]))
    if {manifest.get("split_seed"), pilot_bundle.get("seed"), eval2_bundle.get("seed")} != {here["split_seed"]}:
        refuse("split_seed", "the pilot's split seed is %s, this job's %s"
               % (manifest.get("split_seed"), here["split_seed"]))
    for field in ("eval_draws", "eval_sequences", "pool_sandboxes_per_host"):
        if manifest.get(field) != here[field]:
            refuse(field, "the pilot job ran %s, this job %s" % (manifest.get(field), here[field]))
    if not here["commit_descends"]:
        refuse("commit", "this checkout does not descend from the pilot's commit %s"
               % manifest.get("commit"))
    return out


def git_lineage(pilot_commit):
    """(descends, head, [{sha, subject}] from the pilot's commit, exclusive, to HEAD)."""
    def git(*argv):
        return subprocess.run(["git", *argv], cwd=ROOT, capture_output=True, text=True)
    head = git("rev-parse", "HEAD").stdout.strip()
    if not pilot_commit or not re.fullmatch(r"[0-9a-f]{40}", pilot_commit):
        return False, head, []
    if git("merge-base", "--is-ancestor", pilot_commit, "HEAD").returncode != 0:
        return False, head, []
    log = git("log", "--reverse", "--format=%H %s", "%s..HEAD" % pilot_commit).stdout
    return True, head, [{"sha": line[:40], "subject": line[41:]} for line in log.splitlines() if line]


def reselect(evaluations, rule):
    """What `rule` selects from the pilot's own SFT dev evaluations; aggregates only.

    A re-reading, never the selection of record: the pilot selected under its
    own rule, and the finish evaluates the step `main` was given. So a record
    this cannot read is reported as unreadable, by exception type only, and
    never stops the finish.
    """
    from pipeline.training_metrics import CheckpointGate, summarize
    from pipeline.training_types import TrainingError

    steps = {}
    for row in evaluations:
        steps.setdefault(row.get("step"), []).append(row)
    if 0 not in steps:
        return None
    try:
        gate = CheckpointGate(baseline=summarize(steps[0]), rule=rule)
        for step in sorted(s for s in steps if isinstance(s, int) and s > 0):
            gate.observe(step, summarize(steps[step]))
    except (TrainingError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        return {"rule": rule, "selected_step": None, "unreadable": type(exc).__name__}
    return {"rule": rule, "selected_step": gate.best_step,
            "observed": [{k: o[k] for k in ("step", "delta", "standard_error", "margin", "rejected_for")}
                         for o in gate.report()["observed"]]}


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main(argv=None):
    from pipeline.jsonio import read_jsonl, sha256_of, write_json
    from pipeline.public_view import public_view
    from pipeline.training import TrainingError, engine_identity, verifier_sha256
    from pipeline.training_contract import seal_adapter, source_identity
    from pipeline.training_metrics import SELECTION_RULE

    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--pilot", type=Path, required=True, help="downloaded round-02/<job> directory")
    p.add_argument("--job", required=True, help="the pilot job id (24 hex)")
    p.add_argument("--step", required=True, help="the SFT step to evaluate")
    p.add_argument("--engine", type=Path, required=True)
    p.add_argument("--round", type=Path, required=True, help="this job's round directory")
    args = p.parse_args(argv)
    if not JOB_ID.fullmatch(args.job) or not re.fullmatch(r"[1-9][0-9]*", args.step):
        print("== finish refused: the pilot job id must be 24 lower-case hex characters and "
              "the step a positive integer")
        return 2
    step = int(args.step)
    manifest = _read(args.pilot / "job-manifest.json")
    best = _read(args.pilot / "sft" / "best.json")
    adapter = args.pilot / "sft" / ("adapter-%d" % step)
    experiment = _read(adapter / "experiment.json")
    try:
        seal = _read(adapter / "seal.json")
        seal_ok = bool(experiment) and seal == seal_adapter(adapter)
    except (OSError, TrainingError):
        seal, seal_ok = {}, False
    bundles = {}
    for name in ("pilot", "eval2"):
        bundle = _read(args.pilot / name / "bundle.json")
        bundles[name] = (bundle, sha256_of({k: v for k, v in bundle.items() if k != "digest"})
                         if bundle else None)
    descends, head, commits = git_lineage(manifest.get("commit"))
    here = {"space": os.environ["SPACE_REPO"], "space_head": os.environ["SPACE_HEAD"],
            "qwen_revision": os.environ["QWEN_REV"], "engine_identity": engine_identity(args.engine),
            "verifier_sha256": verifier_sha256(), "seed": int(os.environ["SEED"]),
            "split_seed": int(os.environ["SPLIT_SEED"]), "eval_draws": int(os.environ["EVAL_DRAWS"]),
            "eval_sequences": int(os.environ["EVAL_SEQUENCES"]),
            "pool_sandboxes_per_host": int(os.environ["NTX_POOL_SANDBOXES_PER_HOST"]),
            "commit_descends": descends}
    found = problems(manifest, best, experiment, seal_ok, bundles["pilot"][0], bundles["pilot"][1],
                     bundles["eval2"][0], bundles["eval2"][1], here, args.job, step)
    if found:
        for line in found:
            print("== FINISH LINEAGE REFUSED:", line)
        return 1
    for name in ("pilot", "eval2"):
        (args.round / name).mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.pilot / name / "bundle.json", args.round / name / "bundle.json")
    (args.round / "sft").mkdir(parents=True, exist_ok=True)
    shutil.copytree(adapter, args.round / "sft" / adapter.name)
    shutil.copy2(args.pilot / "sft" / "best.json", args.round / "sft" / "best.json")
    pilot_code, finish_code = experiment.get("code_sha256") or {}, source_identity(ROOT / "training")
    try:
        evaluations = read_jsonl(args.pilot / "sft" / "evaluations.jsonl")
    except (OSError, ValueError):
        evaluations = []
    rule_step = best.get("step")
    lineage = {
        "finish_of": args.job, "pilot_commit": manifest.get("commit"), "finish_commit": head,
        "selected_step": step, "rule_selected_step": rule_step,
        "rule_version": (best.get("rule") or {}).get("version", LEGACY_RULE),
        "selection_override": (dict(OVERRIDES[args.job], rule_selected_step=rule_step)
                               if step != rule_step else None),
        "adapter": "sft/" + adapter.name, "adapter_seal_sha256": sha256_of(seal),
        "pilot_bundle_digest": bundles["pilot"][1], "eval2_bundle_digest": bundles["eval2"][1],
        "engine_sha256": here["engine_identity"].get("sha256"),
        "verifier_sha256": here["verifier_sha256"],
        "pair": {field: manifest.get(field) for field in PAIR_FIELDS},
        "finish_lineage": {
            "pilot_code_sha256_digest": sha256_of(pilot_code),
            "finish_code_sha256_digest": sha256_of(finish_code),
            "code_sha256_moved": sorted(k for k in set(pilot_code) | set(finish_code)
                                        if pilot_code.get(k) != finish_code.get(k)),
            "commits": commits},
        # The corrected rule, re-read over the pilot's own dev draws: evidence
        # beside the override, not a second selection.
        "reselection": reselect(evaluations, SELECTION_RULE)}
    write_json(args.round / "lineage.json", lineage)
    print("== finish lineage verified:", json.dumps(public_view(lineage)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
