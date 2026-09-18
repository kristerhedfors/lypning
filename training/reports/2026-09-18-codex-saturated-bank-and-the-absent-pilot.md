# Round report — a saturated bank, and an absent pilot that may not be absent

## Outcome and decision requested

**Date** 2026-09-18. **Round** the $0 S0 evidence round, assigned command-by-command
in [`START_NEXT_ROUND.md`](../START_NEXT_ROUND.md) §"Next Fable session — 2026-09-17"
and listed as rungs S0a–S0c in [`STATUS.md`](../STATUS.md) §10. **Status: blocked,
for a different reason than the one on the assignment.**

**No GPU run occurred. No provider was called. No training job was submitted. No
Space was built or touched. No dataset was written. No frozen artifact was
altered. No gate, floor, seed list or k was edited. Nothing was billed.** Every
Hub number below is read from a GitHub Actions job on a public repository's
hosted runners — three `s0-inventory` runs on this branch and one `hf-status`
run on `main`, all 2026-09-18 — each of which installed `huggingface-hub`,
listed a private dataset repo and downloaded JSON manifests and one metrics
file. No approval was requested, because nothing was run that consumes one.
**Two of those runs also printed bank cases into a public log and have been
deleted; §6 is the record.**

The assignment says to run the rungs "on the device that already owns the
private round-02 artifacts". **There is no such device, and the private
artifacts are not on a device at all — they are on the Hub, and the token that
reads the Hub exists only as a GitHub Actions secret.** §2 records the device
audit. The rungs were therefore not attempted a third time on a clone that
cannot hold their inputs; what was done instead is the read that decides whether
they have inputs anywhere, which is free and which nobody had run.

Three things came back, and the third is why this report is longer than a
blocked notice.

1. **S0a and S0b's input is still missing, and S0c's is not.** The pilot's
   `eval2_rows.jsonl` and `attempts.jsonl` are absent from the artifact repo's
   complete 86-file listing. The probe rollouts S0c reads are **present**, in the
   second private repo a prior track asserted and nobody had checked: run
   35373009782 printed
   `round-02/6aaa87465527934177ee9f34/probe/probe-rollouts.jsonl`. That assertion
   is now measured rather than repeated, and half of one rung's blockage was
   never real (§5).
2. **A complete base eval arm exists that no document in this tree recorded when
   the inventory read it.** Job `6aacd5cfb1dc2b62dc590b82` failed at stage `sft`,
   but before it failed it finished `base-dev/` in full: 256 dev cases, 1,024
   draws, 9 families, truncation rate 0.0, on unadapted `Qwen/Qwen3.8-27B` (§4).
   A concurrent session has since read the same file and is proposing ledger
   text for it in
   [`reviews/2026-09-18-codex-ledger-reconciliation.md`](../reviews/2026-09-18-codex-ledger-reconciliation.md);
   §5 records where the two reads agree and where that one corrects this one.
3. **That arm is a strong design signal that the split the round selects
   checkpoints on cannot answer the question the round exists to ask.** On its
   coverage population the base model's correct-and-native rate is **0.9686**
   and the count of correct-but-fallback draws is **zero**, so the quantity
   round-02 moves has next to no mass there (§4, §5). **That 0.9686 is the
   equal-weight family macro over the 7 coverage families** — precisely the
   statistic `EVAL2.md` §4 freezes as the primary metric, not a draw-weighted or
   case-weighted aggregate; §4 (a) reproduces the printed overall from it to the
   last float digit. The inference therefore rests on the right statistic, which
   makes it stronger, not weaker. Three things still bound it, and none of them
   can be removed from inside this file: the arm was drawn at **k = 4**, which
   `EVAL2.md` §4 calls "a smoke setting" and not the confirmatory **k = 16**, so
   it is a **point estimate carrying its own sampling error**, not a ceiling; it
   is the training bank's **dev split**, the right split to design on and the
   wrong one to quote as a result; and it is a macro over **7 clusters**, a small
   count for a bootstrap that resamples families whole — and neither a bootstrap
   nor an interval was computed on this arm at all. It is a signal to design
   on, not an arithmetic proof that the rule cannot fire. **This is a finding
   about the benchmark, not about the model** — and it is about *this* split:
   the training bank's dev split, not eval-2, whose base rate is unmeasured (§5).

**Decisions requested of Codex** — three, all in §5:

1. Whether the saturation finding retires bank v2's coverage arm as the
   population a round selects on. `EVAL2.md` §9 already wrote the rule ("a base
   rate at or above 90% correct-and-native on eval-2 leaves no headroom worth
   training for") and `PREREGISTRATION.md` §2(g) already wrote the cause. The arm
   in §4 is the first measurement of this programme's own falsifier on a
   task-first bank — on the dev split, not on eval-2. Applying it is a ruling,
   not a report's call.
2. Whether `START_NEXT_ROUND.md` line 19, `STATUS.md` §10's S0 cells and the
   `round02-evidence` skill §7 are corrected for the second repo. All three say
   or imply that the S0 inputs are gone; one of them now demonstrably is not.
   **This session did not edit any of those documents** — other sessions hold
   them, and a shared document edited from a report is how two trees disagree.
3. Whether the S0c read proceeds from CI rather than waiting for a device. The
   mechanism is the same one that produced every number here.

**Decision requested of the operator: one, and it is not about spend.** No
ceiling is asked for, no rung is proposed for spend, and nothing in this report is
a recommendation to launch anything. But **this session published roughly 11 bank
cases into public Actions logs** before the cause was found and fixed, and
whether the affected eval-2 families are re-cut is the operator's call, not this
report's. §6 is the disclosure; it does not propose a remedy. What runs next is
`STATUS.md` §10 and the operator's call.

## Reproduction and authority

**Repository commit** `4c2429cbe4df8872af4ac90ff9db09502dfc8642`, branch
`s0/evidence-read`, in the worktree
`.claude/worktrees/bridge-cse_01CfQnY6ScJzwUh36sczro21`. **Report version** 2 —
version 1's central inference is corrected in §4 and §5, its citation of #93 in
§5, and §6 is new. **This session wrote this report, and three commits to
`.github/scripts/s0_inventory.py` (`27d6f0d`, `6f53409`, `4c2429c`) plus the
repair `a2958b6`** — version 1 said it had written only the report, which was
wrong and is what §6 is about. `git status --porcelain`
at the time of writing also shows capture-corpus sightings this session's own
shell produced, and edits by concurrent sessions sharing the worktree
(`training/pipeline/cli.py`, `training/pipeline/headroom.py`,
`training/tests/test_headroom.py`, `.github/scripts/token_floor.py`,
`training/reviews/2026-09-18-codex-ledger-reconciliation.md`, two skills). None
of them is this session's, and none is quoted here as this session's evidence.

**Authority:** none needed, none used. No approved device, time, cost or storage
ceiling was requested. All four CI jobs ran on GitHub-hosted runners for a
public repository (`gh repo view --json visibility` → `PUBLIC`, 2026-09-18), so
the minutes are free; the jobs downloaded JSON only and no weights. **Public is
also why §6 happened:** on a public repository an Actions log is a publication,
and this session treated it as a console.

**The device, audited here on 2026-09-18, and the reason §1 reads as it does:**

| check | command | result |
|---|---|---|
| the token locally | `env \| grep -ci hf_token` | `0` |
| the secret's home | `gh secret list` | `HF_TOKEN`, set 2026-09-18T02:08:19Z, value never printed |
| the Hub client | `python3 -c "import huggingface_hub"` | `ModuleNotFoundError` |
| the Hub cache | `ls ~/.cache/huggingface` | absent |
| the private work tree | `ls work` | absent |
| the pilot run | `ls training/runs/eval-20260916-063539` | absent |
| the probe rollouts | `find / -name probe-rollouts.jsonl -maxdepth 6` | only pytest fixtures under `/private/tmp/…/test_probe_gate_exact_identity0/` |

This is the third session to report the same audit, and the conclusion the three
of them support is not "run it on the other device" but **"the other device does
not exist"**. The artifacts live in a private Hugging Face dataset repo; the only
credential that opens it is an Actions secret; therefore the read happens in a
job. The mechanism is recorded in the `round02-evidence` skill and in
`.github/workflows/s0-inventory.yml` with `.github/scripts/s0_inventory.py`.

**Exact commands, with their run ids.** Every number below carries the run that
printed it and the date (invariant 3):

```bash
# The inventory: every path in the artifact repo, the S0 inputs by name, a line
# count per .jsonl, and each small JSON file whole. Triggered by pushing a
# branch matching the workflow's "s0/**" glob.
gh run view 35372015658 --log   # commit 27d6f0d, 2026-09-18 17:02 UTC; paths
                                # only, and the one of the three still standing
gh run view 35372153392 --log   # commit 6f53409, 2026-09-18 17:04 UTC — DELETED,
                                # now HTTP 404; printed bundle heads (see 6)
gh run view 35373009782 --log   # commit 4c2429c, 2026-09-18 17:13 UTC, adds
                                # the lookup of the asserted second repo —
                                # DELETED for the same reason, now HTTP 404
gh run view 35371692743 --log   # hf-status: HF job states, 2026-09-18 16:59 UTC
```

`gh run view --log` prefixes every line with job, step and an ISO timestamp
separated by tabs; the tables below are read from `cut -f3-` with the timestamp
stripped.

**Identities of the arm in §4**, as its own `experiment.json` and the job
manifest print them (run 35372153392, 2026-09-18):

- HF job `6aacd5cfb1dc2b62dc590b82`, repository commit
  `5580fe766a178444b0949972e735b9b0c12c3ebc`, flavor `gpu`, seed `1111`.
- Base model `Qwen/Qwen3.8-27B` at revision
  `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`. **`adapter: null`, `stage: eval`**
  — this arm is the unadapted model.
- Bank `banks/v2`; the arm's `bundle_digest`
  `797cfe7e12589c919b20873866615545d9aa436e2b19928ad6b7b5a6460c47d0` is
  identical to the manifest's `pilot_bundle_digest`, so the arm and the job
  agree on what was evaluated.
- Decoding as recorded: `greedy: false`, `max_new_tokens: 1024`, `max_seq: 4096`,
  `eval_draws: 4`, `eval_sequences: 128`, `score_workers: 16`. **k = 4**, which
  is a pilot-grade draw; `EVAL2.md` §4's k = 16 is for a confirmatory run and the
  manifest reserves it for the eval-2 stage that never ran.
- Verifier Space `headforce/lypning-round02-verifier` at
  `5fa4f3127f7a3d70b84d4e1c93923f18f0c43a41`.
- The engine is the `lypning-l` shipped in the job's own `engine-home/bin/`.
  **Its sha256 is not printed by the inventory and is not quoted here**; the arm
  is therefore quotable as a rate, not as a number comparable with a rate taken
  at another engine identity (the S0b finding of 2026-09-17, `STATUS.md` §10).

**Missing artifacts, named rather than worked around:**

| artifact | needed by | state, 2026-09-18 |
|---|---|---|
| `eval2_rows.jsonl` of run `eval-20260916-063539` | S0a, S0b, S0c's base column | absent from the artifact repo's full listing; not matched in the second repo by the three patterns the script used (§5) |
| `attempts.jsonl` of the same run | the `eval2-rows` projection | same |
| `probe-rollouts.jsonl` of job `6aaa87465527934177ee9f34` | S0c's probe column | **present**, in `headforce/lypning-round02-work` (run 35373009782) |
| the pilot's `lypning-l` at its recorded sha256 | S0b faithfully | three `lypning-l` binaries exist in the artifact repo, one per 2026-09-18 job; none is stated to be the pilot's |
| a resolved Python 3.12 build | the round's interpreter | not audited this session; the rungs did not get that far |

**No secret and no eval-2 prompt appears in this writeup, and no `.jsonl` body
was printed into a CI log. This report's earlier claim that no bank case reached
a CI log was wrong, and §6 ("What this session published into public logs") records what actually happened**: the inventory's
`SMALL` tuple included `bundle.json`, which carries cases, so the head of every
bundle it met — the `pilot/` one and the `eval2/` one alike — was printed
verbatim into world-readable Actions logs before the tuple was narrowed.
The repository is public, so its logs are world-readable; that is the reason the
mistake matters and the reason it is written up rather than quietly fixed.

## Data and hypothesis

**The question this session was to answer** is the one `ASSESSMENT.md` §6 step 1
asks: whether the S0 rungs can be read, and what they say. **The question it
could answer** is narrower and was chosen because it is free and decisive: *do
the S0 rungs have inputs anywhere, and what has the Hub got that no document in
this tree records?*

**Predicted outcome, before the inventory ran:** that the artifact repo would
hold the round-02 smoke and little else, and that the pilot's rows would be
absent. Half right. The pilot rows are absent from that repo; the repo also
holds two later jobs and a complete base arm nobody had read, and the probe
rollouts are in the repo next door.

**The prediction that matters was made on 2026-09-16, before the data, and it is
the one §4 tests.** `reports/2026-09-16-fable-round02-pilot.md` wrote, from a
15-case glimpse: "If the 300-case eval-2 base rate lands where these fifteen do,
there is no +3pp of correct-and-native headroom for an adapter to earn on this
population, and the programme's remaining headroom is in the engine … not in the
model." `EVAL2.md` §9 had already fixed the falsifier at a base rate ≥ 90%
correct-and-native. §4 is the first time a full designed split has been measured
against that prediction on a task-first bank — **the training bank's dev split,
not the 300-case eval-2 the prediction names**, so it is a test of the
prediction's mechanism on the population the round selects on, and not yet a
test of its literal subject.

**Population.** Bank v2's pilot dev split: 256 cases, 9 families, two populations
by construction — 215 coverage cases in 7 families, and 41 fallback-control cases
in 2 families, where the right answer keeps the import and takes the fallback.
The bank's own review manifest (`reviewed-train/review.json`, run 35372153392)
records 1,689 cases in 33 train families / 9 dev / 9 test, `purpose: pilot`,
`seed: 1111`, and **`correctness_verified: false`**; the eval-2 side
(`reviewed-eval2/review.json`) records 597 cases, `purpose: benchmark`, the same
flag false. Both flags are quoted as printed and not interpreted here.

**Oracle and rights review, teacher and repair conditions, split assignment,
conditioned prompts, first drafts versus repaired answers, rejected and
quarantined counts: not applicable to this session.** No case was proposed,
graded, admitted or rejected; no bank was authored or mutated; no model was
called by this session.

**No held-out task influenced anything here**, because nothing was graded, no
repair was made and no recipe was chosen. The arm in §4 was produced by a job on
2026-09-18 that this session did not launch and cannot re-run.

## Measurements

### What the Hub holds

`headforce/lypning-round02-artifacts`, private dataset, **86 files**, printed in
full by run 35372153392 on 2026-09-18 and again unchanged by run 35373009782 the
same day. Three trees, and three round-02 job directories all dated 2026-09-18:

| job | status | last stage | exit | what it holds |
|---|---|---|---|---|
| `6aaca538b1dc2b62dc59024d` | COMPLETED | — | — | the smoke: `sft-smoke/`, `grpo-smoke/`, seven sealed adapters, `plan-001.json`, 4 execution witnesses |
| `6aacca0ab1dc2b62dc590991` | failed | `review` | 1 | `reviewed-train/` (1,971 case rows), witnesses, manifest; no bundle digest of either kind |
| `6aacd5cfb1dc2b62dc590b82` | failed | `sft` | 1 | **a complete `base-dev/` arm**, plus `pilot/` and `eval2/` bundles and both `reviewed-*` case sets |

The platform's own view of the third, from `hf-status` run 35371692743
(2026-09-18): `6aacd5cfb1dc2b62dc590b82 … ERROR  Job timeout`, and of the second:
`ERROR  Job failed with exit code: 1. Reason: Error`. **A failed job is not an
empty job**, and that is the whole reason the inventory prints paths rather than
counts.

Line counts, run 35372153392, 2026-09-18:

| file | lines |
|---|---|
| `banks/v2/train.jsonl` | 1,689 |
| `banks/v2/eval2.jsonl` | 597 |
| `bank-v3/batches/35320962503/native.jsonl` | 3,499 |
| `bank-v3/batches/35320962503/repaired.jsonl` | 13 |
| `bank-v3/batches/35340137976/native.jsonl` | 1,235 |
| `bank-v3/batches/35340137976/repaired.jsonl` | 103 |
| `round-02/6aacd5cfb1dc2b62dc590b82/base-dev/evaluations.jsonl` | 1,024 |
| `round-02/6aacd5cfb1dc2b62dc590b82/pilot/dev-prompts.jsonl` | 256 |
| `round-02/6aacd5cfb1dc2b62dc590b82/pilot/train-prompts.jsonl` | 1,120 |

### The base arm, in full

`round-02/6aacd5cfb1dc2b62dc590b82/base-dev/metrics.json`, job
`6aacd5cfb1dc2b62dc590b82`, 2026-09-18, printed whole by run 35372153392.
**Unadapted `Qwen/Qwen3.8-27B` at revision `1d4bf0f2…`, `adapter: null`,
256 dev cases, 1,024 draws, 9 families, k = 4, truncation rate 0.0 on every row.**

| population | cases | draws | families | correct | correct-and-native |
|---|---|---|---|---|---|
| **overall** | 256 | 1,024 | 9 | **0.93011063011063** | **0.7533910533910534** |
| coverage | 215 | 860 | 7 | 0.9686456400742115 | 0.9686456400742115 |
| fallback-control | 41 | 164 | 2 | 0.7952380952380953 | **0.0** |

By capability, same file:

| capability | cases | draws | families | correct | correct-and-native | mean completion tokens |
|---|---|---|---|---|---|---|
| set-ops | 35 | 140 | 1 | 1.0 | 1.0 | 126.51 |
| text | 70 | 280 | 2 | 0.9964285714285714 | 0.9964285714285714 | 48.58 |
| argv-arith | 35 | 140 | 1 | 0.9928571428571429 | 0.9928571428571429 | 92.31 |
| int-reduce | 40 | 160 | 2 | 0.9545454545454546 | 0.9545454545454546 | 113.39 |
| stdlib-module | 35 | 140 | 1 | 0.8857142857142857 | 0.8857142857142857 | 145.30 |
| fallback-control | 41 | 164 | 2 | 0.7952380952380953 | 0.0 | 100.47 |

Draw statuses, overall: `correct-native` 838, `correct-control` 126,
`incorrect` 60. Mean completion tokens overall 96.87.

**This is the first eval arm on a task-first bank to complete over a full
designed split.** The 2026-09-16 pilot's dev and test arms completed over 7 cases
each, and its eval-2 base arm blocked at 384 of 1,200 draws (`STATUS.md` §2);
no eval-2 arm has ever completed, in any round-02 job, including this one.

**And it is a BASE arm.** There is no adapter, no candidate, no paired
comparison, no interval and no ΔSLR here. **It settles nothing whatever about
training.** What it settles is what the instrument reads before training — which
is the only thing a base arm can settle, and the thing this programme has twice
paid for not having.

### What that table supports, and with what error bars

**First, what this arm is not.** `EVAL2.md` §4 is the one home of the primary
metric, and it fixes the endpoint as the correct-and-native first-draft rate
**macro-averaged over families**, sampled pass@1 at **k = 16**, compared paired
and cluster-bootstrapped by `split_group` through
`pipeline.training_metrics.paired_comparison` at 2,000 resamples with a
percentile interval, and it calls the script's default of 4 draws "a smoke
setting". This arm is at **k = 4**: 1,024 draws over 256 cases. **It is a smoke
setting by `EVAL2.md` §4's own words — but what it lacks is the bootstrap and
the interval, not the macro.** §4 defines all three, verbatim: the rate is
*"macro-averaged over families; sampled pass@1 at **k = 16** draws per case
(`--eval-draws 16`; the script's default of 4 is a smoke setting)"*, and *"The
comparison is paired, cluster-bootstrapped by `split_group` with families
resampled as whole clusters — `pipeline.training_metrics.paired_comparison`,
2,000 resamples, percentile interval"*. This arm **does** carry that macro —
(a) below shows the `correct_native` fields are family macros, exactly —
computed on the training bank's dev split at k = 4, with no adapter, no paired
comparison, no resample and no interval. Every number below is therefore a point
estimate with sampling error attached, on a population, at a draw count the
programme does not decide on. `PREREGISTRATION.md` §7b is cited here for one
thing only — the **+3pp** minimum detectable effect — because §7b is written in
SLR terms and the string "correct-and-native" does not occur in that file at all
(`grep -c correct-and-native training/PREREGISTRATION.md` → `0`, 2026-09-18).
The endpoint is `EVAL2.md` §4's, not §7b's.

**(a) The overall correct-and-native number is not an independent measurement.**
`0.9686456400742115 × 7 ÷ 9 = 0.7533910533910534`, exactly the printed overall
figure, because the fallback-control families contribute 0 by construction. The
overall 0.7534 carries no information the coverage row does not. Read as headroom
it is a mirage: it says 24.7pp only because two of nine families are *designed*
to score zero on that axis.

**That identity also settles the weighting question, and it settles it in favour
of the macro.** `correct_native` in this `metrics.json` is an **equal-weight
family macro**, not an aggregate over draws or cases: with the coverage figure
over 7 families and fallback-control's 0.0 over 2, `(7 × 0.9686456400742115 + 2 ×
0.0) ÷ 9 = 0.7533910533910534` reproduces the printed overall to the last float
digit, while draw-weighting gives `838 ÷ 1024 = 0.818359375` and case-weighting
gives `(215 × 0.9686456400742115 + 41 × 0.0) ÷ 256 = 0.813510986781076`, and
neither is the printed number. The same weighting reproduces the coverage row
from the capability rows: weighting the five coverage capabilities by their
family counts (1, 2, 1, 2, 1) returns `0.9686456400742115` exactly, where
case-weighting returns 0.9706 and draw-weighting `838 ÷ 860 = 0.9744`. **So
0.9686456400742115 is the family macro over the 7 coverage families — the
statistic `EVAL2.md` §4 freezes as the primary metric.** What separates it from
§4's *endpoint* is population and procedure, not statistic: this is the training
bank's dev split, at k = 4, unadapted, with no paired `split_group` bootstrap and
no percentile interval.

**(b) The coverage population has zero correct-but-fallback draws.** Every
coverage capability prints `correct` and `correct_native` equal to the last
digit, and the coverage statuses are `correct-native` 838 and `incorrect` 22 —
there is no third status. The axis round-02 trains on is *native given correct*,
and on 860 draws over 215 cases **its mass is exactly zero**. The 2026-09-16
pilot on the 64-case bank had 171 such draws out of 1,022 (`EVAL2.md` §11). The
new bank's coverage arm has none. Those are different banks, at different engine
identities and different k, so 171 and 0 are not a difference — they are two
readings of where the fallback mass sits, and on the newer population it does
not sit anywhere.

**(c) The estimated headroom is about 3pp, and the decision rule needs more than
that — but "about 3pp" is an estimate, not a ceiling.** The printed coverage
figure gives `1 − 0.9686456400742115 = 0.0313543599257885`, i.e. **≈3.14pp**,
and by (a) that is the headroom **on the frozen statistic**: the equal-weight
family macro, which is what the decision rule is read against. Quoted beside it,
not instead of it: the equal-weight macro over the five coverage *capabilities*
gives `1 − 0.965909090909091 = 0.034090909090909`, i.e. **≈3.41pp** — a unit
`EVAL2.md` §4 does not weight by, shown only because it demonstrates how little
the figure moves when the unit changes. What keeps ≈3.14pp from being a ceiling
is therefore not the weighting, which is now settled, but sampling error: 215
coverage cases at 4 draws each — a smoke setting, a quarter of the confirmatory
k — macro-averaged over **7 clusters**, with one capability (`stdlib-module`, 35
cases) supplying 16 of the 22 wrong draws, so the estimate moves with a handful
of draws in one family, and 7 is a small cluster count for a bootstrap that
resamples families whole. No such bootstrap was run here.

Against that, `PREREGISTRATION.md` §7b fixes the minimum detectable effect as the
**lower bound** of the paired 95% cluster bootstrap exceeding **+3pp**. A perfect
adapter — every coverage draw correct and native — would gain **≈+3.14pp** on
this split on the frozen statistic (≈+3.41pp if the capability unit is used
instead), so its lower bound would have to sit within a few tenths of a point of
its estimate. The widest thing in the way is the programme's own
measured noise: same weights through two serving stacks, ΔSLR −0.22pp
[−2.46, +2.03]; same weights through two kernels, ΔSLR +1.57pp [−0.51, +3.89]
(`STATUS.md` §2, 2026-09-14). Those are SLR on the 74-case rewrite hold-out, a
different metric on a different population, so they do not transfer as a number —
but they are the only paired intervals this programme has ever measured, they
are 4.5pp and 4.4pp wide, and **the whole estimated headroom here is narrower
than either one of those widths**. No interval has been
computed on this bank; that is the gap, and it is why the finding below is
phrased as a design signal.

The check, which costs nothing and is the arithmetic part of the claim:

```python
cov   = 0.9686456400742115         # coverage correct_native, 7 families
fbc   = 0.0                        # fallback-control correct_native, 2 families
caps  = [1.0, 0.9964285714285714, 0.9928571428571429,
         0.9545454545454546, 0.8857142857142857]  # the 5 coverage capabilities
fams  = [1, 2, 1, 2, 1]                           # families in each of them
cases = [35, 70, 35, 40, 35]                      # cases in each of them

# (a) the overall figure is the equal-weight FAMILY macro, and nothing else
assert (7 * cov + 2 * fbc) / 9 == 0.7533910533910534    # exact
838 / 1024                                # -> 0.818359375        draw-weighted, no
(215 * cov + 41 * fbc) / 256              # -> 0.813510986781076  case-weighted, no

# the coverage row is that same macro over its own 7 families
assert sum(c * f for c, f in zip(caps, fams)) / sum(fams) == cov   # exact
sum(c * n for c, n in zip(caps, cases)) / sum(cases)
                                          # -> 0.9706131078224102 case-weighted, no
838 / 860                                 # -> 0.9744186046511628 draw-weighted, no

# (c) headroom on that statistic, and on the capability unit §4 does not use
(1 - cov) * 100                                   # -> 3.13543599257885
sum(caps) / len(caps)                             # -> 0.965909090909091
(1 - sum(caps) / len(caps)) * 100                 # -> 3.409090909090895
```

Run 2026-09-18 with `python3` on this tree; both assertions hold. Every constant
in it is a `correct_native`, case or draw count of `base-dev/metrics.json` as
printed by the inventory job, quoted in the tables above. The two assertions are
what makes the weighting a fact rather than a reading: the family macro
reproduces both printed figures to the last float digit, and neither the
draw-weighted nor the case-weighted alternative reproduces either one. That
settles which statistic the number is; the conclusion drawn from it in (c) is a
design signal at k = 4 over 7 clusters, and stays an estimate.

**And (b) is why (c) understates it.** Even a perfect adapter cannot win those
three points by making fallbacks native, because there are no fallbacks to
convert: on this arm the whole gap is plain incorrectness — 22 wrong draws, 16 of
them in `stdlib-module`. Correctness is what gate A *protects*
(`PREREGISTRATION.md` §7c: the tuned rate may not fall more than 2pp below base),
not what this round is being sized to move.

### Cost and resources

Zero dollars. Three CI jobs on hosted runners for a public repository, JSON
downloads only. No provider request, no retry, no truncation, no token of any
kind, no optimizer update, no GPU second, no supervised token. Wall time is this
session's own and measures nothing.

## Failures, reflections and next experiment

**What did not work: the round, as assigned, for the third time — and this time
the reason is legible.** The assignment routes the rungs to a device; the device
is a fiction; two prior sessions burned a round each discovering that on their own
clone. The wall is not a missing machine, it is a credential that lives in
Actions and an assignment written as though it lived on a laptop.

**What this costs, and what it does not.** S0a and S0b cannot be read until
`eval2_rows.jsonl` is found or the pilot draw is re-made, and S0c's base column
needs the same file. **What is not lost is the pilot's conclusions.** Run
`eval-20260916-063539` was read when it was fresh, and its findings are recorded
with their identities in `EVAL2.md` §11 and `STATUS.md` §2: 87.9% correct, 68.7%
correct-and-native, macro over 64 cases at k = 16 and engine `2e079e786a655ab6`;
749 correct-and-native draws, 171 correct-but-fallback, 102 incorrect, 2 with no
code. What is at risk is the ability to **re-read the rows** — to re-print the
power curve in realised-macro units (S0a) and to break the 171 down by refusal
kind (S0b). Those are re-derivations of a completed measurement, not the
measurement. Nothing already concluded is withdrawn by their absence, and
nothing already concluded may be re-stated with new precision until they return.

**What this session got wrong, and it is worse than the blockage.** Widening the
inventory's `SMALL` tuple to `bundle.json` published bank cases into two
world-readable logs on a public repository. The tuple is a publication decision
and was treated as a convenience; the report that resulted then asserted, in its
own §2, that nothing had been printed. Both are recorded in §6, with the fix,
the deleted run ids, and the part deletion does not reach.

**What worked, and is the reason to read this report.** Checking an assertion
instead of repeating it. A prior track claimed the S0c probe rollouts were in a
second private repo; the `round02-evidence` skill §7 recorded that as "a
hypothesis, not a fact" and declined to act on it. One lookup — eight lines in
`s0_inventory.py`, one push, one free job — settled it:

```
== the asserted second home for the S0c probe
   headforce/lypning-round02-work: ['probes/hub-commit-probe.txt',
     'round-02/6aaa73e95527934177ee9b22/probe/experiment.json',
     'round-02/6aaa87465527934177ee9f34/probe/experiment.json',
     'round-02/6aaa87465527934177ee9f34/probe/metrics.json',
     'round-02/6aaa87465527934177ee9f34/probe/probe-rollouts.jsonl',
     'round-02/6aaa87465527934177ee9f34/probe/probe.json']
```

Run 35373009782, 2026-09-18. **The pilot job is not absent from the Hub; it is
absent from the repository the inventory was pointed at.** A rung reported
blocked twice for a file that was in the next repository along is the expensive
kind of wrong, and it was one free read from being caught.

**The same read is why the rows are not yet declarable absent.** That lookup
filtered by three patterns — a path containing `probe`, a path containing
`eval2_rows`, or a path ending `attempts.jsonl` — and printed only the hits. It
did not enumerate the repo. This tree's own record says where to look:
`reports/2026-09-16-fable-round02-run.md` §"Codex review handoff" locates "the
pilot rows and power curve" at `banks/2026-09-16-eval2-v1/` in that same repo,
and `EVAL2.md` §11 names the same path for both banks. **No pattern in that
filter matches a path under `banks/2026-09-16-eval2-v1/`.** So the honest state
of S0a and S0b is *input not yet located*, not *input destroyed*, and the
difference between those two is one listing.

**Which claim is directly supported, and which is inferred.** Directly supported
by printed output quoted with its run id: the 86-file listing and every line
count; the three job states and their manifests; the base arm's whole
`metrics.json`; the presence of `probe-rollouts.jsonl` in the second repo; the
absence of `eval2_rows.jsonl` and `attempts.jsonl` from the artifact repo; the
device audit; the arithmetic in §4, which is closed-form over the printed
numbers. **Inferred, and inferred twice over:** that this split's coverage arm is
unlikely to fire the §7b rule. The statistic is the right one — §4 (a) proves
`correct_native` here is the equal-weight family macro `EVAL2.md` §4 freezes,
which is what makes ≈3.14pp the headroom the rule would be read against rather
than an artefact of weighting — but it is an **estimate at k = 4** — a smoke
setting by `EVAL2.md` §4 — on the **dev** split, macro-averaged over **7**
clusters, with no bootstrap and no interval, so it is a point estimate and not a
ceiling; and the interval width the argument needs is borrowed from a different
metric on a different population, so the claim is "no interval this programme has
ever measured is narrow enough", not "the interval on this bank was measured and
is too wide". The frozen *endpoint* — that same macro at k = 16, on eval-2, with
the paired `split_group` bootstrap and percentile interval of `EVAL2.md` §4 — has
never been computed, and computing it is what would turn this inference into a
measurement.
**Unknown, and deliberately left so:** whether the pilot rows are in the second repo under another name, what
eval-2's base rate is, and whether the supervised-token floor passes.

**The scope of the finding, corrected by a concurrent read and then checked
here.** `reviews/2026-09-18-codex-ledger-reconciliation.md` §1, written by
another session from the same `metrics.json`, makes the correction this report
adopts: the measured arm is `--bundle work/round-02/pilot/bundle.json
--eval-split dev`, so it is the **training bank's dev split, not eval-2**, and
eval-2's base rate is still unknown. Checked locally rather than taken on trust,
over the committed bank on this tree, 2026-09-18:

```
$ python3 -c "
import json
for n in ('train', 'eval2'):
    rows = [json.loads(l) for l in open('training/data/bank_v2/%s.jsonl' % n)]
    caps = sorted({c for r in rows for c in r['capabilities']})
    print('%-7s %4d rows  %2d families  %s'
          % (n, len(rows), len({r['family'] for r in rows}), ', '.join(caps)))
"
train   1689 rows  51 families  argv-arith, fallback-control, int-reduce, set-ops, stdlib-module, text
eval2    597 rows  18 families  fallback-control, file, string
```

The two row counts match what the Hub printed for `banks/v2/` (run 35372153392),
so the local copy is the same bank; the family sets are disjoint and the
capability sets intersect in `fallback-control` alone. **The two banks share
exactly one capability and no family**, and it is the counterweight; eval-2's coverage
capabilities, `file` and `string`, have never been drawn at all. So the
saturation finding is a statement about the split the SFT stage selects
checkpoints on, and about the generator that produced it — not a measurement of
eval-2.

**The other alternative explanation, not resolvable here.** That the engine in
this job serves more than the pilot's did, inflating nativeness: the arm's
`lypning-l` sha256 was not printed, so it cannot be ruled out. It does not touch
(b), because a *more* permissive engine can only push correct-but-fallback draws
further toward zero.

**The defect was named in generation before it was measured in evaluation.** PR
#93 (commit `b42b611`, merged before this session) diagnosed it exactly: "Our run
was 3,499 of 3,837 natively served: 91.2% of the one population that was
excluded", against `PREREGISTRATION.md` §2(g), decided 2026-09-13, which strikes
already-native cases out of the pool as "the cases that cannot teach" because "an
unobserved case is a plain coding task the stock model already answers". §4 (b) is
that same diagnosis arriving from the other end: the generator wrote inside the
served subset, so the benchmark it produced is inside the served subset, so the
base model is already native on it. **bank v2 predates the fix and is that
population.** Its "fallback-control" families are the counterweight, and #93
records that in bank v2 they were drawn from the rewritable side — which is why
they can be 0.0 correct-and-native by design and still not be the headroom.

**bank v3 is the retargeted population, and it is not a bank yet.** Counted by
run 35372153392 on 2026-09-18 (that log has since been deleted — §6): batch
35320962503 holds 3,499 native and 13 repaired rows; batch 35340137976 holds
1,235 native and 103 repaired.

**The retargeting figures, attributed properly.** Both are **measurements of GH
run `35340137976`, 2026-09-18**, and their home in this tree is the
`CHANGELOG.md` *Unreleased* entry for #93: *"Measured 2026-09-18 (GH run
35340137976): the refused fraction went **5.2% to 42.1%**, repaired rows 13 to
103, and the stratum draw came out at 0.708 against the preregistered 0.710."*
Version 1 of this report credited both to "PR #93", which is right for only one
of them. `git log -1 --format=%B b42b611` (checked 2026-09-18) does carry the
refused fraction — *"Retargeting the prompt at the refused surface moved the
refused fraction from 5.2% to 42.1%"* — but **it does not contain 13-to-103 at
all**. What it records instead is a different quantity on a different
denominator: *"the rules take the repair rate over the same queue from 13 to 78
of 198"*, which is the calendar/datetime repair rules over one queue, not the
batch's banked repaired rows. The run id is the source; the changelog entry is
the record; the PR narrative is neither. The repaired rows are
the half that teaches a substitution. What it would take for bank v3 to become
the pilot population is not a judgement call — the gates are written down: a
review manifest and a sealed bundle; the bank-versus-bank leak check against
eval-2 (`EVAL2.md` §8); `preflight`'s ≥1,000 train cases, registered seed, k = 16
and one complete family cycle; and the ≥50,000 supervised-token floor that
`run()` takes with the real tokenizer (`STATUS.md` §10). Against the first of
those, 103 repaired rows is the distance still to cover.

**One projection this session refuses to carry forward.** A prior investigation
projected that `--steps 250` would fail the 50,000-supervised-token floor;
adversarial verification refuted the projection, because it used
`completion_tokens` from frozen attempts as the denominator for fenced program
text, which is not a valid ratio. **The floor's state is UNKNOWN, not failing**,
and the exact count needs the real Qwen tokenizer inside `run()`. It is recorded
here only so the refuted number does not come back as a memory.

**Next experiment, bounded, $0, discriminating.**

- **What.** Enumerate `headforce/lypning-round02-work` in full — every path, no
  filter — and line-count its `.jsonl` files, by the same push-triggered
  inventory that produced this report. One script edit, one push, one free job.
- **Why it discriminates.** It returns exactly one of two answers, and they lead
  opposite ways. Rows present: S0a, S0b and S0c are readable this week at $0, and
  three rungs of the ladder stop being owed. Rows absent after a complete
  listing of both repos: they were never uploaded, and "re-make the draw" becomes
  a real question for the operator instead of an assumption.
- **Inputs.** `HF_TOKEN` as an Actions secret; nothing else. **Owner.** Whoever
  next holds `s0/evidence-read`. **Cost.** $0; hosted minutes on a public repo.
- **Stop rule.** Unchanged and now enforced by the tools: a rung whose input is
  absent exits 2 naming the path, one whose input read or graded nothing exits 1,
  and no missing artifact is converted into a score.
- **What is still not authorized by any of this:** S1 or any paid rung, a GPU
  job, a training launch, a Space rebuild, a dataset mutation, an edit to a
  frozen artifact, or any widening of the inventory's `SMALL` tuple to a
  `.jsonl` — the log is world-readable and a `cases.jsonl` is eval-2 task text.

## What this session published into public logs

**This session printed bank cases into world-readable GitHub Actions logs. That
is a disclosure of held-out benchmark material, and it cannot be taken back.**

**What happened.** `.github/scripts/s0_inventory.py` had a `SMALL` tuple of
filenames the job reads whole and prints, so that a manifest or a metrics file is
evidence rather than a line count. `bundle.json` was in that tuple. A sealed
bundle **carries the cases** — task text, reference programs and expected stdout
— so every inventory run that met a `bundle.json` printed cases verbatim into a
log. It printed each one as
`json.dumps(payload, indent=2, sort_keys=True)[:4000]`, and `cases` sorts first
among a bundle payload's keys (`pipeline/training.py`), so what each `bundle.json`
published is the **head of its `cases` array**, up to 4,000 characters — and the
loop ran over every matching path, so **more than one bundle was printed per
run**. `kristerhedfors/lypning` is **PUBLIC** (`gh repo view --json visibility` →
`PUBLIC`, 2026-09-18), so those logs were readable by anyone, without
authentication, for as long as they existed.

**Scale, as closely as it can still be stated.** Roughly **11 bank cases** were
published across the affected logs; the audit that caught this counted **three**
affected logs. That count was taken from the logs before they were removed and
**cannot be re-derived now**, which is itself part of the cost of the mistake —
after deletion there is no way from this tree to enumerate what each log held.
Two of the affected runs can still be named by id: `35372153392` (commit
`6f53409`) and `35373009782` (commit `4c2429c`); both now answer `gh run view
<id>` with `HTTP 404: Not Found` (checked 2026-09-18), i.e. deleted. A third, if
the audit's count is right, is equally gone and equally unenumerable. The one
surviving inventory run, `35372015658` (commit `27d6f0d`), predates the `SMALL`
block and printed paths only — `gh run view 35372015658 --log | grep -ci
bundle.json` → `3`, all of them path lines, and `| grep -ci task` → `0` (both
run 2026-09-18).

**The fix.** Commit `a2958b6`, *"Never print a bundle into a public log: the
cases are the held-out benchmark"*, removes `bundle.json` from `SMALL` and
records in the file why the tuple is a publication decision and not a
convenience: *"Never widen this to anything that holds task text, a reference
program or an expected stdout; count its lines instead, or compute the statistic
in the job and print only the statistic."* Aggregates only — a manifest, a
metric, a seal.

**What deletion does and does not do.** Deleting a run removes the log from
GitHub. **It does not undo a read that already happened.** A public Actions log
is fetched by crawlers, mirrors, proxies and anyone watching the repository's
Actions feed; between the push and the deletion the material was public, and
there is no way from here to establish that nobody took a copy. **The correct
assumption is that the published cases are compromised**, not that they are
probably fine.

**The residual risk, stated plainly.** Eval-2 is the deployment benchmark and its
value rests on the model never having seen its cases. Published task text,
reference programs and expected stdout are exactly the material that, if it
reaches a training corpus or a future crawl, makes a score on those cases
unreadable — and the same disclosure weakens any future claim that eval-2 was
held out, whether or not contamination actually occurs.

**Which cases, as precisely as a deleted log allows — and version 1 of this
report overstated it.** It said a `bundle.json` "is the sealed benchmark bundle,
so the cases in it are precisely the held-out ones". That is wrong in one
direction and would be equally wrong to flip. The artifact repo holds **three**
`bundle.json` paths — `gh run view 35372015658 --log | grep -ci bundle.json` →
`3`, all path lines, 2026-09-18 — including the `pilot/` and `eval2/` bundles of
job `6aacd5cfb1dc2b62dc590b82` (the listing above); the loop walked the sorted
file list and printed from **every** one of them, 4,000 characters each. A
recheck of the log before it was deleted recorded that **the first case printed
carried `"split": "train"`** (2026-09-18; the log now answers 404, so this cannot
be re-read). That label does **not** by itself make it a training case:
`training_data.split_cases` stamps `train`/`dev`/`test` on the cases of *every*
bundle, benchmark bundles included, and a benchmark bundle is held out whole
regardless of the stamps (`training.py`: *"Never export test solutions as SFT
rows; a benchmark is all held out"*). So the honest statement is: what was
published is the **head of each printed bundle's case list**, spanning more than
one bundle — the pilot bank's training population and the eval-2 bundle among
them — and **which of the roughly 11 cases came from which bundle can no longer
be established from this tree**, because the logs that would answer it are gone. Treat it as: *some
held-out eval-2 material was published, and an unknown part of the 11 was
training material instead*. Neither "it was all eval-2" nor "it was only training
data" is supportable.

Eleven cases are a small count against a 597-case eval-2, but they are not spread
evenly — they are whatever each bundle's head held, concentrated in a few
families, and a family whose cases are public is a family whose reading cannot be
defended. The exposure is bounded in count and unbounded in time: it is not zero,
it is not recoverable, and it does not expire. The uncertainty about the split is
a reason to assume the worse case, not a discount on it.

**This session also damaged its own evidence trail.** The two deleted runs are
the runs this report cites for the 86-file listing, the line counts, the base
arm's `metrics.json` and the second-repo lookup. Those citations no longer
resolve; the numbers stand as recorded here and in the concurrent review, but
they can no longer be re-read from the log, only re-produced by a fresh
inventory job on the fixed script. The handoff section is corrected accordingly.

**The decision is the operator's, and this report does not pre-empt it.** Whether
the affected eval-2 families are re-cut — or the exposed cases quarantined and
replaced, or eval-2 re-drawn — is a call about benchmark integrity and cost, and
it belongs to whoever owns `EVAL2.md` §8 and `STATUS.md` §10. What this section
supplies is the fact, the scale as far as it is still knowable, the fix, and the
statement that deletion is not a remedy.

## What a reader must not conclude from this report

1. **Not that training was evaluated.** §4 is an unadapted base arm. No adapter
   exists in it, no paired comparison was made, no interval was computed.
2. **Not that the model is 75.3% correct-and-native.** That figure is the
   coverage family macro × 7/9 by construction — the identity is exact (§4 (a))
   — and two of the nine families score 0.0 on that axis *by design*.
3. **Not that fallback-control scoring 0.0 is a failure.** The right answer in
   that arm keeps the import and takes the fallback; 126 of its 164 draws are
   `correct-control`.
4. **Not that the base model is good at Python.** This is one bank's dev split,
   k = 4, macro over 9 families, graded by one engine whose sha256 is not quoted
   here, inside a job that failed at the next stage.
5. **Not that eval-2 has been measured, or that eval-2 is saturated.** No eval-2
   arm has completed in any round-02 job; the bundle was cut and the stage never
   ran. The two banks share one capability and no family, so nothing in §4
   transfers to eval-2's `file` and `string` coverage.
6. **Not that the 2026-09-16 pilot's findings are withdrawn.** Only the ability
   to re-read its rows is in question, and only until the second repo is listed.
7. **Not that any S0 rung has been read.** None was run. The ladder's state in
   `STATUS.md` §10 is unchanged by this report.
8. **Not that 0.9686 is a ceiling, or that the §7b rule has been shown unable to
   fire.** It *is* the family macro `EVAL2.md` §4 freezes (§4 (a)), which is why
   the finding is worth acting on — but it is a point estimate at k = 4,
   `EVAL2.md` §4's smoke setting, on the dev split, over 7 clusters, with
   sampling error and no interval. The frozen endpoint is that macro at k = 16 on
   eval-2 with a paired `split_group` bootstrap, and it has not been computed.
9. **Not that deleting the runs in §6 contained the disclosure.** It
   removed the logs from GitHub; it did not remove any copy taken while they
   were public.

## Codex review handoff

**Artifacts Codex can inspect**, all free:

- this report;
- **`gh run view 35372153392 --log` and `gh run view 35373009782 --log` are
  GONE** — both return `HTTP 404: Not Found` (2026-09-18). They were the full
  86-file listing, the S0-input block, the line counts, `base-dev/metrics.json`
  whole, and the second-repo lookup, and they were deleted because they also
  printed bank cases (§6). Re-producing them means one push of the fixed
  `s0_inventory.py` and one free job; the numbers in §4 are quoted here as they
  were printed and are corroborated by the concurrent review's independent read
  of the same `metrics.json`;
- `gh run view 35372015658 --log` — the surviving inventory run, paths only;
- `gh run view 35371692743 --log` — the HF job states, including
  `6aacd5cfb1dc2b62dc590b82 … ERROR Job timeout`;
- `.github/workflows/s0-inventory.yml` and `.github/scripts/s0_inventory.py`,
  and the `round02-evidence` skill that documents the mechanism;
- `reviews/2026-09-18-codex-ledger-reconciliation.md`, a concurrent session's
  independent read of the same `metrics.json`, which this report cites for the
  dev-split-not-eval-2 correction and does not otherwise depend on;
- for the arithmetic: the constants in §4 and the Python block there,
  and the bank command in §5 over `training/data/bank_v2/`, which reads a
  committed file and writes nothing.

**No private row, bank row or task text is attached to this report. Bank cases
were printed into public Actions logs by this session's own job, and §6 is the
record of it — including the part that deletion does not fix.**

**Unresolved questions and decisions needed:** the three in §1 — whether bank
v2's coverage arm is retired as the population a round selects on, under
`EVAL2.md` §9 and `PREREGISTRATION.md` §2(g); whether `START_NEXT_ROUND.md` line
19, `STATUS.md` §10's S0 cells and the `round02-evidence` skill §7 are corrected
now that the second repo demonstrably holds the S0c probe; and whether S0c
proceeds from CI. A fourth, procedural: the base arm belongs in `STATUS.md` §2's
scoreboard with its job id and date, which is where every other measured number
in this programme lives — and the concurrent review is already proposing text
for it, so **one of the two proposals should land, not both**. This session
wrote no `STATUS.md` text for exactly that reason. **A fifth, and the only one
addressed to the operator rather than to Codex: whether the eval-2 families whose
cases were published in §6 are re-cut, quarantined or left standing.** This
report does not recommend an answer; it supplies the disclosure the answer needs.

**Codex's independent assessment is deliberately not written here.** This session
produced the evidence in §4 and argued the inference in §5, and ledger row R3
already records what a review costs when it is not independent of the work it
reviews.
