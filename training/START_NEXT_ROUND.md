# Fable: start the next round here

This file is the portable assignment for the **other device**. Start on merged
`main`; read the root `CLAUDE.md`, this file, then `NEXT_ROUND.md`. Do not launch
training merely because these files exist. No GPU experiment or quality win was
produced by the pipeline refactor.

**Ownership:** Codex orchestrates data/repair priorities and independently reviews
Fable's training writeups; Fable runs approved training loops. Read
[ORCHESTRATION.md](ORCHESTRATION.md) for the active decision ledger and use
[FABLE_REPORT_TEMPLATE.md](FABLE_REPORT_TEMPLATE.md) at every handoff, including
blocked/no-run outcomes. New harvesting data never mutates your active bundle.

## Next session — 2026-09-22: follow `PLAN.md`

Seed 1111 of round-02 on bank v3 ran on 2026-09-20/21 (job
`6ab01cbb51992417dfccd64c`) through SFT, probe, GRPO and all three test arms
before the operator cancelled its eval-2 triplicate. It selected step 0 twice,
and that is **not** a result about the training: the checkpoint selector it
ran under could not see a native-only gain. Step 1.1–1.2 replaced that
selector on 2026-09-21, and `tests/test_gate_admission.py` measures the
replacement on the same simulation — which makes the *next* round readable,
not this one. What seed 1111 established and what comes next, in order, is
[`PLAN.md`](PLAN.md); the read
is [`reports/2026-09-21-fable-round02-seed1111-read.md`](reports/2026-09-21-fable-round02-seed1111-read.md).
Step 1 implementation is now in the ordered PR stack #97–#101. Step 2, the
positive control, is the next open step after review and the existing admission
checks. The new engine/image and 256-sequence batch still need pinning and a
hardware smoke; the macOS baseline retains five conformance mismatches, so
this is not a claim that every release check is green. The exact validation
record is `reviews/2026-09-22-step1-validation.md`.
Continue Step 2 in `PLAN.md`; the operator chose **free first** on 2026-09-22.
Finish free validation and preparation; paid inference and GPU training are
held, with no paid ceiling approved. The measured cost and active free checks
are in `ROUND_READINESS.md` under “Current Step 2 admission”. The section below is the previous
assignment, kept as history; its S0 rungs are Step 0 of the plan.

## Next Fable session — 2026-09-17

The next session is a **read-only, $0 S0 evidence round**, not another training
launch. Start from the merged commit carrying
[`reviews/2026-09-17-fable-s0-independent-assessment.md`](reviews/2026-09-17-fable-s0-independent-assessment.md)
on the device that already owns the private round-02 artifacts. Do not run this
assignment on a substitute clone again. Set `PILOT_LYPNING_L` to the historical
binary named below; if any of these four files is absent, report the missing
path and stop without running a rung:

Save the block below as `s0.sh` and run it with `bash s0.sh` from the
repository root. **Do not paste it into an interactive shell**: the preflight
has to stop the rungs, and at an interactive prompt `exit` would close the
session while `return` would not stop anything — neither is a guard.

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

# The 3.12 build `NEXT_ROUND.md` resolves; the pinned binary was built against it,
# and the identity line S0b prints reports the oracle Python it actually used.
# It is a prerequisite like the four files, so it fails the same way.
ROUND_PYTHON=$(uv python find 3.12) || { echo "S0 blocked: no Python 3.12 build" >&2; exit 1; }
export PILOT_RUN=training/runs/eval-20260916-063539
export PILOT_RUN_ID=eval-20260916-063539
export PILOT_ROWS="$PILOT_RUN/eval2_rows.jsonl"
export PILOT_PROBE=work/round-02/6aaa87465527934177ee9f34/probe/probe-rollouts.jsonl
export PILOT_LYPNING_L=/approved/private/path/to/pilot/lypning-l
# `test -f` alone prints nothing and stops nothing: the earlier block would fail
# here and run all three rungs anyway, against whatever the device does have.
for f in "$ROUND_PYTHON" "$PILOT_RUN/attempts.jsonl" "$PILOT_ROWS" "$PILOT_PROBE" \
         "$PILOT_LYPNING_L"; do
  test -f "$f" || { echo "S0 blocked: absent input $f" >&2; exit 1; }
done
test -x "$PILOT_LYPNING_L" || { echo "S0 blocked: not executable: $PILOT_LYPNING_L" >&2; exit 1; }

# S0a: re-print power from the completed private pilot draw, in realised-macro units.
PYTHONPATH=src:training "$ROUND_PYTHON" -m pipeline.cli power --eval2 \
  --rows "$PILOT_ROWS" --draws 16 --mde 0.03

# S0b: freeze the original 171-draw population; replay supplies refusal kinds only.
# --rank is refused on draw/held-out rows.
PYTHONPATH=src:training "$ROUND_PYTHON" -m pipeline.cli levers \
  --run "$PILOT_RUN_ID" --population-rows "$PILOT_ROWS" \
  --engine "$PILOT_LYPNING_L" \
  --require-engine-sha256 a23b30832e00640cec2090d8403a6beeaa2087083d0fbd9080210fd8d4fc1096 \
  --status correct-fallback --expect-draws 171 --vector --limit 0

# S0c: per-train-case native status, probe beside the completed base pilot.
# Paths are cwd-relative and resolved against the repository root.
PYTHONPATH=src:training "$ROUND_PYTHON" -m pipeline.cli probe-vector \
  --probe "$PILOT_PROBE" --base "$PILOT_ROWS"
```

**S0b is not engine-free, and its number is not engine-independent** (measured
2026-09-17). The eval-2 draw rows carry `native` and `status` but no refusal
*kind*. The earlier `--run` path replayed every program through the local binary
and re-derived the population too: on `runs/stock-nothinking`, the same command
matched 14 correct-but-fallback draws through the built engine and 26 through a
broken one. The command above instead reads the historical population from
`PILOT_ROWS`, uses the replay only to attach refusal kinds, requires exactly 171
matching draws, and pins the explicit binary by its full recorded SHA-256. It
prints that SHA, version line and oracle Python. Any mismatch, replay error,
unmatched draw or fallback row without a refusal exits nonzero before a vector
is printed. Do not quote a partial vector.

Quote the vector only as a *shape* — which kinds carry the mass — never as an
absolute headroom and never subtracted from a vector taken at another identity.
The composite engine *fingerprint* of that build is not reproducible here (it
folds in a core `lypning` sha no document records), which is why the binary's
own recorded SHA-256 is what the command binds. An input that is absent exits 2
naming the path, and a vector with no record behind it exits 1 rather than
printing an empty table at exit 0.

Before S0c, download the immutable private artifact directory
`round-02/6aaa87465527934177ee9f34/` into the path shown. **An absent
`$PILOT_ROWS` is a blocked rung, not a file to rebuild** (audited 2026-09-17).
The withdrawn instruction here was to materialize it with `nt eval2-rows`, which
is the confound this round was re-assigned to remove, wearing the other hat:
that command defaults its engine to whichever `lypning-l` the device has
installed, re-derives `native` from that replay, and so re-derives `status` and
hence the population — the same mechanism that read 14 draws through one binary
and 26 through another. Rows made that way are a *new* population at an unknown
identity. What each rung would do with them differs, and the
quieter half is the dangerous one. S0b counts: `--expect-draws 171` refuses a
rebuilt population whose count moved, which is the likely case but not a
guaranteed one — it is a count check, not an identity check, and a rebuild that
happens to land on 171 would pass it. **S0a has no check at all**: `power
--eval2 --rows` accepts any file that exists and would print a confident curve
at an unrecorded identity. The 171 is a count at the pilot's identity
`2e079e786a655ab6`, not a property of the run id.

If the rows must be rebuilt for some later purpose, name the engine and give
the result a path of its own:

```bash
PYTHONPATH=src:training "$ROUND_PYTHON" -m pipeline.cli eval2-rows "$PILOT_RUN_ID" \
  --engine "$PILOT_LYPNING_L" --output work/round-02/rebuilt-rows.jsonl
```

Since 2026-09-17 that command refuses an `--engine` that is not a file, refuses
a replay in which any program failed to grade, and prints the binary's own
sha256 rather than the installed chain's fingerprint. Report the result under
that sha256 — never as the 171, never as S0a's input, and never written over
`$PILOT_ROWS`. Use `"$ROUND_PYTHON"` rather than `nt`, which execs a bare
`python3`.

`probe-vector` prints every status and every unmatched ID; a probe case
missing from base exits 1, and since 2026-09-17 an input that is not a file
exits 2 rather than printing a zeros table at exit 0. This is a descriptive
read, not a new score and not a reason to tune on eval-2. Its two columns are
**not the same measurement** and the per-case difference is confounded in one
direction: the probe column grades every test through the container verifier,
the base column one projected stdout test through the legacy provider arm, and
a fallback-control case is structurally 0/k on the probe side. Read which cases
move, not by how much.

Write one Fable report from `FABLE_REPORT_TEMPLATE.md` containing the full S0a
output, full S0b vector, S0c table, artifact hashes, unmatched counts, and the
three conclusions those reads support. Then stop and hand it back for Codex
review. Do not run S1, create a GPU job, rebuild a dataset, or alter a frozen
artifact in this session.

If a later review authorizes another paid round, it uses a **new** verifier
Space commit and new bundles. The worker and sandbox harness changed, and the
Space Dockerfile must keep a health process alive:

```dockerfile
CMD ["python3", "-I", "/usr/local/lib/lypning-verifier/container_worker.py", "--health-server"]
```

The launcher defaults to 16 scorers backed by four sandboxes per CPU host and
at most four hosts, and since 2026-09-17 a banked launch is **refused above four
sandboxes per host and above four hosts**, as well as below the worker count —
the product check and the density ceiling together are what force sixteen
scorers onto four hosts; separately neither did, and `16/16/1` was admitted.
`native` is host-load-dependent, so per-host density is part of the instrument:
the ceiling is four *at `cpu-basic`*, and changing the pool flavor voids the
number. A serial `1/1/1` diagnostic stays legal because it creates no CPU
contention, and there is deliberately no floor on the worker count, the host
count or total capacity — no eval-2 arm has ever completed, so a throughput
threshold would be set against a forward estimate. A confirmatory eval-2
arm remains k=16. Real adapter stages now refuse fewer than 1,000 train cases,
an SFT schedule below 50,000 supervised tokens, a schedule that cannot cover
every family once, or a seed outside `1111, 2222, 3333`. A complete S4 result
requires all three seed jobs; one successful job is one replicate, not a round.

Of those, the **≥50,000-supervised-token floor is the only one `--plan` cannot
settle** (audited 2026-09-17; since the same day it is no longer wholly blind to
it). The exact floor is refused inside `run()` (`gpu/train_verified.py`), after
the tokenizer download but before the base weights are fetched, because counting
**assistant tokens the schedule exposes** needs the tokenizer that `--plan`
exists to avoid. A schedule that fails it there is refused *on a metered job* —
after the dependency install, the bank download, bundle preparation and the
unadapted base-dev arm (`hf/round02_pilot.sh` steps 7a and 7b) — and the round
ends there with no adapter.

`preflight` now refuses the certainly-too-small half of that before anything is
downloaded: `supervised_plan` sums the scheduled references' UTF-8 bytes, which
under byte-level BPE can never cost more tokens than they have bytes, and a
`--plan` whose `supervised_token_upper_bound` is below 50,000 exits naming the
bound, the floor and the word *upper bound*. So **a `--plan` that passes still
does not certify the schedule.** It means only that the certain failure is
absent; the exact count is taken in `run()`, and a bound above the floor is
never a pass. `planned_exposures` and `supervised_token_upper_bound` are printed
in the plan JSON, and are `null` — never `0` — for a stage with no supervised
dose.

The check is **not** `steps × batch_size`: that product counts example
exposures and is 1,000 at this runbook's own `--steps 250 --batch-size 4`
(`NEXT_ROUND.md`), so comparing it to 50,000 would refuse a schedule the floor
admits. Do not substitute it. Every other gate — `k`, the case count, the seed,
the family cycle — is refused at plan time outright.

The stage still computes the exact count before the 27B weights are downloaded
and records `planned_supervised_tokens`; that recorded number, not the plan's
bound, is what a report quotes.

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
#               CMD ["python3", "-I", "/usr/local/lib/lypning-verifier/container_worker.py", "--health-server"]
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
`train_verified.py ... --plan` before every actual stage — for the gates the
plan can settle. The ≥50,000-supervised-token floor is not one of them: the plan
refuses a schedule whose `supervised_token_upper_bound` is already below it, but
a plan that passes is not a pass on the floor, which `run()` still checks
exactly. Do the real tiny-model
GPU wiring tests before loading the full model. If the probe fails, preserve it,
stop GRPO and evaluate the better base/SFT arm using the matched evaluation
commands in that runbook; do not manufacture signal by rewarding wrong code.

Return a private artifact inventory (hashes and relative locations), approvals,
actual cost/time, selected adapter seals, matched base/SFT/GRPO metrics and
uncertainty, slice regressions, failures and quarantines. Promote only on earned
correct-native gains without correctness regression. Feed new observations and
capability demands into a **new reviewed dataset version**, never back into the
current frozen holdout or an automatic training job.
