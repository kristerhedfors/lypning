# Seed 1111, arm A, SFT step 1,050 — the first complete trained-vs-base read

Read 2026-09-26 from finish HF job `6ab7b0b76b030d633f693e48` (Actions
`36239792036`, status `complete`), aggregates only, by
`.github/scripts/finish_readout.py` (Actions `36266025245`, artifact-repo
revision `04d50370c3e0ce80cddb69c24262559711d39ace`). Pilot HF job
`6ab52a686b030d633f68e503`; engine `lypning-l` for CPython 3.12, sha256
`3da77f03…` at verifier Space revision `eafca686…`; SFT step 1,050, the
operator's override of rule v1's 350 (`training/EVAL2.md` §4, 2026-09-25),
which rule v2 also selects on the pilot's own dev draws (`reselection` in the
job manifest). **One seed: one replicate, not a round** (`training/EVAL2.md` §6).

## 1. The pre-registered read

Primary metric: correct-and-native family macro, paired, source/family-component
bootstrap, 2,000 resamples (`training/EVAL2.md` §4).

| split | cases / families / draws per arm | base | SFT | delta [95% CI] | rule: lower bound > +3pp |
|---|---|---|---|---|---|
| eval-2 | 803 / 19 / 12,848 (k = 16) | 68.97% | 74.28% | **+5.30pp [+2.22, +8.88]** | **not met** (+2.22) |
| test | 315 / 7 / 1,260 (k = 4) | 68.49% | 71.90% | +3.41pp [−1.35, +9.68] | not met |

Gates (`training/EVAL2.md` §4, `PREREGISTRATION.md` §7c), all pass on both splits:

| gate | eval-2 | test |
|---|---|---|
| A correctness, delta ≥ −2pp | −0.12pp [−1.32, +1.31] | −0.08pp [−3.25, +3.57] |
| B supported-import retention ≥ 0.80× base (arm A's engine asked) | 0.998 (97.3% → 97.1%; 753 cases, 12,048 draws) | 1.023 |
| C completion tokens ≤ +20% | −13.6% (168.3 → 145.5) | −14.8% (184.3 → 157.0) |

Engine mismatches: eval-2 0 / 0; test 1 (base) / 0.

**Reading.** The adapter moves the primary metric up with its interval clear
of zero and correctness flat, but the pre-registered bar is a lower bound above
+3pp, and one seed cannot carry a claim. It is a positive, single-seed,
exploratory result.

## 2. Where the gain came from

Eval-2 statuses, 12,848 draws per arm:

| status | base | SFT |
|---|---|---|
| correct-native | 8,758 | 9,296 (+538) |
| correct-fallback | 917 | 350 (−567) |
| correct-control | 1,701 | 1,672 |
| incorrect | 1,423 | 1,508 (+85) |
| no-code | 49 | 22 |

Population slices (family macro; case-weighted in brackets):

| population | base correct | SFT correct | base correct-native | SFT correct-native |
|---|---|---|---|---|
| coverage (11,040 draws) | 87.66% [87.64%] | 87.57% [87.37%] | 79.29% [79.33%] | 83.96% [84.20%] |
| fallback-control (1,808 draws) | 96.31% [94.08%] | 91.84% [92.48%] | 22.92% [5.92%] | 36.35% [12.72%] |

The gain is almost entirely **correct-fallback turned into correct-native**:
the model stopped reaching for modules the engine refuses and wrote the code
by hand. Watch the fallback-control slice: correctness fell 4.5pp on its family
macro (1.6pp case-weighted) — the slice where full Python is the right answer.
The retention gate is not a significance test and did not fire, but a stronger
push in the same direction would cost correctness there first.

## 3. What still falls back — the coverage worklist

Refusal kinds behind correct-fallback draws on eval-2 (one count per draw per
kind): base `module` 543, `module-attr` 289, `str-method` 31, `set-order` 19,
`class-subscript` 15, `json` 11, `nonlocal` 6; SFT `module` 156, `module-attr`
131, `str-method` 24, `set-order` 18, `nonlocal` 8, `builtin` 7,
`class-subscript` 6.

The stdlib names behind `module`/`module-attr`, each probed against arm A's
engine and against main's (`3882b424…`, built from main in the same readout
run). Draw counts over 16 draws per case, eval-2 unless marked:

| target | base draws | SFT draws | refused by main (2026-09-26)? |
|---|---|---|---|
| `functools` | 212 | 14 | yes |
| `copy` | 168 | 119 | yes |
| `collections.OrderedDict` | 140 | 26 | yes |
| `math.comb` | 62 | 15 | yes |
| `itertools` | 46 (test 31) | 2 (test 1) | **no — served** |
| `math.lcm` | 30 | 50 | yes |
| `datetime` | 29 | 0 | yes |
| `string` | 26 | 5 | yes |
| `bisect` | 26 | 0 | yes |
| `math.exp` | 18 | 20 | yes |
| `collections.deque` | 17 | 0 | yes |
| `heapq` | 16 (test 8) | 16 | yes |
| `fractions` | 16 | 0 | yes |
| `math.pow` | 10 | 4 | yes |
| `hashlib.new` | 0 | 11 | yes |
| `io.StringIO` | 5 | 3 | yes |
| `math.log`, `math.log10` | 6 | 1 | yes |
| `ast` | 2 | 0 | **no — served** |
| `csv.writer`, `typing`, `argparse`, `urllib` (test) | 1–2 each | | yes |

Every refused name was a CPython stdlib name; no draw's `module` refusal named
a non-stdlib package.

## 4. What this means

**For coverage.** The base model's correct programs fall back mostly on a short
list of pure stdlib names: `functools`, `copy`, `collections.OrderedDict`, the
`math` functions `comb`/`lcm`/`exp`/`pow`/`log`, `datetime`, `string`,
`bisect`, `deque`, `heapq`, `fractions`. Of the coverage work merged by
2026-09-26, only `itertools` and `ast` reach these draws. Serving the table
above converts base draws directly — the same headroom SFT bought.

**For training.** SFT and coverage are competing for one pool: 917 base
correct-fallback draws (7.1% of eval-2 draws). SFT drained 62% of it by teaching
avoidance, at a small correctness cost on fallback controls. Once the engine
serves the names above, the base model's native rate rises without training and
the adapter's measurable headroom shrinks. So before the next arm: measure the
base on the new engine (`training/ENGINE_BUMP.md` §4 step 10, free), and aim the
next arm at what coverage cannot reach — `copy` and `math.lcm` rose or held
under SFT, incorrect draws rose by 85, and the fallback-control correctness
dip is the risk to design against. Three seeds, under `training/RAMP.md`, are
still what a claim needs.

**Cost.** Scheduled 11:47Z, uploaded complete by about 19:15Z on 2026-09-26:
about 7.5 h of h200, **about $37 at $5/h — an Actions-wall estimate, not a
bill**. The seed-1111 finish chain in total is this plus the failed first
attempt (HF `6ab6a20c6b030d633f691a95`, ~$19 est.).
