"""Free checks before a `finish` job is billed: the ones a CI runner can make.

`round02_finish.sh` refuses every identity mismatch itself (`finish_lineage.py`),
but only after the dependency install on an h200. Some of those refusals can be
decided here, for nothing, from the pilot job's manifest and the Hub's file
listing:

  job, sft_completed, grpo_steps   the manifest is the pilot's, SFT finished,
                                   and arm A ran no GRPO
  selected_step, selection_override  the adapter directory exists, and a step
                                   other than the rule's is registered in
                                   `finish_lineage.OVERRIDES`
  space, space_revision            the pilot verified through this Space, its
                                   revision still serves `lypning-l`, and it is
                                   the head bootstrap just resolved -- the pool
                                   serves only the head (bootstrap HOLDS the
                                   Space for a finish, so it cannot move it)
  seed, split_seed, eval_draws, eval_sequences, pool_sandboxes_per_host,
  qwen_revision                    what this dispatch will send the job is what
                                   the pilot ran
  pilot_bundle_digest, eval2_bundle_digest
                                   both bundles' digests recompute and are the
                                   manifest's
  verifier_sha256                  THIS checkout's verifier modules are the ones
                                   both bundles were prepared by
  engine                           the `lypning-l` at the pilot's revision is both
                                   bundles' engine and the one the adapter's
                                   targets were graded by
  selected_step (experiment)       the adapter's experiment.json is SFT step
                                   `step`'s, trained on the pilot bundle at the
                                   pilot's seed and Qwen revision

The adapter's weights and seal are checked in the job (`finish_lineage.py`):
they are the one download too large to make here.

Prints aggregates only: the job, its status and last stage, the steps and the
revision; never a bundle's cases. Reads the private dataset repo and the Space;
writes nothing; exits 1 on a refusal.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def lineage_module():
    spec = importlib.util.spec_from_file_location("finish_lineage_preflight",
                                                  ROOT / "training" / "hf" / "finish_lineage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def problems(manifest, files, here, lineage):
    """Every free refusal, each naming its field. `files` is the pilot job's file list."""
    out = []
    job, step = here["job"], here["step"]
    if manifest.get("job") != job:
        out.append("job: the manifest is job %s, not %s" % (manifest.get("job"), job))
    if manifest.get("last_stage") not in lineage.POST_SFT_STAGES:
        out.append("sft_completed: the pilot job's last stage is %r, not one after SFT"
                   % manifest.get("last_stage"))
    if manifest.get("grpo_steps") != 0:
        out.append("grpo_steps: the pilot ran GRPO; the finish carries the base and SFT arms only")
    prefix = "round-02/%s/" % job
    need = ["sft/best.json", "sft/adapter-%d/seal.json" % step, "sft/adapter-%d/experiment.json" % step,
            "pilot/bundle.json", "eval2/bundle.json"]
    missing = [name for name in need if prefix + name not in files]
    if missing:
        out.append("selected_step: the pilot job holds no %s" % ", ".join(missing))
    rule_step = manifest.get("sft_selected_step")
    if step != rule_step and (lineage.OVERRIDES.get(job) or {}).get("step") != step:
        out.append("selection_override: step %s is not the rule's step %s and no dated amendment "
                   "registers it (finish_lineage.OVERRIDES)" % (step, rule_step))
    if manifest.get("space") != here["space"]:
        out.append("space: the pilot job verified through another Space")
    if not here["revision_serves_engine"]:
        out.append("space_revision: the pilot's revision %s no longer serves lypning-l"
                   % manifest.get("space_revision"))
    if manifest.get("space_revision") != here["space_head"]:
        out.append("space_revision: the Space's head is %s, the pilot's revision is %s; the pool "
                   "serves only the head" % (here["space_head"], manifest.get("space_revision")))
    for field in ("seed", "split_seed", "eval_draws", "eval_sequences", "pool_sandboxes_per_host",
                  "qwen_revision"):
        if manifest.get(field) != here[field]:
            out.append("%s: the pilot job ran %s, this dispatch sends %s"
                       % (field, manifest.get(field), here[field]))
    bundles = here["bundles"]
    for name, key in (("pilot", "pilot_bundle_digest"), ("eval2", "eval2_bundle_digest")):
        bundle, digest = bundles.get(name) or ({}, None)
        if not bundle or digest != bundle.get("digest") or manifest.get(key) != digest:
            out.append("%s: the %s bundle's digest does not recompute to the manifest's" % (key, name))
            continue
        identity = bundle.get("identity") or {}
        if identity.get("verifier_sha256") != here["verifier_sha256"]:
            out.append("verifier_sha256: this checkout's verifier modules are not the ones the %s "
                       "bundle was prepared by; load_bundle would refuse it" % name)
        if identity.get("sha256") != here["engine_sha256"]:
            out.append("engine: the %s bundle's engine is not the lypning-l at the pilot's revision"
                       % name)
    experiment = here["experiment"]
    if (experiment.get("stage") != "sft" or experiment.get("checkpoint_step") != step
            or experiment.get("bundle_digest") != manifest.get("pilot_bundle_digest")
            or experiment.get("seed") != here["seed"]
            or experiment.get("revision") != here["qwen_revision"]):
        out.append("selected_step: the adapter's experiment.json is not SFT step %s of the pilot "
                   "bundle at this seed and Qwen revision" % step)
    targets = experiment.get("sft_targets") or {}
    if (targets.get("lineage") or {}).get("engine_sha256") != here["engine_sha256"]:
        out.append("engine: the adapter's targets were graded by another engine")
    return out


def bundle_digest(bundle):
    from pipeline.jsonio import sha256_of
    return sha256_of({k: v for k, v in bundle.items() if k != "digest"})


def main():
    job, step = os.environ.get("FINISH_OF", ""), os.environ.get("SFT_STEP", "")
    if not re.fullmatch(r"[0-9a-f]{24}", job) or not re.fullmatch(r"[1-9][0-9]*", step):
        print("finish preflight: FINISH_OF must be a 24-hex HF job id and SFT_STEP a positive integer",
              file=sys.stderr)
        return 2
    from huggingface_hub import HfApi, hf_hub_download
    if str(ROOT / "training") not in sys.path:
        sys.path.insert(0, str(ROOT / "training"))
    from pipeline.training import verifier_sha256
    launch = importlib.util.spec_from_file_location("launch_defaults", ROOT / "training" / "hf" / "launch.py")
    defaults = importlib.util.module_from_spec(launch)
    launch.loader.exec_module(defaults)

    token = os.environ["HF_TOKEN"].strip()
    api = HfApi(token=token)
    repo = "%s/%s" % (os.environ["OWNER"], os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))
    info = api.repo_info(repo, repo_type="dataset")
    if info.private is not True:
        raise SystemExit("refusing to read the pilot job from %s: not a private dataset repository" % repo)
    path = hf_hub_download(repo, "round-02/%s/job-manifest.json" % job, repo_type="dataset",
                           revision=info.sha, token=token)
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    files = set(api.list_repo_files(repo, repo_type="dataset", revision=info.sha))
    space, revision = os.environ["SPACE_REPO"], manifest.get("space_revision")
    engine_sha = None
    try:
        if re.fullmatch(r"[0-9a-f]{40}", revision or ""):
            engine = hf_hub_download(space, "lypning-l", repo_type="space", revision=revision, token=token)
            engine_sha = hashlib.sha256(Path(engine).read_bytes()).hexdigest()
    except Exception:                                          # noqa: BLE001
        engine_sha = None

    def private_json(name):
        """A file of the pilot job, or {} when it is not there; its content is never printed."""
        if "round-02/%s/%s" % (job, name) not in files:
            return {}
        try:
            return json.loads(Path(hf_hub_download(repo, "round-02/%s/%s" % (job, name), repo_type="dataset",
                                                   revision=info.sha, token=token)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    bundles = {}
    for name in ("pilot", "eval2"):
        bundle = private_json(name + "/bundle.json")
        bundles[name] = (bundle, bundle_digest(bundle) if bundle else None)
    here = {"job": job, "step": int(step), "space": space, "space_head": os.environ["SPACE_REV"],
            "revision_serves_engine": engine_sha is not None, "engine_sha256": engine_sha,
            "seed": int(os.environ["PILOT_SEED"]), "split_seed": int(os.environ["PILOT_SPLIT_SEED"]),
            "eval_draws": 16, "eval_sequences": defaults.DEFAULT_EVAL_SEQUENCES,
            "pool_sandboxes_per_host": defaults.DEFAULT_POOL_SANDBOXES_PER_HOST,
            "qwen_revision": os.environ["QWEN_REV"], "verifier_sha256": verifier_sha256(),
            "bundles": bundles, "experiment": private_json("sft/adapter-%s/experiment.json" % step)}
    print("== finish preflight: pilot %s status=%s last_stage=%s steps=%s rule step=%s finish step=%s "
          "space revision=%s" % (job, manifest.get("status"), manifest.get("last_stage"),
                                 manifest.get("steps"), manifest.get("sft_selected_step"), step, revision))
    found = problems(manifest, files, here, lineage_module())
    for line in found:
        print("FINISH PREFLIGHT REFUSED: %s" % line, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
