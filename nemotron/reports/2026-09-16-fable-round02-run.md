# Fable round report — round-02 pilot, completed run

## Outcome and decision requested

Date 2026-09-16. Round `round-02`, stage **pilot**. Status: **completed** (job
`6aaa87465527934177ee9f34`, h200, commit `3b0d4f064bd2195aedf17de036a2596a25c47039`,
the ninth GPU attempt of the day; eight earlier attempts and what each one
taught are in *Failures* below). A GPU run occurred: SFT and GRPO ran on the
real `Qwen/Qwen3.8-27B` weights, the probe admitted GRPO, and the three arms
(base, SFT, GRPO) were measured on the pilot bundle's test split and on the
whole eval-2 bank v1 at k = 4.

Status at report time (2026-09-16 14:40 UTC): the job is running its
eval-2 arms. The base arm (300 cases × 4 draws) started 14:18 UTC; the SFT
and GRPO arms follow, each about 55 minutes, each uploaded to
`round-02/6aaa87465527934177ee9f34/<arm>-eval2/` as it ends, and the job's
own report step pairs them into `reports/base-vs-sft-eval2.json` and
`reports/base-vs-grpo-eval2.json`. **The number of record is therefore not
in this writeup**; it is in those artifacts when they land, and the command
to read them is in the handoff. What has landed: SFT and GRPO ran on the 27B
weights; the probe admitted GRPO; GRPO did not move the policy; the 7-case
test split shows no gain (correct-and-native delta 0.0pp [−10.7, +10.7]).

Decision requested of Codex: when the eval-2 arms have uploaded, pair
them (handoff) and read the §4 rule; then decide whether the SFT target
(terse captured references, which the adapter imitates to the point of
losing correctness after step 5 on dev) is the right teacher, and whether
policy v3 (a self-disagreeing candidate scores as incorrect) stands. No
model-quality claim is made here beyond "no gain on 7 test cases".

**Correction, 14:52 UTC, after the first version of this report.** The base
eval-2 arm was **blocked** at 14:50 UTC after 384 of 1,200 draws:
`engine mismatch: e2-590f99f145c4 test 0`, the CPython oracle correct and the
engine run timed out at 5 s (observed `[None, "", "", timed_out=True, …]`).
The job failed at stage `eval2`, uploaded what existed (manifest status
`failed`, last stage `eval2`), and **no eval-2 arm completed; the SFT and GRPO
arms never ran.** The case is a CPU-bound float recurrence (`float-recurrence-wrap`,
N iterations from argv); the reference passed natively at preparation, run
alone. Two explanations, not yet separated: the engine is slower than CPython
on tight numeric loops and the model's program is heavier than the reference;
or 16 concurrent sandboxes on one `cpu-basic` pool host starved the run past
its 5 s budget, which preparation (sequential) and the light dev/test
programs would not show. The verifier policy reads a native timeout after a
correct oracle as an engine fault and stops the stage (`ORCHESTRATION.md`
ledger, "never teach avoidance of a runtime bug"). What did land of the base
arm, 384 draws over 96 cases in 87 families: 87.4% correct, 69.8%
correct-and-native, family macro, in line with the pilot draw (§11). Not a
number of record: a partial arm, unpaired.

## Reproduction and authority

- Repository commit `3b0d4f064bd2195aedf17de036a2596a25c47039` on
  `claude/next-round-7dilm6` (merged base `8ac8c64`, PR #79). Report version 1.
- Ceiling stated to the operator: about $100 for the round; h200 at $5/h,
  per-job cap 8 h on the final attempt (6 h on the earlier ones). Observed:
  nine h200 jobs, about 10.5 GPU-hours in all by the
  launcher logs' start and end times (the Hub lists no end times), about $52
  at $5/h, plus the serverless pilot draws (1,024 + 268 redraws, cost not
  itemised by the router). Job 9 alone is projected at about 5 h.
- Base model `Qwen/Qwen3.8-27B` at Hub revision
  `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`; tokenizer, chat template and
  decoding as recorded in each stage's `experiment.json` (temperature 0.7,
  top-p 0.8, top-k 20, thinking off, 1024 new tokens); Python
  `3.12.14 (main, Sep 1 2026) [GCC 14.2.0]` in the job; torch 2.9.1+cu128,
  transformers 5.17.0, trl 1.13.0, peft 0.20.0, huggingface-hub 1.31.0 (pinned
  in `gpu/train_verified.py`). Fused kernels deliberately off
  (`NTX_USE_FLA=0`): gated-delta-net on the torch reference.
- Engine `lypning-l` sha256 `a23b30832e00640cec2090d8403a6beeaa2087083d0fbd9080210fd8d4fc1096`,
  fingerprint `2e079e786a655ab6`, downloaded from the verifier Space at the
  pinned commit. Verifier Space `headforce/lypning-round02-verifier` at
  `6d287057e491e9edc22b677e2bf0dd5200f9b40a`; candidates ran in pooled
  sandboxes booted from that Space's image (uid ≥ 20000, no HF token, see the
  four execution witnesses in `execution-witnesses.jsonl`); every response
  carried the admitted identity (worker `d9ccf333…`, sandbox.py `70276f2e…`,
  child_exec `54c124cb…`).
- Verifier policy `l-correctness-v3` (this round; v2 until the probe of job
  `6aaa73e9`, see *Failures*), verifier module sha256 `9481650c870141ce…`.
- Bundles, prepared in the job through the pool (every reference run twice):
  pilot `652f0da5c3ad7aeb82cbd61f427fb3c3c923d4222c089c1e5e1b7fb75b5ac18e`
  (64 cases: 50 train / 7 dev / 7 test), eval-2
  `8ae4056ef0e9ea08e11466489c0be384f92dcafa914c74c0d532b653c31da562` (300
  cases, measured whole). Banks: `banks/2026-09-16-eval2-v1/` in the private
  dataset `headforce/lypning-round02-work` (eval2.jsonl sha256 `46cff1d7…`,
  train.jsonl `31edda65…`, EVAL2.md §11).
- Probe digest `bd74c41efd61779d7ba3615e5cd20510681f0ff2e61cf63b1e139fb201ba0f13`,
  records sha256 `acf21686…`. Selected checkpoints: SFT step 5 (of 20,
  eval every 5, patience 3, rank 16, batch 4, lr 2e-5), GRPO step 0 (of 20,
  4 generations, lr 1e-6): no GRPO checkpoint beat its warm start on dev.
- Seeds: 1111 everywhere (review, preparation, training, evaluation chunks).
  Evaluation: 128 sequences per generate call, 16 concurrent scorings, k = 4
  on eval-2 (EVAL2.md §6 on chunk pairing; §4's k = 16 is for a confirmatory
  run and this pilot says so).
- Commands: `nemotron/hf/launch.py pilot --branch claude/next-round-7dilm6
  --commit 3b0d4f0… --space headforce/lypning-round02-verifier --space-revision
  6d287057… --qwen-revision 1d4bf0f2… --work-repo headforce/lypning-round02-work
  --bank-path banks/2026-09-16-eval2-v1 --steps 20 --eval-draws 4
  --eval-sequences 128 --score-workers 16 --seed 1111 --flavor h200 --timeout 8h`,
  which runs `nemotron/hf/round02_pilot.sh` on the job.
- Private artifacts: `round-02/6aaa87465527934177ee9f34/` in the work dataset
  (uploaded after every stage; the final manifest is `job-manifest.json`).
  Earlier attempts' partial uploads sit under their own job ids. Nothing
  secret or raw is in this writeup.

## Data and hypothesis

Question (EVAL2.md §1, §9): does a small SFT on 50 captured, verified tasks
raise first-draft correct-and-native on ordinary, unconditioned tasks the
model has never seen, by more than the rule's +3pp lower bound? Predicted
before the run (pilot report, same day): most likely no detectable gain, with
the full pilot draw having shown about 19 points of correct-but-fallback
headroom on the training bank (EVAL2.md §11) and the power curve (§7) able
to see a concentrated +5pp at N = 300 but not a uniform lift under +10pp.

Population: eval-2 v1, 300 cases in 248 families and their split components
(18 families span source groups), coverage and fallback-control populations
as tagged; training bank 64 cases (49 families), spent for eval-2. Prompts:
ordinary task text, never naming a runtime (RUNTIME_NAMES lint at assembly).
First drafts only; no repair, no feedback, no pass@k in the primary numbers.
No held-out case influenced any recipe choice: the recipe was fixed in the
pilot script before the bank was frozen, and the only knobs changed during
the day (eval draws, sequences per call, scoring concurrency, policy v3) were
changed for cost or for a stage abort, never after reading an eval-2 number.

## Measurements

Dev split of the pilot bundle (7 cases, 4 draws each, family macro):

| arm | step | correct | correct-and-native | mean tokens |
|---|---|---|---|---|
| base | 0 | 78.6% | 75.0% | 297 |
| SFT | 5 (selected) | 82.1% | 75.0% | 238 |
| SFT | 10 | 82.1% | 67.9% | 127 |
| SFT | 15 | 78.6% | 71.4% | 66 |
| SFT | 20 | 71.4% | 64.3% | 66 |
| GRPO | 0, 5, 10, 15 | 82.1% | 75.0% | 234–244 |

SFT loss fell from 0.83 (step 1) to 0.32–0.55 (steps 14–20). Job `6aaa73e9`
ran the same SFT under the same seed and selected step 10; at 28 dev draws
the checkpoint choice is inside the noise. Probe of the selected SFT policy on
the 50 train cases, 4 generations each: 172 of 200 draws correct, 8
truncated, 15 of 50 groups informative, GRPO admitted.

Test split of the pilot bundle (7 cases, 4 draws each, family macro; paired
split-component bootstrap, 2,000 resamples, 7 clusters):

| arm | correct | correct-and-native | mean tokens | statuses |
|---|---|---|---|---|
| base | 85.7% | 57.1% | 368 | 12 native, 8 fallback, 4 control, 3 no-code, 1 incorrect |
| SFT step 5 | 82.1% | 57.1% | 316 | 12 native, 7 fallback, 4 control, 5 incorrect |
| GRPO step 0 | 82.1% | 57.1% | 316 | identical rows to SFT |

Paired deltas SFT − base: correct −3.6pp [−10.7, 0.0], correct-and-native
0.0pp [−10.7, +10.7]. The GRPO arm is the SFT adapter re-sealed at step 0
and its rows are byte-identical to the SFT arm's: batched generation under
the chunk seed is deterministic on this hardware, and a same-policy replicate
adds no information beyond that.

Eval-2 v1, whole bank, k = 4 per case, paired by chunk, family macro over
248 families, split-component bootstrap (EVAL2.md §4 rule: 95% lower bound
of the correct-and-native delta > +3pp): **pending at report time** (see the handoff; the job
uploads each arm as it ends). The pre-run expectation (EVAL2.md §7, §11) is
that a uniform lift below +10pp is invisible at N = 300 even at k = 16, and
this pilot runs k = 4.

Read what exists with care: the dev and test splits are
7 cases each, 28 draws per arm, and every interval above spans zero by a
margin larger than any plausible effect. The only claim they support is that
the adapter did not break the model: correctness within 4pp of base on both
splits, no truncation, mean completion length shorter, not longer.

Costs and counts: SFT 20 optimizer steps, 6,251 supervised
tokens, effective batch 4, rank 16 on 496 modules (116.7M trainable
parameters); probe 200 draws, 52,003 completion tokens; GRPO 20 steps at 4
generations per prompt (80 rollouts). Stage wall times on the h200 (from the
log stream): base-dev 7 min including the 55.6 GB weight download, SFT 16 min
(five dev evals of 28 draws), adapter reload eval 3 min, probe 12 min, GRPO
14 min, three test-split evals 24 min, then eval-2. Verification ran through
pooled sandboxes at 16 concurrent scorings; no request was blocked and no
identity drift was observed in this job.

## Failures, reflections and next experiment

Nine GPU attempts on 2026-09-16, in order, each one a fix carried forward:

| attempt | job | ended at | cause | fix |
|---|---|---|---|---|
| 1 | `6aaa202c` | deps | platform cancel (billing) | credits |
| 2 | `6aaa38b5` | base-dev, 55 min | one Hub 500 on a sandbox create ended the run | transport retries (155 s) |
| 3 | `6aaa49a9` | deps, 3 min | pip broken pipe | install retries |
| 4 | `6aaa4a0b` | handshake | my sandbox.py fix changed the harness hash | reverted; the gate was right |
| 5 | `6aaa4b2c` | SFT step-0 eval, 70 min | verifier Space PAUSED by the platform (no listening app) | Space restarted; per-job pool names |
| 6 | `6aaa5c1e` | cancelled in GRPO, 95 min | sequential eval could not finish: ~30 s per draw | batched generation, concurrent scoring, k = 4 |
| 7 | `6aaa7339` | bundles | reused bundles pinned an older verifier hash | prepare fresh; message names the field |
| 8 | `6aaa73e9` | probe, 75 min | a nondeterministic candidate aborted the stage as "unstable oracle" | policy v3: scores `unstable` |
| 9 | `6aaa8746` | eval-2 base arm, 384 of 1,200 draws | engine timeout on a CPU-bound candidate under 16-way concurrent scoring, read as an engine fault | open: give each sandbox CPU headroom (fewer sandboxes per host, more hosts) and decide whether a native timeout after a correct oracle is a fault or a `not-native` score with a witness |

Job 6 also showed that a cancelled job runs no EXIT trap; every stage now
uploads as it ends. The Space's runtime stays in RUNTIME_ERROR ("workload not
healthy after 30 min") because the image has no listening app; only PAUSED
blocks sandbox creation, and the next verifier build should serve a trivial
health endpoint so the platform never pauses it.

What is directly supported: the whole pipeline runs end to end on real
weights behind the fail-closed identity checks, and produces paired,
uploaded eval-2 measurements. Directly supported: SFT on this data does not raise
correct-and-native on 7 dev or 7 test cases and shortens completions; GRPO
at lr 1e-6 for 20 steps does nothing measurable. Inferred, not shown: the
eval-2 arms will read the same way, because the pre-run power analysis says a
small uniform lift is invisible at this size and the dev/test splits show no
concentrated one.

Alternative explanations to keep: k = 4 is a quarter of the pre-registered
draws, so the paired intervals here are wider than §7 priced; the batched
generation pairs chunks, not draws; the SFT references are terse and the
adapter learns terseness first (mean tokens 297 → 66 by step 15), which is a
style shift as much as a skill shift; GRPO at lr 1e-6 for 20 steps did not
move the policy at all on dev.

Before that, the eval-2 arms have to be measurable at all: the next launch
must (a) run scorings with CPU headroom per sandbox (`sandboxes_per_host`
4, `max_hosts` 4 for 16 workers, or 4 workers on one host) and (b) settle,
with Codex, whether an engine timeout on a candidate whose oracle passed
blocks the arm or scores `not-native` with a witness row; (a) alone may be
enough, and the witness row is what tells them apart next time.

Next experiment (bounded): before any further SFT, test the
teacher rather than the student. Take the 26 correct-fallback probe rollouts
and the 171 correct-but-fallback pilot draws (EVAL2.md §11), and measure on
the training bank alone, k = 16, whether a native rewrite of those programs
by the solving agent (no held-out case touched) that keeps length within
gate C would move correct-and-native by the concentrated +5pp the instrument
can see. Inputs: the pilot rows, the engine, one authoring pass; owner Fable
under Codex review; cost about $5 of serverless draws and no GPU; stop rule:
if fewer than 10 of the 64 training cases gain a native reference this way,
the SFT target cannot carry a +5pp concentrated effect and the round's next
lever is the engine's refusals, not the model.

## Codex review handoff

Artifacts: `round-02/6aaa87465527934177ee9f34/` in `headforce/lypning-round02-work`
(base-dev, sft, sft-dev-reload, probe, grpo, base-test, sft-test, grpo-test,
base-eval2, sft-eval2, grpo-eval2, reports/, execution-witnesses.jsonl,
job-manifest.json); the pilot rows and power curve at
`banks/2026-09-16-eval2-v1/` and EVAL2.md §7/§11; this branch's commits
`a723e19..HEAD`. Open questions: whether k = 4 pilot intervals are worth
reading at all against the §4 rule; whether the next verifier build should
also carry the unshare exec fix withdrawn in `fc4d489`; whether policy v3
(nondeterministic candidate = incorrect) should stay. Codex's assessment is
not written here.
