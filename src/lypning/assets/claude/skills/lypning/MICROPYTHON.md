---
name: lypning-mp
description: >-
  The second tier of lypning's mixture — a MicroPython variant with a frozen
  shim stdlib, static musl i386. Load when building it (`lypning build
  --micropython`), when a program was routed to lypning-mp and refused, when a
  conformance run reports MISMATCH on that arm, when python startup or binary
  size in a sandbox is the question, when adding a module to its frozen stdlib,
  or when growing the harvested corpus both tiers are built against. Covers the
  cost model that justifies the project, the two gates and how to read them, the
  STOCK-MICROPYTHON CONTROL, the capture harness, and the traps already paid for
  — including a .gitignore that silently swallowed the whole frozen stdlib and a
  measurement bug that inverted into a pass.
---

# lypning-mp — the minimal Python for the sandbox

**lypning-mp** is a MicroPython unix-port variant — static, musl, i386, frozen
stdlib — that runs the python one-liners an agentic CLI actually types, inside
the CheerpX Linux VM. It is deliberately not a Python implementation.

Full charter: `docs/MICROPYTHON.md`. Subset spec: `docs/SUBSET.md`. The survey
that chose the base: `docs/RESEARCH.md`.

## 1. The one number that justifies everything

`docs/SANDBOX-PERFORMANCE.md` §1, measured against production:

> `python3 --version` — **8573 ms cold**, 87 ms warm. The exec ceiling is 30 s,
> and crossing it **destroys the VM and ends the agent's turn.**

The cost is not CPU. The root filesystem streams block by block over a
WebSocket, so cold cost tracks **bytes and file-opens**, nothing else. CPython
opens 22 files on `-c 'pass'`, probes 7 more that do not exist, and makes 65
stat calls, from a 6.6 MB dynamically linked binary. lypning-mp opens **zero**.

**So optimise for file touches, not RSS and not warm throughput.** Any change
that adds a file the interpreter reads at startup is a regression even if it
makes the binary smaller.

Three costs bound what is achievable and no runtime beats them: a **50–85 ms
exec round-trip floor** per command, **6.5 ms** per process spawn, and
**~1.1 MB/s** returning output to JS.

## 2. The two gates — run both, they answer different questions

```bash
lypning gate --compare                        # shape (defaults to lypning-mp)
lypning conformance --engine lypning-mp       # correctness
```

**The build gate** checks static / ≤700,000 B / ≤3 file opens on `-c 'pass'`,
and prints CPython beside it. It projects a cold cost from the measured shape —
that projection is a convenience, never the acceptance. `docs/MICROPYTHON.md` §2
accepts on a cold run in a real VM, never on the projection.

**The conformance runner** executes every corpus entry under *both* CPython and
lypning-mp and splits the result three ways. The split is the whole point:

| verdict | meaning | is it a failure? |
|---|---|---|
| MATCH | stdout + exit code identical to CPython | no |
| UNSUPPORTED | exit **90** with `<engine>: unsupported: <kind>: <detail>` | **no** — this is coverage, and the build order |
| MISMATCH | anything else | **yes, always** |

Silent semantic divergence is the one outcome that would make a subset runtime
worse than nothing, because the agent that typed the one-liner will not notice.
MISMATCH must be zero; UNSUPPORTED is just work not done yet.

Useful invocations:

```bash
lypning conformance --engine lypning-mp --plan   # just the ranked build order
lypning conformance --limit 50                   # the first 50 programs, for a fast loop
lypning conformance --json                       # machine-readable
```

`--plan` ranks every missing feature by how many corpus entries it unblocks.
**That is the build order.** Re-run after each module — the counts shift as
features land, because an entry is only ever blocked by the first thing the
interpreter hits.

## 2b. The third measurement — what our variant COSTS

The two gates answer "is the shape right" and "is the answer right". Neither
answers "what did our changes cost", and lypning-mp's own timings cannot: a number
with nothing to divide by is not a measurement. So there is a **control** —
stock MicroPython, same pinned commit, same musl-i386 toolchain, unpatched, no
frozen lypning-mp stdlib.

```bash
bash assets/scripts/build-micropython.sh --stock   # → assets/micropython/build/micropython-stock
lypning bench                                      # the four arms: startup and corpus
```

The per-subsystem variant-vs-stock table in `docs/BENCH-LEDGER.md` was produced
by an upstream harness that did not come across with the extraction; the numbers
below are its readings, and `lypning bench` is what this package measures with.

**Run it after any variant or patch change, any addition to `micropython/lib/`, and
any bump of the MicroPython pin** — and before/after any change made in order to
be faster, since that is the only way to know whether it was. It is **not in
CI**: a wall-clock benchmark on a shared runner measures the runner. CI keeps
the deterministic half (size, opens, conformance), which catches the changes
that would move the timings anyway.

Four things about how it reads, each of which is a way to get it wrong:

- **The verdict is the floor-subtracted MIN ratio**, lypning-mp/stock. Noise on a
  shared box is one-sided — it can only add time — so the minimum is the least
  biased estimate. The median is printed beside it, and a row where the two
  disagree by >25% is marked `!` and **is not a finding**.
- **Startup is subtracted from every workload.** `-c 'pass'` is ~0.9 ms for both
  builds; without subtracting it a 3 ms workload reads as a 4 ms one.
- **`unsupported` is data.** Stock has no `re.findall`, no match `.start()`, no
  `Counter` — those are the frozen stdlib. The run records the reason and
  continues. `ERROR` is the other thing and should be chased.
- **Decompose before blaming a subsystem.** `re-sub-ascii` is 10.9× — and
  `ure.sub` — the NATIVE sub, reached without our shim — is **0.68×**. So the
  gap is `micropython/lib/re.py`'s Python `_subn`, not the engine underneath it. Note
  the engines are not byte-identical: our patch edits `lib/re1.5/charclass.c` to
  make `\w` unicode-aware, so this pair isolates *shim vs no shim*, not
  *patched vs unpatched engine*. A case pair like this is worth adding whenever
  a shim wraps something in C.

Standing results (2026-08-14, first entry in the ledger): startup at parity;
the ordered-dict quadratic at 2.1× / 4.0× / 7.7× / 9.4× for 1k/5k/10k/20k keys
and **12.4× on lookup**; `json.dumps` 3.8× from the same cause; exact float repr
1.9× with its fixed-precision control at 0.86×; `\w` on non-ASCII 1.84×, which
is not a clean cost because the two builds match different amounts of text.

## 2c. The fourth measurement — the corpus, which is what anyone actually waits for

The gates answer "is the shape right" and "is the answer right", and the bench
answers "what did our variant cost against stock, per subsystem". None of them
answers "did this change make the programs lypning-mp is asked to run faster".

```bash
lypning bench --corpus                     # every arm, every program
lypning bench --corpus --repeat 5          # min of 5, arms interleaved per entry
LYPNING_MP_BIN=./candidate.bin lypning bench --corpus --arm lypning-mp --arm cpython
```

Every program in `corpus.jsonl` and `seed-corpus.jsonl`, min of repeats, both
arms interleaved per entry, each in its own temp cwd with `LYPNING_CAPTURE=0`.
**This is the acceptance instrument for any change made in the name of speed** —
the bench is the diagnostic that says where the time went. The tool prints the
count it loaded; do not carry a remembered number, because the capture harness
grows the corpus every session. It was 351 when the instrument was written, and
this sentence originally named the figure a couple of days later — which was
already wrong by 51 before the change landed. Run `loadCorpus()` if you need the
number; do not quote one from here.

**Run both, and expect them to disagree.** Every technique tried in the
2026-08-15 pass had the two instruments telling different stories: the bench said
`re.sub` got 16x faster and the corpus said 0.998x; PGO looked free on the corpus
and was 11% SLOWER on a held-out workload. Neither instrument is wrong — a
corpus one-liner is 1.7 ms of which **0.04 ms is lypning-mp's own code** (interpreter
init 0.96 ms against a 0.92 ms empty-C floor), so it cannot show a steady-state
win, and a 2,000-iteration microbenchmark is nothing but steady state.

## 2d. Compiler optimisation is FINISHED here — do not re-survey it

The 2026-08-15 pass measured the whole compiler lane and every technique lost.
`docs/MICROPYTHON.md` §8c has the table and the reasoning; the short version, so
nobody spends another session on it:

- **`-O2` everywhere: +66,624 B (+24.7%) for 0.994x on the corpus.** Rejected.
- **PGO: 0.996x on the corpus even when trained on that same corpus, and
  1.07–1.11x — SLOWER — on held-out workloads.** The profile demotes the code the
  training set does not exercise, and lypning-mp's two distributions (one-liners that
  exit; the occasional loop over a big input) cannot both be profiled. Rejected.
- **PGO at `-Os` is inert by construction**: `optimize_size` gates every
  speed-for-size transform, so a profile buys block layout and nothing else.
- **GCC's `libgcov.a` will not statically link against musl** (glibc/fortify
  symbols: `mmap64`, `open64`, `fopen64`, `fcntl64`, `__memcpy_chk`,
  `__isoc23_strtol`, three `*printf_chk`). A nine-alias shim fixes it, if you
  ever need an instrumented build for something else.
- **`llvm-bolt` has no i386 target.** Section ordering buys nothing anyway —
  see below.

**Why the whole lane loses:** 96% of an invocation is the OS spawning a process.
Codegen quality can only touch the 4% that is lypning-mp's own startup plus a few
hundred microseconds of execution. **The wins are algorithmic and above the
compiler** — the same pass got 16x on `re.sub` by not re-slicing a string.

**The cost model's missing constant: CheerpX streams the image in 131,072 B
device blocks.** Cold cost is a STEP function, not linear in bytes. The binary is
three blocks; a saving that does not cross a boundary streams the same number of
fetches. The next **6,980 B** would take it under 262,144 B and remove a whole
fetch — which is what makes the 4,096 B cuts §4c declined worth taking as a
bundle, and what makes function placement worth exactly zero.

## 3. The corpus grows by itself — do not hand-write entries

`lypning install` puts a `python`/`python3` shim early on `$PATH` that logs
every invocation, plus a PreToolUse hook that catches command strings the shim
cannot see (heredocs, `uv run`). `lypning harvest` merges those and any Claude
Code transcript into `assets/corpus/corpus.jsonl`, deduped by normalised program
text. Both hooks are merged into `.claude/settings.json` by the same command, so
every session in a wired repo feeds it.

```bash
lypning shim install            # idempotent; also `shim status`, `shim uninstall`, --force
lypning harvest                 # fold everything into the corpus
lypning harvest --export        # what the Stop hook runs
```

**What is COLLECTED is `tests/corpus/sightings/<session>.jsonl`, not the corpus**
(2026-08-14 — `docs/MICROPYTHON.md` §7b). The Stop hook used to fold each session's
log into `corpus.jsonl` directly and the collection rate was still zero: one
shared file that every session rewrites conflicts across branches, and merging
it was never worth it to a PR about something else. Of the 19 branches cut since
the corpus landed, **2 carried growth, 0 reached main, 17 sessions were lost.**
A shared mutable file is not a collection point when the writers are ephemeral
containers on independent branches.

Now the Stop hook publishes one file per session (one writer per path, so no
branch conflicts, and an unrelated PR carries an *added* file) and `corpus.jsonl`
is DERIVED — regenerate it with `lypning harvest` whenever you are working on
lypning-mp; no session has to. The hooks never run `git`, so staging that
directory is yours to do.
Sighting keys are namespaced by session because a log line number means
something different in every container — that namespacing is also what stops the
fold counting one invocation twice when it reads both a session's live log and
its own published file.

**A session cannot publish a converged copy of its own sightings, and chasing
one is a loop** (found 2026-08-15 while merging #456/#459). A sighting key is
per INVOCATION — `hook:<session>#<n>` and `transcript:<file>#<tool-use-id>` —
while the corpus fold dedupes by program hash. So re-running the same program
adds rows to the sightings file and nothing downstream. The trap is that
*checking* a published file is itself an invocation: validate it with python
(unique keys, sortedness, a set difference against the committed copy) and
`--export` immediately has two more rows to publish, which is a dirty tree
again, which invites another check. Three commits went that way before the
shape was clear. **Publish once, then inspect with `git`/`grep` only** — or
accept that the file trails the container by however much work the publishing
took, which is what `--export` is designed for anyway. Nothing is lost either
way: the next session's export picks up the remainder, and the fold collapses
the duplicates.

Two rules that keep the evidence honest:

- **The two corpus files stay separate.** `seed-corpus.jsonl` is written from
  expectation; `corpus.jsonl` is harvested from real invocations. Blending them
  lets guesswork inflate the frequency table that decides build order.
- **Harvest redacts before committing.** Captured programs embed repo content;
  the `scripts/scan-secrets` patterns run over every entry. This is not
  theoretical — two credential-shaped tokens were redacted on the first real
  harvest.

**Separate FILES did not achieve separation, and the first harvest was mostly
fiction** (found 2026-08-14 — `docs/MICROPYTHON.md` §7a). The conformance runner
executes every corpus entry, resolved the reference interpreter by NAME, and so
found the capture shim first on `$PATH`: every run logged 212 invocations that
the next harvest merged back as observed evidence. **138 of the 197 "observed"
programs were byte-identical to seed programs**, and 139 sat at `count=8` — one
per conformance run, not a Zipfian spread. `--plan` ranks build order by those
counts, so the loop ranked guessed-at programs above real ones. Meanwhile the log
at `$HOME/.lypning/` never reached the repo: `corpus.jsonl` was committed once and
every `first_seen` falls in one 36-minute window.

Now: the runner resolves a real CPython ELF **and** spawns with
`LYPNING_CAPTURE=0` (either alone closes the loop); harvest drops seed-identical
sightings as `seedCollision` — on publication as well as on the fold — and says
so on every run; a `Stop` hook publishes before teardown without committing.
**The committed counts are still inflated** — the fixes stop them growing,
nothing can retroactively correct them — so treat `count` as an upper bound and
any build order from it as provisional.

The guard applies to new SIGHTINGS only. Do not run it over already-committed
records: §7a's rule is that it guards against new contamination rather than
rewriting evidence, and applying it to `main`'s own corpus deletes 138 records.

Two traps if you touch this: a stand-in shim in a test must reproduce the real
one's `$HOME/.lypning/` fallback, because `runOne` strips `LYPNING_LOG` but keeps
`HOME` — the first loop test passed with every defence removed. And mutate the
defences **separately**; running the test against fixed code alone would not
have caught that.

## 4. Building

```bash
lypning build --micropython                # musl-i386 from source, pinned MicroPython, variant, strip
lypning build --micropython --dry-run      # print the command and the cache state; build nothing
```

That drives `assets/scripts/build-micropython.sh`, which can also be run
directly. It needs `gcc-multilib` and a network for two pinned downloads; both
are checked before anything is started, and a missing one is named rather than
discovered five minutes in.

`gcc -m32 -static` works in the dev container **and i386 binaries execute
there**, so the real target artifact can be built and gated in CI with no
browser, no VM, no Docker and no cross toolchain. Building musl for i386 from
source takes under a minute. Two flag traps: the target is `i686`, not `i386`,
and it needs `AR=ar RANLIB=ranlib`; the `musl-gcc` wrapper then needs
`-Wl,-m,elf_i386`.

**musl is a precondition, not a preference.** An empty `main` is 635,744 B
under glibc-static i386 and 13,020 B under musl. The 700 KB gate is unreachable
with glibc before a single line of interpreter exists.

**The `sys.path` pin is load-bearing.** MicroPython probes `sys.path` *before*
consulting the frozen table, at 3 `statx` per entry per module. Trimming the
path to `['.frozen']` cut a workload from 56 syscalls to 26. Verify with
`strace` after any variant change — this is exactly the pathology the project
exists to avoid, and it is invisible without measuring.

## 4a. Optimising it further — what is left, and what is settled

The 2026-08-14 pass took the binary 390,456 → 304,440 B (−22%) with 0 MISMATCH
throughout. `docs/MICROPYTHON.md` §8a has the full table. What matters when you come
back to this:

**Opens are at zero and cannot improve.** A six-module import costs 13 syscalls
and no file syscalls at all. Cold cost tracks bytes and opens, so **bytes are
the only lever left** — check `size -A` before theorising about anything else.

**The wins were dead weight, not tuning.** `.eh_frame` was 51,644 B of DWARF
unwind tables that nothing could read (MicroPython raises through setjmp, and
`nm` shows zero `_Unwind`/`__cxa`/`backtrace` symbols); `--gc-sections` does not
collect `.eh_frame`, so it survives size passes while looking legitimate. Then
`framebuf` + `uctypes` at 9,789 B. **The test for cutting a module is not "is it
in the corpus" but "does CPython have it"** — conformance is defined against
CPython, so a MicroPython-only module can never appear in a MATCHing entry.
That is why `framebuf`/`uctypes`/`micropython` went and `heapq` stayed despite
zero corpus references.

**LTO is on, and it is the one flag that is also a correctness risk.** It buys
16,384 B and 16% workload speed, but LTO across setjmp/longjmp is the classic
miscompile and that is exactly how MicroPython raises. The six `nlr-*`
seed-corpus entries exist for this. If you bump MicroPython or change LTO flags,
those are the entries to watch.

**Settled negatives — do not re-run these:** disabling computed goto (saves
4,096 B, costs 0.14 ms per program, break-even at ~37 programs per session);
cutting `complex` (the `1j` literal escapes the exit-90 contract and closing it
costs a port-patch hunk); `ld.lld` (cannot consume GCC's GIMPLE LTO plugin);
`ld.gold --icf=safe` (folds 14 bytes and forbids `-z noseparate-code`); `-Oz`
(a clang flag, not GCC).

**Two traps when measuring.** File size is quantised to the 4,096 B page, so
unrelated changes all report −4,096 B and one of them may have moved 2,100 B —
use `size -A`. And a synthetic heavy workload overstates VM speed by ~10× against
the real corpus; time the 340 corpus programs, not a benchmark you wrote.

Rebuilding after a **config** change needs the generated headers dropped —
`rm -rf micropython/.build/micropython/ports/unix/build-lypning-mp` — or a stale
`moduledefs.h` keeps a disabled module in the builtin table and the link fails
on `undefined reference to mp_module_framebuf`.

## 4b. The contract only ever worked from C — check this first

**The single most important thing found in the 2026-08-14 passes.**
`lypning_exit_not_implemented()` hangs off `py/runtime.c`'s
`mp_raise_NotImplementedError()`, which is **C**. The frozen shims raise
`NotImplementedError` from **Python**, so every shim-level gap exited **1 with a
traceback** instead of **90 with one line** — the message was right and the exit
code, the channel and the line count were all wrong. Nothing caught it because
the corpus had no entry for a shim gap.

Three rules follow, and each cost a build to learn:

- **Two call sites are required.** `-c` and a script FILE leave through
  `shared/runtime/pyexec.c`; a program on **stdin** leaves through `main.c`'s
  `handle_uncaught_exception()`. Deleting either silently returns that path to
  exit 1. Found by removing one and watching stdin break.
- **Only the marker prefix may trigger the 90.** A program's own
  `NotImplementedError` must keep its traceback and exit 1. Hijacking it would be
  worse than the original bug.
- **A shim that declares a constant must honour it or refuse it.** `re.VERBOSE`
  and `csv`'s `quoting=` were declared and ignored, returning wrong answers at
  exit 0. The shims now reject the whole class — any unimplemented `re` flag by
  name, any `csv` keyword whose value differs from actual behaviour. When adding
  a shim, grep it for constants it defines but never reads; that is where the
  next one of these lives.

All of it is pinned by smoke checks in `assets/scripts/build-micropython.sh` and by the
`re-verbose-flag` / `re-ascii-flag` / `csv-quote-all` / `csv-escapechar` corpus
entries.

## 4c. Size: what is taken, and what is settled

Two passes took 390,456 → **269,124 B (−31%)**. `docs/MICROPYTHON.md` §8a and §8b have
the tables. Taken, in descending order: unwind tables (−49,152), `-fno-pie`
(−12,288), `main.c` `fprintf`→`mp_printf` (−11,604, which was holding musl's
entire `vfprintf` engine), LTO (−16,384, also 16% faster), the i386 codegen pack
(−8,192), `framebuf`+`uctypes` (−12,288), `mpy-cross -O3` (−2,592), and three
padding flags.

**Settled negatives — do not re-run:** UPX (opens go 0→1 on `/proc/self/exe`,
needs a third-party binary in CI, and pays decompression every run);
`-mregparm=3` (segfaults — musl's i386 `memcpy` is cdecl assembly, and 69 `.s`
files mean rebuilding musl cannot fix it); stripping shim docstrings (**0 B** —
`mpy-cross` sets `MICROPY_ENABLE_DOC_STRING=0`, the parser already discards
them); dropping frozen modules (4,096 B for 7 lost MATCHes); computed goto;
cutting `complex`; `ld.lld`; `ld.gold --icf`; `-Oz`.

**Three measurement rules.** File size is quantised to the **4,096 B page** — use
`size -A` for anything sub-page. **Measure combinations, never sum them**:
`-fno-pie` and the codegen pack compose at 96.5% and two lanes each missed the
other's flag. And a synthetic workload overstates VM speed ~10× against the real
corpus, so time the 340 corpus programs instead.

**Where a flag goes matters.** Anything inside the SHARED TOOLCHAIN BLOCK markers
in `mpconfigvariant.mk` is extracted verbatim into the stock control, so codegen
flags belong there — put them below the marker and the benchmark quietly starts
measuring the compiler instead of lypning-mp. `MPY_CROSS_FLAGS` is the exception: it
only affects the frozen stdlib, which the control does not have.

Rebuilding after a **config** change needs the generated headers dropped —
`rm -rf micropython/.build/micropython/ports/unix/build-lypning-mp/genhdr` — or a stale
`moduledefs.h` fails the link on `undefined reference to mp_module_framebuf`. A
`.mk` change does **not** invalidate the frozen artifacts either; drop
`build-lypning-mp/frozen_mpy` and `frozen_content.{c,o}` when changing
`MPY_CROSS_FLAGS`.

## 4d. Measuring in a REAL VM (the number §2 accepts on)

```
(upstream only — the image builder and the headless-VM harness stayed with
 deepresearch.se and are not part of this package)
```

This needs no deploy, no R2 and no live site: it serves a local ext2 over HTTP
Range, boots the pinned CheerpX in headless Chromium with the same device stack
and mounts as `public/js/sandbox.js`, and times probes against a fresh IDB cache.

**Quote the BYTES column, not the milliseconds.** The harness streams over
loopback and production streams over a WebSocket, so timings are optimistic by
~26× (`python3 --version`: 318 ms here, 8573 ms in production). Bytes are
transport-independent and are what the cost model is built on.

Results (2026-08-14, `docs/MICROPYTHON.md` §1a): `lypning-mp -c 'import json…'` streams
**zero bytes** cold — the frozen stdlib confirmed directly, not projected — and
**CPython cannot run a one-liner in this image at all**: `python3 -c 'print(1+1)'`
streams ~2.3 MB, freezes, and wedges the VM (verified over 435 s of static byte
counters; `-S` does not help), while `python3 --version` succeeds in 318 ms.

Six obstacles, all of which will recur:

- **Every HTTPS request from Chromium resets through the agent proxy**, so the
  CheerpX CDN import is unreachable from the browser. The tool vendors the
  engine into `.cache/cheerpx/` and lazily mirrors misses server-side — the
  server can reach the CDN even though the browser cannot.
- **`HttpBytesDevice` refuses to initialise without `ETag` or `Last-Modified`.**
  R2 sends an ETag so production gets this free; any hand-rolled origin must.
- **CheerpX loads a wider asset graph than its loader names** — `tun/direct.js`,
  `tun/ipstack.js`, `tun/wasm_exec.js` all load at boot with networking unused.
  Enumerating by hand does not converge; fetch-on-miss does.
- **`pgrep -f` / `pkill -f` kills the calling shell** when the pattern appears in
  its own command line. This is already trap #4 in §5 and it still cost three
  shells. Enumerate `/proc/*/cmdline`, or bind by port.
- **`| head -N` in a monitored pipeline delivers nothing** until N lines
  accumulate, so a live run looks hung when it is fine. Write to a file.
- **Escaping crosses JS → shell → Python.** `\d` silently became a literal
  backslash and the probe printed `[]` at exit 0 — measuring the wrong thing
  successfully. Use `[0-9]` and avoid the layers.

## 5. Traps already paid for

Each of these cost real time and each would recur.

- **A bare `lib/` in the root `.gitignore` swallowed the entire frozen
  stdlib.** It comes from the standard Python-packaging template, and unanchored
  it matches a directory named `lib` at *any* depth — so every module in
  `micropython/lib/` was untracked. It built perfectly locally and would have shipped
  a stdlib-less binary from CI. Now `/lib/`. **Check `git check-ignore -v` on a
  new source directory before trusting that it is committed.**
- **Tracebacks must go to stderr.** lypning-mp wrote uncaught tracebacks to
  *stdout*, which poisons a pipeline — `stdin → transform → stdout` is the
  corpus's largest cluster, so `lypning-mp … | wc -l` counted the traceback. The
  exit code was right, which is what makes it insidious. The exit-90 contract
  line has the identical failure mode; both are pinned by tests.
- **A measurement bug can invert into a pass.** The gate's strace parser missed
  the bare-pid prefix that `-f -o` emits and read a 110-line trace as **zero
  file opens** — a perfect score on the project's central metric. Zero is the
  target, so nothing looked wrong. Prefix forms are now pinned in
  `tests/test_gate.py`.
- **The capture shim breaks naive baselines.** With the shim installed,
  `command -v python3` returns an 8,971-byte shell script; measuring it as "the
  baseline" gave 30 file opens and a projected cold cost of 330 seconds. A wrong
  baseline is worse than none because it looks like a number. Use
  `engines.find_cpython`, which walks PATH past anything carrying the shim
  marker.
- **Per-entry temp cwd breaks relative binary paths.** The conformance runner
  gives every entry its own temp directory (so file-writing entries stop
  littering the repo, and so lypning-mp cannot read back a file CPython created).
  That makes a relative `LYPNING_MP_BIN=...` resolve against the temp dir.
  Resolve to absolute once — `lypning status` prints the path it resolved.
- **A generated index that enumerates TRACKED files goes stale the moment a
  change ADDS one.** Regenerating before `git add` silently skips the new files:
  the committed snapshot described 1089 files while the working tree had 1092,
  the suite passed locally because the artifacts and the untracked files were
  consistent right up until the commit made them tracked, and CI failed on both
  drift tests. Editing an existing file does not have this failure mode, which
  is why it takes a while to bite. Also note such a snapshot indexes `docs/` and
  the skills, not just the source — a docs-only or skill-only edit stales it
  too, and the fix is always to regenerate, never to edit an artifact.

- **Exiting hard truncates piped stdout.** `--json` was cut mid-object because
  the process exited before the pipe drained. Return an exit code and let the
  runtime flush; `cli.py` flushes `sys.stdout` in a `finally` for this reason.

## 6. What is deliberately NOT here

- **No daemon.** `lypningd` was an explicit requirement and was measured before
  being dropped: interpreter init is **0.96 ms against a 0.92 ms empty-C
  floor**, so a zygote amortises 0.04 ms inside a 50–85 ms exec floor it cannot
  touch — and signal delivery and process termination **do not work in the
  CheerpX guest**, which is a fork server's core loop. Owner-confirmed
  2026-08-13. The design is preserved in `docs/MICROPYTHON.md` §4 with the
  measurement that would justify reversing it. Do not rebuild it on intuition.
- **No `subprocess`.** It appears nowhere in this repository, so excluding it is
  evidence rather than taste. It must exit 90 rather than fake a shell-out, so
  the agent hoists the command into its own bash block.
- **lypning-mp is not a platform-wide python replacement.** It wins in the streamed
  in-browser VM. In `container/Dockerfile` — a normal container on a normal
  filesystem — CPython's cold cost is a page-cache miss, and lypning-mp buys little.

## 7. Honest scope

Cold **VM boot** still dominates a sandbox turn: 24.4 s of boot against 290 ms
of commands. lypning-mp improves a real but **secondary** term. It is worth doing
because 8.5 s is a large secondary term that can cross the 30 s ceiling and
destroy the VM, and because dropping CPython takes 27.0 MiB and 16 shared
libraries out of an image whose whole design goal is to stream without
stalling. It does not make the sandbox fast, and no user-facing copy should say
so.
