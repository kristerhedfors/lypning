# Training programme status — 2026-09-19

One page for the operator and the reviewing session: where the numbers stand,
what is built, what is next, how much the needle can be expected to move, and
whether the instrument can see it move. **§0 is where we are in one paragraph
and §10 is what runs next; everything between them is the evidence for those
two.** Every number carries its run and date
(root `CLAUDE.md`, invariant 3); nothing here is a new measurement. Sources are
the pre-registration, `LADDER.md`, `REVIEW.md`, `AUDIT.md`, the run summaries
under `runs/`, the reviews under `reviews/` and the round-02 smoke report.

## 0. Where we are, in one paragraph

**Five days and roughly $105 have bought four adapters and no informative
result, positive or negative.** Every finding so far is about the instrument and
none is about the model — which is the signature of measuring an effect smaller
than the noise and answering with more measurement (`ASSESSMENT.md` §2). The
programme is not blocked on money or on GPUs. It is blocked on four things that
cost nothing or almost nothing; **one of the four closed on 2026-09-18** and the
other three have still never been done: **no positive control has ever been
run** (stage 0b and stage 1a, about $5 each, defined 2026-09-14 and skipped both
times), **no adapter has been trained at a scale that could move a 27B prior**
(round-02, 2026-09-16: 6,251 supervised tokens, 20 steps, one seed; the
2026-09-18 attempt at 250 steps on the 1,120-case bank cleared `preflight`,
reached stage `sft` and died there, leaving a base arm and no adapter — job
`6aacd5cfb1dc2b62dc590b82`), **the supply ratio was backwards and no longer is**
(bank v2, 2026-09-18: 1,120 admitted training cases to 597 benchmark cases,
against train v1's 64 to 300), and **the examples that would carry the signal
have never been admitted** — ledger row D1 still reads so, and the bank-v3
repair throughput beside it (13 rows, then 103) is not that kind: the
signal-bearing population is the 2026-09-16 pilot's correct-but-fallback
*draws*, which the repair loop never saw. Meanwhile the one lever with a
non-null return is the engine, and it can address about a third of this
repository's 643 refused entries — the same third a model could.

**What changed on 2026-09-16, and it is not a result.** The instrument finding
the assessment rested on was itself mislabelled, and is now fixed in code rather
than described: the `EVAL2.md` §7 power table keyed its rows by a lift the
simulation was not shown to deliver, so its conclusion that the rule is blind to
a uniform few-point effect is **withdrawn**, along with the "more draws, a larger
bank" prescription that followed from it. No power figure from that curve is
quotable until rung S0a re-prints it from the private pilot rows, keyed on the
realised macro the tool now returns. A benchmark eval arm at a k the rule was not
priced at is now refused outright, so round-02's k=4 arm cannot recur.

**What changed on 2026-09-18/19: the data loop runs, and the finding is again
about the instrument rather than the model.** Generation had been read as a
quota problem, a key problem and a GitHub-IP problem across four CI dispatches
and a secret rotation. It was none of them: `pipeline/backends.py` sent no
`User-Agent`, so `urllib` announced `Python-urllib/3.x` and the provider's edge
answered HTTP 403 with Cloudflare error 1010 — "the owner has banned your client
based on its signature" — before the key was read. A 403 from an edge is
indistinguishable from a rejected key at the call site, which is why it survived
so long. The same request, same key, same body, differing only in that header,
returns a completion (`backends.USER_AGENT`, commit `33d5666`, measured
2026-09-19). With it, GH run 35399909232 wrote **7,967** candidate tasks on
25,048 provider calls before its own wall clock stopped it, against 2,784 on
8,753 calls in the previous best run (GH run 35340137976, 2026-09-18). Neither
batch is a training asset yet, and the 4,850 rows already banked cannot become
one at any size: they carry no `fallback-control` row at all (§2, data side).

**The next step is §10, and its first four rungs cost nothing.** Nothing paid
runs before the rung below it has been read. The 2026-09-17 decisions are in
`reviews/2026-09-17-round02-full-assessment.md` and the closed ledger rows.

**Codex review, 2026-09-17:** round-02 produced no completed eval-2 arm and no
model-quality verdict. Paid work remains held. The timeout remains a hard abort;
16 scorers now have an explicit 4-sandboxes/host × 4-host capacity plan, and
successful rows beside a block survive. Held-out lever ranking is refused,
the declaration review is closed under rule 2, real adapter dosage floors are
gates, and the verifier Space now has a content-free health mode. Fable's next
session is only the private-artifact S0a–S0c read in `START_NEXT_ROUND.md`.

**Codex review, 2026-09-17 (second, of the S0-blocked report).** Two sessions
reviewed it independently and agreed on the diagnosis; this is the combined
ruling. The round is still unrun, on a third clone holding none of its inputs,
and no rung is answered. The guards and the document corrections are accepted.
Four findings the report did not have. The guards closed four paths, not five —
`levers` could still print a full vector at exit 0 over two empty files, and now
exits 1 whenever no record backs the vector. S0b's `--run` form re-derived the
historical population and printed no engine identity, so the rung now freezes
status and family to the pilot rows, uses the replay only for refusal kinds,
pins the explicit binary by its recorded SHA-256 and requires exactly 171
matching draws. The token floor's plan-time blindness is real but its
prescribed substitute was wrong in its units; `--plan` now refuses a schedule
whose byte-derived upper bound is already below the floor, while the exact count
stays in the stage. And a banked launch is refused above four sandboxes per host
and above four hosts, which `16/16/1` was not.
`reviews/2026-09-17-codex-s0-guards-and-engine-identity.md` and
`reviews/2026-09-17-fable-s0-independent-assessment.md`.

## 1. The goal, in one sentence and one number

Adapt `Qwen/Qwen3.8-27B` so that an agent with **no knowledge that lypning
exists** writes ordinary first-draft programs that `lypning-l` runs natively at
a rate materially above the base model, **without getting more answers wrong**,
without dropping imports the engine serves, and without writing longer programs
(`LADDER.md` §target state; `TRAINING.md` §problem).

The number that would show it: the paired, cluster-bootstrapped change in the
**correct-and-native first-draft rate** on an unconditioned task benchmark
(the ladder's eval-2), reported beside the subset-legality rate (SLR), with
gates A (correctness not more than 2pp below base), B (supported-import
retention at least 0.80× base) and C (mean completion tokens not more than 20%
longer), engine fingerprint and serving stack quoted. Minimum detectable effect
as pre-registered: the 95% interval's **lower bound above +3pp**
(`PREREGISTRATION.md` §7b, 2026-09-14).

The programme's metric has changed generation three times (correctness pass@1
on the rewrite corpus; SLR; correct-and-native on task families). That drift is
itself a finding, addressed in §6.

## 2. Scoreboard: every number that exists, with its date

The first six rows below are on the frozen 74-case rewrite hold-out (manifest
`80b2fc52…`, `prompt_sha cbb7be44937a6b41`), which by the project's own
admission measures **compliance with a rewrite instruction, not the deployment
prior** (`REVIEW.md` §6, 2026-09-14). They are the numbers we have, not the
number we want. Every row below those six carries its own population in the
middle column, and rows from different populations must never be compared or
subtracted.

| What | Value | Run, date, identity |
|---|---|---|
| Stock Qwen, first-draft pass@1, k=16, thinking off | 0.4257 (95% CI 0.3269–0.5270); pass@16 0.5676 | `qwen38-regrade-20260913b`, 2026-09-13, engine `9d412a3131dc6a8a`, provider novita, seed 1234; the promoted baseline of record |
| Same completions at three earlier engines | 0.4037 → 0.4375 → 0.4375 | `qwen38-baseline-k16` (2026-09-12) and two regrades; zero new inference, so the spread is engine drift, not model movement |
| Verified rejection-sampled LoRA, run of record (v1) | correctness 41.16% → 45.62%, +4.46pp, CI [+1.52, +7.86]; McNemar p = 1.0000 (gained 2, lost 2). **ΔSLR −1.00pp**, CI [−3.36, +1.51]. Verdict: no win. $28.70 | `qwen38-base-arm-v3` vs `qwen38-lora-r16-v3`, 2026-09-14, one H200, `fla-0.5.2`, engine `9d412a3131dc6a8a`, seed 1111, one training seed |
| Context-distilled LoRA (v2 run 1, hinted sampling → unhinted training) | correctness 40.71% → 44.55%, +3.84pp, CI [+0.80, +7.50]; McNemar p = 1.0000. **ΔSLR −0.21pp**, CI [−3.19, +2.67]; gates A/B/C pass. Verdict: no win. $23.08 | `qwen38-base-torchref` vs `qwen38-hinted-v4`, 2026-09-14, one H200, torch-reference kernel, engine `83bc54b6dcd43f46`, seed 1111 |
| Noise floor, same weights through two serving stacks | ΔSLR −0.22pp, CI [−2.46, +2.03] | `qwen38-baseline-k16` vs `qwen38-base-arm-v3`, 2026-09-14 |
| Noise floor, same weights through two kernels | **ΔSLR +1.57pp**, CI [−0.51, +3.89]; correctness −0.14pp | `qwen38-base-arm-v3` vs `qwen38-base-torchref`, 2026-09-14. The kernel moved legality more than either adapter did |
| RL reachability on the refusing tail, k=16 | 84.62% of 52 tier-1 hold-out cases reachable, 42.31% rewardable; train pool 82.38% / 34.72% over 193 | `LADDER.md` stage 0a, 2026-09-14, engine `23684d6c40738fcf`; train-pool row provisional (53 mismatches over 14 cases found in the replay) |
| Round-02 pipeline on Hugging Face, pooled sandboxes | plumbing complete: 16/16 starter references verified, SFT and GRPO smoke stages sealed, planner ran. **No model-quality number** | job `6aa9c4c35527934177ee6c46`, 2026-09-15, tiny random Qwen config, never the 27B weights (`reports/2026-09-15-fable-round02-smoke.md`) |
| Round-02 pilot on the 27B weights, task-first path | SFT 20 steps (loss 0.83 → 0.54), step 5 selected; probe 172/200 correct, GRPO admitted, GRPO flat (step 0 kept); dev 7 cases: base 78.6% / 75.0% correct / correct-and-native, SFT 82.1% / 75.0%; test 7 cases: base 85.7% / 57.1%, SFT 82.1% / 57.1%, paired native delta 0.0pp [−10.7, +10.7]. **Eval-2: base arm blocked at 384/1,200 draws by an engine timeout on a CPU-bound candidate under 16-way concurrent scoring (partial base 87.4% / 69.8%); SFT and GRPO arms never ran** | job `6aaa87465527934177ee9f34`, 2026-09-16, engine `2e079e786a655ab6`, policy v3, k = 4 (`reports/2026-09-16-fable-round02-run.md`) |
| Eval-2 base rate on the training bank (legacy tree), full pilot draw | 87.9% correct, 68.7% correct-and-native, family macro over 64 cases. **No power figure from that draw is quotable**: every row of the `EVAL2.md` §7 curve is keyed by a lift the simulation was not shown to deliver, and the uniform reading is withdrawn (2026-09-16). The re-print, keyed on the realised macro, is rung S0a in §10 | run `eval-20260916-063539`, 2026-09-16, k = 16, engine `2e079e786a655ab6` (`EVAL2.md` §7, §11) |
| Lever split of this repository's own refused captures | 195 self-referential / 242 legitimate-fallback / 206 engine-addressable / 0 residue, over 643 refusals in 9,064 classified rows. Codex moved 13 deterministic new families (20 entries) to engine-addressable and accepted the remainder as current-corpus policy. Against historical §4: **−16 engine-addressable, +21 fallback, −5 other**. Not a model number and not the deployment population — S0b's private draw vector decides the budget | `nt levers --against`, 2026-09-17, rule 2, reviewed in `reviews/2026-09-17-round02-full-assessment.md` |
| Base rate on the **training bank's dev split**, unadapted 27B, task-first path | 256 cases / 1,024 draws / 9 families, truncation 0.0: correct 0.9301, correct-and-native 0.7534, mean completion 96.87 tokens. Coverage arm 215 cases / 860 draws / 7 families: correct 0.9686, correct-and-native 0.9686. Fallback-control arm 41 cases / 164 draws / 2 families: correct 0.7952, correct-and-native 0.0 **by design** — the right answer keeps the import. By capability, correct-and-native: set-ops 1.000, text 0.9964, argv-arith 0.9929, int-reduce 0.9545, stdlib-module 0.8857. The `correct_native` in this file is the **family macro** `EVAL2.md` §4 freezes, which `nt headroom` shows by decomposition rather than assumes. **Not an eval-2 number and not a model-quality verdict**: it is the pilot bundle's dev split at k = 4, the setting `EVAL2.md` §4 calls a smoke setting (the refusal in §0 that stops a k = 4 arm recurring is on **benchmark** arms; this is a dev arm, and it is the right split for a design decision and the wrong one to quote as a result) | job `6aacd5cfb1dc2b62dc590b82`, 2026-09-18, `Qwen/Qwen3.8-27B` @ `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`, pilot bundle digest `797cfe7e12589c…`, seed 1111, `--eval-draws 4`, 16 scorers, verifier Space `headforce/lypning-round02-verifier` @ `5fa4f3127f…`; from `round-02/6aacd5cfb1dc2b62dc590b82/base-dev/metrics.json` and `experiment.json`, re-printed in GH run 35399900848 (`s0-inventory.yml` at `33d5666`), 2026-09-18 |

Data side, each row with its own date:

| Asset | Count | Status |
|---|---|---|
| Admitted training cases on the task-first path | **1,120** over 33 independent split components (1,015 coverage, 105 fallback-control), held out of a 1,689-row bank whose dev (256) and test (313) halves the gates reserve. Every row's `review.reviewer` reads "Codex orchestrator session, 2026-09-18 (an agent, not a human reviewer)": a self-declared agent review with an executed differential oracle behind it, no external evidence attached, and **not** a human review | `data/bank_v2/train.jsonl` through `training_data.split_cases(seed=1111)` and `validate_pilot`, re-run in this worktree 2026-09-19; the GPU job's own bundle prints the same 1,120 / 256 / 313 (`pilot/*-prompts.jsonl`, GH run 35399900848, 2026-09-18) |
| Benchmark bank, v2 | **597** cases, 18 families, 18 split components (492 coverage, 105 fallback-control) | `data/bank_v2/eval2.jsonl` through `split_cases(seed=1111)` and `validate_benchmark`, re-run 2026-09-19; capability-disjoint from the training bank by construction (`bank_v2/README.md`) — its coverage capabilities are `file` and `string`, and the row above holds `argv-arith`, `int-reduce`, `set-ops`, `stdlib-module` and `text` |
| Admitted training cases, train v1 (superseded) | 64 (49 families, 13 fallback-control) | frozen 2026-09-16, `EVAL2.md` §11; superseded by bank v2 above, kept because the 2026-09-16 pilot ran on it |
| Bank v3, rows already banked | 4,850 over two batches — 3,499 native + 13 repaired (GH run 35320962503) and 1,235 native + 103 repaired (GH run 35340137976) — and **0 fallback-control** | line counts from GH run 35399900848, 2026-09-18. The publisher that wrote both batches accepted `native.jsonl` and `repaired.jsonl` only, so every `ceiling` row it made was dropped; `ceiling.jsonl` was added at `f5f3b35`. `validate_pilot` requires coverage **and** fallback-control in each of the three splits, so these rows cannot form a pilot at any size. Re-adapting the surviving candidate artifacts with the current `synth-adapt` is what recovers the discarded ceiling rows, and it costs no provider call |
| Bank v3, candidates generated and not yet adapted | 7,967 written of 12,817 tasks seen, on 25,048 provider calls, stopped on time | GH run 35399909232, generate job 105777198079, 2026-09-18/19, artifact `bank-v3-candidates` (expires 2026-10-03). The adapt job of the same run banked nothing: one candidate declared a test setup file outside the workdir, and `synth-adapt` writes nothing rather than a partial batch (job 105802008535, `synth-adapt blocked: harness: setup file escapes workdir`, exit 1) |
| Starter smoke curriculum | 16 families | smoke fixture by declaration; the pilot floor is 18 and `validate_pilot` rejects the starter by name |
| Reviewed project task catalog | 12 tasks | collection fixture, not a split |
| Question proposals from the 2026-09-15 pilots | 63 structurally valid of 120 requested (`none` arm); 0 of 120 (`medium` arm, all length-truncated) | unreviewed; four sampled proposals already found contradictory (`reviews/2026-09-15-question-pilots.md`) |
| Legacy rewrite-corpus SFT rows | 154 / 117 / 318 / 326 | historical regime; not reused on the task-first path |
| Rewrite-material ceiling | 259 candidate cases, 178 already in the corpus | `AUDIT.md`, 2026-09-13: "not enough refusal material left to fine-tune against" |

## 3. What is done

- **Two complete, matched, pre-registered training rounds** with their controls,
  both null on the endpoint that matters and both documented to the last dollar.
- **An instrument that catches its own errors**: engine fingerprints on every
  legality number, serving stack as part of arm identity, the amended McNemar
  rule that closed a false positive from engine work alone, the kernel null
  that would otherwise have manufactured a +1.2pp win of the wrong sign.
- **The task-first pipeline** (`pipeline.training`, `gpu/train_verified.py`):
  grouped multi-input verified bundles, sealed adapters, capability and
  population gates, train-only RL admission probe, matched seeds, paired
  family-component reports, portable round plans.
- **Two candidate execution boundaries**: Docker on a disposable worker, and
  since 2026-09-15 pooled Hugging Face sandboxes with one CPython base digest on
  both sides of the identity handshake; the latter smoke-tested on a real GPU
  (PR #79). Codex's review of 2026-09-16 held it for two fixes, now in the PR:
  a Space name is not an immutable image, so the runner fails closed on the
  Space's Hub commit and on the identity every response carries; and the
  artifact repository must be private before anything is submitted.
- **Engine work paid for by the programme**: `math`, `type()`, `%.2d` and the
  hillclimb-83 fixes came from on-policy evidence; stage 0a alone added 14 cases
  to `data/engine-mismatches.jsonl`.
- **A data-production loop with hard budgets** (49,152 output tokens per
  session, no automatic retries) and a review queue. Since 2026-09-18 it
  produces rows — 4,850 banked and 7,967 more generated and unadapted (§2, data
  side) — and **not one of them has been admitted to a training bank**: the bank
  a stage now sees was authored, not harvested.

## 4. What is next, in order, with what each step decides

> **Superseded as the live sequence on 2026-09-16 — see §10, which is the one
> home for what runs next.** This table is kept, not rewritten: it is the dated
> record of what was believed on 2026-09-16 before `ASSESSMENT.md`, and step 6
> was run against it. Its step numbers are cited from `EVAL2.md`, so they do not
> move either. Read §10 for the order; read this for the history.

| # | Step | Owner | Cost | Decides |
|---|---|---|---|---|
| 1 | Codex decision on gate 6: pooled tier admitted, or dedicated required | Codex | $0 | whether round-02 may run at all on HF |
| 2 | Run the `question-proposals` profile pilot (reasoning off, 12 × 4,096 tokens), then semantic review and lineage review of what it yields | Codex reviews, Fable runs | tokens only | whether the question feed produces independent families at all |
| 3 | **Build eval-2** (`LADDER.md` stage 4): ≥300 unconditioned ordinary-task prompts, no mention of lypning, own lock and `prompt_sha`, both arms baselined before any adapter; pre-registered in [EVAL2.md](EVAL2.md) before any draw is sampled | Fable authors, Codex reviews | ~$40 | the only benchmark on which a positive result is a deployment claim |
| 4 | Stage 0b prompt ceiling: base Qwen with the subset spec in the system prompt, on eval-1 and eval-2 | Fable | ~$5 | whether the boundary is elicitable from a description (≥10pp SLR up) or only installable by a signal |
| 5 | Author and review the pilot dataset to the gate-4 floor and well beyond it (≥18 independent families is a floor; aim for the hundreds), verified through the admitted boundary, new bundle frozen | Codex + operator review, Fable prepares | tokens + review time | closes gates 3 and 4 |
| 6 | Round-02 proper: base-dev control → verified SFT (3 seeds) → train-only probe → GRPO only if the probe is informative → locked test eval per arm | Fable, under an operator cost ceiling | h200 at $5/hr; the ladder's ~$130–160 total | the first real number on eval-2 |
| 7 | Stage 5, live sessions: refusals per 100 programs through opencode against the same script on base | Fable | harness time | the README number |

Steps 2, 3 and 4 do not depend on each other and should run in parallel. Step 6
does not start until 1, 3 and 5 are closed. The `training/` directory rename is
the operator's call and gates nothing.

## 5. Expected movement of the needle

Honest priors, not measurements. Each is conditional on a step above.

- **On eval-1 (the rewrite tail)**, two adapters trained on ~230–320 verified
  rows moved SLR by −1.00pp and −0.21pp against noise floors of −0.22pp and
  +1.57pp (2026-09-14). A third adapter of the same kind on the same data
  should be expected to do the same. Do not run one.
- **The reachability ceiling is high but the rewardable ceiling is not**: at
  k=16, 84.62% of tier-1 hold-out cases can be reached natively but only 42.31%
  natively and correctly (stage 0a, 2026-09-14). RL has something to reinforce;
  more than half of the tail is not rewardable at any policy the base model can
  sample, and the kinds `decorator`, `generator` and `walrus` had no native
  draw in 16. A finite 0/16 is not proof of impossibility under RL; it says
  those samples give RL nothing to reinforce.
- **Stage 0b is the fork.** If the spec in the prompt lifts SLR by ≥10pp, the
  model can hold the boundary when shown the map, and distillation on ordinary
  tasks (not rewrite prompts) is the recipe with a real chance; the v2 run's
  null does not rule this out because its population was the wrong one. If 0b is
  flat, no amount of SFT on descriptions helps and only a per-kind signal
  (preference or RL) installs the boundary.
- **On eval-2 nothing is known**, including the base rate. It is plausible that
  an ordinary-task SLR for stock Qwen is already far above the tail's 42–44%,
  in which case the deployment headroom is smaller than the tail suggests and
  a +3pp lower bound is a demanding target. It is equally plausible the tail
  under-represents kinds that ordinary tasks hit constantly. Measuring the base
  rate is step 3, and it is worth more than any training run.
- **The dev split the SFT stage selects on has almost no room left, and the
  judgement is a command rather than a paragraph.** `nt headroom` over the base
  arm's `metrics.json` (run 2026-09-19 in this worktree, on the file GH run
  35399900848 re-printed) reports the carrier population INSUFFICIENT: coverage
  at 0.9686 correct-and-native leaves **+3.14pp** of estimated ceiling, the
  `PREREGISTRATION.md` §7b bar takes 3.00pp of it, and +0.14pp is left for the
  whole 95% interval over seven family clusters. The rate it measures is
  `EVAL2.md` §4's own family macro, shown by decomposition and not assumed, so
  the comparison is like for like. Three caveats travel with it or it will be
  misused: it is k = 4, the smoke setting, not the confirmatory k = 16; it is
  the dev split, which is the right split for a design decision and the wrong
  one to quote as a result; and it is a point estimate with no interval. So this
  is a strong reason not to spend on this population and never a proof that
  nothing could clear the bar. Do not select a checkpoint on correct-and-native
  on this bank — select on correctness, as §7 item 4 already says, and read
  correct-and-native as a gate — and re-measure the arm at k = 16 before quoting
  any headroom as a number. The fallback-control arm's 0.0 is the design and is
  a retention counterweight, never headroom. What this does **not** say:
  eval-2's base rate is still unknown, because eval-2's coverage capabilities
  (`file`, `string`) do not appear in this bank at all.
- **Detectability**: on 74 cases a fine-tune that flips fewer than six cases and
  loses none cannot fire the pre-registered rule at any effect size, and the
  headline verdict cannot fire below roughly +11pp on the whole hold-out
  (`AUDIT.md` §FN, `PREREGISTRATION.md` §3c). A larger benchmark is
  necessary but 300 tasks alone do not guarantee power for +3pp: with a
  paired cluster bootstrap, power depends on the discordance rate, the family
  clustering and the base rate, none of which is known for ordinary tasks.
  Size eval-2 from a design-specific power analysis on its own pilot draws
  before freezing it. That is the second reason eval-2 comes before training.
- **Kill criteria stand** (`LADDER.md` §6): if 0b is flat and the by-kind curves
  do not converge, or if eval-1 converges but eval-2 stays flat across two seeds,
  stop training and put the budget into the engine, which has so far returned
  more per dollar than the adapters.

## 6. Are we measuring properly?

For the narrow question ("does this adapter make the model comply with a
rewrite instruction more often, without getting more wrong?") yes, and the
answer was no, twice. For the deployment question, **not yet**, for reasons
the documents themselves record:

1. **Wrong population.** Every `refused:*` hold-out prompt names the refusal
   (`REVIEW.md` §6, 2026-09-14). SLR on it is compliance, not the prior an
   agent brings. Eval-2 does not exist.
2. **Metric drift.** Correctness pass@1 → SLR → correct-and-native, across three
   document generations. The next round must freeze one primary metric before
   anything runs and report the other two beside it. Recommended primary:
   correct-and-native sampled pass@1 on eval-2, macro over families, with SLR
   and correctness as gated secondaries.
3. **Noise floors exceed effect sizes.** Kernel swap +1.57pp on legality;
   engine regrade +3.38pp on correctness with zero new inference (2026-09-12
   to 2026-09-13). Both are now preconditions of a comparison, which is right,
   but they mean a single-seed, single-stack delta under about +2pp is noise by
   construction.
4. **Contamination in the frozen split.** 27 of 74 hold-out cases have a train
   neighbour at ≥0.85 prompt similarity; 58 of 175 train cases were excluded
   from sampling rather than re-cutting the split (`AUDIT.md`,
   `PREREGISTRATION.md` §1b, 2026-09-12). Eval-2 must be split by family and
   source group, as the schema-3 bundle already does.
5. **One seed.** Both runs of record are one training seed; the
   pre-registration now demands three. No result with fewer is a result.
6. **The corpus is exhausted.** 259 candidate rewrite cases exist in total;
   the sub-tail cannot be widened 25×. Ordinary tasks are the only population
   with supply, and their verifier contract (deterministic UTF-8 stdout, exit
   zero) bounds what can be admitted.
7. **The current pipeline has never trained the 27B weights.** The smoke
   proves protocol and plumbing; the exact Qwen/TRL GPU stack found two
   argument-level breakages on its first real run (2026-09-15). Expect more at
   full scale; budget a failed first attempt.

What is measured correctly and should be kept: matched base arm regenerated in
the same container, engine fingerprint and serving stack as arm identity,
paired cluster bootstrap with a pre-registered MDE, gates that void rather than
discount, sealed adapters, the train-only probe before RL, per-kind refusal
vectors beside the scalar.

## 7. How to train to reach the goal

The recipe below is the one the evidence supports today. It is a proposal for
Codex's review, not an approval.

1. **Freeze the primary metric and the benchmark first.** Eval-2 as in step 3:
   ≥300 ordinary tasks reverse-prompted from real captures, no runtime named,
   family/source/AST-grouped split, own lock. Baseline base Qwen on it at k=16,
   thinking off, one pinned container and kernel, before any adapter exists.
   Report SLR, correctness and correct-and-native with by-kind vectors.
2. **Change the training population to match.** Train on ordinary-task prompts,
   never on rewrite prompts. Supervised targets come from the model's own
   verified successes on train families (rejection sampling at k=32, keep ≤4,
   diverse, ceiling cases keep one), plus retention examples where fallback is
   the correct answer, so the model is not taught to avoid imports the engine
   serves.
3. **Let stage 0b choose the sampler.** If the spec lifts SLR ≥10pp, sample
   with the spec and train on bare prompts (distillation); if flat, sample bare
   and rely on the verifier to select, and plan the preference or RL stage from
   the start.
4. **SFT as the first arm, three seeds, LoRA rank 16 alpha 32 on all text
   projections** (the `out_proj` exclusion was an error inherited from the
   earlier model; fixed 2026-09-13). Assistant-only loss, non-thinking template,
   the fixed decoding contract (temperature 0.7, top-p 0.8, top-k 20, presence
   penalty 0). Select the checkpoint on dev by correctness, tie-break by
   correct-and-native. Step 0 is a valid selection.
5. **Probe before RL.** Four generations per train prompt from the selected
   SFT policy. RL only if at least two train groups are informative (mixed
   rewards); report the informative fraction, all-equal groups and truncation
   by family. Dr. GRPO, beta 0, four generations, reward exactly as the
   contract: correct-and-native 1, correct with valid refusal 0.25 on coverage
   and 1 on controls, everything else 0, mismatch aborts. Never reshape reward
   to pay for wrong code.
6. **Decide on eval-2, not eval-1.** Lock settings on dev, run test once per
   arm, paired cluster bootstrap by family component. Promote only on
   correctness non-inferiority (gate A) plus a correct-and-native lower bound
   above the pre-registered margin. Otherwise keep base or SFT and say why.
7. **Keep feeding the engine.** Every verified-wrong native answer is an engine
   bug before it is a training example; every high-frequency refusal kind on
   eval-2 is a runtime candidate ranked by independent families affected
   (`L-TRAINING-ROADMAP.md`). The two loops share one budget and the engine
   loop has the better record.

## 8. Decision state

- Codex's 2026-09-17 review accepts the operator-selected pooled tier with its
  recorded residual risks, holds paid work, and assigns only S0a–S0c to Fable.
- Operator: cost ceiling for eval-2 and for round-02 proper; approval of the
  question-proposals pilot budget; the primary metric freeze in §6 item 2.

## 9. Assessment, 2026-09-16

[ASSESSMENT.md](ASSESSMENT.md) reads this document, the ladder, the
pre-registrations, the reports and the reviews against the goal in §1 and
answers why no run so far could say whether the programme is moving: no
positive control has been run, every adapter was trained at a scale that
installs style rather than a boundary, the training side got the leftover of
the supply, the power table in `EVAL2.md` §7 keys its uniform rows by a
nominal lift the simulation does not realise on a saturated pilot, and the
examples that would carry the signal — verified native rewrites of
correct-but-refused answers — have never been admitted. It replaces §4's
sequence with a signal ladder (S0–S4) whose first three rungs cost nothing,
and an action plan with an owner, a cost and a stop rule per step. Read it
before the next paid step; §4 above stands as history until Codex rules.

Acted on in code 2026-09-16, nothing spent (`ORCHESTRATION.md` ledger row S3):
the §7 mislabel is fixed at its source rather than only described — the power
tool now returns and prints the realised lift in the rule's own unit and counts
the cells the rate cap clipped, so the table cannot be re-printed keyed by a
lift the simulation did not deliver. `EVAL2.md` §7's uniform reading, and the
"more draws and a larger bank" prescription that followed from it, are withdrawn;
its concentrated rows stand. A benchmark eval arm at a k the rule was not priced
at is refused in `preflight`. **§10 is now the live sequence.** Two of the
assessment's own claims were corrected in the process: `mean_effect` is per-case
and is not the unit power is read against, and the concentrated shape clips too.

Acted on in code 2026-09-16, a second time, nothing spent and no frozen artifact
touched (`ORCHESTRATION.md` ledger row S4). The assessment's §4 bucketing — the
table that decides how the budget divides between the engine lever and the model
lever — was a judgement call made once in prose, and nothing in the tree could
re-make it; it is now `pipeline.levers` / `nt levers`: three mechanical layers
(this package's own imports, the engine's closed list imported and never
restated, and what this interpreter does not ship) over a frozen 130-row
declaration table, each row carrying its reason and whether §4 or this tree is
its source. Its `--rows` form reads the eval-2 draw rows unchanged, which is
what makes rung S0b a command rather than a judgement re-made on another device;
its **bare** `--run` form does **not** — it replays through the local binary and
re-derives `native`, hence the population. Since `6044886` there is a third
form, `--run --population-rows`, which freezes status and family to the given
rows and uses the replay only to attach refusal kinds; that is the one §10's
S0b row assigns, and the sentence above is about the form without it. On this tree it
reproduces §4's self-referential bucket exactly and disagrees on the lever
boundary — 36 fewer entries engine-addressable, 41 more legitimate-fallback —
and that disagreement is pinned by a test as the one that was reviewed, not
closed by guessing. Two further $0 items from the same round: a blocked
**evaluation** arm now preserves the program that blocked it, the abort and
every score unchanged, which is the half of `ASSESSMENT.md` §6 step 2 that
carries no policy (the ruling row T4 asks for is deliberately still open); and a
defect pre-existing at `346e59c` was fixed in the candidate-execution boundary,
where under network isolation a harness setup failure was indistinguishable from
the program's own exit 127 — the distinction the verification contract uses to
decide whether a run is a model result at all. The round itself did **not** run:
every rung of §10 is blocked on this device, each on a different prerequisite
(`reports/2026-09-16-fable-sladder-s0-device-audit.md` §1).

Reviewed independently by Codex on 2026-09-17
(`reviews/2026-09-17-round02-full-assessment.md`). The declaration delta above
is superseded by rule 2's reviewed 195 / 242 / 206 / 0 vector; held-out ranking
is refused; the native-timeout policy stays abort; pool headroom, partial-row
durability, Space health and adapter dosage gates are implemented. Historical
text remains here because it records what the S-ladder audit asked, not the
answer it later received.

## 10. The live sequence: the S-ladder

**This is the one home for what runs next**, added 2026-09-16 as step 8 of
`ASSESSMENT.md` §6. Every other ordering in this tree is either history or
mechanism, and each says which it is:

| document | what it still owns | what it no longer says |
|---|---|---|
| §4 above | the dated record of the seven-step plan, and the step numbers `EVAL2.md` cites | what runs next |
| §7 below | the training recipe's mechanism — rank, decoding, the GRPO reward contract | the order the stages run in |
| `LADDER.md` | stage 0a's measured results, the fork definitions, the kill criteria (§6), the reproduction commands | its §5 budget-and-sequence ordering, which the ladder below replaces |
| `NEXT_ROUND.md` | the executable launch order and its flags, which exist nowhere else and which `training/hf/round02_pilot.sh` runs by name | which round to launch, and when |
| `START_NEXT_ROUND.md` | the handoff, the boundaries, the admission gates, the evidence commands (root `CLAUDE.md` names it first for a reason) | the training sequence, one sentence of which now points here |
| `ORCHESTRATION.md` | ownership, the verified-outcome routing table, the decision ledger | its method ordering, which is a priority list and not a plan |

The ladder itself, with the prediction and stop rule for each rung, is
`ASSESSMENT.md` §5 and §6; it is not restated here, because a seventh copy of a
sequence is the problem §3.7 of that document names. In one line each:

| rung | what it measures | cost | state, 2026-09-19 |
|---|---|---|---|
| S0a | `EVAL2.md` §7 re-printed from the real pilot rows, keyed on the realised macro lift | $0 | exact command assigned to Fable in `START_NEXT_ROUND.md`. **BLOCKED because the input is on neither private repository, 2026-09-18**, which is not the same as untransferred: `headforce/lypning-round02-artifacts` holds 86 files over three round-02 job directories and `headforce/lypning-round02-work` holds six, and `eval2_rows.jsonl` is in neither listing (GH run 35399900848, 2026-09-18). The rung is unblocked by re-running the 2026-09-16 pilot that would produce those rows, not by fetching them |
| S0b | the by-kind refusal vector of the 171 correct-but-fallback pilot draws | $0 | rule 2 reviewed; `--rank` is refused on held-out draws. **Re-assigned 2026-09-17**, because `--run` replayed through the local binary and re-derived `native`, hence the population, so it manufactured its own denominator. The corrected command freezes status and family to the pilot rows, uses the replay only for refusal kinds, pins the explicit historical binary by its recorded SHA-256 and requires exactly 171 draws; a partial replay prints no vector. The composite *fingerprint* of that build is not reproducible and is deliberately not a prerequisite. **BLOCKED on the same absent input, 2026-09-18**: `--population-rows` reads that same `eval2_rows.jsonl`, so the 171-draw population is the blocker. **The pinned binary is a second open question and not a solved one** — the S0 inventory lists three `engine-home/bin/lypning-l` paths, but a file name is not a hash, and those three belong to the three 2026-09-18 jobs while line 60 of `START_NEXT_ROUND.md` pins the 2026-09-16 build by SHA-256. Hash each Hub copy before assuming any of them is it; no copy of that build is reachable on this device |
| S0c | the round-02 probe rollouts by native status, per train case | $0 | private artifact and comparison contract named in `START_NEXT_ROUND.md`. Attempted 2026-09-17 without the artifacts: it printed a zeros table at exit 0, and now exits 2 naming the absent path, or 1 on a present-but-empty probe. **BLOCKED on its BASE column only, 2026-09-18 — the probe itself is present.** `headforce/lypning-round02-work` holds `round-02/6aaa87465527934177ee9f34/probe/` with `probe-rollouts.jsonl`, `metrics.json`, `probe.json` and `experiment.json`; the `ABSENT` the S0 inventory prints on that line is a read of `…-artifacts`, the other repository (GH run 35399900848, 2026-09-18). So `--probe` has its input and only `--base` does not, for want of `eval2_rows.jsonl`: this rung needs a transfer, where S0a and S0b need a re-run |
| S1 | stage 0b on the training bank: bare vs `--system-file subset-spec.md`, k=16 | ~$5 | not started; the first positive control the programme would have |
| S2 | the rewritable fraction: verified native rewrites of the fallback draws | tokens | not started. Its population is the 2026-09-16 pilot's 171 + 26 correct-but-fallback draws — the same rows S0a and S0b are blocked for want of — and the bank-v3 repair loop never saw them. Ledger row D1's throughput is bank rows produced, with no denominator in common, and must not be read as S2 progress |
| S3 | stage 5: refusals per 100 programs through opencode on base | harness time | not started; settles the deployment prior |
| S4 | the first SFT with a predicted effect: ≥1,000 cases, ≥50,000 supervised tokens, three seeds, eval-2 at k=16 | ~$60–90 | blocked on S0–S3; the first GPU spend. **The case floor is met on supply since 2026-09-18**: bank v2 admits 1,120 on the split the gates use. The 2026-09-18 job cleared `preflight` at 250 steps and died inside `sft` (`job-manifest.json`: `last_stage sft`, `exit_code 1`, `status failed`, GH run 35399900848, 2026-09-18), so admission is not what stopped it. Which of wall clock, the ≥50,000-supervised-token floor and a kill during the 55.6 GB load did is **UNKNOWN**, and the absent `sft/` directory does not decide it: `run()` creates that directory only after the download, `from_pretrained` and the LoRA attach (`training/tests/test_training.py::test_the_output_directory_is_created_after_the_weights_and_not_before`), so its absence is equally consistent with all three |

**The standing rule, and the only one that matters here: no paid rung runs
before the rung below it has been read.** S0a–S0c and S3 cost nothing and
depend on nothing.

The runner now enforces the per-job parts of all three “stop doing” rules:
confirmatory eval-2 is k=16; real adapter stages require ≥1,000 train cases and
one of seeds 1111/2222/3333; SFT requires one complete family cycle and ≥50,000
scheduled supervised tokens. The three-seed aggregate still requires three
separate jobs and is reviewed from their manifests, never inferred from one.

**Four of those five are `preflight` gates; the token floor is not** (audited
2026-09-17, miscounted as three of four until the review below). The k, the case
count, the seed and the family-cycle capacity are refused by
`train_verified.preflight`, so `--plan` sees them. The ≥50,000 supervised-token
floor is refused in `run()`, because counting the exposures needs
`build_examples`, hence the tokenizer download and the GPU deps `--plan` exists
to avoid. So a passing `--plan` does **not** mean the schedule is admissible,
and `START_NEXT_ROUND.md`'s instruction to plan before every stage cannot be
read as a token-floor check. Since 2026-09-17 `preflight` does refuse the
certainly-too-small half without downloading anything: `supervised_plan` sums
the scheduled references' UTF-8 bytes, a one-sided upper bound on the tokens,
and a plan below the floor exits naming it. **A plan that passes still does not
certify the schedule** — a bound above the floor is only the absence of that
certain failure, and the exact count is taken in `run()`. Ruled in
`reviews/2026-09-17-codex-s0-guards-and-engine-identity.md` §D3, which also
records why the exact floor stays where it is.
`training/tests/test_training.py::test_the_supervised_token_floor_is_a_stage_gate_and_not_a_plan_gate`.
The exact count is made before 27B weight download and recorded as
`planned_supervised_tokens`; `steps × batch_size` is an example count and is not
a substitute token estimate.
