# Compatibility-first data production and learning loop

The model objective is **correct lypning-l-compatible first drafts**, not a
9×-per-script filter. The user's approximately 10× compatible-workload premise
motivates expanding coverage. Aggregate >9× runtime health is a separate measured
goal, not a claim that every task or every machine has that speedup. No maximum
speedup is imposed. Every incremental correct-compatible gain is useful.

For larger question campaigns, verified teacher repairs, behavioral-training
experiments and Codex's review of Fable's rounds, use the active ownership and
decision ledger in [ORCHESTRATION.md](ORCHESTRATION.md). Collection commands and
the review queue live in [HARVESTING.md](HARVESTING.md).

## One loop, distinct evidence and decisions

1. **Observe authorized normal use.** Keep source, actual invocation context,
   producer/model attribution when known, inputs/outputs when available, native
   outcome/refusal and identity/limits. Capture is not permission to run again.
2. **Snapshot privately.** `lypning.evidence` copies exact raw log bytes, indexes
   every occurrence, preserves unknown fields and stores exact UTF-8 source by
   full SHA-256 when a producer supplies `program`. Malformed, oversized and
   incomplete lines remain in the raw archive with explicit quarantine reasons.
   Ambiguous duplicate keys, nonfinite numbers and metadata nesting beyond 128
   levels are likewise quarantined without discarding the original bytes.
3. **Review intent and rights.** Supply a real task specification, reviewed
   provenance, independently expected outputs, input variants, capabilities and
   source/template grouping. `pipeline.data_loop` checks schema, evidence links,
   split independence and review assertions without executing any programs.
4. **Verify in fresh containers.** `training-prepare` first checks correctness on
   CPython for every input, then L. A native mismatch blocks preparation; a valid
   native refusal is a coverage fact, not proof of a wrong task. Review and
   execution identities join the immutable bundle. Publish its manifest last.
5. **Train conditionally.** Base control, assistant-only compatible-reference SFT,
   train-only on-policy probe, GRPO only on informative verified signal. Decode
   ordinary task prompts without engine feedback/reference answers. Keep raw
   generation/verification evidence even when it contributes no training loss.
6. **Evaluate and decide.** Select on dev under population/capability correctness
   gates; lock choices before held-out matched evaluation and grouped uncertainty
   reports. Measure aggregate interpreter performance separately. Failed learning
   stages do not imply a runtime improvement or permission to weaken correctness.
7. **Feed the next version.** Preserve rollout failures, refusals and runtime bug
   witnesses. Prioritize real compatibility demand, fix/expand L deliberately,
   rebuild its image and reverify labels. Create a new review/bundle; never mutate
   the running experiment's tests, population labels or split.

The legacy rewrite corpus/tools remain historical evidence. The new task-first
path does not silently relabel their feedback-augmented prompts as ordinary
first-draft tasks. See `TRAINING.md` for the optimization contract and
`START_NEXT_ROUND.md` for the other-device commands.

## Identities and context are separate

| Object | Meaning and retention |
| --- | --- |
| Exact source SHA-256 | Exact UTF-8 bytes, including whitespace inside literals; not AST normalization |
| Event ID | Origin/log generation + line + exact raw-line digest; repeated executions remain distinct |
| Raw producer record | Lossless unknown fields, source/argv/stdin and available outcome; missing values stay unknown |
| Legacy corpus ID | Short normalized discovery/regression summary; not a unique execution or training task |
| Task + reviewed source group | Independently specified problem and lineage, potentially many contexts/solutions |
| Verification bundle | Reviewed task split + engine/oracle/harness/image identities + reference results |
| Model run / adapter seal | Exact base revision, bundle, decoding, code, dependencies, seeds and checkpoint bytes |

Reuse the same `--origin` when snapshotting a growing log. Assign a new origin
for rotations or rewritten logs. Reimported exact events can be joined by ID;
different origins are not assumed to be distinct semantic events. Cross-feed
joins require explicit tool/run IDs and producer namespace. Timestamp proximity
alone must not become certain model attribution. Source dedup saves storage;
it must never erase differing argv, stdin, environment assumptions or outcomes.

Historical `Sighting`/`corpus.Entry` are summaries, not the evidence archive.
Their normalizer now preserves multiline literals/un-tokenizable source and
forward metadata survives conversion. Existing historical IDs/files are not
bulk rewritten; text already lost to earlier normalization cannot be recovered
without its raw source. Use new exact artifacts for training lineage.

## Interface coverage and deliberate limits

| Interface | Current default observation | Missing/host responsibility |
| --- | --- | --- |
| Claude/OpenHands hooks, OpenCode plugin | Python-ish commands and volunteered tool/session context | Hook may be pre-execution; a command is not its result; no blanket transcript/file read |
| Python PATH shim | Actual invocation, inline `-c`, argv/script/module descriptors | Does not consume stdin or copy file source; default `exec` preserves process behavior; optional exit capture has signal tradeoffs |
| Python `embed.Library.run` | Private source, argv, limits, version/path, binary I/O, status and commit/fallback flags | Does not snapshot filesystem state, identify a model, or claim correctness; oversized source/I/O becomes hash+length with incomplete flag |
| Direct CLI/runtime route ledger | Bounded refusal diagnostic where that path records it | Write-only/evictable route ledger is not a full history or data-driven routing policy |
| Direct C ABI, C++/Node/Go/Swift/Lua/Rust hosts | No new automatic cross-language source/I/O logging in this refactor | Host should emit the same event fields to its own authorized private sink; do not put logging or filesystem policy inside the ABI core |

`LYPNING_CAPTURE=0` disables supported capture feeds, including the Python
binding. `LYPNING_HARVEST=0` disables automatic published sighting derivation while
retaining private capture. Use both for benchmark/test/training replay to avoid
recapturing the evaluation corpus. The shim still defaults to real CPython;
capture installation alone is not automatic acceleration.

Python-binding logging is outside the native interpreter but adds host overhead.
Benchmark both capture-on end-to-end and capture-off engine-only paths with
explicit labels before making a performance claim. Native hosts still need a
low-overhead host-owned collector before claiming complete interface coverage.
No CLI dispatcher, fallback predicate, output commit barrier or hot-path route
decision is changed here.

## Privacy and retention

Default capture is local observation, **not consent to publish or train on
everything collected**. Logs can contain secrets, proprietary code, personal
data, paths and terminal output. New Python log files are owner-only; snapshots
have owner-only directories/files. Existing file permissions are not rewritten.
Archive only data the operator may retain; apply access/retention policies and
honor deletion requirements. `work/` is gitignored, not encrypted or backed up.

Raw snapshots never call redaction, upload, commit or execute. This preserves
authorized originals; it also makes them unsafe to share without review. Legacy
published sightings still use their redaction safeguards, which are not a
guarantee that every secret shape is found. For privacy-sensitive projects,
disable automatic harvest and review any proposed public artifacts explicitly.

The capture line cap is 4 MiB. The Python binding retains source and each I/O
buffer up to 64 KiB, otherwise hashes/lengths; a wholly oversized record may fail
to append. That is a stated bounded-observation limit, not lossless observation
of every use. Snapshot losslessness applies to the supplied log bytes. It does
not restore omitted source, absent inputs, rotated-away records or unobserved
native-host calls. No automatic log rotation/deletion is introduced.

## Views, not deletion

- **Compatible SFT:** reviewed tasks with correct native references on all
  supported observable tests. Timing not required.
- **Fallback controls:** legitimate out-of-subset problems; prevent compatibility
  pressure from destroying general correctness. Reverify after engine changes.
- **RL:** current-policy train-only draws and executable correctness/native
  outcomes; wrong code gets zero, correct fallback retains credit.
- **Repair research:** original + attempt + actual diagnostic + successful
  revision, explicitly a different prompting regime from first-draft SFT.
- **Runtime regressions:** native mismatches and refusal witnesses, retained and
  isolated; never train the model to imitate an engine bug.
- **Aggregate benchmarks:** explicit machine/engine/oracle, repetition statistic,
  denominator, refusal handling, timing scope and capture setting. No individual
  speed filter leaks into SFT admission or GRPO reward.
- **Unselected/unknown evidence:** keep authorized unique context with reason;
  missing intent, unsafe side effects, incomplete observables or unknown rights
  prevents training admission, not proof of worthlessness.

## Capture tier: from the raw log to eval-2-style candidates

Captured programs reach training through one route, and it is not bank v3. They
go into a **separate capture-tier bank** for the round after S4. They are never
appended to v3 and never passed through `training_data.split_cases`. Adding
components there re-ranks every existing one, which would move sealed dev/test
cases and mix the differential-oracle tier with v3's self-consistency tier. They
are not an arm-A input either: a program Claude wrote is not a draw the target
model made.

```bash
./nt capture-export --origin <host>/<log-generation> --output work/capture.jsonl \
    --evidence work/capture-evidence          # reads the log; writes only these two
./nt eval2-select --export work/capture.jsonl --output work/capture-cands.jsonl
./nt eval2-bank --candidates work/capture-cands.jsonl --evidence work/capture-evidence ...
```

- **Export** (`pipeline.capture_export`) reads `$LYPNING_HOME/invocations.jsonl`
  and never writes to it. It runs `harvest.extract_with_tails` over every Bash
  command and keeps the exact program bytes, so `sha256(program)` is the
  `source_sha256` that `eval2_bank` links on. Each row carries the
  `parent_event_id` that `lypning.evidence` gives the log line under the same
  origin, plus the session, the host and the tool call's `ok` when a record of
  it exists. The output files are owner-only.
- **Attribution** uses exact joins only. It reads the append-only
  `attribution.jsonl` journal first, then the transcript's `tool_use` id, then,
  for records written before the id was captured, the byte-identical Bash
  command in that session's transcripts, but only when every match names the
  same model. Anything else is `unknown`, never a time guess.
- **Quality** (`pipeline.capture_quality`) is a static AST verdict that executes
  nothing. The first rule that fails decides the verdict: unparseable,
  repo-local import, process, network, environment, file writes, file or path
  reads, non-stdlib import, stdin glue under 60 nodes, under 40 nodes or no
  def/loop, unseeded clock or randomness, privacy. Privacy covers a `redact`
  hit, an email, a home path or an absolute path. Every rule reads imports as
  they resolve — `import os as o`, `from os import system`,
  `__import__("subprocess")`, `getattr(os, "system")` — and a module imported by
  a non-literal name is never stdlib. Only tier A (no rule failed) is written by
  default. A tier-A program typed with a private argv is charged to privacy on
  that occurrence. `--all` writes every row for an audit, but withholds the
  program and argv of any row whose text trips the privacy rule, whichever rule
  rejected it first. A tool call logged twice under one `tool_use_id` (two hook
  scopes) counts once.
- **Contamination** is decided per program. If a command mentions eval-2, a
  bank, positive-control, completions or `invocations.jsonl`, every program in
  it is tainted, and so is every other occurrence of the same bytes.
- **Selection** folds each distinct program into one record and ranks
  `claude-opus-5-5` first. Other Claude models stay eligible, ranked after it.
  Other hosts, and GPT or Codex model strings, are dropped unless
  `--host`/`--allow-other-vendors` asks for them. The operator made GPT-written
  programs conditional on being materially similar to Claude's. The
  pre-registered test on 2026-09-23 found they are not: all three criteria
  failed ([report](reports/2026-09-23-codex-similarity/README.md)). They stay
  excluded.
- **Execution** happens only in `eval2_select`'s two regeneration runs, and only
  for tier-A rows. `lypning_source.classify_entry` now refuses to run a program
  that spawns processes, opens sockets or writes files (`capture_quality.hazard`).
  `is_tooling` now covers every repo-local module, not just `lypning`. On
  2026-09-22 that changed 0 of the 643 refused rows in `data/classified.jsonl`.
  The execution gate is different: `nt classify` is incremental, so it leaves
  existing rows alone, but re-classified from scratch on 2026-09-22 it would
  skip 99 of those 643 refused rows and 79 of 618 tier-1 rows, and 6 frozen
  held-out cases come from the skipped rows. `nt harvest` would then refuse
  on holdout loss. Do not delete `data/classified.jsonl` to re-run it.

**Measured yield, 2026-09-22** (`capture_export.export` over the live log,
7,997 lines at the time; no journal existed yet, so every model came from a
transcript; re-measured after the resolver, argv-privacy and one-call-one-
occurrence fixes, which is why it differs from the lane's first 7,909-line
figure of 187):

| Stage | Count |
|---|---|
| Bash commands / commands with a program | 7,992 / 4,904 |
| Program occurrences / distinct programs | 5,426 / 5,046 |
| Distinct tier A / tier A and uncontaminated | 204 / 179 |
| Distinct programs tainted by contamination (any tier) | 807 |
| Occurrences attributed by transcript id / by exact command / unknown | 4,734 / 692 / 0 |
| Duplicate tool calls / tier-A occurrences with a private argv | 0 / 8 |

The three largest first-failing rules, counted over occurrences, were file
writes (1,816), file reads (1,320) and repo-local imports (864). The 179
selected programs came from these writers: `claude-opus-5` 154,
`claude-fable-5-1` 14, `claude-opus-5-5` 9 (all ranked first) and
`claude-opus-4-8` 2. The median program is 11 lines. `claude-opus-5-5` typed
282 of the 5,426 occurrences.

At bank v1's author yield (§11 of [EVAL2.md](EVAL2.md): 364 admitted from 691
candidates), 179 candidates come to roughly 95 cases. That is breadth across
families, not volume.

## L capabilities that would help next

Prioritize by distinct reviewed task/source demand and correct-native conversion
potential, not raw repeated log frequency or per-script speed thresholds. Useful
next work includes a versioned machine-readable capability/refusal catalog,
host-owned observation adapters for the native bindings, and observable test
contracts beyond deterministic UTF-8 stdout (file effects, nonzero exits and
binary output) before admitting those tasks. Preserve safe refusal/rollback and
core/L boundaries. Use `docs/L-COVERAGE.md` and `L-TRAINING-ROADMAP.md` for current
runtime boundaries; no new unsupported surface is claimed implemented here.
