---
name: round02-evidence
description: Read the private round-02 artifacts on the Hugging Face Hub when HF_TOKEN exists only as a GitHub Actions secret — the Hub is the device, and a CI job is how you reach it. Covers the push-triggered workflow pattern, the two readers already in the tree (.github/workflows/s0-inventory.yml and hf-status.yml), stripping the gh log prefix, the artifact repo layout, and which files may and may not be printed into a world-readable log. TRIGGER on "read the private artifacts", "what does the Hub actually hold", "is the S0 input still there", "which round-02 job finished", "did the bank upload", "HF_TOKEN is not set", or any rung reported blocked for want of a private file. SKIP for launching or resuming a training job (that is `round02-launch` and the operator), for preparing or sealing a bundle (`training-bundle`), for authoring cases (`training-cases`), and for anything that would print eval-2 task text or bank rows into a CI log.
---

# Reading the private round-02 artifacts

The token is not on this machine and never will be, so the read happens where
the token is. `training/START_NEXT_ROUND.md` line 19 says to run the S0 rungs
"on the device that already owns the private round-02 artifacts"; no such device
exists, and waiting for one is what cost days. The Hub owns them, CI can reach
the Hub, and a CI job that only lists and downloads JSON is free.

## 1. The shape of the wall

`HF_TOKEN` is a GitHub Actions repository secret, which is write-only from
outside a job. Verified 2026-09-18:

```bash
gh secret list            # BERGET_API_KEY, CEREBRAS_API_KEY, HF_TOKEN
env | grep -ci hf_token   # 0 — no local shell has it
command -v huggingface-cli hf         # prints nothing, exits 1 — neither is installed
python3 -c "import huggingface_hub"   # ModuleNotFoundError
```

There is no local fallback to reach for. `gh secret list` prints the name and
the date it was last set and never the value, the CLI is not installed, and the
library is not importable — a local fetch has nothing to authenticate with, so
do not spend a turn writing one.

## 2. The mechanism: push a branch, read the log

Add a workflow whose `on: push: branches:` glob matches the branch you are
about to push, push it, and read the run's log. `gh` has `workflow` scope here,
so the push itself is the trigger and no approval step stands between.

```bash
git push -u origin s0/evidence-read          # matches "s0/**"
gh run list --workflow=s0-inventory.yml --limit 5
gh run view <run-id> --log
```

**Why push and not `workflow_dispatch`.** `gh workflow run` resolves the
workflow by name against the **default branch**, so a workflow file that exists
only in your tree or only on a feature branch is not addressable. Both failure
modes, measured 2026-09-18:

```
$ gh workflow run never-pushed.yml --ref s0/evidence-read
HTTP 404: workflow never-pushed.yml not found on the default branch

$ gh workflow run s0-inventory.yml --ref main
HTTP 422: Workflow does not have 'workflow_dispatch' trigger
```

The 422 is the trap that reads like a bug: the workflow *does* declare
`workflow_dispatch`, but on the feature branch, and `--ref main` asks GitHub to
run main's copy, which does not exist. The first run of a new workflow must
therefore be a push. After that first push run GitHub holds an ID for it, and
`gh workflow run <file> --ref <that-branch>` then works — run 35372925789 was
dispatched that way onto `s0/evidence-read`, after run 35372015658 had created
it by push. Run 35372925789 404s today — it was deleted (§5) — which changes
nothing about the mechanism and everything about quoting its output.

## 3. The two readers already in the tree

Both install `huggingface-hub==1.31.0`, pass `secrets.HF_TOKEN` into one step,
never download a weight, and bill nothing.

| Workflow | Script | Trigger | Prints |
| --- | --- | --- | --- |
| `hf-status.yml` | `.github/scripts/hf_status.py` | `round02/**`, `bank3/**` | recent HF jobs with stage and message, file **counts** by prefix, the last four round manifests, the bank-v3 batch ids |
| `s0-inventory.yml` | `.github/scripts/s0_inventory.py` | `s0/**` | **one named** second repository, filtered; **every path** in the artifact repo; the S0 inputs by name; a line count per `.jsonl`; and every aggregate JSON in `SMALL` whole |

Use `hf-status.yml` for "did the job finish, did the upload land" — it answers
in about twenty seconds and says nothing it does not need to. Use
`s0-inventory.yml` when the question is "does this rung have its inputs" or
"what did that arm actually measure": counts cannot settle either, and a
remembered layout is not evidence.

It does **not** enumerate the token's scope. Its first section checks exactly
one named repository — `<owner>/lypning-round02-work`, the owner read off
`api.whoami()` — and prints only the paths in it matching `probe`, `eval2_rows`
or `attempts.jsonl`. Listing every repo a token can see was considered and
rejected: the question is whether one asserted file exists, and a scope dump
answers a question nobody asked, into a log anyone can read.

The line counts are not free of the rows. To count lines the job downloads
**every** `.jsonl` in the artifact repo into the runner — `banks/v2/train.jsonl`
and `bank-v3/batches/*/native.jsonl`, every `cases.jsonl`, every
`*-prompts.jsonl`. Only the count is printed; the rows themselves cross the wire and are
thrown away with the runner. That is the boundary §5 draws, and it is a boundary
about *printing*, not about downloading.

`s0_inventory.py` gates its whole-file printing on a `SMALL` tuple —
`job-manifest.json`, `metrics.json`, `experiment.json`, `best.json`,
`config.json`, `plan-001.json`, `seal.json` — and truncates each at 4,000
characters. Everything else stays a path in the listing. `bundle.json` and
`review.json` were in that tuple and were removed on 2026-09-18 (commit
a2958b6); read §5 before you put anything back, and extend the tuple rather
than writing a second script.

## 4. Stripping the log prefix

`gh run view <id> --log` prefixes every line with job name, step name and an
ISO timestamp, separated by real tabs. Three fields, so cut then strip the
timestamp:

```bash
gh run view 35372015658 --log | cut -f3- \
  | sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z //' > /tmp/clean.log
awk -F'\t' 'NF>2' /tmp/clean.log | wc -l    # 0 — re-run 2026-09-18
```

The first line carries a UTF-8 BOM ahead of its timestamp and so keeps it; that
line is `Set up job` and never evidence. Do not reach for `\xEF` in the `sed`
expression — BSD `sed` on this machine does not take it, and the failure is
quiet enough to send you chasing a phantom.

## 5. What the artifact repo holds

`headforce/lypning-round02-artifacts`, a **private** dataset repo. Printed by
run 35372015658 on 2026-09-18: `86 file(s)`, in three trees. That run survives;
the three inventory runs after it — 35372153392, 35372925789, 35373009782 — were
deleted the same day, and the rule at the end of this section is why.

- `round-02/<job-id>/` — one directory per HF job. `6aaca538b1dc2b62dc59024d`
  is a completed SMOKE (`sft-smoke/`, `grpo-smoke/`, sealed adapters).
  `6aacca0ab1dc2b62dc590991` failed at stage `review`. `6aacd5cfb1dc2b62dc590b82`
  failed at stage `sft` and still holds a complete `base-dev/` arm plus `pilot/`,
  `eval2/` and `reviewed-*` bundles.
- `banks/v2/` — `train.jsonl` and `eval2.jsonl`; run 35372153392 counted 1,689
  and 597 lines on 2026-09-18.
- `bank-v3/batches/<gh-run-id>/` — `native.jsonl` and `repaired.jsonl` per
  generation run; the same run counted 3,499 and 13 for batch 35320962503,
  1,235 and 103 for batch 35340137976.

Run 35372153392 has since been deleted, so those four counts cannot be re-read
from a log; they are quoted with their run and date in
`training/reports/2026-09-18-codex-saturated-bank-and-the-absent-pilot.md`, and
re-checking them means re-running the workflow, not remembering them.

One caution for whoever prints the base arm's `metrics.json` (run 35372153392,
2026-09-18, quoted in that same report): it is a **dev-split smoke at k = 4** —
1,024 draws over 256 cases, and `EVAL2.md` §4, the one home of the primary
metric, calls k = 4 a smoke setting and fixes the endpoint at k = 16,
macro-averaged over families. Its coverage `correct_native` of 0.9686 is a
case-weighted **aggregate** and a **point estimate** carrying its own sampling
error; the macro over the five coverage capabilities in the same file is 0.9659.
Either is quotable as an estimate at k = 4, with that caveat attached. Neither
is an exact ceiling, and neither is the primary metric.

**The rule, and it is about content, not size:** a file that holds task text, a
reference program or an expected stdout is **never printed** — not truncated,
not sampled, not "just the first one". Count its lines, or compute the statistic
inside the job and print only the statistic. Everything else that may be printed
is an aggregate: counts, digests, seeds, rates.

**Printable, and exactly the `SMALL` tuple in `s0_inventory.py`:**
`job-manifest.json`, `metrics.json`, `experiment.json`, `best.json`,
`config.json`, `plan-001.json`, `seal.json`. The base arm's whole `metrics.json`
printed in well under the 4,000-character cap.

**`bundle.json` is not an aggregate, whatever it looks like.** It carries
`cases` — the task text, the reference program and the expected stdout of the
held-out benchmark — so printing one publishes eval-2 to anyone who reads or
scrapes the log. It was in the tuple until 2026-09-18; three Actions runs
printed roughly eleven cases into world-readable logs before commit a2958b6 took
it out (with `review.json`, which travels with it) and those three runs were
deleted. **Deleting a run is not a retraction**: it ends the exposure, it cannot
undo it, and it is why the four counts above now have no log to point at. That
cost is the whole argument for the rule.

**Never printed at all:** `adapter_model.safetensors` (weights — never even
downloaded), `rollouts.jsonl`, `probe-rollouts.jsonl`, `evaluations.jsonl`,
`execution-witnesses.jsonl`, `bundle.json`, and every `cases.jsonl` /
`*-prompts.jsonl`, plus the `*-sft.jsonl` views in bundles prepared before
2026-09-22 (newer bundles no longer write them). Line-count them; that is what
the `line counts` section of `s0_inventory.py` is for, and note that counting
them downloads them (§3).

**Printed whole, and wider since 2026-09-22:** `metrics.json` and `best.json`
now carry `case_clusters` — per-case draw/correct/native counts in private
case order plus a digest of the case set, no ids or text — so eval-2's per-case
outcome distribution reaches the public log through `s0_inventory.SMALL`.
Whether to allow that or strip the key is an open operator decision.

## 6. The log is public. Check before you print.

Verified 2026-09-18:

```bash
gh repo view --json visibility,nameWithOwner
# {"nameWithOwner":"kristerhedfors/lypning","visibility":"PUBLIC"}
```

The repository is public, therefore **every workflow log is world-readable**,
therefore anything a CI job prints out of the private dataset repo is published
by printing it. The secret itself is safe — GitHub masks it, and `HF_TOKEN: ***`
is what the log shows — but masking applies to the secret, not to what the
secret fetched.

That is why the boundary in §5 is where it is. Aggregate JSON leaves the repo
private in every way that matters; a `cases.jsonl` or a `*-prompts.jsonl` is
eval-2 task text, and printing it publishes the held-out set to anyone who can
read the repository — including a future crawl of it, and including after the
run is deleted. Never widen the `SMALL` tuple to a `.jsonl`, and never to a
JSON that carries cases: `bundle.json` looked like a manifest and was one such,
which is how eleven cases reached a public log on 2026-09-18 (§5). If a
question genuinely needs row contents, answer it with a derived statistic
computed inside the job and print only the statistic.

## 7. What the Hub does and does not hold

Job `6aaa87465527934177ee9f34`, the 2026-09-16 pilot that rungs S0a/S0b/S0c
read, is not in the **artifact** repo. Run 35372015658 checked by name on
2026-09-18 and printed:

```
== S0 inputs, by the names START_NEXT_ROUND.md uses
   probe rollouts (S0c)     ABSENT
   eval2 rows (S0a/S0b)     ABSENT
   attempts (preflight)     ABSENT
   pinned lypning-l (S0b)   ['round-02/6aaca538b1dc2b62dc59024d/engine-home/bin/lypning-l', ...]
```

Three `lypning-l` binaries exist, one per round-02 job; none of the pilot's rows
is in this repo, and none is on this machine either.

**But the artifact repo is not the only home, and that is now verified rather
than asserted.** `headforce/lypning-round02-work` holds
`round-02/6aaa87465527934177ee9f34/probe/` with `probe-rollouts.jsonl`,
`metrics.json`, `probe.json` and `experiment.json` — printed by run 35373009782
on 2026-09-18, the run that added the one-repo check of §3. That run has since
been deleted with the other two (§5), so the output survives quoted in
`training/reports/2026-09-18-codex-saturated-bank-and-the-absent-pilot.md` and
re-reading it means re-running `s0-inventory.yml`.

`eval2_rows.jsonl` and `attempts.jsonl` are in **neither** repo as read on
2026-09-18: absent from the artifact repo's full 86-path listing, and unmatched
in the work repo. One caveat travels with that, because the check filters rather
than enumerates — it prints only paths matching `probe`, `eval2_rows` or
`attempts.jsonl`, so a row file stored under some other name (this tree's own
records point at `banks/2026-09-16-eval2-v1/`) would not show. *Input not
located* is the honest state; *input destroyed* is a stronger claim than one
filtered listing supports.

So, precisely: **S0a and S0b stay blocked** and need the pilot re-run, or those
rows found. **S0c is blocked on its base column only** — its probe column has
its rows. A rung that reads nothing reports blocked; it never reports a clean
read of nothing.

## 8. Before you call it done

- Every number you quote names the run id and the date it was printed
  (invariant 3). `86 file(s)` is what run 35372015658 printed on 2026-09-18, not
  a size you remember — and a number whose run has been deleted names the
  document that quotes it, because it can no longer be re-read from a log.
- What the job printed is narrower than what it fetched: the line-counts loop
  downloads **every** `.jsonl` in the repo — bank rows, `cases.jsonl`,
  `*-prompts.jsonl` — into the runner and returns only the count (§3). No weight
  was downloaded, no stage ran, no adapter trained, nothing billed.
- Nothing you added prints a `.jsonl` body, a `cases` array, a reference program
  or an expected stdout — and `gh repo view --json visibility` was re-checked if
  it has been a while. If something did print one, deleting the run limits the
  next reader and retracts nothing; say so where the numbers are quoted.
- The new workflow's first invocation was a **push**, and its branch glob does
  not match branches other sessions push.
