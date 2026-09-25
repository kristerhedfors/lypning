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
| `lypning` | the capability-frozen Rust core; a 9-block budget on x86-64 musl (`gate.VARIANT_BLOCK_BUDGET`), other targets measured separately | `src/lypning/assets/rust/`, `--features variant-m` (the default) |
| `lypning-l` | the same crate with `cap-base64`, `cap-bigint`, `cap-binascii`, `cap-collections`, `cap-csv`, `cap-difflib`, `cap-future`, `cap-glob`, `cap-hashlib`, `cap-itertools`, `cap-pathlib`, `cap-random`, `cap-re`, `cap-statistics`, `cap-textwrap` and `cap-time` (`engines.VARIANT_CAPS`), budgeted 32 blocks | `--features variant-l` |
| `cpython` | the real thing, and the reference every verdict is graded against | the system `python3` (`engines.find_cpython`) |

The chain is `engines.ENGINE_ORDER`: the Rust spectrum, cheapest first,
then CPython as the reference and fallback.

## 1. Measurement

Every count carries the run it came from; re-run the tool and quote the count
it prints, with the date (`CLAUDE.md` invariant 3). `lypning bench` measures
cost, arm by arm in `bench.ARM_ORDER` (`cpython`, `lypning`, `lypning-l`,
`mixture`); `lypning conformance` measures correctness and grades routes in the
same run. No timing from the run of record is quoted (the host was shared);
`docs/BENCH-LEDGER.md` carries the dated ones.

The counts grow with the corpus and move with every capability. `MISMATCH 0` is
supposed not to move, and on 2026-09-07 it did: the run above is the first in
which stderr was compared at all, and one entry (`py-771e5de335fc`) had been
scored MATCH with CPython raising `IndentationError` and the engine raising
`SyntaxError`. That is an engine defect the instrument could not see, not a
softened gate — see `docs/VERIFICATION.md` §C3. The second table is why the
first cannot be read alone: 172 of `lypning`'s MATCHes compared nothing but an
exit code. The bytes are host builds, not the musl bytes CI gates. Run a battery
only in a worktree with its own `LYPNING_HOME` (`docs/VERIFICATION.md` §C8).

## 2. Conformance

Every corpus program runs under CPython and under each engine, and each
engine's result is one of three things (`conformance.classify`):

| verdict | meaning | is it a failure? |
|---|---|---|
| MATCH | stdout + exit code + the exception, if either arm raised, identical to CPython | no |
| UNSUPPORTED | exit **90** with `<engine>: unsupported: <kind>: <detail>` | **no** — this is coverage, and the build order |
| MISMATCH | anything else | **yes, always** |

**A subset runtime that silently disagrees with CPython is worse than no runtime
at all**, because the agent that typed the one-liner will not notice. That is
why MISMATCH is the gate and UNSUPPORTED is not.

## 3. The subset

The subset is chosen from the corpus, not from the language reference:
expressions, statements, comprehensions, f-strings, `%` and `.format()`,
functions with closures and `lambda`, `try`/`except`, `with`, slicing and
unpacking. The module surface is `modules.rs:MODULES`, one array whose capability rows each sit behind their own `cfg`,
and which names each variant resolves is `docs/DIFFERENCES.md` — written down
once, there, and pinned to those tables by `tests/test_differences.py` rather
than copied into each document that needs it.

`re` is on the larger variant only: the core routes `import re` to
`lypning-l`. What `re.rs` serves is a SLICE of the pattern language —
literals, classes, `. ^ $ \b`, the table classes over ASCII, the quantifiers
greedy and lazy, groups and alternation, `search`/`match`/`fullmatch`/
`findall`/`finditer`/`compile`/`sub`/`subn`/`split` and the `Pattern` and
`Match` values. ASCII named captures `(?P<name>...)` use the same numbered
capture slots: `group(name)`, `m[name]`, `start/end/span(name)` and
`groupdict(default=None)` answer, including nested numbering, retained captures
across repetition, unmatched defaults and insertion order. `groupdict` returns
a fresh dictionary but retains the caller's default object, just like CPython.
Non-ASCII group names refuse rather than approximate CPython's Unicode
identifier tables. `Pattern.groupindex` (a read-only mapping proxy),
`lastindex`/`lastgroup`, named replacement templates and `expand` remain outside
this slice. Backreferences, lookaround, bytes patterns,
Unicode `\w`/`\d`/`\s` and Unicode case folding are refusals, and
`conformance --plan` ranks them. So is a step budget: CPython is exponential
on `(a+)+$` and so is any backtracker, and a budget that answered "no match"
when it ran out would be a wrong answer at exit 0.

**The four refusals.** A subset can be wrong in two ways, and only one of them
is acceptable. These are the places where lypning refuses rather than
approximates:

1. **Integers are i64 on the CORE; Python's are arbitrary precision.** Every
   arithmetic operation there is checked and an overflow is `unsupported:
   bigint`, never a wrap. On `lypning-l` this is no longer a refusal but a
   capability: `cap-bigint` widens `Value::Int`'s payload — one integer variant
   that can be wide, not a second `Value::BigInt` — so `+ - * ** // % divmod <<
   >>`, the comparisons, `str`/`repr`/`%`/`.format`, `bin`/`hex`/`oct`,
   `json.dumps` and `int()` are exact at any width (`bigint.rs`). What still
   refuses there is written down in that file's header, and the largest item is
   any mixing of a wide integer with a **float**, which needs a rounding this
   engine does not do; so is a wide integer as a **dict key**, because
   `2**100 == 2.0**100` is True in CPython and the two are one key.
   `int / int` where an operand is past 2\*\*53 needs a quotient rounded from the
   integers themselves, and converting each to `f64` first loses the low bits
   before the divide. On the core that is `unsupported: int-div-precision`;
   `bigint::div_exact` answers it on `lypning-l`, which is why the kind left
   `route::ONLY_CPYTHON_KINDS` and `engines.ONLY_CPYTHON_REFUSALS`. The separate
   NAME is still load-bearing: it is the one refusal a larger variant answers
   with arithmetic rather than with a module, and `route::CAPS` carries it and
   `bigint` as `cap-bigint`'s two kinds — the first row of that table whose
   claim is a runtime kind and not a module.
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
- file writes accumulate per path, and deletes and renames are staged (a
  rename of a directory or a link, at either end, is `unsupported: rename`:
  the kernel moves a tree the overlay cannot stage);
- a directory is made for **real** and recorded in an undo log;
- exit 90 undoes all of it, so the program is observably a no-op.

**Two techniques, and the third line is why.** Content can be staged, so it is.
A directory cannot — `os.mkdir` has no content to hold back — and modelling one
instead would put a second directory tree behind `os.path.isdir`, `glob`'s
`readdir` and every write into it, any one of which could disagree with the disk
at exit 0. So `io::make_dir` creates it and remembers it, and `io::rewind`
removes it newest-first if the run has to fall onward. That always works from
inside one run: every file the program wrote is still staged, so the run's own
directories are empty when the refusal arrives.

The barrier is invisible to the program and visible only to the dispatcher: a
read consults the staged writes first, so `open(p,'w').write(x)` followed by
`open(p).read()` behaves exactly as in CPython. `os.path.exists`, `getsize`,
`isfile`, `remove` and `rename` all see the overlay too.

Four escape hatches in lypning's own barrier, all handled rather than assumed
away:

- **Size.** Past 8 MiB of buffered output the run commits early and *loses* its
  ability to fall back; a later refusal is then reported as a hard error rather
  than a routing signal. Nothing in the corpus comes close.
- **`os.rmdir`** of a directory this run did not make. `create_dir` cannot give
  back a mode, a timestamp or an owner, so that one commits — where `os.rmdir`
  of the run's *own* directory is a no-op over the run and stays routable.
- **A directory that will not come off.** Only something outside the process can
  arrange it — the run's own directories are empty when the refusal arrives — but
  when `io::rewind` cannot remove one it puts back whatever it had already taken,
  keeps the staged output and writes, and commits. The refusal is then the
  program's own exit 1 **with its output intact**: a barrier that cannot undo
  must cost the program its routing and nothing else, which is exactly the
  behaviour `os.mkdir` had before #51.
- **stdin.** A consumed pipe cannot be rewound. If lypning already read stdin
  before refusing, the dispatcher forks instead of exec'ing and replays the
  captured bytes.

Each of the first three names itself in the refusal line — `reached after
{why}, so the run cannot be routed onward`, from `io::commit_reason` — because
one bool cannot say which of three things happened, and a diagnostic that
guesses sends the reader to the wrong mechanism.

`open()` reads a path **whole** and `.read(n)` slices the snapshot, so a path
with no end (`/dev/zero`, `/dev/random`) is refused before the read starts
(`open-special`). A hang is the one outcome worse than a wrong answer: it has no
exit code, so the dispatcher cannot even reach the next tier. `os.mkdir` and
`os.makedirs` refuse `mode=` and every keyword CPython does not take, rather
than accepting and dropping it (`mkdir`); `create_dir` hands the kernel `0o777`
and the umask decides, which is CPython's default and nothing else.

`os.mkdir` was a fourth until issue #51: it committed the run the moment it ran,
so every later refusal — `builtin: eval`, `builtin: complex`, `bigint`, any of
them — was `lypning: error: … cannot be routed onward` at exit 1, with the
directory on disk and no answer, where CPython answers at exit 0. Three
capability rounds each met it through a different refusal kind and each worked
around it in the router; `bigint` is why that could not finish the job, because
its refusals depend on a value and no static walk can hoist them. Pinned by
`tests/test_commit_barrier.py::test_the_chain_makes_the_directory_exactly_once`
— `os.mkdir` raises the second time, so exit 0 through the chain is itself the
proof that the retry saw a clean directory.

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

- **No `subprocess`, threading or networking.** A working `subprocess` fake
  would keep the expensive pattern alive; each is a `module` refusal `--plan`
  ranks. `re` was on this list until `cap-re` (§3): the matcher is on `lypning-l`
  and the core still refuses it.
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
| `src/base64.rs`, `src/bigint.rs`, `src/binascii.rs`, `src/collections.rs`, `src/csv.rs`, `src/future.rs`, `src/glob.rs`, `src/hashlib.rs`, `src/itertools.rs`, `src/pathlib.rs`, `src/randobj.rs`, `src/re.rs`, `src/statistics.rs`, `src/textwrap.rs`, `src/time.rs` | `cap-base64`, `cap-bigint`, `cap-binascii`, `cap-collections`, `cap-csv`, `cap-future`, `cap-glob`, `cap-hashlib`, `cap-itertools`, `cap-pathlib`, `cap-random`, `cap-re`, `cap-statistics`, `cap-textwrap`, `cap-time` — compiled into `lypning-l` only; `cap-difflib` has no file (its one row is in `modules.rs`, and nothing on the module is served); `glob`'s ORDER half is a static blocker in `route.rs`, not a value in `glob.rs`; `cap-bigint` serves no module at all; `cap-random` adds names to the core's own `random` and `sys`; `future.rs` is a pass over the parse, not a module; and `hashlib`'s object is a `Value::IterObj` over an `Iter`, not a `Value` variant of its own |
| `src/io.rs`, `src/alloc.rs`, `src/hash.rs`, `src/args.rs`, `src/err.rs` | the commit barrier; the size-class allocator; hashing; call arguments; the refusal line and `ENGINE` |
| `src/route.rs`, `src/main.rs`, `src/embed.rs`, `src/capi.rs`, `src/host.rs`, `src/lib.rs` | the classifier; CLI, exit contract, dispatcher; the in-process runner and `fall_onward`; the C ABI (`capi` feature); host hooks |
| `../scripts/build-rust.sh` | the standalone build, with the shape and contract smoke checks; `lypning build --rust` drives the same build |

Python side: `src/lypning/`, laid out in `README.md` §9; `cli.py` is the only
module that prints.

## 11. Adding a capability to lypning-l

The realistic next PR is a `cap-*` on the larger variant; the core is frozen.
`cap-pathlib` (`CHANGELOG.md` #41) is the worked example, in this order:

**Verify.** `docs/VERIFICATION.md` §C1–§C6 hold these claims as commands
with expected output; the two that change most:

```bash
lypning conformance --mixture both | grep -E '^(MISMATCH [0-9]|dispatchers|monotone)|UNSAFE'; echo $?
~/.lypning/bin/lypning route --spectrum; echo $?     # the table §4 describes, as JSON; the binary only, 0
```
