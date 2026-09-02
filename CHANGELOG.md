# Changelog

Every change that matters, newest first, one entry deep. Each links to the pull
request that carries the full reasoning — that text is not repeated here, which
is what keeps this file scannable.

The project's own history starts before its name does. *Before the name* at the
bottom is where it came from, and what the two components used to be called.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) ·
Versioning: [SemVer](https://semver.org/spec/v2.0.0.html)

---

## Unreleased

**2026-09-02** — The corpus can no longer fork-bomb its own battery · [#28]

- **`lypning conformance` and `lypning bench` now skip a corpus program that
  would launch a battery** — a CLI battery subcommand, or the runner modules
  driven from Python (`conf.run`, `engines.dispatch`, `bench.corpus_time`,
  loading the whole corpus). This project's own dev sessions type these, so
  they are harvested into the corpus like any one-liner (235 of 3,688); run
  inside the battery each spawned a battery over the whole corpus again, a fork
  bomb that reached load average 340 on a shared host. A net, not a sandbox
  (invariant 4): capture still records them, the runner refuses to replay them,
  exactly as an absolute-path program is recorded and skipped. `lypning
  route`/`run` over one program stay representative usage and are not skipped.

**2026-09-02** — the corpus can be sliced by the model that issued the program ([#27](https://github.com/kristerhedfors/lypning/pull/27))

- **A new `models` field** on sightings and corpus records: a per-model
  histogram of the occurrences behind `count`. It is a subset of them, never a
  partition — an occurrence that cannot be attributed contributes to `count` and
  to nothing else, so `count - sum(models)` is the unattributed hole and a merge
  raises `count` rather than let that go negative. The key is omitted entirely
  when nothing is known, so records captured before this existed keep the bytes
  they have.
- **The hook stays on the hot path it was on.** Nothing in the PreToolUse
  payload names a model, so the hook writes down the `tool_use_id` it already
  receives and the join is done at harvest time, against the session transcript
  and its subagent tree. No new fork, no new file open, no change to the shell
  hook.
- **Attribution is Claude Code's only, by construction.** An opencode tool hook
  and an OpenHands `PostToolUse` payload carry no model and no key to join one
  on, so their records stay unattributed — which is what the hole in `models` is
  for, and is pinned by a test rather than assumed.
- **The harvest-time join reads only what was appended.** Transcripts are
  append-only, so the index is incremental and its offsets are cached under
  `$LYPNING_HOME` — a cache that is never a source of truth and whose every
  failure is a full re-read. Quoted in bytes because this machine is shared and
  a wall clock on it measures the machine: on 2026-09-02, over a copy of the
  real capture log (892 records, 4 sessions, 180 transcript files, 45,642,644 B)
  a cold harvest scans all of it and every harvest after it scans 0 B, reading
  734,678 B of staleness digests instead. The same log unmodified, with no ids
  in it, reads 0 transcript bytes and writes no cache at all.
- `lypning corpus --stats` now reports entries per model with an explicit
  `unattributed` row, and `--model NAME` slices the corpus, naming the whole the
  slice came from.
- Sightings records now carry unknown keys through, the way corpus records
  already did — an older harvest was silently stripping fields a newer one had
  written.

**2026-09-02** — Three more hosts over the one ABI: Go, Swift, LuaJIT; and a quickstart for every host · [#25]

- **Go** (`assets/go/`, cgo over the unchanged header, zero modules), **Swift**
  (`assets/swift/`, a Clang module map over the header, SwiftPM or plain
  `swiftc`) and **LuaJIT** (`assets/lua/lypning.lua`, `ffi` over the header read
  at load, no build step) join C, C++, Rust, Node and Python. The table in
  `docs/EMBEDDING.md` §4 is now the one place the hosts are counted; every other
  document says "every host" and points there.
- **One quickstart contract, eight files.** Every host has a
  `quickstart "<python source>" [args...]` that runs in-process under a step
  limit, hands a refusal to `python3 -c` once, and otherwise returns the
  program's own bytes and exit code. `tests/test_hosts.py` drives all of them
  through the same five probes and counts the traceback on stderr exactly once,
  which is the retry-a-failed-program drift no per-host test can see; CI runs
  the same five probes as one shell function per host, on Linux and on a new
  macOS job.
- **macOS is a first-class library platform.** `lypning build --lib` writes
  `liblypning.dylib` with an `@rpath` install name (a `build.rs` in the core
  crate, macOS only, and byte-identical `lypning` binary with or without it);
  the truncation check reads Mach-O the Mach-O way; `doctor` and `status`
  report a missing library as a hole, not a zero. `pyproject.toml` now claims
  `Operating System :: MacOS` on that basis.
- **Run against each other, not assumed to agree.** On 2026-09-02 (macOS arm64;
  clang, cargo, node 26, go 1.26, swift 6.3, luajit 2.1) `study/hosts/run_all.sh`
  drove every host over the 393-program study set: each of the eight reported
  341 ran, 52 refused, 0 other; 3144 capture records; `git status` unchanged.
- **Packaging.** The wheel ships every binding and quickstart as source (40
  files under `assets/{examples,go,lua,node,swift}` on 2026-09-02) and no build
  output; `dist-check` also rejects `.build/`, `.swiftpm/`, `node_modules/` and
  `.so`/`.dylib`/`.node`/`.class`. Both crates declare `rust-version = "1.78"`,
  the floor the committed lockfile format actually imposes.
**2026-09-02** — Tier 1 serves seeded `random`, bit for bit; four silent wrong answers found on the way · [#24]

- **`random`, the seeded-integer subset, on tier 1.** `seed(int)`,
  `random()`, `randint`, `randrange(a, b)`, `choice`, `getrandbits` are
  CPython's MT19937 exactly; everything else refuses. `random` leaves the
  middle tier's module table — its generator is not MT19937 and seededness
  cannot be decided statically — and both dispatchers now re-read a program's
  imports when a runtime refusal falls onward. Conformance 1325 → 1336 MATCH
  over 2125 graded (2026-09-02), MISMATCH 0, UNSAFE 0; `__text` +8.1 KB for
  everything below too.
- **`sum()` over floats answers only where CPython 3.11, 3.12 and 3.14
  agree**, else refuses `float-sum`. It was a naive fold — `sum([0.1]*10)`
  printed 0.9999999999999999 where 3.14 prints 1.0.
- **`raise SystemExit(n)` exits `n`.** It exited 1 with a traceback.
  SystemExit is one exception shared with `sys.exit()`, caught and `finally`'d
  as CPython does; ambiguous arguments refuse.
- **`lypning run` no longer re-runs a program that exits 90 on its own.**
  `print(1); sys.exit(90)` printed 1 twice — invariant 2's double run.
- The grader compares seeded streams (they were blanket-uncompared, which
  would have graded a wrong Mersenne Twister as MATCH); CPython warnings on
  stderr are no longer "an error the engine was silent about".

**2026-09-02** — the capture loop runs under opencode and the OpenHands SDK ([#26](https://github.com/kristerhedfors/lypning/pull/26))

- Two harness adapters, both MIT-licensed hosts, installed with
  `lypning install --harness opencode,openhands`. Neither merges into a file the
  user owns: opencode auto-discovers a plugin file, OpenHands discovers a plugin
  directory. opencode is what Berget Code's agents are built on.
- `capture.py` grows one neutral record builder and one small mapper per
  harness; every record now carries a `host`, and `lypning harvest --json`
  counts by it. The corpus schema is unchanged.
- Deliberately not shipped: automatic routing under either harness, and any
  write to `.openhands/hooks.json` — that file is first-match-wins and unmerged,
  so writing it would hide the user's rather than join it.
- How many python one-liners either harness actually types is **unmeasured**,
  as is the injected routing paragraph. `docs/HARNESSES.md` says which claims
  were verified against a real install and which were not.
||||||| parent of 9eac506 (Three more hosts over the one ABI: Go, Swift, LuaJIT; a quickstart for every host)

**2026-08-31** — pathlib costs nothing; `conformance --plan` is now ranked by cost

- **pathlib was measured before it was built, and not built.** 85 of the 87
  graded programs importing `pathlib` already route to lypning-mp; **2 reach
  CPython**, one of them the irreducible `from lypning import …`. Implementing
  it in tier 1 would have saved ~0.03 s and spent bytes against 16 KB of
  headroom.
- **`conformance --plan` now ranks by what a feature COSTS, not by how many
  programs it blocks.** A refusal the classifier sends to lypning-mp is answered
  at that tier's spawn; one reaching CPython costs ~30× more. The two orderings
  disagree sharply — `import re` blocks 185 and 12 of them cost anything;
  `import pathlib` blocks 83 and costs **nothing**; `.__name__()` blocks 22 and
  costs more than both. The table now prints `->cpy` beside `blocks`, and
  `plan_cost()` exposes the key. Falls back to block count when the mixture arm
  did not run.
- This loop spent two iterations proposing `--plan`'s top rows before the
  destination was measured. The ordering is the steering wheel, so it is now
  pinned by a test with a case where cost and count disagree.
- No engine change: bytes 1,032,400 (8 blocks), conformance 1325/800/1, mixture
  2119/0/7, 1314 tests green.

**2026-08-31** — `re` was the wrong row: `--plan` optimises the wrong objective

- **Retracting a number from the previous entry.** It claimed 800 tier-1
  refusals × ~11 ms of CPython spawn ≈ 8.8 s. Routing all 1,990 graded programs
  shows only **271 reach CPython** (13.6%); 410 are served by lypning-mp at
  0.36 ms. The avoidable cost is ≈**2.98 s**, and 111 of the 271 are this
  project's own `from lypning import …` development one-liners, which no tier
  can serve.
- **`conformance --plan` ranks tier-1 blockers, not cost.** Its top row, `re`
  at 185 programs, is already answered correctly by lypning-mp — as
  `modules.rs` has said in a comment all along. Verified before writing an
  engine, not after.
- **Tier 1 now answers `__name__` on a builtin receiver** (`int.__name__`,
  `len.__name__`, `ValueError.__name__`) — the one receiver whose name is not a
  guess. The wildcard refusing every other dunder is intact; a 20-case grid
  confirms it.
- **Two changes measured and reverted, both for invariant 1.** Rerouting
  `__name__` to lypning-mp gained 4 mixture MISMATCHes — the block had been an
  accidental shield against defects that tier has elsewhere. Teaching `type()`
  to answer for an exception instance gained 2 tier-1 MISMATCHes by unblocking
  programs into a separate pre-existing defect (`Value::Exc` stores its
  argument as a string, so `OSError(2)` reports `('2',)`).
- **`study/re/SEMANTICS.md` is new**: ~300 `re` input/output pairs run against
  real CPython 3.11 — the differential spec a future tier-1 engine must meet.
- Bytes unchanged at 1,032,400 (8 blocks); conformance 1325/800/1 with the
  ledger clean at 87/87; corpus-time 3.42 → 3.03 s; 1313 tests green.

**2026-08-31** — Iterations 66–68 all reverted; the speed gradient is flat and the dial is re-aimed

- **Three measured failures, three reverts, all kept in `docs/HILLCLIMB.md`.**
  Exact-capacity split (+15.6%), a shared ASCII case buffer (+4–6% on the very
  cases it targeted), and a first-byte pre-filter on builtin dispatch (+15.0%
  wide, +15.2% narrow). Each was A/B'd interleaved against **four
  unchanged-source probe builds**, which put the perturbation band at 5–9% on
  these paths.
- **The finding is the deliverable.** At `opt-level = "s"` with LTO and one
  codegen unit, the wins still available on the hot paths are smaller than the
  band the build itself moves them by. Iterations 64–65 took the allocations
  that were free to remove; what remains costs a pass to eliminate, or moves
  inlining more than it moves work. `builtin()` now carries a comment recording
  the 15% measurement so the idea is not re-attempted there.
- **The focus dial in `.claude/skills/hillclimb/SKILL.md` is re-aimed from raw
  performance to coverage**, which is the skill's own stop condition after
  three flat iterations — and the arithmetic is not close: 800 UNSUPPORTED
  programs at an ~11 ms CPython spawn each is ~8.8 s of avoidable work against
  a whole-corpus lypning total of 3.05 s. Fifteen blockers account for 619 of
  the 800; the top three are `re` (185), `lypning` itself (131) and `pathlib`
  (90).
- No engine behaviour changed: conformance 1325 / 800 / 1, binary 1,032,400 B
  at 8 blocks, 1311 tests green.

**2026-08-31** — Hillclimb iterations 64–66: two allocation kills, one measured revert

- **`str-fmt-pct` 8.09x → 5.49x** (iteration 64): `percent_format` stops
  allocating per conversion — the argument tuple is borrowed instead of cloned,
  the output is reserved past the growth cliff, and bare `%s`-on-str /
  `%d`-on-int write into the output directly. Sixteen shapes diffed against
  CPython; byte-identical binary.
- **`str-split` 8.21x → 4.41x** (iteration 65): the 128 ASCII one-character
  strings are interned singletons, consulted by every site that materializes a
  single character — both split paths, `s[i]`, `for c in s`, `chr()`. Two sites
  were allocating twice per character. Safe by construction here (`is` between
  equal immutables refuses) and convergent with CPython (its latin-1
  singletons). +4,096 B, still 8 blocks.
- **Iteration 66 measured, lost, reverted, ledgered**: exact-capacity two-pass
  splitting was +15.6% in a direct A/B — with tokens interned, the realloc it
  removes is cheaper than the second scan it adds. The failure is in
  `docs/HILLCLIMB.md` so it is not re-proposed.
- Suite TOTAL 2.52x → 2.25x across the two accepted steps; whole-corpus
  `corpus-time` 3.42 s → 3.05 s over 2,126 programs; conformance unchanged at
  1325/800/1 (the ledgered musl `pow` ULP).
- **Pool round-trip −0.37 ms/request**: the child applies the caller's
  environment as a diff instead of `clear()+update()` (~272 libc calls → a
  handful), semantics verified in both directions.
- Method-name id-dispatch was priced and retired: nulling the whole arity
  string-match pass bought 2.7% of wall on a scaled `str-methods` loop, so the
  ~20% of instructions in name matching does not convert (the ledger's
  binary-search lesson, again).

**2026-08-31** — The pool-backstopped chain, built: 1.77x and 745/745 correct

- **`lypning pool serve` is new** (`src/lypning/pool.py`): a pre-warmed CPython
  that forks per program, wired in as the chain's CPython tier via
  `LYPNING_POOL`. Opt-in, off by default, and it degrades to a cold spawn if the
  pool is down, wedged or unreachable — the backstop may be faster than CPython,
  never a new way for CPython to be unavailable.
- **The composition the measurements pointed at, measured instead of estimated**
  (`study/paper/pool_chain.py`, best-of-3 over the 745 clean programs):
  pool-backstopped chain **14.85 ms/prog, 1.77x, 745/745 correct** (481 answers
  from tier 1, 264 from the pool) against the cold chain's 17.41 ms / 1.51x /
  744, the pool alone at 18.76 ms / 1.40x, and cold CPython at 26.27 ms. It is
  the fastest arm and the only fast arm that answers everything.
- **Our own arithmetic was wrong by 40% and the paper says so.** The projection
  was ~3x; it ignored that the tier-1 pass is paid on all 745 programs including
  the 264 it refuses, and that the pool leg pays a socket round-trip.
- **A published claim is retracted: a warm pool is NOT correct by construction.**
  It freezes its environment at start. Forwarding the caller's `os.environ` per
  request took divergences from 6 to 3; the residual 3 were set/dict-view
  orderings, which a fork cannot fix because `PYTHONHASHSEED` is consumed at
  interpreter start. Starting the pool under the caller's seed took 3 to 0.
  **Start the pool as the interpreter its callers think they are getting.**
- **Two fidelity bugs in the pool, found by the corpus and not by hand.** The
  forked child must rebind `sys.stdout`/`stderr`/`stdin` onto its dup'd
  descriptors with CPython's own `surrogateescape`/`write_through` settings —
  inheriting the parent's objects silently drops all output whenever the host
  has replaced them (every hand-written smoke test passed; the test suite
  caught it immediately). And the pool arm needed the universal-newline
  translation the spawned arms get from `text=True`, or a `csv.writer` program
  grades as a divergence against its own reference.
- **The "2-program library defect" reported yesterday was build hygiene, not a
  bug.** `liblypning.so` had last been built a day before the binary, so the two
  artifacts answered from different subsets — the library still raised on
  `dict.keys() | {...}` the binary had learned to refuse, and refused an
  `rsplit` the binary had learned to answer. Rebuilding took frontier-probe
  disagreements from 4 to 1 (a musl-vs-glibc `pow` ULP, expected between a
  static binary and a host library).
- **`lypning doctor` now fails when the two artifacts disagree.** Seven
  constructs chosen to sit on the refusal frontier are run through both the C
  ABI and the spawned binary; nothing else in the package would have noticed,
  since both pass their own contract assertion and both report the same version.
- 26 new tests pin the pool to CPython byte-for-byte: traceback text with no
  pool frame, non-integer `SystemExit`, signalled children, state isolation
  between programs, large output through the pipe, and a socket that is not
  world-readable.

**2026-08-31** — The Monty stack warm-for-warm, and the executive verdict

- **The warm-shape asymmetry is closed** (`study/paper/warm_parity.py`, paper
  §5.5): both substrates warm, same 745 programs, same per-program temp cwd.
  liblypning sweeps at 2.23 ms/program against the Monty pool's 9.41 —
  ≈3.5 vs ≈25 ms per *answered* program, since lypning declines 262 cheaply
  while Monty attempts everything — with 480 matches / 1 silent divergence
  against 275 / 23. Stated with equal care: the all-answering warm chain
  (12.60 ms) is still slower on wall than Monty's pool; the extra 3.2 ms buys
  742 correct answers instead of 275 correct plus 447 errors.
- **The parity instrument caught a lypning defect**: the in-process library arm
  errs on 2 programs the spawned binary correctly refuses (480/262/2/1 vs
  480/264/0/1). Unresolved, reported in every affected table, and now the warm
  chain's correctness line reads 742/2/1 rather than borrowing the binary's.
- **`docs/EXECUTIVE-SUMMARY.md` is new**: does lypning improve, and where — as
  objective as the data allows. It was adversarially reviewed for bias in both
  directions before publication; the pro-lypning pass caught a buried loss and
  an order-of-magnitude flourish, the anti-lypning pass caught the warm-pool
  loss overweighted past what the data says and the topline framing our best
  result as a cost. Both sets of corrections are in the published text, and the
  verdict table now states its denominators and weightings per row.
- pydantic-monty 0.0.21 confirmed the current PyPI release at review time.

**2026-08-31** — The overarching review: gaps closed by measurement, and one claim revised down

- **Six new measurements landed in `docs/PAPER.md`, run because reviews demanded
  them.** The `-S` ablation (CPython starts in 8.67 ms with site disabled — the
  one-flag baseline recovers a quarter of its startup). CPython 3.13 as a sixth
  arm: it starts *slower* than 3.11 on this container (13.8 vs 11.6 ms,
  back-to-back), carries no experimental JIT — and as an engine against the 3.11
  reference it produces **9 silent divergences of its own**, so tier 1 (1) tracks
  CPython 3.11 more closely than CPython 3.13 does. A false-refusal sweep: zero
  exit-90s in 1,990 CPython runs. The tier-2 ablation: correctness identical
  without MicroPython (744/1), so the classifier's routing is demonstrated, not
  asserted. The per-program price list: the chain is slower than cold CPython on
  216 of 745 programs (29.0%), median delta −10.56 ms, worst +173 ms. And the
  invocation-weighted wall: weighting each program by its 6,171 capture-log
  invocations lifts the chain from 1.77× to **2.35×** — sessions re-run the
  simple programs, so for once the unrun measurement was hiding a number in our
  favour.
- **One of the paper's own claims is revised down.** Hand-classifying all 39
  PyPy divergences (shipped at `study/paper/data/`) splits them six ways:
  file-finalization 10, set order 7, call-signature 4, error-text 4, singletons
  4, probe-grids-beyond-window 10. The largest family is the one the profile
  predicts, but "dominant" was too strong a word and the paper now says so.
- **The prospective holdout is registered, not promised.**
  `study/paper/holdout_registration.json` pins the freeze commit, the date, and
  all 2,906 in-sample entry ids; anything captured later is out-of-sample by
  construction.
- **The documents now agree with each other.** COMPARISON.md drops the retracted
  tier-1 "ceiling" splice, gains the warm-pool loss and a cross-reference to the
  paper, and explains its 7-vs-1 and 340-vs-447 differences as instrument
  differences. The README's 0.302x headline is date-scoped and points at the
  paper's current ratios and the baseline that beats us; the paper is surfaced
  at the top of the doc table. Stale phrasings in this changelog's own earlier
  entries were corrected in place.

**2026-08-31** — The paper: an agent-Python profile, and five engines measured against it

- **`docs/PAPER.md` is new.** It asks what coding agents actually hand to an
  interpreter, answers it by measurement, and benchmarks CPython 3.11, PyPy
  7.3.20, MicroPython, Monty 0.0.21 and both lypning configurations on that
  corpus with one instrument. Harnesses ship in `study/paper/`.
- **The profile is the finding.** Over the corpus as loaded on 2026-08-30 (2,906
  entries, 2,869 parsed): the median program is 384 bytes, 10 lines, 74 AST
  nodes; 0.3% define a class; `match`, walrus and `async` do not appear at all;
  and 45.8% call `open()` (AST-counted). Decomposed inside the child over 765
  CPython-clean programs, the median program spends **0.019 ms executing against
  16.83 ms of spawn, interpreter startup and imports** — 88.4% execute in under a millisecond.
- **PyPy is the slowest engine on this workload** (3.0–3.1× CPython's wall in two
  sweeps) and returns **39 silent divergences**. All three divergence families
  are ones PyPy documents: non-prompt file finalization, ordered sets, and
  keyword arguments CPython rejects. The contribution is the blast radius, not
  the discovery. The cause is also now measured rather than assumed — PyPy pays
  **+16.22 ms over CPython on `print(1)`**, so the penalty is majority fixed
  startup, not unamortized warmup.
- **We lose to the obvious baseline and say so.** A pre-warmed CPython forking
  per program serves the 745 clean programs at 8.39 ms each (2.04× cold CPython)
  against our chain's 11.38 ms (1.50×), and it is correct by construction. The
  same table points past both: tier 1 alone serves its share at 2.67 ms, so a
  chain whose backstop is a warm pool should beat either.
- **Two reviewer objections answered with measurement, not prose.** The
  absolute-path exclusion really does remove the harder half — skipped programs
  loop at 75.1% against 38.2% retained, and define functions 3.6× as often — so
  the coverage rate is optimistic by an amount we now bound. And the
  self-hosting worry is a null on the evaluation: **none of the 745 clean
  programs import lypning**, so dropping them changes nothing (480/264/1 either
  way); the contamination lands on the profile, not the benchmark.
- **A real irreproducibility was found and fixed:** corpus programs calling
  `sys.stdin.read()` blocked on the harness's inherited stdin. Every child now
  gets an explicit EOF. Re-running the whole sweep afterwards reproduced every
  correctness count exactly, while wall clock moved with machine load — which is
  why timings are reported as ratios.
- **The bibliography was rebuilt after the survey agents turned out to have no
  network access.** Twelve references are now checked against primary sources;
  one recalled author list was confirmed fabricated (the POPL 2018 sourir paper's
  third author is Ming-Ho Yee). Attributions still resting on recall are marked
  `[unverified]` inline rather than quietly kept.
- **A hostile review of the finished draft found two arithmetic errors in our own
  favour, and both are corrected in place.** The composed "chain with a warm-pool
  backstop" estimate had spliced a blended average (2.67 ms covers 480 answers
  *and* 264 refusals) against the pool's per-program cost; done correctly it is
  5.65 ms and 3.03×, not the ~3.6× implied. And the chain's fallback is not one
  cold CPython spawn — 1,993 + 264 x 17.10 = 6,507 ms against a measured 8,476 ms,
  so a refused program costs 24.6 ms because it pays three tier spawns, not one.
  Every remaining arithmetic claim in the paper was then machine-checked.
- **Two further corrections of our own numbers.** `open(` by substring said 49.0%
  of programs; AST-counted `open()` calls give 1,314 of 2,869 parsed (45.8%), and
  that is the figure now used. And PyPy's fixed-startup share was reported as
  "roughly half" — the most flattering of three available denominators; the range
  is 49-78%, and on the clean-subset arm it is closer to four fifths.
- **The deduplication threat is answered with data rather than a caveat.** The
  capture log carries invocation counts: 2,906 distinct entries were seen 7,406
  times (mean 2.55, max 45, 63.5% seen once). Re-weighting by invocation moves the
  profile *away* from complexity - comprehensions 23.8% to 15.7%, loops 48.6% to
  44.7% - so the programs agents re-run are simpler than the ones they run once,
  and our coverage numbers are computed on the harder population.
- **The finding sharpened to its causal core.** 96.0% of the 1,314 file-touching
  programs use a bare `open()` rather than `with open(...)`; only 4.6% use the
  context manager. That is precisely the idiom PyPy documents as unsafe, which is
  why a documented difference becomes 39 silent wrong answers here.

**2026-08-30** — Measured against ADK-Rust CodeAct + Monty, on one instrument

- **`docs/COMPARISON.md` is new, and its numbers are runs, not vibes.** Both
  systems graded by the same harness over the same 1,990 corpus programs
  (2,906 loaded, absolute-path and nondeterministic entries excluded), CPython
  as the oracle with a pinned hash seed, 2026-08-30, pydantic-monty 0.0.21:
  lypning tier 1 answers 64.4% of the CPython-clean subset with **1** silent
  divergence (the ledgered musl `pow` ULP); Monty answers 36.9% with **23**.
  The identical both-fail counts (1,245) are the one-instrument sanity check.
- **Every published divergence was independently re-verified first** — three
  CPython runs each — and the verification *refuted two candidates*: Monty
  reproduces `hash(-1) == -2` correctly, and one set-order case agreed under a
  pinned seed. Both were dropped; the doc says so.
- **The framing is honest about the different jobs.** Monty + ADK-Rust CodeAct
  is a sandboxed in-process substrate for LLM-written tool-calling code, with
  snapshots and resource limits — nothing lypning does. lypning makes a
  harness's real `python3` spawns cheaper under a never-wrong-at-exit-0
  contract with CPython fall-through. Startup is a wash shape-for-shape
  (0.05 ms vs 0.04 ms in-process); coverage and fidelity are the axes that
  separate them.
- **Completed with the full performance picture, four instruments** (all
  2026-08-30): *startup* shape-for-shape (in-process 0.05 ms vs 0.04 ms — a
  wash; spawn 0.64 ms vs CPython's 10.33 ms); *sustained compute* over six
  workloads first validated byte-identical on every engine (nothing beats
  CPython on loops — lypning 1.9–4.5×, Monty 1.9–4.5×, MicroPython bimodal
  0.78×–23×, with callgrind instruction counts showing the wall costs are
  dispatch- and memory-bound, not instruction-bound); *end-to-end* over the 745
  CPython-clean corpus programs (chain 8.6 s with 744/745 identical and 1 ledgered ULP, CPython
  12.7 s, tier 1 alone 2.0 s at 64% coverage, Monty pool 6.8 s at 37% correct
  with 60% handed back to the model — and a model turn dwarfs every number in
  the table); *memory* (all at the ~8.6 MB spawn floor; Monty +1 MB on
  dict-heavy work). Headline claims were re-measured a second time before
  publication; the verification workflow's agents hit the harness
  parameter-stripping fault and honestly reported measuring nothing, so the
  re-check ran inline.
- The grading and performance harnesses ship at `study/monty/` (with the six
  workloads), so every table is re-runnable — and re-run is the instruction,
  per invariant 3.

**2026-08-30** — lypning is the Coding Harness Interpreter Optimizer

- **The identity changes; the architecture keeps its name.** The headline in
  the README, `docs/LYPNING.md`, the CLI description, the package and crate
  docstrings, the site title and footer, and the shipped skill now read
  *"lypning — the Coding Harness Interpreter Optimizer"*. *Mixture of Pythons*
  stays everywhere it explains the design — it is what the architecture *is* —
  including the classifier's own module doc and the *Before the name* history.
- **The new name is a claim about adaptability, and the docs now say so up
  front.** The README and `docs/LYPNING.md` intros state it directly: the
  corpus is captured from live sessions, every table is derived from it and
  re-derivable from yours, and `docs/FORKING.md` is the manual for pointing
  the optimizer at your own harness, agent and programs.
- Nothing load-bearing moves (invariant 9): engine strings, `LYPNING_*` env
  vars, `~/.lypning`, hook names and the CLI surface are all unchanged.

**2026-08-30** — The loop gathered 667 of its own programs, and the tree now
ships the instructions for pointing it at yours

- **`lypning harvest` folded one session's captures into the corpus: 2,239 →
  2,906 programs** (counts printed by the tools, 2026-08-30). The new entries —
  including the session's *own* probe programs — immediately surfaced five
  tier-1 defects, all fixed same-day: dict-view set algebra (`d.keys() | {"c"}`)
  raised where CPython computes the union, and now refuses as the escalated
  `dict-view` kind; `bytes(str, encoding)` never read the encoding's *value*,
  so `bytes('a', 'bogus')` answered `b'a'` where CPython raises `LookupError`
  and `latin-1` came back as UTF-8 bytes; `sorted([3,1], strict_mode=True)`
  silently ignored the unknown keyword where CPython raises.
- **Four more kinds join the escalation table, each measured on the mp binary
  first**: `nan-order`, `identity`, `iterator-type-name`, `encoding`, and a
  split `dunder-missing` (`__module__`/`__doc__`, which mp lacks) out of
  `dunder-attr` (`__name__`/`__class__`, which mp answers). `import random as
  r` defeated the `random.seed` marker — aliases now resolve, and
  `from random import seed` is caught at the import line.
- **The battery's nondeterminism screens grew two patterns the harvest
  demanded**: `os.path.getsize`-family (a program printing the live capture
  log's size can never match a reference taken a moment earlier) and
  `subprocess.*` (one probe spawns python3 300× with `PYTHONHASHSEED`
  deliberately removed — its own reference drifts).
- **The ledger was regenerated: 87 entries in 34 families, scorer exit 0.** The
  37 additions are the harvest re-capturing the session's documented mp
  defects on the arm that cannot be fixed here; seven new families
  (`encoding-arguments-ignored`, `iterator-type-names`, `dict-key-collapse`,
  `reversed-dict-absent`, `str-unicode-whitespace`, `repr-quote-choice`,
  `dict-view-set-algebra`) name defects the grids found this week.
- **`docs/FORKING.md` is the new deliverable**: the capture→harvest→gate→step
  loop documented as the project's standing feature — including the `/loop`
  invocation — plus complete fork-and-specialize instructions and every
  optimization in the tree classified as *universal* (keep byte for byte),
  *workload-general* (keep the mechanism, re-derive the contents with the
  named tool), or *corpus-specific* (re-measure or discard). Tier-1 arm over
  the grown corpus: **1 MISMATCH** (the ledgered musl-libm pow ULP).

**2026-08-30** — 23 of the 40 kinds in the classifier's MicroPython arm could
never reach it

- **`engine_for` runs at routing time, so the only refusal kinds that can reach
  it come from the parser, the lexer, or `Requirements::block`.** The match arm
  nonetheless listed 23 kinds only the *evaluator* emits — `set-order`, `del`,
  `json`, `round`, `percent-format` and the rest of the runtime vocabulary.
  Verified two ways: by scraping every `unsupported("…")` the parse/lex sources
  can produce, and by the routing table being bit-identical after the deletion
  (388 / 1,056 / 202, unchanged).
- **Six of the dead names contradicted `ONLY_CPYTHON_KINDS`** — one table said
  "send to lypning-mp" about kinds the other says only CPython gets right.
  Inert while dead; the day the parser learned to spot one of those constructs
  statically, the arm would have routed programs to exactly the tier the
  escalation table exists to keep them off, with no gate looking.
- **`micropython_kinds()` was written to read this arm and had zero callers.**
  It now has its job: a test holds the arm to the kinds the classifier can
  actually emit (`routing.classifier_kinds()`, scraped from the source) and to
  disjointness from the escalation table.
- Deleting the 23 string literals bought back exactly the 4,096 B the
  identity-rule work had added: the binary is back to **1,024,208 B**, 8 blocks,
  24,368 B headroom. Suite 1,271 → 1,272; battery identical.

**2026-08-30** — The element test is `x is y or x == y`, and every sequence
scan now uses it

- **`[n].count(n)` answered `0`; CPython answers `1`.** CPython's container
  protocols compare identity *before* equality, and a NaN is the one value for
  which the shortcut is observable. The rule was implemented once — as a
  whole-sequence prescan in `eq` that refused whenever a NaN was anywhere —
  while the ordering descent, `in` over lists and tuples, `min`/`max`, and the
  five scan methods (`list.count`/`.index`/`.remove`, `tuple.count`/`.index`)
  each called bare `eq` and skipped it. Seven measured programs answered
  wrongly at exit 0: `[n] <= [n]` was `False` against `True`,
  `max([[n],[n,1]])` picked the wrong element, `[n].index(n)` raised
  `ValueError` at exit 1 — the program's own exit, which the chain does not
  retry — and two *distinct* NaNs collapsed to one set element because the hash
  key was their bit pattern.
- **The rule now lives once, in `value::elem_eq`, and it is exactly as narrow
  as the ambiguity.** Both sides NaN → the question is identity, which a bare
  `f64` cannot carry → exit 90. One side NaN → they cannot be the same object
  *and* they are not equal, so `False` is CPython's own answer — which
  **recovers coverage**: `[n] == [1]`, `n in [1, 2]` and `[1, 2].count(n)` were
  refused by the old prescan and now answer. `hkey` refuses a NaN dict key or
  set member for the same reason, instead of collapsing by bits.
- **The test sits on the *unequal* exit, and that placement is the performance
  story.** A both-NaN pair always compares unequal, so only comparisons `eq`
  already rejected can need the refusal — equal elements cost nothing. Two
  earlier shapes were measured and discarded (the test in front of every
  comparison, then a whole-sequence prescan), but the wall clock on this host
  swings ±8% on identical binaries, so the deciding instrument was callgrind:
  instruction counts, which are exact. Against the pre-change build:
  list equality **−12.8%**, needle scans **−3.2%**, an equality-heavy composite
  **−3.8%**, mixed-method and whitespace workloads ±0.01% — the old
  whole-sequence NaN prescan cost more than the identity rule now does.
  Needle scans hoist the NaN half out of the loop (the needle is fixed) and
  test it only on a miss; `refuse_nan_identity` is `#[cold]` so its `format!`
  machinery stays out of the inlined loops.
- **Docs updated to match the tree.** `docs/LYPNING.md` gains the NaN-identity
  rule as the fourth deliberate refusal and describes the escalation the chain
  now performs (`ONLY_CPYTHON_KINDS`, read by both dispatchers); the README's
  dispatch diagram shows the skip. Found by a survey agent that measured 30
  divergences across a 400-cell NaN ordering grid.
- Corpus unmoved: UNSAFE 2, IDEAL 1512, tier-1 MISMATCH 1 (musl libm),
  mixture MISMATCH 1. Suite 1,261 → 1,271.

**2026-08-28** — There are two dispatchers, and the escalation rule was in one
of them

- **`lypning run -c 'print({3,1,2})'` answered `{3, 1, 2}`** where CPython
  answers `{1, 2, 3}` — at exit 0, through the binary. So did
  `x = float('nan'); print(x in [x])` (`False` against `True`) and
  `print(9007199254740993 / 3)` (`…330.5` against `…331.0`). All three are
  refused by tier 1 by name, and every kind was already in the escalation table.
- **The table was only in `engines.py`.** `engines.dispatch` is the Python
  dispatcher, which is what `lypning conformance` measures through its `mixture`
  arm. `main.rs::dispatch` is the Rust one — what `lypning run` executes and what
  `lypning bench` times — and it handed every tier-1 refusal to lypning-mp
  without looking at the kind. **The correctness gate tested a dispatcher users
  do not run, and the cost gate ran a dispatcher nothing checked.**
- **`route.rs` now owns `ONLY_CPYTHON_KINDS` and the Rust dispatcher reads it.**
  `finish` surfaces the refusal kind it already had in hand, so the chain can
  choose the next tier instead of assuming it. The Python copy is held to the
  Rust table by a test that reads it out of the source, the way
  `micropython_modules()` already does.
- **A capability gap still falls through.** `print(2**70)` is a `bigint`
  refusal and MicroPython has arbitrary-precision integers, so it must still
  reach the cheaper tier — escalating everything would be safe and slow, and the
  table's whole value is that it does not. Pinned.
- Bytes exactly unchanged at 1,024,208. Suite 1,232 → **1,261**. Corpus
  unmoved: UNSAFE 2, IDEAL 1512, mixture MISMATCH 1.
- Found by a survey workflow whose first run produced nothing — five agents did
  the analysis and all five failed a nested output schema five times over. The
  same three axes returned it on a flat one.

**2026-08-28** — The whitespace-split rule lived twice, and the copy that said
otherwise was the complete one

- **`str.rsplit(None, 2)` refused; `bytes.rsplit(None, 2)` answered.** The rule
  — leading and trailing whitespace never make an empty field, a spent
  `maxsplit` hands back the remainder verbatim, and from the far end a bounded
  `rsplit` keeps the *leading* whitespace — was implemented twice. The bytes
  copy's own comment read *"this is the only place the rule lives"*. It was not,
  and the str copy was missing a case.
- **One implementation now, `split_ws_each`, used by both.** Not `bytes_split`
  called on `s.as_bytes()`: the two whitespace sets genuinely differ — `str`
  splits on U+00A0, U+2000, U+3000, `\x1c` and `\x85`, `bytes` on ASCII only —
  so the rule is shared and the predicate is not. `rsplit` walks backwards
  instead of building a reversed copy, which the bytes version had needed.
- **Measured: bytes exactly unchanged at 1,024,208, and the corpus gained.**
  Tier-1 MATCH 1076 → **1077**, refusals 569 → **568**, IDEAL 1511 → **1512**.
  The string-methods grid went from 325 refusals to **261** — 64 programs that
  used to refuse now answer.
- **Performance, A/B against the pre-refactor binary, 25 interleaved rounds:**
  +3% on a microbenchmark that does nothing but whitespace-split, +2% on a mixed
  method workload, against a same-binary control reading −2%. Getting there took
  two attempts: decoding a character per position to ask one question cost
  **21%**, and an intermediate vector of ranges cost most of what was left.
- **It aborted the process before it did any of that.** The first version
  advanced the scan one *byte* at a time, walked into the middle of a multi-byte
  character and made `&str` slicing panic — `'café'.split()` exited 134 on a
  SIGABRT. A 728-program grid had passed it, because every subject was ASCII or
  whitespace and none held a non-space multi-byte character. The unit readers
  now return a width, which makes the mistake unrepresentable; the grid and the
  pins carry the missing characters.
- **And the forcing function the twins never had.** A parametrised differential
  runs all twelve shared method names against both `str` and `bytes` and
  requires each to match CPython — proven load-bearing by reintroducing one of
  the five drifts in a scratch build and watching it fail.

**2026-08-28** — An unused import was enough to reach a tier that answers wrongly

- **The two escalation tables did not cover the same ground.**
  `engines.ONLY_CPYTHON_REFUSALS` is the *runtime* half: it fires on a refusal
  tier 1 actually emitted. But tier 1 only runs when the classifier sends the
  program there, so a program whose **first** blocker is an ordinary capability
  gap goes straight to lypning-mp — tier 1 never refuses, the runtime table
  never sees the kind, and the tier answers at exit 0:

  ```
  import math                      # an unused import is enough
  x = float("nan")
  print(x in [x])                  # CPython True, lypning-mp False
  ```

  Same shape for `import math` plus `print(9007199254740993 / 3)`:
  `3002399751580331.0` against `3002399751580330.5`.
- **Both are visible in the source, so the static half can catch what the
  runtime half cannot.** `route.rs` now marks a `float("nan")` literal and a
  division whose literal operand is past 2\*\*53.
- **Measured cost: zero corpus programs.** The rules match the *AST*, not the
  text — and the only two corpus entries carrying that text hold it inside a
  string, a regex pattern in one and a literal being string-replaced in the
  other. A regex estimate over the source said "one program each"; it was
  counting strings.
- Found by an adversarial review of this session's diff. Its rebuttal agent was
  right that the escalation fires for the population it was built for, right
  that the routing gate would flag a corpus program of this shape as UNSAFE, and
  right that one of the three examples was invalid (a set-order comparison run
  without `PYTHONHASHSEED` pinned). The other two reproduce, and they are why
  this is a change and not a note.
- Routing unmoved: 1056 lypning / 388 lypning-mp / 202 cpython, UNSAFE 2,
  IDEAL 1511, mixture MISMATCH 1.

**2026-08-28** — A vanished arity check, and five ways a `range` was not a range

From a 39-agent grid campaign over comprehension scope, slicing, conversions,
`print`/`repr` and `range` — every finding independently reproduced before it
was acted on.

- **`[v for v, in [(1, 2)]]` printed `[(1, 2)]`; CPython raises
  `ValueError: too many values to unpack (expected 1)`.** A trailing comma after
  a *single* name makes a one-element tuple target, and `target_list` collapsed
  a one-item list back to a bare name — so no unpacking happened and the arity
  check vanished with it. A program CPython stops with an exception ran to
  completion and printed plausible wrong data. The parenthesized `(v,)` and two
  names `a, b,` were always right, which is what kept it quiet; it reached
  statement for-loops and all four comprehension forms.
- **`1.0 in range(5)` and `True in range(5)` answered `False`.** A range holds
  integers but `in` asks about *values*, and `1.0 == 1`. Matching only
  `Value::Int` missed both; a non-integral float is still `False`.
- **`range(0) == range(1, 1)` answered `False`.** Two ranges are equal when they
  describe the same *sequence*, not when their three fields match — both are
  empty, and a one-element range's step is not observable
  (`range(1) == range(0, 1, 2)`).
- **A range is hashable**, keyed on that same normalised form so equal ranges
  collapse in a set. This was `TypeError: unhashable type: 'range'` at exit 1.
- **`.start`, `.stop` and `.step`** are ordinary attributes CPython exposes and
  raised `AttributeError`; `.index` and `.count` are real methods this engine
  does not implement and raised it too. Exit 1 is the program's own exit, which
  the chain does not retry — so all five died where a refusal would have been
  answered one spawn later. The attributes now answer; the two methods refuse,
  as every other unimplemented method does. An attribute a range genuinely does
  not have still matches CPython's `AttributeError`.
- Corpus unmoved: UNSAFE 2, IDEAL 1511, mixture MISMATCH 1. 6,000 fuzz programs,
  one counterexample, and it is the known musl `libm` `pow` difference.

**2026-08-28** — Valid Python the parser does not know is a capability gap, not
a syntax error

- **`print((n := 1))` exited 1 with a `SyntaxError`.** So did `[*xs, 3]`,
  `{*s, 3}` and `x[0:1, 2]` — all four valid programs CPython runs. Exit 1 is
  the *program's* own exit, which the chain does not retry, so each simply died
  where a refusal would have been answered one spawn later.
- **The line was already drawn, in the other direction.**
  `docs/HILLCLIMB.md` iteration 14 deliberately turned `unsupported: token` into
  an exit-1 `SyntaxError` for bytes like `$` that cannot begin a token in *any*
  Python program — a SyntaxError is terminal, so spending a spawn to be told by
  CPython what lypning already knew was waste. The converse had no such care.
  Syntax the parser can **recognise and name** now refuses, exactly as `async`,
  `kwonly` and `nonlocal` already did; genuinely invalid syntax still exits 1.
- **`**` in a dict display is not swept up with it.** `{**d, 'b': 2}` is dict
  merging, and it already worked — only the `*` set-unpacking form beside it
  does not. Pinned both ways, along with `print(1 +` and `print($p)` still
  exiting 1.
- The classifier already contained all four (`route` reports `syntax` and sends
  them to CPython), so no dispatch outcome changes. What changes is the binary
  run directly, and what the conformance tier-1 arm would score if a corpus
  program ever hit one — a MISMATCH under invariant 1.

**2026-08-28** — A SIGABRT on a large range, and a correction to this morning's
NaN sort

- **`range(-2**62, 2**62)[:1]` aborted the process.** That range has 2\*\*63
  elements and the length was computed in i64, so the subtraction wrapped to
  `i64::MIN` and `slice_span`'s `clamp(0, n)` panicked on *min > max*. Exit 134,
  a SIGABRT — the one outcome the dispatcher cannot route onward, and one that
  takes an embedding host's process with it. `range_len` now computes in i128,
  which holds every length an i64 range can have.
- **Three quieter faults fell out of the same overflow.** `len()` of such a
  range answered `0` (the wrapped count, tidied by a `.max(0)`) and now raises
  CPython's own `OverflowError: Python int too large to convert to C ssize_t`.
  Indexing raised a spurious `IndexError`, and slicing built a range from
  `st * step` that had wrapped to a *negative* step, so
  `list(range(0, 4, 2)[::2**62])` answered `[]` where CPython answers `[0]`.
  Both refuse now: a range holds three i64s and cannot represent CPython's
  answer, which is `range(0, 4, 9223372036854775808)`.
- **A correction to a change made this morning.** `order` was changed to treat
  a NaN as *neither less nor greater* rather than raising, and
  `sorted([nan, 1.0])` was pinned as matching CPython. It did — for two
  elements, which is one comparison, where any consistent comparator agrees.
  It does not in general: `sorted([3, 1, float('nan'), 2])` is `[1, 2, 3, nan]`
  in CPython and was `[1, 3, nan, 2]` here.
- **Because a NaN stops the comparator being an order at all.** Every
  comparison against one is false, so *not less* holds in both directions and
  which element moves depends on the sequence of questions the algorithm asks.
  CPython's answer is timsort's, and no fix to the comparison can close that —
  so a sort over a NaN is now `unsupported: nan-order`. Answering wrongly at
  exit 0 is worse than the TypeError it used to raise, which is why this could
  not be left as it was. `min` and `max` are unaffected and stay correct: they
  are linear scans asking one question per element.
- Corpus unmoved: UNSAFE 2, IDEAL 1511, mixture MISMATCH 1.

**2026-08-28** — The bytes methods are a copy of the str methods, and the copy
had drifted in five places

A 1,938-program grid over six subjects, seventeen argument shapes and seventeen
methods. The str original is correct in every one of these; only the twin is
wrong.

- **162 divergences were a suffix nothing in CPython prints.** Every bytes
  `TypeError` read *"a bytes-like object is required, not 'str' (in
  bytes.split())"*. `str(e)` is what a program prints, so the annotation is the
  whole message as far as the caller is concerned.
- **`find`/`rfind`/`index`/`rindex`/`count` have their own wording**, because
  those five also accept a single integer byte value: *"argument should be
  integer or bytes-like object, not 'str'"*.
- **`startswith` and `endswith` did not take a tuple of prefixes** — the point
  of the method. `b'abc'.startswith((b'a',))` is `True` in CPython and was a
  `TypeError` here; `str.startswith` has taken one since it was written.
- **`b''.join(1)`** said *"'int' object is not iterable"* where CPython — and
  `str.join` four hundred lines up, with the same `map_err` — say *"can only
  join an iterable"*. `join` also now names the failing item's index and type.
- **Two were answers, not messages.** `b'abc'.split(b'')` returned `[b'abc']`
  where CPython raises `ValueError: empty separator` (`str.split('')` already
  raised it). And `in` converted with `as u8`, which **truncates**: `300 in
  b'abc'` tested byte 44 and answered `False`, `-1` tested 255. Both are a
  `ValueError` in CPython.
- Grid after: **1,938 programs, 0 divergences, 816 refusals** — the refusals are
  `bytes.count`, `.index`, `.partition` and friends, which are not implemented,
  which is a coverage number and never a defect. Corpus unmoved: UNSAFE 2,
  IDEAL 1511, mixture MISMATCH 1.

**2026-08-28** — A keyword argument could silently refill a parameter the
positionals had already filled

- **`f(1, 2, a=9)` ran the function with `a=9` and `b=2`.** CPython raises
  *"got multiple values for argument 'a'"*; the binder looked the name up and
  inserted over the top, so the body executed on data the caller never passed
  together — at exit 0, with a plausible answer. Found by a 116-program grid
  over eight signatures against fourteen call shapes. The check is on the
  parameter's *used* bit, so every legal way of filling the same parameters
  still works.
- **`def f(a, *c, d)` is the same feature as `def f(a, *, d)`, which is
  refused.** This spelling fell through and recorded `d` as an ordinary
  positional parameter, which the binder cannot represent — it derives the
  positional count as `names.len() - star - dstar` and then slices
  `names[..npos]` from the *front*, which only holds while `*args` and `**kw`
  come last. `f(1, 2, d=3)` said *"unexpected keyword argument 'd'"* and
  `f(1, 2, 3)` raised `UnboundLocalError`. Neither was a refusal, so neither
  could be answered one spawn later. Now both spellings refuse.
- **`reversed()` reversed an iterator.** CPython needs `__reversed__`, or
  `__len__` and `__getitem__` together, so `list(reversed(iter([1, 2])))` is a
  TypeError there and answered `[2, 1]` here — the rarer divergence, where the
  engine *succeeds* and CPython refuses. `reversed({1, 2})` had been refused as
  a set-order exposure, which was a spawn spent on nothing: CPython never gets
  far enough to iterate, so it now gives CPython's exact TypeError.
- **An iterator's type name is not reproducible, so messages that would print
  one refuse.** CPython has a family — `list_iterator`, `tuple_iterator`, and
  `str_ascii_iterator`, which is `str_iterator` for a non-ASCII string. Same
  reason `repr()` of an iterator already refuses.
- Grids after: arg-binding 116 programs, dict-ops 350, iteration 458,
  string-methods 1,547, unpacking 198, control-flow 103 — **0 divergences**.
  Corpus unmoved: UNSAFE 2, IDEAL 1511, mixture MISMATCH 1.

**2026-08-28** — 1,611 divergences in the comparison operators, in three shapes

A 5,460-program grid over every ordering operator across 26 operand values. All
three shapes were silent — exit 0, a plausible answer, a wrong one.

- **1,461 named the wrong operator.** Every ordering comparison derived its
  answer from a single `Ordering`, so every one of them reported `'<'`:
  `0 <= ''` said *"`'<'` not supported between instances of `'int'` and
  `'str'`"*. CPython names the operator you wrote, at every depth — a sequence
  compares element-wise and hands the *original* operator to the first
  differing pair, so `[1] <= ['a']` says `'<='`. Rewritten as CPython's
  `list_richcompare` rather than an `Ordering` plus a mapping. `sorted` still
  says `'<'`, because that is the comparison sort makes.
- **120 were a NaN short-circuit that ran before the type check.** An ordering
  over a NaN is False because IEEE 754 says the relation does not hold — but
  only between values that *have* an ordering to fail. `'' < float('nan')` is a
  TypeError in CPython and was `False` here: an exception silently turned into
  a value. The same root cause reached the other direction, where
  `sorted([nan, 1.0])`, `min` and `max` raised *"cannot order NaN"* for three
  results CPython computes.
- **30 were `is`, and no amount of computing fixes those.** CPython answers
  identity on immutables from *interning*: `0 is 0` and `'ab' is 'ab'` are True
  because the compiler folded two constants into one object, while
  `int('1000') is 1000` is False for the same values. The answer depends on
  where the value came from. Now `unsupported: identity` — and `value.rs` has
  carried the comment *"refusing beats guessing either way"* since it was
  written, while returning `false`.
- **The refusal is narrowed to the question that cannot be answered.**
  `x is None`, `is True`, `is False`, two unequal values, `[1] is [1]`, and
  `x is x` for anything carrying an `Rc` all still answer.
- Grid after: **5,460 programs, 0 divergences, 48 refusals.** The corpus is
  unmoved — 569 tier-1 refusals before and after, UNSAFE 2, mixture MISMATCH 1.

**2026-08-28** — Two silent wrong answers in the Rust core, from one grid and
one guard

- **`7.0 // 1e-308` answered `nan`; CPython answers `inf`.** The float
  floor-division path guarded on `(x / y).is_finite()` and returned `nan` when
  it was not. That is right for `float('inf') // 2.5`, which *is* `nan`, and
  wrong for a finite pair whose quotient merely overflows. CPython never looks
  at the quotient — it takes `fmod` first, and the two cases separate
  themselves there, because `fmod(inf, y)` is `nan` while `fmod(7.0, 1e-308)`
  is an ordinary small number. Removing the guard is the whole fix: Rust's
  `f64::floor` is total, so the code below it already produced both answers. A
  390-program grid over the overflow neighbourhood: **98 divergences → 0**.
- **`type(2).__name__` raised AttributeError; CPython says `int`.** A dunder is
  part of the data model, so `AttributeError` for one is not a fact about the
  program — it is a false claim about Python. And it arrives at **exit 1, the
  program's own exit, which the chain does not retry**: unlike a refusal it
  cannot be answered one spawn later, so the program simply died.
  `e.__class__.__name__` and `len.__doc__` failed the same way — three measured
  MISMATCHes on the tier-1 arm. An unimplemented `__x__` is now
  `unsupported: dunder-attr`.
- **Deliberately a wildcard, not a list.** A list of the dunders CPython has is
  incomplete the moment someone uses the next one, and incomplete here means a
  silent wrong answer where over-broad means a process spawn. `(2).__dict__`
  refuses even though CPython raises `AttributeError` for it too — one spawn,
  and then the same error the program would have got.
- **`dunder-attr` is not escalated to CPython.** MicroPython gets `__name__`
  and `__class__` right, so this is a mixed kind, and the rule set last change
  holds: where a kind is mixed, split it in the engine rather than escalate the
  whole of it.
- Same run after both: the corpus is unmoved — UNSAFE 2, IDEAL 1511, LATE 90,
  WASTED 43, mixture MISMATCH 1. Every affected program already routed to
  CPython on an unrelated blocker, which is exactly why the corpus could not
  have found either defect.

**2026-08-28** — The session-start hook reported that capture was dead while it
was running

- **It was the third hook to lose the same arm.** A hook finds the package one
  of three ways: the `lypning` console script, the source tree via
  `$CLAUDE_PROJECT_DIR/src`, or a bare `python3 -m lypning`. In a checkout of
  lypning *itself* only the middle one works — and that is the session most
  worth capturing, because it is the one editing the engine. The capture and
  harvest hooks were given that arm earlier this session; `lypning-session-start.sh`
  never had it.
- **So it announced the opposite of the truth.** Its `additionalContext` read
  *"hooks are installed but the package is not importable. Capture and routing
  are inert this session"* — into a session where capture had already logged
  **457 invocations and 334 sightings**. Invariant 5 is why nobody noticed: a
  hook never fails a session, so a broken one goes quiet rather than loud. Its
  shim refresh had been failing silently the same way, which is the half that
  actually stops the feed when a container is recycled.
- **Two tests, because the arm has now been lost three times.** One asserts every
  shipped hook carries it; the other asserts `.claude/hooks/` still matches
  `assets/claude/hooks/`, since the tree carries the hooks twice and a fix
  applied to one copy is a fix this repository runs and no user gets, or the
  reverse.

**2026-08-28** — Two more constructs the classifier declines, and a refusal kind
that was two kinds

- **UNSAFE 4 → 2.** `hashlib.algorithms_guaranteed` and the `strict_mode=`
  keyword are the two commit-barrier constructs a parser can actually see, and
  they cost **one corpus program each** to route away. The third barrier entry
  is a regex whose pattern is only a string until it is compiled, so it stays —
  and stays the live reproduction in `tests/test_routing.py`. IDEAL 1509 → 1511.
- **`bigint` was doing two jobs.** Eleven refusals share the name; ten of them
  mean *Python would use a bignum here*, and lypning-mp **is** MicroPython,
  which has arbitrary-precision integers — it answers all ten correctly. The
  eleventh, `int / int` past 2\*\*53, means *the quotient needs rounding I cannot
  do exactly*, and MicroPython does the same lossy conversion and answers
  wrongly. Escalating the shared kind sent all eleven to CPython to rescue one.
  It is now `unsupported: int-div-precision`, and only that one escalates.
- **A correction.** `ONLY_CPYTHON_REFUSALS` was documented as costing "zero
  spawns on this corpus" because every kind in it was one MicroPython never got
  right. That was true of seven of the ten and false of three — `bigint` 10,
  `set-order` 4, `repr-unicode` 1. The comment now carries the per-kind
  measurement instead of the summary: five programs get slower, nine wrong
  answers become right.

**2026-08-28** — The mixture arm was scored against a reference it did not share
an environment with

- **One arm skipped `_env_for`, and it was the arm that measures what a user
  actually runs.** Every other arm — and the CPython reference itself — is
  handed `PYTHONHASHSEED=0`, `LC_ALL=C.UTF-8` and a capture log redirected into
  the sandbox. The mixture arm called `engines.dispatch`, which had no `env`
  parameter to hand it to, so its children inherited the battery's own
  environment instead.
- **It made the battery disagree with itself.** `print(min({(1,"z"),(1,"a")},
  key=lambda t: t[0]))` returns whichever element set iteration reached first,
  and without the pinned seed CPython randomises that per process. The entry
  flipped between MATCH and MISMATCH from run to run — showing up in the
  accepted-mismatch ledger as a *regression* on one run and as *no longer
  reproduces* on the next, which is the ledger's two loudest signals firing at
  random. Ten battery runs on that program now give one verdict.
- **The locale half was the more dangerous one.** Under `LC_ALL=C` every
  non-ASCII byte decodes to U+FFFD, so two engines printing *different*
  non-ASCII compare equal — a MISMATCH scored MATCH. `engines.run` has carried
  that comment since the bug was found; the mixture arm was outside it.
- **`dispatch` and `route` now take `env` and hand it to every child**,
  including the tier reached after a fall-through. Same run, after: the mixture
  arm is at **MISMATCH 1**, and that one is the last-ULP `pow` difference in
  musl's libm.

**2026-08-28** — A correct refusal was being turned into a wrong answer by the
tier below it

- **Tier 1 refuses; tier 2 answers wrongly; the user sees the wrong answer at
  exit 0.** The fall-through assumes the next tier down is at least as correct
  as the one that refused. Measured over the corpus the run loaded (2,239
  programs, 2026-08-28): tier 1 refuses **569** programs and **25** of those are
  then answered wrongly by lypning-mp. `n=float('nan'); print(n in [n])` is the
  short one — tier 1 declines it by name (`nan-identity`), MicroPython prints
  `False`, and CPython prints `True`.
- **The grader called this WASTED**, whose definition ended *"and the chain
  still produces the right answer"*. It graded the tier that was **named**, not
  the tier that **answered**. `score_route` now walks the fall-through chain, so
  seven routes that read as spare process spawns read as what they are. UNSAFE
  went 4 → 11 on no code change: the number was wrong, not the router.
- **A refusal says why, and some reasons rule out every tier but CPython.**
  `engines.ONLY_CPYTHON_REFUSALS` names ten kinds — `nan-identity`, `bigint`,
  `set-order`, `dict-view`, `exception-chaining`, `repr-unicode`,
  `percent-format` among them — where the refusal exists *because* the
  behaviour is subtle, which is the same reason a second reimplementation gets
  it wrong. A capability gap (`decorator`, `class`, `generator`) still falls
  through one tier at a time, because escalating one of those would pay a
  CPython spawn on every occurrence and buy nothing.
- **Same run, after: UNSAFE 11 → 4 and the mixture arm's MISMATCH 8 → 3.** Five
  wrong answers gone, measured on the dispatcher and not only on the grader,
  with IDEAL (1509), LATE (90) and WASTED (43) all unmoved. The ledger loses six
  more `mixture` lines and keeps one.

**2026-08-28** — The classifier declines the tier that would get it wrong

- **UNSAFE 7 → 4, and the three it closed were closed by not routing there.**
  lypning-mp is a third-party binary whose defects cannot be fixed in this tree,
  so the only lever left is the classifier: when the *source* shows a program
  would trip on a known one, send it to CPython. `route.rs` gained a
  `MICROPYTHON_UNSAFE` table naming three constructs — `random.seed(`,
  `.__module__`, and `pathlib`'s `.parts` — each a family in
  `.github/known-mismatches.json`.
- **Construct-level, not module-level, and that is the whole design.** `random`
  without a seed is reproducible, `Path.name` is right on that tier, and `.parts`
  on an unrelated object is an ordinary attribute. Measured over the corpus the
  run loaded (2,239 programs, 2026-08-28): the three constructs move **25**
  programs to CPython, against 133 for routing all of `pathlib` away. Twenty-five
  extra spawns buys three UNSAFE, and UNSAFE is a gate where LATE is a budget.
- **It cost nothing it was supposed to cost.** Same run: IDEAL 1505 → 1509 and
  WASTED 46 → 43, because those programs' cheapest *matching* tier was CPython
  all along; LATE 88 → 90. The ledger lost its three `mixture` lines and
  `known-mismatches.py` still exits 0 — 56 observed, 56 accepted. The
  `lypning-mp` lines stay: the tier is still wrong, it is just no longer asked.

**2026-08-25** — `sorted(…, reverse=True)` was reversing the ties · [#16]

- **A silent wrong answer in the Rust core, in one of the most ordinary lines an
  agent writes.** `sorted(counts, key=lambda k: counts[k], reverse=True)` — top-N
  by frequency — returned tied keys in the wrong order at exit 0. Python's sort
  is stable descending as well as ascending, and `sort_values` implemented
  `reverse=True` as `idx.reverse()` over a finished ascending sort, which
  reverses the ties along with everything else. CPython reverses the input, sorts,
  and reverses again; so does this now.
- **Three instruments were blind to it.** The corpus was at MISMATCH 0 over this
  bug for its whole life and is still at 0 after the fix; `perf` measures time,
  and a wrong answer arrives just as fast; the fuzzer generates random keys,
  which are mostly distinct. The defect is invisible without ties — and 67 corpus
  programs contain a keyed sort while **none of them diverge**, because 46 use
  tuple keys with an explicit tiebreaker.
- Pinned as six cases and as a grid over key functions with 1, 2, 3 and 5
  distinct values across lengths 0–13, cross-checking `sorted` against
  `list.sort`. Both were **run against the broken binary first** — 5 failures
  there, 0 after. Two seed corpus entries cover the idiom going forward.
- **`key=None` raised TypeError and `reverse=None` was obeyed** — the same
  mistake from both sides, at all four call sites (`sorted`, `list.sort`, `min`,
  `max`). `None` is the default for `key=`, and it is how an optional key gets
  spelled, so `sorted(xs, key=chooser)` with a `None` chooser died at exit 1 —
  which the dispatcher does not treat as a refusal, so nothing rescued it.
  `reverse=` goes through `__index__` in CPython; read for truthiness it turned
  a TypeError into an ascending sort at exit 0. Both pinned, and both checked
  against the broken binary first.
- **Keyword arguments were silently ignored across the builtins and container
  methods.** `'xax'.strip(chars='x')` returned `'xax'`, `'a'.ljust(width=5)`
  returned `'a'`, `{'a':1}.get('b', default=2)` returned `None`, `bool(x=1)`
  returned `False`, `int(x='5')` returned `0` — all at exit 0, all a TypeError in
  CPython. The allow-lists of which parameters may be named are now enumerated by
  asking CPython 3.11, and everything else raises with its exact wording.
- **The half-wired half was worse.** `str.split` read `maxsplit=` and not `sep=`,
  so `'a,b'.split(sep=',')` split on whitespace and answered `['a,b']`;
  `sum(xs, start=10)` ignored the start and summed from zero;
  `round(2.5, None)` and `round(number=2.5)` raised where CPython answers.
- **`int(s, 0)` aborted the interpreter.** `i64::from_str_radix` panics outside
  radix 2–36, so every out-of-range base exited **134** — neither 0 nor 90, so
  the dispatcher hands the Rust abort straight back to the caller. Base 0 is now
  implemented, leading-zero rule included, and verified as a 252-cell grid.
- Pinned as a 60-cell keyword grid comparing values *and* messages. Binary
  995,528 B, still 8 blocks; every arm's MISMATCH count and the whole routing
  table unchanged.
- **Malformed calls were answered instead of raising.** Extra positional
  arguments were dropped and missing ones defaulted, so `'ab'.strip('a','b')`
  returned `'b'`, `len([1],[2])` returned `1`, `chr(65,66)` returned `'A'`,
  `divmod(1,2,3)` returned `(0,1)` — nineteen cases, all at exit 0 — and
  `[1].insert(0)` put a `None` **into the list**. Arity tables now bound both
  ends, with the floor counting positionals only so `round(number=2.5)` still
  works. Fifty-five error-message *wordings* still differ from CPython's and are
  recorded rather than fixed: both interpreters raise, which is the part that
  matters.
- **`bytes` was the unswept side, and six defects lived there.** `bytes.rsplit`
  was `split` under a different name — `from_right` reached the splitter and was
  discarded; the whitespace set omitted `\x0b`, which Rust excludes and Python
  counts; whitespace splitting with `maxsplit` was wrong at both ends;
  `find`/`startswith`/`endswith` ignored `start` and `end`; and `hex(sep)`
  dropped the separator. Gridded at 1,342 cells. The `str` side was gridded the
  same way and is clean — it *refuses* the one case bytes got wrong silently.
- **Six more, each its own root cause.** `9.0 // 0.7` answered `11.0` (CPython
  corrects the floor when the discarded fraction exceeds half a unit — 623-cell
  grid); `True | False` answered `1` (bool overrides three bitwise operators, and
  only when both operands are bool); `list.index`/`tuple.index` ignored `start`
  and `stop`; `zip(a, b, strict=True)` **silently removed the guard it was asked
  to enforce** and is now refused; and `max({-1,1}, key=abs)` leaked this
  engine's set order, which the existing set-order guard did not cover — refused
  now only when a tie actually occurs, so `sorted(s, key=len)` still answers.
- Found by a six-lens fan-out at the Rust core that returned **32 verified silent
  wrong answers**, each adversarially re-run and minimised by a second agent.
  Twelve fixed; the remaining twenty are enumerated in `docs/HILLCLIMB.md` rather
  than half-done.
- **`try/except/else` ran the else clause on `break` and on `continue`.** The
  clause runs only when the body falls off the end — `break`, `continue` and
  `return` all leave without reaching it. Any flow at all used to run it, so
  `while True: try: break / else: print(...)` printed, and a `continue` printed
  once per iteration. Side effects are the ordinary reason to write an else
  clause, so this executed arbitrarily much code CPython does not. `finally` was
  already right and still runs on every path.
- **A bare `raise` inside a handler could not re-raise** — it answered
  `RuntimeError: No active exception to reraise`, which is correct only outside
  one. The interpreter now keeps a stack of the exceptions its enclosing handlers
  are handling, so a try/except nested inside a handler does not lose the outer.
- **`KeyError` was quoted at one construction site and not the other**, so
  `str(KeyError('f'))` was `f` while `repr()` of a lookup KeyError was
  `KeyError("'k'")`. Both now carry the key's repr.
- Pinned as an 18-case control-flow grid that includes the well-formed paths, so
  a fix that simply stopped running the else clause fails it. Binary unchanged at
  1,003,720 B.
- **Slicing and indexing swept as a 10,990-cell grid: zero silent wrong
  answers.** Ten receivers against every combination of start, stop and step. On
  the highest-traffic surface after `print`, the existing implementation is
  exactly CPython — recorded because it is the first sweep in five iterations to
  find none. Two real gaps did surface: `range` could not be SLICED at all
  (indexing worked; slicing raised a TypeError at exit 1, which the dispatcher
  does not treat as a refusal, so nothing rescued a construct CPython answers),
  and two index-error messages named types this subset does not have —
  `bytes` said "bytearray".
- **300 of 1,026 answerable format specs disagreed with CPython**, found by
  gridding the whole mini-language cross-product one program per spec. Six root
  causes: the `0` flag must set the FILL even when an alignment is given
  (`format(5, '<04')` was `'5   '`); zero padding is group-aware
  (`format(5, '09,')` was `'000000005'`); `,` and `_` were ignored for `g` and
  `%`; a precision with an empty presentation type was ignored, so
  `format(123456.789, '.4')` answered the whole repr; a precision on an integer
  type is a ValueError and was ignored; and `#` with a zero precision and
  grouping put the decimal point after the leading digit.
- **The `%` operator does not share the integer-precision rule** — `'%.2d' % 5`
  is `'05'`, a minimum digit count the mini-language cannot spell — so
  `format_value` and `format_value_pct` are now two entry points over one body.
  Two existing pins caught the conflation in the minute it was written.
- **Nested replacement fields took the wrong argument.**
  `"{:.{}f}".format(3.14159, 2)` raised and `"{:{}}".format(3.0, 5)` answered
  `'3e+00'`: the spec was expanded before the outer field claimed its argument,
  and the recursion restarted the auto-numbering counter. Explicit numbering was
  always correct, which is why it survived every hand-written example.
- **`round(5.0, -1)` answered `10.0`** — Rust breaks ties away from zero where
  Python breaks them to even, and only the negative-ndigits branch lacked the
  correction. `round(int, -n)` is now implemented rather than refused, in integer
  arithmetic so that ints past 2**53 keep their digits.
- All of the above at **1,007,816 B, 8 blocks — the binary did not grow.**
- **The operator matrix found four more.** `True in b"ab"` raised where CPython
  answers False — the `bool`-is-a-subclass-of-`int` slip already pinned for
  `bytes.find(False)` and never looked at for `in`; `{1} in {1}` raised where
  CPython converts the set to a frozenset and answers False; `'ab' % [1]` raised
  because the leftover-argument check exempted only `dict`, where CPython exempts
  anything that subscripts; and `b"%d" % 5` — PEP 461 bytes formatting, not
  implemented — became a **TypeError** rather than a refusal, so valid Python died
  at exit 1 with nothing to rescue it. 2,040 cells, now 0 differ.
- **Three lenses swept clean and recorded as such**: laziness and side-effect
  ordering (generator expressions, `map`, `filter`, `zip`, `enumerate` all lazy;
  `sum` eager; `any`/`all` short-circuiting identically), dict and set detail
  (33 cases including equal-key collapse), and `repr`/`str` (120 cases, 110 run,
  10 correctly refused, 0 differ).
- **The tier-1 arm is down to one mismatch, and that one is not ours** —
  `1.797e308 ** 0.5` off by a single ulp, measured against the library arm and
  proved to be musl libm against glibc libm, the same source compiled twice.
- **Dict views never reached the mutation guard that already existed.**
  `for k in g.keys(): del g[k]` emptied the dict and answered normally where
  CPython raises RuntimeError. A bare `for k in g` was guarded; the three views
  snapshotted into a plain vector and threw the dict away, leaving nothing to
  compare against. Both paths now build the same guarded iterator.
- **Every exception reported the wrong class.** `type_name` answered
  `"Exception"` for all twenty-four exception classes, so
  `'Exception' object has no attribute …` and
  `unsupported operand type(s) for +: 'Exception' and 'int'` both named a type
  the program had not used.
- **`e.__context__` claimed not to exist**, which is a claim about Python rather
  than about the program — and AttributeError is exit 1, so a handler inspecting
  the context died instead of being answered one spawn later. Refused now.
- **The accepted-mismatch ledger was rebuilt from measurement.**
  `.github/known-mismatches.json` named twelve; the arms report **fifty-nine**
  (48 lypning-mp, 10 mixture, 1 lypning), because the corpus grew 44% in a day
  and nobody had re-derived it. Every entry now carries a root-cause **family**
  and a reason: 59 entries, **27 families, 0 unclassified**, and the scorer exits
  0 — every mismatch is one the ledger names and every one it names still
  reproduces. Fifty-nine lines read as fifty-nine problems and they are
  twenty-seven; grouping says which would close together, and makes a fixed
  family a block of lines to delete rather than an unexplained drop in a number.
- A survey of **lypning-mp** verified 34 further divergences, recorded in
  `docs/HILLCLIMB.md` rather than fixed: that tier's sort is genuinely unstable,
  `round(2.5, 0)` is `3.0`, `isinstance(True, int)` is `False`, `json.loads`
  ignores its hooks, and `Path('/a/b').parts` drops the root. None fires through
  the dispatcher today, which is a fact about the corpus rather than about the
  tier.

**2026-08-25** — The classifier can see `os.path` · [#15]

- **`os.path.basename` routed to CPython for a function the engine has always
  had.** The module check in `route.rs` resolved only a bare name for a base, so
  every dotted path one level deeper fell into the method table and was blocked
  as `method: .basename()`. Fourteen `os.path` functions were invisible to the
  classifier that way. `resolve_module` now walks the path a step at a time, and
  a step counts only when it lands on a module — so `os.environ.get` stays a
  method, which it is.
- Measured over 1305 graded programs: **IDEAL 1190 → 1204, LATE 83 → 69**, and
  **14 programs stopped paying a CPython spawn** (12 to lypning, 2 to
  lypning-mp). WASTED, UNSAFE and every arm's MISMATCH count were unchanged, and
  the binary is identical to the byte — routing is parse-time.
- The cost this closes is not the spawn. `lypning route` is what the skill tells
  an agent to trust, and the prompting study watched agents replace working
  `os.path.splitext` calls with hand-rolled `rfind` to satisfy a tier that had
  already run them.
- `docs/LYPNING.md` §4's account of the fourth UNSAFE route was **wrong and is
  corrected**: `py-9b16a7261b96` dies at exit 1 with a traceback on
  `type(e).__module__`, not at exit 0 with wrong output.
  `.github/known-mismatches.json` had it right.
- **LATE counted 19 programs that were routed correctly.** A program that does
  not parse has an empty stdout and a non-zero exit on every tier, so each
  scored MATCH for producing nothing and the cheapest was graded the ideal
  destination for a program none of them can run — the difference is CPython's
  message, which lives on stderr and is not compared. The grader now skips a
  tier whose match was a shared failure, on `syntax` routes only and only when
  that tier exited non-zero; a tier that exited 0 with real output answered, and
  a classifier calling *that* a syntax error stays visible as LATE. Routing
  reads **IDEAL 1223, LATE 50** over 1305 graded programs, with correct-on-first-
  try unchanged at 97.5% — those programs always reached the right answer on the
  first spawn.
- **`decorator` and `generator` were listed as constructs no MicroPython-derived
  runtime has.** It has both, in the language. Ten more programs stopped paying a
  CPython spawn, and **WASTED did not move by one** — the imports are checked
  before the blocker kind, so `@functools.lru_cache` is still decided by
  `import functools`. `async` stays CPython-only for the opposite reason to the
  one recorded: `async def` parses there, but `asyncio` does not exist.
- Across the three routing changes: **IDEAL 1190 → 1233, LATE 83 → 40, programs
  routed to CPython 132 → 108**, at zero bytes — the binary is identical
  throughout — and with every arm's MISMATCH count unchanged.

**2026-08-25** — `%`-formatting agrees with CPython, and the numbers are re-measured · [#14]

- **The `%` conversion grid goes to zero.** A grid over conversion × flags ×
  width × precision × value reported **8,346 differing cells of 29,100**; it now
  reports none. The spec was assembled in the wrong order (`+0f` is valid,
  `0+f` is a ValueError, and the zero-pad flag was emitted first as if it were an
  alignment), `%5s` leaned left where CPython leans right, the `0x` prefix landed
  after the zero fill instead of before it, `#` dropped the decimal point it
  exists to keep, and `%c` was wrong four ways at once — including refusing
  `'%c' % 'a'`.
- `%.Nd` — minimum digits, which `format()` cannot spell — is **refused**, and
  only for the values where the precision actually adds digits. `'%.2d' % 42`
  still answers.
- **Every performance figure in `README.md` and `docs/LYPNING.md` §1, §2, §4 and
  §8 was re-measured** on 2026-08-25 against a corpus of 1551 programs, with all
  three engines built. The mixture answers 1305 of 1305 at **0.302x** of
  CPython's cost, a 69.8% saving.
- **The MicroPython tier was built for the first time in this tree, and it is
  red** — 11 mismatches, four of them the known commit-barrier defect. Six of the
  rest arrived because the *corpus* grew: last session's differential probes for
  the Rust core were harvested into it, and they find the same defect families in
  lypning-mp. Enumerated by identity in `.github/known-mismatches.json`, which
  the scorer now passes.

**2026-08-24** — The allocator, and fifty silent wrong answers · [#13]

- **The allocator was the workload.** Callgrind said 43.9% of instructions on a
  hot loop were inside musl's mallocng. A size-classed free-list allocator over
  bump-allocated chunks replaces it for the binary only — never for the C ABI,
  which must not impose an allocator on its host. `perf` TOTAL fell by a third;
  `str-concat`, blamed on a quadratic copy for this project's whole history, was
  really 32,104 `mmap`/`munmap` calls and is now 13x faster.
- **Static rather than static-PIE**, which is one CheerpX device block; boxing
  the error payload, which is 45 KB more and made every `R<T>` return in
  registers. The binary is smaller than it started and 8 blocks either way.
- **About fifty wrong answers at exit 0**, none of which any gate could see:
  Python's whitespace is not Rust's, `splitlines` splits on eleven boundaries,
  `str.count` ignored its bounds, six methods clamped a `start` CPython does not,
  all six case methods disagreed, and `json.loads` **answered malformed
  documents**. Each is pinned against live CPython, and the string bounds are
  pinned as a 44,352-cell grid rather than a list — a list is what failed to find
  the bug the first time.
- One MISMATCH is left open and named rather than papered over: the case methods
  still differ on 55 codepoints because CPython 3.11 ships Unicode 14.0 and the
  Rust toolchain ships a later one. `docs/HILLCLIMB.md` proposes the branch.

**2026-08-24** — Every markdown file the docs cite opens as rendered · [#12]

- `docs/PROMPTING.md` and `docs/HILLCLIMB.md` are in README §9's table and were
  cited across the docs, and the site published neither. Both now have a page,
  and `site/build.py` refuses to build if any `docs/*.md` has none.
- A backtick citation of markdown the site does *not* publish — a study prompt,
  a skill, an asset README — now resolves to the blob view that renders it.
  Upstream provenance paths stay plain code: they are not in this tree to open,
  and a citation that 404s is worse than one that does not move.
- `site/build.py --check` grew the assertion that catches this class of failure:
  an unlinked citation is not a dead link, it is grey text, so `check_links`
  could never see it. Bare names like `SKILL.md` are held to it too.

**2026-08-23** — Split the MicroPython gate, and fix what it turned out to hide · [#11]

- The job could not build for four consecutive runs and rendered identically
  whether the tier had answered a program wrongly or `musl.libc.org` had stopped
  answering. It is now two: **does it build**, which is blocking and can only
  redden when every precondition held and the build still produced no binary;
  and **does it agree with CPython**, which runs on the built binaries and
  *skips*, visibly, when there are none.
- `BuildResult.unavailable` splits a precondition this machine does not meet
  from a build that ran and broke — until now both printed `FAILED` and exited
  1. A failed download is classified by the fetcher's own exit code, not by a
  second network probe: the failure that started this (`curl: (35) Recv
  failure`) happens while a TCP connect to the same host still succeeds.
  `lypning build --skip-unavailable` is what a gate uses to tell them apart.
- Accepted mismatches are enumerated by **identity** in
  `.github/known-mismatches.json`, never by count: a count lets one defect be
  fixed while another appears and keeps the tick green. Measured with the tier
  built over the 1430 programs then loaded — the commit barrier, three
  self-referential entries that `import lypning`, and one that was not a
  refusal at all.
- That last one: **`base64` was not validating.** `validate=True` was accepted
  and ignored, so `b64decode(b"a!Gk=", validate=True)` returned `b'hi'` where
  CPython raises, and `b64encode("hi")` encoded a `str` the same way. Not a
  message difference — the tier answering "is this valid base64" wrongly, on a
  module the classifier routes to on sight, and invisible to the `core` job
  because the tier is absent there.
- `lib/base64.py` gains `_scan`, a transcription of CPython's error detection
  **only** — the decode stays on the C function, so the happy path is still one
  C call. It closes the message text too. Checked by brute force rather than by
  reading the C: every string up to length 6 over ``aG=!\n-`` plus 60,000
  random ones, **0 divergences**, and all eleven `base64` cases run against a
  built lypning-mp. `Discontinuous padding not allowed` was found that way and
  by nothing else. The strict messages are CPython 3.11's; the case says so.
- **The `binascii` model in `tests/test_shims.py` was wrong** in the direction
  that hides work — it wrapped CPython's decoder and lower-cased the message,
  modelling a divergence that does not exist while hiding one that does. Now a
  transcription of `extmod/modbinascii.c`. The tier grew 1,216 B, still 3
  device blocks.

**2026-08-23** — `base64.b64decode` raises the class CPython raises · [#9]

- The MicroPython tier's shim raised a bare `ValueError` where CPython raises
  `binascii.Error`, so `type(e).__name__` disagreed. Found by a harvested corpus
  entry that prints it — and it surfaced as an **UNSAFE route**, not a MISMATCH,
  because the classifier had already sent the program to that tier.
- `Error(ValueError)` with `__module__ = "binascii"` in the class body, so the
  qualified name agrees too. Verified on a real MicroPython 1.22.1 rather than
  reasoned about: the shipped shim raises `binascii.Error` there.
- `tests/test_shims.py` could not have caught it: the shim run imported
  CPython's `binascii` and got `Error` for free. It now models MicroPython's,
  whose defining feature is an **absence**.
- Also recorded, and *not* fixable from a shim: **MicroPython builtin types have
  no `__module__`**. `TypeError.__module__` is `'builtins'` on CPython and an
  `AttributeError` there. That is what makes one corpus entry exit 1, and it is
  a MISMATCH on that tier until the runtime grows the attribute.

**2026-08-23** — How far a prompt can push an agent into the subset · [#10]

- Nine prompt treatments × 3–4 independent agents × 26 deterministic tasks:
  **884 generated programs**, all kept, all routed by lypning's own parser and
  run against CPython. **66.3% → 88.5%**, and 88.5% *is the ceiling* — three
  tasks are outside the subset for any natural solution. Six treatments answer
  100% of what is feasible. **0 MISMATCH, 0 wrong answers.**
- **The cheapest saturating prompt carries no feature list**: 744 bytes of
  motive ties the generated capability tables, the rewrite cookbook, both, and
  both plus the engine in a verify loop. The one-sentence nudge is the least
  reproducible prompt measured — 11.5 pp between replicates, against 0.0 pp for
  every saturating one.
- Cost beats coverage: the mixture's bill over the same tasks falls **0.470x →
  0.169x** of CPython, because a program that leaves the subset costs a wasted
  classification *plus* a full spawn. The price is ~1.4 lines per program.
- `SKILL.md` scored **81.7%** — second-weakest of the nine — so it gains a §1a
  on writing *for* the subset rather than working *on* lypning. Not measured,
  and says so; `study/prompts/skill.md` keeps the text T3 actually scored.
- **Every one of the classifier's false negatives was `os.path`**: 35 of 884
  sent past tier 1 that tier 1 then ran correctly, all `.getsize()`,
  `.splitext()` and `.basename()`. `walk_expr` resolves a module attribute only
  when the base is a bare name. Not fixed here — it would invalidate the
  study's own measurements — and written up in `docs/LYPNING.md` §4, because an
  agent told to trust `lypning route` rewrites working code to satisfy it.
- **Using lypning as a library is invisible to lypning's capture.** Both feeds
  watch for a process; `lypning_run()` spawns none. Written up in
  `docs/CAPTURE.md`; `study/hosts/capture.h` is the forty-line workaround and
  argues the fix belongs in the C ABI.
- All five hosts — C, C++, Rust, Node, Python — driven over one shared set of
  393 programs and **agreeing byte for byte, refusal path included**. Corpus
  1037 → 1430.
- ⚠️ That fold moved conformance's tier-1 coverage 61.4% → 69.9% with the engine
  unchanged. Those points are this study's own output; exclude
  `tests/corpus/sightings/lypning-prompting-study.jsonl` before quoting corpus
  coverage as a field number.
- Re-scored against the merged engine after [#7]: **not one of the 884 rows
  moved.**

**2026-08-23** — A hillclimb loop, the instrument it needs, and six defects it
found · [#7]

- `lypning perf` — a per-construct diagnostic ranked by **ratio × how much of
  the corpus types the construct**, which is not the same list as ratio alone.
- `for line in sys.stdin` was **quadratic** in the size of stdin: 22,422 ms →
  21 ms at 50,000 lines. No gate could see it — 19 corpus entries carry a stdin
  sample and the largest is 38 bytes.
- Six correctness defects, five silent at exit 0: `isinstance` on an exception
  disagreed with CPython five ways; `except OSError` missed an `IOError`; and
  `lypning run` printed *nothing* where CPython printed the answer, whenever a
  program read stdin and then refused.
- `$`, `` ` ``, `?` and a bare `!` are a `SyntaxError` rather than a refusal —
  5 programs from UNSUPPORTED to MATCH, for zero bytes.
- Binary unchanged at 1,045,176 B across every commit. Corpus 842 → 1037.
- `.claude/skills/hillclimb/SKILL.md` and `docs/HILLCLIMB.md` are the loop and
  its ledger, including the four steps that did not work.

**2026-08-21** — The documentation leads with a measurement taken today · [#6]

- `README.md` and `docs/LYPNING.md` opened on an upstream table whose headline
  claim had already failed to reproduce twice. Both now open on a run taken in
  this tree, with the reversal stated where the claim used to be.
- A logo — the thundercloud for the lightning the name came from — and the
  dataflow drawn: shim or hook, the classifier, the three tiers.
- Every `docs/*.html` link on the landing page pointed at a GitHub 404. The
  link check skipped them because it skips absolute URLs; it does not now.

**2026-08-21** — Embeddable: a C ABI, and five hosts over it · [#5]

- `lypning build --lib` produces `liblypning.so`/`.a` and headers, so a harness
  can run a program **in its own process**. On the programs lypning accepts
  that removes the spawn, and the spawn was 96% of a one-liner's cost.
- One crate, lib + bin: `main.rs` and `capi.rs` are both consumers of the same
  `embed::run`, because a second implementation of the refusal contract is how
  a MISMATCH reaches a release.
- Nine ways an embedded program could kill its host, closed — unguarded value
  recursion in `==`, `<`, `in`, `sorted`, tuple dict keys and `json.dumps`; a
  long flat `1+1+1+…` spine; `"a" * (10**14)`; a NUL byte in the source.
- Two of those were wrong *answers* rather than crashes: a `break` in a
  `finally` swallowed a refusal, and `os.mkdir` reported a run as reversible
  when it was not.

**2026-08-20** — A case declares which CPython its oracle must be · [#4]

- CI went red on 3.9 and 3.10 with four failures never seen locally: the floor
  the package advertises had never been run before it was advertised. These
  suites use a live CPython as the oracle, and four cases ask a question an
  older one cannot express. A case now names its minimum and is skipped below
  it, rather than deleted.

**2026-08-20** — The site publishes itself · [#2], [#3]

- A Pages workflow that enables Pages, and — when that turned out to need
  repository-admin rights a `GITHUB_TOKEN` does not have — the one-time manual
  step written down, with the build left loudly red until someone does it.

---

## 0.1.0 — 2026-08-20 · the extraction · [#1]

First release. The two runtimes lifted out of [DeepResearch.se][ds], where they
were entangled with its npm scripts, its `tests/corpus/`, its `.claude/` wiring
and its shell scripts, and turned into a standalone installable package.

- **The three tiers and the router**: the Rust subset, the MicroPython variant
  with its frozen shim stdlib, and CPython — with a classifier that asks the
  Rust core's own parser which tier can take a program, and a dispatcher that
  falls onward on exit `90` and on nothing else.
- **`lypning` is an interpreter**: `-c PROG`, `FILE` and `-` exec straight into
  the Rust core, so anything that calls `python3` can call this instead.
- **A CLI**: `run`, `route`, `build`, `status`, `doctor`, `install`,
  `uninstall`, `shim`, `hook`, `conformance`, `fuzz`, `bench`, `corpus-time`,
  `gate`, `harvest`, `corpus` — every one with `--json`.
- **The corpus**, 839 harvested and seeded programs, moved into package assets.
- **Renamed throughout** — see the table below.
- **Zero runtime dependencies**, enforced by a test rather than a rule.

---

## Tracked defects

Recorded here rather than waived, because widening a capability table to make a
number green converts a loud failure into a silent one. `README.md` §5 and
`.github/workflows/ci.yml` both point at this section.

- **`lypning conformance` does not end at 0 on the `lypning-mp` arm**, and the
  largest class is one defect: MicroPython streams stdout, so a program that
  prints before reaching an unsupported construct has already committed those
  bytes when it exits 90. The Rust core stages output and discards it on
  refusal; the MicroPython tier cannot, and the dispatcher covers for it.
  Reproduction in `docs/LYPNING.md` §6. Blocking again once that tier grows a
  commit barrier.

  The count is **not** the 2 this file used to state. Measured 2026-08-23 with
  the tier actually built, **over the 1037-program corpus of that hour**:
  MISMATCH 8, UNSAFE 3. Three of those are the barrier defect above; the rest
  are gaps this tier had never been run against, exposed by a corpus that had
  grown 842 → 1037 the same day. It is **1430 now** ([#10]), so that pair is
  already a reading about a smaller corpus and not a current fact.
  `tests/test_routing.py` pins `contract:` as the only shape an UNSAFE route may
  take, so one that goes wrong any other way fails there rather than joining a
  count. Re-measure before quoting either number — and note that CI's
  MicroPython job builds that tier over the network and often cannot, in which
  case it reports nothing at all rather than a number.
- **`float ** float` is one ULP off on some arguments.** Both engines call
  their libm's `pow`; the core is static musl and the reference here is glibc,
  and glibc's is correctly rounded on these where musl's is not. `lypning fuzz`
  reproduces it at seed 1817614320. Closing it means a correctly-rounded `pow`
  or refusing float `**` — both decisions, not fixes.
- **`repr(float)` breaks an exact shortest-repr tie the other way.** CPython's
  dtoa rounds the last digit to even; Rust's rounds up. 0 of 2996 random
  doubles differ; 2 of 10 hand-picked ties do.

---

## Before the name

lypning was built inside [DeepResearch.se][ds] — a privacy-research platform
whose agent runs shell commands in an **in-browser CheerpX Linux VM**. That
sandbox is the reason this project exists: its root filesystem streams block by
block over a WebSocket, so `python3 --version` costs **8,573 ms cold** against
87 ms warm, and the exec ceiling is 30 s. Cost there tracks bytes and file
opens, nothing else — which is the cost model both runtimes are still optimised
against today.

Two components were built for it, and both were renamed on extraction:

| upstream | here | what it is |
|---|---|---|
| `pygram` | `lypning-mp` | the MicroPython variant with the frozen shim stdlib |
| `mopy` | `lypning` | the Rust subset, and the Mixture-of-Pythons router |

`mopy` was short for *Mixture of Pythons*, which is still what the design is
called. The current name comes from lightning; `docs/logo.svg` is the
thundercloud that says so.

**2026-08-20** — Both runtimes close their contract holes, then generate the
cases the corpus never covered · [PR #485][u485]. The last upstream work on
either; the extraction happened the same day.

**2026-08-19** — A differential fuzzer over the Rust subset's own declared
subset, and the 105 gaps it found. It still ships, as `lypning fuzz`.

**2026-08-16** — **`mopy` arrives** · [PR #478][u478] — a Rust Python subset
and the Mixture-of-Pythons router. Re-measured against a corpus that grew from
420 to 472 programs within the day · [PR #481][u481]; that pair of numbers is
why no document in this repository quotes a remembered corpus size.

**2026-08-15** — The compiler-optimisation lane closed with measurements rather
than opinions · [PR #453][u453]. The corpus becomes per-session and survives the
container · [PR #451][u451], [#460][u460].

**2026-08-14** — **`pygram` lands** · [PR #432][u432] — a 390 KB Python for the
sandbox that opens zero files. Then 22% off the binary, every change measured
· [PR #434][u434]; a **stock MicroPython control** so an optimisation can be
judged at all · [PR #435][u435]; and the first measurement in a real VM, where
the frozen stdlib streams zero bytes and CPython wedges · [PR #444][u444].

**2026-08-13** — `pygram` begins, and the first commit contains **no
interpreter**: a charter, a subset spec, a conformance runner, a size gate and a
seed corpus — 1,856 lines of instrument before a line of the thing it measures.
The exit-`90` refusal contract and the MATCH / UNSUPPORTED / MISMATCH split,
which are invariants 1 and 2 today, are specified there on day one. MicroPython
is picked on evidence two commits later and a daemon design dropped on the same
evidence; `docs/RESEARCH.md` is that survey.

**2026-07-24** — `python3 --version` is measured at **8,573 ms cold** against
87 ms warm inside the sandbox, and written down. Three weeks before either
runtime exists — the number came first, and both were built for it. It is
`docs/SANDBOX-PERFORMANCE.md` today.

**2026-07-04** — DeepResearch.se's first commit.

---

[#25]: https://github.com/kristerhedfors/lypning/pull/25
[#1]: https://github.com/kristerhedfors/lypning/pull/1
[#2]: https://github.com/kristerhedfors/lypning/pull/2
[#3]: https://github.com/kristerhedfors/lypning/pull/3
[#4]: https://github.com/kristerhedfors/lypning/pull/4
[#5]: https://github.com/kristerhedfors/lypning/pull/5
[#6]: https://github.com/kristerhedfors/lypning/pull/6
[#7]: https://github.com/kristerhedfors/lypning/pull/7
[#9]: https://github.com/kristerhedfors/lypning/pull/9

[#10]: https://github.com/kristerhedfors/lypning/pull/10
[#11]: https://github.com/kristerhedfors/lypning/pull/11
[#12]: https://github.com/kristerhedfors/lypning/pull/12
[#13]: https://github.com/kristerhedfors/lypning/pull/13
[#14]: https://github.com/kristerhedfors/lypning/pull/14
[#15]: https://github.com/kristerhedfors/lypning/pull/15
[#16]: https://github.com/kristerhedfors/lypning/pull/16
[#24]: https://github.com/kristerhedfors/lypning/pull/24
[ds]: https://github.com/kristerhedfors/deepresearch.se
[u432]: https://github.com/kristerhedfors/deepresearch.se/pull/432
[u434]: https://github.com/kristerhedfors/deepresearch.se/pull/434
[u435]: https://github.com/kristerhedfors/deepresearch.se/pull/435
[u444]: https://github.com/kristerhedfors/deepresearch.se/pull/444
[u451]: https://github.com/kristerhedfors/deepresearch.se/pull/451
[u453]: https://github.com/kristerhedfors/deepresearch.se/pull/453
[u460]: https://github.com/kristerhedfors/deepresearch.se/pull/460
[u478]: https://github.com/kristerhedfors/deepresearch.se/pull/478
[u481]: https://github.com/kristerhedfors/deepresearch.se/pull/481
[u485]: https://github.com/kristerhedfors/deepresearch.se/pull/485
