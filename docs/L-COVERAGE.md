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
| `ast` (`cap-ast`) | `import ast`; `ast.literal_eval` over a `str`: str/bytes literals and adjacent concatenation, int, float, one sign before a number, `True`/`False`/`None`, tuples, lists, dicts, sets, `set()` (printing a set of more than one element still declines as `set-order`, as it does everywhere) | `ast.parse` in any form (the engine's parser accepts programs CPython's rejects), `walk`, `dump`, `unparse` and the node classes, routed to CPython; any input where `lex.rs` and CPython's tokenizer could disagree refuses |
| Binary records (`struct`, added to `cap-binascii`) | `struct.pack`/`unpack`/`unpack_from`/`calcsize` over `x c b B ? h H i I l L q Q s d`, standard orders everywhere and native layout on 64-bit little-endian Unix; `unpack_from` with a positional or `offset=` offset; `random.getrandbits(64)`, the draw the float-reinterpret idiom feeds `'<Q'` | Every `struct.error` and `TypeError` path, NaN packs, `f`/`e`/`n`/`N`/`P`/`p`, native `l`/`L`, whitespace or non-ASCII in a format, `bytes` formats, `bytearray`/`memoryview` buffers, a negative offset, `Struct`, `error`, `pack_into` and `iter_unpack` (refused at run time) |
| Decorators and keyword-only parameters (added to `cap-future`) | `@<expr>` lines before a `def`, at any depth (PEP 614 expressions: names, attributes, calls, subscripts, lambdas), evaluated before the defaults and bound once; `def f(a, *, b, c=1)`, `def f(*args, k, **kw)` and the same in `lambda`; every such run is held from its first statement | A decorated `class`, `async def` or generator; `@staticmethod`/`@classmethod`/`@property`; every binding `TypeError` and `*` over a non-iterable (`call`, in every variant), a function as a set element or dict key (`iterator-identity`), and every compile-time error after the first `@` or keyword-only name |
| Hex and lossy text (added to `cap-binascii`) | `bytes.fromhex(str)` off the type or an instance; `bytes.decode` with UTF-8 and `errors='replace'` (the module's `hexlify`/`unhexlify` family was already served) | `bytes.fromhex` of a bytes argument (which CPython accepts only from 3.14), every other codec or error handler, and every argument shape CPython raises for |

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
`tests/test_bigint_bytes_ratio.py`, `tests/test_ast_grid.py`,
`tests/test_binascii_grid.py`, `tests/test_struct_grid.py`,
`tests/test_decorator_grid.py`, `tests/test_kwonly_grid.py` and
`tests/test_coverage_integration.py`; the
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

The `ast` and hex rows were added on 2026-09-25 from programs captured in the
most recent sessions. [HILLCLIMB.md](HILLCLIMB.md) iteration 84 records what
they answer together (lypning-l +33 programs over the round) and what still
goes to CPython, with the reason. `decode(errors='replace')` on its own is
still routed to the core, which declines, so it reaches lypning-l one spawn
later.

## How this informs the next manually started training round

Follow [the training handoff](../training/NEXT_ROUND.md). These runtime changes
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
