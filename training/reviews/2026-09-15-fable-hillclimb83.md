# Codex assessment: Fable's Hillclimb 83

Reviewed 2026-09-15: [PR #77](https://github.com/kristerhedfors/lypning/pull/77),
runtime/test diff, retirement references and the iteration-83 writeup. Initial
head was `8278ef6092e0a8403ae5065520a1ca360343242b`; Fable subsequently integrated
main and addressed the fixture-history comment. Its reviewed follow-up head is
`14b96639b7ae515d8074afb993930b03acfabedf` (the last change adds capture sightings).
No private Fable training artifacts or captured grid programs were executed here.

## Findings and resolution

The list error-message fixes, keyword argument binding and rejection-order fixes,
float-format corrections and safe refusal for class unions address concrete
semantic discrepancies and add differential/refusal tests. Refusal remains a safe
coverage boundary, not a claim of support for union values. The file-writing test
now uses a temporary cwd. Runtime and size claims belong to Fable's dated report
and final CI; no model-quality gain follows from these changes alone.

The retirement cleanup does not change live training parameters in the reviewed
Qwen YAML or trainer diff; those changes are comments. No live caller of the
deleted legacy launcher was found in the searched current pipeline/workflows.
Historical retirement documentation and old refactor diagrams may still mention
the removed files as history. They remain recoverable in Git, not executable
instructions for the current training round.

Codex identified one provenance issue: renaming a synthetic comparability fixture
must not claim that historical audited runs used Qwen. Fable corrected the
docstring to distinguish the synthetic fixture from the original audit. Fable
also resolved the changelog-only conflict with main while preserving both entries.
Codex's concurrent integration push was safely rejected after Fable advanced the
branch; no force-push or overwrite was used.

## Independent assessment beyond the writeup

1. Authored surface sweeps are useful new regression evidence but do not replay
   or close the outstanding captured grid witnesses. Keep the dedicated-container
   replay obligation and per-witness disposition explicit.
2. A correct refusal for annotations preserves program behavior through fallback
   but does not expand native coverage. Future native annotation/union support
   needs separate semantics and measured demand from correct fresh programs.
3. Any new L binary changes the verifier identity. Rebuild the candidate image,
   regrade references and freeze a NEW bundle before a training comparison.
   Do not mutate Fable's existing engine-bound round in place.
4. Reported noisy filesystem timings are not evidence for a training recipe or
   per-script reward. Continue using independently correct native coverage as
   the model objective and aggregate timing as a separate runtime-health check.

## Decision: accept runtime fixes after final CI; training gates remain open

Focused integration review on the same runtime changes plus main's orchestration
files passed 231 tests with three skips and 20 existing non-strict xpasses on
2026-09-15. Final PR-head CI is still the merge gate. This review does not start a
GPU run or certify a training result. Fable remains responsible for the operator,
data/image/hardware prerequisites and a report using `FABLE_REPORT_TEMPLATE.md`.
Codex proceeds with question-quality repair and independently reviewed data.
