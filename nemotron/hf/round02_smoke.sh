#!/usr/bin/env bash
# Round-02 smoke on a Hugging Face Job. Runs INSIDE the job, from a fresh clone.
#
# What this proves and what it does not. It proves the round's plumbing on the
# Hugging Face boundary: the trainer job and the verifier image share one
# CPython base digest, the identity handshake passes, the starter bundle
# verifies its references through pooled sandboxes, the tiny-model SFT and
# GRPO stages run two real trainer steps each on a real GPU, and the planner
# reads the sealed result. It is smoke-only: the starter curriculum is not a
# pilot dataset, and no model-quality claim follows from a tiny random model.
#
# Inputs (environment, set by nemotron/hf/launch.py):
#   SPACE_REPO   the verifier Space, e.g. headforce/lypning-round02-verifier
#   SPACE_REV    its immutable 40-character commit (the image and the engine)
#   QWEN_REV     the approved immutable Qwen/Qwen3.8-27B Hub commit
#   WORK_REPO    private dataset repo that receives work/round-02 afterwards
#   HF_TOKEN     job secret; the trainer holds it, candidates never see it
set -euo pipefail
: "${SPACE_REPO:?}" "${SPACE_REV:?}" "${QWEN_REV:?}" "${WORK_REPO:?}" "${HF_TOKEN:?}"
cd "$(dirname "$0")/../.."
export PYTHONPATH=src:nemotron LYPNING_CAPTURE=0 LYPNING_HARVEST=0 PIP_DISABLE_PIP_VERSION_CHECK=1
ROUND=work/round-02
JOB="${JOB_ID:-local}"
echo "== round-02 smoke on $(hostname) job=$JOB commit=$(git rev-parse HEAD)"
echo "== python: $(python3 -c 'import sys; print(sys.version)')"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo "== no GPU visible"

# 1. The GPU script's own pinned dependencies are the single source of truth.
python3 - <<'EOF' | xargs -r pip install -q --no-cache-dir
import re
head = open("nemotron/gpu/train_verified.py").read().split("# ///")[1]
print(" ".join(re.findall(r'"([^"]+)"', head.split("dependencies")[1])))
EOF
python3 -c 'import torch, transformers, peft, trl, huggingface_hub; print("== torch", torch.__version__, "cuda", torch.cuda.is_available(), "| transformers", transformers.__version__, "| trl", trl.__version__, "| hub", huggingface_hub.__version__)'

# 2. The engine: the same bytes the verifier image carries, from the same commit.
mkdir -p "$ROUND/engine-home/bin"
python3 - <<'EOF'
import os, shutil
from huggingface_hub import hf_hub_download
p = hf_hub_download(os.environ["SPACE_REPO"], "lypning-l", repo_type="space", revision=os.environ["SPACE_REV"])
shutil.copy(p, "work/round-02/engine-home/bin/lypning-l")
os.chmod("work/round-02/engine-home/bin/lypning-l", 0o755)
EOF
export LYPNING_L_BIN="$PWD/$ROUND/engine-home/bin/lypning-l"
"$LYPNING_L_BIN" --version
sha256sum "$LYPNING_L_BIN"

# 3. Identity handshake only: no candidate execution, no GPU imports.
python3 -c 'import os; from pipeline.training import engine_identity; from pipeline.hf_sandbox_runner import HfSandboxPoolRunner; r = HfSandboxPoolRunner("hf.co/spaces/" + os.environ["SPACE_REPO"], os.environ["SPACE_REV"], engine_identity(os.environ["LYPNING_L_BIN"])); r.close(); print("== identity handshake: ok")'

# 4. The authored starter, verified through the pool; smoke purpose only.
python3 -m pipeline.cli training-prepare --starter --engine "$LYPNING_L_BIN" \
  --execution-kind hf-sandbox-pool --execution-image "hf.co/$SPACE_REPO" \
  --execution-revision "$SPACE_REV" --output "$ROUND/smoke"

# 5. Plan first, then the two tiny-model stages on the real GPU.
python3 nemotron/gpu/train_verified.py sft --plan --smoke --isolated-worker \
  --bundle "$ROUND/smoke/bundle.json" --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" \
  --output "$ROUND/sft-smoke-plan"
python3 nemotron/gpu/train_verified.py sft --smoke --isolated-worker \
  --bundle "$ROUND/smoke/bundle.json" --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" \
  --output "$ROUND/sft-smoke"
python3 nemotron/gpu/train_verified.py grpo --smoke --from-base --isolated-worker \
  --bundle "$ROUND/smoke/bundle.json" --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" \
  --output "$ROUND/grpo-smoke"

# 6. The portable plan, with the approved revision; it emits commands, runs none.
python3 - <<'EOF'
import json, os
cfg = json.load(open("nemotron/round-02.example.json"))
cfg["revision"] = os.environ["QWEN_REV"]
json.dump(cfg, open("work/round-02/config.json", "w"), indent=2)
EOF
python3 -m pipeline.round_plan --config "$ROUND/config.json" --output "$ROUND/plan-001.json" > /dev/null
python3 -c 'import json; p = json.load(open("work/round-02/plan-001.json")); print("== plan actions:", [a["name"] for a in p["actions"]], "| pending:", p["pending_selection"])'

# 7. Hand the round directory to the private artifact repo; the manifest is last.
python3 - <<'EOF'
import json, os, subprocess
from huggingface_hub import HfApi
api = HfApi()
job = os.environ.get("JOB_ID", "local")
manifest = {"job": job, "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "space": os.environ["SPACE_REPO"], "space_revision": os.environ["SPACE_REV"],
            "qwen_revision": os.environ["QWEN_REV"], "flavor": os.environ.get("ACCELERATOR", ""),
            "bundle_digest": json.load(open("work/round-02/smoke/bundle.json"))["digest"]}
json.dump(manifest, open("work/round-02/job-manifest.json", "w"), indent=2)
api.upload_folder(folder_path="work/round-02", repo_id=os.environ["WORK_REPO"], repo_type="dataset",
                  path_in_repo="round-02/" + job, commit_message="round-02 smoke from job " + job)
print("== uploaded work/round-02 to", os.environ["WORK_REPO"], "under round-02/" + job)
EOF
echo "== round-02 smoke: complete"
