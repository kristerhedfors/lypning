# Fable round report — the S0 reads are blocked, and one of them could have lied

## Outcome and decision requested

**Date** 2026-09-17. **Round** the $0 S0 evidence round assigned by
[`reviews/2026-09-17-round02-full-assessment.md`](../reviews/2026-09-17-round02-full-assessment.md)
§"Next bounded action" and specified command-by-command in
[`START_NEXT_ROUND.md`](../START_NEXT_ROUND.md) §"Next Fable session — 2026-09-17".
**Status: blocked.**

**No GPU run occurred. No provider was called. No Space was built or touched.
No dataset was prepared or mutated. No frozen artifact was altered. Nothing was
spent.** No approval was requested, because nothing was run that consumes one.

The round is blocked for the same reason the 2026-09-16 attempt was, on a
different clone: **this device holds none of the round's inputs.** The review
assigns the round to "one $0 session on the private-artifact device"; this is
not that device. All three rungs were attempted verbatim and all three failed to
read anything. §3 names every missing artifact.

What makes this report worth more than a second no-run notice is **what the
third rung did instead of failing.** Run verbatim with both of its private
inputs absent, S0c printed a complete, well-formed, all-zeros table and **exited
0**:

```
probe/base native-status vector (descriptive; no gate moves)
probe rows 0   base rows 0   matched cases 0   probe-only 0   base-only 0

case                                    probe     base  probe statuses                  base statuses
```

That is not a failure mode a careful operator catches by reading the exit code,
because the exit code says success. `jsonio.read_jsonl` answers `[]` for a path
that is not there, so both sides parsed empty, `probe_only` came back empty, and
`return 1 if result["probe_only"] else 0` — the command's only failure signal,
and the one `START_NEXT_ROUND.md` names — had nothing to range over. **A rung
assigned as a $0 read was able to report a clean read of nothing.** Had this
session been the private device and had the download silently landed in the
wrong place, that table is transcribable into a report as an S0c result.

Four more of the same shape were found and fixed on the way — two of them only
because the first fix was tested rather than trusted (§5). The sharpest is
in S0b, and it is worse than a zeros table: **rung S0b's number is not
engine-independent, and a `--engine` that was not a file printed an empty vector
at exit 0 while the considered population silently grew.**

**Decisions requested of Codex** — three, all in §6:

1. Whether the five fail-closed guards and the two documentation corrections in
   §5 are accepted, or whether any of them is a change to a rung's contract that
   Codex wants to make itself. Two of the five change an exit code from 0 to 1
   on an input that read or graded nothing, which is the only behaviour change
   here that a caller could depend on.
2. Whether the S0b engine-identity finding changes the rung's assignment. The
   reviewed "171 correct-but-fallback pilot draws" is a count **at the pilot's
   engine identity**; this tree measured the same population moving from 14 to
   26 draws on one local run purely by changing the binary. If the private
   device cannot build `lypning-l` at `2e079e786a655ab6`, S0b as written is not
   reproducible there either, and that is a prerequisite nobody had written down.
3. Whether the ≥50,000-supervised-token floor should stay a stage gate.
   `START_NEXT_ROUND.md` says to `--plan` before every stage; the plan cannot
   see this gate, and moving it earlier costs `--plan` its no-download contract.
   This report corrects the documents and pins the split rather than moving the
   gate.

**Decision requested of the operator:** nothing. No ceiling is asked for and no
rung is proposed for spend.

## Reproduction and authority

**Repository commit** `8c00fbbc9e30ff6ca9fd32f70e1d968efb180d38`, identical to
`origin/main` at session start; branch `claude/ultracode-next-training-o79ld8`.
Working tree clean before the changes in §5 and after the battery
(`git status --porcelain` empty). **Report version** 1.

**Authority:** none needed, none used. No approved device, time, cost or storage
ceiling was requested. No credential was passed to anything; `HF_TOKEN` is
present in this environment and was not used to fetch any artifact.

**Device.** No `work/` or `runs/` tree at the repository root, and none
elsewhere: a filesystem-wide search for `round-02`, `probe-rollouts.jsonl` and
`eval-20260916-063539` matches only pytest fixtures under `/tmp/pytest-of-root`.
No GPU (`/dev/nvidia*` absent). `huggingface_hub` is not installed and there is
no `~/.cache/huggingface`. PyPI and `huggingface.co` are both reachable through
the session proxy, so the private download is **prohibited, not impossible** —
it is withheld on `START_NEXT_ROUND.md`'s device assignment and its
operator-approval requirement, not on a capability gap. It was not attempted.

**Interpreter.** `python3` is **3.11.15**. The round requires one resolved Python
**3.12** build (`START_NEXT_ROUND.md` §"First session"), so this device could not
have supplied the round's interpreter either.

**Engine identity, built here from the checkout:**

```
engine     target                     bytes    code     blocks  status
lypning    x86_64-unknown-linux-musl  1151184  883399   9       ok
lypning-l  x86_64-unknown-linux-musl  1331408  1025495  11      ok
```

`engines.identity()` → fingerprint `c8b1d4f5d07f3500`, `oracle_python 3.11.15`,
`lypning cb5aaefe15e7558f…`, `lypning-l 0c2c99d11571960c…`, both
`for cpython 3.11`. **The pilot was graded at fingerprint `2e079e786a655ab6`
(`lypning-l a23b3083…`, `EVAL2.md` §11).** These are different engines, which is
a second independent reason S0b could not have been executed faithfully here
even with the rows delivered — see §6.

`lypning` is not on `PATH` on this box (`doctor` says so), so every command below
is the `PYTHONPATH=src python3 -m lypning …` form from `docs/VERIFICATION.md`.
The per-engine build seconds are not quoted as a build: the cargo target tree
was warm.

**Exact commands, as assigned, with their literal outcomes.** Each is
re-runnable in this tree and each was run from the repository root:

```bash
# S0a — exit 1, stderr only, nothing on stdout.
PYTHONPATH=src:training python3 -m pipeline.cli power --eval2 \
  --rows eval-20260916-063539 --draws 16 --mde 0.03
# no rows file at eval-20260916-063539 and no /home/user/lypning/training/runs/
# eval-20260916-063539/eval2_rows.jsonl: write them with `nt eval2-rows
# eval-20260916-063539 --output runs/eval-20260916-063539/eval2_rows.jsonl`

# S0b — exit 1. The engine gate at cli.py:1836-1839 passes (the binary is built
# here); the blocker is the absent private run.
PYTHONPATH=src:training python3 -m pipeline.cli levers \
  --run eval-20260916-063539 --status correct-fallback --vector --limit 0
# no such run: eval-20260916-063539

# S0c — exit 0 and a zeros table, BEFORE the guard in §5. Exit 2, naming the
# path, after it.
PYTHONPATH=src:training python3 -m pipeline.cli probe-vector \
  --probe work/round-02/6aaa87465527934177ee9f34/probe/probe-rollouts.jsonl \
  --base runs/eval-20260916-063539/eval2_rows.jsonl
```

**Missing artifacts, named rather than worked around:**

| artifact | needed by | state here |
|---|---|---|
| `runs/eval-20260916-063539/eval2_rows.jsonl` (pilot draw rows) | S0a, S0c base | absent; private to the other device (`EVAL2.md` §7, §11) |
| `runs/eval-20260916-063539/attempts.jsonl` | S0b, and `nt eval2-rows` | absent |
| `work/round-02/6aaa87465527934177ee9f34/probe/probe-rollouts.jsonl` | S0c probe | absent; private artifact repo |
| `lypning-l` at fingerprint `2e079e786a655ab6` | S0b faithfully | absent; this tree builds `c8b1d4f5d07f3500` |
| eval-2 v1 (300 cases) / train v1 (64 cases) banks | any later rung | private (`EVAL2.md` §11) |
| a Python 3.12 build | the round's interpreter | absent; 3.11.15 here |

**The escape hatch does not apply.** `START_NEXT_ROUND.md` says to materialize
the rows with `nt eval2-rows` if the named file is absent. `cmd_eval2_rows`
resolves an engine, then reads **the same absent** `runs/<id>/attempts.jsonl`,
then `meta.json`, then the case files, then replays. Run on the named id it
exits 1 with `no such run: eval-20260916-063539` and writes nothing. It is a
projection of a completed run, not a source of one.

**Substitution was available and was refused.** The tools do not stop it:
`training/runs/` holds 14 committed run directories, and `power --eval2` accepts
any file path — a synthetic 32-row file produces a complete 40-cell power table
at exit 0. What stops it is the documents, independently and in five places
(`START_NEXT_ROUND.md` :19; `EVAL2.md` §11; `STATUS.md` §10 S0a/S0b cells;
`ORCHESTRATION.md` rows S4 and R4; `reviews/2026-09-17-…` :104-106, "missing
private artifact … stops the relevant rung; none is converted into a model
score"). **No number in this report is a substitute for a private read, and no
local run stands in for the pilot.** Nothing downstream is unblocked by faking
one.

No secret and no private row appears in this writeup.

## Data and hypothesis

**Question the round was to answer** — the three named in `ASSESSMENT.md` §6
step 1: whether §3.4's instrument reading holds on the real pilot rows (S0a);
where the correct-but-fallback headroom lives, by kind (S0b); and whether
round-02's adapter moved native status on its own training prompts (S0c).

**Predicted outcomes, from `ASSESSMENT.md` §5, unchanged and untested:** S0a,
realised macro +5pp at ~50% and +8pp at ~100%; S0b, a handful of kinds carrying
most of the mass; S0c, a stage-1a proxy showing SFT moved native on its own
prompts (§3.1 predicts it did not).

**All three remain untested. This report answers none of them, and no kill
criterion fires.** The step-1 stop rule is literally "none: these are reads",
S0b's kill criterion needs a vector that was never read, and the review's rule
is that a missing private artifact stops the rung without becoming a score. The
three questions are recorded here as still open, not as conclusions.

**Population, task mixture, family/source counts, oracle and rights review,
teacher and repair conditions, split assignment, ordinary versus conditioned
prompts, first drafts versus repaired answers, rejected and quarantined
counts: not applicable.** No case was proposed, graded, admitted or rejected; no
bank was read; no model was called; no bundle was prepared.

**No held-out task influenced anything here.** Nothing was graded, `eval2-leaks`
was not run because no case was proposed for admission, and the reviewed
195/242/206/0-over-643 local lever vector is **not** reproduced here as an S0b
result: the review is explicit that "S0b must still measure the private
deployment population", and that population was not read.

**Two round deliverables were not met, and this is the record of it:** the
re-printed power table was not appended to `EVAL2.md` §7, and the by-kind vector
was not appended to §11 (`ASSESSMENT.md` §6 step 1). Both need the private rows.

## Measurements

**No model was measured. No artifact hash of a private input is quotable,
because no private input was read; the unmatched counts S0c would have printed
do not exist.** They are recorded as "not computable — input absent", never
estimated.

Two things were measured, both on this tree on 2026-09-17, both at commit
`8c00fbb` plus the §5 changes.

### The population moves with the engine (the S0b finding)

The same command, the same local run, the same status filter — only the binary
differs:

| `--engine` | draws matching `correct-fallback` | carried a refusal | vector | exit |
|---|---|---|---|---|
| built `lypning-l` (`c8b1d4f5d07f3500`) | 14 | 14 | populated | 0 |
| a path that is not a file | 26 | 0 | **empty** | **0** |

Run on `runs/stock-nothinking`, 2026-09-17, this tree. The mechanism: the
eval-2 draw rows carry `native` and `status` but **no refusal kind**, so `--run`
replays every program through the local binary to obtain one — and in doing so
re-derives `native`, hence the population. With a broken engine every program
grades `ERROR`, no draw carries a refusal, and `levers`' only census alarm
covered `MISMATCH`, never `ERROR`. The single-line `UNRESOLVED` note on stdout
was the whole signal.

This is the measurement behind decision request 2. It says the reviewed count of
**171** correct-but-fallback pilot draws is a statement about a population *at
an engine identity*, and that rung S0b needs the pilot's binary, not any binary.

### The checklist, run here today

Root `CLAUDE.md` "Before you say you are done", 2026-09-17, commit `8c00fbb`
plus §5, quoted with its own run per invariant 3:

| command | result |
|---|---|
| `lypning build --rust` | exit 0; `ok` for both variants; the exit-90 refusal contract asserted on the binary |
| `lypning conformance` | exit 0, **`MISMATCH 0 — ok`** over 6,316 corpus programs of 9,064 loaded; UNSUPPORTED 3,393 / 1,756 / 0; coverage 46.3% / 72.2% / 100.0%; monotone violations 0; routing UNSAFE 0, NO-ENGINE 0, accuracy 94.6% ideal |
| `lypning doctor` | exit 0; **19 checks, 0 FAIL**, 3 WARN (12 OK + 3 WARN + 4 NOTE) |
| `lypning gate` | **PASS**, core 9 blocks against budget 9 |
| `lypning gate ~/.lypning/bin/lypning-l` | **PASS**, 11 blocks against budget 32 |
| `git status` | clean; the corpus battery restored what it rewrote |
| `pytest tests -q` | exit 0; 8,337 passed, 591 skipped, 3 xfailed, 20 xpassed |
| `pytest training/tests -q` | exit 0; 652 passed, 4 skipped (647 before the five tests added in §5) |

Bare `gate` measures **only** the core, so `CLAUDE.md`'s "every variant inside
its block budget" needs the second invocation; both are shown. The pytest
warning count is deliberately not quoted: it varies with bytecode-cache state
(6 warm, 7 cold) and is not a property of the tree.

**Two things this battery taught about its own numbers, both invariant 3 in
practice.** First, the corpus total was 9,064 in every run, but the run/skip
split moved between two runs on the same tree an hour apart — 6,316 programs
run and 2,748 skipped here, 6,317 and 2,747 earlier — because one program
crossed the timing-dependent `reference timed out after 30s` skip reason. The
per-engine UNSUPPORTED counts move with it (3,393/1,756 here, 3,394/1,757
earlier). Neither is a coverage change and neither number is quotable without
its run.

Second, **the first `conformance` run of this session was a FAIL, and the net
was right.** It was launched on a dirty tree and this session went on editing
documents while it ran, so a corpus program's write to a repository file was
followed by a restore to a snapshot predating those edits: `MISMATCH 0 — FAIL`,
exit 1, `4 repository files changed by corpus programs and have been restored`.
Nothing was lost — the edits were in the index — and the counts above are from a
clean-tree re-run at exit 0 with `git status` empty afterwards. `CLAUDE.md`
invariant 4 says to run the battery only in a worktree with its own
`LYPNING_HOME`; that is a net, not a sandbox, and this is what it looks like
when it fires.

**The oracle is absent, as expected** (`docs/VERIFICATION.md` §C12): `lypning-mp`
is not built here, and all five paths that touch it degrade to `not built` —
`status`, `conformance --engine lypning-mp` ("that arm was not measured"), the
`doctor` WARN row, `overview` (a hole with no numeric column), and
`oracle --json` (`lypning-mp False 79 34`, closing `binary: not built`). Every
one exits 0: a hole, never a zero. Tested by the binary's absence, not by
reasoning about it.

**Cost and resources.** Zero dollars. No provider request, no retry, no
truncation, no token of any kind, no optimizer update, no GPU second. Wall time
is this session's own and is not a measurement of anything.

## Failures, reflections and next experiment

**What did not work: the round, as assigned.** Three rungs, three absent inputs,
on a device the review did not assign it to. That is not a surprise and it cost
nothing; the 2026-09-16 attempt reached the same wall.

**What worked, and is the reason to read this report.** Attempting all three
rungs verbatim on a device that holds none of their inputs is a test the
assigned device will never run, and it found a class of defect the assigned
device would have been exposed to silently. **Five** ways a rung could report a
clean read of nothing, found in two passes:

1. **`probe-vector` (rung S0c)** — both inputs absent, exit 0, zeros table.
2. **`levers --run … --engine <not a file>` (rung S0b)** — empty vector, exit 0,
   population silently grown from 14 to 26.
3. **`levers --rows … --replay …`** with either path absent — empty vector,
   exit 0.

Those three are guarded in the CLI's own existing idiom for a required explicit
path (`if not path.is_file(): print("not a file: %s"); return 2`, as
`cmd_eval2_leaks` and `cmd_eval2_legacy` already do), and the `ERROR` census is
now said on stderr where `MISMATCH` is said.

**Then the first fix turned out to be incomplete, which is worth recording
rather than smoothing over.** Testing that the new `ERROR` line could actually
fire — a guard that never fires being worse than none — exposed two survivors of
the same defect, one step past each guard:

4. **`levers --run … --engine <exists but cannot execute>`** passes `is_file`
   and then reproduces the whole thing: `ERROR 74`, no draw carrying a refusal,
   the empty vector printed, the population still inflated to 26, **exit 0**.
   Guarding absence was never the point; publishing a vector over a replay that
   graded nothing was. Nothing graded now exits 1 and prints no vector.
5. **`probe-vector` over a present-but-empty probe file** — a probe stage that
   produced no rollouts did not run, `probe_only` is empty over it, so the hole
   detector cannot catch this one either. Now exit 1.

Seven tests pin all five, including the first CLI-level test `probe-vector` has
ever had. The exit-1 hole detector `START_NEXT_ROUND.md` names still fires on
real inputs, and the real `--run` path still exits 0 on 14 draws — both verified
after the change, not assumed.

**The lesson generalises past these two commands, and Codex may want it as a
rule:** every one of these five printed a well-formed table. The defect was
never a crash or a wrong number, it was a *shape* — a $0 rung whose absent,
unreadable or ungraded input renders identically to a real read of zero. The
`is_file` guards are the cheap half; the half that mattered was asking, for each
rung, "what does this command do when it measures nothing?"

**Which claim is directly supported, and which is inferred.** Directly
supported, by re-run output quoted above: the three rungs' literal outcomes; the
14-versus-26 population shift; the checklist counts; the oracle-absent
degradation; that the guards now exit 2 and that real inputs still behave as
before. **Inferred:** that the assigned device would in fact have hit the S0c
zeros table — it would have hit it only if a download landed in the wrong place,
which is a plausible operator slip, not an observed one. The defect is real
regardless of whether anyone was about to trip on it.

**Alternative explanation considered and rejected.** That S0c's exit 0 was
deliberate leniency, since `read_jsonl`'s `[]`-for-absent behaviour is relied on
elsewhere (`evaluate.py`'s resume logic reads a not-yet-created `attempts.jsonl`
by design). That is why the fix guards the **two required path arguments in the
command**, and leaves `jsonio.read_jsonl` alone. A second reading — that
`START_NEXT_ROUND.md` is *contradicted* by the zeros table — was checked and
withdrawn: its "a probe case missing from base exits 1" clause is vacuous when
the probe file is absent, not violated. The document was not wrong; the command
had no behaviour for a missing required input.

**One gate audit finding, unfixed on purpose.** Of the nine admission gates the
documents claim the runner enforces, eight are enforced as documented,
re-verified at source and by driving `preflight` directly (k=16; ≥1,000 train
cases; registered seeds; family-cycle capacity; the native-anomaly abort; the
private-destination refusal; the Space-commit pin and per-response identity
re-admission; and the four-by-four verifier capacity, including its refusal of
under-capacity). The ninth is a split, not a hole: the **≥50,000-supervised-token
floor is enforced in `run()`, not in `preflight`**, so `--plan` — which
`START_NEXT_ROUND.md` says to run before every actual stage — accepts a schedule
the stage later refuses. Moving it into `preflight` would cost `--plan` its
stated no-tokenizer, no-download contract, so §5 corrects the two documents and
pins the split instead of moving the gate. **Decision request 3.**

A related residual, flagged and **not** changed: the verifier capacity gate
refuses *under*-capacity but has no **floor**, so
`--score-workers 1 --pool-sandboxes-per-host 1 --pool-max-hosts 1` passes every
check — the one-host contention shape that produced round-02's blocked arm, and
that `START_NEXT_ROUND.md` guards only in prose ("do not collapse them onto one
host"). Adding a minimum changes which launches are permitted, so it is Codex's
call, not this report's.

**Three refusal messages had no test** — the registered seeds, the token floor
and the family cycle. An inverted comparison in the first and third would have
been caught by the existing accept-side assertion; deleting either guard
outright, or corrupting its message, would not. The token floor had no coverage
in either direction. All three are now pinned.

**Next experiment, bounded, unchanged in shape from the review's:** the same $0
S0a–S0c session, on the private-artifact device, now with two prerequisites that
were not written down before this session and one hazard removed.

- **Inputs:** the private pilot rows and probe rollouts; **and** a `lypning-l`
  built to fingerprint `2e079e786a655ab6`, or an explicit note in the report
  that the vector was measured at a different identity.
- **Owner:** Fable, on that device. **Cost:** $0. **Expected benefit:** the three
  answers `ASSESSMENT.md` §6 step 1 names.
- **Stop rule:** unchanged — a missing artifact stops the rung and is never
  converted into a score. Now enforced by the tools as well as the rule: a rung
  whose input is absent exits 2 naming the path, and one whose input read or
  graded nothing exits 1 rather than printing a table over it.
- **What is still not authorized:** S1 or any paid rung, a GPU job, a Space
  rebuild, a dataset mutation, or any edit to a frozen artifact.

## Codex review handoff

**Artifacts Codex can inspect, all in this tree:**

- this report;
- the five guards and the `ERROR` census: `training/pipeline/cli.py`
  (`cmd_probe_vector`, `cmd_levers`);
- the seven pinning tests: `training/tests/test_probe_vector.py`,
  `training/tests/test_levers.py`, `training/tests/test_training.py`;
- the two documentation corrections: `training/START_NEXT_ROUND.md` (the S0b
  engine-identity paragraph, the S0c path, the plan-versus-stage note) and
  `training/STATUS.md` (§10's S0b cell and the preflight/stage split);
- the ledger row in `training/ORCHESTRATION.md`.

**No private artifact is attached, because none was read.**

**Unresolved questions and decisions needed:** the three in §1 — whether the
guards and doc corrections are accepted as Fable's to make; whether the S0b
engine-identity finding changes that rung's assignment or its reviewed count of
171; and whether the token floor stays a stage gate, with the capacity-floor
residual as a fourth, smaller call.

**Codex's independent assessment is deliberately not written here, and should
not be read as implied by anything above.** Ledger row R3 already records the
cost of a review that was not independent of the work it reviewed; this session
authored the changes in §5 and is therefore not the session to rule on them.
