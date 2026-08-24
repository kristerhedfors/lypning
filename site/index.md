# lypning

<div class="hero">
<img class="hero-logo" src="docs/logo.svg" alt="" width="72" height="72">
<h1>lypning</h1>
<p class="tagline">Run a Python one-liner on the cheapest of three interpreters that can
actually run it — a subset written in Rust, a frozen-stdlib MicroPython, and CPython for
everything the first two refuse.</p>
<p class="cta">
<a class="btn" href="readme.html#2-install">Install</a>
<a class="btn ghost" href="docs/lypning.html">How it works</a>
<a class="btn ghost" href="https://github.com/kristerhedfors/lypning" rel="noopener">Source</a>
</p>
</div>

A classifier picks the tier per program by asking the Rust core's own parser, not by
guessing at the text. The subset is not sized to Python — it is sized to the one-liners a
coding agent actually types, which is a far narrower target and the only reason any of
this is affordable.

Every tier refuses the same way: **exit `90`, one line on stderr, nothing on stdout.** That
is what makes the three interchangeable, and what makes a wrong route cost one wasted
process spawn instead of a wrong answer.

<div class="tiers">
<div class="tier">
<h3>lypning</h3>
<div class="meta">Rust · 1,045,176 B · static musl · 0 file opens</div>
<p>A from-scratch Python subset, no crates. Stages its output and discards it on refusal,
so a refused run is observably a no-op.</p>
</div>
<div class="tier">
<h3>lypning-mp</h3>
<div class="meta">MicroPython · 294,788 B · static musl i386 · 0 file opens</div>
<p>A variant whose shim stdlib is compiled in. <code>import json</code> streams zero bytes
off the disk image — that is the whole design thesis.</p>
</div>
<div class="tier">
<h3>cpython</h3>
<div class="meta">system · 6,639,992 B · dynamic · 22 file opens</div>
<p>The reference, and the answer to everything the other two refuse. Correct always,
cheap never.</p>
</div>
</div>

## The measurement, first

Everything else is downstream of this table. Measured 2026-08-21 on a 4-CPU container
(Linux 6.18.44-fc-v21), **842 programs harvested, 763 timed**, min of 3, arms interleaved
per entry, startup min of 15.

| arm | ran | refused | shared total (500) | vs cpython | startup |
|---|---:|---:|---:|---:|---:|
| `cpython` | 763 | 0 | 6658.3 ms | 1.000x | 10.88 ms |
| `lypning` | 500 | 263 | 486.2 ms | 0.073x | 0.70 ms |
| `lypning-mp` | 714 | 49 | 407.7 ms | 0.061x | 0.61 ms |
| **mixture** | **763** | **0** | **685.7 ms** | **0.103x** | **0.58 ms** |

Over the whole corpus the mixture answers **763 of 763** at **0.340x** of CPython — a
**66.0% saving**, 8.9 seconds off a session's worth of one-liners, with nothing left
unanswered. The two subset arms look cheaper only because they refuse work, and a refusal
still costs its spawn.

<div class="note">
<p><strong>Re-measure. Do not cite.</strong> The upstream run of 2026-08-16 had
<code>lypning</code> ahead of <code>lypning-mp</code>; both re-runs here reversed it. The
mixture result held, the ranking of the two subset engines did not — it is a claim about
one corpus on one machine, not a property of the design. Every tool prints the corpus size
it loaded, every run, for exactly this reason.</p>
</div>

## How a program reaches an interpreter

The classifier is a static analysis over lypning's own front end, not a heuristic over the
program text, so *can tier 1 run this* is an exact answer costing one parse and no spawn.
The shares below are where it sent the 763 programs in the run above — **91.1% to the
cheapest tier that works, 97.5% right on the first try.**

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

A wrong route costs one process spawn. It never costs a wrong answer, and that is the only
reason a mixture is allowed to guess at all.
[The dispatcher, in detail →](docs/lypning.html#5-the-dispatcher-and-why-it-is-the-binary-itself)

## Three things that make it work

<div class="cards">
<div class="card">
<h3>A refusal beats a wrong answer</h3>
<p>A subset runtime that silently disagrees with CPython is worse than no runtime at all,
because the agent that typed the line will not notice. It <em>will</em> notice a refusal —
the answer arrives from CPython one spawn later. So MISMATCH is the gate and UNSUPPORTED is
a coverage number. <a href="readme.html#5-the-conformance-contract">The contract →</a></p>
</div>
<div class="card">
<h3>The corpus is harvested, not invented</h3>
<p>842 programs captured from real agent sessions by a shim and a hook that cost ~2 ms on a
non-Python command. The subset was sized from that distribution rather than from the
language reference. <a href="docs/capture.html">How capture works →</a></p>
</div>
<div class="card">
<h3>Fuzzing finds what the corpus cannot</h3>
<p>The corpus only covers ground someone already walked. <code>lypning fuzz</code> generates
programs from the subset lypning <em>claims</em> and diffs them against CPython — it has
found six silent bugs, including a float <code>repr</code> that broke ties away from zero
where CPython breaks them to even. <a href="changelog.html">All six →</a></p>
</div>
</div>

## Where it stands

Every row from a run in this tree on 2026-08-21, over the 842-program capture.
Re-run them rather than citing them.

| check | result |
|---|---|
| conformance, `lypning` | 500 MATCH · 263 UNSUPPORTED · <span class="verdict ok">0 MISMATCH</span> · 65.5% coverage |
| conformance, `lypning-mp` | 714 MATCH · 47 UNSUPPORTED · <span class="verdict bad">2 MISMATCH</span> · 93.6% coverage |
| conformance, mixture | <span class="verdict ok">763 / 763</span> · 100% |
| routing safety | 91.1% ideal · 97.5% correct first try · <span class="verdict warn">UNSAFE 1</span> |
| gate | <span class="verdict ok">PASS</span> on all four binaries · 0 file opens each |
| test suite | <span class="verdict ok">666 passing</span> · 58 skipped · Python 3.9–3.13 |

The two MISMATCHes are one known defect, tracked rather than waived: `lypning-mp` streams
stdout, so a program that prints before reaching an unsupported construct has already
committed those bytes when it exits 90. It is contained through the dispatcher and unsafe
if you exec the binary directly. `lypning conformance` fails while it stands.
[The reproduction and its consequences →](docs/lypning.html#the-barrier-is-lypnings-alone-lypning-mp-has-none)

## Hook it into a coding session

```bash
pip install lypning     # pure Python, zero runtime dependencies
lypning build           # compile the engines into ~/.lypning/bin
cd /path/to/your/repo
lypning install --dry-run   # see every file it would touch, and the settings diff
lypning install
```

`settings.json` is a file you own, so it is merged and never overwritten: unrelated keys,
unrelated hooks and their original order all survive, and the original is backed up once.
Uninstall is the exact inverse and never deletes the capture log.
[The full walkthrough →](readme.html#3-hook-it-into-a-coding-session)

## Documentation

<div class="docgrid">
<a class="doclink" href="readme.html"><strong>Overview</strong><span>What it is, install, and wiring it into a session.</span></a>
<a class="doclink" href="docs/lypning.html"><strong>Design</strong><span>The mixture, the classifier, and the commit barrier.</span></a>
<a class="doclink" href="docs/subset.html"><strong>The subset</strong><span>What the engines implement, and what they refuse.</span></a>
<a class="doclink" href="docs/cookbook.html"><strong>Cookbook</strong><span>Rewrites for constructs outside the subset — every recipe executed by the suite.</span></a>
<a class="doclink" href="docs/micropython.html"><strong>MicroPython tier</strong><span>The frozen-stdlib variant and the cost model behind it.</span></a>
<a class="doclink" href="docs/research.html"><strong>Research</strong><span>How the runtime was chosen, including what was measured and rejected.</span></a>
<a class="doclink" href="docs/capture.html"><strong>Capture</strong><span>The hooks and shim that grow the corpus.</span></a>
<a class="doclink" href="docs/prompting.html"><strong>Prompting</strong><span>Can an agent be asked into the subset? 884 generated programs, nine treatments.</span></a>
<a class="doclink" href="docs/embedding.html"><strong>Embedding</strong><span>The C ABI, the five hosts over it, and what a refusal means with no exit code.</span></a>
<a class="doclink" href="docs/bench-ledger.html"><strong>Bench ledger</strong><span>Append-only history, including the runs where the subset lost.</span></a>
<a class="doclink" href="docs/hillclimb.html"><strong>Hillclimb ledger</strong><span>Every improvement step, the four numbers it moved, and the ones that moved nothing.</span></a>
<a class="doclink" href="docs/sandbox-performance.html"><strong>Sandbox cost</strong><span>The measurements the whole project is downstream of.</span></a>
<a class="doclink" href="changelog.html"><strong>Changelog</strong><span>Every change that matters, back to before the project had this name.</span></a>
<a class="doclink" href="contributing.html"><strong>Working agreement</strong><span>The invariants an agent changing this repo must not break.</span></a>
</div>
