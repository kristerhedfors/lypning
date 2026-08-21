# CLAUDE.md — working agreement for this repository

Read `README.md` for what the project is. This file is the short list of things
that are easy to break here and expensive to notice.

Every point below is a pointer. The reasoning lives in `docs/` and in the module
docstrings, which are written to be read — each module states the one invariant
it exists to hold. Do not restate them here; go read them and then change the
code.

## The invariants

**1. MISMATCH is always a bug. UNSUPPORTED never is.**
A tier that silently disagrees with CPython is worse than no tier at all,
because the agent that typed the one-liner will not notice. A refusal it will
notice — the answer arrives from CPython one spawn later. So `lypning
conformance` must end at `MISMATCH 0`, and a rising UNSUPPORTED count is a
coverage number and a build order (`--plan`), not a regression. Never "fix" a
MISMATCH by widening a capability table; the table describes what the engine
does, and editing it to describe what you wish it did converts a loud failure
into a silent one. `docs/LYPNING.md` §2, `conformance.py`.

**2. The exit-90 contract, and the stderr/stdout split.**
A refusal is exit `90`, exactly one `<engine>: unsupported: <kind>: <detail>`
line on **stderr**, and **nothing at all on stdout**. Any other non-zero exit is
the program's own and must be returned unchanged — a dispatcher that retried on
exit 1 would run a half-completed program twice. This is the one thing about
lypning that has only ever broken *silently*: a parser change that turns a
refusal into a traceback still compiles, still links, still passes `--version`.
So it is asserted on the binary that was just built, before a build is allowed
to report `ok`. If you touch the refusal path, `lypning build --rust` and
`lypning conformance` are both required, and neither is optional because the
other passed. `engines.py`, `build.py`.

**3. Never quote a remembered corpus size.**
Capture grows it every session, and `lypning collect` grows it again from
repositories that are not this one. Upstream published 420, then 472 within the
day; this tree was written up at 839 and loaded 842 the next morning — which is
why no live number appears in this sentence either. Every tool prints the count
it loaded — quote that number, from that run, with its date. A remembered size
is now stale for two reasons rather than one. The same applies to every timing
in `docs/`: those were measured on the upstream container before extraction.
If a number matters to a claim you are making, re-run it. `bench.py`,
`docs/BENCH-LEDGER.md`.

**4. The corpus rewrites repositories. Run it behind the net.**
These are real programs from real agent sessions, so the corpus is full of
one-liners that edit `src/` and `docs/`. Running the battery is running an
agent's edit history. Every entry gets its own temp cwd (a separate one per
engine, so the second cannot read back what the first wrote), entries naming an
absolute path are skipped rather than run, and the run is bracketed by a `git
status` snapshot that restores and reports anything that changed anyway. That
is a **net, not a sandbox** — it cannot undo a write outside the repository, it
only makes the next occurrence loud. Do not remove it, do not "optimise" the
temp cwd away, and check `git status` yourself after any run that crashed
mid-way. It exists because the first measurement runs upstream rewrote 34
tracked files. `lypning collect` widens whose edit history that is: an imported
program came out of a session in another repository and gets no review here, so
nothing in a fetched tree is ever *executed* on the way in — it is read as data,
with git's own hooks disabled — and it runs behind the same net and no other.
`conformance.py`, `bench.py`, `collect.py`.

**5. Hooks never block and never fail a session.**
Every hook prints `{"continue":true,"suppressOutput":true}` and exits 0 on every
path, including its own failures — malformed event, unwritable log, missing
package, uninstalled engine. There is deliberately **no `permissionDecision`
field**: answering `allow` from a PreToolUse hook would bypass the permission
prompt for every Bash command in the session, which is far more than a capture
harness may do. `PreToolUse` runs before *every* Bash call and almost none are
python, so the no-match path stays fork-free: the shell screen decides first,
and an interpreter is spawned only after it matches. Keep the shell screen
broader than the Python regexes — an over-match costs one wasted spawn, a miss
loses a corpus entry forever. `capture.py`, `assets/claude/hooks/`.

**6. Zero runtime dependencies. stdlib only.**
This package installs into the same environment as whatever the agent is
working on, so a dependency of ours is a version conflict in someone else's
project. The Rust crate has the same rule for a second reason: every linked
crate is bytes in a binary whose cold cost is a step function in 131,072 B
device blocks. `pytest` is dev-only. Python **3.9** is the floor: every module
starts with `from __future__ import annotations`, and no `match`, no runtime
`X | Y`. `pyproject.toml`, `docs/LYPNING.md` §7.

**7. Nothing we write may cost a user something they had.**
`.claude/settings.json` is merged, never overwritten: append only, unrelated
keys and hooks and their order preserved, backed up once to
`settings.json.lypning-backup` and never re-backed-up over. `--dry-run` is real
— it opens files, writes none, and prints the unified diff. Uninstall is the
exact inverse and never deletes the capture log. The shim refuses to overwrite
a `python3` it did not write unless forced, and forcing moves the original
aside. `install.py`, `shim.py`.

**8. Library code does not print.**
Modules return data; `cli.py` renders it and maps outcomes onto exit codes
(`0` ok, `1` this command failed, `2` usage, `90` an engine refusal passed
through untouched). The named reporter functions — `report`, `render`,
`render_plan`, `render_status` — return strings; they do not print either. No
traceback reaches a user unless `LYPNING_DEBUG=1`.

**9. The names.**
Engine strings are exactly `"lypning"`, `"lypning-mp"`, `"cpython"`; env vars
are `LYPNING_*`; the state dir is `~/.lypning` (`$LYPNING_HOME`). In anything
this repository *writes* — prose, code, comments — the two upstream names
appear in exactly one place, the credit paragraph in `README.md`. Nowhere else.
The exemption is captured program TEXT, whatever it happens to contain, from a
session here or in any repository `lypning collect` imports from: that is a
recording, it is never edited, and it now grows by machine on a schedule. An
edited recording is not a recording. `collect.py`.

## Before you say you are done

```bash
lypning build --rust      # the contract is asserted on the binary, not assumed
lypning conformance       # MISMATCH must be 0
lypning doctor            # 0 FAIL
git status                # the corpus runs behind a net, but check it yourself
```

`lypning bench` is a fourth gate and answers a different question — cost, not
correctness. Run it when you changed something that could plausibly be slower,
quote the corpus size it printed, and remember it is deliberately not in CI: a
wall-clock benchmark on a shared runner measures the runner.

## Two shapes that must both keep working

**A source checkout**, where `assets/` is writable, `build_dir()` is the asset
tree, and `cargo build` by hand shares an object cache with `lypning build`.

**A wheel**, where `assets/` is read-only, the crate is copied into
`~/.lypning/build` and built there, and the shell hook scripts may not have
shipped at all — in which case the installer wires the `lypning hook …` CLI
entry points instead, which do the same work one exec later. The wheel path is
the one a `pip` user actually hits and the one nobody tests by accident. Test it
on purpose: `pip install --no-build-isolation .` into a venv, then
`LYPNING_HOME=<tmp> lypning build --rust && lypning status`.

**The MicroPython tier is absent by default** — it needs a 32-bit toolchain and
a network — so every path that touches it must degrade to "not built" and carry
on: a status line, a hole in a table (never a zero), an unmeasured arm with a
note. Test that path by moving the binary aside, not by reasoning about it.
