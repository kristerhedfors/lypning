# Codex validation of Step 1 implementation — 2026-09-22

Step 1 code is complete in the ordered stack:
[#97](https://github.com/kristerhedfors/lypning/pull/97) selector/stopping,
[#98](https://github.com/kristerhedfors/lypning/pull/98) macro scope,
[#99](https://github.com/kristerhedfors/lypning/pull/99) LoRA recipe,
[#100](https://github.com/kristerhedfors/lypning/pull/100) evaluation cost/deadline,
[#101](https://github.com/kristerhedfors/lypning/pull/101) sized stdin reads.
Review/merge proceeds in that order. Step 2 is next in the plan, subject to the
existing admission, image pinning, hardware smoke and operator cost ceiling.
No training or provider inference ran in this session.

## Training and evaluation contracts

The final full training suite passes **1,311 tests, with 12 skipped**, under
Python 3.14. The overflow refusal uses the existing `alloc` category; it does
not change a capability table or rewrite the subset prompt. Focused validation
covers the macro rule and bootstrap links, the 99/100-step LR boundary,
final-checkpoint cadence, reuse identity/completeness and timeout parsing/privacy.
[Evaluation-cost CI](https://github.com/kristerhedfors/lypning/actions/runs/35680265879)
passes on Ubuntu and macOS, including the real GNU timeout process-group
regression on Linux; macOS has no GNU timeout.

The timeout metadata audit and its limitations are recorded separately in
[`../reports/2026-09-22-evaluation-cost.md`](../reports/2026-09-22-evaluation-cost.md).
The batch increase to 256 is code/configuration only: no hardware throughput or
memory result is claimed, and a real smoke remains required.

## Engine validation

Both host variants were built with
`PYTHONPATH=src LYPNING_HOME=/tmp/lypning-step1-home python3 -m lypning build --rust --target host`.
Both refusal contracts pass. `doctor` reports **0 FAIL, 4 WARN**. The host gate
passes with three platform/tooling checks unmeasured; this is not a Linux musl
size/linkage gate. The filed sized-read witness agrees with CPython on both
variants using the input recorded in its provenance. Frozen witness data was
not edited.

The full corpus command was `lypning conformance --mixture both --workers 4`,
with its own temporary `LYPNING_HOME` in an isolated worktree. It loaded 9,064
programs, scored **6,318** and skipped **2,746**, reporting **20 MISMATCH**:
five programs in each of four arms. The dispatchers agree **6,318/6,318**;
monotonicity violations are zero and the repository net reports no damage.

Every failing program was re-run serially with a 60-second deadline on both
Rust variants, first on clean pre-fix commit `bcefefa`, then on the patched
engine. The verdict, output, exit and comparison fields are identical; only
wall-clock timings differ:

| corpus ID | inherited mismatch |
|---|---|
| `py-53786ee8ed26` | floating-point result differs in its final digit |
| `py-7528eaaf90d7` | `%c` TypeError wording |
| `py-ab7286f43b7a` | floating-point result differs in its final digit |
| `py-cef162ae0e87` | unhashable-list TypeError wording under CPython 3.14 |
| `py-d08f841abc57` | engine exits 1 where reference exits 0 |

**These are inherited bugs, not waivers.** The source change adds no corpus
mismatch; the host conformance run is still red. The next pinned candidate
must reach MISMATCH 0 before it is used for a paid result. No routing capability
was widened to hide a failure.

## Package and documentation validation

The full package suite passes **8,591 tests**, with 451 skipped, 3 expected
failures and 20 unexpected passes. It uses `/opt/homebrew/bin/python3.14` to
match the built engines and `--basetemp /tmp/ls1-test` to stay within macOS Unix
socket path limits. This includes 114 sized-stdin differential regressions.

The complete documentation site builds (24 pages), and every internal link,
Markdown reference and heading fragment resolves. The final diff check is clean.
