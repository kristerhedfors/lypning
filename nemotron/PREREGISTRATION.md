# Pre-registration: does a LoRA make Qwen3.8-27B write lypning-supported python?

Written **2026-09-12, before any fine-tuned model exists** — no adapter has been
trained, no training data has been sampled, and no post-fine-tune number has been
seen by anyone. Engine at `0f61407` (2026-09-12T12:46:26Z). Every number below is
reproducible by the command printed beside it; none is remembered (invariant 3).

The point of writing this first is narrow and specific: three of the decisions
here — which rule is primary, which cases count, and what the comparison is
measured against — all move the answer, and all of them would be unfalsifiable
if taken after the result was visible.

## 1. The claim under test

Rank-16 LoRA on Qwen3.8-27B, trained only on programs the model itself wrote and
that the engine verified, makes it produce more python that lypning can run
**without making it produce wrong python**. The second half is not decoration:
a model that learns to emit `print(1)` for everything would win the first half.

## 2. The instrument, and the three things wrong with it today

**The held-out split is frozen** at 74 cases,
`holdout.lock.json`, manifest `80b2fc52…733ce588`, pinned by
`tests/test_recorded.py::HOLDOUT_MANIFEST`. It does not move.

**(a) The baseline is stale and must be re-graded.** `runs/qwen38-baseline-k16`
was graded this morning against an engine that has since gained `math`, `type()`
of any class, int/float methods and `%`-precision. Every `lypning`-kind
acceptance test checks that lypning ACCEPTS the program, so the same programs
score differently now. **The baseline is re-graded against the engine the
fine-tuned model will be evaluated on, from the recorded completions, with no
new inference.** Comparing a fine-tune against the un-regraded 0.4037 would
credit it with a day of engine work.

**(b) Four held-out cases are now degenerate.** `nt refusals --held-out` says the
engine now RUNS the original program of `ntx-1eb18c506d05`, `ntx-3eb388881928`,
`ntx-4f5b30dcdfe5`, `ntx-9533782459ac` — `import math` twice, `type()` once,
`%.2d` once, which are precisely the refusals closed today. On those a model can
return the input unchanged and pass. Two of them scored **0/16** this morning.

> Decided now: the delta is reported over **all 74** and over the **70
> non-degenerate** cases, and the 70-case figure is primary. The split itself is
> not re-frozen — the lock is the integrity guarantee and it stays — and the
> exclusion criterion is mechanical (`refusals --held-out` names them) rather
> than chosen by looking at scores.

**Re-run 2026-09-13 at the engine at HEAD** (`nt usable`): 74 cases, the same
four degenerate ids and no others, five unsatisfiable, 65 usable. **The 70-case
primary denominator holds unchanged.** What it is made of, which §2(g) makes
newly relevant: 48 rewrite, 14 ceiling, 8 unobserved, at re-graded baseline
per-case means of 0.202, 0.893 and 0.977 — 31% of the primary denominator is
populations the fine-tune is not for, and the 17 movable rewrite cases are where
the claim can actually be won or lost. (**CLARIFICATION, corrected 2026-09-13**:
this sentence first read 0.232 and 20 movable, which are the rewrite figures over
all **52** rewrite cases — the all-74 denominator, three of whose four degenerate
cases are movable rewrites and are exactly what the paragraph around it excludes.
Mixing the two denominators inside the paragraph announcing the exclusion is the
error the exclusion exists to prevent. The counts 48/14/8 were already the primary
denominator's and do not move.) The denominator is **not** changed for
that; it was knowable when it was fixed, and moving it now is the thing this
document exists to prevent. It does fix what the training mixture must be
consistent with: **ceiling cases are trained on** (they are 14 of the cases that
grade the model, and training only on rewrites is exactly the damage they
detect), and **unobserved cases are not** (they are the damage detector for
general coding, and a detector you have trained against detects nothing).

**(c) 5% of draws are truncated.** 59 of 1,184 attempts stopped at
`max_tokens: 2048` with `finish_reason: length`, across 19 cases, and **every one
of them fails** (0/59). `max_tokens` stays at 2048 for both arms: raising it for
the fine-tuned arm alone is a confound, and a model that learns to write shorter
subset-conforming programs should be allowed to show that as a win.

**(d) The held-out set was leaking into train, and ids did not show it.** Added
2026-09-12, still before any adapter exists. `nt leaks` measures three ways a
train case can be the same question as a held-out one:

| test | held-out cases affected | train cases affected |
|---|---|---|
| prompt similarity >= 0.85 to a train case | **26 of 74** | 56 of 175 |
| >= 0.95 | 11 | — |
| expected stdout byte-identical to a train case's | 10 | 11 |
| negative program byte-identical | 1 | 1 |

**CLARIFICATION, corrected 2026-09-13, and corrected again the same day.** The
stdout row read 11 in the held-out column; 11 is the count of *train* cases
carrying a held-out case's expected stdout (`split.cross_split_leaks`'s own
docstring says so). The first correction put 7 there, which is also wrong, and
wrong in a way worth naming because it is how the 11 got there in the first
place: 7 counts `cross_split_leaks`'s `twin` field, which undercounts twice over
— `twin = twin or held_stdout[want]` never fires once a prompt twin has claimed
the row, and `held_stdout` is keyed on the stdout itself, so two held-out cases
with the same expected output collapse into one. Counted directly over
`data/corpus.jsonl` against the lock, the answer is **10** held-out cases
(`ntx-1eb18c506d05`, `293fe1680aa3`, `4bf956899386`, `4f5b30dcdfe5`,
`5bce5a934d5d`, `5d3a1e41f193`, `b840c98a9b42`, `bdb7ef0dfd39`, `dc8e8190ab84`,
`f72ddbdfe8f7`) behind 11 train cases. The 0.85 row reads 26 when recounted
today against the corpus on disk, not 27. Neither number is an input to anything: the
**decision** below is the union over train cases, and that union is unchanged at
58 dropped / 117 clean, which is what `nt leaks` prints and what
`sample.train_cases()` acts on.

Disjoint ids buy nothing here. The corpus is capture-derived, so one agent
hitting the same wall twice in a session produces two entries differing in a
variable name, and `freeze` puts one in each split. Three of the 154 rows in the
first SFT set were verified solutions to two HELD-OUT cases.

> Decided now: **58 of the 175 train cases are excluded from sampling**, by the
> mechanical union of the three tests at a ceiling of 0.85 fixed in
> `split.SIMILARITY_CEILING`. 117 remain. The held-out set is NOT re-cut — the
> lock stays — so the cost falls entirely on the training side, which is the
> right side for it: a train case wrongly dropped costs a few examples, a train
> case wrongly kept costs the defensibility of the number.
>
> `sample.train_cases()` now excludes them by default. Its previous assertion
> was tautological — it filtered by held id, then asserted no held id had
> survived the filter.

**Checked 2026-09-13 by asking the targets rather than the cases**
(`nt leaks --sft data/sft/v1`, `split.sft_solves_holdout`): of the 154 rows in
the SFT file on disk, **16 are verified solutions to 7 held-out cases** — they
pass those cases' own acceptance tests as written. All nine train cases behind
them are dropped by the 0.85 filter, and the same draws re-folded over the clean
117 leave **zero**. That is the first non-tautological evidence that the
exclusion excludes; it is a check that runs, and it runs on the file that would
otherwise have been trained on.

**(e) Five held-out cases are unsatisfiable.** `nt usable` runs each case's own
negative — correct python by the corpus's definition — and asks whether it
reproduces the expected output. Five do not: a frozen `datetime.now()`,
a benchmark's timings, a set's iteration order captured under another hash seed,
a `TypeError` worded by another CPython, and an `os.listdir()` count of a
directory nothing seeds. Nothing passes them: not the model, not a human, not
CPython. They sit in the "never passes" column looking exactly like hard cases.

> **65 of 74 cases can measure a model** — 74 less 4 degenerate less 5
> unsatisfiable, with no overlap. `nt usable` names all nine.

**Re-run 2026-09-13 at HEAD's engine: unchanged, 65 usable and the same nine.**
The test was also widened that day, before any adapter exists: it ran a case's
*negative*, so for a ceiling case — which has a reference and no negative — it
returned `no-negative`, which reads like "checked" and was "not checked", for 45
of the 249 cases. It now runs whichever of the two the case carries. Running the
45 references found none unsatisfiable, so the nine above and the denominators
built on them do not move; the point is that the blind spot is now measured
rather than assumed.

**(f) The spend cap was decorative.** `ChatBackend.cost()` multiplies by
`NTX_PRICE_IN`/`NTX_PRICE_OUT`, which are unset by default, so every draw of the
first SFT set recorded `cost_usd: 0.0` and `--max-spend` could never trip. Both
must be exported before any sampling run.

**(g) The training set is three populations, and yield was choosing the
mixture between them.** Added 2026-09-13, still before any adapter exists and before the
sampling run is paid for. The corpus holds three populations, told apart by each
case's own test rather than by its name (`sample.population`): **rewrite** — a
`lypning` test with `require_tier1` true, the thing being fine-tuned; **ceiling**
— the same test with `require_tier1` false, where the right answer keeps the
import and takes the fallback; **unobserved** — a test of any other kind, where
no engine is ever asked anything. Run `nt sample` (it prints the pool before it
spends) and `uv run --with pytest pytest nemotron/tests/test_mixture.py`.

| | corpus | train | held out |
|---|---|---|---|
| rewrite | 178 | 126 | 52 |
| ceiling | 45 | 31 | 14 |
| unobserved | 26 | 18 | 8 |
| | **249** | **175** | **74** |

Yield runs the wrong way. A ceiling case passes whenever the model can copy the
program the prompt handed it, and an unobserved case is a plain coding task the
stock model already answers; a rewrite case is the hard one. Folding the recorded
`data/sft/v1` draws over the 117 clean train cases gives **113 rows: 27 rewrite,
54 ceiling, 32 unobserved** — 24% of the training set is the task. At the
pre-registered `keep=4` the same draws give **208 rows of which 47 are the task**
(23%): a run that clears the 150-example abandon threshold three times over on
rows the experiment is not about. Of the 154 rows in the SFT file on disk, **all
60 ceiling rows are refused by both engines** — verified correct, verified not
to route.

> Decided now, mechanically and before the spend: sampling draws from
> **rewrite + ceiling only**, less the cases that cannot teach — the 5 clean
> train cases the engine now RUNS (the program the prompt hands over passes the
> case's own test as-is, measured on all five: the train-side twin of §2(b)) and
> the 3 that nothing passes (§2(e), train side). **The pool is 93 cases: 66
> rewrite, 27 ceiling.** Ceiling cases keep **one** target each, not four,
> because a second is the same answer typed twice — distinct kept programs
> within one ceiling case are 0.865 mean pairwise similar, above the 0.85 at
> which `split.SIMILARITY_CEILING` calls two cases the same question, against
> 0.427 within a rewrite case. That is ~3,000 draws at k=32 rather than ~3,700,
> so the sampling step costs about $5 rather than $6.

**What would say this mixture is wrong, decided now so it cannot be decided
later.** (i) The tuned arm *loses* held-out ceiling cases — `nt slices` per
stratum, and `nt compare`'s per-case flips — means the counterweight was too
light and 27 ceiling rows were not enough. (ii) The tuned arm gains nothing on
the rewrite stratum while the ceiling stratum holds: the mixture was not what was
in the way, and the next lever is the engine, not the data. (iii) The tuned arm's
own output on ceiling prompts starts hand-rolling what it should import —
`nt refusals --run <eval>` sees the programs, and a MISMATCH there fails the
safety gate outright. Each is read off the run that already has to happen; none
of them costs another dollar.

**And the abandon threshold now reads on the population it is about.** "Fewer
than 150 verified examples" (§5) was being cleared by rows that are not the task.
`sample.json` reports `sft_examples_on_task`.

**CORRECTED 2026-09-13, before the spend.** The projection first written here
folded `data/sft/v1/draws.jsonl` and reported 14 solved cases and 39 rows at
`keep=4`. That file is the earlier model's (`SWITCH.md` §98 says so in this
same tree: "off-policy for Qwen … must be re-sampled"), so it was that model's yield
presented as a property of a Qwen run — the same substitution this document
exists to prevent, made inside it. An on-policy control was already on disk:
`runs/headroom-k16` and `runs/qwen38-baseline-k16` are the same 74 held-out
cases, the same `holdout_manifest_sha256`, the same `prompt_sha`, and
byte-identical sampling dicts; only the model differs. Over the 43 clean
(non-degenerate, non-unsatisfiable) held-out **rewrite** cases, from the recorded
flags, 2026-09-13:

| run | model | cases solved | per-draw | rows at `keep=4` |
|---|---|---|---|---|
| `headroom-k16` | the earlier model (`SWITCH.md`) | 12 of 43 (27.9%) | 0.119 | ~43 |
| `qwen38-baseline-k16` | Qwen3.8-27B | **16 of 43 (37.2%)** | **0.198** | **~55** |

Qwen out-yields the earlier model by 1.33x on cases solved and 1.66x per draw, so the
original figure understates the run it describes. Scaling the Qwen held-out rate
onto the 66-case pool gives roughly **85 on-task rows at k=16**, not 39, and k=32
is higher by an amount nothing here measures.

**The conclusion is therefore weaker than it was first written, and is left
weaker rather than restated.** ~85 at k=16 is still short of 150, so the abandon
condition may well fire — but "projected to produce well under 150" was asserted
on the wrong model's yield, and the honest statement is that the on-task count is
not known before the sampling run and **must be read off `sft_examples_on_task`
after it**. That read is free: sampling is step one, and the threshold is
evaluated before a GPU is rented.

## 3. The rules, fixed now

Run `nt power qwen38-baseline-k16` — 74 cases, k=16, 34 never pass, 16 always
pass, 24 movable. **Corrected 2026-09-12, still before any adapter exists:** the
curve first simulated only the bootstrap leg, so the headline was the power of
half a rule the pre-registration defines as a conjunction. Both legs now:

| true lift | unpaired | bootstrap leg | McNemar leg | **PRIMARY (both)** |
|---|---|---|---|---|
| +2pp | 0% | 45% | 62% | 42% |
| +4pp | 0% | 92% | 97% | **90%** |
| +8pp | 1% | 100% | 100% | 100% |
| +12pp | 43% | 100% | 100% | 100% |
| +15pp | 97% | 100% | 100% | 100% |

**AND A DISCRETE FLOOR THE PERCENTAGE HIDES.** Exact two-sided McNemar over `b`
gained and `c` lost is `2/2**b` when nothing is lost: p = 0.0625 at five cases
and 0.03125 at six. **A fine-tune that flips fewer than six cases and loses none
cannot fire this rule at any effect size.** The +4pp above is the power of a
uniform smear across many cases, which is the most favourable shape an
improvement can take; a fine-tune that completely solves three previously
hopeless cases is +4.3pp on this denominator and fires nothing. The rule asks
how many CASES moved, not how far the mean did, and both numbers are reported.

> **§3c corrected this paragraph, and then the rule was amended to match it.**
> The paragraph says the rule "asks how many CASES moved". It did not: until
> 2026-09-13 `paired_delta` called a case gained when its per-case *mean* moved,
> and at k=16 noise alone loses ~9.5 of these 70 cases in every run — measured
> `c == 0` in **0 of 2,000** null trials — so six-and-none-lost was unreachable
> and the real requirement was twelve to fifteen outright-solved cases.
> **Since the §3c amendment of 2026-09-13 the rule does what this paragraph
> always said**: discordance is solved / not-solved, so the `2/2**b` arithmetic
> and the six-case floor describe the implementation rather than an intention.
> The amendment is a CHANGE OF RULE made before any adapter existed; §3c states
> it as one, shows the re-measured power, and shows that it makes §3b's null test
> harder to fire rather than easier. Read §3c before spending.

**The standing unpaired rule reaches 80% power only at +15pp.** Moving a 74-case
mean that far means taking about eleven cases from never-passing to
always-passing — a third of the 34 that never pass. An experiment whose rule
cannot see a plausible effect reports "no win" whatever happens, and the money is
spent either way.

- **PRIMARY:** the paired test. The two arms run on the *same* frozen cases, so
  they are paired by construction and the unpaired rule pays for the shared
  per-case difficulty twice. A win requires **both** the bootstrap 95% CI lower
  bound of the per-case delta to be above 0 **and** exact two-sided McNemar
  p < 0.05 on the case-level flips, where a flip is **solved / not-solved**
  (§3c, amended 2026-09-13). Both are already implemented
  (`stats.paired_delta`, `stats._mcnemar`); neither was written for this.
- **SECONDARY, always reported:** the unpaired rule, `stats.beats`, unchanged.
  It was fixed before any run happened and it stays in the table. **Where the two
  disagree, the disagreement is the finding and is reported as one** — not
  resolved in favour of whichever is kinder.
- **The safety gate, which is not a statistic.** `conformance` MISMATCH must be 0
  and routing UNSAFE must be 0 on the engine used for both arms, and
  `nt refusals --run <eval>` must report MISMATCH 0 over the fine-tuned model's
  own output. A pass-rate gain bought with a silent wrong answer is a loss.

## 3b. The rule, run against the engine drift it was written to survive

Recorded 2026-09-12 after re-grading the frozen baseline completions at the
current engine — no new inference, and still before any adapter exists. This is
the **null** for this experiment: the same model, the same completions, a newer
engine. If the rule fired here it would fire on engine work alone.

Re-run 2026-09-13 under the §3c amendment, `nt compare qwen38-baseline-k16
qwen38-regrade-20260913 --anyway`, engine `42ab2d3b7608dfe1`. Both the rule as
originally pre-registered and the amended rule are shown, because the whole point
of a null test is that it be checkable:

| denominator | before | after | paired delta | 95% CI | McNemar, mean-moved | McNemar, **amended** | fires |
|---|---|---|---|---|---|---|---|
| all 74 | 0.4037 | 0.4375 | +3.38pp | [+0.84, +6.59] | p = 0.0312 → **would fire** | **p = 0.2500** | no |
| **70 non-degenerate (PRIMARY)** | 0.4116 | 0.4286 | **+1.70pp** | [+0.00, +4.38] | p = 0.2500 | **p = 0.5000** | **no** |
| 65 usable | 0.4433 | 0.4615 | +1.83pp | [+0.00, +4.71] | p = 0.2500 | p = 0.5000 | no |

**The amendment closes a false positive rather than opening one.** The old
definition fired on the naive all-74 denominator against a comparison that is
pure engine drift — the same model, the same 1,184 completions. The amended one
does not fire on any denominator here. That is the direction a change of rule
must move a known null if it is to be believed, and it is why the §3c amendment
is recorded as admissible rather than merely declared so.

**CLARIFICATION, corrected 2026-09-13** (`nt compare qwen38-baseline-k16
qwen38-regrade-20260912`, engine `7846191`, built here today): the all-74 row
read 0.4382 / +3.45pp and the run's own `summary.json` says `pass_rate 0.4375`,
which is also what the paragraph four below it already said. The verdict column
does not move, in either row, and it is the verdict that is pre-registered.

The primary denominator holds and the all-74 one does not, which is precisely
what §2(b) was written for: the four degenerate cases carry more than half the
apparent gain, and a model can pass three of them by echoing its input. Measured
on those three, **11 of 23 newly-passing draws still contain the very construct
the prompt asked the model to remove, and 4 draws are verbatim echoes** — the
stock model is already exploiting the degeneracy, so this is not a hypothetical.

Type-I, measured over 400 null trials on the re-graded rates: bootstrap leg 4.0%,
McNemar leg 1.5%, conjunction ~1%. The conjunction is conservative, as intended.

Two of the four degenerate cases share a byte-identical negative program — the 52
rewrite cases hold only 51 distinct ones — so two of the six apparent gains are
one program counted twice.

**Re-measured 2026-09-13 at engine `7846191`, and 0.4375 does not move.** PR #60
and PR #61 changed what the engine answers again — a base64 refusal, fifteen
error wordings and a `type()` kind-flip — so the §2(a) argument was re-run rather
than assumed. The 1,184 completions reconstruct from
`runs/qwen38-baseline-k16/attempts.jsonl` (1184/1184 re-extract byte-identical,
checked before grading) and re-grade to **pass_rate 0.4375, with 0 of 1,184
attempt flags changed and 0 of 74 per-case rates moved**. Graded twice, the two
grades agree on every verdict. Run directly against both builds, exactly one of
the 1,184 programs gets a different answer out of the engine, and the difference
is the wording of a refusal on stderr at the same exit 90 — which no acceptance
test reads, because `_matches` compares exit code and stdout and nothing else.
**The baseline does not move, and `qwen38-regrade-20260912` keeps its job.**

## 3c. The discrete floor is not where §3 says it is, and the rule is near-blind to the shape this fine-tune will produce

Measured 2026-09-13, before any adapter exists, before the sampling run is paid
for, on the **primary 70** at the **re-graded** per-case rates (`nt compare`
denominator, `qwen38-regrade-20260912`), 600 simulated runs per row, k=16. §3's
power table is computed on the **all-74, pre-regrade** scores and lifts every
case by the same amount; this is the same simulation pointed at the denominator
the rule is actually pre-registered on, and at effect **shapes** rather than one.

**The floor arithmetic is right and its premise never happens.** `stats._mcnemar(b, 0)`
is `2/2**b` — 0.0625 at five, 0.03125 at six, and `stats._min_discordant()`
returns **6**, so §3's sentence is arithmetically exact. But `paired_delta` calls
a case *gained* when its per-case **mean** moved, and at k=16 sampling noise alone
moves means in both directions: over 2,000 null trials on these 70 cases the mean
discordant counts are **b = 9.5, c = 9.5**, and **`c == 0` occurred 0 times out of
2,000**. Six gains and nothing lost is not a stringent case; it is an
unreachable one. With c ≈ 9 the exact test needs **b >= 21** (`_mcnemar(21, 9) =
0.043`) — twenty-one of seventy cases moving up against nine moving down.

| what the fine-tune does | overall lift | **rule as implemented** | boot leg alone |
|---|---|---|---|
| uniform +2pp on all 70 | +2.0pp | 32% | 37% |
| uniform +4pp on all 70 | +4.0pp | **80%** | 81% |
| +6pp on the 48 rewrite cases only | +4.1pp | **96%** | 96% |
| solves 3 hopeless rewrite cases outright | +4.3pp | **4%** | 39% |
| solves 6 | +8.6pp | **14%** | 98% |
| solves 8 | +11.4pp | **24%** | 100% |
| solves 12 | +17.1pp | **49%** | 100% |
| solves 15 | +21.4pp | **71%** | 100% |

**Read the last five rows before spending anything.** A rejection-sampling LoRA
trained on verified rewrites of specific refusals is far likelier to produce the
bottom shape — a handful of previously-hopeless cases solved outright — than a
uniform smear across seventy cases including the sixteen that already always
pass. Against that shape the rule needs **twelve to fifteen** completely solved
cases for 50–70% power, not six; §3's "fewer than six flips cannot fire" reads as
a floor and is in fact an understatement by a factor of about two and a half. In
every one of those rows the bootstrap leg is at 98–100% and the McNemar leg is
what refuses: **the conjunction is, in this regime, the McNemar leg alone.**

**And more samples do not fix it.** Re-run at k=32 and k=64 the same rows give
12%/13% at six solved and 45%/45% at twelve — flat, because a finer-grained
per-case mean makes *more* pairs discordant, not fewer (c rises from 9.5 to 10.8).
Buying a second eval Job would buy nothing here.

**What would fix it, and it is free.** Count a case's flip as a case-level
**binary** outcome — solved / not solved — instead of a move in its mean, which
is what §3's own words ("case-level flips", "how many CASES moved") and its
`2/2**b` arithmetic already describe, and what makes the six-case floor the real
floor. Simulated the same way, binarising on *solved at least once in k*: null
0% (conservative, against a nominal 5%), six solved **49%**, eight **83%**, ten
**97%**, and none of the top four rows loses anything (uniform +4pp stays 80%,
rewrite +6pp stays 96%). It is a change to one comparison in
`stats.paired_delta` and it costs $0.

> **ADOPTED 2026-09-13 by Krister Hedfors, on option (ii). THIS IS A CHANGE OF
> RULE, NOT A CLARIFICATION, AND IT WAS MADE AFTER SEEING A POWER CURVE.** That
> sentence stays at the top of this amendment permanently, because it is the
> fact a reader most needs and the one most easily lost. What makes it
> admissible rather than fatal is *when*: no adapter exists, no SFT set has been
> sampled, no dollar has been spent, and no treatment arm has been graded. There
> is no outcome to choose a statistic in favour of. Writing the same change after
> the arms were graded would be choosing the test that fires, and would be
> indefensible.
>
> `stats.paired_delta` now counts a case discordant when it crosses
> **solved / not-solved** (`after > 0 and before == 0`, and its mirror), not when
> its per-case mean moves. The mean-moved counts survive beside it as
> `moved_up` / `moved_down` / `mcnemar_p_mean_moved` and are printed, because a
> rule whose alternative you can no longer see is a rule nobody can check.
>
> **Re-measured independently on landing**, 400 trials per row, k=16, against
> `qwen38-regrade-20260913`'s own per-case scores at the primary n=70 (30 cases
> at 0.0, 16 at 1.0). These numbers are lower than the 49%/83% quoted in the
> paragraph above, which were simulated at a different post-solve rate; the
> measured ones are what this amendment claims:
>
> | scenario | mean-moved | **solved/not-solved** |
> |---|---|---|
> | 4 hopeless cases solved | 6% | 4% |
> | 6 hopeless cases solved | 12% | **27%** |
> | 8 hopeless cases solved | 20% | **60%** |
> | 10 hopeless cases solved | 32% | **90%** |
> | uniform +2pp | 31% | 31% |
> | uniform +4pp | 84% | 85% |
> | uniform +6pp | 99% | 99% |
> | null: no change at all | 0% | 0% |
>
> So it buys the concentrated shape, costs nothing on the broad shapes the rule
> already saw, and does not inflate the null. At four solved cases it is
> *slightly worse*, which is recorded here rather than omitted.
>
> **The evidence that this is not the statistic that flatters us: it makes §3b's
> null test HARDER to fire, not easier.** Engine drift alone — the same 1,184
> completions re-graded — fired the old rule on the naive all-74 denominator at
> p=0.0312. Under the amended definition that same comparison gives p=0.2500 and
> does not fire, and the primary denominator moves from p=0.2500 to p=0.5000.
> A change of rule chosen to produce wins would not close a false positive on a
> known null. Re-run both after landing; the table in §3b is the amended one.

**The honest summary of what $16 can buy.** If the LoRA broadly shifts style so
that most rewrite cases get a little better, this design sees it (96% at +6pp on
the rewrite stratum). If it instead solves a specific handful of refusals — the
shape §2(g) points at — on-policy, 34 of the 52 held-out rewrite cases (65%) have
never once passed in Qwen's 16 draws; the "55 of 66" first written here was
the earlier model's rate on the train pool and overstated it — this design reports "no
win" for anything short of about eight to ten
solved cases under the §3c amendment (60% at eight, 90% at ten), where before the
amendment it needed twelve to fifteen. The other pre-registered abandon
condition (§2(g), fewer than 150 on-task SFT examples) bites first and is the
cheaper place to stop.

## 3d. The engine is part of the pre-registration and was not named as one

Added 2026-09-13, before any adapter exists. §4's first row says a differing
engine is "covered". Audited against the code, it is covered **only between two
runs that both recorded one**, and there is a third engine in the room that
neither run records.

- `stats._engine` returns no reason at all when either side's fingerprint is
  missing (`if not (b.get("fingerprint") and r.get("fingerprint")): return []`).
  That is deliberate and documented — an unknown must not assert a difference —
  but it means the guard is **silent, not withholding**, and both runs on disk
  (`qwen38-baseline-k16`, `qwen38-regrade-20260912`) have `engine: null`. Every
  comparison published so far is unguarded. `nt eval` and `nt grade` both write
  `eng.identity()` now, so the two eval arms will be guarded — provided both are
  graded after today and neither is compared against a pre-2026-09-13 run.
- The **denominator** is computed live. `cli._denominators` asks the binary on
  this box which held-out cases are degenerate, so which cases are in the primary
  70 is decided by whatever engine is installed when `nt compare` runs — after
  the data is visible. `cmd_compare` does compare the live fingerprint against
  each run's, and that check is silent for the same reason: the runs record none.

> Decided now, and it is a **CLARIFICATION** — the intent was always one engine
> for both arms: **the engine is pinned as part of this pre-registration.** Both
> arms are graded by one build, the primary denominator is computed by that same
> build, and the run that reports the verdict prints the fingerprint it used. The
> build is the one in this tree at `7846191`; built fresh into an empty
> `LYPNING_HOME` on 2026-09-13 it reports `engines.identity()` fingerprint
> **`42ab2d3b7608dfe1`** over `lypning 609f9338…cffdc7f1` and
> `lypning-l bf16bd08…a820c3b2` at `oracle_python 3.11.15`, both binaries saying
> `for cpython 3.11`. It is written down here so that a later build can be *seen*
> to differ rather than assumed to match, and it is re-read from
> `engines.identity()` rather than remembered (invariant 3). `lypning doctor`'s
> `reference cpython` row must read OK, not WARN. If the engine moves before the
> eval, the re-grade is free and both arms move together; what is not permitted is
> two arms, or an arm and a denominator, at two fingerprints.

## 4. What would make a positive result a lie

| confound | the check | status |
|---|---|---|
| engine differs between arms | `engines.identity()` records the graded-against binaries and the oracle CPython on every run; `stats._engine` withholds the subtraction on any difference, and `nt compare` also refuses when the live engine computing the denominators is not the one the runs were graded by | **covered only where both sides recorded a fingerprint — silent, not withholding, where either did not, which is every run on disk today. §3d pins the engine and says what to check.** |
| statistical rule cannot see the effect | `nt power`, both legs, on the pre-registered denominator and at more than one effect shape | **§3c: near-blind to a concentrated win; a decision is owed before the spend** |
| degenerate cases | 70-case primary figure, cases named mechanically above | covered |
| model echoes the input | the corpus's negative control: the original program is recorded per case, and an output equal to it is a fail by construction on non-degenerate cases | covered |
| model games the acceptance test | `sample.discriminate()` perturbs the input and requires the candidate to follow the mutation | **sampling path only — NOT wired into eval** |
| prompt drift | `prompt_sha` recorded per run, compared by `stats.comparability` | covered |
| grading nondeterminism | re-grade the same attempts twice and diff | to be run before the comparison |
| training contamination | train split only; `holdout.lock.json` verified before sampling and again before training | to be run |

## 5. Spend

Hugging Face for everything, decided 2026-09-12: training on HF Jobs, weights on
the Hub, evaluation inference on HF. `RUNBOOK.md` has the arithmetic; the whole
run is ~$16.

**SAMPLING** goes through the HF router at the novita price for
`Qwen/Qwen3.8-27B` — $0.42/M in, $3.00/M out, read from `/v1/models` on
2026-09-12 — bounded by `--max-spend`, which needs `NTX_PRICE_IN` and
`NTX_PRICE_OUT` exported or it is a no-op.

**TRAINING AND EVALUATION** go to HF Jobs on **one `h200`** ($5.00/hr, 141 GB),
under a platform-enforced `--timeout`. The checkpoint is 55.6 GB and rank-16 LoRA
adds ~1.5 GB of optimizer state, so it fits on a single card with room to spare
and there is no sharding — `distributed: fsdp2` comes out, and with it the
largest class of config error in the run.

**AND THE ARMS COME OUT OF ONE vLLM INSTANCE.** This amends §2(a). The baseline
re-graded above was GENERATED on novita's serving stack through the router; a
tuned arm served from our own vLLM would differ in kernels, sampling and
tokenizer handling, so a delta between them would be part weights and part
stack with nothing to separate the two. The evaluation is therefore one Job, one
vLLM process, two arms — base `Qwen/Qwen3.8-27B` with `--enable-lora`, the
held-out split generated twice, once without the adapter and once with it, same
seed and same decode budget.

**CLARIFICATION, 2026-09-13: "one vLLM instance" is not what runs, and the
requirement it stands for survives anyway.** `gpu/lypning_lora.py` — the only
executable form of this step — serves both arms from HF `transformers`
(`Qwen3_5ForConditionalGeneration`, `model.generate`), and `--base-arm` is a flag
on the whole invocation, so the control arm is a **second** Job with its own
55.56 GB pull rather than a second pass inside one. The pre-registered
requirement is **one stack for both arms**, not one process, and the script meets
it and writes the stack down: `backend.base_url` becomes
`incontainer://transformers-<v>+torch-<v>+<kernels>/<gpu>`, so two arms that did
not come off identical software land as a `stats._arm` difference and `nt compare`
refuses. The cost consequence is not a clarification and belongs to `RUNBOOK.md`,
which carries the arithmetic; the "costs nothing extra: it is the same Job"
sentence below is true of the vLLM design and false of the script.

So `qwen38-regrade-20260912` is **not** the reference the fine-tune is measured
against. It keeps its job — it is the engine-drift null test in §3b, where both
arms genuinely are one stack and it is the right instrument. The fine-tune's
reference is the no-adapter arm of the eval Job, which costs nothing extra
because it is the same Job.

**The experiment is abandoned, not rescued, if:** the re-graded baseline leaves
fewer than 15 movable cases; verified on-policy SFT yields fewer than 150
examples; or the safety gate fails on the fine-tuned arm.

Over the 65 usable cases the baseline point is 0.4433 (still graded by this
morning's engine, so it will move again on the re-grade), with 27 that never
pass, 16 that always do, and **22 movable**. Power is unchanged by the
exclusions: paired 80% at +4pp, unpaired 80% at +15pp.

**Re-measured 2026-09-13**, all four figures verified as written on
`qwen38-baseline-k16`, and the re-grade does move them: at
`qwen38-regrade-20260912` the 65-usable point is **0.4615** with 25 never, 16
always, **24 movable**, and the primary-70 point is **0.4286** with 30 never, 16
always, **24 movable**. The 15-movable abandon condition is not tripped on any
denominator. "+4pp" is the *uniform-smear* lift and §3c is the qualification it
needs: that same 80% becomes 14% against a fine-tune that solves six
previously-hopeless cases outright.

Sampling 117 clean train cases at k=16, keep=2 is projected to yield well under
150 examples. **The sampling run therefore uses k=32 and keep=4**, decided here
rather than after seeing a thin result — roughly 3,700 draws, about $6 at the
novita price, and the abandon threshold above stands.

## 6. The outcome, recorded 2026-09-14

A pre-registration without its result is half a document. This section is
written after the run and says so; everything above it was fixed before.

**The run.** 4,640 draws over the 290-case pool ($8.81), 318 SFT examples of
which **222 on task** — the §2(g) abandon threshold of 150 did not fire, where
the same threshold fired at 91 on the previous 93-case pool. The difference is
not a better model: `lypning harvest` folded 7,243 published sightings that had
never been derived into the corpus, and the pool went 93 → 290. A rank-16 LoRA
over 236 examples, 45 steps, 3 epochs, validation loss 0.1133. Both arms
generated in ONE container and graded by one engine, `9d412a3131dc6a8a`.

**The verdict: no win.**

| primary, 70 non-degenerate | |
|---|---|
| base arm | 41.16% |
| tuned arm | 45.62% |
| paired delta | **+4.46pp**, 95% CI [+1.52, +7.86] |
| bootstrap leg | **fires** |
| McNemar leg | does not — gained 2, lost 2, p = 1.0000 |

**§3 said the disagreement between the legs would be the finding, and it is.**
The adapter produced a BROAD shift rather than acquisitions: 16 of 70 cases
improved their per-case rate and 6 got worse, but only two crossed from
never-solved to solved and two crossed back.

**The §3c amendment did not change the verdict.** It was made on 2026-09-13,
before this data existed, to gain power against a CONCENTRATED effect — six or
eight cases solved outright. The effect came out broad instead, which is the
shape the superseded per-case-mean statistic suited better, and that statistic
gives **p = 0.0525**: also above 0.05. Neither rule fires. The prediction that
motivated the amendment was wrong, the amendment cost nothing and bought
nothing, and the only reason any of that is checkable is that it was dated
before the arms were graded.

**No slice was traded for another**, which §2(c) exists to detect:

| slice | n | base | tuned | delta |
|---|---|---|---|---|
| rewrite | 48 | 20.31% | 24.35% | +4.04pp |
| ceiling | 14 | 80.36% | 88.39% | **+8.04pp** |
| unobserved | 8 | 97.66% | 98.44% | +0.78pp |

Ceiling cases catch a model that learns "never import". They rose, so the
rewrite gain is not bought by damaging them.

**What this does not license.** It does not say the adapter does nothing:
+4.46pp with an interval excluding zero is evidence of something. It says this
design cannot separate it from noise at the level fixed in advance — and §3c
measured that in advance too, 27% power at six newly-solved cases and 60% at
eight. Two is below the floor by construction, so "no win" was the likely
verdict for a real-but-broad effect before the first dollar was spent.

**Spend: $28.70** — sampling $8.81, training $4.95, tuned eval $3.96, base eval
$4.75, three failed jobs $1.08, grading and comparison $0.00.

---

## 7. v2: the endpoint changes, and this section is written before the run that will use it

**Status: registered, not started.** Nothing in §7 has been sampled, trained or
evaluated. It fixes the rules for a run that needs authorisation and money it
does not have yet; §7f says how much.

### 7a. Disclosure, first, because the order things happened in matters

An external review of this experiment argued that §3's second leg tested the
wrong hypothesis. Acting on it did **not** require a new run: every program of
the run of record was stored, so the proposed endpoint was computable from data
already paid for. That computation was performed **before this section was
written**, and its result is in `REVIEW.md` §1.

So §7 is not blind. It is written by someone who has already seen what the new
endpoint says about the old run. Two consequences, and both are constraints on
what §7 may claim:

1. The `REVIEW.md` §1 numbers are **exploratory re-analysis of a completed run**.
   They are not a pre-registered result, no verdict of §3 is revised by them,
   and §6 stands exactly as recorded. What they found is a null — ΔSLR −1.00pp,
   95% CI [−3.36, +1.51], against a base-vs-base null of −0.22pp [−2.46, +2.03]
   — so the thresholds below were not chosen to clear a bar this data had
   already cleared. That is a weaker defence than blindness and it is the only
   one available.
2. The thresholds below (an MDE of +3pp, gate A at 2pp, gate B at 0.80, gate C
   at 20%) were chosen with that re-analysis visible. They are stated here so
   that a future reader can see they were *not* chosen blind, and can discount
   them accordingly. They come from the review, which proposed +3pp and 2pp
   before seeing any legality number at all; the two it did not propose are
   marked.

This is the same discipline §3c was held to and failed: that amendment was
dated before the data, and it still predicted the wrong effect shape. Writing
the provenance down does not make a post-hoc threshold pre-registered. It makes
it *legible*.

### 7b. The primary endpoint

**Subset-Legality Rate.** Over a fixed task set, k programs per task at fixed
sampling parameters:

> SLR = programs the pinned engine runs without emitting a refusal
>       ÷ programs generated

First draft only: no retries, no repair loop, no refusal line fed back — a
retry loop measures the harness and not the prior. No correctness gate inside
the number: a program may be legal and wrong, and keeping correctness out is
what makes SLR isolate the quantity being moved. The denominator is programs;
the *unit of inference* is the task.

**One leg, not two.** §3's conjunction failed because one conjunct asked an
acquisition question this project is not asking. §7 replaces it with a single
primary leg and three gates. A gate that fails **voids** the result; it does not
discount it.

- **Primary:** ΔSLR, paired, **cluster-bootstrapped by task**. The k draws for
  one task are one observation of the model's behaviour on that task, not k
  independent ones; resampling programs would return an interval far too narrow
  and would report a single task changing its mind as a significant effect.
  `stats.paired_delta` over per-task rates is exactly this bootstrap and is the
  same one §3's correctness leg already uses.
- **Minimum detectable effect:** the lower bound of the 95% CI must exceed
  **+3pp**. "Excludes zero" is not enough at this n — with thousands of programs
  it will exclude zero for effects too small to pay for anything.
- **Reported beside it, never instead of it:** the **by-kind refusal vector**.
  A scalar SLR can move entirely because the model stopped reaching for `re`
  while twenty-eight other refusal kinds do not budge. The vector is what
  predicts generalisation; the scalar is what fits in a headline.

### 7c. The gates

| gate | blocks | rule | source of the threshold |
|---|---|---|---|
| **A** correctness non-regression | the empty program, the hard-coded literal | tuned correctness pass rate may not fall more than **2pp** below base | the review |
| **B** supported-import retention | "never import anything" | on tasks whose reference solution imports a module the engine *serves*, the tuned model must still reach for it at ≥ **0.80×** the base arm's rate | threshold chosen here, not by the review |
| **C** length non-inflation | paying for legality in output tokens | mean completion tokens may not grow more than **20%** | threshold chosen here, not by the review |

Gate B asks the **engine** which modules it serves. A hand-written table of the
subset would be invariant 1's failure mode relocated: it describes what you wish
the engine did, and it goes stale in the direction that flatters the result.

Gate C exists because the economics do not automatically favour the rewrite. A
refusal costs one CPython spawn, about 12 ms. A tier-1 rewrite that hand-rolls a
scan instead of calling `re` can cost more in output tokens than the spawn it
saves, at any SLR including 100%.

The degenerate-literal guard already in `sample.py` stays, and does its work
under gate A.

### 7d. The engine is frozen first, and the build order never comes from held-out

Under a correctness endpoint a moving engine is a confound a recorded
fingerprint can flag afterwards. Under a legality endpoint it is
**definitional**: SLR is measured relative to what this binary accepts, so
serving one more module raises every arm's SLR with no change to any model.

This repository has already done that twice, in the open, in its own changelog:
`math` was implemented because it "was the top row of `conformance --plan` on
both lists — the corpus's and a fine-tune's held-out set", and the numeric-method
feature is recorded as "paid for by the held-out model set, not by the corpus".
Both are honest entries. Both are test-set steering, and a fingerprint cannot
see them: it detects drift *between two arms*, not the choice of what to build.

Four rules, all enforceable:

1. **Freeze before anything runs.** One engine build for the whole window, both
   arms. `nt grade --require-fingerprint <fp>` refuses to grade against any
   other, so this is a precondition and not a note. *(Shipped.)*
2. **The build order comes from train + corpus only.** `nt refusals --held-out`
   now prints a banner saying its output is a description and never a build
   order, and drops the words "the engine build order" from its footer.
   *(Shipped.)*
3. **Every SLR is quoted with its fingerprint.** `SLR = 61.3% @ 9d412a3131dc6a8a`.
   A bare SLR is not a number. *(Shipped: `legality.compare` refuses two
   fingerprints in one comparison and the report leads with it.)*
4. **Report which held-out tasks changed population** between the frozen
   baseline and the run.

### 7e. The populations, and why the current one cannot answer the deployment question

Held-out today is 74 cases: 52 `refused:*`, 14 `ceiling:*`, 8 `unobserved`. The
66 corpus-derived ones exist *because a program refused* — a program **Claude
Code** wrote, not one the model under test wrote. That is the refusing tail of
the task distribution. It is a legitimate slice and it is where an effect will
look largest. It is not what deployment looks like, where an agent writes a
hundred programs and what matters is the fraction of all hundred that run.

v2 evaluates three populations and names them separately:

| slice | what it is | role |
|---|---|---|
| **all tasks** | an unconditioned sample of the task distribution, drawn without regard to whether anything refused | **primary** — the deployment number |
| **refusing tail** | tasks where the base arm's legality is below some fixed rate | secondary — continuity with v1, largest effect |
| **the frozen 74** | today's held-out set, untouched | continuity — so v1 and v2 remain comparable |

An unconditioned sample needs a task bank that does not exist, which is 7f.

### 7f. What v2 needs that does not exist, and what it costs

Everything above is rules and costs nothing. The run does not. Stated as
estimates, to be authorised or refused rather than assumed:

| stage | what | estimate |
|---|---|---|
| task bank | reverse-prompt corpus entries into instructions; back-translate tier-1 MATCHes into import-heavy Python; template `docs/COOKBOOK.md`; verify every one against CPython | teacher calls, **~$30** |
| split | cluster by similarity **before** splitting and assign whole clusters group-wise, stratified on refusal kind at cluster level — nothing is dropped and held-out diversity holds by construction, instead of today's split-then-drop-58-leaks | $0, CPU |
| baseline | base model, k=16, thinking off (primary) and on @2k (secondary); record SLR, the by-kind vector, correctness, tokens/program, fingerprint | **~$5** |
| SFT v2 | hinted sampling → unhinted training (context distillation: sample *with* the refusal line and the matching recipe in context, train on the bare prompt), task→program format, completion-masked, 2 epochs, **3 seeds** | **~$30–60** |
| RLVR | GRPO from the SFT checkpoint, reward per the review's F6 with the gate B and gate C terms folded in | **~$50–150** |

A single-seed ΔSLR is not a result and v2 does not report one: three training
seeds, per-seed ΔSLR, and the spread quoted with the point estimate.

**Nothing in 7f runs without explicit authorisation.** The rules in 7a–7e are
in force for whatever runs next regardless of whether 7f is ever funded.

---

## 8. v2 run 1, recorded 2026-09-14: context distillation, and a confound larger than the effect

Registered in §7, run the same day, $23.08. **No win, and the interesting number
is not the verdict.**

### What was done

§7f's first line item — hinted sampling → unhinted training. The draw got the
engine's live refusal for that case plus a matching worked `COOKBOOK` pair; the
SFT row and the eval kept the bare prompt. 4,640 draws over the same 290-case
pool as the run of record, k=16, `--keep 4`, $11.35. Trained on one H200, 48
steps over 3 epochs, 116.7M trainable parameters, $5.53. Both arms generated
in-container on one stack; grading and both endpoints cost $0.

### The result

```
correctness, the §3 rule, 70 non-degenerate (PRIMARY)
  40.71% -> 44.55%   +3.84pp  95% CI [+0.80, +7.50]   McNemar p=1.0000   no win

subset legality, the §7b rule, 2,360 programs over 74 cases
  SLR 44.13% -> 43.83%   dSLR -0.21pp  95% CI [-3.19, +2.67]   MDE +3pp NOT met
  gate A  correctness   41.64% -> 45.35%  +3.71pp     PASS
  gate B  import retention  94.09% -> 100.00%  1.06x  PASS
  gate C  tokens/program   473 -> 358  -24.4%         PASS
```

Bootstrap fires, McNemar does not, exactly as in §6. The legality leg is a null
for the second time, by a different route.

### The finding, which is about the instrument

The same base weights, served through two kernels — `fla-0.5.2` and the
`torch-reference` fallback forced by the Hopper backward bug — differ by:

```
dSLR +1.57pp  95% CI [-0.51, +3.89]      (correctness: -0.14pp)
```

**The serving stack moves subset legality by more than the adapter does.** Had
the hinted arm been compared against the stored `fla` base arm rather than a
control regenerated on its own kernel, the delta would have read about +1.2pp —
a spurious improvement of the wrong sign, entirely manufactured by a kernel
swap. The decision to spend $5.90 on a matched control was taken before any
number was read, and it is the only reason this run reports a null instead of a
win.

Two consequences for every future run:

1. **The serving stack joins the engine fingerprint as a precondition, not a
   note.** §7d froze the engine because SLR is defined relative to what the
   binary accepts. This says the same of the kernels: SLR is also defined
   relative to how the tokens were produced. `stats.comparability` already
   records `backend.base_url`; what this adds is that a mismatch there is
   disqualifying for the legality endpoint specifically, at a magnitude that
   swamps a real effect.
2. **Correctness and legality have different noise floors.** The kernel swap
   moved correctness by −0.14pp and legality by +1.57pp. Legality is the more
   fragile measurement, because a token-level difference changes *which
   construct* a program reaches for, while correctness survives it. An endpoint
   that is more sensitive needs more control, not less.

### What it says about the technique

Context distillation changed what the model writes and not whether the engine
will run it. Of the 61 rewrite cases both sampling runs solved, only 19 kept an
identical program and 28 differed substantially; the tuned model's programs are
**24.4% shorter** and it hits the decode cap a third as often (16/1184 against
51/1184). The by-kind vector moves a lot — `class` refusals −22, `module-attr`
+43 — and nets to nothing.

So the intervention worked on the model and not on the metric. Combined with §6
of `REVIEW.md` — the case prompt already names the refusal, so the constraint was
never absent — the reading is that **the format is the binding constraint, not
the supervision**. *Refused program + refusal line → rewrite* is a repair-loop
prompt, and every remaining lever in §7f (the unconditioned task bank, RLVR,
three seeds) is worth less than fixing that first. §7e's task bank moves from
"registered for v2" to the next thing to build.

### Spend

| | |
|---|---|
| hinted sampling, 4,640 draws | $11.35 |
| training + tuned generation, 66 min H200 | $5.53 |
| matched control arm, 71 min H200 | $5.90 |
| two launches that died in phase 0/1, before the 55 GB pull | ~$0.30 |
| grading, both endpoints, the null test | $0.00 |
| **total** | **$23.08** |
