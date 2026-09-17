# Training assessment — are we moving toward the goal, and how would we know?

Written 2026-09-16 by Fable, for Codex's independent review under
`ORCHESTRATION.md`. It reads every document, report, review and run summary in
this tree as of commit `7170c48` and spends nothing: the two new numbers in it
(§3.4, §4) were computed on this tree today, and the commands are printed so
they can be re-run. No frozen artifact is touched. Every other number carries
the run and date it was recorded under; none is remembered (root `CLAUDE.md`,
invariant 3).

The question asked is narrow: **does the approach in force lead to the goal,
and why has nothing so far said whether it is even pointed the right way?**
The answer is in §3, the plan in §6.

## 1. The goal, as a chain of numbers

The goal (`STATUS.md` §1, `LADDER.md` §1) is an agent that does not know
lypning exists writing first-draft programs that `lypning-l` runs natively at a
rate materially above base, without more wrong answers. Behind that sentence
is a chain, and every link has a number or a hole:

| link | what it is | number today |
|---|---|---|
| wall clock | the thing the project sells: a session of one-liners for less than CPython's time | mixture 0.523× over 2,504 programs, `lypning-l` alone 0.102× on what it answers (`lypning bench`, 2026-09-06, README §1) |
| refusal share | the fraction of deployment programs that fall through to CPython | `lypning-l` refuses 537 of 2,504 (21.4%) on the same run |
| which refusals a model could remove | refusals whose program has a correct native rewrite the model could plausibly write | **never measured on the deployment population**; §4 is the closest thing, on this repository's own capture |
| the model's prior on ordinary tasks | correct-and-native first-draft rate with no runtime named | 68.7% macro over 49 families, 87.9% correct (`eval-20260916-063539`, k=16, 64-case train bank, `EVAL2.md` §11) |
| the adapter's effect on that prior | the deployment quantity | **no completed measurement**: the round-02 base arm blocked at 384 of 1,200 draws, SFT and GRPO arms never ran (`reports/2026-09-16-fable-round02-run.md`) |

Two things follow from the chain before any experiment is read. The benefit of
the model lever is bounded above by the refusal share that is model-addressable
(the third link), and that link has no number. And the model lever competes
with the engine lever for the same programs: a refusal kind the engine learns to
serve is a refusal kind the model no longer has to avoid.

## 2. What was spent, and what it bought

Between the target switch on 2026-09-11 and this document: 114 commits,
about 64,000 lines added under `training/` (`git log --shortstat --since
2026-09-11 -- training`, 2026-09-16), 18 markdown documents totalling 5,102
lines, 13,379 lines of pipeline and GPU code and 8,230 lines of tests
(`wc -l`, 2026-09-16).

| round | date | what ran | what it found | spend |
|---|---|---|---|---|
| v1, rejection-sampled LoRA | 2026-09-14 | one seed, 236 rows, 45 steps, eval-1 (74 rewrite-instruction cases) | no win: correctness +4.46pp, ΔSLR −1.00pp [−3.36, +1.51] | $28.70 |
| v2, context-distilled LoRA | 2026-09-14 | one seed, 48 steps, eval-1 | no win: ΔSLR −0.21pp; the kernel swap alone moved SLR +1.57pp | $23.08 |
| round-02 smoke | 2026-09-15 | tiny random model through the pooled-sandbox boundary | plumbing runs | ≤ $3.75 |
| round-02 pilot | 2026-09-16 | 27B weights, 50 train cases, 20 SFT steps, 6,251 supervised tokens; 7-case dev and test; eval-2 at k=4 | no gain on 7 test cases (0.0pp [−10.7, +10.7]); eval-2 arms never completed | about $52 GPU over nine attempts, serverless draws not itemised |

Roughly $105 and five days bought four adapters, zero wins and a long list of
findings — engine drift, kernel nulls, split contamination, a rule blind to
concentrated effects, a prompt population that names the refusal, sandbox
starvation — **every one of which is about the instrument and none about the
model.** That is honest work and it is also the symptom this document is
about: a programme whose every result is a measurement finding is a programme
measuring an effect smaller than its own noise, and answering with more
measurement.

## 3. Why there is no signal

### 3.1 No positive control has ever been run

A positive control is a treatment known to move the metric, run through the
same instrument, so that a null on the real treatment can be read as "the
treatment did nothing" rather than "the instrument cannot see anything".
`LADDER.md` defined two on 2026-09-14, each about $5: stage 0b (the subset
spec in the system prompt; if the prior can be moved by a description at all,
this moves it) and stage 1a (the adapter's native rate on its **own training
prompts**; if it cannot move those, nothing else matters). Neither has been
run. Round-02 skipped both rungs and went to the rung that costs the most and
resolves the least.

A template for the control already exists and is the strongest result in the
repository: the prompting study of 2026-08-23 (`docs/PROMPTING.md`), 884
programs from Claude Code agents, lifted the runs-natively rate from 66.3%
(no prompt) to 88.5% (one paragraph of motive, T2), with 0 MISMATCH and 0
wrong answers, and the whole effect concentrated in six of 26 tasks where the
idiomatic answer is an import. That is what a real effect looks like on this
metric: about +20pp, concentrated, free of correctness cost. Nobody has asked
whether Qwen shows the same shape on eval-2. Until that number exists, no null
from an adapter can be read.

The nearest thing to stage 1a that exists is negative, and it is buried in the
run report's "next experiment" paragraph. The selected SFT policy's probe on
its own 50 training prompts (job `6aaa8746`, 2026-09-16, 4 draws each): 172
of 200 draws correct, 26 of them correct-but-fallback, so about 146 of 200
(73%) correct-and-native per draw — against the base model's 749 of 1,022
(73.3%) per draw on the 64-case bank the same day (different k, overlapping
cases; a proxy, not a paired number). Read as a memorisation check, **the
adapter did not move the native rate on the prompts it was trained on.** What
it did move is length: mean completion tokens 297 → 238 at the selected
step and 66 by step 15. The adapter learned the references' terseness, not
the boundary.

### 3.2 The training signal is too small to move a 27B prior

Round-02 trained rank-16 LoRA over 116.7M parameters on **6,251 supervised
tokens** for 20 steps at effective batch 4. The v1 and v2 runs used 236 and
326 rows for 45–48 steps. These are wiring-test scales. A prior shift on a
post-trained 27B model — "when you would reach for `itertools`, write the
loop" across thirty refusal kinds — is not installed by a few thousand
tokens; what a few thousand tokens install is surface style, which is exactly
what every run has shown (v2: programs 24.4% shorter, `class` −22 and
`module-attr` +43, netting to zero; round-02: tokens 297 → 66).

The pre-registration's own abandon threshold (`PREREGISTRATION.md` §2g) was
150 on-task examples. That threshold was set to decide whether a run could
work at all, and 150 is a floor for a smoke test, not a training set.

### 3.3 The supply ratio is backwards

On 2026-09-16 the assembled bank of 364 admitted cases was cut 300 to eval-2
and 64 to training (`EVAL2.md` §11), because the power analysis demanded 300
for the benchmark and the training side got the remainder. For a training
experiment the ratio should be the other way: the benchmark needs enough
cases to see the effect, the training set needs enough to produce one. The
benchmark is now frozen at 300 and must not move; the training side has no
such constraint and is where every new case should go. `AUDIT.md`
(2026-09-13) recorded that the rewrite corpus was exhausted at 259
candidates; the ordinary-task supply is not exhausted — 1,261 runnable
entries sit in `data/classified.jsonl` on this tree (618 `tier1` and 643
`refused`, 2026-09-16), of which 691 were drawn as candidates and 364
survived review. The rest of the draw, the Cerebras question campaign
(63 structurally valid proposals from one pilot, `reviews/2026-09-15-question-pilots.md`)
and teacher repairs are all training-side supply that eval-2's freeze does
not touch.

### 3.4 The power table mislabels the effect it simulates

`EVAL2.md` §7 concludes that the pre-registered rule "cannot see a uniform
lift of +8pp or less at any size on the grid", and that a uniform few-point
lift — "the shape a small SFT most plausibly produces" — is invisible at
N=300, k=16. Round-02 ran under that reading. **The reading is a labelling
artefact of the simulation, not a property of the instrument.**

`stats.power_curve_clustered` applies a uniform lift as `min(1.0, p + delta)`
per case (`stats._treated`). On a pilot where most cases already sit at 1.0
the lift is clipped on those cases and the realised mean effect is a fraction
of the nominal `delta`. The function computes that realised effect and returns
it; the table in `EVAL2.md` §7 omitted the column and keyed its rows by the
nominal value. Measured today on synthetic pilots
where the answer is known (64 single-case families, k=16, N=300, MDE +3pp,
60 trials, 200 resamples, seed 7; `PYTHONPATH=src:training python3 -c` over
`pipeline.stats.power_curve_clustered`, 2026-09-16):

| pilot shape | nominal uniform lift | realised `mean_effect` | power |
|---|---|---|---|
| every case at 0.69 | +5pp | +5.0pp | 62% |
| every case at 0.69 | +8pp | +7.9pp | 100% |
| 45 cases at 1.0, 19 at 0.0 (mean 0.70) | +5pp | **+1.5pp** | 0% |
| 45 cases at 1.0, 19 at 0.0 | +8pp | **+2.4pp** | 0% |
| 45 cases at 1.0, 19 at 0.0 | +10pp | **+3.0pp** | 0% |
| 45 cases at 1.0, 19 at 0.0, concentrated shape | +5pp | +5.0pp | 92% |
| rates spread 0.2 … 1.0 | +5pp | +4.5pp | 53% |
| rates spread 0.2 … 1.0 | +8pp | +7.2pp | 98% |

The false-positive rate was 0% in every cell. So: the rule sees a **realised**
+5pp at roughly 50–60% and a realised +8pp at nearly 100% at N=300, k=16,
whatever the shape. What the §7 table shows is that on a saturated pilot a
"uniform" lift of nominal +8pp is a realised +2–3pp, which is below the bar
by construction — and the concentrated rows realise their nominal value
because the cases they lift start at zero. The uniform-versus-concentrated
contrast in that table is mostly the ceiling, not the statistic.

The consequence for reading round-02: the correct-but-fallback headroom on
the training bank is 171 of 1,022 draws (16.7% per draw) and about 19pp
macro over families (`EVAL2.md` §11). An adapter that converts a quarter of
that macro headroom is a realised +4–5pp macro and would be seen about half
the time; one that converts 40% is a realised +8pp and would be seen almost
always. **The instrument is not the reason the programme has no signal.**

**Corrected 2026-09-16, the same day, after this section was written.** This
paragraph first prescribed re-printing §7 "with `mean_effect` as its key
column", and stated the headroom as "171 of 1,022 draws (16.7% per draw, about
19pp macro)" before reasoning to "+4–5pp" without saying which of the two it
was. Both are the same mistake, and it is the mistake this section is about: a
realised effect has two units.

- `mean_effect` is the mean lift per **case**. The §4 rule macro-averages over
  **families**. Power is a property of the statistic, so the macro is the column
  a power row is read against, and `mean_effect` is not it. The two coincide
  when families carry equal case counts — however the lift lands, cap included —
  *or* when the lift lands evenly across families of any size. They diverge when
  both fail, which is unequal families **and** an uneven lift, so the mechanism is
  that conjunction and not unequal families alone. The cap is one way a lift
  comes out uneven and not the only one: the concentrated shape lands its whole
  lift on a subset of families, and diverges with the cap never biting (measured
  2026-09-16, this tree: one 40-case family at 0.0 plus 24 singletons at 1.0, k=16,
  N=300, 60 trials, 200 resamples — concentrated at a nominal +10pp reads +10.0pp
  per case against +0.7pp macro, `capped` 0.00).
- The eight rows above are unaffected: every pilot in them is 64 **single-case**
  families, so the macro and the per-case figure are the same number there by
  construction. The defect was in the prescription for the re-print, not in the
  evidence, and it would have bitten only on the real pilot, whose families are
  unequal (§11: 18 spanning families, one of 12 cases in 64).
- **Which direction it bites on the real rows is not known from this tree**, and
  this section does not guess. What decides it is where the *saturated* cases sit:
  `_treated` gives a case `min(1, p + delta) − p`, so the cap falls on the side
  holding the cases nearest 1.0, and the per-case column understates the macro
  when the heavy families are the saturated ones and overstates it when they sit
  at the floor. §11's figures do not settle that — the macro headroom (about 19pp
  over 49 families) sitting above the per-draw headroom (171 of 1,022 draws,
  16.7%) says only that the heavy families are *less often* correct-but-refused,
  which is consistent with their being saturated or with their being hard. Rung
  S0a reads it off the rows; until then the size of the error is unknown and its
  sign is unknown, so the conclusion of this section rests on the +5pp/+8pp
  calibration above and not on any claim about which way the column would move.

The tool no longer permits the confusion: `power_curve_clustered` returns
`mean_macro_effect` — the realised lift in the rule's own unit, noise-free —
beside `mean_effect` and a `capped` flag, `nt power --eval2` prints the macro in
every cell, counts the cells the cap bit and names the worst, and
`training/tests/test_eval2_power.py` pins that power tracks the macro and not the
per-case column. **§7's table should now be re-printed from the real pilot rows**
before it is quoted again; the rows are private
(`work/eval2/legacy-pilot/rows-full.jsonl`), so the read is the other device's
task. It is a read and nothing more *now*; it was not free when this section
claimed it was, because the renderer printed none of these columns.

One further finding from the same calibration, not in the table above: the
concentrated shape clips too. Its reachable lift **per case** is at most
`fraction × (1 − mean(lifted decile))` — the macro is not bounded by that
expression and exceeds it whenever the lifted cases sit in the light families —
so at `fraction` 0.1 a nominal +10pp arrives in full only on a decile sitting
exactly at zero, and the top of `CLUSTER_DELTAS` is on that boundary by
construction. Whether §7's concentrated rows clipped is not established: a
clipped cell can still sit on a rising power curve, so their rising power is not
the evidence it looks like. Check the lifted decile, and the `capped` column the
tool now prints, before trusting a concentrated row on any pilot.

### 3.5 The data admission excludes the examples that carry the signal

Schema 3 admits a coverage reference only when it is "correct and fully
native on every input" (`TRAINING.md`, data admission). On the task-first
path those references are captured programs that already ran natively —
which is to say, programs the base model already writes natively about three
draws in four. SFT on them teaches the model to imitate what it already does.
The examples that would move the prior are the other kind: an ordinary task
whose natural answer is refused, paired with a correct native answer the
model did not write. `ORCHESTRATION.md`'s loop names exactly this
("correct with a valid L refusal → Codex produces a faithful supported-subset
implementation → verified repaired SFT candidate") and its ledger row D1
reads, on 2026-09-16, "no new training examples admitted". The signal-bearing
arm of the loop has had zero throughput.

The run report's proposed next experiment (test the teacher: native rewrites
of the 26 + 171 correct-but-fallback draws on the training bank) is the right
one and is worth more than it claims: it measures the **rewritable fraction
of the headroom** — the third link of §1 — as well as producing the first
SFT target with a predicted effect.

### 3.6 Eval-2 is refusal-enriched, and the deployment prior is unmeasured

`EVAL2.md` §2 draws the bank over `tier1` and `refused` classified entries
together, which on this tree is 618 and 643 — a pool that is 51%
refusal-origin. In the bench corpus `lypning-l` refuses 21.4% of programs
(2026-09-06), and the as-invoked share, weighted by how often each program
was actually typed, is lower still (`docs/PAPER.md`; not re-run here). So the
"19 points of headroom" on eval-2 is the headroom of a population about twice
as refusal-heavy as this repository's own sessions, which are themselves a
developer working on an interpreter and not an agent doing ordinary work. The
two preview numbers that were closer to unconditioned — 8 held-out cases
reaching the subset on 89.06% of draws (stage 0a, 2026-09-14) and the
15-case glimpse at 96.9% correct-and-native (2026-09-16, the easy head of the
bank) — point the same way: the deployment headroom may be well under 10pp,
which is `EVAL2.md` §9's own falsifier. Stage 5 of the ladder (refusals per
100 programs through opencode on base Qwen, no GPU) would settle it and has
not been run.

This is not an argument against eval-2. A benchmark enriched for the tail is
the right place to *see* an effect. It is an argument that the benchmark's
number is not the deployment number, and that the deployment number is the
one that decides whether the model lever is worth its cost against the engine
lever.

### 3.7 The plans supersede each other faster than they are executed

Five planning documents of the last three days each state an ordered
sequence, and no two agree: `LADDER.md` (2026-09-14: 0b → 1a → 1b → DPO →
GRPO → eval-2), `TRAINING.md` and `NEXT_ROUND.md` (2026-09-15: verified SFT
first, RL conditional), `ORCHESTRATION.md` (2026-09-15: question campaign →
review → repairs → SFT), `STATUS.md` §4 (2026-09-16: seven steps, "step 6
does not start until 1, 3 and 5 are closed"), and `STATUS.md` §7 (a different
recipe again). Round-02 then ran step 6 on the operator's instruction with
steps 1, 3-as-power-sized and 5 open, data reviewed by the authoring agents
rather than Codex, and k=4 instead of the pre-registered 16. Each of those
decisions was defensible and recorded; together they mean that the written
gates are not the gates in force, and a reader cannot tell from the documents
what the next paid step is. The metric has also changed three times
(correctness pass@1 → SLR → correct-and-native), which `STATUS.md` §6 already
names as a finding.

The cost is not the paper. It is that the cheap, decisive rungs — 0b, 1a, the
by-kind vector, stage 5 — keep being displaced by the expensive, indecisive
one, because each new plan restarts the sequence at "build the next bundle".

## 4. The lever that has produced signal, and what the refusals are made of

Every measurable gain in the programme's window came from the engine: `math`,
`type()`, `%.2d` served from on-policy evidence (2026-09-12 to 13), nineteen
silent wrong answers closed (PR #63), fourteen mismatch witnesses from stage
0a, hillclimb 83's fixes. Each one raises every arm's native rate for every
model, permanently, at no inference cost, and moves the deployment number
directly. `LADDER.md` §6 already says the engine "has so far returned more per
dollar than the adapters"; the numbers above make it the only lever with a
non-null return.

To see how the two levers divide the work, the 643 refused entries of
`data/classified.jsonl` were bucketed today by their refusal detail (a
judgement call per kind, applied once; 2026-09-16):

| bucket | entries | the largest kinds |
|---|---|---|
| self-referential: this repository working on itself | 195 | `import lypning` 100, `sys.path` 52, `sys.version` 11, `sys.executable` 5 |
| legitimate fallback: semantics, I/O, environment, nondeterminism, bignum | 221 | `subprocess` 27, `glob()` order 23, `eval` 15, set `repr` order 12, `open()` of a descriptor 10, `urllib` 11, `time` 8, NaN identity 10 |
| engine-addressable: a capability the engine could serve | 222 | class definition 17, `itertools` 17, `unicodedata` 14, `math` 12 (served since 2026-09-12; the cache predates it), `binascii` 9, `datetime` 9, `textwrap` 8, decorated definition 7, `struct` 7, `io.StringIO` 6, `yield` 5, `argparse` 5, `csv.writer` 4, named regex groups 4 |
| other | 5 | |

The model-addressable set is a subset of the engine-addressable one: a model
can only rewrite around a refusal that has a native equivalent, and every such
refusal is also one the engine could remove by serving the construct. On this
population that is at most 222 of 643 refusals, about a third, and the third
that the engine roadmap (`L-TRAINING-ROADMAP.md`) already targets. The
by-kind vector of eval-2's 171 correct-but-fallback draws — promised by
`EVAL2.md` §4 and not yet published — is the same table for the population
that matters, and it decides the split of the budget between the two levers.

**Codex review, 2026-09-17.** The table above remains the dated §4 judgement;
the executable rule-2 review does not reproduce it blindly. Thirteen of the
S-ladder audit's new deterministic families (20 entries) moved from fallback
to engine-addressable. The reviewed local result is now 195 self-referential,
242 legitimate fallback, 206 engine-addressable and 0 other. The remaining
new declarations stand as current-corpus policy, not a universal statement
about Python. The population that decides investment remains S0b's private
draw vector.

## 5. What a signal would look like

A signal, for this programme, is a number with four properties: it costs
under about $10 or nothing; its direction and rough size are written down
before it is run; it moves monotonically with something under our control
(data, steps, prompt); and it is reported at every round in one table, so
that "are we moving" is a curve and not a verdict. None of the numbers
reported so far has all four. The ladder below does, and every rung is cheap
enough to run this week.

| rung | measurement | cost | prediction if the approach is sound | if not |
|---|---|---|---|---|
| S0a | `EVAL2.md` §7 re-printed from the real pilot rows, keyed on the realised macro lift the tool now prints | $0 (the tool half is done, 2026-09-16) | realised macro +5pp seen at ~50%, +8pp at ~100% | the rows contradict the synthetic calibration; fix before anything runs |
| S0b | by-kind refusal vector of the 171 correct-but-fallback pilot draws, bucketed as §4 | $0 | a handful of kinds carry most of the mass | the headroom is diffuse and the engine cannot take it either |
| S0c | the round-02 probe rollouts read by native status per train case, against the base pilot draw on the same cases | $0 | a stage-1a proxy: SFT moved native on its own prompts | it did not (§3.1 says it did not); no more SFT on this data |
| S1 | stage 0b on the 64-case training bank: bare vs `--system-file subset-spec.md`, k=16, thinking off, one pinned provider (`nt eval --system-file`, shipped 2026-09-16) | ~$5 | correct-and-native up ≥ 10pp with correctness flat, concentrated in a few kinds — the 2026-08-23 shape | flat: the boundary is not installable from a description at this size; only verified rewrites (a teacher) can supply targets |
| S2 | the rewritable fraction: teacher rewrites of the 171 + 26 fallback draws, verified native and correct, within gate C | tokens | ≥ 40% of the headroom is rewritable | the model lever is capped low; budget goes to the engine |
| S3 | stage 5: refusals per 100 programs through opencode on base Qwen, by kind | harness time | the deployment prior and its vector | if under 10 refusals per 100, `EVAL2.md` §9 fires and training stops |
| S4 | the first SFT with a predicted effect: targets from S1's conditioned draws or S2's rewrites, ≥ 1,000 training cases, ≥ 50,000 supervised tokens, three seeds, eval-2 at k=16 | ~$60–90 | realised ≥ +5pp on eval-2, gate A flat | the null is now informative, because S0–S3 say the effect should have been there |

S0 and S3 do not depend on anything and S1 and S2 do not depend on each
other. S4 is the first GPU spend, and it is the first run whose predicted
effect exceeds the instrument's demonstrated sensitivity.

## 6. Action plan

Ordered. Each step names its owner, its cost, what it decides and when to
stop. Nothing below launches on its own; the operator's ceiling and Codex's
review apply as `ORCHESTRATION.md` says.

| # | step | owner | cost | decides | stop rule |
|---|---|---|---|---|---|
| 1 | Run S0a–S0c on the private pilot and probe rows; append the re-printed power table to `EVAL2.md` §7 dated, and the by-kind vector to `EVAL2.md` §11 | Fable (other device) | $0 | whether the instrument reading in §3.4 holds on real rows; where the headroom lives; whether round-02's adapter moved its own prompts | none: these are reads |
| 1a | Done 2026-09-16, this tree: the mislabel fixed at its source rather than only described — `power_curve_clustered` returns the realised macro lift and a `capped` flag, `nt power --eval2` keys every cell on the macro and counts the clipped cells, `EVAL2.md` §7's uniform reading withdrawn, and a benchmark eval arm at a k the rule was not priced at is refused in `preflight` | — | that step 1's re-print cannot reprint the same mislabel, and that round-02's k=4 arm cannot recur | none: no run touched |
| 2 | **Done in code 2026-09-17:** 16 scorers are provisioned as 4 sandboxes/host × at most 4 hosts; launcher capacity below the worker count is refused. A native timeout after a correct oracle still aborts every arm, because scoring it would make the endpoint load-dependent; the witness and successful sibling rows survive | Codex decision | $0 | whether eval-2 can complete without changing what “native” means | any blocked arm remains incomplete and stops the rung |
| 3 | Run S1 (stage 0b) on the training bank and S3 (stage 5) on base Qwen, in parallel | Fable | ~$5 + harness time | the fork `LADDER.md` stage 0b describes, on the population that matters; the deployment prior | S3 under 10 refusals per 100 → stop training, engine only |
| 4 | Run S2: Codex authors or reviews native rewrites of every correct-but-fallback draw on the training bank, through the pooled verifier, gate C enforced; the rewritable fraction and its by-kind table go in the report | Codex authors, Fable verifies | tokens | the ceiling of the model lever; the first SFT target with a reason to work | under 10 of 64 cases gain a native reference → the model lever is capped and the round's lever is the engine (the run report's own stop rule) |
| 5 | Grow the **training** side to ≥ 1,000 cases without touching eval-2: the remaining classified draw, the question campaign, S2's rewrites, fallback controls kept at the bank's ratio. Rejection-sampled targets need the verifier, not oracle-grade review; only authored tasks need the review record | Codex reviews, Fable prepares | tokens + review | whether S4 can be sized to its prediction | eval-2 leak check (`nt eval2-leaks`) fails → drop the training case, never the eval case |
| 6 | Feed the engine from S0b and S2's tables: rank kinds by independent families × draws, serve the top of the list, rebuild, regrade, new bundle | hillclimb loop | engine time | the deployment number, directly | a MISMATCH is a bug and stops the change (invariant 1) |
| 6a | **Closed 2026-09-17:** `nt levers --rank` remains a train-side build order over the repository capture. It is refused on draw/held-out rows; S0b uses `--vector`, which prints reviewed families and counts without a score or priority order | hillclimb loop | $0 for the train-side ranking | which construct is in front of the most independent train-side programs | unchanged: a MISMATCH is a bug and stops the change |
| 7 | S4: three-seed SFT, ≥ 50,000 supervised tokens, distillation targets if S1 was positive else S2 rewrites, selection on dev by correctness, eval-2 at k=16 with the regenerated base arm; predicted effect and its shape written into `EVAL2.md` before the job | Fable, Codex reviews | ~$60–90 | the first informative answer to the programme's question | gate A fails → void; realised effect under +3pp with S1–S3 positive → the SFT recipe is wrong, not the data |
| 8 | Collapse the plan into one place: `STATUS.md` carries the S-ladder as the one live sequence and every other ordering points at it rather than restating it. **Re-scoped and done 2026-09-16 — see below; as first written this step would have destroyed records** | operator's call | $0 | which document a new session reads first | none |

**Step 8 as first written was wrong, and the narrower version is what was
done.** It said the S-ladder should become `STATUS.md` §5 and that
`START_NEXT_ROUND.md` and `NEXT_ROUND.md`'s sequence sections should become
pointers. Three things were missed:

- §5 is *Expected movement of the needle* — the only dated statement of the
  programme's priors, and the pointer to `LADDER.md` §6's kill criteria.
  Overwriting it deletes a record; evicting it renumbers §5–§9 and makes five of
  the ten inbound `STATUS.md §N` citations wrong — every one of them silently,
  because the headings they name all still exist after a shift, and one of them
  inside a dated `CHANGELOG.md` entry, where correcting it means editing a record
  to cite a section that did not exist when it was written. Appending the ladder as a new §10 costs no renumbering and breaks
  nothing.
- `NEXT_ROUND.md`'s launch sequence is not prose to be replaced by a pointer: it
  holds command flags that exist nowhere else, and `training/hf/round02_pilot.sh`
  says in two places that it runs "in `NEXT_ROUND.md`'s order". Pointing it at
  `STATUS.md` would make that script's own comment false and leave the root
  `CLAUDE.md`'s "manual run plan" bottoming out in nothing.
- `START_NEXT_ROUND.md` is 250 lines of mechanism and two lines of sequence, and
  the root `CLAUDE.md` names it as the handoff entry point. It is a poor target.

So the ordering *claims* were pointed at one home and the ordering *mechanisms*
were left where they work. What §3.7 diagnosed was five documents disagreeing
about what runs next — not five documents existing.

And three things to stop doing, each of which has already cost a round:

- **No adapter run on fewer than 1,000 training cases or one seed.** Both
  runs of record and the pilot were single-seed on 50–320 rows, and every one
  produced a style shift and a null. The pre-registration demands three seeds
  (`PREREGISTRATION.md` §7f); it has not been honoured once.
- **No eval-2 arm at k=4.** The rule was pre-registered at k=16; a k=4 arm
  is a different instrument and its interval is wider than any plausible
  effect (`EVAL2.md` §6 says so and the pilot ran it anyway). **This one is no
  longer a rule in a document: since 2026-09-16 `preflight` refuses a non-smoke
  benchmark eval arm at any k but `training_contract.PROTOCOL_EVAL_DRAWS`, so
  the runner's default of 4 — which `EVAL2.md` §4 already called a smoke
  setting — cannot reach a confirmatory arm again.** Since the independent
  review on 2026-09-17, real adapter stages also enforce the 1,000-case floor,
  the registered seeds 1111/2222/3333, at least one complete family cycle and
  50,000 scheduled supervised-token exposures. All three seeds remain an
  aggregate-round requirement: one process cannot prove the other two ran.
- **No new planning document.** Amend `STATUS.md`. A sixth sequence is not a
  plan; it is the reason the first five were not executed.

## 7. Kill criteria, restated with dates

`LADDER.md` §6's criteria stand; this restates them against the rungs above so
each has a date by which it will have fired or not.

- S3 reports fewer than 10 refusals per 100 programs on base Qwen through
  opencode, or S0b shows the headroom diffuse across kinds none of which
  exceeds a few families: the deployment headroom is not worth the model
  lever. Stop training; the budget is the engine's.
- S1 flat **and** S2 under 10 of 64: the boundary is neither elicitable nor
  cheaply rewritable at this model size. Stop training.
- S4 null across three seeds after S1–S3 were positive: the recipe cannot
  install what the prompt can elicit. One more recipe (preference pairs from
  the verifier's rejected draws) under the same rules, then stop.

None of these is a failure of the apparatus, which is the best thing the
programme has built. They are the programme finally being able to lose in a
way it can see.

## 8. Decision state

- **Codex, closed 2026-09-17:** §3.4 changes the reading of round-02's null; it
  is uninformative because dosage/data were inadequate and no eval-2 arm
  completed. Native timeout stays a hard abort. Only S0a–S0c are authorized;
  steps 3, 4 and every GPU rung remain held pending that report.
- **Operator:** the ceiling for steps 3, 4 and 7; whether step 8 is wanted;
  and the standing rule that no GPU step runs before the rung below it has
  been read.
- **Codex, closed 2026-09-17 after Fable's S0 attempt:** the attempt was blocked
  on the wrong device and answered no S0 question. Its fail-closed guards stand.
  S0b now reads the frozen 171-draw population, pins the explicit historical
  engine SHA and refuses a partial join. The supervised-token floor remains a
  stage-time exact-token gate. Only the corrected private-device S0a–S0c retry
  is authorized (`reviews/2026-09-17-fable-s0-independent-assessment.md`).
