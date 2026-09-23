"""A split eval-2 job is the SAME arm as the pilot job it finishes, or it refuses.

`round02_pilot.sh` with EVAL2_MODE=separate stops before step 7g and leaves
`eval2-deferred.json`; `round02_eval2.sh` then runs 7g in a second job from
that pilot job's artifacts. Between the two jobs anything can move -- the
verifier Space can be rebuilt, the Qwen pin edited, the trainer's code changed,
the dispatch given another seed -- and a benchmark read from a second job is
only the pilot's benchmark if none of that happened. So before a single draw,
the second job compares what it holds with what the first job recorded, and
refuses, NAMING every field that differs:

  status, eval2_deferred   the pilot finished and deferred its eval-2
  job                      the manifest is the job it was asked for
  sft_selected_step        best.json, the marker and the manifest agree, and
                           the adapter directory is that step's
  seal                     the adapter's files are the ones it was sealed with
  eval2_bundle_digest      the bundle's own digest recomputes, and matches the
                           manifest and the marker
  pilot_bundle_digest      the adapter was trained on the pilot's bundle
  engine                   the downloaded lypning-l is the bundle's engine
  space, space_revision    the verifier image is the pilot's
  qwen_revision            the base model is the pilot's and the adapter's
  code_sha256              `training/pipeline` and `training/gpu` are
                           byte-identical to the code that trained the adapter
  seed, eval_draws, eval_sequences, pool_sandboxes_per_host
                           the evaluation's own arm fields are the pilot's

`problems` is pure and takes everything as arguments; `main` gathers them from
disk, stages the verified bundle and adapter into the round directory, and
writes `lineage.json`. Nothing case-level is printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT / "training") not in sys.path:
    sys.path.insert(0, str(ROOT / "training"))

JOB_ID = re.compile(r"[0-9a-f]{24}")
#: Evaluation arm fields a split eval-2 job must share with its pilot job; the
#: eval-2 manifest copies them so `arm_check` can see the pair agree.
PAIR_FIELDS = ("seed", "eval_draws", "eval_sequences", "pool_sandboxes_per_host",
               "space_revision", "qwen_revision", "bank_path", "split_seed")


def problems(manifest, deferred, best, adapter_experiment, seal_ok, eval2_bundle, eval2_digest,
             here, job):
    """Every identity refusal, each naming its field; empty when the pair is one arm.

    `here` is what this job holds: space, space_revision, qwen_revision,
    engine_identity, code_sha256, seed, eval_draws, eval_sequences,
    pool_sandboxes_per_host. `eval2_digest` is the bundle's recomputed digest.
    """
    out = []

    def refuse(field, detail):
        out.append("%s: %s" % (field, detail))

    if manifest.get("job") != job:
        refuse("job", "the manifest is job %s, not %s" % (manifest.get("job"), job))
    if manifest.get("status") != "complete":
        refuse("status", "the pilot job's status is %r, not 'complete'" % manifest.get("status"))
    if manifest.get("eval2_deferred") is not True or not deferred:
        refuse("eval2_deferred", "the pilot job did not defer its eval-2 (EVAL2_MODE=separate)")
    step = best.get("step")
    steps = {best.get("step"), (deferred or {}).get("sft_selected_step"), manifest.get("sft_selected_step"),
             adapter_experiment.get("checkpoint_step")}
    if step is None or len(steps) != 1:
        refuse("sft_selected_step", "best.json, eval2-deferred.json, the manifest and the adapter "
                                    "disagree on the selected step")
    if not seal_ok:
        refuse("seal", "the selected adapter's files do not match its seal.json")
    want = {manifest.get("eval2_bundle_digest"), (deferred or {}).get("eval2_bundle_digest")}
    if eval2_digest != eval2_bundle.get("digest") or want != {eval2_digest}:
        refuse("eval2_bundle_digest", "the eval-2 bundle is not the one the pilot job prepared")
    if adapter_experiment.get("bundle_digest") != manifest.get("pilot_bundle_digest"):
        refuse("pilot_bundle_digest", "the adapter was not trained on the pilot job's bundle")
    if eval2_bundle.get("identity") != here["engine_identity"]:
        moved = sorted(k for k in set(eval2_bundle.get("identity") or {}) | set(here["engine_identity"])
                       if (eval2_bundle.get("identity") or {}).get(k) != here["engine_identity"].get(k))
        refuse("engine", "the bundle's engine identity differs in " + ", ".join(moved))
    if manifest.get("space") != here["space"]:
        refuse("space", "the pilot job verified through another Space")
    execution = {"kind": "hf-sandbox-pool", "image": "hf.co/spaces/" + here["space"],
                 "revision": here["space_revision"]}
    if manifest.get("space_revision") != here["space_revision"] or eval2_bundle.get("execution") != execution:
        refuse("space_revision", "the pilot job's verifier revision is %s, this job's is %s"
               % (manifest.get("space_revision"), here["space_revision"]))
    if {manifest.get("qwen_revision"), adapter_experiment.get("revision")} != {here["qwen_revision"]}:
        refuse("qwen_revision", "the pilot job's base model is not this job's")
    if adapter_experiment.get("code_sha256") != here["code_sha256"]:
        prior = adapter_experiment.get("code_sha256") or {}
        moved = sorted(k for k in set(prior) | set(here["code_sha256"])
                       if prior.get(k) != here["code_sha256"].get(k))
        refuse("code_sha256", "the trainer code differs from the adapter's in " + ", ".join(moved))
    for field in ("seed", "eval_draws", "eval_sequences", "pool_sandboxes_per_host"):
        if manifest.get(field) != here[field]:
            refuse(field, "the pilot job ran %s, this job %s" % (manifest.get(field), here[field]))
    if adapter_experiment.get("seed") != here["seed"]:
        refuse("seed", "the adapter was trained at seed %s" % adapter_experiment.get("seed"))
    return out


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main(argv=None):
    from pipeline.jsonio import sha256_of, write_json
    from pipeline.public_view import public_view
    from pipeline.training import TrainingError, engine_identity
    from pipeline.training_contract import seal_adapter, source_identity

    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--pilot", type=Path, required=True, help="downloaded round-02/<job> directory")
    p.add_argument("--job", required=True, help="the pilot job id (24 hex)")
    p.add_argument("--engine", type=Path, required=True)
    p.add_argument("--round", type=Path, required=True, help="this job's round directory")
    args = p.parse_args(argv)
    if not JOB_ID.fullmatch(args.job):
        print("== split eval-2 refused: the pilot job id must be 24 lower-case hex characters")
        return 2
    manifest = _read(args.pilot / "job-manifest.json")
    deferred = _read(args.pilot / "eval2-deferred.json")
    best = _read(args.pilot / "sft" / "best.json")
    step = best.get("step")
    adapter = args.pilot / "sft" / ("adapter-%s" % step)
    experiment = _read(adapter / "experiment.json")
    try:
        seal_ok = _read(adapter / "seal.json") == seal_adapter(adapter) and bool(experiment)
    except (OSError, TrainingError):
        seal_ok = False
    bundle = _read(args.pilot / "eval2" / "bundle.json")
    digest = sha256_of({k: v for k, v in bundle.items() if k != "digest"}) if bundle else None
    here = {"space": os.environ["SPACE_REPO"], "space_revision": os.environ["SPACE_REV"],
            "qwen_revision": os.environ["QWEN_REV"], "engine_identity": engine_identity(args.engine),
            "code_sha256": source_identity(ROOT / "training"), "seed": int(os.environ["SEED"]),
            "eval_draws": int(os.environ["EVAL_DRAWS"]), "eval_sequences": int(os.environ["EVAL_SEQUENCES"]),
            "pool_sandboxes_per_host": int(os.environ["NTX_POOL_SANDBOXES_PER_HOST"])}
    found = problems(manifest, deferred, best, experiment, seal_ok, bundle, digest, here, args.job)
    if found:
        for line in found:
            print("== SPLIT LINEAGE REFUSED:", line)
        return 1
    shutil.copytree(args.pilot / "eval2", args.round / "eval2")
    (args.round / "sft").mkdir(parents=True, exist_ok=True)
    shutil.copytree(adapter, args.round / "sft" / adapter.name)
    shutil.copy2(args.pilot / "sft" / "best.json", args.round / "sft" / "best.json")
    lineage = {"eval2_of": args.job, "pilot_commit": manifest.get("commit"),
               "sft_selected_step": step, "adapter": "sft/" + adapter.name,
               "adapter_seal_sha256": sha256_of(_read(adapter / "seal.json")),
               "eval2_bundle_digest": digest, "pilot_bundle_digest": manifest.get("pilot_bundle_digest"),
               "code_sha256_digest": sha256_of(here["code_sha256"]),
               "pair": {field: manifest.get(field) for field in PAIR_FIELDS}}
    write_json(args.round / "lineage.json", lineage)
    print("== split lineage verified:", json.dumps(public_view(lineage)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
