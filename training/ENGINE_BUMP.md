# Training on the next lypning-l — the coupling map and the engine-bump checklist

This file is the one home for **what a new engine invalidates in training and in
what order to rebuild it**. The spend ramp that follows it is
[RAMP.md](RAMP.md); the step plan is [PLAN.md](PLAN.md); the failure → free
check table is `.claude/skills/round02-preflight/SKILL.md`. Written 2026-09-26,
when the operator paused training so engine coverage could continue in a
parallel session, with training to resume on the improved engine.

## 1. A training arm is defined at one engine identity

Every training artifact is labelled by the engine that graded it, so an arm is
fixed to one lypning-l byte identity from its targets to its last eval-2 draw.
Coverage work on main moves HEAD's engine every PR; an arm must not move with
it. The trainer, the bundle loader, the finish and the target grader all refuse
a second engine, so a drift costs either a free refusal (if a preflight runs
first) or a paid one (if the job finds it after the model pull).

The current arm, arm A, is frozen at:

| pin | value | where verified |
|---|---|---|
| engine | lypning-l for cpython 3.12, sha256 `3da77f03e97ef26f50921a7d250bd08429252a8a4eb7803bf80467e5f3f8ec0e` | finish log, Actions `36161157776` (2026-09-25) |
| Space | `headforce/lypning-round02-verifier` at `eafca686ea13a1bec0f105d802dc78c1b5ac9f74` | finish log, Actions `36161157776` (2026-09-25T16:31Z, the hold line names this head); read again at 22:28Z by bootstrap Actions `36196784607`, which skipped an identical upload and was then cancelled — no Space build started |
| targets | `full-merged-0527b3cd2c0d8916bebd086abcc46459d7ae46b1-35913600534` | `training/PLAN.md` Step 4 (arm A as approved), passed as the `target_run` dispatch input of `round02.yml`; the `bare,subset-spec` merge, Actions `35913600534` (2026-09-23T20:05Z) — not `35912725289`, the `subset-spec`-only merge that made the Step 2 decision |
| bank | `BANK_PATH: banks/v3-20260920b` | `.github/workflows/round02.yml` line 89 |

The Space revision and engine sha live only in logs and job manifests today;
§3 makes them a committed pin.

## 2. The coupling map

Each row is an artifact whose content or validity depends on the engine.
"Paid" means regenerating it needs a billed provider or GPU call.

| artifact | where | breaks when the engine moves | regenerate | paid |
|---|---|---|---|---|
| bundle engine identity | `training/pipeline/training.py` `engine_identity` (binary sha256, `--version`, CPython minor, `verifier_sha256`); `load_bundle` refuses drift | every prepared pilot/eval-2 bundle stops loading; `--bundles-from` reuse refuses in `training/hf/round02_pilot.sh` | re-run training-prepare inside a pilot job against the new Space revision | yes (GPU job) |
| bank reference native/fallback labels | `training/pipeline/training_data.py` `validate_reference_scores`, called at prepare and in `load_bundle` | a fallback-control reference the engine now runs natively fails admission *inside* the paid job; the ceiling moves | free: `.github/scripts/verify_references.py` in the pinned image (via `bank-publish.yml`), `bank-gates.yml`, `bank-ceiling.yml`; publish a **new** `BANK_PATH` | no |
| repair rules and repaired pairs | `training/pipeline/repair_rules.py`; `training/tests/test_repair_rules.py::test_no_rule_rewrites_a_module_the_engine_serves` | a rule rewrites away a module the engine now serves; its pairs teach avoiding a served import. Seen for `itertools` and `statistics` on #128 (training verifier, Actions `36172527214`, 2026-09-25) | delete the rule (done on #129), re-run `bank-v3-readapt.yml` without it, retire its pairs before the next `BANK_PATH` | no |
| subset-spec system paragraph | `training/prompts/gen_subset_spec.py` reads the Rust source at HEAD; `training/tests/test_eval2_prompt.py` holds `subset-spec.md` equal to it | new refusal kinds lack a recipe; kind and python-only lists mismatch (3 of 4 failures on `36172527214`). Its bytes set `spec_sha256` in every step-2 shard identity, so old and new shards cannot merge | free: add recipes, run `python3 training/prompts/gen_subset_spec.py`, commit (done on #129) | no |
| SFT targets and their `lineage.engine_sha256` | `training/pipeline/positive_control_generate.py`; `.github/scripts/step2_grade.py` (grading engine must equal generation admission); `training/gpu/train_verified.py` line ~290 | old targets are refused by `.github/scripts/s4_target_floor.py` (free), then by the pilot, then by the trainer's plan stage; old draws cannot be regraded without a protocol change | `step2-control.yml` (provider), then `step2-control-grade.yml`, `step2-merge.yml`, step3-pairs, `s4-target-preflight.yml` | yes (provider) |
| step3 kind vocabulary | `.github/scripts/step3_pairs.py`, `.github/scripts/step0_summary.py` `known_kinds` (HEAD Rust literals) | new kinds read as `other-kind`; an old-engine probe reports `same_engine_as_run=false` | free re-run after new targets are graded | no |
| stdlib corpus labels | `training/data/stdlib/stdlib.jsonl`; `training/tests/test_stdlib.py` requires byte equality with a verify pass on today's engines | a unit's cheapest engine changes, training CI goes red | free: `stdlib-verify` per `training/stdlib/README.md`; mechanism in `training/STDLIB.md` §3 | no (new units are) |
| eval-2 prompt signature | `training/tests/test_eval2_prompt.py` | the subset-spec eval arm's prompt sha moves: before and after are different arms | free with subset-spec; treat as a new arm | no |
| verifier Space revision | `.github/scripts/round02_space.py` builds from the dispatching checkout unless `SPACE_HOLD=1`; the pool serves only the head (`training/pipeline/hf_sandbox_runner.py`) | any non-finish bootstrap from a new-engine commit moves the head; every pilot pinned to the old revision becomes unfinishable (`.github/scripts/finish_preflight.py` refuses) | free, but irreversible for older pilots | no |
| step-2 conformance reuse | `.github/scripts/step2_reuse_conformance.py` | a passing result is not reused; the check re-runs | free, automatic | no |
| training CI | `.github/workflows/training.yml` builds both engines at HEAD and runs `training/tests` against them | any engine PR that leaves the rows above stale turns the "training verifier" red, even with no `training/` edit | regenerate in the same PR (§6) | no |

## 3. Pinning: what holds today, and the gap

The engine is pinned per artifact but not per programme. Bundles carry the
engine sha256 and `verifier_sha256`; targets carry `lineage.engine_sha256`; jobs
download lypning-l from the Space at a 40-hex revision; `finish_lineage.py` and
`finish_preflight.py` check all three against each other. Every one of those
checks compares artifacts with *each other*, not with a committed intended
engine.

The gap is the Space. `round02.yml` sets `SPACE_HOLD` only for
`stage == finish` (line 249), so a pilot, smoke or hwsmoke dispatch — and any
push to `round02/**` (line 14) — rebuilds the Space from that commit's engine,
built by that day's `ubuntu-latest` rustc. Because the pool serves only the
head, one Space move retires every older pilot at once.

**TODO — the pin mechanism (not built):**

1. A committed pin file, `training/hf/engine_pin.json`: `engine_sha256`,
   `space_revision`, the source commit, `BANK_PATH`, targets run. One arm, one
   file; a new arm is a new commit of it, reviewed.
2. Hold by default: `.github/scripts/round02_space.py` and
   `.github/workflows/round02.yml` hold the Space for every stage and every push;
   rebuilding needs an explicit `rebuild_space` dispatch input, and after a
   rebuild the run prints the new revision and engine sha for the pin file.
3. A free refusal: `.github/scripts/round02_preflight.py` (and
   `.github/scripts/s4_target_floor.py`) download lypning-l at the Space head
   and fail when its sha256 or the head revision differ from the pin file,
   before any billed submit.
4. Optional: store the exact binary in the private artifact repo, since the
   same commit does not rebuild byte-identically across runner rustc versions
   (`.github/scripts/round02_space.py` docstring).

## 4. The checklist: starting the next arm on the improved engine

Run in order, after the coverage session reports done. Each step is free unless
marked paid; stop at the first that fails.

1. **Settle arm A first** (§5). No step below that touches the Space may run
   until seed 1111 is finished or recorded as abandoned in
   `training/ORCHESTRATION.md`.
2. **Land the coverage stack whole.** #128 alone leaves main's training verifier
   red (Actions `36172527214`); #129, stacked on it, carries the training-side
   regenerations and was green (Actions `36230324509`, 2026-09-26). Fold draft
   #118 in (§5). #128, #129 and #133 (coverage) and #130 (prefill parts) were
   merged on 2026-09-26; #118 was still a draft.
3. **Freeze the engine commit.** Pick one main commit as the candidate and write
   it into the pin file (§3). Later coverage PRs on main do not move it.
4. **MISMATCH 0 on that candidate, where it counts.** The reference is CI's
   "rust core — contract, gate, conformance" job (`.github/workflows/ci.yml`)
   at the candidate commit; record its run id in the pin file. That job runs
   CPython 3.11 on `ubuntu-latest`, while the Space serves lypning-l for
   CPython 3.12 in `python:3.12-slim`, and the same commit does not rebuild
   byte-identically across rustc versions — so also run conformance under
   CPython 3.12 (TODO: no CI job does today). A local run counts only as an
   A/B against main on the same host: a host whose main already shows
   MISMATCH (a 3.14 engine does) cannot certify the candidate alone. Then a
   free, private check: re-grade the mismatch witnesses of the last base-model
   draws (Step 2's grade and the pilot's `engine-mismatches.jsonl`) against the
   candidate, so the 1% per-arm mismatch bound (#126) is checked against a
   measured rate. A candidate that is not MISMATCH 0 is not frozen: the base
   model will reach its bugs, as it reached one in pilot
   `6ab52a686b030d633f68e503`.
5. **Build the pin mechanism** (§3 TODO), so steps 6-12 cannot be undone by a
   stray dispatch.
6. **Rebuild the Space deliberately**, once, from the candidate commit (a
   bootstrap with the explicit rebuild input). Record the revision and the
   downloaded lypning-l sha256 in the pin file.
7. **Verify what describes HEAD's engine**: confirm `training.yml` is green
   at the candidate. If §6 was followed, the coverage PRs already regenerated
   `training/prompts/subset-spec.md` (`gen_subset_spec.py`) and
   `training/data/stdlib/stdlib.jsonl` (`stdlib-verify`); regenerate here only
   what one of them missed.
8. **Retire repair pairs for modules the engine now serves**:
   `test_no_rule_rewrites_a_module_the_engine_serves` names them; re-run
   `bank-v3-readapt.yml` without the deleted rules. First known item: on
   2026-09-26 that test failed for `itertools` and `statistics` (1 failed of
   `training/tests`) against a lypning-l built from coverage work in the shared
   default `LYPNING_HOME`; delete those two rules and retire their pairs when
   the next engine lands. Re-run it with `LYPNING_HOME` pointing at a
   per-worktree home built from HEAD to tell environment from HEAD.
9. **Relabel into a new bank.** Re-derive native/fallback labels with
   `verify_references.py` in the pinned image, pass `bank-gates.yml`, publish
   under a **new** `BANK_PATH`. Never re-cut or overwrite a frozen bank:
   `banks/v3-20260920b` stays arm A's.
10. **Measure headroom** — the seed-1111 finish showed coverage and SFT draw on one pool (917 base correct-fallback draws, 7.1% of eval-2; `training/reports/2026-09-26-seed1111-finish-read.md` §4), so the base on the new engine is the number the next arm is sized against — with `bank-ceiling.yml` (free; its last run
    `35427139796` on 2026-09-19 failed, so re-validate it first). If the ceiling
    leaves less than the +3pp MDE, stop: no adapter can fire the rule.
11. **Targets at the new engine (paid).** Old targets cannot train here. Either
    a new target rung, or regrading the old draws under a dated protocol
    amendment (PLAN.md forbids regrading today). Last cost: arm A's full rung
    was two shards, `35828368891`+`35849787147` ($11.38071959) and `35828540620`
    ($11.00544250), 2026-09-23; the Step 2 rungs totalled $28.12751896 charged or
    reserved (PLAN.md Step 2). Start with the smallest rung per [RAMP.md](RAMP.md).
    Then grade, merge, step3-pairs, `s4-target-preflight.yml`. Free check
    before any bundle: the new targets' `spec_sha256` equals the committed
    `subset-spec.md` at the frozen commit, and their `lineage.engine_sha256`
    equals the pin file.
12. **Re-prepare bundles** at the new Space revision — in the ramp's
    prepare-only rung (R2b in [RAMP.md](RAMP.md)), never by reusing arm A's
    bundles.
13. **Enter [RAMP.md](RAMP.md) at rung 0.** Add a dated `training/EVAL2.md`
    amendment naming the new engine. The new arm needs all three seeds;
    `arm_check.py` will not join them with seed 1111.

## 5. Arm A, seed 1111, and draft #118

Seed 1111 of arm A can be finished only on the current Space head, so its
finish runs **before any Space rebuild or never**. The pilot
`6ab52a686b030d633f68e503` (Actions `36008052722`, 2026-09-24) completed SFT at
1,050 steps; its finish `6ab6a20c6b030d633f691a95` (Actions `36161157776`,
2026-09-25) completed base-test and sft-test (1,260 draws each) and then ran out
of CUDA memory in base-eval2 prefill. PR #130 fixes that (merged 2026-09-26,
`9b17b32`). A rerun (`stage=finish`, `finish_of 6ab52a686b030d633f68e503`,
`sft_step 1050`) holds the Space and was projected near 520 min against the
648-min budget in the #130 body — about $43 at $5/h by arithmetic, not a bill.
It is an R8 rung and climbs under [RAMP.md](RAMP.md) §5's salvage conditions. **Dispatched 2026-09-26** on the operator's instruction ("run the eval now"; the waiver is recorded in `RAMP.md` §7): #130 merged as `9b17b32`, finish Actions `36239792036`, HF job `6ab7b0b76b030d633f693e48`.

The finish refuses for free in `.github/scripts/finish_preflight.py` unless all
of these still match the pilot: the Space head (`eafca686…`); the
`verifier_sha256` of the dispatching checkout, a digest over the seven
`VERIFIER_MODULES` in `training/pipeline/training.py` (including
`hf_sandbox_runner.py`); `QWEN_REV`; the seed and split seed; eval draws and
sequences; and pool density. So it must be dispatched from a main whose
`VERIFIER_MODULES` are byte-identical to the pilot's: **land no edit to those
modules** (RAMP tooling in `hf_sandbox_runner.py` or `train_verified.py` is the
likely one) before the finish runs or is abandoned. #128 and #129 touch none of
the seven (checked with `gh pr diff`, 2026-09-26), so coverage merges are safe.

Its value is that it would be the programme's first trained-vs-base eval-2
comparison on bank v3, and the first eval-2 arm any job has finished. It cannot
join seeds on the new engine. If the operator declines it, record in
`training/ORCHESTRATION.md` and [PLAN.md](PLAN.md) that arm A on engine
`3da77f03…` is closed without eval-2 — before step 6 above.

Draft #118 (parser fix, "HOLD until S4 arm A's three seeds finish") overlaps #128
on five Rust files and fixes the bug behind the pilot's base-test engine
mismatch. Its hold premise ends once arm A stops at seed 1111; fold it into the
coverage stack so the frozen candidate carries the fix.

## 6. Hazards

**A push to `round02/**` rebuilds the Space.** `round02.yml` bootstraps on every
such push with no hold; Actions `36196784607` (run started 2026-09-25T22:27Z)
was one: bootstrap skipped an identical upload, the run was then cancelled, and
no Space build started, so the head stayed `eafca686`. A push whose head commit
message carries `[submit-pilot]` or `[submit-smoke]` also bills. Free readers
(`hf-status.yml`) also listens on that glob (`eval2-shape.yml` is dispatch-only since 2026-09-26); both declare
`workflow_dispatch`: run them with `gh workflow run` on main, never by a
push. Until §3 lands, push no `round02/**` branch.

**Coverage PRs turn training CI red; keep it green in the same PR.** A coverage
PR that changes what the engine serves must regenerate the files that describe
**HEAD's** engine — `subset-spec.md`, `stdlib.jsonl`, repair rules for newly
served modules — as #129 does. It must **not** relabel, re-cut or overwrite an
arm-frozen bank, target run or bundle: those describe the arm's engine, and
their only valid change is a new name at the next freeze. Record the capability
delta in the PR's CHANGELOG entry so step 7 of §4 can read it.
