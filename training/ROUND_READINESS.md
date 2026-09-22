# Round readiness — 2026-09-19

What still has to be true before a GPU is booked, in order, with the state of
each. `STATUS.md` §10 owns **which** round runs and whether it runs at all; this
owns **what is left** for the one being prepared, and nothing here authorises a
spend. Every number carries the command or run that produced it.

**2026-09-21.** The round this file prepared ran as seed 1111 (job
`6ab01cbb51992417dfccd64c`) and is read in
[`reports/2026-09-21-fable-round02-seed1111-read.md`](reports/2026-09-21-fable-round02-seed1111-read.md).
The next round is not this one again: [`PLAN.md`](PLAN.md) Step 1 changes the
selector, the learning rates and the eval cost before a GPU is booked, so the
readiness below is necessary and no longer sufficient.

**2026-09-22 cost-bound amendment (Step 1.5).** The proposed pilot timeout is
720 minutes. The launcher resolves it to 43,200 seconds for both the provider
and an independent in-container process-group deadline: TERM at 719 minutes,
KILL by 720, including bootstrap. At the historical $5/h rate this is a **$60
GPU ceiling per seed**, not the earlier $25 estimate; CPU pool costs are extra
and the current preflight price table controls. The workflow value is not spend
authorization. The next paid round still needs an operator ceiling, all six
preflight checks and a hardware smoke of the 256-sequence evaluation batch.
Smaller explicit batches remain possible but must match across all arms and
seeds; changing the chunking changes the sampling instrument.

## Current Step 2 admission — 2026-09-22

**State at the end of 2026-09-22.** Paid Cerebras generation has run; no GPU job
has been submitted. Codex dispatched the rungs under the per-rung ceilings in
`step2-control.yml`; the approval is not recorded in this tree.

| run | what | cost, charged or reserved | outcome |
|---|---|---|---|
| `35751938025` | smoke, 64 cases × 2 arms × k=4 | $1.20715678 of $5 | 512/512, zero retries |
| `35759939928` | grade smoke (no provider, no GPU) | $0 | pooled with controls: native +14.78pp [+3.89, +26.81], correct −9.01pp [−17.34, −2.02]; neither route earned; 157 targets / 54 cases |
| `35762924601` | S4 target preflight | $0 | 156,691 scheduled tokens vs 50,000, by repetition |
| `35763603648` | targets, 192 × 2 × 4 at 60 rpm | $0.90358492 of $6 | stopped at 327/1,536; not gradeable |
| `35767396604` | targets, 192 × 2 × 4 at 45 rpm | $3.63061517 of $6 | complete 1,536/1,536; **not yet graded** |

Two further runs failed before spending (`35749197934`, `35758545464`). What
remains before a GPU is booked is in the independent review,
[`reviews/2026-09-22-claude-step2-s4-review.md`](reviews/2026-09-22-claude-step2-s4-review.md)
(**revise, prepare**), and in `PLAN.md` Step 4: a coverage-only grade of
`35767396604`, seed 1111's kernel read, and operator decisions on a full-split
target rung — arm A now needs verified targets on ≥ 1,000 of the 1,355 train
cases — and on evaluation draws. Bundles must be re-prepared before arm A's
first seed (`verifier_sha256` and `code_sha256` changed). The text below is
the morning's free-first record, kept as history.

PRs #97–#101 are merged. The six-check free preflight
[35683103077](https://github.com/kristerhedfors/lypning/actions/runs/35683103077)
passed, with zero live HF jobs and no completed seeds to join on this bank.
That does not authorize another old-configuration seed.

The positive-control plan is now measured: **1,355 training cases, 31 families,
16 draws in each of two arms, 43,360 requests**. Dev/test are excluded. Cerebras
lists Qwen 3.8 27B at **$0.99/M input and $1.49/M output tokens**
([pricing](https://www.cerebras.ai/pricing), checked 2026-09-22). The pinned-tokenizer
count gives **$92.81 of input alone**, dominated by repeating the subset spec.
At 512 output tokens/request the estimate is **$125.88**; at the 2,048-token
allowance it is **$225.12**, before tax, retries or provider-template differences.
These are cost scenarios, not a bill or an enforced ceiling. The old ~$5
estimate is withdrawn for this full population.

The operator chose **free first** on 2026-09-22. Continue free validation,
diagnostics and preparation; hold paid inference and GPU training. No paid
ceiling is approved. After free readiness is complete, present its result and
the concrete scope/cost before requesting a paid ceiling. The previously
offered $250 full comparison and $25 redesigned control remain unapproved
options; at that point no paid provider call or GPU job had been dispatched
(the rungs above followed the same day).
Do not shrink the population, shorten the spec, lower k or substitute a model
silently to fit the old estimate.

[PR #102](https://github.com/kristerhedfors/lypning/pull/102) prepares the actual
training-split control. Pinned-base conformance
[35683688427](https://github.com/kristerhedfors/lypning/actions/runs/35683688427)
loaded 9,064 corpus programs: 4,560 match, 1,757 unsupported, **zero mismatch**,
2,747 skipped, and no repository damage. This is the large native arm on the
pinned CPython 3.12.14 base, not a waiver of macOS 3.14 failures. The initial
reference admission did not finish: run
[35684815342](https://github.com/kristerhedfors/lypning/actions/runs/35684815342)
hit its 70-minute job limit after 69 minutes in reference verification, with no
intermediate counts. This is CPU container startup/verification work, not GPU
training. The checker now reports aggregate completion and an estimated time
remaining every 30 seconds, selects up to eight concurrent cases within CPU
and container-memory limits, and stops new work on failure or after 50 minutes.
Partial results cannot create an admission proof. Free rerun
[35693996662](https://github.com/kristerhedfors/lypning/actions/runs/35693996662)
**passed**, completing at 06:56 UTC on 2026-09-22. All 1,355 references passed:
1,132 correct-native and 223 correct-control, no failures, eight workers,
2,412.9 seconds (40 minutes 13 seconds). Its provider/tokenizer plan and exact
conformance reuse also passed; provider calls were zero. The public aggregate
report proves validation completed, but does not replace the private admission
proof required by generation. The generation library reserves budget before each
request, disables retries, and binds its input to a private runtime/reference
proof. Since #103 the manual dispatch exists and admission evidence, completions
and graded rows persist in the private dataset repo. The 256-sequence GPU batch smoke
belongs before a later GPU training arm; Step 2 uses hosted inference and does
not allocate a GPU.

## The bank a round would now read

**2026-09-22.** `round02.yml`'s `BANK_PATH` is `banks/v3-20260920b`, the bank
seed 1111 read; its seed-1111 train split is the 1,355 cases admitted by
`35693996662`. The table and counts below are the 2026-09-19 cut and the
authored-reference schedule — seed-1111 history. Arm A trains on target rows,
counted by `s4-target-preflight`, not on these.

`banks/v3-20260919` in the private artifact repo, cut from six batches by
`bank-publish.yml` (run `35439152832`, 2026-09-19) and carved so the two halves
share no family:

| | cases | families | coverage | control |
|---|---|---|---|---|
| `train.jsonl` (pilot) | 8,370 | 47 | 44 | 18 |
| `eval2.jsonl` (benchmark) | 4,633 | 22 | 22 | 6 |

Priced on that bank by run `35439221438`: at `--steps 300` it exposes
193,677 / 197,707 / 191,153 supervised tokens at seeds 1111/2222/3333 against
the 50,000 floor, with 4,840 / 6,755 / 6,335 train cases against the 1,000
floor, no row over `--max-seq`, and the eos admission passing. Its smallest
clearing `--steps` is 74.

## Where we are in one paragraph

*Superseded 2026-09-22 by "Current Step 2 admission" above; kept as the
2026-09-19 record.* **Every preparation is done and the only step left is the operator's.** The
bank exists, is carved, is published, and is priced; the launcher points at it;
and the billed submit cannot start until a free job has counted the schedule it
would bill. What remains unmeasured is the thing a round is for: whether
training installs the effect. bank v3 has room for one — `bank-native
--mix-only` read a macro delta ceiling of 11.00pp and 11.33pp on its batches
against a 3.00pp bar, where bank v2's completed base arm left 3.14pp — but a
ceiling says an effect *can* exist, never that one will.

## The ladder to a first real round

| # | What | State |
|---|---|---|
| 1 | Generation reaches the provider | **done** — `backends.USER_AGENT`; the 403 was Cloudflare 1010 on `Python-urllib`, not the key |
| 2 | A generate run survives adapt | **done** — one malformed row no longer discards the batch; adapt shards four ways |
| 3 | The bank carries fallback controls | **done** — 1,601 control rows; the pre-fold publisher had discarded every one |
| 4 | The bank clears the plan-time gates | **done** — run `35430465717`, all three seeds |
| 5 | The bank can host the effect | **done, with a caveat** — ceiling 11.00/11.33pp vs a 3.00pp bar, but this is `bank_native`'s free proxy on the bank's own programs, an upper bound on movable delta and not a base-model draw |
| 6 | Enough families for a pilot **and** a disjoint benchmark | **done** — pool 37 → 71; one run drew all 71 (run `35431441875`: 9,154 candidates, 28,799 calls) |
| 7 | Carve `banks/v3/{train,eval2}.jsonl`, disjoint by family | **done** — `nt bank-carve`; validated against bank v2's union, which it carves back to its own hand-made 51/18 shape |
| 8 | Publish to the Hub and point `BANK_PATH` at it | **done** — `banks/v3-20260919` pushed (run `35439152832`); `BANK_PATH` repointed |
| 9 | The exact supervised-token count | **done, and it caught a refusal** — run `35434623069`: `--steps 250` exposes 46,535/45,952/44,940 against the 50,000 floor, so the pilot as configured would have been refused after the weights. `PILOT_STEPS` is now 300 and the billed submit needs a passing count |
| 10 | Operator authorises the spend | h200 at the 720m ceiling ≈ $60/seed (2026-09-22 amendment above; ≈ $25 at the old 300m); a complete S4 is three seeds (1111, 2222, 3333) |

Two open items that block nothing above but change how a result reads:

- **Dev and test hold 4 families each** against 29 in train (run `35430465717`).
  No gate checks this and a family-clustered bootstrap will feel it. The carve
  makes it worse before it makes it better: it takes families away from the
  pilot for the benchmark, so the widened pool is what pays for both.
- **One engine mismatch was filed**: `sys.stdin.read(n)` ignored its size
  argument and returned the whole remaining stream, in
  `data/engine-mismatches.jsonl`. **Fixed 2026-09-22 in #101** (`PLAN.md` Step
  1.6). Invariant 1 made it interpreter work, not training data.

## The stdlib corpus: an arm, not an ingredient

The corpus that landed in #88 is **complementary training material and is
deliberately not in the first round.** The reason to hold it back is that
mixing it in and measuring nothing else would tell us the round's result but not
what the corpus bought; running the round without it first leaves an arm to
compare against.

Two facts decide how it is used, and both are measured rather than assumed:

1. **A unit cannot be a bank case.** `validate_cases` requires three
   independently specified tests whose outputs differ. A unit is a
   self-contained program with no stdin, no argv and one fixed stdout, so it is
   refused, and there is no honest way to give it three. `pipeline.stdlib_sft`
   therefore emits **supervised rows**, never cases, and they never reach a
   split or a benchmark.
2. **The corpus overlaps the bank's surface almost completely.** 14 of the 15
   modules it fills also appear in the bank's construct pool (measured
   2026-09-19). Against a realistic bank-v3 family list, **19 of 34 units are
   contaminating** and are held back, leaving 15 rows over 9 modules. Mixing a
   `textwrap.wrap` unit into a round whose benchmark holds a `textwrap` family
   is teaching toward the test.

So the arm is gated, not default:

```bash
PYTHONPATH=src:training python3 -m pipeline.cli stdlib-sft \
  training/data/stdlib/stdlib.jsonl \
  --held-out-bank <the round's eval2 bank> \
  --output work/stdlib-sft.jsonl
```

It drops the overlapping units by name, prints which modules caused it, and
exits 1 when nothing is left to mix — a bank whose benchmark reaches every unit
cannot measure this arm at all, which is a finding about the pairing rather than
a usage error. `--allow-overlap` keeps them, and then the benchmark result has
to be reported as contaminated.

**The comparison, when it is run:** same bank, same seeds, same benchmark, two
SFT arms — one without these rows, one with. The difference is what the corpus
bought. There is no point running only the mixed arm, because nothing would be
left to compare it to.

## The carve, and what it refuses

`nt bank-carve <bank> --output <dir>` writes `train.jsonl` and `eval2.jsonl`
that share no family, and writes **nothing** unless both are admissible: the
pilot must pass `validate_pilot` after `split_cases` at every protocol seed,
not the one the caller passed, because a bank admissible at 1111 and not 2222
fails a three-seed round halfway through.

The floors it enforces are arithmetic, and it reports them rather than failing
later: ≥18 families per bank (`validate_bank`), and **≥6 control families in
the pilot**, because `split_cases` hands each split
`max(2 if groups >= 6 else 1, groups // 6)` families of a population and
`validate_pilot` wants two per split. With the benchmark's own ≥2, that is 8
control families minimum. bank v3 had 10 before the pool widened, which is why
this was the step that could not be taken.

Checked against the one bank whose carve is known: bank v2's two banks were
separated by hand into 51 and 18 families, and re-uniting and re-carving them
reproduces 51/18.

## The count that now stands between the marker and the meter

`round02.yml`'s billed `submit` job `needs: token-floor`, a free tokenizer-only
job that counts exactly what the schedule exposes and exits 1 when it is short.
Both read the same `PILOT_STEPS`, so the schedule that was counted is the
schedule that is billed.

It earned its place immediately. On `banks/v2` (run `35434623069`, 2026-09-19)
the pilot's own `--steps 250 --batch-size 4` exposes **46,535 / 45,952 /
44,940** supervised tokens at seeds 1111/2222/3333, against the 50,000 floor —
refused by `run()` before the first optimizer step, after the dependency
install, the bank download, bundle preparation, the unadapted base-dev arm and
55.6 GB of weights, three times over. The `--plan` upper bound for the same
schedule is 134,384 and passes.

`PILOT_STEPS` is 300. The smallest clearing value on that bank was 269/273/279
by seed; **a different bank needs its own count**, which is what the job is for
— bank v3's rows are not these rows.

## What is deliberately not claimed here

- That bank-v3 will produce a positive result. The ceiling says an effect *can*
  exist in the population; it says nothing about whether training installs one.
- That 11.00pp of ceiling is the endpoint. The endpoint is the correct-and-native
  family macro at k = 16 on a held-out benchmark (`EVAL2.md` §4); the ceiling is
  a free proxy read off the bank's own programs before a draw is paid for.
- That the stdlib arm helps. It is set up to be measured, and has not been.
