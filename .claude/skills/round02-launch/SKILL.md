---
name: round02-launch
description: Get from a case bank to a launched, admissible round-02 job — the CI-only route to the Hub, the free preflight and the deliberate billed submit, what the launcher and the job refuse and where, the stage order inside the job and which stage pulls 55.6 GB, and the cost model in which one seed job is one replicate and never a round. TRIGGER on requests like "launch the round", "submit the pilot", "why did the job fail at stage review", "what does a seed job cost", "how do we get HF_TOKEN", "which gate refuses this", "can this round detect anything". SKIP for authoring cases (use `training-cases`), for preparing and sealing a bundle (use `training-bundle`), for reading what a finished job left behind (use `round02-evidence`), and for deciding that a round may run at all — that is `training/STATUS.md` §10 and the operator, never this skill.
---

# Launching a round-02 job

A round is not a script you run; it is a bank that survives four separate
admission layers, a job that bills from its first second, and three seeds. This
skill owns the route and the money. Everything it says was read from the files
it names at commit `4c2429c`, 2026-09-18; line numbers are from then.

**2026-09-22 — what changed before S4 arm A** (`training/PLAN.md` Step 4):

- `launch.py` defaults: `DEFAULT_GRPO_STEPS = 0` (arm A never bills GRPO; the
  probe still runs and `grpo-skipped.json` says "arm A only"), a banked stage
  defaults to **h200 / 720m** and refuses a longer timeout, and `--seed` /
  `--split-seed` must be protocol seeds.
- **Split seed fixed at 1111.** `round02.yml` has a `seed` dispatch input
  (1111/2222/3333) that moves only LoRA init and data order; `PILOT_SPLIT_SEED`
  stays 1111, so every seed trains and is measured on the same sealed split.
- A pilot needs `target_run`; the free `token-floor` job now needs `bootstrap`,
  checks engine lineage against the Space, and refuses a curriculum under
  1,000 distinct target cases. No existing Step 2 rung clears that.
- `QWEN_REV` is pinned in every round-02 workflow; a moved Hub head is red.
- Existing bundles are refused (`verifier_sha256` widened); re-prepare.

## The four skills, and the seam between them

| skill | owns |
|---|---|
| `training-cases` | authoring schema-3 cases, the differential oracle, `train.jsonl` / `eval2.jsonl` split by family |
| `training-bundle` | `data_loop` review, `training-prepare`, the bundle's digest and seals, publishing a bank to the private dataset repo |
| **`round02-launch`** | **the CI route, the submit decision, the gates' locations, the stage order, the cost** |
| `round02-evidence` | reading a finished or failed job back out of the private artifact repo |

The seam: `training-bundle` stops at a bundle that would be admitted; this skill
starts at the decision to spend. If you are asking "is the data good", you are
in the wrong skill.

## There is no token on this machine

`HF_TOKEN` is a GitHub Actions repository secret and exists nowhere else, so
every read of the Hub — inventory, upload, submit — happens inside a workflow.
`gh` here has `workflow` scope, so pushing a branch that matches an on-push
trigger is how you run code with the token.

`.github/workflows/s0-inventory.yml` with `.github/scripts/s0_inventory.py` is
the minimal working example of a free read: `on: push: branches: ["s0/**"]`,
`pip install -q "huggingface-hub==1.31.0"`, one script, `HF_TOKEN` from
`secrets`. Copy that shape for any new question about the Hub. A read that
downloads nothing bills nothing.

## The workflow has two halves and only one of them costs

`.github/workflows/round02.yml`, triggered by `push` to `round02/**` or by
`workflow_dispatch`:

- **`preflight`** (lines 44-63) — free. Reads the account, the Jobs hardware
  table with its price, and the approved Qwen revision. Always runs.
- **`bootstrap`** (lines 68-136) — free. Builds `lypning-l` against 3.12,
  creates or updates the verifier Space and records its 40-character commit,
  ensures the artifact repo, publishes the committed bank, runs `eval2-leaks`,
  and ends with a **dry run** that prints the plan and the hourly price and
  submits nothing. Every push to the branch therefore states what a submit would
  cost.
- **`submit`** (lines 138-182) — billed. Runs only on `[submit-smoke]` or
  `[submit-pilot]` in the head commit message, or `inputs.submit == 'SUBMIT'`.

The env block is the whole configuration (lines 30-41):

```yaml
MARKER: "[submit-smoke]"
SPACE_REPO_NAME: lypning-round02-verifier
WORK_REPO_NAME: lypning-round02-artifacts
QWEN_MODEL: Qwen/Qwen3.8-27B
FLAVOR: a10g-small        # smoke only: 24 GB cannot hold a 27B bf16 model
BANK_PATH: banks/v2
PILOT_FLAVOR: h200
```

`MARKER` is documentation, not the check: the `env` context is unavailable in a
job-level `if`, so the condition at line 142 hard-codes both markers. Changing
`MARKER` changes nothing. The submit job's `timeout-minutes: 350` (line 149) is
set above the job's own `--timeout 300m`, because a runner that dies mid-follow
loses the streamed log while the GPU keeps billing.

A pilot is the `[submit-pilot]` branch: `--qwen-revision "${QWEN_REV}"
--bank-path "${BANK_PATH}" --eval-draws 16 --seed "${PILOT_SEED}" --split-seed
"${PILOT_SPLIT_SEED}" --steps "${PILOT_STEPS}" --grpo-steps
"${PILOT_GRPO_STEPS}" --sft-target-run "${SFT_TARGET_RUN}" --score-workers
"${PILOT_SCORERS}" --pool-max-hosts "${PILOT_POOL_HOSTS}" --flavor
"${PILOT_FLAVOR}" --timeout "${PILOT_TIMEOUT}" --yes --follow` (h200, 720m,
GRPO 0 as of 2026-09-22). `--eval-draws 16` reaches only the eval-2 stages;
dev selection reads `launch.py --dev-eval-draws` (`DEV_EVAL_DRAWS`, default 4,
arm field `dev_eval_draws`). `launch.py` also takes `--grpo-generations` and
`--grpo-prompts`, but `round02.yml` has no input for those, the dev draws or
`--grpo-informative-only`, so arm C needs a workflow
edit. One seed. Read the next section before you type that marker.

Since 2026-09-23 the pilot also passes `--eval-every "${PILOT_EVAL_EVERY}"`
(350; an arm field) and `--eval2 "${PILOT_EVAL2}"` (`same-job` or `separate`).
Two more dispatch-only stages, each billed only with `submit=SUBMIT`:
`hwsmoke` (h200, 90m ceiling, no bank) measures generation at 256/128
sequences plus one 256 call forced to full length, and SFT seconds per step,
then projects the approved arm against 720m (`training/hf/projection.py`;
`projection_bounds.upper` prices every call at full length). `eval2` (`eval2_of` = a completed pilot
job that ran `separate`) runs that job's step 7g after `split_eval2.py`
verifies every identity field (`PLAN.md` Step 4).

## What it costs, and what one job is not

`h200` is **$5.00/hour** (`training/RUNBOOK.md` §1, `hf jobs hardware` read
2026-09-12 and re-read unchanged 2026-09-13; the preflight job re-reads it live
and prints the whole priced table). At the pilot's `--timeout 720m`
(2026-09-22) the platform-enforced ceiling is **twelve hours, about $60 per
seed job**; it was about $25 at the old 300m.

**One job is one replicate.** `PROTOCOL_TRAIN_SEEDS = (1111, 2222, 3333)`
(`training/pipeline/training_contract.py:26`) is three jobs; a result from one
seed is not a round-02 result, and the launcher will happily give you one. The
ladder's per-rung cost is the `cost` column of `training/STATUS.md` §10's rung
table — S1 ~$5, S4 (the first three-seed SFT) ~$60–90, the S0 reads $0 — and the
same costs sit against the action plan in `training/ASSESSMENT.md` §6. That S4
figure predates the 720m ceiling: three seed jobs at about $60 each can reach
about $180 (`training/PLAN.md` Step 4).

The meter runs during the data stages too. `review` and `prepare` execute on the
h200, which is why the free local pass below is worth more than it looks.

## The launcher: `training/hf/launch.py`

Runs on the operator's machine or in the submit job; it clones the repo inside
the job and runs a stage script from that clone, so nothing private is baked
into an image.

```
DEFAULT_STEPS, DEFAULT_EVAL_DRAWS, DEFAULT_SEED = 250, 16, 1111
DEFAULT_GRPO_STEPS = 0
DEFAULT_GRPO_GENERATIONS = 4   # probe and GRPO group size; arm C's recipe is 8
DEFAULT_GRPO_PROMPTS = 4       # prompt groups per GRPO step
DEFAULT_SPLIT_SEED = 1111
BANKED_FLAVOR, BANKED_TIMEOUT = "h200", "720m"
DEFAULT_EVAL_SEQUENCES, DEFAULT_SCORE_WORKERS = 256, 12
DEFAULT_POOL_SANDBOXES_PER_HOST, DEFAULT_POOL_MAX_HOSTS = 4, 4
POOL_FLAVOR, MAX_POOL_SANDBOXES_PER_HOST, MAX_POOL_HOSTS = "cpu-basic", 4, 16
BANKED = ("pilot",)
```

What it refuses before anything is submitted:

- a `--commit`, `--space-revision` or `--qwen-revision` that is not 40 hex
  characters (line 126) — a mutable ref cannot pin an experiment;
- a banked stage with no `--bank-path` (line 130): the pilot reads
  `eval2.jsonl`, `train.jsonl` and `evidence-*/` from a directory of the private
  `--work-repo`, never from the checkout;
- pool capacity below `--score-workers`, and per-host density above 4 at
  `cpu-basic` (lines 138-183) — density is an instrument parameter, so two arms
  scored at different densities are not comparable;
- **a `--work-repo` that exists and is not private** (lines 71-82, 209-211).
  `create_repo(private=True, exist_ok=True)` neither checks nor changes an
  existing repo's visibility, so this is checked explicitly and visibility is
  never flipped silently.

The plan — stage, image digest, flavor, timeout, `hourly_usd`, every pinned
revision, and for a banked stage the bank path, steps, draws and seed — is
printed as JSON **before** submission (line 205), and without `--yes` the run
stops there with `dry run: pass --yes to submit`. Read that JSON; it is the
last free moment.

## Inside the job: `training/hf/round02_pilot.sh`

Stages in order, each echoed as `== <stage>`; on any exit a trap writes
`job-manifest.json` with a status and uploads `work/round-02` to the private
repo, and `checkpoint` uploads after each expensive stage because a cancelled
job runs no trap.

| line | stage | what it does | GPU spent |
|---|---|---|---|
| 134 | `deps` | pinned deps from the `train_verified.py` header, 4 attempts | idle |
| 155 | `engine` | `lypning-l` from the verifier Space at `SPACE_REV` | idle |
| 171 | `handshake` | identity and execution witnesses through the pool | idle |
| 234 | `bank` | `snapshot_download` of `$BANK_PATH` from the private dataset repo | idle |
| 266 | `review` | `data_loop` on both banks — bookkeeping, no execution | idle |
| 273 | `prepare` | `training-prepare` through the sandbox pool, two bundles | idle |
| 294 | `plan` | `train_verified.py sft --plan` — no torch, no downloads | idle |
| 296 | `base-dev` | unadapted dev control — **the first 55.6 GB pull** | yes |
| 301 | `sft` | bounded SFT, `best.json` selects, never the last checkpoint | yes |
| 310 | `sft-dev-reload` | reload the selected adapter, reproduce its dev record | yes |
| 315 | `probe` | the exact selected policy on train cases only | yes |
| 322 | `grpo-gate` | exit 0 admits GRPO, exit 3 skips it with a marker | — |
| 359 | `test` | matched test-split eval per arm | yes |
| 368 | `eval2` | the benchmark whole, `EVAL_DRAWS` per arm | yes |
| 379 | `report` | paired grouped comparisons; no model, no execution | idle |

`base-dev` is the stage that downloads the weights: it is the first
non-`--plan` invocation, and `run()` reaches `snapshot_download(BASE_MODEL, ...)`
at `training/gpu/train_verified.py:308`. The checkpoint is **55.6 GB**
(`training/SWITCH.md`, `training/RUNBOOK.md` §1 — `model.safetensors.index.json`
`total_size` 55,562,855,904, read from the Hub API 2026-09-13). Everything after
`plan` therefore pays a large fixed cost before it can fail, which is the whole
reason to care where a gate lives.

`BUNDLES_FROM` (lines 195-231) reuses the `pilot/` and `eval2/` bundles a
previous job prepared, skipping `bank`, `review` and `prepare` entirely — the
cheap way to re-run an arm after a late failure. Preparation re-verifies every
reference through the pool and took about 45 minutes on 2026-09-16 (the script's
own note at line 198), which is roughly $4 of h200 time spent on a CPU-bound
step.

## Which gate lives where. Be exact.

`train_verified.py` has two functions and the difference between them is money.
`preflight()` (lines 99-185) runs with no torch and no downloads. `run()` (line
244) runs on the GPU.

**Refused at plan time, free:**

| gate | value | line |
|---|---|---|
| `--revision` is an immutable 40-hex commit | — | 102 |
| bundle purpose is `pilot` or `benchmark` | `smoke data cannot launch a real run` | 127 |
| train cases | `MIN_TRAIN_CASES = 1000` | 133 |
| training seed | `PROTOCOL_TRAIN_SEEDS = (1111, 2222, 3333)` | 136 |
| one complete family cycle | `steps × batch_size ≥ distinct train families` | 139 |
| eval-2 k | `PROTOCOL_EVAL_DRAWS = 16` | 156 |
| `--isolated-worker` and an isolated execution kind | `ISOLATED_KINDS` | 166-169 |

**Refused inside `run()`, after the tokenizer download:**

| gate | value | line |
|---|---|---|
| CUDA and native BF16 | — | 277-280 |
| per-case prompt + completion budget | `check_prompt_budget`, `≤ --max-seq` | 245-260, called at 292 |
| every SFT row's whole turn | `build_examples` drops it, `run()` refuses the drop | 299-301 |
| **supervised tokens** | **`MIN_SUPERVISED_TOKENS = 50_000`** | **305** |

The prompt budget counts through `pipeline.training.chat_prompt_token_ids`, not
`len()` of the render: under transformers 5.x `apply_chat_template(tokenize=True)`
returns a `BatchEncoding` whose `len()` is 2, the number of keys, and the check
read that as the prompt length until 2026-09-19. The row below it is the one
that still refuses a long *train* row while it did — also before the download,
so an `sft` stage that dies with no output directory has that candidate too.

`supervised_plan()` (lines 188-218) gives plan time a **one-sided upper bound**
on the token floor: the scheduled references' UTF-8 bytes, which byte-level BPE
can never exceed. Preflight refuses only when that bound is already below the
floor (lines 142-151) — a *certain* failure. **A passing `--plan` does not
certify the schedule.** Above the bound there is no information; the exact count
is taken in `run()` and recorded as `planned_supervised_tokens`.

Measured here on the committed bank, 2026-09-18, commit `4c2429c`, by applying
`split_cases(..., 1111)` and `sft_batches(train, train, 250, 4, 1111)` to
`training/data/bank_v2/train.jsonl` and summing exactly what `supervised_plan`
sums: **1,000 planned exposures, upper bound 134,384 bytes**. That is 2.69× the
50,000 floor, so `--steps 250 --batch-size 4` clears the floor only if the
tokenizer averages at most 2.69 UTF-8 bytes per supervised token. Nothing in
this tree can settle that; only the real Qwen tokenizer can. Treat the floor as
**unknown**, not as passing and not as failing.

The tokenizer is a few megabytes, not 55.6 GB, so the exact count does not need
the GPU — it needs the token. If `.github/scripts/token_floor.py` is in the tree,
run it from a workflow of the `s0-inventory.yml` shape and quote what it prints;
that turns the last plan-time unknown into a free CI answer, and a refusal it
finds is the deliverable rather than a broken job.

Note the ordering inside `run()`: for the `sft` stage the token floor is checked
at line 292, *before* `snapshot_download` at line 308. The expense of a token-floor
refusal is therefore not sft's own download — it is the `base-dev` stage that
already pulled 55.6 GB and evaluated 256 dev cases.

## A bank is not a bundle

`training/pipeline/training.py` is what "admissible" means.

- `SCHEMA = 3` and a fixed `SYSTEM` prompt (lines 26, 40); `load_bundle` (215)
  recomputes the digest over the payload and refuses a mismatch, then refuses a
  changed schema or system prompt, then refuses **engine/oracle/policy drift**
  by recomputing `engine_identity(binary)` — the binary's sha256, its
  `--version` string, and the CPython that built it.
- `PURPOSES = ("smoke", "pilot", "benchmark")`; `ADMISSION` runs
  `validate_pilot` / `validate_benchmark` (`training/pipeline/training_data.py`:
  at least 18 semantic families at line 145, both populations in every pilot
  split with two independent families each at 151, both populations bank-wide
  for a benchmark at 164).
- A pilot or benchmark bundle must carry a **review manifest whose digest and
  `cases_sha256` still match its cases and seed** (lines 231-238) and an
  execution kind in `ISOLATED_KINDS = ("docker", "hf-sandbox-pool")` (line 254).
  The local subprocess runner is not an isolation boundary and is admitted for
  reviewed smoke fixtures only.
- The family split is recomputed from the seed and must reproduce (line 244).

So: **a raw `cases.jsonl` is not a bundle.** `training-prepare` is what turns
one into the other — it executes every reference, decides `population` by what
the engine does rather than by the label, splits by family, and writes the
digest the training stage pins. `bank-v3` batches are published as `cases.jsonl`
(`.github/scripts/bank3_publish.py`, `PUBLISHED`), so a bank-v3 batch is two
steps away from a launch: split it into `train.jsonl` + `eval2.jsonl` by family
(`training/data/bank_v2/split_bank.py` is the worked example) and publish that
pair at a `BANK_PATH`. `.github/scripts/round02_bank.py` hard-codes
`BANK = Path("training/data/bank_v2")` at line 20 — `BANK_PATH` changes the
destination, not the source.

## Run the free half here first

The `review` stage is admission bookkeeping with no execution, so it runs on
this laptop with no token and no GPU. Doing it before you push a submit marker
converts an h200-priced failure into a five-second one:

```bash
PYTHONPATH=src:training LYPNING_CAPTURE=0 LYPNING_HARVEST=0 \
  python3 -m pipeline.data_loop --cases training/data/bank_v2/train.jsonl \
  --purpose pilot --seed 1111 --output /tmp/r02-review-train
PYTHONPATH=src:training LYPNING_CAPTURE=0 LYPNING_HARVEST=0 \
  python3 -m pipeline.data_loop --cases training/data/bank_v2/eval2.jsonl \
  --purpose benchmark --seed 1111 --output /tmp/r02-review-eval2
```

Both exited 0 on 2026-09-18 at commit `4c2429c`. The train bank reviewed as
1,120 train / 256 dev / 313 test cases over 33 / 9 / 9 families; the eval-2 bank
as 597 rows over 18 families (`wc -l` gives 1,689 and 597 rows). The dev split's
256 cases over 9 families is exactly the shape the base arm's metrics report,
which is how you check that a published number came from the bank you are
holding.

## The failure modes this round has actually produced

Observed in the private artifact repo, read via CI (measured upstream on
2026-09-18; not reproducible from this tree):

- **stage `review`, exit 1** (job `6aacca0ab1dc2b62dc590991`) — `benchmark needs
  at least 18 independent semantic families`. A 9-family eval-2 bank cleared the
  case target and failed the family floor. This is the failure the local pass
  above catches for free; `training/data/bank_v2/split_bank.py` now takes whole
  families until both constraints hold.
- **stage `sft`, exit 1, HF job status `Job timeout`** (job
  `6aacd5cfb1dc2b62dc590b82`) — the job still uploaded a complete `base-dev` arm
  plus `pilot/` and `eval2/` bundles, because the checkpoint uploads are not the
  trap. Re-run that one with `--bundles-from round-02/<job>` rather than
  re-preparing.
- **a cancelled job runs no trap at all** (job `6aaa5c1e`, 2026-09-16, noted in
  `round02_pilot.sh` lines 58-60) — its SFT and probe were lost.

The rule they add up to: a late refusal is an expensive refusal. Every check you
can move ahead of `base-dev` is 55.6 GB and an eval arm you do not buy twice.

## Can the bank host the effect at all?

**The endpoint has one home: `training/EVAL2.md` §4.** It is the
**correct-and-native first-draft rate**, **macro-averaged over families**,
sampled pass@1 at **k = 16**, compared paired and cluster-bootstrapped by
`split_group` through `pipeline.training_metrics.paired_comparison` — 2,000
resamples, percentile interval. The rule is the **95% interval's lower bound
above +3pp**. That +3pp is the minimum detectable effect of
`training/PREREGISTRATION.md` §7b and **only the threshold comes from there**:
§7b is written for the Subset-Legality Rate, and the string `correct-and-native`
does not occur anywhere in that file (`grep -c correct-and-native
training/PREREGISTRATION.md` → `0`, run 2026-09-18). Cite §7b for the bar,
EVAL2.md §4 for the endpoint; never §7b for the endpoint.

A rate with a 1.0 ceiling can lift by at most `1 − rate`, so a bank whose base
arm already sits near the ceiling reports "no win" whatever the adapter does —
and the draws are paid for either way. That is a property of the **bank**,
checkable before a GPU is booked. It is a precondition on spending, not a
result.

**On `training/data/bank_v2` the available estimate is a strong design signal
that it cannot — confirm it before spending, do not file it as a verdict.** The
completed unadapted `base-dev` arm of job `6aacd5cfb1dc2b62dc590b82` reads
`correct_native` **0.9686** on its coverage population
(round-02/`6aacd5cfb1dc2b62dc590b82`/base-dev/metrics.json; measured upstream on
2026-09-18; not reproducible from this tree). That number is **the family macro
over the 7 coverage families**, which is exactly the statistic `EVAL2.md` §4
freezes — not a case-weighted or draw-weighted aggregate. The file's own
aggregation says so: `(7 × 0.9686456400742115 + 2 × 0.0) / 9 =
0.7533910533910534`, which is the overall `correct_native` in that same
metrics.json to the last digit, while draw-weighted (838/1024 = 0.8184) and
case-weighted ((215 × 0.9686 + 41 × 0) / 256 = 0.8135) both miss it
(orchestrator, 2026-09-18). So the headroom below was taken on the right
statistic. Quote it only with all four caveats:

- It is **k = 4** — 1,024 draws over 256 cases — which `EVAL2.md` §4 calls "a
  smoke setting", not the confirmatory k = 16 the rule was priced at.
- It is a macro over **7 families**, a small cluster count for an endpoint whose
  interval comes from a bootstrap that resamples families as whole clusters, so
  the interval around it is wide in a way a 215-case count does not suggest. The
  capability macro — a **different grouping**, five coverage capabilities, not
  the §4 endpoint — is **0.9659** (set-ops 1.000, text 0.9964, argv-arith
  0.9929, int-reduce 0.9545, stdlib-module 0.8857; same arm, same date), and its
  spread is where the ceiling bites.
- It is a **point estimate with its own sampling error**. The ~3.14pp to a 1.0
  ceiling is therefore an *estimated* margin against a +3pp bar, never an exact
  ceiling; "the arithmetic is exact" is not a thing to write about it. What
  the arithmetic fixes is which statistic the margin was taken on, not the
  margin. The ~0.14pp it leaves the whole 95% interval is a demanding width,
  and no measurement in this tree has reported an interval that narrow on
  correct-and-native. The ΔSLR noise floors of −0.22pp and +1.57pp
  (`training/STATUS.md` §2, 2026-09-14) are **a different quantity** —
  arm-to-arm deltas on the Subset-Legality Rate, not interval half-widths on
  this endpoint — so read them as indicative of the scale of noise here, never
  as a bound this 0.14pp has been shown to fail.
- It is the **dev** split, which is the right split for a design decision: the
  decision is allowed to look at it, and eval-2 is not spent to make it — and
  the wrong split to quote as a result.

Taken together: firing the rule on this population would need a near-perfect
adapter **and** a near-zero-width interval at once, which is a strong signal
against spending draws here — a reason to re-measure before buying, not a
proof that the effect is absent.

The `fallback-control` arm's `correct_native` **0.0 is by design** — the right
answer keeps the import — so its nominal 1.0 of room is room to break the
control, not headroom for the effect.

**Run the arithmetic; do not remember it.** `training/pipeline/headroom.py` is
the instrument and the command is

```bash
./training/nt headroom <metrics.json>      # add --json for the whole table
```

It refuses a metrics file with a missing field rather than printing a zeros
table at exit 0, gives counterweight populations a verdict of their own, and
**exits 1 when the population is saturated**, so a script that reads it before
spending cannot ignore the finding by ignoring the prose. `--mde` and `--noise`
are the bar and the noise floor as parameters (`--noise 0` asks the strict
ceiling question). `training/tests/test_headroom.py` pins the arm above. Point
it at the base arm of the bank you are about to buy draws on; if that bank has
no base arm yet, that read is the cheap thing to buy first.

## Before you type a submit marker

Answer these in writing, in the PR body, before the meter starts.

1. **Which bank, and can it move the endpoint?** The endpoint and its rule are
   `training/EVAL2.md` §4; `training/PREREGISTRATION.md` §7b owns the +3pp bar
   and nothing else. Run `./training/nt headroom` on that bank's base arm and
   read the section above: on `bank_v2` the k = 4 dev estimate says
   the preregistered effect has nowhere to come from, and the `fallback-control`
   arm is a retention counterweight rather than headroom. PR #93 named the same
   defect in generation: the first scaled run natively served 91.2% of the one
   population §2(g) struck out as "the cases that cannot teach". `bank-v3` is the
   retarget — refused fraction 5.2% → 42.1%, repaired rows 13 → 103, measured
   2026-09-18 on GH run 35340137976.
2. **Three seeds or one?** Say which, and say that one is a replicate.
3. **Did the free local review pass on the exact bank you are publishing?**
4. **Does `training/STATUS.md` §10 still say this rung may run?** A bundle that
   would be admitted is not an authorisation, and no paid rung runs before the
   rung below it has been read.
