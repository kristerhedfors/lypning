# The stdlib units — the operator's note

These files are never imported. The engines have no local imports and no `exec`
(measured 2026-09-16: `lypning-l main.py` on `import mylib` exits 90 with
`lypning-l: unsupported: module: import mylib`), so a unit exists to be copied
into the program that needs it. Why that makes this a corpus rather than a
package, what the label means, what the workflow does and what the subset costs
each unit's API: `../STDLIB.md`.

## The format, in one paragraph

One file is one unit under `units/`, and the whole file is a valid,
self-contained, deterministic program: a module docstring naming the gap and
every accepted divergence from CPython, then `# fills:` (comma-separated dotted
CPython names, required, non-empty) and `# reference:` (one importable CPython
module, or `-` for a language gap), then the helper definitions a model will
inline, then the line `# --- cases ---` exactly once, then nothing but `print()`
statements. Above the separator: definitions only, no top-level side effects.
Below it: output that is byte-identical on every run on every machine — no
clocks, no pids, no addresses, no unseeded random, no set iteration order, no
filesystem order, no temp paths. Helper names match CPython's where they can,
private helpers start with `_`, and the subset means no classes, no generators,
no decorators, no `nonlocal`, no recursion past 180 frames and no exception but
`ValueError("message")`.

## Adding one by hand

1. Pick a surface. `targets.json` lists the ones worth filling, each with the
   census that ranked it and the date that census was taken. A target whose
   `# fills:` names are all taken by a committed unit is dropped by
   `stdlib-plan` automatically, so the file is a description of the surface,
   not a to-do list to maintain.
2. Check it is not a closed kind. `lypning.engines.ONLY_CPYTHON_REFUSALS`, and
   `closed_kinds` in `targets.json` restates it with a reason for each. A
   pure-Python `math.log` or a set-ordering helper passes its own cases and
   returns a silent wrong answer; that is the one failure this corpus cannot
   see for itself, so the gate rejects the unit rather than trusting the author.
3. Write `units/<name>.py` in the format above.
4. Verify it — the three commands below, plus the differential.
5. Re-run the writer and commit `../data/stdlib/stdlib.jsonl` with it. That
   file is the verified rows for this tree — committed so the corpus is
   readable and mergeable without a Rust toolchain — and a unit added without
   it is a unit the corpus does not have. It is the only row file in the tree:
   everything the workflow produces stays an artifact.

## The three commands

```bash
/usr/bin/python3.11          training/stdlib/units/<name>.py
/root/.lypning/bin/lypning   training/stdlib/units/<name>.py
/root/.lypning/bin/lypning-l training/stdlib/units/<name>.py
```

CPython must exit 0 — it is the oracle, and a unit it rejects is never a row.
At least one engine must exit 0 with **byte-identical** stdout; that engine,
cheapest first, is the label. An engine that exits 90 with one
`<engine>: unsupported: <kind>: <detail>` line on stderr refused cleanly, which
is coverage and not a defect. An engine that exits 0 with different bytes is a
MISMATCH: the unit is wrong, and no relabelling fixes it (invariant 1).

Then the differential, which is not optional: write a scratch program that
imports the real module named by `# reference:`, computes the same values your
cases compute and prints them the same way, and `diff` it against your unit's
CPython output. Byte-identical, or the divergence goes in the docstring.
Reasoning about what CPython does is how silent divergence ships.

## Verifying and labelling the whole tree

```bash
cd /home/user/lypning
PYTHONPATH=src:training python3 -m pipeline.cli stdlib-verify \
  --units training/stdlib/units \
  --out training/data/stdlib/stdlib.jsonl --drops /tmp/drops.jsonl \
  --first-seen 2026-09-16 --producer authored --strict \
  --cpython /usr/bin/python3.11 \
  --engine lypning=/root/.lypning/bin/lypning \
  --engine lypning-l=/root/.lypning/bin/lypning-l
```

`--strict` fails on any drop, which is what a check over committed units wants:
these are the corpus's existing content, so a drop here is a defect and not a
candidate. `--first-seen` is passed in and never taken from the clock, so
re-verifying rewrites nothing. Every engine is pinned explicitly, because a
label taken without the cheaper engine would understate what the unit needs —
the verifier rejects that rather than labelling optimistically.

`--out` is the committed corpus on purpose: that is the file the tree carries,
and running the writer anywhere else leaves it stale. Two consecutive runs on
2026-09-16 over these 34 units wrote byte-identical files (`cmp` silent,
502,083 B), which is what lets `git diff --exit-code` stay quiet.

The pytest gate over the same tree, which adds the CPython differential, the
determinism check and the closed-kind gate:

```bash
uv run --with pytest pytest training/tests/test_stdlib.py -q
```
