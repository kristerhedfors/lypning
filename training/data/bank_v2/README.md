# bank v2 — an authored task bank

`train.jsonl` (1,689 cases / 51 families) and `eval2.jsonl` (597 cases / 18
families) are a split of one authored bank. `generate.py` writes the bank,
`split_bank.py` splits it, and `cases.jsonl` is the unsplit intermediate and is
gitignored.

The split is by **capability**, not by family, and that is not a detail. Two
families of one kind answer the same shape of question, so they collide by the
identical-stdout rule however differently their tasks read — `file-firstline`
matched four other `file-*` families across the split, because a first line is
often also the longest. Keeping a whole capability on one side removes the
collision at its cause. The benchmark takes the kinds whose answers are **text**
and leaves the bare-integer kinds in train, because two families that both print
a large integer coincide eventually whatever they compute.

`fallback-control` is the exception: it is one capability, and sending all of it
to either side would leave the other with no control at all. It is split by
family, and only controls with a distinctive output shape go to the benchmark.

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
| `eval2-leaks eval2.jsonl train.jsonl` | exit 0, 0 pairs, **597 of 597 clean** |
| `training-prepare --cases train.jsonl` | digest `10492e75cc8c52a6`, 1,689 cases |
| bundle train split | **1,120** (floor is 1,000) |
| eval-2 families | **18** (benchmark floor is 18) |
| populations per split | both, over 33 / 9 / 9 families |
| cases dropped by the oracle | 129, all non-discriminating; see `dropped.jsonl` |

Getting to zero leaks took three rounds, and each one is recorded above because
the next person will hit them again: identical task preambles (4,419 task pairs),
same-kind families across the split (`file-firstline` against four siblings), and
bare-integer kinds coinciding (`stdlib-collections-counter` against
`int-maximum`). None of them was fixed by relaxing a threshold.
