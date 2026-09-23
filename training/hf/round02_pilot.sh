#!/usr/bin/env bash
# Round-02 pilot on a Hugging Face Job. Runs INSIDE the job, from a fresh clone.
#
# The first real round, modelled on round02_smoke.sh: the same pinned deps, the
# same engine bytes from the verifier Space, the same identity handshake and
# execution witnesses through the pool. Then the reviewed banks come down from
# the private artifact repo, are reviewed (data_loop), prepared through the
# pool (training-prepare: the train bank as a pilot, the eval-2 bank as a
# benchmark), and run in NEXT_ROUND.md's order: base on dev, bounded SFT
# (its step 0 read from base-dev when the fresh LoRA is a proven no-op),
# reload the selected adapter, and -- only if GRPO_STEPS asks for GRPO -- probe
# it on train cases and run GRPO if the probe is admitted; then matched
# test-split evaluations per arm, and the eval-2
# benchmark whole with --eval-draws EVAL_DRAWS per arm. Reports compare
# base-vs-sft and base-vs-grpo on the test split and on the benchmark.
#
# Every stage is echoed with a "== " marker before it runs. On any exit, a
# job-manifest.json with a status field is written and work/round-02 is
# uploaded to the private repo, so a failed stage still hands back what exists.
#
# Inputs (environment, set by training/hf/launch.py):
#   SPACE_REPO   the verifier Space, e.g. headforce/lypning-round02-verifier
#   SPACE_REV    its immutable 40-character commit (the image and the engine)
#   QWEN_REV     the approved immutable Qwen/Qwen3.8-27B Hub commit
#   WORK_REPO    private dataset repo: the banks come from it, work/round-02 goes to it
#   BANK_PATH    directory in WORK_REPO holding eval2.jsonl, train.jsonl, evidence-*/
#   HF_TOKEN     job secret; the trainer holds it, candidates never see it
#   STEPS        SFT optimizer steps (default 250; SFT's token floor still decides)
#   GRPO_STEPS   GRPO optimizer steps (default 0: no probe, no GRPO -- S4 arm A)
#   EVAL_DRAWS   matched-seed draws per case on the eval-2 benchmark (default 16)
#   SEED         training seed: initialisation, data order, draws (default 1111)
#   SPLIT_SEED   review and preparation seed, i.e. the train/dev/test split
#                (default 1111 for EVERY training seed; launch.py says why)
#   EVAL_EVERY   SFT dev-evaluation cadence in optimizer steps (default 50);
#                step 0 and the last step are always evaluated as well
#   EVAL2_MODE   same-job (default): 7g runs here. separate: 7g is skipped,
#                eval2-deferred.json says so, and `round02_eval2.sh` runs it in
#                a second job from this job's sealed adapter (launch.py eval2)
set -euo pipefail
: "${SPACE_REPO:?}" "${SPACE_REV:?}" "${QWEN_REV:?}" "${WORK_REPO:?}" "${BANK_PATH:?}" "${HF_TOKEN:?}"
STEPS="${STEPS:-250}"
GRPO_STEPS="${GRPO_STEPS:-0}"
# Probe and GRPO share one group size (the probe contract binds them). 4 is
# seed 1111's; arm C dispatches 8 (`PLAN.md`). Arm A runs neither.
GRPO_GENERATIONS="${GRPO_GENERATIONS:-4}"
GRPO_PROMPTS="${GRPO_PROMPTS:-4}"         # prompt groups per GRPO optimizer step
EVAL_DRAWS="${EVAL_DRAWS:-16}"
# Draws per dev case in the stages that SELECT a checkpoint (base-dev, sft,
# its reload, grpo). Separate from EVAL_DRAWS, which only the eval-2 stages
# read. 4 is what every earlier job used; the case-clustered selector has
# little power there (`PLAN.md` Step 1), and raising it is an arm change.
DEV_EVAL_DRAWS="${DEV_EVAL_DRAWS:-4}"
# SFT's dev-evaluation cadence. Every evaluation is a full dev pass at
# DEV_EVAL_DRAWS, so at 16 draws the cadence is most of the SFT stage's clock
# (`training/hf/projection.py`); it is an arm field, recorded in the manifest.
EVAL_EVERY="${EVAL_EVERY:-50}"
# Where the eval-2 benchmark runs: in this job, or in a second job that
# `round02_eval2.sh` runs from this job's sealed SFT selection. The measurement
# is the same either way; only which job's clock pays for it moves.
EVAL2_MODE="${EVAL2_MODE:-same-job}"
case "$EVAL2_MODE" in
  same-job|separate) ;;
  *) echo "== EVAL2_MODE must be same-job or separate, not $EVAL2_MODE"; exit 2 ;;
esac
# The split carries the base and SFT arms only (arm A); a GRPO arm's eval-2
# stays in its own job until the second job learns to carry it.
if [ "$EVAL2_MODE" = separate ] && [ "$GRPO_STEPS" != 0 ]; then
  echo "== EVAL2_MODE=separate needs GRPO_STEPS=0"; exit 2
fi
EVAL_SEQUENCES="${EVAL_SEQUENCES:-256}"   # sequences per generate call in evaluation
SCORE_WORKERS="${SCORE_WORKERS:-16}"      # concurrent verifier scorings (one pool host serves 50)
export NTX_POOL_SANDBOXES_PER_HOST="${NTX_POOL_SANDBOXES_PER_HOST:-4}"
export NTX_POOL_MAX_HOSTS="${NTX_POOL_MAX_HOSTS:-4}"
BUNDLES_FROM="${BUNDLES_FROM:-}"          # reuse the bundles an earlier job prepared, e.g. round-02/<job>
SFT_TARGET_RUN="${SFT_TARGET_RUN:-}"      # graded positive-control run; empty retains authored references
SEED="${SEED:-1111}"
# Exported: the reused-bundle check and `targets_fit_split` read it from Python,
# and a default assigned here but not exported is a KeyError there.
export SPLIT_SEED="${SPLIT_SEED:-1111}"
cd "$(dirname "$0")/../.."
export PYTHONPATH=src:training LYPNING_CAPTURE=0 LYPNING_HARVEST=0 PIP_DISABLE_PIP_VERSION_CHECK=1
ROUND=work/round-02
JOB="${JOB_ID:-local}"
export NTX_POOL_TAG="$JOB"   # this run's sandbox pool is its own; see hf_sandbox_runner.pool_name
STAGE=start
mkdir -p "$ROUND"
echo "== round-02 pilot on $(hostname) job=$JOB commit=$(git rev-parse HEAD) sft_steps=$STEPS grpo_steps=$GRPO_STEPS grpo_generations=$GRPO_GENERATIONS grpo_prompts=$GRPO_PROMPTS eval_draws=$EVAL_DRAWS dev_eval_draws=$DEV_EVAL_DRAWS eval_every=$EVAL_EVERY eval2_mode=$EVAL2_MODE eval_sequences=$EVAL_SEQUENCES score_workers=$SCORE_WORKERS pool_sandboxes_per_host=$NTX_POOL_SANDBOXES_PER_HOST pool_max_hosts=$NTX_POOL_MAX_HOSTS seed=$SEED split_seed=$SPLIT_SEED bundles_from=${BUNDLES_FROM:-none} sft_target_run=${SFT_TARGET_RUN:-authored-references}"
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
  if ! STATUS="$status" EXIT_CODE="$code" STAGE="$STAGE" STEPS="$STEPS" GRPO_STEPS="$GRPO_STEPS" GRPO_GENERATIONS="$GRPO_GENERATIONS" GRPO_PROMPTS="$GRPO_PROMPTS" EVAL_DRAWS="$EVAL_DRAWS" DEV_EVAL_DRAWS="$DEV_EVAL_DRAWS" SEED="$SEED" SPLIT_SEED="$SPLIT_SEED" \
      EVAL_EVERY="$EVAL_EVERY" EVAL2_MODE="$EVAL2_MODE" EVAL_SEQUENCES="$EVAL_SEQUENCES" SCORE_WORKERS="$SCORE_WORKERS" BUNDLES_FROM="$BUNDLES_FROM" SFT_TARGET_RUN="$SFT_TARGET_RUN" \
      NTX_POOL_SANDBOXES_PER_HOST="$NTX_POOL_SANDBOXES_PER_HOST" NTX_POOL_MAX_HOSTS="$NTX_POOL_MAX_HOSTS" \
      python3 - <<'PYEOF'
import json, os, subprocess
from huggingface_hub import HfApi
from pipeline.public_view import public_view
api = HfApi()
job = os.environ.get("JOB_ID", "local")
def digest(path):
    try:
        return json.load(open(path))["digest"]
    except (OSError, KeyError, ValueError):
        return None
def read(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}
# What the arm IS, beyond the knobs: the targets it trained on, the rates the
# trainer resolved and the kernel it ran (`.github/scripts/arm_check.py` reads
# every one). Taken from the stage's own experiment.json, which records what
# happened, not from this script's intent; a stage that never ran leaves None.
sft = read("work/round-02/sft/experiment.json")
grpo = read("work/round-02/grpo/experiment.json")
targets = read("work/round-02/sft-targets/sft-report.json")
manifest = {"job": job, "status": os.environ["STATUS"], "exit_code": int(os.environ["EXIT_CODE"]),
            "last_stage": os.environ["STAGE"],
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "space": os.environ["SPACE_REPO"], "space_revision": os.environ["SPACE_REV"],
            "qwen_revision": os.environ["QWEN_REV"], "flavor": os.environ.get("ACCELERATOR", ""),
            "bank_path": os.environ["BANK_PATH"], "steps": int(os.environ["STEPS"]),
            "grpo_steps": int(os.environ["GRPO_STEPS"]),
            "grpo_generations": int(os.environ["GRPO_GENERATIONS"]),
            "grpo_prompts": int(os.environ["GRPO_PROMPTS"]),
            "eval_draws": int(os.environ["EVAL_DRAWS"]), "dev_eval_draws": int(os.environ["DEV_EVAL_DRAWS"]), "seed": int(os.environ["SEED"]),
            "split_seed": int(os.environ["SPLIT_SEED"]),
            "eval_every": int(os.environ["EVAL_EVERY"]),
            # Scheduling, not the arm: which job runs 7g. A deferred eval-2 is
            # finished by a second job whose manifest names this one
            # (`eval2_of`); `arm_check` reads the pair as one seed.
            "eval2_mode": os.environ["EVAL2_MODE"],
            "eval2_deferred": os.path.exists("work/round-02/eval2-deferred.json"),
            "sft_selected_step": read("work/round-02/sft/best.json").get("step"),
            "eval_sequences": int(os.environ["EVAL_SEQUENCES"]), "score_workers": int(os.environ["SCORE_WORKERS"]),
            "pool_sandboxes_per_host": int(os.environ["NTX_POOL_SANDBOXES_PER_HOST"]),
            "pool_max_hosts": int(os.environ["NTX_POOL_MAX_HOSTS"]),
            "bundles_from": os.environ.get("BUNDLES_FROM") or None,
            "sft_target_run": os.environ.get("SFT_TARGET_RUN") or None,
            "sft_sha256": targets.get("sft_sha256"),
            # Which Step 2 arms the targets were harvested from. `sft_sha256`
            # already separates two target sets; this says what they were.
            "sft_target_arms": targets.get("arms"),
            "sft_learning_rate": (sft.get("effective") or {}).get("learning_rate"),
            "grpo_learning_rate": (grpo.get("effective") or {}).get("learning_rate"),
            "kernels": sft.get("kernels"),
            "kernel_binding": sft.get("kernel_binding"),
            "pilot_bundle_digest": digest("work/round-02/pilot/bundle.json"),
            "eval2_bundle_digest": digest("work/round-02/eval2/bundle.json"),
            "grpo_skipped": os.path.exists("work/round-02/grpo-skipped.json"),
            # Arm A skips the probe outright; the marker says so and why.
            "probe_skipped": bool(read("work/round-02/grpo-skipped.json").get("probe_skipped"))}
json.dump(manifest, open("work/round-02/job-manifest.json", "w"), indent=2)
print("== manifest:", json.dumps(public_view(manifest)))
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
trap 'exit 124' TERM
trap 'exit 130' INT

# 1. The GPU script's own pinned dependencies are the single source of truth.
STAGE=deps
echo "== install pinned dependencies from training/gpu/train_verified.py"
# The installer is shared with the hardware smoke and the split eval-2 job, so
# all three install exactly these pins and ask the kernel question the same way.
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

# 2b. The rejection targets, right after the engine and before anything slow.
# They came from the same private artifact repository, were execution-graded
# before this job, and remain private. Their engine lineage is checked HERE,
# against the bytes just downloaded: the trainer checks it again, but only at
# the plan stage, after the bank, review and ~45 minutes of GPU-idle
# preparation had been billed. `s4_target_floor.py` makes the same comparison
# in CI against the Space's engine, for nothing; this is the last free chance.
SFT_TARGET_ARGS=()
if [ -n "$SFT_TARGET_RUN" ]; then
  STAGE=sft-targets
  export SFT_TARGET_RUN
  python3 - <<'PYEOF'
import hashlib, json, os, shutil
from huggingface_hub import HfApi, snapshot_download
repo = os.environ["WORK_REPO"]
run = os.environ["SFT_TARGET_RUN"]
if "/" in run or ".." in run:
    raise SystemExit("SFT_TARGET_RUN must be one run id, not a path")
api = HfApi()
info = api.repo_info(repo, repo_type="dataset")
if info.private is not True:
    raise SystemExit("refusing to read SFT targets from a non-private repository")
prefix = "positive-control/%s/grade" % run
root = snapshot_download(repo, repo_type="dataset", revision=info.sha,
                         allow_patterns=[prefix + "/sft.jsonl", prefix + "/sft-report.json"],
                         local_dir="work/round-02/targets-download")
source = os.path.join(root, prefix)
target = "work/round-02/sft-targets"
os.makedirs(target, exist_ok=False)
for name in ("sft.jsonl", "sft-report.json"):
    path = os.path.join(source, name)
    if not os.path.isfile(path):
        raise SystemExit("private target run is missing grade/" + name)
    shutil.copy2(path, os.path.join(target, name))
report = json.load(open(os.path.join(target, "sft-report.json")))
want = (report.get("lineage") or {}).get("engine_sha256")
with open(os.environ["LYPNING_L_BIN"], "rb") as fh:
    got = hashlib.sha256(fh.read()).hexdigest()
if want != got:
    raise SystemExit("ENGINE LINEAGE: targets of %s were graded by engine %s, this job's engine "
                     "is %s; the trainer would refuse them at the plan stage" % (run, want, got))
print("== downloaded private graded SFT targets for %s; engine lineage %s matches" % (run, got[:16]))
PYEOF
  SFT_TARGET_ARGS=(--sft-targets "$ROUND/sft-targets/sft.jsonl")
fi

# Every target case must be a TRAIN case of the split this job trains on, and
# the trainer refuses the first one that is not -- at the plan stage. The split
# is known the moment the cases are, so this asks before preparation, on the
# reviewed train bank split at SPLIT_SEED, or on the reused bundle as prepared.
targets_fit_split() {
  [ -n "$SFT_TARGET_RUN" ] || return 0
  echo "== every target case is a train case at split seed $SPLIT_SEED, and the curriculum clears its floors"
  PYTHONPATH="$PYTHONPATH:training/gpu" python3 - "$1" <<'PYEOF'
import json, os, sys
from pipeline.jsonio import read_jsonl
from pipeline.training_data import split_cases
from train_verified import curriculum_floor
source = sys.argv[1]
if source.endswith("bundle.json"):
    cases = json.load(open(source))["cases"]
else:
    cases = split_cases(read_jsonl(source), int(os.environ["SPLIT_SEED"]))
train = {c["case_id"]: c for c in cases if c["split"] == "train"}
rows = read_jsonl("work/round-02/sft-targets/sft.jsonl")
outside = sorted({r.get("case_id") for r in rows} - set(train))
if outside:
    raise SystemExit("%d target case(s) are not train cases of this split; were the targets "
                     "graded at another split seed?" % len(outside))
# The trainer's plan stage refuses the same floors, but only after preparation;
# the CI route checks them for free (`s4_target_floor.py`), a hand launch here.
floor = curriculum_floor([train[r["case_id"]] for r in rows])
if floor["problems"]:
    raise SystemExit("; ".join(floor["problems"]))
print("== %d target rows over %d train cases fit the split" % (len(rows), len({r["case_id"] for r in rows})))
PYEOF
}

# 3. Identity handshake, then authored execution witnesses through the pool,
#    logged to the round directory so the report can cite them: no GPU imports.
STAGE=handshake
echo "== identity handshake and execution witnesses through hf.co/spaces/$SPACE_REPO"
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
        # verifier_sha256 is the hash of pipeline/training.py: a bundle is reusable
        # only by a commit that left the verifier module untouched (job 6aaa7339).
        moved = sorted(k for k in set(bundle.get("identity") or {}) | set(identity)
                       if (bundle.get("identity") or {}).get(k) != identity.get(k))
        raise SystemExit("%s bundle was prepared against another engine identity; differs in %s" % (name, ", ".join(moved)))
    if bundle.get("seed") != int(os.environ["SPLIT_SEED"]):
        raise SystemExit("%s bundle was split at seed %s; this job trains on split seed %s"
                         % (name, bundle.get("seed"), os.environ["SPLIT_SEED"]))
    shutil.copytree(d, os.path.join("work/round-02", name))
    print("== %s bundle from %s: digest %s, purpose %s, cases %d"
          % (name, src, bundle["digest"], bundle.get("purpose"), len(bundle["cases"])))
PYEOF
targets_fit_split "$ROUND/pilot/bundle.json"
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
counts = {}
for name in ("eval2.jsonl", "train.jsonl"):
    path = os.path.join(root, name)
    if not os.path.isfile(path):
        raise SystemExit("bank is missing " + name)
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    counts[name] = sum(1 for line in body.splitlines() if line.strip())
    print("== bank %s: %d cases" % (name, counts[name]))

# THE BANK MUST BE THE BANK IT SAYS IT IS. A bank is a path in a shared
# repository and another job can write to it: on 2026-09-19 one did, and this
# round read 1,689 cases where 8,370 had been published, trained on the wrong
# population and reported a result. The count was printed in the log the whole
# time and nothing compared it to anything.
#
# `bank.json` is written by `bank_publish.py` when the bank is cut. A foreign
# writer overwrites the JSONL without knowing to update it, so a mismatch is
# exactly the case this refuses. A bank without one is older than the manifest
# and says so rather than failing, because `banks/v2` predates it.
manifest_path = os.path.join(root, "bank.json")
if not os.path.isfile(manifest_path):
    print("== bank manifest: none (a bank published before bank.json existed); "
          "its identity is NOT verified")
else:
    import hashlib, json as _json
    manifest = _json.loads(open(manifest_path, encoding="utf-8").read())
    print("== bank manifest: %s, cut at seed %s" % (manifest.get("bank"), manifest.get("seed")))
    bad = []
    for name, key in (("train.jsonl", "train"), ("eval2.jsonl", "eval2")):
        want = manifest.get(key) or {}
        with open(os.path.join(root, name), "rb") as fh:
            got = hashlib.sha256(fh.read()).hexdigest()
        if want.get("cases") != counts[name]:
            bad.append("%s: %d cases on disk, manifest says %s"
                       % (name, counts[name], want.get("cases")))
        # The manifest digest is over the file's text as written, so compare
        # counts first and report both rather than only the opaque one.
        print("   %-12s %d cases   sha256 %s" % (name, counts[name], got[:16]))
    if bad:
        raise SystemExit("BANK MISMATCH: %s. Something wrote to %s after it was published; "
                         "refusing to train on a bank that is not the one it names."
                         % ("; ".join(bad), bank))
    print("   bank matches its manifest")
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
  --purpose pilot --seed "$SPLIT_SEED" --output "$ROUND/reviewed-train"
run python3 -m pipeline.data_loop --cases "$BANK_DIR/eval2.jsonl" ${SNAPSHOTS[@]+"${SNAPSHOTS[@]}"} \
  --purpose benchmark --seed "$SPLIT_SEED" --output "$ROUND/reviewed-eval2"
targets_fit_split "$ROUND/reviewed-train/cases.jsonl"

# 6. Prepare both bundles through the pool: references verified, populations checked.
STAGE=prepare
run python3 -m pipeline.cli training-prepare \
  --cases "$ROUND/reviewed-train/cases.jsonl" --review "$ROUND/reviewed-train/review.json" \
  --purpose pilot --execution-kind hf-sandbox-pool --execution-image "hf.co/spaces/$SPACE_REPO" \
  --execution-revision "$SPACE_REV" --engine "$LYPNING_L_BIN" --seed "$SPLIT_SEED" --output "$ROUND/pilot" --score-workers "$SCORE_WORKERS"
run python3 -m pipeline.cli training-prepare \
  --cases "$ROUND/reviewed-eval2/cases.jsonl" --review "$ROUND/reviewed-eval2/review.json" \
  --purpose benchmark --execution-kind hf-sandbox-pool --execution-image "hf.co/spaces/$SPACE_REPO" \
  --execution-revision "$SPACE_REV" --engine "$LYPNING_L_BIN" --seed "$SPLIT_SEED" --output "$ROUND/eval2" --score-workers "$SCORE_WORKERS"
fi

# 7. The stages, in NEXT_ROUND.md's order. One set of common flags for every one.
TV=(python3 training/gpu/train_verified.py)
COMMON=(--isolated-worker --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" --seed "$SEED"
        --eval-sequences "$EVAL_SEQUENCES" --score-workers "$SCORE_WORKERS")
DEV=(--eval-draws "$DEV_EVAL_DRAWS")
PILOT="$ROUND/pilot/bundle.json"
EVAL2="$ROUND/eval2/bundle.json"
SFT_TRAIN=(--steps "$STEPS" --eval-every "$EVAL_EVERY" --rank 16)
GRPO_TRAIN=(--steps "$GRPO_STEPS" --eval-every 50 --rank 16 --grpo-prompts "$GRPO_PROMPTS")

# 7a. Plan first (no GPU imports), then the unadapted dev control.
STAGE=plan
run "${TV[@]}" sft --plan --bundle "$PILOT" --output "$ROUND/sft-plan" "${COMMON[@]}" "${DEV[@]}" "${SFT_TRAIN[@]}" "${SFT_TARGET_ARGS[@]}" --batch-size 4
STAGE=base-dev
run "${TV[@]}" eval --bundle "$PILOT" --output "$ROUND/base-dev" "${COMMON[@]}" "${DEV[@]}"
checkpoint

# 7b. Bounded SFT; best.json selects the adapter, step 0 included, never the last checkpoint.
#     Step 0 is the fresh LoRA, which is the base when its B matrices are zero:
#     --reuse-step0 records base-dev's draws as step 0 (reuse.json says so)
#     after checking that proof and that base-dev ran in THIS job under the
#     same contract, draws, chunking, seed, bundle and engine; no proof, no reuse.
STAGE=sft
run "${TV[@]}" sft --bundle "$PILOT" --output "$ROUND/sft" --reuse-step0 "$ROUND/base-dev" "${COMMON[@]}" "${DEV[@]}" "${SFT_TRAIN[@]}" "${SFT_TARGET_ARGS[@]}" --batch-size 4
SFT_STEP=$(python3 -c 'import json; print(json.load(open("work/round-02/sft/best.json"))["step"])')
SFT_ADAPTER="$ROUND/sft/adapter-$SFT_STEP"
echo "== sft selected step $SFT_STEP: $SFT_ADAPTER"
[ -f "$SFT_ADAPTER/seal.json" ] || { echo "== selected adapter has no seal.json"; exit 1; }
checkpoint

# 7c. Reload the selected adapter and reproduce its dev record.
STAGE=sft-dev-reload
run "${TV[@]}" eval --adapter "$SFT_ADAPTER" --bundle "$PILOT" --output "$ROUND/sft-dev-reload" "${COMMON[@]}" "${DEV[@]}"
checkpoint

# 7d. GRPO_STEPS=0 is S4 arm A: no probe and no GRPO. The probe is not arm C's
#     admission evidence either: its contract (`probe_contract`) binds the
#     exact adapter, code_sha256, seed and group size, so a later arm C job --
#     another commit, possibly another starting adapter, 8 generations --
#     cannot validate against it and re-probes in its own job. The marker says
#     so; nothing is generated.
GRPO_ADAPTER=""
if [ "$GRPO_STEPS" = 0 ]; then
  STAGE=grpo-gate
  python3 - <<'PYEOF'
import json
from pipeline.public_view import public_view
marker = {"why": "arm A only; the probe binds adapter and code, so arm C re-probes in its own job",
          "probe_skipped": True, "grpo_steps": 0}
json.dump(marker, open("work/round-02/grpo-skipped.json", "w"), indent=2)
print("== probe and GRPO skipped:", json.dumps(public_view(marker)))
PYEOF
  checkpoint
else
# 7d'. Probe the exact selected policy on TRAIN cases only; no optimizer updates.
STAGE=probe
run "${TV[@]}" probe --adapter "$SFT_ADAPTER" --bundle "$PILOT" --output "$ROUND/probe" "${COMMON[@]}" --generations "$GRPO_GENERATIONS"
checkpoint

# 7e. GRPO only if the probe is admitted (probe_report: at least two
#     informative groups and a correct draw). Otherwise a marker says why, and
#     the round evaluates the base and SFT arms only.
STAGE=grpo-gate
echo "== read $ROUND/probe/probe.json: exit 0 admits GRPO, exit 3 skips it, anything else fails"
set +e
python3 - <<'PYEOF'
import json
from pipeline.public_view import public_view
report = json.load(open("work/round-02/probe/probe.json"))
verdict = {"admitted": bool(report.get("admitted")), "informative_groups": report.get("informative_groups"),
           "groups": report.get("groups"), "correct_draws": report.get("correct_draws"),
           "truncated_draws": report.get("truncated_draws"), "draws": report.get("draws")}
print("== probe verdict:", json.dumps(public_view(verdict)))
if not verdict["admitted"]:
    verdict["why"] = ("GRPO skipped: probe not admitted; needs at least two informative train groups "
                      "(non-truncated reward variation) and at least one correct draw")
    json.dump(verdict, open("work/round-02/grpo-skipped.json", "w"), indent=2)
    print("==", verdict["why"])
raise SystemExit(3 if "why" in verdict else 0)
PYEOF
GATE=$?
set -e
if [ "$GATE" -eq 0 ]; then
  STAGE=grpo
  run "${TV[@]}" grpo --adapter "$SFT_ADAPTER" --probe "$ROUND/probe/probe.json" --bundle "$PILOT" \
    --output "$ROUND/grpo" "${COMMON[@]}" "${DEV[@]}" "${GRPO_TRAIN[@]}" --generations "$GRPO_GENERATIONS"
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
#     With EVAL2_MODE=separate this job stops before it: the marker records
#     what a second job needs, and `round02_eval2.sh` runs these same commands.
if [ "$EVAL2_MODE" = separate ]; then
  STAGE=eval2-deferred
  SFT_STEP="$SFT_STEP" python3 - <<'PYEOF'
import json, os
from pipeline.public_view import public_view
marker = {"why": "EVAL2_MODE=separate: eval-2 runs in a second job (round02_eval2.sh) "
                 "from this job's sealed SFT selection",
          "sft_selected_step": int(os.environ["SFT_STEP"]),
          "eval2_bundle_digest": json.load(open("work/round-02/eval2/bundle.json"))["digest"]}
json.dump(marker, open("work/round-02/eval2-deferred.json", "w"), indent=2)
print("== eval-2 deferred:", json.dumps(public_view(marker)))
PYEOF
  checkpoint
else
STAGE=eval2
run "${TV[@]}" eval --eval-split all --eval-draws "$EVAL_DRAWS" --bundle "$EVAL2" --output "$ROUND/base-eval2" "${COMMON[@]}"
checkpoint
run "${TV[@]}" eval --eval-split all --eval-draws "$EVAL_DRAWS" --adapter "$SFT_ADAPTER" --reuse-evaluation "$ROUND/base-eval2" --bundle "$EVAL2" --output "$ROUND/sft-eval2" "${COMMON[@]}"
checkpoint
if [ -n "$GRPO_ADAPTER" ]; then
  run "${TV[@]}" eval --eval-split all --eval-draws "$EVAL_DRAWS" --adapter "$GRPO_ADAPTER" --reuse-evaluation "$ROUND/sft-eval2" --bundle "$EVAL2" --output "$ROUND/grpo-eval2" "${COMMON[@]}"
  checkpoint
fi
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
if [ "$EVAL2_MODE" = same-job ]; then
  report base-vs-sft-eval2 "$ROUND/base-eval2" "$ROUND/sft-eval2"
fi
if [ -n "$GRPO_ADAPTER" ]; then
  report base-vs-grpo-test "$ROUND/base-test" "$ROUND/grpo-test"
  report base-vs-grpo-eval2 "$ROUND/base-eval2" "$ROUND/grpo-eval2"
fi
for f in "$ROUND"/reports/*.json; do
  # Printed through `public_view`: the report's summaries carry per-case
  # `case_clusters` counts, which stay private (2026-09-23); the file keeps them.
  python3 -c 'import json, sys; from pipeline.public_view import public_view; r = json.load(open(sys.argv[1])); print("== report", sys.argv[1], json.dumps(public_view(r["paired"])))' "$f"
done

# 9. The trap uploads work/round-02 with the manifest's status field set to complete.
STAGE=done
echo "== round-02 pilot: all stages ran; uploading"
