#!/usr/bin/env bash
# Round-02 pilot on a Hugging Face Job. Runs INSIDE the job, from a fresh clone.
#
# The first real round, modelled on round02_smoke.sh: the same pinned deps, the
# same engine bytes from the verifier Space, the same identity handshake and
# execution witnesses through the pool. Then the reviewed banks come down from
# the private artifact repo, are reviewed (data_loop), prepared through the
# pool (training-prepare: the train bank as a pilot, the eval-2 bank as a
# benchmark), and run in NEXT_ROUND.md's order: base on dev, bounded SFT,
# reload the selected adapter, probe it on train cases only, GRPO only if the
# probe is admitted, matched test-split evaluations per arm, and the eval-2
# benchmark whole with --eval-draws EVAL_DRAWS per arm. Reports compare
# base-vs-sft and base-vs-grpo on the test split and on the benchmark.
#
# Every stage is echoed with a "== " marker before it runs. On any exit, a
# job-manifest.json with a status field is written and work/round-02 is
# uploaded to the private repo, so a failed stage still hands back what exists.
#
# Inputs (environment, set by nemotron/hf/launch.py):
#   SPACE_REPO   the verifier Space, e.g. headforce/lypning-round02-verifier
#   SPACE_REV    its immutable 40-character commit (the image and the engine)
#   QWEN_REV     the approved immutable Qwen/Qwen3.8-27B Hub commit
#   WORK_REPO    private dataset repo: the banks come from it, work/round-02 goes to it
#   BANK_PATH    directory in WORK_REPO holding eval2.jsonl, train.jsonl, evidence-*/
#   HF_TOKEN     job secret; the trainer holds it, candidates never see it
#   STEPS        SFT and GRPO optimizer steps (default 20)
#   EVAL_DRAWS   matched-seed draws per case on the eval-2 benchmark (default 16)
#   SEED         review, preparation and training seed (default 1111)
set -euo pipefail
: "${SPACE_REPO:?}" "${SPACE_REV:?}" "${QWEN_REV:?}" "${WORK_REPO:?}" "${BANK_PATH:?}" "${HF_TOKEN:?}"
STEPS="${STEPS:-20}"
EVAL_DRAWS="${EVAL_DRAWS:-16}"
EVAL_SEQUENCES="${EVAL_SEQUENCES:-128}"   # sequences per generate call in evaluation
SCORE_WORKERS="${SCORE_WORKERS:-32}"      # concurrent verifier scorings (one pool host serves 50)
BUNDLES_FROM="${BUNDLES_FROM:-}"          # reuse the bundles an earlier job prepared, e.g. round-02/<job>
SEED="${SEED:-1111}"
cd "$(dirname "$0")/../.."
export PYTHONPATH=src:nemotron LYPNING_CAPTURE=0 LYPNING_HARVEST=0 PIP_DISABLE_PIP_VERSION_CHECK=1
ROUND=work/round-02
JOB="${JOB_ID:-local}"
export NTX_POOL_TAG="$JOB"   # this run's sandbox pool is its own; see hf_sandbox_runner.pool_name
STAGE=start
mkdir -p "$ROUND"
echo "== round-02 pilot on $(hostname) job=$JOB commit=$(git rev-parse HEAD) steps=$STEPS eval_draws=$EVAL_DRAWS eval_sequences=$EVAL_SEQUENCES score_workers=$SCORE_WORKERS seed=$SEED bundles_from=${BUNDLES_FROM:-none}"
echo "== python: $(python3 -c 'import sys; print(sys.version)')"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo "== no GPU visible"

# Every stage command goes through `run`: the marker first, then the command.
run() {
  echo "== $*"
  "$@"
}

# A stage's artifacts go up as soon as it ends. A cancelled or timed-out job
# runs no EXIT trap (job 6aaa5c1e, 2026-09-16, lost its SFT and probe that way),
# so the trap is the last upload, never the only one. A failed checkpoint is
# reported and the run continues; uploads are incremental (unchanged files skip).
checkpoint() {
  echo "== checkpoint after $STAGE"
  STAGE_DONE="$STAGE" python3 - <<'PYEOF' || echo "== checkpoint upload failed after $STAGE (continuing)"
import os
from huggingface_hub import HfApi
api = HfApi()
if api.repo_info(os.environ["WORK_REPO"], repo_type="dataset").private is not True:
    raise SystemExit("refusing to upload: %s is not a private dataset repository" % os.environ["WORK_REPO"])
job = os.environ.get("JOB_ID", "local")
api.upload_folder(folder_path="work/round-02", repo_id=os.environ["WORK_REPO"], repo_type="dataset",
                  path_in_repo="round-02/" + job, ignore_patterns=["bank-download/**", "bundles-download/**"],
                  commit_message="round-02 pilot job %s: checkpoint after %s" % (job, os.environ["STAGE_DONE"]))
print("== checkpoint uploaded after", os.environ["STAGE_DONE"])
PYEOF
}

# The trap: whatever happened, write the manifest with its status and hand the
# round directory to the private repo. The privacy check precedes the upload.
finish() {
  local code=$1
  trap - EXIT
  local status=complete
  [ "$code" -eq 0 ] || status=failed
  echo "== finish: status=$status stage=$STAGE exit=$code"
  if ! STATUS="$status" EXIT_CODE="$code" STAGE="$STAGE" STEPS="$STEPS" EVAL_DRAWS="$EVAL_DRAWS" SEED="$SEED" \
      EVAL_SEQUENCES="$EVAL_SEQUENCES" SCORE_WORKERS="$SCORE_WORKERS" BUNDLES_FROM="$BUNDLES_FROM" \
      python3 - <<'PYEOF'
import json, os, subprocess
from huggingface_hub import HfApi
api = HfApi()
job = os.environ.get("JOB_ID", "local")
def digest(path):
    try:
        return json.load(open(path))["digest"]
    except (OSError, KeyError, ValueError):
        return None
manifest = {"job": job, "status": os.environ["STATUS"], "exit_code": int(os.environ["EXIT_CODE"]),
            "last_stage": os.environ["STAGE"],
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "space": os.environ["SPACE_REPO"], "space_revision": os.environ["SPACE_REV"],
            "qwen_revision": os.environ["QWEN_REV"], "flavor": os.environ.get("ACCELERATOR", ""),
            "bank_path": os.environ["BANK_PATH"], "steps": int(os.environ["STEPS"]),
            "eval_draws": int(os.environ["EVAL_DRAWS"]), "seed": int(os.environ["SEED"]),
            "eval_sequences": int(os.environ["EVAL_SEQUENCES"]), "score_workers": int(os.environ["SCORE_WORKERS"]),
            "bundles_from": os.environ.get("BUNDLES_FROM") or None,
            "pilot_bundle_digest": digest("work/round-02/pilot/bundle.json"),
            "eval2_bundle_digest": digest("work/round-02/eval2/bundle.json"),
            "grpo_skipped": os.path.exists("work/round-02/grpo-skipped.json")}
json.dump(manifest, open("work/round-02/job-manifest.json", "w"), indent=2)
print("== manifest:", json.dumps(manifest))
info = api.repo_info(os.environ["WORK_REPO"], repo_type="dataset")
if info.private is not True:
    raise SystemExit("refusing to upload: %s is not a private dataset repository" % os.environ["WORK_REPO"])
api.upload_folder(folder_path="work/round-02", repo_id=os.environ["WORK_REPO"], repo_type="dataset",
                  path_in_repo="round-02/" + job, ignore_patterns=["bank-download/**", "bundles-download/**"],
                  commit_message="round-02 pilot from job %s (%s)" % (job, os.environ["STATUS"]))
print("== uploaded work/round-02 to", os.environ["WORK_REPO"], "under round-02/" + job)
PYEOF
  then
    echo "== upload failed"
    [ "$code" -eq 0 ] && code=1
  fi
  echo "== round-02 pilot: $status"
  exit "$code"
}
trap 'finish $?' EXIT

# 1. The GPU script's own pinned dependencies are the single source of truth.
STAGE=deps
echo "== install pinned dependencies from nemotron/gpu/train_verified.py"
DEPS=$(python3 - <<'PYEOF'
import re
head = open("nemotron/gpu/train_verified.py").read().split("# ///")[1]
print(" ".join(re.findall(r'"([^"]+)"', head.split("dependencies")[1])))
PYEOF
)
# A dropped download is not a failed run: pip retries its own connections,
# and the whole install is retried with a growing pause (job 6aaa49a9, 2026-09-16,
# died at exit 123 on one broken pipe three minutes in).
for attempt in 1 2 3 4; do
  # shellcheck disable=SC2086
  if pip install -q --no-cache-dir --retries 10 --timeout 120 $DEPS; then break; fi
  if [ "$attempt" = 4 ]; then echo "== pip install failed 4 times"; exit 123; fi
  echo "== pip install attempt $attempt failed; retrying in $((attempt * 30))s"
  sleep $((attempt * 30))
done
python3 -c 'import torch, transformers, peft, trl, huggingface_hub; print("== torch", torch.__version__, "cuda", torch.cuda.is_available(), "| transformers", transformers.__version__, "| trl", trl.__version__, "| hub", huggingface_hub.__version__)'

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

# 3. Identity handshake, then authored execution witnesses through the pool,
#    logged to the round directory so the report can cite them: no GPU imports.
STAGE=handshake
echo "== identity handshake and execution witnesses through hf.co/spaces/$SPACE_REPO"
python3 - <<'PYEOF'
import json, os
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
        print("== execution witness:", json.dumps(row))
r.close()
PYEOF

# 4-6. Either reuse the bundles an earlier job of this round prepared through
# the pool (same Space revision, same engine identity, checked here and again
# by the trainer's bundle load), or download the banks, review them and prepare
# both bundles. Preparation re-verifies every reference through the pool and
# took about 45 minutes on 2026-09-16; a bundle is immutable once written.
if [ -n "$BUNDLES_FROM" ]; then
  STAGE=bundles
  echo "== reuse prepared bundles from $WORK_REPO/$BUNDLES_FROM (pilot, eval2)"
  python3 - <<'PYEOF'
import json, os, shutil
from huggingface_hub import HfApi, snapshot_download
from pipeline.training import engine_identity
repo, src = os.environ["WORK_REPO"], os.environ["BUNDLES_FROM"].strip("/")
if not src or ".." in src.split("/"):
    raise SystemExit("BUNDLES_FROM must be a relative directory inside the dataset repo")
if HfApi().repo_info(repo, repo_type="dataset").private is not True:
    raise SystemExit("refusing to read bundles from %s: not a private dataset repository" % repo)
local = snapshot_download(repo, repo_type="dataset", allow_patterns=[src + "/pilot/**", src + "/eval2/**"],
                          local_dir="work/round-02/bundles-download")
identity = engine_identity(os.environ["LYPNING_L_BIN"])
want = {"kind": "hf-sandbox-pool", "image": "hf.co/spaces/" + os.environ["SPACE_REPO"], "revision": os.environ["SPACE_REV"]}
for name in ("pilot", "eval2"):
    d = os.path.join(local, src, name)
    bundle = json.load(open(os.path.join(d, "bundle.json")))
    if bundle.get("execution") != want:
        raise SystemExit("%s bundle was prepared under another execution contract: %s" % (name, bundle.get("execution")))
    if bundle.get("identity") != identity:
        raise SystemExit("%s bundle was prepared against another engine identity" % name)
    shutil.copytree(d, os.path.join("work/round-02", name))
    print("== %s bundle from %s: digest %s, purpose %s, cases %d"
          % (name, src, bundle["digest"], bundle.get("purpose"), len(bundle["cases"])))
PYEOF
else
# 4. The banks: eval2.jsonl, train.jsonl and the evidence snapshots they cite,
#    from the private dataset repo only. The download is never uploaded back.
STAGE=bank
echo "== download $BANK_PATH from $WORK_REPO (private dataset)"
python3 - <<'PYEOF'
import os
from huggingface_hub import HfApi, snapshot_download
repo, bank = os.environ["WORK_REPO"], os.environ["BANK_PATH"].strip("/")
if not bank or ".." in bank.split("/"):
    raise SystemExit("BANK_PATH must be a relative directory inside the dataset repo")
info = HfApi().repo_info(repo, repo_type="dataset")
if info.private is not True:
    raise SystemExit("refusing to read banks from %s: not a private dataset repository" % repo)
local = snapshot_download(repo, repo_type="dataset", allow_patterns=[bank + "/**"],
                          local_dir="work/round-02/bank-download")
root = os.path.join(local, bank)
for name in ("eval2.jsonl", "train.jsonl"):
    path = os.path.join(root, name)
    if not os.path.isfile(path):
        raise SystemExit("bank is missing " + name)
    with open(path, encoding="utf-8") as fh:
        print("== bank %s: %d cases" % (name, sum(1 for line in fh if line.strip())))
snapshots = sorted(d for d in os.listdir(root) if d.startswith("evidence-") and os.path.isdir(os.path.join(root, d)))
print("== bank evidence snapshots:", snapshots or "none (authored bank)")
PYEOF
BANK_DIR="$ROUND/bank-download/${BANK_PATH#/}"
BANK_DIR="${BANK_DIR%/}"
SNAPSHOTS=()
for d in "$BANK_DIR"/evidence-*/; do
  [ -d "$d" ] && SNAPSHOTS+=(--snapshot "${d%/}")
done

# 5. Review both banks: admission bookkeeping only, no execution. The train
#    bank is a pilot (controls in every split); eval-2 is a benchmark (whole).
STAGE=review
run python3 -m pipeline.data_loop --cases "$BANK_DIR/train.jsonl" ${SNAPSHOTS[@]+"${SNAPSHOTS[@]}"} \
  --purpose pilot --seed "$SEED" --output "$ROUND/reviewed-train"
run python3 -m pipeline.data_loop --cases "$BANK_DIR/eval2.jsonl" ${SNAPSHOTS[@]+"${SNAPSHOTS[@]}"} \
  --purpose benchmark --seed "$SEED" --output "$ROUND/reviewed-eval2"

# 6. Prepare both bundles through the pool: references verified, populations checked.
STAGE=prepare
run python3 -m pipeline.cli training-prepare \
  --cases "$ROUND/reviewed-train/cases.jsonl" --review "$ROUND/reviewed-train/review.json" \
  --purpose pilot --execution-kind hf-sandbox-pool --execution-image "hf.co/spaces/$SPACE_REPO" \
  --execution-revision "$SPACE_REV" --engine "$LYPNING_L_BIN" --seed "$SEED" --output "$ROUND/pilot"
run python3 -m pipeline.cli training-prepare \
  --cases "$ROUND/reviewed-eval2/cases.jsonl" --review "$ROUND/reviewed-eval2/review.json" \
  --purpose benchmark --execution-kind hf-sandbox-pool --execution-image "hf.co/spaces/$SPACE_REPO" \
  --execution-revision "$SPACE_REV" --engine "$LYPNING_L_BIN" --seed "$SEED" --output "$ROUND/eval2"
fi

# 7. The stages, in NEXT_ROUND.md's order. One set of common flags for every one.
TV=(python3 nemotron/gpu/train_verified.py)
COMMON=(--isolated-worker --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" --seed "$SEED"
        --eval-sequences "$EVAL_SEQUENCES" --score-workers "$SCORE_WORKERS")
PILOT="$ROUND/pilot/bundle.json"
EVAL2="$ROUND/eval2/bundle.json"
TRAIN=(--steps "$STEPS" --eval-every 5 --patience 3 --rank 16)

# 7a. Plan first (no GPU imports), then the unadapted dev control.
STAGE=plan
run "${TV[@]}" sft --plan --bundle "$PILOT" --output "$ROUND/sft-plan" "${COMMON[@]}" "${TRAIN[@]}" --batch-size 4
STAGE=base-dev
run "${TV[@]}" eval --bundle "$PILOT" --output "$ROUND/base-dev" "${COMMON[@]}"
checkpoint

# 7b. Bounded SFT; best.json selects the adapter, step 0 included, never the last checkpoint.
STAGE=sft
run "${TV[@]}" sft --bundle "$PILOT" --output "$ROUND/sft" "${COMMON[@]}" "${TRAIN[@]}" --batch-size 4
SFT_STEP=$(python3 -c 'import json; print(json.load(open("work/round-02/sft/best.json"))["step"])')
SFT_ADAPTER="$ROUND/sft/adapter-$SFT_STEP"
echo "== sft selected step $SFT_STEP: $SFT_ADAPTER"
[ -f "$SFT_ADAPTER/seal.json" ] || { echo "== selected adapter has no seal.json"; exit 1; }
checkpoint

# 7c. Reload the selected adapter and reproduce its dev record.
STAGE=sft-dev-reload
run "${TV[@]}" eval --adapter "$SFT_ADAPTER" --bundle "$PILOT" --output "$ROUND/sft-dev-reload" "${COMMON[@]}"
checkpoint

# 7d. Probe the exact selected policy on TRAIN cases only; no optimizer updates.
STAGE=probe
run "${TV[@]}" probe --adapter "$SFT_ADAPTER" --bundle "$PILOT" --output "$ROUND/probe" "${COMMON[@]}" --generations 4
checkpoint

# 7e. GRPO only if the probe is admitted (probe_report: at least two informative
#     groups and a correct draw). Otherwise a marker says why, and the round
#     evaluates the better base/SFT arm.
STAGE=grpo-gate
GRPO_ADAPTER=""
echo "== read $ROUND/probe/probe.json: exit 0 admits GRPO, exit 3 skips it, anything else fails"
set +e
python3 - <<'PYEOF'
import json
report = json.load(open("work/round-02/probe/probe.json"))
verdict = {"admitted": bool(report.get("admitted")), "informative_groups": report.get("informative_groups"),
           "groups": report.get("groups"), "correct_draws": report.get("correct_draws"),
           "truncated_draws": report.get("truncated_draws"), "draws": report.get("draws")}
print("== probe verdict:", json.dumps(verdict))
if not verdict["admitted"]:
    verdict["why"] = ("GRPO skipped: probe not admitted; needs at least two informative train groups "
                      "(non-truncated reward variation) and at least one correct draw")
    json.dump(verdict, open("work/round-02/grpo-skipped.json", "w"), indent=2)
    print("==", verdict["why"])
raise SystemExit(0 if verdict["admitted"] else 3)
PYEOF
GATE=$?
set -e
if [ "$GATE" -eq 0 ]; then
  STAGE=grpo
  run "${TV[@]}" grpo --adapter "$SFT_ADAPTER" --probe "$ROUND/probe/probe.json" --bundle "$PILOT" \
    --output "$ROUND/grpo" "${COMMON[@]}" "${TRAIN[@]}" --generations 4
  GRPO_STEP=$(python3 -c 'import json; print(json.load(open("work/round-02/grpo/best.json"))["step"])')
  GRPO_ADAPTER="$ROUND/grpo/adapter-$GRPO_STEP"
  echo "== grpo selected step $GRPO_STEP: $GRPO_ADAPTER"
  [ -f "$GRPO_ADAPTER/seal.json" ] || { echo "== selected adapter has no seal.json"; exit 1; }
  checkpoint
elif [ "$GATE" -eq 3 ]; then
  echo "== grpo skipped; marker at $ROUND/grpo-skipped.json"
else
  echo "== probe.json could not be read (exit $GATE)"
  exit "$GATE"
fi

# 7f. Matched test-split evaluation per arm on the pilot bundle.
STAGE=test
run "${TV[@]}" eval --eval-split test --bundle "$PILOT" --output "$ROUND/base-test" "${COMMON[@]}"
run "${TV[@]}" eval --eval-split test --adapter "$SFT_ADAPTER" --bundle "$PILOT" --output "$ROUND/sft-test" "${COMMON[@]}"
if [ -n "$GRPO_ADAPTER" ]; then
  run "${TV[@]}" eval --eval-split test --adapter "$GRPO_ADAPTER" --bundle "$PILOT" --output "$ROUND/grpo-test" "${COMMON[@]}"
fi
checkpoint

# 7g. The eval-2 benchmark, whole, EVAL_DRAWS matched-seed draws per case, per arm.
STAGE=eval2
run "${TV[@]}" eval --eval-split all --eval-draws "$EVAL_DRAWS" --bundle "$EVAL2" --output "$ROUND/base-eval2" "${COMMON[@]}"
checkpoint
run "${TV[@]}" eval --eval-split all --eval-draws "$EVAL_DRAWS" --adapter "$SFT_ADAPTER" --bundle "$EVAL2" --output "$ROUND/sft-eval2" "${COMMON[@]}"
checkpoint
if [ -n "$GRPO_ADAPTER" ]; then
  run "${TV[@]}" eval --eval-split all --eval-draws "$EVAL_DRAWS" --adapter "$GRPO_ADAPTER" --bundle "$EVAL2" --output "$ROUND/grpo-eval2" "${COMMON[@]}"
  checkpoint
fi

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
if [ -n "$GRPO_ADAPTER" ]; then
  report base-vs-grpo-test "$ROUND/base-test" "$ROUND/grpo-test"
  report base-vs-grpo-eval2 "$ROUND/base-eval2" "$ROUND/grpo-eval2"
fi
for f in "$ROUND"/reports/*.json; do
  python3 -c 'import json, sys; r = json.load(open(sys.argv[1])); print("== report", sys.argv[1], json.dumps(r["paired"]))' "$f"
done

# 9. The trap uploads work/round-02 with the manifest's status field set to complete.
STAGE=done
echo "== round-02 pilot: all stages ran; uploading"
