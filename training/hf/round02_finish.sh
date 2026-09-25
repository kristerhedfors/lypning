#!/usr/bin/env bash
# Round-02 finish on a Hugging Face Job. Runs INSIDE the job, from a fresh clone.
#
# Finishes a pilot job that completed SFT and then died, from the adapters it
# saved, instead of training again. Seed 1111's arm-A pilot (HF job
# 6ab52a686b030d633f68e503) completed SFT and died in its base test arm on one
# engine mismatch (2026-09-24), which #126 now counts instead of aborting on.
# This job downloads that pilot's bundles, its SFT selection record and ONE
# sealed adapter from the private artifact repo, fetches the engine from the
# verifier Space at the PILOT'S recorded revision, refuses unless every
# identity field matches (`finish_lineage.py` names the one that does not),
# and then runs the pilot's steps 7f and 7g with that adapter -- the same
# commands, the same flags, the same reuse rules, the GRPO arm absent -- and
# both reports. Its manifest names the pilot job (`finish_of`), the step it
# evaluated, the step the pilot's rule selected and the dated amendment that
# overrides it, and the commits between the pilot's code and this one
# (`finish_lineage`); `arm_check` reads the two manifests as one seed.
#
# Inputs (environment, set by training/hf/launch.py):
#   SPACE_REPO, QWEN_REV, WORK_REPO, HF_TOKEN   as for the pilot
#   SPACE_REV      the Space revision bootstrap resolved at dispatch; recorded,
#                  never used: the engine and the pool are the PILOT'S revision
#   FINISH_OF      the pilot job id, 24 lower-case hex characters
#   SFT_STEP       the SFT checkpoint to evaluate; a step other than the rule's
#                  must be registered in finish_lineage.OVERRIDES
#   EVAL_DRAWS, SEED, SPLIT_SEED, EVAL_SEQUENCES, SCORE_WORKERS,
#   NTX_POOL_SANDBOXES_PER_HOST, NTX_POOL_MAX_HOSTS   as for the pilot; the arm
#                  fields among them must equal the pilot job's
set -euo pipefail
: "${SPACE_REPO:?}" "${SPACE_REV:?}" "${QWEN_REV:?}" "${WORK_REPO:?}" "${FINISH_OF:?}" "${SFT_STEP:?}" "${HF_TOKEN:?}"
# One job directory and one step, each matched on the WHOLE value, as the
# workflow matches them, so a multi-line value cannot pass on one line.
if ! [[ "$FINISH_OF" =~ ^[0-9a-f]{24}$ ]]; then
  echo "== FINISH_OF must be a 24-hex HF job id"; exit 2
fi
if ! [[ "$SFT_STEP" =~ ^[1-9][0-9]*$ ]]; then
  echo "== SFT_STEP must be a positive integer"; exit 2
fi
EVAL_DRAWS="${EVAL_DRAWS:-16}"
SEED="${SEED:-1111}"
SPLIT_SEED="${SPLIT_SEED:-1111}"
EVAL_SEQUENCES="${EVAL_SEQUENCES:-256}"
SCORE_WORKERS="${SCORE_WORKERS:-16}"
export NTX_POOL_SANDBOXES_PER_HOST="${NTX_POOL_SANDBOXES_PER_HOST:-4}"
export NTX_POOL_MAX_HOSTS="${NTX_POOL_MAX_HOSTS:-4}"
export DISPATCH_SPACE_REV="$SPACE_REV" PILOT_SPACE_REV=""
export FINISH_OF SFT_STEP EVAL_DRAWS SEED SPLIT_SEED EVAL_SEQUENCES SCORE_WORKERS
cd "$(dirname "$0")/../.."
export PYTHONPATH=src:training LYPNING_CAPTURE=0 LYPNING_HARVEST=0 PIP_DISABLE_PIP_VERSION_CHECK=1
# Fragmentation, not demand, decided the last 0.13 GiB of the 2026-09-25 OOM
# (2.62 GiB asked, 2.49 free). The allocator setting changes no result.
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
ROUND=work/round-02
JOB="${JOB_ID:-local}"
export NTX_POOL_TAG="$JOB"   # this run's sandbox pool is its own; see hf_sandbox_runner.pool_name
STAGE=start
mkdir -p "$ROUND"
echo "== round-02 finish on $(hostname) job=$JOB commit=$(git rev-parse HEAD) finish_of=$FINISH_OF sft_step=$SFT_STEP eval_draws=$EVAL_DRAWS eval_sequences=$EVAL_SEQUENCES score_workers=$SCORE_WORKERS pool_sandboxes_per_host=$NTX_POOL_SANDBOXES_PER_HOST pool_max_hosts=$NTX_POOL_MAX_HOSTS seed=$SEED split_seed=$SPLIT_SEED dispatch_space_rev=$DISPATCH_SPACE_REV"
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
# The pilot job's bundles and adapter are already in its own directory, and
# lineage.json names them by digest; this job uploads what it produced.
UPLOAD_IGNORE = ["pilot-download/**", "pilot/**", "eval2/**", "sft/**"]
api = HfApi()
if api.repo_info(os.environ["WORK_REPO"], repo_type="dataset").private is not True:
    raise SystemExit("refusing to upload: %s is not a private dataset repository" % os.environ["WORK_REPO"])
job = os.environ.get("JOB_ID", "local")
api.upload_folder(folder_path="work/round-02", repo_id=os.environ["WORK_REPO"], repo_type="dataset",
                  path_in_repo="round-02/" + job, ignore_patterns=UPLOAD_IGNORE,
                  commit_message="round-02 finish job %s: checkpoint after %s" % (job, os.environ["STAGE_DONE"]))
print("== checkpoint uploaded after", os.environ["STAGE_DONE"])
PYEOF
}

# The manifest names the pilot job and copies the pilot's pair fields from the
# verified lineage, so `arm_check` can see the two jobs agree.
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
UPLOAD_IGNORE = ["pilot-download/**", "pilot/**", "eval2/**", "sft/**"]
api = HfApi()
job = os.environ.get("JOB_ID", "local")
def read(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}
lineage = read("work/round-02/lineage.json")
test_args = read("work/round-02/base-test/experiment.json").get("args") or {}
manifest = {"job": job, "kind": "finish", "stage": "finish", "finish_of": os.environ["FINISH_OF"],
            "status": os.environ["STATUS"], "exit_code": int(os.environ["EXIT_CODE"]),
            "last_stage": os.environ["STAGE"],
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "pilot_commit": lineage.get("pilot_commit"),
            "space": os.environ["SPACE_REPO"], "flavor": os.environ.get("ACCELERATOR", ""),
            # Recorded, never used: the engine and the pool ran at the pilot's.
            "dispatch_space_revision": os.environ["DISPATCH_SPACE_REV"],
            "score_workers": int(os.environ["SCORE_WORKERS"]),
            "pool_max_hosts": int(os.environ["NTX_POOL_MAX_HOSTS"]),
            "selected_step": lineage.get("selected_step", int(os.environ["SFT_STEP"])),
            "rule_selected_step": lineage.get("rule_selected_step"),
            "rule_version": lineage.get("rule_version"),
            "selection_override": lineage.get("selection_override"),
            "reselection": lineage.get("reselection"),
            "finish_lineage": lineage.get("finish_lineage"),
            "test_eval_draws": test_args.get("eval_draws"),
            "pilot_bundle_digest": lineage.get("pilot_bundle_digest"),
            "eval2_bundle_digest": lineage.get("eval2_bundle_digest"),
            "adapter_seal_sha256": lineage.get("adapter_seal_sha256"),
            "engine_sha256": lineage.get("engine_sha256"),
            "verifier_sha256": lineage.get("verifier_sha256"),
            "lineage_verified": bool(lineage)}
# The pair fields are what THIS job ran, never copies of the pilot's: `arm_check`
# compares them with the pilot manifest, and a copy would agree by construction.
# `bank_path` alone is the pilot's (a finish reads no bank); the space revision
# is the one the engine and the pool used, None before it was read.
manifest.update({
    "seed": int(os.environ["SEED"]), "split_seed": int(os.environ["SPLIT_SEED"]),
    "eval_draws": int(os.environ["EVAL_DRAWS"]), "eval_sequences": int(os.environ["EVAL_SEQUENCES"]),
    "pool_sandboxes_per_host": int(os.environ["NTX_POOL_SANDBOXES_PER_HOST"]),
    "space_revision": os.environ.get("PILOT_SPACE_REV") or None, "qwen_revision": os.environ["QWEN_REV"],
    "bank_path": (lineage.get("pair") or {}).get("bank_path")})
json.dump(manifest, open("work/round-02/job-manifest.json", "w"), indent=2)
print("== manifest:", json.dumps(public_view(manifest)))
info = api.repo_info(os.environ["WORK_REPO"], repo_type="dataset")
if info.private is not True:
    raise SystemExit("refusing to upload: %s is not a private dataset repository" % os.environ["WORK_REPO"])
api.upload_folder(folder_path="work/round-02", repo_id=os.environ["WORK_REPO"], repo_type="dataset",
                  path_in_repo="round-02/" + job, ignore_patterns=UPLOAD_IGNORE,
                  commit_message="round-02 finish from job %s of %s (%s)"
                                 % (job, os.environ["FINISH_OF"], os.environ["STATUS"]))
print("== uploaded work/round-02 to", os.environ["WORK_REPO"], "under round-02/" + job)
PYEOF
  then
    echo "== upload failed"
    [ "$code" -eq 0 ] && code=1
  fi
  echo "== round-02 finish: $status"
  exit "$code"
}
trap 'finish $?' EXIT
trap 'exit 124' TERM
trap 'exit 130' INT

# 1. The pilot's own pinned dependencies and kernel question.
STAGE=deps
echo "== install pinned dependencies from training/gpu/train_verified.py"
bash training/hf/pinned_deps.sh

# 2. The pilot job's record, bundles and ONE sealed adapter, from the private
#    repo only, at one dataset revision. Nothing is prepared or trained here.
STAGE=pilot-download
echo "== download round-02/$FINISH_OF: manifest, SFT selection, adapter-$SFT_STEP, both bundles"
python3 - <<'PYEOF'
import json, os, re
from huggingface_hub import HfApi, snapshot_download
repo, job, step = os.environ["WORK_REPO"], os.environ["FINISH_OF"], int(os.environ["SFT_STEP"])
api = HfApi()
info = api.repo_info(repo, repo_type="dataset")
if info.private is not True:
    raise SystemExit("refusing to read the pilot job from %s: not a private dataset repository" % repo)
src = "round-02/" + job
local = snapshot_download(repo, repo_type="dataset", revision=info.sha, local_dir="work/round-02/pilot-download",
                          allow_patterns=[src + "/job-manifest.json", src + "/sft/best.json",
                                          src + "/sft/evaluations.jsonl",
                                          "%s/sft/adapter-%d/**" % (src, step),
                                          src + "/pilot/bundle.json", src + "/eval2/bundle.json"])
manifest = os.path.join(local, src, "job-manifest.json")
if not os.path.isfile(manifest):
    raise SystemExit("round-02/%s has no job-manifest.json" % job)
revision = json.load(open(manifest)).get("space_revision")
if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
    raise SystemExit("space_revision: the pilot job recorded no 40-character Space revision")
open("work/round-02/pilot-space-revision", "w").write(revision)
print("== downloaded round-02/%s at dataset revision %s; pilot Space revision %s"
      % (job, info.sha[:12], revision[:12]))
PYEOF
PILOT_SPACE_REV=$(cat "$ROUND/pilot-space-revision")
export PILOT_SPACE_REV

# 3. The engine: the bytes the PILOT's verifier image carried, from the pilot's
#    Space revision -- never the head. A revision that can no longer be fetched
#    is a refusal, not a fallback. The Space's current head is read too: the
#    pool serves only the head, and `finish_lineage` refuses when it moved.
STAGE=engine
echo "== download lypning-l from $SPACE_REPO at the pilot's revision $PILOT_SPACE_REV"
mkdir -p "$ROUND/engine-home/bin"
python3 - <<'PYEOF'
import os, shutil
from huggingface_hub import HfApi, hf_hub_download
repo, revision = os.environ["SPACE_REPO"], os.environ["PILOT_SPACE_REV"]
try:
    p = hf_hub_download(repo, "lypning-l", repo_type="space", revision=revision)
except Exception as exc:                                   # noqa: BLE001
    raise SystemExit("space_revision: the pilot's revision %s of %s is no longer fetchable (%s); "
                     "refusing rather than using the head" % (revision, repo, type(exc).__name__))
shutil.copy(p, "work/round-02/engine-home/bin/lypning-l")
os.chmod("work/round-02/engine-home/bin/lypning-l", 0o755)
open("work/round-02/space-head", "w").write(HfApi().repo_info(repo, repo_type="space").sha)
PYEOF
export LYPNING_L_BIN="$PWD/$ROUND/engine-home/bin/lypning-l"
run "$LYPNING_L_BIN" --version
run sha256sum "$LYPNING_L_BIN"
SPACE_HEAD=$(cat "$ROUND/space-head")
export SPACE_HEAD
# From here on the Space revision IS the pilot's: the handshake, the pool and
# every bundle's execution record name it.
export SPACE_REV="$PILOT_SPACE_REV"

# 4. Every identity field, before the handshake and long before a weight load.
STAGE=lineage
run python3 training/hf/finish_lineage.py --pilot "$ROUND/pilot-download/round-02/$FINISH_OF" \
  --job "$FINISH_OF" --step "$SFT_STEP" --engine "$LYPNING_L_BIN" --round "$ROUND"
checkpoint

# 5. Identity handshake, then authored execution witnesses through the pool,
#    as in the pilot, logged to the round directory: no GPU imports.
STAGE=handshake
echo "== identity handshake and execution witnesses through hf.co/spaces/$SPACE_REPO at $SPACE_REV"
python3 - <<'PYEOF'
import json, os
from pipeline.public_view import public_view
from pipeline.training import engine_identity
from pipeline.hf_sandbox_runner import HfSandboxPoolRunner
r = HfSandboxPoolRunner("hf.co/spaces/" + os.environ["SPACE_REPO"], os.environ["SPACE_REV"],
                        engine_identity(os.environ["LYPNING_L_BIN"]))
print("== identity handshake: ok")
witnesses = [("cpython-argv", "import sys\nprint(sum(int(x) for x in sys.argv[1:]))", dict(argv=["1", "2"])),
             ("native-file", "print(open('in.txt').read())", dict(files={"in.txt": "seeded"}, interpreter=["native"])),
             ("timeout", "while True: pass", dict(timeout_s=1)),
             ("no-token", "import os; print(os.getuid() >= 20000, os.environ.get('HF_TOKEN'))", {})]
with open("work/round-02/execution-witnesses.jsonl", "w") as log:
    for name, program, kw in witnesses:
        kw.setdefault("timeout_s", 5); kw.setdefault("mem_mb", 256)
        res = r(program, **kw)
        row = dict(witness=name, program=program, exit_code=res.exit_code, stdout=res.stdout,
                   timed_out=res.timed_out, harness_error=res.harness_error)
        log.write(json.dumps(row) + "\n")
        print("== execution witness:", json.dumps(public_view(row)))
r.close()
PYEOF

# 6. Steps 7f and 7g of round02_pilot.sh with the verified adapter: the same
#    commands with the same flags, one stage and one checkpoint per arm. Arm A
#    ran no GRPO, so there is no third arm.
TV=(python3 training/gpu/train_verified.py)
COMMON=(--isolated-worker --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" --seed "$SEED"
        --eval-sequences "$EVAL_SEQUENCES" --score-workers "$SCORE_WORKERS")
PILOT="$ROUND/pilot/bundle.json"
EVAL2="$ROUND/eval2/bundle.json"
SFT_ADAPTER="$ROUND/sft/adapter-$SFT_STEP"
[ -f "$SFT_ADAPTER/seal.json" ] || { echo "== selected adapter has no seal.json"; exit 1; }
echo "== finish evaluates sft step $SFT_STEP: $SFT_ADAPTER"

# 7f. Matched test-split evaluation per arm on the pilot bundle.
STAGE=base-test
run "${TV[@]}" eval --eval-split test --bundle "$PILOT" --output "$ROUND/base-test" "${COMMON[@]}"
checkpoint
STAGE=sft-test
run "${TV[@]}" eval --eval-split test --adapter "$SFT_ADAPTER" --bundle "$PILOT" --output "$ROUND/sft-test" "${COMMON[@]}"
checkpoint

# 7g. The eval-2 benchmark, whole, EVAL_DRAWS matched-seed draws per case, per arm.
STAGE=base-eval2
run "${TV[@]}" eval --eval-split all --eval-draws "$EVAL_DRAWS" --bundle "$EVAL2" --output "$ROUND/base-eval2" "${COMMON[@]}"
checkpoint
STAGE=sft-eval2
run "${TV[@]}" eval --eval-split all --eval-draws "$EVAL_DRAWS" --adapter "$SFT_ADAPTER" --reuse-evaluation "$ROUND/base-eval2" --bundle "$EVAL2" --output "$ROUND/sft-eval2" "${COMMON[@]}"
checkpoint

# 8. Paired, grouped comparisons; no model loading or program execution.
STAGE=report
mkdir -p "$ROUND/reports"
report() {
  local name=$1 base=$2 candidate=$3
  echo "== python3 -m pipeline.training_report $base $candidate > $ROUND/reports/$name.json"
  python3 -m pipeline.training_report "$base" "$candidate" > "$ROUND/reports/$name.json"
}
report base-vs-sft-test "$ROUND/base-test" "$ROUND/sft-test"
report base-vs-sft-eval2 "$ROUND/base-eval2" "$ROUND/sft-eval2"
for f in "$ROUND"/reports/*.json; do
  # Printed through `public_view`: the report's summaries carry per-case
  # `case_clusters` counts, which stay private (2026-09-23); the file keeps them.
  python3 -c 'import json, sys; from pipeline.public_view import public_view; r = json.load(open(sys.argv[1])); print("== report", sys.argv[1], json.dumps(public_view(r["paired"])), "engine_mismatches", json.dumps(public_view(r.get("engine_mismatches"))))' "$f"
done
checkpoint

# 9. The trap uploads work/round-02 with the manifest's status field set to complete.
STAGE=done
echo "== round-02 finish: all stages ran; uploading"
