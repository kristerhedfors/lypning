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
                                   serves only the head

Prints aggregates only: the job, its status and last stage, the steps and the
revision. Reads the private dataset repo; writes nothing; exits 1 on a refusal.
"""
from __future__ import annotations

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
    return out


def main():
    job, step = os.environ.get("FINISH_OF", ""), os.environ.get("SFT_STEP", "")
    if not re.fullmatch(r"[0-9a-f]{24}", job) or not re.fullmatch(r"[1-9][0-9]*", step):
        print("finish preflight: FINISH_OF must be a 24-hex HF job id and SFT_STEP a positive integer",
              file=sys.stderr)
        return 2
    from huggingface_hub import HfApi, hf_hub_download

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
    try:
        serves = bool(re.fullmatch(r"[0-9a-f]{40}", revision or "")) and api.file_exists(
            space, "lypning-l", repo_type="space", revision=revision)
    except Exception:                                          # noqa: BLE001
        serves = False
    here = {"job": job, "step": int(step), "space": space, "space_head": os.environ["SPACE_REV"],
            "revision_serves_engine": serves}
    print("== finish preflight: pilot %s status=%s last_stage=%s steps=%s rule step=%s finish step=%s "
          "space revision=%s" % (job, manifest.get("status"), manifest.get("last_stage"),
                                 manifest.get("steps"), manifest.get("sft_selected_step"), step, revision))
    found = problems(manifest, files, here, lineage_module())
    for line in found:
        print("FINISH PREFLIGHT REFUSED: %s" % line, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
