# Round readiness — 2026-09-19

What still has to be true before a GPU is booked, in order, with the state of
each. `STATUS.md` §10 owns **which** round runs and whether it runs at all; this
owns **what is left** for the one being prepared, and nothing here authorises a
spend. Every number carries the command or run that produced it.

## Where we are in one paragraph

The bank is no longer the blocker and the gates are no longer the blocker. A
re-adapted bank-v3 of 7,042 cases clears `validate_pilot`, `validate_benchmark`
and the 1,000-train-case floor at all three protocol seeds (CI run
`35430465717`, 2026-09-19), and it has the headroom bank v2 lacked: `bank-native
--mix-only` reads a macro delta ceiling of 11.00pp and 11.33pp on its two
batches against a 3.00pp bar, where bank v2's completed base arm left 3.14pp.
What is left is **structural, not statistical**: the bank cannot yet be carved
into a pilot and a *disjoint held-out benchmark*, because the construct pool
capped families at 37 and two banks need ≥18 each. The pool is now 71 families,
a generation run over it is in flight, and `nt bank-carve` exists to do the
carve once those families exist. After that: publish, price the token floor,
and ask the operator.

## The ladder to a first real round

| # | What | State |
|---|---|---|
| 1 | Generation reaches the provider | **done** — `backends.USER_AGENT`; the 403 was Cloudflare 1010 on `Python-urllib`, not the key |
| 2 | A generate run survives adapt | **done** — one malformed row no longer discards the batch; adapt shards four ways |
| 3 | The bank carries fallback controls | **done** — 1,601 control rows; the pre-fold publisher had discarded every one |
| 4 | The bank clears the plan-time gates | **done** — run `35430465717`, all three seeds |
| 5 | The bank can host the effect | **done, with a caveat** — ceiling 11.00/11.33pp vs a 3.00pp bar, but this is `bank_native`'s free proxy on the bank's own programs, an upper bound on movable delta and not a base-model draw |
| 6 | Enough families for a pilot **and** a disjoint benchmark | **in flight** — pool widened 37 → 71 families; generation running |
| 7 | Carve `banks/v3/{train,eval2}.jsonl`, disjoint by family | **done** — `nt bank-carve`; validated against bank v2's union, which it carves back to its own hand-made 51/18 shape |
| 8 | Publish to the Hub and point `BANK_PATH` at it | not started — `round02.yml` still reads `banks/v2` |
| 9 | The exact supervised-token count | **runnable, never run** — `token-floor.yml`; the ≥50,000 floor is refused inside `run()`, on a metered job, after the weights |
| 10 | Operator authorises the spend | **owed** — h200 ≈ $25/seed; a complete S4 is three seeds (1111, 2222, 3333) |

Two open items that block nothing above but change how a result reads:

- **Dev and test hold 4 families each** against 29 in train (run `35430465717`).
  No gate checks this and a family-clustered bootstrap will feel it. The carve
  makes it worse before it makes it better: it takes families away from the
  pilot for the benchmark, so the widened pool is what pays for both.
- **One engine mismatch is filed and unfixed**: `sys.stdin.read(n)` ignores its
  size argument and returns the whole remaining stream (`readline()` is
  correct), in `data/engine-mismatches.jsonl`. Invariant 1 makes it interpreter
  work, not training data.

## The stdlib corpus: an arm, not an ingredient

The corpus that landed in #88 is **complementary training material and is
deliberately not in the first round.** The reason to hold it back is that
mixing it in and measuring nothing else would tell us the round's result but not
what the corpus bought; running the round without it first leaves an arm to
compare against.

Two facts decide how it is used, and both are measured rather than assumed:

1. **A unit cannot be a bank case.** `validate_cases` requires three
   independently specified tests whose outputs differ. A unit is a
   self-contained program with no stdin, no argv and one fixed stdout, so it is
   refused, and there is no honest way to give it three. `pipeline.stdlib_sft`
   therefore emits **supervised rows**, never cases, and they never reach a
   split or a benchmark.
2. **The corpus overlaps the bank's surface almost completely.** 14 of the 15
   modules it fills also appear in the bank's construct pool (measured
   2026-09-19). Against a realistic bank-v3 family list, **19 of 34 units are
   contaminating** and are held back, leaving 15 rows over 9 modules. Mixing a
   `textwrap.wrap` unit into a round whose benchmark holds a `textwrap` family
   is teaching toward the test.

So the arm is gated, not default:

```bash
PYTHONPATH=src:training python3 -m pipeline.cli stdlib-sft \
  training/data/stdlib/stdlib.jsonl \
  --held-out-bank <the round's eval2 bank> \
  --output work/stdlib-sft.jsonl
```

It drops the overlapping units by name, prints which modules caused it, and
exits 1 when nothing is left to mix — a bank whose benchmark reaches every unit
cannot measure this arm at all, which is a finding about the pairing rather than
a usage error. `--allow-overlap` keeps them, and then the benchmark result has
to be reported as contaminated.

**The comparison, when it is run:** same bank, same seeds, same benchmark, two
SFT arms — one without these rows, one with. The difference is what the corpus
bought. There is no point running only the mixed arm, because nothing would be
left to compare it to.

## The carve, and what it refuses

`nt bank-carve <bank> --output <dir>` writes `train.jsonl` and `eval2.jsonl`
that share no family, and writes **nothing** unless both are admissible: the
pilot must pass `validate_pilot` after `split_cases` at every protocol seed,
not the one the caller passed, because a bank admissible at 1111 and not 2222
fails a three-seed round halfway through.

The floors it enforces are arithmetic, and it reports them rather than failing
later: ≥18 families per bank (`validate_bank`), and **≥6 control families in
the pilot**, because `split_cases` hands each split
`max(2 if groups >= 6 else 1, groups // 6)` families of a population and
`validate_pilot` wants two per split. With the benchmark's own ≥2, that is 8
control families minimum. bank v3 had 10 before the pool widened, which is why
this was the step that could not be taken.

Checked against the one bank whose carve is known: bank v2's two banks were
separated by hand into 51 and 18 families, and re-uniting and re-carving them
reproduces 51/18.

## What is deliberately not claimed here

- That bank-v3 will produce a positive result. The ceiling says an effect *can*
  exist in the population; it says nothing about whether training installs one.
- That 11.00pp of ceiling is the endpoint. The endpoint is the correct-and-native
  family macro at k = 16 on a held-out benchmark (`EVAL2.md` §4); the ceiling is
  a free proxy read off the bank's own programs before a draw is paid for.
- That the stdlib arm helps. It is set up to be measured, and has not been.
