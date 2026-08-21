# lypning — Mixture of Pythons

**Status: experimental, measured, not wired into the sandbox yet.**

lypning is a Python subset written from scratch in Rust, sized to the *bottom* of
the distribution of one-liners an agentic CLI actually types — plus a classifier
that decides, per program, which of three interpreters should run it.

The three interpreters are the mixture:

| tier | what it is | where it lives |
|---|---|---|
| **lypning** | this — a Rust subset, ~1,300 lines of interpreter | `assets/rust/` |
| **lypning-mp** | a MicroPython variant with a frozen shim stdlib | `assets/micropython/`, [`docs/MICROPYTHON.md`](MICROPYTHON.md) |
| **CPython** | the real thing | the system `python3` |

lypning-mp exists because CPython costs **8,573 ms cold** in the CheerpX sandbox
(`docs/SANDBOX-PERFORMANCE.md` §1) and the exec ceiling is 30 s. lypning exists
because lypning-mp is still an interpreter written for microcontrollers, and the
programs an agent types are a much narrower target than "Python".

## 1. The measurement, first

Everything below is downstream of one table, and the table is re-measured
rather than quoted from memory. The capture harness grows the corpus every
session — this project's first table was over 420 programs and the number was
stale within the day. Every tool prints the count it loaded; quote that one,
with its date.

This tree, on **2026-08-21**: `lypning bench --startup-repeat 15 --repeat 3`,
4 CPUs, Linux 6.18.44-fc-v21, all three engines built, **842 programs loaded and
763 measured** (`assets/corpus/corpus.jsonl` + `assets/corpus/seed-corpus.jsonl`;
79 skipped for naming an absolute path the per-entry temp cwd does not contain).

```
startup — `-c 'pass'`, min of 15, arms interleaved

arm         min ms   vs cpython
cpython      10.88     1.000x
lypning       0.70     0.064x
lypning-mp    0.61     0.056x
mixture       0.58     0.053x

shared subset — the 500 programs every arm executed, min of 3

arm          ran  refused   shared total   median   vs cpython
cpython      763        0       6658.3 ms   12.03     1.000x
lypning      500      263        486.2 ms    0.94     0.073x
lypning-mp   714       49        407.7 ms    0.79     0.061x
mixture      763        0        685.7 ms    0.92     0.103x

whole corpus — what a session of 763 one-liners costs

cpython     13428.9 ms   1.000x
lypning       743.8 ms   0.055x   (263 unanswered)
lypning-mp   1297.1 ms   0.097x   (49 unanswered)
mixture      4565.2 ms   0.340x   (0 unanswered — saves 8863.7 ms, 66.0%)
```

Read it in this order:

- **The mixture answers everything CPython answers** — 763 of 763, zero
  mismatches on its own arm — at **0.340x of CPython's cost**, a 66.0% saving.
  This is the result the design is built for, and it has held on every machine
  it has been run on.
- **The other two arms are cheap because they refuse**, not because they are
  faster: 263 and 49 programs unanswered, and a refusal still costs its spawn.
  `bench` prints that note next to those totals.
- **Startup is a floor the three share.** All three engines land within a tenth
  of a millisecond of each other, 15–19x under CPython. They are static musl
  binaries that open no files at startup; past that, the differences are the
  machine.
- **The subset was not tuned to this corpus.** It was built against 420
  programs and has been measured against every capture since; coverage has gone
  up as the corpus grew, with mismatches on the lypning arm still at zero, which
  suggests it generalises rather than fitting the sample.

Both binary sizes move with every rebuild — 1,045,176 B and 294,788 B in this
tree today; `lypning status` and `lypning gate` print the ones you actually
have.

### The upstream table, and the bullet that did not reproduce

The project was written up on a different run: upstream, **2026-08-16**, over
the 472 programs the corpus then held, min of 5, arms interleaved, before this
package was extracted from that container.

```
corpus — 472 programs

arm          ran  refused   shared total (323)   vs cpython
cpython      472        0          4314.4 ms      1.000x
lypning-mp   444       28           616.5 ms      0.143x
lypning      324      148           440.3 ms      0.102x
mixture      472        0           547.0 ms      0.127x

whole corpus     cpython 6987.9 ms 1.000x     mixture 1860.8 ms 0.266x
```

That run put **lypning ahead of lypning-mp on the shared subset** — 0.102x
against 0.143x — and it was written up as the thesis: a runtime built for
two-thirds of the distribution beats a general one on that two-thirds.

**Both re-runs in this tree reversed it.** On 2026-08-20 and again on
2026-08-21 lypning-mp came in ahead (0.061x against 0.073x above), and starts
marginally faster too. Successive runs on one box agree on the ordering and on
the ratios to within about a point while the absolute milliseconds move by tens
of percent with load — which is why ratios are what get quoted, and why `bench`
is not a CI gate.

So that thesis is upstream's result on upstream's corpus, not a property of the
design. The shared subset is by construction the programs lypning accepted —
the simplest in the corpus — where both engines sit near their startup floor,
and lypning-mp's floor is lower because its binary is a third the size. What
survived re-measurement is the mixture result: everything CPython answers, for
about a third of the cost.

Reproduce: `lypning build --rust && lypning bench`.

> **Running the corpus can rewrite this repository.** It is harvested from real
> agent sessions, so it is full of programs that edit `src/` and `docs/`. Every
> entry runs in its own temp directory, which contains the relative paths.
> Entries that name an ABSOLUTE path are skipped rather than run, and both
> tools print how many they skipped on every run — do not carry the number from
> here, it grows with the corpus. Both tools then bracket the battery with a
> `git status` check that reports and restores anything that changed anyway,
> and **fail the run** when something did: numbers taken against a moving tree
> are numbers nobody can stand behind. This is a net, not a sandbox — it cannot
> undo a write outside the repository, only make the next one loud. It exists
> because the first measurement runs here rewrote 34 tracked files.

`lypning bench` is deliberately **not in CI** — a wall-clock benchmark on a
shared runner measures the runner. CI keeps the deterministic half (conformance,
routing safety).

## 2. Conformance: the number that must be zero

```
lypning conformance
```

Every corpus program runs under CPython (the reference) and under each engine,
and each engine's result is one of three things:

| verdict | meaning | is it a failure? |
|---|---|---|
| MATCH | stdout + exit code identical to CPython | no |
| UNSUPPORTED | exit **90** with `<engine>: unsupported: <kind>: <detail>` | **no** — this is coverage, and the build order |
| MISMATCH | anything else | **yes, always** |

This tree, on **2026-08-21**, over the 763 of 842 corpus programs the battery
could run:

```
engine      MATCH  UNSUPPORTED  MISMATCH   coverage
lypning       500          263         0     65.5%
lypning-mp    714           47         2     93.6%
mixture       763            0         0    100.0%
```

**The gate is red on the lypning-mp arm.** Both MISMATCHes are one defect, and
it is in the contract rather than in a computation: MicroPython streams stdout,
so a program that prints before it reaches an unsupported construct has already
committed those bytes when it exits 90 (§6). It is tracked rather than waived —
`lypning conformance` fails while it stands.

Upstream, on 2026-08-16, over the 472 programs the corpus then held:

```
engine      MATCH  UNSUPPORTED  MISMATCH   coverage
lypning       324          148         0     68.6%
lypning-mp    443           28         1     93.9%
mixture       472            0         0    100.0%
```

**That table is history, not status**, and so is the one above it by the time
you read this — run `lypning conformance` for your own, and quote the corpus
size it prints. The single lypning-mp MISMATCH upstream was a different defect:
`json.load` on a 4 MB file exhausting its heap. The dispatcher recovers from
that one too (§5).

**A subset runtime that silently disagrees with CPython is worse than no runtime
at all**, because the agent that typed the one-liner will not notice. That is
why MISMATCH is the gate and UNSUPPORTED is not.

## 3. What lypning implements, and why exactly that

The subset is chosen from the corpus, not from the language reference. Measured
prevalence over the 558 harvested and seeded programs:

```
print(     93.5%      json.     16.5%      listcomp   5.9%
open(      42.7%      slice     12.4%      fstring    5.0%
for        38.4%      genexp    11.1%      try        4.8%
if         24.2%      sys.stdin 10.4%      os.        4.7%
assert     19.9%      re.       10.2%      def        3.2%
```

So: expressions, statements, comprehensions (list/dict/set/generator),
f-strings, `%` formatting, `.format()`, functions with closures, `try`/`except`,
`with`, slicing, unpacking — and the modules `sys`, `os`, `os.path`, `io`,
`json`.

`re` is the largest single gap (66 programs, 14.0%) and is **deliberately not
implemented**: lypning-mp already has a regex engine, so `import re` is a routing
decision rather than a hole. `subprocess` appears nowhere in `src/` and goes
straight to CPython.

### The three refusals that keep it honest

A subset can be wrong in two ways, and only one of them is acceptable. These are
the places where lypning refuses rather than approximates:

1. **Integers are i64; Python's are arbitrary precision.** Every arithmetic
   operation is checked and an overflow is `unsupported: bigint`, never a wrap.
2. **Set iteration order is CPython's hashing, and cannot be reproduced.** So
   order-*independent* operations on sets work (`len`, `in`, the set algebra,
   `sorted`, `min`, `max`, `any`, `all`) and anything that would expose an order
   (`repr`, iteration, `list()`, `.join`) exits 90. Dicts, whose order Python
   *defines* as insertion order, have no such restriction.
3. **`repr` of a non-ASCII character** needs CPython's Unicode category tables
   to decide whether to escape it. lypning carries a whitelist of blocks that are
   unambiguously printable and refuses the rest.

Everything CPython specifies exactly is implemented exactly, including the ones
that look like they should fall out of the host language and do not: floor
division and `%` round toward negative infinity (Rust truncates), `/` on two
ints is always a float, `float` repr is shortest-roundtrip with the
fixed/scientific switch at `decpt <= -4 || decpt > 16`, and a function's
`UnboundLocalError` comes from a real analysis of the names its body assigns.

## 4. The classifier

```
lypning route -c 'import re; print(re.findall(r"\d+", s))'
    lypning-mp   module: import re
```

Routing is a **static analysis over lypning's own front end**, not a heuristic over
the program text. That is the design:

- lypning's parser already reports the exact construct that would stop it. Asking
  the parser is therefore an *exact* answer to "can lypning run this", costing one
  parse and no process spawn.
- The tiers below cannot be asked the same way — they are separate binaries — so
  those are capability **tables** in `assets/rust/src/route.rs`, kept honest by the
  routing arm of the conformance runner.

The routing score is asymmetric on purpose (this tree, 2026-08-21, same run as
§2 — `lypning conformance` grades routes and answers together):

```
routing over 763 programs

  IDEAL       695  routed to the cheapest engine that works
  WASTED       18  engine refused; one extra spawn, right answer
  LATE         49  worked, but a cheaper engine would have too
  UNSAFE        1  routed to an engine that MISMATCHES
  NO-ENGINE     0

  accuracy 91.1% ideal, 97.5% correct-on-first-try
  predictions: lypning=489  lypning-mp=190  cpython=84
```

The one UNSAFE is the streamed-stdout defect of §2 reached through the router:
a program predicted for lypning-mp whose ideal tier is CPython. The dispatcher
recovered it, and it still counts, because a route to an engine that mismatches
costs a wrong answer rather than an extra spawn.

A wrong route costs a process spawn. A wrong *answer* costs the user's trust, so
UNSAFE is tracked separately and the dispatcher is built to recover from it.

## 5. The dispatcher, and why it is the binary itself

```
lypning run -c 'print(1 + 1)'
```

End to end, with the shares from the routing run in §4:

```
  python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'
     │
     │  the shim on PATH — or the PreToolUse hook — hands the program
     ▼  to lypning instead of to CPython
  ┌─────────────────────────────────────────────────────────────────────┐
  │ lypning run — the dispatcher IS the Rust binary, not a wrapper      │
  │ classify: ask lypning's own parser which tier can take this program │
  └───┬─────────────────────────────────┬──────────────┬────────────────┘
      │ 64.1%                           │ 24.9%        │ 11.0%
      ▼                                 │              │
  ┌───────────────────────────────────┐ │              │
  │ 1  lypning · Rust subset          │ │              │
  │    runs IN-PROCESS — zero spawns  │ │              │
  │    output staged to the barrier   │ │              │
  └───┬───────────────────────────────┘ │              │
      │ exit 90 · one line on stderr,   │              │
      │ and stdout never written        │              │
      ▼                                 │              │
  ┌───────────────────────────────────┐ │              │
  │ 2  lypning-mp · MicroPython       │◀┘              │
  │    forked, so its own refusal     │                │
  │    is catchable; streams stdout   │                │
  └───┬───────────────────────────────┘                │
      │ exit 90 · MemoryError · traceback with exit 0  │
      ▼                                                │
  ┌───────────────────────────────────┐                │
  │ 3  cpython · the reference        │◀───────────────┘
  │    exec'd — no fork, no way back  │
  │    and none is needed             │
  └───┬───────────────────────────────┘
      ▼
  the program's own stdout, the program's own exit code
```

Two properties make the mixture cheap enough to be worth having:

- **The winning case costs nothing.** A program routed to lypning runs *in this
  process* — no second spawn, no pipe. Since 96% of a one-liner's cost is the OS
  spawning a process (`docs/MICROPYTHON.md` §8c), a dispatcher that spawned a child
  would give back most of what the fast engine won.
- **Falling onward costs one `exec`, not one fork** for the terminal tier. An
  *intermediate* tier is forked instead, because its own refusal has to be
  caught — lypning-mp's capability table knows lypning-mp *has* `hashlib` and `re`, not
  that this build lacks `hashlib.md5` and `re.VERBOSE`. Measurement found 14
  corpus programs where that difference bites.

The chain moves past an intermediate engine on three signals: exit 90; a
`MemoryError` (a property of that engine's heap, never the program's answer);
and a traceback with exit 0 (which no conforming Python produces). An ordinary
non-zero exit with a traceback is deliberately *not* one of them — that is very
often the program's own correct answer, and re-running it would execute its side
effects twice.

## 6. The commit barrier — what makes falling onward safe

Routing to lypning is only sound if a lypning run that ends in `unsupported` left
**nothing** behind. Otherwise the retry re-executes the side effects and the
file is written twice, or half. So a lypning run is transactional
(`assets/rust/src/io.rs`):

- stdout and stderr accumulate in memory and are written once, at a successful
  exit;
- file writes accumulate per path, and deletes and renames are staged;
- exit 90 discards all of it, so the program is observably a no-op.

The barrier is invisible to the program and visible only to the dispatcher: a
read consults the staged writes first, so `open(p,'w').write(x)` followed by
`open(p).read()` behaves exactly as in CPython. `os.path.exists`, `getsize`,
`isfile`, `remove` and `rename` all see the overlay too.

### The barrier is lypning's alone — lypning-mp has none

**This is the sharpest asymmetry between the two subset tiers, and it is not
visible from the exit codes.** lypning-mp is MicroPython: it streams stdout as
the program produces it, so a program that prints and *then* reaches an
unsupported construct has already committed those bytes when it exits 90.

```
$ lypning-mp -c 'print("BEFORE")
import unicodedata as u
print(u.decomposition(chr(0xC0)))'
BEFORE                                            # <- already on stdout
lypning-mp: unsupported: attribute: unicodedata.decomposition   # (stderr, exit 90)

$ lypning -c 'print("BEFORE")
import subprocess'
lypning: unsupported: module: import subprocess   # (stderr, exit 90) — stdout empty
```

Two corpus programs (`py-876af0f0a956`, `py-b2a043f241f1`) reproduce it. The
upstream harness could not see them: it classified a run as UNSUPPORTED the
moment a refusal line appeared on stderr, without asking whether stdout was
already dirty. `lypning conformance` checks, and reports it as
`contract: refused after N byte(s) had already reached stdout`.

What follows from it:

- **Through the dispatcher, it is contained.** `lypning run` captures each
  tier's stdout in the parent and discards it on exit 90, so the caller sees
  exactly one tier's output. The mixture arm is clean over the whole corpus.
  The barrier for lypning-mp therefore lives in the *dispatcher*, not in the
  engine — which is a weaker guarantee, because it holds only while the
  dispatcher is the one running it.
- **Invoking `lypning-mp` directly is not safe for a program that might
  refuse.** As a drop-in `python3` in a pipeline it can emit partial output and
  exit 90, and a consumer reading stdout will act on the fragment.
- **Side effects, not just stdout.** lypning-mp stages nothing, so a file it
  wrote before refusing stays written. The retry then re-executes those writes.
  Nothing in the corpus does this today, which is luck rather than design.

Fixing it properly means buffering all output inside the MicroPython VM, which
fights the heap budget the tier exists to respect. It is tracked rather than
waived, and `lypning conformance` fails while it stands.

Two escape hatches in lypning's own barrier, both handled rather than assumed
away:

- **Size.** Past 8 MiB of buffered output the run commits early and *loses* its
  ability to fall back; a later refusal is then reported as a hard error rather
  than a routing signal. Nothing in the corpus comes close.
- **stdin.** A consumed pipe cannot be rewound. If lypning already read stdin
  before refusing, the dispatcher forks instead of exec'ing and replays the
  captured bytes.

## 7. Building

```bash
lypning build --rust                      # host musl (x86_64) — what the bench uses
lypning build --rust --target i686        # the CheerpX sandbox target
lypning build --rust --target host        # the dynamic-linking control
```

All three install over the same `~/.lypning/bin/lypning`, and the build line
names the target it just put there — a control left installed is a control
every route then uses. `lypning build --rust` puts the default musl build back.
`lypning build --dry-run` prints the cargo command without running it.

**Static musl is a precondition, not a preference.** Measured, `-c 'pass'`, min
of 30:

| build | startup | file opens |
|---|---|---|
| glibc, dynamically linked | 1.33 ms | 5 |
| musl, static | **0.24 ms** | **0** |
| lypning-mp (musl, static) | 0.21 ms | 0 |

Cold cost in the sandbox tracks bytes and file opens and nothing else, so the
dynamic loader's five opens are the entire gap. A dynamically linked lypning is
5.5x slower to start and gives back most of what the runtime won.

Zero runtime dependencies — `std` only. That follows CLAUDE.md invariant 5 and
adds a second reason: every crate linked in is bytes in a binary whose cold cost
is a step function in CheerpX's 131,072 B device blocks.

## 8. Size: where it stands

| binary | bytes | CheerpX blocks |
|---|---|---|
| lypning-mp | 269,316 | 3 |
| lypning (x86_64 musl) | 1,020,600 | 8 |
| lypning (i686 musl) | 973,428 | 8 |
| CPython 3.11 | 6,639,992 | 51 |

`opt-level = "z"` was measured at 963,256 B — 57,344 B smaller, and **still 8
blocks**, so it buys nothing under the cost model that matters. Getting to 7
blocks needs 103,096 B, which no single flag provides; the levers not yet tried
are `build-std` with `panic_immediate_abort` (nightly) and cutting the `std`
formatting machinery. This is the clearest open work item.

## 8a. In a real VM — the measurement that was missing

Every lypning figure before 2026-08-19 came from a normal Linux filesystem, with
the **x86_64** binary. CheerpX is 32-bit x86 only, so that binary cannot be
loaded in the sandbox at all: the numbers that motivate the project were taken
with an artifact that does not run in the environment they are about.

The i686 build (`lypning build --rust --target i686`) now ships in the upstream
image beside lypning-mp, and that project's VM harness has lypning probes.
Measured
2026-08-19 against `build/alpine-i386-lypning.ext2`, 5 repeats, headless CheerpX:

| probe | lypning-mp cold | lypning cold | python3 cold |
|---|---|---|---|
| `--version` (first touch of the binary) | 80 ms / **768 KB** | 193 ms / **1,280 KB** | 281 ms / 3,584 KB |
| `-c 'print(1+1)'` | 52 ms | 31 ms | 3,353 ms |
| `-c 'import json; …'` | 61 ms | 23 ms | **13,281 ms** |
| a 200,000-iteration loop | 964 ms | 815 ms | — |

Three things follow, and only the third is the one anyone expected.

**lypning costs 1.67x lypning-mp's bytes on first touch** — 1,280 KB against 768 KB,
which is the 8-blocks-against-3 of §8 showing up as real fetches. Cold cost in
this environment is a step function in 131,072 B device blocks, so size is not a
tiebreak here, it is the dominant term for anything that runs once.

**lypning's warm advantage does not clearly survive.** It is 0.102x CPython on a
normal filesystem and the fastest engine on what it accepts; here the two
subsets are within noise of each other on every probe, because a 50–85 ms exec
round-trip floor sits under all of them and neither interpreter's own execution
is anywhere near it. The loop probe — the only one with real work in it — is
0.85x, and a single run at that magnitude is not a finding.

**Both subsets keep CPython away from the ceiling, and that is the result that
matters.** `python3 -c 'import json; …'` takes **13.3 seconds** cold in this
image. The exec ceiling is 30 s and crossing it destroys the VM and ends the
agent's turn, so that one line spends nearly half the budget; lypning-mp does the
same work in 61 ms and lypning in 23 ms. The case for a Python subset in the
sandbox does not rest on lypning being faster than lypning-mp, and on this evidence it
should not be argued that way.

**Read the byte columns with the tool's ordering caveat in hand.** The IDB cache
is fresh per RUN, not per probe, so the first probe to touch a binary pays for
all of its blocks and later probes on the same binary read as free. Compare each
runtime's FIRST probe; a later one's byte count is not a size.

## 9. What is deliberately not here

- **No `re`.** 14.0% of the corpus and the single biggest routing bucket, but a
  regex engine is a large amount of code with deep semantics, and lypning-mp already
  has one. If lypning ever gets one it should be a small backtracking engine that
  exits 90 on any syntax outside a measured subset — the mixture pattern applied
  one level down.
- **No `subprocess`, threading, or networking.** They appear in the corpus only
  as CPython-routed programs.
- **No classes, decorators, generators, or `async`.** Under 4% combined, and
  each is a routing decision today.
- **No daemon.** The same reasoning that ruled one out for lypning-mp
  (`docs/MICROPYTHON.md` §4) applies unchanged: interpreter init is a rounding error
  inside the process-spawn floor.

## 10. Files

| path | what |
|---|---|
| `assets/rust/Cargo.toml` | zero-dependency crate, size-tuned release profile |
| `assets/rust/src/lex.rs` | tokenizer, layout (INDENT/DEDENT), string prefixes and escapes |
| `assets/rust/src/parse.rs` | recursive-descent parser; every gap becomes `unsupported: <kind>` |
| `assets/rust/src/ast.rs` | the subset AST |
| `assets/rust/src/eval.rs` | tree-walking evaluator, real scope chains, `UnboundLocalError` analysis |
| `assets/rust/src/value.rs` | values, insertion-ordered dict, set, the bigint and set-order refusals |
| `assets/rust/src/ops.rs` | operators, indexing, slicing, `%`-formatting, Python's floor/mod rules |
| `assets/rust/src/iter.rs` | iteration; lazy `range`, file lines and generator expressions |
| `assets/rust/src/fmt.rs` | `str`/`repr`, float repr, the format-spec mini-language |
| `assets/rust/src/builtins.rs` | the builtin functions, chosen by corpus frequency |
| `assets/rust/src/methods.rs` | str/list/dict/set/bytes/file methods; the tables the router reads |
| `assets/rust/src/modules.rs` | `sys`, `os`, `os.path`, `io`, `json` |
| `assets/rust/src/json.rs` | JSON parse + dump, written against CPython's exact output |
| `assets/rust/src/io.rs` | files, streams, and the commit barrier |
| `assets/rust/src/route.rs` | the classifier |
| `assets/rust/src/main.rs` | CLI, the exit contract, and the dispatcher |
| `assets/scripts/build-rust.sh` | the standalone build, with the shape and contract smoke checks |
| `lypning build --rust` | the same build, driven from the CLI |
| `lypning bench` | the four-arm benchmark |
| `lypning conformance` | three engines + the mixture + routing accuracy |
| `tests/test_engines.py`, `tests/test_conformance.py` | the unit half (`make test`) |
