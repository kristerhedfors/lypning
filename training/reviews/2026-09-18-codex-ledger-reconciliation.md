# Codex review — reconciling the ledger with the banks that exist and the base arm that ran

Date: 2026-09-18. Decision: **apply the patch below to `training/STATUS.md` and
`training/ORCHESTRATION.md`.** This is a reconciliation, not an assessment: the
programme acquired a 1,120-case training split, a 597-case benchmark and a
complete unadapted base arm on 2026-09-18, and no programme document records any
of them. Three sessions are editing this tree concurrently, so nothing here is
applied — every item gives file, line, current text, proposed text and one
sentence of why, and the maintainer applies it.

Everything below was re-derived in this worktree at `6f53409` on 2026-09-18, and
then put through an adversarial review whose corrections were applied at
`a2958b6` the same day. No training, no GPU job, no provider call, no metered
call, no gate touched, no frozen artifact touched. The only network reads are
`gh` and unauthenticated GitHub API reads of already-completed Actions runs.

**Six claims in the first draft were wrong and are corrected in place, not
quietly.** In order of how much they would have cost: the S0c probe was called
absent when it is present in the *other* private repository (§1, item 7); the
verifier Space revision `6d287057` was called nowhere in this tree when it is in
two reports (§6); the pinned engine was called a non-blocker on the strength of
three files sharing a *name* (item 7, S0b); item 1 described the bank's review
metadata as the opposite of what it says (item 1); item 6 attributed the primary
endpoint to the wrong document and quoted a k = 4 point estimate as an exact
ceiling (item 6); and §3 amended a ledger row without amending the four places
that point at it (§3). A seventh was found while checking those: the GH run
number this review cites for its private reads does not exist (§6). Where a
correction made a number unsupported, the number is marked owed rather than
deleted.

**A later recheck on 2026-09-18 found three more, and two of them were in the
corrections themselves.** Item 6's replacement text called `correct_native` "a
case-weighted aggregate, not the family macro the endpoint is defined on" and
built a two-number "+3.1pp aggregate / +3.4pp macro" split on that premise;
`metrics.json`'s own arithmetic makes the number the **family macro**, so the
premise is false and the split was invented — item 6 is rewritten on the true
basis and shows the arithmetic. §6's hit count for `6d287057` was wrong in both
figures; the grep is re-run and reported as it actually returns. And the
owed-re-run marking reached only §0(b) and item 5, leaving §1, item 4 and item
7's S4 clause quoting the same un-re-findable read unqualified; §6 now scopes it
to every inside-a-file number, and names the ones the surviving run does
support.

## 0. What I ran

Two commands, both free, both reproducible.

**(a) The split the gates actually use**, over the committed bank. This is
`training.prepare`'s own order — `validate_cases`, then `split_cases(seed=1111)`,
then `validate_pilot` (`pipeline/training.py:167-177`) — with the engine-backed
reference scoring left out, because the split does not depend on it:

```
PYTHONPATH=src:training python3 -c "
import collections
from pipeline.jsonio import read_jsonl
from pipeline.training_data import validate_cases, split_cases, validate_pilot
cases = read_jsonl('training/data/bank_v2/train.jsonl')
validate_cases(cases); rows = split_cases(cases, 1111); validate_pilot(rows)
for s in ('train','dev','test'):
    sub=[c for c in rows if c['split']==s]
    print(s, len(sub), len({c['family'] for c in sub}), dict(collections.Counter(c['population'] for c in sub)))
"
train 1120 33 {'coverage': 1015, 'fallback-control': 105}
dev 256 9 {'coverage': 215, 'fallback-control': 41}
test 313 9 {'coverage': 243, 'fallback-control': 70}
```

The same form with `eval2.jsonl` and `validate_benchmark`: 597 rows, 18
families, `{'coverage': 492, 'fallback-control': 105}`, 18 split groups.
`split_group` count on the train side: **33 train, 51 over the whole bank**.

**1,689 rows is not 1,689 admitted TRAIN cases and never was.** `split_cases`
holds out dev and test by connected source/family/solution component
(`training_data.py:97-139`), so the number a training stage sees is 1,120. The
same command run against `eval2.jsonl` shows why the benchmark's 597 is the whole
bank: `validate_benchmark` evaluates it whole and does not use the split.

**(b) The private artifact repo, read from CI**, because `HF_TOKEN` exists only
as a GitHub Actions secret. GH run
[35372015658](https://github.com/kristerhedfors/lypning/actions/runs/35372015658),
2026-09-18, workflow `s0-inventory.yml` on branch `s0/evidence-read` at
`27d6f0d`: `headforce/lypning-round02-artifacts`, **86 file(s)**. That count,
every path, and the `S0 inputs` block quoted in §1 are in that log, re-read
`gh run view 35372015658 --log`, 2026-09-18. The draft of this review cited run
`35372153392`, which does not exist — see §6. Three line counts matter here, and
they are **not** in that log: they need the small-file reader added at
`6f53409`, which has not run in CI.

```
   round-02/6aacd5cfb1dc2b62dc590b82/pilot/train-prompts.jsonl              1120
   round-02/6aacd5cfb1dc2b62dc590b82/pilot/dev-prompts.jsonl                 256
   round-02/6aacd5cfb1dc2b62dc590b82/pilot/test-prompts.jsonl                313
```

If those counts hold, the GPU job's own bundle reproduces the split I computed
locally digit for digit, and that is the check that would make (a) more than an
arithmetic exercise. **Until a run of `6f53409` prints them they are
unverified** (§6). (a) stands on its own regardless: it is local and
re-runnable.

## 1. What is now established, and what is not

**Established.** Job `6aacd5cfb1dc2b62dc590b82` completed an unadapted base-dev
arm on `Qwen/Qwen3.8-27B` at revision `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`
and then died at stage `sft` (`job-manifest.json`: `last_stage sft`,
`exit_code 1`, `status failed`; the platform reported the job as timed out). It
left `base-dev/metrics.json`, `base-dev/evaluations.jsonl` (1,024 rows) and
`base-dev/experiment.json`, and **no `sft/` directory at all** — no `loss.jsonl`,
no adapter, no seal. So the round produced a control and no adapter.

**Every field in that paragraph is an inside-a-file read and carries §6's owed
re-run.** `job-manifest.json`, the Qwen revision and the 1,024-row count of
`evaluations.jsonl` were read in a CI run that is **not re-findable**, exactly as
§0(b)'s line counts were; push `s0/evidence-read` past `6f53409` before quoting
any of them onward. The one exception is the absence of `sft/`, which is visible
in the file listing the surviving run did print (GH run 35372015658).

**Not established, and the brief I was given overstates it.** The measured arm is
`--bundle work/round-02/pilot/bundle.json --eval-split dev`
(`base-dev/experiment.json` — another inside-a-file read, owed the same re-run,
§6; it supports only the conservative reading below, which is why it is safe to
act on and not to quote): it is the **training bank's dev split**, not eval-2.
The two banks share no coverage capability — a one-liner over both files gives
train `{argv-arith, fallback-control, int-reduce, set-ops, stdlib-module,
text}` and eval2 `{fallback-control, file, string}` — so this arm says nothing
directly about the eval-2 base rate. `STATUS.md` §5's "On eval-2 nothing is
known, including the base rate" **stands unchanged** and must not be softened by
this row. What the arm bounds is checkpoint selection, which happens on dev.

**What the S0 inventory printed — and read the paragraph under it before
quoting it.** The same CI run printed, under the names `START_NEXT_ROUND.md`
uses:

```
   probe rollouts (S0c)     ABSENT
   eval2 rows (S0a/S0b)     ABSENT
   attempts (preflight)     ABSENT
```

**That block reads one repository, and the probe is in the other.**
`.github/scripts/s0_inventory.py` at `27d6f0d` lists
`headforce/lypning-round02-artifacts` only, so `ABSENT` on the first line means
*not in `…-artifacts`*, not *nowhere*. The probe **is** present:
`headforce/lypning-round02-work` holds
`round-02/6aaa87465527934177ee9f34/probe/` with `probe-rollouts.jsonl`,
`metrics.json`, `probe.json` and `experiment.json` (confirmed 2026-09-18). The
same script at `4c2429c` now looks in `…-work` too, and no completed run of that
revision exists yet — `gh api` on
`repos/kristerhedfors/lypning/commits/4c2429c/check-runs` returns
`total_count 0` (2026-09-18) — so re-run it before quoting either line.

**The eval-2 rows are the input that is genuinely absent.** There is no
`round-02/6aaa87465527934177ee9f34/` directory in the **artifact** repo; its
three job directories are `6aaca538b1dc2b62dc59024d`,
`6aacca0ab1dc2b62dc590991` and `6aacd5cfb1dc2b62dc590b82`. Locally,
`training/runs/eval-20260916-063539` does not exist (`test -d`, 2026-09-18). And
`eval2_rows.jsonl` is not among the files reported from `…-work` on 2026-09-18
either. So the S0a/S0b input is absent from this device and from both
repositories as far as anything has looked.

## 2. The patch

### Item 1 — the admitted-cases row is off by a factor of seventeen

**File:** `training/STATUS.md` **line 116.**

Current:

```
| Admitted training cases on the task-first path | **64** (train v1, 49 families, 13 fallback-control), reviewed by authoring and solving agents, not by Codex | frozen 2026-09-16, `EVAL2.md` §11; the first eval-2 bank of 300 cases froze the same day |
```

Proposed — two rows, because train v1 is history and bank v2 is what a stage now
sees:

```
| Admitted training cases on the task-first path | **1,120** over 33 independent split components (1,015 coverage, 105 fallback-control), from a 1,689-row bank whose dev and test halves are held out; every row's `review.reviewer` reads "Codex orchestrator session, 2026-09-18 (an agent, not a human reviewer)" — **an agent reviewer is not a human reviewer**, and the field is the bank's own claim about itself | `data/bank_v2/train.jsonl` at `split_cases(seed=1111)`, re-run locally 2026-09-18 (§0a); the GPU job's own bundle is reported to print the same 1,120 / 256 / 313, but that read is **not re-findable in any completed CI run** (§6) and is owed a re-run before it is quoted |
| Admitted training cases, train v1 (superseded) | 64 (49 families, 13 fallback-control) | frozen 2026-09-16, `EVAL2.md` §11; superseded as the training bank by bank v2 above, kept because round-02's 2026-09-16 pilot was run on it |
```

**Why:** 64 was the count of a bank that no longer feeds any stage, and the
number that replaces it is not the file's line count — the split holds out 569
of 1,689 rows, and quoting 1,689 would overstate the admitted supply by 51%.

**On the review clause, because the draft of this row had it backwards.** It is
not true that an authoring agent reviewed the bank and Codex did not: the
`review` block is identical on all 1,689 train rows and all 597 eval-2 rows, and
it names a Codex session. What it claims, field by field, is `origin
"authored"`; `reviewer "Codex orchestrator session, 2026-09-18 (an agent, not a
human reviewer)"`; `oracle_basis` a *differential* check — an independent spec
function and the reference must produce byte-identical stdout on every test,
**checked by execution**; `intent_basis` the family's English task statement,
written before either implementation; `independence_basis` one `source_group`
per family; `rights_basis` authored here, nothing captured or licensed; and
`evidence_ids` **empty** on every row. So the honest reading is: a self-declared
agent review with an executed differential oracle behind it and no external
evidence attached — which is stronger than "an authoring agent" and weaker than
a human review, and the row must not be allowed to read as either. Counted
2026-09-18 with:

```
PYTHONPATH=src:training python3 -c "
import collections
from pipeline.jsonio import read_jsonl
for f in ('train','eval2'):
    rows = read_jsonl('training/data/bank_v2/%s.jsonl' % f)
    print(f, len(rows), collections.Counter((r.get('review') or {}).get('reviewer') for r in rows))
"
train 1689 Counter({'Codex orchestrator session, 2026-09-18 (an agent, not a human reviewer)': 1689})
eval2 597 Counter({'Codex orchestrator session, 2026-09-18 (an agent, not a human reviewer)': 597})
```

### Item 2 — the benchmark row the bank never got

**File:** `training/STATUS.md`, **insert after line 116** (before the
`Starter smoke curriculum` row).

Proposed:

```
| Benchmark bank, v2 | **597** cases, 18 families, 18 split components (492 coverage, 105 fallback-control) | `data/bank_v2/eval2.jsonl`, frozen 2026-09-18; capability-disjoint from the training bank by construction (`bank_v2/README.md`), so no coverage capability is shared with the row above |
```

**Why:** a 597-case benchmark exists, a GPU job prepared a bundle from it
(`eval2_bundle_digest 2ffc3a936c04f481…`, job `6aacd5cf…`), and the only place
it is written down is its own README — which is exactly the failure this patch
exists to close.

### Item 3 — the supply ratio is no longer backwards

**File:** `training/STATUS.md` **lines 23–24.**

Current:

```
**the supply ratio is backwards** (300 cases to the frozen benchmark, 64 to
training), and **the examples that would carry the signal have never been
```

Proposed:

```
**the supply ratio was backwards and no longer is** (bank v2, 2026-09-18: 1,120
admitted training cases to 597 benchmark cases, against train v1's 64 to 300),
and **the examples that would carry the signal have never been
```

**Why:** this is one of §0's four named blockers and it is closed; leaving it
standing would have the programme's front page name a blocker that the last two
PRs removed.

### Item 4 — the dosage blocker is still open, and now has a reason

**File:** `training/STATUS.md` **line 22.**

Current:

```
move a 27B prior** (round-02: 6,251 supervised tokens, 20 steps, one seed),
```

Proposed:

```
move a 27B prior** (round-02, 2026-09-16: 6,251 supervised tokens, 20 steps, one
seed; the 2026-09-18 attempt at 250 steps on the 1,120-case bank reached stage
`sft` and the job timed out, leaving a base arm and no adapter — job
`6aacd5cfb1dc2b62dc590b82`, from that job's `job-manifest.json`, read 2026-09-18
in a CI run that is **not re-findable**, so this clause is owed the same re-run
as the §2 item 5 row),
```

**Why:** the blocker is unchanged but its cause has moved from "never attempted"
to "attempted once and hit a wall-clock limit", and the next person needs the
second fact to size the next job rather than re-diagnose the first.

### Item 5 — the base arm belongs on the scoreboard

**File:** `training/STATUS.md`, **insert after line 110** (the last row of the
§2 scoreboard, `Lever split of this repository's own refused captures`).

Proposed, in the existing three-column format:

```
| Base rate on the **training bank's dev split**, unadapted 27B, task-first path | 256 cases / 1,024 draws / 9 families, truncation 0.0: overall correct 0.9301, correct-and-native 0.7534, mean completion 96.87 tokens. Coverage arm 215 cases / 860 draws / 7 families: correct 0.9686, correct-and-native **0.9686** — and `correct_native` in this file is a **family macro**, not a case- or draw-weighted aggregate (arithmetic in §5's bullet, item 6). Fallback-control arm 41 cases / 164 draws / 2 families: correct 0.7952, correct-and-native 0.0 **by design** — the right answer keeps the import. By capability, correct-and-native: set-ops 1.000, text 0.9964, argv-arith 0.9929, int-reduce 0.9545, stdlib-module 0.8857. **Not an eval-2 number**: this is the pilot bundle's dev split, and the two banks share no coverage capability | job `6aacd5cfb1dc2b62dc590b82`, 2026-09-18, `Qwen/Qwen3.8-27B` @ `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`, bundle digest `797cfe7e12589c…`, seed 1111, k = 4 (`--eval-draws 4`), 16 scorers, verifier Space `headforce/lypning-round02-verifier` @ `5fa4f3127f…`; read from `round-02/6aacd5cfb1dc2b62dc590b82/base-dev/metrics.json`, 2026-09-18 — **the CI run that printed it is not re-findable** (§6), so this row is owed one push of `s0/evidence-read` past `6f53409` before any of its numbers is quoted onward |
```

**Why:** it is the only model-quality number the programme has ever taken on the
task-first path, and the two clauses in bold are the two ways it will be
misquoted — as an eval-2 base rate, and as a fallback-control failure.

### Item 6 — what that number does, and does not, license

**File:** `training/STATUS.md`, **insert as a new bullet in §5**, immediately
after the bullet ending at line 196 ("Measuring the base rate is step 3, and it
is worth more than any training run.").

Proposed:

```
- **Checkpoint selection on this bank has very little room, and the room is an
  estimate at a smoke setting — not a ceiling.** The coverage arm of the dev
  split the SFT stage selects on scores 0.9686 correct-and-native at base (job
  `6aacd5cf…`, 2026-09-18), and that number **is the family macro over the seven
  coverage families** — precisely the statistic `EVAL2.md` §4 freezes as the
  primary endpoint, and not a case- or draw-weighted aggregate. The file's own
  arithmetic settles it exactly: the arms are `correct_native`
  0.9686456400742115 over 7 coverage families and 0.0 over 2 fallback-control
  families, and (7 × 0.9686456400742115 + 2 × 0.0) / 9 = 0.7533910533910534,
  which is the overall `correct_native` `metrics.json` reports, digit for digit.
  Neither alternative reproduces it: draw-weighted would be 838 / 1,024 =
  0.818359 and case-weighted (215 × 0.9686… + 41 × 0.0) / 256 = 0.813511. So the
  headroom below is measured on the endpoint's own statistic. Three caveats have
  to travel with the number or it will still be misused. It is at **k = 4**
  (1,024 draws over 256 cases, `--eval-draws 4`), which `EVAL2.md` §4:81-92 names
  in so many words as "a smoke setting" and not the confirmatory k = 16. It is
  the **dev split** — the right split for a design decision and the wrong one to
  quote as a result. And it is a **point estimate with its own sampling error**
  and no interval: nothing in the job reports one, and the bootstrap that would
  resamples families as whole clusters, of which there are seven. The room to 1.0
  is therefore about **+3.14pp** on the right statistic, *estimated at a smoke
  k*, and a k = 16 estimate on the same arm can land either side of it.
- **The endpoint it would have to clear is `EVAL2.md` §4's**, and only the
  threshold comes from elsewhere: the correct-and-native first-draft rate,
  **macro-averaged over families**, sampled pass@1 at **k = 16**, paired and
  cluster-bootstrapped by `split_group` through
  `pipeline.training_metrics.paired_comparison`, 2,000 resamples, percentile
  interval, and the rule is the 95% interval's **lower bound above +3pp**
  (`EVAL2.md` §4:81-92). The +3pp itself is `PREREGISTRATION.md` §7b:706-708,
  which states it in ΔSLR terms and never uses the string "correct-and-native"
  (`grep -c "correct-and-native" training/PREREGISTRATION.md` → `0`,
  2026-09-18). Cite §4 for the endpoint and §7b for the threshold; attributing
  the endpoint to `PREREGISTRATION.md` is the error this bullet replaces.
- **The consequence is a strong design signal and not arithmetic certainty,
  which is why it is worth writing down — and worth writing down that way.** A
  rule that needs a 95% lower bound above +3pp, against room of about 3.14pp on
  the very statistic the rule is defined on, needs both a near-perfect adapter
  and an interval narrower than the gap — and the clusters are seven coverage
  families, which will not give one. Neither half of that is exact: the room is a
  k = 4 dev estimate and the interval is unmeasured, so this is a strong reason
  not to spend on this population and never a proof that nothing could clear the
  bar, and it must never be written as "the arithmetic is exact". For scale, the
  measured noise floors are −0.22pp and +1.57pp on SLR (§2, 2026-09-14). So: do
  not select a checkpoint on correct-and-native on this bank. Select on
  correctness, as §7 item 4 already says, read
  correct-and-native as a gate, and **re-measure the base arm at k = 16 before
  quoting any headroom as a number at all**. The fallback-control arm is 0.0
  correct-and-native by design and is a retention counterweight, never headroom.
  What this does **not** say: eval-2's base rate is still unknown, because
  eval-2's coverage capabilities (`file`, `string`) do not appear in this bank.
```

**Why:** the room left on this bank is a property of the bank rather than of the
model, so it belongs beside the bank — and the number that measures it is the
endpoint's own family macro, so the headroom is a like-for-like comparison and
not a statistic swapped in for another. It is still an estimate at a smoke k on
the dev split, so it belongs there *with that caveat attached*, which is what
stops the next reader from quoting 0.9686 as an exact ceiling and sizing a round
against it.

### Item 7 — the S-ladder states

**File:** `training/STATUS.md`, **§10, lines 385–393.**

Line 385, current: `| rung | what it measures | cost | state, 2026-09-17 |` →
proposed: `| rung | what it measures | cost | state, 2026-09-18 |`.

**Line 387 (S0a)**, current tail:

```
exact command assigned to Fable in `START_NEXT_ROUND.md`; private read still owed — attempted 2026-09-17 on a clone without the rows, exit 1
```

Proposed:

```
**BLOCKED, input not located 2026-09-18**, not merely un-transferred: the artifact repo `headforce/lypning-round02-artifacts` holds 86 files over three round-02 job directories and none of them is `6aaa87465527934177ee9f34` (GH run 35372015658, 2026-09-18), and `eval2_rows.jsonl` was not among the files reported from `headforce/lypning-round02-work` either. Note what is **not** claimed: `START_NEXT_ROUND.md`:89-91's "immutable private artifact directory" **does** exist — `round-02/6aaa87465527934177ee9f34/` is in `…-work` and holds the S0c probe — it is the eval-2 *rows* that have not been found in it. The rung is unblocked by locating those rows in `…-work` or by re-running the 2026-09-16 pilot, in that order, because the first is free
```

**Line 388 (S0b)**, current tail, after "…and requires exactly 171 draws; a
partial replay prints no vector. The composite *fingerprint* of that build is not
reproducible and is deliberately not a prerequisite" — append:

```
. **BLOCKED on the same absent input, 2026-09-18**: `--population-rows` reads the same `eval2_rows.jsonl`, which neither repository has been shown to hold, so the 171-draw population is the blocker. **The pinned binary is a second open question, not a solved one.** The S0 inventory lists three `engine-home/bin/lypning-l` paths, but a filename is not a hash: those three belong to the three 2026-09-18 jobs, and `START_NEXT_ROUND.md`:60 requires `--require-engine-sha256 a23b30832e00640cec2090d8403a6beeaa2087083d0fbd9080210fd8d4fc1096`, the 2026-09-16 pilot's build. The only such copy reachable on this device — `work/round-02/engine-home/bin/lypning-l` in a sibling worktree — hashes `1fb9be9081580ecb5387a553736b5901a911e612d64cdf77662d212ea4daaebd` (`shasum -a 256`, 2026-09-18), which is **not** the pin. Hash each Hub copy before assuming any of them is
```

**Line 389 (S0c)**, current tail:

```
private artifact and comparison contract named in `START_NEXT_ROUND.md`; read still owed. Attempted 2026-09-17 without the artifacts: it printed a zeros table at exit 0, and now exits 2 naming the absent path, or 1 on a present-but-empty probe
```

Proposed — append:

```
. **BLOCKED on its BASE column only, 2026-09-18 — the probe is present.** `headforce/lypning-round02-work` holds `round-02/6aaa87465527934177ee9f34/probe/` with `probe-rollouts.jsonl`, `metrics.json`, `probe.json` and `experiment.json` (confirmed 2026-09-18); the `ABSENT` the S0 inventory printed was a read of `…-artifacts`, which is the other repository. So `--probe` has its input and only `--base` does not, for want of `eval2_rows.jsonl`. The next action is a transfer, not a re-run
```

**Why for S0a and S0b:** "read still owed" reads as a transfer that has not been
done yet, and the next session will spend another round trying to do it. "Input
established absent" is the different and more expensive fact, and it changes the
next action from *fetch* to *re-run or abandon the rung*.

**Why S0c is the opposite case, and why the distinction is the point.** Its
probe was never missing; the inventory was pointed at the wrong repository, and
a draft of this review turned that into "doubly blocked". Marking a rung
abandoned for want of a file that exists one repository along is the more
expensive error of the two, because nobody looks again. S0c needs a transfer,
and S0a/S0b need a re-run — writing both as "blocked" without the distinction is
what loses that.

**Line 393 (S4)**, current:

```
| S4 | the first SFT with a predicted effect: ≥1,000 cases, ≥50,000 supervised tokens, three seeds, eval-2 at k=16 | ~$60–90 | blocked on S0–S3; the first GPU spend |
```

Proposed:

```
| S4 | the first SFT with a predicted effect: ≥1,000 cases, ≥50,000 supervised tokens, three seeds, eval-2 at k=16 | ~$60–90 | blocked on S0–S3; the first GPU spend. **The case floor is met on supply since 2026-09-18** — bank v2 admits 1,120 — and the 2026-09-18 job shows the remaining risk is wall clock, not admission: it cleared `preflight` at 250 steps and timed out inside `sft` (that last clause is the same un-re-findable `job-manifest.json` read as §2 item 4 and 5, and is owed the same re-run). The ≥50,000-token floor is **UNKNOWN**, not failing: it is counted in `run()` against the real tokenizer, and the projection that 250 steps would fail it was refuted for using completion tokens as the denominator for fenced program text |
```

**Why:** the rung's own precondition changed, and the token floor is the one
number a reader is most likely to quote from a refuted projection.

### Item 8 — the §2 preamble no longer describes §2

**File:** `training/STATUS.md` **lines 92–96.**

Current first clause: `All eval numbers below are on the frozen 74-case rewrite
hold-out (manifest …)`.

Proposed: `The first six rows below are on the frozen 74-case rewrite hold-out
(manifest …). Rows below them carry their own population in the middle column
and must not be compared across populations.`

**Why:** rows 107-110 already left that hold-out and Item 5 adds a seventh
population; a preamble that claims one population for the whole table is how two
arms get subtracted from each other.

### Item 9 — the document date

**File:** `training/STATUS.md` **line 1.** `# Training programme status —
2026-09-17` → `# Training programme status — 2026-09-18`. **Why:** the date at
the head is what a reader trusts when deciding whether to re-derive.

## 3. Ledger row D1, and the conflation it is one step away from

**The row is stale in its first clause and sound in its second, and the two must
not be merged.**

**File:** `training/ORCHESTRATION.md` **line 156.**

Current:

```
| D1 / Codex | Review/repair queue implemented; no new training examples admitted | Independently review tasks/oracles, author references, freeze a new container-backed pilot, map reviewed TRAIN sources, grade and repair |
```

Proposed:

```
| D1 / Codex | Review/repair queue implemented **and no longer at zero throughput** (bank-v3 repaired rows 13 then 103, GH runs 35320962503 and 35340137976, read 2026-09-18); training examples **are** now admitted — bank v2 admits 1,120 on the split the gates use. **The S-ladder rung S2 is untouched by both**: its population is the 171 + 26 correct-but-fallback *pilot draws*, which the repair loop never saw | Re-run or abandon the 2026-09-16 pilot that holds the S2 population; independently review the bank-v3 tier before any arm trains on it; keep the two tiers in separate numbers |
```

**The distinction, stated once so it is not re-made by guessing.** Three things
differ between the bank-v3 repair loop and rung S2, and any one of them is
enough:

1. **The population.** S2 rewrites the **171 + 26 correct-but-fallback draws**
   of the 2026-09-16 pilot (`ASSESSMENT.md`:354, `STATUS.md`:391) — a model's own
   answers to bank cases, where the answer was *correct* and took the fallback.
   The repair queue's input is a **winning reference program for a
   model-proposed task**, routed there because the engine refused it and the task
   was generated in the `rewrite` stratum (`pipeline/synth.py`, ROUTING block).
   No pilot draw has ever entered that queue; the pilot's rows are not in the
   artifact repo at all.
2. **What is being rewritten.** S2 rewrites a *draw* — evidence about the
   deployment prior. The repair rules rewrite a *reference* — a pure source
   transform re-verified against an output agreed before the repair existed
   (`pipeline/repair_rules.py` docstring). One measures what the model does; the
   other manufactures what the model should be shown.
3. **What the number means.** S2's answer is *the rewritable fraction of the
   headroom*, and its stop rule is "under 10 of 64 cases gain a native reference
   → the model lever is capped" (`ASSESSMENT.md` §6 step 4). The repair loop's 13
   and 103 are *bank rows produced*, with no denominator in common. Reading 103
   repairs as evidence about S2 would substitute a supply number for a ceiling
   number, and the ceiling is the thing the programme is trying to find out.

### What must move with the row, all in one commit

The draft of this review said the two sentences that point at D1 "need no edit".
That is wrong, and wrong in the expensive direction: the moment D1 stops saying
the queue has no throughput, every sentence that cites D1 *for* zero throughput
becomes a pointer into text that no longer says what it is being quoted for.
Five locations, and the fifth is a name collision rather than a pointer. Apply
them together or not at all; the whole value of the row edit is that nothing is
left claiming the old reading.

1. **`training/ORCHESTRATION.md`:156 — the row itself.** The edit proposed
   above.
2. **`training/STATUS.md`:24-25 — §0's fourth named blocker**, currently "**the
   examples that would carry the signal have never been admitted** (ledger row
   D1, zero throughput)". This is the front-page pointer the review item is
   about. The blocker is still open, but its parenthesis is now false. Proposed:
   "**the examples that would carry the signal have never been admitted**
   (ledger row D1: the repair queue now produces rows — 13, then 103 — but not
   one of them is the signal-bearing kind, whose population is the pilot's
   correct-but-fallback *draws*)".
3. **`training/STATUS.md`:391 — the S2 rung**, currently "not started; ledger
   row D1". Proposed: "not started; its population is the 2026-09-16 pilot's
   171 + 26 correct-but-fallback draws, which the bank-v3 repair loop never
   saw — ledger row D1 now records both tiers and must not be read as S2
   progress".
4. **`training/ASSESSMENT.md`:243-245**, which quotes D1 as reading "no new
   training examples admitted" and adds "The signal-bearing arm of the loop has
   had zero throughput." That is a **dated** quotation — "reads, on 2026-09-16"
   — so the quotation stays; it needs one appended clause, "and still reads so
   for that arm on 2026-09-18, though the repair queue itself no longer has zero
   throughput", or it will be read as current.
5. **`training/ORCHESTRATION.md`:167 and :179 — do not touch either.** Line
   179's "After H2/D1 and training launch gates close" is a gate pointer, and
   D1 does **not** close here: the signal-bearing arm has still produced
   nothing. Line 167's bold **D1** inside the R5 row is a *different D1
   entirely* — it is decision 1 of the R5 ruling on Fable's guard changes, not
   the ledger row — and editing it because it matched a grep is the failure mode
   this list exists to prevent. `grep -n D1 training/*.md` returns all five
   (2026-09-18); only 2, 3 and 4 are the row's dependents.

And a mechanical note for whoever applies §2 and §3 together: every line number
in this review is against `6f53409`, and items 1, 2, 3 and 5 insert lines above
items 6, 7 and 9. Apply by anchor text, bottom of the file upward, or the later
line numbers will be wrong by the count of the earlier insertions.

## 4. Two rows the ledger is missing

Both are `training/ORCHESTRATION.md` and both are owned by whichever session is
editing that file; I propose the text and apply nothing.

**T7 — the retargeted generation (PR #93), which has no ledger row at all.**
T6 records the first scaled bank-v3 run and nothing records the fix:

```
| T7 / Codex | **Bank v3 retargeted 2026-09-18 (PR #93).** The first scaled run generated the one population `PREREGISTRATION.md` §2(g) struck out: 3,499 of 3,837 tasks were already served natively, because "solvable in under 25 lines of ordinary Python" is a description of the served subset. Generation now names a construct the engine refuses and asks for an ordinary task whose natural answer reaches for it, never naming the module. Measured on GH run 35340137976: refused fraction 5.2% → 42.1%, repaired rows 13 → 103, stratum draw 0.708 against the preregistered 0.710. `calendar` and `datetime` repair rules added; two repairs that `verify()` accepted and that were silently wrong are now refused, at a cost of 4 repairs | The tier is unchanged and still weaker than bank v2 — self-consistency, not independent derivation — and must never be merged into one number with it. No arm has trained on a v3 row |
```

**T2 needs a successor row for bank v2.** T2 records "eval-2 v1 (300 cases) and
train v1 (64 cases)"; nothing records that both were superseded on 2026-09-18 by
a 1,689-row authored bank splitting 1,120 / 256 / 313 with a 597-case benchmark
beside it, that a GPU job prepared bundles from both, and that the `review` block
on every case still says an agent and not a human reviewed it.

## 5. One thing outside `training/`

**File:** `CHANGELOG.md` **lines 152–154.** The #92 entry says preparation gave
"digest `7c8997e1e128c47f` and a train split of **1,355** over 40 families" and
"`eval2-leaks` exits 0 with 315 of 315 clean". The bank as committed — one
commit, `803e9f2`, touched `train.jsonl` and nothing since — splits **1,120 over
33 families**, and its own README records digest `10492e75cc8c52a6` and 597 of
597 clean. The entry describes a draft of the bank, not the bank that shipped in
the same PR. I am not proposing an edit to a dated changelog entry without the
maintainer's call on whether that file records the artifact or the moment; I am
recording that the two disagree and that the artifact is 1,120.

## 6. UNVERIFIED

- **`headforce/lypning-round02-work` — half of this is now VERIFIED, and the
  verified half refutes a claim this review made.** `EVAL2.md`:372-373 names it
  as the private home of both 2026-09-16 banks at commit
  `7b10488bbc7be5cd28ebc901a5ffd16e028c8986`. It **does** hold the S0c probe:
  `round-02/6aaa87465527934177ee9f34/probe/` with `probe-rollouts.jsonl`,
  `metrics.json`, `probe.json` and `experiment.json` (confirmed 2026-09-18). §1
  and item 7's S0c edit are corrected accordingly. What is still **UNVERIFIED**
  is whether the same repository holds `eval2_rows.jsonl` or `attempts.jsonl`
  for that job; it was not among the files reported on 2026-09-18, which is
  weaker than a search having been run for it. `.github/scripts/s0_inventory.py`
  at `4c2429c` adds the `…-work` lookup, and no run of that revision has
  completed — `gh api repos/kristerhedfors/lypning/commits/4c2429c/check-runs` →
  `total_count 0`, 2026-09-18 — so one push settles it, and it should be pushed
  before anyone re-runs the pilot.
- **The `lypning-l` served by verifier Space revision `6d287057`. UNVERIFIED —
  the reason the first draft gave was a false negative, and the count the
  correction replaced it with was itself wrong.** The first draft said it found
  no `6d287057` anywhere in this tree; the correction said `grep -rn 6d287057 .`
  returns "five hits in four files". Both figures are withdrawn: neither is what
  the grep returns. A plain `grep -rn 6d287057 .` counts this review's own
  mentions, so its answer moves with every edit to this file and is not a
  quotable number at all — which is how a wrong one got quoted twice. The
  re-runnable form, from the worktree root, excludes this file and holds still:

  ```
  $ grep -rn 6d287057 . | grep -v '^training/reviews/2026-09-18-codex-ledger-reconciliation\.md:'
  training/reports/2026-09-16-fable-round02-pilot.md:56
  training/reports/2026-09-16-fable-round02-run.md:71
  training/reports/2026-09-16-fable-round02-run.md:95
  tests/corpus/sightings/520bd75f-5243-58e2-9e64-37d102fd9ae1.jsonl:159
  tests/corpus/sightings/520bd75f-5243-58e2-9e64-37d102fd9ae1.jsonl:213
  tests/corpus/sightings/17942933-ccc9-56f8-b046-32643d3ba05d.jsonl:105
  tests/corpus/sightings/17942933-ccc9-56f8-b046-32643d3ba05d.jsonl:227
  ```

  **7 hits in 4 files** (2026-09-18, working tree at `40456b4`). Two of them are
  not sightings of the Space at all: the `17942933` pair are captures of a
  session **editing this review file**, so they echo this document rather than
  witness anything. The `520bd75f` pair are genuine — captured handshakes
  against `hf.co/spaces/headforce/lypning-round02-verifier` at that revision. So
  the independent evidence is **5 hits in 3 files**, and that is the figure to
  quote.

  On what those hits actually say: `6d287057e491e9edc22b677e2bf0dd5200f9b40a` is
  the verifier Space revision the **2026-09-16 pilot** ran against;
  `5fa4f3127f7a3d70b84d4e1c93923f18f0c43a41` is the revision the **2026-09-18 CI
  round** ran against. Two revisions of one Space, a rebuild apart, not a
  contradiction — and there was never any tension between a Space revision and a
  binary SHA, because they identify different things. What is genuinely unknown
  is the thing S0b needs: whether the `lypning-l` inside Space revision
  `6d287057` still hashes to the pin `a23b3083…`.
  `reports/2026-09-16-fable-round02-run.md`:68-71 records that binary as
  "downloaded from the verifier Space at the pinned commit", which is a record
  of a past download and not a re-read. Nothing in this tree can re-check it; a
  single file read of the Space at that revision can, and would also settle
  whether S0b's pinned input is recoverable at all.
- **GH run `35372153392`, cited throughout the draft of this review, does not
  exist.** `curl https://api.github.com/repos/kristerhedfors/lypning/actions/runs/35372153392`
  → HTTP `404`, and
  `gh api repos/kristerhedfors/lypning/actions/workflows/s0-inventory.yml/runs`
  → `total_count 1` (both 2026-09-18). The one `s0 inventory` run is
  **35372015658**, at `27d6f0d` on `s0/evidence-read`, and its log does print
  `86 file(s)`, every path, and the `S0 inputs … ABSENT` block — those claims
  are re-verified and re-cited above. Its log does **not** print the pilot
  prompt line counts (1,120 / 256 / 313) or anything out of
  `base-dev/metrics.json`: reading small files landed later, at `6f53409`, and
  `gh api …/commits/6f53409/check-runs` → `total_count 0`. So **everything in this
  review that comes from inside a file, rather than from the file listing that
  run did print, is quoted from a read this session could not re-find**, and
  invariant 3 says each of them must be re-run before it is relied on. A draft of
  this bullet scoped that to §0(b) and item 5 only, which left three more places
  standing unqualified. The full list, so none is missed again:

  1. §0(b)'s three pilot prompt line counts, 1,120 / 256 / 313.
  2. §1's "Established" paragraph — `job-manifest.json`'s `last_stage sft`,
     `exit_code 1` and `status failed`, the Qwen revision
     `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`, and the 1,024 rows of
     `evaluations.jsonl`.
  3. §1's "Not established" paragraph — the `--bundle … --eval-split dev` arm
     string out of `base-dev/experiment.json`.
  4. §2 item 4's "250 steps … reached stage `sft` and the job timed out".
  5. §2 item 5's row entire: every rate in it, the bundle digest, the seed, the
     k, the scorer count and the verifier Space revision — including the 0.9686
     the whole of item 6 turns on, and every figure in item 6's macro
     arithmetic.
  6. §2 item 7's S4 clause, "cleared `preflight` at 250 steps and timed out
     inside `sft`".
  7. This section's own note that `6aacca0ab1dc2b62dc590991` failed at stage
     `review` and holds a 1,971-case `reviewed-train/cases.jsonl`.

  What the surviving run **does** support, and what therefore needs no re-run:
  the 86-file count, every path in that listing — hence the three
  `engine-home/bin/lypning-l` paths in item 7's S0b, the three job directory
  names in §1, and the absence of an `sft/` directory — and the `S0 inputs …
  ABSENT` block. Pushing `s0/evidence-read` past `6f53409` re-prints the rest of
  it for nothing.
- **Why job `6aacd5cf…` timed out**, and whether the ≥50,000 supervised-token
  floor was reached, passed or never evaluated. **UNVERIFIED**: the job left no
  `sft/` directory, so nothing in the artifact repo distinguishes a floor refusal
  from a wall-clock death. The HF job log would say; the artifact repo does not.
- **Whether `6aacca0ab1dc2b62dc590991`'s 1,971-case `reviewed-train/cases.jsonl`
  is a third bank or an intermediate.** **UNVERIFIED** — it differs from both
  1,689 and 1,120, it failed at stage `review`, and no document mentions it.
