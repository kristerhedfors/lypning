# Independent review: Step 2 positive control and S4 readiness — 2026-09-22

**Verdict: REVISE (prepare).** No GPU spend until three things exist: the
coverage-only grade of run `35767396604`, the seed-1111 kernel read, and an
operator decision on a full-split target rung and on evaluation draws. The
smoke shows a real native effect under the subset spec. It does not support the
launch the report prepares. After this PR's case floor, no existing target set
can launch arm A.

This is the independent assessment asked for by item 2 of *Remaining gates* in
[`reports/2026-09-22-step2-positive-control-and-s4-readiness.md`](../reports/2026-09-22-step2-positive-control-and-s4-readiness.md).
The shape follows `ORCHESTRATION.md` *Fable writeup → Codex assessment contract*.

## 0. Who is reviewing, and what was read

- **Reviewer.** Claude (Opus 5.5), on branch `claude/s4-training-refactor`.
  - Independent of the Codex session that dispatched the runs and wrote the report.
  - **Not independent of the code in this PR.** This session orchestrated the
    refactor lanes. Each lane had its own adversarial reviewer; those verdicts
    are summarised in the PR body, not here.
- **Read.**
  - The report, which is committed verbatim in this PR.
  - PR bodies #102–#111. They were read-only. None records an operator approval
    of a paid ceiling.
  - `gh run view` conclusions and head SHAs for all seven Step 2 runs.
  - The public aggregate log lines of grade `35759939928` and generation
    `35767396604`.
  - The code at `b96bbfe`.
- **Not read.** Any private row, completion or target. The adapter and
  experiment files of seed 1111.
- **Approval.** The paid runs were dispatched by Codex under the per-rung
  ceilings in `step2-control.yml`. The approval is not recorded in this tree.
  PLAN's own operator direction on 2026-09-22 was "free first … no paid ceiling
  is approved", and nothing after it records a change.

## 1. Provenance and identities

| run (2026-09-22) | workflow · head | outcome |
|---|---|---|
| `35749197934` | generate · `2cfed80` | failed before generation; fixed in #105 |
| `35751938025` | generate smoke · `57f63b7` | 64 cases × 2 arms × k=4 = 512/512 requests, zero retries; **$1.20715678** charged or reserved against a $5 ceiling |
| `35758545464` | grade · `72688a5` | failed before executing a candidate (oracle identity); fixed in #108 |
| `35759939928` | grade · `8da81c4` | all 512 completions scored in networkless containers; figures in §2 |
| `35762924601` | S4 target preflight · `82a28bb` | tokenizer `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`; target digest `788b98d4…`; 156,691 scheduled supervised tokens against a 50,000 floor |
| `35763603648` | generate targets, 60 rpm · `7797830` | stopped after 327/1,536 on an opaque provider error; **$0.90358492** charged or reserved against $6; not gradeable |
| `35767396604` | generate targets, 45 rpm · `dd5cb9c` | **complete** 1,536/1,536, `failure_types []`; **$3.63061517** charged or reserved against $6; references 192/192 verified (159 correct-native, 33 correct-control); **not yet graded** |

- **Population.** The seed-1111 train split of `banks/v3-20260920b`: 1,355
  cases in 31 families (free admission `35693996662`). Dev and test were never
  drawn.
- **Case selection.** Family-balanced round-robin (`positive_control.stratified_population`).
- **Model.** The provider model is Cerebras `qwen-3.8-27b`. The trained model is
  HF `Qwen/Qwen3.8-27B` (architecture class `qwen3_5`) at the pinned
  `QWEN_REV`. **Nothing shows these are the same weights, template and decode.**
  - Thinking is off through `reasoning_effort: none`, not through the HF
    template's `enable_thinking=False`.
  - `top_k` is omitted: "hosted endpoint does not promise it". The trainer's
    decoding contract sets `top_k=20`.
  - So the targets are near-policy, not the model's own draws in the ReST-EM
    sense.
- **Grade provenance.** The smoke grade ran before this PR, so its `comparison`
  and `decision` **pool controls with coverage**. After this PR, both keys are
  coverage-only under an unchanged schema number, so the smoke's figures are
  not the same quantity as any later grade.

## 2. Correctness AND native, with the uncertainty the run can support

Grade `35759939928`. The deltas are conditioned minus bare, family-macro, with a
paired source/family-component percentile bootstrap (2,000 resamples, 30
independent clusters, smallest family 1 case). The log itself labels the method
"exploratory with few clusters". These pooled figures are what that run
computed:

| metric | bare | subset-spec | delta, 95% cluster CI |
|---|---:|---:|---|
| correct-native | 54.03% | 68.82% | **+14.78pp** [+3.89, +26.81] |
| correct | 85.08% | 76.08% | **−9.01pp** [−17.34, −2.02] |
| mean completion tokens | 162.38 | 129.42 | −32.96 |
| truncation | 0% | 0% | — |

Draw statuses (256 per arm) show where the native gain came from:

| status | bare | subset-spec |
|---|---:|---:|
| correct-native | 133 | 163 |
| correct-fallback (correct, refused) | 41 | **0** |
| correct-control | 44 | 31 |
| incorrect | 38 | **61** |
| no-code | 0 | 1 |

`distillation_route=false` and `confirmatory_signal=false`. **The spec removed
refusals by rewriting.** All 41 correct-but-refused bare draws disappear, and 23
more draws become wrong. 13 of the spec arm's correct-native draws are on
**control** cases: the target builder rejected them as `control-became-native`.
The pooled native delta counts them as gains.

The public report does not say how many bare draws on control cases ran
natively, so the coverage-only delta cannot be recomputed from it. That is the
first thing the regrade in §6 reports.

## 3. What supports the report's reading

- **A native effect exists under the spec.** The pooled interval excludes zero
  by 3.89pp, and the status table shows the mechanism directly
  (correct-fallback 41 → 0).
- **The rejection filter is the right response to the correctness cost.**
  Targets are the spec's correct-native coverage programs and correct non-native
  control programs, trained behind the bare prompt. None of the 61 incorrect
  draws can enter.
- **The plumbing held.** 512/512 and then 1,536/1,536 paid requests had zero
  retries. The partial run failed closed and stayed ungradeable. Grading reused
  the admitted oracle and rehashed the engine. The private/public split held in
  the log lines read here.

## 4. What contradicts it, or is not yet evidence

1. **The decision counted controls.** See §2. The smoke's +14.78pp is not the
   coverage-only quantity the decision now reads.
2. **The smoke is 64 cases at k=4.**
   - With 30 clusters and single-case families, a percentile bootstrap
     undercovers.
   - At k=4 the "keep ≤ 4" cap never binds. In the log, 149 coverage targets
     were eligible before the cap and 149 were kept after it. So nothing was
     *selected*: every verified draw that was not an exact duplicate was kept
     (17 duplicates dropped).
3. **The rungs are nested, so the 192-case rung is not a replication.**
   - `stratified_population` is a deterministic round-robin, so
     s(192)[:64] == s(64).
   - Requests carry `seed = 1111 + draw` with identical messages, so 512 of the
     rung's 1,536 requests repeat the smoke exactly.
   - Whether Cerebras honours `seed` is unverified. If it does, about a third
     of `35767396604` re-bought the smoke's completions.
   - Either way, its grade contains the smoke's cases and must not be read as
     independent confirmation.
4. **157 rows over 54 cases cannot launch arm A.**
   - The existing `MIN_TRAIN_CASES` (1,000) now counts the distinct cases the
     targets train on (`train_verified.curriculum_floor`).
   - `35762924601` cleared the token floor only by repetition: 300 steps × 4 =
     1,200 exposures over 157 rows is about 7.6 passes. The token count was
     also taken on the old schedule (with-replacement family cycling) and the
     old builder (no AST dedup, no length drop).
   - The largest existing rung has 300 cases, so none can reach 1,000.
5. **Training only on spec-conditioned wins may move the spec's correctness
   cost into the bare policy.**
   - The targets teach "write in the subset" from the cases where that worked.
   - The 23 extra wrong draws are the cases where the same move failed.
   - An adapter that learns the move without the judgement would reproduce
     both.
   - The filter keeps wrong programs out of the loss. It does not keep the
     behaviour that produced them out of the policy.
6. **The report's prepared recipe is superseded by this PR.** Its selection is
   now on the coverage macro against a paired, case-clustered margin, not the
   all-family macro. Its launch is now refused by the case floor. Seeds
   2222/3333 now share the 1111 split, which answers the report's "separate
   decisions".

## 5. Alternative explanations to hold open

- **Refusal avoidance, not capability.** The spec may teach the model to avoid
  unsupported imports rather than to express the task natively. The bare arm's
  133 correct-native draws (of 256) show the model already writes native code
  for about half its draws on these cases. A bare-arm target set (ReST-EM on the training prompt;
  [arXiv 2312.06585](https://arxiv.org/abs/2312.06585),
  [arXiv 2308.01825](https://arxiv.org/abs/2308.01825)) is the on-policy
  comparison. `build_targets` supports it (`target_arms`).
- **Provider stack.** A different serving stack or decode could make spec and
  bare differ for reasons that do not transfer to the HF checkpoint. Only an S4
  read on the HF model answers this.
- **Family-balanced sampling.** 64 cases over 31 families weights small
  families up. A full-split rung weights by case, so its effect need not match.

## 6. Cheapest discriminating next reads, in order (all free unless marked)

1. **Grade `35767396604` once, after this PR merges.**
   - Use `rung=targets`, the coverage-only decision and the `QWEN_REV`
     tokenizer.
   - Decide `target_arms` first, because a second grade overwrites
     `positive-control/<run>/grade`.
   - It reports coverage-only native and correctness deltas plus
     `control_comparison`. Its 192 cases include the smoke's 64.
2. **Run `s4-target-preflight` on it.**
   - **Expected: refusal on the case floor** (at most 192 cases).
   - Still read unique supervised tokens, passes over rows and the family floor
     from `s4-target-floor.json`.
3. **Seed 1111 kernel binding.** Read the `kernels` field of `sft/experiment.json`
   under `round-02/6ab01cbb51992417dfccd64c/` and grep its log for the
   torch-reference fallback line. This is an aggregate CI read.
4. **Draw coupling.**
   - Measure per-(case, draw) agreement between step 0 and later steps in seed
     1111's `sft/evaluations.jsonl`, printing aggregates only.
   - Evaluation seeds are shared across steps (`chunk_seed`), and the gate's
     power depends on how coupled those draws stay after training.
   - Simulation in `training/tests/test_gate_admission.py` (2026-09-22) shows
     what is at stake. For +10pp native at k=4 with independent draws, the
     admission rate is 38.0% (KAPPA 2) and 63.5% (KAPPA 20), over 2,000 trials
     with seed 7. With fully coupled draws it is 99.9% and 100%, over 1,500
     trials with seed 9.
   - `--eval-draws 16` recovers 75.3% and 97.7% (600 trials) at four times the
     dev-eval generation cost. Measure the coupling before buying it.
5. **Does the provider honour seed?** Count byte-identical completions between
   the smoke and the first 64 cases of `35767396604`, printing only the count.
   If they match, every nested rung re-buys its prefix.
6. **Operator: a full-split target rung.**
   - Arm A needs verified targets on ≥ 1,000 of the 1,355 train cases.
   - Scale from `35767396604`: $3.63 for 1,536 requests is about $0.0024 per
     request. So 1,355 × 2 arms × k=4 = 10,840 requests is about $26. At 45 rpm
     that is about 241 minutes, over the rung's 240-minute window, so it needs
     sharding.
   - A spec-arm-only draw of 5,420 requests is **not** half the cost. The
     2026-09-22 plan found input cost "dominated by repeating the subset spec"
     (`ROUND_READINESS.md`). Price it with `step2_bank_plan.py` before asking.
   - Neither shape is implemented. The floor was not lowered.

## 7. Decision

**REVISE (prepare).**

**Bounded next action.** Merge this PR. Then do §6.1–§6.5 in order: one
coverage-only grade, one floor read, and three aggregate CI reads. Record each
in `PLAN.md` Step 2 and a ledger row. Then put two decisions to the operator,
each with its measured price: the §6.6 rung shape, and eval draws 4 or 16.

**Owner.** Codex, as orchestrator, for the reads and the ledger. The operator
for the paid rung, the eval-draw cost and any GPU ceiling. Fable only after a
GPU ceiling is recorded.

The operator also owns one publication decision. This PR's `case_clusters`
puts per-case counts, including eval-2's, into `metrics.json` and `best.json`,
and `s0_inventory` prints both files whole into a public log. There are no ids
and no text, but the outcome distribution is exposed. Either allow it or strip
the key there before the first S4 seed.

**Inputs.**
- Merged `main`.
- Private runs `targets-dd5cb9cb2e47d64bfad519ebeba6571a52b2a0fd-35767396604`
  and `smoke-57f63b7e68c4e341fe90de1aa6f7f76a2241e072-35751938025`.
- Seed 1111's saved artifacts under `round-02/6ab01cbb51992417dfccd64c/`.
- `QWEN_REV` `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`.

**Stop criteria.**
- **Coverage-only native CI includes zero** on the regrade: the
  spec-distillation arm A is not earned. Fall back to bare-arm (ReST-EM)
  targets or to Step 3.
- **Native up with correctness down:** PLAN's new third branch applies.
  Rejection-filtered context distillation stays exploratory, and the S4 dev
  read must hold gate A (−2pp all-family correctness) and control retention.
  An adapter that fails either is not selected, whatever its native gain.
- **The full-split rung yields verified targets on fewer than 1,000 cases:**
  arm A does not launch. A lower floor is a dated protocol decision, never an
  edit to make a launch fit.
- **Seed 1111 bound fla:** record that its arm identity was not the declared
  torch reference. This does not block the next arm, because the run now
  refuses any other binding.
- **No approval text for a paid ceiling is on record:** no further paid
  dispatch.
- **In every case:** re-prepare bundles before arm A's first seed, because
  `verifier_sha256` and `code_sha256` changed in this PR.
