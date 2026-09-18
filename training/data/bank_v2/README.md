# bank v2 — an authored task bank

`train.jsonl` (1,971 cases / 60 families) and `eval2.jsonl` (315 cases / 9
families) are a family-disjoint split of one authored bank. `generate.py` writes
the bank, `split_bank.py` splits it, and `cases.jsonl` is the unsplit
intermediate and is gitignored.

**Read `.claude/skills/training-cases` before changing this.** The two things
that are easy to break:

- **The oracle is differential.** Every family has a `spec` and a `reference`
  written against the same English sentence and never against each other, and a
  case is emitted only if they agree byte for byte under execution. 129 cases
  were dropped on 2026-09-18, all for tests that shared one expected output and
  so could not discriminate; `dropped.jsonl` records them.
- **Cases are not independence.** 2,286 cases over 69 families is **69**
  independent components. The parameter inside a family changes the answer but
  not the idea, so a family must never straddle the two banks.

`population` is decided by executing each reference through the pinned engine,
not by the label: on 2026-09-18 the built `lypning-l` served `math`, `json`,
`collections`, `re` and `random`, and refused `functools`, `itertools`,
`statistics`, `decimal`, `heapq`, `bisect`, `operator`, `copy`, `string`,
`array`, `fractions` and `textwrap`. Re-probe before trusting that.

The `review` block on every case says `reviewer: Codex orchestrator session,
2026-09-18 (an agent, not a human reviewer)`. That is deliberate and must not be
softened — ledger row T2 records the same flag on the previous bank, and a round
trained on this has to state it rather than imply a human review happened.

Verified 2026-09-18 against `6044886` + this branch:

| check | result |
|---|---|
| `eval2-leaks eval2.jsonl train.jsonl` | exit 0, 0 pairs, 315 of 315 clean |
| `training-prepare --cases train.jsonl` | digest `7c8997e1e128c47f`, 1,971 cases |
| bundle train split | **1,355** (floor is 1,000) |
| populations per split | both, over 40 / 10 / 10 families |
