# Assessment: the round-02 run report, and the two rulings it asks for

Reviewed 2026-09-16 against the merged tree at
`346e59c072233eafaa29c43c67375b41df4c0c32`, which is `origin/main`. Subject:
`reports/2026-09-16-fable-round02-run.md` and the decisions `ORCHESTRATION.md`
ledger row T4 records as owed — the native-timeout policy and pool CPU headroom.

**Who wrote this and what that limits.** This is the session that ran the
2026-09-16 S-ladder device audit
(`reports/2026-09-16-fable-sladder-s0-device-audit.md`). It did **not**
participate in the round-02 run and holds none of its artifacts. It therefore
reviews the run report and **does not review its own report**, which is still
owed an independent assessment by a session that did not write it; the contract
in `ORCHESTRATION.md` says as much in as many words, and this file is not a way
around it.

**What could not be reproduced here.** Everything behind the run: no `work/`
tree, no GPU, no provider key, no pooled sandbox, no bank. Every number below is
either quoted from the report with its run and date, or re-derived from the code
in this tree — and each is marked which.

## Findings: three corrections to the report, re-derived from the tree

**1. The "about 55 minutes per arm" figure is a forward estimate, not an
observation, and the cost arithmetic resting on it is unsupported.** The report
writes it while the base arm was still running ("the SFT and GRPO arms follow,
each about 55 minutes"), and its own later section records that no arm
completed. What was observed is the base arm starting 14:18 and blocking at
14:50 — about 32 minutes for 384 of 1,200 draws. Any headroom proposal costed
against 55 minutes per arm is costed against a number that was never measured.

**2. The report's own proposed fix is no longer sufficient, and the reason is a
gate the report predates.** It proposes running the next arm with fewer score
workers. But since 2026-09-16 `train_verified.preflight` refuses a non-smoke
benchmark eval arm at any `--eval-draws` but
`training_contract.PROTOCOL_EVAL_DRAWS`, which is 16 (re-derived here:
`gpu/train_verified.py`, `pipeline/training_contract.py`; `STATUS.md` §10 and
ledger row S3 record the change). The blocked run used `--eval-draws 4`. A
conforming arm is therefore **four times the draws of the one that blocked**, so
reducing concurrency multiplies an already-longer arm. Cutting workers alone
does not get an eval-2 arm through; it makes it slower. **The next launch needs
more hosts, not fewer workers per host**, and the report's own second option
(four sandboxes per host across four hosts) is the one that survives this.

**3. The plumbing for that option does not exist.** Re-derived here:
`pipeline.training.execution_runner` constructs `HfSandboxPoolRunner` with three
positional arguments and never passes `sandboxes_per_host`, which the runner
accepts and forwards only if truthy; and `max_hosts` appears nowhere in the tree
except the report's own prose. There is no flag, no environment variable and no
bundle field for either knob today. The report reads as though the choice were
available; it is not, and the gap is small but it is code, not a launch
argument. Where it may **not** go: the bundle's `execution` dict is validated
against an exact key set and hashed into the bundle digest, so a pool-shape
field there re-prepares every bundle. The precedent that costs nothing is
`NTX_POOL_TAG`, read from the environment inside the runner.

## What the report does support

The block itself is well evidenced and correctly refused. A native timeout after
a correct oracle raising rather than scoring is the training tree's local
reading of invariant 1 — never teach avoidance of a runtime bug — and the report
does not try to talk its way past it. The per-stage uploads, the nine preserved
attempts including the failures, and the explicit "no arm completed" are the
behaviour `ORCHESTRATION.md` asks for and they are why this review can be
written at all.

## Independent assessment beyond the report

**The null is uninformative, and the report gives the wrong reason.** The report
reads its 0.0pp paired native delta on 7 test cases as a measurement problem.
`ASSESSMENT.md` §3.2 has the better explanation and this review agrees with it:
20 steps over 6,251 supervised tokens on a 27B prior installs style, not a
boundary, and the run's own probe shows exactly that — the adapter did not move
the native rate on the prompts it was trained on, and what it moved was
completion length. An instrument finding was reported where a dosage finding was
available. That matters for the next round: it is an argument for a bigger
training set, not a better test.

**On the native-timeout ruling, this review recommends and deliberately does not
install.** The substantive risk is one the report does not name: if the cause is
starvation rather than the engine, then scoring a timeout as `not-native` makes
the primary endpoint **load-dependent**, and base, SFT and GRPO arms could
differ by scheduling noise alone despite the chunk-seed pairing. That is a worse
failure than an aborted arm, because it is silent. Recommendation: **fix
headroom first and keep the abort.** Revisit only if a witnessed timeout
reproduces on an unloaded host, which is now answerable, because as of this date
a blocked evaluation arm preserves the program that blocked it
(`gpu/verified_evaluation.py`, `blocked_witness`). Changing what `native` means
is pre-registration territory, not plumbing, and nothing here should install it.

**One thing neither the report nor the ledger names.** Rows are appended per
chunk, after the whole chunk is scored, so a blocking draw discards its whole
chunk's rows — which is why the run stopped at exactly 384 = three chunks of
128. The witness now preserves the blocking program; the other draws of that
chunk are still thrown away. Worth fixing before an arm that costs four times as
many draws.

## Decision: hold the next paid launch; two bounded, unpaid actions first

**Hold.** Not because the round-02 work was poor — it was carefully recorded —
but because the standing rule of `STATUS.md` §10 has not been satisfied and the
conforming arm is now four times larger than the one that already failed to
finish.

Bounded next actions, in order:

1. **Read S0a and S0b on the private rows.** Owner: the device that holds
   `eval-20260916-063539`. Inputs: those rows; `nt power --eval2` and, as of
   today, `nt levers --run <run> --status correct-fallback --rank`. Cost: $0. Stop
   rule: none, these are reads.
2. **Add the `sandboxes_per_host` / host-count knob** as an environment-read
   construction argument, with a fake-pool unit test. Owner: Fable. Cost: $0.
   Stop rule: if it cannot be done without touching the bundle's `execution` key
   set or `engine_identity`, stop and report — that price is a re-preparation of
   every bundle and it is not worth paying quietly.

**Not authorised by this review:** any GPU step, any change to `Score.native` or
the eval-2 endpoint, any raise of the bundle timeout (that widens what counts as
native, which is a measurement change wearing a headroom fix's clothes), and any
admission of training data. Those remain the operator's and Codex's.
