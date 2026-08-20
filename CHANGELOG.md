# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **`lypning` no longer execs into its own console script.** `find_lypning()`
  accepted any executable named `lypning` on `$PATH`, and after
  `pip install lypning` that is this package's entry point. With the Rust core
  not built, `lypning -c PROG` and `lypning route` therefore exec'd into
  themselves and hung forever with no output, and `lypning status` reported a
  291-byte shell script as the engine. Engine discovery now takes only compiled
  binaries — a `#!` header disqualifies a candidate — so an unbuilt tree reports
  `not built` and routes to CPython, which is what `README.md` §2 always said it
  did.
- **`$LYPNING_BIN`, `$LYPNING_MP_BIN` and `$LYPNING_CPYTHON` are no longer
  ignored when they point at nothing.** A bad override fell through to ordinary
  discovery, so the run measured a binary the caller did not name and reported
  the number as if it had. Each now fails with one line naming the variable and
  the fix, exit 2.
- **`lypning build --micropython` produces a binary again.** The build stage
  handed `build-micropython.sh` its engine tree under the name `lypning-mp`,
  which the script never looks for — it derives `$REPO_ROOT/micropython` from
  its own location — so every build died at `no patches in
  micropython/variant/patches` before the toolchain or the network was
  exercised.
- **A corpus file that is not JSON is no longer silent.** `corpus.load()`
  skipped undecodable lines without saying so, so a corrupted `corpus.jsonl`
  loaded as zero programs and `status`, `doctor`, `bench` and `conformance` all
  reported a count that was quietly wrong. Unreadable files and undecodable
  lines are now named; `lypning doctor` FAILs on them.
- **A capture log that exists but cannot be used is reported.** A log path that
  is a directory, or is unreadable, read as `not created yet` in `status` and
  `OK` in `doctor` — the same output as a fresh install, for a state where
  capture can never write.
- **`lypning bench --startup` shows a row for every arm.** Arms that are not
  built vanished from the startup table entirely, against `bench --help`'s own
  rule that a missing arm is a hole and never a zero. `bench.render` had it
  right for the corpus half; the startup-only renderer did not.
- **`lypning install --dry-run` no longer counts warnings as "already in
  place".** A PATH warning, an unparseable `settings.json`, a missing skill
  source — all skips, all summarised as though the install were already done.
  They are now counted and labelled as warnings.
- **`lypning build --dry-run` prints the commands**, as `README.md` §2 and
  `--help` both said it did; they were only shown under `-v`.
- **`lypning doctor` reports a `settings.json` that does not parse**, which is
  why the hooks are not wired and is a different fix from running `lypning
  install`.
- **`lypning install` says when the project root came from the current
  directory** rather than from a git work tree, and `--project`'s help now
  mentions that fallback.
- **`lypning build` names the target it installed.** `--target host` (the
  dynamically linked control) and `--target i686` land under the same name as
  the default musl build, so a control built once was silently the engine every
  route used afterwards.
- **`lypning build --help` no longer advertises a `--clean` flag** that does not
  exist.

### Changed

- **`docs/` and the shipped skill now describe this package's commands.** The
  extraction left behind `npm run lypning:*`, `node lypning …`, `bash
  scripts/build-rust.sh`, `lypning shim install --status`, `lypning harvest
  --no-sightings`, a `LYPNING_CORPUS` variable nothing reads, and paths under
  `tests/corpus/` and `~/.local/bin` — none of which work here. Commands that
  belong to the upstream project and did not come across (the Playwright
  sandbox battery, the VM harness, the variant-vs-stock micro-benchmark) are now
  marked as upstream-only rather than presented as things to run.
- **The `lypning` skill's frontmatter describes when to load it.** It described
  a repository layout that does not exist in a project the skill is installed
  into, so it would not have triggered where it is useful.

## [0.1.0] — 2026-08-20

First release. Extracted from
[github.com/kristerhedfors/deepresearch.se](https://github.com/kristerhedfors/deepresearch.se),
where the two runtimes were built to make Python affordable inside that
project's in-browser CheerpX sandbox, and where they were entangled with its
npm scripts, its `tests/corpus/`, its `.claude/` wiring and its shell scripts.
This release is that work as a standalone, installable Python package.

### Added

- **The three tiers, and the router between them.** The Rust core (a
  from-scratch Python subset, zero crates), the MicroPython variant with its
  frozen shim stdlib, and the real CPython — with a classifier that asks the
  Rust core's own parser which tier can take a program, and a dispatcher that
  falls onward on exit `90` and on nothing else.
- **`lypning` as an interpreter.** `lypning -c PROG`, `lypning FILE` and
  `lypning -` `exec` straight into the Rust core, decided before argument
  parsing, so anything that calls `python3` can call this instead.
- **A CLI around it**: `run`, `route`, `build`, `status`, `doctor`, `install`,
  `uninstall`, `shim`, `hook`, `conformance`, `bench`, `gate`, `harvest`,
  `corpus`. Every one takes `--json`.
- **`lypning build`** — replaces the upstream `scripts/build-*.sh` invocations.
  Builds each tier independently, never stopping on the other's failure, and
  refuses to call a build `ok` until the refusal contract (exit 90, one line on
  stderr, clean stdout) holds on the binary it just produced.
- **`lypning install`** — replaces hand-editing `.claude/settings.json`. Merges
  three hook entries rather than overwriting, backs the file up once, ships the
  skill and the hook scripts, installs the `python`/`python3` capture shim, and
  prints the whole plan plus a unified diff under `--dry-run`.
  `lypning uninstall` is its exact inverse and never deletes the capture log.
- **The corpus**, 839 harvested and seeded programs, moved from the upstream
  repository's `tests/corpus/` into package assets, with `lypning corpus
  --stats` to describe it and `lypning harvest` to grow it.
- **The measurement tooling**: `conformance` (MATCH / UNSUPPORTED / MISMATCH,
  plus `--plan`), `bench` (four arms, interleaved, min of repeats, both the
  shared-subset and whole-corpus totals), and `gate` (static? bytes? file
  opens?).
- **Documentation**: `README.md`, this changelog, `CLAUDE.md` (the working
  agreement for an agent working *on* this repository), a `Makefile` of thin
  CLI wrappers, and the eight design documents under `docs/`.

### Changed

- **Renamed throughout.** The Rust subset and the MicroPython variant are now
  `lypning` and `lypning-mp`; environment variables are `LYPNING_*`; the state
  directory is `~/.lypning` (`$LYPNING_HOME`). The former names survive only in
  the credit paragraph of `README.md` and inside the historical corpus JSONL,
  whose captured programs are left verbatim.
- **Ported from Node to Python.** The upstream `npm run` entry points became
  subcommands of one CLI with no third-party runtime dependencies at all — this
  package installs alongside whatever the agent is working on, and a dependency
  of ours would be a version conflict in someone else's project.
- **Split assets from state.** Package assets (crate source, MicroPython
  variant, corpus, skill, hooks, shim) are read-only inside the wheel; built
  binaries, the capture log and build trees live in `~/.lypning`, so nothing
  needs write access to `site-packages`.

### Known gaps

- **The measured tables in `docs/` predate the extraction.** They were taken on
  the upstream container (2026-08-16, 472 programs) and are quoted as the
  argument for the project, not as a claim about your machine. Re-running
  `lypning bench` here on 2026-08-20 over 760 measured programs reproduced the
  ordering and moved every absolute number. Re-measure; do not cite.
- **`lypning-mp` breaks the refusal contract on 2 of 763 corpus programs**
  (measured 2026-08-20 on a binary from `lypning build --micropython` here): it
  refuses with exit 90 *after* 54 and 147 bytes have already reached stdout,
  where the contract is exit 90, one line on stderr and **nothing at all on
  stdout**. Under `README.md` §5 that is a MISMATCH and therefore a bug, tracked
  rather than waived: lypning-mp has no commit barrier, so a program that prints
  several lines before hitting an unsupported construct has already written
  them. The mixture still answers 763/763, because the dispatcher treats exit 90
  as a refusal — but a caller reading only stdout sees a truncated answer, which
  is exactly the silent-disagreement failure the contract exists to prevent.
  `lypning conformance` names the two programs on every run.
- **Not published to PyPI yet.** `pip install lypning` is the intended install
  and does not resolve today; install from a checkout meanwhile.

[Unreleased]: https://github.com/kristerhedfors/lypning/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kristerhedfors/lypning/releases/tag/v0.1.0
