# Expanding lypning-l without changing the frozen core

The new surfaces below belong to lypning-l. Correct fallback remains a valid
answer; neither training nor coverage tests may reward a wrong native result.
The core's capability set is unchanged.

## The added surfaces and their boundaries

| Surface | Now served by L | Still outside this slice |
| --- | --- | --- |
| CSV | `reader` and `DictReader` over direct lists, tuples and strings; lazy list mutation and exhaustion; shared supplied field-name lists | Writers, arbitrary generators and shared iterator inputs; unsupported dialect/error shapes still refuse |
| Regex | ASCII named captures; name-based group/index/start/end/span access; `groupdict(default)`; nested and repeated captures | Unicode group names, named backreferences/templates, `groupindex`, `expand`, last-group metadata and previously unsupported regex constructs |
| Numeric conversion | Wide signed/unsigned `int.to_bytes`; exact `float.as_integer_ratio` for every finite binary64 value, including subnormals | The existing 4,096-byte conversion ceiling; general mixed wide-integer/float arithmetic is not added |

A bare string passed to `csv.reader` is an iterable of characters, not one
complete line. List sources remain live until their iterator is exhausted.
These details are observable and are part of the differential tests, not
opportunities to simplify the training reference.

## Evidence to keep with a coverage change

Build both engines against one explicitly selected CPython executable. Run the
strict native-answer tests, refusal/rollback tests and the combined-feature
tests against those exact binaries. Record the interpreter patch version and
engine hashes, not just the repository commit.

The checks live in `tests/test_csv_grid.py`, `tests/test_re_grid.py`,
`tests/test_bigint_bytes_ratio.py` and `tests/test_coverage_integration.py`; the
numeric design and limits are in [BIGINT-NUMERIC.md](BIGINT-NUMERIC.md).
Use raw stdout/stderr bytes. A test permitting any clean refusal cannot prove
that an intended new capability actually runs natively.

Corpus reports must include loaded, selected, graded and safety-skipped counts.
Report stdout-comparing matches separately from programs where both engines
raised: both matter for correctness, but they are not the same coverage gain.
Require zero mismatches and agreement between both dispatchers. Corpus runs
remain confined to a worktree with its own `LYPNING_HOME` and the repository
write-detection net; that net is not a security sandbox.

Linux CI gates both native Rust artifacts against their existing target-specific
budgets. A local Mach-O file size is useful evidence, but cannot certify the
x86-64 musl budget or the zero-file-open startup property.

## How this informs the next manually started training round

Follow [the training handoff](../nemotron/NEXT_ROUND.md). These runtime changes
do not launch a training job and do not turn the authored starter into a pilot
benchmark.

Candidate new task families include quoted-table transformations, structured
text extraction with named fields, fixed-width integer serialization and exact
floating-point decomposition. Include tasks composing two surfaces, such as
extracting named fields from quoted CSV values. These are authoring directions,
not an admitted dataset: independently review their expected outputs, include
multiple inputs that distinguish plausible wrong programs, and split by source
and semantic family before generating rollouts.

Keep fallback controls in every split. Select them by verifying that the frozen
engine really refuses while CPython answers; a once-unsupported regex fixture
can become supported after a coverage expansion. A larger example count from
cloning templates is not broader family coverage.

Rebuild after these changes and prepare a fresh immutable bundle. Do not reuse
old native-acceptance labels, rewards, caches or checkpoint comparisons across
engine identities. Re-audit recorded mismatch witnesses before admitting data;
the new capability tests do not resolve every historical semantic discrepancy.
The next agent still owns dataset admission, the external filesystem/egress
boundary, GPU-stack smoke tests and explicit training-cost approval.
