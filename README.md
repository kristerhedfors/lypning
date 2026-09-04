# lypning — the Coding Harness Interpreter Optimizer

<img src="docs/logo.svg" alt="" width="72" height="72">

`lypning` optimizes the interpreter layer underneath a coding harness: it runs
a Python program on the cheapest interpreter that can actually run it. The
architecture is a *mixture of Pythons* — a **spectrum** of from-scratch Python
subsets written in Rust and built from one crate at increasing sizes
(`lypning`, 1 MB; `lypning-l`, up to 4 MB) and the real
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

## How a program reaches an interpreter

```
  python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'
     │  the shim on PATH — or the PreToolUse hook — hands the program
     ▼  to lypning instead of to CPython
  ┌──────────────────────────────────────────────────────────────────┐
  │ lypning run — the dispatcher IS the Rust binary, not a wrapper   │
  │ route: one parse of lypning's own front end grades every variant │
  └───┬─────────────────────────────────────────────────┬───────┬────┘
      ▼                                                 │       │
  ┌────────────────────────────────────────────────┐    │       │
  │ lypning · the Rust core — runs IN-PROCESS      │    │       │
  │ no spawn; output staged, never written on 90   │    │       │
  └───┬────────────────────────────────────────────┘    │       │
      │ exit 90 · one line on stderr, none on stdout    │       │
      ▼                                                 │       │
  ┌────────────────────────────────────────────────┐    │       │
  │ lypning-l · the same crate, larger             │◀───┘       │
  │ FORKED, stderr piped, so its exit 90 is caught │            │
  └───┬────────────────────────────────────────────┘            │
      │ exit 90, as above                                       │
      ▼                                                         │
  ┌────────────────────────────────────────────────┐            │
  │ cpython · the reference — EXEC'D               │◀───────────┘
  │ replaces the process; no way back, none needed │
  └───┬────────────────────────────────────────────┘
      ▼  the program's own stdout, the program's own exit code
```

- **The route is exact for every Rust variant**: `route.rs` grades each from
  the one parse (`verdicts`), and a kind in `ONLY_CPYTHON_KINDS` skips the
  whole spectrum. One parse, no spawn.
- **The winning case costs nothing**: a program routed to `lypning` runs in
  *this* process (`main.rs` `dispatch`).
- **A refusal is a non-event**: exit `90`, one line on stderr, nothing on
  stdout — output is staged behind the commit barrier and discarded on refusal
  (`docs/LYPNING.md` §6).
- **Any rung with something after it is forked**, stderr piped, so its exit 90
  can be caught; **only the last rung is exec'd** (`main.rs` `exec_engine`).
  So a wrong route costs one process spawn, never a wrong answer.

The same `lypning conformance` run grades the routes — IDEAL, WASTED, LATE,
UNSAFE, NO-ENGINE (`routing.py`) — and UNSAFE must be 0. Its `accuracy` line
is a census, not a cost model: LATE and WASTED weigh the same there and cost
differently — a LATE is a CPython spawn, a WASTED an in-process parse
(measured 2026-09-04, `CHANGELOG.md` #42) — so read the grades with the
milliseconds beside them. The checks are `docs/VERIFICATION.md` §C4–C5.

---

## 1. Measurement

One instrument per question, and every instrument prints the corpus size it
loaded — quote that number with its date, never a remembered one (`CLAUDE.md`
invariant 3):

| question | instrument |
|---|---|
| what the mixture costs against CPython | `lypning bench` — arms `cpython`, `lypning`, `lypning-l`, `mixture`, interleaved; two totals, the shared subset and the whole corpus |
| did my change to one engine speed up its programs | `lypning corpus-time --baseline F` — one binary, two runs, diffed over the entries both timed |
| which construct is slow | `lypning perf` — one loop per construct against CPython, startup subtracted |
| does every answer agree with CPython, and did the router pick right | `lypning conformance` — §5 |
| what a binary costs cold | `lypning gate` — static, bytes in blocks of 131,072 B (`gate.DEVICE_BLOCK`), file opens |

The `lypning bench` run of 2026-08-25 (1551 programs loaded, 1305 measured)
put the mixture at 0.302x of CPython over all 1305; that table, and the
2026-08-16 upstream one it was written up against, are in
`docs/BENCH-LEDGER.md` under 2026-09-04, where they were moved from here, with
every run since, including the ones the subset lost. Both predate 2026-09-04,
when `lypning-mp` became the oracle — measured, never routed to; nothing has
been re-measured on `lypning → lypning-l → cpython`, so every timing on that
chain is unmeasured on this tree. `docs/PAPER.md` has the 2026-08-31 sweeps.

> **Docs site.** Published from this repository's markdown by `site/build.py`
> to [kristerhedfors.github.io/lypning](https://kristerhedfors.github.io/lypning/).
> Pages must be enabled once, under **Settings → Pages → Source: GitHub
> Actions** — a workflow token can deploy to a Pages site but cannot create one.

---

## 2. Installation

```bash
pip install lypning       # pure Python, zero runtime dependencies
lypning build --rust      # the spectrum, into ~/.lypning/bin; prints `ok` and the seconds per variant
lypning status            # prints each engine's path, bytes and blocks, and the corpus count loaded
```

> Not on PyPI as of 2026-09-04. Install from a checkout: `pip install .` (or
> `pip install -e .`), which is the same wheel and the same console script.

`pip install` ships the CLI, the corpus and the engine *sources*; nothing
compiles at install time. Until `lypning build --rust` runs, `lypning status`
says `not built` and every program routes to CPython. The crate needs `cargo`
and nothing from crates.io (`CLAUDE.md` invariant 6), and a build is not `ok`
until the refusal contract holds on the binary it just produced
(`build.check_refusal_contract`); only `ok` binaries are installed.

```bash
lypning build --rust                        # every spectrum variant: lypning, lypning-l
lypning build --rust --variant lypning-l    # one of them
lypning build --lib                         # the C ABI, into ~/.lypning/lib (§3b)
lypning build --all                         # the spectrum and the library
lypning build --micropython                 # the oracle — needs a 32-bit toolchain and a network
lypning build --dry-run                     # prints the commands, builds nothing
```

`lypning-mp`, the oracle, is absent by default, and a missing arm is a status
line, never an error: `status` and `doctor` say `not built`, no route has a
rung for it, `bench` leaves a hole in its table rather than a zero, and
`conformance` measures it only when named (`--engine lypning-mp`).

---

## 3. Integration with a coding session

`lypning install` wires three things into a repository so that a Claude Code
session can route its python through the mixture and records what python it
typed — which is what grows the corpus every table here is downstream of: a
skill (what the subset refuses), three hooks merged into
`.claude/settings.json` (SessionStart refreshes the shim, PreToolUse logs
python-ish Bash commands, Stop publishes the session's sightings), and a
`python`/`python3` shim in `~/.lypning/bin` that logs one line and execs the
real interpreter. `--harness opencode,openhands` wires the same capture into
those hosts; `docs/HARNESSES.md` says what each writes and what is verified.

**Read the plan first.** `--dry-run` opens files and writes none (`CLAUDE.md`
invariant 7):

```bash
cd /path/to/your/repo
lypning install --dry-run     # exit 0; prints the plan, then the settings.json diff
```

```
harness: Claude Code
project : /path/to/your/repo
scope   : project (/path/to/your/repo/.claude)

+ write   .claude/skills/lypning/MICROPYTHON.md  — new
+ write   .claude/skills/lypning/SKILL.md  — new
+ write   .claude/hooks/lypning-capture.sh  — new
+ write   .claude/hooks/lypning-harvest.sh  — new
+ write   .claude/hooks/lypning-session-start.sh  — new
b backup  .claude/settings.json.lypning-backup  — copy of the current settings.json
~ merge   .claude/settings.json  — add 3 hook entries
+ write   ~/.lypning/bin/python  — install shim
+ write   ~/.lypning/bin/python3  — install shim
. skip    ~/.lypning/bin  — WARNING: ~/.lypning/bin is NOT on PATH — the shim will never run — fix: export PATH="~/.lypning/bin:$PATH"

9 changes, 0 already in place, 1 warning (the `.` line above)
```

…followed by the unified diff of `settings.json` the merge would produce (run
2026-09-04; paths are printed absolute and are shortened here). How far the
skill moves the agent is measured in `docs/PROMPTING.md` (nine prompt
treatments, 2026-08-23, on `lypning` alone).

### Settings merged

`settings.json` is merged, never overwritten: hook entries whose command is
absent are appended; unrelated keys, hooks and their order survive; the
original is copied once to `settings.json.lypning-backup` and never
re-backed-up. A file that does not parse is reported and left alone.
Re-running prints `all 3 hook entries already present`.

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
same matcher group. `--user` installs into `~/.claude` instead; `--no-shim`,
`--no-hooks` and `--no-skill` each drop one piece. The shim will **refuse** to
overwrite a `python3` it did not write — a venv stub, a pyenv shim, a distro
symlink; `--force` moves the original to `<name>.lypning-backup` and
`uninstall` puts it back.

### Uninstallation

```bash
lypning uninstall --dry-run    # list exactly what would go
lypning uninstall              # do it; the last line says the log was kept
```

Uninstall is the exact inverse: it removes the skill, our hook scripts, the
hook entries whose command mentions `lypning`, and the shims — restoring
anything `--force` moved aside. Somebody else's hooks in the same file survive.
The file is rewritten as 2-space-indented JSON both ways, so your own
formatting does not survive the round trip; `settings.json.lypning-backup` is
the byte-for-byte original.

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

The other way in is to **link the runtime**: `liblypning` is `lypning-l`
reached through the C ABI in your own thread — no fork, no exec, no pipe —
and its refusal line begins `lypning-l:`.

```bash
lypning build --lib                             # the C ABI, into ~/.lypning/lib; prints ok
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

Every host — C, C++, Rust, Node, Go, Swift, LuaJIT, Python — has a quickstart
that is the file to copy, obeying one contract, `quickstart "<python source>"
[args...]`; the table is `docs/EMBEDDING.md` §4. An embedded run takes a
**step limit** instead of a timeout, and a refusal is how it reports one
(`docs/EMBEDDING.md` §6).

---

## 4. Command reference

Interpreter mode is decided before argument parsing, so anything that calls
`python3` can call `lypning` instead. One row per subcommand; flags are in
`lypning <command> --help`. `run` and `hook` have a caller-defined output; every
other subcommand takes `--json`, held by
`tests/test_docs.py::test_json_is_offered_exactly_where_the_readme_says_it_is`.

| command | what it does | `--json` |
|---|---|---|
| `lypning -c PROG [args…]` | exec the Rust core directly — no wrapper left in the process | — |
| `lypning FILE [args…]`, `lypning -` | the same, from a file or stdin | — |
| `lypning run -c PROG` | route, run, and fall through to the next rung on exit 90 only | no |
| `lypning route -c PROG` | print the engine a program would run on, and why | yes |
| `lypning build` | build the spectrum into `~/.lypning/bin`; `--lib` the C ABI, `--micropython` the oracle | yes |
| `lypning lib` | the flags a C or C++ host needs to link liblypning | yes |
| `lypning pool` | a warm CPython backstop for the chain — opt-in, `LYPNING_POOL` points at it | yes |
| `lypning status` | what is built, wired and captured | yes |
| `lypning doctor` | the same with an opinion; exit 1 on any FAIL | yes |
| `lypning install` | wire capture into a coding harness (`--harness claude,opencode,openhands`) | yes |
| `lypning uninstall` | remove exactly what install added | yes |
| `lypning shim {install,uninstall,status}` | the `python`/`python3` capture shim on its own | yes |
| `lypning hook EVENT` | harness hook entry points: event JSON on stdin, protocol JSON on stdout | no |
| `lypning conformance` | run the corpus against CPython and grade the answers — §5 | yes |
| `lypning fuzz` | generate programs from the subset and diff against CPython; exit 1 on a counterexample | yes |
| `lypning bench` | time startup and the whole corpus, arm by arm | yes |
| `lypning corpus-time` | time the whole corpus on ONE binary, and diff two runs of it | yes |
| `lypning perf` | time one construct at a time against CPython | yes |
| `lypning gate [BIN]` | measure a binary against the acceptance table; exit 1 on a failed check | yes |
| `lypning oracle` | what a second reimplementation of Python got wrong | yes |
| `lypning harvest` | turn captured invocations into corpus entries | yes |
| `lypning corpus` | inspect the harvested programs | yes |
| `lypning routes` | the value-dependent refusals a static route could not see — write-only with respect to routing | yes |

Exit codes (`cli.py`): `0` ok; `1` the command failed — a MISMATCH, a failed
gate, a fuzz counterexample, a doctor FAIL; `2` usage, including "the core is
not built" and a pinned binary that is not an engine (`engines.EngineError`);
`90` passed through untouched from an engine refusal; `127` from the Rust
dispatcher when it cannot run an engine (`lypning: cannot run <bin>`); `130`
interrupted. Default timeouts: 30 s per program (`--timeout`) in `run`,
`conformance`, `bench`, `corpus-time` and `fuzz`, 60 s per `perf` case.
`LYPNING_DEBUG=1` turns the one-line errors back into tracebacks.

A clean route prints the engine name alone; a refusal-derived route prints
`<engine>\t<kind>: <detail>` — the rung, and the construct that stopped the
cheaper one. Run on 2026-09-04 against the binaries in §7's block:

```
$ lypning route -c 'print(2**8)'
lypning
$ lypning route -c 'import collections; print(collections.Counter("abca").most_common(1))'
lypning-l	module: import collections
$ lypning route -c 'import subprocess; subprocess.run(["ls"])'
cpython	module: import subprocess
$ lypning run -v -c 'import collections; print(collections.Counter("abca").most_common(1))'
lypning: route lypning-l (module: import collections), ran lypning-l
[('a', 2)]
$ lypning -c 'import re'; echo $?
lypning: unsupported: module: import re
90
```

Environment:

| variable | effect |
|---|---|
| `LYPNING_HOME` | state dir (default `~/.lypning`) — binaries, log, build trees |
| `LYPNING_LOG` | capture log path (default `$LYPNING_HOME/invocations.jsonl`) |
| `LYPNING_BIN`, `LYPNING_L_BIN` | pin a spectrum variant's binary (`engines.env_var_for`) |
| `LYPNING_MP_BIN` | pin the oracle's binary — measured, never routed to |
| `LYPNING_LIB` | override the embeddable C ABI library (`lypning lib`, `lypning.embed`) |
| `LYPNING_POOL` | socket of a warm CPython pool (`lypning pool serve`); the chain's CPython tier uses it, and falls back to a cold spawn if it is unreachable |
| `LYPNING_CPYTHON` | override the reference CPython |
| `LYPNING_CAPTURE=0` | disable the whole capture harness |
| `LYPNING_HARVEST=0` | keep capturing, stop the Stop hook publishing |
| `LYPNING_ROUTES=0` | stop the Python dispatcher's write-only ledger (`lypning routes`); `LYPNING_CAPTURE=0` also covers it |
| `LYPNING_DEBUG=1` | show tracebacks |

---

## 5. Conformance contract

```bash
lypning conformance                 # exit 1 on any MISMATCH or UNSAFE; MISMATCH must be 0
lypning conformance --mixture both  # …and both dispatchers: prints `dispatchers agree N/N`
```

Every corpus program runs on CPython and on each built arm — by default
`lypning`, `lypning-l` and the mixture (`conformance.DEFAULT_ARMS`); the
oracle, the `library` (`lypning-l` through the C ABI, in-process) and
`mixture-rust` (the chain walked by the Rust dispatcher) are opt-in by
`--engine`. Each arm's answer is one of three things:

| verdict | meaning | failure? |
|---|---|---|
| **MATCH** | stdout and exit code identical to CPython | no |
| **UNSUPPORTED** | exit `90` with `<engine>: unsupported: <kind>: <detail>` on stderr | **no** — this is coverage, and it is the build order |
| **MISMATCH** | anything else | **yes, always** |

MISMATCH is the gate and UNSUPPORTED is a coverage number; never clear a
MISMATCH by widening a capability table (`CLAUDE.md` invariant 1). Programs
whose output cannot be equal on two interpreters — timestamps, pids, addresses,
set order — are skipped by rule and listed, never quietly passed; the reference
and the engine share one deadline, so a reference timeout leaves the
measurement and an engine-only timeout is a MISMATCH. The same run grades the
routes (IDEAL / WASTED / LATE / UNSAFE, above) and holds the two dispatchers to
each other: `--mixture both` must print `dispatchers agree N/N`, a router never
sends a program to a variant smaller than itself, and a program `lypning`
answers `lypning-l` must answer (`monotone violations 0`).

`--plan` turns the refusals into a build order ranked by what a feature
COSTS — the `->cpy` column, a CPython spawn per program that reaches CPython —
with the block count beside it; `lypning routes --plan` is the companion
ranking, drawn from live sessions. The full check, with expected output, is
`docs/VERIFICATION.md` §C3–C5.

---

## 6. Performance tools

```bash
lypning bench                              # startup and corpus, four arms interleaved; prints two totals
lypning corpus-time --record before.json   # one binary; then --baseline before.json after the change
lypning perf --only str-concat             # one construct against CPython; exit 1 on a checksum disagreement
```

**`bench` compares arms; `corpus-time` compares runs.** `bench` asks what the
mixture costs against CPython — arms `cpython`, `lypning`, `lypning-l` and
`mixture`, interleaved — and prints two totals: the *shared subset* every arm
ran, the only apples-to-apples comparison, and the *whole corpus*, what a
session costs, where an arm that refuses work is annotated `cheaper because it
REFUSES, not because it is faster`. `corpus-time` times the whole corpus on
**one** binary and diffs it against a recorded run over the entries both timed;
refusals are timed and counted apart. `perf` finds the gradient — one loop per
construct, startup subtracted, ranked by ratio times corpus prevalence — and is
not an acceptance gate: find with `perf`, accept with `corpus-time --baseline`.
A `perf` case whose checksum the arms dispute, or that `lypning` refuses, fails.

`bench` is deliberately not in CI: a wall-clock benchmark on a shared runner
measures the runner, and `bench` says so in a banner when it detects one. The
corpus battery rewrites repositories, so every run is behind the net
(`CLAUDE.md` invariant 4): a temp cwd per entry per engine, absolute-path
entries skipped, and a `git status` bracket that restores and reports.

---

## 7. Corpus, capture, and privacy

The corpus is the argument: what agents actually typed, captured from real
sessions. Two feeds append JSON lines to `~/.lypning/invocations.jsonl`: the
shim on `$PATH` catches every invocation that reached an interpreter, and the
PreToolUse hook catches the Bash command string, the only place a heredoc body
or a `uv run` wrapper is visible. The Stop hook publishes a session's captures
as `tests/corpus/sightings/<session>.jsonl` — one writer per path, a union by
key, byte-identical on re-run — and `lypning harvest` derives `corpus.jsonl`
from the sightings, redacting before the id is computed. `docs/CAPTURE.md` is
the whole story, including *Privacy*: the log itself is not redacted and stays
outside the repository.

**Nothing is ever committed on your behalf.** The hooks write files; they do not
run `git`. A hook that made commits would fight the session's own git work.
Hooks never block and never fail a session: every one prints
`{"continue":true,"suppressOutput":true}` and exits 0 on every path
(`CLAUDE.md` invariant 5). `LYPNING_CAPTURE=0` disables both feeds,
`LYPNING_HARVEST=0` keeps capturing but stops the Stop hook publishing, and
`lypning uninstall` removes the wiring.

What this tree loads, from one run — `lypning corpus --stats`, 2026-09-04,
commit 437056c, Darwin arm64, the top-imports and top-builtins table omitted;
the same run's `lypning status` reported `lypning` at 818,080 B and
`lypning-l` at 867,744 B, 7 blocks each, on that host:

```
corpus
======
entries           3688
  hook            1863   50.5%
  transcript       992   26.9%
  shim             670   18.2%
  seed             163    4.4%
by model
  unattributed    3688  100.0%
one-liners         292    7.9%
multi-line        3396   92.1%
length (lines)  median 10  p90 51  max 600
with argv          152
with stdin          19
unparsed here       47
```

`hook` and `transcript` are largely what the sessions *working on lypning*
typed, so a build order read off this table is partly a mirror; the split is
printed so it can be read that way.

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
  cli.py            the front door: interpreter mode, then subcommands
  engines.py        find an engine, run it, route between them, dispatch
  paths.py          the assets (read-only, in the wheel) / state (~/.lypning) split
  build.py          cargo and make — and the refusal-contract assert on the result
  conformance.py    MATCH / UNSUPPORTED / MISMATCH, and the build plan
  routing.py        IDEAL / LATE / WASTED / UNSAFE — did the classifier pick right
  routes.py         the write-only ledger of runtime refusals
  oracle.py         the oracle's recorded divergences
  fuzz.py           generate from the subset's own grammar, diff, shrink
  bench.py          four arms, interleaved, two totals — and corpus-time
  perf.py           one construct at a time against CPython
  pool.py           the warm CPython backstop: opt-in, off by default
  gate.py           static? how many bytes? how many file opens?
  corpus.py         load, merge, describe — the one module that is pure data
  capture.py        the hook entry points
  harvest.py        log → sightings → corpus, with redaction before the hash
  install.py        merging into someone else's .claude/settings.json
  shim.py           python/python3 on PATH, and when to refuse to install it
  embed.py          liblypning from Python: ctypes over the C ABI
  harness/          the claude, opencode and OpenHands adapters
  assets/rust/      the one crate: every spectrum variant, zero crate dependencies
  assets/include/   lypning.h, the C ABI, and lypning.hpp over it
  assets/examples/ node/ go/ swift/ lua/   the hosts: one quickstart each
  assets/micropython/   the oracle's build: patches and the frozen shim stdlib
  assets/corpus/    corpus.jsonl + seed-corpus.jsonl
  assets/claude/ opencode/ openhands/   what `install` writes per harness
  assets/prompt/    routing.md, the paragraph the prompting study measured
  assets/shim/ scripts/   python-shim (POSIX sh); build-rust.sh, build-micropython.sh
docs/               see below, plus logo.svg
site/               the GitHub Pages generator: build.py, index.md, style.css
study/              the prompting study: tasks, prompts, scoring, and hosts/
tests/
Makefile            thin wrappers  (`make help`)
```

| doc | what |
|---|---|
| `docs/VERIFICATION.md` | every contract as a command, its expected output and the test that pins it — the QA document |
| `docs/LYPNING.md` | the design: the measurement, the subset, the refusals, the classifier, the dispatcher, the commit barrier |
| `docs/SUBSET.md` | what the subset must execute, entry by entry |
| `docs/COOKBOOK.md` | unsupported Python, rewritten — what to type when an engine refuses |
| `docs/CAPTURE.md` | the two capture feeds, the harvest, and the privacy rules |
| `docs/HARNESSES.md` | wiring capture into opencode and the OpenHands SDK: what each install writes, what it refuses to write, and what is verified against a real install |
| `docs/EMBEDDING.md` | linking the runtime into a harness: the C ABI, the hosts over it, and what a refusal means when there is no exit code |
| `docs/MICROPYTHON.md` | `lypning-mp`, the oracle: what a second reimplementation got wrong, and the cost model both were built against |
| `docs/SANDBOX-PERFORMANCE.md` | the cost model — cold blocks, the exec floor, spawns — measured upstream, dated |
| `docs/PROMPTING.md` | can an agent be *asked* into the subset? nine prompt treatments, measured 2026-08-23 |
| `docs/COMPARISON.md` | against ADK-Rust CodeAct + Monty: one instrument over the corpus, both columns measured |
| `docs/PAPER.md` | the write-up: what coding agents actually emit, and CPython / PyPy / MicroPython / Monty / lypning benchmarked on it |
| `docs/EXECUTIVE-SUMMARY.md` | the verdict: where lypning improves, where it regresses, where it loses, and the biases that flatter it |
| `docs/RESEARCH.md` | how the reimplementation was chosen — history |
| `docs/BENCH-LEDGER.md` | append-only measurement history, including the losses |
| `docs/HILLCLIMB.md` | append-only ledger of improvement steps — the four numbers each moved, and the ones that moved nothing |
| `docs/FORKING.md` | fork it and tune it to YOUR programs: the capture→harvest→gate→step loop |

Check it yourself, on a fresh checkout with `~/.lypning/bin` built:

```bash
lypning build --rust && lypning conformance --mixture both && lypning doctor && lypning gate
# → `ok` per variant · MISMATCH 0, dispatchers agree N/N · 0 FAIL · PASS — docs/VERIFICATION.md §C1–C15
```

Working on this repository? Read `CLAUDE.md` first, and
`.claude/skills/hillclimb/SKILL.md` for the loop that improves it — what to
measure, which instrument can see which curve, and the traps already paid for.
