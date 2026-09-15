# Compatibility-first data production and learning loop

The model objective is **correct lypning-l-compatible first drafts**, not a
9×-per-script filter. The user's approximately 10× compatible-workload premise
motivates expanding coverage. Aggregate >9× runtime health is a separate measured
goal, not a claim that every task or every machine has that speedup. No maximum
speedup is imposed. Every incremental correct-compatible gain is useful.

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

## L capabilities that would help next

Prioritize by distinct reviewed task/source demand and correct-native conversion
potential, not raw repeated log frequency or per-script speed thresholds. Useful
next work includes a versioned machine-readable capability/refusal catalog,
host-owned observation adapters for the native bindings, and observable test
contracts beyond deterministic UTF-8 stdout (file effects, nonzero exits and
binary output) before admitting those tasks. Preserve safe refusal/rollback and
core/L boundaries. Use `docs/L-COVERAGE.md` and `L-TRAINING-ROADMAP.md` for current
runtime boundaries; no new unsupported surface is claimed implemented here.
