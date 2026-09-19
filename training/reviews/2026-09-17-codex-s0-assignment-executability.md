# Codex review — can the pinned S0 assignment be executed at all?

Date: 2026-09-17. Decision: **continue — the S0a–S0c assignment stands and is
still owed, but it could not have been executed as written.** Three of its four
prerequisites and one blocking self-contradiction are fixed here. No paid rung,
GPU job, Space rebuild or dataset mutation is authorised by this review, and
none was performed.

This is not a review of a Fable report; no new report exists. It is the review
of the **assignment itself**, prompted by a third attempt to start the next
training round landing on a substitute clone. Ledger rows R5 and S0 say the
private-device S0a–S0c session is owed and unrun; that remains true after this
review. What changed is that the session, when it happens, will not be spent on
a defect we could have found for free.

Everything below was derived in this worktree on 2026-09-17 at `6044886`. No
conformance battery, no `bench`, no network, no provider call, no GPU job, no
frozen artifact touched. `training/tests` was green before the change (670
passed, 5 skipped, 2026-09-17 at `6044886`).

## 0. Why this review exists: the round was blocked here, for the third time

The assignment names four inputs. In this worktree:

| input | state |
|---|---|
| `training/runs/eval-20260916-063539/attempts.jsonl` | absent |
| `training/runs/eval-20260916-063539/eval2_rows.jsonl` | absent |
| `work/round-02/6aaa87465527934177ee9f34/probe/probe-rollouts.jsonl` | absent (no `work/` tree at all) |
| `$PILOT_LYPNING_L` | unset; the doc carries a placeholder path |

Per `START_NEXT_ROUND.md`, that is a stop: report the missing paths, run no
rung. Substitution was available and refused, as it was on 2026-09-17's first
attempt. **The correct outcome of "next training round" on a machine without
the round's inputs is a blocked round**, and repeating it buys nothing.

What a machine without the artifacts *can* do is read the assignment against
the tree it will run in. That is this review.

## 1. The blocking finding: the assignment contradicted its own stop rule

**Three independent audits converged on the same eleven lines.** The document
said, at :21–22, that an absent input is a stop. It then said, at :72–75, that
an absent `$PILOT_ROWS` should be rebuilt with `nt eval2-rows`.

That second sentence is the confound this entire round was re-assigned to
remove, wearing the other hat. `cmd_eval2_rows` defaulted its engine to
`eng.engine_path("lypning-l") or eng.engine_path("lypning")` — whichever binary
the private device happens to have installed. `eval2_rows.row_for` reads
`native` off that replay and `status` off `native`. So the rows' **population**
is a function of the binary, which is exactly the mechanism that, on
`runs/stock-nothinking`, matched 14 correct-but-fallback draws through the built
engine and 26 through a broken one (`START_NEXT_ROUND.md`, measured 2026-09-17).
That pair is a working engine against one that could not execute, not two
working engines; the two-working-engines case is the same mechanism and has not
been measured here.

The blast radius is asymmetric, and the worse half is the quiet one:

- **S0b would have failed loudly.** `--expect-draws 171` refuses a population
  that does not match, so a re-derived file is caught.
- **S0a would not have.** `power --eval2 --rows` accepts any file that exists.
  It would have printed a confident §7 curve over a population graded by an
  unrecorded engine, to be written up as the re-print of a number `STATUS.md`
  binds to the pilot's identity `2e079e786a655ab6`.

And nothing downstream could have noticed: `eval2_rows.row_for` writes a
verdict and **no identity**, and the one provenance line the command printed
was `eng.identity()["fingerprint"]` — the *installed chain*, which for an
explicit historical `--engine` fingerprints binaries the replay never executed.

This was already recorded as residual defect 2 of
[`2026-09-17-codex-s0-guards-and-engine-identity.md`](2026-09-17-codex-s0-guards-and-engine-identity.md).
It was left open because it looked like a property of a command nobody was
being told to run. The audit's contribution is noticing that the handoff *does*
tell someone to run it, on the one device where it would matter.

**Fixed.** The escape hatch is withdrawn: an absent `$PILOT_ROWS` is a blocked
rung. If the rows must be rebuilt for a later purpose, the document now spells
the engine and says the result is never the 171 and never S0a's input. In code,
`cmd_eval2_rows` refuses an `--engine` that is not a file (exit 2, matching
`levers`) and prints `eng.binary_identity(engine)` — the sha256 and version
line of the bytes that graded the rows.

> A pre-existing test was passing `--engine /bin/true`, which does not exist on
> darwin — so on this host, and on every darwin host that has run it, the test
> was asserting that a non-file engine path sails through to the replay. On
> Linux, where `/bin/true` exists, it was testing what it looks like it tests.
> The test is repaired to use a file it creates itself, which is the same test
> on both.

## 2. The three prerequisites that would have failed before the first rung

**The preflight block was silent and stopped nothing.** Four bare `test -f`
lines in the same fenced block as the rungs. `test` prints nothing on failure
and does not exit, so a pasted block ran all three rungs against whatever the
device did have, and the operator saw the rungs' own errors several lines later
with no line naming the absent prerequisite. The prose two lines above promised
"report the missing path and stop". **Fixed**: the block is a script,
run as `bash s0.sh` rather than pasted — at an interactive prompt `exit` closes
the session and `return` stops nothing, so neither is a guard — and it names the
missing file on stderr and exits. The resolved 3.12 interpreter and the pinned
binary's execute bit are prerequisites on the same footing and fail the same way.

**The rungs were assigned to bare `python3`.** The pinned binary's version line
says `for cpython 3.12`; `refusals.grade_against_engine` grades the replay
against `sys.executable`, so a program whose behaviour differs between 3.12 and
the device's default `python3` grades MISMATCH — or dies before reaching the
construct it would have refused on, which becomes `without_refusal`, which
trips the exit-1 guard. The rung is blocked, the private session is spent, and
the diagnosis is three layers down. **Fixed**: all three rungs run on the
`$ROUND_PYTHON` 3.12 build `NEXT_ROUND.md` already resolves.

**S0c's two columns are not the same measurement.** The probe column requires
every test to pass and every test to be native (`training.py`); the base column
is one projected stdout test (`eval2_legacy.py` keeps "the first test with a
non-empty stdout") replayed once. A case with three tests needs 3/3 on the
probe side and 1/1 on the base side, so the probe column is biased downward
monotonically in tests-per-case. Separately, a `fallback-control` case returns
`correct-control` before the native test is reached, so it is structurally 0/k
on the probe side however native it was. **Documented, not fixed**: the
assignment now carries the caveat and says to read *which* cases move, never by
how much. The code fix — key both sides on the `native` boolean both row shapes
already carry — is left to a session that can test it against the real files.

## 3. Two contamination gates that issued an all-clear over nothing

Commit `dd47bae` closed this defect class in five zero-cost rungs and `6044886`
closed four more routes in `levers`. The sweep found it still open one rung up,
in the two gates that stand between a training bank and an eval number:

- **`leaks --sft`** — whose own comment calls it "the one finding here that must
  stop a training run" — printed "no training target passes a held-out case"
  and exited 0 for a mistyped path, a directory with no `sft.jsonl`, or an
  empty file. `read_jsonl` answers `[]` for a path that is not there.
- **`eval2-leaks`** certified two empty banks as non-overlapping: the `is_file`
  guard covers absence, not emptiness, and an all-filtered bank reads the same.

**Both fixed**, on the invariant-8 split the S0 guards already use: a path typed
on the command line that is not a file is a usage error (2); having compared
nothing is the command failing (1). `--allow` forgives found pairs, not an
absent comparison.

## 4. The launcher dead end, one knob further out

R5/D4 added a per-host density ceiling to stop the `16/16/1` shape. The product
check's advice — "increase `--pool-max-hosts`" — was written to avoid sending a
banked operator to a knob the density ceiling forbids. But `--pool-max-hosts` is
itself capped at four, so above 16 scorers **no knob is available**, and the
advice recreated the same two-message dead end one step out: `--score-workers
32` earns "increase `--pool-max-hosts`", and taking that advice earns "pool cost
ceiling is 4 CPU hosts". The one number that would end it — a banked launch
tops out at 16 scorers — was stated in neither message nor in any document.

**Fixed**: above the ceiling, the refusal names the ceiling. At or below it the
knob is still the right answer and is unchanged, and a smoke — which carries no
pool knob into the job — keeps both halves of the original advice.

## 5. What is found and not fixed

58 raised, 2 refuted on adversarial re-derivation, 56 standing: 3 blocking,
14 high, 28 medium, 11 low. The blocking three are one defect and are closed
here, as are the items in §2–§4. **What follows is a triaged excerpt, not the
56**: the low tier and several documentation nits are not restated. Findings
marked ‡ were adversarially re-derived by a second pass; the rest were
established once. Two items the previous review was carrying are closed by this
change — its residual 2 (`eval2-rows` engine identity) and the `leaks --sft`
half of its residual 5 — as is `STATUS.md`'s account of `levers --run`, which
described the bare form as if it were the one §10's S0b row assigns.

**Still open in the S0 path** (none blocks the assignment; each would degrade
its report):

| what | where | why it matters |
|---|---|---|
| S0a's default delta grid has no cell near a **realised** +5pp macro, which is the unit the sizing rule and S0a's question are defined in | `stats.CLUSTER_DELTAS` | the rung prints a table that brackets the question rather than answering it |
| `smallest N at 80% power` is a hard cut over `--trials 200`, SE ≈ 2.8pp | `cli.py` power renderer | the named N moves on simulation noise; consider `--trials 2000` |
| S0a prints the **simulated** k inside the line describing the pilot, and no rows provenance, draws-read or harness-error-dropped census | `cli.py` power renderer | a reader cannot tell the pilot's k from the simulation's |
| S0a's closing paragraph states the bank-sizing rule as live | `cli.py` power renderer | the bank is frozen at 300 by supply; the curve is sensitivity, not a prescription |
| an absent `--rows` exits 1, not 2, and prints a remediation command that is garbage when `--rows` is a path | `cli.py` `_power_eval2` | disagrees with the exit-code split the sibling rungs were ruled onto |
| `--vector` drops `engine_note`, so a vector taken with lypning unimportable cannot say so | `levers.vector_report` | every closed kind would fall through to declarations, silently |
| S0b's replay can be **context-free**: `_case_context` silently yields no tests for a case id this tree does not know, which the grader's own docstring says systematically understates refusals | `cli.py` `_case_context` | the eval-2 cases live in a separate tree; worth a pre-replay count and an `NTX_ROOT` note |
| S0b executes ~171 model-written programs locally under `sandbox.run_python`, not a container, and `netns_available()` is False on darwin | `sandbox.py` | the assignment calls the round read-only; it is read-only in *effect on artifacts*, not in execution |
| `probe-vector` never checks the rollouts are a complete draw group, though `training_contract.probe_report` has that check and the artifact comes from a job whose arm aborted | `probe_vector.py` | a short draw group reads as a low native rate |
| a present-but-empty `--base` is unguarded where `--probe` is guarded | `cli.py` `cmd_probe_vector` | prints the full table with every base cell `-`, reported as "probe-only IDs" |

**Still open in the wider sweep**: `refusals`/`usable` score an engine that
cannot execute as one that accepts everything ‡ (`probe()` swallows `OSError`
and `SubprocessError` and both consumers read `None` as "accepted"); `slices`,
bare `levers`, and `refusals --run <absent>` each render a header over nothing
at exit 0; `power --eval2` tracebacks on a wrong-schema JSONL rather than
refusing. `--require-engine-sha256` pins the *resolved* engine rather than
requiring `--engine` to be explicit — harmless in the assigned command, which
always passes it, but the flag's help claims otherwise.

**Still open in the documents**: the k=16 gate is the *last* thing a metered job
checks ‡ — `launch.py` accepts any `--eval-draws` for a banked stage and
`round02_pilot.sh` plans only the SFT stage, so a non-16 k is refused after the
bank download, bundle preparation, SFT, probe and GRPO; S1 has three different
definitions across `LADDER.md`, `ASSESSMENT.md` §5 and `STATUS.md` §4, only one
of which declares itself history; `STATUS.md` §7 states an ordering without the
banner §10 says every superseded ordering carries; "one pool host serves 50" justifies `SCORE_WORKERS=16` in the two
files an operator reads, is unsourced, and is contradicted by D4.

The k=16 one is the cheapest to close and the most expensive to leave: adding
an `eval --plan` beside the SFT plan in step 7a, or the `PROTOCOL_EVAL_DRAWS`
predicate to `launch.py`, moves that refusal from the end of a paid round to
before it.

## 6. Ruling

**Continue.** Unchanged: Fable runs S0a–S0c on the private-artifact device
using the command block in `START_NEXT_ROUND.md`, preserves the engine SHA line
and unmatched counts, writes one report from `FABLE_REPORT_TEMPLATE.md`, and
stops. No paid rung, GPU job, Space rebuild or dataset mutation is authorised.

Changed, and the reason the round should not be attempted again before reading
this: the block now stops on a missing prerequisite instead of running three
rungs against whatever is present, runs on the 3.12 oracle the pinned binary
was built against, and no longer offers to rebuild the frozen population with
an unpinned engine.

**Stop criteria are the ones already ruled.** Additionally: if `$PILOT_ROWS` is
absent on the private device, that is a blocked round and an artifact-transfer
problem — it is not a file to rebuild.

## 7. This review's own change, reviewed

The change was adversarially reviewed the same day: no blocking finding, two
high and several medium, **all of them introduced by this change** rather than
pre-existing, and all fixed before the changelog entry was written. The two that
mattered: the replacement rebuild command could not run as written — the
positional is a run id, not a path, and `--output` is required — and §5 of this
document had listed as still-open a `STATUS.md` defect the same change fixed.

Four further code fixes came out of it. `eval2-rows` now refuses a replay in
which any program failed to grade: `is_file` rejects a path but cannot reject a
*regular file that will not execute*, and a hand-transferred binary losing its
execute bit writes an all-`correct-fallback` population at exit 0, which is the
original defect one step out. `leaks --sft` counts programs built rather than
lines read, because a bundle's `train-sft.jsonl` carries `messages` and no
`program`, so every row is dropped before execution and the empty result reads
as an all-clear. `power --eval2` no longer advertises the withdrawn rebuild for
a path the caller typed. And the launcher names the knob with remaining
headroom rather than the knob that matches the stage — `16/1/4` was still a dead
end, because hosts were already at their ceiling and the knob that reached was
the suppressed half.

**Owner of the residue:** Codex. The §5 table is the queue, ordered by whether
it degrades the S0 report. None of it is a precondition for the S0 session.
