#!/usr/bin/env bash
# Round-02 hardware smoke on a Hugging Face Job. Runs INSIDE the job, from a fresh clone.
#
# What the pilot's clock is made of, measured on the pilot's own GPU before the
# pilot is billed: `training/hf/hwsmoke.py` loads Qwen/Qwen3.8-27B at QWEN_REV
# through `train_verified`'s own loaders (torch-reference kernel enforced, LoRA
# r16), times one 256-sequence and one 128-sequence `generate` call with the
# pilot's decoding, one 256 call forced to max_new_tokens, and SFT steps at
# batch 4 through `train_sft` at two row lengths, with peak CUDA memory for
# each; then projects the approved arm-A job over those numbers
# (`training/hf/projection.py`). hwsmoke.json is saved before every
# measurement, so a failure or this job's timeout leaves what was measured. No bank is downloaded and no verifier Space is
# used: the prompts are the public starter tasks, and nothing case-level
# exists to print. hwsmoke.json goes to the private work repo under
# round-02/<job>/hwsmoke/ and, through `public_view`, to this log.
#
# Inputs (environment, set by training/hf/launch.py):
#   QWEN_REV     the approved immutable Qwen/Qwen3.8-27B Hub commit
#   WORK_REPO    private dataset repo that receives work/hwsmoke
#   HF_TOKEN     job secret
#   SPACE_REPO, SPACE_REV   recorded only; the smoke runs no program
set -euo pipefail
: "${QWEN_REV:?}" "${WORK_REPO:?}" "${HF_TOKEN:?}"
cd "$(dirname "$0")/../.."
export PYTHONPATH=src:training LYPNING_CAPTURE=0 LYPNING_HARVEST=0 PIP_DISABLE_PIP_VERSION_CHECK=1
OUT=work/hwsmoke
JOB="${JOB_ID:-local}"
STAGE=start
echo "== round-02 hardware smoke on $(hostname) job=$JOB commit=$(git rev-parse HEAD) qwen_revision=$QWEN_REV"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo "== no GPU visible"

# Whatever happened, print the aggregates and hand them to the private repo.
finish() {
  local code=$1
  trap - EXIT
  echo "== finish: stage=$STAGE exit=$code"
  if [ -f "$OUT/hwsmoke.json" ]; then
    if ! python3 - <<'PYEOF'
import json, os
from huggingface_hub import HfApi
from pipeline.public_view import public_view
report = json.load(open("work/hwsmoke/hwsmoke.json"))
print("== hwsmoke:", json.dumps(public_view(report)))
api = HfApi()
if api.repo_info(os.environ["WORK_REPO"], repo_type="dataset").private is not True:
    raise SystemExit("refusing to upload: %s is not a private dataset repository" % os.environ["WORK_REPO"])
job = os.environ.get("JOB_ID", "local")
api.upload_folder(folder_path="work/hwsmoke", repo_id=os.environ["WORK_REPO"], repo_type="dataset",
                  path_in_repo="round-02/%s/hwsmoke" % job,
                  commit_message="round-02 hardware smoke from job %s (%s)" % (job, report.get("status")))
print("== uploaded work/hwsmoke to", os.environ["WORK_REPO"], "under round-02/%s/hwsmoke" % job)
PYEOF
    then
      echo "== upload failed"
      [ "$code" -eq 0 ] && code=1
    fi
  else
    echo "== no hwsmoke.json was written"
  fi
  echo "== round-02 hardware smoke: exit $code"
  exit "$code"
}
trap 'finish $?' EXIT
trap 'exit 124' TERM
trap 'exit 130' INT

# 1. The pilot's pinned dependencies and its minute-one kernel question.
STAGE=deps
echo "== install pinned dependencies from training/gpu/train_verified.py"
bash training/hf/pinned_deps.sh

# 2. Load as the pilot loads, measure, project.
STAGE=measure
echo "== python3 training/hf/hwsmoke.py --revision $QWEN_REV --output $OUT"
python3 training/hf/hwsmoke.py --revision "$QWEN_REV" --output "$OUT"
STAGE=done
