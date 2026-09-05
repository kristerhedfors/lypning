# lypning

<div class="hero">
<img class="hero-logo" src="docs/logo.svg" alt="" width="72" height="72">
<h1>lypning</h1>
<p class="tagline">The Coding Harness Interpreter Optimizer. Run a Python
one-liner on the cheapest interpreter that can actually run it — the chain is
<code>lypning</code> → <code>lypning-l</code> → <code>cpython</code>.</p>
<p class="cta">
<a class="btn" href="readme.html#2-installation">Install</a>
<a class="btn ghost" href="docs/lypning.html">How it works</a>
<a class="btn ghost" href="docs/verification.html">Check it</a>
<a class="btn ghost" href="https://github.com/kristerhedfors/lypning" rel="noopener">Source</a>
</p>
</div>

The router asks the Rust core's own parser which engine can run a program; no
text heuristics. The subset is sized to what agents type, not to Python.

Every tier refuses the same way: **exit `90`, one line on stderr, nothing on
stdout.** The line is `<engine>: unsupported: <kind>: <detail>`
(`engines.refusal_line`); any other non-zero exit is the program's own,
returned unchanged (`docs/VERIFICATION.md` §C1).

<div class="tiers">
<div class="tier"><h3>lypning</h3>
<p class="meta">Rust · no crates · static musl on Linux · frozen at
8 blocks (<code>gate.VARIANT_BLOCK_BUDGET</code>)</p>
<p>The core, run in-process. Stages its output and discards it on refusal, so
a refused run is observably a no-op.</p></div>
<div class="tier"><h3>lypning-l</h3>
<p class="meta">the same crate, built larger ·
32 blocks (<code>gate.VARIANT_BLOCK_BUDGET</code>)</p>
<p>Carries every new capability — <code>collections</code>,
<code>pathlib</code> (<code>engines.VARIANT_CAPS</code>) — and answers what
the core refuses.</p></div>
<div class="tier"><h3>cpython</h3>
<p>The reference, and the answer to everything the other two refuse. Correct
always, cheap never.</p></div>
<div class="tier">
<h3>lypning-mp</h3><p class="meta">the oracle — measured, never routed to</p>
<p>A second reimplementation kept to measure against
(<code>engines.ORACLES</code>). <code>lypning oracle</code> renders where it
diverged from CPython in families — a family is one reason to implement
something exactly or to refuse it (<code>oracle.py</code>) — binary built or
not; absent, every tool prints <code>not built</code>, and a wheel, which
ships no catalogue, prints <code>no catalogue</code>: a hole, never a zero
(§C12).</p></div>
</div>

## How a program reaches an interpreter

One parse of lypning's own front end grades every variant (`route.rs`
`verdicts`); a kind in `ONLY_CPYTHON_KINDS` skips the whole spectrum. A router
never sends a program to a variant smaller than itself, and a program
`lypning` answers `lypning-l` must answer. `lypning` runs in this process; a
rung with something after it is forked so its exit 90 is caught, and the last
rung is exec'd (`main.rs`; `docs/LYPNING.md` §5; `docs/VERIFICATION.md` §C4).
A wrong route costs one process spawn. It never costs a wrong answer, and that
is the only reason a mixture is allowed to guess at all.

`lypning conformance` grades every route — IDEAL, WASTED, LATE, UNSAFE,
NO-ENGINE (`routing.py`) — and UNSAFE is the only fatal grade. `accuracy` is a
census, not a cost model: it weights LATE and WASTED equally, and a LATE costs
a CPython spawn where a WASTED costs an in-process parse (measured 2026-09-04,
`CHANGELOG.md` #42). `lypning routes` is a write-only ledger of the refusals a
static route could not see: nothing on the routing path reads it, only
`engines.dispatch` writes it, and every count is a floor (§C5, §C11).

## Three design decisions

<div class="cards">
<div class="card"><h3>A refusal beats a wrong answer</h3>
<p>A subset runtime that silently disagrees with CPython is worse than no
runtime at all, because the agent that typed the line will not notice. It
<em>will</em> notice a refusal — the answer arrives from CPython one spawn
later. So MISMATCH is the gate and UNSUPPORTED is a coverage number.
<a href="readme.html#5-conformance-contract">The contract →</a></p></div>
<div class="card"><h3>The corpus is harvested, not invented</h3>
<p>Programs are captured from real agent sessions by a shim and a hook, and
the subset is sized from that distribution. <code>lypning status</code> prints
the count it loaded; quote that, with its date.
<a href="docs/capture.html">How capture works →</a></p></div>
<div class="card"><h3>Fuzzing finds what the corpus cannot</h3>
<p>The corpus only covers ground someone already walked. <code>lypning
fuzz</code> generates programs from the router's own tables
(<code>fuzz.py</code>), diffs them against CPython, shrinks a counterexample,
and exits 1 with its seed. <a href="changelog.html">Findings →</a></p></div>
</div>

## Install it, then check it

```bash
pip install lypning                # pure Python, zero runtime dependencies
lypning build --rust               # → `ok` per variant, only once the exit-90 contract holds on that binary
lypning install --dry-run          # → the settings diff for this repo; writes nothing
lypning install
lypning conformance --mixture both # → MISMATCH 0 · UNSAFE 0 · dispatchers agree N/N; exit 1 otherwise
lypning doctor                     # → 0 FAIL
lypning gate                       # → PASS — every variant inside its block budget
git status                         # → clean; the battery replays agents' edits behind a git-status net
```

`settings.json` is a file you own, so it is merged and never overwritten:
unrelated keys, unrelated hooks and their original order all survive, and the
original is backed up once. Uninstall is the exact inverse and never deletes
the capture log.
[The full walkthrough →](readme.html#3-integration-with-a-coding-session)

Run the battery in a worktree with its own `LYPNING_HOME`
(`docs/VERIFICATION.md` §C8). Every contract, its byte-exact expected output
from a dated run of record, and the test that pins it:
[docs/verification.html](docs/verification.html). `LYPNING_ROUTES=0` and
`LYPNING_CAPTURE=0` opt out of the two ledgers.

## Documentation

{{docgrid}}
