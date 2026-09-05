# lypning — the Coding Harness Interpreter Optimizer

*Design. Numbers are from the run of record, `docs/VERIFICATION.md` §0
(2026-09-04 · 437056c · corpus 3688 programs), or carry their own date.*

lypning optimizes the interpreter layer under a coding harness: a **mixture of
Pythons** — a Python subset written from scratch in Rust, sized to the
one-liners an agentic CLI types and built as a **spectrum** of variants from
one crate — plus a classifier that picks the cheapest engine per program.
Every table is re-derivable from your own corpus ([`FORKING.md`](FORKING.md)).

| engine | what it is | where it lives |
|---|---|---|
| `lypning` | the Rust core, frozen at 8 blocks (`gate.VARIANT_BLOCK_BUDGET`); it gains no capability | `src/lypning/assets/rust/`, `--features variant-m` (the default) |
| `lypning-l` | the same crate with `cap-collections` and `cap-pathlib` (`engines.VARIANT_CAPS`), budgeted 32 blocks | `--features variant-l` |
| `cpython` | the real thing, and the reference every verdict is graded against | the system `python3` (`engines.find_cpython`) |

The chain is `engines.ENGINE_ORDER`, cheapest first. `lypning-mp` is the oracle
— measured, never routed to ([`MICROPYTHON.md`](MICROPYTHON.md)): its recorded
divergences say what a Rust variant must implement exactly or refuse.

## 1. Measurement

Every count carries the run it came from; re-run the tool and quote the count
it prints, with the date (`CLAUDE.md` invariant 3). `lypning bench` measures
cost, arm by arm in `bench.ARM_ORDER` (`cpython`, `lypning`, `lypning-l`,
`mixture`); `lypning conformance` measures correctness and grades routes in the
same run. No timing from the run of record is quoted (the host was shared);
`docs/BENCH-LEDGER.md` carries the dated ones.

```
lypning conformance · 2026-09-04 · 437056c · 3688 loaded
engine       MATCH  UNSUPPORTED  MISMATCH   coverage
lypning      1573          931         0     62.8%
lypning-l    1741          763         0     69.5%
mixture      2504            0         0    100.0%
lypning status · same run · on Darwin arm64:  lypning 818,080 B, 7 blocks;  lypning-l 867,744 B, 7 blocks;  lypning-mp not built
```

The counts grow with the corpus and move with every capability; `MISMATCH 0`
does not. The bytes are host builds, not the musl bytes CI gates. Run a battery
only in a worktree with its own `LYPNING_HOME` (`docs/VERIFICATION.md` §C8).

## 2. Conformance

Every corpus program runs under CPython and under each engine, and each
engine's result is one of three things (`conformance.classify`):

| verdict | meaning | is it a failure? |
|---|---|---|
| MATCH | stdout + exit code identical to CPython | no |
| UNSUPPORTED | exit **90** with `<engine>: unsupported: <kind>: <detail>` | **no** — this is coverage, and the build order |
| MISMATCH | anything else | **yes, always** |

**A subset runtime that silently disagrees with CPython is worse than no runtime
at all**, because the agent that typed the one-liner will not notice. That is
why MISMATCH is the gate and UNSUPPORTED is not.

A MISMATCH carries a sub-kind: `stdout`, `exit`, `stderr`, `timeout` (one
deadline on both sides; only the engine hit it), `unbuilt`, and `contract` —
exit 90 after bytes reached stdout, or exit 90 without the line.
`conformance.DEFAULT_ARMS` is `engines.SPECTRUM` plus `mixture`, the Python
dispatcher end to end; `lypning-mp`, `library` and `mixture-rust` are
`conformance.OPT_IN_ARMS`, and an unbuilt arm is a `note:` line, never a
MISMATCH. `--mixture both` adds the Rust dispatcher as an arm (`dispatchers
agree N/N`, §5); `--plan` ranks every blocker by the programs it sends to
CPython (`conformance.plan`): `lypning-l`'s build order. Checks:
`docs/VERIFICATION.md` §C3.

## 3. The subset

The subset is chosen from the corpus, not from the language reference:
expressions, statements, comprehensions, f-strings, `%` and `.format()`,
functions with closures and `lambda`, `try`/`except`, `with`, slicing and
unpacking. The module surface is `modules.rs:MODULES`, one table per variant:

| variant | modules |
|---|---|
| `lypning` | `sys`, `os`, `os.path`, `posixpath`, `io`, `json`, `random` (the seeded-integer subset, MT19937 bit for bit — `random.rs`) |
| `lypning-l` | the same, plus `collections` (`Counter`, `defaultdict` — `collections.rs`) and `pathlib` (`Path` — `pathlib.rs`) |

`re` is absent from every variant: `import re` routes to `cpython`, and
`conformance --plan` ranks what that costs.

**The four refusals.** A subset can be wrong in two ways, and only one of them
is acceptable. These are the places where lypning refuses rather than
approximates:

1. **Integers are i64; Python's are arbitrary precision.** Every arithmetic
   operation is checked and an overflow is `unsupported: bigint`, never a wrap.
   One integer refusal is deliberately *not* `bigint`: `int / int` where an
   operand is past 2\*\*53 needs a quotient rounded from the integers themselves,
   and converting each to `f64` first loses the low bits before the divide. That
   is `unsupported: int-div-precision`, and the separate name is load-bearing —
   lypning-mp *has* arbitrary-precision integers, so it answers a `bigint`
   refusal correctly and is worth falling through to, while on this one it does
   the same lossy conversion and answers wrongly. See
   `engines.ONLY_CPYTHON_REFUSALS`.
   (Since 2026-09-04, `CHANGELOG.md` #38, nothing falls through to lypning-mp;
   the distinction survives as the meaning of that table — see below.)
2. **Set iteration order is CPython's hashing, and cannot be reproduced.** So
   order-*independent* operations on sets work (`len`, `in`, the set algebra,
   `sorted`, `min`, `max`, `any`, `all`) and anything that would expose an order
   (`repr`, iteration, `list()`, `.join`) exits 90. Dicts, whose order Python
   *defines* as insertion order, have no such restriction.
3. **`repr` of a non-ASCII character** needs CPython's Unicode category tables;
   a whitelist of unambiguously printable blocks is answered, the rest refused.
4. **A NaN inside a container is compared by object identity, which a bare
   `f64` cannot carry.** CPython's element test is `x is y or x == y` —
   identity *first* — and a NaN is the one value for which the shortcut is
   observable: `n in [n]` is True there and `[n].count(n)` is 1. The rule is
   exactly as narrow as the ambiguity: when **both** sides of one element
   comparison are NaN the question is identity and lypning exits 90; when only
   one side is, they cannot be the same object *and* they are not equal, so
   False is CPython's own answer and lypning gives it — `[n] == [1]`,
   `n in [1, 2]` and `[1, 2].count(n)` all answer. The same rule keys dicts and
   sets: two *distinct* NaNs are two different keys in CPython, so a NaN as a
   dict key or set member is refused rather than collapsed by bit pattern.

Everything CPython specifies exactly is implemented exactly, including the ones
that look like they should fall out of the host language and do not: floor
division and `%` round toward negative infinity (Rust truncates), `/` on two
ints is always a float, `float` repr is shortest-roundtrip with the
fixed/scientific switch at `decpt <= -4 || decpt > 16`, and a function's
`UnboundLocalError` comes from a real analysis of the names its body assigns.

`route.rs:ONLY_CPYTHON_KINDS` names the refusal kinds that rule out **every**
Rust variant, at any size — behaviours a reimplementation gets wrong, from the
oracle's catalogue; `engines.ONLY_CPYTHON_REFUSALS` is the Python copy, held by
`tests/test_routing.py::test_both_dispatchers_read_the_same_escalation_table`.

## 4. The classifier

Routing is a **static analysis over lypning's own front end**, not a heuristic over
the program text. That is the design:

- lypning's parser already reports the exact construct that would stop it. Asking
  the parser is therefore an *exact* answer to "can lypning run this", costing one
  parse and no process spawn.
- A larger sibling cannot be asked, so its reach is a table: `route.rs:verdicts`
  gives one verdict per rung from `route.rs:CAPS` and `served_module`.

**The floor rule.** `route.rs:engine_from_verdicts` never names a variant
smaller than the binary that routed (its blocks are already paid for); a rung
below is marked `floor: below the routing binary`. `lypning route` prints a
clean route as the engine name alone, a refusal-derived one as
`<engine>\t<kind>: <detail>` (`engines.Route.__str__`); the binary's own
`~/.lypning/bin/lypning route --spectrum` (the flag is the binary's, not the
CLI's) prints the table as JSON, and `engines.VARIANT_CAPS` is pinned to it by
`tests/test_routing.py::test_the_spectrum_copy_in_engines_is_the_rust_table`.
The fixture table — every refusal kind, both variants, the floor rule — is
`tests/verification/route-fixtures.json` (`docs/VERIFICATION.md` §C5).

```bash
lypning route -c 'import collections; print(collections.Counter("aab"))'
# → lypning-l	module: import collections      (`class A: pass` → cpython	class: class definition)
```

`lypning conformance` grades every route (`routing.py`): IDEAL; WASTED (the
engine refused — one extra spawn, right answer); LATE (a cheaper engine would
have answered); UNSAFE (routed to an engine that MISMATCHES — must be 0);
NO-ENGINE. The `accuracy` line is a census, not a cost model: it weights LATE
and WASTED equally, and a LATE costs a CPython spawn where a WASTED costs an
in-process parse (2026-09-04, `CHANGELOG.md` #42: 12.0 ms against 1.21 ms). A
systematic LATE is a defect — an agent reads `cpython` from `lypning route`
and rewrites working code; `docs/HILLCLIMB.md` iteration 45 (2026-08-25) is
the `os.path` case, closed by `route.rs:resolve_module`.

## 5. The dispatcher

```
  python3 -c '…' ──shim or PreToolUse hook──▶ lypning run (main.rs): one verdict per rung
  lypning    IN-PROCESS, no spawn; output staged to the barrier (§6)
     │ exit 90 + the line
  lypning-l  forked, so its own exit 90 can be caught
     │ exit 90 + the line
  cpython    exec'd — no fork, no way back, and none is needed
```

A program routed to this binary runs in this process — no second spawn — and
exit 90 is a refusal only when a refusal fired: `main.rs:finish` leaves `kind`
empty for `sys.exit(90)`, the program's own number, returned unchanged. A rung
with something after it is forked so its exit 90 can be caught; the last is
exec'd (`main.rs:exec_engine`). The two dispatchers fall onward on:

| dispatcher | falls onward on |
|---|---|
| Python, `engines.dispatch` | exit 90 **with** the contract line (`engines.Result.refused`), and nothing else |
| Rust, `main.rs` through `embed.rs:fall_onward` | exit 90; also `MemoryError` on stderr; also `Traceback (` on stderr at exit 0 |

Where the chain goes is `route.rs:chain_after`, mirrored by
`engines.chain_after_refusal`: a kind in `ONLY_CPYTHON_KINDS` goes straight
to `cpython`; otherwise each later sibling whose static verdict was "can run"
and whose `cap-*` set is a strict superset, then `cpython`. `conformance
--mixture both` must print `dispatchers agree N/N` and `monotone violations 0`
(what `lypning` answers, `lypning-l` answers). `docs/VERIFICATION.md` §C4.

## 6. The commit barrier

Routing to lypning is only sound if a lypning run that ends in `unsupported` left
**nothing** behind. Otherwise the retry re-executes the side effects and the
file is written twice, or half. So a lypning run is transactional
(`assets/rust/src/io.rs`):

- stdout and stderr accumulate in memory and are written once, at a successful
  exit;
- file writes accumulate per path, and deletes and renames are staged;
- exit 90 discards all of it, so the program is observably a no-op.

`lypning-mp` has no barrier: it streams stdout, so a refusal after a `print`
is graded `MISMATCH contract` — one reason it is an oracle and not a rung
(`tests/test_routing.py::test_the_one_unsafe_route_is_the_tracked_barrier_defect`).

The barrier is invisible to the program and visible only to the dispatcher: a
read consults the staged writes first, so `open(p,'w').write(x)` followed by
`open(p).read()` behaves exactly as in CPython. `os.path.exists`, `getsize`,
`isfile`, `remove` and `rename` all see the overlay too.

Two escape hatches in lypning's own barrier, both handled rather than assumed
away:

- **Size.** Past 8 MiB of buffered output the run commits early and *loses* its
  ability to fall back; a later refusal is then reported as a hard error rather
  than a routing signal. Nothing in the corpus comes close.
- **stdin.** A consumed pipe cannot be rewound. If lypning already read stdin
  before refusing, the dispatcher forks instead of exec'ing and replays the
  captured bytes.

The threshold is `io.rs:COMMIT_THRESHOLD`. A refusal after bytes reached stdout
is `MISMATCH contract` (§2; the oracle's `commit-barrier` family), pinned by
`tests/test_commit_barrier.py::test_rust_core_refuses_with_stdout_untouched`.

## 7. Building

`lypning build --rust` builds every variant in `engines.SPECTRUM` from the one
crate (`--variant V` for one), installs each under its own name in
`~/.lypning/bin`, and asserts the refusal contract on the binary it just built
before it reports `ok` (`build.check_refusal_contract`; `BROKEN — <why>`
otherwise; `docs/VERIFICATION.md` §C2). A cross `--target` installs as
`lypning-i686`, never over the default (`engines.parse_binary_name`).
**Static musl is a precondition, not a preference.** `--target host` builds
the dynamically linked control, whose loader opens files `lypning gate` counts
against `gate.MAX_OPENS`. Zero runtime dependencies (`CLAUDE.md` invariant 6)
holds here for a second reason: every crate linked in is bytes in a binary
whose cold cost is a step function in device blocks (§8).

## 8. Size

Cold cost in the sandbox is a step function in 131,072 B (`gate.DEVICE_BLOCK`)
device blocks, so a variant is budgeted in blocks, not bytes — `lypning` 8,
`lypning-l` 32 (`gate.VARIANT_BLOCK_BUDGET`) — and
`lypning gate` fails a build that crosses its budget (`docs/VERIFICATION.md`
§C6). The core is frozen by decision (`Cargo.toml`, the `variant-m` comment):
every new capability goes to `lypning-l`. `opt-level = "z"` was measured on
2026-08-24 (`docs/HILLCLIMB.md`, iterations 18 and 19) as smaller and **still 8
blocks**, so it buys nothing under the cost model that matters, and the same
day it cost interpreter throughput on the `perf` suite. The crate is static,
not static-PIE; `.cargo/config.toml` carries that measurement and the trade.

## 8a. Measurement in a sandbox VM

CheerpX is 32-bit x86, so `lypning build --rust --target i686` is the binary
that runs there; the VM probes were measured upstream on 2026-08-19 with it and
are not reproducible from this tree; the probe table (first-touch bytes per
runtime, cited by `gate.py`) is in `docs/BENCH-LEDGER.md` under 2026-09-05. The
cost model is `docs/SANDBOX-PERFORMANCE.md`; `lypning gate` measures the shape
locally.

**Read the byte columns with the tool's ordering caveat in hand.** The IDB cache
is fresh per RUN, not per probe, so the first probe to touch a binary pays for
all of its blocks and later probes on the same binary read as free. Compare each
runtime's FIRST probe; a later one's byte count is not a size.

## 9. What is excluded

- **No `re`, `subprocess`, threading or networking.** A regex engine is a large
  amount of code with deep semantics, and a working `subprocess` fake would keep
  the expensive pattern alive; each is a `module` refusal `--plan` ranks.
- **No classes, decorators, generators, or `async`.** Each is a parse-time
  refusal (`parse.rs`) and a route to `cpython`; `lambda` is in the subset.
- **No daemon.** Interpreter init is a rounding error inside the process-spawn
  floor (`docs/RESEARCH.md` §5), so a fork server has nothing to save.

## 10. Layout

| path (under `src/lypning/assets/rust/`) | what |
|---|---|
| `Cargo.toml`, `build.rs`, `.cargo/config.toml` | the crate: `variant-*` and `cap-*` features; `LYPNING_ENGINE`/`LYPNING_CAPS` from the feature set; static, not static-PIE |
| `src/lex.rs`, `src/parse.rs`, `src/ast.rs` | tokenizer; recursive-descent parser — every gap is `unsupported: <kind>`; the AST |
| `src/eval.rs`, `src/value.rs`, `src/ops.rs`, `src/iter.rs`, `src/fmt.rs` | evaluator with real scopes; values (insertion-ordered dict, the set-order and NaN refusals); operators and Python's floor/mod rules; lazy iteration; `str`/`repr` and format specs |
| `src/builtins.rs`, `src/methods.rs`, `src/modules.rs`, `src/json.rs`, `src/random.rs` | builtins and methods (the tables the router reads); `MODULES` per variant; JSON against CPython's exact output; MT19937 |
| `src/collections.rs`, `src/pathlib.rs` | `cap-collections`, `cap-pathlib` — compiled into `lypning-l` only |
| `src/io.rs`, `src/alloc.rs`, `src/hash.rs`, `src/args.rs`, `src/err.rs` | the commit barrier; the size-class allocator; hashing; call arguments; the refusal line and `ENGINE` |
| `src/route.rs`, `src/main.rs`, `src/embed.rs`, `src/capi.rs`, `src/host.rs`, `src/lib.rs` | the classifier; CLI, exit contract, dispatcher; the in-process runner and `fall_onward`; the C ABI (`capi` feature); host hooks |
| `../scripts/build-rust.sh` | the standalone build, with the shape and contract smoke checks; `lypning build --rust` drives the same build |

Python side: `src/lypning/`, laid out in `README.md` §9; `cli.py` is the only
module that prints.

## 11. Adding a capability to lypning-l

The realistic next PR is a `cap-*` on the larger variant; the core is frozen.
`cap-pathlib` (`CHANGELOG.md` #41) is the worked example, in this order:

1. **Cargo.toml.** A `cap-<name> = []` feature in the `variant-l` list and
   nothing smaller; `build.rs` turns the set that is on into `LYPNING_CAPS`.
2. **route.rs.** A `CAPS` row (the modules it serves; a runtime kind only if a
   sibling would answer it) and the name in `SPECTRUM`'s `lypning-l` row.
3. **engines.VARIANT_CAPS**, the Python copy of that row;
   `tests/test_routing.py::test_the_spectrum_copy_in_engines_is_the_rust_table`
   fails until the two agree
   (`tests/test_engines.py::test_parse_binary_name_grows_with_the_spectrum`
   holds the name grammar).
4. **modules.rs and lib.rs.** A `#[cfg(feature = "cap-<name>")]` row of
   `MODULES`, a `get_attr` arm per served name, a `pub mod` in `lib.rs`; the
   code lives in its own file so the core does not move a byte.
5. **The wiring list — the recurring defect.** A new `Value` variant or dict
   tag reaches every path that materialises, compares or formats a value, and
   the adversarial pass has found the missed one three times
   (`docs/HILLCLIMB.md` iteration 74; `CHANGELOG.md` #39): `value.rs`
   (`type_name`, `eq`, `is_same`, hash, truthiness), `fmt.rs` (`repr`, `str`,
   format specs), `ops.rs` (operators, ordering, `in`, indexing, slices,
   `getattr`), `methods.rs`, `builtins.rs` (`isinstance`, `len`, `bytes`,
   `reversed`, constructor), `iter.rs`, `eval.rs`: grep `cfg(feature = "cap-`.
6. **The grid test.** One `tests/test_<name>_grid.py` running a cross-product
   of shapes under CPython and the variant
   (`tests/test_pathlib_grid.py::test_the_pathlib_grid_agrees_with_cpython`);
   a family in `lypning oracle` is a row here.
7. **The density measurement.** Programs gained per KB of `lypning-l` growth
   decides the order, because the block budget is the one thing a spectrum
   cannot spend twice: `lypning gate ~/.lypning/bin/lypning-l` for the bytes,
   `lypning conformance --engine lypning-l` for the programs, before and after
   (`docs/HILLCLIMB.md` iteration 74, 2026-09-04, ranked four by it).
8. **The gates.** `lypning build --rust`, `lypning gate` (`PASS`), `lypning
   conformance --mixture both` (`MISMATCH 0`, `monotone violations 0`,
   `dispatchers agree N/N`), `lypning doctor`, `git status`; then the
   `CHANGELOG.md` entry, its coverage delta quoted from that run.

**Verify.** `docs/VERIFICATION.md` §C1–§C6 hold these claims as commands
with expected output; the two that change most:

```bash
lypning conformance --mixture both | grep -E '^(MISMATCH [0-9]|dispatchers|monotone)|UNSAFE'; echo $?
~/.lypning/bin/lypning route --spectrum; echo $?     # the table §4 describes, as JSON; the binary only, 0
```
