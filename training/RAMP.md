# The spend ramp, and the ledger of paid failures

Written 2026-09-26, when training paused for engine coverage (operator decision,
2026-09-26). This file is the one home for **how billed training and evaluation
spend climbs**, for **the per-rung dollar ceilings**, and for **the record of
every paid attempt that failed**. The free check for each failure mode lives in
`.claude/skills/round02-preflight/SKILL.md`; whether a round runs at all is
`training/STATUS.md`; the steps and arm definitions are `training/PLAN.md`;
what a new engine invalidates is `training/ENGINE_BUMP.md`, not this file.

## 1. The rule

**No paid step runs at a scale, shape or code path that a cheaper rung has not
already exercised end to end.** Almost every billed round-02 job died of a check
or shape that first ran hours into an h200 job (ledger, section 6); each of
those had a free or cents-scale equivalent that nobody ran first.

Every rung below states six things, and a dispatch that cannot fill all six
for its rung is not ready:

- **proves** — what a green result establishes;
- **blind to** — what it cannot see, which is what the next rung is for;
- **entry** — the run or job id of the previous rung's green artifact;
- **ceiling** — the dollar and wall-clock cap, set as the job's `--timeout`;
- **stop** — the condition that ends the climb (section 5);
- **evidence** — what it leaves on the Hub or in the Actions log.

**Identity.** A rung's green result holds for one identity: the engine sha256,
the verifier Space revision, `BANK_PATH`, the target run, the arm config
(steps, cadence, draws, selection rule) and the base image digest. Changing any
of them sends the climb back to R1: the identity checks there are the ones that
refused, late and paid, on 2026-09-16 and 2026-09-19. **A code change between
rungs** (`code_sha256` moves — every rung so far was followed by a fix PR)
re-runs R0 and R1 at the new commit and then the lowest rung that exercises the
changed path; a change to a `VERIFIER_MODULES` file
(`training/pipeline/training.py`) is an identity change.

## 2. The rungs

Ceilings below are **proposed, not approved**: the operator records the approved
ceiling in section 7 before dispatch, and gives the per-dispatch go (the
auto-mode classifier refuses a billed `gh workflow run` without it). Dollar
ceilings are wall-clock ceiling × flavor price (section 8).

| Rung | Where | Proves | Blind to | Entry | Ceiling |
|---|---|---|---|---|---|
| R0 | laptop: `uv run --with pytest pytest training/tests -q`; `python3 training/hf/projection.py [--finish]` | code paths, refusals, prefill-part arithmetic, that the schedule fits 720m less 10% (648 min) | GPU, memory, Hub, Space, pool throughput, the real bank | a commit | $0 |
| R1 | free CI: `training.yml` (training verifier), `round02-preflight.yml`, `token-floor.yml`, `s4-target-preflight.yml`, `bank-ceiling.yml`, `eval2-shape.yml`, `hf-status.yml`, `finish_preflight.py` (inside round02.yml submit) | training artifacts agree with the engine; bank is the named one; token floor; target lineage equals the Space's engine; every split's max padded prompt; nothing still running | GPU memory, wall clock, model quality | R0 green on the same commit | $0 |
| R2 | cpu-basic pool soak | a pool at production density survives the longest idle gap and the CPU-heaviest reference inside the 5 s budget | GPU | R1 green | cents |
| R2b | prepare only: training-prepare of the pilot and eval-2 bundles against the pooled Space, no GPU, uploaded so later rungs load them with `bundles_from` | bank labels reproduce in the slim image at the Space's engine; preparation wall clock; the real bundles exist, so R1's shape read can run on them | GPU, SFT, evaluation | R2 green | cents to a few $ (cpu-basic, unpriced; section 8) |
| R3 | round02.yml `stage=smoke`, a10g-small, `--timeout 75m` | HF boundary, image digest, identity handshake, pooled scoring, 2 SFT + 2 GRPO steps on a tiny model | the 27B model, memory, the bank, wall clock | R1 green | ≤ $1.25 |
| R4 | round02.yml `stage=hwsmoke`, h200, `HWSMOKE_TIMEOUT` 90m | the 27B loads at `QWEN_REV`; generate/SFT timings; peak memory; projection | long-prompt prefill (see TODO), pool, bank | R1 (+R3 if the Space or image moved) | ≤ $7.50 |
| R5 | tiny GPU eval: a few chunks per split, **including each split's longest-prompt chunk**, on the R2b bundles | max-shape prefill and decode on real prompts; scoring of real draws | SFT, the idle gap between evaluations | R4 green; R2b bundles; R1 `eval2-shape` read of those bundles | ≤ 60 min, $5 |
| R6 | short SFT on the R2b bundles: a handful of steps with a dev evaluation at step 1, then one gap as long as the real cadence, then another evaluation. Reduced-draw or reduced-step variants live here: they are **not replicates**, and their outputs are discarded | SFT at the longest row, adapter sealing, the pool surviving a real idle gap | full-dose timing, test and eval-2 arms | R5 green | ≤ 120 min, $10 |
| R7 | seed 1 of the arm: the **exact** arm config (steps, cadence, dev draws as recorded in `training/PLAN.md` Step 4), eval-2 deferred (`PILOT_EVAL2=separate`) | the whole pilot pipeline at scale; adapters on the Hub | the arm's full evaluation | R6 green; projection ≤ 648 min; the arm definition for this engine re-derived and recorded in `training/PLAN.md` Step 4 (`PILOT_STEPS` was 1,050 as one pass over 4,197 rows; a new target count changes it); the P16 Codex review of the finish override and selection rule v2 (`training/ORCHESTRATION.md`) done | ≤ 720m, $60 |
| R8 | the same seed's `stage=finish` (test + eval-2 arms; holds the Space) or `stage=eval2` (eval-2 only; its bootstrap rebuilds the Space, not held). Prefill bounded by `--eval-prefill-tokens` | a complete replicate | the other seeds | R7 green; `finish_preflight` green | ≤ 648 min projected, ~$55 |
| R9 | seeds 2222 and 3333 via `bundles_from` | the arm | — | R8 green; `arm_check.py` joins them | ≤ $60 each |

**No billed rung above R4 is dispatched until its tooling exists** (the list
below). Without it the only billed options are R3/R4 and then the full R7,
which is the jump to a full replicate this file exists to prevent.

**Tooling to build before the climb.** Owner: the next training session, in
this order, each with a pinning test under `training/tests/`, each $0 to build:

1. **Hold the Space by default, and give readers their own glob.**
   `.github/scripts/round02_space.py` and `.github/workflows/round02.yml` hold
   the Space for every stage and push unless an explicit rebuild input is set
   (`training/ENGINE_BUMP.md` §3); move `hf-status.yml` off `round02/**`
   (`eval2-shape.yml` is dispatch-only since 2026-09-26). Until then run readers with `gh workflow run` on main,
   never by pushing `round02/**` (section 8).
2. **A shape read that needs no prepared bundle.** `.github/scripts/eval2_shape.py`
   reads only bundles a pilot job already uploaded, and `PILOT_JOB`/`FAILED_JOB`
   are env defaults pinned to arm A's jobs with no dispatch input. Either take
   the bundle source as a `workflow_dispatch` input, or compute prompt lengths
   from the bank + prompt template + subset-spec directly (prompt shape does not
   need a prepared bundle). **Until then R5's shape entry is unmet for a new
   arm**, except through R2b.
3. **A max-prefill case in `training/hf/hwsmoke.py`** (R4 generates on the
   short public starter prompts): sized from the measured maximum padded prompt
   × 256 sequences, at and above `--eval-prefill-tokens`.
4. **An evaluation-slice flag** in `training/gpu/train_verified.py` and a stage
   input in `.github/workflows/round02.yml` (R5): the slice named by chunk
   index, so no case id reaches a log.
5. **Reviewed inputs for `PILOT_STEPS` (1050), `PILOT_EVAL_EVERY` (350) and
   `PILOT_DEV_EVAL_DRAWS` (16)** in `.github/workflows/round02.yml` (R6), with
   the floors still enforced; today they are workflow env.
6. **A prepare-only stage** (R2b): a `stage` input in `.github/workflows/round02.yml`
   and a prepare-and-upload path in `training/hf/round02_pilot.sh` that stops
   before the model pull, on cpu-basic or a runner driving the pooled Space.
7. **The pool soak** (R2): `.github/workflows/pool-soak.yml` plus
   `training/hf/pool_soak.py` — create a pool at `PILOT_POOL_HOSTS` × 4, score,
   idle longer than the longest SFT gap, score again, and time the CPU-heaviest
   bank references at `PILOT_SCORERS` concurrency.

Other notes on existing tooling:

- R1 `eval2-shape.yml` and `.github/scripts/eval2_shape.py` shipped with PR #130
  (merged 2026-09-26, `9b17b32`). Both it and `hf-status.yml` declare
  `workflow_dispatch`, which runs them without triggering round02.yml.
- R1 `bank-ceiling.yml`: its last run, 35427139796 (2026-09-19), failed.
  Re-validate it before relying on it.
- R3 and every non-finish rung run bootstrap, which rebuilds the Space from the
  dispatching commit's engine. A moved Space head makes any pinned pilot
  unfinishable, so no rung above R1 is dispatched while an older pilot still
  needs its finish.

## 3. The shape rule

**Before a rung runs at scale, the cheapest rung that can hold a stage's maximum
shape has run that maximum.** The seed-1111 finish (HF 6ab6a20c6b030d633f691a95,
Actions 36161157776, 2026-09-25) ran out of CUDA memory on the first shape
larger than any measured one; every smoke before it had measured typical
shapes. The failed chunk was **chunk index 26 (0-based; the 27th of 51, after
26 chunks had been scored)**; other files say "chunk 26 of 51" and mean this.
Known maxima, each with its source:

| Stage and dimension | Maximum | Largest ever run clean | Source |
|---|---|---|---|
| eval-2 prefill, padded prompt × 256 sequences | 434 × 256 = 111,104 tokens | 48,384 (189 × 256, pilot base-dev) | Actions 36196784628, 2026-09-25T22:27Z; `training/EVAL2.md` amendment of 2026-09-26 |
| eval-2 chunk padding, p50 / p90 | 129 / 164; three of 51 chunks over 189 | — | same run |
| pilot-dev chunk padding, p50 / p90 / max | 128 / 151 / 189 (48,384 tokens; 306 cases, 20 chunks) | 189 | same run |
| pilot-test chunk padding, p50 / p90 / max | 131 / 143 / 153 (39,168 tokens; 315 cases, 20 chunks) | 153 | same run |
| generate, 256 sequences forced to 1,024 new tokens | peak 120.7 GB | 120.7 GB | hwsmoke HF 6ab4582d6b030d633f68c90e, Actions 35930577878 (2026-09-23T22:51Z; `training/PLAN.md` dates the job 2026-09-24) |
| SFT row length at batch 4 | `--max-seq` 4096 allowed | measured memory point: 1,024-token rows, 61.5 GB (same hwsmoke). The pilot 6ab52a686b030d633f68e503 ran 1,050 SFT steps on real target rows, but its longest row is **unmeasured** | TODO: read the pilot's maximum SFT row length from its artifacts through CI |
| gap between pool uses during SFT | 25–40 min between evaluations | host idle timeout now 3 h (`training/pipeline/hf_sandbox_runner.py` `HOST_IDLE_TIMEOUT`) | died at 600 s: HF 6ab4a05a…, 6ab4d66d… (2026-09-24) |
| scorers vs pool slots | `PILOT_SCORERS` 48 vs 16 hosts × 4 = 64 | 48 | exact fit 64/64 died, HF 6ab0139152d0dbd7f1d74da2 (2026-09-20) |
| job wall vs follower | job 720m; GitHub follower capped at 350 min | — | round02.yml submit job comment; read the job with `hf-status.yml` |
| weights pulled | 55.6 GB | 55.6 GB | `.claude/skills/round02-launch/SKILL.md` |

A new bank, prompt template, subset-spec or engine changes the first four rows:
read the shape again (tooling item 2, or R2b's bundles) before R5.

## 4. Budget discipline

- **Ceiling per rung.** The job's `--timeout` is the ceiling, and the dollar
  figure is written in section 7 before dispatch. The provider has not always
  enforced its timeout: HF 6ab01cbb51992417dfccd64c ran about 11h51m against
  480m (2026-09-20/21, `training/reports/2026-09-21-fable-round02-seed1111-read.md`),
  which is why round02 jobs now also carry an in-container GNU timeout.
- **Cumulative spend after every paid run.** Append a row to section 7 with
  the Actions wall, the HF job's own end if `hf-status.yml` shows one, and the
  running total. An estimate says so; the HF billing page is the only bill, and
  it is reachable only through CI.
- **One rung per go.** A rung is dispatched only after the previous rung's
  evidence is read, and each billed dispatch has the operator's explicit go.
  The handoff for each rung names the exact `gh workflow run` command the user
  runs.
- **Watch and stop.** Every billed rung's handoff also names how to watch it
  mid-run (`gh workflow run hf-status.yml -f job_log=<id> -f tail=200`, about
  every 30 min, since the Actions follower shows no progress past its cap) and
  how to stop it (`hf-stop.yml` with `jobs=<id>`, then cancel the Actions run).

## 5. The stop rule

**After any unexplained paid failure, stop paying.** Diagnose on a free rung,
add the free check that would have caught it (as a row in
`.claude/skills/round02-preflight/SKILL.md`), and re-enter at the rung below the
one that failed. A retry of the same dispatch is not a diagnosis: on 2026-09-24
the 503 retry budget was raised (#123) before the cause (pool hosts idling out)
was known, and the second attempt (HF 6ab4d66d6b030d633f68d8d7) ran about
4.9 h of Actions wall against the first's 2.3 h before dying the same way.
Classify first: a persistent error on a host already used is not transient.

**The seed-1111 salvage is under this rule too.** A finish redispatch
(`training/START_NEXT_ROUND.md`, resume step 1) is an R8 rung taken straight
after an R8 failure, and it would run #130's `prefill_parts` path, which has
never run on a GPU, at the exact 111,104-token shape that failed. It bypasses
R4–R7 because arm A's R7 already ran (pilot 6ab52a686b030d633f68e503) and the
Space it needs cannot survive a climb. Before it is dispatched, all of:

1. **R0**: a unit case showing `prefill_parts` splits the failing chunk's shape
   (one 434-token prompt in a 16-case, 16-draw chunk) under 48,384 tokens.
   Done 2026-09-26: `test_the_chunk_that_ran_the_finish_out_of_memory_now_fits_in_three_parts`
   in `training/tests/test_verified_evaluation.py` (three parts of 6/6/4 cases).
   It proves the arithmetic, not the GPU memory: item 2 still stands.
2. **Either** an R5-style billed slice that evaluates only the longest eval-2
   chunk (tooling item 4, not built), **or** an explicit operator waiver of it,
   written in section 7 before dispatch.
3. `finish_preflight` green, and every condition in `training/ENGINE_BUMP.md` §5.

`training/hf/round02_finish.sh` cannot skip the completed test arms: it runs
base-test and sft-test again before eval-2. In the failed job both test arms
were done by about 55 minutes after the identity handshake (log timestamps are
batched), so rerunning them costs roughly $5 of the ~$43 projection.

## 6. The ledger of paid failures

One row per billed attempt that failed. Cost is the submit job's Actions wall ×
flavor price unless a report in the tree records it; "est." marks the
estimate, and an Actions wall understates a job the follower left still
running. The **Rung** column names the cheapest rung that now catches it; "—"
means none does yet. The 2026-09-20 preflight skill's per-mode costs (~$30 for
the wrong bank, ~$55 for two rounds at the wall, ~1 h for the locale case) were
never tied to job ids; the per-job rows below supersede them.

| Date | Job | Reached | Cause | Cost | Fix | Rung |
|---|---|---|---|---|---|---|
| 2026-09-15 | HF 6aa9c202f76d6a098a70e99c (a10g-small) | preparation | the script named the image without its `spaces/` segment | cents | image path | R3 |
| 2026-09-15 | HF 6aa9c34c5527934177ee6beb (a10g-small) | `GRPOConfig` | transformers 5 folded `warmup_ratio`; a never-exercised code path | cents | argument fixed under the exact pins | R3 |
| 2026-09-16 | HF 6aaa202cf76d6a098a70f7e1 (attempt 1 of 9) | deps install | platform cancel (billing); the serverless provider returned 402 the same day | < $1 | round02.yml preflight reads billing | R1 |
| 2026-09-16 | HF 6aaa38b5… | base-dev, 55 min | one Hub 500 on sandbox create, no retry | ~$4.6 est. | transport retries | R0 (fault injection) |
| 2026-09-16 | HF 6aaa49a9… | deps install | pip broken pipe | < $1 | install retries, pinned deps | R3 |
| 2026-09-16 | HF 6aaa4a0b… | handshake | hand edit moved the harness hash | minutes | reverted | R1 |
| 2026-09-16 | HF 6aaa4b2c… | SFT step-0 eval, 70 min | Space PAUSED | ~$6 est. | `round02_space.py` wakes it | R1 |
| 2026-09-16 | HF 6aaa5c1e… | GRPO, 95 min, cancelled | ~30 s/draw serial evaluation; no upload on cancel | ~$8 est. | batched generate, per-stage upload | R0 (projection) |
| 2026-09-16 | HF 6aaa7339… | bundles | reused bundles pinned an old verifier hash | minutes | prepare fresh | R1 |
| 2026-09-16 | HF 6aaa73e95527934177ee9b22 | probe, 75 min | unstable-oracle route never exercised | ~$6 est. | verifier policy v3 | R0 |
| 2026-09-16 | HF 6aaa87465527934177ee9f34 | eval-2 base, 384/1,200 draws | CPU-bound candidate timed out under 16-way scoring | ~$25 est. | sandbox density ceilings | R2 (TODO) |
| 2026-09-18 | HF 6aacca0ab1dc2b62dc590991 (Actions 35309923728) | review, ~2.5 min | benchmark under the 18-family floor | < $0.25 | `split_bank.py` family floors | R0 (`pipeline.data_loop`) |
| 2026-09-18 | HF 6aacd5cfb1dc2b62dc590b82 (Actions 35313182044) | SFT, 300m wall | timeout; 46,535 < 50,000 token floor too | ≤ $25 est. | `token-floor.yml`; longer timeout | R1 |
| 2026-09-19 | HF 6aae7cf651992417dfcc96e6 (Actions 35441879702) | still RUNNING when the follower exited (17:13Z); end stage not in the tree | attributed to the wrong-bank failure (bank uploaded to the v3 path; serial prep); not confirmed from its log | ≥ $25 est. (submit wall 4h57m; job end not recorded) | `bank.json` manifest | R1 |
| 2026-09-19 | HF 6aaed96052d0dbd7f1d702bc (Actions 35462393347) | pilot prepare; ERROR at 23:28Z per the follower | a reference did not reproduce in the slim image (locale) | ~$23 est. (submit wall 4h38m) | `verify_references.py` in the pinned image | R1 |
| 2026-09-20 | HF 6aaf6d3752d0dbd7f1d728fa (Actions 35491382022) | pilot prepare, wall; run cancelled | serial reference loop, ~4 s/case | ~$29 est. (submit wall 5h50m) | concurrent scoring (981629a) | R0 (projection) |
| 2026-09-20 | HF 6aaff1c052d0dbd7f1d7464f (Actions 35517415342) | prepare, ~2h16m | more hosts behind a serial loop | ~$11 est. | same | R2 (TODO) |
| 2026-09-20 | HF 6ab0139152d0dbd7f1d74da2 (Actions 35524938920) | prepare, ~14 min | exact-fit pool, 64 scorers / 64 slots | ~$1.2 est. | 7d2bb09; scorers 48 | R0 |
| 2026-09-20/21 | HF 6ab01cbb51992417dfccd64c (Actions 35527021744) | cancelled in eval-2 | step-0 selected; uninformative, ~11h51m | ~$60 | selector PRs #97–#101 | R0 (gate simulation) |
| 2026-09-24 | HF 6ab4a05a52d0dbd7f1d8909d (Actions 35953660170) | SFT step-350 eval | pool hosts idled out at 600 s | ~$11 est. | #123, #124 | R2 (TODO); R6 |
| 2026-09-24 | HF 6ab4d66d6b030d633f68d8d7 (Actions 35971691550) | SFT eval | same, after a longer retry budget | ~$24 est. | #124 | R2 (TODO); R6 |
| 2026-09-24 | HF 6ab52a686b030d633f68e503 (Actions 36008052722) | SFT done; base-test | abort-on-any engine mismatch | > $22 est.; job end not recorded | #126 counts it, 1% bound | R1 (the grade showed the rate first) |
| 2026-09-25 | HF 6ab6a20c6b030d633f691a95 (Actions 36161157776) | base-eval2 chunk index 26 (section 3) | CUDA OOM, 111,104-token prefill | ~$19 est. (submit wall 3h50m) | #130 (open) | R1 `eval2-shape`; R4/R5 (TODO) |

Sources: `training/reports/2026-09-15-fable-round02-smoke.md`,
`training/reports/2026-09-16-fable-round02-pilot.md`,
`training/reports/2026-09-16-fable-round02-run.md`, `training/PLAN.md`,
`CHANGELOG.md`, and `gh run view` for each Actions id (read 2026-09-26). Free
failures (bootstraps, provider rungs) are in `training/ORCHESTRATION.md` and
`training/ROUND_READINESS.md`, not here.

TODO (free): read the HF end times and final stages of 6aae7cf6…,
6ab52a686b030d633f68e503 and 6ab6a20c6b030d633f691a95 with `hf-status.yml`
(`timeout_audit_job`), and replace the Actions-wall estimates above.

Known totals, not a bill: nine h200 jobs on 2026-09-16 at about 10.5 GPU-h,
about $52 (the run report); seed 1111 on the old selector about $60; Step 2
provider rungs $28.13 charged or reserved (`training/PLAN.md`). The rest are the
estimates above.

**Rungs that have run clean**, so a reader can see what is already proven:

| Date | Rung | Job | What it established |
|---|---|---|---|
| 2026-09-15 | R3 | HF 6aa9c4c35527934177ee6c46 (a10g-small, 4 min 20 s) | the HF boundary and pooled scoring end to end (`training/reports/2026-09-15-fable-round02-smoke.md`) |
| 2026-09-18 | R3 | HF 6aaca538b1dc2b62dc59024d (Actions 35300350159, A10G) | the smoke from CI (`training/ORCHESTRATION.md` T5) |
| 2026-09-16 | R8 (old selector) | the 2026-09-16 run | SFT vs base on 7 dev and 7 test cases, read (`training/reports/2026-09-16-fable-round02-run.md`) |
| 2026-09-23 | R4 | HF 6ab4582d6b030d633f68c90e (Actions 35930577878) | the 27B at `QWEN_REV`; peak 120.7 GB; SFT timings |
| 2026-09-24 | R7 | HF 6ab52a686b030d633f68e503 (Actions 36008052722) | arm A SFT to 1,050 steps, adapters 0/350/700/1,050 on the Hub |
| 2026-09-25 | R8, partly | HF 6ab6a20c6b030d633f691a95 (Actions 36161157776) | base-test and sft-test, 1,260 draws each; no eval-2 arm |

## 7. Spend log for the next climb

Append-only. One row per paid rung, written **before** dispatch (ceiling) and
completed after (actual). The climb starts at R0 on the next engine; a waiver
(for example the salvage's item 2 in section 5) is a row here too.

| Date | Rung | Actions run | HF job | Ceiling approved | Wall | Cost | Running total | Result |
|---|---|---|---|---|---|---|---|---|
| 2026-09-26 | R8 salvage (seed-1111 finish at step 1,050), §5 item 2 **waived** by the operator ("run the eval now, we need that data to course correct training", then "merge and go") | 36239792036 | 6ab7b0b76b030d633f693e48 | 720m, $60 (h200 $5/h); projection ~520 min, ~$43 | pending | pending | pending | pending |

## 8. Operating constraints

- **The Hub is reachable only through CI.** `HF_TOKEN` is a GitHub Actions
  secret, so every read and every dispatch is a workflow run
  (`.claude/skills/round02-evidence/SKILL.md`).
- **Actions logs are public.** Never print eval-2 or train case text, case
  ids, programs or bank rows; read only aggregates through `hf-status.yml`,
  `s0-inventory.yml`, `eval2-shape.yml` or the pilot readout.
- **Run readers by `gh workflow run` on main, never by pushing `round02/**`.**
  A `round02/**` push runs round02.yml bootstrap, which rebuilds the Space
  (Actions 36196784607, 2026-09-25); a push whose head commit message carries
  `[submit-pilot]` or `[submit-smoke]` also **bills** (round02.yml submit job
  condition). `hf-status.yml` also listens on `bank3/**`, which is safe.
- **Mid-run progress is mostly invisible.** The follower tails logs for at most
  350 min, the Hub shows no end time for a job, and a job keeps billing after
  the follower expires (6ab52a686b030d633f68e503). Walls and costs here are
  Actions-wall proxies; the follower's timestamps are not the HF bill.
- **What the operator must do by hand.** Auto mode blocks `gh pr merge`,
  force-push, deleting CI logs and a billed dispatch without a per-dispatch
  user go; each rung's handoff names the exact command the user runs.
- **Prices.** h200 $5/h and a10g-small $1/h, as round02.yml's preflight job
  prices them; cpu-basic pool hosts and Cerebras generation are not priced by
  any tool here — say "unpriced", not zero.
