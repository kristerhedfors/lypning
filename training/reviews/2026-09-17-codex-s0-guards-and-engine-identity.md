# Codex review — the S0 guards, and the engine identity under rung S0b

Date: 2026-09-17. Decision: **continue — accept the guards and the corrections;
hold rung S0b until one guard and two runbook sentences are fixed; hold every
paid or GPU rung as before.** This is the independent review owed by ledger row
S0 (`ORCHESTRATION.md:166`) of
[`reports/2026-09-17-fable-s0-blocked-and-the-vacuous-read.md`](../reports/2026-09-17-fable-s0-blocked-and-the-vacuous-read.md),
merged as `dd47bae`. S0a and S0c are not held.

Everything below was re-derived in this worktree on 2026-09-17 at `dd47bae`: no
conformance battery, no `bench`, no network, nothing written in the repository.

## What the report establishes, and what it does not

The round did not run. No model was trained, loaded or called; **no power
figure, no lever vector, no probe table and no model-quality claim exists from
it**, the three questions of `ASSESSMENT.md` §6 step 1 are still open, and the
two named deliverables (`EVAL2.md` §7 and §11) are still owed. What it does
establish is a defect class in the $0 rungs: a command whose required input was
absent, empty or ungraded rendered a well-formed table and exited 0. `dd47bae`
is 10 paths, `+54/-4` in `training/pipeline/cli.py` and `+58/+43/+72` with zero
deletions across three test files, adding exactly seven `def test_` functions
(`git show dd47bae --numstat` and `… | grep -c '^+def test_'`, 2026-09-17).
Nothing under `training/runs/`, `training/data/` or `.github/` is touched, and
under `training/reports/` only its own new file.

## Verification

| # | claim | verdict |
|---|---|---|
| 1 | five vacuous-read paths are now fail-closed | **PARTIALLY CONFIRMED** — four unconditionally, the fifth conditionally |
| 2 | S0b's population moves with the engine binary | **CONFIRMED** in mechanism, **PARTIALLY CONFIRMED** in evidence |
| 3 | 8 of 9 gates enforced; the token floor is a plan/stage split | **PARTIALLY CONFIRMED** — the split is exact, its framing is not |
| 4 | seven tests pin all five paths; two documents corrected | **PARTIALLY CONFIRMED** — the token-floor test pins a location, not a refusal |
| 5 | no GPU run, no provider call, no spend, no frozen artifact touched | **CONFIRMED** in-repo; **NOT VERIFIABLE HERE** for the rest |

Claims 1 and 3 changed verdict between my passes, and the disagreement is the
finding. On claim 1 the first pass filed the fifth guard's narrowness as scope;
the adversarial pass showed it misses even the ERROR route it was written for. I
reproduced that myself on a synthetic one-attempt run under `NTX_ROOT`
(`print(1)`, no corpus replayed), 2026-09-17, all `levers … --vector --limit 0`:

| command | result |
|---|---|
| `--run r1 --status correct-native --engine <unexecutable>` | `ERROR 1` on stderr, **exit 0**, full empty vector |
| `--run r1 --status correct-fallback --engine <unexecutable>` | `no draw graded — 1 considered, 1 ERROR`, exit 1 |
| `--run r1 --status correct-fallback --engine /bin/echo` | `MISMATCH 1` on stderr, **exit 0**, empty vector |
| `--run rempty` (present, 0-byte `attempts.jsonl`) | `0 draw(s) match status correct-fallback`, **exit 0**, vector |
| `--rows <0-byte> --replay <0-byte>` | `0 draw(s) match status (any)`, **exit 0**, vector |

The four guards that do fire were re-run the same day and do: `probe-vector` on
an absent input → exit 2 `not a file: …`, on a present-but-empty probe → exit 1;
`levers --engine <absent>` and `levers --rows <absent>` → exit 2, stdout empty.
Happy paths are unchanged. So: four unconditional, one conditional with four
reachable openings — including an engine that executes but is not `lypning`,
which neither guard sees because it grades MISMATCH, not ERROR.

On claim 3 the first pass returned CONFIRMED on a sentence whose own
verification clause is false. The split itself is exact: `preflight`
(`gpu/train_verified.py:99-175`) holds k, case count, seed and family cycle, and
`main` calls it before branching on `args.plan` (`:365-380`), while the floor is
at `:249` inside `run()`. The other five audited items sit in another program or
fire mid-run and cannot be driven through `preflight`; and the reason given for
having no plan-time check does not hold (D3). Claim 2's mechanism is confirmed
(D2); its 14-vs-26 evidence is the no-engine degenerate case. Claim 5 is settled
in-repo by the diff and is unsettleable otherwise: a tree cannot record a call
that was not made.

## D1 — the five guards and the two document corrections

**ACCEPT-WITH-CONDITIONS.** The three absent-input guards (`cli.py:1845-1847`,
`:1907-1911`, `:1991-1994`) copy the CLI's existing idiom for a path taken from
argv exactly — `cmd_eval2_legacy` (`cli.py:359`) and `cmd_eval2_leaks`
(`cli.py:1736`) print the same `not a file: %s` and return 2. `probe-vector`'s
present-but-empty probe at exit 1 (`:2003-2006`) is accepted; it overloads an
exit code that already meant "a probe case has no base comparison" (`:2014`),
but the two stay distinguishable because the new path leaves stdout empty and a
test pins that. `levers`' "no draw graded" guard (`:1893-1897`) is accepted as
far as it goes and is not the five-of-five the ledger row claims.

Fable was entitled to make these changes. `ORCHESTRATION.md:22-23` reserves
experiment decisions and independent assessment to Codex; `:29-31` forbids
silently changing the task, oracle, split, runtime or budget to finish a round.
Each change only converts an output into a refusal, none completed the round,
and no frozen artifact was touched. Nothing automated consumes either command —
no `.sh`, no `Makefile` target, nothing under `.github/` invokes them — which is
why this can be accepted after the fact. The boundary for next time is that
line: a change that can only turn an output into a refusal is inside Fable's
lane; a change to what a rung *reports* — a population, a filter, a statistic —
comes to Codex first. The 1-versus-2 split is right (root `CLAUDE.md` invariant
8), though `cmd_levers` answers an absent `--run` (`:1849-1851`) and an unbuilt
engine (`:1837-1839`) with 1 and an absent `--engine` path with 2, and nothing
says why.

Conditions:

- **C1 (blocks any S0b run).** Widen `cli.py:1893` to fire on "nothing was read
  or graded", independent of `errors` and of `considered`. Four routes are open
  today (table above); two of them are the forms the private device may hit.
  Pick one exit code for a present-but-empty typed path and apply it to both
  commands; `probe-vector` already chose 1.
- **C2 (ledger text, Codex writes it).** Not "five paths are now fail-closed".
  Four fail closed unconditionally (`:1845-1847`, `:1907-1911`, `:1991-1994`,
  `:2003-2006`); the fifth fires only when the failure took the ERROR route and
  the filtered population is non-empty.
- **C3 (next PR touching `cmd_levers`).** Put that exit-code rule in the
  docstring at `cli.py:1816-1824`.
- **C4/C5 (block the private-device session).** Two sentences the correction
  added are not executable: `START_NEXT_ROUND.md:44-45` tells the operator to
  record the `@ engine <fingerprint>` line `levers` prints, and `levers` prints
  none in any mode (`@ engine %s` is `cli.py:397`, `eval2_bank.py:533`,
  `legality.py:410`/`:610`); and `START_NEXT_ROUND.md:83-84` with
  `STATUS.md:385-386` prescribe checking `steps × batch_size`, which counts
  example exposures, against a floor counting assistant tokens
  (`gpu/verified_stages.py:51-54`). **Both are discharged by the replacement
  texts in D2 and D3, not separately — one PR, three files.**
- **C6 (same PR).** `STATUS.md:382` says "Three of those four are `preflight`
  gates" and then names four plus the floor: it is four of five. And
  `STATUS.md:319-320` still says `levers` "reads the eval-2 draw rows
  unchanged", which this PR's own finding falsifies for the assigned `--run`
  form and which `STATUS.md:365` contradicts 45 lines later.
- **C7 (non-blocking).** `tests/test_levers.py:667` pins the fifth guard by
  replaying `runs/stock-nothinking`; the same guard is reachable on a
  one-attempt synthetic run under `NTX_ROOT` (`cli.py:36`), as used throughout
  this review. Add the `correct-native` direction when re-pinning.
- **C8 (bookkeeping).** Do not rewrite the merged entry. `CHANGELOG.md:17` says
  "three zero-cost rungs", the commit subject says five, and neither is a count
  of rungs: two commands in two of the three S0 rungs, three CLI branches, five
  guards. Correct it in the next entry touching these paths.

## D2 — does the engine-identity finding change S0b's assignment

**ACCEPT-WITH-CONDITIONS: re-assign the rung, do not hold it, and do not gate it
on a rebuild.** The mechanism is real and I re-derived it rather than accepting
it: `levers --run` replays every program through the local binary
(`cli.py:1857`) and feeds that fresh census in as the `replay` argument of
`eval2_rows.rows` (`:1859`), where `native` is recomputed (`eval2_rows.py:59`)
and `status` with it (`:66-67`); the `--status` filter (`levers.py:628`) then
runs over recomputed statuses, not the frozen file's. The `--run` form
manufactures its population.

The ruling does not rest on the report's evidence for it. With an engine that
cannot execute, every verdict is ERROR, `native` is False everywhere and the
correct-but-fallback bucket degenerates to the whole set of passed attempts —
the case `dd47bae`'s own guards now refuse. What a real, capable engine does to
the 171 is unmeasured here.

The rung survives because S0b is a shape read — which kinds carry the mass
(`ASSESSMENT.md:351`), dividing budget between the engine lever and the model
lever — not a build order; `--rank` is already refused on draw rows
(`cli.py:1938-1940`). It forbids two things only: publishing the vector
unlabelled, and cross-identity arithmetic. `171` stands and stops being
re-derivable: it is a property of the frozen rows at fingerprint
`2e079e786a655ab6` (`EVAL2.md:382-394`) and remains the denominator for
round-02's arms and for S2 (`ASSESSMENT.md:354`). Attach the identity wherever
it is quoted bare — `ASSESSMENT.md:167`, `:175`, `:203`, `:248`, `:325`, `:351`,
`:354`; `STATUS.md:365` and `START_NEXT_ROUND.md:46-47` already carry it, and
`reports/2026-09-16-fable-round02-run.md:228` is frozen and must be caveated in
the live documents instead.

A rebuild at `2e079e786a655ab6` must **not** become a prerequisite:
`engines.identity` folds the sha of every binary in the chain — `lypning` and
`lypning-l` both (`engines.py:22`, `:124-131`) — plus the oracle python, and no
document here records the core `lypning` sha of that build. An unsatisfiable
gate on a $0 rung is how a $0 rung stops being run. Two follow-ups, neither part
of this ruling: `--replay` has no producer in the tree (the `--cache` census is
`indent=2` JSON, `jsonio.py:102`, and `read_jsonl` wants one object per line,
`:32-45`; the only `write_jsonl` sites are `cli.py:345`, `:362`, `:396`), so
ship a converter or a runbook step with it; and the real repair is upstream —
`cmd_eval2_rows` already holds the refusal kind (`refusals.py:211` produces
`blocker`, carried onto every census row at `:270-271`) and
`eval2_rows.row_for` keeps only `verdict` (`eval2_rows.py:82`). Carrying
`blocker`, `detail` and the grading fingerprint onto the row would make S0b a
file read like S0a and S0c.

Replace `START_NEXT_ROUND.md:28-31` and `:37-47` with:

```
**S0b reads the pilot's frozen rows; it must not re-derive them.** Prefer
`levers --rows training/runs/eval-20260916-063539/eval2_rows.jsonl --replay
<census>.jsonl --status correct-fallback --vector --limit 0`, where `<census>`
is the replay `nt eval2-rows` took on 2026-09-16 at fingerprint
`2e079e786a655ab6` (persisted only if that command was given `--cache`),
converted to one JSON object per line. That form filters the frozen file's own
`status`, so the population is the pilot's 171 and no binary re-derives it. If
the census did not survive, fall back to `levers --run …` with whatever
`lypning-l` the device can build, print the grading identity beside the vector
(`python3 -c 'from pipeline import engines; print(engines.identity()["fingerprint"])'`),
and report its re-derived correct-but-fallback count as its own number under
that fingerprint — never as 171, which is a count at `2e079e786a655ab6` only
(`EVAL2.md` §11). Do not try to rebuild at `2e079e786a655ab6`: the fingerprint
folds in the core `lypning` sha of that build, which no document records, so it
cannot be hit to spec and is not a prerequisite. Quote the vector only as a
shape — which kinds carry the mass — never as an absolute headroom and never
subtracted from a vector taken at another identity, and copy the UNRESOLVED line
verbatim if any draw has no replay row or no refusal on record.
```

## D3 — should the ≥50,000-supervised-token floor stay a stage gate

**ADD A CONSERVATIVE PREFLIGHT BOUND; the exact floor stays where it is.** Do
not move `gpu/train_verified.py:249-251`: the exact count needs `build_examples`
and therefore `AutoTokenizer.from_pretrained` (`:222`), the deps `--plan` exists
to avoid. But "pin the split and correct the documents" is insufficient, because
the stated reason for having no plan-time check is false and the remedial
sentence shipped is wrong in its units.

A sound one-sided bound is computable in `preflight` with no download.
`sft_batches` selects by family and index and never inspects the examples it
carries — verified by run, 2026-09-17: passing `cases` in place of `examples`
yields the identical schedule at steps=5, batch=4, seed=1111 over 3 families × 3
cases (20 identical picks). `preflight` already holds the bundle (`:126`), every
case is guaranteed a non-empty `reference` at load
(`pipeline/training_data.py:43-46`), the supervised segment is that reference in
a fenced block (`:241-242`, `gpu/lypning_lora.py:176`), and under byte-level BPE
tokens ≤ UTF-8 bytes. So the schedule-sum of assistant bytes bounds the planned
tokens above and can only refuse schedules `run()` would certainly refuse. On
the in-tree proxy `training/data/corpus.jsonl` (measured 2026-09-17: 517 rows,
147 carrying a `reference`; assistant-segment bytes min 53, median 216, mean
407.8, max 2,536), an 80-exposure schedule sums to about 32,627 B in expectation
and would refuse, while the naive count-times-maximum form gives 202,880 B and
would not — which is why the bound must be the schedule sum.

The bill for the missing check is not hypothetical: `sft --plan` is step 7a of
the pilot job (`hf/round02_pilot.sh:294`), so it runs **on the meter**, after
the installs, the engine and bank downloads and bundle preparation, and the real
SFT at `:302` is where the floor fires — the round ends inside a wall-clock
budget (`hf/launch.py:112-113`) with a base-dev arm and no adapter. The dollar
figure needs an authenticated Hub call (`launch.py:144`): not verifiable here.

Conditions: the floor stays at `:249-251`, unmoved; add the bound to `preflight`
for `stage == "sft" and not args.smoke`, raising `TrainingError` that names the
bound, the floor and the words *upper bound*; print `planned_exposures` and
`supervised_token_upper_bound` in the `--plan` JSON (`:369-378`); pin it with
two behavioural tests, one refused and one admitted — not a source grep, because
`tests/test_training.py:529-561` proves the floor's location by reading the file
as text and its assert at `:555` cannot fail. Whether the bound would have
caught round-02's schedule (`ASSESSMENT.md:49`, 2026-09-16: 20 steps, 6,251
supervised tokens) is **not verifiable here**: that bank is private.

Replace `START_NEXT_ROUND.md:80-84` with:

```
Of those, **only the ≥50,000-supervised-token floor is invisible to `--plan`**
(audited 2026-09-17). It is refused inside `run()` (`gpu/train_verified.py:249`),
after the tokenizer download but before the base weights are fetched (`:265`),
so a schedule that fails it is refused *on a metered job* — after the dependency
install, the bank download, bundle preparation and the unadapted base-dev arm
(`hf/round02_pilot.sh:294`, `:297`, `:302`) — and the round ends there with no
adapter. The floor counts **assistant tokens the schedule exposes**, which
`--plan` cannot compute without the tokenizer. It is **not** `steps ×
batch_size`: that product counts example exposures and is 1,000 at this
runbook's own `--steps 250 --batch-size 4` (`NEXT_ROUND.md:128`), so comparing
it to 50,000 refuses a schedule the floor would admit. Do not substitute it.
What `--plan` reports is `planned_exposures` and a
`supervised_token_upper_bound` from the scheduled cases' reference bytes, with
no download: a bound **below** 50,000 is a certain refusal later, while a bound
above it is only the absence of a certain failure, never a pass. Every other
gate — `k`, the case count, the seed, the family cycle — is refused at plan time.

(at `START_NEXT_ROUND.md:317`:) Run `train_verified.py ... --plan` before every
actual stage — for the gates the plan can see. The ≥50,000-supervised-token
floor is not one of them.
```

## D4 — the verifier capacity floor

**ADD A FLOOR — as a per-host density ceiling, not a worker minimum.** The
report's framing is backwards in both directions, verified by driving
`hf/launch.py:main` with `HF_TOKEN` unset (2026-09-17; a shape that clears the
capacity gate falls through to `HF_TOKEN is not set`):

| workers / per host / hosts | result |
|---|---|
| 16 / 4 / 4 (defaults) | `HF_TOKEN is not set` — admitted |
| 16 / 4 / 1 | `pool capacity must cover --score-workers` |
| **16 / 16 / 1** | `HF_TOKEN is not set` — **admitted** |
| 1 / 1 / 1 | `HF_TOKEN is not set` — admitted |
| 16 / 4 / 0 | `training, evaluation and pool limits must be positive` |

`1/1/1` is the least contended shape in the space, not the one that blocked
round-02; and the shape that did run round-02 — sixteen sandboxes on one
`cpu-basic` host — still passes, because `launch.py:129` is a product and a
product is blind to density. The hole is a missing ceiling. `native` is a
host-load-dependent endpoint, which is exactly why T4 keeps the abort
(`reviews/2026-09-17-round02-full-assessment.md:61-64`), so sandboxes-per-host
is an instrument parameter and two arms scored at different densities are not
comparable — the argument `training_contract.py:14-21` already makes for k.
Codex decided the value (four by four, `ORCHESTRATION.md:162`); today it is a
default, not a gate, and `START_NEXT_ROUND.md:74` promises a guard the code does
not implement.

No floor on `--score-workers`, `--pool-max-hosts` or total capacity: no eval-2
arm has ever completed, so a throughput threshold would be set against a forward
estimate — the error `reviews/2026-09-16-round02-run-and-the-blocked-arm.md:24-30`
corrected once already — low concurrency is the safe direction for a
load-dependent endpoint, and a slow launch is priced in the plan printed before
`--yes`. Conditions: the predicate lands in `hf/launch.py:main` beside the
product check, conditioned on `args.stage in BANKED` (`:33`) and nothing else —
that is exactly the set whose env carries the pool knobs (`:51-58`), so a smoke
is unaffected by construction, and k is redundant because `preflight` pins k=16
for every banked arm (`train_verified.py:147-151`). The constant names its
flavor (four **at** `cpu-basic`, `hf_sandbox_runner.py:57`); pin it behaviourally
beside `tests/test_hf_launch.py:128-130`; mirror or document the runner bypass,
since `NTX_POOL_SANDBOXES_PER_HOST` is read from the environment with only a
positivity check (`hf_sandbox_runner.py:134-140`), and never make an absent knob
an error; keep pool shape out of the bundle, whose `execution` dict is hashed
into the digest; re-word `START_NEXT_ROUND.md:72-75` to state what is enforced.
This buys comparability, not a repair — round-02's block is still unseparated.

```
**D4 — verifier pool capacity. Ruled 2026-09-17: ADD, as a density ceiling, not
a worker floor.** A banked launch is refused above four sandboxes per host,
beside the existing product check in `hf/launch.py:main`. Together the two force
sixteen scorers into four hosts or wider — the shape T4 decided; separately,
neither does, and `16/16/1` is admitted today (driven 2026-09-17). `1/1/1`,
`16/2/8`, `16/1/16` and every smoke stay legal. `native` is host-load-dependent,
so per-host density is part of the instrument; the ceiling is four *at
`cpu-basic`*, and changing the pool flavor voids the number.
```

## Residual risks and defects this PR did not fix

Ranked; 1–4 came from verification and are not in the report.

1. `training/pipeline/cli.py:1893` — four reachable read-of-nothing routes, all
   exit 0 with the full vector (runs above). C1.
2. `training/pipeline/cli.py:380` — `nt eval2-rows` takes `--engine` through the
   same idiom with **no** `is_file` check, and it manufactures the rows S0a, S0b
   and `power --eval2` consume. The rows it writes (`:396`) carry no stamp; the
   `@ engine` line (`:397`) goes to stdout only and reports
   `engines.identity()`, i.e. host state, not the `--engine` argument. Defect 2
   of the report, unfixed, one command upstream — and Codex-owned, because it
   feeds every S0 rung.
3. `training/pipeline/cli.py:1510-1518` — `power --eval2 --rows` accepts any
   path that `is_file()`, bound to no run id, digest or fingerprint, and
   `stats.pilot_from_rows` (`stats.py:570-596`) keeps six fields. The report
   built a synthetic rows file and refused to publish from it; the tool would
   not have stopped it. **A future call, not ruled here.**
4. `training/pipeline/eval2_rows.py:63-69` — `native` is tested before
   `correct`, so a draw the run recorded as failing but the engine ran correctly
   is labelled `correct-native`. `EVAL2.md:394`'s partition is that same status
   partition, so its first bucket can hold draws CPython did not pass, by an
   engine-dependent amount. Not quantified here. **A future call.**
5. `cli.py:1698` (`leaks --sft`), `:1793`/`:1799` (`refusals`), `:2019` (`show`)
   — the same absent-input shape, unguarded; the first is the contamination gate
   whose docstring says it "must stop a training run".
6. `gpu/train_verified.py:249` — the floor's refusal branch has no behavioural
   test; and `gpu/verified_stages.py:100` lets early stopping end SFT at the
   floor, so the realised dose is never re-checked.
7. `STATUS.md:319-320`, `:382`, `:385-386`; `START_NEXT_ROUND.md:44-45`,
   `:83-84`, `:317` — the six live document lines C4–C6 cover.
8. `tests/corpus/sightings/7b355e85-…jsonl:18` carries a literal
   `[REDACTED-token-5-chars]` marker that is a scrubber false positive over the
   phrase "token floor" (`src/lypning/harvest.py:371-377`). No secret existed —
   the over-match is the bias invariant 5 prescribes — but the published program
   text no longer matches what was run, and nothing says so.

## The evidence boundary

Unavailable here and named rather than substituted: run `eval-20260916-063539`
and its attempts, rows and replay census; the round-02 probe rollouts under
`work/`; the private train and eval banks; the verifier Space, its build history
and any Hub or billing record. Checked literally on 2026-09-17: `ls
training/runs` → 14 directories, none of them that run; no `work/` directory;
`find . -name 'probe-rollouts*'` outside `.git` → nothing. **This device holds
none of the round's inputs either**, which is the same reason the round is
blocked — on a third clone.

That bounds four things. How far a capable engine moves the 171 is unmeasured.
Whether the plan-time bound would have caught round-02 is uncomputable without
the private bank. Whether the 2026-09-16 replay census survives — persisted only
if `nt eval2-rows` was given `--cache` (`cli.py:393`) — decides whether S0b
costs one format conversion or a re-derived count. And "no provider was called,
no Space was touched, nothing was spent" is not the kind of claim a tree can
settle: the diff shows no artifact landed, and that is all it shows.

## The bounded next action

**Decision: continue.** Next action: one $0 PR — C1, C2, C6 and the two
replacement texts (D2, D3) — plus, separately and not blocking S0b, the D4
ceiling with its test. **Owner:** Codex for C2 and the two replacement texts,
which are rulings; Fable may implement C1, C6 and D4 under them. **Inputs:**
this tree only; no private artifact, no credential, no provider. **Cost:** $0,
no GPU, no launch. **Stop criteria:** a missing artifact stops its rung and is
never converted into a score; an UNRESOLVED or ERROR line stops the vector; a
repeated native timeout, identity drift or MISMATCH stops the arm.

**The S0a–S0c private-device read remains owed and unrun**, unchanged in shape.
The engine-identity finding adds one prerequisite nobody had written down: **S0b
must name the identity that graded the vector it publishes and must not call any
re-derived count 171.** Since `levers` cannot print that identity today, the
operator satisfies it by printing `engines.identity()["fingerprint"]` beside the
vector, per D2. No paid rung, GPU job, Space rebuild or dataset mutation is
authorised by this review.

## On this review's independence

This session did not author what it rules on: the guards, the tests and the
document corrections arrived already merged at `dd47bae`, written by another
session on another clone. Every verdict rests on this tree and on commands run
here on 2026-09-17, never on the report as evidence for itself. That is the
condition ledger row R3 records as missing last time; the report was right to
decline to rule on its own §5, and to say why.

## What landed with this review

The bounded next action above was taken in the same PR rather than deferred, so
this document is a ruling and a record of its execution. C1 is closed: the
`levers` vector is publishable only if at least one record backs it, exit 1 with
stdout empty in every render mode including `--json`, with `loaded`,
`considered` and `unmatched` on the refusal line — the first wording carried
only `loaded` and `refusals`, which is pre-filter and post-filter respectively
and could assert that named rows carried no refusal when the `--status` filter
had removed them. The narrow `ERROR` guard stays in front of it, because naming
the failed replay is the more useful message. C2, C6 and the D2 and D3
replacement texts are in `ORCHESTRATION.md`, `STATUS.md` and
`START_NEXT_ROUND.md`. D3's preflight bound and D4's density ceiling landed with
behavioural tests, each verified non-vacuous by mutation rather than by
assertion-reading.

Three corrections to this review's own prescriptions, found by implementing
them. **The D3 replacement text was wrong about what the operator reads.** It
said a bound below 50,000 is "a certain refusal later"; because the bound is
enforced in `preflight`, a sub-floor bound is never printed — the plan refuses
there and then. The live text says that instead, and keeps the half that
matters: a plan that passes is not a pass on the floor. **The product check's
advice was dead on half its knobs.** Raising `--pool-sandboxes-per-host` to
cover sixteen workers on one host now lands on D4's ceiling, so an operator at
`16/8/1` would have followed the sentence to `16/16/1` and been refused again by
a different message; a banked stage is now told to spread hosts only. **And the
plan's "not applicable" was unpinned.** Mutating its `None` fallback to zeros
passed every test, which would print `supervised_token_upper_bound: 0` on every
smoke and GRPO plan — the worst possible schedule, for a stage the floor does
not price. Now pinned.

Residual item 3 (`power --eval2 --rows` bound to no run), item 4 (`native`
tested before `correct`), item 5 (the unguarded `leaks --sft` / `refusals` /
`show` family) and item 6's early-stopping half are **not** closed, and the
local `entry` route of `levers` still prints a table at exit 0 over an absent or
empty `--source` — deliberately, because widening that guard changes what
`--rank` and `--against` publish over the repository capture, which is a report
shape and therefore a separate call. C3 is done; C7's `correct-native` direction
is pinned; C8 is discharged in the changelog entry rather than by rewriting the
merged one.

## Reconciliation with the second independent review

This round was reviewed twice, by two sessions that did not see each other's
work: this document and
[`2026-09-17-fable-s0-independent-assessment.md`](2026-09-17-fable-s0-independent-assessment.md).
Both are kept, because the agreement is evidence and the disagreements are where
the ruling actually had to be made. They agreed, independently, on everything
load-bearing: the round is blocked and answers no rung; the guards are accepted;
the `@ engine` line the handoff told the operator to record does not exist;
`levers --run` redefines the population it reports; `steps × batch_size` counts
exposures and is withdrawn; and the pool's defect is a per-host density hole
that `16/16/1` walked through, not a missing global floor. Two sessions reaching
the same reading of `16/16/1` from different starting points is the strongest
evidence in either document.

Where they differ, the combined ruling takes the better half, and two of those
go against this document.

**S0b: the other review's mechanism wins.** This document's D2 concluded that
the pilot's engine could not be bound, because the composite fingerprint folds
in a core `lypning` sha no document records — true of the *fingerprint*, and the
wrong conclusion, because `EVAL2.md` §11 records the `lypning-l` **binary's own
SHA-256**. `--require-engine-sha256` binds exactly that, `--population-rows`
freezes status and family to the materialized rows so the replay supplies only
refusal kinds, and `--expect-draws 171` refuses a wrong or partial population
before a vector prints. That is strictly stronger than the `--rows`/`--replay`
fallback prescribed here, and it does not depend on a `--cache` census that may
not have survived. D2's operative instruction is superseded; its reasoning about
the fingerprint stands and is why the *binary* sha is the right pin.

**Capacity: a host ceiling as well, and this document's D4 was wrong to call
`16/2/8` and `16/1/16` legal.** They are the least contended shapes in the
space, which is why D4 admitted them; the other review caps the host count at
four on **cost**, an argument D4 never made, and cost binds independently of
contention. Both ceilings are conditioned on a banked stage, because only a
banked stage carries these knobs into the job at all. Line 317 above is
therefore superseded: `1/1/1`, `16/4/4`, `8/4/2` and every smoke stay legal;
`16/2/8` and `16/1/16` are now refused by the cost ceiling.

**The token floor: this document's ruling wins.** The other review kept the
floor purely stage-time on the grounds that moving it would cost `--plan` its
no-download contract. That argument is sound for the *exact* count and does not
reach a one-sided byte bound, which needs no tokenizer. Both survive: the exact
count stays in the stage and is recorded as `planned_supervised_tokens`, and
`--plan` additionally refuses a schedule already certainly below the floor.

**The four open vacuous-read routes: only this document found them**, and the
other review accepted "five guards" as complete. Both sessions then wrote a
guard for the `--run` path; the merged code keeps both, and the other review's
is the more informative — it refuses any *incomplete* replay or join, reporting
MISMATCH, ERROR, unmatched and without-a-refusal counts, rather than only the
case where nothing at all was graded. This document's guard remains as the
backstop that covers what neither `--run` guard reaches: the `--rows` route over
two files that exist, parse and join to nothing. Both are pinned by tests.
