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

- **`lypning conformance` ends at MISMATCH 2, both on `lypning-mp`**, both the
  same defect: MicroPython streams stdout, so a program that prints before
  reaching an unsupported construct has already committed those bytes when it
  exits 90. The Rust core stages output and discards it on refusal; the
  MicroPython tier cannot, and the dispatcher covers for it. Reproduction in
  `docs/LYPNING.md` §6. Blocking again once that tier grows a commit barrier.
- **1 UNSAFE route**, which is the same defect from the other side: `hashlib`
  is in the classifier's table, so one program routes to `lypning-mp`, prints
  147 bytes and then refuses. Narrowing the table to dodge it would cost every
  other `hashlib` program a tier and hide the defect. `tests/test_routing.py`
  pins it as the only shape an UNSAFE route takes here.
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

[#1]: https://github.com/kristerhedfors/lypning/pull/1
[#2]: https://github.com/kristerhedfors/lypning/pull/2
[#3]: https://github.com/kristerhedfors/lypning/pull/3
[#4]: https://github.com/kristerhedfors/lypning/pull/4
[#5]: https://github.com/kristerhedfors/lypning/pull/5
[#6]: https://github.com/kristerhedfors/lypning/pull/6
[#7]: https://github.com/kristerhedfors/lypning/pull/7
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
