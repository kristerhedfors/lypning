# CLAUDE.md — working agreement for this repository

Read `README.md` for what the project is. This file is the short list of things
that are easy to break here and expensive to notice.

Every claim has one home: this file states the **rule**, one design document
states the **mechanism**, and `docs/VERIFICATION.md` states the **check** — the
command, its expected output, and the test that pins it. The first sentence of
every section is the fact; justification is one sentence and comes second.
Each module docstring states the one invariant it exists to hold; do not
restate it here — read it, then change the code.

## The invariants

**1. MISMATCH is always a bug. UNSUPPORTED never is.**
`lypning conformance` must end at `MISMATCH 0`; a rising UNSUPPORTED count is
coverage and a build order (`--plan`), not a regression. Never "fix" a MISMATCH
by widening a capability table; the table describes what the engine does, and
editing it to describe what you wish it did converts a loud failure into a
silent one. `conformance.classify`, `docs/LYPNING.md` §2; `docs/VERIFICATION.md`
§C3.

**2. The exit-90 contract, and the stderr/stdout split.**
A refusal is exit `90`, exactly one `<engine>: unsupported: <kind>: <detail>`
line on **stderr**, and **nothing at all on stdout**. Any other non-zero exit is
the program's own and must be returned unchanged — a dispatcher that retried on
exit 1 would run a half-completed program twice. `build.check_refusal_contract`
asserts it on every binary before `ok`; a change to the refusal path needs both
`lypning build --rust` and `lypning conformance`. `docs/VERIFICATION.md` §C1–C2.

**3. Never quote a remembered corpus size.**
Capture grows the corpus every session. Every tool prints the count it loaded;
quote that number with the run's date, and give every timing in `docs/` its run
and date the same way — re-run before relying on one. One carve-out: a number
measured upstream that cannot be re-run from this tree is kept, once per
document, under `measured upstream on <date>; not reproducible from this tree`.
`bench.py`, `docs/BENCH-LEDGER.md`.

**4. The corpus rewrites repositories. Run it behind the net.**
Running the battery is running an agent's edit history, so every entry runs in
its own temp cwd, entries naming an absolute path or running this battery are
skipped, and a `git status` snapshot restores and reports what changed anyway.
That is a **net, not a sandbox** — it cannot undo a write outside the
repository, it only makes the next occurrence loud. Run a battery only in a
worktree with its own `LYPNING_HOME`, and check `git status` yourself after any
run that crashed mid-way. `conformance.py`; `docs/VERIFICATION.md` §C8.

**5. Hooks never block and never fail a session.**
Every hook prints `{"continue":true,"suppressOutput":true}` and exits 0 on every
path, including its own failures. There is deliberately **no
`permissionDecision` field**: answering `allow` from a PreToolUse hook would
bypass the permission prompt for every Bash command in the session, which is
far more than a capture harness may do. Keep the shell screen broader than the
Python regexes — an over-match costs one wasted spawn, a miss loses a corpus
entry forever.
`capture.py`, `assets/claude/hooks/`; `docs/VERIFICATION.md` §C9.

**6. Zero runtime dependencies. stdlib only.**
This package lands in the same environment as the agent's own project, so a
dependency of ours is a version conflict in someone else's project:
`pyproject.toml` says `dependencies = []`, `requires-python = ">=3.9"`. The Rust
crate has the same rule for a second reason: every linked crate is bytes in a
binary whose cold cost is a step function in 131,072 B (`gate.DEVICE_BLOCK`)
blocks. `pytest` is dev-only. Python **3.9** is the floor: `from __future__
import annotations` in every module, no `match`, no runtime `X | Y`.

**7. Nothing we write may cost a user something they had.**
`.claude/settings.json` is merged, never overwritten: append only, unrelated
keys and hooks and their order preserved, backed up once to
`settings.json.lypning-backup` and never re-backed-up over. `--dry-run` is real
— it opens files, writes none, and prints the unified diff. Uninstall is the
exact inverse and never deletes the capture log. The shim refuses to overwrite
a `python3` it did not write unless forced, and forcing moves the original
aside. `install.py`, `shim.py`; `docs/VERIFICATION.md` §C10.

**8. Library code does not print.**
Modules return data; `cli.py` renders it and maps outcomes onto exit codes
(`0` ok, `1` this command failed, `2` usage — including `engines.EngineError`,
a pinned binary that is not one — `90` an engine refusal passed through
untouched, `130` interrupted). The named reporter functions — `report`,
`render`, `render_plan`, `render_status` — return strings; they do not print
either. No traceback reaches a user unless `LYPNING_DEBUG=1`.

**9. The names.**
Engine strings are exactly the members of `engines.ENGINE_ORDER`: the Rust
**spectrum** in `engines.SPECTRUM`, cheapest first — `"lypning"`, the core,
frozen at 8 blocks (`gate.VARIANT_BLOCK_BUDGET`), and `"lypning-l"`, budgeted 32
blocks, where every new capability goes — then `"cpython"`. A variant's name is
`lypning` plus one lowercase letter from the closed set `engines.py` spells
(`l`; never `m`, which reads as `-mp`), ahead of any install-target suffix
(`lypning-l-i686`; `engines.parse_binary_name` is the one reader of the shape).
`"lypning-mp"` is a name but not a rung: the **oracle** — measured, never routed
to (`engines.ORACLES`, `lypning oracle`, `CHANGELOG.md` #38). Each variant
writes its **own** name at the head of its refusal line, through
`engines.refusal_line`, never a literal. `engines.env_var_for` spells the
`LYPNING_*` variable that pins each binary (`LYPNING_BIN`, `LYPNING_L_BIN`,
`LYPNING_MP_BIN`, `LYPNING_CPYTHON`); the state dir is `$LYPNING_HOME`, default
`~/.lypning`. No engine name is spelled by hand outside `engines.py`:
`tests/test_engines.py::test_no_engine_name_is_spelled_by_hand_outside_engines_py`
holds the `lypning-mp` literal, and review holds the rest. The two upstream
names appear in exactly three places: the credit paragraph in `README.md`, the
*Before the name* section of `CHANGELOG.md`, and the corpus JSONL files under
`assets/corpus/`, whose programs are captured verbatim and are not ours to
edit. Nowhere else, including comments — and never as a live identifier, which
is the half that matters: a name that still resolves to something is a name
that can drift back into the code. `docs/VERIFICATION.md` §C15.

**10. Two dispatchers, one answer.**
`route.rs` and `engines.dispatch` decide in lockstep: `lypning conformance
--mixture both` must print `dispatchers agree N/N`, a router never sends a
program to a variant smaller than itself (the floor rule, `route.rs` verdicts),
and a program `lypning` answers `lypning-l` must answer (`monotone violations
0`). `lypning routes` is a **write-only** ledger: nothing on the routing path
reads it, only `engines.dispatch` writes it, and every count it renders is a
floor. The routing report's `accuracy` line is a census, not a cost model: it
weights LATE and WASTED equally, and a LATE costs a CPython spawn where a WASTED
costs an in-process parse (measured 2026-09-04, `CHANGELOG.md` #42). UNSAFE
must be 0. `routing.py`, `routes.py`; `docs/VERIFICATION.md` §C4–C5, §C11.

## Before you say you are done

```bash
lypning build --rust      # the contract is asserted on the binary, not assumed
lypning conformance       # MISMATCH must be 0
lypning doctor            # 0 FAIL
git status                # the corpus runs behind a net, but check it yourself
lypning gate              # PASS — every variant inside its block budget
uv run --with pytest pytest tests -q   # no failure that was not already there
```

`lypning bench` answers a different question — cost, not correctness. Run it
when a change could plausibly be slower, quote the corpus size it printed, and
remember it is deliberately not in CI: a wall-clock benchmark on a shared
runner measures the runner.

**And add the changelog entry.** One per pull request, at the top of
`CHANGELOG.md` under `## Unreleased`: a dated line, a title, a PR link, at most
a handful of bullets; the reasoning stays in the PR body. It is published at
`lypning.dev/changelog.html`; numbers follow invariant 3 or are left out.

## Two shapes that must both keep working

**A source checkout**, where `assets/` is writable, `paths.build_dir()` is the
asset tree, and `cargo build` by hand shares an object cache with `lypning
build`.

**A wheel**, where `assets/` is read-only, the crate is built in
`~/.lypning/build`, and when the hook scripts did not ship the installer wires
the `lypning hook …` CLI entry points instead, which do the same work one exec
later. Nobody hits this path by accident; test it on purpose — `pip install
--no-build-isolation .` into a venv, then `LYPNING_HOME=<tmp> lypning build
--rust && lypning status` (`tests/test_packaging.py`; `docs/VERIFICATION.md`
§C13).

**The oracle is absent by default.** `lypning-mp` needs a 32-bit toolchain and
a network (`lypning build --micropython`), so every path that touches it must
degrade to `not built` and carry on: a status line, a hole in a table (never a
zero), an unmeasured arm with a note. Test that path by moving the binary aside,
not by reasoning about it. `LYPNING_MP_BIN` pins it; `lypning oracle` renders
the divergence catalogue without it. `docs/VERIFICATION.md` §C12.
