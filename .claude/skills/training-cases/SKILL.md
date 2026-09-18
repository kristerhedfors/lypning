---
name: training-cases
description: Author schema-3 training cases for a lypning training bank — task text, reference program, tests, population and review fields — with a differential oracle that admits a case only when two independently written implementations agree under execution. Produces cases.jsonl, then train.jsonl / eval2.jsonl split by family. TRIGGER on requests like "author training cases", "build a new task bank", "make more training data", "extend the bank", "we need a pilot dataset", "add families to the corpus", or any request whose blocker is "a real adapter stage needs >=1,000 train cases". SKIP for preparing or verifying an existing bank (use `training-bundle`), for the capture corpus under `tests/corpus/` (that is the engine's conformance corpus, not training data), and for anything that would train on eval-2.
---

# Authoring a lypning training bank

A bank is JSONL. Each line is one **case**: an English task, one reference
program, several tests, a population, and review assertions. This skill is about
making cases that are worth training on — the shape is the easy part, and
`training-bundle` checks it.

Work from `training/data/bank_v2/generate.py`, which is a working generator with
69 families; copying its structure is faster than starting from the schema.

## The four rules that actually bite

**1. The oracle must not be a transcript of your own answer.**
`ORCHESTRATION.md` step 4 wants expectations "from the task, not by copying
either Qwen's or Codex's output", via "independently calculated examples and
appropriate property/differential checks". So write **two** implementations
against the same English sentence and never against each other:

- a `spec` that computes the expected stdout plainly, optimised for being
  obviously right;
- a `reference` in the plain-builtin style the engine serves.

Emit the case only when the reference, executed under CPython, reproduces the
spec byte for byte on every test. A disagreement means one of them is wrong —
drop the case and count it. Never edit one to match the other.

**2. `task` is the prompt, so two cases with the same task are one case.**
`training-prepare` refuses duplicate prompts, and it is right to. Parameterise
so the parameter appears *in the English task and changes the answer* — a prefix
window ("considering only the first 7"), a modulus, a threshold. Do not
paraphrase: `ORCHESTRATION.md` is explicit that paraphrases add nothing.

**3. Cases are not independence.** The splitter groups by `family` /
`source_group`, so parameterised cases inside one family are near-twins. A
1,900-case bank over 59 families has **59** independent components. Quote both
numbers, always, and never let a family straddle the train and eval-2 banks.

**4. Let the engine decide `population`, and check before you label.**
`coverage` references must answer natively on every input; `fallback-control`
references must be correct *and* refuse on every input. Your belief about which
modules refuse is probably stale — probe the built binary:

```bash
E=work/round-02/engine-home/bin/lypning-l
"$E" -c "import statistics; print(statistics.median([1,2,3]))"; echo "exit $?"
```

Exit 0 is native; exit 90 is a refusal. Measured 2026-09-18 on a host build,
`math` / `json` / `collections` / `re` / `random` were **served**, while
`functools` / `itertools` / `statistics` / `decimal` / `heapq` / `bisect` /
`operator` / `copy` / `string` / `array` / `fractions` / `textwrap` /
`datetime` / `unicodedata` refused. Re-probe rather than trusting that list —
capabilities get added, and a stale label fails preparation with
`reference native/fallback label is stale`.

Keep real controls. A bank with no fallback-controls cannot distinguish "learned
to serve more natively" from "learned to avoid every import", and the second is
a way to score well while getting worse.

## Case shape

```json
{
  "case_id": "int-sum-k07",
  "family": "int-sum",
  "source_group": "int-sum",
  "capabilities": ["int-reduce"],
  "task": "Read whitespace-separated integers from stdin. Considering only the first 7 of them (or all of them if there are fewer), print their sum. Print 0 if none remain.",
  "reference": "import sys\nns = [int(x) for x in sys.stdin.read().split()][:7]\nprint(sum(ns))",
  "tests": [{"stdin": "1 2 3", "stdout": "6\n"}, "... >= 3 tests, >= 2 distinct stdout"],
  "population": "coverage",
  "provenance": "who authored it, when, and how the parameters were chosen",
  "review": {"origin": "authored", "evidence_ids": [], "reviewer": "...",
             "intent_basis": "...", "oracle_basis": "...",
             "rights_basis": "...", "independence_basis": "..."}
}
```

A test may carry `stdin`, `argv` and `files` (a dict of filename to UTF-8 body).
The verifier covers deterministic UTF-8 stdout, empty stderr and exit zero — do
not author file-mutating or binary-output tasks, they have no contract yet.

`review` strings are **human assertions**. If an agent authored and checked the
bank, say so in `reviewer` in those words rather than implying a human did; the
ledger flags such banks rather than hiding them (row T2, and row T6 for this one).

## Floors you will hit, in the order you hit them

| floor | value | where |
|---|---|---|
| tests per case | >= 3, with >= 2 distinct stdout | `training-prepare` |
| duplicate prompts | none | `training-prepare` |
| population labels | must match what the engine does | `training-prepare` |
| families | >= 18, both populations in every split | schema-3 admission |
| **train cases** | **>= 1,000 in the prepared bundle's train split** | `train_verified.preflight` |
| supervised tokens | >= 50,000 scheduled | `train_verified.run` (not `--plan`) |

The train floor is on the **bundle's train split**, not the bank. Preparation
put ~68% of the bank in train on 2026-09-18, so a bank of ~1,500 is the
practical minimum and more is safer.

## Running it

```bash
work/round-02/venv/bin/python training/data/bank_v2/generate.py    # differential admission
work/round-02/venv/bin/python training/data/bank_v2/split_bank.py  # family-disjoint split
```

`generate.py` writes `cases.jsonl` and `dropped.jsonl`; read the dropped file,
it is where the oracle earns its keep. `split_bank.py` writes `train.jsonl` and
`eval2.jsonl` and exits non-zero if a family or task appears on both sides.

Then check the benchmark is not contaminated — this is a gate, not a report:

```bash
PYTHONPATH=src:training python -m pipeline.cli eval2-leaks \
  training/data/bank_v2/eval2.jsonl training/data/bank_v2/train.jsonl
```

It exits 1 on any pair. Its rules include **identical expected stdout**, which
catches families that compute different things but print the same answer — a
real near-duplicate even though the tasks read differently. Fix it by changing
what one family prints, or by moving the family wholly to one side. Do not pass
`--allow` to make a launch proceed.

Hand the result to `training-bundle`.
