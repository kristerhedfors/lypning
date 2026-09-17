# The stdlib corpus: code the engines run, written to be inlined

## 1. Why this is a corpus and not a package

A unit can only ever be **inlined**, because the engines have no local imports
and no `exec`. Measured 2026-09-16 against the binaries built this session,
with `main.py` containing `import mylib` and a `mylib.py` beside it:

```
$ /root/.lypning/bin/lypning-l main.py
lypning-l: unsupported: module: import mylib          # exit 90
$ /root/.lypning/bin/lypning-l ex.py                  # ex.py is exec("x = 1")
lypning-l: unsupported: builtin: exec                 # exit 90
```

That single fact decides the shape of everything below. Nothing imports
`training/stdlib/units/*.py`, nothing ever will, and no version of this work
ends in a package on disk that a program under lypning could load. What a
program under lypning *can* have is the function it needs, written out in its
own source — so the artifact is training data: reference material a model
reproduces inline, each file tagged with the cheapest engine that actually runs
what it reproduces.

The corpus fills the gap between what CPython offers and what the Rust engine
serves. `import statistics` refuses; a program that needs a median still needs
a median. A unit is that median, written in the subset, checked against CPython
on every run, and labelled.

Three homes, as `../CLAUDE.md` divides them: CLAUDE.md states the rules this
obeys (1, 3, 8, 9), **this file states the mechanism**, and
`training/tests/test_stdlib.py` is the check — it runs every committed unit
against live CPython and against both engines.

## 2. What a unit is

One file is one unit, and the whole file is a valid, self-contained,
deterministic program. It has to be: the only way to check a unit is to run it,
and the only definition of "right" is the bytes CPython prints.

The structure, in this order:

```python
"""One line naming the gap this fills.

Prose: which CPython surface, what is covered, what deliberately is not, and
any divergence from CPython that is accepted and why.
"""
# fills: statistics.mode
# reference: statistics

<helper definitions — the inlinable part>

# --- cases ---
<statements that print deterministic values>
```

`training/stdlib/units/statistics_mode.py` is a real one. Its header is the two
lines above; above the separator it defines `_tally`, `mode` and `_error_of`;
below it, 27 `print()` statements walk the ties, the `1`/`1.0`/`True`
collisions and the empty-input error:

```python
def mode(data):
    """Return the most common value, the earliest-seen one when counts tie."""
    counts = _tally(data)
    ...
    if not seen:
        raise ValueError("no mode for empty data")
    return best


# --- cases ---
print(repr(mode([1, 1, 2, 3, 3, 3, 3, 4])))
print(repr(mode(["red", "red", "green", "blue", "blue"])))
print(repr(mode([1, 1.0, 1.0, 2])))
```

The rules the parser enforces (`pipeline.stdlib.parse_unit`, `UnitError` on any
breach):

- `# fills:` is required and non-empty — comma-separated dotted CPython names.
- `# reference:` is one importable CPython module, or `-` when the unit fills a
  language gap rather than a module.
- `# --- cases ---` appears exactly once. Two separators would give two answers
  to "what does a model reproduce"; none leaves nothing to check.
- Above it: definitions only, no top-level side effects. Below it: prints only.
- The output must be byte-identical on every run on every machine — no clocks,
  no pids, no addresses, no unseeded random, no set iteration order, no
  filesystem order, no temp paths.
- Helper names match CPython's where they can (`fill`, `wrap`, `dedent`),
  private helpers start with `_`.

Determinism is not style. A unit whose second run differs is a unit whose label
is a coin toss, and the corpus rows are compared byte for byte across runs.

## 3. What the label means, and how it is produced

The label is the cheapest engine that ran the unit and printed exactly what
CPython printed. `pipeline.stdlib.label_unit` produces it in this order, and the
order is the product:

1. **CPython first, live, every time.** A non-zero exit means the unit is
   broken and it is never a row. The oracle is never overruled, and reasoning
   about what CPython does is how silent divergence ships.
2. Each engine in `lypning.engines.ENGINE_ORDER`, cheapest first.
3. The first engine that exits 0 with CPython's stdout **bytes** and the same
   stderr shape is `requires`.
4. An engine that exits 0 with **different** bytes — or that crashes on a
   program CPython ran — is a MISMATCH: fatal, the unit is rejected, the reason
   recorded. Invariant 1, read from the corpus side: never relabel around it.
5. An engine that exits 90 with the contract line refused cleanly. That is
   coverage, not failure; labelling moves to the next engine.
6. Nothing ran it: `requires` is `cpython`, a recorded gap and not a row.

The comparison is over bytes on both sides, never over decoded text: decoding
applies universal-newline translation, which would hide a CRLF divergence on
exactly the axis an engine is most likely to get wrong.

Over the 34 committed units, on 2026-09-17, with

```
PYTHONPATH=src:training python3 -m pipeline.cli stdlib-verify \
  --units training/stdlib/units --out rows.jsonl --drops drops.jsonl \
  --first-seen 2026-09-16 --producer authored \
  --cpython /usr/bin/python3.11 \
  --engine lypning=/root/.lypning/bin/lypning \
  --engine lypning-l=/root/.lypning/bin/lypning-l
```

the labels came out 25 `lypning`, 9 `lypning-l`, 0 drops, across 120 distinct
CPython names — 121 `# fills:` entries, because `struct.calcsize` is named by
both `struct_pack` and `struct_unpack`. Re-run it before quoting any of that
(invariant 3): the units grow, and a unit edited after this run can move its own
label. That is not a caution in the abstract — the same command on 2026-09-16
printed 26 and 8, and `struct_pack` moved to `lypning-l` the day after, when it
gained cases above 2**63 - 1 and the value going in became a bigint.

### Where the verified rows live

`training/data/stdlib/stdlib.jsonl` — the rows above, committed, with
`--out` pointed at that path and `--strict` on. It is committed rather than
rebuilt on demand for one reason: labelling needs both Rust binaries, and a
reader, a reviewer or the assemble stage should be able to hold the corpus
without a toolchain. `first_seen` is passed in and the serialisation is fixed,
so re-running the writer over an unchanged tree rewrites nothing — which is what
the `Tests must not rewrite recorded datasets or results` step of
`.github/workflows/training.yml` checks, with `git diff --exit-code` straight
after the training suite.

That makes it the one artifact here that can go stale quietly, so
`training/tests/test_stdlib.py` §9 reads it: the names and `source_sha256` of
its rows must match the units on disk (engine-free, so it runs on every leg),
and with the binaries present its bytes must equal what a verify pass writes
today. Edit a unit, re-run the writer, commit both.

### The static prediction, and why it may disagree

Every row also records `requires_static` — what `lypning route` predicted from
the source alone — and `route_agrees`. Both are kept because they answer
different questions: the dynamic label says what *will run this file*, while the
static one says what the dispatcher will *choose* for it, and a dispatcher that
chooses wrong pays a refusal and a second spawn.

A disagreement is legitimate, not a bug, because the static walk cannot see a
runtime-only refusal. Measured 2026-09-16 on `print(2**70)`:

```
$ /root/.lypning/bin/lypning route big.py
lypning
$ /root/.lypning/bin/lypning big.py
lypning: unsupported: bigint: integer result beyond 64-bit range (Python would use a bignum)   # exit 90
$ /root/.lypning/bin/lypning-l big.py
1180591620717411303424
```

Nothing in the text of `print(2**70)` names a module or a feature; the integer
only leaves 64-bit range when it is computed. Three of the 34 units in the
verify run above are exactly this shape — `statistics_mean`,
`statistics_variance` and `struct_unpack` each route `lypning` statically and
refuse `bigint` at runtime, so each is labelled `lypning-l` with
`route_agrees: false`.

What is *not* legitimate is a disagreement in the other direction, or a MISMATCH
dressed up as one. The router never routing below itself is invariant 10's
business and is checked by `lypning conformance --mixture both`, not here.

## 4. The closed list, and the gate over it

`lypning.engines.ONLY_CPYTHON_REFUSALS` is the one thing this corpus must never
contain. It is not a backlog: each kind in it — `math`, `json`, `random`,
`set-order`, `encoding`, `nan-order`, `percent-format`, `repr-unicode` and the
rest — is a claim that no reimplementation short of CPython gets the construct
right, and the engine's job on those is to keep refusing.

**This is the corpus's single most dangerous failure mode, because it is the
only one that fails quietly.** Every other defect here is loud: CPython runs
every case, and one wrong byte rejects the unit. A unit over a closed kind
passes its cases and is still wrong. A pure-Python `math.log` agrees with
CPython on the twelve values its cases print and diverges in the last place on
the thirteenth. A set-ordering helper agrees under this machine's hash seed. A
hand-rolled JSON float repr agrees until it meets `0.1 + 0.2`. A model that
reproduced one of those inline would ship a silent wrong answer wearing the
shape of a feature — which is what invariant 1 exists to prevent.

So it is a gate, not a prompt instruction. `pipeline.stdlib.closed_kind_violation`
rejects the unit with drop reason `closed-kind`, on two checks:

- **Measured** — the refusal a naive `import <reference>` actually got from the
  cheapest engine. If the engine's own word for the gap is a closed kind, the
  unit fills a closed kind whatever its header says.
- **Declared** — the leading segment of each `# fills:` name, plus `<seg>-order`
  and `<seg>-method`, against the same set. `math.log` names `math`;
  `set.union` reaches `set-order` and `set-method`.

`dict-view` is deliberately out of reach of the suffix rule: `dict.fromkeys` is
a legitimate gap the engine refuses as `dict-method`, and a gate that rejected
it would cost a good unit to catch nothing. A dict-view unit is caught the
measured way or by its cases; that is the honest limit of the declared half.

`stdlib/targets.json` restates the set with the reason for each, so a reader
does not hold two files open, and `stdlib_generate.load_targets` checks that
restatement against the engine's own set and refuses to plan if they differ — a
kind the engine added and the file did not name would become a target nobody
excluded. When they diverge, fix the file, never the engine's set.

## 5. The stages

`.github/workflows/stdlib-corpus.yml` runs five stages on `workflow_dispatch`
only. No `push`, no `pull_request`, no `schedule`: the provider secret must
never be reachable from a fork PR, and a corpus that regenerated itself on every
push would spend money nobody authorised. `NTX_API_KEY` is mapped from the
`BERGET_API_KEY` secret at **step** level, never job-wide and never
workflow-wide, on the **three** steps that call the provider: *Resolve the
newest active GLM and plan the batches* in `plan`, *Generate one batch* in
`generate`, and *One bounded repair round* in `repair`. `grep -n
'BERGET_API_KEY' .github/workflows/stdlib-corpus.yml` returns exactly those
three lines (101, 203, 352 on 2026-09-17); every other step in the workflow,
including all four `dry_run` steps and every verify and assemble step, runs
without the secret in its environment at all. If that grep ever returns a
fourth line, either the count here is stale or a step gained the secret without
anyone saying so.

1. **plan** — resolves the generation model against the provider's live
   `/v1/models`, drops every target in `stdlib/targets.json` whose `# fills:`
   names are already taken by a committed unit, prices the worst case against
   the token and cost ceilings in the workflow's `env:`, and emits `plan.json`.
   Nothing is generated and nothing is spent. *Uploads:* `stdlib-plan`.
2. **generate** — one job per batch, writing candidate unit files plus a
   `draws.jsonl` that records **every** raw completion, including the ones that
   produced nothing: a rollout that contributed no candidate is still the only
   measurement of whether the prompt is working. Uploaded `if: always()`, so a
   batch that spent tokens and failed still hands over the ledger.
   *Uploads:* `stdlib-candidates-<batch>`.
3. **verify** — builds both engines in the job rather than assuming them, then
   labels §3's way. The committed seed units are verified first with `--strict`,
   because they are the corpus's existing content and a defect in one is a
   defect, not a candidate. Generated candidates are verified without `--strict`:
   a candidate no engine runs is a recorded gap the repair stage reads. A
   MISMATCH exits 1 either way. *Uploads:* `stdlib-verified` — rows and drops.
4. **repair** — exactly one round. Each rejected candidate is shown the refusal
   line the engine actually wrote, re-measured against the engines built in this
   job rather than paraphrased from the drop, and regenerated once. There is no
   second round, because a loop over the verifier is a search that fits units to
   the checks instead of to CPython; the next attempt is a new dispatch a human
   authorises. *Uploads:* `stdlib-repaired`.
5. **assemble** — merges the committed `training/data/stdlib/stdlib.jsonl`
   first, then this run's re-verified seed rows, then the generated ones;
   de-duplicates by id, sorts by name, renders the report into the job summary.
   Seed rows go first because the first writer wins: a committed unit outranks
   a regenerated one carrying the same id. The committed file outranks even the
   fresh seed rows, and on one field only — the verify job stamps every row it
   writes with the dispatch date, and a `first_seen` that moved on every
   dispatch would record the dispatch rather than the unit. The re-verification
   is still the check; this is only about which copy of an identical row the
   corpus keeps. *Uploads:* `stdlib-corpus`.

**It does not commit.** `permissions: contents: read`; the assembled corpus
leaves as an artifact and a human reads the report and merges it. Generated
content that wrote itself into the tree would be content nobody read, and the
rows carry a `producer` field precisely so provenance survives that review.

The `dry_run` input runs all five stages against a deterministic offline
provider: no secret, no network to the provider, no spend. It asserts an
outcome rather than exiting 0 — the canned unit must reach the corpus, be
labelled by an engine that actually ran it, and be stamped with the fake
producer so it can never be mistaken for content.

## 6. The limits, and what they cost a reader

A unit's API differs from CPython's wherever CPython's is class-shaped, and a
reader should know that before trusting one. The subset has no classes, so
there is nothing to hang a method on; what CPython spells `d.isoformat()` a unit
spells `date_isoformat(d)` over a tuple or a dict.

Measured 2026-09-16 and re-run unchanged 2026-09-17, each refusing with exit 90
on `lypning-l`. The refusal column is the `<kind>: <detail>` half of the
contract line, quoted whole — the `raise ValueError()` detail is long because
the engine explains what it cannot distinguish, and truncating it here would
make this table disagree with the bytes:

| written                     | refusal                                                                                        |
| --------------------------- | ---------------------------------------------------------------------------------------------- |
| `class C: pass`             | `class: class definition`                                                                       |
| `class E(ValueError): pass` | `class: class definition`                                                                       |
| `def g(): yield 1`          | `generator: yield expression`                                                                   |
| `import functools`          | `module: import functools`                                                                      |
| `raise ValueError()`        | `exception: ValueError() with no arguments, which this value cannot tell from ValueError("")`   |
| recursion at depth 181      | `recursion: call depth beyond 180`                                                              |

(`f(178)` in the same shape printed `0` and exited 0.) Decorators, `nonlocal`,
`async`, the walrus and keyword-only parameters refuse the same way.

What that means for how units are written:

- **No classes.** State is a dict or a list passed by reference; a constructor
  becomes `thing_new(...)` returning a dict and a method becomes
  `thing_verb(st, ...)`.
- **No generators.** A unit returns a materialised list. `itertools_chain`
  covers what `chain` computes, not its laziness, and an infinite iterator has
  no unit at all.
- **No `nonlocal`.** A closure cell is a one-element list.
- **Recursion caps at 180 frames.** Every tree walk and divide-and-conquer
  carries an explicit stack, which is why some units are longer than the CPython
  source they fill.
- **No custom exceptions**, and most builtin exception names are not even bound.
  A unit raises `ValueError("message")` and nothing else — one argument,
  non-empty. So `statistics_mode` raises `ValueError("no mode for empty data")`
  where CPython raises `StatisticsError` with that same message; the message
  matches, the type does not.
- **No decorators, no `@property`, no dunder protocol.** A unit cannot make its
  result work with `len()`, `in` or `[]` the way a CPython object does.

Every such divergence is written into the unit's own docstring, which is the
one place it belongs: a reader inlining the function has the docstring in front
of them and does not have this file open. `training/tests/test_stdlib.py` runs
the differential that keeps those docstrings honest — it rebinds each unit's
helpers against the real CPython module and fails when a declared divergence has
quietly closed, as well as when an undeclared one opens.

Adding or checking a unit by hand: `stdlib/README.md`.
