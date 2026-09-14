# The signal ladder — what to measure next, and what each rung buys

*Written 2026-09-14, after two adapters moved ΔSLR by nothing. The plan this
implements is the fourth revision of the training review; `REVIEW.md` is the
third, and the run it reads is the one in `AUDIT.md`.*

The failure so far is not that training does nothing. It is that nothing was
measured at a scale where the effect **should** be large. Every rung below is a
yes/no with a directional prediction, ordered so the cheapest question that can
stop the programme is asked first. No rung is skipped, and a "no" stops the
ladder until it is explained.

---

## 0. Ground rules — Qwen, not Nemotron

The target is `Qwen/Qwen3.8-27B` (`SWITCH.md`). Most of the apparatus carried
over from the Nemotron build unchanged: the corpus, the case gates, the frozen
split, the engine fingerprint, `nt legality`, gates A/B/C, the serving-stack
precondition, and the deployment argument — the harness model must be the tuned
model, which means opencode or OpenHands pointed at the tuned Qwen.

Three numbers did not carry over, and two of them were used as decision inputs
anyway. **Retire these:**

| number | where it came from | why it does not apply |
|---|---|---|
| pass@1 35.1%, rewrite slice 17.3% thinking-on vs 9.6% off | Nemotron 3.5 Lightning on vLLM/A100, 2026-09-11 | different model, different serving stack. It was used to argue for thinking-on eval; for Qwen the thinking effect on this eval is **unmeasured** |
| "12 of 74 cases flip between thinking on/off on an identical checkpoint" | the same Nemotron run | it was used as the noise floor. The Qwen noise floors are the two serving-stack nulls: **−0.22pp** and **+1.57pp** ΔSLR (`REVIEW.md` §1) |
| 505,576 thinking tokens vs 22,254 | Nemotron | the Qwen thinking budget is its own measurement |

A Nemotron number is not a weaker Qwen number. It is a number about a different
experiment, and the only safe use for one is history.

## 1. What "adapted to our interpreter" means

One target state, one number:

> An agent running on the tuned Qwen, writing one-liners in opencode or
> OpenHands with **no knowledge that lypning exists**, produces programs the
> pinned engine runs on tier 1 at a rate materially above the base model —
> without getting more answers wrong, without dropping imports the engine
> serves, and without writing longer programs.

The number is **ΔSLR on eval-2** (unconditioned task→program prompts, Stage 4),
with gates A/B/C, the engine fingerprint and the serving stack quoted. Eval-1 —
the existing frozen set, whose prompts carry an explicit rewrite instruction —
measures *compliance*, not the prior, and stays for continuity with two runs of
record.

The state this ladder starts from: two adapters, ΔSLR ≈ 0 on eval-1, by-kind
churn (`class` ↓, `module-attr` ↑) netting to zero, correctness up ~4pp of which
up to ~3pp is a decode-cap artefact.

---

## Stage 0 — ceilings, no training

Two numbers that bound everything downstream, both mostly free because the run
of record stored every program.

### 0a. RL reachability — MEASURED, 2026-09-14

`nt legality <pool> --pass-at-k` replays stored draws through the pinned engine
and asks, per case, whether **any** draw landed in the subset. It generates
nothing: CPU and no money.

This is the ceiling on everything reward-based. GRPO reinforces what the policy
already produces; a case whose every rollout is refused hands it a group of
identically-scored rollouts, an advantage of zero, and no gradient.

Measured on engine `23684d6c40738fcf`, 2026-09-14, thinking off:

| pool | cases requiring tier 1 | reachable (≥1 legal draw) | rewardable (≥1 legal **and** correct) |
|---|---|---|---|
| held-out, `qwen38-baseline-k16`, k=16 | 52 | **84.62%** (44/52) | 42.31% (22/52) |
| train pool, `qwen-full-20260913`, k=16 | 193 | **82.38%** (159/193) | 34.72% (67/193) |

Both are well clear of the ~60% floor. **Reachability does not block a
reward-based stage**, and the Stage 0a half of the kill criterion in §6 does not
fire.

Three readings that the scalar hides, and that the command prints:

*The population is split three ways, not two.* A ceiling case's own test carries
`require_tier1: False` because falling back **is** the right answer there — its
reference solution is the program the engine refuses. Averaging those in scores
the model for failing to do the wrong thing. A third population (this
repository's own task bank) carries no tier clause at all: 8 held-out cases that
ask for a program and never mention a runtime. Those 8 reached the subset
unprompted at **89.06% of draws** — the only preview in this corpus of what
eval-2 will ask at scale, and a hint that the unconditioned prior is already
better than the rewrite-instruction number suggests.

*The rollout budget is the ceiling that matters, not k=16.* Stage 3 plans 8
rollouts per step, so pass@8 is what GRPO stands on: **75.50%** legal and 38.87%
legal-and-correct on held-out; 72.89% and 31.27% on the train pool. The ladder
from k=1 to k=16 is printed with each run.

*Some kinds are at zero and no amount of on-policy training will move them.*
On the train pool `refused:decorator` (2 cases), `refused:generator` (2) and
`refused:walrus` (1) are 0/16 across every draw, and `ceiling:bigint` reaches
1 of 7. Those are teacher work or engine work, not RL work.

**This measurement also cost the engine something.** Replaying 4,608 train-pool
draws turned up **53 MISMATCHes over 14 cases** — programs this engine runs and
answers differently from CPython. Invariant 1 says a MISMATCH is always a bug, so
the train-pool row above is provisional until they are closed; the held-out row
has none. All 14 witnesses are in `data/engine-mismatches.jsonl`, and four
reduce to one-liners: a `'\udcff'` surrogate escape in a literal is a
`SyntaxError` here and a string in CPython; `enumerate(iterable=…, start=…)` is
rejected here and accepted there; `v is v` for a bound `dict.values()` is False
here and True there; `divmod(1.0, 0)` says *float floor division by zero* where
CPython says *float divmod()*.

A fifth finding is about the instrument rather than the engine: 6 of the original
59 were not the engine's fault at all. Every sandbox run gets its own `mkdtemp`,
so `print(os.path.abspath(...))` differs between the reference run and the engine
run **by construction**, and so does anything reading the clock or the hash seed.
Called MISMATCH, each is invariant 1's alarm fired by the harness on a corpus
nobody would think to vet for determinism, because a model wrote it. A first
disagreement now buys one more CPython run: if the reference disagrees with
itself the verdict is `UNSTABLE` — legal, since the engine did run it, never
correct, since nothing is, and never a MISMATCH.

### 0b. Prompt ceiling — not yet run (~$5)

Base Qwen on eval-1, thinking off, pinned stack, with the subset spec in the
system prompt — a compressed `docs/SUBSET.md`, or the thirty refusal kinds each
with a one-line recipe. `docs/PROMPTING.md` measured nine treatments on
2026-08-23; re-run the strongest on the current frozen set, as a diagnostic arm
with its own `prompt_sha`.

- **SLR up ≥10pp** → the boundary is elicitable from a description. The model
  can stay in the subset when shown the map and training has never shown it one.
  Spec-distillation (sample with the spec, train on bare prompts) is live, and
  Stage 1 should use it.
- **SLR flat** → a 30-kind boundary cannot be held from a description at this
  size. Skip distillation entirely; only a signal that punishes every kind
  (Stages 2–3) installs it.

**Stop rule.** 0a under ~50% *and* 0b flat means the model cannot write this
subset and no LoRA will make it. 0a came back at 82–85%, so this rule cannot fire
on reachability; 0b still has to be run on its own account.

---

## Stage 1 — can the adapter move SLR at all? (~$15)

Nobody has reported **SLR on the training pool**. That is the first number any
fine-tune should produce, because it separates "the pipeline is broken" from
"the data does not generalise" — two failures with different fixes and identical
symptoms on held-out.

**1a. Train-pool SLR.** 16 draws per case on the 63–65 rewrite cases the SFT rows
came from, tuned arm and base arm, same stack (~$5). If the pipeline works this
should rise a lot: these are the exact prompts whose legal answers the adapter
saw and memorised to loss 0.03.

- Large rise → the pipeline is sound and the problem is generalisation. Proceed.
- No rise → **stop**, and work §4 before anything else.

The base-arm half of 1a is already on disk and measured: the train-pool row in
0a above. What is missing is the tuned arm over the same prompts.

**1b. Single-kind ablation.** Build an SFT set from `class` rows only, train,
evaluate on eval-1. `class` qualifies on all three counts — every variant refuses
it, it is always rewritable, and it has ≥10 held-out cases — and it is the kind
the churn shows the model already moving away from.

- `class` ↓, everything else flat → the technique installs a boundary point per
  kind; the 30-kind problem is data balance and Stage 2 is a scale-up.
- `class` ↓, another kind ↑ → confirmed avoidance rather than boundary. Stage 2
  with that finding in hand.

This is the cleanest yes/no in the plan: a directional prediction with a built-in
control, and no new statistical machinery.

## Stage 2 — negative signal (~$15)

Uses the 9,280 draws already on disk. Within each case, pair chosen =
legal-and-correct against rejected = refused, preferring a rejected draw from a
*different* refusal kind than the case's own, so the pair says "not that either".
DPO or ORPO from base, same LoRA config, two seeds.

Read, in this order: **by-kind churn** (does `module-attr` stop rising when
`class` falls — the collapse of the swap is the signal, whatever the scalar
does), then train-pool SLR, then held-out SLR on eval-1.

## Stage 3 — on-policy, with the signal watched live (~$50–80)

GRPO from the best Stage 1/2 checkpoint. The verifier is the reward; there is no
reward model to train or game.

| outcome | reward |
|---|---|
| legal and correct | +1.0 |
| legal, wrong output | +0.5 |
| correct but refused | +0.1 |
| wrong and refused | 0 |
| dropped a supported import where the reference keeps one (gate B) | −0.3 |
| length above reference × 1.2 (gate C) | −0.1 per 20% |

**Any** refusal kind is penalised, not just the case's own — the amendment the
churn earned. 64 prompts × 8 rollouts per step, a few hundred steps, one H200;
reference logits from the same weights with the adapter disabled, so no second
54 GB copy. Two seeds.

One thing 0a adds to this table before it is run: **the ceiling cases must not be
in the prompt pool under this reward**. 97 of the 290 train-pool cases are ceiling
cases whose reference solution is the program the engine refuses, and a reward
paying +1.0 for legal-and-correct pays the model to work around the right answer
on every one of them. 0a reports that column separately for exactly this reason
(39 of 97 already reach the subset), and Stage 3 should either drop them from the
pool or invert their reward.

The in-run signal is the reward curve **disaggregated by refusal kind**, logged
every step — the only place in the programme where "are we on track" is a curve
rather than a before/after. All kinds trending down is the boundary being
learned; one down and another up is churn surviving into RL; legal-but-wrong
rising faster than legal-and-correct is hacking toward trivial programs; length
falling with legality flat is the model finding gate C easier than the task.

## Stage 4 — eval-2, the deployment quantity (~$40, parallel from day one)

Reverse-prompt corpus entries into short natural-language instructions with no
mention of lypning, refusals or a runtime; ≥300 cases drawn without regard to
whether base refuses; its own lock, its own `prompt_sha`; both arms baselined
before any adapter touches it. This is the first point where a positive ΔSLR is a
claim about deployment rather than about the eval prompt, so it should be under
construction while Stages 1–3 run.

## Stage 5 — real sessions (~$0 GPU)

The instrument already exists: `lypning install --harness opencode` plus the
capture and routes ledgers. Point opencode at the tuned Qwen, run a fixed script
of agent tasks, and read **refusals per 100 programs** off `lypning routes
--plan` against the same script on base Qwen. That is the number the README
should eventually carry beside the bench table.

---

## 3. The signal dashboard

Every training arm reports this table. A row that does not move where the "on
track" column says it should is a stop, not a footnote.

| signal | stage | cost | on track looks like | if not, it means | status |
|---|---|---|---|---|---|
| pass@16 legality, by kind | 0a | $0 | ≥60% of cases reachable | RL has nothing to reinforce on the rest | **84.6% held-out, 82.4% train, 2026-09-14** |
| prompt-spec SLR vs bare | 0b | ~$5 | +10pp or more | boundary not elicitable → skip distillation | not run |
| train-pool SLR, tuned vs base | 1a | ~$5 | large rise | pipeline mismatch — §4 | base arm measured; tuned arm not run |
| single-kind ablation | 1b | ~$10 | target kind ↓, others flat | avoidance, not boundary | not run |
| by-kind churn (net swap) | 2, 3 | $0 | the swap collapses | SFT/DPO too thin | not run |
| GRPO reward by kind | 3 | in-run | all kinds trending down | churn / hacking / length shortcut | not run |
| held-out ΔSLR, eval-1 | 1–3 | ~$6 | positive, CI clear of both nulls | compliance unmoved | −1.00pp, null −0.22pp |
| gates A / B / C | all | $0 | pass | a degenerate strategy was found | all pass on the run of record |
| ΔSLR eval-2 | 4 | ~$6 | positive, MDE met | compliance ≠ prior | eval-2 does not exist yet |
| refusals / 100 programs, live opencode | 5 | harness time | below base | eval-2 not representative | not run |
| engine fingerprint + serving stack | all | $0 | identical across arms | it is not a comparison | enforced by `legality.compare` |

---

## 4. The Qwen training checklist — before Stage 1a

These would make 1a fail for reasons that have nothing to do with the data. Each
is a test in `tests/test_sft_rows.py`, not a belief.

- **Completion-only loss.** Every prompt token's label is −100 and every
  completion token's is not. On 246 short rows, training on prompt tokens
  dominates the gradient. *Pinned — and the slice it relies on is now checked:
  masking `len(tok(prompt))` tokens is completion-only loss only if the prompt's
  tokenisation is a prefix of the whole turn's, and a tokeniser that merges
  across that boundary shifts the mask by one token with a perfectly healthy
  loss curve. `build_examples` now refuses instead.*
- **The empty think block.** With `enable_thinking=false` the Qwen3 template
  emits an empty `<think></think>` at the head of the assistant turn. Whatever
  the eval path produces there, the training rows must contain byte for byte.
  *Pinned two ways: the rendered prefix, and the three keyword arguments of every
  `apply_chat_template` call in the GPU script.*
- **Tokeniser round-trip.** Every SFT completion decodes to the program that
  passed the verifier — through the **eval's** extractor, not a second one
  written for training. *Pinned over every shipped SFT set.*
- **LoRA targets.** 496 modules covers attention + GDN. The MLP projections
  (`gate_proj`, `up_proj`, `down_proj`) matter more than attention for a prior
  shift. *Checked: they are in `TARGET_MODULES`, and so is `out_proj`, which this
  project already paid to learn about. Pinned.*
- **Steps and LR.** 0.229 → 0.03 in 48 steps is memorisation. Fine for 1a, which
  is supposed to memorise. From Stage 2 on, hold out a train-side validation
  slice and stop on it — never on the frozen held-out.
- **Kernels.** Training on torch-reference (fla #640). Generation on whatever
  stack is pinned for that comparison, and both arms of any comparison on the
  same one. The +1.57pp kernel null is the reason.
- **Thinking.** Primary eval thinking-off, matching deployment. Whether
  thinking-on helps *Qwen* here is unmeasured; measure it once as a secondary arm
  if budget allows, never assume it.
- **Seeds.** Two per arm from Stage 2 on; report the spread.

---

## 5. Budget and sequence

| stage | GPU / API | wall time | decision it buys |
|---|---|---|---|
| 0a reachability | $0 | hours | is RL possible on this model at all — **done** |
| 0b prompt ceiling | ~$5 | hours | is the boundary elicitable |
| 1a train-pool SLR | ~$5 | half day | pipeline vs data |
| 1b single-kind ablation | ~$10 | one day | boundary vs avoidance |
| 2 DPO | ~$15 | one day | does negative signal fix the churn |
| 3 GRPO, two seeds | ~$50–80 | 2–3 days | can the boundary be installed |
| 4 eval-2 | ~$40 | 1–2 days, parallel | the deployment quantity |
| 5 live sessions | harness time | half day | the README number |
| **total** | **~$130–160** | **~1.5 weeks** | |

Roughly three times the two runs so far, and every dollar past Stage 0 is spent
only after the previous rung said yes.

---

## 6. Kill criteria — when to stop training and grow the engine instead

Two loops reduce refusals: teach the model the subset, or teach the engine the
model. The second found six silent wrong answers this week and served `math`,
`type()` and `%.2d` from on-policy evidence — and Stage 0a alone added fourteen
more cases to `data/engine-mismatches.jsonl`. The first moved ΔSLR by nothing,
twice. Training is not owed a result.

Stop the training programme and put the budget into the engine if any of these
hold:

- Stage 0a under ~50% **and** 0b flat. *0a is 82–85%: this half cannot fire.*
- Stage 1a rises, 1b shows pure avoidance, Stage 2 does not collapse the churn,
  and Stage 3's by-kind curves do not converge within ~200 steps.
- Stage 3 converges on eval-1 but eval-2 ΔSLR is flat across two seeds: the model
  learned the rewrite instruction, not the prior, and the deployment quantity is
  out of reach at this data scale.

None of these is a failure of the apparatus. The measurement now says cleanly
when you have not won, which is what makes it safe to try.

---

## 7. Reproducing the Stage 0a numbers

```bash
lypning build --rust                       # the numbers are relative to a binary
cd nemotron
./nt legality qwen38-baseline-k16 --pass-at-k --jobs 12   # held-out
./nt legality qwen-full-20260913  --pass-at-k --jobs 12   # the 290-case train pool
```

Both take a run id, an SFT draw pool under `data/sft/`, or a path to a JSONL of
draws, and both print the engine fingerprint they are relative to. `--cache DIR`
stores the replay so the aggregation can be re-cut without re-running the
programs; it is reused only when the engine fingerprint **and** the grader
version both still match. The exit code is non-zero when the pool is under the
floor or when any MISMATCH is outstanding.
