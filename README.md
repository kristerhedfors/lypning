# lypning — the Coding Harness Interpreter Optimizer

<img src="docs/logo.svg" alt="" width="72" height="72">

`lypning` optimizes the interpreter layer underneath a coding harness: it runs
a Python program on the cheapest of three interpreters that can actually run
it. The architecture is a *mixture of Pythons* — a from-scratch Python subset
written in Rust, a MicroPython variant with a frozen shim stdlib, and the real
CPython for everything the first two refuse — with a classifier that decides
which one, per program, by asking the Rust core's own parser rather than
guessing at the text. The subset is not sized to Python — it is sized to the
one-liners a coding agent actually types, which is a much narrower target and
is the only reason any of this is affordable.

**And "the one-liners a coding agent actually types" is a moving target, which
is the point of the name.** The corpus is captured from live sessions, the
loop re-derives the tables from it, and the whole thing is built to be forked
and re-tuned to *your* harness, *your* agent, *your* programs — models drift,
and this optimizer drifts with them. [`docs/FORKING.md`](docs/FORKING.md) is
the complete path.

Every tier refuses the same way: exit `90`, one line on stderr, nothing on
stdout. That is what makes the three interchangeable, and what makes a wrong
route cost one wasted process spawn instead of a wrong answer.

---


> **The write-up:** `docs/PAPER.md` profiles what coding agents actually
> execute and benchmarks CPython, PyPy, MicroPython, Monty and lypning on
> that corpus with one instrument — including the baselines that beat us.

## Measured performance

`lypning bench --startup-repeat 15 --repeat 3`, run on **2026-08-25** on this
container — 4 CPUs, Linux 6.18.44-fc-v21, all three engines built — against a
corpus capture that had grown to **1551 programs, 1305 of them measurable**:

| | `cpython` | `lypning` | `lypning-mp` | **mixture** |
|---|---:|---:|---:|---:|
| startup, `-c 'pass'`, min of 15 | 11.57 ms | 0.66 ms | 0.61 ms | **0.60 ms** |
| the 904 programs every arm ran | 13093.8 ms | 1164.3 ms | 1336.8 ms | **1718.1 ms** |
| …as a ratio | 1.000x | 0.089x | 0.102x | **0.131x** |
| all 1305, refusals included | 23865.0 ms | 1638.0 ms | 2335.8 ms | **7206.6 ms** |
| …of which it answered | 1305 | 906 | 1236 | **1305** |
| binary | 6,639,992 B | 987,336 B | 296,100 B | — |

**The mixture answers all 1305 programs for 0.302x of CPython's cost** — 16.7
seconds saved across one session's worth of one-liners, with nothing left
unanswered (that run: 2026-08-25, 1305-program corpus; the corpus has since
grown, and the 2026-08-31 sweeps in `docs/PAPER.md` put the chain at 1.50–1.77×
distinct-weighted and 2.35× invocation-weighted over a 2,906-entry corpus). The
two subset arms are cheaper than the mixture only because they refuse work, and
a refusal still costs its spawn. The paper also reports the baseline that beats
us — a pre-warmed CPython fork pool at 2.04×, correct by construction — and what
that pool costs; read its §5.4 before quoting any single ratio from this table.

Correctness on the same tree, from `lypning conformance`: `lypning` 906 MATCH ·
399 UNSUPPORTED · **0 MISMATCH**; `lypning-mp` 1229 · 65 · **11**; the mixture
**1305 / 1305** with **1**. Every one of those twelve is lypning-mp's, four are
the known contract defect, and six arrived when the corpus grew probes that look
for exactly this class — tracked rather than waived, §5 and `docs/LYPNING.md`
§2.

Numbers from one run on one machine. The reason every tool prints the corpus
size it loaded is that yours will differ, so re-run rather than cite: §1 is this
table in full, with its caveats and with the upstream result this tree did *not*
reproduce.

## How a program reaches an interpreter

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
      │ (a kind in ONLY_CPYTHON_KINDS   │              │
      │  skips 2 and goes straight to 3)│              │
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

The classifier is a static analysis over lypning's own front end, not a
heuristic over the program text — so "can tier 1 run this" is an *exact*
answer, costing one parse and no spawn. The tiers below it cannot be asked that
way, because they are separate binaries, so those are capability tables; and
the same `lypning conformance` run that grades answers also grades routes:
**91.0% IDEAL, 97.5% right on the first try** over the 1305 programs above.

Three properties make the fall-through affordable, and each is load-bearing:

- **The winning case costs nothing.** A program routed to tier 1 runs in *this*
  process — no second spawn, no pipe. About 96% of a one-liner's cost is the OS
  spawning a process, so a dispatcher that forked even for the case it got
  right would hand back most of what the fast engine won.
- **A refusal is a non-event.** Exit `90`, one `<engine>: unsupported: <kind>:
  <detail>` line on stderr, nothing on stdout — lypning stages its output and
  discards it, so a refused run is observably a no-op. That commit barrier is
  what makes falling onward *safe* rather than merely possible
  (`docs/LYPNING.md` §6).
- **Only the middle tier is forked.** It has to be, because its own refusal has
  to be catchable: the capability table knows lypning-mp *has* `re`, not that
  this build lacks `re.VERBOSE`. The terminal tier is `exec`'d — no fork, no
  way back, and none needed.

So a wrong route costs one process spawn. It never costs a wrong answer, and
that is the only reason a mixture is allowed to guess at all.

---

## 1. Measurement

Everything else is downstream of one table, and the table is re-measured rather
than remembered. This is the run quoted above, in full — `lypning bench
--startup-repeat 15 --repeat 3` on **2026-08-25**, 4 CPUs, Linux 6.18.44-fc-v21,
**1551 programs loaded, 1305 measured**, 246 skipped for naming an absolute path
the per-entry temp cwd does not contain:

```
startup — `-c 'pass'`, min of 15, arms interleaved

arm             min ms   vs cpython
cpython          11.57   1.000x
lypning           0.66   0.057x
lypning-mp        0.61   0.053x
mixture           0.60   0.052x

shared subset — the 904 programs every arm executed, min of 3

arm          ran  refused   shared total    median   vs cpython
cpython     1305        0      13093.8 ms    12.83    1.000x
lypning      906      399       1164.3 ms     0.91    0.089x
lypning-mp  1236       69       1336.8 ms     0.90    0.102x
mixture     1305        0       1718.1 ms     0.92    0.131x

whole corpus — what a session of 1305 one-liners costs

cpython     23865.0 ms   1.000x
lypning      1638.0 ms   0.069x   (399 unanswered)
lypning-mp   2335.8 ms   0.098x   (69 unanswered)
mixture      7206.6 ms   0.302x   (0 unanswered — saves 16658.4 ms, 69.8%)
```

Read it in this order:

- **The mixture answers everything CPython answers** — 1305 of 1305 — for
  0.302x of CPython's cost. That is the claim the project exists to make, and it
  is the one that has held on every machine it has been run on. Its own arm now
  carries one mismatch, and that is lypning-mp leaking through it rather than
  the dispatcher: §5.
- **The other two arms are cheap because they refuse**, not because they are
  faster: 399 and 69 programs unanswered. `bench` annotates their whole-corpus
  totals with exactly that sentence, because the number is otherwise a trap.
- **Startup is a floor, not a ranking.** All three engines arrive within a
  twentieth of a millisecond of each other, 17–19x under CPython; they are static
  musl binaries that open no files at startup, and past that the differences
  are the machine.

There are two totals in that output and they answer different questions. The
*shared subset* is the only apples-to-apples comparison — a total over
different program sets is not a comparison at all — and the *whole corpus* is
what the session actually costs. Both are printed and both are labelled, for
the same reason.

### Upstream measurements and their reproduction

The table this project was written up with is not the table above. Upstream, on
**2026-08-16**, over the 472 programs the corpus then held (min of 5, arms
interleaved):

```
corpus — 472 programs

arm          ran  refused   shared total (323)   vs cpython
cpython      472        0          4314.4 ms      1.000x
lypning-mp   444       28           616.5 ms      0.143x
lypning      324      148           440.3 ms      0.102x
mixture      472        0           547.0 ms      0.127x
```

That run had **lypning ahead of lypning-mp on the shared subset** — 0.102x
against 0.143x — and it was written up as the thesis: a runtime built for
two-thirds of the distribution beats a general one on that two-thirds.

**The ordering reversed here, and has stayed reversed.** Both re-runs in this
tree — 2026-08-20 and the 2026-08-21 run above — put lypning-mp ahead
(0.061x against 0.073x, and 0.61 ms against 0.70 ms at startup). Successive
runs on this box agree on the ordering and on the ratios to within about a
point, while the absolute milliseconds move by tens of percent with the
machine's load, which is why the ratios are what get quoted and why `bench` is
not a CI gate.

Read honestly, that thesis was **upstream's result, not a property of the
design**. The shared subset is by construction the programs lypning accepted —
the simplest in the corpus — where both engines sit near their startup floor,
and lypning-mp's floor is lower: 296,100 B against lypning's 987,336 B (both
printed by `lypning status`, and both move whenever an engine is rebuilt). On
2026-08-25 the ordering flipped back — lypning 0.089x against lypning-mp's
0.102x — which is the third time it has moved and is not evidence that it has
settled.

What survives re-measurement is the part the mixture is actually for: answering
everything CPython answers, for about a third of the cost. Both tools print the
corpus size they loaded on every run for exactly this reason — **never quote a
remembered corpus size**, and do not carry a remembered ordering either.

`docs/LYPNING.md` §1 is the design's own version of this table,
`docs/BENCH-LEDGER.md` is the append-only history including the runs where the
subset lost.

---

> **Docs site.** The reference documentation is published from this repository's
> own markdown to
> [kristerhedfors.github.io/lypning](https://kristerhedfors.github.io/lypning/).
> It requires Pages to be enabled once, under **Settings → Pages → Source:
> GitHub Actions** — a workflow token can deploy to a Pages site but cannot
> create one, so the `pages` workflow fails until that is set rather than
> pretending to have published.

## 2. Installation

```bash
pip install lypning     # pure Python, zero runtime dependencies
lypning build           # compile the engines into ~/.lypning/bin
```

> Not on PyPI yet — that name does not resolve today. Until it does, install
> from a checkout: `pip install .` (or `pip install -e .`), which is the same
> wheel and the same console script.

`pip install` gives you the CLI, the corpus and the engine *sources*. It does
not give you an engine — nothing is compiled at install time, because a wheel
that shelled out to `cargo` during `pip install` would fail in every
environment that does not have one. Until `lypning build` runs, `lypning
status` says `not built` and every program routes straight to CPython.

**The Rust core needs `cargo`** and nothing else — the crate has zero
dependencies, so nothing is fetched from crates.io. Clean release builds on this
container (4 CPUs, 2026-08-20) took **13.3 s and 17.9 s** — the spread is the
box's load, not the crate — so call it well under a minute and budget more on a
slower machine. `lypning build --rust` prints the seconds it actually took.

**The MicroPython tier is optional and usually absent.** It needs a 32-bit C
toolchain (`gcc-multilib`) and network access — the build fetches musl and a
pinned MicroPython — and it takes minutes rather than seconds. `lypning build`
with no flags attempts both and exits non-zero if either fails, so ask for the
one you want:

```bash
lypning build --rust           # the core, seconds, cargo only
lypning build --micropython    # the second tier, needs a toolchain + network
lypning build --dry-run        # print the commands, build nothing
```

A missing tier is a status line, never an error. Routing skips it, `bench`
leaves a hole in the table rather than a zero, `conformance` says
`lypning-mp is not built — that arm was not measured`, and the mixture works
one tier shallower. Verified by moving the binary aside and re-running the lot.

Check what you got:

```bash
lypning status      # engines, corpus, shim, hooks, log — read-only
lypning doctor      # the same with an opinion; exits non-zero on any FAIL
```

---

## 3. Integration with a coding session

This is the point of the package. `lypning install` wires three things into a
repository so that a Claude Code session (a) can route its python through the
mixture and (b) records what python it typed, which is what grows the corpus
that every design decision here is downstream of.

**Read the plan first.** `--dry-run` opens files and writes none:

```bash
cd /path/to/your/repo
lypning install --dry-run
```

```
project : /path/to/your/repo
scope   : project (/path/to/your/repo/.claude)

+ write   .claude/skills/lypning/MICROPYTHON.md    — new
+ write   .claude/skills/lypning/SKILL.md          — new
+ write   .claude/hooks/lypning-capture.sh         — new
+ write   .claude/hooks/lypning-harvest.sh         — new
+ write   .claude/hooks/lypning-session-start.sh   — new
b backup  .claude/settings.json.lypning-backup     — copy of the current settings.json
~ merge   .claude/settings.json                    — add 3 hook entries
+ write   ~/.lypning/bin/python                    — install shim
+ write   ~/.lypning/bin/python3                   — install shim

9 changes, 0 already in place
```

…followed by the exact unified diff of `settings.json` that the merge would
produce. Paths are printed absolute; they are shortened here.

### Files written

| what | where | why |
|---|---|---|
| skill | `.claude/skills/lypning/` | so the agent knows the subset exists and what it refuses |
| hooks | `.claude/hooks/lypning-*.sh` | SessionStart refreshes the shim and reports the engine state; PreToolUse logs python-ish Bash commands; Stop publishes the session's sightings |
| settings | `.claude/settings.json` | three hook entries, **merged** |
| shim | `~/.lypning/bin/{python,python3}` | a POSIX-sh wrapper that logs one JSON line and then execs the real interpreter |

**How much does the skill actually move the agent?** Measured, in
[docs/PROMPTING.md](docs/PROMPTING.md): nine prompt treatments over 884 generated
programs, on 2026-08-23. An unprompted agent writes Python that runs on the
cheapest tier **66.3%** of the time; the best prompts reach **88.5%**, which is
the ceiling of that battery; and the shipped `SKILL.md` reaches 81.7%, behind a
744-byte paragraph that gives the agent the *motive* and deliberately no feature
list at all. The same document has what that is worth in milliseconds — the
mixture's bill over the same tasks falls from 0.470x of CPython to 0.169x — and
what it costs in program length, which is about 1.4 lines.

### Settings merged

`settings.json` is a file you own and have opinions about, so it is never
overwritten. The merge deep-copies what is there, appends only entries whose
command is not already present, and leaves every unrelated key, every unrelated
hook and their original order untouched. It copies the file to
`settings.json.lypning-backup` before the first modification and never
overwrites that backup afterwards — the pristine original is the thing worth
keeping, not the last state.

A settings file that does not parse is reported and left alone. Re-running the
install is a no-op (`all 3 hook entries already present`).

Here is a real before/after. Given a repo that already had its own audit hook:

```json
{
  "permissions": { "allow": ["Bash(pytest:*)"] },
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash",
        "hooks": [ { "type": "command", "command": "sh .claude/hooks/my-audit.sh" } ] }
    ]
  }
}
```

`lypning install` produces:

```json
{
  "permissions": {
    "allow": [
      "Bash(pytest:*)"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "sh .claude/hooks/my-audit.sh"
          },
          {
            "type": "command",
            "command": "sh \"$CLAUDE_PROJECT_DIR/.claude/hooks/lypning-capture.sh\""
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "sh \"$CLAUDE_PROJECT_DIR/.claude/hooks/lypning-session-start.sh\""
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "sh \"$CLAUDE_PROJECT_DIR/.claude/hooks/lypning-harvest.sh\""
          }
        ]
      }
    ]
  }
}
```

The existing `my-audit.sh` entry keeps its position; ours is appended to the
same matcher group. `--user` installs into `~/.claude` instead. `--no-shim`,
`--no-hooks` and `--no-skill` each drop one piece. `lypning install --dry-run`
is safe to run anywhere, including a repo you do not own.

The shim will **refuse** to overwrite a `python3` it did not write — a venv
stub, a pyenv shim, a distro symlink — because clobbering one fails later,
somewhere else, as a different bug every time. `--force` moves the original to
`<name>.lypning-backup` and `uninstall` puts it back.

### Uninstallation

```bash
lypning uninstall --dry-run    # list exactly what would go
lypning uninstall              # do it
```

Uninstall is the exact inverse: it removes the skill, our hook scripts, the
hook entries whose command mentions `lypning`, and the shims — restoring
anything `--force` moved aside. Somebody else's hooks in the same file survive,
and an event array that we emptied is deleted while the file itself stays.
Verified on the example above: the `my-audit.sh` entry and the `permissions`
block came back unchanged. Not byte-identical — the file is rewritten as
2-space-indented JSON both on install and on uninstall, so your own formatting
does not survive the round trip. The `settings.json.lypning-backup` written
before the first modification is the byte-for-byte original.

**The capture log is never deleted by uninstall.** The programs in it outlive
the harness that captured them, and deleting them here would be unrecoverable.
`uninstall` says so on its last line. `rm -rf ~/.lypning` is the manual step.

### Verifying the installation

The shim only runs if its directory is first on `$PATH`:

```bash
export PATH="$HOME/.lypning/bin:$PATH"
```

`lypning status` and `lypning shim status` both shout when the shim is
installed but shadowed, because "installed but never runs" and "not installed"
have the same symptom — an empty log — and only one of them looks fixed.

---

## 3b. Embedding the runtime in a host

A shim on `$PATH` is one way in. The other is to **link the runtime** and skip
the process entirely: `liblypning` runs a program in your own thread, with its
output captured and its stdin handed to it, and there is no fork, no exec and no
pipe. On the programs lypning accepts, the process was 96% of the cost.

```bash
lypning build --lib                       # the C ABI, into ~/.lypning/lib
gcc $(lypning lib --cflags) h.c $(lypning lib --libs)
```

```c
lypning_result *r = lypning_run(q);
if (lypning_result_should_fall_onward(r)) run_on_python3(src);  /* your path */
else                                      use(lypning_result_stdout(r, &n));
```

That branch is the whole integration, and getting it right is the whole
contract: **a refusal is not an error.** It means the program is outside the
subset, that lypning ran none of it, and that CPython should answer now. A
harness that reports it as a failure has turned a speedup into a bug — silently,
because the program was fine.

Five hosts, one ABI: C and C++ headers in `assets/include/`, a Node addon with
no npm dependencies in `assets/node/`, the Rust crate directly, and
`lypning.embed` for Python via `ctypes`. Runnable examples for each live in
`assets/examples/`.

Because there is no process to kill, an embedded run takes a **step limit**
instead of a timeout, and a refusal is how it reports one. `docs/EMBEDDING.md`
is the whole story — including §7, which lists what used to be able to take a
host process down and what each of those does now.

---

## 4. Command reference

Interpreter mode is decided before argument parsing, so anything that calls
`python3` can call `lypning` instead:

| command | what it does |
|---|---|
| `lypning -c PROG [args…]` | exec the Rust core directly — no wrapper left in the process |
| `lypning FILE [args…]`, `lypning -` | same, from a file or stdin |
| `lypning run -c PROG` | route, execute, and fall through to the next tier on exit 90 only |
| `lypning route -c PROG` | print `<engine>\t<kind>: <detail>` — the tier, and the construct that stopped the cheaper one |
| `lypning build [--rust\|--micropython\|--lib\|--all]` | build the engines into `~/.lypning/bin`, and `--lib` the C ABI into `~/.lypning/lib` |
| `lypning status [--json]` | what is built, wired and captured |
| `lypning doctor [--json]` | the same with an opinion; non-zero on any FAIL |
| `lypning install [--dry-run] [--user] [--force]` | wire the skill, hooks and shim into a project |
| `lypning uninstall [--dry-run]` | remove exactly what install added |
| `lypning shim {install,uninstall,status}` | the `python`/`python3` capture shim on its own |
| `lypning hook {pre-tool-use,stop}` | hook entry points; event JSON on stdin, protocol JSON on stdout |
| `lypning conformance [--plan] [--engine E]` | run the corpus against CPython and grade every answer |
| `lypning fuzz [--iterations N] [--seed S]` | generate programs from the subset and diff them against CPython |
| `lypning bench [--startup] [--corpus]` | time the four arms, interleaved, min of repeats |
| `lypning corpus-time [--engine E] [--baseline F] [--record F]` | time the corpus on ONE binary, and diff two runs of it |
| `lypning perf [--only CASE] [--baseline F] [--record F]` | one construct at a time against CPython — where the interpreter's time goes |
| `lypning lib [--cflags\|--libs\|--path\|--static\|--include] [--json]` | where the embeddable C ABI is, and the line to compile against it |
| `lypning pool [serve\|ping\|stop] [--socket P] [--max-requests N]` | a warm CPython backstop for the chain: opt-in, off by default, `LYPNING_POOL` points at it |
| `lypning gate [BIN] [--compare]` | static? how many bytes? how many file opens? |
| `lypning harvest [--export]` | captured invocations → sightings → corpus |
| `lypning corpus [--stats] [--list]` | inspect the harvested programs |

Every subcommand that reports something takes `--json` (all of them except
`run` and `hook`, which have a caller-defined output already). Exit codes: `0` ok, `1` the command failed (a
MISMATCH, a failed gate), `2` usage — including "the core is not built",
because there is nothing to run and the fix is a command — and `90` passed
through untouched from an engine refusal. `LYPNING_DEBUG=1` turns the one-line
errors back into tracebacks.

```
$ lypning route -c 'print(2**8)'
lypning
$ lypning route -c 'import re; print(re.findall(r"\d+", s))'
lypning-mp	module: import re
$ lypning route -c 'import subprocess; subprocess.run(["ls"])'
cpython	module: import subprocess
$ lypning run -v -c 'import re; print(re.findall(r"\d+","a1b22"))'
lypning: route lypning-mp (module: import re), ran lypning-mp
['1', '22']
$ lypning -c 'import re'; echo $?
lypning: unsupported: module: import re
90
```

Environment:

| variable | effect |
|---|---|
| `LYPNING_HOME` | state dir (default `~/.lypning`) — binaries, log, build trees |
| `LYPNING_LOG` | capture log path (default `$LYPNING_HOME/invocations.jsonl`) |
| `LYPNING_BIN`, `LYPNING_MP_BIN` | override the engine binary that gets used |
| `LYPNING_LIB` | override the embeddable C ABI library (`lypning lib`, `lypning.embed`) |
| `LYPNING_POOL` | socket of a warm CPython pool (`lypning pool serve`); the chain's CPython tier uses it, and falls back to a cold spawn if it is unreachable |
| `LYPNING_CPYTHON` | override the reference CPython |
| `LYPNING_CAPTURE=0` | disable the whole capture harness |
| `LYPNING_HARVEST=0` | keep capturing, stop the Stop hook publishing |
| `LYPNING_DEBUG=1` | show tracebacks |

---

## 5. Conformance contract

```bash
lypning conformance
```

Every corpus program runs under CPython — the reference, by definition — and
under each engine. Each engine's answer is one of three things:

| verdict | meaning | failure? |
|---|---|---|
| **MATCH** | stdout and exit code identical to CPython | no |
| **UNSUPPORTED** | exit `90` with `<engine>: unsupported: <kind>: <detail>` on stderr | **no** — this is coverage, and it is the build order |
| **MISMATCH** | anything else | **yes, always** |

**A subset runtime that silently disagrees with CPython is worse than no
runtime at all**, because the agent that typed the one-liner will not notice.
It will notice a refusal, because the answer then comes from CPython one spawn
later. That asymmetry is the whole design: MISMATCH is the gate, UNSUPPORTED is
a coverage number, and driving UNSUPPORTED to zero is a project plan rather
than a release blocker.

Two caveats on how the grading actually works, both worth knowing before you
quote a coverage number:

**8.5% of the corpus is graded on its exit code alone.** 132 of 1551 programs ask
about *this run* rather than about a computation — `os.fstat(1).st_ino` prints
the inode of whichever pipe it was handed, `datetime.now()` is never twice the
same, `repr(frozenset)` moves with the hash seed, and `sys.stdlib_module_names`
differs between CPython minor versions. Two runs of the *same* interpreter
disagree on these, so demanding a stdout match would fail them forever and bury
the real signal. The other 783 are compared in full. The exclusion list is in
`conformance.py`, each entry justified in a comment, and it is deliberately hard
to grow — the temptation it guards against is silencing a real divergence by
declaring it unspecified.

**The gate is live, not aspirational, and it is currently red.** This tree has
two MISMATCHes, both on the `lypning-mp` arm, both the same defect: MicroPython
streams stdout, so a program that prints before reaching an unsupported
construct has already committed those bytes when it exits 90. lypning's Rust
core stages output and discards it on refusal; lypning-mp cannot, and the
dispatcher covers for it. `docs/LYPNING.md` §6 has the reproduction and the
consequences, `CHANGELOG.md` records why it is tracked rather than waived. It is
a real defect inherited from upstream, where the harness could not detect it —
not a harness artefact, and not something to fix by loosening the check.

`--plan` turns the refusals into that plan — which unimplemented feature blocks
the most programs:

```
by kind:
 214  module
  15  module-attr
   6  class
   6  decorator
   5  type
   4  bigint
   …
```

…above a per-blocker ranking that names an example program for each, so the
next thing to implement and something to test it against arrive together.

Two things the runner does that are not optional. Programs whose output cannot
be equal on two interpreters — timestamps, pids, addresses, set iteration order
— are **skipped by rule and listed**, never quietly passed. And the reference
run and the engine runs share one deadline, so a slow program times out on both
sides and leaves the measurement rather than being scored as a disagreement.

---

## 6. Benchmarking

```bash
lypning bench              # startup and corpus
lypning bench --startup    # `-c 'pass'`, min of 5, arms interleaved
```

**`bench` compares arms; `corpus-time` compares runs.** They answer different
questions and are not interchangeable. `bench` asks what the mixture costs
against CPython — four arms, interleaved. `corpus-time` asks whether the change
you just made to one engine sped up the programs it is actually asked to run,
by timing the whole corpus on **one** binary and diffing that run against a
recorded one:

```bash
lypning corpus-time --record before.json          # the baseline
lypning corpus-time --baseline before.json        # after the change
lypning corpus-time --engine target/release/lypning --baseline before.json
```

The diff is taken over the entries **both** runs timed, and that intersection
is printed rather than assumed — the corpus grows every session, so a baseline
from last week covers a different set of programs. Entries that exit 90 are
timed rather than skipped (a refusal costs the spawn the agent waited for) and
counted apart, because a change that moves an entry in or out of the subset
changes what is being timed.

**`perf` finds the gradient; neither of the other two can.** A corpus run says
the programs cost N milliseconds. It does not say *which construct* to open
next, because a corpus entry touches twenty of them and its cost is mostly the
spawn. `lypning perf` runs one small loop per construct on lypning and on
CPython, subtracts each arm's own startup, and sorts by the ratio:

```bash
lypning perf                        # the whole suite, worst ratio first
lypning perf --only str-concat      # one construct, while you work on it
lypning perf --record before.json   # …and --baseline before.json after
```

The table sorts by ratio; the **queue printed under it does not**. A ratio ranks
by how badly lypning loses, which is not the same list as what that costs
anybody: this suite reported `s += x` in a loop at 43x CPython — its worst row —
against a corpus in which that construct appears in *no program at all* — the
suite prints its prevalence beside every row, and `str-concat`'s is 0%. So every case carries a regex, the corpus is scanned on each run, and
the queue is ordered by **how far behind, times how much of the corpus types
it**. That second ordering is the work queue for raw speed.

It is **deliberately not an acceptance gate** — a microbenchmark once said a change was worth 48 ms per
program where the corpus said 0.14 (`docs/MICROPYTHON.md` §8a). Find with
`perf`, accept with `corpus-time --baseline`. Two rules keep the table honest:
every case prints a checksum the arms must agree on, so a construct that is
fast because it computes something else fails rather than wins; and a case
lypning *refuses* fails too, because the suite is a claim about what the subset
already covers. Either exits 1.

Two warnings, both of which this project has already paid for.

**There are two totals and they answer different questions.** The *shared
subset* total covers only the programs every arm actually ran — the only
apples-to-apples comparison. The *whole corpus* total covers everything, which
is what a session actually costs, and where an arm that refuses work looks
cheap for a reason that is not speed. A total over different program sets is
not a comparison. `bench` prints both, labels both, and annotates the refusing
arms with `cheaper because it REFUSES, not because it is faster`.

**This is deliberately not in CI.** A wall-clock benchmark on a shared runner
measures the runner. `bench` detects a CI environment and says so in a banner
rather than let a number from a noisy box get quoted as a finding. CI keeps the
deterministic half — conformance and routing safety.

> **Running the corpus can rewrite a repository.** These are real programs from
> real agent sessions, so the corpus is full of one-liners that edit `src/` and
> `docs/`. Every entry runs in its own temp cwd, entries naming an absolute path
> are skipped rather than run (246 of 1551 here), and the whole battery is
> bracketed by a `git status` snapshot that restores and reports anything that
> changed anyway. That last one is a **net, not a sandbox**: it cannot undo a
> write outside the repository, it only makes the next occurrence loud. It
> exists because the first measurement runs upstream rewrote 34 tracked files.

---

## 7. Corpus, capture, and privacy

The corpus is the argument. It is not a test suite someone designed; it is what
agents actually typed, captured from real sessions:

```
$ lypning corpus --stats
entries           1551
  shim             670   43.2%
  hook             404   26.0%
  transcript       316   20.4%
  seed             161   10.4%
one-liners         141    9.1%
multi-line        1410   90.9%
length (lines)  median 7  p90 46  max 600
with argv           55
with stdin          19
unparsed here       21

top imports           top builtins
sys            465    print          2025
json           269    open            909
re             166    len             418
io             124    int             255
```

`transcript` is 20.4% of that and climbing, and it is worth reading the split
before reading a build order off it: the transcript feed is what the sessions
*working on lypning* typed, so part of any ranking derived from it is a mirror.
The hillclimb skill says the same thing at more length, and `lypning corpus
--stats` prints the split precisely so it can be read that way.

**What gets logged.** Two feeds, both appending JSON lines to
`~/.lypning/invocations.jsonl`. The shim catches every invocation that reached
an interpreter — nested ones, subshells, pipelines, Makefiles. The PreToolUse
hook catches the Bash *command string*, which is the only place a heredoc body,
a `uv run` wrapper or a write-then-run pattern is visible at all. A record holds
the program text, the argv tail, the cwd, a timestamp and the session id.

**Where it goes.** The Stop hook publishes this session's captures as
`tests/corpus/sightings/<session>.jsonl` in the project — one writer per path,
an *added* file rather than a rewritten one, so two branches cannot conflict.
The export is a union by key: re-running it produces byte-identical output and
does not touch the file, which is what makes firing on every turn boundary
safe. `lypning harvest` (run deliberately, never from a hook) derives
`corpus.jsonl` from the accumulated sightings.

**Nothing is ever committed on your behalf.** The hooks write files; they do not
run `git`. A hook that made commits would fight the session's own git work.

**Redaction.** Sightings are committed, so every program, argv tail and stdin
sample goes through redaction *before* the record's id is computed. Live
credentials are matched **by value** — the harvester runs in the same container
as your environment variables, so a credential-named env var's literal value is
replaced with `[REDACTED env <NAME> <n> chars]`, naming which credential to
rotate without restating it — and by shape for the ones that announce
themselves (`sk-`, `ghp_`, `AKIA`, `AIza`, `xox*`, PEM blocks). The log itself
is **not** redacted and is not safe to publish; it stays outside the repository
on purpose. Redaction is a backstop, not a promise: read the diff on a corpus
refresh. See `docs/CAPTURE.md` §Privacy.

**Turning it off.** `LYPNING_CAPTURE=0` disables both feeds — the shim still
execs python, the hooks still answer, nothing is written.
`LYPNING_HARVEST=0` keeps capturing but stops the Stop hook publishing.
`lypning uninstall` removes the wiring entirely. No edit to any file is needed
for any of the three.

Hooks never block and never fail a session: every one of them prints
`{"continue":true,"suppressOutput":true}` and exits 0 on every path, including
its own failures. There is deliberately no `permissionDecision` field anywhere
— answering `allow` from a PreToolUse hook would bypass the permission prompt
for every Bash command in the session, which is a far bigger change than a
capture harness is allowed to make.

---

## 8. Credit

This package was extracted from
[github.com/kristerhedfors/deepresearch.se](https://github.com/kristerhedfors/deepresearch.se),
where it was developed as **`mopy`** (the Rust subset) and **`pygram`** (the
MicroPython variant) to make Python affordable inside that project's in-browser
CheerpX sandbox. The measurements in §1, the corpus, the conformance battery
and the capture harness all come from there. `CHANGELOG.md` carries the dated
history under *Before the name*, with links to the upstream pull requests; those
two names appear in this repository only there, here, and inside the historical
corpus JSONL, whose captured programs are left verbatim.

MIT licensed. See `LICENSE`.

---

## 9. Repository layout

```
src/lypning/
  cli.py               the front door: interpreter mode, then subcommands
  engines.py           find an engine, run it, route between them, dispatch
  paths.py             the assets (read-only, in the wheel) / state (~/.lypning) split
  build.py             cargo and make — and the refusal-contract assert on the result
  conformance.py       MATCH / UNSUPPORTED / MISMATCH, and the build plan
  routing.py           IDEAL / LATE / WASTED / UNSAFE — did the classifier pick right
  fuzz.py              generate from the subset's own grammar, diff, shrink
  bench.py             four arms, interleaved, min of repeats, two totals — and corpus-time
  gate.py              static? how many bytes? how many file opens?
  corpus.py            load, merge, describe — the one module that is pure data
  capture.py           the hook entry points
  harvest.py           log → sightings → corpus, with redaction before the hash
  install.py           merging into someone else's .claude/settings.json
  shim.py              python/python3 on PATH, and when to refuse to install it
  assets/rust/         the Rust core — zero crates, size-tuned release profile
  assets/micropython/  the MicroPython variant, its patches and frozen shim stdlib
  assets/corpus/       corpus.jsonl + seed-corpus.jsonl
  assets/claude/       the skill, the hook scripts, the settings.json fragment
  assets/shim/         python-shim (POSIX sh, no python)
  assets/scripts/      build-rust.sh, build-micropython.sh
docs/                  see below, plus logo.svg — the thundercloud in the name
site/                  the GitHub Pages generator: build.py, index.md, style.css
tests/
Makefile               thin wrappers: build test check conformance fuzz bench gate
                       doctor install dist dist-check clean  (`make help`)
```

| doc | what |
|---|---|
| `docs/LYPNING.md` | the design: the measurement, the subset, the three refusals, the classifier, the commit barrier |
| `docs/MICROPYTHON.md` | the cost model both runtimes are optimised against, and the second tier's charter |
| `docs/SANDBOX-PERFORMANCE.md` | where the 8,573 ms cold `python3 --version` comes from |
| `docs/SUBSET.md` | what the subset must execute, entry by entry |
| `docs/RESEARCH.md` | what the second tier should have been built from, and why MicroPython won |
| `docs/CAPTURE.md` | the two capture feeds, the harvest, and the privacy rules |
| `docs/HARNESSES.md` | wiring the loop into opencode and the OpenHands SDK: what each install writes, what it refuses to write and why, and what is verified against a real install versus merely documented |
| `docs/COOKBOOK.md` | unsupported Python, rewritten — what to type when a tier refuses |
| `docs/EMBEDDING.md` | linking the runtime into a harness: the C ABI, the five hosts over it, and what a refusal means when there is no exit code |
| `docs/PROMPTING.md` | can an agent be *asked* into the subset? 884 generated programs across nine prompt treatments, and what each one bought |
| `docs/BENCH-LEDGER.md` | append-only measurement history, including the losses |
| `docs/HILLCLIMB.md` | append-only ledger of improvement steps — the four numbers each moved, and the ones that moved nothing |
| `docs/FORKING.md` | fork it and tune it to YOUR programs: the capture→harvest→gate→step loop as a standing feature, and every optimization classified by whether it transfers, re-derives, or must be re-measured |
| `docs/COMPARISON.md` | against ADK-Rust CodeAct + Monty: one instrument over the corpus, both columns measured, and when to choose which |
| `docs/PAPER.md` | the write-up: what coding agents actually emit, profiled; and CPython / PyPy / MicroPython / Monty / lypning benchmarked on it — startup, parse, execute, memory, compatibility, and total wall clock |
| `docs/EXECUTIVE-SUMMARY.md` | the verdict, as objective as the data allows: where lypning improves (measured), where it regresses, where it loses, and the biases that flatter it |

Working on this repository? Read `CLAUDE.md` first, and
`.claude/skills/hillclimb/SKILL.md` for the loop that improves it — what to
measure, which instrument can see which curve, and the traps already paid for.
