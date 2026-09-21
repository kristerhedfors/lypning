# Round-02 seed 1111 on bank v3: what ran, what it measured, and why "step 0" is not a result

**Job** `6ab01cbb51992417dfccd64c`, h200, started 2026-09-20 17:49:47Z, cancelled
by the operator 2026-09-21 05:41Z after ~11h51m, during the first eval-2 arm.
Commit `7d2bb09`. Bank `banks/v3-20260920b`. Seed 1111, `--steps 300
--grpo-steps 20 --eval-draws 16 --eval-every 25 --patience 3 --rank 16`.
Kernel: torch reference for gated-delta-net (`NTX_USE_FLA=0`, deliberate).

Every number here is read from the job's log or the artifacts it uploaded to
`headforce/lypning-round02-artifacts` under `round-02/6ab01cbb51992417dfccd64c/`
(inventory run `35566251371`-adjacent, 2026-09-21). Nothing is remembered.

## 1. What ran and what landed

| stage | outcome | on the Hub |
|---|---|---|
| prepare pilot / eval2 | ok, 1,355 / 306 / 315 train/dev/test | `pilot/`, `eval2/` |
| `sft --plan` | ok | — |
| eval base-dev | ok | `base-dev/` |
| **sft** | selected **step 0**; ran **75** of 300 steps (patience 3, evals at 25/50/75) | `sft/` incl. `adapter-{0,25,50,75}`, `loss.jsonl`, `evaluations.jsonl`, `best.json` |
| eval sft-dev-reload | ok, identical to base-dev | `sft-dev-reload/` |
| probe | admitted: 286 informative of 1,355 groups, 4,813 correct of 5,420 draws, 3 truncated | `probe/` |
| **grpo** | selected **step 0**; 20 steps | `grpo/` incl. `adapter-{0,5,10,15,20}` |
| test ×3 (base / sft / grpo) | ok — **byte-identical** | `base-test/`, `sft-test/`, `grpo-test/` |
| eval2 ×3 | first arm cancelled after ~2h10m | — |

`job-manifest.json` was not written (cancellation pre-empted the trap).

**Test split, 315 cases, 7 families, 4 draws:** correct 0.8667, correct-native
0.6873, truncation 1.1%, mean completion 188.02 tokens — the same in all three
arms, because both selected adapters are `adapter-0`, a LoRA at initialisation
(B = 0), and `draw_seed(seed, case_id, draw)` is a pure hash. That identity is
the strongest fidelity check the pipeline has produced: adapter load and merge
perturb nothing, and evaluation is deterministic given the seed. It also means
the cancelled eval-2 triplicate would have been three copies of one number.

## 2. The selector is blind to the effect being trained for

`pipeline/training_metrics.py::CheckpointGate.observe` admits a checkpoint only if

- `correct` does not fall below baseline on **each** capability (7 on dev) and
  **each** population (2) — nine no-regression constraints on ~180-draw
  sub-metrics — and then
- `(correct, correct_native)` beats the incumbent lexicographically.

Simulation on this round's dev baseline (`sft/best.json` by-capability rates,
capabilities standing in for families, draws re-sampled at the observed rates,
4,000 trials; `tests/test_gate_admission.py` runs the same experiment against
the real class):

| candidate | admitted at one eval | admitted in any of three |
|---|---|---|
| same model, re-drawn (null) | 2.1% | 6.2% |
| +3pp native, correctness unchanged | 1.9% | 5.7% |
| **+10pp native, correctness unchanged** | **1.7%** | 5.0% |
| +2pp correct and +3pp native | 47.8% | 85.8% |
| +5pp correct and +10pp native | 98.7% | 100% |

A native-only gain of any size is indistinguishable from noise. With
`--eval-every 25 --patience 3`, three rejections end SFT at step 75, which is
exactly where `loss.jsonl` stops. `STATUS.md` §6 item 3 already states that
noise floors exceed effect sizes; the selector then demanded zero noise on nine
sub-metrics simultaneously. **"Selected step 0" is a statement about the
selector, not about the training**, and `ASSESSMENT.md` §3.1's warning applies
verbatim: without a positive control, a null cannot be read as "the treatment
did nothing" rather than "the instrument cannot see anything".

## 3. The SFT objective: learnable, near-empty, under-powered

`sft/loss.jsonl` (`loss_summary.py`, run 2026-09-21):

```
steps 75   loss_first 0.1436   loss_last 0.0534   loss_min 0.0426 @71
mean_first_half 0.1410   mean_second_half 0.1077   drop 0.0334
supervised_tokens_total 56,523   lr 6.67e-07 → 1.67e-05
```

- **Initial loss 0.1436** ≈ perplexity 1.15: the base already reproduces the
  reference programs. SFT rows are `messages + reference` (`training.py:270`),
  so training fitted the last of a residual that was already tiny.
- **The schedule was healthy**: 30 warm-up steps to 2e-5, then linear decay;
  step 75 predicts 1.667e-5, recorded 1.674e-5.
- **2e-5 is a full-fine-tuning learning rate.** Thinking Machines' *LoRA
  Without Regret* finds the LoRA optimum ~10× full FT and ~15× for runs under
  100 steps, approximately independent of rank; *Learning Rate Matters*
  (arXiv 2602.04998) finds LR the dominant LoRA hyperparameter.
- The stop condition fired as designed: patience exhausted **and** 56,523 ≥ the
  50,000-token floor.

## 4. GRPO: about four informative steps

20 optimizer steps, `per_device_train_batch_size=1`, `gradient_accumulation_steps=4
= num_generations`: one prompt per step. The probe found 286 of 1,355 groups
informative (21%), so ~4 of the 20 steps carried a non-zero advantage, at lr
1e-6 (a full-FT number; LoRA RL practice is 5e-6–1e-5, and typical RLVR runs
are ≥ 1,000 steps). Selected step 0 through the same selector. Uninformative.

## 5. The headroom is mostly one case

Dev baseline (`sft/best.json`, 1,224 draws, 7 capabilities):

| capability | draws | correct | native | statuses |
|---|---|---|---|---|
| base64 | 180 | 0.872 | 0.000 | control 153, fallback 4, incorrect 23 |
| collections | 180 | 0.939 | 0.872 | native 157, fallback 12, incorrect 11 |
| enum | 180 | 0.967 | 0.967 | native 174, incorrect 6 |
| heapq | 180 | 0.900 | 0.839 | native 151, fallback 11, incorrect 16, no-code 2 |
| statistics | 144 | 0.951 | 0.951 | native 137, incorrect 7 |
| textwrap | 180 | 0.883 | 0.883 | native 159, incorrect 21 |
| unicodedata | 180 | 0.917 | 0.228 | control 123, native 40, fallback 2, incorrect 15 |

Population macro: coverage correct 0.9211, native **0.7545** (920 draws);
control correct 0.9151, native 0.0039 (304 draws). The coverage native macro
is a family macro over seven coverage "families", one of which is base64's
coverage slice: **one case, four draws, all correct-but-refused, native 0.0**.

```
(0.872 + 0.967 + 0.839 + 0.951 + 0.883 + 0.77 + 0.000) / 7 = 0.7546
```

That single case is ~12.6pp of the "24.55pp headroom" that justified bank v3.
Without it the coverage macro is ~0.88. By status over all dev draws: incorrect
99 (8.1%), correct-but-refused **29 (2.4%)**, correct-control 276, native 818.
The nativeness mass a boundary-installing treatment can move on this dev split
is small; most of the gap is correctness. The eval-2 benchmark (893 cases, 21
families, capped at 45 per family) is exposed to the same fragility; the
cluster bootstrap widens the interval but does not make the point estimate
honest. This is an `EVAL2.md` amendment, not a training question.

## 6. What was fine

- **Architecture.** LoRA r16/α32 on 496 modules (116.7M params): all text-tower
  linears including MLP and the gated-delta `in_proj*`/`out_proj`. All-layer
  LoRA is the documented best practice; rank 16 is well above the rank-1
  sufficiency reported for policy gradient and the rank-1 bottleneck reported
  for RLVR reasoning.
- **Pipeline.** Both preparations, every stage upload, the probe gate, the
  seed determinism proved in §1. The four fixes landed during the run
  (`7d2bb09`, `18dfe4c`, `0733ac3`, `67e06e8`, `21ae28c`, `de01846`) held.
- **Kernel.** `flash-linear-attention` is pinned and installed and *deliberately
  blocked* (`lypning_lora.py:104`). This is an arm identity (`STATUS.md` §2), not
  a defect. The run record now carries `kernels` so the next arm cannot straddle
  it silently.
- **Wall clock** went to evaluation draws (≈1,224 per dev eval, ≈14,300 per
  eval-2 arm), not to optimizer steps; the 480m timeout never fired and its
  enforcement is unexplained.

## 7. What is still unread, and is free

- Dev correct / correct-native at SFT steps 25/50/75 and GRPO 5/10/15/20, from
  `evaluations.jsonl` — whether native moved inside the selector's blind spot.
- Probe rollouts by native status per train case vs the base dev draw (S0c).
- Eval-2 bundle family-size distribution.

Each is a summary job printing aggregates only. `PLAN.md` Step 0.

## 8. Cost

≈11.9h of h200 (~$60), plus `cpu-basic` pool hosts (cents). The three test
arms and every checkpoint are banked; the eval-2 triplicate was not, and would
have measured one model three times.
