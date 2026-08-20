# lypning-mp — a minimal Python for the in-browser sandbox

*Charter and design. Started 2026-08-13.*

**lypning-mp** (py + gram) is a Python-subset runtime built for one job: run the
python one-liners an agentic coding CLI actually types, inside the in-browser
CheerpX Linux VM, without the cold-start cost that makes `python3` unusable
there.

It is deliberately **not** a Python implementation. It is the smallest thing
that executes the observed corpus. Everything it does not cover, it must fail
on loudly and predictably (§6).

> **Status: design + build in progress.** Nothing here is deployed. The
> companion documents are `docs/RESEARCH.md` (what to build it from),
> `docs/SUBSET.md` (what it must execute), and the corpus at
> `tests/corpus/`.

## 1. Why — the measurement that justifies the project

From `docs/SANDBOX-PERFORMANCE.md` §1, measured by
`tests/e2e/sandbox-perf.spec.js` driving a real Chromium against production:

| command | cold | warm | ratio |
|---|---|---|---|
| `python3 --version` | 8573 ms | 87 ms | 98× |
| `perl -e 'print 42'` | 8333 ms | 108 ms | 77× |

Eight and a half seconds to print a version string. The exec ceiling is 30 s,
and a command that crosses it **discards the VM and ends the agent's turn** —
so a cold python is not merely slow, it is within one factor of fatal.

The cause is not CPU. The root filesystem is an ext2 image streamed block by
block over a WebSocket and cached in IndexedDB, so the first execution of a
binary pulls its ELF, every library it links, and every file it opens, over the
network. CPython is close to the worst possible shape for that: a dynamically
linked interpreter plus `libpython`, plus `import site`, plus the `encodings`
package, plus a `/usr/lib/python3.11/` tree that a trivial `-c 'pass'` walks a
surprising amount of.

**So the metric lypning-mp is optimised against is bytes and file-opens touched on
a cold run — not RSS, not warm benchmark throughput.** A fully static binary
with its standard library compiled in touches exactly one file: itself.

The other three costs bound what any solution can achieve
(`docs/SANDBOX-PERFORMANCE.md` §2–§4):

- **50–85 ms exec round-trip floor**, per command, unavoidable. No runtime and
  no daemon beats this.
- **6.5 ms per process spawn.**
- **~1.1 MB/s** returning output across the VM→JS boundary.

The 50–85 ms floor sets the target. There is no point making lypning-mp faster than
the envelope it arrives in; the goal is to get a cold one-liner from ~8.5 s down
to *within the same order as the floor*.

## 1a. Measured in a real VM (2026-08-14)

The acceptance metric below was blocked on work item 8 from the day this file was
written. It is now measured. `scripts/vm-measure.mjs` builds nothing and
deploys nothing: it serves a local ext2 over HTTP Range, boots the pinned CheerpX
in headless Chromium with the same device stack and mounts as
`public/js/sandbox.js`, and times probes with a fresh IndexedDB block cache.

Against a 131 MB Alpine i386 image carrying both interpreters:

| probe | cold | warm | bytes streamed cold |
|---|---|---|---|
| `lypning-mp --version` | 86 ms | 16 ms | 1,152 KB |
| `lypning-mp -c 'print(1+1)'` | 43 ms | 21 ms | 128 KB |
| `lypning-mp -c 'import json; …'` | 27 ms | 23 ms | **0 KB** |
| `lypning-mp -c 'import re; …'` | 29 ms | 25 ms | **0 KB** |
| `python3 --version` | 318 ms | 44 ms | 3,460 KB |
| `python3 -c 'print(1+1)'` | **never completed** | — | 2,304 KB, then frozen |

**Read the bytes column, not the milliseconds.** This harness streams the image
over loopback while production streams it over a WebSocket from R2, so the
timings are optimistic by roughly 26× — `python3 --version` is 318 ms here
against the 8573 ms §1 quotes. Bytes are transport-independent: the same command
pulls the same blocks either way, and §1 says cold cost tracks bytes and opens.

Two results matter more than the speed-up:

**The frozen stdlib streams nothing.** `import json` and `import re` pull **zero
bytes** off the disk image. Not few — none. That is the entire design thesis
(§1: "a fully static binary with its standard library compiled in touches exactly
one file: itself") confirmed directly rather than by projection, and it is why
lypning-mp's second and third one-liners cost less than its first.

**CPython does not merely lose, it fails.** `python3 -c 'print(1+1)'` streams
about 2.3 MB and then the block fetches stop dead; the command never returns and
the VM is wedged. Verified over 435 s of frozen byte counters, from a fresh boot,
reproducibly, and `-S` does not save it — while `python3 --version` in the same
VM succeeds in 318 ms. So in this image CPython can print its version and cannot
run a program. §9's first falsification test ("if a static lypning-mp still takes
seconds cold, the cost was somewhere else") is answered: it does not.

**What this does not establish.** The image is not deployed — `/api/sandbox-image`
still serves the third-party WebVM disk, so no user is affected yet. The hang is
Alpine's CPython 3.14.7 on the pinned CheerpX 1.2.6; the 8573 ms figure came from
Debian's 3.11, so the two are not the same binary and the wedge may not be
universal. And loopback is not the WebSocket path. What is now measured is the
ratio and the byte cost; the absolute production timing still needs the image
deployed.

## 2. Acceptance metric

One number, measured the same way the table above was measured — a new case in
`tests/e2e/sandbox-perf.spec.js`, against a real VM, cold cache:

> **A cold `lypning-mp -c '<typical corpus one-liner>'` completes in under 500 ms,
> and a warm one within the 50–85 ms exec floor.**

Secondary, checked locally and cheaply in CI:

| gate | target | how it is checked |
|---|---|---|
| stripped static binary size | ≤ 700 KB | `lypning gate` |
| file-opens on `-c 'pass'` | ≤ 3 | same, via `strace -f -e trace=file` |
| linked shared objects | 0 | same, `file` reports "statically linked" |
| corpus conformance | 100% of Tier 0 | `npm run lypning:conformance` |

`lypning gate` enforces the first three and prints the baseline
alongside, so a build change is judged in seconds instead of a Playwright run.

### The baseline, measured

`lypning gate <bin> --compare` on this dev container, against
the system CPython 3.11:

| | CPython 3.11 | an empty static i386 C binary |
|---|---|---|
| binary | 6,639,992 B, dynamically linked | 703,120 B, statically linked |
| distinct files opened on `-c 'pass'` | **22** | **0** |
| failed open probes | 7 | 0 |
| `stat`/`access` calls | 65 | 0 |

Twenty-two files opened, seven more probed and missed, sixty-five stat calls —
each one a lookup that in the VM crosses a network. That is the 8573 ms.

The 703 KB figure is also a warning: that is glibc-static with **no interpreter
in it at all**. The same empty program against musl is 18,688 B on x86-64 and
13,020 B on i386. So a musl-based static build is not a preference, it is a
precondition, and `docs/RESEARCH.md` §2.1 has the working i386 musl
build — from source, in this container, in under a minute, no Docker and no
cross toolchain.

That measurement also retired the original 400 KB size gate. It was set before
the floor was known and was unreachable for anything that speaks Python: an
empty `main` is 635,744 B under glibc-static i386, and Berry — a complete,
mature dynamic-language VM with **neither `re` nor `json`** — compiles to
365,660 B. **The gate is 700 KB**, set against the measured 541,688 B
MicroPython prototype with room for the frozen shims.

### The toolchain works here, which makes the gate cheap

Verified in this container: `gcc -m32 -static` produces a working
`ELF 32-bit LSB executable, Intel 80386, statically linked`, **and i386
binaries execute here**. So the real target artifact can be built and measured
locally and in CI without a browser or a VM. `strace` is available and
unrestricted. What is missing is a 32-bit musl libc (`musl-gcc` here is
x86-64 only) and a usable Docker daemon.

The conformance runner is the one that matters for correctness: every corpus
entry is executed by **both** system CPython and lypning-mp, and the stdout, stderr
and exit code must match. A subset runtime that silently disagrees with Python
is worse than no runtime, because the agent will not notice.

## 3. The implementation, and the daemon that was asked for

**Decided (2026-08-13, `docs/RESEARCH.md` §6): lypning-mp is a MicroPython
unix-port variant** — static, musl, i386, with a frozen lypning-mp stdlib and
`sys.path` pinned to `['.frozen']`. A shallow variant, so tracking upstream
stays a rebase rather than a merge.

The two alternatives were considered and rejected on measurement, not taste:

- **A purpose-built C interpreter for the corpus.** The most appealing option on
  paper, and the corpus is narrow enough that it is not absurd. Berry is the
  control experiment: a complete, mature dynamic-language VM with a frozen
  stdlib, written by people optimising for exactly this, lands at 365,660 B —
  only 32% below MicroPython's 541,688 B, which already speaks Python and
  already has `re` and `json`. A couple of hundred KB, against writing *and then
  owning* a tokenizer, parser, bytecode VM, GC, regex engine and JSON parser,
  plus matching CPython closely enough that generated one-liners do not silently
  produce wrong answers. In a VM whose exec floor is 50–85 ms, that size
  difference is worth roughly 150 ms. It does not buy back the risk.
- **Stripping CPython.** Looks like the safe option and is not. 6.6 MB before
  stripping, dynamically linked against 3.1 MB of libraries, and a stdlib that
  lives as files on disk — which is the actual source of the 8573 ms. One cheap
  win is worth stealing regardless: **`-S` cuts CPython's startup file syscalls
  109 → 63**, so while `python3` remains in the image the agent prompt should
  prefer `python3 -S -c`.

### 3a. The daemon does not earn its keep, and the mechanism it needs is broken

The daemon was an explicit requirement — a resident runtime to cut startup — so
it was measured before being dropped rather than argued away. Three findings,
in order of force:

**There is nothing left to save.** A fork server's entire product is
"interpreter initialisation, amortised". Measured, lypning-mp's interpreter init is
**0.96 ms against a 0.92 ms empty-C-program floor**. A zygote would amortise
0.04 ms. Meanwhile every one-liner still arrives as `execInSandbox` →
`/bin/sh -c`, whose **50–85 ms floor a daemon cannot touch** — a socket
round-trip from a thin client sits *inside* that envelope, not instead of it.
The daemon would be optimising roughly 0.05% of the cost.

**The mechanism is not functional in this guest.**
`docs/SANDBOX-PERFORMANCE.md` records a direct probe: `timeout 2 sleep 60`,
`timeout -s KILL 2 sleep 60`, and an explicit `kill -9` on a known PID **all
fail** — "signal delivery and process termination are not functional in the
CheerpX guest". A fork server's core loop is fork, wait, reap, forward signals.
Half of that does not work. It would also die with the VM on every
`resetSandbox`, needing constant re-establishment, and a resident background
process is the exact shape that wedged the single-threaded VM in `chat_logs
#522`.

**It is the right fix for the wrong binary.** A zygote is genuinely the correct
answer *for CPython*: 8573 ms cold against 87 ms warm is precisely the profile
it repairs. But it repairs it by keeping a 9.7 MiB working set resident in a VM
whose memory is the browser's, and it does nothing about the first boot.
Shipping a 541 KB static binary removes the problem instead of amortising it.

**So lypning-mp ships daemonless, and `lypningd` is not built.** The daemon was an
explicit part of the original ask; the measurements above were put to the owner
and the answer was **"fine, no daemon, just a binary" (2026-08-13)**. That
settles it — lypning-mp is one static binary, and nothing in this project should
reintroduce a resident process without new evidence.

The design is still recorded in §4, because a decision made on measurement
should be reversible by reading rather than by rediscovery. Revisit it if — and
only if — a measurement shows interpreter init appearing in a real trace: a
future workload running many one-liners inside one `execInSandbox` batch, or a
lypning-mp whose initialisation grows expensive (a large frozen corpus, a loaded
index). Neither is true today.

## 4. The `lypningd` design, recorded but not built

**Not built, by owner decision (§3a). Nothing below is implemented.** Kept so
that reversing §3a means reading a design rather than rediscovering one. A zygote, the shape Android's zygote and preforked application servers
use: pay initialisation once in a parent, `fork()` per request so children
inherit the initialised heap copy-on-write.

The request frame would carry, and the forked child would have to faithfully
adopt, all of:

| field | why it must be passed |
|---|---|
| `argv` | the program and its arguments, `argv[0]` included |
| `cwd` | one-liners use relative paths constantly |
| `env` | inheriting the daemon's env silently changes `PATH`, `HOME`, locale |
| stdin/stdout/stderr | **passed as file descriptors over `SCM_RIGHTS`**, not proxied |
| umask | file-creating scripts |
| signal handlers and mask | inherited across `fork`, so they must be reset in the child |

Passing the three fds over the socket rather than relaying bytes through the
daemon is the load-bearing choice: it keeps pipes, redirections and
`sys.stdout.isatty()` behaving exactly as they do for a normal process, and it
avoids doubling the ~1.1 MB/s output path. The reply frame carries the wait
status so the thin client can exit with the right code — an agent loop branches
on exit codes, so getting these wrong is a correctness bug.

**Failure would always be soft.** Socket missing, refused, stale, or a protocol
version mismatch ⇒ the client runs the program in-process and says nothing. A
daemon that can break a command is worse than no daemon. Which means the client
must contain a full interpreter anyway — so the daemon could only ever save
initialisation, never size. Given that initialisation measures 0.96 ms, that
observation is most of §3a on its own.

## 5. Delivery path into the sandbox

`scripts/build-sandbox-image.sh` builds the Alpine i386 image and today lists:

```
PKGS_COMMON="bash coreutils grep sed gawk findutils file less python3 jq nodejs git"
```

That recipe is the delivery path: lypning-mp installed as `/usr/local/bin/lypning-mp`,
with a decision about `python3` that turned out to be less balanced than it
looked.

- **Add alongside** — `python3` stays, lypning-mp is opt-in. Costs nothing at rest,
  since a binary nobody runs is never streamed. But the agent keeps typing
  `python3` and keeps paying 8.5 s, so it wins nothing on its own.
- **Alias** — lypning-mp *is* `python3`, and CPython leaves the image. Wins
  immediately and everywhere, and meets the subset boundary head-on.

`docs/RESEARCH.md` §6 item 6 recommends **aliasing**, and the argument
that decides it is not about python at all: dropping CPython removes **27.0 MiB
and 16 shared-library dependencies** from an image whose entire design goal is
to be small and to stream without stalling — and a `command -v python3` that
finds *nothing* is the single most expensive failure mode in this VM, the one
that once consumed the whole 30 s ceiling and destroyed the VM. Aliasing is
therefore safer than removing CPython without a stand-in, and much cheaper than
keeping it.

What aliasing costs, stated plainly: every generated `python3 -c` gets the
subset, so `subprocess` and anything else outside it now fails at exit 90
instead of working. That is only acceptable because of §6 — a greppable,
branchable failure — and it obliges a matching change to `bashAgentPrompt`
stating the subset and naming `subprocess` a non-goal, so the model hoists such
work into its own bash block rather than being silently misled.

The order is still staged, because the acceptance metric has not been measured
live yet: build, gate, conform, **then** measure in a real VM (§2), and only
then edit `PKGS_COMMON`. This image is the tier with no server to fall back on
(Se/cure), so nothing here should land on a projection.

The same binary is relevant to `container/Dockerfile` (the server-side
execution environment) but wins far less there — that image is a normal
container on a normal filesystem, where CPython's cold cost is a page cache
miss rather than a network fetch. **lypning-mp is a sandbox optimisation, not a
platform-wide python replacement.**

## 6. The fallback contract — what happens outside the subset

This is the part that decides whether a subset runtime is usable at all.

When lypning-mp meets syntax or a name it does not implement, it must:

1. write a single greppable line to **stderr** naming the exact missing
   feature — module, attribute or construct — and nothing else;
2. exit with a **distinctive code** reserved for "unsupported", never 1, so a
   caller can tell "your program is wrong" from "lypning-mp is too small";
3. never partially execute a program it cannot finish, where that is
   detectable ahead of time. Unsupported *syntax* is caught at parse time and
   costs nothing. Unsupported *attributes* are only discoverable at runtime, so
   partial execution is sometimes unavoidable — the error line has to make it
   obvious that this is what happened.

That contract is what lets the agent loop, or a shell wrapper, retry with real
`python3` automatically. `docs/SUBSET.md` owns the exact exit code and
message format.

## 7. Growing the corpus

lypning-mp is defined by what it is asked to run, so the corpus is the
specification and it must keep growing on its own.

`lypning capture/` installs a `python`/`python3` shim early on `$PATH`
that appends every invocation — argv, the `-c` program text, cwd, exit code —
to a log, plus a `PreToolUse` hook that records python-bearing shell commands.
`harvest.mjs` merges those, and any Claude Code transcript it can find, into
`tests/corpus/corpus.jsonl`, deduplicated by normalised program text.

Two rules keep this honest:

- **Every entry is tagged with provenance** — observed from a real invocation,
  or written from expectation. They are not the same evidence and the frequency
  table must not blend them.
- **The corpus is committed, so harvest redacts.** Captured programs can embed
  repo content and secrets; harvest runs the `scripts/scan-secrets` patterns
  over every entry before it lands.

The corpus drives the build order: implement in descending order of how many
entries a feature unblocks, and re-run the conformance diff against CPython on
every change.

### 7a. The feedback loop that made the first harvest mostly fiction

Capture worked from the day it shipped. Growth did not, and the frequency table
was measuring the wrong thing. Both were found on 2026-08-14, by reading the
committed corpus rather than the code.

**The corpus fed itself.** The conformance runner executes every corpus entry
under a reference interpreter, and it resolved that interpreter by NAME — so it
found the capture shim sitting first on `$PATH`, which logged each run and then
exec'd the real CPython. The comparison still passed, so nothing looked wrong.
The next harvest merged those runs back in as observed evidence. The result:
**138 of the first harvest's 197 "observed" programs were byte-identical to
hand-written seed programs**, and the counts were not a distribution at all —

```
1x:1   2x:10  3x:7  4x:1  5x:1  8x:139  9x:25  10x:11  12x:1  45x:1
```

139 entries seen *exactly* eight times is eight conformance runs, not eight
decisions to write a program. Real usage is Zipfian; this is a test loop. Since
`--plan` ranks the build order by those counts, the loop did not merely add
noise — it ranked the programs someone **guessed at** above the ones an agent
actually typed. Stripping seed programs, lypning-mp's own build chatter and trivial
probes left **35 unique programs**, of which about four were genuinely
task-driven.

**Nothing persisted.** The log lives at `$HOME/.lypning/invocations.jsonl` —
outside the repo, in an ephemeral container — and nothing ever ran the harvest.
`corpus.jsonl` was committed exactly once (`b07654c1`); every entry's
`first_seen` falls inside a single 36-minute window, and every session since
captured into a file that died with its container.

Three fixes, in the order they matter:

1. **The runner no longer feeds the log.** `referencePython()` resolves to a
   real CPython ELF via `findRealCPython` (the same helper the gate uses, for
   the same reason — measuring the shim as a baseline once projected a 330-second
   cold cost), and `runOne` spawns with `LYPNING_CAPTURE=0`. Either alone closes
   the loop; both are kept because they fail differently. Pinned by
   `tests/corpus/conformance.test.mjs`, whose loop test asserts an empty log
   after a full run.
2. **Harvest enforces the separation it documents.** A sighting whose program
   is byte-identical to a seed program is dropped and counted as
   `seedCollision`, reported on every run. Keeping the two *files* apart never
   achieved separation on its own, because the pipeline kept copying one into
   the other. Existing contaminated records are **not** deleted — this guards
   against new contamination rather than rewriting committed evidence — but
   their counts can no longer grow.
3. **A `Stop` hook harvests before teardown** (`.claude/hooks/lypning-harvest.sh`).
   It does not commit: it leaves `corpus.jsonl` dirty in the working tree, which
   is visible in `git status` and is the session's call to make.

**The committed counts remain inflated.** Fixes 1 and 2 stop them growing; they
do not retroactively correct the 138 laundered records, and no honest way to do
so exists from the data — the log that would say which sightings were real is
gone. Treat the current `count` column as an upper bound, and treat any build
order derived from it as provisional until the corpus has grown from sessions
running the fixed pipeline.

**Two traps, both paid for here.** The first version of the loop test passed
with every defence removed: its stand-in shim honoured only `$LYPNING_LOG`, and
`runOne` deliberately strips that variable while keeping `HOME` — so the
stand-in wrote nowhere and a green test proved nothing. A stand-in must
reproduce the real shim's fallback path. And mutation-testing the two defences
separately is what showed the test was hollow; running it against the fixed code
alone would not have.

### 7b. The corpus was the wrong unit to collect into (2026-08-14)

Fix 3 above — the `Stop` hook — closed the gap between the log and the working
tree and left the rest to the session. That was not enough, and the shape of the
failure is worth recording because it is not a bug in any component.

Every session harvested into **one shared file**, `tests/corpus/corpus.jsonl`.
Two branches touching it conflict by construction, and resolving that conflict
was never worth it to a session whose PR was about something else. Counted over
the 19 branches cut since the corpus first landed:

| | |
| --- | --- |
| branches carrying any harvested growth | **2** |
| of those, merged into `main` | **0** |
| commits to `corpus.jsonl` on `main`, ever | **1** |
| sessions whose python was captured, harvested, and lost | **17** |

So capture worked, harvest worked, the hook worked, and the collection rate was
still zero. **A shared mutable file is not a collection point when the writers
are ephemeral containers on independent branches.**

The durable unit is now one file per session,
`tests/corpus/sightings/<session>.jsonl`, written by the same `Stop` hook
(`--export`) and staged by `.githooks/pre-commit` — narrowly, that one directory
only, and before the secret scan so what it stages is scanned. One writer per
path means no branch can conflict with another, and an unrelated PR carries an
**added** file rather than a rewritten one. `corpus.jsonl` becomes a DERIVED
artifact, regenerated by `npm run lypning:harvest` from however many sightings
have accumulated; no individual session has to produce it.

Two details this forced, both load-bearing:

- **A sighting key can no longer be a bare log line number.** Line 1 means a
  different invocation in every container, so keys are namespaced by session
  (`shim:<session>#12`). That is also what makes the fold safe: a harvest reads
  a session's live log *and* its own published file, and the identical key is
  what stops one invocation being counted twice.
- **Publication is filtered, not just scanned.** The redaction and the
  seed-collision guard run before a sightings file is written, because that file
  is committed. The pre-commit secret scan is the backstop.

The same pass recovered the 38 programs stranded on the two unmerged branches
(37 from `claude/deep-science-feedback-epa7i6`, 1 from
`claude/video-capture-rerun-agent-link-r3r1q0`), taking the corpus from 197 to
235. The seed guard was deliberately **not** applied to those records: all three
inputs were already-committed corpora, and §7a's rule is that the guard stops
new contamination rather than rewriting committed evidence. (Applying it anyway
deletes 138 of `main`'s own records, which is how that rule got tested.)

## 8. Work breakdown

| # | piece | artifact | state |
|---|---|---|---|
| 1 | implementation survey, ranked on cold cost | `docs/RESEARCH.md` | **done** — MicroPython variant, musl, i386 |
| 2 | subset spec + seed corpus | `docs/SUBSET.md`, `tests/corpus/seed-corpus.jsonl` | **done** — 161 entries, tiered |
| 3 | conformance runner + build gate | `lypning conformance`, `lypning gate` | **done** — both proven against stubs |
| 4 | charter | this file | **done** |
| 5 | capture harness + corpus growth | `lypning capture/` | in progress |
| 6 | the variant + build | `micropython/`, `scripts/build-micropython.sh` | in progress |
| 7 | the frozen shim stdlib | `micropython/lib/` | in progress |
| 8 | live cold/warm measurement in the real VM | `scripts/vm-measure.mjs` | **done** — §1a, measured 2026-08-14 |
| 9 | image delivery, staged | `scripts/build-sandbox-image.sh` | lypning-mp installs; the alias-vs-alongside call is now an owner decision with evidence (§1a) |
| — | `lypningd` | — | **not being built** — §3a |

## 8a. The optimisation pass (2026-08-14)

The first working binary was 390,456 B. A pass over the build flags and the
variant config took it to **304,440 B — 22.0% off — with conformance unchanged
at 0 MISMATCH**, and made a real workload 17% faster on the way.

Every line below was measured on this container by rebuilding and re-running
both gates. Nothing here was accepted on reasoning alone.

| change | file size | what moved |
|---|---|---|
| baseline | 390,456 B | — |
| drop DWARF unwind tables | −49,152 B | `.eh_frame` 51,644 → 388 B |
| drop `framebuf` + `uctypes` | −12,288 B | 6,606 B + 3,183 B of module code |
| link-time optimisation | −16,384 B | `.text` 230,855 → 213,253 B |
| drop `micropython` module, 1-byte qstr hash | −4,096 B | 2,100 B of sections, crossing a page |
| `-z noseparate-code` | −4,096 B | inter-segment padding, no section changed |
| **total** | **304,440 B** | **−86,016 B, −22.0%** |

Projected cold cost falls 504 ms → 393 ms on the gate's own model. File opens
were 0 before and after: that metric was already at its floor.

**The largest single win was dead weight nothing could read.** `.eh_frame` held
51,644 B of DWARF unwind tables, 13% of the stripped binary. MicroPython raises
through setjmp/longjmp — the NLR machinery — never through a DWARF unwinder,
and `nm` finds zero `_Unwind`, `__cxa` or `backtrace` symbols in the artifact.
`--gc-sections` never removed it because it does not collect `.eh_frame`, so
the section survived every earlier size pass while looking like it belonged.

**LTO is the only change that improved both terms.** It removed 16,384 B *and*
took a loop/regex/json workload from 518 ms to 433 ms, because it inlines and
drops code across translation units where `--gc-sections` only works at section
granularity. It is also the one change with a real correctness hazard: LTO
across `setjmp`/`longjmp` is the classic miscompile, and that is precisely how
MicroPython raises. Six `nlr-*` entries in `tests/corpus/seed-corpus.jsonl` pin
it — deep recursive unwinding, `finally` ordering, locals live across a raise,
generator close, 2000 sequential raises reusing the NLR buffer, and the
recursion limit. All six match CPython exactly.

### Rejected, with the measurement that rejected them

Recorded so the same ideas are not re-tried by whoever reads the config next.

- **`MICROPY_OPT_COMPUTED_GOTO = 0`** saves 4,096 B and is still wrong. It costs
  0.14 ms per program — the real corpus of 340 programs went 630 ms → 678 ms —
  against a one-time 5 ms cold saving, so it only pays back after ~37 programs
  in a session. That is 1.3% of size for 7.6% slower execution.
- **`MICROPY_PY_BUILTINS_COMPLEX = 0`** saves 3,248 B of `.text`, and
  `complex(1,2)` correctly exits 90 because `complex` is already in the
  CPython-builtins table of `lypning_unsupported.h`. But the **`1j` literal** is
  caught by the lexer instead and exits 1 with a `SyntaxError`, so a caller
  cannot tell "lypning-mp is too small" from "your program is wrong" (§6). Closing
  that needs a new hunk in the port patch, which is upstream-tracking surface
  the variant deliberately does not spend — for 1% of the binary and the loss of
  a genuine CPython builtin.
- **`ld.lld`** cannot link this at all: GCC's LTO plugin emits GIMPLE and lld
  wants LLVM bitcode, so `main` comes out undefined from `Scrt1.o`.
- **`ld.gold --icf=safe`** links and folds 14 bytes. MicroPython has almost no
  byte-identical functions. Gold also rejects `-z noseparate-code`, so it lands
  156 B larger than the default linker while adding a toolchain dependency.

### Two measurement traps this pass paid for

- **The stripped file size is quantised to the 4,096 B page.** Three different
  changes each reported exactly −4,096 B, and one of them had moved only 2,100 B
  of actual content. `size -A` is the fine-grained truth; the file size is what
  the gate measures because it is what the sandbox streams.
- **A synthetic workload overstates VM speed by about tenfold.** The heavy
  benchmark (20,000 iterations plus regex plus json) said computed-goto was
  worth 48 ms. Timing the 340 real corpus programs said 0.14 ms each. The corpus
  is the honest instrument, because it is the distribution lypning-mp was built for.

### What is now at its floor

A six-module import — `re, json, os, base64, collections, datetime` — costs
**13 syscalls in total and zero file syscalls beyond `execve`**. The `sys.path`
probe pathology that `docs/RESEARCH.md` §2.5 measured at 56 syscalls is
gone, not merely reduced. Since cold cost tracks bytes and opens, and opens are
0, **bytes are the only lever left** — which is why this pass is entirely a
size pass.

## 8b. The second optimisation pass (2026-08-14), and the bug it uncovered

A six-lane survey of what was left took the binary from 304,440 B to
**269,124 B — a further 11.6%, and 33.7% below the original 390,456 B**. Adding
corpus entries for two suspected silent divergences then exposed a defect in the
fallback contract that had been present since the frozen shims were written.

### The size work

| change | file size | mechanism |
|---|---|---|
| after §8a | 304,440 B | — |
| `-fno-pie` / `-no-pie` | −12,288 B | `.data.rel.ro` 20,992 → 4 B |
| i386 codegen pack | −8,192 B | stack boundary, frame pointer, alignment padding |
| `-Wl,-z,norelro` | −1,440 B | inert segment; **0** `mprotect` calls at runtime |
| `MPY_CROSS_FLAGS += -O3` | −2,592 B | frozen bytecode line-number tables |
| `main.c` `fprintf` → `mp_printf` | −11,604 B | musl's whole `vfprintf` engine |
| contract fix + 4 corpus entries | +800 B | see below — bought deliberately |
| **total** | **269,124 B** | **−35,316 B** |

**gcc had been compiling every translation unit position-independent.** Ubuntu's
gcc 13.3 defaults to `-fPIE` and the musl-gcc wrapper does not turn it off, so
the build paid for position independence it never used — the link has always
produced a non-PIE `EXEC` at a fixed `0x08048000`. On i386 that is charged twice:
a GOT base pointer consumes a register on the most register-starved ISA in common
use, and every const aggregate holding a pointer is demoted out of `.rodata`.

**Two lanes each found a ~29 KB stack and neither knew about the other's headline
flag.** `-fno-pie` and the codegen pack turned out to compose at 96.5% — they
attack different registers (`%ebx`/GOT versus `%ebp`/stack frame) — so the
combination is worth more than either lane claimed. Combinations get measured
here, never summed.

**Two lines of `fprintf` were holding ~9.5 KB of musl.** `main.c` was the only
remaining caller of libc's `fprintf`, which drags in `vfprintf`, `printf_core`,
`fmt_fp` and the long-double helpers — in a binary that already links
`py/formatfloat.c` for its own float formatting. `MICROPY_USE_INTERNAL_PRINTF`
supplies `printf` but not `fprintf`, which is how they survived.

### The bug: the fallback contract only ever worked from C

Adding corpus entries for `re.VERBOSE` and `csv.writer(quoting=…)` — both of
which the shims declared as constants and then silently ignored, returning `[]`
where CPython returns `['1','22']`, at **exit 0** — showed something worse than
the two divergences themselves.

`lypning_exit_not_implemented()` is reached from `py/runtime.c`'s
`mp_raise_NotImplementedError()`, **a C function**. The frozen shims raise
`NotImplementedError` from *Python* (`re._unsupported()`), which never passes
through it. So every shim-level gap — regex backreferences, named-group
references, `{n,m}` on a group — exited **1 with a multi-line traceback** instead
of **90 with one greppable line**. The message was right and everything around it
was wrong, so a caller branching on 90 to retry with real `python3` had never
fired for any of them.

The fix routes a `NotImplementedError` carrying the `lypning: unsupported: `
marker into the same exit-90 path. Two things about it are worth keeping:

- **It needs two call sites, and that was found by deleting one.** `-c` and a
  script file surface their exception through `shared/runtime/pyexec.c`; a
  program piped on **stdin** comes out through `main.c`'s
  `handle_uncaught_exception()`. Removing either silently returns that path to
  exit 1. All three are now pinned in the build's smoke checks.
- **A program's own `NotImplementedError` must not be hijacked.** Only the marker
  prefix triggers the 90; anything else keeps its traceback and exit 1. That is
  pinned too, because getting it wrong would be a worse bug than the one fixed.

The shims now refuse the whole class rather than the two instances: `re` rejects
any flag it does not implement by name, and `csv` rejects any keyword whose value
differs from the behaviour it actually has. Conformance went 195/13/0 →
**195 MATCH / 17 UNSUPPORTED / 0 MISMATCH** — the four new entries are honest
coverage gaps instead of silent wrong answers.

### Rejected, with the measurement

- **UPX** (−123,388 B at `-9`, −142,936 B with LZMA) is the largest number in the
  survey and still wrong. The stub opens `/proc/self/exe`, so **file opens go
  0 → 1** and the project's central metric dies; it needs a third-party binary in
  CI that rewrites the shipped ELF; and it pays decompression on every run
  against a 50–85 ms exec floor.
- **`-mregparm=3`** (−4,944 B of `.text`) segfaults on every program. musl's i386
  `memcpy` is hand-written assembly reading `0xc(%esp)`, and rebuilding musl with
  the flag cannot fix it — 69 `.s` files are cdecl by construction. Fixing it
  means forking musl and losing the tarball-by-SHA-256 provenance.
- **Stripping docstrings from `micropython/lib`** saves **0 B**, not "a little":
  `mpy-cross` hard-sets `MICROPY_ENABLE_DOC_STRING=0`, so the parser discards
  them already. There are only 268 docstring bytes in the whole shim stdlib.
- **Dropping the six least-used frozen modules** saves 4,096 B for MATCH 195 →
  188. Every one is a real CPython module, so it is coverage sold for one page.

### What this pass does not change

lypning-mp still is not deployed — the sandbox image is not built, so `/api/sandbox-image`
serves the third-party WebVM disk and nothing here reaches a user yet. Both
numbers below are projections from the gate's model, not measurements:
504 ms → 347 ms. Against a **24.4 s cold VM boot**, this whole pass is worth
about 46 ms of a sandbox turn. Two pending decisions would change its value more
than any flag: transport compression on the image would halve every byte saved
(the 35,316 B here is 17,329 B under `gzip -9`), and implementing the recorded
`sandbox.prefetch` directive would remove per-file cold cost altogether.

## 8c. The third pass (2026-08-15): compiler techniques, and where the time actually is

The first two passes were size passes, because opens were already zero and bytes
were the only lever anyone could name. This one asked the other question — what
do modern compiler optimisation techniques have left to give — and answered it
with builds rather than reasoning. **The answer is nothing, and the reason is
worth more than the flags.**

### The measurements that closed the compiler question

Every row was built and timed on one machine, against the same corpus.

| technique | size | real corpus (351 programs) | heavy workloads | verdict |
|---|---|---|---|---|
| `-O2` everywhere (from `-Os`) | **+66,624 B (+24.7%)** | 0.994x | 0.95–0.99x | rejected |
| PGO at `-Os` (`-fprofile-use`) | +2,240 B | 0.996x | **1.07–1.11x — slower** | rejected |
| geometric map growth | +32 B | 1.000x | 0.96x on 20k-key insert | **taken** |
| the `re` shim rewrite (below) | +192 B | 0.998x | **0.087x on re.sub** | **taken** |

**The corpus column is why every compiler technique loses.** A corpus program
costs about 1.7 ms, and `docs/RESEARCH.md` §2.7 already measured where
that goes: interpreter init is **0.96 ms against a 0.92 ms empty-C-program
floor**. Ninety-six per cent of a lypning-mp invocation is the operating system
spawning a process, which no compiler flag touches. What is left for codegen to
improve is a few hundred microseconds of parse, compile and execute — so a
technique that makes generated code 10% faster moves the thing anyone waits for
by well under one per cent, and the timings above say exactly that.

**PGO is worse than inert, and the shape of its failure is the lesson.** Trained
on all 351 corpus programs and then measured on that same corpus — training on
the test set, so an upper bound — it was worth 0.4%, which is noise. Measured on
the held-out dict workloads it was **7 to 11% slower than plain `-Os`**: the
profile said `mp_map_lookup`'s scan was lukewarm, so GCC optimised it for size.
That is the classic profile-representativeness failure, and it is unavoidable
here rather than fixable, because lypning-mp's two distributions genuinely differ —
one-liners that start and exit, and the occasional program that loops over a
large input. A profile can serve one or the other.

Two mechanical findings ride along, both of which cost a build:

- **GCC's `libgcov.a` cannot be statically linked against musl.** Ubuntu's
  prebuilt copy is a glibc/`_FORTIFY_SOURCE` artifact and pulls in `mmap64`,
  `open64`, `fopen64`, `fcntl64`, `__memcpy_chk`, `__isoc23_strtol` and three
  `*printf_chk` variants, none of which musl has. Aliasing those nine to their
  unsuffixed musl equivalents in one small translation unit closes it completely,
  and the instrumented binary is a throwaway, so nothing shipped would ever link
  it — but it is a real cost to put in CI for a technique measured at zero.
- **At `-Os`, `-fprofile-use` cannot make code faster at all.** GCC's
  `optimize_size` gates every speed-for-size transform, so a profile buys block
  layout and nothing else — measured, a hot function was byte-identical with and
  without a real profile. Anyone hoping for PGO's speed while keeping `-Os` is
  chasing something that does not exist.

### The cost model gains a constant: 131,072 B

CheerpX streams the disk image in **128 KiB device blocks** (the constant is in
`cx_esm.js`; `262144` and `524288` appear nowhere). Cold cost is therefore not
linear in bytes, it is a **step function**: the 269,316 B binary occupies three
blocks, and any saving that does not cross a 131,072 B boundary streams exactly
the same number of blocks as before. This retires a great deal of speculative
work — code layout, function ordering, hot/cold partitioning and `llvm-bolt` all
optimise placement inside a file that is fetched whole — and it reprices the
size passes: the next 6,980 B would drop the binary under 262,144 B and remove a
whole fetch, while the 4,096 B cuts the earlier passes declined were worth
nothing on their own and are worth a block as part of that total.

`llvm-bolt` is independently ruled out: it has no i386/x86-32 target.

### What was actually worth doing

All of it was algorithmic, and all of it was above the compiler.

- **The `re` shim stopped re-slicing the subject.** Every scanning operation ran
  one loop that advanced by `search(seg[pos:])`, copying the remainder of the
  string per match. The C engine has taken a start position all along. Scanning
  is now a cursor on an ASCII subject — the engine's `pos` is a byte offset while
  `span()` answers in characters, so the two units have to coincide — and
  `re.sub` delegates to the engine's own substitution loop whenever its four
  divergences cannot bite. `re.sub` went **10.36x -> 0.87x** against stock, which
  is 16x faster in absolute terms and faster than CPython on the same case.
- **A silent divergence fell out of writing the corpus entries for that gate.**
  `_subn` carried CPython's pre-3.7 rule and skipped an empty match abutting the
  previous one, so `re.sub(r"b*", "-", "abc")` answered `-a-c-` instead of
  `-a--c-` — at exit 0, for every quantifier that can match empty. This is the
  failure mode §9 names as the one that would make lypning-mp a liability, and it
  was found by adding coverage for a performance change.
- **The ordered map grows geometrically.** Upstream's `alloc += 4` reallocated
  the table once per four insertions; lypning-mp points the Python-level dict at that
  map, so a 20,000-key dict reallocated 5,000 times. Worth 0.96x, and worth more
  as information: the allocator was never the problem.

### The dict, priced and left standing

`insert 10,000 then look up all 10,000` is **8.49x** stock and a 20,000-key
insert takes 1.15 s on this host — which the ledger's own note says is optimistic
against the emulated guest, and the 30 s exec ceiling destroys the VM. Now that
the allocator is measured at 4% of it, the remaining 96% is the **linear scan**
in `mp_map_lookup`: MicroPython's ordered map is an array, and insertion-ordered
dicts are what keeps `print(d)`, `d.items()` and `json.dumps(d)` agreeing with
CPython (`json.dumps` is 4.30x from the same cause).

The fix is CPython's own compact-dict shape, and the containment matters more
than the idea: `mp_map_t`'s bitfield has spare bits, so a `has_index` flag costs
**zero bytes**, and a hash index of positions can live in the same allocation as
the table — past `&table[alloc]` — so `mp_map_t` does not grow and every ROM
module table stays the size it is. Above a threshold an ordered map probes the
index instead of scanning; below it the array is genuinely faster. Deletion
(`OrderedDict` only) shifts positions, so it rebuilds the index.

It is not in this pass because it is a hundred lines inside `py/map.c`, which
would be by far the largest hunk in a port patch that the project keeps small so
that tracking upstream stays a rebase (§6). It wants its own change, its own
review and its own conformance run — not the tail of a pass about compiler flags.

### The instrument this pass needed and did not have

`lypning corpus-time` times every corpus program on one binary or
two, interleaved, min-of-repeats, each in its own temp directory with
`LYPNING_CAPTURE=0`. (The measurements above were taken over 351 of them; the
capture harness has since carried that past 460, which is why the tool prints
the count it loaded rather than anyone quoting one.) Everything above depends on it. The benchmark answers "what
does our variant cost against stock" per subsystem; only the corpus answers "did
this change make the programs lypning-mp is actually asked to run faster", and for
all four techniques in the table the two instruments disagreed — the benchmark
said 16x and the corpus said nothing, or the benchmark said nothing and the
held-out workload said 11% slower. §8a already recorded a synthetic workload
overstating a result tenfold. This is the general form of it.

## 9. What would make this project wrong

Recorded up front so it can be checked rather than argued:

- **If the 8573 ms is not mostly file-touching.** The whole premise is that
  cold cost tracks bytes and opens. If a static lypning-mp still takes seconds cold,
  the cost was somewhere else and the design was aimed at the wrong thing.
- **If the corpus is much wider than expected.** "Just enough Python" only works
  if the distribution has a short head. If the observed one-liners keep reaching
  for new modules, the subset grows until it is CPython with bugs, and the right
  answer becomes a smaller *CPython*, not a new interpreter.
- **If disagreement with CPython shows up in the conformance diff and cannot be
  fixed cheaply.** Silent semantic divergence is the failure mode that would
  make lypning-mp a liability rather than an optimisation.

Each of these is measurable, and each has a gate above that would catch it.

### What lypning-mp does not fix, stated plainly

Cold **VM boot** still dominates a sandbox turn. The agent trace in
`docs/SANDBOX-PERFORMANCE.md` shows 24.4 s of boot against 290 ms of commands,
so lypning-mp improves a real but **secondary** term. It is worth doing because it
is cheap, because 8.5 s is a large secondary term that can cross the 30 s
ceiling and destroy the VM, and because removing CPython takes 27.0 MiB and 16
shared-library dependencies out of an image whose entire design goal is to be
small and to stream without stalling — which helps the boot too. It is not
worth doing on a claim that it makes the sandbox fast, and no user-facing copy
should say that.

The other honest gap is `subprocess`. It appears nowhere in this repository, so
excluding it is evidence-backed (`docs/SUBSET.md` §5) — but a subset
runtime aliased as `python3` will meet it eventually, and the answer is exit 90
plus a stated non-goal in `bashAgentPrompt`, so the agent hoists the command
into its own bash block rather than being silently misled.
