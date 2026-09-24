#!/usr/bin/env bash
# Round-02 split eval-2 on a Hugging Face Job. Runs INSIDE the job, from a fresh clone.
#
# The second half of a pilot launched with EVAL2_MODE=separate. That job ran
# everything up to and including the test split, sealed its SFT selection, and
# wrote eval2-deferred.json instead of step 7g. This job downloads that
# selection and that job's eval-2 bundle from the private artifact repo,
# refuses unless every identity field matches (`split_eval2.py` names the one
# that does not), and then runs 7g EXACTLY as `round02_pilot.sh` does -- the
# same commands, the same flags, the same step-0 reuse rule -- and the eval-2
# report. Its manifest names the pilot job (`eval2_of`), and `arm_check` reads
# the two manifests as one seed.
#
# Inputs (environment, set by training/hf/launch.py):
#   SPACE_REPO, SPACE_REV, QWEN_REV, WORK_REPO, HF_TOKEN   as for the pilot
#   EVAL2_OF       the completed pilot job id, 24 lower-case hex characters
#   EVAL_DRAWS, SEED, EVAL_SEQUENCES, SCORE_WORKERS,
#   NTX_POOL_SANDBOXES_PER_HOST, NTX_POOL_MAX_HOSTS     as for the pilot; the
#                  arm fields among them must equal the pilot job's
set -euo pipefail
: "${SPACE_REPO:?}" "${SPACE_REV:?}" "${QWEN_REV:?}" "${WORK_REPO:?}" "${EVAL2_OF:?}" "${HF_TOKEN:?}"
# One job directory and nothing else: matched on the WHOLE value, as the
# workflow matches bundles_from, so a multi-line value cannot pass on one line.
if ! [[ "$EVAL2_OF" =~ ^[0-9a-f]{24}$ ]]; then
  echo "== EVAL2_OF must be a 24-hex HF job id"; exit 2
fi
EVAL_DRAWS="${EVAL_DRAWS:-16}"
SEED="${SEED:-1111}"
EVAL_SEQUENCES="${EVAL_SEQUENCES:-256}"
SCORE_WORKERS="${SCORE_WORKERS:-16}"
export NTX_POOL_SANDBOXES_PER_HOST="${NTX_POOL_SANDBOXES_PER_HOST:-4}"
export NTX_POOL_MAX_HOSTS="${NTX_POOL_MAX_HOSTS:-4}"
export EVAL2_OF EVAL_DRAWS SEED EVAL_SEQUENCES SCORE_WORKERS
cd "$(dirname "$0")/../.."
export PYTHONPATH=src:training LYPNING_CAPTURE=0 LYPNING_HARVEST=0 PIP_DISABLE_PIP_VERSION_CHECK=1
ROUND=work/round-02
JOB="${JOB_ID:-local}"
export NTX_POOL_TAG="$JOB"   # this run's sandbox pool is its own; see hf_sandbox_runner.pool_name
STAGE=start
mkdir -p "$ROUND"
echo "== round-02 split eval-2 on $(hostname) job=$JOB commit=$(git rev-parse HEAD) eval2_of=$EVAL2_OF eval_draws=$EVAL_DRAWS eval_sequences=$EVAL_SEQUENCES score_workers=$SCORE_WORKERS pool_sandboxes_per_host=$NTX_POOL_SANDBOXES_PER_HOST pool_max_hosts=$NTX_POOL_MAX_HOSTS seed=$SEED"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo "== no GPU visible"

run() {
  echo "== $*"
  "$@"
}

# Incremental, as in the pilot: a cancelled job runs no EXIT trap.
checkpoint() {
  echo "== checkpoint after $STAGE"
  STAGE_DONE="$STAGE" python3 - <<'PYEOF' || echo "== checkpoint upload failed after $STAGE (continuing)"
import os
from huggingface_hub import HfApi
# The pilot job's bundle and adapter are already in its own directory, and
# lineage.json names them by digest; this job uploads what it produced.
UPLOAD_IGNORE = ["pilot-download/**", "eval2/**", "sft/**"]
api = HfApi()
if api.repo_info(os.environ["WORK_REPO"], repo_type="dataset").private is not True:
    raise SystemExit("refusing to upload: %s is not a private dataset repository" % os.environ["WORK_REPO"])
job = os.environ.get("JOB_ID", "local")
api.upload_folder(folder_path="work/round-02", repo_id=os.environ["WORK_REPO"], repo_type="dataset",
                  path_in_repo="round-02/" + job, ignore_patterns=UPLOAD_IGNORE,
                  commit_message="round-02 split eval-2 job %s: checkpoint after %s" % (job, os.environ["STAGE_DONE"]))
print("== checkpoint uploaded after", os.environ["STAGE_DONE"])
PYEOF
}

# The manifest names the pilot job, and copies the pilot's pair fields from
# the verified lineage so `arm_check` can see the two jobs agree.
finish() {
  local code=$1
  trap - EXIT
  local status=complete
  [ "$code" -eq 0 ] || status=failed
  echo "== finish: status=$status stage=$STAGE exit=$code"
  if ! STATUS="$status" EXIT_CODE="$code" STAGE="$STAGE" python3 - <<'PYEOF'
import json, os, subprocess
from huggingface_hub import HfApi
from pipeline.public_view import public_view
UPLOAD_IGNORE = ["pilot-download/**", "eval2/**", "sft/**"]
api = HfApi()
job = os.environ.get("JOB_ID", "local")
def read(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}
lineage = read("work/round-02/lineage.json")
manifest = {"job": job, "kind": "eval2", "eval2_of": os.environ["EVAL2_OF"],
            "status": os.environ["STATUS"], "exit_code": int(os.environ["EXIT_CODE"]),
            "last_stage": os.environ["STAGE"],
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "pilot_commit": lineage.get("pilot_commit"),
            "space": os.environ["SPACE_REPO"], "flavor": os.environ.get("ACCELERATOR", ""),
            "score_workers": int(os.environ["SCORE_WORKERS"]),
            "pool_max_hosts": int(os.environ["NTX_POOL_MAX_HOSTS"]),
            "sft_selected_step": lineage.get("sft_selected_step"),
            "eval2_bundle_digest": lineage.get("eval2_bundle_digest"),
            "pilot_bundle_digest": lineage.get("pilot_bundle_digest"),
            "adapter_seal_sha256": lineage.get("adapter_seal_sha256"),
            "code_sha256_digest": lineage.get("code_sha256_digest"),
            "lineage_verified": bool(lineage)}
manifest.update(lineage.get("pair") or {
    "seed": int(os.environ["SEED"]), "eval_draws": int(os.environ["EVAL_DRAWS"]),
    "eval_sequences": int(os.environ["EVAL_SEQUENCES"]),
    "pool_sandboxes_per_host": int(os.environ["NTX_POOL_SANDBOXES_PER_HOST"]),
    "space_revision": os.environ["SPACE_REV"], "qwen_revision": os.environ["QWEN_REV"]})
json.dump(manifest, open("work/round-02/job-manifest.json", "w"), indent=2)
print("== manifest:", json.dumps(public_view(manifest)))
info = api.repo_info(os.environ["WORK_REPO"], repo_type="dataset")
if info.private is not True:
    raise SystemExit("refusing to upload: %s is not a private dataset repository" % os.environ["WORK_REPO"])
api.upload_folder(folder_path="work/round-02", repo_id=os.environ["WORK_REPO"], repo_type="dataset",
                  path_in_repo="round-02/" + job, ignore_patterns=UPLOAD_IGNORE,
                  commit_message="round-02 split eval-2 from job %s of %s (%s)"
                                 % (job, os.environ["EVAL2_OF"], os.environ["STATUS"]))
print("== uploaded work/round-02 to", os.environ["WORK_REPO"], "under round-02/" + job)
PYEOF
  then
    echo "== upload failed"
    [ "$code" -eq 0 ] && code=1
  fi
  echo "== round-02 split eval-2: $status"
  exit "$code"
}
trap 'finish $?' EXIT
trap 'exit 124' TERM
trap 'exit 130' INT

# 1. The pilot's own pinned dependencies and kernel question.
STAGE=deps
echo "== install pinned dependencies from training/gpu/train_verified.py"
bash training/hf/pinned_deps.sh

# 2. The engine: the same bytes the verifier image carries, from the same commit.
STAGE=engine
echo "== download lypning-l from $SPACE_REPO at $SPACE_REV"
mkdir -p "$ROUND/engine-home/bin"
python3 - <<'PYEOF'
import os, shutil
from huggingface_hub import hf_hub_download
p = hf_hub_download(os.environ["SPACE_REPO"], "lypning-l", repo_type="space", revision=os.environ["SPACE_REV"])
shutil.copy(p, "work/round-02/engine-home/bin/lypning-l")
os.chmod("work/round-02/engine-home/bin/lypning-l", 0o755)
PYEOF
export LYPNING_L_BIN="$PWD/$ROUND/engine-home/bin/lypning-l"
run "$LYPNING_L_BIN" --version
run sha256sum "$LYPNING_L_BIN"

# 3. The pilot job's sealed selection and eval-2 bundle, from the private repo
#    at one revision; then every identity field, before a single draw.
STAGE=lineage
echo "== download the sealed SFT selection and eval-2 bundle of round-02/$EVAL2_OF"
python3 - <<'PYEOF'
import json, os
from huggingface_hub import HfApi, snapshot_download
repo, job = os.environ["WORK_REPO"], os.environ["EVAL2_OF"]
api = HfApi()
info = api.repo_info(repo, repo_type="dataset")
if info.private is not True:
    raise SystemExit("refusing to read the pilot job from %s: not a private dataset repository" % repo)
src = "round-02/" + job
local = snapshot_download(repo, repo_type="dataset", revision=info.sha, local_dir="work/round-02/pilot-download",
                          allow_patterns=[src + "/job-manifest.json", src + "/eval2-deferred.json",
                                          src + "/sft/best.json", src + "/eval2/**"])
best = os.path.join(local, src, "sft", "best.json")
if not os.path.isfile(best):
    raise SystemExit("round-02/%s has no sft/best.json" % job)
step = json.load(open(best))["step"]
if not isinstance(step, int):
    raise SystemExit("sft/best.json names no integer step")
snapshot_download(repo, repo_type="dataset", revision=info.sha, local_dir="work/round-02/pilot-download",
                  allow_patterns=["%s/sft/adapter-%d/**" % (src, step)])
print("== downloaded round-02/%s at dataset revision %s: selected step %d" % (job, info.sha[:12], step))
PYEOF
run python3 training/hf/split_eval2.py --pilot "$ROUND/pilot-download/round-02/$EVAL2_OF" \
  --job "$EVAL2_OF" --engine "$LYPNING_L_BIN" --round "$ROUND"
SFT_STEP=$(python3 -c 'import json; print(json.load(open("work/round-02/lineage.json"))["sft_selected_step"])')
checkpoint

# 4. Step 7g of round02_pilot.sh, the same commands with the same flags.
TV=(python3 training/gpu/train_verified.py)
COMMON=(--isolated-worker --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" --seed "$SEED"
        --eval-sequences "$EVAL_SEQUENCES" --score-workers "$SCORE_WORKERS")
EVAL2="$ROUND/eval2/bundle.json"
SFT_ADAPTER="$ROUND/sft/adapter-$SFT_STEP"
[ -f "$SFT_ADAPTER/seal.json" ] || { echo "== selected adapter has no seal.json"; exit 1; }
STAGE=eval2
run "${TV[@]}" eval --eval-split all --eval-draws "$EVAL_DRAWS" --bundle "$EVAL2" --output "$ROUND/base-eval2" "${COMMON[@]}"
checkpoint
run "${TV[@]}" eval --eval-split all --eval-draws "$EVAL_DRAWS" --adapter "$SFT_ADAPTER" --reuse-evaluation "$ROUND/base-eval2" --bundle "$EVAL2" --output "$ROUND/sft-eval2" "${COMMON[@]}"
checkpoint

# 5. The eval-2 comparison; the test comparison is in the pilot job.
STAGE=report
mkdir -p "$ROUND/reports"
echo "== python3 -m pipeline.training_report $ROUND/base-eval2 $ROUND/sft-eval2 > $ROUND/reports/base-vs-sft-eval2.json"
python3 -m pipeline.training_report "$ROUND/base-eval2" "$ROUND/sft-eval2" > "$ROUND/reports/base-vs-sft-eval2.json"
python3 -c 'import json, sys; from pipeline.public_view import public_view; r = json.load(open(sys.argv[1])); print("== report", sys.argv[1], json.dumps(public_view(r["paired"])), "engine_mismatches", json.dumps(public_view(r.get("engine_mismatches"))))' "$ROUND/reports/base-vs-sft-eval2.json"

STAGE=done
echo "== round-02 split eval-2: all stages ran; uploading"
