# Forking — grow your own corpus, tune your own subset

lypning is optimized for one thing: the small Python programs *coding agents
actually type*. The corpus that defines "actually" is a recording, not a
specification — and recordings go stale. A new model reaches for different
idioms; your agent works in a different domain; your programs are not our
programs. This document is the complete path from "forked the repo" to "a
mixture tuned to my workload", built around the one feature that makes it
work: **the loop**.

The promise this tree keeps, and your fork must keep, is CLAUDE.md invariant 1:
a tier that silently disagrees with CPython is worse than no tier at all. Every
step below is measured because of it.

## 1. The loop — capture, harvest, gate, step

Everything else in this document serves a four-beat loop:

```
      ┌────────────────────────────────────────────────────────────┐
      │                                                            │
      ▼                                                            │
  CAPTURE — hooks + python3 shim record every program your      STEP — take ONE
  agent runs, into ~/.lypning/invocations.jsonl and             measured change:
  tests/corpus/sightings/<session>.jsonl. Free after            a fix, a table
  `lypning install`; runs while you work on anything else.      row, a refusal.
      │                                                            ▲
      ▼                                                            │
  HARVEST — `lypning harvest` derives the corpus from every     GATE — the four
  sightings file: redacted, deduped, content-addressed.  ──────▶ gates decide if
  Your corpus grows only with what was really typed.            the step stands.
```

The gates, in the order that catches the most:

```bash
lypning build --rust      # the exit-90 contract is asserted on the binary
lypning conformance       # MISMATCH must be 0 (or ledgered by identity — §4)
lypning doctor            # 0 FAIL
git status                # the corpus runs behind a net; check it anyway
```

**Run it as a standing loop.** In a Claude Code session on your fork, the loop
is one prompt, repeated:

```
/loop  Harvest new sightings into the corpus (lypning harvest), run the four
       gates, and take the top item from `lypning conformance --plan` — one
       measured step, committed, with the numbers the tools printed this run.
```

`/loop` self-paces; a cron or CI schedule that runs `lypning harvest` +
`lypning conformance` and files the diff works the same way. The point is that
gathering is continuous and cheap while stepping is deliberate and gated.

### Why this is the feature, not a chore: model drift, measured

This tree's own history is the demonstration. In one session the capture
harness gathered **667 new programs** (corpus 2,239 → 2,906, counts printed by
`lypning harvest` on 2026-08-30) — including the session's *own* probe
programs. Running the gates over the grown corpus immediately surfaced five
tier-1 defects (dict-view set algebra, ignored encoding arguments, swallowed
unknown keywords among them), all fixed the same day, and re-derived which
refusal kinds must skip the middle tier. Nobody predicted those programs; a
model typed them, the loop caught them. When the model under your agent
changes, the same loop notices *for you*: the new model's idioms show up as
sightings, the plan re-ranks, and the tables stop matching until you re-derive
them.

Two traps the loop has already paid for, so you don't have to:

* **The corpus benchmark is spawn-bound.** `lypning bench` times whole runs, so
  it cannot see a compute win or loss; a change accepted on it alone is
  accepted on noise. For anything under ~20%, use instruction counts
  (`valgrind --tool=callgrind`) — exact, free, and immune to a noisy host.
* **Never quote a remembered number** (invariant 3). Every tool prints the
  count it loaded; quote that, from that run, with its date. The corpus grows
  every session, so yesterday's number is already wrong.

## 2. Forking, step by step

```bash
# 1. Fork and clone, then build the tier(s) you can:
lypning build --rust                  # tier 1 — needed for everything below
lypning build --micropython           # tier 2 — needs a 32-bit toolchain + network;
                                      # OPTIONAL: every path degrades to "not built"

# 2. Wire capture into the project(s) where your agent works:
lypning install                       # merges hooks into .claude/settings.json
                                      # (append-only, backed up, reversible)
lypning shim install                  # python3 on $PATH records what runs
lypning status                        # confirm both feeds are live

# 3. Work normally. Capture is passive. Then, deliberately:
lypning harvest --export              # publish this session's sightings
lypning harvest                       # derive the corpus (prints the count)

# 4. Point the loop at YOUR programs:
lypning conformance                   # where do the tiers disagree? (gate)
lypning conformance --plan            # what to build next, ranked by unblocked count
lypning perf                          # which construct is slow (compute-bound!)
lypning fuzz --iterations 10000       # generative coverage of what you claim to run
```

**Starting from a clean slate:** if your domain is far from ours, delete
`src/lypning/assets/corpus/corpus.jsonl`'s harvested entries and keep the seed
vectors (`tests/corpus/seed`); your first few sessions of capture become the
corpus. Keep the ledger empty until a measured mismatch earns an entry.

**Replacing a tier:** the middle tier is any binary honoring the contract —
exit 90, one `<engine>: unsupported: <kind>: <detail>` line on stderr, nothing
on stdout. Swap the build in `build.py`, re-derive every workload-general table
below, and let `tests/test_routing.py` and the battery tell you what the new
tier actually serves. *Importable is not the same as complete* — the tables are
earned with `lypning conformance`, never with `import x` returning 0.

## 3. What transfers: optimizations by generality

Everything in this tree sits in one of three buckets. The bucket tells you
what your fork does with it.

### Universal — correctness; keep byte for byte

These are facts about Python or about running untrusted captured programs.
They do not depend on whose corpus you hold. **A fork should never touch
them**, and most are pinned by tests that will object if you do.

| What | Where |
|---|---|
| The exit-90 refusal contract, spelled once and asserted on the built binary | `engines.py`, `main.rs::finish`, `build.py` |
| The commit barrier: stdout/writes staged, discarded on refusal, so a retry is safe | `io.rs` |
| Fall-through only on a genuine refusal — never on exit 90 alone, never on the program's own errors | `engines.dispatch`, `main.rs::dispatch` |
| CPython-exact semantics: identity-first element comparison (`elem_eq`), floor-division rounding, arity/keyword rejection, refuse-don't-guess (NaN identity, set order, interning) | `value.rs`, `ops.rs`, `builtins.rs` |
| The comparison environment: `PYTHONHASHSEED=0`, `LC_ALL=C.UTF-8`, identical decode rules on both sides, capture disabled inside the battery | `conformance._env_for` and siblings |
| The repository net and per-run sandboxes — captured programs edit repos; run them where that costs nothing | `conformance.py` |
| The battery's verdict asymmetry: MISMATCH is always a bug, UNSUPPORTED never is | `conformance.classify` |
| Recursion guards expressed as refusals, so deep data can never SIGSEGV a host | `err.rs::Nest`, `eval.rs` |

### Workload-general — keep the mechanism, re-derive the contents

The *shape* transfers to any fork; the *rows* are re-earned against your
corpus and your tier binaries. Each has a re-derivation tool.

| Mechanism (keep) | Contents (re-derive) | How |
|---|---|---|
| Capability table for a tier that cannot be asked | `MICROPYTHON_MODULES` in `route.rs` | `lypning conformance` + the `test_routing.py` warning about modules your corpus uses |
| Escalation table: refusal kinds that skip the middle tier because it answers them *wrongly* | `ONLY_CPYTHON_KINDS` (route.rs) = `ONLY_CPYTHON_REFUSALS` (engines.py), held equal by a test, read by **both** dispatchers | run each kind's construct on your tier binary; escalate only kinds it never gets right — a *mixed* kind is split in the engine instead |
| Construct-level static markers vetoing the middle tier from the source | `MICROPYTHON_UNSAFE` / `_KWARGS` | measure each entry's spawn cost on your corpus before adding it (construct-level, never kind-level) |
| The tier ladder itself | `ENGINE_ORDER`, `DEFAULT_ARMS` | declare your engines; every path degrades to "not built" |
| The nondeterminism screens (run-specific, interpreter-specific) | pattern lists in `conformance.py` | extend as your corpus surfaces new shapes; never prune |
| Tunable constants sized to one-liners | `COMMIT_THRESHOLD` (8 MiB), `LIBRARY_STEP_LIMIT`, recursion depths | re-tune only if your programs are heavier; the mechanisms stand |

### Corpus-specific — re-measure or discard

Valid only for the corpus and binaries measured in this tree. Your fork
regenerates all of it; quoting any of it about *your* workload is invariant 3's
exact failure.

* **Every number**: corpus counts, coverage percentages, the routing table
  (IDEAL/WASTED/LATE/UNSAFE), spawn costs quoted in comments, everything in
  `docs/BENCH-LEDGER.md` and `docs/HILLCLIMB.md`.
* **The build order**: `lypning conformance --plan` output is a pure function
  of the loaded corpus. Re-run it; it is the loop's steering wheel.
* **The accepted-mismatch ledger**: `.github/known-mismatches.json` names, by
  identity and family, the defects *this* corpus surfaces in *this* mp build.
  Yours starts from the families (they describe the tier) and earns its own
  entries (they describe your programs hitting them).
* **Which optimizations were worth their bytes**: the 8-block ceiling is a
  CheerpX deployment fact; measure your own target's step function or ignore
  it.

## 4. The ledger: how a fork stays honest while red

`lypning conformance` is absolute — any MISMATCH exits 1. When the middle tier
has defects you cannot fix (it is a third-party binary), the ledger lets ONE CI
job stay red for exactly the documented defects without swallowing new ones:
each entry is accepted **by identity** (engine, program, kind) and grouped into
a **family** (the underlying defect). A mismatch not in the ledger fails the
scorer; a ledger entry that stops reproducing also fails it — good news that
still requires deleting the line. See `.github/scripts/known-mismatches.py`.

The families are the transferable half: they say what the tier gets wrong.
The entries are yours: they say which of *your* programs hit it.

## 5. Publishing your fork's identity

Keep the names (`invariant 9`): engine strings, `LYPNING_*` env vars, and the
`~/.lypning` state dir are load-bearing across the hooks, the shim, the tests
and the CLI. Rename the *project*, not the plumbing, or rename both completely
and let the test suite walk you through every place the old name was
load-bearing.

Ship your changelog the way this tree does: one entry per change, numbers
quoted from the run that printed them, with the date. A fork whose README
says "3× faster" without a date and a corpus count is claiming someone else's
measurement.
