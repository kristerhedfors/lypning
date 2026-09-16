# Claude Code: start the next round here

This file is the portable assignment for the **other device**. Start on merged
`main`; read the root `CLAUDE.md`, this file, then `NEXT_ROUND.md`. Do not launch
training merely because these files exist. No GPU experiment or quality win was
produced by the pipeline refactor.

**Ownership:** Codex orchestrates data/repair priorities and independently reviews
Fable's training writeups; Fable runs approved training loops. Read
[ORCHESTRATION.md](ORCHESTRATION.md) for the active decision ledger and use
[FABLE_REPORT_TEMPLATE.md](FABLE_REPORT_TEMPLATE.md) at every handoff, including
blocked/no-run outcomes. New harvesting data never mutates your active bundle.

## The assignment

Adapt **Qwen/Qwen3.8-27B** to write correct, first-draft **lypning-l-compatible**
Python for ordinary tasks. The approximately 10× speed premise concerns an
aggregate compatible workload, with >9× an aggregate runtime-health goal. A
correct compatible 2× example and an untimed example are both valuable. There is
**no per-program speed floor, upper cap, timing-based SFT filter or speed reward**.
Correctness comes first; compatibility coverage is the model objective.

Use the task-first `pipeline.training` / `gpu/train_verified.py` path, not the
historical rewrite trainer. **Which round runs next, and whether it runs at all,
is `STATUS.md` §10 — read it first; nothing here chooses the next paid step.**
Within a round the stage order is base → verified SFT → train-only signal probe
→ GRPO only if informative → matched evaluation, run by the exact commands in
`NEXT_ROUND.md`. Keep the better base/SFT policy if RL does not earn its place. Broader independent task coverage is more valuable
than repeatedly cloning starter templates. Training remains manually launched.

## What is implemented; what is still a launch gate

Implemented: private exact evidence snapshots; reviewed-data manifests and source
lineage checks; grouped multi-input verified bundles; container execution from
preparation through generation; assistant-only SFT; sealed checkpoints;
capability/population correctness gates; matched seeds; train-only GRPO admission;
paired grouped reports; non-executing portable round plans.

**Not provided:** an independently reviewed production pilot dataset, approved
immutable Hub revision, provisioned/budgeted GPU, production execution image, or
real Qwen/TRL GPU integration results. The starter is smoke-only. Do not invent
these prerequisites, turn unknown intent into a task, or weaken gates to proceed.
CPU/native/container protocol tests are not GPU integration or a model-quality
claim. Historical mismatches still require the audit in `NEXT_ROUND.md`.

## First session: persist decisions before GPU work

1. Record the merged repository commit and CI result in private
   `work/round-02/operator.md`. This entire `work/` tree is gitignored. Preserve
   and transfer it through an **approved private** channel; Git alone does not
   transfer logs, weights, bundles, images or private task data.
2. Copy `round-02.example.json` to `work/round-02/config.json` and fill the exact
   approved Hub commit. Its placeholder deliberately fails validation. Resolve
   one Python **3.12 build**, not just a minor version; build L with that oracle.
3. Get explicit operator approval for device, enforced time/cost ceiling,
   storage/privacy, base-image digest and model acquisition. Record approvals,
   package/image identities, data exclusions and stop conditions. Preload only
   after approval. Never pass credentials into candidate containers.
4. Prepare the candidate image and reviewed dataset below, then run the plan
   command. It only emits commands and missing artifacts; it never executes them:

```bash
PYTHONPATH=src:training python3.12 -m pipeline.round_plan \
  --config work/round-02/config.json --output work/round-02/plan-001.json
```

Read each command before manual execution. Re-plan into a **new** file after a
stage finishes; the planner reads sealed `adapter-N` selected by `best.json`,
including step 0. It never substitutes the last checkpoint. Output existence is
not completion, approval or passing evaluation. A failed/interrupted directory
must be retained and a fresh round directory chosen for a new attempt.

## Candidate boundary: required even for generated smoke code

Two execution contracts are implemented and the bundle records which one
verified it: a locked-down Docker container on a disposable worker (below), or
a pooled Hugging Face sandbox on a host VM that is never the trainer's
([the section after it](#the-hugging-face-boundary-chosen-2026-09-15)). The
local subprocess helper is neither and is admitted for reviewed CPU smoke
fixtures only.

### The Docker boundary

Use a dedicated disposable **Linux** worker. Docker shares the kernel; this
boundary does not make hostile code safe on a sensitive shared host. Maintain
the normal default seccomp policy, resource controls and a patched runtime. Do
not add mounts, network, GPU, privileges or a Docker socket to candidate runs.
The trusted trainer may access the daemon; its generated candidates must not.

Provision the trainer and candidate image from the **same approved CPython base
build**. Pin `VERIFY_BASE_IMAGE` by registry digest, not `latest`. The handshake
compares full `sys.version`, engine hash/version and harness hashes, and stops
before model loading if these differ. Merely having Python 3.12 in both places
does not satisfy that check. Rebuild the image after a harness or engine change.

From the repository root, after building `LYPNING_L_BIN` as in `NEXT_ROUND.md`:

```bash
# VERIFY_BASE_IMAGE is the approved base@sha256:... used by the trainer too.
# This context contains FOUR files, never the checkout, datasets or secrets.
VERIFY_CONTEXT=$(mktemp -d)
cp training/pipeline/sandbox.py training/pipeline/child_exec.py \
  training/pipeline/container_worker.py "$VERIFY_CONTEXT/"
cp "$LYPNING_L_BIN" "$VERIFY_CONTEXT/lypning-l"
docker build --build-arg BASE_IMAGE="$VERIFY_BASE_IMAGE" \
  -f training/worker/Dockerfile.verifier -t lypning-round-02-verifier "$VERIFY_CONTEXT"
export EXECUTION_IMAGE=$(docker image inspect lypning-round-02-verifier --format '{{.Id}}')

# Identity handshake only; no candidate execution or GPU imports.
PYTHONPATH=src:training "$ROUND_PYTHON" -c \
  'import os; from pipeline.training import engine_identity; from pipeline.container_runner import ContainerRunner; ContainerRunner(os.environ["EXECUTION_IMAGE"], engine_identity(os.environ["LYPNING_L_BIN"]))'

# Explicit authored protocol fixtures, never a replay of private captured code.
NTX_TEST_EXECUTION_IMAGE="$EXECUTION_IMAGE" PYTHONPATH=src:training \
  "$ROUND_PYTHON" -m pytest training/tests/test_container_runner.py -q
```

Each verification creates a fresh container: immutable local image ID,
`--pull=never`, no network or host mounts, read-only root, non-root user, no
capabilities, no-new-privileges, PID/CPU/memory limits and bounded tmpfs. The
request contains **only** source, argv, stdin, input files and limits. Expected
outputs, task registry, model and credentials stay in the trainer. Transport
failure aborts verification; it is never converted to a low model reward.

Container startup time is verification overhead, **not interpreter speed**.
Benchmark aggregate engine performance separately with matched workload and
timing scope. Never optimize GRPO against Docker startup time.

### The Hugging Face boundary, chosen 2026-09-15

The operator chose to run the round on Hugging Face, where a Job is a VM
without a Docker daemon, and chose the **pooled** sandbox tier over a dedicated
VM per request: candidates from one training run are treated as one trust
class. `pipeline.hf_sandbox_runner` runs every verification request in a fresh
pooled sandbox on a host Job that is a different VM from the trainer's, under
its own uid (>= 20000), a private home, a scrubbed environment, rlimits and a
per-sandbox Landlock ruleset, with the HF token never forwarded. The request
carries only source, argv, stdin, input files and limits, through the same
`container_worker.py` protocol as Docker. What this tier does **not** give, in
the platform's own words: a separate kernel per candidate, and no outbound
network (Landlock stops binding, not connecting). A dedicated sandbox per
request is the stronger tier and is one call away (`Sandbox.create` in place
of the pool); it costs about six seconds per verification.

The verifier image is a private Docker Space built from exactly four files and
a CPython base pinned by digest; the trainer Job runs from the **same digest**,
which is what makes the `sys.version` half of the identity handshake hold. The
harness lives under `/usr/local/lib`, not `/runner`: a pooled sandbox's Landlock
ruleset reads the standard system trees and nothing else at the root.

**A Space name is not an immutable image.** The Hub SDK offers no revision or
digest on `hf.co/spaces/<owner>/<name>` (huggingface_hub 1.31.0, read
2026-09-16): the string resolves to whatever the Space last built. The runner
therefore fails closed in two places, found by the Codex review of PR #79.
Before the first host is touched, the Space's current Hub commit must equal
the bundle's pinned revision. And every response from every sandbox carries
the identity the worker measures from inside the image (engine, harness, the
worker file itself, the interpreter binary); a response whose identity is not
the one the handshake admitted aborts the run, so a replacement host or a
rebuilt image cannot serve one request unnoticed. Rebuilding the Space is a
new commit, a new bundle and a new handshake, never a silent swap.

**The artifact destination must be private before anything runs.** The
launcher refuses an existing repository that is not private and never flips
visibility; the job checks again before it uploads. Step 3 of the smoke script
also logs authored execution witnesses (`execution-witnesses.jsonl`) so a
report can cite candidate execution through the pool rather than infer it.

```bash
# Build the image: a Space whose context is the four reviewed files. Record its
# 40-character commit; the bundle pins it, and the pool is named by it so a
# host booted from an earlier build never serves a later bundle.
#   Dockerfile: FROM python:3.12-slim@sha256:<digest>
#               COPY sandbox.py child_exec.py container_worker.py /usr/local/lib/lypning-verifier/
#               COPY --chmod=755 lypning-l /usr/local/bin/lypning-l   (no USER line)
# Prepare with the pooled contract (inside a Job on the same base digest):
PYTHONPATH=src:training python3 -m pipeline.cli training-prepare --starter \
  --engine "$LYPNING_L_BIN" --execution-kind hf-sandbox-pool \
  --execution-image hf.co/spaces/<owner>/<verifier> --execution-revision <space commit> \
  --output work/round-02/smoke
# Submit a stage; the cost is printed first and nothing runs without --yes:
PYTHONPATH=src:training python3 training/hf/launch.py smoke --branch <branch> --commit <sha> \
  --space <owner>/<verifier> --space-revision <space commit> --qwen-revision "$QWEN_REV" \
  --work-repo <owner>/<private dataset> --flavor a10g-small --timeout 75m --yes --follow
```

`training/hf/round02_smoke.sh` is what the job runs: the handshake, the starter
bundle through the pool, the tiny-model SFT and GRPO stages on the real GPU,
the plan, and an upload of `work/round-02` to the private artifact repo. It is
smoke: the starter is not a pilot, and a pilot still needs the reviewed dataset
above. Sandbox and Job time are verification overhead, not interpreter speed.

## Evidence and reviewed data

Read [DATA_PRODUCTION.md](DATA_PRODUCTION.md) for the complete loop and interface
limits. Snapshot only specifically authorized logs. No automatic upload or
captured-program execution is part of this step:

```bash
PYTHONPATH=src python3.12 -m lypning.evidence \
  --log /approved/private/invocations.jsonl \
  --origin device-session-log-generation \
  --output work/round-02/evidence-001
```

Author `work/round-02/candidate-cases.jsonl` with the task schema in
`NEXT_ROUND.md`. Add `review` to **each** task:

```json
{
  "origin": "captured",
  "evidence_ids": ["full event_id from events.jsonl"],
  "reviewer": "actual reviewer identity/date",
  "intent_basis": "where the task requirement came from; never inferred from the answer",
  "oracle_basis": "independently derived expected results and boundary tests",
  "rights_basis": "authorization/license and privacy review for this use",
  "independence_basis": "source/project/template overlap reviewed; source_group rationale"
}
```

For genuinely authored tasks, set `origin` to `authored` and use an empty link
list; retain the other review fields. These strings are human assertions, not
automatic certification. Captured links must resolve in supplied intact
snapshots. All tasks derived from one occurrence/exact source must belong to one
connected `source_group`; do not change families to leak variants into holdout.

```bash
PYTHONPATH=src:training "$ROUND_PYTHON" -m pipeline.data_loop \
  --cases work/round-02/candidate-cases.jsonl \
  --snapshot work/round-02/evidence-001 --purpose pilot --seed 1111 \
  --output work/round-02/reviewed
```

Omit `--snapshot` for an entirely authored dataset, or repeat it for several
archives. Review is read-only with respect to archives and executes no programs.
Use the emitted `reviewed/cases.jsonl` and `reviewed/review.json` together when
preparing the bundle. The bundle pins their digest and execution image. Untimed,
unsupported, incorrect, incomplete and unselected observations remain available
privately; they are not all appropriate positive SFT examples.

## Finish and hand back

Continue with the exact launch/evaluation order in `NEXT_ROUND.md`. Run
`train_verified.py ... --plan` before every actual stage. Do the real tiny-model
GPU wiring tests before loading the full model. If the probe fails, preserve it,
stop GRPO and evaluate the better base/SFT arm using the matched evaluation
commands in that runbook; do not manufacture signal by rewarding wrong code.

Return a private artifact inventory (hashes and relative locations), approvals,
actual cost/time, selected adapter seals, matched base/SFT/GRPO metrics and
uncertainty, slice regressions, failures and quarantines. Promote only on earned
correct-native gains without correctness regression. Feed new observations and
capability demands into a **new reviewed dataset version**, never back into the
current frozen holdout or an automatic training job.
