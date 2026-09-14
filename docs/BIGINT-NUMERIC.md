# Wide byte conversion and exact float ratios

`lypning-l` serves wide `int.to_bytes` receivers and every finite
`float.as_integer_ratio` result through the existing `cap-bigint` representation.
The frozen core retains its previous machine-word path and wide-value refusals;
no capability table or dispatch rule changes.

## Representation and boundaries

`bigint::to_bytes` writes a sign/magnitude integer into a fixed-width
little-endian buffer; `methods::int_method` reverses it for big endian.
Unsigned negative values raise `OverflowError`. Signed positive values reserve
a sign bit, while signed negatives additionally admit exactly
`-2**(8*length-1)`. The latter is detected from magnitude bits before allocating.
Negative values are complemented and incremented across the complete output
buffer, including sign extension. No cast truncates a wide value.

Only normalized `Int::B` receivers take this path. Small integers retain their
existing implementation, including the reference-version-sensitive zero-width
case for `-1`. Argument handling and the shared 4096-byte conversion limit are
unchanged: unsupported argument forms and resource limits still refuse. A
resource refusal before a committed write clears buffered stdout; after a write
it remains the existing non-retryable runtime backstop.

`methods::as_integer_ratio` decodes binary64 bits, handles both signed zeros,
raises the existing NaN/infinity exceptions, and removes trailing mantissa
zeros. `bigint::float_ratio` then shifts the numerator or denominator into the
existing limb representation and normalizes it. There is no floating-point
arithmetic or rounding in this conversion. Even the smallest subnormal's
denominator fits in 1075 bits, well below the existing bigint allocation budget.
This does not add general bigint/float arithmetic or bigint dictionary keys.

## Verification, 2026-09-15

Run in an isolated worktree with its own `LYPNING_HOME`, using CPython 3.12.13
on Darwin arm64, and explicitly set `LYPNING_CPYTHON` when building. The Python
running pytest must match that calibration.

```sh
export LYPNING_CPYTHON="$(command -v python3.12)"
export LYPNING_HOME="$PWD/work/numeric-verification"
export PYTHONPATH=src
"$LYPNING_CPYTHON" -m lypning build --rust --target host
"$LYPNING_CPYTHON" -m pytest tests/test_bigint_bytes_ratio.py \
  tests/test_bigint_grid.py tests/test_semantics.py -q
"$LYPNING_CPYTHON" -m lypning conformance --engine lypning-l --limit 100 --timeout 10
"$LYPNING_CPYTHON" -m lypning doctor
"$LYPNING_CPYTHON" -m lypning gate "$LYPNING_HOME/bin/lypning-l"
```

Measured results for this focused change against base `834f7e7`:

- 70 new strict tests pass, including both byte orders, signed/unsigned boundary
  widths through 4096 bytes, overflow messages, roundtrips, committed writes,
  refusal stdout/stderr contracts, all binary64 exponents and 512 seeded finite
  bit patterns. New coverage tests reject exit 90 rather than skipping it.
- The combined three files give 824 passed, 7 existing subset skips.
- The bounded corpus sample runs 94 programs: 66 MATCH, 28 UNSUPPORTED,
  0 MISMATCH; 6 entries are skipped by the corpus safety rules. This is not a
  whole-corpus coverage-delta claim.
- Both build refusal checks pass; doctor reports 0 FAIL. The host gate passes
  its available checks, with static linking, shared objects and file opens
  unmeasured. It does not establish a Linux/musl size-budget result.
- Native L text grows from 847,440 to 848,216 bytes (+776). Its page-padded file
  remains 1,083,248 bytes (9 device blocks). Core text remains 717,872 bytes and
  its file 917,568 bytes (8 blocks). These are host-build measurements only.

The larger covered domain is explicit, not a benchmark score: wide integer
serialization up to the existing output cap, and exact ratios across the entire
finite binary64 domain. Keyword/default expansion, wider resource budgets and
general bigint/float interoperability remain outside this change.
