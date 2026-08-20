# lypning — a mixture of Pythons

`lypning` runs a Python program on the cheapest of three interpreters that can
actually run it: a from-scratch Python subset written in Rust, a MicroPython
variant with a frozen shim stdlib, and the real CPython for everything the
first two refuse. A classifier decides which one, per program, by asking the
Rust core's own parser rather than guessing at the text. The subset is not
sized to Python — it is sized to the one-liners a coding agent actually types,
which is a much narrower target and is the only reason any of this is
affordable.

Every tier refuses the same way: exit `90`, one line on stderr, nothing on
stdout. That is what makes the three interchangeable, and what makes a wrong
route cost one wasted process spawn instead of a wrong answer.

---

## 1. The measurement, first

Everything below is downstream of one table. **472 harvested programs, min of
5, arms interleaved per entry, measured 2026-08-16** — on the upstream
container, *before* this package was extracted from it. Quoted here because it
is the argument for the project; it is not a claim about your machine.

```
startup — `-c 'pass'`

arm         min ms   vs cpython
cpython      11.04     1.000x
lypning-mp    1.20     0.109x
lypning       1.03     0.093x
mixture       1.05     0.095x

corpus — 472 programs

arm          ran  refused   shared total (323)   vs cpython
cpython      472        0          4314.4 ms      1.000x
lypning-mp   444       28           616.5 ms      0.143x
lypning      324      148           440.3 ms      0.102x
mixture      472        0           547.0 ms      0.127x

whole corpus — what a session of 472 one-liners costs

cpython     6987.9 ms   1.000x
lypning-mp  1153.1 ms   0.165x   (28 unanswered)
lypning      673.3 ms   0.096x   (148 unanswered)
mixture     1860.8 ms   0.266x   (0 unanswered)
```

Read it in this order:

- **lypning is the fastest engine on the work it accepts** — 0.102x of CPython
  on the shared subset, and faster there than lypning-mp (0.143x). That is the
  whole thesis: a runtime built for two-thirds of the distribution beats a
  general one on that two-thirds.
- **The mixture answers everything CPython answers** — 472/472, zero
  mismatches — at 0.266x of CPython's cost. The other two arms are cheaper
  only because they refuse work, and a refusal still costs its spawn.
- **Startup is at parity with lypning-mp, not better.** lypning's binary is
  ~1.0 MB against lypning-mp's ~270 KB; both are static musl and both open zero
  files at startup, so they arrive at the same place by different routes.

**Re-measure. Do not cite — and the first bullet above did not reproduce.**
Running `lypning bench` here on 2026-08-20, on a different container against a
corpus capture has grown to **842 programs, 763 measured**, with all three
engines built:

```
shared subset — 500 programs every arm executed

arm          ran  refused   shared total    median   vs cpython
cpython      763        0        9153.3 ms    16.12    1.000x
lypning      500      263         716.9 ms     1.37    0.078x
lypning-mp   714       49         583.6 ms     1.13    0.064x
mixture      763        0         978.4 ms     1.33    0.107x

whole corpus                     vs cpython
cpython     18207.5 ms             1.000x
mixture      5925.7 ms             0.325x   (0 unanswered — saves 67.5%)

startup — `-c 'pass'`, min of 15
mixture 0.77 ms   lypning-mp 0.83 ms   lypning 0.91 ms   cpython 13.84 ms
```

The mixture result held: 763/763 answered at 0.325x of CPython, a 67.5% saving.
**The ordering of the two subset engines reversed.** Upstream measured lypning
at 0.102x beating lypning-mp at 0.143x on the shared subset; here lypning-mp is
0.064x against lypning's 0.078x, and it starts faster too (0.83 ms against
0.91 ms). Two consecutive runs agree to within 2%, so this is not noise.

Read honestly, that means **the first bullet above is upstream's result, not a
property of the design.** The shared subset is by construction the 500 programs
lypning accepted — the simplest in the corpus — where both engines sit near
their startup floor, and lypning-mp's floor is lower: its binary is 294,788 B
against lypning's 1,036,984 B. The claim that a runtime built for two-thirds of
the distribution beats a general one *on that two-thirds* is a claim about a
particular corpus on a particular machine, and this machine did not reproduce
it.

What survives re-measurement is the part the mixture is actually for: answering
everything CPython answers, for a third of the cost. Both tools print the corpus
size they loaded, every run, for exactly this reason — **never quote a
remembered corpus size**, and do not carry a remembered ordering either.

`docs/LYPNING.md` §1 is the full table with its caveats, `docs/BENCH-LEDGER.md`
is the append-only history including the runs where the subset lost.

---

## 2. Install

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
dependencies, so nothing is fetched from crates.io. A clean release build took
**17.9 s** on this container (4 CPUs, 2026-08-20); budget ~25 s on a slower box.
Build it alone with `lypning build --rust`.

**The MicroPython tier is optional and usually absent.** It needs a 32-bit C
toolchain (`gcc-multilib`) and network access — the build fetches musl and a
pinned MicroPython — and it takes minutes rather than seconds. `lypning build`
with no flags attempts both and exits non-zero if either fails, so ask for the
one you want:

```bash
lypning build --rust           # the core, ~18 s, cargo only
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

## 3. Hook it into a coding session

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

### What it writes

| what | where | why |
|---|---|---|
| skill | `.claude/skills/lypning/` | so the agent knows the subset exists and what it refuses |
| hooks | `.claude/hooks/lypning-*.sh` | SessionStart refreshes the shim and reports the engine state; PreToolUse logs python-ish Bash commands; Stop publishes the session's sightings |
| settings | `.claude/settings.json` | three hook entries, **merged** |
| shim | `~/.lypning/bin/{python,python3}` | a POSIX-sh wrapper that logs one JSON line and then execs the real interpreter |

### What it merges

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

### Undo it

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

### Once it is wired

The shim only runs if its directory is first on `$PATH`:

```bash
export PATH="$HOME/.lypning/bin:$PATH"
```

`lypning status` and `lypning shim status` both shout when the shim is
installed but shadowed, because "installed but never runs" and "not installed"
have the same symptom — an empty log — and only one of them looks fixed.

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
| `lypning build [--rust\|--micropython\|--all]` | build the engines into `~/.lypning/bin` |
| `lypning status [--json]` | what is built, wired and captured |
| `lypning doctor [--json]` | the same with an opinion; non-zero on any FAIL |
| `lypning install [--dry-run] [--user] [--force]` | wire the skill, hooks and shim into a project |
| `lypning uninstall [--dry-run]` | remove exactly what install added |
| `lypning shim {install,uninstall,status}` | the `python`/`python3` capture shim on its own |
| `lypning hook {pre-tool-use,stop}` | hook entry points; event JSON on stdin, protocol JSON on stdout |
| `lypning conformance [--plan] [--engine E]` | run the corpus against CPython and grade every answer |
| `lypning bench [--startup] [--corpus]` | time the four arms, interleaved, min of repeats |
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
| `LYPNING_CPYTHON` | override the reference CPython |
| `LYPNING_CAPTURE=0` | disable the whole capture harness |
| `LYPNING_HARVEST=0` | keep capturing, stop the Stop hook publishing |
| `LYPNING_DEBUG=1` | show tracebacks |

---

## 5. The conformance contract

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

**7% of the corpus is graded on its exit code alone.** 59 of 842 programs ask
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
> are skipped rather than run (79 of 842 here), and the whole battery is
> bracketed by a `git status` snapshot that restores and reports anything that
> changed anyway. That last one is a **net, not a sandbox**: it cannot undo a
> write outside the repository, it only makes the next occurrence loud. It
> exists because the first measurement runs upstream rewrote 34 tracked files.

---

## 7. The corpus, capture, and privacy

The corpus is the argument. It is not a test suite someone designed; it is what
agents actually typed, captured from real sessions:

```
$ lypning corpus --stats
entries            842
  hook             384   45.6%
  shim             277   32.9%
  seed             161   19.1%
  transcript        20    2.4%
one-liners         112   13.3%
multi-line         730   86.7%
length (lines)  median 6  p90 61  max 600
with argv           25
with stdin          19
unparsed here        5

top imports           top builtins
sys            135    print          1025
json           128    open            652
io             122    len             241
re             115    repr            101
```

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
and the capture harness all come from there. Those two names appear nowhere
else in this repository — this paragraph is the historical attribution and the
whole of it.

MIT licensed. See `LICENSE`.

---

## 9. Layout

```
src/lypning/
  cli.py               the front door: interpreter mode, then subcommands
  engines.py           find an engine, run it, route between them, dispatch
  paths.py             the assets (read-only, in the wheel) / state (~/.lypning) split
  build.py             cargo and make — and the refusal-contract assert on the result
  conformance.py       MATCH / UNSUPPORTED / MISMATCH, and the build plan
  bench.py             four arms, interleaved, min of repeats, two totals
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
docs/                  see below
tests/
Makefile               thin wrappers: build test conformance bench gate install dist clean
```

| doc | what |
|---|---|
| `docs/LYPNING.md` | the design: the measurement, the subset, the three refusals, the classifier, the commit barrier |
| `docs/MICROPYTHON.md` | the cost model both runtimes are optimised against, and the second tier's charter |
| `docs/SANDBOX-PERFORMANCE.md` | where the 8,573 ms cold `python3 --version` comes from |
| `docs/SUBSET.md` | what the subset must execute, entry by entry |
| `docs/RESEARCH.md` | what the second tier should have been built from, and why MicroPython won |
| `docs/CAPTURE.md` | the two capture feeds, the harvest, and the privacy rules |
| `docs/COOKBOOK.md` | unsupported Python, rewritten — what to type when a tier refuses |
| `docs/BENCH-LEDGER.md` | append-only measurement history, including the losses |

Working on this repository? Read `CLAUDE.md` first.
