# Independent assessment — Fable's blocked S0 round and the next bounded action

**Date:** 2026-09-17. **Reviewed tree:** `dd47bae` (`main`, PR #89), plus the
corrections described in this review. **Reviewed report:**
[`reports/2026-09-17-fable-s0-blocked-and-the-vacuous-read.md`](../reports/2026-09-17-fable-s0-blocked-and-the-vacuous-read.md).

## Decision

The round produced **no training result and no S0 evidence result**. It ran on a
clone that held none of the three private inputs, so S0a, S0b and S0c remain
unanswered. No model, dataset, benchmark result, adapter or frozen artifact
changed; no provider or GPU was used; cost was $0. The programme still has no
completed eval-2 model arm and paid work remains held.

Fable's five fail-closed guards are accepted. They correctly distinguish a
missing required path (usage, exit 2) from a present input that measured nothing
(failed measurement, exit 1), without changing `jsonio.read_jsonl`'s deliberate
resume semantics. The added tests exercise the failure branches, not only the
accepting path. The two documentation corrections about S0b's engine dependence
and the token gate's stage-time location are also accepted.

The next action remains one read-only, $0 S0a–S0c session on the device that
already owns the private round-02 artifacts. It is not a GPU round. It must stop
and report if any prerequisite is absent. No S1, Space rebuild, provider call,
dataset mutation or paid work is authorized by this review.

## What the report establishes

The report's blocked outcome is internally consistent with the tree and its
quoted command behavior:

- S0a and S0b could not find the named legacy run.
- Before PR #89, S0c read two absent JSONL paths as empty lists, rendered a
  plausible all-zero table and exited 0.
- The same empty-measurement shape existed in the two-path lever join and in a
  replay whose explicit engine existed but could not execute.
- On one local run, changing only the replay binary changed the derived
  `correct-fallback` population from 14 draws to 26. That demonstrates that the
  old `levers --run` command did not merely attach refusal kinds; it redefined
  the population with the local engine.
- The exact supervised-token count is available only after tokenization. It is
  therefore a stage gate, not one of the no-download `--plan` gates.

None of these observations answers the three scientific questions registered
for S0. In particular, an absent artifact is not evidence for zero power, zero
refusal concentration or zero adapter movement.

## Additional review findings and corrections

### 1. S0b still did not bind the claimed evidence

PR #89's handoff said to record an `@ engine <fingerprint>` line from
`levers --run`, but that command printed no engine identity. More importantly,
the installed-chain fingerprint cannot identify an explicit
`--engine /path/to/historical/lypning-l`: that path need not be installed in the
chain `engines.identity()` inspects.

The command also continued to generate its status rows by replaying the legacy
attempts through the current engine. Requiring the historical binary would make
that reproduction closer, but it would still make a frozen population an
implicit recomputation.

The corrected S0b contract has three independent pins:

1. `--population-rows` reads status, family and split group from the already
   materialized pilot rows. The engine replay supplies refusal kinds only.
2. `--require-engine-sha256` binds the explicit replay binary to the recorded
   `lypning-l` bytes
   (`a23b30832e00640cec2090d8403a6beeaa2087083d0fbd9080210fd8d4fc1096`).
   The command prints that SHA, version line and oracle Python.
3. `--expect-draws 171` refuses a wrong or incomplete population. Any replay
   mismatch, harness error, unmatched draw or fallback row without a refusal
   now prevents the vector from being printed; a partial vector is not a
   successful result.

This preserves the reviewed 171 as a historical population instead of changing
the number. The family vector is still relative to the historical engine, and
held-out ranking remains prohibited.

### 2. The verifier needed a topology cap, not a global capacity floor

The launcher already refused total pool capacity below `score_workers`, but it
accepted `16 sandboxes/host × 1 host`. That recreates the one-CPU-host
contention shape that blocked round 02 while satisfying the arithmetic gate.
It now caps concurrency at four sandboxes per CPU host and caps the cost
envelope at four hosts, in addition to requiring total capacity for all scorer
workers.

The report's suggested global minimum is not adopted. A `1 worker × 1 sandbox ×
1 host` diagnostic is serial and slow, but it is not CPU contention and it does
not change a score. The safety property is bounded concurrency per host; the
confirmatory round's configured topology remains 16 workers, four per host,
four hosts.

### 3. The supervised-token floor stays at stage time

Moving the exact 50,000-token check into `preflight` would make `--plan` load the
Qwen tokenizer and break its no-download/no-GPU-dependency contract. The stage
already computes the exact assistant-token exposure before downloading the 27B
weights and refuses a short schedule. That split remains.

The prior handoff's instruction to check `steps × batch_size` against 50,000 is
withdrawn: that product counts example exposures, not tokens. A passing plan is
not a token-dose certificate. The exact `planned_supervised_tokens` value from
the stage must be recorded, and a refusal ends the job without weakening the
floor.

## Next bounded action for Fable

Use the exact command block in `START_NEXT_ROUND.md` on the private-artifact
device. The required inputs are:

- `training/runs/eval-20260916-063539/attempts.jsonl`;
- its materialized `eval2_rows.jsonl` containing exactly 171
  `correct-fallback` draws;
- `work/round-02/6aaa87465527934177ee9f34/probe/probe-rollouts.jsonl`;
- the historical `lypning-l` binary with SHA-256
  `a23b30832e00640cec2090d8403a6beeaa2087083d0fbd9080210fd8d4fc1096`.

Run S0a, then the newly pinned S0b, then S0c. Preserve complete stdout/stderr,
input hashes, the explicit engine identity line, unmatched counts and exit
codes. Write one Fable report and stop for review. A missing file, an engine SHA
mismatch, a population other than 171, a partial replay or a probe-only case is
a blocked rung—not permission to substitute another run or engine.

Only after that report can Codex decide whether S1 is worth authorizing. The
standing order remains: no paid rung before every cheaper rung below it has
been read.
