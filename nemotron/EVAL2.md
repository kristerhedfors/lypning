# Eval-2 pre-registration: the deployment benchmark, fixed before a draw exists

Written **2026-09-15, before any eval-2 case has been reverse-prompted, before
any pilot draw has been sampled, and before any adapter has been evaluated on
an unconditioned task.** The discipline is `PREREGISTRATION.md` §3c and §7: a
rule chosen after the number is visible is not a rule, and every threshold
below is stated with where it came from so a later reader can discount it.
Every number carries its run and date (`CLAUDE.md` invariant 3); nothing is
remembered.

## 1. Purpose

Eval-2 measures the deployment quantity: the rate at which an agent that does
not know lypning exists writes a first-draft program that is correct and that
`lypning-l` runs natively. Eval-1 — the frozen 74-case hold-out, manifest
`80b2fc52…`, `prompt_sha cbb7be44937a6b41` — measures compliance with a rewrite
instruction, because every one of its 52 corpus-derived `refused:*` cases
embeds the failing program and names the refusal (`REVIEW.md` §6, 2026-09-14;
populations in `PREREGISTRATION.md` §7e). Both runs of record are null on
eval-1 (ΔSLR −1.00pp and −0.21pp, 2026-09-14, `STATUS.md` §2), and the only
preview of the unconditioned prior is 8 held-out cases that reached the subset
on 89.06% of draws (`LADDER.md` stage 0a, 2026-09-14, engine
`23684d6c40738fcf`). Eval-2 is the prior, at scale, on purpose. That
`prompt_sha` is the value every recorded run carries; since 2026-09-16
`evaluate.prompt_signature` also hashes the rendered runtime contract, so
the same bare prompt reads `d23e9420b5812443` on a new run and the two values
name one prompt (`AUDIT.md`, *prompt-signature-blind-to-render-contract*).

## 2. The bank

One shared case bank, in the schema-3 shape, serves both homes in §3. Each
case carries `case_id`, `family`, `source_group`, `capabilities`, `task`,
`reference`, `provenance`, `population` (`coverage` or `fallback-control`),
`tests` with at least three distinct inputs and at least two distinct expected
stdouts, and a `review` record with `origin` and `evidence_ids`
(`pipeline.training_data.validate_cases`, `pipeline.data_loop.review`;
`TRAINING.md` "Data admission: schema 3").

**Source.** Cases are reverse-prompted from captured ordinary programs in
`data/classified.jsonl`, drawn **without regard to whether the engine refuses
them**: the draw is over the `tier1` and `refused` outcomes together, never over
`refused` alone, because a refusing-tail bank is eval-1 again. On this tree on
2026-09-15 the file held 9,064 entries — 618 `tier1`, 643 `refused`, 2,752
`skip`, 5,051 `unusable` — counted by
`python3 -c 'import json,collections;print(collections.Counter(json.loads(l)["outcome"] for l in open("nemotron/data/classified.jsonl")))'`
from the repository root; re-run before relying on it. Authored
`fallback-control` cases are added so the bank as a whole carries both
populations, which `training_data.validate_benchmark` requires; a control's
reference is correct and cleanly refused on every input, and a model that later
solves one natively is not penalised.

**Task text.** The task never names the runtime or the boundary: not
*lypning*, *refuse*, *unsupported*, *tier*, *engine*, *CPython*, not "without
importing", and no module name stated as a constraint. A task that says "do not
use `re`" is a rewrite instruction wearing a different coat. The task must
state how input arrives — stdin, `argv`, or named input files — because a
reference that reads `sys.argv` cannot be graded against a draw that reads
stdin, and `tests` may vary exactly those three channels.

## 3. Two homes, and what each may produce

The number of record is produced on the task-first path and nowhere else.
`gpu/train_verified.py eval` on a bundle prepared with `--purpose benchmark`
and evaluated with `--eval-split all` — the whole bank, in one pinned container
and one kernel, both arms in the same container — is the only home of a
base-versus-adapter comparison on eval-2. The bank is never trained on, so
`validate_benchmark` asks for both populations somewhere in the bank and for a
`split_group` on every case, not for the per-split population rule of a pilot.

The legacy `nt` tree is the home of everything exploratory and of nothing
comparative. In its own `NTX_ROOT`, with `nt split --fraction 1.0` so every
case is held out, and `nt eval` against one pinned provider, it produces: the
base-rate pilot (§7), the design-specific power analysis (§7), the stage 0b
prompt-ceiling probe (`LADDER.md` stage 0b) and the SLR by-kind census
(`nt legality`). It never produces a base-versus-adapter delta, because the
serving stack alone moved legality by +1.57pp (95% CI [−0.51, +3.89]) between
two kernels on the same weights on 2026-09-14 (`PREREGISTRATION.md` §8), which
is more than either adapter did, and a provider endpoint is a serving stack
nobody here pins.

## 4. Primary metric, frozen

The primary metric is the **correct-and-native first-draft rate**: a draw
counts when it prints the expected stdout on every test input under CPython
**and** runs natively on `lypning-l` on every input; macro-averaged over
families; sampled pass@1 at **k = 16** draws per case (`--eval-draws 16`; the
script's default of 4 is a smoke setting). The comparison is paired,
cluster-bootstrapped by `split_group` with families resampled as whole
clusters — `pipeline.training_metrics.paired_comparison`, 2,000 resamples,
percentile interval. The rule: the 95% interval's **lower bound above +3pp**,
the minimum detectable effect of `PREREGISTRATION.md` §7b (2026-09-14), whose
provenance §7a records as chosen with a re-analysis visible.

Reported beside it, never instead of it: correctness alone (the `correct`
metric of the same comparison), the subset-legality rate (`nt legality` on
the same draws), and the by-kind refusal vector. Gates A, B and C apply exactly
as `PREREGISTRATION.md` §7c: correctness may not fall more than 2pp below base,
supported-import retention at least 0.80× base on tasks whose reference imports
a module the engine serves, mean completion tokens not more than 20% longer.
A failed gate **voids** the result; it does not discount it.

## 5. Decoding contract

Every eval-2 draw uses `pipeline.training_contract.decoding` verbatim:
temperature 0.7, top-p 0.8, top-k 20, min-p 0, repetition penalty 1, one beam,
thinking off, 1,024 new tokens, and a per-draw seed from
`training_contract.draw_seed(seed, case_id, draw)`. Greedy is an eval-only
diagnostic and never the number of record (`TRAINING.md` "Qwen-specific
contract").

A legacy-path arm is a different arm. Until 2026-09-16
`pipeline.backends.complete` passed `temperature`, `top_p`, `max_tokens` and
`seed` and had no `top_k`, so every `nt eval` run before that date sampled
from a different distribution and is quoted as "legacy arm, no top-k". Since
2026-09-16 `backends.py` passes `top_k` and `min_p` through as top-level
request keys (what the SDK's `extra_body` puts on the wire; vLLM and SGLang
read them, a hosted endpoint may not), `nt eval --top-k 20` records it in the
run's sampling block, and `stats.SAMPLING_KEYS` carries `top_k` so an arm that
set it and an arm that did not are never subtracted. A run whose block has no
`top_k` is read as `top_k: null` — the server's default, which is what it
sampled with. Even matched knob for knob (`--temperature 0.7 --top-p 0.8
--top-k 20 --max-tokens 1024 --no-thinking`) the legacy arm is still a
different serving stack (§3) and never the number of record. Its defaults
(temperature 1.0, top-p 0.95, 4,096 tokens, no top-k) are never used for
eval-2.

## 6. Seeds and arms

No adapter claim on eval-2 rests on fewer than three training seeds; the
per-seed delta and the spread are quoted with the point estimate
(`PREREGISTRATION.md` §7f). The base arm is regenerated in the same container
and kernel as the candidate, never read from a stored run: the stored-base
shortcut is what would have turned 2026-09-14's kernel null into a +1.2pp win
of the wrong sign (`PREREGISTRATION.md` §8). Every number is quoted with the
engine fingerprint (`identity` in the bundle, checked by `training.load_bundle`)
and the `lypning-l` sha it was graded against. The bundle digest is the eval-2
identity: the bank is frozen at that digest and **never re-cut**. A bank that
needs a change is a new bank with a new name and its own pre-registration.

## 7. Power

The bank size is set by a design-specific power analysis on a pilot draw, not
by the 300-case floor; 300 (`LADDER.md` stage 4, 2026-09-14) is the floor. The
pilot draw is about 60 to 100 reverse-prompted cases, base model only, k = 16,
on the legacy tree (§3), and it is **spent**: those cases and their draws
inform the size and the effect-shape assumptions and are then set aside before
the bank is frozen, so no case whose base rate was seen chooses the bank.

The analysis resamples family clusters, not cases, and reports the power of
the §4 rule at two effect shapes — uniform (a lift on every family) and
concentrated (a handful of families solved outright, the shape
`PREREGISTRATION.md` §3c found the old rule near-blind to) — as a function of
bank size. The existing `pipeline.stats.power_curve` resamples cases and cannot
do this; the cluster version, `stats.power_curve_clustered`, is written and
tested before the pilot draw is sampled, and its curve is appended to this
document dated. Power is read at an expected effect above the bar, not at
the bar itself: at a true effect equal to +3pp the lower bound sits under the
point estimate and no size reaches 80% power. The bank is the smallest size
at which both shapes reach 80% power for a +5pp true effect under the +3pp
rule, or 300, whichever is larger.

`stats.power_curve_clustered` was written and tested on 2026-09-16, before any
pilot draw (`nt power --eval2 --rows <rows JSONL or run id> --mde 0.03 --sizes
100,200,300,500,800`, on the rows `nt eval2-rows` writes). Every trial
resamples the pilot's clusters with replacement to the candidate size, cases
kept with their families, simulates both arms at k draws per case and asks the
§4 rule; the null (delta 0) row is the false-positive rate. Concentrated means
the 10% of cases with the lowest base rate lifted to one target rate for the
same mean lift as the uniform row, so the two rows differ only in shape.

**The curve, 2026-09-16.** Pilot: run `eval-20260916-063539` on the legacy
tree, the 64 cases of train v1 (§11) at k = 16, 1,024 draws, all 64 cases
drawn (the run was resumed twice through router outages; the 268 attempts that
never reached the model are superseded by their redraws, `nt eval2-rows`, and
no harness error remains in the rows). Base correct-and-native as the power
tool reads it, macro over 64 (cluster, family) units in 63 independent
clusters: 73.1%. `nt power --eval2 --rows work/eval2/legacy-pilot/rows-full.jsonl
--draws 16`, 200 banks per cell, 400 resamples each, rule 95% lower bound > +3pp:

| shape | effect | N=100 | N=200 | N=300 | N=500 | N=800 |
|---|---|---|---|---|---|---|
| uniform | +0pp (false positives) | 0% | 0% | 0% | 0% | 0% |
| uniform | +3pp | 0% | 0% | 0% | 0% | 0% |
| uniform | +5pp | 0% | 0% | 0% | 0% | 0% |
| uniform | +8pp | 2% | 3% | 2% | 2% | 3% |
| uniform | +10pp | 20% | 28% | 40% | 59% | 81% |
| concentrated | +0pp (false positives) | 0% | 0% | 0% | 0% | 0% |
| concentrated | +3pp | 0% | 0% | 0% | 0% | 0% |
| concentrated | +5pp | 9% | 30% | 64% | 98% | 100% |
| concentrated | +8pp | 73% | 100% | 100% | 100% | 100% |
| concentrated | +10pp | 98% | 100% | 100% | 100% | 100% |

Read at the frozen bank size of 300 (supply-capped, §11): the rule has 80%
power only for a concentrated effect of +8pp or more; a concentrated +5pp is
seen in about two banks of three, a uniform +10pp in two of five, and a uniform
lift of +8pp or less is invisible at every size on the grid. The false-positive
rate is 0% in every cell. So a null result from round-02 rules out a
concentrated gain of roughly +8pp on the lowest-base families and nothing
finer; a uniform few-point lift, the shape a small SFT most plausibly produces,
cannot be detected by this instrument at N = 300 and k = 16, and the honest
route to it is more draws per case (k) and a larger bank, not a looser rule.

One caveat on the statistic itself, found while reading the curve. The rows'
summariser (`training_metrics.summarize`) takes the macro over the bank's 49
families; `power_curve_clustered` keys a family inside its cluster, so a
family that straddles source groups (`stdin-nonblank-line-count`, 12 cases in
several groups) is several units to it and the same rows read 68.7% to the
summariser and 73.1% to the power tool. The curve is therefore priced on a
statistic slightly closer to a per-case macro than the §4 rule's, which should
overstate power a little for effects concentrated inside a straddling family.
Follow-up: pool a family across clusters in the power tool, re-run the curve
on the same rows, and replace this table.

## 8. Contamination

A bank-versus-bank leak check runs before any training bundle is frozen, and
eval-2 is never the side that moves. A training case leaks if its task text is
at or above 0.85 similarity to an eval-2 task (the threshold `AUDIT.md` used on
the frozen split, 2026-09-12), its expected stdout is identical on any test, its
`training_data.solution_fingerprint` matches, or it shares a `source_sha256` or
`source_group` with an eval-2 case. The leaking case is removed from the
training side and the training bundle is re-cut; eval-2 keeps its digest. The
27-of-74 contamination `STATUS.md` §6 records for eval-1 (2026-09-12) was handled by
excluding train cases after the fact; here the check is a precondition of
freezing.

## 9. What would falsify

A base rate at or above 90% correct-and-native on eval-2 leaves no headroom
worth training for: the +3pp rule cannot be met inside the remaining 10pp
without a ceiling effect, and the programme's budget goes to the engine
(`LADDER.md` §6). The 8-case preview at 89.06% legal draws (2026-09-14) makes
this a live outcome, not a formality. A stage 0b probe that is flat — under
10pp of SLR lift with the subset description in the system prompt — says a
description cannot install the boundary at this model size, and distillation is
skipped for the signal-based stages (`LADDER.md` stage 0b). Either result is
recorded here, dated, before anything downstream is paid for.

## 10. Costs and approvals

Every paid step is named with its estimate and none runs without the operator's
ceiling. Reverse-prompting the bank: teacher calls, about $30
(`PREREGISTRATION.md` §7f, 2026-09-14). The base-rate pilot and the base arm
on the frozen bank: about $5 each, the ladder's stage 4 total about $40
(`LADDER.md` §5, 2026-09-14). The stage 0b probe on eval-2: about $5
(`LADDER.md` §5, 2026-09-14). Any adapter arm: three seeds at the round-02 rate, an H200 at
$5 per hour (`STATUS.md` §4, 2026-09-15). The power analysis, the leak check
and the SLR census cost nothing.

Decisions requested of Codex, per `ORCHESTRATION.md`: that eval-2 construction
is authorised ahead of the pilot dataset (`STATUS.md` §4 step 3 before step 5),
and that §4 above is the primary-metric freeze `STATUS.md` §6 item 2 asked for.
Until both are recorded in the decision ledger, this document is a proposal and
no draw is sampled.

## 11. Bank v1, frozen 2026-09-16

The first eval-2 bank was assembled and frozen on 2026-09-16 from 691
reverse-prompting candidates (`nt eval2-select`, seed 1111, drawn over tier1 and
refused outcomes together): 397 proposals kept by the authoring agents, 364
admitted by `nt eval2-bank` after the lint, the twice-run reference, the
independent solution (364 of 364 agreeing) and the population label from the
engine (`lypning-l` `a23b3083…`, fingerprint `2e079e786a655ab6`), 294 dropped by
the authors (self-check harnesses, REPL scratch lines, environment-specific and
nondeterministic programs), 20 dropped as mixed native/refused across inputs,
one engine mismatch filed as a witness (`min()` on an empty sequence: the engine
words the error differently from CPython) and never as a case.

Sixteen expected-output coincidences between the two banks (integer square
root, SHA-256 and float division families) were unified into shared source
groups before the cut, so kin travel together. The cut (`pipeline.eval2_split`,
seed 1111, eval-2 size 300, no separate pilot draw) is leak-free on all five
rules:

| Bank | Cases | Families | Coverage | Fallback-control | sha256 |
|---|---|---|---|---|---|
| eval-2 v1 | 300 | 248 | 241 | 59 | `46cff1d740c93b596c80095233818781629e8a9766545410febdf58eca36d37b` |
| train v1 | 64 | 49 | 51 | 13 | `31edda65c2c71dda7e0f46038bf30da283bf10564855a56b866076635bfee9ff` |

Supply capped the bank at the 300 floor, so the pilot draw of §7 is the training
bank itself, drawn on the legacy tree at k = 16: it is spent for eval-2 (no
case in it can enter eval-2) and it is the only training material of round-02.
The power curve on that draw is appended below when it lands, dated. Both banks
and their evidence snapshots are stored privately in `headforce/lypning-round02-work`
under `banks/2026-09-16-eval2-v1/`, commit `7b10488bbc7be5cd28ebc901a5ffd16e028c8986`.

The first pilot draw (2026-09-16, run `eval-20260916-044656`, k = 16) reached
15 of the 64 cases before the account's included provider credits ran out
(HTTP 402 on 796 of 1,024 calls). On the 228 clean draws: coverage 97.4%
correct and 96.9% correct-and-native over 12 cases; fallback-control 100% on
both over 3 cases. The power curve printed from that draw treated the failed
calls as incorrect draws and is void. See `reports/2026-09-16-fable-round02-pilot.md`.

The full pilot draw landed the same day (run `eval-20260916-063539`, k = 16,
all 64 cases, 1,024 draws, 1,022 replayed through engine fingerprint
`2e079e786a655ab6`, `prompt_sha d23e9420b5812443`, provider novita, decoding
per §5), after two resumes through router outages. `nt eval2-rows`, macro over
the 49 families:

| Population | Cases | Correct | Correct-and-native |
|---|---|---|---|
| all | 64 | 87.9% | 68.7% |
| coverage | 51 | 92.5% | 75.6% |
| fallback-control | 13 | 75.0% | 49.5% |

Draw counts: 749 correct-and-native, 171 correct on CPython but refused by the
engine, 102 incorrect, 2 with no code. The fifteen-case glimpse above was the
easy head of the bank: on the whole training bank the base model leaves about
19 points of correct-but-fallback headroom on the native axis, which is the
axis training targets, so the §9 falsifier "base rate ≥ 90% correct-and-native"
is not met and round-02 has something to move. The power curve on these rows
is in §7.

