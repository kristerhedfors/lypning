# CLAUDE.md — working agreement for this repository

`README.md` says what the project is; this is the short list of things that are
easy to break here and expensive to notice. Every claim has one home: this file
states the **rule**, one design document states the **mechanism**, and
`docs/VERIFICATION.md` states the **check** — command, expected output, pinning
test. The first sentence of every section is the fact; justification is one
sentence and comes second. Read the module docstring; do not restate it here.

## The invariants

**1. MISMATCH is always a bug. UNSUPPORTED never is.**
`lypning conformance` must end at `MISMATCH 0`; a rising UNSUPPORTED count is
coverage and a build order (`--plan`), not a regression. Never "fix" a MISMATCH
by widening a capability table; the table describes what the engine does, and
editing it to describe what you wish it did converts a loud failure into a
silent one. `conformance.classify`; `docs/VERIFICATION.md` §C3.

**2. The exit-90 contract, and the stderr/stdout split.**
A refusal is exit `90`, exactly one `<engine>: unsupported: <kind>: <detail>`
line on **stderr**, and **nothing at all on stdout**. Any other non-zero exit is
the program's own and must be returned unchanged — a dispatcher that retried on
exit 1 would run a half-completed program twice. `build.check_refusal_contract`
asserts it before `ok`; a change to the refusal path needs both `lypning build
--rust` and `lypning conformance`. `main.rs`; `docs/VERIFICATION.md` §C1–C2.

**3. Never quote a remembered corpus size.**
Capture grows the corpus every session: every tool prints the count it loaded,
so quote that number with its date, give every timing in `docs/` its run and
date the same way, and re-run before relying on one. Carve-out: a number
measured upstream that cannot be re-run here is kept, once per document, under
`measured upstream on <date>; not reproducible from this tree`. `bench.py`.

**4. The corpus rewrites repositories. Run it behind the net.**
Running the battery is running an agent's edit history, so each entry gets its
own temp cwd and a `git status` bracket restores and reports what changed. That
is a **net, not a sandbox** — it cannot undo a write outside the repository, it
only makes the next occurrence loud. Run it only in a worktree with its own
`LYPNING_HOME`. `conformance.py`; `docs/VERIFICATION.md` §C8.

**5. Hooks never block and never fail a session.**
Every hook prints `{"continue":true,"suppressOutput":true}`, exit 0, always.
There is deliberately **no `permissionDecision` field**: answering `allow` from
a PreToolUse hook would bypass the permission prompt for every Bash command in
the session, which is far more than a capture harness may do. Keep the shell
screen broader than the Python regexes — an over-match costs one wasted spawn, a
miss loses a corpus entry forever. `capture.py`; `docs/VERIFICATION.md` §C9.

**6. Zero runtime dependencies. stdlib only.**
`pyproject.toml` says `dependencies = []`, `requires-python = ">=3.9"`: this
package lands in the agent's own environment, so our dependency is their version
conflict. The crate too — every linked crate is bytes in a binary whose cold
cost is a step function in 131,072 B (`gate.DEVICE_BLOCK`) blocks. Every module
starts `from __future__ import annotations`; no `match`, no runtime `X | Y`.
`Cargo.toml`; `docs/VERIFICATION.md` §C6.

**7. Nothing we write may cost a user something they had.**
`.claude/settings.json` is merged, never overwritten: append only, unrelated
keys and hooks kept in order, backed up once to `settings.json.lypning-backup`
and never re-backed-up over. `--dry-run` opens files, writes none, and prints
the unified diff. Uninstall is the exact inverse and never deletes the capture
log. The shim refuses to overwrite a `python3` it did not write unless forced;
forcing moves it aside. `install.py`, `shim.py`; `docs/VERIFICATION.md` §C10.

**8. Library code does not print.**
Modules return data; `cli.py` renders it and maps outcomes onto exit codes
(`0` ok, `1` this command failed, `2` usage — including `engines.EngineError`,
a pinned binary that is not one — `90` an engine refusal passed through
untouched, `130` interrupted). The named reporters return strings too. No
traceback reaches a user unless `LYPNING_DEBUG=1`. `docs/VERIFICATION.md` §15.

**9. The names.**
Engine strings are `engines.ENGINE_ORDER`; `lypning-mp` is the oracle —
measured, never routed to. Each variant writes its **own** name at the head of
its refusal line, through `engines.refusal_line`, never a literal. Upstream
names: `README.md` §8, `CHANGELOG.md` *Before the name*, the corpus JSONL.
Nowhere else, including comments — and never as a live identifier, which is the
half that matters: a name that still resolves to something is a name that can
drift back into the code. `engines.py`; `docs/VERIFICATION.md` §C15.

**10. Two dispatchers, one answer.**
`route.rs` and `engines.dispatch` decide in lockstep: `lypning conformance
--mixture both` prints `dispatchers agree N/N`, a router never routes below
itself, and what `lypning` answers `lypning-l` answers. `accuracy` is a census,
not a cost model: a LATE costs a CPython spawn, a WASTED an in-process parse.
`lypning routes` is write-only — only `engines.dispatch` writes it, nothing that
routes reads it. `routing.py`; `docs/VERIFICATION.md` §C4–C5, §C11.

## Before you say you are done

```bash
lypning build --rust      # the contract is asserted on the binary, not assumed
lypning conformance       # MISMATCH must be 0
lypning doctor            # 0 FAIL
git status                # the corpus runs behind a net, but check it yourself
lypning gate              # PASS — every variant inside its block budget
uv run --with pytest pytest tests -q   # no failure that was not already there
```

`lypning bench` answers cost, not correctness, and is deliberately not in CI: a
wall-clock benchmark on a shared runner measures the runner. **And add the
changelog entry** — one per PR, at the top of `CHANGELOG.md` under `##
Unreleased`: a dated line, a title, a PR link, a handful of bullets; reasoning
stays in the PR body, and every number follows invariant 3 or is left out.

**Two shapes must both keep working.** A source checkout, where `assets/` is
writable and `paths.build_dir()` is the asset tree; and a wheel, where `assets/`
is read-only, the crate builds in `~/.lypning/build`, and the installer wires
the `lypning hook …` CLI entry points if the hook scripts did not ship. Test the
wheel on purpose: `tests/test_packaging.py`, `docs/VERIFICATION.md` §C13.

**The oracle is absent by default** (`docs/VERIFICATION.md` §C12): `lypning-mp`
needs a 32-bit toolchain, so every path touching it degrades to `not built` — a
status line, a hole in a table (never a zero), an unmeasured arm with a note.
Test that path by moving the binary aside, not by reasoning about it.
