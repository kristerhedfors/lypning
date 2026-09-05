# lypning — the Coding Harness Interpreter Optimizer

<img src="docs/logo.svg" alt="" width="72" height="72">

`lypning` optimizes the interpreter layer underneath a coding harness: it runs
a Python program on the cheapest interpreter that can actually run it. The
architecture is a *mixture of Pythons* — a **spectrum** of from-scratch Python
subsets written in Rust, built from one crate at two sizes (`lypning`, budgeted
8 blocks; `lypning-l`, 32 blocks — `gate.VARIANT_BLOCK_BUDGET`), and the real
CPython for everything they refuse — with a classifier that asks the Rust core's
own parser which one, per program. The subset is sized not to Python but to the
one-liners a coding agent actually types, the only reason this is affordable.
**And that is a moving target, which is the point of the name**: the corpus is
captured from live sessions, the loop re-derives the tables from it, and the
whole thing is built to be forked and re-tuned to *your* harness, *your* agent,
*your* programs — models drift, and this optimizer drifts with them
([`docs/FORKING.md`](docs/FORKING.md)).

Every tier refuses the same way: exit `90`, one line on stderr, nothing on
stdout. That is what makes the three interchangeable, and what makes a wrong
route cost one wasted process spawn instead of a wrong answer. `docs/PAPER.md`
is the write-up, the baselines that beat us included.

## How a program reaches an interpreter

```
  python3 -c '…'   the shim on PATH — or the PreToolUse hook — hands the program to
       │           `lypning run`; the dispatcher IS the Rust binary, not a wrapper
       ▼           route: one parse of lypning's own front end grades every variant
  ┌─▶ lypning      the Rust core — runs IN-PROCESS, no spawn; on refusal, exit 90:
  │      │         one line on stderr, none on stdout, and the chain moves on
  ├─▶ lypning-l    the same crate, larger — FORKED, stderr piped, so its 90 is caught
  └─▶ cpython      the reference — EXEC'D: replaces the process, no way back, none needed
         ▼         the program's own stdout, the program's own exit code
```

- **The route is exact for every Rust variant**: `route.rs` grades each from
  the one parse (`verdicts`); a kind in `ONLY_CPYTHON_KINDS` skips the spectrum.
- **The winning case costs nothing**: `lypning` runs in *this* process, output
  staged behind the commit barrier, discarded on refusal (`docs/LYPNING.md` §6).
- **Any rung with something after it is forked**, so its exit 90 can be caught;
  **only the last rung is exec'd** (`main.rs` `exec_engine`).

## 1. Measurement

One instrument per question (§5, §6), and every instrument prints the corpus
size it loaded: quote that number with its date, never a remembered one
(`CLAUDE.md` invariant 3). Every measurement this document once carried is in
`docs/BENCH-LEDGER.md`, dated — the last full `lypning bench` (2026-08-25) and
the upstream table it was written against moved there on 2026-09-04, the day
`lypning-mp` became the oracle — measured, never routed to; nothing has been
re-measured on `lypning → lypning-l → cpython` since.

## 2. Installation

```bash
pip install lypning       # pure Python, zero runtime dependencies (not on PyPI as of 2026-09-04: `pip install .` from a checkout)
lypning build --rust      # → `ok` per spectrum variant, and the seconds each took, into ~/.lypning/bin
lypning status            # → each engine's path, bytes and blocks; the corpus count loaded
```

Nothing compiles at install time: until `lypning build --rust` runs, `status`
says `not built` and every program routes to CPython. The crate needs `cargo`
and nothing from crates.io (`CLAUDE.md` invariant 6); a build is not `ok` until
the refusal contract holds on the binary it just produced
(`build.check_refusal_contract`; `docs/VERIFICATION.md` §C2). `--lib` builds
the C ABI (§3b), `--all` both, and `--micropython` the oracle, which needs a
32-bit toolchain and a network and is absent by default — a missing arm is a
status line, never an error: `status` and `doctor` say `not built`, `bench`
leaves a hole rather than a zero, and `conformance` measures it only when named
(`--engine lypning-mp`; `docs/VERIFICATION.md` §C12). The wheel shape — `pip
install --no-build-isolation .` into a venv, `assets/` read-only — is tested on
purpose, never by accident (`docs/VERIFICATION.md` §C13).

## 3. Integration with a coding session

`lypning install` wires a skill (what the subset refuses), three hooks merged
into `.claude/settings.json` (SessionStart refreshes the shim, PreToolUse logs
python-ish Bash commands, Stop publishes the session's sightings) and a
`python`/`python3` shim in `~/.lypning/bin` into a repository, so a Claude Code
session routes its python through the mixture and records what it typed;
`--harness opencode,openhands` does the same for those hosts
(`docs/HARNESSES.md`). `--dry-run` prints the plan — one line per file, the
backup, the merge, a warning when `~/.lypning/bin` is not on `PATH` — then the
`settings.json` diff, and writes nothing; a second `lypning install` prints
`all 3 hook entries already present`.

`settings.json` is merged, never overwritten: hook entries whose command is
absent are appended; unrelated keys, hooks and their order survive; the
original is copied once to `settings.json.lypning-backup` and never
re-backed-up; a file that does not parse is reported and left alone
(`CLAUDE.md` invariant 7; `docs/VERIFICATION.md` §C10). A real before/after,
given a repo that already had its own audit hook:

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
same matcher group. `--user` installs into `~/.claude`; `--no-shim`,
`--no-hooks` and `--no-skill` each drop one piece. The shim **refuses** to
overwrite a `python3` it did not write — a venv stub, a pyenv shim, a distro
symlink; `--force` moves it to `<name>.lypning-backup`. `lypning uninstall`
(`--dry-run` lists what would go) is the exact inverse: it removes the skill,
our hook scripts, the hook entries whose command mentions `lypning`, and the
shims, restoring anything `--force` moved aside; other hooks survive.

**The capture log is never deleted by uninstall.** The programs in it outlive
the harness that captured them, and deleting them here would be unrecoverable.
`uninstall` says so on its last line. `rm -rf ~/.lypning` is the manual step.

The shim only runs if `~/.lypning/bin` is first on `$PATH`; `lypning status`
and `lypning shim status` both shout when it is shadowed, because "installed but
never runs" and "not installed" have the same symptom — an empty log — and only
one of them looks fixed.

## 3b. Embedding the runtime in a host

The other way in is to **link the runtime**: `liblypning` (`lypning build
--lib`, then `gcc $(lypning lib --cflags) h.c $(lypning lib --libs)`) is
`lypning-l` reached through the C ABI in your own thread — no fork, no exec, no
pipe — and its refusal line begins `lypning-l:`.

```c
lypning_result *r = lypning_run(q);
if (lypning_result_should_fall_onward(r)) run_on_python3(src);  /* your path */
else                                      use(lypning_result_stdout(r, &n));
```

That branch is the whole integration, and getting it right is the whole
contract: **a refusal is not an error.** It means the program is outside the
subset, that lypning ran none of it, and that CPython should answer now. A
harness that reports it as a failure has turned a speedup into a bug — silently,
because the program was fine. Every host has a quickstart (`docs/EMBEDDING.md`
§4); an embedded run takes a **step limit**, not a timeout, and reports one as
a refusal (`docs/EMBEDDING.md` §6; `docs/VERIFICATION.md` §C14).

## 4. Command reference

Anything that calls `python3` can call `lypning` instead. One row per
subcommand; flags are in `lypning <command> --help`; `run` and `hook` have a
caller-defined output, every other subcommand takes `--json`.

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
| `lypning routes` | the value-dependent refusals a static route could not see — write-only with respect to routing (`docs/VERIFICATION.md` §C11) | yes |

Exit codes (`cli.py`): `0` ok; `1` the command failed (a MISMATCH, a failed
gate, a fuzz counterexample, a doctor FAIL); `2` usage, including "the core is
not built" and `engines.EngineError`; `90` an engine refusal, passed through
untouched; `127` the Rust dispatcher cannot run an engine; `130` interrupted.
Timeouts, by command: `docs/VERIFICATION.md` §15. `LYPNING_DEBUG=1` restores
tracebacks. A clean route prints the engine name alone, a refusal-derived one
`<engine>\t<kind>: <detail>` (run 2026-09-04 against the binaries in §7):

```
$ lypning route -c 'import collections; print(collections.Counter("abca").most_common(1))'
lypning-l   module: import collections
$ lypning -c 'import re'; echo $?
lypning: unsupported: module: import re
90
```

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

## 5. Conformance contract

```bash
lypning conformance                 # exit 1 on any MISMATCH or UNSAFE; MISMATCH must be 0
lypning conformance --mixture both  # …and both dispatchers: prints `dispatchers agree N/N`
```

Every corpus program runs on CPython and on each built arm — by default
`lypning`, `lypning-l` and the mixture (`conformance.DEFAULT_ARMS`); the oracle,
`library` (the C ABI) and `mixture-rust` (the Rust dispatcher's chain) are
opt-in by `--engine`. Each answer is one of three:

| verdict | meaning | failure? |
|---|---|---|
| **MATCH** | stdout and exit code identical to CPython | no |
| **UNSUPPORTED** | exit `90` with `<engine>: unsupported: <kind>: <detail>` on stderr | **no** — this is coverage, and it is the build order |
| **MISMATCH** | anything else | **yes, always** |

MISMATCH is the gate and UNSUPPORTED is a coverage number; never clear a
MISMATCH by widening a capability table (`CLAUDE.md` invariant 1). Programs
whose output cannot be equal on two interpreters — timestamps, pids, set order
— run with stdout uncompared and are graded on exit code alone
(`conformance.is_nondeterministic`); reference and engine share one deadline, so
a reference timeout leaves the measurement and an engine-only timeout is a
MISMATCH. The same run grades the routes — IDEAL, WASTED, LATE, UNSAFE
(`routing.py`); UNSAFE must be 0, and `accuracy` is a census, not a cost model:
a LATE is a CPython spawn, a WASTED an in-process parse (measured 2026-09-04,
`CHANGELOG.md` #42) — and holds the two dispatchers to each other: `dispatchers
agree N/N`, the floor rule, `monotone violations 0` (`CLAUDE.md` invariant 10).
`--plan` ranks the refusals by cost (`->cpy`, a CPython spawn per program);
`lypning routes --plan` is its companion. `docs/VERIFICATION.md` §C3–C5.

## 6. Performance tools

**`bench` compares arms; `corpus-time` compares runs.** `lypning bench` prints
two totals over `cpython`, `lypning`, `lypning-l` and `mixture`, interleaved:
the *shared subset* every arm ran, the only apples-to-apples comparison, and
the *whole corpus*, where an arm that refuses work is annotated `cheaper because
it REFUSES, not because it is faster`. `lypning corpus-time --record F`, then
`--baseline F` after the change, times **one** binary twice and diffs the runs.
`lypning perf` finds the gradient — one loop per construct, startup subtracted
— and is not an acceptance gate: find with `perf`, accept with `corpus-time`.
`lypning gate` is static — bytes in blocks of 131,072 B (`gate.DEVICE_BLOCK`),
file opens. `bench` is not in CI: a wall-clock benchmark on a shared runner
measures the runner. Every battery runs behind the net (`CLAUDE.md` invariant
4; `docs/VERIFICATION.md` §C6, §C8).

## 7. Corpus, capture, and privacy

The corpus is the argument: what agents actually typed, captured from real
sessions. Two feeds append JSON lines to `~/.lypning/invocations.jsonl` — the
shim catches every invocation that reached an interpreter, the PreToolUse hook
the Bash command string. The Stop hook publishes a session's captures under
`tests/corpus/sightings/`, and `lypning harvest` derives `corpus.jsonl` from
them, redacting before the id is computed (`docs/CAPTURE.md`, *Privacy*: the
log itself is not redacted and stays outside the repository).

**Nothing is ever committed on your behalf.** The hooks write files; they do not
run `git`. Hooks never block and never fail a session: every one prints
`{"continue":true,"suppressOutput":true}` and exits 0 on every path
(`CLAUDE.md` invariant 5; `docs/VERIFICATION.md` §C9). `LYPNING_CAPTURE=0`
disables both feeds; `LYPNING_HARVEST=0` keeps capturing, stops publishing.

What this tree loads, from one run — `lypning corpus --stats`, 2026-09-04,
commit 437056c, Darwin arm64: 3688 entries (hook 50.5%, transcript 26.9%, shim
18.2%, seed 4.4%); `lypning status` that run put `lypning` at 818,080 B and
`lypning-l` at 867,744 B, 7 blocks each. `hook` and `transcript` are largely
what sessions *working on lypning* typed, so a build order read off the corpus
is partly a mirror; the split is printed so it can be read that way.

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
src/lypning/   cli.py (the front door) · engines.py (find, run, route, dispatch) · paths.py · build.py · gate.py · conformance.py ·
               routing.py · routes.py · oracle.py · fuzz.py · bench.py · perf.py · pool.py · corpus.py · capture.py · harvest.py ·
               install.py · shim.py · embed.py · harness/ — and assets/: rust/ (the one crate) · include/ (lypning.h, .hpp) · examples/
               node/ go/ swift/ lua/ (one quickstart each) · micropython/ (the oracle's build) · corpus/ · prompt/ · claude/ opencode/
               openhands/ shim/ scripts/ (what install writes)   —   docs/ · site/ (Pages) · study/ · tests/ · Makefile (`make help`)
```

| doc | what |
|---|---|
| `docs/VERIFICATION.md` | the QA spine: every contract as a command, its expected output from a dated run of record, the failure text a regression prints, and the test that pins it |
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

`site/build.py` publishes this markdown at
[kristerhedfors.github.io/lypning](https://kristerhedfors.github.io/lypning/)
(enable Pages once: **Settings → Pages → Source: GitHub Actions**). Working on
this repository? Read `CLAUDE.md` first, then the `hillclimb` skill under
`.claude/skills/`. Check it yourself, with `~/.lypning/bin` built:

```bash
lypning build --rust && lypning conformance --mixture both && lypning doctor && lypning gate
# → `ok` per variant · MISMATCH 0, dispatchers agree N/N · 0 FAIL · PASS — docs/VERIFICATION.md §C1–C15
```
