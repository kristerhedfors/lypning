# Changelog

Every change that matters, newest first, one entry deep. Each links to the pull
request that carries the full reasoning — that text is not repeated here, which
is what keeps this file scannable.

The project's own history starts before its name does. *Before the name* at the
bottom is where it came from, and what the two components used to be called.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) ·
Versioning: [SemVer](https://semver.org/spec/v2.0.0.html)

---

## Unreleased

**2026-09-26** — Draw an oversize evaluation chunk in parts, so eval-2 fits the h200 ([#130](https://github.com/kristerhedfors/lypning/pull/130))

- The seed-1111 finish (HF job 6ab6a20c6b030d633f691a95) completed both test
  arms and then ran out of CUDA memory in base-eval2 chunk 26 of 51. One
  434-token prompt had padded 256 sequences to 111,104 prefill tokens.
- `--eval-prefill-tokens` (default 48,384, the largest prefill already run)
  bounds a `generate` call. An oversize chunk is drawn as contiguous parts,
  each seeded by its own cases. Chunks under the budget keep their seeds.
- Both arms split identically. `training_report` refuses mismatched budgets,
  and each split is logged, counts only, in `prefill-splits.jsonl`.
- The finish job sets `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.
- `.github/scripts/eval2_shape.py` and `eval2-shape.yml` print chunk prompt
  lengths, aggregates only. Recorded in `training/EVAL2.md` §4, 2026-09-26.


**2026-09-25** — A coverage round from the latest sessions' programs: `cap-ast`, `bytes.fromhex`, and five wrong answers the harvest exposed ([#129](https://github.com/kristerhedfors/lypning/pull/129))

- The corpus is harvested from the sessions since 2026-09-07: 5,752 new
  programs, 14,653 in total. The routers show most are blocked by
  `subprocess` and project imports, which stay refusals.
- `cap-ast` in lypning-l: `import ast` and `ast.literal_eval` over a `str`,
  parsed at the token level with a screen that refuses anything `lex.rs` and
  CPython could read differently. Every error is a refusal. `ast.parse` and
  the rest route to CPython.
- `cap-binascii`: `bytes.fromhex` and UTF-8 `decode(errors='replace')`. The
  core routes `.fromhex()` to lypning-l.
- Shared, the core included: `except ((A, B), C)`, `except A, B` and an
  f-string reusing its own quote refuse instead of raising the parser's
  SyntaxError. An exception attribute the value does not keep refuses
  instead of AttributeError. An in-place operator mutates its dict, set or
  list, and `dict |= iterable` refuses.
- The frozen core stays in 9 musl blocks by shortening fourteen long
  refusal details. Its read-only segment had 44 B of slack.
- `lypning conformance --mixture both` reaches MISMATCH 0, UNSAFE 0 and
  dispatcher agreement on this macOS host for the first time. Float `**`
  calls the host libm's `pow` on macOS (two programs filed as "drift" were
  this). A 3.14-built engine refuses the two errors whose wording 3.14
  changed. Before 3.14, module-level annotations refuse. Listing
  `os.environ` is run-specific in the grader.
- Training: recipes for the 19 new refusal kinds, and [subset-spec.md](training/prompts/subset-spec.md)
  regenerated. The `statistics` and `itertools` repair rules are withdrawn,
  because lypning-l now serves both imports and gate B would score their
  pairs as regressions. **Their pairs in the published banks still need
  retiring by the training owner**; no bank was touched.
- The Pages landing page has a dated **Coverage** section. [L-COVERAGE.md](docs/L-COVERAGE.md)
  gains the `ast` and hex rows.

**2026-09-25** — lypning-l: hold where the capability runs, one parse for every variant, refuse the staged moves the disk cannot see ([#128](https://github.com/kristerhedfors/lypning/pull/128))

- The walk only arms a run. The hold starts where a capability the core
  lacks runs, and a run whose capability never runs is answered as the core
  answers it. `LYPNING_ROUTED` is gone.
- Every compile-time SyntaxError the parser let through is the parser's own
  in every variant, including a starred tuple closed by `;`.
- `os.rename`/`os.replace` refuse unless both ends are files this run wrote.
  `os.rmdir` over a staged entry, `os.remove`/`Path.unlink` of a directory
  and `os.mkdir` under a staged file refuse too. Three of these used to lose
  a file at exit 0. After a commit, where a refusal could only be exit 1, a
  rename onto itself or into a missing directory is answered as CPython
  answers it.
- `TabError` and `IndentationError` refuse as `indent`.
- Tests compare against the interpreter running the suite; the 3.14.5 bytes
  are checked only on 3.14.

**2026-09-25** — Finish a pilot from its saved SFT adapter, and select new checkpoints case-weighted ([#127](https://github.com/kristerhedfors/lypning/pull/127))

- A `finish` stage (`launch.py finish`, `round02.yml` `stage: finish` with
  `finish_of` and `sft_step`, h200 at 720m, billed only on `SUBMIT`) runs
  `training/hf/round02_finish.sh`. It evaluates the test split and eval-2 of
  a pilot that completed SFT and then died, using that pilot's saved adapter.
  The steps are the pilot's 7f and 7g, command for command, with a checkpoint
  upload after every stage.
- `finish_lineage.py` refuses before any weight load and names the field. It
  checks the pilot's SFT completion, the seal, both bundle digests,
  `verifier_sha256`, the engine, the pilot's Space revision (fetched, never
  the head), Qwen, the seeds, the draws, the chunking and the density.
  `code_sha256` may differ; both digests and the commits between them are
  recorded as `finish_lineage`. It also compares the pinned tokenizer with
  the adapter's, before the weight pull. `finish_preflight.py` checks, for
  free before submit, the Space revision, the dispatch's arm fields, both
  bundles, `verifier_sha256`, the engine and the adapter's experiment.
- A `finish` dispatch holds the verifier Space (`SPACE_HOLD`): bootstrap
  reads and wakes it and uploads nothing, so a rebuild cannot move the head
  past the pilot's revision.
- A step other than the pilot rule's needs a dated entry in
  `finish_lineage.OVERRIDES`. Seed 1111 (HF job `6ab52a686b030d633f68e503`) is
  registered at step 1,050, where its rule chose 350. `arm_check` joins a
  pilot and its finish as one seed. Recorded in `training/EVAL2.md` §4
  (amendment of 2026-09-25) and `training/PLAN.md` Step 4.
- `CheckpointGate` rules are versioned. The default for new selections is now
  `coverage-case-weighted/2`: case-weighted coverage correct-and-native, with
  the same paired margin. `best.json` records the version. Its null admission
  stays at the stated 10% in `test_gate_admission.py`, including on a split
  with a two-case family where v1 over-admits, and on a fixture shaped like
  seed 1111 it picks step 1,050 where v1 picks 350. `code_sha256` moves;
  `verifier_sha256` does not.
- `projection.py --finish` prices the job at 445.2 of 648 minutes (re-run
  2026-09-25, at the ~65 draws/min of the pilot's base-dev).

**2026-09-24** — Count an engine-mismatch draw in GPU evaluation and GRPO instead of aborting the arm ([#126](https://github.com/kristerhedfors/lypning/pull/126))

- The seed-1111 arm-A pilot (HF job `6ab52a686b030d633f68e503`, Actions
  `36008052722`) completed SFT, then aborted in its base test arm on one
  base-model draw that reached a `lypning-l` bug. That bug's fix is held while
  the engine is frozen. Evaluation now scores such a draw as `engine-mismatch`,
  reward 0, not correct and not native, and writes its witness to the stage's
  private `engine-mismatches.jsonl` in (case, draw) order. The arm fails only
  once such draws exceed 1% of its planned draws. The overlapped and serial
  modes still write identical bytes.
- GRPO scores such a completion 0 and counts it, with the same bound per run.
  `summarize` reports `engine_mismatches` in every slice, zero included.
- Every other block still aborts, and so does a native timeout after a correct
  oracle (ledger row T4). The bound now lives in one module,
  `pipeline/mismatch_policy.py`, which the Step 2 grade also reads. Recorded as
  an amendment to `training/EVAL2.md` §4 and in `training/PLAN.md` Step 4.
- `training_report` writes each arm's count over every draw, and the round
  scripts print both counts on each `== report` line. An evaluation with no
  private mismatch file counts nothing and aborts as before.
- `code_sha256` moves. `verifier_sha256` does not, so prepared bundles still
  load. Arm A seed 1111 is re-run under this rule; its saved adapters cannot be
  carried over, because `split_eval2` refuses a changed `code_sha256`.

**2026-09-24** — A free readout of any pilot job's SFT selection ([#125](https://github.com/kristerhedfors/lypning/pull/125))

- `pilot-readout.yml` prints a job's selected SFT step, selection rule, every
  observation and the base-dev and selected headline metrics, by named
  aggregate keys only and through `public_view`.

**2026-09-24** — Keep verifier pool hosts alive across SFT's training gaps ([#124](https://github.com/kristerhedfors/lypning/pull/124))

- Both seed-1111 arm-A attempts (HF jobs `6ab4a05a52d0dbd7f1d8909d` and
  `6ab4d66d6b030d633f68d8d7`) died in an SFT evaluation on sandbox 503s, the
  second after 30 minutes of retries. The cause was not an outage: pool hosts
  shut down after 600 s without a sandbox, SFT trains 25–40 minutes between
  evaluations, and huggingface_hub 1.31.0 re-raises for a host it has already
  used instead of replacing it. Hosts now idle out after 3 h, and
  `train_verified.run` closes its verifier pool on every exit so they do not
  bill that long after a stage. A pool rebuild was tried and withdrawn in review:
  closing a pool under in-flight scorers is itself a way to lose a run.
- A sandbox-server 4xx (`SandboxError.status_code`) is refused at once instead
  of being retried for the whole budget.

**2026-09-24** — Ride out a sandbox API outage; keep case ids out of `training-prepare`'s output ([#123](https://github.com/kristerhedfors/lypning/pull/123))

- The seed-1111 arm-A pilot (HF job `6ab4a05a52d0dbd7f1d8909d`, Actions
  `35953660170`) ended about 2.2 h in, during the step-350 evaluation, when
  the sandbox API answered 503 for longer than the 155 s of retries. Transport
  retries are now time-budgeted: doubling from 5 s, each wait capped at 120 s,
  up to 30 min in all (19 attempts). Only a request that produced no response is
  retried, so no score can change. `verifier_sha256` moves, so bundles re-prepare.
- `training-prepare` printed every reference's case id and refusal detail,
  for the eval-2 benchmark too, into the GPU job's public log (four round-02
  runs since 2026-09-17). It now prints digests, split counts and a status
  histogram; the detail stays in the private `bundle.json`.

**2026-09-24** — Score each evaluation chunk while the next one generates ([#122](https://github.com/kristerhedfors/lypning/pull/122))

- `verified_evaluation.ScoringStage`: chunk i is scored on the verifier pool
  while chunk i+1 generates. One chunk is scored at a time, and the scoring
  threads draw no torch RNG. Rows, their order, metrics, witnesses and a
  blocked abort are byte-identical to the serial loop, pinned serial against
  overlapped in `training/tests/test_verified_evaluation.py`, including abort
  cases. An abort surfaces once the overlapping `generate` returns. The pool
  is cancelled and joined on every path, and a chunk interrupted mid-scoring
  is written whole or not at all. Changes `code_sha256`.
- `train_verified --serial-scoring` keeps the serial loop for diagnosis;
  `experiment.json` records `scoring` (`overlapped`|`serial`), outside every
  reuse and arm identity.
- `projection.py` prices the overlap: per call max(generation, previous
  scoring), then a final scoring tail; `--serial-scoring` reproduces the
  smoke's own projection. `training/PLAN.md` Step 4 and `training/ORCHESTRATION.md` P14 carry
  the re-run from smoke job `6ab4582d6b030d633f68c90e` (2026-09-24).

**2026-09-24** — Fit arm A's pilot to the h200 smoke: no probe, SFT step 0 from base-dev, eval-2 split ([#122](https://github.com/kristerhedfors/lypning/pull/122))

- `round02_pilot.sh`: with `GRPO_STEPS` 0 no probe runs; `grpo-skipped.json`
  says why (a probe binds adapter and code, so arm C re-probes in its own job)
  and the manifest records `probe_skipped`.
- `train_verified sft --reuse-step0 <base-dev>`: step 0 is recorded from the
  same job's base-dev draws when the fresh LoRA is a verified no-op and the
  runtime contract, draws, chunking, seed, bundle and engine match
  (`evaluation_reuse.reuse_step_zero`, provenance in `reuse.json`);
  `experiment.json` records `job_id`. Changes `code_sha256`.
- `round02.yml`: `PILOT_EVAL2` is `separate`.
- `projection.py`: models both changes and adds a `realistic` reading (a call
  lasts its expected longest draw; stated constants p, mean and tail length);
  `--reading all` prints the four readings side by side, with what whole
  scoring waves would add (`scoring_wave_minutes`). `train_verified` refuses
  `--reuse-step0` without `JOB_ID` or a completed source before the model
  load. `training/PLAN.md` Step 4
  holds the re-run from smoke job `6ab4582d6b030d633f68c90e` (2026-09-24).

**2026-09-23** — Arm A's approved configuration, an h200 hardware smoke, and an optional split eval-2 job ([#121](https://github.com/kristerhedfors/lypning/pull/121))

- `round02.yml`: `PILOT_STEPS` 1050 (one pass over 4,197 target rows),
  `PILOT_DEV_EVAL_DRAWS` 16, new `PILOT_EVAL_EVERY` 350 wired to the job's SFT
  cadence and recorded as arm field `eval_every`; `PILOT_EVAL2` stays `same-job`.
- New `hwsmoke` stage (h200, 90m, no bank): loads through `train_verified`'s
  own loaders, times 256- and 128-sequence generation, a forced full-length
  256 call, and SFT steps at ~1,024- and ~256-token rows, and projects every
  stage of the arm against 720m (`training/hf/projection.py`), as a
  measured reading with an upper and a lower bound. Each measurement is saved
  before it starts; a failure or the job's timeout is recorded, not lost.
- New `eval2` stage: runs a `separate` pilot's step 7g in a second job after
  refusing any identity field that moved; `arm_check` joins the pair.
- Fix: `experiment.json` now records `purpose`, without which every SFT adapter
  was refused at `sft-eval2`.

**2026-09-23** — Close Step 2 (flat) and Step 3 (below 300 pairs); arm A targets ready ([#120](https://github.com/kristerhedfors/lypning/pull/120))

- Full-split Step 2 over all 1,355 train cases (merge `35912725289`):
  coverage native +2.59pp [−3.94, +9.74], correct −7.13pp. No distillation
  route. Rungs total $28.12751896 charged or reserved.
- Step 3 (`35918571219`): 110–257 pair prompts by source, below the 300 that a
  preference arm needs; arm C carries the contrastive signal.
- Three target sets (1,155–1,291 cases) clear the S4 preflight at all three
  seeds; the choice, the SFT dose and the dev draws wait for GPU approval.

**2026-09-23** — Count Step 3's contrastive pair supply from the graded rows ([#119](https://github.com/kristerhedfors/lypning/pull/119))

- `.github/scripts/step3_pairs.py` and the free `step3-pairs.yml` read one
  graded positive-control run's private `grade/rows.jsonl`. They count train
  prompts that hold both a correct-native and a correct-fallback draw. The
  count is per arm, same-arm, any-arm and context-distillation (a positive from
  the spec-conditioned arm).
- It also prints pair counts capped at 1/2/4 per prompt, the refusal kinds of
  the paired negatives (engine literals only), families with a pair prompt,
  and control prompts with a native-ran or fallback draw, never paired.
- Seed 1111's probe rollouts are a separate source only when their case ids
  join the run's; the join itself is always printed, and so is whether the
  probe was graded by the run's engine.
- Aggregates only, through `public_view`. No case id, family, program or
  refusal detail is printed, and malformed evidence fails closed. Tests use
  fixtures: `training/tests/test_step3_pairs.py`. Nothing was dispatched.

**2026-09-23** — Keep verification detail private; grade an engine mismatch as a counted status ([#117](https://github.com/kristerhedfors/lypning/pull/117))

- `VerificationBlocked`'s message is a kind and a 12-hex digest only. The case
  id, test, expected and observed output, harness text and program ride on
  `.witness`. Every private witness file persists that field: the RL reward's
  `blocked-witnesses.jsonl`, the evaluation arms' `eval-blocked-witnesses.jsonl`
  and the harvest review queue.
- A Step 2 grade records an engine mismatch as `engine-mismatch`. It is not
  correct, not native and never a target. Its witness goes to the private
  `grade/engine-mismatches.jsonl`, and only the count is public. A grade fails
  above 1% of its graded draws, and the merge re-checks that bound over the
  union. A grade with no mismatch writes byte-identical files, pinned against
  a fixture.
- `step2_grade.py` prints a failure's type and digest only. Its traceback
  goes to the private `grade-failure/<run>` through a new `if: failure()`
  step.
- Messages that the GPU job prints now name a case only as
  `case <sha256[:12]>`. That covers reference admission and the prompt
  budget. A trainer `KeyError` prints only its type and a digest of the key.
  If a grade is aborted by another block, it still keeps the mismatch
  witnesses it had already found.
- `training.py` changed, so `verifier_sha256` moved and every prepared bundle
  must be re-prepared.

**2026-09-23** — Keep `case_clusters` private: every public printer strips it ([#116](https://github.com/kristerhedfors/lypning/pull/116))

- The operator decided that per-case `case_clusters` counts stay private.
  `metrics.json`, `best.json` and reports keep them unchanged for offline
  re-selection; only what is printed loses them.
- One helper, `training/pipeline/public_view.py`, removes the key at any
  depth. `s0_inventory`, `step0_summary`, `loss_summary`, `hf_status`, the
  other `.github/scripts` report printers and the round scripts' printed JSON
  (`== report`, `== probe verdict`, `== manifest`) go through it, and so do
  the two files uploaded as public Actions artifacts, `public-report.json`
  and `s4-target-floor.json`.
- `training/tests/test_public_view.py` drives each printer with a fixture
  carrying the key, and guards that no script, round-script Python body or
  workflow prints a metrics, best or report object, or names the key, outside
  the helper.

**2026-09-23** — Resume a stopped Step 2 run explicitly, never rerun it ([#115](https://github.com/kristerhedfors/lypning/pull/115))

- `step2-control.yml` gains `resume_run_id`. A new run proves it is the same
  experiment as the stopped one, refusing on the first field that differs. It
  requests only planned − settled, and the ambiguous request again as a
  recorded `re-request`. `ceiling_usd` is the chain's total, so the prior
  dollars count against it.
- A resumed result reports the union's completed/planned and a `resumed_from`
  chain with each run's own dollars. `step2_grade` and `step2_merge` read the
  union, and a request settled in two runs is refused, not deduplicated.
- A run that another run already resumed is never resumed again: the fork
  would buy the successor's requests twice, outside the chain's ceiling.
- The `full` rung's rate is `full_rpm`, 30–45; the shard gate sizes by it.
  A transport error is a resume, not a rerun. Nothing was dispatched.

**2026-09-23** — Publish attributed sightings; Codex/GPT programs stay excluded
([#114](https://github.com/kristerhedfors/lypning/pull/114))

- Sightings re-exported with `lypning harvest --export --transcripts`, which
  also journaled 15,307 model attributions to `attribution.jsonl`.
- A pre-registered test found Codex/GPT-written programs not materially
  similar to Claude's: 444 vs 4,914 distinct programs, all three criteria
  failed (`training/reports/2026-09-23-codex-similarity/`). They stay out of
  the capture tier.

**2026-09-23** — Capture at all times with model attribution; a capture-tier
export; the sharded arm-A target rung ([#113](https://github.com/kristerhedfors/lypning/pull/113))

- User-scope install registers only the Bash capture hook, reports when it
  cannot reach lypning, and a Stop hook never exports into a repository that
  is not a lypning checkout. Write-then-run `.py` files and Codex rollouts
  (`lypning harvest --codex`, read-only) are now harvested; a tool call seen
  by both scopes counts once.
- Every model harvest resolves is appended to `attribution.jsonl`, so which
  model wrote a program survives transcript expiry.
- `nt capture-export`: static-AST quality tiers that execute nothing,
  contamination checks, and model ranking. 179 tier-A distinct programs from
  the live log on 2026-09-22. A separate tier, never merged into bank v3.
- `step2-control.yml` gains a sharded `full` rung over the 1,163 cases run
  `35767396604` did not draw, and `step2-merge`; `round02.yml` carries the
  dev-draw and arm-C knobs. Nothing was dispatched.

**2026-09-22** — Refactor the S4 pipeline before arm A's first seed, and review Step 2
([#112](https://github.com/kristerhedfors/lypning/pull/112))

- Record Step 2's paid Cerebras rungs (PRs #103–#111), which Codex dispatched
  under the per-rung ceilings; the approval is not recorded in this tree.
  Smoke `35751938025` cost $1.20715678 charged or reserved. Its grade
  `35759939928` pooled controls with coverage: native +14.78pp, correct
  −9.01pp. Target rung `35767396604` completed 1,536/1,536 for $3.63061517
  and is graded once, after this merges. Commit Codex's report verbatim with
  an independent review,
  `training/reviews/2026-09-22-claude-step2-s4-review.md`: revise (prepare),
  no GPU spend yet.
- Selection and the Step 2 decision now read the coverage population only.
  The checkpoint margin is a paired, case-clustered standard error
  (`training/tests/test_gate_admission.py`), and grading refuses incomplete
  runs.
- The split seed is fixed at 1111 for every training seed. Arm A launches
  with GRPO dose 0 on h200 / 720m. The 1,000-case floor now counts distinct
  target cases, which no existing rung reaches. `QWEN_REV` is pinned in
  every round-02 workflow and the grader.
- The torch-reference gated-delta kernel is enforced before the weight pull,
  and the bound kernel is recorded. LoRA init is reseeded per seed. SFT rows
  are drawn without replacement within each family.
- Arm C's recipe is 4 prompts × 8 generations and LoRA LR 1e-5; the launcher
  keeps 4 generations so the probe stays comparable with seed 1111's.
  No-signal groups are logged as a fraction, never an abort. Dev-selection
  draws get their own setting (`--dev-eval-draws`, default 4) and arm field.
- `verifier_sha256` now covers the sandbox runner and container worker, so
  bundles must be re-prepared. The legacy LoRA runner, `cheatscan.py` and
  unread `*-sft.jsonl` views are removed.

**2026-09-22** — Prepare the positive control on the actual training split
([#102](https://github.com/kristerhedfors/lypning/pull/102))

- Add a free private-bank/tokenizer cost plan for both sixteen-draw prompt arms.
- Verify the large engine in the pinned training Python runtime without a provider call.
- Bound reference-check concurrency by worker resources; report progress and
  estimated remaining time, preserve partial counts, and fail closed on timeout.
- Add a tested reservation ledger for hosted calls, with no retries or implicit resume.
- Record the Step 1 merges and free admission result; paid execution remains gated.
- Remove the retired MicroPython runtime, build path, shim library, oracle command,
  benchmark control, mismatch ledger, package assets and CI jobs. Routing,
  conformance and training reports now describe only the Rust spectrum and CPython.

**2026-09-22** — Make sized stdin reads consume only the requested characters
([#101](https://github.com/kristerhedfors/lypning/pull/101))

- `sys.stdin.read(n)` counts Unicode characters and advances the shared stdin
  cursor, preserving the original bytes for dispatcher replay after refusal.
- Zero-length reads do not wait for input; invalid argument lists raise before
  consuming it. Invalid UTF-8 and allocation-overflow-sized reads refuse cleanly.
- Adds differential tests for both Rust variants, interleaved stream operations,
  Unicode, argument errors, zero-length pipes and fallback replay.

**2026-09-22** — Reuse equivalent evaluation arms and enforce the job deadline locally
([#100](https://github.com/kristerhedfors/lypning/pull/100))

- Reuses completed eval-2 draws only with sealed step-zero policy equivalence,
  matched runtime/sampling contracts and complete case coverage; records provenance.
- Raises evaluation batches to 256 sequences and the proposed pilot ceiling to
  720 minutes, with a process-group deadline covering bootstrap and training.
- Adds a metadata-only audit of the prior job timeout, without printing logs,
  environment variables, credentials or private cases.

**2026-09-22** — Apply the planned LoRA learning rates and evaluation cadence
([#99](https://github.com/kristerhedfors/lypning/pull/99))

- Defaults to SFT 1e-4 (2e-4 under 100 effective steps) and GRPO 5e-6;
  explicit overrides remain recorded in the effective schedule.
- Aligns trainer, pilot script, example plan and manual commands on evaluation
  every 50 steps, with the final checkpoint retained and evaluated.

**2026-09-22** — Preregister the benchmark family floor and expose slice fragility
([#98](https://github.com/kristerhedfors/lypning/pull/98))

- Applies the five-distinct-case floor to benchmark primary metrics and paired
  comparisons while preserving source-group links and all slice diagnostics.
- Records the policy in each new evaluation manifest and rejects mixed policies.
- Adds case-weighted slice rates and a reproducible size-only stress simulation
  from the saved Step 0 bundle histograms; see `training/EVAL2.md` §4.

**2026-09-21** — Select on the metric being trained for, and stop stopping
([#97](https://github.com/kristerhedfors/lypning/pull/97))

- `pipeline.training_metrics.CheckpointGate` ranks the correct-and-native
  family macro behind two tolerances — gate A at −2pp on the all-family
  correctness macro, and per-population retention at three standard errors —
  in place of nine hard floors on ~180-draw sub-metrics and a
  correctness-first lexicographic key. Capability regressions are reported in
  `best.json`, not cast as vetoes.
- A candidate must clear the starting policy by the macro's own standard
  error: a pure argmax on a noisy metric admits the null about half the time,
  because the best of several noisy evaluations is biased upward. The margin
  is the 90th percentile, so the constant is the rule. On seed 1111's dev
  split it is 1.07pp.
- `training/tests/test_gate_admission.py` runs the same simulation against the
  real class and now measures **null 9.17%, +10pp native 84.85%** (4,000
  trials, seed 7), inverting the two assertions it was written to pin.
- Selection is post hoc and never stops training: `observe` returns nothing,
  `--patience` is gone, and `best.json` keeps every observation with the
  reason it was or was not selected. Three rejected checks under the old gate
  ended seed 1111's registered 20-step GRPO dose at 15, and Step 0 could not
  read an adapter that was never saved.
- `summarize` reports `by_family`; the Step 0 CI reader drops it with
  `by_capability`, because both are keyed by private labels and it streams
  into a public log.
- Completes `training/PLAN.md` Step 1.1–1.2 and records what the ≤ 10% half of
  its own acceptance rule forced. Items 1.3–1.6 stay open. No paid or GPU step.

**2026-09-21** — Read the saved checkpoints before changing the instrument
([#96](https://github.com/kristerhedfors/lypning/pull/96))

- Completes `training/PLAN.md` Step 0 with a revision-pinned, aggregate-only
  CI reader and a separate Codex assessment of the seed-1111 report. No
  private task, program or expected output is published; incomplete saved
  evaluations fail closed.
- GH run `35575454075` on 2026-09-21 finds no checkpoint meeting the rescue
  rule, no saved GRPO step 20, 635 train correct-fallback draws and an
  803-case / 19-family benchmark. `training/reports/2026-09-21-codex-step0-read.md`
  records the decision: Step 1 next, with corrected macro-slice and pair-supply
  premises. No paid training or model inference was launched.
- Repairs two unqualified training-document paths in the preceding changelog
  entry so the docs site's reference check passes.

**2026-09-21** — Seed 1111 read, the selector's blindness pinned, and a plan a session can follow
([#95](https://github.com/kristerhedfors/lypning/pull/95))

- Seed 1111 of round-02 on bank v3 ran through SFT, probe, GRPO and all three
  test arms (job `6ab01cbb51992417dfccd64c`; test 315 cases, 4 draws: correct
  0.8667, correct-native 0.6873, identical in every arm) and selected step 0
  twice. **That is not a result about the training.** Simulated on the round's
  own dev baseline, `CheckpointGate.observe` admits a +10pp correct-and-native
  checkpoint 1.7% of the time against 2.1% for noise, because it demands no
  correctness regression on nine ~180-draw sub-metrics and ranks correctness
  before nativeness. `training/tests/test_gate_admission.py` pins this against
  the real class until the selector is fixed.
- The read (`training/reports/2026-09-21-fable-round02-seed1111-read.md`): SFT
  loss 0.144 → 0.043 from a base already at ≈1.15 perplexity on the references,
  under a full-fine-tuning learning rate; GRPO ≈ 4 informative steps of 20;
  ~12.6pp of the bank's headroom is one 1-case family slice in the macro; the
  architecture, pipeline and seed determinism were fine.
- `training/PLAN.md` is the live plan — five ordered steps with cost, decision
  and state — and `training/START_NEXT_ROUND.md`, `training/STATUS.md` §10,
  `training/ROUND_READINESS.md`, the orchestration ledger and the `round02-plan` skill
  point at it.
- Fixes a `training/STATUS.md` section reference in an earlier entry that had
  `test_docs` red on `main`.

**2026-09-20** — Three seeds are one result only if they are one experiment

- A complete S4 replicate is seeds 1111, 2222 and 3333, read from three
  `job-manifest.json` files. **Nothing related those three to each other**:
  `s0_inventory` could print a manifest and no code anywhere compared two. So
  seeds run weeks apart, across an engine fix or a re-cut bank, would be
  combined by hand and nothing would say they were different experiments —
  silent, about $27 a seed, and discoverable only by someone remembering what
  changed between two dates.
- `arm_check.py` enforces bank, Space revision, model revision, the training
  dose, the evidence dose and **sandbox density**: `native` is host-load
  dependent, so packing more sandboxes onto a host changes what the label
  means. Host count is deliberately excluded — a cost ceiling is not an
  instrument, and raising it between seeds must not invalidate a result.
- `commit` and `kernels` are reported, never enforced: a workflow fix and an
  engine change are indistinguishable from here, and a check that fired on
  every unrelated commit would be turned off. `kernels` reads `unrecorded` on
  manifests written before today rather than being compared against nothing.
- Runs as preflight check 5, before any seed is billed.

**2026-09-20** — A training stage that says where it is

- The SFT stage emitted nothing between its banner and its end. `train_sft`
  writes a row per step to `loss.jsonl` and `round02_pilot.sh` uploads per
  STAGE, so a reader had no step, no rate, and no way to tell whether the wall
  clock would be met. Two rounds died at that wall; on 2026-09-20 a live round
  ran 70 minutes of SFT during which the only honest answer about its progress
  was "unreadable".
- `checkpoint` now logs `<stage> step N/total <metrics>`. It is the one place
  that runs on a schedule inside the stage, and `core.log` stamps elapsed
  seconds — so two lines give the rate and the rate gives the finish.
- Numbers only, enforced by test: the follower streams the job's stdout into a
  **public** Actions log, and a future `metrics` key holding a case id or a
  program would otherwise walk straight into one.

**2026-09-20** — A pin a fallback can satisfy is not a pin

- The live round is running with `flash-linear-attention==0.5.2` pinned and
  installed — `runtime_versions` passed — and transformers still reports it
  "not installed" and puts all 48 gated-delta-net layers on the reference
  PyTorch path. The distribution is present; the module will not import.
  `runtime_versions` reads distribution metadata, and metadata was not the
  question.
- `kernel_state()` asks the question transformers asks — can the module be
  imported — and the run record carries the answer. The probe/GRPO contract
  compares `kernels` alongside the tokenizer and model config, because
  `training/STATUS.md` §2 records a kernel swap on identical weights moving ΔSLR by
  +1.57pp, larger than either adapter of 2026-09-14 moved it.
- It also reports `NTX_USE_FLA`, which `train_verified.run` sets to `0`: an
  importable kernel can still be deliberately unused, and the manifest should
  distinguish that from one that would not load.
- It never raises, and catches `BaseException` — a kernel import can fail on a
  missing CUDA symbol. An observation written into a manifest must not be able
  to end a metered round.

**2026-09-20** — Ask the reference question on the machine that answers it

- `bank_publish.py --verify-references` ran on `ubuntu-latest` while the
  verifier runs in `python:3.12-slim`. `ubuntu-latest` ships locales the slim
  image does not, so the check passed the `locale.setlocale` case that then
  ended a round at `last_stage: prepare` — it had been asked of the wrong
  machine.
- `verify_references.py` runs the same check **inside** the pinned image.
  `sandbox.run_python` spawns `sys.executable`, so putting the script in the
  container is the whole fix; `training/pipeline` is stdlib-only, so it needs
  no `pip install` and cannot drift by resolving a wheel.
- `bank_publish.py` takes `--reference-report` instead of redoing the check on
  whatever runner it stands on, and refuses a report cut against a different
  union. The workflow reads the image from `launch.BASE_IMAGE` rather than
  copying a digest into YAML where it would drift.
- The manifest records `references_verified_in`, not a bare boolean:
  `references_verified: true` was already true of the check that missed the
  case. The runner-side path now says so in the bank.
- First tests for `.github/scripts/bank_publish.py`, which had none. Family
  exclusion stays — depending on the clock or the tz database is a property of
  the construct, which no per-case run in any single image can see.

**2026-09-20** — The exact-fit pool: give the hosts back, and never ask for every slot

- A round died at its **second** preparation with `Pool needs 1 more host(s)
  but max_hosts=16 allows only 0 more` — after the pilot bundle was built, so
  the expensive half was paid for and discarded. Two causes, both fixed.
- **Nothing had ever called `close()`.** A round's stages are separate
  processes sharing one named pool, and the hosts outlived the process that
  booted them with nobody to hand them back; the next stage adopted them at
  whatever occupancy they carried. `prepare` now releases through
  `release_runner` in a `finally`, so the failure path releases too — a retry
  must not meet the pool it just filled. Release is best-effort: the bundle is
  already written and hosts idle-time out, so failing to release must not fail
  a finished preparation.
- **The launch guard admitted equality.** `16 x 4 = 64` slots for
  `--score-workers 64` reads like full utilisation and is the one shape with no
  recovery, because `SandboxPool.create` raises rather than waits once every
  host is full. A multi-host pool now needs a full host of slack; a single-host
  pool does not, having no cross-host packing. `PILOT_SCORERS` 64 → 48 and the
  default scorer count 16 → 12, which was the same exact fit at 4×4.
- Three tests that fail against the code as it was, including one that reads
  `round02.yml`'s own numbers — the scorer count lives in YAML and the rule
  admitting it lives in Python, and nothing else related the two.
- **And the race the release would have created.** A host cancelled a moment
  ago still answers `list_jobs(status="RUNNING")`, and pools adopt hosts by
  NAME — so the next stage could adopt twelve dying hosts, then need twelve
  live ones, and hit `max_hosts` again. Each stage now names its own pool
  (`pilot`, `eval2`, `grpo`), so the race stops existing instead of becoming
  narrow enough to usually win. The tag could not carry this: a job id is
  exactly 24 characters and the tag truncates to 24.
- `round02-preflight` carries it as ledger row 7.

**2026-09-19** — A round's bank exists, carved and priced: every preparation but the operator's

- Generation over the widened pool drew **all 71 families** in one run
  (`35431441875`: 9,154 candidates, 28,799 calls, stratum 0.736 against the
  preregistered 0.710), and four adapt shards banked 5,961 cases over 64–69
  families each.
- `banks/v3-20260919` is cut from six batches and published (run
  `35439152832`): pilot 8,370 cases over 47 families (44 coverage, 18 control),
  benchmark 4,633 over 22 (22 coverage, 6 control), sharing no family, the
  pilot admitting at every protocol seed. `BANK_PATH` points at it; `banks/v2`
  stays on the Hub and is not it.
- Priced on that bank (run `35439221438`): `--steps 300` exposes 193,677 /
  197,707 / 191,153 supervised tokens against the 50,000 floor, 4,840 / 6,755 /
  6,335 train cases against the 1,000 floor, no row over `--max-seq`. Its
  examples are ~3.7x longer than bank v2's, so its smallest clearing `--steps`
  is 74 where bank v2 refused 250.
- **A family can carry both populations, and refusing that was wrong.**
  `synth.judge` labels a case by what the engine did, not by the pool its
  construct came from, so a ceiling-stratum candidate the model happens to
  write natively becomes a coverage case and its family carries both. Five of
  bank v3's do; bank v2 had none, which is why reading bank v2 said otherwise.
  The family is allocated whole and counted toward both populations.
- Two shell defects in the new publish workflow, both caught before they
  mattered: a digits-only batch-id rule refused every sharded batch this
  repository produces, and `[ test ] && assign` is a statement whose exit
  status is the test's, so under `set -e` a false boolean input ended the step.

**2026-09-19** — The supervised-token floor, counted before it is billed rather than after the weights

- Run `35434623069`, free and tokenizer-only: on the bank the pilot would have
  used, `--steps 250 --batch-size 4` exposes **46,535 / 45,952 / 44,940**
  supervised tokens at seeds 1111/2222/3333 against the 50,000 floor. `run()`
  refuses that before the first optimizer step — after the dependency install,
  the bank download, bundle preparation, the unadapted base-dev arm and 55.6 GB
  of weights. At roughly $25 a seed, the round would have bought nothing.
- The `--plan` upper bound for that same schedule is 134,384 and passes. "A
  passing plan is not a certificate" has been in the tree since 2026-09-17 as a
  sentence; this is the number.
- `round02.yml`'s billed `submit` now `needs: token-floor`, a free job that
  counts exactly and exits 1 when short. Both read one `PILOT_STEPS`, so the
  schedule counted is the schedule billed. `PILOT_STEPS` is 300; 250 is refused
  and the smallest clearing value on that bank was 269/273/279 by seed.
- `token_floor.py --require-clears` is the gate. Without it the script stays a
  report, where a refusal in the grid is the deliverable and a non-zero exit
  means a bad input — the contract its docstring already stated.
- The guard held: the job's Hub cache finished at 25 MB, so it fetched a
  tokenizer and no weights.

**2026-09-19** — Carve a bank into a pilot and a benchmark that share no family

- `split_cases` divides ONE bank into train/dev/test, and every split it makes
  is trained against or selected on. A round also needs a bank nothing in
  training has seen — the benchmark the endpoint is read on (`training/EVAL2.md`
  §4). Only the first operation existed: bank v2's two banks were separated by
  hand, which is why nothing could re-make the decision and why bank v3 had none.
- `pipeline.bank_carve` / `nt bank-carve` allocates families per population —
  every family carries exactly one, measured over bank v2 on 2026-09-19 — and
  writes nothing unless both banks are admissible. The pilot must pass
  `validate_pilot` at EVERY protocol seed, not the one the caller passed: a bank
  admissible at 1111 and not 2222 fails a three-seed round halfway through.
- The floors are reported before they bite: ≥18 families per bank, and ≥6
  control families in the pilot, because `split_cases` hands each split
  `max(2 if groups >= 6 else 1, groups // 6)` of a population and
  `validate_pilot` wants two per split. With the benchmark's ≥2 that is 8
  control families minimum, against the 10 bank v3 had before the pool widened.
- Checked against the only carve that has an oracle: bank v2's hand-made 51/18
  family split, which re-uniting and re-carving reproduces.
- Two defects in this session's own test fixture, both worth knowing because
  they are properties of `split_cases` rather than of the test. Reusing one
  `reference` across families merges them, because the union is by
  source/family/SOLUTION; and making it unique with a comment does not, because
  `solution_fingerprint` hashes the AST dump and comments are not in it.

**2026-09-19** — The stdlib corpus becomes a measurable arm, and what is left before a GPU gets a document

- A stdlib unit **cannot** be a bank case, and this is a property of the
  validator rather than a preference: `validate_cases` requires three
  independently specified tests whose outputs differ, and a unit is a
  self-contained program with no stdin, no argv and one fixed stdout.
  `pipeline.stdlib_sft` therefore emits supervised rows, never cases, so they
  never reach a split or a benchmark. `nt stdlib-sft` is the command.
- The corpus overlaps the bank's own surface almost completely: 14 of the 15
  modules it fills are also in the construct pool (measured 2026-09-19).
  Against a realistic bank-v3 family list, **19 of 34 units are contaminating**
  and are held back by name, leaving 15 rows over 9 modules. Mixing a
  `textwrap.wrap` unit into a round whose benchmark holds a `textwrap` family
  is teaching toward the test; `--allow-overlap` keeps them and says the
  result must be read as contaminated.
- The overlap gate could not fire when it was written. A held-out token arrives
  as a capability (`textwrap`), a dotted surface (`textwrap.fill`) or a
  `synth.family_of` family, which hyphenates (`textwrap-fill`); the reduction
  split on `.` only, so no family matched and the gate reported a clean bank
  while every unit overlapped it. `test_stdlib_sft.py` pins all three spellings.
- Three units fill builtins (`dict.fromkeys`, `str.center`, `str.translate`)
  and carry no module, so the prompt no longer tells a model not to import
  something that has no import.
- The corpus is **deliberately not in the first round**. Running it mixed and
  nothing else would say what the round scored and not what the corpus bought;
  the round runs without it first so an arm is left to compare against.
- `training/ROUND_READINESS.md` is what is left before a GPU is booked, per
  step, with state: the bank and the gates are no longer the blocker, the
  remaining work is the pilot/benchmark carve, the publish, the token floor and
  the operator's approval. `training/STATUS.md` §10 still owns which round runs.

**2026-09-19** — The construct pool, not the row count, was the gate on a real round

- A pilot bank and a disjoint eval-2 benchmark bank each need ≥18 independent
  families (`training_data.validate_bank`), and one family is one target
  construct, so the pool caps both. At 27 rewritable + 10 unrewritable it did
  not fit: an eval-2 bank wants ~16 coverage families and a pilot ~12, against
  27 that exist — off by one before any margin, and tighter on the control
  side, where `validate_pilot` needs ≥2 control families per split (6) out of
  10 that must also stock the benchmark. bank v2 shipped 51 + 18 = 69 disjoint
  families; 37 cannot be carved into that shape at any row count, which is why
  the 7,042-row re-adapted bank passes `validate_pilot` and still cannot host a
  round with a held-out benchmark.
- The pool is now 47 rewritable + 24 unrewritable = 71 distinct families, no
  collisions. Every addition was probed against the built engine on 2026-09-19
  and is REFUSED, and every rewritable one carries an `engine-addressable`
  verdict in `levers.DECLARED`, so the pair teaches a substitution the served
  subset can express.
- What was deliberately left out, and why it matters: `collections.Counter`,
  `collections.defaultdict`, `math.gcd`, `math.isqrt`, `math.factorial`,
  `os.path.splitext`, `json.dumps` and `re.findall` all came back SERVED.
  Generating toward constructs the engine already runs is exactly what left
  bank v2 at 0.9686 correct-and-native with no room for the preregistered
  effect. `secrets` was rejected outright: a control still has to print the
  same bytes every run.
- The stratum draw is unchanged. `generate` picks the stratum at
  `REWRITE_FRACTION` before it indexes a pool, so pool sizes do not move the
  preregistered 66:27 mixture (0.7162 over 10,000 draws at seed 1111).

**2026-09-19** — A repair rule is a capability claim; make the table say so, and make it fail when the engine catches up

- A repair rule rewrites a refused module into the served subset and the
  rewrite verifies natively, so the module's used surface is expressible there
  by construction — the rule is the proof, and `fractions` was withdrawn on
  2026-09-18 precisely because its rule could not be. `levers.DECLARED` had no
  verdict on eight of the twelve modules the rules rewrite (functools, operator,
  copy, heapq, bisect, array, decimal, calendar; measured 2026-09-19 with
  `levers.classify("module", "import <m>")`). They are declared
  engine-addressable now under a third provenance, `rule`: the row is matched
  by the rule existing, not by a capture-corpus family, so `declared_unused`
  still catches a verdict whose rule was withdrawn, and a module the corpus
  never refused still has its verdict on record before its pairs reach a model.
  None of the eight appears in the capture corpus, so the reviewed §4 vector
  does not move.
- `repair_rules.MODULES` names the list, and `test_repair_rules.py` holds two
  invariants over it: every entry carries an engine-addressable verdict (fails
  8/12 before this entry, 0/12 after), and none is a module the built engine
  serves. The second reads the engine through `legality.modules_served`, the
  same probe gate B uses, with `glob` as a served sentinel so a broken probe
  fails loudly instead of passing vacuously.
- Why it matters: gate B probes the engine, not a table. The day the engine
  gains `bisect`, every bisect repair pair becomes a supported-import
  regression. The drift test makes that day a failing test, not a voided
  round.
- `synth-adapt` reports `rule_verdicts` beside `rules_fired` and renders a
  loud line for any rule without an engine-addressable bucket. Derived from
  `levers` at report time, never written on the case.
- The re-adapted bank-v3 clears the plan-time gates at all three protocol
  seeds, read in CI over the union of batches 35422748954 and 35422741922
  (run 35430465717, 2026-09-19): 7,042 cases after one cross-batch duplicate
  was dropped at the normaliser, train 5,482–5,964 against the 1,000 floor,
  `validate_pilot` and `validate_benchmark` OK. Dev and test hold 4 families
  each against 29 in train, which the gates do not check and a family-clustered
  bootstrap will feel. `bank3_gates.py` is the read; `bank-gates.yml` runs it.
- One engine witness from batch 35422741922 reproduced against a fresh build
  and is filed in `training/data/engine-mismatches.jsonl`: `sys.stdin.read(n)`
  ignores its size argument and returns the whole remaining stream
  (`readline()` is correct). Invariant 1: a bug, never a data point; it moved
  from the private Hub to the file as a CI artifact, never a log line.
- Withdrawn from this session's own analysis: "itertools is deliberately
  excluded because lazy-iterator semantics sank cap-csv and cap-glob". Both
  landed in iteration 77 (`docs/HILLCLIMB.md`); the engine serves `iter`,
  `next`, generator expressions, `zip`, `enumerate` and `map` (rc 0 on all,
  2026-09-19) and refuses only `yield`. `itertools` was already declared
  engine-addressable from §4, and `rule_itertools` is the proof.

**2026-09-19** — Count the tokens the prompt budget was supposed to be counting

- The per-case prompt budget in `train_verified.py` had been vacuous since the
  experiment pinned transformers 5: `apply_chat_template(tokenize=True)` returns
  a plain list of ids under 4.x and a `BatchEncoding` under 5.x, both answer
  `len()`, and the 5.x answer is the number of KEYS. So
  `len(prompt_ids) + args.max_new_tokens > args.max_seq` evaluated
  `2 + 1024 > 4096` and admitted a prompt of any length whatsoever. Measured
  2026-09-19: `uv run --no-project --python 3.12 --with transformers==5.17.0`,
  `len(BatchEncoding({"input_ids": [0…43], "attention_mask": […]}))` is **2**
  while `len(be["input_ids"])` is **44**. The comment above the check called it
  an admission check "before downloading 27B weights"; it was two lines of
  arithmetic on a dictionary.
- The extraction has one home and a name that says what it is:
  `pipeline.training.chat_prompt_token_ids` renders with the three settings the
  SFT examples and the held-out generation already agree on, accepts both
  shapes, unnests a batched row, and **raises** on a third shape rather than
  counting whatever it was handed. The arithmetic has one home too —
  `train_verified.check_prompt_budget`, module level rather than inline in
  `run()`, because inline it was reachable only behind `import torch` and so
  could not be tested at all. That is the whole reason it could rot.
- `.github/scripts/token_floor.py` refused to run on the environment its own
  workflow pins. Its new shape guard demanded a `list` of `int`, which is
  exactly what transformers 5.17.0 does not return, so the job would have
  exited 2 on a correct tokenizer; and the sweep on the line below it took
  `len()` of the same call, so had the guard passed, every bank row would have
  been reported as a two-token prompt. Both now count through
  `chat_prompt_token_ids`, and the guard refuses only what that refuses.
- `training/tests/test_prompt_budget.py` is the regression, and two of its twelve
  tests are sweeps rather than cases: nothing outside the extraction may call
  `apply_chat_template(tokenize=True)`, and no call site anywhere in
  `training/gpu/`, `training/pipeline/` or `.github/scripts/` may take `len()`
  of a chat-template render. Against the unfixed tree the sweeps name the three
  offending lines; the behavioural tests drive a 4,000-token prompt through a
  `BatchEncoding` double against a 2,048 budget and require the refusal.
- **Amends the entry below.** "The base-dev arm of the same job did complete,
  which rules out … the per-case prompt budget" was true for the wrong reason:
  that arm ran a check that could not fail. What the completed base-dev arm
  actually shows is that the dev prompts generate (256 cases, truncation 0.0,
  mean completion 96.87 tokens, job `6aacd5cfb1dc2b62dc590b82`, 2026-09-18).
  The candidate list for the `sft` death gains one entry and it is not this
  check: `build_examples` drops any row whose whole turn exceeds `--max-seq`
  and `run()` then raises *"SFT rows over token limit"* — before the download
  and before the mkdir, which is where that job died. `token_floor.py` decides
  it for free, now that it can run: it prints rows over budget, rows dropped,
  and the mean and longest prompt over the same bank, tokenizer and revision.
- Not a tokenizer bug but found by the same sweep: `pipeline/sample.py` carried
  `\$` inside a plain docstring, a `SyntaxWarning` today and a `SyntaxError` in
  a later Python.

**2026-09-19** — Stop one generated row from throwing away a whole adapted batch, and shard adapt so a full generate run fits its job

- The adapt job of GH run 35399909232 did not run out of memory, which is what
  a step that prints nothing for 38 minutes looks like. It printed the reason
  on its last line: `synth-adapt blocked: harness: setup file escapes workdir:
  '/data/logs.txt'` (job 105802008535, log read 2026-09-19). One candidate
  asked for an absolute path, `sandbox.materialize` called that a *harness*
  error, and a harness error is ours and aborts everything — so 2,078
  already-judged candidates were discarded and nothing was written. Three of
  the artifact's 7,967 rows carry such a name — rows 2078, 2082 and 2937, the
  first of which is where the job stopped.
- `synth.validate_candidate` now applies the input-path rule before anything is
  executed, so the row is rejected as malformed and the batch survives. The rule
  has one home, `training_data.unsafe_input_path`, which `validate_cases`
  already enforced downstream — the candidate validator was simply not asking.
- **A NUL is the second defect class, and it is independent of the first.** A
  NUL byte inside an `argv` element or an input-file name never reaches a
  verdict at all: `subprocess.Popen` and `Path.mkdir` raise `ValueError`, which
  is neither a harness error nor a program result, so it left `synth.run` as a
  traceback and took the batch with it by a different door. `validate_candidate`
  rejects it as malformed too, with its own parametrised regression tests in
  `training/tests/test_synth.py`. File *content* may still hold a NUL — bytes on
  a pipe and bytes in a file both arrive intact, and `validate_cases` admits
  them. Three of GH run 35340137976's 2,784 candidates carry one, at rows **28,
  31 and 1712**; row 461 of that same artifact is the *other* class, an escaping
  file name, and is not a NUL row. Four of GH run 35399909232's 7,967 carry one,
  at rows 2380, 3498, 3501 and 5058 (`gh run download <run> -n
  bank-v3-candidates`, then a scan of every `argv` element and input-file name
  for `\0` and through `training_data.unsafe_input_path`, 2026-09-19).
- **Sharding alone would not have saved that run, and the arithmetic says which
  shards.** `size = ceil(7967 / 4) = 1992`, so shard 1 is rows 1992–3983 and
  shard 2 is 3984–5975. All three escaping-path rows fall in shard 1 and one
  NUL row (5058) falls in shard 2, so **two of the four shards** would still
  have aborted and two would have completed. The guard is what saves those two;
  the matrix only bounds the loss (same scan, same date).
- The input-path rule now has the one home the bullet above claims for it.
  `eval2_bank.check_tests_shape` held a third, verbatim copy, and a copy does
  drift: that one never grew `unsafe_input_path`'s directory-collision clause,
  so an authored eval-2 proposal naming both `d` and `d/x` passed the cheap
  shape check and was charged to `invalid-case` after the reference had been
  executed twice and the engine consulted. It is charged to `tests-shape` now,
  at the first rule it fails, and
  `test_this_module_holds_no_second_copy_of_the_input_path_rule` is the grep
  that keeps the claim true rather than merely written down.
- The memory theory is recorded as falsified rather than dropped: the 7,967
  candidates occupy **38.2 MB** resident once parsed, 39.8 MB with a full
  retained rejected list, against a 16 GB runner (measured 2026-09-19 on the
  `bank-v3-candidates` artifact of that run, which is 18,290,882 B of JSONL —
  the 2,956,177 B is the compressed artifact).
- Adapt is four shards, because the batch no longer fits one job even when it
  does not crash: 2,078 candidates in 38m54s is 1.12 s each and projects 7,967
  to ~149 min against a 90-minute cap. `--offset` is the new flag, `--limit`
  was already there, and the divisor is `strategy.job-total` so the slice
  cannot drift from the matrix. Each shard banks its own `<run_id>-<shard>`
  batch, the matrix is not `fail-fast`, and publish runs `if: !cancelled()`:
  a shard that dies costs a quarter of a paid run, not a run.
- The safety split is unchanged and is the reason the shards are shaped this
  way: generate holds `CEREBRAS_API_KEY` and executes nothing, every adapt
  shard executes model-written code and holds no secret, publish holds
  `HF_TOKEN` and runs no candidate code.

**2026-09-19** — Reconcile the programme ledger with the banks, the base arm and the instrument finding that unblocked generation

- `training/STATUS.md` named a blocker that two PRs had closed and an admitted
  case count off by a factor of seventeen. The supply ratio is no longer
  backwards: bank v2 admits **1,120** train cases over 33 independent split
  components against a 597-case benchmark, which is what `split_cases(seed=1111)`
  and `validate_pilot` return over `training/data/bank_v2/`, re-run 2026-09-19 —
  not the 1,689 lines of the file, because dev (256) and test (313) are held out.
  The GPU job's own bundle prints the same three numbers (GH run 35399900848,
  2026-09-18).
- The unadapted base arm is on the scoreboard with the two clauses it will be
  misquoted without: it is the **training bank's dev split**, not eval-2, and it
  is k = 4, which `training/EVAL2.md` §4 calls a smoke setting. `nt headroom` over its
  `metrics.json` reports the carrier population INSUFFICIENT — +3.14pp of
  estimated ceiling against a 3.00pp bar — which is a strong design signal and
  never arithmetic certainty (job `6aacd5cfb1dc2b62dc590b82`, 2026-09-18).
- S0a and S0b are blocked because `eval2_rows.jsonl` is on **neither** private
  repository, so the next action is a pilot re-run and not a transfer; S0c is
  blocked on its base column only, because its probe rollouts are in
  `lypning-round02-work` (GH run 35399900848, 2026-09-18). Reading those two as
  one state is what would abandon a rung for want of a file that exists.
- The programme records measurements about its own instrument, and this is one:
  `pipeline/backends.py` sent no `User-Agent`, so the provider's edge answered
  HTTP 403 (Cloudflare error 1010) before the key was read. With the header,
  GH run 35399909232 wrote 7,967 candidates on 25,048 calls (2026-09-18/19)
  against 2,784 on 8,753 (GH run 35340137976, 2026-09-18).
- Recorded, not corrected: the #92 entry below describes a **draft** of bank v2
  — a train split of 1,355 over 40 families, digest `7c8997e1e128c47f`, 315 of
  315 clean — where the bank that shipped in the same PR splits 1,120 over 33
  and records digest `10492e75cc8c52a6` and 597 of 597 (`data/bank_v2/README.md`,
  and `split_cases(seed=1111)` re-run 2026-09-19). A dated entry is a record of
  a moment, so it is left standing; the artifact is 1,120.
- Bank v3's 4,850 banked rows carry **zero** `fallback-control` rows, because
  the publisher that wrote both batches accepted `native.jsonl` and
  `repaired.jsonl` only. `validate_pilot` requires both populations in every
  split, so they cannot form a pilot at any size (line counts from GH run
  35399900848, 2026-09-18).

**2026-09-19** — Make the token-floor job runnable, and stop reading an empty stage directory as a diagnosis

- `.github/workflows/token-floor.yml` could not have run: `apply_chat_template`
  needs jinja2, and jinja2 is a `torch` dependency rather than a `transformers`
  one. The metered GPU job gets it by accident, because `train_verified.py`'s
  script header installs torch; a tokenizer-only runner installs neither. The
  workflow now names it, `token_floor.py` asks for it beside `transformers`
  instead of failing forty lines later from inside the template renderer, and
  the job pins `HF_HOME` into the workspace so a new step can assert the hub
  cache stayed under 1 GiB rather than argue that no weights came down.
- `apply_chat_template(tokenize=True)` is checked once for shape. A release
  that returned a mapping would still answer `len()`, with its number of keys,
  and every count below the prompt-budget line would be a measurement of that.
- Round-02 job `6aacd5cfb1dc2b62dc590b82` failed at stage `sft` with no
  `work/round-02/sft/`, and that was read as "it died before the weights". It
  was not: `run()` mkdirs *after* `snapshot_download`, after `from_pretrained`
  and after the LoRA attach, so the absent directory is equally consistent with
  the supervised-token floor, a failure in the gradient smoke, and a kill during
  the 55.6 GB load. `test_the_output_directory_is_created_after_the_weights_and_not_before`
  pins the ordering. The base-dev arm of the same job did complete, which rules
  out everything `run()` checks before the SFT-only branch — the tokenizer, the
  eos admission and the per-case prompt budget — because stage `eval` ran those
  same lines over the same cases.
- What the job has to print for the pilot to be admissible: the schedule's
  exact supervised tokens must reach 50,000, and the byte upper bound over the
  schedule is 134,384 B at `--steps 250`, 161,286 B at 300 and 214,968 B at 400
  (seed 1111 over `training/data/bank_v2/train.jsonl`, `split_cases(rows, 1111)`
  then `train_verified.supervised_plan` at `--batch-size 4`, computed
  2026-09-19). So steps 250 clears the floor only if the realised encoding is at
  most 2.6877 bytes/token, steps 300 at most 3.2257, steps 400 at most 4.2994.

**2026-09-18** — Read the Hub from CI, find the benchmark saturated, and move generation to where the provider answers

- `HF_TOKEN` exists only as an Actions secret, so the private round-02 artifacts
  are readable only from a CI job. `training/START_NEXT_ROUND.md` asks for the device that
  owns them and no such device exists; the Hub is the device.
  `.github/workflows/s0-inventory.yml` is the reader, and
  `.claude/skills/round02-evidence/` is the route written down.
- The 2026-09-16 pilot job `6aaa87465527934177ee9f34` is absent from
  `lypning-round02-artifacts`. Its `probe/` survives in a second private repo,
  `lypning-round02-work`; `eval2_rows.jsonl` and `attempts.jsonl` are in neither,
  so rungs S0a and S0b cannot be read without re-running that pilot.
- A base arm completed (HF job `6aacd5cfb1dc2b62dc590b82`, 2026-09-18): 256 dev
  cases, 1,024 draws, 9 families, `correct` 0.9301, `correct_native` 0.7534, and
  on the coverage population 0.9686 over 7 families. That value is a family
  macro, not an aggregate — `7 × 0.9686456400742115 / 9 = 0.7533910533910534`
  exactly, and `training_metrics.py` computes it as `macro("native")`.
- So the coverage population leaves ~3.14pp of headroom against a rule that
  fires on a 95% lower bound above +3pp. At k = 4 on a dev split over 7 clusters
  that is a design signal, not a verdict, but it says a round on `bank_v2` cannot
  answer the question. `nt headroom` now re-makes that judgement from a metrics
  file rather than leaving it in prose, and detects the family-macro basis
  instead of assuming it.
- Three faults that stopped bank-v3 growing: every run drew constructs from the
  same seed 1111 and paid for tasks `--exclude-tasks` then filtered; runs stopped
  on wall time with a quarter of their paid-for calls unused; and the generation
  probe gated on `GET /models`, which a key can be refused while still being
  entitled to complete.
- Measured 2026-09-18 over four dispatches: `chat/completions` returns 403 from a
  GitHub runner for a key that answers the developer's own shell, including
  immediately after the secret was rotated. Generation therefore moves to that
  shell and adaptation stays on a runner that holds no provider key
  (`bank-v3-adapt.yml`), preserving the split `training/HARVESTING.md` requires.
- This session published roughly eleven bank cases into three world-readable
  Actions logs before `bundle.json` was removed from the printable set; the runs
  were deleted and the cause fixed, and deleting a run does not undo a scrape.
  `training/reports/2026-09-18-codex-saturated-bank-and-the-absent-pilot.md`,
  *What this session published into public logs*.

**2026-09-18** — Ask a bank whether it can host the effect before booking a GPU

- `nt headroom` answers `training/EVAL2.md` §9's falsifier off a finished arm's
  `metrics.json`, which costs the draws that produced it. `nt bank-native`
  asks the same question of a BANK, locally and free, two ways: it runs every
  row's program through the pinned engine, and — with `--mix-only` — it reads
  the per-family first-draft mix off the labels `pipeline.synth` already wrote,
  executing nothing.
- The instrument is `synth.Runner.engine_verdict`, the same call the bank was
  admitted with, so a bank is measured against its own admission test rather
  than a second opinion about it. The engine is named by `binary_identity`:
  `legality.py` says the number is quoted with its fingerprint or not quoted.
- Instrument check on `training/data/bank_v2/train.jsonl`, run 2026-09-18 with
  `lypning-l` sha256 `56a23c13286bb6bd…` built from this tree: 1,689 rows, 51
  families, coverage 1,473/1,473 native and fallback-control 0/216 native, zero
  witnesses. Both populations land exactly on what they were authored to be.
- `--mix-only` refuses rather than reporting a zero. `bank_v2` carries no
  `synth.kind`, so its macro delta ceiling is `None` and the renderer prints
  UNREADABLE: a zero there would read as "no room", which is a verdict this
  file cannot reach.
- A counterweight's nominal room is not room. A `fallback-control` row is
  `correct-control` in `training.Score` and `Score.native` is False for every
  arm, so ceiling rows are counted in the denominator and never as movable.

**2026-09-18** — Fold the bank-v3 synthesis into the pipeline, on the one net, emitting the one schema ([#94](https://github.com/kristerhedfors/lypning/pull/94))

- The generate-and-adapt loop was four standalone scripts under
  `training/data/bank_v3/` and `.github/scripts/`, each with its own
  `subprocess.run` harness, its own JSONL reader and its own reading of the
  refusal line — and its output was a record shape nothing downstream could
  load. Two independent surveys of the tree agreed: no converter from a v3 row
  to schema-3 existed, so the loop terminated at the private dataset repo.
- Now `pipeline/synth.py` (the oracle, the routing, the proved repair, the
  schema-3 projection), `pipeline/repair_rules.py` (the rewrites, as pure source
  transforms) and `pipeline/synth_generate.py` (the prompts, the construct
  lists, the budget loop), driven by `nt synth-generate` and `nt synth-adapt`.
  Model-written code runs through `sandbox.run_python` — scrubbed environment,
  process-group kill, memory and output caps — where it used to run through a
  bare subprocess with a timeout. The refusal line is read by
  `engines.parse_refusal`; `engines.check_refusal_contract` is the one home for
  "exit 90, one line, empty stdout", and `eval2_bank` now uses it too.
- What comes out is schema-3. `synth-adapt` output goes straight into
  `training-prepare --cases`, which confirmed every label by execution on a
  handcrafted batch (coverage references `correct-native`, controls
  `correct-control`), and into `eval2-leaks` against the frozen benchmark.
  Emitting the shape admits nothing: review, preparation and every floor in
  `training-bundle` still stand, and each case says in `review.oracle_basis`
  that its tier is self-consistency, not independent derivation — the
  docstrings claimed that field was written before; it never was.
- Three routing defects closed. A ceiling-stratum row the engine served is now
  a coverage case, decided by execution and not by its label; a control refused
  on some inputs only is rejected, because `validate_reference_scores` wants
  every test refused; and an engine that runs a program to a different answer
  than CPython is a **witness** (root invariant 1), where triage used to file
  it as `engine_error` and discard it. Stratum routing moved from the
  publishing script, which holds `HF_TOKEN`, into the stage that decides.
- The adapt job no longer fails when nothing needs repairing: `repair.py`
  exited 1 on an empty queue, so a batch that was all native banked nothing.
  A batch with no admitted case still exits 1, because nothing is not a clean
  read.
- The generator goes through `pipeline.backends`, the one OpenAI-shaped door,
  with a `reasoning_effort` knob it needed and the eval arms do not; the vendor
  SDK leaves the workflow, and so does the second copy of the key-stripping
  rationale in `cerebras_probe.py`.
- `training/tests/test_synth.py` and `test_synth_generate.py`: every route
  in `synth.judge` reached from a fixture engine, the repair accepted only when
  native and byte-identical, every admitted kind validated by
  `training_data.validate_cases`, the generator's bounds and resume against a
  scripted backend, and the door's payload. `test_repair_rules.py` imports the
  package instead of a file path. Touches no Rust, nothing under `src/lypning/`,
  no admission gate, and no frozen artifact; `training/data/bank_v2/` is
  untouched.

**2026-09-18** — Generate the population the programme asked for, and repair what the engine refuses ([#93](https://github.com/kristerhedfors/lypning/pull/93))

- `training/PREREGISTRATION.md` §2 item (g) fixed the training mixture on 2026-09-13,
  before any spend: a pool of 66 rewrite to 27 ceiling, drawn from "rewrite +
  ceiling only, less the cases that cannot teach", because "an unobserved case
  is a plain coding task the stock model already answers". The first scaled run
  was 3,499 of 3,837 already native — 91.2% of the one population that was
  struck out. The prompt caused it: asking for tasks "solvable in under 25 lines
  of ordinary Python" is a description of the served subset.
- Generation now names a construct the engine refuses and asks for an ordinary
  task whose natural answer reaches for it, never mentioning the module or any
  restriction. Measured 2026-09-18 (GH run 35340137976): the refused fraction
  went **5.2% to 42.1%**, repaired rows 13 to 103, and the stratum draw came out
  at 0.708 against the preregistered 0.710.
- Two lists, not one, and the difference is the point. REWRITABLE means a native
  equivalent exists, so the pair teaches a substitution; UNREWRITABLE means the
  right answer keeps the import. `fractions` and `namedtuple` moved to the second
  list after the repair failed: `lypning-l -c "class C: pass"` exits 90, so a
  shim built on a user-defined type can never be native.
- Ceiling cases were being generated and then thrown away. `bank3_publish.py`
  banked only native and repaired rows, so the run's 105 ceiling task-calls
  reached the queue, refused correctly, and were discarded — the whole
  counterweight that stops an arm scoring well by avoiding every import.
  `triage.py` now carries the stratum and publishing separates a banked ceiling
  row from a rewrite row that still owes a repair.
- `calendar` and `datetime` repair rules, which were the two largest unserved
  buckets. Plain functions only, verified against CPython over 3,652,059 days,
  216,012 `monthrange` pairs and 323,935 date triples with zero mismatches. The
  repair rate over the same queue went **13 to 78 of 198**.
- Adversarial review demonstrated two repairs that `verify()` accepted and that
  were silently wrong, both now refused. A date is carried as an integer day
  count, and the leak guard matched the carrier only as a FIRST argument, so
  `print("far", d)` printed `21358` where the original printed `2028-06-23`.
  And `date + timedelta(days=1.5)` advances ONE day in CPython, because
  `timedelta` truncates before `date.__add__` reads `.days`; rewriting it to
  `+ 1.5` advanced 1.5. Closing both cost 4 repairs, which is the right price.
- `training/tests/test_repair_rules.py`, 28 tests: the verification above was
  measured by hand and quoted in a docstring, which is the same defect as a
  number with no command behind it. Four inputs per queue row cannot see that
  1900 is not a leap year while 2000 is.

**2026-09-18** — Grow the bank from Qwen on Cerebras, and adapt what the engine refuses ([#92](https://github.com/kristerhedfors/lypning/pull/92))

- A generate-and-adapt loop, which is `training/ORCHESTRATION.md`'s data loop and its
  step-6 repair made executable. Qwen proposes tasks and answers each k times;
  triage runs every sample, keeps a case only when at least two agree byte for
  byte on every input, re-runs the winner to catch output that moves between two
  clean runs, and routes by what the engine does with it.
- The safety property is a job boundary, not a convention: generation holds
  `CEREBRAS_API_KEY` and executes nothing, triage executes model-written code and
  holds no provider key, publishing holds `HF_TOKEN` and executes nothing.
- First scaled run, 2026-09-18 (GH run 35320962503): 12,000 calls, 2,286,142
  output tokens, 49 minutes, 3,837 tasks, **3,499 native + 13 repaired = 3,512
  banked**, 140 rejected, 185 refused with no working rule.
- Repairs are proved rather than trusted: a rewrite is accepted only when the
  engine serves it natively *and* it reproduces the output agreed before the
  repair existed, so a rule cannot move its own target. `heapq` is a complete
  reimplementation using CPython's sift order — an improvised heap pops equal
  elements in a different order, which is observable once values carry payloads.
- **This is a weaker evidence tier than the authored bank and is kept separate
  on purpose.** Its expected outputs come from samples of one model agreeing, so
  a misreading shared by every sample survives. The teacher is `qwen-3.8-27b`,
  the model being trained: on-policy rejection sampling, not distillation.
- What the rules could not repair is the useful half of the failure, and is
  written out by kind: `calendar` 39, `datetime` 31, `fractions` 13, `string` 12.
  Those are capability requests for the engine. The `decimal/fractions` rule
  fired 15 times and was accepted 0 times; it is wrong and is recorded as such
  rather than quietly left to keep firing.

**2026-09-18** — Author a task bank with a differential oracle, and run the round-02 pilot on a real GPU ([#92](https://github.com/kristerhedfors/lypning/pull/92))

- The blocker on a real adapter stage was never the GPU, it was data:
  `MIN_TRAIN_CASES` is 1,000 and the tree held 517 corpus cases, 147 with
  references, in the rewrite schema rather than schema-3. The floor was not
  weakened; a bank was authored.
- The oracle is differential. Each family carries a `spec` and a `reference`
  written against the same English sentence and never against each other, and a
  case is admitted only when the two agree byte for byte under execution.
- Three of the repo's own gates rejected the first drafts, and each rejection
  improved the data: identical prompts across a family are one case with hidden
  tests; four families labelled `fallback-control` are in fact served natively
  (`math`, `json`, `collections`, `re`, `random`), so they became coverage and
  ten genuinely refused modules became the controls; and one shared task
  preamble put pairwise similarity at 0.81 — 0.958 between two families of one
  kind on opposite sides of the split — so every family now has its own sentence
  shape. Task leaks went 4,419 to 0.
- Measured 2026-09-18: `eval2-leaks` exits 0 with 315 of 315 clean; preparation
  gives digest `7c8997e1e128c47f` and a train split of **1,355** over 40
  families with both populations in all three splits. Cases are not
  independence: 2,286 cases over 69 families is 69 components, and every
  `review` block records that an agent, not a human, authored and checked it.
- Two skills so the next bank does not start from scratch:
  `.claude/skills/training-cases` (authoring and the differential oracle) and
  `.claude/skills/training-bundle` (preparation, the gates, and publishing a
  bank to the private dataset repo).
- CI gained a pilot path. The bank is committed to git for review and copied to
  the private dataset repo inside the job, because an Actions secret is
  write-only. Two free gates run before any submit: the upload refuses a bank
  with a missing population or a family on both sides, and `eval2-leaks` must
  exit 0. A 27B model loads in full bf16 with no quantization, so the smoke's
  24 GB `a10g-small` cannot hold it; the pilot runs on `h200`, priced by the
  preflight at $5.00/hour.

**2026-09-18** — Run round-02 from CI: a free preflight, a bootstrapped verifier Space, and a marker-gated GPU submit ([#92](https://github.com/kristerhedfors/lypning/pull/92))

- The round is launchable from GitHub Actions. A GitHub runner is a disposable
  Linux VM with no sensitive files, which is what `--isolated-worker` attests,
  and the GPU is a Hugging Face Job the workflow submits — never the runner.
  This is also the only place the token can be used: an Actions secret is
  write-only, so every Hub operation has to happen inside the job.
- Three jobs, and only the last one costs anything. **preflight** asks the Hub
  the three questions that decide whether a round can run at all and that no
  checkout can answer: whether the token sees Jobs hardware, what the flavor
  costs, and whether the model revision resolves to a 40-character commit.
  Ledger row T3 records the last attempt dying on a provider 402 *after* the job
  was submitted; this asks first. **bootstrap** builds the verifier Space and
  the private artifact repo and ends in a launch dry run that prints the priced
  plan. **submit** runs only on a commit-message marker.
- Measured 2026-09-18 on run 35299470403: account `headforce`, 26 Jobs flavors
  visible, `a10g-small` at $1.00/hour, `Qwen/Qwen3.8-27B` at
  `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`, verifier Space
  `headforce/lypning-round02-verifier` at
  `5fa4f3127f7a3d70b84d4e1c93923f18f0c43a41` built from six files and RUNNING,
  artifact repo `headforce/lypning-round02-artifacts` created private.
- The Space Dockerfile is deliberately not `training/worker/Dockerfile.verifier`.
  That one is the Docker boundary's — `/runner/`, uid 65534, an ENTRYPOINT. A
  pooled sandbox reads the standard system trees and nothing else at the root,
  so the harness goes under `/usr/local/lib` and the Space needs a CMD that
  keeps a health process alive and no USER line. The base digest is read out of
  `launch.py` rather than restated, because one CPython build across the trainer
  Job and the verifier image is what makes the `sys.version` half of the
  identity handshake hold.
- Bootstrap failures are free by construction: the Space must reach RUNNING and
  the destination must be private before `launch.py` is reached at all.
- **This is the smoke, and the smoke is not a round.** It exercises the round's
  plumbing on a real GPU with a tiny random model and the authored starter
  fixture. The pilot needs a reviewed bank that is not in this tree, and a real
  adapter stage still gates at >=1,000 train cases against the starter's 12.

**2026-09-17** — Stop the S0 handoff from telling the private device to rebuild the population it was pinned to read ([#92](https://github.com/kristerhedfors/lypning/pull/92))

- The next training round was attempted on a third clone holding none of the
  round's four inputs. It was reported blocked and no rung was run, which is the
  assignment's own instruction. What the clone *could* do was read the
  assignment against the tree it will run in, and it does not survive that read.
- **`training/START_NEXT_ROUND.md` contradicted its own stop rule.** :21-22 stops on an
  absent input; :72-75 told the device to materialise an absent `$PILOT_ROWS`
  with `nt eval2-rows`. That command's engine defaults to whichever `lypning-l`
  the host has installed, `native` is read off its replay and `status` off
  `native`, so the rows' *population* is a function of the binary — the same
  mechanism that read 14 correct-but-fallback draws through one binary and 26
  through another. The blast radius is asymmetric: S0b refuses a re-derived
  population on `--expect-draws 171`, but S0a accepts any file that exists and
  would have printed a confident §7 curve at an unrecorded identity. The
  sentence is withdrawn; an absent `$PILOT_ROWS` is a blocked round and an
  artifact-transfer problem.
- `eval2-rows` now refuses an `--engine` that is not a file, and prints the
  sha256 and version line of the binary it replayed through rather than
  `identity()["fingerprint"]`, which fingerprints the host's installed chain and
  cannot name an explicit historical binary. Its pinning test had been passing
  `/bin/true`, which does not exist on darwin — the test was demonstrating the
  defect it was meant to prevent.
- The preflight block now stops. Four bare `test -f` lines print nothing and
  exit nothing, so a pasted block ran all three rungs against whatever the
  device had; the operator saw the rungs' errors, not the missing path.
- The three rungs run on the resolved 3.12 `$ROUND_PYTHON`, not bare `python3`.
  The replay grades against `sys.executable` and the pinned binary says *for
  cpython 3.12*; a mismatch there is scored as MISMATCH or as a draw with no
  refusal, either of which blocks the rung three layers from its cause.
- Two contamination gates issued an all-clear over a file they never read.
  `leaks --sft` — "the one finding here that must stop a training run" —
  reported no target passing a held-out case for a mistyped path, a directory
  with no `sft.jsonl`, and an empty file; `eval2-leaks` certified two empty
  banks as non-overlapping. Both now split on invariant 8: a path that is not a
  file is usage (2), comparing nothing is failure (1), and `--allow` forgives
  found pairs rather than an absent comparison.
- A banked launch above its capacity was told to raise a knob its own ceiling
  forbids — `--score-workers 32` earned "increase `--pool-max-hosts`", and
  taking that advice earned "pool cost ceiling is 4 CPU hosts". Above the
  ceiling the refusal now names the ceiling. At or below it, nothing changes.
- `training/STATUS.md`'s account of `levers --run` is corrected: it describes the bare
  form, and the `--run --population-rows` form §10's S0b row assigns does not
  re-derive the population.
- The change was then adversarially reviewed in turn, which found no blocking
  defect and two high ones, both introduced by the change itself: the
  replacement rebuild command could not run as written — the positional is a run
  id, not a path, and `--output` is required — and the review listed as
  still-open a `training/STATUS.md` defect the same change fixed. Both are fixed, along
  with four more: `eval2-rows` refuses a replay in which any program failed to
  grade, because `is_file` rejects a path but not a *regular file that will not
  execute*, and a binary that lost its execute bit in transfer writes an
  all-`correct-fallback` population at exit 0; `leaks --sft` counts programs
  built rather than lines read, because a bundle's `train-sft.jsonl` carries
  `messages` and no `program`; `power --eval2` stops advertising the withdrawn
  rebuild for a path the caller typed; and the capacity refusal names the knob
  with remaining headroom, since `16/1/4` was still a dead end with hosts
  already at their ceiling.
- Review: `training/reviews/2026-09-17-codex-s0-assignment-executability.md`,
  ledger row R6. 58 findings were raised and 2 refuted; the blocking 3 and the
  items above are closed here, and §5 of the review is a triaged excerpt of the
  residue. None of it blocks the S0 session. No paid rung, GPU job, Space
  rebuild or dataset mutation is authorised, and none was performed.

**2026-09-17** — Assess the blocked S0 round from two independent reviews, close the four vacuous reads its guards left open, pin Fable's retry to the historical binary, and stop a plan from certifying a schedule it cannot price ([#91](https://github.com/kristerhedfors/lypning/pull/91))

- The independent Codex review owed by ledger row S0 was written twice, by two
  sessions that did not see each other's work and agreed on the diagnosis:
  `training/reviews/2026-09-17-codex-s0-guards-and-engine-identity.md` and
  `training/reviews/2026-09-17-fable-s0-independent-assessment.md`. Both are
  kept as evidence and this PR carries the combined ruling:
  **continue**, all four decision requests ruled, every paid and GPU rung still
  held. The round remains unrun — this is a third clone holding none of its
  inputs, checked literally rather than assumed — so no power figure, lever
  vector, probe table or model-quality claim exists from it, and the two
  `training/EVAL2.md` deliverables are still owed. Five of the report's load-bearing
  claims were re-derived in-tree before being ruled on; three came back
  partially confirmed, and the disagreement is recorded rather than smoothed.
- The guards closed four paths, not five. `levers`' fifth fired only when the
  failure took the `ERROR` route *and* the filtered population was non-empty,
  so a full descriptive vector — held-out banner and all — still printed at
  exit 0 over two empty files, over a present-but-empty `attempts.jsonl`, under
  a `--status` filter that matched nothing, and through an engine that runs but
  is not `lypning` (`MISMATCH`, so `errors` was 0). The vector is now
  publishable only if at least one record backs it: exit 1, stdout empty, in
  every render mode including `--json`. The narrow `ERROR` guard stays in front
  of it because it names the failed replay, which is the more useful message.
  The refusal line now carries `loaded`, `considered` and `unmatched`, since
  `loaded` is pre-filter and `refusals` post-filter and the first wording could
  assert that named rows carried no refusal when the filter had removed them.
- Rung S0b no longer re-derives the population it reports. `--run` replayed
  every program through the local binary, which recomputes `native` and `status`
  with it, so the `--status` filter ran over a population the binary had just
  manufactured — and the handoff's instruction to record an `@ engine` line
  named output no mode of `levers` produced. The rung now freezes status and
  family to the materialized 171 pilot rows, uses the replay only to attach
  refusal kinds, binds the explicit historical `lypning-l` by its full recorded
  SHA-256, prints that identity, and refuses a wrong draw count or any partial
  replay before publishing a vector. The composite build *fingerprint* is
  deliberately not a prerequisite: it folds in a core `lypning` sha no document
  records, and an unsatisfiable gate on a $0 rung is how a $0 rung stops being
  run. 171 stands as a historical population rather than a number to re-derive.
- `--plan` now refuses the certainly-too-small half of the supervised-token
  floor without downloading anything. `sft_batches` picks by family and index
  and never inspects what it carries, so the schedule is buildable from the
  cases alone; summing the scheduled references' UTF-8 bytes bounds the tokens
  above under byte-level BPE. The exact floor stays in `run()`, unmoved, because
  the exact count needs the tokenizer. A passing plan still does not certify the
  schedule and the refusal says so. The shipped substitute — check
  `steps × batch_size` against 50,000 — counted example exposures rather than
  tokens and would have refused the runbook's own `--steps 250 --batch-size 4`;
  it is withdrawn. `planned_exposures` and `supervised_token_upper_bound` are
  `null`, never `0`, for a stage the floor does not price.
- A banked launch is refused above four sandboxes per host. The existing check
  was a product and a product is blind to density, so `16/16/1` — sixteen
  sandboxes on one host, the shape round-02 ran — cleared it, while `1/1/1` is
  the least contended shape in the space and was never the problem. `native` is
  host-load-dependent, so per-host density is an instrument parameter and two
  arms scored at different densities are not comparable. The ceiling is four
  *at `cpu-basic`*; a different pool flavor voids the number. No floor on the
  worker count, host count or total capacity, because no eval-2 arm has ever
  completed and a throughput threshold would be set against a forward estimate.
  The host count is capped at four as well, so the cost envelope is bounded in
  both directions, and the product check no longer advises raising a knob the
  ceiling forbids.
- Eight residual risks are recorded with file and line, four of them absent from
  the report; two are parked as future calls — `power --eval2 --rows` accepts
  any file bound to no run, digest or fingerprint, and `eval2_rows` tests
  `native` before `correct`, so its `correct-native` bucket can hold draws
  CPython did not pass. Neither is ruled here.
- Bookkeeping, per review condition C8: the entry below says "three zero-cost
  rungs" and its commit subject says five. Neither is a count of rungs — it was
  two commands across two of the three S0 rungs, three CLI branches and five
  guards. The merged entry is left as written; this is the correction.

**2026-09-17** — Stop three zero-cost rungs from reporting a clean read of nothing, and record that rung S0b's number moves with the engine ([#89](https://github.com/kristerhedfors/lypning/pull/89))

- The assigned S0 round did not run. This clone holds none of its inputs: the
  private pilot rows, the round-02 probe rollouts, the banks and a Python 3.12
  build are all absent, and `nt eval2-rows` cannot materialize the rows because
  it reads the same missing run. Substitution was available — the tools accept
  any path — and was refused.
  `training/reports/2026-09-17-fable-s0-blocked-and-the-vacuous-read.md` names
  every missing artifact. Nothing was spent, no provider was called and no GPU
  ran.
- Run verbatim with both private inputs absent, rung S0c printed a complete
  all-zeros table and exited 0, because `read_jsonl` answers `[]` for a path
  that is not there and `probe_only` was the command's only failure signal. Two
  further commands had the same shape, and the worst was rung S0b's: an
  `--engine` that is not a file graded every program `ERROR`, printed an empty
  vector at exit 0, and silently grew the considered population. All three now
  exit 2 naming the path, in the idiom `eval2-leaks` and `eval2-legacy` already
  used, and an `ERROR` census is reported where `MISMATCH` is. The exit-1 hole
  detector on real inputs is unchanged.
- Guarding absence was not enough, and testing that the new `ERROR` line could
  actually fire is what showed it. Two survivors reproduced the whole defect
  one step past each guard: an `--engine` that exists but cannot execute passed
  `is_file` and still printed the empty vector at exit 0 over the inflated
  population, and a present-but-empty probe file still printed a zeros table.
  A replay that graded nothing, and a probe with no rows, now exit 1 and print
  no table at all. Seven tests pin all five paths.
- Rung S0b is not engine-independent. The eval-2 draw rows carry no refusal
  kind, so `--run` replays through the local binary and re-derives `native` with
  it: one local run matched 14 correct-but-fallback draws through the built
  engine and 26 through a broken one. The reviewed count of 171 pilot draws is
  therefore a count at the pilot's engine identity, and
  `training/START_NEXT_ROUND.md` now says so and asks for the fingerprint in
  the report.
- Of nine admission gates the documents claim the runner enforces, eight are
  enforced as documented. The ninth is a split: the 50,000-supervised-token
  floor is refused in the stage, not in `preflight`, so `--plan` accepts a
  schedule the stage later rejects. Moving it earlier would cost `--plan` its
  no-download contract, so the documents are corrected and the split is pinned.
  The registered seeds, the family cycle and the token floor had no test on
  their refusal branch; they have one now.

**2026-09-17** — A "standard library" corpus: units the engines run, each labelled by the cheapest one ([#88](https://github.com/kristerhedfors/lypning/pull/88))

- `training/stdlib/units/` holds self-contained, function-only programs that
  fill CPython surfaces the engines refuse. Nothing imports them, and nothing
  can: measured 2026-09-16, `/root/.lypning/bin/lypning-l main.py` on `import
  mylib` refuses `module: import mylib` and `exec("x = 1")` refuses `builtin:
  exec`. A unit can only be INLINED, which is what makes this training data
  rather than a shipped library.
- A unit is labelled by running it, never by reading it: CPython first as the
  oracle, then each engine in `engines.ENGINE_ORDER` cheapest-first, and the
  first byte-identical zero exit wins. Exit 90 is coverage and labelling moves
  on; an engine that exits 0 with different bytes is a MISMATCH, so the unit is
  rejected and the reason recorded (invariant 1).
- `PYTHONPATH=src:training python3 -m pipeline.cli stdlib-verify --units
  training/stdlib/units --first-seen 2026-09-16 --producer authored --cpython
  /usr/bin/python3.11 --engine lypning=… --engine lypning-l=…`, run 2026-09-17:
  34 units, 25 `lypning`, 9 `lypning-l`, 0 drops, 120 distinct CPython names
  over 121 `# fills:` entries. Three of the 34 disagree with what `lypning
  route` predicted statically, all in the legitimate direction — a `bigint`
  refusal only exists at runtime. The same command on 2026-09-16 printed 26 /
  8 and "121 CPython names filled": `struct_pack` moved to `lypning-l` when it
  gained the cases above 2**63 - 1, and the name count was the entry count
  printed under the wrong word — `struct.calcsize` is filled by two units, so
  coverage is 120 and the labelling work is 121. Both numbers are printed now.
- `training/data/stdlib/stdlib.jsonl` is those rows, committed, so the corpus
  can be read and merged without a Rust toolchain. `first_seen` is passed in
  and the serialisation is fixed, so re-running the writer over an unchanged
  tree rewrites nothing; `test_stdlib.py` §9 fails when it has gone stale
  against the units, engine-free by name and hash and byte-exact when the
  binaries are present.
- `engines.ONLY_CPYTHON_REFUSALS` is gated, not merely discouraged. A
  pure-Python `math.log` or set-ordering helper passes its own cases and
  returns a silent wrong answer, which is the one defect this corpus cannot see
  for itself; `training/tests/test_stdlib.py` is the check, over the CPython
  differential, determinism and that gate.
- `.github/workflows/stdlib-corpus.yml` runs plan, generate, verify, one
  bounded repair round and assemble — `workflow_dispatch` only, the provider
  secret at step level, `permissions: contents: read`. It uploads the corpus
  and never commits, and its `dry_run` runs all five stages offline and asserts
  the shape a real run would produce.
- `training/STDLIB.md` states the mechanism, `training/stdlib/README.md` is the
  operator's note, and `training/stdlib/targets.json` carries the surfaces with
  the census that ranked them and its own date.
- Adversarial review of the above, 2026-09-17, sixteen findings fixed in the
  units and in the checks rather than in the tables that grade them. The
  parser reads its two headers and its separator from `tokenize` COMMENT
  tokens, so a `# fills:` written inside any string is prose and cannot shadow
  the real one — a unit may now document the format it is written in. The
  reference differential is exempted by the reviewed `_DIVERGENCES` table
  alone, never by the word "divergence" appearing in a docstring, which had
  been switching the check off for 16 of the 34 units. `fmean`'s weighted
  products are formed with `*` on the values, as `fsum(map(mul, …))` does, so
  they round once and not three times; `struct.pack` folds with `%` and `//`
  rather than a bitwise mask the wider engine refuses on a bigint; both struct
  parsers share one byte-identical ASCII/format-bound block; `timedelta`'s C
  int limit and `bytes.hex`'s ASCII-separator rule are ported with CPython's
  messages and CPython's check order.
- The one unit whose divergence the new differential caught, `binascii_hex`,
  was fixed rather than waived: `hex_str` was handing its own default `None`
  down to `hexlify` as an explicit argument, which is a `TypeError` in the
  real `binascii`, so the reference run died at case 28 of 71 and the other 43
  cases were compared against nothing. It now calls `hexlify(data)` the way
  `bytes.hex()` does, all 71 agree, and the entry that would have admitted the
  abort is not in the table.
- That abort was not the only one, and the differential could not see it: it
  compares the lines it HAS, so a reference arm that dies part way down the
  case block passes by comparing a prefix. Measured 2026-09-17 by driving
  `_REF_DRIVER` over every unit, five more were short — `struct_pack` 240
  lines against 44, `struct_unpack` 126 against 42, `itertools_chain` 76
  against 62, `itertools_product` 42 against 33, `itertools_accumulate` 67
  against 60 — leaving 310 case lines compared against nothing, including the
  2\*\*64 `struct` boundary cases added in this same PR. Each unit captured a
  message with `except ValueError`, which is what it raises and not what
  `struct` or `itertools` raise. The five now catch the real type as well and
  PRINT it, so the declared type divergence is demonstrated by a line a reader
  can run instead of by ending the run; all five arms are equal-length, and
  the recovered lines exposed no behavioural divergence.
- `test_the_reference_arm_ran_every_case` is the check that would have caught
  it: the reference arm must print as many LINES as the unit arm, counted in
  lines rather than bytes so it asks only "did every case run" and cannot be
  silenced by `_DIVERGENCES`. Its reviewed table `_SHORT_REFERENCE_RUNS` is
  empty, checked in both directions like `_DIVERGENCES`, and a short run
  caused by a diverging case does not belong in it — reorder the case or widen
  the capture. Re-measured 2026-09-17: all 31 reference-bearing units
  equal-length, the other 3 name no module.
- **A unit may not pin CPython-version detail, and ten of them did.** A unit is
  INLINED, so it is run on whichever CPython the reader has, and CI runs this
  suite on 3.9 through 3.14. A case that prints an error MESSAGE, a generated
  regular expression or any other implementation detail is right on the release
  it was authored against and wrong on the others — the same rule
  `conformance.classify` already follows when it compares exception types and
  never traceback text. Found by running every unit, and every unit's cases
  through `_REF_DRIVER` against the real module, on CPython 3.9.23, 3.10.18,
  3.11.15, 3.12.11, 3.13.7 and 3.14.0rc2, 2026-09-17. Layer 1 cannot see this
  defect at all and said so: all 34 units print byte-identical stdout on all six
  both before and after, because a unit runs its own inlined helper. Only the
  reference arm moves.
- Before that run, five units agreed with CPython on some releases and not
  others: `struct_pack` (22 differing lines on 3.9 and 3.10, 8 on 3.11, 83 on
  3.12, 3.13 and 3.14, against 8 declared), `statistics_variance` (2 on 3.9 and
  3.10, 0 from 3.11), `urllib_urljoin` (1 on 3.9 and 3.10, 0 from 3.11),
  `bisect_search` and `statistics_mean` (the reference arm died outright, on 3.9
  and on 3.9-3.10). Two more, `itertools_accumulate` and `itertools_product`,
  declared a TYPE divergence but printed a message CPython reworded in 3.13, so
  the declaration was true on four releases and understated on two. Afterwards
  no unit's agreement depends on the release: of the 31 reference-bearing units
  23 agree byte for byte on all six, 6 differ by exactly the same lines on all
  six and each of those 6 is a reviewed `_DIVERGENCES` entry, and the last 2 are
  `bisect_search` and `statistics_mean`, which agree on every release where
  their surface exists. The real module also gives the byte-identical answer to
  all six interpreters for 28 of the 31.
- Each was fixed by printing the stable fact, never by widening `_DIVERGENCES`.
  `statistics_variance` prints the exception TYPE for the too-little-data cases,
  because 3.11 relabelled `stdev`'s and `pstdev`'s message from `variance
  requires at least two data points` to `stdev requires …`; it gained four cases
  pinning the other side of that boundary. `urllib_urljoin` splits `_http://x`
  instead of `1http://x`, since a digit led a scheme until 3.11 and does not
  from 3.11 — the alphabet half of the scheme rule is printed and the
  letter-first half is docstring prose. `itertools_accumulate` and
  `itertools_product` print the type alone, and raise their own message rather
  than a copy of a retired CPython one.
- `struct_pack` was the large one and its central claim had expired. It printed
  CPython's range-check messages as a grid and called that grid the
  specification; 3.12 unified the two handler tables and deleted it. Over 680
  `pack(prefix + code, value)` probes on the same six interpreters, 350 answer
  with a different message on some release — 38 at 3.10→3.11 (a C macro leaked,
  `short format requires (-32767 -1) <= number <= 32767`) and 312 at 3.11→3.12
  (one message per code, no byte-order split, no `argument out of range`). Zero
  of the 680 disagree on whether `pack` raises, and zero on the exception type.
  So the grid is gone and `_rejects` prints the boolean on both sides of every
  boundary instead, which pins the range contract harder than the messages did.
  The 43 format, argument-count and argument-type messages are identical on all
  six and are still printed, character for character. `_range_message` was
  rewritten to 3.12's one-message-per-code rule at the same time so the text the
  port raises is current rather than retired: 5,320 (prefix, code, value) probes
  give the identical raise/no-raise answer before the rewrite, after it, and on
  every one of the six releases.
- `bisect_search` and `statistics_mean` are the other kind and are not a defect
  in what they print: `bisect`'s `key` arrived in 3.10 and `fmean`'s `weights`
  in 3.11, so on the older releases there is no surface to check those cases
  against rather than a wrong answer. Both docstrings now name the release the
  surface comes from and what the older ones answer instead. `hashlib_digest`
  keeps its declared divergence and states it release by release, because
  CPython's own answer there is not one answer — a plain `ValueError` saying
  `unsupported hash type` on 3.9 and an `UnsupportedDigestmodError` saying
  `[digital envelope routines] unsupported` from 3.11 — while `hashlib.new`'s
  message, the one this port carries, is the same on all six.
- The rows were rewritten and proved version-independent: `stdlib-verify` under
  CPython 3.11.15 with the 3.11-built engines and under CPython 3.14.0rc2 with
  engines built against 3.14 produce byte-identical `stdlib.jsonl`
  (md5 `9a7f0eaec5ef0fcc89e7846042c15c92`, both runs 2026-09-17), 34 rows, 0
  drops, and not one `requires`, `requires_static`, `caps`, `route_agrees` or
  `naive_kind` field moved from the previous rows.
- Building that second pair has a trap worth writing down, because it produced a
  binary that looked like the arm it was not. `build.py` pins the reference
  version to `engines.find_cpython()` (`build.py:495`), which walks `$PATH` for
  the real interpreter and does NOT read the one running the build — so
  `python3.14 -m lypning build --rust` still builds `for cpython 3.11`, and says
  so in `--version` if asked. The arm above is a pair built with 3.14 first on
  `$PATH`, which reports `for cpython 3.14`; a pair built the other way was
  discarded once `--version` gave it away.
**2026-09-17** — Assess round 02, preserve blocked evaluation evidence, and constrain the next Fable run to zero-cost validation ([#87](https://github.com/kristerhedfors/lypning/pull/87))

- Round 02 produced no completed eval-2 arm and therefore no model-quality
  verdict: the only real 27B attempt reached SFT, probe and GRPO, then the base
  evaluation blocked when sixteen isolated scorers competed on one CPU host.
  The assessment records the attempt history, separates infrastructure evidence
  from model evidence, and keeps paid and GPU work on hold.
- Verified evaluation now drains an interrupted chunk, persists every successful
  sibling row, and then re-raises the first infrastructure failure. Native
  timeouts after a correct oracle remain aborts, not scores. The launcher caps
  each scorer host at four sandboxes, uses at most four hosts, and refuses an
  undersized pool before work starts.
- Held-out draw rows can no longer be ranked as a build queue. `nt levers`
  exposes descriptive vectors instead, and a new `probe-vector` command compares
  probe outcomes with base rows case by case. A review of all 108 new refusal
  declarations moves twenty entries in thirteen deterministic families from the
  fallback bucket to the engine-addressable bucket without changing evaluation
  labels or gates.
- The verifier Space now has content-free `/` and `/healthz` endpoints plus a
  container health mode. The earlier exit-127 correction changes the worker and
  harness hashes, so the next remote run must use a new Space commit and fresh
  bundles.
- Real adapter stages require at least 1,000 training cases, at least 50,000
  supervised token exposures, a complete family cycle, and the three registered
  seeds 1111, 2222 and 3333. Family-cyclic scheduling replaces replacement
  sampling, and all three jobs are required before the aggregate gate.
- `training/START_NEXT_ROUND.md` gives Fable an exact S0a-S0c sequence on the
  private device that owns the artifacts. That zero-cost audit must be reported
  and reviewed before any provider allocation, upload, training or evaluation.

**2026-09-16** — Make the lever split a command, preserve a blocked arm's program, and stop a harness failure from wearing the program's exit code ([#86](https://github.com/kristerhedfors/lypning/pull/86))

- The next round did not run. Every rung of `training/STATUS.md` §10 is blocked
  on this device, each on a different prerequisite; `training/reports/2026-09-16-fable-sladder-s0-device-audit.md`
  names them one by one rather than inventing any of them. Nothing was spent and
  no GPU ran.
- `training/ASSESSMENT.md` §4's split of refusals between the engine lever, the
  model lever and neither was a judgement made once in prose. It is now
  `pipeline.levers` and `nt levers`: three mechanical layers — this package's own
  imports, the engine's closed list imported through `refusals.closed_kinds()`
  and never restated, and what the running interpreter does not ship — over a
  frozen declaration table of one reasoned line per refusal family. The oracle's
  module list is an evidence column and never a layer, and a test pins that.
- It reads the eval-2 draw rows unchanged, so the ladder's rung S0b is a command
  on the device that holds them rather than a judgement re-made there. It also
  gives `training/ASSESSMENT.md` §6 step 6 the ranked build order it never had.
- On this tree (`nt levers --against`, 2026-09-16, 9,064 rows loaded, 643
  carrying a refusal, rule 1) it reproduces §4's self-referential bucket exactly
  and disagrees on the lever boundary by 36 entries, entirely inside the 235
  entries §4 counted but never named. The disagreement is pinned
  by a test as the one that was reviewed, so a new one fails rather than passing
  quietly; which side is right is asked of Codex in the ledger.
- A blocked **evaluation** arm now writes the program that blocked it, as the
  reward stage already did. The abort, every score and every gate are unchanged
  on purpose: the ruling `training/ORCHESTRATION.md` row T4 asks for is the one
  this evidence was missing, and it is still open.
- Fixed a defect that predates this change: under network isolation the harness
  execs `unshare`, which succeeds and then fails to start the real program, so a
  setup failure arrived as exit 127 with an empty error pipe — indistinguishable
  from the program's own `SystemExit(127)`, which is the distinction the
  verification contract uses to decide whether a run is a model result at all.
  Taking it rebuilds the verifier image and needs a new bundle.

**2026-09-16** — Fix the power-table mislabel at its source, and make the pre-registered k a refusal ([#85](https://github.com/kristerhedfors/lypning/pull/85))

- `training/ASSESSMENT.md` described the `training/EVAL2.md` §7 mislabel and
  left it in the code, so the next print would have reproduced it.
  `stats.power_curve_clustered` now returns the realised lift in the unit the
  §4 rule works in — the macro over families, noise-free — beside the per-case
  figure and a flag for whether the rate cap clipped the cell, and
  `nt power --eval2` keys every cell on the macro, counts the cells the cap
  clipped and names the worst of them in the cap's own per-case unit.
  A row can no longer be read by the lift it was asked for.
- §7's uniform reading is withdrawn, dated, with the withdrawn sentences quoted:
  it concluded that a uniform few-point lift "cannot be seen by this instrument
  at N = 300 and k = 16" and prescribed more draws and a larger bank. Neither
  follows from rows that tested a smaller effect than their label. The
  concentrated rows stand — their lifted decile starts at zero, which their
  strictly rising power confirms — and the §4 rule, N and the §9 falsifier are
  untouched.
- Two corrections to the assessment's own findings, both from adversarial
  re-calibration on synthetic pilots (2026-09-16, this tree, k=16, N=300, mde
  +3pp, seed 7). `mean_effect` is per-case and is not the unit power is read
  against, so the prescription to key the re-print on it named the wrong
  column; on the real bank it would understate rather than overstate, because
  §11 puts the macro headroom above the per-draw headroom. And the concentrated
  shape clips too: its reachable lift is `fraction × (1 − mean(lifted decile))`,
  so at fraction 0.1 a nominal +10pp survives only on a decile at exactly zero.
- A non-smoke benchmark eval arm at any k but
  `training_contract.PROTOCOL_EVAL_DRAWS` is refused in
  `train_verified.preflight`; `--greedy`, which draws once by construction and is
  already restricted to a diagnostic, is exempt. `training/EVAL2.md` §4 already called the runner's default
  of 4 a smoke setting; round-02 spent an arm proving it. The other two "stop
  doing" rules stay prose on purpose — a training-case floor of 1,000 would
  refuse every bundle in this tree, and which floor is right is a decision
  `training/ASSESSMENT.md` §8 asks for.
- `training/STATUS.md` §10 is the single live sequence, appended rather than installed as
  §5: §5 is the only dated record of the programme's priors, and evicting it
  would have made five of the ten inbound `training/STATUS.md` §N citations
  wrong — silently, since the headings they name all survive a shift — one of
  them inside a dated entry in this file. `training/LADDER.md` §5,
  `training/NEXT_ROUND.md`, `training/START_NEXT_ROUND.md` and `training/ORCHESTRATION.md` point at it and
  keep their mechanism, which for `training/NEXT_ROUND.md` is the launch flags that exist
  nowhere else.
- `training/STATUS.md` §0 states where the programme stands in one paragraph —
  five days and about $105 for four adapters and no informative result, and the
  four cheap things that have never been done — because a reader had to reach §9
  to find it. Its scoreboard row for the pilot draw no longer quotes power
  figures from the withdrawn curve; it says instead that none is quotable until
  rung S0a re-prints it.
- `test_reward_scores_a_group_concurrently_and_keeps_batch_order` asserted that
  GRPO's thread pool overlaps two scorings by sleeping 0.05s in each and
  demanding the pair finish inside 0.15s. That is a wall-clock budget on a
  shared runner, which `ci.yml` refuses to put `bench` in CI for in those exact
  words, and it was the macOS job's only red on `0fdb527` — at 0.21s, with
  nothing wrong with the code. A `threading.Barrier` now asserts the overlap
  directly: two completions each wait for the other, so `reward` returns only if
  both were in flight. Checked both ways — it passes in 0.10s concurrently and
  fails on `score_workers=1`.
- `training/AUDIT.md` carries the defect as
  `data-integrity/power-table-keyed-by-a-lift-the-simulation-does-not-deliver`,
  with the repro; `training/ORCHESTRATION.md` gains ledger row S3.

**2026-09-16** — `nemotron/` is `training/`, and the retired name is a test ([#83](https://github.com/kristerhedfors/lypning/pull/83))

- Rename the training tree after the model it is for, not the model it is
  not: 208 files, plus the three workflows and their path filters,
  `PYTHONPATH=src:training`, `round_plan`'s `source_identity` root (a real
  breakage the moment the directory moved), the `overview` registration, the
  runbook commands, the Hugging Face job scripts and every document citation.
  `training/SWITCH.md` deferred this twice on the three workflows; they were the work.
- The name is model-agnostic on purpose. `qwen/` would repeat the mistake
  being undone, for the reason invariant 9 already gives: a name that still
  resolves to something is a name that can drift back into the code.
- The earlier target's name now survives only where a rewrite would falsify a
  record — the recorded run ids under `training/runs/` and their `meta.json`
  (each run records its own `run_id`, and a run id is the join key between a
  number and its evidence, including evidence outside this tree), the captured
  corpus and sightings JSONL, and the dated entries in this file, `training/AUDIT.md`
  and `training/REFACTOR.md`. `training/SWITCH.md` stops being the home of the name and becomes
  the home of that rule. Earlier entries' *paths* were repointed, because
  `tests/test_docs.py` resolves them and a ledger may not cite a file that is
  not there; their branches, models and numbers are untouched.
- `training/tests/test_naming.py` is the grep: the name appears nowhere
  outside the frozen evidence, no tracked path spells it outside `runs/`, and
  `training/SWITCH.md` states the rule without spelling it. The test does not spell it
  either — it reads it off the recorded run directory names, so there is no
  literal to go stale and the last evidence deleted deletes the rule with it.

**2026-09-16** — Assess the Qwen training approach against its goal, and plan the signal ladder ([#82](https://github.com/kristerhedfors/lypning/pull/82))

- Add `training/ASSESSMENT.md`: why no run so far could say whether the
  programme is moving — no positive control was ever run, every adapter was
  trained at a scale that installs style rather than a boundary, the training
  side got the leftover of the supply, and the examples that would carry the
  signal (verified native rewrites of correct-but-refused answers) were never
  admitted. Nothing spent; no frozen artifact touched.
- Calibrate `stats.power_curve_clustered` on synthetic pilots: a uniform lift
  is applied as `min(1.0, p + delta)`, so on a saturated pilot the uniform
  rows of `training/EVAL2.md` §7 realise a fraction of the nominal lift they
  are keyed by; the function's `mean_effect` is the column to print. The rule
  sees a realised +5pp about half the time and a realised +8pp nearly always
  at N=300, k=16, whatever the shape.
- Bucket the 643 refused entries of `data/classified.jsonl` by which lever
  can remove them: 222 engine-addressable, 221 legitimate fallback, 195 this
  repository working on itself.
- Propose the S0–S4 signal ladder (three rungs at $0, one at ~$5) and an
  eight-step plan with owner, cost, decision and stop rule per step; link it
  from the training README and `training/STATUS.md` §9, and add ledger row S2 to
  `training/ORCHESTRATION.md`.
- The #80 entry named that file without its `training/` prefix, which
  `tests/test_docs.py` reads as a missing file; it now carries the full path.

**2026-09-16** — Round-02 run report corrected: the eval-2 base arm was blocked, no arm completed ([#81](https://github.com/kristerhedfors/lypning/pull/81))

- Job `6aaa8746` failed at stage `eval2` after 384 of 1,200 base-arm draws:
  a CPU-bound candidate's engine run timed out after a correct oracle run,
  which the verifier policy reads as an engine fault. The report, `training/STATUS.md`
  and the ledger now say so, with the two explanations still to separate
  (engine speed on tight loops vs 16 sandboxes starving one pool host) and
  the decision Codex is asked for.

**2026-09-16** — Round-02 pilot on the 27B weights: nine attempts, one run, eval-2 in flight ([#80](https://github.com/kristerhedfors/lypning/pull/80))

- Run SFT, probe and GRPO on `Qwen/Qwen3.8-27B` through the pooled-sandbox
  verifier on an h200 (job `6aaa87465527934177ee9f34`): SFT trains, the probe
  admits GRPO, GRPO does not move the policy, and 7-case dev and test splits
  show no gain; the three eval-2 arms upload as they finish. Report in
  `training/reports/2026-09-16-fable-round02-run.md`.
- Evaluate in chunks: one `generate` call per chunk of cases with every draw,
  paired by chunk seed across arms, completions scored concurrently; GRPO
  rewards scored concurrently; the sequential form ran at ~30 s per draw.
- Cluster the paired comparison and the power curve by split component, so a
  family spanning source groups is one family and the rule accepts eval-2 v1
  (18 such families); the corrected power curve is in `training/EVAL2.md` §7 with the
  full pilot draw's base rates in §11.
- Verifier policy v3: a candidate that disagrees with itself across two clean
  oracle runs scores `unstable` instead of aborting the stage.
- HF job hardening from the day's failures: transport retries on Hub 5xx,
  install retries, per-job sandbox pool names, uploads after every stage,
  reuse of prepared bundles gated on the verifier identity, and a reused
  legacy eval run folds its redrawn harness errors to one row per draw.

**2026-09-15** — Run round-02 on Hugging Face: pooled sandboxes as the execution boundary ([#79](https://github.com/kristerhedfors/lypning/pull/79))

- Add the `hf-sandbox-pool` execution contract: every verification request
  runs the unchanged `container_worker.py` protocol in a fresh pooled
  Hugging Face sandbox on a host VM that is never the trainer's, under its own
  uid and Landlock ruleset, token never forwarded; the identity handshake holds
  because the trainer job and the verifier Space share one CPython base digest.
- Move the worker's harness path off `/runner` for that image: a pooled
  sandbox reads the standard system trees only. The Docker image is unchanged.
- Add `training/hf/launch.py` and `round02_smoke.sh`: submit a stage with the
  cost printed first and never without `--yes`; the smoke job prepares the
  starter through the pool, runs tiny-model SFT and GRPO on a real GPU, emits
  the plan and uploads the round directory to a private artifact repo.
- Cut and run the first real round: `python -m pipeline.eval2_split` cuts an
  assembled schema-3 bank by seed into a spent pilot draw, an eval-2 bank and
  a train bank, assigning whole `split_cases` components, stratifying by
  population, preferring a train bank that clears the pilot admission floor
  and exiting 1 on any `eval2_leaks` pair; `training/hf/round02_pilot.sh` is
  the job-side pilot (banks from the private dataset, `data_loop` review,
  pilot and benchmark bundles through the pool, base/SFT/probe, GRPO only on
  an admitted probe, matched test and whole-benchmark evaluations, paired
  reports, and an upload with a status field on every exit); `launch.py pilot`
  submits it with `--bank-path`, `--steps`, `--eval-draws` and `--seed` as
  job environment and refuses a pilot without a bank path.
- Amend the handoff: gate 6 admits either boundary; the pooled tier's residual
  risks are written down; a pilot still needs the reviewed dataset.
- GRPO warmup goes through `warmup_steps`: the pinned TRL has no
  `warmup_ratio`, which the first GPU smoke under these pins found.
- Add `training/STATUS.md`: the programme's dated scoreboard, gate state,
  ordered next steps, expected movement, a measurement assessment and the
  proposed training recipe, for the reviewing session's assessment.
- After the Codex review: a Space name is not an immutable image, so the
  runner refuses a Space whose Hub commit is not the pinned one and aborts on
  any sandbox response whose in-image identity (engine, harness, worker,
  interpreter) is not the admitted one; the launcher refuses a non-private
  artifact repository and quotes every operator word; the smoke script logs
  authored execution witnesses and the report no longer counts `no-code`
  rollouts as sandbox round-trips.
- `nt eval2-bank` assembles authored proposals into schema-3 cases: lints
  the task text against the one-home no-runtime-name list, recomputes every
  expected output by running the reference twice in the sandbox, requires an
  independent solution written from the text alone to agree, labels the
  population by running the engine on every test, attaches evidence ids, and
  files native mismatches as witnesses rather than cases.
- Pre-register eval-2 in `training/EVAL2.md`: the unconditioned task bank,
  the frozen correct-and-native primary metric, the decoding contract, seeds
  and arms, the pilot-draw power analysis, the leak check and the costs, all
  written before any draw is sampled.
- Add `nt eval2-leaks EVAL2 TRAIN` (`pipeline.eval2_leaks`): a bank-vs-bank
  contamination check between the schema-3 eval-2 bank and a training bank,
  one row per pair that trips any of five rules — task text at the split's
  similarity ceiling, an identical expected stdout of at least 8 characters,
  an identical reference fingerprint, a shared `source_group`, a shared
  evidence id — exiting 1 on any pair unless `--allow`; neither bank is written.
- Bridge the eval-2 bank to the legacy `nt` tree: `nt eval2-legacy --bank
  --output` (`pipeline.eval2_legacy`) projects schema-3 cases to `jsonl`-adapter
  records — one exact-`stdout` test per case (the first with a non-empty
  stdout), category `unobserved`, never the `lypning` kind, the bank's identity
  in `family:`/`group:`/`population:`/`capability:` tags — and `nt eval2-rows
  RUN --output` (`pipeline.eval2_rows`) turns a run's `attempts.jsonl` plus a
  legality replay into `training_metrics` rows, so `summarize` and
  `paired_comparison` serve both homes. Verified end to end on a scratch tree
  on 2026-09-16: two bank cases, `harvest kept 2 dropped 0`, `split frozen at
  100% held-out 2`, `holdout OK 2 cases`.
- `legality.arm` reports a per-case `native_rate` (verdict `MATCH` and correct,
  off the replay and never off the run's `passed`) beside `legal_rate` and
  `pass_rate`; `nt legality` prints `native` and `dnative` under `SLR`, a hole
  and not a zero for arms without an expected stdout.
- `backends.complete` passes `top_k` and `min_p` as top-level request keys
  (what the SDK's `extra_body` puts on the wire) only when given; `nt eval
  --top-k` (default none) records it in the run's sampling block and
  `stats.SAMPLING_KEYS` carries `top_k`, an absent key reading as null so
  every earlier run keeps its arm. `training/EVAL2.md` §5 amended to match.
- Add `stats.power_curve_clustered` and `nt power --eval2 --rows --mde --sizes`:
  the design-specific power analysis `training/EVAL2.md` §7 promised, resampling the
  pilot's family clusters to each candidate bank size and asking the §4 rule
  (family-cluster paired bootstrap, lower bound above the bar) at a uniform and
  a concentrated effect of the same mean lift, the null row as the
  false-positive rate; deterministic by seed, one seed per cell.
- Add the `benchmark` bundle purpose (`training.PURPOSES`/`ADMISSION`,
  `training_data.validate_benchmark`): a reviewed, isolated bank that
  `gpu/train_verified.py eval --eval-split all` measures whole and that
  sft/probe/grpo refuse to train on; `nt training-prepare --purpose benchmark`
  writes no SFT export, and `data_loop --purpose benchmark` reviews it.
- Add `nt eval2-select --output` (`pipeline.eval2_select`): the eval-2
  reverse-prompting candidates drawn from `data/classified.jsonl` over `tier1`
  and `refused` together, each static exclusion rule named and counted, every
  survivor regenerated twice in the sandbox under two hash seeds, and a
  shape-stratified `--limit`/`--seed` draw that ignores the refusal kind.
- Add `nt eval --system-file` and `training/prompts/subset-spec.md` (generated
  by `training/prompts/gen_subset_spec.py` from the crate's refusal sites; the
  file is held equal to the generator's output): the stage 0b prompt-ceiling
  switch, appended as its own paragraph after the bare system prompt and
  recorded as `meta.system_file_sha256`. `evaluate.prompt_signature` now folds
  in the rendered runtime contract, so the bare `prompt_sha` moved from
  `cbb7be44937a6b41` to `d23e9420b5812443` on 2026-09-16 with nothing the
  model sees changed; runs recorded under either value rendered one prompt.

**2026-09-15** — Check question delivery and assess the first live proposal pilots ([#78](https://github.com/kristerhedfors/lypning/pull/78))

- Fail missing, incomplete or truncated harvest delivery while retaining partial
  evidence; inspect question schemas and source-line identities without execution.
- Record the measured pilots and independent Fable review, and prepare a smaller
  question recipe under the same token ceiling. No automatic training admission.

**2026-09-15** — Hillclimb 83: five wrong answers from the un-replayed ledger surfaces, and one home for the earlier model's name ([#77](https://github.com/kristerhedfors/lypning/pull/77))

- Sweep the seven surfaces the ledger's un-replayed witnesses name with
  authored programs against the 3.12 reference round-02 pins; fix what they
  found, one mechanism per commit: list IndexErrors name the operation,
  `format(nan, '%')` and `#` on the empty type say what the host says,
  `bytes(source=)` and `open(file=)` bind their Argument Clinic names while
  `sorted`, `min`, `sum` and `reversed` count before naming, and a `|` union
  of classes refuses as `class-union` instead of killing the program at its
  first annotated `def`. Ledger entry in `docs/HILLCLIMB.md`.
- Name the earlier target model in one place:
  [`training/SWITCH.md`](https://github.com/kristerhedfors/lypning/blob/main/training/SWITCH.md)
  carries its name and the retired numbers; every other document says "the
  earlier model". Remove
  the retired GCP launch scripts and the earlier model's LoRA recipe. Run
  records, data files and the mismatch ledger keep their recorded names.

**2026-09-15** — Add question campaigns and the Codex–Fable repair/training roadmap ([#76](https://github.com/kristerhedfors/lypning/pull/76))

- Add bounded question-bank proposals, reviewed-catalog paging and matched
  nonthinking/medium-reasoning profiles without raising total token ceilings.
- Persist source-linked review queues and container-verified TRAIN-only repair
  routing; retain controls and block on native mismatches without auto-admission.
- Establish Codex orchestration, Fable writeups, independent round assessments
  and a decision ledger for verified SFT, preference and execution-reward work.

**2026-09-15** — Verify parallel harvest dispatch without API spending ([#75](https://github.com/kristerhedfors/lypning/pull/75))

- Convert validated concurrency explicitly to a number and rehearse the same
  matrix, OpenCode workers and evidence collector with a secret-free dry run.

**2026-09-15** — Make Cerebras preflight failures diagnosable without secrets ([#74](https://github.com/kristerhedfors/lypning/pull/74))

- Report HTTP status and response type without keys or response bodies; normalize
  surrounding secret whitespace consistently before any generation is allowed.

**2026-09-15** — Close five engine mismatches found while auditing the Stage 0a ledger ([#73](https://github.com/kristerhedfors/lypning/pull/73))

- `enumerate(iterable=…, start=…)` binds both of Argument Clinic's names, so
  the first spelling answers instead of dying with a TypeError CPython never
  raises; the same parameter given by name and position is the given-twice
  TypeError CPython raises.
- `divmod` over a float and a zero divisor says `float divmod()`, the wording
  `float_divmod` owns, rather than `//`'s `float floor division by zero`;
  `zero_div` folds it into the 3.14 respelling with the rest.
- A `\u` or `\U` escape naming a lone surrogate refuses as `escape` rather
  than dying as a SyntaxError CPython does not raise: CPython compiles the
  literal and no UTF-8 string here can hold it.
- `is` between two dict views refuses as `dict-view`: the value carries the
  dict's `Rc`, not the view's, so neither True nor False was a fact.
- A slice as a dict key says what the host says: `KeyError` with the slice's
  repr from 3.12, where slices hash, and `unhashable type: 'slice'` before;
  storing one refuses as `slice-key`. Four corpus sightings found it against
  the 3.12 reference this round pins; CI's 3.11 reference agreed on the class.
- Manual Qwen round-02 handoff, first session: engine built against 3.12,
  planner and starter preparation exercised, ledger audited; no training,
  no Docker boundary and no GPU on this worker, so every launch gate that
  needs an operator, a candidate image or hardware remains open.

**2026-09-15** — Add bounded parallel Cerebras/OpenCode project harvesting ([#72](https://github.com/kristerhedfors/lypning/pull/72))

- Collect authored Python project sessions with Qwen 3.8, isolated workers and
  a separate secret-holding proxy; cap parallelism, requests, tokens and time.
- Preserve source, tool/session traces, provider usage and failed observations
  for independent review. Keep Fable's active training inputs unchanged and
  require no per-script speed threshold or automatic training admission.

**2026-09-15** — Refactor the Qwen training experiment contract ([#71](https://github.com/kristerhedfors/lypning/pull/71))

- Split admission, verification, optimization and evaluation; freeze decoding,
  dependencies and sealed adapter provenance for the lypning-l-first experiment.
- Prevent source/solution leakage and stale native/fallback labels; gate RL on
  train-only reward signal and protect capability correctness at checkpointing.
- Add matched-seed evaluation and source/family-cluster uncertainty reporting.
  Document the manual next round, isolation gates and L capability priorities;
  no training or model-quality improvement is claimed by this change.
- Preserve exact private evidence and execution contexts; add reviewed-data
  lineage, container-backed candidate verification and a portable Claude Code
  handoff/round planner. Optimize correct-compatible coverage with no per-script
  speed gate; leave training, data approval and GPU provisioning manual.

**2026-09-15** — Expand lypning-l CSV, named-regex and exact numeric coverage ([#70](https://github.com/kristerhedfors/lypning/pull/70))

- Add lazy CSV list/tuple/string inputs, preserving source mutation, exhaustion
  and shared DictReader field names. Add ASCII named regex captures and named
  match access without admitting unsupported backreferences or templates.
- Add L-only wide integer byte conversion and exact finite float ratios,
  retaining the conversion cap and the core's frozen capability set.
- Verify cross-feature programs as raw bytes, require new capabilities to
  answer natively, and preserve refusal/rollback tests and route fixtures.
- Gate both Rust variants in Linux CI against unchanged target-specific
  budgets. Keep the default core gate distinct from the explicitly named oracle.
- Document coverage boundaries and dataset-authoring directions for the next
  manually started training round; require rebuilt engines and fresh bundles.

**2026-09-15** — Add a correctness-gated, lypning-l-first Qwen training path ([#69](https://github.com/kristerhedfors/lypning/pull/69))

- Add multi-input task-family bundles, explicit engine/oracle pinning, verified
  SFT preparation and an optional on-policy GRPO runner. Wrong runnable code
  receives no reward; correct fallback retains credit. Preserve the historical
  corpus and grades, and document GPU/data admission gates before real training.
- Fix the pipeline's process launcher, tracebacks-as-instability bug, replay
  cache identity and historical-test writes; protect fallback correctness at
  checkpoint selection. Add Linux/macOS verifier CI and the manual next-round
  handoff. Starter data is smoke-only; no training run is launched by this PR.
- Repair baseline CI's absent-engine barrier fixture, macOS build/test paths,
  and Python 3.13+ pool traceback parity. Give the CPU-heavy conformance corpus
  a shared-runner deadline on both arms without changing verdict rules.
- Expose conformance worker count and serialize CI's CPU-heavy captured
  benchmarks; retain identical reference/engine deadlines and fatal engine-only
  timeouts instead of treating a timing failure as semantic agreement.
- Safety-skip recognised package installs and model downloads before corpus
  conformance or benchmarking starts either arm. Preserve those captures but
  prevent known site-packages/cache mutations from contaminating the reference.
- Calibrate build-sensitive Python behavior against the selected executable,
  not its minor version alone: normpath identity, reverse conversion, iterator
  wording and zero-length signed integer conversion can vary within a release.
- Fix version-sensitive unpacking, index and numeric error messages; stop
  non-starred unpacking after one excess item instead of draining iterators.
  Add Python 3.14 package CI and native compatibility regression checks.
- Consolidate string keyword admission so the existing Python 3.13+
  `str.replace(count=...)` implementation is not blocked by a stale second table.
- Repair embedding examples and C++/Go/Swift/Lua host identity handling for the
  linked L variant; fallback demonstrations use genuinely unsupported source
  and retain their output, exit-status and no-duplicate-side-effect assertions.

**2026-09-14** — Gate target detection is tested against the artifact, not the host (direct commit to `main`)

- The target-classification test now supplies an explicit x86-64 ELF header
  instead of discovering a host binary and requiring a macOS Mach-O build to
  identify as Linux musl. The gate's artifact-derived behavior is unchanged;
  the test is deterministic on both supported host platforms.

> Four entries below cite a **commit**, not a pull request: those changes were
> merged straight to `main` and never had one. The numbers in their merge
> subjects (`#43`, `#45`–`#47`) do not refer to them — `#43` and `#45` are
> issues, and `#46` and `#47` were later taken by unrelated pull requests.
> The commit link is the one that resolves.

**2026-09-14** — the ceiling on everything reward-based, measured for $0, and fourteen cases it cost the engine (branch `claude/qwen-adapter-signal-ladder-ra5qbz`)

- **`nt legality --pass-at-k`: reachability, per case, by refusal kind.** Not how
  often a draw is legal but whether ANY draw is — the bound on every reward-based
  stage, since a case whose every rollout is refused hands GRPO a group of
  identically-scored rollouts and no gradient. It generates nothing — the draws
  were paid for by runs already on disk. Held-out `qwen38-baseline-k16`: **84.62%** of the 52
  cases that require tier 1 have a legal draw (42.31% legal *and* correct); the
  290-case train pool **82.38%** (34.72%). Both clear of the 60% floor, so
  reachability does not block the on-policy stage. Engine `23684d6c40738fcf`,
  k=16, thinking off, 2026-09-14.
- **The population is three, not one.** A ceiling case's test says
  `require_tier1: False` because falling back IS the answer there, so averaging
  it in scores the model for failing to do the wrong thing; a `stdout`-kind case
  never mentions the engine at all. The floor reads only the cases that demand
  tier 1. The third group is worth its own line: 8 held-out cases that just ask
  for a program reached the subset unprompted on **89.06% of draws** — the only
  preview here of what eval-2 asks at scale.
- **pass@8 is the number GRPO stands on, not pass@16** — 75.50% legal held-out
  against 82.22% at k=16 — so the ladder is reported at k=1,2,4,8,16 with the
  unbiased estimator rather than as `c > 0` under a smaller k's name.
- **53 MISMATCHes over 14 cases, found by the replay.** Programs this engine runs
  and answers differently from CPython, all from the train pool, all now in
  `training/data/engine-mismatches.jsonl`. Four reduce to one-liners: a
  `'\udcff'` surrogate escape is a `SyntaxError` here and a string in CPython;
  `enumerate(iterable=…, start=…)` is rejected here and accepted there; `v is v`
  for a bound `dict.values()` is False here and True there; `divmod(1.0, 0)` says
  *float floor division by zero* where CPython says *float divmod()*. Invariant 1:
  the train-pool row stays provisional until they close. Held-out has none.
- **And 6 that were the harness, not the engine.** Every sandbox run gets its own
  `mkdtemp`, so `print(os.path.abspath(...))` differs between the reference run
  and the engine run by construction — invariant 1's alarm, fired by the harness,
  on the one corpus nobody vets for determinism because a model wrote it. A first
  disagreement now buys one more CPython run: if the reference disagrees with
  itself the verdict is `UNSTABLE` — legal, never correct, never a MISMATCH. The
  replay cache now keys on a grader version as well as the engine fingerprint,
  because the fingerprint cannot see a change on this side of the comparison.
- **The four training-checklist assertions are tests now**
  (`training/tests/test_sft_rows.py`): completion-only loss, the empty
  `<think></think>` block byte-for-byte between training and eval, every SFT
  completion decoding back through the *eval's* extractor to the program the
  verifier passed, and the MLP projections being in `TARGET_MODULES`. The GPU
  script is read out of by `ast` rather than imported, which keeps its one-file
  contract. `build_examples` also now refuses a tokeniser that merges across the
  prompt/completion boundary — that shifts completion-only masking by one token
  and leaves the loss curve looking perfectly healthy.
- **The documentation checker stopped crying wolf about a second suite.**
  `tests/test_docs.py` matched only the tail of a test path, so a document citing
  a test file under `training/tests/` was reported as promising a file that does
  not exist — while the file did. Paths now resolve as written, which also means
  a path named in a document is checked where it actually points.
- **`training/LADDER.md`** carries the plan these rungs belong to, and the three
  Nemotron numbers that must stop being decision inputs for a Qwen arm.
  `training/README.md` now says on line one that the model is Qwen and the
  directory name is history.

**2026-09-14** — What differs from Python was written down twice and both copies were stale ([#67](https://github.com/kristerhedfors/lypning/pull/67))

- **`docs/DIFFERENCES.md` is the one home for the delta.** What the engines are
  architecturally — no bytecode, a compiled-in module table instead of an import
  system, no object model, no introspection, no GC, a native stack whose guard
  refuses rather than raising `RecursionError` — then the builtins, exception
  names and modules that resolve, the four modules `lypning-l` serves as a named
  attribute list, the eight `cap-*` features, the refusals that skip the larger
  engine, and the constructs that exit 90 before a statement runs.
- **The copies it replaces had been wrong for six capabilities.**
  `docs/SUBSET.md` §3.3 and `docs/LYPNING.md` §3 both named `collections` and
  `pathlib` as the whole of what `lypning-l` adds, written before `cap-re`,
  `cap-csv`, `cap-glob`, `cap-base64`, `cap-hashlib` and `cap-bigint`;
  `docs/SUBSET.md` §6 still sent `import csv` and `import re` to CPython. Both
  tables are now pointers, and those two rows say what `lypning-l` answers.
- **`tests/test_differences.py` holds the page to the crate.** Every list is
  read out of `builtins.rs`, `modules.rs`, `route.rs` and `hashlib.rs` rather
  than restated, and one test fails on any `unsupported: <kind>` the crate does
  not raise. A stale sentence compiles, links, routes and benches at the same
  speed as a true one; this is the check that would have caught the two it
  replaces.
- **On lypning.dev**: a hero link, a line in the opening paragraph, and its row
  in the document grid, all three from the one `site/build.py` `PAGES` entry.
  `site/build.py --check`: 22 pages, every intra-site href resolves.

**2026-09-14** — the serving stack moves subset legality by more than the adapter does (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **v2 run 1: context distillation, and a second null.** `nt sample --hinted`
  draws with the engine's live refusal and a matching `COOKBOOK` pair in the user
  turn, and trains on the bare prompt. Correctness `40.71% → 44.55%`, **+3.84pp**
  95% CI [+0.80, +7.50], McNemar p=1.0000 — bootstrap fires, McNemar does not, no
  win under §3 for the second time. Legality **ΔSLR −0.21pp** 95% CI [−3.19,
  +2.67], MDE +3pp not met. Gates all pass: correctness +3.71pp, import retention
  1.06×, **tokens/program −24.4%**.
- **And the number that matters is the null.** The same base weights through two
  kernels — `fla-0.5.2` against the `torch-reference` fallback the Hopper
  backward bug forces — differ by **ΔSLR +1.57pp** 95% CI [−0.51, +3.89], while
  correctness moves −0.14pp. Comparing the hinted arm against the stored `fla`
  base would have read about +1.2pp: a spurious improvement of the wrong sign,
  manufactured entirely by a kernel swap. The matched control was bought for
  $5.90 on a decision taken before any number was read, and it is the only reason
  this reports a null. **The serving stack is now a precondition for the legality
  endpoint, like the engine fingerprint** — and legality is the more fragile
  measurement: the same swap that moved it 1.57pp moved correctness 0.14pp.
- **Two silent wrong answers, found on-policy over 2,360 model-written programs
  and unreachable from any of the 6,321 corpus programs.** `'a' in d.keys()`
  raised `TypeError: argument of type 'dict_keys' is not iterable` where CPython
  answers `True` — `contains` had no `DictView` arm, so every view fell to the
  generic tail at exit 1, which the dispatcher never retries. Now served: `keys`
  is the dict's own lookup, `values` and `items` scan with `elem_eq`, sixteen
  shapes differential-tested with fifteen exact and the sixteenth a correct
  `nan-identity` refusal. And `print(__file__)` raised `NameError` where CPython
  prints the path; refused rather than bound, because binding it needs the
  invocation's path at every entry point and would then be wrong under `-c`.
- **A completion that spent the whole decode budget was being scored as wrong
  rather than cut.** 90 completions across three arms hit the 2,048-token cap and
  none passed, recorded as `syntax-error` (57) and `wrong-output` (31). The cap is
  not neutral: base 4.3%, tuned-unhinted 1.9%, tuned-hinted 1.4% — a fine-tune
  that makes the model terser hits it less, so the confound runs the same way the
  treatment does. `nt grade` now derives truncation from the token count and the
  header's budget and counts it under its own name; the hinted arm's
  `syntax-error` count falls 15 → 2 and `pass@1` does not move.
- Bytes: `lypning` 1,142,992 (**9 blocks**), `lypning-l` 1,323,216 (**11
  blocks**). `conformance` over the 6,321 corpus programs loaded this date:
  MISMATCH **0** on every arm, monotone violations **0**, routing UNSAFE **0**.
  Spend $23.08.

**2026-09-14** — `lypning-l` had not compiled since #63, and two silent wrong answers were hiding behind the stale binary (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **The capability build was broken on `main` for nine days.** #63 added
  `BinOp::MatMul` and guarded it in `Interp::binop` — correctly, because reaching
  the numeric fast path with `@` found an `unreachable!()` and aborted at exit
  134. `bigint::int_op` also matches exhaustively on `BinOp` and lives behind
  `cap-bigint`, so `variant-m` compiled and `variant-l` did not. `lypning build
  --rust` printed `FAILED` and returned 1; the table was read and the exit code
  was not.
- **Which means every `conformance` run since then graded a stale `lypning-l`.**
  `engines.find` reads `$LYPNING_HOME/bin` first, and the binary sitting there
  predated the nineteen wrong answers #63 closed. "MISMATCH 0" on #63 and #64 was
  true of `lypning` and untested on `lypning-l`.
- **One home for the `@` TypeError**, `err::matmul_type_err`, called from the
  guard and from the arm the compiler demands. That arm answers rather than
  panicking: a panic there is exit 134, which is not the program's own exit code,
  so the dispatcher cannot hand it back. Deduplicating the format string made
  `lypning` 48 code bytes smaller.
- **`round(2.5e25, -25)` answered `2e+25`; CPython answers `3e+25`.** The
  negative-`ndigits` float path divides by a power of ten first, and the quotient
  is not the value: `2.5e25 / 1e25` is exactly 2.5 and looks like a tie, while the
  double is 25000000000000000905969664, above the halfway point. Below 2**53 the
  tie test is sound and stays; at or above it the engine cannot tell a tie from a
  near-miss without the exact decimal expansion, and now refuses.
- **`os.environ` was missing a key CPython puts there.** PEP 538: when `LC_CTYPE`
  is unset, empty, `C` or `POSIX`, CPython's startup coerces the C locale and
  `setenv`s `LC_CTYPE=C.UTF-8` into its own environment. Measured on this box:
  140 keys under CPython, 139 here. Whether the coercion fires depends on whether
  the locale can be *set*, which needs libc, so the engine refuses in exactly that
  state and serves the exact environment everywhere else (verified: 140 = 140
  under `LC_CTYPE=C.UTF-8` and under `PYTHONCOERCECLOCALE=0`).
- **Both were found on-policy, not by the corpus.** Zero of the 9,064 corpus
  entries loaded on this date read `os.environ`; 49 of the 2,362 model-written
  programs in the run of record do. The corpus's blind spots are shaped like its
  capture mechanism.
- **A MISMATCH is now checked against a second reference before it is
  reported.** A MISMATCH claims an engine answered what CPython does not, and
  that claim is only worth making if CPython answers the same twice.
  `_RUN_SPECIFIC` screens the program text and cannot see the whole class — a
  default `repr` prints an address no two processes share without the program
  naming `id`, `tqdm` writes its own throughput, an HTTP error carries the
  request id the server just minted. Six entries were being reported as engine
  bugs on this date, all six captured from agent sessions, none matchable by
  CPython against itself. The second reference is taken only where a MISMATCH
  would otherwise be reported (~0.1% of entries) and is compared with
  `classify`, so it inherits every waiver the arms get.
- **`nt legality`: the subset-legality endpoint, computed from programs already
  paid for.** ΔSLR over both arms of the run of record, cluster-bootstrapped by
  case, with correctness / supported-import-retention / token-length as gates
  rather than contributors. **ΔSLR −1.00pp, 95% CI [−3.36, +1.51]** against a
  base-vs-base null of **−0.22pp [−2.46, +2.03]** — the adapter's effect on
  subset legality is inside the noise floor, while correctness over the same
  programs moves +4.92pp. Spend $0.00: it replays stored programs. Also
  `nt grade --require-fingerprint`, and `nt refusals --held-out` no longer calls
  its own output a build order. `training/REVIEW.md` is the response to the
  external review that asked for the endpoint; `training/PREREGISTRATION.md` §7
  registers the rules for a v2 that has not been run.
- **The core answered what its own superset refused, and one `#[cfg]` was
  why.** With `lypning-l` finally building, `conformance` reported 2 monotone
  violations — `lypning` MATCH, `lypning-l` UNSUPPORTED — which invariant 10
  forbids. Not a capability gap but a timing one: `route::static_stop_check`
  refuses `glob-order` before the program runs, and it was gated to the variants
  that *have* `cap-glob`/`cap-hashlib`, on the reasoning that a core without the
  capability cannot serve the call anyway. It can't — but it refuses LATER, at
  the import, so a program that died first was answered by the core and refused
  by its superset: `open("/nonexistent"); import glob; print(glob.glob("*"))` was
  exit 1 with CPython's traceback on `lypning` and exit 90 on `lypning-l`. The
  check is now compiled into every variant. `route.rs` is not gated per variant
  and `spectrum_stop` is set only where no rung can serve the call, so the core
  reports the same accurate kind; the substring guard means a program naming
  neither pays nothing. Measured: monotone violations **2 → 0**, `lypning` MATCH
  2927 → 2925 (the two that were violating, and nothing else), coverage 46.3%
  either way, `lypning-l` unmoved at 4554, +736 code bytes and **9 blocks either
  way**.
- Bytes: `lypning` 1,142,992 (**9 blocks**), `lypning-l` 1,319,120 (**11
  blocks**) — the first measurement of the latter since #62, because until this
  change there was nothing to measure.


**2026-09-13** — The last four the fold found, and `conformance` is back to MISMATCH 0 (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **Annotations are evaluated when the `def` runs.** `parse.rs` dropped them with
  a comment saying CPython drops them too; CPython evaluates them at definition
  time unless `from __future__ import annotations` is in force, and this engine
  refuses that import, so there is no second case. `def f() -> Undefined: pass`
  ran to exit 0 where CPython raises `NameError`, and any side effect the
  expression had was lost with it. Parameters left to right, then the return,
  which is CPython's order and is pinned by a side-effecting test.
- **`str` methods reject keywords they do not take.** `"a b".split(" ", 1, foo=1)`
  answered `['a', 'b']`. The allowed set is a table now, with `str.format`
  exempt because it really does take arbitrary keywords. `tests/test_keyword_grid.py`
  immediately caught the first attempt using one sentence for both cases:
  CPython says `str.strip() takes no keyword arguments` for a method that takes
  none and the invalid-keyword sentence for one that takes some.
- **`int()`'s error message truncates like CPython's.** The format is `%.200R`,
  so the repr is cut at 200 characters — taking the closing quote with it, which
  is why CPython's message for a long literal ends mid-string. Printing the whole
  repr grew without bound: 252 characters where CPython gives 240. Checked at
  n = 100, 200, 210, 220 and 5000.
- **`@` parses.** No type here implements `__matmul__`, so every use is CPython's
  own TypeError — but the file has to PARSE to get there, and refusing at the
  lexer made `A @ B` a SyntaxError at exit 1 for a program CPython reads and then
  rejects for a missing import. Parsing it first found something worse: the
  numeric fast path reached an `unreachable!()` and **aborted the process at exit
  134**, which is not the program's exit code and so is the one outcome a
  dispatcher cannot hand back. `@` now raises before any arithmetic.
- **`conformance` MISMATCH 19 → 0** over 6,323 programs, MATCH 2,911 → 2,927.
  Suite 7,680 passed, 0 failed; `gate` PASS; `doctor` 0 FAIL.
- Known and not fixed: `x @= 2` reports `for @` where CPython says `for @=`, the
  same exception type with one character different, and threading an
  augmented-flag through the binop path for it is not worth the coupling.

**2026-09-13** — `nt harvest` with no arguments was a delete (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **The default source was `study` alone.** That adapter yields 26 cases; the
  corpus it replaces is 223 `lypning` cases and 26 `study` ones. A bare
  `nt harvest` therefore rebuilt 249 cases into 26, took four frozen held-out
  cases with it, and broke the split lock — discovered here by running it, and
  recovered from git. A default that cannot rebuild what is on disk is not a
  default. It is `["lypning", "study"]` now.
- **The `lypning` adapter was not in `--source`'s help at all**, so the source of
  most of the corpus was undiscoverable from the CLI. Listed, with its
  `cache=PATH` option.
- **A harvest that would drop frozen held-out cases now refuses before writing.**
  `split.freeze` already declines to honour a lock whose cases have vanished, but
  it finds out at the *next* `nt split` — after the corpus that dropped them is
  on disk, from a different command, with nothing linking the two. The refusal
  names the casualties, says nothing was written, and points at
  `--allow-holdout-loss` for the case where re-freezing is the intent. Verified
  by re-running the exact command that caused the loss: it now exits 1, names the
  four ids, and the corpus md5 is unchanged.
- Which four is itself the finding: they are the cases whose programs the engine
  has since learned to run. The guard surfaces corpus depletion as a refusal
  instead of as silent data loss.

**2026-09-13** — A block count is not portable, and `gate` had been saying so in the wrong direction for nine days (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **`lypning gate` read FAIL against a binary that had never been under its
  budget.** `VARIANT_BLOCK_BUDGET` held one number per variant and compared it
  against whatever the host built. The core's `8` came with the claim
  `1,007,824 B on musl = 8 blocks, 40,752 B of headroom`; rebuilding `7a72aaf`,
  the commit that wrote that sentence and froze the core, gives **1,052,880 B =
  9 blocks** on this container's toolchain. 45,056 B of the gap is toolchain,
  not code, and the gate was reporting a unit mismatch as a size regression.
- **The budget is keyed by `(engine, target triple)` now**, and `gate.elf_target`
  reads the triple off the artefact's own ELF header rather than assuming the
  host's. The size row names it: `budget 9 for x86_64-unknown-linux-musl`. A
  triple nobody has measured is reported and **not gated**, and says so — a
  budget invented for an unmeasured target is the defect this change exists to
  stop, not a stricter version of it.
- **Growth, on one toolchain, is not the story the old number told.** `7a72aaf`
  to `6f3ea7c` is +77,824 B across nine days with **no change in block count**:
  it was 9 then and it is 9 now. The core is still frozen in the sense that
  matters — new capability goes to the larger variant — and the number recorded
  is what the frozen core costs here.
- **§C6's run of record was Darwin arm64** (818,080 B = 7 blocks, three checks
  unmeasured because that host has no `file(1)`, `readelf` or `strace`) and the
  test replays it on musl with all three present, so it could never hold. Re-taken
  on this container; the Darwin numbers are kept once under invariant 3's
  `measured upstream` carve-out, because they are a real measurement of a
  different target rather than a wrong one.
- **The manifest pins the property, not the number.** `want <= 8 blocks$` pinned
  a count from another machine into a test that runs here; it now pins that the
  size row NAMES the target its budget was measured on, which is the absence that
  let one number be enforced against another. Two tests added: an unmeasured
  target is reported and not gated and must say so, and the triple comes from the
  artefact rather than the host.
- The suite is green: **7,680 passed, 0 failed**, and `lypning gate` PASSes.

**2026-09-13** — The test statistic was chosen before the data, and the choice is written down as a change of rule (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **The pre-registered rule had 12% power against the effect it was bought to
  detect.** `stats.paired_delta` called a case discordant when its per-case
  *mean* moved. At k=16 that counts one Bernoulli draw of noise the same as an
  outright acquisition, so against the shape a rejection-sampling LoRA actually
  produces — a handful of previously-hopeless cases solved — the rule would have
  reported "no win" almost whatever happened.
- **Discordance is now solved / not-solved**, which is what §3's own words
  ("case-level flips", "how many CASES moved") and its `2/2**b` arithmetic
  always described. Measured against the re-graded baseline's own per-case
  scores at the primary n=70, 400 trials, k=16: six solved cases 12% → **27%**,
  eight 20% → **60%**, ten 32% → **90%**, while the uniform shapes are unchanged
  (+2pp 31/31, +4pp 84/85, +6pp 99/99) and the null fires at 0% under both. At
  four solved cases it is slightly worse, 6% → 4%, which is recorded rather than
  omitted.
- **It is a CHANGE OF RULE, not a clarification, and it was made after seeing a
  power curve.** What makes it admissible is when: no adapter exists, no SFT set
  has been sampled, no dollar has been spent, no treatment arm has been graded.
  §3c records the decision, its date and its author, and keeps that sentence at
  the top permanently.
- **The evidence it is not the statistic that flatters us: it makes the null
  test harder to fire.** Engine drift alone — the same 1,184 completions
  re-graded — fired the old rule on the naive all-74 denominator at p=0.0312.
  Amended, that comparison gives p=0.2500 and does not fire; the primary
  denominator moves p=0.2500 → p=0.5000. A rule chosen to produce wins would not
  close a false positive on a known null.
- **Both counts stay reported** (`moved_up`, `moved_down`,
  `mcnemar_p_mean_moved`) — a rule whose alternative you can no longer see is a
  rule nobody can check — and `power_curve` now simulates the definition the
  rule uses, having previously simulated one leg of a two-leg rule. Two tests
  pin it: one builds six acquisitions against nine noise-jitters and shows the
  amended rule fires at exactly `2/2**6` where the superseded one cannot fire at
  any effect size; the other pins that a conjunction never exceeds either leg.

**2026-09-13** — The pre-registration, audited against the tree it claims to describe (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **The primary rule is near-blind to the shape this fine-tune will produce, and
  §3's "six flips" floor is not the binding constraint.** `paired_delta` calls a
  case gained when its per-case *mean* moved, so at k=16 sampling noise loses
  ~9.5 of the 70 cases every run — `c == 0` in 0 of 2,000 null trials, against a
  floor whose premise is `c == 0`. Measured on the primary 70 at the re-graded
  rates, 600 trials per row: a fine-tune that solves six previously-hopeless
  cases outright (+8.6pp) fires the rule 14% of the time, twelve 49%, fifteen
  71%; k=32 and k=64 change nothing. A uniform +4pp is 80% and +6pp on the
  rewrite stratum 96%, so the design is well-powered against a broad style shift
  and underpowered against a concentrated one. New §3c states this, states the
  free fix (binarise the discordance: six solved → 49%, eight → 83%, ten → 97%,
  null 0%), and does **not** adopt it — that is a change of rule and is owed a
  dated human decision before the sampling run.
- **The engine is now pinned as part of the pre-registration** (new §3d).
  `stats._engine` is silent, not withholding, when either side recorded no
  fingerprint, and both runs on disk record none; the primary denominator is also
  computed by whatever binary is on the box at `nt compare` time. Fingerprint,
  both binary hashes and the oracle CPython written down.
- Corrections, all clarifications and none to a verdict: §2(b)'s rewrite figures
  were the all-74 ones inside the paragraph that excludes the degenerate cases
  (0.202 and 17 movable, not 0.232 and 20); §2(d)'s stdout row counted train
  cases in the held-out column (7, not 11) and the 0.85 row recounts to 26;
  §3b's all-74 row reads 0.4375 / +3.38pp, which is what the run's own
  `summary.json` says; §5's "one vLLM instance, one Job" is not what
  `gpu/lypning_lora.py` implements, though the one-stack requirement it stands
  for survives.
- Verified unchanged against a fresh build at `7846191`: 74 cases, 4 degenerate,
  5 unsatisfiable, 65 usable; 58 train cases leak and 117 are clean; the sampling
  pool is 93 cases (66 rewrite, 27 ceiling); 59 of 1,184 draws truncated across
  19 cases, 0 passing; the §3b null still fires on all-74 (p = 0.0312) and still
  does not on the primary 70 (p = 0.2500).

**2026-09-13** — The money path, walked without spending: four guards, one wrong LoRA target set (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **`nt sample` would have folded another model's draws into the Qwen SFT set.**
  `--name` defaults to `v1`, `data/sft/v1/draws.jsonl` is committed and was drawn
  from Nemotron, `draws.jsonl` is append-only and `fold_draws` reads all of it.
  Measured 2026-09-13: 1,488 of its 2,800 rows belong to all 93 cases of the
  current pool, 457 of them already passing. Sampling now claims its directory in
  `backend.json` and refuses both an unclaimed one and one another model drew —
  before the first request.
- **A sampling run that died restarted from zero and paid twice.** `--resume`
  skips the `(case, draw)` pairs already recorded and retries the ones recorded
  as harness errors; the report says how many of each. `--dry-run` prints the
  pool, the draw count and the ceiling cost and sends nothing. `--max-spend` with
  no prices exported is now an error rather than a cap that cannot fire
  (`training/PREREGISTRATION.md` §2(f)).
- **The lock is verified before sampling, which §4 has promised since it was
  written.** Only `evaluate.load_holdout` did it; `sample.train_cases` loaded the
  lock without checking the corpus against it, so a corpus edited after the
  freeze left the exclusion excluding ids that were no longer the held-out cases.
- **`gpu/lypning_lora.py` still excluded `out_proj`** — the file `hf jobs` runs,
  as opposed to the YAML the 2026-09-12 correction was written against. In HF
  transformers' `Qwen3_5GatedDeltaNet` the kernel takes query/key/value/g/beta
  and `output = self.out_proj(core_attn_out)` is an ordinary module call
  (`modeling_qwen3_5.py:662`, declared at `:540`), so a LoRA there applies. The
  exclusion froze 48 projections carrying 1,509,949,440 base parameters and cut
  the adapter from 116,727,808 to 108,077,056 trainable parameters. The belief is
  replaced by a measurement: the smoke test now requires every targeted leaf to
  carry a nonzero gradient, on `cpu-basic`, before the checkpoint downloads.
- **`stats._arm` was inert on the planned eval.** It withholds a delta on
  differing `backend.base_url`, and nothing on the GPU path wrote one — so it
  would not have fired if the two arms really had come off different stacks. The
  completions header now names the stack (transformers, torch, kernels, device).
- `training/RUNBOOK.md` §1 re-derives the memory arithmetic in one unit against
  the script that runs, and adds the generation cache it had no line for; §2 records that
  the implementation is `transformers`, not vLLM, and two Jobs, not one.

**2026-09-13** — A run records which engine graded it, and a cross-engine delta is refused (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **The baseline was pinned by its pass rate; the engine that produces it was
  not pinned at all.** `qwen38-regrade-20260912` exists because an engine that
  gained `math` and `type()` moved the same 1,184 completions from 40.37% to
  43.75% with no model anywhere near it. That was caught by a person reading a
  paragraph, and nothing stopped it recurring.
- **`engines.identity()` writes the engine down on every run** — the sha256 and
  `--version` line of each binary in the chain plus the oracle CPython the
  acceptance test's correctness leg runs on, since the engine started answering
  as the CPython it was built for (#61). The hash, not the version: `0.1.0` is
  every build this project has made. Two clean builds of `7846191` are
  byte-identical here (measured 2026-09-13), so it does not fire on a rebuild
  that changed nothing.
- **`stats._engine` withholds the subtraction on any difference**, in `_arm`'s
  shape and for `_arm`'s reason: a known difference blocks, an unrecorded engine
  is an unknown and blocks nothing, so every run graded before the field existed
  compares exactly as it did.
- **And the third engine, which is in neither run:** `nt compare` computes the
  pre-registered denominators from the *live* binary, so it now refuses when
  that is not the engine the runs were graded by.
- **The baseline was re-measured at HEAD rather than argued about.** The 1,184
  completions reconstruct byte-identically from the recorded attempts and
  re-grade to pass_rate 0.4375 at engine `7846191` — 0 of 1,184 attempt flags
  changed, 0 of 74 per-case rates moved. Run against both builds directly,
  1 of 1,184 programs gets a different answer and the difference is a refusal's
  wording on stderr, which no acceptance test reads.

**2026-09-13** — The SFT mixture is chosen, not yielded: three populations, and the hazards made loud (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **The training set was three tasks in one file, and yield was picking the
  mixture.** `sample.population` reads the population off each case's own test —
  `lypning`+`require_tier1` (rewrite, the task), `lypning` without it (ceiling,
  where keeping the import is the right answer), any other kind (unobserved, no
  engine is ever asked). Folding the recorded `data/sft/v1` draws over the 117
  clean train cases gives 113 rows, 27 of them rewrite — and 208 rows, 47 of
  them rewrite, at the keep the run was to use: a ceiling case passes
  whenever the model can copy, so the easiest population was buying most of the
  training set. All 60 ceiling rows in the shipped SFT file are refused by both
  engines (measured 2026-09-13) — verified correct, verified not to route.
- **`nt sample` prints its pool before it spends.** `sample.sampling_pool` drops
  the leaks it already dropped, the unobserved population, the 5 clean train
  cases the engine now RUNS — where the program the prompt hands over passes the
  case's own test as-is, so the sampler can learn to echo its input — and the 3
  nothing passes. 175 -> 93 cases, 66 rewrite and 27 ceiling.
- **A ceiling case keeps one target, not four.** Distinct kept programs within
  one ceiling case are 0.865 mean pairwise similar, above the 0.85 at which
  `split.SIMILARITY_CEILING` calls two cases the same question; within a rewrite
  case, 0.427.
- **`nt leaks --sft` asks the targets, not the cases.** 16 of the 154 rows in
  `data/sft/v1` are verified solutions to 7 held-out cases; every train case
  behind them is dropped by the similarity filter and the clean re-fold solves
  none — the first non-tautological evidence that the exclusion excludes.
- **The abandon threshold now reads on the population it is about.**
  `sft_examples_on_task`; `training/PREREGISTRATION.md` §2(g) and §2(b) re-run at HEAD's
  engine — the 70-case primary denominator holds, same four degenerate ids.

**2026-09-12** — The binary now says which CPython it was built for, and three things read it (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **A binary built for one CPython and graded against another was invisible.**
  Nine answers became compile-time-version-dependent when `err::REF_PY_MINOR`
  landed, and nothing in the tree reported which version a given binary carried:
  a fresh worktree where `uv` resolved `requires-python = ">=3.9"` down to 3.9
  against an engine built for the host's 3.11 fails 137 tests (2026-09-12), not
  one of them an engine defect. `--version` now ends `for cpython 3.11`,
  printed from the constant the branches read, and that is the only new fact —
  `doctor` and the suite ask the artefact rather than re-deriving it.
- **`lypning doctor` gained one row, `reference cpython`.** OK when the core was
  built for the CPython it falls through to; FAIL, naming both and `lypning
  build --rust`, when they differ; WARN for a core that predates the field; NOTE
  when no CPython answered. It is a FAIL and not a note because it is a measured
  disagreement, not a hole. `lypning --version` was left alone: it is the
  package's version, answered without a filesystem or a spawn and with no binary
  needed, and a state question belongs in `status`/`doctor` (invariant 8).
- **`build.rs` no longer guesses behind a stale pin.** A `$LYPNING_CPYTHON` that
  does not run dropped straight to `REF_PY_FALLBACK`; it now falls through to
  `python3`, and only a host with no python at all gets the fallback. Measured
  on this 3.11 host, `env -u LYPNING_REF_PY LYPNING_CPYTHON=/nonexistent/python3
  cargo build --release`: `for cpython 3.13` and `min() iterable argument is
  empty` before, `for cpython 3.11` and `min() arg is an empty sequence` (what
  the host says) after; with no python on `$PATH` at all, still 3.13. The Python
  side needed nothing — `engines.find_cpython()` raises on a pin that is not
  there and `build.reference_python_env()` returns `{}`, verified.
- **One test was vacuous and one did not exist.**
  `test_the_build_tells_the_crate_which_cpython_it_stands_in_front_of` compared
  `find_cpython()` with `sys.version_info`, which agree under every harness
  (`uv run --python X` puts X first on `$PATH`), so it would have held for the
  implementation it forbids; it is now pinned against a stand-in interpreter
  answering `3.99`. And
  `test_the_suite_and_the_engine_it_grades_speak_the_same_cpython` is the line
  that was missing: the grids take their oracle from `sys.executable`, so it
  asks the BINARY what it was built for and fails with a sentence naming both
  interpreters and what to run. In the 137-failure worktree above it is the one
  that says why — and a core really built for 3.13 (`env -u LYPNING_REF_PY
  LYPNING_CPYTHON=/nonexistent/python3 cargo build`) turns `doctor` red on the
  same fact rather than leaving it to the grids.
- **No engine answer moved.** `conformance --engine lypning` is MATCH 1571 /
  UNSUPPORTED 933 / MISMATCH 0 over 2,504 corpus programs (3,688 loaded) before
  and after, `--plan` is byte-identical, and both binaries are the same size to
  the byte (`lypning` 1,130,704 B / 9 blocks, `lypning-l` 1,310,928 B / 11) with
  16 B more `.text` each — the `--version` string and its one `{}`.

**2026-09-12** — Three more answers were worded for one CPython, and §6a stopped calling itself a list of everything (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **Three wordings the mechanism that landed hours earlier had missed.**
  `1 % 0` said *integer division or modulo by zero* where 3.11, 3.12 and 3.13
  say *integer modulo by zero*; `float([])` said the 3.10+ wording on a 3.9
  host; and `int([])` matched NO supported CPython at all — it had dropped the
  *bytes-like object* clause that 3.9's and 3.10+'s wordings both carry, so it
  was a plain bug wearing a skew's clothes. All three reach STDOUT through
  `except … as e: print(e)`, which is why `conformance` cannot see them:
  `stderr_shape` keeps the exception TYPE and throws the message away, so only
  a program that PRINTS the message is graded.
- **The same mechanism, not a new one.** `err::int_mod_by_zero` and
  `builtins::real_number` are two more compile-time branches on
  `err::REF_PY_MINOR`, read by four sites. Each boundary was measured on
  3.9.23, 3.10.20, 3.11.15, 3.12.3 and 3.13.12 and written down at the site,
  and 3.10 is the host that proves they are two boundaries and not one: `%`
  takes the old wording there and `int()`/`float()` take the new one. No
  refusal was added — every one of these is an answer CPython gives.
- **`%` is one operator, not the family.** `1 // 0` and `divmod(1, 0)` keep the
  long sentence on all five versions, so the branch is `%`'s alone — in
  `ops.rs` for machine integers and in `bigint.rs` for the wide ones
  `lypning-l` reaches. The new row in `tests/test_semantics.py` prints the
  neighbours beside the fix, so widening the branch breaks it.
- **`docs/SUBSET.md` §6a no longer claims a completeness it never had.** Its
  closing paragraph read as an enumeration — "four further divergences in the
  same range" — and these three were outside it. A differential sweep of
  14,808 generated one-liners on the engine and on all five interpreters
  (2026-09-12; one-off, not checked in) found 622 programs whose answer differs
  across the five CPythons in 202 patterns — the engine refuses 62 of those
  patterns, matches its host on 67 and answers differently on 73 — and 1,461 of
  11,777 answered programs on a message the host does not write, from 28 of the
  crate's 225 message literals. §6a now says the table is the list of
  divergences the engine HANDLES, and names the residue for whoever takes it.
- **Byte-neutral.** `lypning` 1,130,704 B / 9 blocks and `lypning-l`
  1,310,928 B / 11 blocks unchanged, with 866,919 → 866,999 B and
  1,010,199 → 1,010,391 B of code. `conformance --engine lypning` is MATCH 1571
  / UNSUPPORTED 933 / MISMATCH 0 over 2,504 corpus programs (3,688 loaded) on
  both builds, and `--plan`'s 108 blockers are byte-identical.

**2026-09-12** — Nine answers were worded for one CPython; the package supports five (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **The engine spoke a CPython the host was not running.** Its message tables
  were read off 3.14.5 and `pyproject.toml` says `requires-python = ">=3.9"`, so
  on a 3.11 host `min([])` said *iterable argument is empty* where CPython says
  *arg is an empty sequence*, `str(b'x','u','s','e')` said *str expected at most
  3 arguments, got 4* where CPython says *str() takes at most 3 arguments (4
  given)*, and `type(os.path.normpath)` said `builtin_function_or_method` where
  CPython says `function`. 13 tests said so.
- **`build.rs` now asks the reference interpreter what version it is** and
  compiles the answer in as `err::REF_PY_MINOR`; nine sites branch on it.
  `lypning build --rust` passes `LYPNING_REF_PY` from `engines.find_cpython()` —
  the interpreter `conformance` grades against and the dispatcher falls through
  to — and a bare `cargo build` asks `python3` itself. With no python reachable
  the fallback is the version the tables were written against, so such a build
  is the behaviour that shipped before.
- **The rule is not "this construct is version-dependent" — it is "the ANSWER
  differs across 3.9 … 3.13".** Every boundary was measured on all five with
  `uv run --python X` and three of them were a version off in the tree's own
  comments: `str`/`int` arity moved at 3.13, not 3.12; `os.path.normpath` at
  3.13, not 3.12; `re.Pattern.match`'s type at 3.10, not 3.11. The table is
  `docs/SUBSET.md` §6a.
- **No refusal was added and nothing stopped being answered.**
  `conformance --engine lypning` is MATCH 1571 / UNSUPPORTED 933 / MISMATCH 0
  over 2,504 corpus programs (3,688 loaded) before and after, and `--plan`'s 108
  blockers and its whole by-kind list are unchanged.
  `sorted([3, 1], strict_mode=True)` still exits 1 with CPython's own TypeError,
  which is the case a broader rule would have refused.
- **It cost negative bytes.** Every branch is against a compile-time constant,
  so one wording survives per build and five duplicated `format!` sites became
  one helper: `lypning` 1,130,704 B / 9 blocks unchanged with 866,983 → 866,919
  B of code, `lypning-l` 1,310,928 B / 11 blocks unchanged with 1,010,247 →
  1,010,199 B.

**2026-09-12** — Four defects shipped earlier the same day, and the float `repr` that was wrong before any of them (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **`repr(float)` wrote a decimal that does not read back as the value.**
  `shortest_digits` asks Rust's `{:e}` for a digit COUNT and re-renders at that
  width with `{:.*e}`, because a genuine TIE — two spellings that both
  round-trip — is broken to even by CPython and away from zero by Rust. That
  part is right. Rounding the exact decimal expansion to the same width is a
  DIFFERENT operation from choosing between two round-tripping candidates, and
  where they differ it lands on neither: `2**-24` is exactly
  `5.9604644775390625e-08`, whose shortest round-trip is `…063e-08` and whose
  half-even render at 16 digits is `…062e-08`, a different double.
  **46 of 4,239 structured doubles, 0 of ~40,000 uniform random ones** — the
  defect lives where the mantissa is short, which is why no fuzz seed had
  reached it. The re-render is kept only when it parses back to the same bits
  now; 0 disagreements in 118,290, against 123 on the engine this morning.
  Pre-existing, and asserted *exonerated* by the change that fixed `**`.
- **A `%` precision or width past the allocation ceiling answered, then
  aborted.** `'%.2147483648d' % 5` built a two-gigabyte string and printed it at
  exit 0 where CPython raises `ValueError: precision too big`; one digit further
  the process died with `memory allocation of 9223372036854775807 bytes failed`,
  **exit 134** — not the exit-90 contract, and an unexplained death to a caller.
  The whole range `[INT_MAX + 1, usize::MAX]` was unguarded. Both fields refuse
  past the ceiling now.
- **An unbound method ran the wrong type's implementation.** `T.m(x)` is a
  TypeError in CPython when `x` is not a `T`; this dispatched on the shifted
  VALUE, so `int.as_integer_ratio(1.5)` answered `(3, 2)`. Harmless while no
  name lived on two types — `as_integer_ratio` is the first — and the same hole
  the file already documents for `dict.update` on a Counter, reached by a second
  door. The descriptor check closes two PRE-EXISTING instances as well:
  `list.count((1,2,1), 1)` and `str.upper(b'a')`.
- **`(-1).to_bytes(0, 'big', signed=True)` is `b''`**, not an OverflowError: -1
  is all sign bits and extending it into zero bytes loses nothing. The only
  value in −3..3, ±256, ±257, 255 and `i64::MIN` that broke. And CPython checks
  `byteorder` BEFORE the length, so a negative length with a bad byteorder is
  the byteorder's sentence — and with a non-str byteorder a TypeError, a
  different CLASS, which `except ValueError` caught here and not there.
- **How they were found, which is the part worth keeping.** A verification stage
  re-checked each landed mechanism adversarially and rejected three of four. The
  grids that shipped with them were large and clean — 304,722 `%` cells, 326
  numeric-method cells — and every case in them was one a person would write.
  Each defect is at an edge the author had explicitly bounded and dismissed.
- Measured: conformance MATCH 1571, MISMATCH 0, UNSAFE 0. On-policy 509 of
  1,173 (43.4%), MISMATCH 0. Bytes unchanged: 1,130,704 / 9 blocks and
  1,310,928 / 11. pytest: the same 57 as the session baseline.

**2026-09-12** — Six numeric methods: `int.bit_length`, `int.to_bytes`, `int.from_bytes`, `int.as_integer_ratio`, `float.as_integer_ratio`, `float.is_integer`

- **`int` and `float` had no method table at all**, so every one of those names
  was `unsupported: int-method` / `float-method` — a refusal and a CPython
  spawn. `methods::INT_METHODS` and `FLOAT_METHODS` are the two new tables, read
  by the same `find_sorted` every other type uses and probed by
  `route::known_method`, which is the half that makes the walk route the
  programs instead of blocking them.
- **Chosen because every one of them is exact.** A bit count, two base-256
  conversions and a rational read out of the mantissa: nothing here rounds, so
  there is no input where a naive version quietly differs from CPython. That
  rules out the neighbours — `float.hex` and `float.fromhex` stay refusals.
- **`int.as_integer_ratio` is here because `float`'s is.** `route::known_method`
  is a union over probe types and cannot see a receiver, so serving the float
  spelling admits the int spelling to the walk too; leaving it missing would
  have moved `(1).as_integer_ratio()` from a blocker decided before the program
  starts to a refusal reached at runtime, past a committed barrier (#51). It is
  `(n, 1)` at every width, so answering closes the hole for four lines.
  `int.bit_count()` (3.10) and `int.is_integer()` (3.12) still lose their static
  block for the same reason, and still refuse: a version-grown method answered
  here would be a wrong answer on an older reference interpreter.
- **A result past 64 bits is the existing `bigint` refusal, never a wrap.**
  `int.from_bytes` of nine bytes, `(1e300).as_integer_ratio()`'s numerator and
  `(5e-324)`'s denominator all refuse; the core has no wide integer, so the
  refusal is what routes the `from_bytes` half to `lypning-l`, which answers it.
- **Three shapes refuse rather than answer, each for a stated reason.** The
  `byteorder`/`length` DEFAULTS are CPython 3.11's and this engine is graded
  against whatever CPython the caller has, so a call that leans on one is exit
  90. `bool.from_bytes` is a classmethod whose result is a `bool`, not an
  `int`.
- **Three grid tables used these names as the stand-in for "a method nothing on
  the spectrum has"** and are re-pointed at `bit_count`, `conjugate`,
  `numerator` and `denominator`, which are still outside `known_method`:
  `test_bigint_grid.py`'s `REFUSED`, `test_hashlib_grid.py`'s `HIDDEN_BLOCKER`,
  `test_base64_grid.py`'s `ROUTED_PAST_LYPNING_L`. The mechanisms they pin are
  unchanged; the examples stopped being examples.
- 326 shapes — zero, negative, `i64::MIN`, the empty `bytes`, `-0.0`, NaN, both
  infinities, the signed/unsigned and big/little cross-product — diffed against
  CPython 3.11.15 on both binaries: **0 mismatches**, 39 clean refusals on the
  core and 23 on `lypning-l`.
- Bytes: `lypning` 1,114,320 → 1,122,512 (+8,192, **9 blocks either way**);
  `lypning-l` 1,294,544 → 1,298,640 (+4,096, 10 blocks either way). No device
  block crossed. `conformance` over the 3,688-entry corpus loaded on this date
  is unmoved at MATCH 1558 / UNSUPPORTED 945 / MISMATCH 1 (the pre-existing
  `py-ab7286f43b7a` float repr): no corpus program is blocked FIRST on a
  numeric method, and `--plan` has no `int-method` or `float-method` row. This
  feature is paid for by the held-out model set, not by the corpus.

**2026-09-12** — a precision on an integer conversion, and an integer format code on a float · [#59]

- **`'%.2d' % 5` is `'05'`, and it used to refuse.** A precision on an integer
  `%` conversion is a MINIMUM DIGIT COUNT, which the `format()` mini-language
  cannot spell — so the translated spec could not carry it, and the conversion
  left by the refusal contract whenever the precision actually added a digit. It
  is rendered directly now. Three fills in three slots, in CPython's order: the
  precision's zeros go between the `0x` and the digits, the `0` flag widens that
  same run to the field width (`'%05.7d' % -5` is `'-0000005'`, seven digits and
  not five), and the width's spaces go outside everything. The sign is not one
  of the minimum digits, which is why the rendering needs the value.
- **`format(2.0, 'd')` raises CPython's ValueError instead of refusing.** An
  integer presentation type on a value that is not an integer is `Unknown format
  code 'd' for object of type 'float'`, and CPython matches the code against the
  object before it reads anything else in the spec — so `format(0.0, '.2d')`
  names the code and not the precision.
- **The `%` operator keeps refusing there, deliberately.** The two grammars
  disagree: `'%d' % 2.7` is `'2'` — the operator truncates — `'%d' % 1e30`
  truncates into a bignum, and `'%x' % 2.7` is a TypeError with a third
  sentence. None of that is implemented, so only the `format()` side raises.
- Verified by enumerating 71,032 formatting programs against live CPython on
  stdout, exit code **and the exception sentence** — conformance compares only
  the exception type. 30 divergences fell out; all 30 reproduce on the unchanged
  binary and none is in the code this entry changes.
[#59]: https://github.com/kristerhedfors/lypning/pull/59

> **Both of the above were measured alone and each stayed inside its block.**
> Landed together on top of `math` and `type()`, `lypning-l` crosses one:
> 1,294,544 B (10 device blocks) at the start of the day to 1,310,928 B
> (11), which is 208 B past the ten-block line. That is a whole 131,072 B
> block of cold read, bought by four features. `lypning`, the hot path,
> is unchanged at 9 blocks, and `gate.VARIANT_BLOCK_BUDGET` gives
> `lypning-l` 32 — so nothing is over budget, and the cost is recorded
> rather than discovered later.

**2026-09-12** — `type()` of any class the engine can name, and five wrong answers the old refusal was hiding (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **`type(e).__name__` inside an `except` now answers.** It is the commonest
  thing anyone writes about an error they just caught and it refused for every
  exception class, because `type()` answered from nine hardcoded arms and an
  exception was in none of them. It reads `builtins::class_name` now — the
  closed set of every class this engine can name — so the nine became every
  class, all twenty-four exceptions included. Ranked first among the `type` rows
  by `nt refusals --run`: **30 of the 1,173 programs a model wrote**.
- **`json.JSONDecodeError` stopped being spelled `ValueError`.** One
  `Value::Builtin` stood for both, justified on the grounds that `isinstance`
  and `except` cannot tell them apart. True, and not the whole surface:
  `json.JSONDecodeError.__name__` answered `ValueError`, and `is` between them
  answered True in both directions. Three wrong answers at exit 0. It has its
  own `Value::Builtin` now (`builtins::MODULE_EXCEPTIONS`), it is still not in
  the builtin namespace so a bare `JSONDecodeError` is still a NameError, and
  `except ValueError` still catches it because `eval::exc_matches` always knew.
  `repr(ValueError)` is served as a side effect — the pair it could not spell
  is gone.
- **`IOError.__name__` said `IOError` and `IOError is OSError` said False.**
  They are ONE class under three names in CPython, so both answer `OSError` and
  True. One map, `builtins::canonical_class`, read by `__name__`, by `eq` and by
  `is_same` — because a name that decides what `__name__` prints and a name that
  decides what `is` answers are the same name.
- **An exception carries `args`, and this value cannot.** `Value::Exc` is a
  class name and one `Rc<str>`; CPython keeps the objects it was constructed
  from, and `e.args`, `repr(e)` and `str(e)` all read them back. Exactly one
  string survives that round trip, so `ValueError(42)`, `ValueError()`,
  `ValueError('a','b')` and `OSError(2,'x')` refuse at construction now instead
  of answering from a message that had lost the argument — nine wrong answers,
  none of them in the corpus. `KeyError.args` refuses too: that class stores
  `repr(key)` so `str(e)` can be `"'k'"`, which makes the key unrecoverable.
  An exception raised by the ENGINE is untouched, so
  `except ZeroDivisionError as e: e.args` still answers.
- **`__builtins__` refused rather than NameError'd.** It is a global CPython
  injects into every module, so a program reaching for it got
  `NameError: name '__builtins__' is not defined` where CPython gives an
  `AttributeError` — an UNSAFE route of exactly the kind `err.rs` documents,
  and the last MISMATCH in the on-policy census.
- **The two corpus programs that regressed are the reason the rest was found.**
  They pass `type(e).__name__`, so the `type` refusal had been routing them past
  the `args` defect. A refusal covering a defect it is not about covers it only
  until someone lifts it for an unrelated reason.
- Measured: conformance MATCH 1565 → 1568, MISMATCH 0 throughout, UNSAFE 0,
  dispatchers agree 2504/2504. On-policy MATCH **468 → 484 of 1,173 (39.9% →
  41.3%)**, the `type` kind 60 → 25, MISMATCH 1 → 0. Bytes 1,118,416 →
  1,126,608, still **9 device blocks**. pytest: the same 57 failures as the
  session baseline, diffed by name.

**2026-09-12** — `math`, bounded to the functions that have one answer (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **`import math` was the top row of `conformance --plan` on both lists** — the
  corpus's and a fine-tune's held-out set. The module is now served by **every**
  variant, core included, because nothing in it is a capability: the served
  subset is IEEE-754 and integer arithmetic, and a larger sibling would answer
  it identically.
- **What is served:** `pi`, `e`, `tau`, `inf`, `nan`; `floor`, `ceil`, `trunc`
  (an **int**, as Python 3 returns, and an int argument comes back unchanged);
  `fabs`, `sqrt`, `copysign`, `fmod`; `isqrt`, `gcd` (variadic), `factorial`;
  `isfinite`, `isinf`, `isnan`.
- **What is refused, and why it must stay refused:** every transcendental —
  `sin`, `cos`, `tan`, `exp`, `log`, `log2`, `log10`, `pow`, `hypot`, `atan2` —
  is libm's answer, and libm is not correctly rounded, so musl's last ulp is not
  glibc's or Apple's. They refuse at `modules::get_attr`, which the walk reads,
  so the block is static and costs a route rather than a spawn. `fsum` is
  refused too: it is exactly specified and is the obvious next step, but it is a
  second mechanism.
- **Every error path refuses rather than raises**, on `random.rs`'s rule: a
  domain error, a `TypeError` on a non-number, a wrong argument count are all
  message text CPython owns. The three exceptions are the three
  `builtins::float_to_int` already spells exactly — `floor(nan)` a `ValueError`,
  `floor(inf)` an `OverflowError`, `floor(1e300)` the `bigint` refusal.
  `factorial` past 20! and `gcd(-2**63)` raise `bigint` rather than wrap.
- **Verified by enumeration, not by the battery**: 1,017 programs over the
  constants, both signed zeroes, both infinities, NaN, the 64-bit boundary, the
  full 16x16 `fmod` grid and every argument type, diffed against CPython 3.11.15
  — 812 agreed, 205 refused, **0 mismatches**. The `fmod` domain rule refuses on
  exactly the 69 of 256 pairs where CPython raises, and answers the other 187
  bit-for-bit.
- Measured 2026-09-12, x86_64 musl, corpus 3,688 loaded / 2,504 graded, CPython
  3.11.15: `lypning` 1,114,320 -> 1,122,512 B (+8,192; 9 device blocks either
  side). `conformance --engine lypning` MATCH 1,558 -> 1,564, UNSUPPORTED 945 ->
  939. `import math` leaves the build order entirely. MISMATCH unchanged at 1
  (`py-ab7286f43b7a`, `1.79e308 ** 0.5` — the float-power path, not this
  change). Routing safety UNSAFE unchanged at 1, the same entry; WASTED 93 ->
  99, the six programs whose refusal moved from the static import blocker to a
  runtime one.
- **One test went red and it was the test that was wrong.** `test_a_construct_the_runtime_table_would_escalate_is_kept_off_the_tier_statically` asserted these programs route straight to CPython because a NaN literal is visible in the source. No such static marker exists — the `MICROPYTHON_UNSAFE` markers were deleted with the tier they served, which is why this test's own sibling is already retired two lines below it. It passed only because `import math` was an unserved module and the module blocker stood in for the marker. Rewritten to assert what is actually guaranteed: tier 1 refuses at run time, nothing reaches stdout first, and the answer matches CPython — from CPython for `nan-identity`, from `lypning-l` for `int-div-precision`, which left `ONLY_CPYTHON_REFUSALS` when `cap-bigint` landed. WASTED, not UNSAFE.

**2026-09-12** — The standing MISMATCH closed: `**` on floats now answers what the reference libm answers (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **`1.7976931348623157e308 ** 0.5` answered `1.3407807929942597e+154` where
  CPython answers `…596`.** One ulp, exit 0, nothing on stderr — the one number
  invariant 1 says is never traded, standing in the corpus as `py-ab7286f43b7a`
  and waived in `.github/known-mismatches.json` as unfixable. It was fixable.
- **It was never float formatting.** `fmt::float_repr` is shortest-round-trip
  and prints both neighbours correctly; the wrong bits came out of the multiply.
- **musl's `pow` and glibc's `pow` are the same routine compiled twice.** Both
  are Arm's optimized-routines double-precision power. Re-running the reference
  under `GLIBC_TUNABLES=glibc.cpu.hwcaps=-FMA,-AVX2` made its answers
  bit-identical to musl's over 18,000 pairs — 16 disagreements to 0. The whole
  gap is fused multiply-add.
- **New `pow.rs`: that routine with the fusions put back.** Both kinds — the
  `#if __FP_FAST_FMA` arms and the `a*b + c` contractions the C compiler makes
  on its own, which Rust never makes. Taking only the first still reproduced
  musl exactly; that is why the second is written out call by call.
- **Verified by enumeration, not by argument.** 6,302,592 `(x, y)` pairs across
  the overflow edge, the subnormal band, either side of the 2^-65 and 2^63
  cutoffs, bases within forty ulp of 1.0, subnormal bases, negative bases at
  integer powers and the full IEEE-special cross product: **0 disagreements**
  against the host libm, and 400,000 of them re-checked end to end through the
  binary. An earlier version was wrong 30 times in 3,002,592 — all in one
  function, all from fusing a product the C compiler reads twice.
- **Measured 2026-09-12 on this container.** Corpus 3688 loaded, 2504 graded:
  MATCH 1558 → 1559, UNSUPPORTED 945 unchanged, **MISMATCH 1 → 0**, coverage
  62.2% → 62.3%. Binary 1,114,320 → 1,118,416 B, **9 device blocks either
  side** — +4,096 B of the 65,328 B that were left before a tenth block.
  `fuzz` 0 counterexamples, `doctor` 0 FAIL, pytest failures identical before
  and after (189, none of them float-shaped). `gate` FAILs on 9 blocks against
  an 8-block budget before and after: that is the absent oracle, not this.
- **The cost is stated, not hidden.** `mul_add` is a call into musl's software
  `fma` on a baseline x86-64 build, so `**` on floats goes ~22 ns → ~277 ns.
  It is in single digits of corpus programs and in no `perf` row. Building
  with `-C target-feature=+fma` would take it back and is a separate step,
  because it raises the CPU floor for the whole binary.
- **Re-measured independently before landing**, on a different 52,000-pair corpus with its own seed: **22 disagreements without `pow.rs`, 0 with it**. That also clears the 44 adjacent-duplicate rows in the transcribed `LOG_TAB` — an `invc` rounded onto a coarse grid repeats, and the sample crosses every subinterval a few hundred times.

**2026-09-12** — Two silent wrong answers, found by running the engine over what a language model writes (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **A `try` with no `except` and no `finally` ran its body and exited 0.** CPython's
  grammar has no such statement — `SyntaxError: expected 'except' or 'finally'
  block` — and neither does one with an `else` but no `except`. The parser
  accepted both. It is the quiet half of a wrong answer: a program that prints
  nothing and exits 0 is indistinguishable from one that worked.
- **`n is n` over a NaN answered `False`.** `ops::identity` refuses `is` between
  two equal immutables because CPython decides that by interning — and a NaN is
  the one value that never reaches the guard, because it is the one value not
  equal to itself. It fell through to `false`, where CPython answers `True` for
  any object compared with itself. A float carries no `Rc`, so nothing in the
  engine can tell one NaN object from two; it refuses as `nan-identity` now,
  narrowed to the NaN so `1.5 is None` still answers.
- **Neither shape is in the corpus, and neither ever would be.** No human types a
  handler-less `try`; a generation cut off by a token cap types one every time.
  The NaN arrived from a model routing *around* the existing refusal — `n in l`
  over a NaN was already `nan-identity`, so the model rewrote the same question
  as `any(x is n for x in l)` and went straight through. A refusal that can be
  reworded into a wrong answer is not a guard.
- **The instrument was the 1,173 programs Qwen3.8-27B wrote** in
  `training/runs/qwen38-baseline-k16`, each run against lypning and CPython:
  462 MATCH, 709 UNSUPPORTED, **2 MISMATCH** — these two. `conformance` grades
  the corpus, not the language, and the corpus's blind spots are shaped like its
  capture mechanism.
- **Zero byte cost** (1,114,320 B, 9 device blocks — unchanged), conformance
  MATCH 1558 / UNSUPPORTED 945 unchanged, and the Python suite has the same 57
  failures before and after, diffed by name rather than by count.
- **`nt refusals`** ranks what the engine still refuses by how many corpus cases
  each kind blocks, and marks which are not a backlog: 139 cases blocked by open
  kinds, 39 by kinds in `ONLY_CPYTHON_REFUSALS`, where the engine's job is to go
  on refusing.

**2026-09-11** — The measurement pipeline audited: 52 findings, 33 survived, and the corpus does not need to be bigger (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **A green suite measures the cases someone thought of.** Six independent lenses
  over `training/pipeline/`, every finding then handed to two refute-by-default
  skeptics: 52 raised, **33 survived**, 19 refuted, every survivor confirmed by
  running a repro. The 62 passing tests caught none of them. Written up in
  `training/AUDIT.md`.
- **`-E` silently voided the harness's own determinism.** `run_python` spawned
  CPython with `-E`, which ignores every `PYTHON*` variable — including the
  `PYTHONHASHSEED=0` set nine lines earlier against exactly this. Found
  independently by three lenses. One case, one stored program, twelve identical
  runs: `[T,F,F,T,F,F,F,T,T,T,F,T]`. Set ordering was a coin flip on every
  reference capture, every gate run and every graded attempt, so some cases were
  frozen to an expect_stdout no correct program can reproduce.
- **An engine MISMATCH was scored as a model failure** — invariant 1 inverted, in
  the metric. Three attempts in `headroom-k16` are the engine bug
  `NameError: __file__` filed as `wrong-output`. A tune that tripped a *new*
  mismatch would have read as a worse model.
- **The eval path had no degenerate-solution guard.** `print(<expected output>)`
  was a full pass; one of the 26 passing baseline attempts is exactly that. The
  sampler's guard was never wired into grading — and its own
  `_LITERAL_MIN_CHARS = 12` plus a shortest-wins selector made it a cheat-seeking
  selector for short outputs: for one case, seven passing programs were drawn
  including an 805-char genuine implementation, and the two chosen as training
  targets were both the hardcoded literal.
- **Corpus recovery was measured and recommended against, five times out of five.**
  FileNotFoundError 1201 entries → **7** cases; no-stdout 205 → 3; json-decode 111
  → **0**; IndexError 62 → 7; import/syntax 78 → 57 but from three captures in one
  session. The prior estimate of a few hundred recoverable cases was wrong by an
  order of magnitude.
- **The critic found what the lenses could not.** The SFT set is drawn with
  thinking off, so a tune will have near-zero truncation by construction — while
  24 of the published baseline's 74 cases scored zero on budget exhaustion, worth
  up to **+6.8pp of free movement** toward the win bar with no capability gain.
  And arm identity is a free-text string: both shipped runs record model
  `nemotron` and were different endpoints.
- **The instrument's null floor, measured.** Same checkpoint, same prompt, same
  holdout, thinking on vs off: both 26/74, `gained 6 / lost 6`. Twelve of
  seventy-four cases flip when nothing changed.

**2026-09-11** — A baseline for the thing the project is actually about: 35.1% pass@1, and a rewrite slice at 17.3% (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **The corpus is the refusals.** `nt classify` put all **3688** corpus entries
  (run of 2026-09-11) through CPython and both engines: 441 already tier-1,
  1186 skipped by the repository's own rules, 1753 with no clean CPython answer
  to compare against, and **308 refused** — the failing population. Minus the 45
  that drive lypning itself, **249 cases**, split **175 train / 74 held-out**,
  stratified over 30 refusal kinds.
- **Baseline, stock `NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16`**, served on
  vLLM 0.27.1 on one A100 80GB, thinking on, 12,288-token budget, temp 1.0 /
  top_p 0.95 (run `baseline-nemotron35-bf16-t12k`, 2026-09-11): **pass@1 35.1%,
  95% CI [24.3%, 45.9%]** over 74 held-out cases.
- **The blend hides the headroom, so `nt slices` was added.** Rewrite cases
  **17.3%** [7.7, 28.8] over 52; ceiling controls 64.3% over 14; the old 26-task
  study bank 100% over 8 — saturated, and evidence that bank cannot measure a
  training effect.
- **The first baseline attempt measured its own token cap.** At 4096 tokens,
  41 of 74 attempts returned no program and *every one* of them stopped at
  exactly 4096 with `finish_reason: length`. Raising the budget to 12,288 moved
  pass@1 28.4% → 35.1% and no-code 41 → 24. The remaining 24 are still at the
  cap: on a third of these cases the model does not stop reasoning.
- **Reasoning bought nothing here and cost 23x.** Thinking off scored the same
  35.1% on 22,254 output tokens in 61 s, against 505,576 tokens in 17m21s with
  it on. The two arms pass 26 cases each and agree on only 20, so they are
  indistinguishable at this n — but they are not interchangeable per slice:
  thinking helps the rewrite cases (17.3% vs 9.6%) and its lower ceiling score
  is the truncation above, not fabrication.
- A `lypning` test kind grades correctness **first** and routing second, so a
  program that stays in the subset by guessing scores zero. Ceiling cases carry
  the rule as a control. An engine that runs a program and disagrees with
  CPython is `engine-mismatch`, never a model failure.

**2026-09-11** — `training/`: a corpus that drops what it cannot test, and a held-out split that is frozen by a file rather than by intention (branch `claude/nemotron-lora-pipeline-1zczi2`)

- **Steps 1 and 2 of a LoRA pipeline** for
  `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16`, in a new top-level
  `training/` that imports nothing from `lypning` and adds no dependency to it.
  Steps 3 (training) and 4 (sweep) are deliberately not started: no training
  config is proposed before there is a baseline to beat.
- **A case is kept only if its test runs and discriminates.** Four gates, and a
  drop is written with the gate that rejected it rather than patched: the
  reference solution must pass, the empty program must fail, the *recorded
  failing generation* must fail, and the verdict must repeat. The third gate is
  the one that pays for itself — the failure that put a case in the corpus is a
  negative control you already have, and a test it passes is measuring something
  else.
- **Frozen means checkable.** `holdout.lock.json` pins every held-out case by
  SHA-256 with a manifest hash over the list; `nt verify` recomputes both, step 2
  refuses to measure a drifted split, and `nt results` marks a run whose prompt
  or manifest differs from the baseline's `n/c` instead of subtracting two
  different measurements. Growth after the freeze lands in train only.
- **A harness error is not a failed program.** A 500 from the server, a timeout
  talking to it, a sandbox fault — excluded from the denominator and counted
  separately, so a flaky endpoint cannot read as a worse model.
- **The Wilson interval beside the bootstrap is not decoration.** At small n the
  percentile bootstrap degenerates; with every case passing it reports a
  zero-width interval, which is false. The summary carries both and flags their
  disagreement.
- The sandbox is the same posture as invariant 4 — own temp cwd, scrubbed
  environment (`HF_TOKEN` included), process-*group* kill on timeout, CPU,
  address-space and file-size rlimits, and an empty network namespace via
  `unshare -n` where the kernel allows it. Still a net, not a sandbox: an
  absolute path still escapes it.
- Stdlib only, `>=3.9`, `from __future__ import annotations` throughout, so the
  same code gives the same verdict on a laptop, in CI and in the GPU container.
  54 tests under `training/tests`.

**2026-09-07** — Round 81: `repr()` of a type at 46 programs per KB, a `zip()` that never returned, and 224 builtin arity divergences

- **`repr()` of a type**, the row the corrected grader put on the board: it was
  0 until round 80 found the battery counting 23 refusals per arm as MATCHes,
  and it landed at rank 6. `lypning-l` 79.2% → **79.8%** for **+304 B** of
  `__text` — **46 programs per KB**, eleven times the densest row previously
  landed (`pathlib` 4.08) and ~50x the ~11 KiB floor round 80 established.
  All fifteen programs are one shape, `print(type(d))` after `json.load`.
- Two traps, both settled by running CPython rather than recalling it. The table
  is **not `tp_name`**: `type.__repr__` prints `<class '{__module__}.{__qualname__}'>`
  with `builtins` elided, so `repr(Counter)` is `<class 'collections.Counter'>`
  where its `tp_name` is bare `Counter`. And a value the engine cannot
  distinguish must REFUSE: `modules.rs` answers one `Value::Builtin` for both
  `pathlib.Path`/`PosixPath` and for `ValueError`/`json.JSONDecodeError`, so
  both refuse. The control that keeps this measured rather than superstitious:
  `IOError is OSError` really is one class, so `repr(IOError)` is served.
- An adversary found a third instance the author missed — `type(os.environ)`
  answered `<class 'dict'>` because `modules.rs` models it as a bare
  `Value::Dict`. Fixed by tagging the mapping where it is built, which also
  closed three more exit-0 wrong answers a docstring had recorded as unfixable:
  `print(os.environ)`, `isinstance(os.environ, dict)` and
  `json.dumps(os.environ)`.
- **`zip()` with zero iterables never terminated** — `Iter::Zip` built its output
  by looping over the iterables, so with none it never signalled exhaustion and
  yielded `()` forever. A hang gives the chain no exit code to retry on. Fixed
  at the root, so `zip(*[])` is covered too. Pre-existing; 0 corpus occurrences,
  fixed anyway.
- **`bytes(-1)`** silently returned empty where CPython raises
  `ValueError: negative count`.
- **Builtin arity: 224 divergences → 0.** All 39 names in `builtins::BUILTINS`
  swept against CPython 3.14.5 over 0–4 arguments. Nine answered at exit 0 where
  CPython raises; ~20 more raised with the wrong text. `arity()` now carries a
  wording column, because CPython spells the same `(1,1)` three different ways
  and a right count with wrong text still disagrees on graded stderr.
- **A sixth defect the fix itself uncovered:** one added byte in `Dict` turned
  `f(179)` — the deepest recursion `MAX_DEPTH` admits — into a killed process
  where the guard is supposed to produce a refusal. `eval`'s match held two
  `Dict`s and a `Set` **by value** on the frame of every expression evaluated.
  Stack headroom went from **0.5% to ~12%**; that guard had been one byte from
  failing for a long time.
- `lypning-l` crosses into **device block 9** (1,050,208 B of a 32-block budget);
  the frozen core stays at 7 of its 8. MISMATCH 0, UNSAFE 0, monotone 0,
  dispatchers agree 2504/2504.
- Also landed: `file-write-read` addressed by reordering `BUILTINS` by measured
  corpus frequency instead of alphabetically — **zero bytes**, `__text`
  identical. Profiling disagreed with iteration 68's callgrind reading again.

**2026-09-07** — Round 80: the grader could not see most disagreements, and `MISMATCH 0` was not true

- **Four confirmed blind spots in `lypning conformance`**, each with a
  reproduction and three proved end-to-end with compiled mutant engines run
  through the real `conformance.run()`:
  - **stderr was never compared, in either direction.** An engine keeping
    stdout and exit code but replacing its entire stderr with an invented
    `RuntimeError` kept **1,571 of 1,571 MATCHes**; one appending
    `lypning: WARNING: results may be wrong` to every run was invisible.
  - **75% of MATCHes compared nothing but an exit code** — 1,185 of 1,571 had
    empty stdout on both arms. 800 were `FileNotFoundError` on both sides for a
    RELATIVE path (absolute ones are skipped, so the skip rule is exactly what
    left this class in); 380 read stdin with no sample, so both hit instant EOF.
  - **The nondeterminism waiver was decided on raw program text**, so a name in
    a string literal or a comment bought it — a mutant printing `WRONG ANSWER`
    scored MATCH 6 / `ok=True` on six entries whose CPython output is `12`.
  - **A program could forge a refusal** and be counted as coverage.
- All four closed. `classify` compares the exception TYPE and the fact of
  raising (never the message wording — that drifts across 3.11/3.12/3.14 and is
  why stderr was left alone); the waiver reads the AST with a text fallback for
  the 47 programs that do not parse; the sandbox is seeded with the relative
  files an entry reads, but only after an empty run dies of `FileNotFoundError`,
  so a program written to test an absent file keeps its branch; and CPython
  exiting 90 is the program's choice, not a refusal.
- **The honest numbers.** `lypning` 62.7% → **61.7%**, `lypning-l` 80.2% →
  **79.2%**, and `MISMATCH 0` was **not true** — it was 1 per Rust arm. The
  report now prints what each MATCH compared: for `lypning`, 487 stdout, 898
  stderr, **172 on an exit code alone** where under the old grader all 1,060
  MATCHes with empty stdout rested on one.
- **The blind spot corrupted the roadmap, not just the verdict.** 23 refusals
  per arm had been counted as MATCH, creating a `--plan` row that did not exist:
  `repr: repr() of a type` 0 → **15**, now rank 6. `import re` 237 → 243,
  `import collections` 42 → 45.
- The MISMATCH it exposed: CPython's tokenizer reads a logical line's
  indentation before lexing it, so an unexpected indent hides everything after;
  this lexer tokenized the whole source first and a later problem won. Fixed as
  a **refusal**, not an imitation — `python -c` DEDENTS its command from 3.13
  on, so ` print(1)` prints `1` on 3.14.5 and raises `IndentationError` on
  3.11.15, and a static binary cannot know which CPython the chain will reach.
  That also closed a latent unsafe route: three shapes CPython 3.14 runs where
  the engine answered `SyntaxError` at **exit 1**, which the chain cannot retry.
- `call-recursive` **−5.6%** with disjoint bands under the corrected protocol.
- **`textwrap` measured 0.54 programs/KB and was reverted**, establishing the
  floor this loop needed: **~11 KiB of `__text` is the cheapest module
  capability shape**, so a row needs ≥10 corpus programs to be worth building.
  `datetime` was not built — 9 blocked entries are 7 once the ambient `now()`
  ones are discounted.
- Band audit: only iteration 23 used the recipe iteration 79 found broken, but
  every other quoted band is worse — most sample two fixed binaries and zero
  build variation, and most accepted rows quote no band at all. Re-measured
  under the corrected protocol: iteration 41 STANDS, iteration 64 stands on
  `str-fmt-pct` but is INSIDE THE BAND on `str-of-scalar`, both round-79 wins
  STAND.

**2026-09-07** — Round 79: the method table stops calling `memcmp`, the barrier can be taken back, and the grader stops hiding disagreements

- **`lypning perf`, seven rows outside their band**: `str-methods` −20.9%,
  `str-of-scalar` −19.8%, `str-slice` −11.1%, `str-split` −10.9%,
  `call-method` −10.2%, `str-scan` −10.0%, `list-append` −5.1%.
  `methods::method_name` binary-searched with `<str as Ord>::cmp`, which on
  macOS arm64 is a branch through a dyld stub into `libsystem_platform.dylib` —
  20% of a method-call loop by sampling. It is a spelled-out search over an
  inlined byte comparator now. Two controls that reach no static table
  (`name-lookup`, `dict-get`) stayed inside their bands, and every moved row
  moved in proportion to its method calls per iteration.
  **This overturns the ledger's standing answer**: allocation was already dead
  on that path (119 allocations for both a 1,000- and a 2,000-iteration loop).
  Iteration 4's "a scan is not worth shortening" was measured on musl x86_64,
  where `memcmp` is a leaf call in the same image.
- **The perturbation recipe in `.claude/skills/hillclimb/SKILL.md` was a no-op.**
  Four rebuilds with a comment appended to `json.rs` are byte-identical by
  sha256 — a comment cannot reach codegen — so the recipe timed one binary four
  times and called it a build band. Two agents reproduced it independently. The
  skill now uses a never-taken `argv` early return at three string lengths, and
  says every band quoted before today was measured the broken way.
- **Issue #51, the barrier, is fixed**: `os.mkdir` no longer forces
  `mark_committed`, because the barrier keeps an undo log. Staging was measured
  and rejected — `glob.rs`'s `real_dir` is `canonicalize`, which fails for a
  directory not on disk. The safety property is re-proved: a program never runs
  its side effects twice.
- **Issue #57: `conformance` graded differently depending on whether
  `PYTHONPATH` was absolute.** Each entry runs in its own temp cwd, so a
  relative path broke the *reference* CPython the same way it broke the engine,
  and the grader read that as agreement. `engines.child_env` resolves every
  path-like variable now. **The blind spot was 88 entries wide** — all dying
  with `ModuleNotFoundError` — though only one changed grade, because the other
  87 were already refused. `MISMATCH 0` on this host had been an artefact.
- The defect it hid was an **invariant 2 violation**: `lambda *a, **k` gave a
  traceback at exit 1 where the contract requires a clean exit-90 refusal.
  Fixed by SERVING it — `def` and `lambda` are one grammar under two
  terminators, so the change **deletes a reader** rather than adding one.
- `doctor`'s `core/library agreement` compared the **1 MB core** against a
  library compiled from the largest variant, so every capability the core lacks
  read as drift. It probes `SPECTRUM[-1]` now.
- `lypning` 62.7% / `lypning-l` 80.2%, MISMATCH 0 under both spellings, UNSAFE 0,
  monotone 0, dispatchers agree 2504/2504, doctor 0 FAIL, gate PASS, 6,760 tests
  passing. Core 7 of its 8 blocks.

**2026-09-07** — `cap-hashlib` and `cap-base64` on lypning-l: two capabilities, one method hatch · [#56]

- `lypning-l` MATCH 1992 → **2009**, coverage 79.6% → **80.2%**. MISMATCH 0,
  UNSAFE 0, monotone 0, dispatchers agree 2504/2504. Frozen core 7 of its 8
  blocks; `lypning-l` +21,000 B code, 8 of 32.
- `hashlib` was rejected at iteration 74 because serving a module admits
  programs whose OTHER constructs the variant lacks, which then die at exit 1
  with partial stdout. The exit-1 count over newly-routed programs was the gate
  this time, and it is **zero**.
- Chasing one of its wrong answers found a shared idiom, not a hashlib bug:
  across **16 call sites**, `args.get(i).cloned().or_else(|| kwget(&kw, "n"))`
  — and `Option::or_else` does not run when the first option is `Some`, so a
  positional plus the same parameter by keyword silently dropped the keyword
  and answered at exit 0 where CPython raises. All 16 go through
  `crate::args::bind` now, and `tests/test_keyword_grid.py` scans the source so
  it cannot come back.
- `base64` serves four names over `Value::Bytes`, no new variant. Its decode
  rule was derived from 150,000 differential rows: pads are counted over the
  whole input, not per quad. Three of the twelve corpus programs are agent-typed
  harnesses against MicroPython's `modbinascii.c`, which counts per quad and
  stops at the first complete one — wrong in both directions, and exactly what a
  reimplementation reaches for.
- The two branches narrowed the same `method` routing hatch in **opposite
  polarity, different slots, different variant sets**, and did not collide
  textually. Semantically base64's default-to-stop would have left `cap-hashlib`
  dead. Resolved by giving `CAP_METHODS` a `hashlib` row read through
  `hashlib::known_method`, so the core's table and lypning-l's walk are one list
  by construction. The row is deliberately smaller than the module's surface —
  `cap_method` matches by name and cannot see the receiver, so `name` would
  admit `print(open(p).name)` into lypning-l for an exit-1 AttributeError;
  `hashlib::ROUTER_WITHHELD` records the two withheld names and why.

**2026-09-06** — the performance case moves to the landing page, measured against a PyPy arm · [#53]

- README §1 carried no numbers and closed by saying nothing had been re-measured
  on `lypning → lypning-l → cpython`. It now carries the table, and the chain has
  been re-measured — with a **fifth arm**, PyPy, which is not an engine and is
  never routed to. `--arm` is name-only and silently drops a path, so the arm
  goes in through the `bench.Arm` pass-through and the resolved arm list is
  asserted before a number is read. First table to carry both Rust variants.
- The headline is the dispatcher over the **whole** corpus with fallback
  charged, not the subset a refusing variant selects for itself: all 2,504
  programs answered for **0.523x** of CPython's wall. Per-program figures are a
  geometric mean over the 532 programs every arm ran to exit 0 — a sum is
  weighted by the corpus's slowest program.
- The compute crossing `docs/PAPER.md` records as never swept is now measured:
  under ten thousand operations the Rust variants win by 3–5x, past a million a
  tracing JIT wins by 11–14x and they lose by 1.4–1.6x.
- The outcome census `bench.render` does not print: a non-zero exit counts as
  `RAN`, so a fast failure hides inside a speed total. `pypy3` fails 1,531
  programs against `cpython`'s 1,530 — it fails where CPython fails, plus one.
- One claim withdrawn rather than published: PyPy's startup cell does not
  reproduce (0.890x, 0.890x, 0.907x, **1.008x** in four min-of-15 samples the
  same day), so the ledger records no stable winner. The corpus rows stay `min`
  and the compute ladder uses the mean, because the one-sided-noise argument
  that justifies `min` is documented false for a tracing JIT.
- These Rust binaries are **host builds, not static musl** — no `rustup` here —
  so the startup rows understate them and are marked not comparable with the
  musl rows in the ledger. Corpus 3,688 loaded / 2,504 measured, shared subset
  1,572, on a shared box at load 1.2–3.3; ratios are the reading, milliseconds
  are not.
**2026-09-06** — Round 78 infrastructure: an honest size column, a grader that compares bytes, a router that answers for the spectrum, and a ledger that records real sessions · [#54]

- `build`, `status` and `gate` now report the **code section** beside file
  bytes (`gate.text_bytes`: `size -m` on Mach-O, the ELF headers parsed with
  `struct` elsewhere; unmeasured where neither is readable, never a silent
  fallback to file size). This matters because file size is page padding —
  `lypning` 867,856 B carries 675,580 B of code — and it had been the
  denominator for every density figure in `docs/HILLCLIMB.md`.
- **Issue #50**: every output comparison in `engines`, `conformance`, `fuzz`,
  `perf`, `build` and `doctor` compares **bytes**. The grader had been blind to
  line endings, because both arms were captured with `text=True` and universal
  newline translation rewrote both before comparison. Answer to the question
  that motivated it: no currently-green row was green only because of the
  translation, and none turned red. The axis had no live corpus witness because
  `csv.writer(sys.stdout)` is still refused; it has unit witnesses now.
- Found while doing that: `engines._run_via_pool` read a free variable `env`
  and raised `NameError` on every call, so the **warm CPython pool has been
  dead since #19** whenever `$LYPNING_POOL` was set. `tests/test_pool.py` held
  `pool.Server` to its contract directly and never entered through that door.
- **Issue #48**: the `re` pattern parser is lifted into `src/repat.rs`, carried
  by every variant, so the core computes the whole spectrum's verdict instead of
  routing a program to a sibling that will refuse it. `import re, os;
  os.makedirs("d"); print(re.findall(b"a", b"aa"))` was exit 1 after the barrier
  committed; it now answers at exit 0. WASTED 99 → **91**; LATE 36 → **47** and
  `lypning-l` MATCH 1978 → 1977, which is the honest cost: those programs ran on
  a variant its own router already refused, and got lucky because the unservable
  pattern sat on a branch never taken.
- **The route ledger records real sessions.** `main.rs::dispatch` is a second
  writer, on the same condition and in the same format as `engines.dispatch`
  (verified byte-compatible: records from both fold by id). `lypning routes` had
  said "no routes learned yet" since it was built, because every real session
  goes through the Rust binary. Still write-only with respect to routing.
- MISMATCH 0, UNSAFE 0, monotone 0, dispatchers agree 2504/2504, doctor 0 FAIL,
  gate PASS. Core 7 of its 8 blocks carrying both new modules.