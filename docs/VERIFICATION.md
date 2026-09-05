# Verification — every contract, as a check

`CLAUDE.md` states the invariants; the design documents say what each
mechanism is; this document says how to check each one and what a
regression prints. Every contract has six parts: STATEMENT (`CLAUDE.md`'s
words, with the invariant number), CODE HOME (`file:symbol`, never a line
number), CHECK (bash for a checkout with `~/.lypning/bin` built, each
command followed by `; echo $?`), EXPECTED (what the run of record printed,
headed by its marker, then which fields may differ), FAILURE MODES and
PINNED BY (tests, by node id). The engines are `lypning`, `lypning-l` and
`cpython`; `lypning-mp` is the oracle — measured, never routed to — and
appears here only as a hole. A reader with a fresh checkout can run this
top to bottom and file a report against a contract id (§17).

## 0. Run of record

Every EXPECTED block is quoted from one run, taken on the day and commit
below in a fresh git worktree with its own `LYPNING_HOME`. The commands were
typed as `PYTHONPATH=src python3 -m lypning …`, the entry point the
`lypning` console script wraps; the blocks spell them `lypning`.

```
run of record · 2026-09-04 · 437056c · Darwin arm64 · corpus 3688 programs
python3 --version      Python 3.14.5
lypning --version      lypning 0.1.0 (lypning)
lypning-l --version    lypning 0.1.0 (lypning-l)
lypning status — the engine, oracle and library lines:
  lypning:    /private/tmp/lyp-b1/bin/lypning  (818,080 B, 7 blocks)
  lypning-l:  /private/tmp/lyp-b1/bin/lypning-l  (867,744 B, 7 blocks)
  cpython:    /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/bin/python3.14  (52,448 B, 1 blocks)
  lypning-mp: not built  — `lypning build --micropython` (needs a network); `lypning oracle` reads the recorded divergences either way
  liblypning: /tmp/lyp-b1/lib/liblypning.dylib  (1,033,328 B, ABI 1)
corpus       3688 programs
```

The byte counts are host builds on Darwin arm64 (`rustup` is absent there;
`build --rust` says so), not the musl bytes CI gates. No timing from this
run is quoted — the host was shared with other batteries — and a `secs`
column or an `in N.Ns` field in a quoted block is shown as `<s>`. The oracle
was never built on this host, so the run of record is the oracle-absent
path (§C12) taken for real.

**How to refresh this document.** Regenerate every EXPECTED block and this
header together, from one run, into a scratch directory; diff; commit them
as one change. No block is edited by hand: a block that disagrees with a
fresh run is a stale run or a defect, and §17 applies.

```bash
git worktree add ../lypning-verify -b verify && cd ../lypning-verify
export LYPNING_HOME=/tmp/lypning-verify RR=/tmp/lypning-verify-run; mkdir -p "$RR"
lypning build --rust -v > "$RR/build.txt"; echo $?
lypning build --lib -v > "$RR/build-lib.txt"; echo $?
lypning conformance --mixture both > "$RR/conformance.txt"; echo $?
lypning conformance --plan > "$RR/plan.txt"; echo $?
for c in doctor gate status oracle routes; do lypning $c > "$RR/$c.txt"; echo "$c $?"; done
python -m pytest -q tests/test_verification.py    # the three fixture tables
git status --porcelain                             # prints nothing
```

**What each gate cannot see.** A green gate is a claim about one thing.

| gate | what it cannot see |
|---|---|
| `lypning build --rust` | routing: it asserts §C1 on the binary it produced and nothing about where a program goes |
| `lypning conformance` | cost: a MATCH says nothing about milliseconds |
| `lypning doctor` | a MISMATCH: it re-checks the contract on one program per artefact, never the corpus |
| `lypning gate` | wall clock: bytes, blocks and opens are a shape, not a time |
| `lypning bench` | compute, and cold blocks: it is spawn-bound and runs on a warm local filesystem |
| `lypning fuzz` | what the engine's own tables do not generate |
| `lypning routes` | the Rust dispatcher's refusals: `lypning run` in the binary never writes it, so every count is a floor |

## 1. C1 — The refusal contract

**STATEMENT.** Invariant 2. A refusal is exit `90`, exactly one
`<engine>: unsupported: <kind>: <detail>` line on **stderr**, and **nothing
at all on stdout**. Any other non-zero exit is the program's own and must be
returned unchanged — a dispatcher that retried on exit 1 would run a
half-completed program twice. Each variant writes its **own** name at the
head of its refusal line, through `engines.refusal_line`, never a literal
(invariant 9).

**CODE HOME.** `engines.refusal_line`; `engines.Result.refused` (exit 90
**and** the line — what the chain keys on); `err.rs:refusal_line`,
`err.rs:ENGINE` (the binary's own name, from `build.rs`); `main.rs:finish`
(`sys.exit(90)` leaves `kind` empty: not a refusal);
`__init__.UNSUPPORTED_EXIT`; `conformance._UNSUPPORTED_RE` (the kind
vocabulary is `[\w-]+`).

**CHECK.**

```bash
lypning -c 'import subprocess'; echo $?
lypning -c 'import subprocess' 2>/dev/null | wc -c | tr -d ' '
~/.lypning/bin/lypning-l -c 'import subprocess'; echo $?
lypning -c 'print(1); import subprocess' 2>/dev/null | wc -c | tr -d ' '   # the commit barrier
lypning -c 'import sys; sys.exit(90)'; echo $?      # 90 without the line: the program's own
lypning -c 'x = 1/0'; echo $?                       # not a refusal: returned unchanged
grep -ho 'unsupported("[a-z-]*' src/lypning/assets/rust/src/*.rs | sort -u | wc -l | tr -d ' '
```

**EXPECTED** — `lypning · 2026-09-04 · 437056c · 3688 loaded`:

```
lypning: unsupported: module: import subprocess
90
0
lypning-l: unsupported: module: import subprocess
90
0
90
Traceback (most recent call last):
ZeroDivisionError: division by zero
1
38
```

Nothing may differ except the last line: the count of distinct kinds the
source spells (38 on 2026-09-04) grows when a variant learns a refusal. The
seventeen probes behind this contract are
`tests/verification/refusal-probes.json`.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| a refusal became a traceback | `Traceback (most recent call last):` and exit 1 — it compiles, links and passes `--version` | build: `BROKEN — exit 1, expected 90 (…)`; conformance: `MISMATCH … exit` |
| bytes reached stdout before the 90 | `MISMATCH  lypning  <id>: contract: refused after N byte(s) had already reached stdout` | conformance; build: `BROKEN — the refusal line reached stdout: …` |
| a variant writes a sibling's name | `BROKEN — stderr was 'lypning: …', expected 'lypning-l: …'` | build; doctor: `FAIL refusal contract (lypning-l)` |

**PINNED BY.**

```
tests/test_engines.py::test_live_engine_emits_the_refusal_contract
tests/test_conformance.py::test_exit_90_without_the_contract_line_is_a_mismatch
tests/test_commit_barrier.py::test_rust_core_refuses_with_stdout_untouched
tests/test_cli.py::test_run_passes_a_program_s_own_exit_code_through
tests/test_verification.py::test_every_refusal_probe_exits_and_prints_as_the_table_says
```

## 2. C2 — Build asserts C1

**STATEMENT.** Invariant 2: the contract is asserted on the binary that was
just built, before a build is allowed to report `ok`. Only `ok` binaries
are installed; a broken one is left in the build tree with its reason. A
cross-target binary is installed under a suffixed name, never over the
plain one.

**CODE HOME.** `build.check_refusal_contract` (exit 90, the exact line
headed by the variant's own name, empty stdout);
`build.check_spectrum_contract` (`route --spectrum` and `--version` name
the variant, and the table is `engines.SPECTRUM`); `build.build_rust` (the
`unsupported contract: held` / `BROKEN — <why>` line, `ok` false on BROKEN);
`build.build_lib` (the same, through `embed.check_refusal_contract`);
`build.install_binaries` (`--target i686` installs as `lypning-i686`);
`cli.cmd_build` (exit 1 unless every requested artefact built).

**CHECK.**

```bash
lypning build --rust -v > build.txt; echo $?
grep -E '^(engine|lypning|installed)|unsupported contract' build.txt
lypning build --lib -v | grep -E '^lypning lib|unsupported contract'; echo $?
```

**EXPECTED** — `lypning build · 2026-09-04 · 437056c · 3688 loaded`:

```
0
engine     target  bytes   blocks  secs  status
lypning    host    818080  7       <s>   ok  (rustup not found: built for the host instead of x86_64-unknown-linux-musl)
lypning-l  host    867744  7       <s>   ok  (rustup not found: built for the host instead of x86_64-unknown-linux-musl)
unsupported contract: held
unsupported contract: held
installed /tmp/lyp-b1/bin/lypning  (target: host)
installed /tmp/lyp-b1/bin/lypning-l  (target: host)
lypning lib  host    1033328  8       <s>   ok
unsupported contract (in-process): held
0
```

Expected to differ: `bytes`, `blocks`, `target` and its parenthetical (a
host with `rustup` builds musl and says nothing), paths, `secs`. Must not
differ: `ok` on every row, one `unsupported contract: held` per variant,
exit 0.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| the contract on the fresh binary | `FAILED: the unsupported contract is broken: exit 1, expected 90 (…)` — not installed | build, exit 1 |
| the binary mis-names itself, or its table drifts | `BROKEN — the binary calls itself 'lypning', expected 'lypning-l'`; `BROKEN — the compiled spectrum […] is not engines.SPECTRUM […]` | build |
| a cross-target build on a host that cannot run it | installed as `lypning-i686`; the plain `lypning` untouched | none — by design |

**PINNED BY.**

```
tests/test_build.py::test_the_host_build_is_this_machines_engine_and_installs_unsuffixed
tests/test_build.py::test_each_variant_is_one_cargo_feature_and_its_own_target_dir
tests/test_build.py::test_verify_measures_the_binary_that_was_just_built
tests/test_engines.py::test_refusal_line_is_what_the_build_and_the_embedding_pin
```

## 3. C3 — Conformance

**STATEMENT.** Invariant 1: MISMATCH is always a bug. UNSUPPORTED never is.
`lypning conformance` must end at `MISMATCH 0`, and a rising UNSUPPORTED
count is a coverage number and a build order (`--plan`), not a regression.
Never "fix" a MISMATCH by widening a capability table.

**CODE HOME.** `conformance.DEFAULT_ARMS` (`engines.SPECTRUM` plus
`mixture`); `conformance.OPT_IN_ARMS` (`lypning-mp`, `library`,
`mixture-rust`: measured only when named); `conformance.classify`;
`conformance.is_nondeterministic`, `_RUN_SPECIFIC`,
`_IMPLEMENTATION_DEFINED`, `draws_from_random`, `only_set_order_differs`;
`conformance.DEFAULT_TIMEOUT`; `conformance.Report.ok`; `conformance.plan`,
`plan_cost` (the `->cpy` ranking); `cli.cmd_conformance` (exit 1 unless the
report and the routing grade are both ok). The verdicts are `README.md`
§5's table; the sub-kinds of MISMATCH:

| sub-kind | when | how it is graded |
|---|---|---|
| `timeout` | the engine hit the deadline the reference finished inside | one deadline on both sides; a reference timeout is a `Skip` (`reference timed out after 30s`) and leaves the measurement |
| `unbuilt` | a requested arm has no binary for one entry | a whole absent arm is a `note:` line and is not measured, never a MISMATCH |
| `contract` | exit 90 after bytes reached stdout, or exit 90 with no line | unless CPython exited 90 too (`sys.exit(90)`), which compares like any exit code |
| `stdout` | stdout differs | the first differing line; not compared when the entry is tagged `nondeterministic`, matches `_RUN_SPECIFIC` or `_IMPLEMENTATION_DEFINED`, draws from `random` unseeded, or differs only in set order — the verdict then reads `MATCH` with `stdout uncompared` |
| `exit` | the exit code differs | `exit N, CPython gave M` |
| `stderr` | CPython reported an error and the engine was silent | CPython's warning blocks are stripped first (`conformance._without_warnings`) |

A seeded `random` stream is compared on every arm but the oracle
(`conformance.is_seeded_stream`).

**CHECK.**

```bash
lypning conformance --mixture both; echo $?
lypning conformance --plan > plan.txt; echo $?; head -3 plan.txt
lypning conformance --engine lypning-mp --limit 5 | grep '^note'; echo $?
```

**EXPECTED** — `lypning conformance · 2026-09-04 · 437056c · 3688 loaded`:

```
conformance over 2504 corpus programs in <s>s (reference: /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/bin/python3.14)

engine       MATCH  UNSUPPORTED  MISMATCH   coverage
lypning      1573          931         0     62.8%
lypning-l    1741          763         0     69.5%
mixture      2504            0         0    100.0%
mixture-rust  2504            0         0    100.0%

skipped 1184 not run: absolute path outside the sandbox (949), imports lypning and drives a battery (208), would spawn a lypning battery (conformance/bench/corpus-time/perf) (27)

monotone violations 0 over 2504 — a larger variant never does worse than a smaller one on a program both ran

dispatchers agree 2504/2504 — the Python dispatcher (`mixture`) and the Rust one (`mixture-rust`, what `lypning run` execs) over the same binaries

MISMATCH 0 — ok

routing safety over 2504 programs (2504 have an engine that matches)

  IDEAL      2393  routed to the cheapest engine that works
  WASTED       73  engine refused; one extra spawn, right answer
  LATE         38  worked, but a cheaper engine would have too
  UNSAFE        0  routed to an engine that MISMATCHES  <-- must be 0
  NO-ENGINE     0  no engine matched; not the router's fault

  accuracy 95.6% ideal, 97.1% correct-on-first-try
  predictions: lypning=1568  cpython=766  lypning-l=170
0
0
build order — 107 distinct blockers over 2504 programs
(a program is blocked by the FIRST thing it hits, so counts shift as features land)
ranked by ->cpy: the refusals that reach CPython, which is what
note: lypning-mp is not built — that arm was not measured
0
```

Expected to differ: every count (they grow with the corpus and move with
every capability that lands), the reference path, `<s>`. Must not differ:
`MISMATCH 0 — ok`, `UNSAFE 0`, `monotone violations 0 over N`,
`dispatchers agree N/N` with the two numbers equal, `ranked by ->cpy` when
the mixture arm ran, exit 0. On 2026-09-04 the first of two `--plan` runs
exited 1 with this identical ranking and the second exited 0: `--plan`
prints the build order instead of the table, so a MISMATCH in the same run
shows only as the exit code — re-run without `--plan` to see which entry.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| an engine disagrees with CPython | `  MISMATCH  <engine>  <id>: <kind>: <detail>` per entry, then `MISMATCH N — FAIL` | conformance, exit 1 |
| a dispatcher disagreement, or a larger variant worse than a smaller one | `dispatchers agree N/M …:  FAIL`; `monotone violations N over M …:  FAIL`, each with the entries named | conformance (§C4), exit 1 |
| the net restored files, or an unsafe route | `!! N repository file(s) changed by corpus programs and have been restored:`; `routing errors N (must be 0) — …` | conformance (§C8, §C5), exit 1 |

**PINNED BY.**

```
tests/test_conformance.py::test_a_refusal_is_coverage_not_a_failure
tests/test_conformance.py::test_a_timeout_is_a_mismatch_not_a_slow_program
tests/test_conformance.py::test_stdout_is_not_compared_for_a_run_specific_program
tests/test_conformance.py::test_an_unbuilt_engine_is_an_absent_arm_not_a_failed_one
tests/test_conformance.py::test_plan_ranks_by_cpython_reach_not_by_block_count
```

## 4. C4 — Two dispatchers, one answer

**STATEMENT.** Two dispatchers walk the chain — `route.rs` inside every
binary (`~/.lypning/bin/lypning run`, what the shim execs) and
`engines.dispatch` (the `lypning run` console script and the `mixture` arm)
— and they decide in lockstep: `lypning conformance --mixture both` prints
`dispatchers agree N/N`, and a disagreement fails the run. The floor rule:
a router never sends a program to a variant smaller than the binary that
routed it. The monotone rule: a program the smaller variant answers, the
larger must answer, with the same stdout.

**CODE HOME.** `route.rs:verdicts` (one verdict per rung; a rung below the
routing binary is `floor: below the routing binary`); `route.rs:chain_after`
and `engines.chain_after_refusal`; `route.rs:ONLY_CPYTHON_KINDS`,
`route.rs:CPYTHON_ONLY_KINDS`, `engines.ONLY_CPYTHON_REFUSALS` (kinds that
rule out every Rust variant); `engines.Result.refused` and
`embed.rs:fall_onward` (the two fall-onward signal sets);
`main.rs:exec_engine` (fork versus exec); `conformance.run` (`dispatchers`,
`monotone_violations`). The two signal sets:

| dispatcher | falls onward on |
|---|---|
| Python, `engines.dispatch` | exit 90 **with** the contract line (`Result.refused`), and nothing else |
| Rust, `main.rs` through `embed.rs:fall_onward` | exit 90; also `MemoryError` on stderr; also `Traceback (` on stderr at exit 0 |

Both then walk one rule: a kind in `ONLY_CPYTHON_REFUSALS` /
`ONLY_CPYTHON_KINDS` goes straight to `cpython`; otherwise each later
sibling whose static verdict was "can run" and whose `cap-*` set is a strict
superset, then `cpython`. A rung with something after it is forked so its
exit 90 can be caught; the last rung is exec'd (`main.rs:exec_engine`):
`lypning` runs in-process, `lypning-l` is forked, `cpython` is exec'd.

**CHECK.**

```bash
lypning conformance --mixture both | grep -E '^(monotone|dispatchers|MISMATCH [0-9])'; echo $?
lypning route --next --after lypning --kind bigint -c 'print(2**100)'
lypning route --next --after lypning --kind set-order -c 'print({3, 1, 2})'
lypning route --json -c 'import collections'
~/.lypning/bin/lypning run -c 'print(2**100)'; echo $?    # the Rust dispatcher
lypning run -c 'print(2**100)'; echo $?                   # the Python dispatcher
```

**EXPECTED** — `lypning conformance · 2026-09-04 · 437056c · 3688 loaded`:

```
monotone violations 0 over 2504 — a larger variant never does worse than a smaller one on a program both ran
dispatchers agree 2504/2504 — the Python dispatcher (`mixture`) and the Rust one (`mixture-rust`, what `lypning run` execs) over the same binaries
MISMATCH 0 — ok
0
["lypning-l","cpython"]
["cpython"]
{"engine":"lypning-l","kind":"module","detail":"import collections","imports":["collections"],"verdicts":[{"engine":"lypning","kind":"module","detail":"import collections"},{"engine":"lypning-l","kind":"","detail":""},{"engine":"cpython","kind":"","detail":""}]}
1267650600228229401496703205376
0
1267650600228229401496703205376
0
```

Expected to differ: the N both dispatcher arms ran. Must not differ: the
two numbers in `agree N/N` equal, `monotone violations 0`, both `--next`
chains (`set-order` is in `engines.ONLY_CPYTHON_REFUSALS` and skips the
sibling), one verdict per member of `engines.ENGINE_ORDER`, the same answer
from both dispatchers, exit 0.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| the dispatchers disagree on an entry | `dispatchers agree N/M …:  FAIL` and `<id>: python <verdict> rc=…, rust <verdict> rc=…` | conformance, exit 1 |
| one table escalates a kind the other does not | `route --next` differs from `engines.chain_after_refusal` | pytest: `tests/test_routing.py::test_both_dispatchers_read_the_same_escalation_table` |
| a route below the routing binary | a variant named that is smaller than `route::SELF` | pytest: `tests/test_routing.py::test_the_larger_variant_knows_its_own_name_and_the_floor_rule_holds` |

**PINNED BY.**

```
tests/test_routing.py::test_both_dispatchers_walk_the_same_chain_after_a_runtime_refusal
tests/test_routing.py::test_a_same_caps_sibling_is_not_in_the_chain_at_all
tests/test_conformance.py::test_the_rust_dispatcher_is_an_arm_and_is_held_to_the_python_one
tests/test_engines.py::test_chain_after_refusal_walks_siblings_that_could_run_then_cpython
tests/test_embed.py::test_fall_onward_matches_the_dispatchers_rule
```

## 5. C5 — Routing grades

**STATEMENT.** A route is graded, not trusted. UNSAFE — the engine that
answered mismatched — must be 0 and fails the run; WASTED and LATE are a
budget, not a gate. The `accuracy` line is a census, not a cost model: it
weights LATE and WASTED equally, and they cost differently. The dated
source for the asymmetry is `CHANGELOG.md`'s entry for #42 (2026-09-04: a
LATE cost a CPython spawn, 12.0 ms there; a WASTED cost a parse, 1.21 ms
there, because `lypning run` is the dispatcher and the process is already
running). This run measured neither.

**CODE HOME.** `routing.IDEAL`, `WASTED`, `LATE`, `UNSAFE`, `NO_ENGINE`;
`routing.score_route`; `routing.RoutingReport.ok` (UNSAFE == 0);
`routing.render` (the `accuracy` line); `routing._matched_by_failing` (the
syntax-only rule for a program both sides fail alike);
`engines.Route.__str__` (a clean route prints the engine name alone; a
refusal-derived one `<engine>\t<kind>: <detail>`); `main.rs:route_cmd`
(`--json`, `--spectrum`, `--next`); `engines.VARIANT_CAPS` (pinned to
`route --spectrum`). The routing block is in §C3's EXPECTED. The fixture
table is `tests/verification/route-fixtures.json` (21 rows); these rows
carry the argument, `\t` for a tab:

| program | `lypning route -c` prints | note |
|---|---|---|
| `print(1)` | `lypning` | a clean route: the name alone |
| `f = lambda x: x + 1; print(f(1))` | `lypning` | `lambda` is in the subset |
| `import collections; print(collections.Counter("aab"))` | `lypning-l\tmodule: import collections` | the first rung refuses statically; the larger serves it |
| `import collections, re` | `cpython\tmodule: import collections` | the first blocker is named; `re` is what sends it past `lypning-l` |
| `class A: pass` | `cpython\tclass: class definition` | `route.rs:ONLY_CPYTHON_KINDS` — no Rust variant, at any size |
| `match 1:` / `    case 1: pass` | `cpython\tsyntax: line 1: invalid syntax: unexpected a literal` | `lypning -c` on it exits 1, not 90 |
| `print(2**100)` | `lypning` | a runtime `bigint`: the route is clean and the variant refuses at the value (§C11) |
| `print(getattr(print, "__name__"))` | `lypning` | a runtime `builtin: getattr` — the case `lypning routes` exists for |
| `print({3, 1, 2})` | `lypning` | a runtime `set-order`, in `engines.ONLY_CPYTHON_REFUSALS`: the chain skips `lypning-l` |
| `import sys; sys.exit(90)` | `lypning` | exit 90 without the line is the program's own |

**CHECK.**

```bash
lypning route -c 'print(1)'; echo $?
lypning route -c 'import collections; print(collections.Counter("aab"))' | cat -A
lypning route -c 'print(getattr(print, "__name__"))'; lypning -c 'print(getattr(print, "__name__"))'; echo $?
lypning route --spectrum
```

**EXPECTED** — `lypning route · 2026-09-04 · 437056c · 3688 loaded`:

```
lypning
0
lypning-l^Imodule: import collections$
lypning
lypning: unsupported: builtin: getattr
90
{"self":"lypning","self_caps":[],"spectrum":[{"name":"lypning","caps":[]},{"name":"lypning-l","caps":["cap-collections","cap-pathlib"]}],"caps":[{"cap":"cap-collections","modules":["collections"],"kinds":[]},{"cap":"cap-pathlib","modules":["pathlib"],"kinds":[]}]}
```

Expected to differ: the `spectrum` table whenever a variant gains a
`cap-*`; in §C3's routing block, every count. Must not differ: the fixture
rows, `self` naming the binary that answered, `UNSAFE 0`.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| a route whose answering engine mismatched | `  UNSAFE <id>: predicted <engine>, ideal <engine> — <detail>` and `routing errors N (must be 0)` | conformance, exit 1 |
| the router names an engine this copy of the table does not list | kind `route-unknown-engine`, routed to `cpython`, ungraded | pytest: `tests/test_engines.py::test_a_route_naming_an_engine_we_do_not_list_is_loud_not_silent` |
| LATE or WASTED move | the census moves; nothing turns red — read the two beside their cost | none |

**PINNED BY.**

```
tests/test_routing.py::test_a_refusal_is_wasted_and_a_wrong_answer_is_unsafe
tests/test_routing.py::test_one_unsafe_route_fails_the_whole_run_and_is_named
tests/test_routing.py::test_the_shared_failure_rule_applies_only_to_syntax_routes
tests/test_cli.py::test_route_names_a_tier_and_says_why
tests/test_verification.py::test_every_route_fixture_routes_as_the_table_says
```

## 6. C6 — The byte budget

**STATEMENT.** Cold cost is a step function in device blocks (invariant 6),
so every Rust variant has a block budget and `lypning gate` fails a build
that crosses it. The core is frozen at its budget; every new capability
goes to the larger variant (invariant 9).

**CODE HOME.** The constants, each written once:

| constant | value | enforced by |
|---|---|---|
| the device block | 131,072 B (`gate.DEVICE_BLOCK`; `build.CHEERPX_BLOCK` is the same number for the build table) | `gate.device_blocks`, rounding up |
| the block budgets | lypning 8, lypning-l 32 blocks (`gate.VARIANT_BLOCK_BUDGET`) | `gate._size_check` |
| shared objects | 0 (`gate.MAX_SHARED_OBJECTS`) — a precondition, not a budget | `gate._needed` |
| file opens on `-c 'pass'` | 3 (`gate.MAX_OPENS`) | `gate.file_opens`, only where `strace` runs |
| the oracle's byte budget | 700,000 B (`gate.MAX_BYTES`) — only when `lypning-mp` is the binary named | `gate._size_check` |
| CPython's cold anchor | 8573 ms (`gate.CPYTHON_COLD_MS`) — measured upstream, never here | `gate.project_cold_ms`, labelled an estimate |

**CHECK.**

```bash
lypning gate; echo $?
lypning gate ~/.lypning/bin/lypning-l | grep -E '^  ok   size|^PASS|^FAIL'; echo $?
lypning gate /no/such/binary; echo $?
```

**EXPECTED** — `lypning gate · 2026-09-04 · 437056c · 3688 loaded`:

```
gate  /private/tmp/lyp-b1/bin/lypning

  ok   target             lypning                want lypning-mp
  ok   runs -c 'pass'     exit 0                 want exit 0
  --   statically linked  unknown                want static
  --   shared objects     unmeasured             want <= 0
  ok   size               7 blocks               want <= 8 blocks
  ok   device blocks      7                      
  --   file opens         unmeasured             want <= 3
       target: lypning-mp is not built; gated lypning instead — these numbers are not lypning-mp's
       statically linked: unmeasured: unknown: no file(1) and no readelf
       shared objects: unmeasured: no readelf and no ldd — zero here would be an artefact of the toolchain, not of the build
       size: 818,080 B = 7 device blocks of 131072; budget 8 (not lypning-mp's 700,000 B)
       device blocks: 131,072 B each
       file opens: unmeasured: strace not available

PASS  (3 of 7 checks unmeasured)
0
  ok   size               7 blocks               want <= 32 blocks
PASS  (3 of 6 checks unmeasured)
0
lypning: gate: no such binary: /no/such/binary
2
```

A `--` row is a check nobody took — no `strace`, `readelf` or `file(1)` on
the host — and it is not a pass: the verdict carries `(3 of 7 checks
unmeasured)` and never prints a zero for it; CI has `strace`. Expected to
differ: the byte and block counts while under budget, which rows are
unmeasured, the `target` row (absent once the oracle is built and named).
Must not differ: `PASS`, the two `want <=` budgets
(`gate.VARIANT_BLOCK_BUDGET`), exit 0; exit 2 for a path that is not a file.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| a `NEEDED` entry — the binary links a shared object | `FAIL shared objects     1                      want <= 0`, the object on the note line | gate, exit 1 |
| opens rise past the budget | `FAIL file opens         N                      want <= 3`, the first paths on the note line | gate, where `strace` runs |
| a block boundary crossed (`gate.VARIANT_BLOCK_BUDGET`) | `FAIL size               9 blocks               want <= 8 blocks`; `build --rust` prints the block count and does not fail on it | gate, exit 1 |

**PINNED BY.**

```
tests/test_gate.py::test_device_blocks_rounds_up
tests/test_gate.py::test_the_rust_core_is_measured_against_its_own_budget
tests/test_gate.py::test_cold_cost_scales_on_opens_when_opens_dominate
```

## 7. C7 — doctor

**STATEMENT.** `lypning doctor` ends at `0 FAIL` and exits 0; any FAIL
exits 1. WARN and NOTE are states, not failures: an absent oracle is WARN,
an unwired harness is NOTE, a runner without `strace` is WARN.

**CODE HOME.** `cli._doctor_checks` (every row, in this order);
`cli.cmd_doctor` (exit 1 on any FAIL); `build.check_refusal_contract` (the
`refusal contract` row per built variant, each with its own name);
`embed.check_refusal_contract` (`library refusal`);
`engines.library_binary_drift` over `engines.DRIFT_PROBES` (`core/library
agreement`: `None` is a NOTE, a hole; `[]` is OK; a list is FAIL);
`cli._check_cli_collision` (`lypning on PATH`); `cli._recent_capture_note`
(the `harness note` WARN, from the log's notes of the last 7 days).

**CHECK.**

```bash
lypning doctor; echo $?
lypning doctor --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["ok"], [c["name"] for c in d["checks"] if c["level"]=="FAIL"])'
```

**EXPECTED** — `lypning doctor · 2026-09-04 · 437056c · 3688 loaded`:

```
OK   toolchain                     cargo/rustc/cc/make present
WARN strace                        absent — `lypning gate` cannot count file opens, and reports them unmeasured rather than zero
OK   lypning core                  /private/tmp/lyp-b1/bin/lypning (818,080 B, 7 blocks)
OK   lypning-l                     /private/tmp/lyp-b1/bin/lypning-l (867,744 B, 7 blocks)
WARN lypning-mp                    not built — the ORACLE, not a tier: nothing routes to it. `lypning build --micropython` needs a network; `lypning oracle` reads the recorded divergences either way
OK   cpython                       /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/bin/python3.14
OK   refusal contract              exit 90, one line on stderr, clean stdout
OK   refusal contract (lypning-l)  exit 90, one line on stderr, clean stdout
OK   library refusal               /tmp/lyp-b1/lib/liblypning.dylib — unsupported status, clean stdout, one line, falls onward
WARN lypning on PATH               not on PATH — pip put the console script in /opt/homebrew/bin; add it to $PATH
OK   capture hooks                 wired in project scope
WARN shim                          not installed — `lypning shim install`
OK   log                           /tmp/lyp-b1/invocations.jsonl (not created yet)
OK   harness claude                wired in project scope
NOTE harness opencode              not wired — `lypning install --harness opencode`
NOTE harness openhands             not wired — `lypning install --harness openhands`
OK   corpus                        3688 programs from /Users/user/dev/lypning-docs-b1/src/lypning/assets/corpus/corpus.jsonl
OK   core/library agreement        7 frontier probes answer alike in both artifacts

18 check(s), 0 FAIL, 4 WARN
0
True []
```

Expected to differ: paths; the WARN rows, which describe the host and the
install (`strace` present, the shim first on PATH, the oracle built: each
reads OK); the corpus count. With no library built the two library rows are
NOTEs: `liblypning is not built — …` and `not compared — the C ABI or the
Rust core is not built, so the frontier probes had one artifact to ask`.
Must not differ: `0 FAIL`, exit 0, `OK refusal contract` for every built
variant, `OK library refusal` when the library is built.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| the core, CPython, or a variant's contract | `FAIL lypning core  not built — …`; `FAIL cpython  no real CPython found — the last tier is missing`; `FAIL refusal contract (lypning-l)  <why>` | doctor, exit 1 |
| `$LYPNING_LIB` names a path that is not there | `FAIL library refusal  $LYPNING_LIB points at …, which does not exist — …` | doctor |
| the library answers from an older tree | `FAIL core/library agreement  N of 7 probes disagree — the C ABI and the binary were built from different trees; run `lypning build --rust --lib`. First: …` — only where a probe in `engines.DRIFT_PROBES` differs; a drift outside the seven is caught by `tests/test_embed.py::test_library_agrees_with_the_binary` | doctor; pytest |

**PINNED BY.** No test drives `lypning doctor` end to end; its predicates
are pinned here and by §C1's tests.

```
tests/test_cli.py::test_status_reports_an_unbuilt_engine_as_not_built
tests/test_embed.py::test_library_agrees_with_the_binary
tests/test_embed.py::test_a_named_library_that_is_missing_is_a_bad_override_not_an_absence
tests/test_embed.py::test_engines_reports_an_unusable_library_rather_than_failing_runs
```

## 8. C8 — The net

**STATEMENT.** Invariant 4: the corpus rewrites repositories; run it behind
the net. Every entry gets its own temp cwd, a separate one per engine;
entries naming an absolute path are skipped rather than run; entries that
would run one of lypning's own batteries are skipped (the fork bomb); the
run is bracketed by a `git status` snapshot that restores and reports
anything that changed anyway, and damage fails the run. The net is not a
sandbox: a write outside the repository is not undone.

**CODE HOME.** `conformance._Sandbox` (a temp cwd per arm);
`conformance.absolute_paths`; `conformance.spawns_a_battery` (the skip for
`lypning conformance|bench|corpus-time|perf` and for a program that imports
lypning and drives a battery); `conformance._snapshot`,
`conformance.close_net`, `conformance._restore` (the bracket);
`conformance.Report.ok` (damage fails); `bench.skip_reason` (the same skips
in the benchmark).

**CHECK.**

```bash
git status --porcelain | wc -l | tr -d ' '
lypning conformance --limit 200 > /dev/null; echo $?
git status --porcelain | wc -l | tr -d ' '
lypning conformance --mixture both | grep '^skipped'
```

**EXPECTED** — `lypning conformance · 2026-09-04 · 437056c · 3688 loaded`:

```
0
0
0
skipped 1184 not run: absolute path outside the sandbox (949), imports lypning and drives a battery (208), would spawn a lypning battery (conformance/bench/corpus-time/perf) (27)
```

Expected to differ: the skip counts. Must not differ: `git status` the same
before and after, no `!!` block, exit 0. Three rules of use: run a battery
only in a worktree with its own state dir (`git worktree add
../lypning-<topic> -b <topic>`, `export LYPNING_HOME=/tmp/lypning-<topic>`);
never in a tree anyone is editing — the net restores changed tracked files
whoever changed them; after a battery that crashed mid-way, run `git status`
yourself, because the restore runs only at the end of a run that got there.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| a program escaped its cwd and touched the tree | `!! N repository file(s) changed by corpus programs and have been restored:` … `the run is a failure regardless of its verdicts` | conformance, exit 1; `git status` |
| a battery inside the battery | load, and orphaned `from lypning import conformance` processes — the skip is the only guard | pytest: `tests/test_conformance.py::test_a_program_that_runs_the_battery_is_skipped_not_replayed` |
| a crash before the restore | a dirty `git status --porcelain` | `git status`, by hand |

**PINNED BY.**

```
tests/test_conformance.py::test_the_git_net_reports_and_restores_what_a_program_dirtied
tests/test_conformance.py::test_the_net_sees_a_file_created_inside_an_untracked_directory
tests/test_conformance.py::test_absolute_paths_are_what_keeps_a_program_out_of_the_battery
tests/test_bench.py::test_a_battery_running_program_is_taken_out_of_the_timing
```

## 9. C9 — Hooks

**STATEMENT.** Invariant 5: every hook prints
`{"continue":true,"suppressOutput":true}` and exits 0 on every path,
including its own failures — malformed event, unwritable log, missing
package, uninstalled engine. There is no `permissionDecision` field (the
reasoning is `CLAUDE.md`'s). The two session-start entry points add context
beside `continue: true`; `opencode-context` prints plain text.

**CODE HOME.** `capture.OK_RESPONSE`, `capture._respond`;
`capture.read_event` (never raises); `capture.append_record` (the log, then
the per-uid tmp fallback `capture._fallback_log`, then silence); the six
entry points in `cli._HOOK_EVENTS` — `capture.hook_pre_tool_use`,
`hook_stop`, `hook_openhands_post_tool_use`, `hook_openhands_session_start`,
`hook_openhands_session_end`, `hook_opencode_context`; the three scripts
under `src/lypning/assets/claude/hooks/` — `lypning-capture.sh`,
`lypning-harvest.sh`, `lypning-session-start.sh` (fork-free until the shell
screen matches); `capture.record_command` (the raw record: `kind`, `ts`,
`session`, `cwd`, `tool`, `command`, `description`, `transcript`, `host`,
`tool_use_id`; OpenHands adds `exit_code`, opencode `run`).

**CHECK.** The ten fixtures are `tests/verification/hook-fixtures.json`;
these are the four failures, the happy path, and the two other hosts.

```bash
E='{"tool_name":"Bash","tool_input":{"command":"python3 -c \"print(1)\""},"session_id":"s","cwd":"/tmp","tool_use_id":"t"}'
echo 'not json at all' | lypning hook pre-tool-use; echo $?
echo "$E" | LYPNING_LOG=/dev/null/invocations.jsonl lypning hook pre-tool-use; echo $?
echo "$E" | env -i PATH=/usr/bin:/bin HOME=/tmp sh src/lypning/assets/claude/hooks/lypning-capture.sh; echo $?
echo "$E" | LYPNING_HOME=/tmp/lypning-empty lypning hook pre-tool-use; echo $?
echo "$E" | LYPNING_LOG=/tmp/hook.jsonl lypning hook pre-tool-use; echo $?; tail -1 /tmp/hook.jsonl
echo '{"tool_name":"terminal","tool_input":{"command":"python3 -c 1"},"tool_response":{"exit_code":0},"session_id":"oh"}' | LYPNING_LOG=/tmp/hook.jsonl lypning hook openhands-post-tool-use; tail -1 /tmp/hook.jsonl
echo '{}' | lypning hook opencode-context | head -1
grep -rn '"permissionDecision"' src/lypning/ | wc -l | tr -d ' '
```

**EXPECTED** — `lypning hook · 2026-09-04 · 437056c · 3688 loaded`:

```
{"continue":true,"suppressOutput":true}
0
{"continue":true,"suppressOutput":true}
0
{"continue":true,"suppressOutput":true}
0
{"continue":true,"suppressOutput":true}
0
{"continue":true,"suppressOutput":true}
0
{"kind":"bash_command","ts":"2026-09-04T21:36:53.935Z","session":"s","cwd":"/tmp","tool":"Bash","command":"python3 -c \"print(1)\"","description":null,"transcript":null,"host":"claude","tool_use_id":"t"}
{"continue":true,"suppressOutput":true}
{"kind":"bash_command","ts":"2026-09-04T21:36:53.968Z","session":"oh","cwd":"/Users/user/dev/lypning-docs-b1","tool":"terminal","command":"python3 -c 1","description":null,"transcript":null,"host":"openhands","exit_code":0}
Python one-liners in this session should be typed as `lypning -c '<program>'`
0
```

The third command also writes one line to stderr (`…python3: No module
named lypning`), where a diagnostic belongs. Expected to differ: `ts`; the
OpenHands record's `cwd` (the shell's when the event carries no
`working_dir`); the stderr text. Must not differ: stdout byte-exact and exit
0 on every line, the record keys, `"host":"openhands"` with `"exit_code"`,
zero hits for the quoted key.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| an entry point returns non-zero | OpenHands reads an exit code of two as *block the agent's tool call* | pytest: `tests/test_capture.py::test_no_hook_entry_point_can_return_two` |
| a `decision` or `permissionDecision` key | the hook would grant or deny permission | pytest: `tests/test_harness_openhands.py::test_no_hook_claims_a_permission_decision` |
| the scripts in the tree drift from the ones shipped | a session captures with one and installs the other | pytest: `tests/test_capture.py::test_the_committed_hooks_match_the_ones_the_installer_ships` |

**PINNED BY.**

```
tests/test_capture.py::test_hook_answers_the_protocol_and_exits_zero
tests/test_capture.py::test_an_unwritable_log_does_not_reach_the_tool_call
tests/test_capture.py::test_every_hook_can_reach_the_package_from_a_source_checkout
tests/test_capture.py::test_the_openhands_hook_records_the_exit_code_it_was_given
tests/test_verification.py::test_every_hook_fixture_answers_the_protocol_line
```

## 10. C10 — Install, uninstall, shim

**STATEMENT.** Invariant 7: nothing we write may cost a user something they
had. `.claude/settings.json` is merged, never overwritten: append only,
unrelated keys and hooks and their order preserved, backed up once to
`settings.json.lypning-backup` and never re-backed-up over. `--dry-run` is
real — it opens files, writes none, and prints the unified diff. Uninstall
is the exact inverse and never deletes the capture log. The shim refuses to
overwrite a `python3` it did not write unless forced, and forcing moves the
original aside.

**CODE HOME.** `install.plan_install` (reads, never writes);
`install.merge_hooks`, `install.strip_hooks`;
`install.SETTINGS_BACKUP_SUFFIX` (taken only when no backup exists);
`install.apply`, `install.uninstall`; `shim.install`, `shim.uninstall`,
`shim.status`, `shim.path_problem`, `shim.render`; `harness.opencode.plan`
(one file, `.opencode/plugin/lypning.js`; a foreign file at that name is a
`skip`); `harness.openhands.plan` and `harness.openhands.FILES` (three
files under `.openhands/plugins/lypning/`; never `.openhands/hooks.json`);
`cli.cmd_install` (exit 1 when an action FAILED; exit 2 for an unknown
harness, `unknown harness 'nope' (known: claude, opencode, openhands, all)`).

**CHECK.** In a throwaway project whose `settings.json` already carries a
foreign hook and an unrelated key.

```bash
P=$(mktemp -d) && git -C "$P" init -q && mkdir "$P/.claude"
printf '{"permissions":{"allow":["Bash(ls:*)"]},"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"echo mine"}]}]}}\n' > "$P/.claude/settings.json"
before=$(find "$P/.claude" -type f | sort | xargs shasum | shasum)
lypning install --dry-run --project "$P" | grep -E '^~|changes'; echo $?
[ "$before" = "$(find "$P/.claude" -type f | sort | xargs shasum | shasum)" ] && echo unchanged
lypning install --project "$P" | grep -E '^b|^~'; echo $?
grep -c 'echo mine' "$P/.claude/settings.json"; grep -c 'Bash(ls:' "$P/.claude/settings.json"
lypning install --project "$P" | grep settings.json; echo $?
lypning uninstall --project "$P" | grep -E 'settings.json|NOT deleted'; echo $?
grep -c lypning "$P/.claude/settings.json"; ls "$P/.claude"
lypning shim status | grep '^PATH'
```

**EXPECTED** — `lypning install · 2026-09-04 · 437056c · 3688 loaded`:

```
~ merge   /private/var/folders/ms/pq0b9hwj5fvfxb__l201gtk80000gn/T/tmp.ZOEb1Dod0l/.claude/settings.json  — add 3 hook entries
9 changes, 0 already in place, 1 warning (the `.` line above)
0
unchanged
b backup  /private/var/folders/ms/pq0b9hwj5fvfxb__l201gtk80000gn/T/tmp.ZOEb1Dod0l/.claude/settings.json.lypning-backup  — copy of the current settings.json
~ merge   /private/var/folders/ms/pq0b9hwj5fvfxb__l201gtk80000gn/T/tmp.ZOEb1Dod0l/.claude/settings.json  — added 3 entries
0
1
1
. skip    /private/var/folders/ms/pq0b9hwj5fvfxb__l201gtk80000gn/T/tmp.ZOEb1Dod0l/.claude/settings.json  — all 3 hook entries already present
0
- remove  /private/var/folders/ms/pq0b9hwj5fvfxb__l201gtk80000gn/T/tmp.ZOEb1Dod0l/.claude/settings.json  — removed 3 hook entries
. skip    /tmp/lyp-b1-inst/bin  — note: the log at /tmp/lyp-b1-inst/invocations.jsonl was NOT deleted.
0
0
hooks
settings.json
settings.json.lypning-backup
skills
PATH        : WARNING — /tmp/lyp-b1-inst/bin is NOT on PATH — the shim will never run
```

Expected to differ: paths, and the PATH line, which describes the shell.
Must not differ: the checksum unchanged after `--dry-run`; the foreign hook
and the unrelated key counted once before and after; `b backup` on the
first install only, the backup byte-identical after the second; `all 3 hook
entries already present` on the second; after uninstall zero `lypning` in
`settings.json`, the backup and the two emptied directories left in place,
the `NOT deleted` note. The shim's three states, by hand —
installed-but-shadowed and not-installed share a symptom, an empty log, and
the middle line tells them apart:

```
PATH        : WARNING — <bin> is NOT on PATH — the shim will never run
PATH        : WARNING — <bin> is on PATH but BEHIND /usr/bin/python3 — that interpreter wins and the shim never runs
PATH        : ok — <bin> is on PATH ahead of any real interpreter
```

A foreign `python3` in the bin dir is refused (`lypning: REFUSING:
<bin>/python3 exists and is not a lypning shim.`, exit 1); `shim install
--force` prints `backed up <bin>/python3 -> <bin>/python3.lypning-backup`;
`shim uninstall` prints `restored <bin>/python3 (from .lypning-backup)`.
`--harness openhands` writes exactly
`.openhands/plugins/lypning/.claude-plugin/plugin.json`, `hooks/hooks.json`
and `README.md`; `--harness opencode` writes exactly
`.opencode/plugin/lypning.js`. Two documented behaviours are not what the
code does, and the code is what this document states: a foreign
`lypning.js` is skipped with `NOT a lypning plugin — left alone; move it
aside yourself (--force moves only a foreign python3 shim, not this file)`
— issue #43; and `LYPNING_HARVEST=0` is inert under the opencode plugin —
issue #44.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| a merge that drops a foreign hook or key, or a second backup | the `echo mine` count reads 0; the backup differs after the second install | pytest: `tests/test_install.py::test_the_merge_preserves_unrelated_keys_and_hooks`, `tests/test_install.py::test_merge_hooks_is_idempotent` |
| a dry run that writes | the checksum differs | pytest: `tests/test_cli.py::test_harvest_dry_run_writes_nothing_under_the_state_dir` |
| a foreign `python3` overwritten, or the log deleted | no `REFUSING:` line; no `NOT deleted` note | pytest: `tests/test_shim.py::test_refuses_to_clobber_a_foreign_python_without_force`, `tests/test_shim.py::test_install_uninstall_round_trip` |

**PINNED BY.**

```
tests/test_install.py::test_install_uninstall_round_trip
tests/test_install.py::test_uninstall_removes_only_our_entries
tests/test_shim.py::test_force_moves_the_foreign_file_aside_and_uninstall_puts_it_back
tests/test_shim.py::test_path_problem_is_loud_when_a_real_interpreter_shadows_the_shim
tests/test_harness_opencode.py::test_a_foreign_lypning_js_is_refused_without_force
tests/test_harness_openhands.py::test_no_user_hooks_json_is_ever_written
```

## 11. C11 — The routes ledger

**STATEMENT.** `lypning routes` is write-only with respect to routing:
nothing that routes reads it. It is written by `engines.dispatch` only, on
one condition — a clean static route followed by an exit-90 refusal from
the engine the route named — so the Rust dispatcher's refusals are never in
it and every count is a floor. An empty store is a hole, never a zero.

**CODE HOME.** `routes.note` (the writer; `engines.dispatch` is its one
caller, and `conformance._run_entry` passes `ledger=False`); `routes.ENV`
(`LYPNING_ROUTES=0`; `LYPNING_CAPTURE=0` covers it too); `routes.load`,
`routes.load_all` (line 1 of each file names the engine, its `cap-*` set
and its binary's stamp; a header that does not describe the engine as
built discards the whole file); `routes.Store` (`present`, `unreadable`,
`truncated`, `stale` are four facts); `routes.render`; the store is
`$LYPNING_HOME/routes/<engine>.jsonl` (`paths.routes_dir`).

**CHECK.**

```bash
lypning routes | head -1
lypning run -c 'print(2**100)'; echo $?    # the Python dispatcher: a clean route, then bigint at runtime
lypning routes | grep -E '^  (engine|lypning|kind|bigint)'
lypning conformance --limit 50 --json | python3 -c 'import json,sys; d=json.load(sys.stdin); d.pop("seconds"); print(json.dumps(d, sort_keys=True))' | shasum
lypning routes --clear
LYPNING_ROUTES=0 lypning conformance --limit 50 --json | python3 -c 'import json,sys; d=json.load(sys.stdin); d.pop("seconds"); print(json.dumps(d, sort_keys=True))' | shasum
```

**EXPECTED** — `lypning routes · 2026-09-04 · 437056c · 3688 loaded`:

```
routes — no routes learned yet.
1267650600228229401496703205376
0
  engine         records  programs   invocations  store
  lypning:             1         1             1  /tmp/lyp-b1/routes/lypning.jsonl
  lypning-l:    no routes learned yet  (/tmp/lyp-b1/routes/lypning-l.jsonl)
  kind                    programs  invocations  note
  bigint                         1            1
570abca302598b5c731292be19ad637f554a0899  -
routes: cleared 1 store(s): lypning
570abca302598b5c731292be19ad637f554a0899  -
```

Expected to differ: the digest (over the first fifty entries' verdicts);
the store path. Must not differ: the two digests equal to each other — with
the store populated, and with the writer off and the store gone, a graded
battery is the same bytes (`seconds` removed); the `bigint` record after one
run through the Python dispatcher; nothing after `~/.lypning/bin/lypning
run -c`, the Rust dispatcher. The ways of not having a store render as
facts, never as a zero: absent, `no file yet`; unreadable, named as such
(`--compact` skips it); truncated, `[TRUNCATED at N — more on disk]`
(`routes.MAX_RECORDS`); a header for a rebuilt binary, `discarded as
stale`. Every rendering ends by saying the ledger under-counts.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| something on the routing path reads the store | the two digests differ | pytest: `tests/test_routes.py::test_a_populated_store_cannot_move_a_measurement` |
| a second writer, or a write on a static route | a record after a route to `cpython`, or after exit 90 without the line | pytest: `tests/test_routes.py::test_a_static_route_to_cpython_writes_nothing`, `tests/test_routes.py::test_exit_90_without_the_contract_line_writes_nothing` |
| a hole rendered as a zero | an unreadable store reads like an empty one | pytest: `tests/test_routes.py::test_an_unreadable_store_is_not_an_empty_one` |

**PINNED BY.**

```
tests/test_routes.py::test_a_populated_store_cannot_move_a_measurement
tests/test_routes.py::test_the_dispatcher_writes_on_a_clean_route_then_a_runtime_refusal
tests/test_routes.py::test_lypning_routes_0_disables_the_writer
tests/test_routes.py::test_the_documented_capture_opt_out_covers_this_feed
tests/test_routes.py::test_an_empty_store_is_a_hole_not_a_zero
tests/test_routes.py::test_a_rebuilt_binary_discards_the_file
```

## 12. C12 — The oracle-absent path

**STATEMENT.** `lypning-mp` is an oracle — measured, never routed to
(invariant 9) — and it is absent by default: it needs a 32-bit toolchain and
a network, so every path that touches it degrades to "not built" and carries
on: a status line, a hole in a table (never a zero), an unmeasured arm with
a note. Test that path by moving the binary aside, not by reasoning about
it. On the run-of-record host the binary was never built, so every block
below is the absent path taken for real; where it is built, `mv
~/.lypning/bin/lypning-mp ~/.lypning/bin/lypning-mp.aside` first.

**CODE HOME.** `engines.ORACLES`; `engines.find_micropython` and
`engines.env_var_for` (`LYPNING_MP_BIN`); `cli._render_status` (the
`oracles (measured, never routed to)` section); `cli._doctor_checks` (the
WARN row); `conformance.run` (`unbuilt`, the `note:` line); `bench` (an
absent arm is absent, never a zero row); `gate.gate` (with no binary named
it gates `lypning` and says so); `oracle.load`, `oracle.render`,
`oracle.ledger_path` (`.github/known-mismatches.json`, keys `_` and
`accepted`; a wheel has no catalogue and says so).

**CHECK.**

```bash
lypning status | sed -n '/^oracles/,/^$/p'
lypning doctor | grep lypning-mp; echo $?
lypning conformance --engine lypning-mp --limit 5 | grep '^note'
lypning gate | grep target
lypning oracle | sed -n '1p;$p'; echo $?
lypning oracle --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["engine"], d["built"], d["divergences"], len(d["families"]))'
```

**EXPECTED** — `lypning oracle · 2026-09-04 · 437056c · 3688 loaded`:

```
oracles  (measured, never routed to)
  lypning-mp: not built  — `lypning build --micropython` (needs a network); `lypning oracle` reads the recorded divergences either way

WARN lypning-mp                    not built — the ORACLE, not a tier: nothing routes to it. `lypning build --micropython` needs a network; `lypning oracle` reads the recorded divergences either way
0
note: lypning-mp is not built — that arm was not measured
  ok   target             lypning                want lypning-mp
       target: lypning-mp is not built; gated lypning instead — these numbers are not lypning-mp's
oracle — lypning-mp against CPython: 79 recorded divergences in 34 families
ledger: /Users/user/dev/lypning-docs-b1/.github/known-mismatches.json (maintained by identity; a divergence that stops reproducing reddens CI)
0
lypning-mp False 79 34
```

Expected to differ: the divergence and family counts, which the CI job
that runs the oracle maintains by identity; the ledger path. Must not
differ: `not built` on every line and never a `0` in its place, `built`
false, exit 0 everywhere, `lypning oracle` rendering without the binary.
From a wheel the catalogue is not a package asset: `lypning oracle` prints
`oracle: no catalogue — <path> is unreadable.` and calls it a hole.
Observed on 2026-09-04 and not filed: `lypning oracle --json` from the same
wheel answers `divergences` 0 and an empty `families` list for that
unreadable catalogue — a hole rendered as a zero. Every expectation that
needs the oracle built (`bench --micropython`, `conformance --engine
lypning-mp` with the arm present, `gate` against `gate.MAX_BYTES`) was not
captured on this host and is not written here; the `micropython-conformance`
job in `.github/workflows/ci.yml` is where those run.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| a zero where a hole belongs | a bench row at 0, a table cell at 0, a JSON count of 0 for an arm nobody ran | pytest: `tests/test_bench.py::test_an_unbuilt_arm_is_absent_rather_than_zero`, `tests/test_routing.py::test_a_tier_that_was_not_measured_is_a_hole_not_a_failure` |
| a Rust variant gated against the oracle's bytes | `size … want <= 700,000 B` on a `lypning` row | pytest: `tests/test_gate.py::test_the_rust_core_is_measured_against_its_own_budget` |
| the catalogue rots | a divergence that stops reproducing, or one it does not name | CI: the `micropython-conformance` job, `.github/scripts/known-mismatches.py` |

**PINNED BY.**

```
tests/test_bench.py::test_an_unbuilt_arm_is_absent_rather_than_zero
tests/test_conformance.py::test_an_unbuilt_engine_is_an_absent_arm_not_a_failed_one
tests/test_cli.py::test_bench_micropython_exits_2_when_the_control_is_absent
tests/test_gate_meaning.py::test_a_mismatch_the_ledger_does_not_name_is_a_regression
tests/test_gate_meaning.py::test_a_ledger_entry_that_stopped_reproducing_is_also_red
```

## 13. C13 — The wheel shape

**STATEMENT.** Two shapes must both keep working. In a wheel `assets/` is
read-only, the crate is copied into `~/.lypning/build` and built there, and
the shell hook scripts may not have shipped — in which case the installer
wires the `lypning hook …` CLI entry points instead, which do the same work
one exec later. The wheel path is the one a `pip` user hits and the one
nobody tests by accident: test it on purpose.

**CODE HOME.** `paths.build_dir` (the asset tree in a checkout,
`$LYPNING_HOME/build` from a wheel); `build._rust_workdir`,
`build._sync_tree`; `install._hook_command`, `install._available_scripts`,
`install.hook_entries` (the `.sh` when it shipped, `lypning hook <event>`
when it did not); `pyproject.toml` `[tool.setuptools.package-data]` (what
ships; `assets/claude/hooks/*.sh` is on the list).

**CHECK.**

```bash
python3 -m venv /tmp/lypning-venv && /tmp/lypning-venv/bin/pip install -q 'setuptools>=77'
/tmp/lypning-venv/bin/pip install -q --no-build-isolation .; echo $?
cd /tmp && export LYPNING_HOME=/tmp/lypning-wheel
/tmp/lypning-venv/bin/lypning build --rust | grep -E '^lypning'; echo $?
/tmp/lypning-venv/bin/lypning status | sed -n '/^library/,/^$/p'; ls /tmp/lypning-wheel/build
/tmp/lypning-venv/bin/lypning lib; echo $?
/tmp/lypning-venv/bin/lypning install --dry-run --no-shim --project /tmp/lypning-wheel-proj | grep -E '^[+.~] (write|merge|skip)|"command"'
```

**EXPECTED** — `venv lypning build · 2026-09-04 · 437056c · 3688 loaded`:

```
0
lypning    host    818080  7       <s>   ok  (rustup not found: built for the host instead of x86_64-unknown-linux-musl)
lypning-l  host    867744  7       <s>   ok  (rustup not found: built for the host instead of x86_64-unknown-linux-musl)
0
library
  liblypning: not built  — `lypning build --lib`
  headers:    /private/tmp/lyp-b1-venv/lib/python3.14/site-packages/lypning/assets/include

rust
lypning: lib: the C ABI library is not built — run `lypning build --lib` (or point $LYPNING_LIB at a built library)
2
+ write   /private/tmp/lypning-wheel-proj/.claude/skills/lypning/MICROPYTHON.md  — new
+ write   /private/tmp/lypning-wheel-proj/.claude/skills/lypning/SKILL.md  — new
+ write   /private/tmp/lypning-wheel-proj/.claude/hooks/lypning-capture.sh  — new
+ write   /private/tmp/lypning-wheel-proj/.claude/hooks/lypning-harvest.sh  — new
+ write   /private/tmp/lypning-wheel-proj/.claude/hooks/lypning-session-start.sh  — new
~ merge   /private/tmp/lypning-wheel-proj/.claude/settings.json  — add 3 hook entries (creating the file)
+            "command": "sh \"$CLAUDE_PROJECT_DIR/.claude/hooks/lypning-session-start.sh\""
+            "command": "sh \"$CLAUDE_PROJECT_DIR/.claude/hooks/lypning-capture.sh\""
+            "command": "sh \"$CLAUDE_PROJECT_DIR/.claude/hooks/lypning-harvest.sh\""
```

The scripts ship, so a wheel's install writes them. With the three `.sh`
files removed from the venv's `site-packages/lypning/assets/claude/hooks/`
the same dry run printed, on the same day, `. skip <project>/.claude/hooks
— no hook scripts in <site-packages>/…/hooks — wiring the `lypning hook`
CLI entry points instead`, and the three entries became `lypning shim
install`, `lypning hook pre-tool-use` and `lypning hook stop`. Expected to
differ: paths, bytes, `secs`. Must not differ: `pip` exit 0, the build
`ok`, `lib` exit 2 with nothing on stdout when no library is built, the
crate under `$LYPNING_HOME/build/rust`, the CLI entry points when the
scripts are absent.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| an asset the crate needs did not ship | `lypning build --rust` fails from a wheel, or builds a binary a block larger than the checkout's | pytest: `tests/test_packaging.py::test_the_wheel_carries_the_crate_cargo_config` |
| a runtime dependency crept in | the package fails to import with nothing but the stdlib | pytest: `tests/test_packaging.py::test_zero_runtime_dependencies` |
| the wheel path lost a half of the crate | `cargo` cannot find `build.rs` or `.cargo/config.toml` | pytest: `tests/test_build.py::test_the_wheel_path_copies_both_halves_keeping_the_layout` |

**PINNED BY.**

```
tests/test_packaging.py::test_every_package_data_glob_matches_a_file
tests/test_packaging.py::test_the_wheel_carries_the_crate_cargo_config
tests/test_build.py::test_the_wheel_path_copies_both_halves_keeping_the_layout
tests/test_capture.py::test_the_committed_hooks_match_the_ones_the_installer_ships
```

## 14. C14 — The library

**STATEMENT.** The C ABI is the same interpreter reached in-process, and it
holds the refusal contract in library terms: status `LYPNING_UNSUPPORTED`,
exit code 90, empty stdout, the one line headed by its own name, and a
request to be routed onward — in-process it is the host that owns the
retry. The library is the largest variant, so its line begins `lypning-l:`.

**CODE HOME.** `embed.REFUSAL_LINE`; `embed.check_refusal_contract`;
`embed.Library.run`, `embed.Outcome`; the statuses `embed.OK`, `ERROR`,
`UNSUPPORTED`, `BUSY`, `PANIC` (0 to 4, `LYPNING_*` in `lypning.h`);
`embed.ABI_VERSION` and `LYPNING_ABI_VERSION` (1); `lypning_engine_self`
and `lypning_result_should_fall_onward` in `capi.rs` (true for UNSUPPORTED,
a BUSY that ran nothing, a PANIC before commit — never for OK or ERROR);
`conformance.LIBRARY_STEP_LIMIT` (the in-process stand-in for a deadline;
reaching it is UNSUPPORTED, never MISMATCH); `engines._CHDIR_LOCK` (the
library arm is serialised: it shares the process's cwd); `eval.rs:MAX_DEPTH`,
`eval.rs:MAX_EXPR_DEPTH` (the recursion guards, sized so a deep program is
a refusal on a host thread with a 1 MB stack rather than a segfault).

**CHECK.**

```bash
lypning lib --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(sorted(d)); print(d["abi"], d["cli_abi"], d["error"])'
python3 - <<'PY'
from lypning import embed
lib = embed.Library()
o = lib.run("import subprocess")
print(lib.engine_name(), o.status_name, o.exit_code, o.stdout, o.stderr, o.committed, o.fall_onward)
o = lib.run("import sys; sys.exit(90)"); print(o.status_name, o.exit_code, o.fall_onward)
o = lib.run("while True: pass", step_limit=1000); print(o.status_name, o.exit_code, o.kind, o.fall_onward)
PY
lypning conformance --engine library --limit 20 | grep -E '^library|^MISMATCH'
LYPNING_LIB=/no/such.so lypning lib --json; echo $?
```

**EXPECTED** — `lypning lib · 2026-09-04 · 437056c · 3688 loaded`:

```
['abi', 'built', 'bytes', 'cflags', 'cli_abi', 'error', 'gcc', 'header', 'include', 'libs', 'path', 'runtime', 'static', 'static_bytes']
1 1 None
lypning-l unsupported 90 b'' b'lypning-l: unsupported: module: import subprocess\n' False True
ok 90 False
unsupported 90 steps True
library         6           14         0     30.0%
MISMATCH 0 — ok
lypning: lib: $LYPNING_LIB points at /no/such.so, which does not exist — unset it or point it at a `lypning build --lib` library
2
```

Expected to differ: the library's counts and coverage. Must not differ:
`abi` equal to `cli_abi`; `engine_name` the last member of
`engines.SPECTRUM`; a refusal with status `unsupported`, exit 90, empty
stdout, the one line, `committed` false and `fall_onward` true;
`sys.exit(90)` as status `ok` with `fall_onward` false; the step limit as a
refusal of kind `steps` that falls onward; `MISMATCH 0` on the library arm;
exit 2 with nothing on stdout for a bad `$LYPNING_LIB` (and, per §C13, for
no library at all). BUSY — one thread inside a run asking for another —
cannot be provoked from Python: `ctypes` cannot re-enter `lypning_run`, and
two threads running two programs is allowed. It is pinned at the ABI level.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| the in-process contract, or the library mis-naming itself | `unsupported contract (in-process): BROKEN — <why>`; `BROKEN — the library calls itself 'lypning'; the library is the largest variant, 'lypning-l'` | build, exit 1; doctor `FAIL library refusal` |
| a refused run reports a commit, or does not ask to fall onward | `BROKEN — a refused run reported that it committed`; `… did not ask to be routed onward` | build; doctor |
| a deep program segfaults the host | the host process dies instead of a refusal | pytest: `tests/test_embed.py::test_deep_programs_stay_refusals_on_a_small_host_stack` |

**PINNED BY.**

```
tests/test_embed.py::test_refusal_contract_holds
tests/test_embed.py::test_sys_exit_is_the_programs_own_code
tests/test_embed.py::test_abi_and_runtime_versions
tests/test_embed.py::test_a_step_limit_bounds_a_program_that_will_not_stop
tests/test_embed.py::test_busy_and_a_clean_panic_are_routable
tests/test_embed.py::test_a_named_library_that_is_missing_is_a_bad_override_not_an_absence
```

## 15. Exit codes and timeouts

`cli.main` maps outcomes onto exit codes (invariant 8): `0` ok, `1` this
command failed, `2` usage or a named engine that is not one
(`engines.EngineError`), `90` an engine refusal passed through untouched,
`130` an interrupt; a closed pipe (`lypning corpus --list | head`) is `0`.
Any other exception prints `lypning: <Type>: <message>` and `lypning: set
LYPNING_DEBUG=1 to see the traceback`, exit 1. The binary itself
(`main.rs:run`) uses `0`/`1` as CPython does, `2` for `can't open file` and
`no program given`, `90` for a refusal (`cli: option -m` included), and
`127` for `cannot run <bin>` when the next rung's binary cannot be run.

| command | 0 | 1 | 2 | 90 | 127 | 130 |
|---|---|---|---|---|---|---|
| `lypning -c` / FILE / `-` (the binary) | ran | the program's own failure | no program, `can't open file` | a refusal (§C1), or the program's own `sys.exit(90)` | — | — |
| `lypning run -c` (either dispatcher) | the answering engine's 0 | the program's own | `run needs -c PROG, a FILE, or -` | passed through unchanged | Rust: `cannot run <bin>`; Python: `lypning: no engine available` | Python CLI: `lypning: interrupted` |
| `conformance` | MISMATCH 0, UNSAFE 0, dispatchers agree, no damage | otherwise | usage | — | — | interrupted |
| `gate` | PASS | FAIL | `no such binary: <path>` | — | — | — |
| `doctor` | `0 FAIL` | any FAIL | — | — | — | — |
| `build` | every requested artefact `ok`, or `--dry-run` | a build `FAILED`, the contract `BROKEN`, or `unavailable` without `--skip-unavailable` | — | — | — | — |
| `fuzz` | no counterexample | a counterexample; the seed is always printed (`seed N   (replay: lypning fuzz --seed N --iterations M)`) | engine or reference not built | — | — | — |
| `perf` | every case agreed | a checksum disagreement, a refusal, or a case that failed | `no such case` | — | — | — |
| `lib` | built | the library cannot be loaded (`error`) | not built, a bad `$LYPNING_LIB`, or no header — nothing on stdout | — | — | — |
| `install` | done | an action `FAILED` | unknown harness | — | — | — |
| `hook <event>` | always | — | — | — | — | — |
| `status`, `routes`, `oracle`, `corpus`, `harvest`, `bench`, `corpus-time`, `shim`, `pool` | done | a `Failure` (one line) | usage; `bench --micropython` with no control | — | — | interrupted |

Timeouts: 30 s per program is the default for `run` (per engine),
`conformance`, `bench`, `corpus-time` and `fuzz`
(`conformance.DEFAULT_TIMEOUT`); 60 s per case for `perf`. How a timeout is
graded is §C3: one deadline on both sides; the reference timing out is a
skip, the engine alone timing out is `MISMATCH timeout`. The library arm
has no deadline and uses `conformance.LIBRARY_STEP_LIMIT` instead (§C14).

## 16. C15 — Names

**STATEMENT.** Invariant 9. Engine strings are exactly the members of
`engines.ENGINE_ORDER` — the spectrum `lypning`, `lypning-l`, then
`cpython`; `lypning-mp` is a name but no tier. The two upstream names
appear in exactly three places: the credit paragraph in `README.md` §8, the
*Before the name* section of `CHANGELOG.md`, and the historical corpus
JSONL. Nowhere else, including comments — and never as a live identifier.

**CODE HOME.** `engines.SPECTRUM`, `engines.ENGINE_ORDER`,
`engines.ORACLES`, `engines.parse_binary_name` (the one reader of the
`<engine>[-<target>]` shape, longest engine first), `engines.env_var_for`,
`engines.refusal_line`. The scope of the literal test is exact: it holds
that no `.py` file under `src/lypning/` other than `engines.py` spells
`"lypning-mp"` in code — comments and docstrings may say the word, and the
other engine literals are held by review.

**CHECK.** The grep reads the two names from `README.md` §8 and never
spells them; the second pattern writes `t[i]er` so that the line does not
match itself.

```bash
names=$(sed -n '/^## 8\. Credit/,/^## 9\./p' README.md | grep -o '\*\*`[a-z]*`\*\*' | tr -d '*`')
for n in $names; do grep -rlw --exclude-dir=.git --exclude-dir=target --exclude-dir=_site --exclude-dir=__pycache__ "$n" .; done | sort -u
grep -rnE 't[i]er [12]\b|t[i]er-[12]\b|middle t[i]er|second t[i]er|MicroPython t[i]er|the MicroPython var[i]ant|three interp[r]eters|three t[i]ers|both t[i]ers|two subset t[i]ers' README.md CLAUDE.md docs/*.md site/index.md src/lypning/cli.py | grep -vE '^docs/(BENCH-LEDGER|HILLCLIMB|PAPER|RESEARCH)\.md' | wc -l | tr -d ' '
python -m pytest -q tests/test_engines.py::test_no_engine_name_is_spelled_by_hand_outside_engines_py tests/test_docs.py -k 'upstream_names or node_id or tier_number or spelled_by_hand'; echo $?
```

**EXPECTED** — `grep · 2026-09-04 · 437056c · 3688 loaded`:

```
./CHANGELOG.md
./README.md
./src/lypning/assets/corpus/corpus.jsonl
./src/lypning/assets/corpus/seed-corpus.jsonl
52
0
```

Must not differ: the four files — and only those — carrying the upstream
names. Expected to differ, in one direction: the count of lines placing an
engine by position (52 on 2026-09-04, across `README.md`, `CLAUDE.md`, seven
documents and `site/index.md`) shrinks to 0 as each rewrite lands;
`tests/test_docs.py::test_no_document_places_an_engine_by_tier_number` is
`xfail` until then. This document contributes none.

**FAILURE MODES.**

| what regressed | what it prints | which gate turns red |
|---|---|---|
| an upstream name in a fifth file | the grep lists it | pytest: `tests/test_docs.py::test_the_upstream_names_appear_only_where_the_credit_says` |
| `"lypning-mp"` spelled in code outside `engines.py` | `[<file>:<line>]` | pytest: `tests/test_engines.py::test_no_engine_name_is_spelled_by_hand_outside_engines_py` |
| a variant name outside the closed letter set, or after the target suffix | `parse_binary_name` returns `("", name)` | pytest: `tests/test_engines.py::test_parse_binary_name_grows_with_the_spectrum` |

**PINNED BY.**

```
tests/test_docs.py::test_the_upstream_names_appear_only_where_the_credit_says
tests/test_docs.py::test_every_test_node_id_a_document_cites_exists
tests/test_engines.py::test_no_engine_name_is_spelled_by_hand_outside_engines_py
tests/test_engines.py::test_parse_binary_name_is_the_one_name_parser
tests/test_engines.py::test_env_var_for_spells_every_pin_by_rule
```

## 17. Filing a report

A report names a contract, shows the run, and shows the diff. Copy this:

```
Contract:  C<n>  (docs/VERIFICATION.md §<n>)
Run:       <date> · <git rev-parse --short HEAD> · <uname -sm> · <the corpus line from lypning status>
Command:   <verbatim, as typed>
Expected:  <the EXPECTED block, copied from this document>
Observed:  diff <(printf '%s\n' "<expected>") <(<command> 2>&1); echo $?
git status --porcelain:  <empty, or the lines it printed>
Gate that turned red:    build | conformance | doctor | gate | git status | pytest
```

It goes to a GitHub issue titled `C<n>: <one line>`. Two rules a report
obeys: a MISMATCH report never proposes a capability-table edit — the table
describes what the engine does (invariant 1) — and an UNSUPPORTED is filed
as coverage under the `--plan` ranking, not as a defect. The classes, and
where each goes:

| class | what it is | where it goes |
|---|---|---|
| MISMATCH | an engine that ran disagreed with CPython (§C3) | an issue `C3: …`; never a table edit |
| CONTRACT | exit 90 with bytes on stdout, without the line, with a sibling's name, or a traceback where a refusal belongs (§C1, §C2, §C14) | an issue `C1: …`; a build that reports `BROKEN` is already red |
| UNSAFE | a route whose answering engine mismatched (§C5) | an issue `C5: …`; fatal |
| LATE-WASTED | the routing budget moved (§C5) | not a report, unless systematic — one construct, many programs — then an issue naming the construct; otherwise `lypning conformance --plan` and `lypning routes --plan` |
| UNSUPPORTED | a refusal (§C1, §C3) | coverage: `lypning conformance --plan`; never an issue |
| HOLE | an unbuilt arm, an unmeasured gate check, an empty ledger (§C6, §C11, §C12) | nowhere — unless it rendered as a zero, which is a CONTRACT-class issue |
| oracle divergence | `lypning-mp` disagrees with CPython (§C12) | `.github/known-mismatches.json`, by identity, through the CI job; never an issue and never a table edit |

Symptoms that are not what they look like:

| symptom | meaning | where to look |
|---|---|---|
| an empty capture log | the shim is not installed, or installed and shadowed — the same symptom | `lypning shim status`, the `PATH` line (§C10) |
| `MISMATCH … contract` | the refusal contract broke, not the program | §C1; `lypning build --rust -v` |
| `not built` | a hole: nothing was measured, nothing is known | §C12; never read it as a zero |
| `UNSAFE` above 0 | the router sent a program to an engine that answered wrongly | §C5; the run names the entry |
| a `lypning routes` count | a floor: the Rust dispatcher never writes | §C11 |
| a battery that crashed mid-way | the restore did not run | `git status` yourself (§C8) |

**Verify this document.** The blocks above are its claims; these two
commands check the ones a test can hold, and §0 regenerates the rest.

```bash
python -m pytest -q tests/test_verification.py tests/test_docs.py; echo $?
lypning conformance --mixture both | grep -E '^(MISMATCH [0-9]|dispatchers|monotone)|UNSAFE'; echo $?
```
