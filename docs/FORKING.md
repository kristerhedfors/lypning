# Forking — grow your own corpus, tune your own subset

The corpus is a recording of what agents typed, and it goes stale as models and
workloads change. This is the path from a fork to a spectrum tuned to your
programs — capture, harvest, gate, step — gated at every step because
invariant 1 (a silent disagreement is worse than a refusal) is your fork's too.

## 1. The loop — capture, harvest, gate, step

```
  CAPTURE  hooks + the python3 shim append every program to $LYPNING_LOG
  HARVEST  `harvest --export` (the Stop hook runs it) → tests/corpus/sightings/
           <session>.jsonl; `harvest` derives the corpus: redacted, deduped,
           content-addressed — it grows only with what was really typed
  GATE     the gates decide whether a step stands
  STEP     one measured change: a fix, a table row, a refusal — then repeat
```

The gates, in the order that catches the most:

```bash
lypning build --rust      # the exit-90 contract is asserted on the binary
lypning conformance       # MISMATCH must be 0
lypning doctor            # 0 FAIL
git status                # the corpus runs behind a net; check it anyway
lypning gate; lypning gate ~/.lypning/bin/lypning-l   # PASS twice — bare gates the core only
```

Run it as a standing loop (`/loop`, or a cron): harvest, run the gates, take
the top item of `lypning conformance --plan`, commit one measured step with the
numbers the tools printed. The point is that gathering is continuous and cheap
while stepping is deliberate and gated. It has caught model drift once
(`CHANGELOG.md`, 2026-08-30). Two traps it has paid for: `lypning bench` is
spawn-bound and cannot see a compute win — use `valgrind --tool=callgrind`
(`.claude/skills/hillclimb/SKILL.md` §3); and a remembered number is already
wrong (invariant 3).

## 2. From fork to tuned spectrum

```bash
lypning build --rust -v          # → `ok` per variant, `unsupported contract: held` in the log  (§C2)
lypning build --micropython      # optional: the oracle; absent, every path prints `not built` (§C12)
lypning install                  # merges hooks into .claude/settings.json; re-run → `all 3 hook entries already present` (§C10)
lypning shim install             # python3 on $PATH records what runs
lypning status                   # wired: the shim's PATH line; shadowed: `… is NOT on PATH — the shim will never run`
lypning harvest --dry-run        # → `harvest: N sighting(s) collected; nothing written (--dry-run).`
lypning harvest --export && lypning harvest   # sightings, then the corpus — prints the count it loaded
lypning conformance --mixture both   # MISMATCH 0 · dispatchers agree N/N  (§C3, §C4)
lypning conformance --plan       # the build order, ranked by ->cpy cost with blocks beside it
lypning routes --plan            # run-time refusals a static route cannot see (§C11)
```

Batteries run only in a worktree with its own `LYPNING_HOME`; `git status
--porcelain` is empty afterwards, and after a crash mid-way you check it
yourself — the net cannot undo a write outside the repository (§C8).

**A clean slate.** Truncate `src/lypning/assets/corpus/corpus.jsonl`
(`paths.CORPUS_FILE`), keep `seed-corpus.jsonl` (`paths.SEED_CORPUS_FILE`), read
the source split from `lypning corpus --stats`; the catalogue (§4) starts empty.

**Adding a rung.** Every Rust variant is the one crate under a `variant-*`
cargo feature (`assets/rust/Cargo.toml`); the common step is a `cap-*` feature
on `lypning-l`, not a new rung. A rung is one row in each of
`route.rs::SPECTRUM` and `CAPS`, `engines.SPECTRUM`, `VARIANT_CAPS`,
`env_var_for` and `parse_binary_name`, pinned by
`tests/test_engines.py::test_parse_binary_name_grows_with_the_spectrum`,
`tests/test_engines.py::test_env_var_for_spells_every_pin_by_rule` and
`tests/test_routing.py::test_the_spectrum_copy_in_engines_is_the_rust_table`;
qualify it with `lypning gate <bin>` (§C6) and `lypning conformance --mixture
both`, which must print `dispatchers agree N/N` (§C4). *Importable is not the
same as complete* — the tables are earned with `lypning conformance`, never
with `import x` returning 0. Test the wheel shape on purpose (§C13).

## 3. What transfers

**Universal — keep byte for byte.** Facts about Python or about running
captured programs; tests object if you touch them.

| what | where |
|---|---|
| The exit-90 refusal contract, spelled once and asserted on the built binary | `engines.refusal_line`, `main.rs::finish`, `build.check_refusal_contract` |
| The commit barrier: stdout/writes staged, discarded on refusal, so a retry is safe | `io.rs::COMMIT_THRESHOLD` (8 MiB) |
| Fall-through only on a genuine refusal — never on exit 90 alone, never on the program's own errors | `engines.dispatch`, `main.rs::dispatch` |
| CPython-exact semantics: identity-first element comparison (`elem_eq`), floor-division rounding, arity/keyword rejection, refuse-don't-guess (NaN identity, set order, interning) | `value.rs::elem_eq`, `ops.rs`, `builtins.rs` |
| The comparison environment: `PYTHONHASHSEED=0`, `LC_ALL=C.UTF-8`, identical decode rules on both sides, capture disabled inside the battery | `conformance._env_for` |
| the net — a temp cwd per entry per engine and a `git status` snapshot; not a sandbox | `conformance.py` (§C8) |
| The battery's verdict asymmetry: MISMATCH is always a bug, UNSUPPORTED never is | `conformance.classify` |
| Recursion guards expressed as refusals, so deep data can never SIGSEGV a host | `err.rs::MAX_NEST` (500), `eval.rs::MAX_DEPTH` (180), `parse.rs::MAX_PARSE_DEPTH` (64) |

**Workload-general — keep the mechanism, re-derive the contents.**
| mechanism | contents | re-derived by |
|---|---|---|
| what each variant serves | `route.rs::CAPS`, `modules.rs::MODULES` (one `cfg`-gated table per `cap-*` set), `engines.VARIANT_CAPS` | `conformance --plan` per variant; `tests/test_routing.py::test_no_module_in_the_table_routes_past_the_tier_that_claims_it` |
| kinds no Rust variant may answer, at any size | `route.rs::ONLY_CPYTHON_KINDS` = `engines.ONLY_CPYTHON_REFUSALS`, read by both dispatchers, held equal by test | `routes --plan` lists them as `NOT IMPLEMENTABLE`; a kind leaves the set only by being implemented exactly |
| the spectrum itself | `engines.SPECTRUM`, `engines.ORACLES`, `route.rs::SPECTRUM` (`ENGINE_ORDER` and `conformance.DEFAULT_ARMS` derive from them) | the tests in §2 |
| the nondeterminism screens | `conformance._RUN_SPECIFIC`, `_IMPLEMENTATION_DEFINED` | extend as your corpus surfaces shapes; never prune |
| constants sized to one-liners | `io.rs::COMMIT_THRESHOLD`, `conformance.LIBRARY_STEP_LIMIT` | re-tune only if your programs are heavier |

**Corpus-specific — re-measure or discard.** Every number (corpus counts,
coverage, routing grades, `docs/BENCH-LEDGER.md`, `docs/HILLCLIMB.md`); the
build order (`--plan` is a pure function of the loaded corpus); the catalogue's
entries (§4); the block budgets, a CheerpX fact (`docs/SANDBOX-PERFORMANCE.md`
§1).

## 4. The oracle catalogue

`.github/known-mismatches.json`, rendered by `lypning oracle`, records what a
second reimplementation got wrong, by identity (engine, entry, kind) in
families. It scores only the CI `micropython-conformance` job
(`.github/scripts/known-mismatches.py`); the local gate has no ledger path —
any MISMATCH exits 1 — and a stale entry fails the scorer too. The families are
the transferable half: they say what the reimplementation gets wrong. The
entries are yours: they say which of *your* programs hit it.

## 5. Keep the names

Engine strings, `LYPNING_*` variables and `$LYPNING_HOME` are load-bearing
(invariant 9); rename the project, not the plumbing. Check the redaction before
committing a sightings file (`docs/CAPTURE.md`, *Privacy*). A fork whose README
says "3× faster" without a date and a corpus count is claiming someone else's
measurement.

```bash
lypning build --rust && lypning conformance --mixture both && lypning doctor && lypning gate; echo $?
# → ok per variant · MISMATCH 0, dispatchers agree N/N · 0 FAIL · PASS · 0 — docs/VERIFICATION.md §C1–C8
```
