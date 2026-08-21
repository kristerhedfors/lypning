# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed — the documentation leads with a measurement taken today, not one remembered from upstream

`README.md` and `docs/LYPNING.md` both opened on the upstream table of
2026-08-16 and its headline bullet, *lypning is the fastest engine on the work
it accepts*. That bullet had already failed to reproduce twice, and the
correction was sixty lines below the claim. Both documents now open on a run
taken in this tree — `lypning bench --startup-repeat 15 --repeat 3` on
2026-08-21, 4 CPUs, 842 programs loaded and 763 measured — with the upstream
table kept underneath as history and the reversal stated where the claim used
to be. `docs/BENCH-LEDGER.md` carries the same run as a dated entry, marked as
`lypning bench` rather than the variant-vs-stock harness the entries below it
come from.

What that run says: the mixture answers **763 of 763 at 0.340x of CPython**, a
66.0% saving, and `lypning conformance` on the same tree grades it 763 / 0 / 0
with the two known lypning-mp MISMATCHes unchanged. What it does not say is
anything about the ordering of the two subset engines, which has now reversed on
every machine outside the one it was first measured on.

### Added — a logo, and a picture of where a program actually goes

- **A thundercloud** — `docs/logo.svg`, in the README and in the site's hero.
  The name came from lightning, and the mark says so without a word of prose.
  It is theme-aware and mid-toned enough to survive a page whose theme its own
  media query cannot see.
- **The dataflow, drawn.** `README.md` and `docs/LYPNING.md` §5 now carry the
  same diagram: shim or hook, then the classifier, then the three tiers with the
  signal that moves a program from each to the next, annotated with where the
  classifier actually sent the 763 programs (64.1% / 24.9% / 11.0%). It is the
  one thing about the design that a paragraph explains badly and a picture
  explains at a glance.

### Fixed — every `docs/*.html` link on the landing page pointed at a GitHub 404

`site/build.py` resolved links relative to the repository, so the landing page's
own site-relative targets — `docs/lypning.html` and the eight documentation
cards — were rewritten to `github.com/.../blob/main/docs/lypning.html`, a file
that does not exist in the repository. `--check` could not see it: it skips
absolute URLs. Targets that already end in `.html` are now left alone, `src` is
rewritten alongside `href` so an image can be referenced by its repository path,
and the link check covers `src` too — a missing image now fails the build rather
than appearing as a broken image on the page. The site's Embedding page, which
existed but was reachable only from the nav, is now in the documentation grid.

### Added — lypning as a library, for harnesses that would rather link than spawn

The runtime is now buildable as a C ABI (`lypning build --lib` →
`liblypning.so`, `liblypning.a`, and the headers), so a coding harness can run a
program **in its own process**: no fork, no exec, no pipe, no serialisation. On
the programs lypning accepts that removes the process, and the process was 96%
of what a one-liner cost. Measured here on 2026-08-20, `pass` costs 0.0071 ms
through the library against 0.2547 ms spawned and 11.12 ms under CPython — and a
second run an hour later reproduced the first two to a tenth of a percent while
CPython's own number moved to 16.6 ms. The method and the caveats are in
`docs/EMBEDDING.md` §2, and none of it is comparable to `lypning bench`'s
numbers, because it is not the same instrument.

The crate is now lib + bin. `main.rs` is one consumer and `capi.rs` is another,
both running programs through the same `embed::run`, because a second
implementation of the refusal contract is how a MISMATCH reaches a release.

- **The exit-90 contract, translated.** A library has no exit code and must not
  touch the host's stderr, so each of the three parts is carried by a value:
  `LYPNING_UNSUPPORTED`, an empty `stdout` by the commit barrier, and `stderr`
  holding exactly the one line the binary would have printed.
  `lypning_result_should_fall_onward()` is the call a host branches on, and it
  is the same predicate the dispatcher uses.
- **Five bindings over one ABI.** C and C++ headers in `assets/include/`, a
  Node-API addon with no npm dependencies in `assets/node/`, the Rust crate
  directly, and `lypning.embed` for Python over `ctypes` — with runnable
  examples in `assets/examples/`. Zero dependencies everywhere, as everywhere
  else in this project.
- **A step limit, because there is no process to kill.** `while True: pass` in
  a host's own thread is a hang with no signal and no way back, and every
  program a coding harness runs was written by a model. The counter ticks on
  every statement *and* every iterator advance — `sum(range(10**12))` is one
  statement — and passing it is a refusal, so the program still gets its answer
  from CPython. Output has the same shape of bound.
- **A filesystem denial that refuses instead of lying.** `set_filesystem(q, 0)`
  makes every file operation an `unsupported: sandbox` refusal. Telling the
  program the file is missing would be a wrong answer at exit 0, which is the
  outcome the whole contract exists to prevent; the host is told instead, and
  decides for itself whether CPython gets the program.
- **`lypning conformance --engine library`** runs the corpus through the ABI
  in-process and scores it against CPython by the rules the spawned arms are
  scored by. On the run that shipped this it matched the `lypning` arm program
  for program. `lypning build --lib` asserts the contract through the ABI before
  reporting `ok`, `lypning doctor` re-asserts it on the installed library, and
  `lypning lib` prints the compile line.

### Fixed — nine ways a program could kill the process, most of them since forever

Embedding found these, because embedding is what made them matter: in a binary a
segfault is a crashed one-liner, and in a host it is an application dying with
nothing to catch. A stack overflow is not an unwind, so the ABI's guard cannot
intercept one — the depth has to be bounded before the stack is. All five are
pinned in `tests/test_embed.py`.

- **Nested parentheses past ~1,000 levels segfaulted**, in the binary too. Now
  `unsupported: recursion` past 64 — three and a half times the deepest program
  in the corpus (842 loaded 2026-08-20; deepest nests 18, p99 nests 11, median
  nests 2, counting `(`, `[` and `{` alike, which is what the guard counts).
  CPython refuses these as well, so routing one onward gets an error either way
  rather than a signal.
- **A long FLAT chain — `1+1+1+…` — was a different bug with the same ending.**
  It does not nest: it is parsed iteratively into a left-leaning spine, one node
  per term, which both the evaluator and the AST's own destructor then walk one
  stack frame at a time. Bounding the evaluator alone still crashed, because the
  tree is dropped after the refusal; the parser now refuses past 1,000 chained
  operators, so the tree is never built that deep.
- **`repr`, `==`, `<`, `in`, `sorted`, a deeply nested tuple used as a dict key,
  `json.loads` and `json.dumps`** each recursed once per level of a value the
  program chose the depth of. All bounded at 500 now by one shared guard,
  released on drop so an error path cannot leak a level.
- **`"a" * (10**14)` aborted the process**, because Rust's allocator failure
  handler aborts and an abort is not an unwind. Program-driven sizes are
  ceilinged and refused before they are asked for.
- **A NUL byte in the source silently ran half the program and reported
  success.** The lexer reads zero as end-of-input, and the ABI takes a pointer
  and a length, which is the one way a NUL can arrive — so the CLI could never
  reach it. Refused now.
- **`format(1, '9'*30)` aborted the binary** on a `.parse().unwrap()`, for a
  program CPython answers with `ValueError`. It answers with one too.
- **A `break` in a `finally` swallowed a refusal.** CPython's rule is that
  `break` there discards an in-flight exception, and that is kept — but a
  refusal is not the program's exception, it is the runtime declining to run it,
  and discarding one turned a routable program into a wrong answer at exit 0.
- **`os.mkdir` reported a run as reversible when it was not.** A directory
  cannot be staged, so a refusal after one was routed onward and the retry
  raised `FileExistsError` for a program that works. It ends the run's
  reversibility now — except for `os.makedirs(..., exist_ok=True)`, which is
  idempotent and stays routable.
- **Tearing down what a program built recursed once per level.** The binary
  never noticed — it exits, and the kernel reclaims. A library hands the thread
  back, so it now takes values apart with a worklist: a million-level list is
  fine.
- **`json.dumps()` with no argument was an index panic**, i.e. an abort in the
  binary. It is a `TypeError`, as CPython says.

### Fixed — six interpreter bugs, all found by `lypning fuzz`

The differential fuzzer generates programs from the subset lypning *claims* to
implement and diffs them against CPython. It found the class the corpus
structurally cannot: the corpus is a sample of what agents happened to type, so
it only ever covers ground someone already walked.

Every one of these was a **silent** failure — a wrong answer at exit 0, or a
traceback at exit 1 for a program CPython runs. Exit 1 is the program's own, so
the dispatcher returns it unchanged and there is no retry on CPython. Pinned in
`tests/test_fuzz_findings.py`, each asserted against live CPython rather than a
remembered string.

- **`repr(float)` broke ties the wrong way.** `(1/-143.0) * 1e17` is exactly
  -699300699300699.25; one ulp is 0.125, so both `…699.2` and `…699.3` are 17
  digits and both round-trip. CPython resolves the tie to even; Rust's `{:e}`
  rounded away. `shortest_digits` now takes only the digit *count* from `{:e}`
  and the digits from `{:.*e}`, which rounds half-to-even. Verified against
  CPython over 4,000 doubles — 18 hand-picked plus 3,982 random bit patterns —
  with zero divergences.
- **`bytes.find(False)` raised `TypeError` at exit 1** where CPython searches
  for byte 0 and answers `-1`. `bool` is a subclass of `int` in Python; every
  other site (indexing, `range`, arithmetic, `*`) already went through numeric
  coercion, but this arm matched `Value::Int` directly.
- **`b"abc".count(b"a")` was a traceback at exit 1.** `route.rs` asks only
  whether a name is a method of *any* type it knows, and `count` is a `str`
  method, so it routed to lypning — which has no `bytes.count`. Methods CPython
  has and lypning does not now leave by the refusal contract.
- **`"日本".islower()` answered `True`.** The test was `is_alphabetic`, but 日 is
  alphabetic and has no case at all. Cased is now the predicate, and a titlecase
  letter is handled by asking for a case *mapping* rather than trusting the two
  predicates.
- **`format(inf, "+")` was `inf`, and `format(-inf, "010")` was `000000-inf`.**
  An infinity gets a sign slot like any other number, and zero fill goes between
  the sign and the digits.
- **`format(1e17, "_")` was `1e_+17`.** A body already in exponent form has no
  integer part to group; the separator landed inside the exponent.
- **`round(-0.5)` lost the sign of its zero.** The half-even correction lands on
  `+0.0` in IEEE where CPython keeps `-0.0`.

### Fixed — tooling

- **A cross-target build installed itself as the host engine.** `lypning build
  --target i686` copied the 32-bit sandbox binary over `~/.lypning/bin/lypning`,
  which is what `engines.find_lypning()` resolves to, so every dispatch,
  conformance run and benchmark afterwards silently measured the wrong artifact
  — and on a host without multilib it would not have run at all. Cross-target
  builds now install under a suffixed name (`lypning-i686`).
- **The gate judged `lypning-i686` against lypning-mp's 700 KB budget** and
  failed a sound build, because it identified the engine by exact binary name.

### Added

- `lypning fuzz`, `lypning corpus-time`, `lypning build --stock`,
  `lypning build --verify`, `lypning bench --micropython`.
- Routing safety in the conformance report: IDEAL / WASTED / LATE / UNSAFE.
- The ported upstream suites: `test_shims.py` (differential against live
  CPython, with restricted MicroPython stand-ins), `test_cookbook.py` (executes
  every recipe in `docs/COOKBOOK.md`), `test_syntax_scan.py`, `test_routing.py`.
  659 tests, up from 195.
- GitHub Actions CI over Python 3.9–3.13.

### Note — upstream's shim suite was partly vacuous

Porting `test_shims.py` surfaced a defect in the *upstream* harness, not in the
shims: it shadowed modules with `sys.path.insert(0, LIB)` alone, which does not
work on CPython 3.11 — `os` and `posixpath` are deep-frozen and `FrozenImporter`
runs ahead of `PathFinder`. Nine `os`/`os.path` cases were comparing CPython
with itself and passing vacuously. The port installs a `sys.meta_path` finder
instead; six modules failed immediately once it did, and now pass for real.


### Added

- **`lypning fuzz` — a differential fuzzer over the subset's own grammar.**
  `conformance` grades the programs agents happened to type, so the corpus is a
  sample and not a specification; this generates programs from lypning's own
  `BUILTINS` and method tables, runs each under CPython and under the engine,
  and reports any disagreement. Exit `90` is a refusal and never a finding.
  Every counterexample is shrunk to a minimal program, and the seed that
  reproduces the run is printed whether or not anything failed. `--iterations`,
  `--seed`, `--engine`, `--json`; exit 1 on any counterexample.

- **`tests/test_docs.py` — the documentation claims that a machine can check.**
  This repository has shipped a dangling documentation reference three times
  (`tests/test_shims.py`, `tests/test_cookbook.py`, `tests/test_syntax_scan.py`
  were each promised before they existed) and `README.md` §4's command reference
  silently lost `corpus-time` when that command was added. Neither failure is
  visible to any other gate: a stale sentence compiles, links, conforms and
  benches identically. So four things are now asserted — every `cli.COMMANDS`
  entry appears in §4 and every §4 row still exists; `--json` is offered exactly
  where §4 says it is; every relative markdown link and every `tests/test_*.py`
  a document names resolves; and every `docs/X.md §N` cross-reference lands on a
  heading that is there. Prose is deliberately out of scope — the rule enforced
  is only that **a document may not name something that is not there.**

### Fixed

- **`lypning corpus-time --record` fails before the run, not after it, and
  never with a traceback.** An unwritable destination surfaced as
  `lypning: FileNotFoundError: [Errno 2] …` *after* the whole corpus had been
  timed, which threw the expensive half of the command away and reported a
  caller mistake as if it were our bug. The target is now checked up front, for
  the same reason `--baseline` already was, and the write itself reports one
  line naming the fix. A corrupt, missing or wrong-schema `--baseline` also now
  names the command that writes a good one rather than only saying what is
  wrong with the bad one.
- **`README.md` §4 documents `lypning corpus-time`**, which has existed as a
  subcommand and been absent from the only table a reader scans to find out
  what exists. §6 now says what separates it from `bench` — arms against each
  other versus runs of one binary against each other — and the shipped skill
  names both it and `lypning fuzz` among the gates.
- **The stale numbers in `README.md` §1 and §2.** The Rust core has been rebuilt
  since those tables were written, so the byte count quoted against
  lypning-mp's was 1,036,984 B where `lypning status` prints 1,045,176 B, and
  the four-arm table's absolute milliseconds were a different run's. Both are
  re-measured, dated, and the ratios — which are what reproduce — are what the
  prose now argues from. `CLAUDE.md`'s own invariant 3 was quoting a remembered
  corpus size (839) in the sentence forbidding it; the tree loads 842 today.
- **`docs/LYPNING.md` linked to `LYPNING-MP.md`**, a file the rename left
  behind, and named the crate as `rust/` throughout where every other document
  says `assets/rust/`. Its 2026-08-16 conformance table was headed "Current",
  which it has not been since the extraction. `docs/MICROPYTHON.md` and
  `docs/SUBSET.md` pointed the seed corpus at `tests/corpus/`, which is where it
  lived upstream; it is `assets/corpus/` here.
- **Upstream-only citations are labelled as such.** `docs/SUBSET.md`'s evidence
  table, `docs/RESEARCH.md`'s sources and the MicroPython asset README's
  acceptance metric all cite files in the `deepresearch.se` repository this
  package was extracted from. They are now marked, so a reader stops looking for
  `docs/TESTING.md` and `scripts/build-sandbox-image.sh` in this tree.
- **`assets/micropython/README.md` gave a build command that could not run.**
  `make -C lypning-mp verify` names a directory that does not exist, and
  `bash scripts/build-micropython.sh` is wrong from the directory the file sits
  in. It now leads with `lypning build --micropython` and states which directory
  the lower-level commands are typed from.

- **`str.partition("")` and `str.rpartition("")` raise `ValueError` again.**
  Both answered `('', '', '')` at exit 0 where CPython raises `ValueError:
  empty separator`. `split()`/`rsplit()` had the check since the beginning and
  these two never did. Found by `lypning fuzz`.
- **`round(-0.5, 0)` keeps the sign of its zero.** It answered `0.0` where
  CPython answers `-0.0`: the half-to-even correction is `r - f.signum()`,
  which for `-0.5` is `-1.0 - -1.0` and therefore `+0.0` in IEEE.
- **An infinity and a NaN get a sign slot.** `format(inf, "+")` was `inf` and
  not `+inf`, `format(nan, " ")` was `nan` and not ` nan`, and
  `format(-inf, "010")` was `000000-inf` and not `-000000inf` — the non-finite
  path returned before the sign logic and carried its own minus inside the
  body, where zero fill then landed in front of it.
- **A number with no presentation type right-aligns.** `format(7, "10")` and
  `f"{1.5:10}"` padded on the right, like a string, where CPython pads on the
  left — so every column of a `f"{name:20}{n:6}"` table was wrong on the
  numbers. `pad()` took a `numeric` flag and ignored it, and the default
  alignment was inferred from a non-empty sign slot instead: exactly the thing
  a non-negative number does not have.
- **Grouping no longer reaches into an exponent.** `format(1e17, "_")` answered
  `1e_+17`; a body already in exponent form has no integer part to group. Found
  by `lypning fuzz`.
- **`islower()` and `isupper()` test CASED characters, not alphabetic ones.**
  `"日本".islower()` and `"日本".isupper()` both answered `True` where CPython
  answers `False`: 日 is alphabetic and has no case at all, so the string has no
  cased character and neither predicate can hold. Found by `lypning fuzz`.
- **A method CPython has and lypning does not refuses instead of raising
  `AttributeError`.** `route.rs` asks only whether a name is a method of ANY
  type it knows, so `b"abc".count(b"a")` — `count` is in the str table —
  routed to the Rust core, which has no `bytes.count`, and the caller got an
  `AttributeError` traceback at exit 1 where CPython prints `1`. Exit 1 is the
  program's own and the dispatcher returns it unchanged, so there was no second
  tier and no second chance: a wrong answer through `lypning run`, not a
  refusal. 44 names were reachable this way (28 on `bytes`, 10 on `str`, 5 on
  `set`, 1 on `dict`); each now exits 90 as `<type>-method`, and an attribute
  NEITHER has still keeps CPython's `AttributeError` at exit 1.
- **`lypning bench --micropython` no longer reports stock's crashes as
  coverage.** The coverage block compared the two arms on "did not exit 90",
  and the control has no exit 90 — so every program lypning-mp refused counted
  as one only stock could run, under a line saying anything but 0 there is a
  capability we lost. On this corpus that read 49 where the true number is 2
  (`os.system`, removed on purpose in `mpconfigvariant.h`); stock crashed on
  the other 47. The comparison is now on completion, and a `failed` column
  carries each arm's own non-zero exits — 531 of stock's 763 runs here.
- **`docs/BENCH-LEDGER.md` says what writes to it.** Its header said neither
  shipped measurement appends to the file, which stopped being true when
  `lypning bench --micropython --record` gained the marker insert.
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

### Known gaps

- **The Rust core's `float ** float` is one ULP off CPython's on some
  arguments.** `1.7976931348623157e308 ** 0.5` answers
  `1.3407807929942597e+154` where CPython answers `…596e+154`; a sweep of 400
  random `a ** b` disagreed on 2. Both call their libm's `pow`, and the core is
  static musl while CPython here is glibc — glibc's `pow` is correctly rounded
  on these and musl's is not. Closing it means a correctly-rounded `pow` in the
  core or refusing float `**`, and both are decisions rather than fixes.
  `lypning fuzz` reproduces it: seed 1817614320.
- **`repr(float)` breaks an exact shortest-repr tie the other way.**
  `print(2250000000000000.0 + 0.3333333333333333)` prints
  `2250000000000000.3` where CPython prints `…0.2`. Both hold the same double
  (`…0.25` exactly); when the shortest round-tripping decimal is an exact tie,
  CPython's dtoa rounds the last digit to even and Rust's rounds up. It needs a
  tie to happen at all — 0 of 2996 random doubles differ, 2 of 10 hand-picked
  ties do.
- **`lypning conformance` reports 1 UNSAFE route**, and it is the commit-barrier
  defect above seen from the other side: `hashlib` is in `route.rs`'s table, so
  `py-b2a043f241f1` routes to lypning-mp, which prints 147 bytes and then
  refuses. The classifier is not wrong about the import; the tier is wrong
  about the barrier. Narrowing the table to dodge it would cost every other
  `hashlib` program a tier and hide the defect, so the number stays.
  `tests/test_routing.py` pins it as the only shape an UNSAFE route takes here.

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
  `lypning bench` here on 2026-08-20 over 763 measured programs, with all three
  engines built, moved every absolute number **and reversed the ordering of the
  two subset engines**: lypning-mp came in ahead of lypning on the shared
  subset and started faster too, where upstream had lypning ahead at 0.102x
  against 0.143x. The mixture result held — 763/763 answered, and roughly a
  two-thirds saving against CPython. So upstream's "the subset engine is
  fastest on the work it accepts" is a result about that corpus on that
  machine, not a property of the design. Re-measure; do not cite, and do not
  carry a remembered ordering either. The numbers are deliberately not restated
  here: `README.md` §1 carries the current run with its date and corpus size,
  and `docs/BENCH-LEDGER.md` is the append-only history. A ratio pinned in a
  changelog is a ratio nobody re-derives.
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
