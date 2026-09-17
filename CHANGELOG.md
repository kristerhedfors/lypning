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

**2026-09-17** — Stop the S0 handoff from telling the private device to rebuild the population it was pinned to read ([#92](https://github.com/kristerhedfors/lypning/pull/92))

- The next training round was attempted on a third clone holding none of the
  round's four inputs. It was reported blocked and no rung was run, which is the
  assignment's own instruction. What the clone *could* do was read the
  assignment against the tree it will run in, and it does not survive that read.
- **`START_NEXT_ROUND.md` contradicted its own stop rule.** :21-22 stops on an
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
- `STATUS.md`'s account of `levers --run` is corrected: it describes the bare
  form, and the `--run --population-rows` form §10's S0b row assigns does not
  re-derive the population.
- The change was then adversarially reviewed in turn, which found no blocking
  defect and two high ones, both introduced by the change itself: the
  replacement rebuild command could not run as written — the positional is a run
  id, not a path, and `--output` is required — and the review listed as
  still-open a `STATUS.md` defect the same change fixed. Both are fixed, along
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
||||||| 0e76749

**2026-09-06** — `cap-glob` on lypning-l: the order question is answered by the walker, not by a value · [#52]

- `glob` was rejected at iteration 76 for seven holes, five from one decision —
  a `Value::Glob` carrying an order-taint, unwired from `set_item`, `del_item`,
  slice assignment and `AugAssign`'s in-place-extend arm. This rebuild has no
  variant: `glob.glob()` returns a plain `Value::List` and the order rule is a
  **static** blocker in `route.rs`, admitting a glob call only inside
  `sorted()`, `len()`, `bool()`, `min()`, `max()`, `any()`, `all()`, `sum()` or
  the right of `in`. Three adversarial rounds, ~7,000 differential runs, found
  no way to observe filesystem order through an admitted position.
- Two rules that were load-bearing and unwritten are now in the code, each
  having been a hole: a name belongs in `ORDER_BLIND` only if its **result**
  carries no order, not merely if its **answer** is order-blind (`set` failed
  this and is dropped); and a position is safe for `iglob` only if it
  **consumes** its argument (`bool` and `len` ask about the container, so a
  generator and a list differ — `bool(glob.iglob("zzz*"))` was a wrong answer).
- Every refusal an admitted call can raise is hoisted into the walk —
  keyword rules, the pattern scan, the served-attribute table — unconditionally,
  because the core is the binary `engines.route()` asks. WASTED 115 → 99. A
  79-row barrier sweep asserts exit 90, one refusal line, empty stdout and an
  unchanged cwd for every refusal shape, measured by a before/after snapshot.
- Two fixes to shared code found while chasing this: the binding table let a
  `def` parameter escape outward to shadow a module-level literal **and** a
  function-local literal escape outward to answer for a name it never held, and
  `for`/`AugAssign`/comprehensions walked the target before the value that binds
  it. And `sorted()`'s O(n²) tie scan: 60,000 keys, **28.09 s → 0.05 s**.
- `lypning-l` MATCH 1967 → **1978**, coverage 78.6% → **79.0%**, MISMATCH 0,
  UNSAFE 0, monotone 0, dispatchers agree 2504/2504, pytest 4,402 passed.
  **Density 0.89 programs/KB on `size -m __text`** — the honest denominator;
  file bytes are page-aligned and measure nothing.
- Residue, filed as #51: a runtime-built pattern and the `**` depth cap cannot
  be hoisted, and `os.mkdir` commits the barrier immediately, so any later
  refusal is exit 1 — `eval`, `complex` and `__import__` included.

**2026-09-06** — `cap-csv` on lypning-l: serve the readers lazily, refuse the writers, add no Value variant · [#49]

- `csv` was held at iteration 74 because a new `Value::CsvWriter` was wired into
  `type_name`, `methods`, `ops` and `fmt` but not into `value::eq`, `is_same` or
  hash. This rebuild adds **no new `Value` variant**: the reader is
  `Value::IterObj(Iter::Csv(..))`, an adapter over an existing lazy `Iter` in
  the shape `Iter::Filter` and `re.finditer` already use.
- A corpus mine (2026-09-06, 3,688 loaded) counts `csv.reader` 16, `csv.writer`
  8, `csv.DictReader` 5, `csv.DictWriter` 2 over the 23 blocked programs, so the
  readers are served and the writers are static `module-attr` refusals — the
  writers are the only part that needs an object with methods.
- **The reader is lazy.** The first cut read the stream eagerly at construction
  and every other path to it then needed a guard; two adversarial rounds found
  14 defects, five of them guard gaps. Making the reader pull through the same
  iterator `for line in f` drives left five guards with nothing to guard, and
  they were deleted. A reader outliving its file now raises CPython's verbatim
  `ValueError: I/O operation on closed file.`
- `lypning-l` MATCH 1950 → **1967**, coverage 77.9% → **78.6%**, MISMATCH 0,
  UNSAFE 0, monotone 0, dispatchers agree 2504/2504, routing unchanged
  (IDEAL 2372, WASTED 96, LATE 36). The frozen core is byte-count identical at
  834,672 B with no csv text in it; `lypning-l` 934,096 → 950,656 B, 8 of 32
  blocks. **Density 1.05 programs/KiB.**
- Filed #50: `conformance` cannot see a newline difference at all, because
  `engines.run_engine` captures both arms with `text=True` and universal-newline
  translation rewrites both before they are compared.

**2026-09-05** — `re` matcher round: lypning-l runs the regexes the corpus writes; `glob` and `class` rejected · [#47]

- `cap-re` step 2. `lypning-l` MATCH 1910 → **1950**, coverage 76.3% → **77.9%**
  (`lypning conformance --mixture both`, 2026-09-05, 2,504 graded, MISMATCH 0,
  UNSAFE 0, monotone 0, dispatchers agree 2504/2504). Both `re` rows leave
  `--plan`; the only one left is 4 programs wanting Unicode tables.
- `lypning-l` 867,840 → **934,096 B**, the first capability to cross into device
  block 8 (which begins at 917,504 B), 8 of a 32-block budget. The frozen core
  is unchanged at 834,672 B and carries no `re` text.
- **Density 0.62 programs/KiB by the battery, 1.44 by corpus intent**, and 0.62
  is below the 0.87 that got `hashlib` rejected. Landed anyway because the
  under-count is measured, not assumed: of the 213 admissible `re` programs,
  174 die identically on both engines before their first regex call in the
  battery's temp cwd, so no matcher can move them.
- A capturing group ending in a lazy quantifier inside a bounded `{m,n}` with
  m ≥ 1 captured the wrong iteration — the span was right, so `group(1)`
  returned a different string at exit 0. Cause: `Op::Until` armed the
  zero-width guard for every iterate kind, where `sre` arms it only in the
  branch that pushes and pops it. The first diagnosis blamed a different line
  and was disproved by experiment before anything was changed.
- Verified by 114,428 differential rows against CPython 3.14.5 — a quantifier
  cross-product over 12 subjects rendered 12 ways, then deeper nesting over 14
  — with zero wrong answers, zero hangs, zero contract violations.
- **`glob` rejected at 0.99 programs/KB** and **`class` at 0.19**, both built,
  measured and attacked; branches `cap-glob` and `cap-class` are on the remote
  unmerged. Reasons in `docs/HILLCLIMB.md` iteration 76.
- Filed #48: a static blocker only the larger variant can compute is inert when
  the chain enters at the core, because `exec_engine` invokes the next rung as
  `<bin> -c`.

**2026-09-05** — The unbuilt-oracle line no longer promises a catalogue a wheel does not ship

- `.github/known-mismatches.json` is deliberately not a package asset, so a
  wheel has no oracle catalogue. `lypning status` and `lypning doctor` told the
  reader that `lypning oracle` "reads the recorded divergences either way",
  which sends them to a command that answers "no catalogue" — a hole reported
  as a capability. Both lines are now conditional on the catalogue being there.
- Found by running the wheel shape end to end (CLAUDE.md's "two shapes that
  must both keep working"): `pip install --no-build-isolation .` into a venv,
  then `LYPNING_HOME=<tmp> lypning build --rust && lypning status`. Everything
  else held — both variants built at the same bytes as the source checkout
  (834,672 B and 867,840 B), `lypning oracle` reported its hole, the
  `lypning hook pre-tool-use` CLI entry point answered
  `{"continue":true,"suppressOutput":true}` at exit 0, and `install --dry-run`
  printed its diff and wrote 0 files.

**2026-09-05** — lypning-l serves the `re` surface; matcher calls route statically to CPython · [49c8aa2]

- `import re` binds a module on `lypning-l`: the flag constants as a value with
  CPython's repr and arithmetic, `re.escape` (the 3.7+ table) and `re.purge`.
  Every matcher call — `search`, `match`, `fullmatch`, `findall`, `finditer`,
  `compile`, `sub`, `subn`, `split` — is a static route to CPython with kind
  `re`, and a runtime refusal as the backstop for dynamic reach.
- `lypning conformance --mixture both` on 2026-09-05, 2,504 programs graded:
  `lypning-l` MATCH 1910 / UNSUPPORTED 594 / MISMATCH 0 — coverage 69.5% → 76.3%.
  MISMATCH 0, UNSAFE 0, monotone 0, dispatchers agree 2504/2504. `lypning-l`
  867,744 → 867,840 B (7 blocks). LATE 38 → 123: those rows are programs that
  call a matcher but die identically on both engines before reaching it in the
  battery's temp cwd — an artefact of the instrument, not a routing cost, and
  the reason `--plan` still ranks `re: re.findall()` at 23.
- Two dispatcher bugs the first runtime-refusal class that reads stdin exposed:
  a forked intermediate rung inherited the pipe, so a program that read stdin
  and then refused handed the next rung an exhausted stream (`''` at exit 0).
  Both dispatchers now buffer a piped stdin before forking — only when the
  route says the program can read it, since unconditionally that blocked on a
  slow producer. `open(0)` and `sys.stdin.buffer` were exit 1 where CPython
  answers; both refuse.
- Three adversarial passes, 540 + 34 differential one-liners; eight holes in
  the first cut (four flag-value paths, the slow-producer hang, the
  makedirs-then-refuse route regression) closed before merge.
- The frozen core is 818,080 → 834,672 B and still 7 blocks: the router rows
  every binary carries plus the conditional preload, with the Mach-O text
  segment crossing a 16 KB page. It carries no `re` code or text (`strings`).
  Earlier "byte-identical" claims for #39 and #41 were size-identical only.

**2026-09-05** — Docs refactor, part 2: every engineer- and QA-facing document condensed to its budget · [4aa8209]

- The fifteen documents an engineer or QA reader is sent to went from 6,696 to
  3,053 lines (`wc -l` on 2026-09-05, this tree against `fc2c8e5`), plus the
  746-line `docs/VERIFICATION.md` spine. Each states what the code does in the
  present tense, cites its home as `file:symbol`, and points at the
  VERIFICATION contract that checks it. History moved to the three ledgers.
- Every EXPECTED block in VERIFICATION.md is a fixture under
  `tests/verification/expected/` with its run-of-record header, held by
  `tests/test_verification.py`; the check scripts live beside them.
- Engine strings only: `lypning`, `lypning-l`, `cpython`, and `lypning-mp` as
  the oracle at every first mention. The test that forbids placing an engine by
  tier number passes for every document. The skill's MICROPYTHON.md is deleted;
  SKILL.md carries a six-line oracle note.
- Six adversarial verifiers raised 41 holes — ten false claims, seven broken
  section anchors in code comments, three lost verbatim passages, seven missing
  QA items — all closed; a seventh verifier confirmed it and found one more,
  fixed by hand.
- Both binaries byte-identical (818,080 B and 867,744 B). Conformance,
  dispatcher agreement, doctor, site build and the Python suite unchanged.

**2026-09-05** — Reading the harness's own live state is run-specific

- `conformance.is_run_specific` now flags a program that reads under
  `~/.lypning` or `~/.claude`: the capture log grows on the battery's own
  reference spawn and the transcripts grow as the session running the battery
  types, so such a program can never match a reference taken a moment earlier.
  On 2026-09-05 `py-627dabb6be55` counted 7180 commands for the reference and
  7181 for the arm — both from CPython — and `MISMATCH 0` went red on an
  instrument artefact. Stdout is no longer compared for 45 such programs; their
  exit codes still are. Every other grade unchanged (2504 programs, MISMATCH 0,
  dispatchers agree 2504/2504).

**2026-09-05** — Docs refactor, part 1: code-side strings, the VERIFICATION spine, CLAUDE.md and README · [6a77a18]

- Every `--help` string, crate doc comment, header comment and skill file
  spells the chain as engine strings — `lypning -> lypning-l -> cpython` — with
  `lypning-mp` as the oracle, measured and never routed to. Engine lists are
  built from `engines.SPECTRUM` / `ENGINE_ORDER` / `ORACLES`, never spelled.
  Both binaries byte-identical (818,080 B and 867,744 B, `lypning build --rust`
  on 2026-09-05).
- New `docs/VERIFICATION.md`: fifteen contracts C1–C15, each a statement, a
  code home by `file:symbol`, a copy-pasteable check with its exit code, the
  expected output from a dated run of record, a failure-mode table and the
  pytest node that pins it. Fixtures live in `tests/verification/`.
- CLAUDE.md gains invariant 10 (the routes ledger is read by nothing that
  routes) and the ownership rule: CLAUDE.md is the rule, one design doc the
  mechanism, VERIFICATION.md the check. README's dated measurement tables moved
  to `docs/BENCH-LEDGER.md` by append.
- Three doc tests: upstream names appear only where README §8 says; no
  document places an engine by tier number (xfail until the remaining docs
  land); every cited `tests/x.py::test_name` exists.
- Opened #43 (opencode `--force` does not move a foreign `lypning.js`) and #44
  (`LYPNING_HARVEST=0` inert under the opencode plugin) rather than document
  behaviour the code does not have.

**2026-09-04** — The route ledger: the refusals a static route cannot predict · [7dc0d26]

- New `lypning routes`. `lypning route` is exact about everything it can see and
  it cannot see VALUE: `print(2**10)` and `print(2**100)` are the same program to
  a static walker, and one of them exits 90 with `bigint`. The ledger records
  those runtime refusals — a CLEAN static route followed by an exit-90 refusal
  from the tier it named, and nothing else — so `--plan` ranks the capability
  gaps real sessions hit next to `conformance --plan`, which only ever sees the
  shipped corpus.
- **Write-only with respect to routing.** Nothing in the store is consulted while
  routing, ever. A machine-local file that could move a route would make
  `conformance` a measurement of one laptop and give the two dispatchers a new
  way to disagree; the test that says so compares a graded `conformance.run`
  byte-for-byte with the store populated and absent.
- One `json.dumps` and one `O_APPEND` write under `PIPE_BUF`, every exception
  swallowed, so the write cannot fail a session (invariant 5). The header pins
  engine, caps and binary identity; anything else discards the file rather than
  put a dead binary's refusal into a build order.
- Absent, unreadable and truncated are rendered as three different facts, and an
  empty store is a hole, never a zero. `LYPNING_CAPTURE=0` disables this feed as
  well as `LYPNING_ROUTES=0` — a documented opt-out may not quietly narrow.
- Only the Python dispatcher writes; the Rust binary's own dispatcher does not,
  so every count is a floor and each rendering says so.
- Measured on 2504 corpus programs: MATCH/UNSUPPORTED/MISMATCH unchanged at
  1573/931/0 and 1741/763/0, LATE 38, WASTED 73, UNSAFE 0, monotone 0/2504,
  dispatchers agree 2504/2504, doctor 0 FAIL. Both binaries byte-identical
  (818,080 B and 867,744 B) — no Rust changed.

**2026-09-04** — Three router over-refusals, and the mis-route economics that rank them · [#42]

- `import os.path` recorded a false alias `os` → `os.path`, so `os.path.basename(...)`
  blocked as `module-attr: os.path.path` and went to CPython for a call this
  engine answers. An alias is an `as` clause and nothing else; the binding is
  compared against the first dotted component now, which is what Python binds.
- `__name__` joins the router's optimistic method union. Safe to admit
  unconditionally for the reason `.name` and `.errno` are not: an unmodelled
  receiver **refuses** (`dunder-attr`, exit 90, one spawn recovered by the
  chain) where those raise `AttributeError` at exit 1, which the chain never
  recovers. Only names whose miss is a refusal belong there.
- `except json.JSONDecodeError` reduced a dotted name to its leaf, found no
  builtin, and blocked. Dotted exception names now resolve the prefix as a
  module and ask it for the leaf, falling back to the old rule when the prefix
  is not a module we serve — so it can only remove refusals it can justify.
- **The measured trade, quoted from this run** (2,504 graded, 2026-09-04):
  LATE 45 → 38, WASTED 59 → 73, MISMATCH 0, UNSAFE 0. A LATE costs a CPython
  spawn (12.0 ms measured here); a WASTED costs a parse, because `lypning run`
  IS the dispatcher and the process is already running (1.21 ms). So the trade
  is −84 ms against +17 ms — a **net ~67 ms**, roughly 5:1 — while the report's
  `accuracy` line reads 95.8% → 95.6%, because it weights the two grades
  equally when they cost an order of magnitude differently. The grades are a
  census, not a cost model; read them with the millisecond beside them.

**2026-09-04** — `lypning-l` serves `pathlib` · [#41]

- Coverage **64.3% → 69.5%** (+132 corpus programs) for +32.3 KB — **4.08
  programs per KB**, nearly double the previous capability. `lypning` is
  byte-identical (818,080 B, 7 blocks); `lypning-l` is 867,744 B, still 7 of
  its 32 blocks.
- The pure-path algebra is exact: construction and normalisation, `/` in all
  three spellings, `.parts` (**with the root as a component** — the oracle
  recorded a reimplementation dropping it), `.name/.stem/.suffix/.suffixes/`
  `.parent/.parents`, `with_name/with_stem/with_suffix`, `relative_to`,
  comparison, hashing, `str`/`repr`/`bytes`/f-strings. The filesystem half
  (`read_text`, `write_text`, `mkdir`, `unlink`, `open`, `exists`…) goes
  through the existing commit barrier.
- Refused rather than approximated: `.glob`/`.rglob`/`.iterdir`/`.walk`
  (directory order is filesystem-defined, as `os.listdir` already is),
  `.resolve`/`.absolute`/`.stat`/`.home`, `PurePath`/`WindowsPath`, every error
  path, and the two places CPython's own versions disagree — `with_stem('')` on
  a suffixed name, and ordering paths where 3.11's `.parts` and 3.12+'s
  `str.split('/')` give different answers.
- Measured and **not** landed: `hashlib` at 0.87 programs/KB (and serving it
  made programs route in that then died at exit 1 on unrelated missing
  methods) and `csv` at 1.39. Density is now a tracked curve; see
  `docs/HILLCLIMB.md` iteration 74.

**2026-09-04** — `lypning-l` serves `collections.Counter` and `defaultdict` · [#39]

- The first capability the spectrum's larger variant carries that the frozen
  core does not: **`cap-collections`**, in `variant-l` only. Coverage
  **62.8% → 64.3%** (+36 corpus programs); `lypning` is byte-identical on disk
  (818,080 B, 7 blocks) and its answers are unchanged.
- Counter and defaultdict are the existing `Dict` with a tag, because they are
  dict subclasses in CPython — length, `in`, iteration order, `==` against a
  plain dict, `dict(c)`, `json.dumps(c)` and key collapse come for free rather
  than being reimplemented. `most_common` keeps insertion order on ties, and
  both reprs are exact.
- Everything outside the measured corpus slice refuses: multiset arithmetic,
  `.elements()`, `.subtract()`, `.total()`, keyword construction, a callable
  `default_factory`, and every error path (message text drifts across
  3.11/3.12/3.14).
- **The oracle wrote the spec.** `lypning oracle` named the traps before a line
  was written — sort instability on `most_common` ties, key collapse across
  bool/int/float, dict-view equality — and each became a grid row. The
  adversarial pass then caught three defects the implementation grid had not:
  `dict.update(c, …)` using Counter's add-semantics where CPython replaces,
  `dict.copy(c)` returning a Counter where CPython returns a plain dict (both
  silent, exit 0), and `sorted([Counter, Counter])` raising TypeError at exit 1
  where a clean exit-90 refusal was owed — the guard was wired into the operator
  paths but not into the free comparator `sorted`/`min`/`max` use.

**2026-09-04** — lypning-mp leaves the tier stack and becomes the oracle · [#38]

- **The chain is now `lypning` → `lypning-l` → CPython.** Nothing routes to
  lypning-mp: it is out of `ENGINE_ORDER`, out of `route.rs`'s `Engine` enum,
  out of both dispatchers' fall-through, and the machinery that existed only to
  steer programs around it (`MICROPYTHON_UNSAFE`, the `mp_risk` markers,
  `engine_for`'s MicroPython arm) is deleted rather than left deciding nothing.
- **It is kept as an ORACLE — measured, never routed to.** It is a second,
  independent, from-scratch implementation of Python that has been run against
  real CPython over the whole corpus with every disagreement recorded:
  **79 divergences in 34 families** in `.github/known-mismatches.json`. Those
  are the empirical clusters where a reimplementation goes wrong — float
  formatting, sort stability, hash order, message text — so each one is
  something a larger Rust variant must implement *exactly* or *refuse*. New
  `lypning oracle [--full] [--json]` renders the catalogue; it reads the ledger,
  so it works without the 32-bit toolchain the binary needs. `engines.ORACLES`,
  a `status` section of its own, and `--engine lypning-mp` still measures it.
- **The cost, measured, not estimated**: 852 of 3,688 corpus programs (23.1%)
  routed to lypning-mp and now reach CPython instead — `re` 345, `pathlib` 177,
  `collections` 58, `glob` 23, `csv` 19, `hashlib` 13, `math` 12 … That list is
  `lypning-l`'s build order, and `conformance --plan` now ranks by real CPython
  cost because there is no cheaper tier left to hide behind.
- `ONLY_CPYTHON_KINDS` survives and means *more*: it used to say "skip the
  MicroPython tier", it now says **no Rust variant may answer this, at any
  size**. The gate's default subject is the Rust core rather than the oracle;
  `lypning build` no longer builds the oracle unless asked; `bench` gains a
  `lypning-l` arm.
- Bytes: removing the tier's routing machinery **shrank the core** 834,592 →
  818,080 B (host build, still 7 blocks).

**2026-09-04** — The spectrum, step 5: `lypning-l` exists, with identical capabilities · [#37]

- The spectrum has two points: `lypning` (1 MB, 8 blocks, frozen) and
  **`lypning-l`** (`--features variant-l`, budget 32 blocks). Today it carries
  exactly `lypning`'s capabilities, so the router — first "can run" at or above
  the running binary — never picks it, a runtime refusal never tries it (a
  sibling with the same capabilities cannot answer what a smaller one could
  not), and every mixture verdict and chain is identical to before; that
  identity is this step's proof. It grows one capability per step from here.
- Every table and ladder is over the spectrum now: `lypning build --rust`
  builds both, `conformance` runs both by default and adds a **monotone**
  check (a larger variant never does worse than a smaller one on a program
  both ran — a violation fails the run), `gate` holds each to its budget,
  `doctor` and `status` show both rows.
- The C ABI library is the largest variant and says so: four additive symbols
  (`lypning_engine_self/count/name/is_rust`), ABI still 1 — no existing symbol
  changed shape, and the header now says a route can name any spectrum member.
- **Correcting step 3**: it said a dispatcher disagreement "fails the run". The
  report printed FAIL; the exit code did not follow — `Report.ok` never
  learned about disagreements. It does now, along with monotone violations.
- Docs: the chain diagram and the tier table show the spectrum.

**2026-09-04** — The spectrum, step 4: build, install and gate per variant · [#36]

- `lypning build --rust` builds every point on the spectrum (`--variant NAME`
  narrows it): one cargo feature per variant, the default variant in cargo's
  own `target/` so a by-hand build still shares the object cache, every other
  one under `target/variant-<letter>/`; the refusal and spectrum contracts are
  asserted with each variant's **own** name. The C ABI library names the
  largest variant explicitly instead of inheriting `default`.
- **The Rust core is now size-gated**: `lypning gate` holds each variant to a
  budget in device blocks (`lypning`: 8, frozen — 1,007,824 B on musl today,
  40,752 B of headroom; every new capability goes to a larger variant). It was
  measured and never failed before.
- **`--target host` installs as `lypning`**, not `lypning-host`: the literal
  `"host"` was compared as an architecture name and the only binary that runs
  on a darwin host landed under a name no finder looked for. `doctor` shows
  one row per variant. Routing and conformance counts unchanged.
- **Retracting a claim from the step-3 entry.** It said "bytes unchanged"; no
  sized build was run for it. Measured now on the host build: step 3's verdict
  vector, chain walk and `--next` cost **+7,472 B of `__text`** (639,296 →
  646,768; file 817,984 → 834,592, still 7 blocks). Steps 1, 2 and 4 are
  Python-only and cost nothing; the musl count is CI's to print.

**2026-09-04** — The spectrum, step 3: one routing rule in both dispatchers (no behaviour change) · [#35]

- `route.rs` now decides by a **verdict vector** — every rung's yes/no on the
  program, in cost order — and picks the first "can run" at or above the
  running binary (the floor rule). `lypning route --json` carries the vector;
  `route --next --after E --kind K` prints the chain the Rust dispatcher walks
  after a runtime refusal, from the same function `lypning run` uses.
- `lypning run` walks that chain (a sibling that could run the program, then
  lypning-mp if it can import everything, then CPython; a semantic refusal
  skips everything), falling to the next rung when one is not installed; a
  sibling variant is found the way Python's `find` finds it.
- Python's `chain_after_refusal` is the same rule over the same verdicts, and a
  cross-product test (`tests/test_routing.py`) holds the two dispatchers to each
  other. A binary that names a variant this copy of the table lacks now routes
  with kind `route-unknown-engine` — loud, never silent.
- `lypning conformance --mixture rust|both`: the Rust dispatcher as its own arm
  (`mixture-rust`), and with `both` the report prints `dispatchers agree N/N`
  and a disagreement fails the run. Until now the battery graded a dispatcher
  nobody execs and the benchmark timed one nothing graded.
- Zero behaviour change: `route --json` over all 3,688 corpus programs is
  identical before and after (engine, kind, detail, imports); conformance
  counts unchanged; bytes unchanged.

**2026-09-04** — The spectrum, step 2: the binary learns its own name (no behaviour change) · [#34]

- Exactly one `variant-*` cargo feature is on in any build (`variant-m`, today's
  binary, is the default); `build.rs` turns it into `LYPNING_ENGINE`, and
  `err::ENGINE` / `err::refusal_line` are the one place a refusal line is
  spelled — the four literals that used to say `lypning:` go through it, so no
  variant can ever write a sibling's name at the head of its refusal line.
- `route::SPECTRUM` (one row) and `route::CAPS` (empty) are compiled into every
  binary; `lypning route --spectrum` prints the table and which row this binary
  is; `--version` names the variant. `lypning build --rust` now asserts, on the
  binary it just produced, that it calls itself what we expect and that its
  compiled table is exactly `engines.SPECTRUM`; `routing.spectrum()` reads the
  same table from the source, and a test holds the Python copy to both.
- Bytes unchanged (817,984 B / 7 blocks host); conformance 1573 / 931 / 0 over
  2504 graded, identical to main.

**2026-09-04** — The spectrum, step 1: the names become a grammar (no behaviour change) · [#33]

- lypning is becoming a **spectrum** of Rust variants from one crate — `lypning`
  (1 MB, today's binary) and, next, `lypning-l` (4 MB, to absorb what the
  MicroPython tier does). This PR is the grammar only, at N=1: invariant 9 is
  amended; `engines.SPECTRUM` (one entry) feeds `ENGINE_ORDER`;
  `parse_binary_name` is the one reader of `<engine>[-<target>]` (it replaces the
  gate's ad-hoc check, longest engine first); `env_var_for` spells every
  `LYPNING_*_BIN` pin by rule where five sites spelled them by hand;
  `refusal_line` is the one formatting site the build and embedding checks pin
  against. A test holds that no engine name is spelled by hand outside
  `engines.py`. Every chain, pin and gate answers exactly as before.

**2026-09-03** — Timing tools report host load, and refuse to call a loaded reading a measurement · [#32]

- `lypning bench`, `corpus-time` and `perf` print the 1-minute load average in
  their host header; when it exceeds the CPU count the header says so, and a
  `corpus-time --baseline` verdict is printed with `UNRELIABLE, host loaded`
  appended rather than as FASTER/SLOWER. Every one of these tools is
  spawn-bound, so an oversubscribed host measures the scheduler — one session
  quoted a 28x regression that was a load average of 340.

**2026-09-02** — The corpus can no longer fork-bomb its own battery · [#28]

- **`lypning conformance` and `lypning bench` now skip a corpus program that
  would launch a battery** — a CLI battery subcommand, or the runner modules
  driven from Python (`conf.run`, `engines.dispatch`, `bench.corpus_time`,
  loading the whole corpus). This project's own dev sessions type these, so
  they are harvested into the corpus like any one-liner (235 of 3,688); run
  inside the battery each spawned a battery over the whole corpus again, a fork
  bomb that reached load average 340 on a shared host. A net, not a sandbox
  (invariant 4): capture still records them, the runner refuses to replay them,
  exactly as an absolute-path program is recorded and skipped. `lypning
  route`/`run` over one program stay representative usage and are not skipped.

**2026-09-02** — the corpus can be sliced by the model that issued the program ([#27](https://github.com/kristerhedfors/lypning/pull/27))

- **A new `models` field** on sightings and corpus records: a per-model
  histogram of the occurrences behind `count`. It is a subset of them, never a
  partition — an occurrence that cannot be attributed contributes to `count` and
  to nothing else, so `count - sum(models)` is the unattributed hole and a merge
  raises `count` rather than let that go negative. The key is omitted entirely
  when nothing is known, so records captured before this existed keep the bytes
  they have.
- **The hook stays on the hot path it was on.** Nothing in the PreToolUse
  payload names a model, so the hook writes down the `tool_use_id` it already
  receives and the join is done at harvest time, against the session transcript
  and its subagent tree. No new fork, no new file open, no change to the shell
  hook.
- **Attribution is Claude Code's only, by construction.** An opencode tool hook
  and an OpenHands `PostToolUse` payload carry no model and no key to join one
  on, so their records stay unattributed — which is what the hole in `models` is
  for, and is pinned by a test rather than assumed.
- **The harvest-time join reads only what was appended.** Transcripts are
  append-only, so the index is incremental and its offsets are cached under
  `$LYPNING_HOME` — a cache that is never a source of truth and whose every
  failure is a full re-read. Quoted in bytes because this machine is shared and
  a wall clock on it measures the machine: on 2026-09-02, over a copy of the
  real capture log (892 records, 4 sessions, 180 transcript files, 45,642,644 B)
  a cold harvest scans all of it and every harvest after it scans 0 B, reading
  734,678 B of staleness digests instead. The same log unmodified, with no ids
  in it, reads 0 transcript bytes and writes no cache at all.
- `lypning corpus --stats` now reports entries per model with an explicit
  `unattributed` row, and `--model NAME` slices the corpus, naming the whole the
  slice came from.
- Sightings records now carry unknown keys through, the way corpus records
  already did — an older harvest was silently stripping fields a newer one had
  written.

**2026-09-02** — Three more hosts over the one ABI: Go, Swift, LuaJIT; and a quickstart for every host · [#25]

- **Go** (`assets/go/`, cgo over the unchanged header, zero modules), **Swift**
  (`assets/swift/`, a Clang module map over the header, SwiftPM or plain
  `swiftc`) and **LuaJIT** (`assets/lua/lypning.lua`, `ffi` over the header read
  at load, no build step) join C, C++, Rust, Node and Python. The table in
  `docs/EMBEDDING.md` §4 is now the one place the hosts are counted; every other
  document says "every host" and points there.
- **One quickstart contract, eight files.** Every host has a
  `quickstart "<python source>" [args...]` that runs in-process under a step
  limit, hands a refusal to `python3 -c` once, and otherwise returns the
  program's own bytes and exit code. `tests/test_hosts.py` drives all of them
  through the same five probes and counts the traceback on stderr exactly once,
  which is the retry-a-failed-program drift no per-host test can see; CI runs
  the same five probes as one shell function per host, on Linux and on a new
  macOS job.
- **macOS is a first-class library platform.** `lypning build --lib` writes
  `liblypning.dylib` with an `@rpath` install name (a `build.rs` in the core
  crate, macOS only, and byte-identical `lypning` binary with or without it);
  the truncation check reads Mach-O the Mach-O way; `doctor` and `status`
  report a missing library as a hole, not a zero. `pyproject.toml` now claims
  `Operating System :: MacOS` on that basis.
- **Run against each other, not assumed to agree.** On 2026-09-02 (macOS arm64;
  clang, cargo, node 26, go 1.26, swift 6.3, luajit 2.1) `study/hosts/run_all.sh`
  drove every host over the 393-program study set: each of the eight reported
  341 ran, 52 refused, 0 other; 3144 capture records; `git status` unchanged.
- **Packaging.** The wheel ships every binding and quickstart as source (40
  files under `assets/{examples,go,lua,node,swift}` on 2026-09-02) and no build
  output; `dist-check` also rejects `.build/`, `.swiftpm/`, `node_modules/` and
  `.so`/`.dylib`/`.node`/`.class`. Both crates declare `rust-version = "1.78"`,
  the floor the committed lockfile format actually imposes.
**2026-09-02** — Tier 1 serves seeded `random`, bit for bit; four silent wrong answers found on the way · [#24]

- **`random`, the seeded-integer subset, on tier 1.** `seed(int)`,
  `random()`, `randint`, `randrange(a, b)`, `choice`, `getrandbits` are
  CPython's MT19937 exactly; everything else refuses. `random` leaves the
  middle tier's module table — its generator is not MT19937 and seededness
  cannot be decided statically — and both dispatchers now re-read a program's
  imports when a runtime refusal falls onward. Conformance 1325 → 1336 MATCH
  over 2125 graded (2026-09-02), MISMATCH 0, UNSAFE 0; `__text` +8.1 KB for
  everything below too.
- **`sum()` over floats answers only where CPython 3.11, 3.12 and 3.14
  agree**, else refuses `float-sum`. It was a naive fold — `sum([0.1]*10)`
  printed 0.9999999999999999 where 3.14 prints 1.0.
- **`raise SystemExit(n)` exits `n`.** It exited 1 with a traceback.
  SystemExit is one exception shared with `sys.exit()`, caught and `finally`'d
  as CPython does; ambiguous arguments refuse.
- **`lypning run` no longer re-runs a program that exits 90 on its own.**
  `print(1); sys.exit(90)` printed 1 twice — invariant 2's double run.
- The grader compares seeded streams (they were blanket-uncompared, which
  would have graded a wrong Mersenne Twister as MATCH); CPython warnings on
  stderr are no longer "an error the engine was silent about".

**2026-09-02** — the capture loop runs under opencode and the OpenHands SDK ([#26](https://github.com/kristerhedfors/lypning/pull/26))

- Two harness adapters, both MIT-licensed hosts, installed with
  `lypning install --harness opencode,openhands`. Neither merges into a file the
  user owns: opencode auto-discovers a plugin file, OpenHands discovers a plugin
  directory. opencode is what Berget Code's agents are built on.
- `capture.py` grows one neutral record builder and one small mapper per
  harness; every record now carries a `host`, and `lypning harvest --json`
  counts by it. The corpus schema is unchanged.
- Deliberately not shipped: automatic routing under either harness, and any
  write to `.openhands/hooks.json` — that file is first-match-wins and unmerged,
  so writing it would hide the user's rather than join it.
- How many python one-liners either harness actually types is **unmeasured**,
  as is the injected routing paragraph. `docs/HARNESSES.md` says which claims
  were verified against a real install and which were not.
||||||| parent of 9eac506 (Three more hosts over the one ABI: Go, Swift, LuaJIT; a quickstart for every host)

**2026-08-31** — pathlib costs nothing; `conformance --plan` is now ranked by cost

- **pathlib was measured before it was built, and not built.** 85 of the 87
  graded programs importing `pathlib` already route to lypning-mp; **2 reach
  CPython**, one of them the irreducible `from lypning import …`. Implementing
  it in tier 1 would have saved ~0.03 s and spent bytes against 16 KB of
  headroom.
- **`conformance --plan` now ranks by what a feature COSTS, not by how many
  programs it blocks.** A refusal the classifier sends to lypning-mp is answered
  at that tier's spawn; one reaching CPython costs ~30× more. The two orderings
  disagree sharply — `import re` blocks 185 and 12 of them cost anything;
  `import pathlib` blocks 83 and costs **nothing**; `.__name__()` blocks 22 and
  costs more than both. The table now prints `->cpy` beside `blocks`, and
  `plan_cost()` exposes the key. Falls back to block count when the mixture arm
  did not run.
- This loop spent two iterations proposing `--plan`'s top rows before the
  destination was measured. The ordering is the steering wheel, so it is now
  pinned by a test with a case where cost and count disagree.
- No engine change: bytes 1,032,400 (8 blocks), conformance 1325/800/1, mixture
  2119/0/7, 1314 tests green.

**2026-08-31** — `re` was the wrong row: `--plan` optimises the wrong objective

- **Retracting a number from the previous entry.** It claimed 800 tier-1
  refusals × ~11 ms of CPython spawn ≈ 8.8 s. Routing all 1,990 graded programs
  shows only **271 reach CPython** (13.6%); 410 are served by lypning-mp at
  0.36 ms. The avoidable cost is ≈**2.98 s**, and 111 of the 271 are this
  project's own `from lypning import …` development one-liners, which no tier
  can serve.
- **`conformance --plan` ranks tier-1 blockers, not cost.** Its top row, `re`
  at 185 programs, is already answered correctly by lypning-mp — as
  `modules.rs` has said in a comment all along. Verified before writing an
  engine, not after.
- **Tier 1 now answers `__name__` on a builtin receiver** (`int.__name__`,
  `len.__name__`, `ValueError.__name__`) — the one receiver whose name is not a
  guess. The wildcard refusing every other dunder is intact; a 20-case grid
  confirms it.
- **Two changes measured and reverted, both for invariant 1.** Rerouting
  `__name__` to lypning-mp gained 4 mixture MISMATCHes — the block had been an
  accidental shield against defects that tier has elsewhere. Teaching `type()`
  to answer for an exception instance gained 2 tier-1 MISMATCHes by unblocking
  programs into a separate pre-existing defect (`Value::Exc` stores its
  argument as a string, so `OSError(2)` reports `('2',)`).
- **`study/re/SEMANTICS.md` is new**: ~300 `re` input/output pairs run against
  real CPython 3.11 — the differential spec a future tier-1 engine must meet.
- Bytes unchanged at 1,032,400 (8 blocks); conformance 1325/800/1 with the
  ledger clean at 87/87; corpus-time 3.42 → 3.03 s; 1313 tests green.

**2026-08-31** — Iterations 66–68 all reverted; the speed gradient is flat and the dial is re-aimed

- **Three measured failures, three reverts, all kept in `docs/HILLCLIMB.md`.**
  Exact-capacity split (+15.6%), a shared ASCII case buffer (+4–6% on the very
  cases it targeted), and a first-byte pre-filter on builtin dispatch (+15.0%
  wide, +15.2% narrow). Each was A/B'd interleaved against **four
  unchanged-source probe builds**, which put the perturbation band at 5–9% on
  these paths.
- **The finding is the deliverable.** At `opt-level = "s"` with LTO and one
  codegen unit, the wins still available on the hot paths are smaller than the
  band the build itself moves them by. Iterations 64–65 took the allocations
  that were free to remove; what remains costs a pass to eliminate, or moves
  inlining more than it moves work. `builtin()` now carries a comment recording
  the 15% measurement so the idea is not re-attempted there.
- **The focus dial in `.claude/skills/hillclimb/SKILL.md` is re-aimed from raw
  performance to coverage**, which is the skill's own stop condition after
  three flat iterations — and the arithmetic is not close: 800 UNSUPPORTED
  programs at an ~11 ms CPython spawn each is ~8.8 s of avoidable work against
  a whole-corpus lypning total of 3.05 s. Fifteen blockers account for 619 of
  the 800; the top three are `re` (185), `lypning` itself (131) and `pathlib`
  (90).
- No engine behaviour changed: conformance 1325 / 800 / 1, binary 1,032,400 B
  at 8 blocks, 1311 tests green.

**2026-08-31** — Hillclimb iterations 64–66: two allocation kills, one measured revert

- **`str-fmt-pct` 8.09x → 5.49x** (iteration 64): `percent_format` stops
  allocating per conversion — the argument tuple is borrowed instead of cloned,
  the output is reserved past the growth cliff, and bare `%s`-on-str /
  `%d`-on-int write into the output directly. Sixteen shapes diffed against
  CPython; byte-identical binary.
- **`str-split` 8.21x → 4.41x** (iteration 65): the 128 ASCII one-character
  strings are interned singletons, consulted by every site that materializes a
  single character — both split paths, `s[i]`, `for c in s`, `chr()`. Two sites
  were allocating twice per character. Safe by construction here (`is` between
  equal immutables refuses) and convergent with CPython (its latin-1
  singletons). +4,096 B, still 8 blocks.
- **Iteration 66 measured, lost, reverted, ledgered**: exact-capacity two-pass
  splitting was +15.6% in a direct A/B — with tokens interned, the realloc it
  removes is cheaper than the second scan it adds. The failure is in
  `docs/HILLCLIMB.md` so it is not re-proposed.
- Suite TOTAL 2.52x → 2.25x across the two accepted steps; whole-corpus
  `corpus-time` 3.42 s → 3.05 s over 2,126 programs; conformance unchanged at
  1325/800/1 (the ledgered musl `pow` ULP).
- **Pool round-trip −0.37 ms/request**: the child applies the caller's
  environment as a diff instead of `clear()+update()` (~272 libc calls → a
  handful), semantics verified in both directions.
- Method-name id-dispatch was priced and retired: nulling the whole arity
  string-match pass bought 2.7% of wall on a scaled `str-methods` loop, so the
  ~20% of instructions in name matching does not convert (the ledger's
  binary-search lesson, again).

**2026-08-31** — The pool-backstopped chain, built: 1.77x and 745/745 correct

- **`lypning pool serve` is new** (`src/lypning/pool.py`): a pre-warmed CPython
  that forks per program, wired in as the chain's CPython tier via
  `LYPNING_POOL`. Opt-in, off by default, and it degrades to a cold spawn if the
  pool is down, wedged or unreachable — the backstop may be faster than CPython,
  never a new way for CPython to be unavailable.
- **The composition the measurements pointed at, measured instead of estimated**
  (`study/paper/pool_chain.py`, best-of-3 over the 745 clean programs):
  pool-backstopped chain **14.85 ms/prog, 1.77x, 745/745 correct** (481 answers
  from tier 1, 264 from the pool) against the cold chain's 17.41 ms / 1.51x /
  744, the pool alone at 18.76 ms / 1.40x, and cold CPython at 26.27 ms. It is
  the fastest arm and the only fast arm that answers everything.
- **Our own arithmetic was wrong by 40% and the paper says so.** The projection
  was ~3x; it ignored that the tier-1 pass is paid on all 745 programs including
  the 264 it refuses, and that the pool leg pays a socket round-trip.
- **A published claim is retracted: a warm pool is NOT correct by construction.**
  It freezes its environment at start. Forwarding the caller's `os.environ` per
  request took divergences from 6 to 3; the residual 3 were set/dict-view
  orderings, which a fork cannot fix because `PYTHONHASHSEED` is consumed at
  interpreter start. Starting the pool under the caller's seed took 3 to 0.
  **Start the pool as the interpreter its callers think they are getting.**
- **Two fidelity bugs in the pool, found by the corpus and not by hand.** The
  forked child must rebind `sys.stdout`/`stderr`/`stdin` onto its dup'd
  descriptors with CPython's own `surrogateescape`/`write_through` settings —
  inheriting the parent's objects silently drops all output whenever the host
  has replaced them (every hand-written smoke test passed; the test suite
  caught it immediately). And the pool arm needed the universal-newline
  translation the spawned arms get from `text=True`, or a `csv.writer` program
  grades as a divergence against its own reference.
- **The "2-program library defect" reported yesterday was build hygiene, not a
  bug.** `liblypning.so` had last been built a day before the binary, so the two
  artifacts answered from different subsets — the library still raised on
  `dict.keys() | {...}` the binary had learned to refuse, and refused an
  `rsplit` the binary had learned to answer. Rebuilding took frontier-probe
  disagreements from 4 to 1 (a musl-vs-glibc `pow` ULP, expected between a
  static binary and a host library).
- **`lypning doctor` now fails when the two artifacts disagree.** Seven
  constructs chosen to sit on the refusal frontier are run through both the C
  ABI and the spawned binary; nothing else in the package would have noticed,
  since both pass their own contract assertion and both report the same version.
- 26 new tests pin the pool to CPython byte-for-byte: traceback text with no
  pool frame, non-integer `SystemExit`, signalled children, state isolation
  between programs, large output through the pipe, and a socket that is not
  world-readable.

**2026-08-31** — The Monty stack warm-for-warm, and the executive verdict

- **The warm-shape asymmetry is closed** (`study/paper/warm_parity.py`, paper
  §5.5): both substrates warm, same 745 programs, same per-program temp cwd.
  liblypning sweeps at 2.23 ms/program against the Monty pool's 9.41 —
  ≈3.5 vs ≈25 ms per *answered* program, since lypning declines 262 cheaply
  while Monty attempts everything — with 480 matches / 1 silent divergence
  against 275 / 23. Stated with equal care: the all-answering warm chain
  (12.60 ms) is still slower on wall than Monty's pool; the extra 3.2 ms buys
  742 correct answers instead of 275 correct plus 447 errors.
- **The parity instrument caught a lypning defect**: the in-process library arm
  errs on 2 programs the spawned binary correctly refuses (480/262/2/1 vs
  480/264/0/1). Unresolved, reported in every affected table, and now the warm
  chain's correctness line reads 742/2/1 rather than borrowing the binary's.
- **`docs/EXECUTIVE-SUMMARY.md` is new**: does lypning improve, and where — as
  objective as the data allows. It was adversarially reviewed for bias in both
  directions before publication; the pro-lypning pass caught a buried loss and
  an order-of-magnitude flourish, the anti-lypning pass caught the warm-pool
  loss overweighted past what the data says and the topline framing our best
  result as a cost. Both sets of corrections are in the published text, and the
  verdict table now states its denominators and weightings per row.
- pydantic-monty 0.0.21 confirmed the current PyPI release at review time.

**2026-08-31** — The overarching review: gaps closed by measurement, and one claim revised down

- **Six new measurements landed in `docs/PAPER.md`, run because reviews demanded
  them.** The `-S` ablation (CPython starts in 8.67 ms with site disabled — the
  one-flag baseline recovers a quarter of its startup). CPython 3.13 as a sixth
  arm: it starts *slower* than 3.11 on this container (13.8 vs 11.6 ms,
  back-to-back), carries no experimental JIT — and as an engine against the 3.11
  reference it produces **9 silent divergences of its own**, so tier 1 (1) tracks
  CPython 3.11 more closely than CPython 3.13 does. A false-refusal sweep: zero
  exit-90s in 1,990 CPython runs. The tier-2 ablation: correctness identical
  without MicroPython (744/1), so the classifier's routing is demonstrated, not
  asserted. The per-program price list: the chain is slower than cold CPython on
  216 of 745 programs (29.0%), median delta −10.56 ms, worst +173 ms. And the
  invocation-weighted wall: weighting each program by its 6,171 capture-log
  invocations lifts the chain from 1.77× to **2.35×** — sessions re-run the
  simple programs, so for once the unrun measurement was hiding a number in our
  favour.
- **One of the paper's own claims is revised down.** Hand-classifying all 39
  PyPy divergences (shipped at `study/paper/data/`) splits them six ways:
  file-finalization 10, set order 7, call-signature 4, error-text 4, singletons
  4, probe-grids-beyond-window 10. The largest family is the one the profile
  predicts, but "dominant" was too strong a word and the paper now says so.
- **The prospective holdout is registered, not promised.**
  `study/paper/holdout_registration.json` pins the freeze commit, the date, and
  all 2,906 in-sample entry ids; anything captured later is out-of-sample by
  construction.
- **The documents now agree with each other.** COMPARISON.md drops the retracted
  tier-1 "ceiling" splice, gains the warm-pool loss and a cross-reference to the
  paper, and explains its 7-vs-1 and 340-vs-447 differences as instrument
  differences. The README's 0.302x headline is date-scoped and points at the
  paper's current ratios and the baseline that beats us; the paper is surfaced
  at the top of the doc table. Stale phrasings in this changelog's own earlier
  entries were corrected in place.

**2026-08-31** — The paper: an agent-Python profile, and five engines measured against it

- **`docs/PAPER.md` is new.** It asks what coding agents actually hand to an
  interpreter, answers it by measurement, and benchmarks CPython 3.11, PyPy
  7.3.20, MicroPython, Monty 0.0.21 and both lypning configurations on that
  corpus with one instrument. Harnesses ship in `study/paper/`.
- **The profile is the finding.** Over the corpus as loaded on 2026-08-30 (2,906
  entries, 2,869 parsed): the median program is 384 bytes, 10 lines, 74 AST
  nodes; 0.3% define a class; `match`, walrus and `async` do not appear at all;
  and 45.8% call `open()` (AST-counted). Decomposed inside the child over 765
  CPython-clean programs, the median program spends **0.019 ms executing against
  16.83 ms of spawn, interpreter startup and imports** — 88.4% execute in under a millisecond.
- **PyPy is the slowest engine on this workload** (3.0–3.1× CPython's wall in two
  sweeps) and returns **39 silent divergences**. All three divergence families
  are ones PyPy documents: non-prompt file finalization, ordered sets, and
  keyword arguments CPython rejects. The contribution is the blast radius, not
  the discovery. The cause is also now measured rather than assumed — PyPy pays
  **+16.22 ms over CPython on `print(1)`**, so the penalty is majority fixed
  startup, not unamortized warmup.
- **We lose to the obvious baseline and say so.** A pre-warmed CPython forking
  per program serves the 745 clean programs at 8.39 ms each (2.04× cold CPython)
  against our chain's 11.38 ms (1.50×), and it is correct by construction. The
  same table points past both: tier 1 alone serves its share at 2.67 ms, so a
  chain whose backstop is a warm pool should beat either.
- **Two reviewer objections answered with measurement, not prose.** The
  absolute-path exclusion really does remove the harder half — skipped programs
  loop at 75.1% against 38.2% retained, and define functions 3.6× as often — so
  the coverage rate is optimistic by an amount we now bound. And the
  self-hosting worry is a null on the evaluation: **none of the 745 clean
  programs import lypning**, so dropping them changes nothing (480/264/1 either
  way); the contamination lands on the profile, not the benchmark.
- **A real irreproducibility was found and fixed:** corpus programs calling
  `sys.stdin.read()` blocked on the harness's inherited stdin. Every child now
  gets an explicit EOF. Re-running the whole sweep afterwards reproduced every
  correctness count exactly, while wall clock moved with machine load — which is
  why timings are reported as ratios.
- **The bibliography was rebuilt after the survey agents turned out to have no
  network access.** Twelve references are now checked against primary sources;
  one recalled author list was confirmed fabricated (the POPL 2018 sourir paper's
  third author is Ming-Ho Yee). Attributions still resting on recall are marked
  `[unverified]` inline rather than quietly kept.
- **A hostile review of the finished draft found two arithmetic errors in our own
  favour, and both are corrected in place.** The composed "chain with a warm-pool
  backstop" estimate had spliced a blended average (2.67 ms covers 480 answers
  *and* 264 refusals) against the pool's per-program cost; done correctly it is
  5.65 ms and 3.03×, not the ~3.6× implied. And the chain's fallback is not one
  cold CPython spawn — 1,993 + 264 x 17.10 = 6,507 ms against a measured 8,476 ms,
  so a refused program costs 24.6 ms because it pays three tier spawns, not one.
  Every remaining arithmetic claim in the paper was then machine-checked.
- **Two further corrections of our own numbers.** `open(` by substring said 49.0%
  of programs; AST-counted `open()` calls give 1,314 of 2,869 parsed (45.8%), and
  that is the figure now used. And PyPy's fixed-startup share was reported as
  "roughly half" — the most flattering of three available denominators; the range
  is 49-78%, and on the clean-subset arm it is closer to four fifths.
- **The deduplication threat is answered with data rather than a caveat.** The
  capture log carries invocation counts: 2,906 distinct entries were seen 7,406
  times (mean 2.55, max 45, 63.5% seen once). Re-weighting by invocation moves the
  profile *away* from complexity - comprehensions 23.8% to 15.7%, loops 48.6% to
  44.7% - so the programs agents re-run are simpler than the ones they run once,
  and our coverage numbers are computed on the harder population.
- **The finding sharpened to its causal core.** 96.0% of the 1,314 file-touching
  programs use a bare `open()` rather than `with open(...)`; only 4.6% use the
  context manager. That is precisely the idiom PyPy documents as unsafe, which is
  why a documented difference becomes 39 silent wrong answers here.

**2026-08-30** — Measured against ADK-Rust CodeAct + Monty, on one instrument

- **`docs/COMPARISON.md` is new, and its numbers are runs, not vibes.** Both
  systems graded by the same harness over the same 1,990 corpus programs
  (2,906 loaded, absolute-path and nondeterministic entries excluded), CPython
  as the oracle with a pinned hash seed, 2026-08-30, pydantic-monty 0.0.21:
  lypning tier 1 answers 64.4% of the CPython-clean subset with **1** silent
  divergence (the ledgered musl `pow` ULP); Monty answers 36.9% with **23**.
  The identical both-fail counts (1,245) are the one-instrument sanity check.
- **Every published divergence was independently re-verified first** — three
  CPython runs each — and the verification *refuted two candidates*: Monty
  reproduces `hash(-1) == -2` correctly, and one set-order case agreed under a
  pinned seed. Both were dropped; the doc says so.
- **The framing is honest about the different jobs.** Monty + ADK-Rust CodeAct
  is a sandboxed in-process substrate for LLM-written tool-calling code, with
  snapshots and resource limits — nothing lypning does. lypning makes a
  harness's real `python3` spawns cheaper under a never-wrong-at-exit-0
  contract with CPython fall-through. Startup is a wash shape-for-shape
  (0.05 ms vs 0.04 ms in-process); coverage and fidelity are the axes that
  separate them.
- **Completed with the full performance picture, four instruments** (all
  2026-08-30): *startup* shape-for-shape (in-process 0.05 ms vs 0.04 ms — a
  wash; spawn 0.64 ms vs CPython's 10.33 ms); *sustained compute* over six
  workloads first validated byte-identical on every engine (nothing beats
  CPython on loops — lypning 1.9–4.5×, Monty 1.9–4.5×, MicroPython bimodal
  0.78×–23×, with callgrind instruction counts showing the wall costs are
  dispatch- and memory-bound, not instruction-bound); *end-to-end* over the 745
  CPython-clean corpus programs (chain 8.6 s with 744/745 identical and 1 ledgered ULP, CPython
  12.7 s, tier 1 alone 2.0 s at 64% coverage, Monty pool 6.8 s at 37% correct
  with 60% handed back to the model — and a model turn dwarfs every number in
  the table); *memory* (all at the ~8.6 MB spawn floor; Monty +1 MB on
  dict-heavy work). Headline claims were re-measured a second time before
  publication; the verification workflow's agents hit the harness
  parameter-stripping fault and honestly reported measuring nothing, so the
  re-check ran inline.
- The grading and performance harnesses ship at `study/monty/` (with the six
  workloads), so every table is re-runnable — and re-run is the instruction,
  per invariant 3.

**2026-08-30** — lypning is the Coding Harness Interpreter Optimizer

- **The identity changes; the architecture keeps its name.** The headline in
  the README, `docs/LYPNING.md`, the CLI description, the package and crate
  docstrings, the site title and footer, and the shipped skill now read
  *"lypning — the Coding Harness Interpreter Optimizer"*. *Mixture of Pythons*
  stays everywhere it explains the design — it is what the architecture *is* —
  including the classifier's own module doc and the *Before the name* history.
- **The new name is a claim about adaptability, and the docs now say so up
  front.** The README and `docs/LYPNING.md` intros state it directly: the
  corpus is captured from live sessions, every table is derived from it and
  re-derivable from yours, and `docs/FORKING.md` is the manual for pointing
  the optimizer at your own harness, agent and programs.
- Nothing load-bearing moves (invariant 9): engine strings, `LYPNING_*` env
  vars, `~/.lypning`, hook names and the CLI surface are all unchanged.

**2026-08-30** — The loop gathered 667 of its own programs, and the tree now
ships the instructions for pointing it at yours

- **`lypning harvest` folded one session's captures into the corpus: 2,239 →
  2,906 programs** (counts printed by the tools, 2026-08-30). The new entries —
  including the session's *own* probe programs — immediately surfaced five
  tier-1 defects, all fixed same-day: dict-view set algebra (`d.keys() | {"c"}`)
  raised where CPython computes the union, and now refuses as the escalated
  `dict-view` kind; `bytes(str, encoding)` never read the encoding's *value*,
  so `bytes('a', 'bogus')` answered `b'a'` where CPython raises `LookupError`
  and `latin-1` came back as UTF-8 bytes; `sorted([3,1], strict_mode=True)`
  silently ignored the unknown keyword where CPython raises.
- **Four more kinds join the escalation table, each measured on the mp binary
  first**: `nan-order`, `identity`, `iterator-type-name`, `encoding`, and a
  split `dunder-missing` (`__module__`/`__doc__`, which mp lacks) out of
  `dunder-attr` (`__name__`/`__class__`, which mp answers). `import random as
  r` defeated the `random.seed` marker — aliases now resolve, and
  `from random import seed` is caught at the import line.
- **The battery's nondeterminism screens grew two patterns the harvest
  demanded**: `os.path.getsize`-family (a program printing the live capture
  log's size can never match a reference taken a moment earlier) and
  `subprocess.*` (one probe spawns python3 300× with `PYTHONHASHSEED`
  deliberately removed — its own reference drifts).
- **The ledger was regenerated: 87 entries in 34 families, scorer exit 0.** The
  37 additions are the harvest re-capturing the session's documented mp
  defects on the arm that cannot be fixed here; seven new families
  (`encoding-arguments-ignored`, `iterator-type-names`, `dict-key-collapse`,
  `reversed-dict-absent`, `str-unicode-whitespace`, `repr-quote-choice`,
  `dict-view-set-algebra`) name defects the grids found this week.
- **`docs/FORKING.md` is the new deliverable**: the capture→harvest→gate→step
  loop documented as the project's standing feature — including the `/loop`
  invocation — plus complete fork-and-specialize instructions and every
  optimization in the tree classified as *universal* (keep byte for byte),
  *workload-general* (keep the mechanism, re-derive the contents with the
  named tool), or *corpus-specific* (re-measure or discard). Tier-1 arm over
  the grown corpus: **1 MISMATCH** (the ledgered musl-libm pow ULP).

**2026-08-30** — 23 of the 40 kinds in the classifier's MicroPython arm could
never reach it

- **`engine_for` runs at routing time, so the only refusal kinds that can reach
  it come from the parser, the lexer, or `Requirements::block`.** The match arm
  nonetheless listed 23 kinds only the *evaluator* emits — `set-order`, `del`,
  `json`, `round`, `percent-format` and the rest of the runtime vocabulary.
  Verified two ways: by scraping every `unsupported("…")` the parse/lex sources
  can produce, and by the routing table being bit-identical after the deletion
  (388 / 1,056 / 202, unchanged).
- **Six of the dead names contradicted `ONLY_CPYTHON_KINDS`** — one table said
  "send to lypning-mp" about kinds the other says only CPython gets right.
  Inert while dead; the day the parser learned to spot one of those constructs
  statically, the arm would have routed programs to exactly the tier the
  escalation table exists to keep them off, with no gate looking.
- **`micropython_kinds()` was written to read this arm and had zero callers.**
  It now has its job: a test holds the arm to the kinds the classifier can
  actually emit (`routing.classifier_kinds()`, scraped from the source) and to
  disjointness from the escalation table.
- Deleting the 23 string literals bought back exactly the 4,096 B the
  identity-rule work had added: the binary is back to **1,024,208 B**, 8 blocks,
  24,368 B headroom. Suite 1,271 → 1,272; battery identical.

**2026-08-30** — The element test is `x is y or x == y`, and every sequence
scan now uses it

- **`[n].count(n)` answered `0`; CPython answers `1`.** CPython's container
  protocols compare identity *before* equality, and a NaN is the one value for
  which the shortcut is observable. The rule was implemented once — as a
  whole-sequence prescan in `eq` that refused whenever a NaN was anywhere —
  while the ordering descent, `in` over lists and tuples, `min`/`max`, and the
  five scan methods (`list.count`/`.index`/`.remove`, `tuple.count`/`.index`)
  each called bare `eq` and skipped it. Seven measured programs answered
  wrongly at exit 0: `[n] <= [n]` was `False` against `True`,
  `max([[n],[n,1]])` picked the wrong element, `[n].index(n)` raised
  `ValueError` at exit 1 — the program's own exit, which the chain does not
  retry — and two *distinct* NaNs collapsed to one set element because the hash
  key was their bit pattern.
- **The rule now lives once, in `value::elem_eq`, and it is exactly as narrow
  as the ambiguity.** Both sides NaN → the question is identity, which a bare
  `f64` cannot carry → exit 90. One side NaN → they cannot be the same object
  *and* they are not equal, so `False` is CPython's own answer — which
  **recovers coverage**: `[n] == [1]`, `n in [1, 2]` and `[1, 2].count(n)` were
  refused by the old prescan and now answer. `hkey` refuses a NaN dict key or
  set member for the same reason, instead of collapsing by bits.
- **The test sits on the *unequal* exit, and that placement is the performance
  story.** A both-NaN pair always compares unequal, so only comparisons `eq`
  already rejected can need the refusal — equal elements cost nothing. Two
  earlier shapes were measured and discarded (the test in front of every
  comparison, then a whole-sequence prescan), but the wall clock on this host
  swings ±8% on identical binaries, so the deciding instrument was callgrind:
  instruction counts, which are exact. Against the pre-change build:
  list equality **−12.8%**, needle scans **−3.2%**, an equality-heavy composite
  **−3.8%**, mixed-method and whitespace workloads ±0.01% — the old
  whole-sequence NaN prescan cost more than the identity rule now does.
  Needle scans hoist the NaN half out of the loop (the needle is fixed) and
  test it only on a miss; `refuse_nan_identity` is `#[cold]` so its `format!`
  machinery stays out of the inlined loops.
- **Docs updated to match the tree.** `docs/LYPNING.md` gains the NaN-identity
  rule as the fourth deliberate refusal and describes the escalation the chain
  now performs (`ONLY_CPYTHON_KINDS`, read by both dispatchers); the README's
  dispatch diagram shows the skip. Found by a survey agent that measured 30
  divergences across a 400-cell NaN ordering grid.
- Corpus unmoved: UNSAFE 2, IDEAL 1512, tier-1 MISMATCH 1 (musl libm),
  mixture MISMATCH 1. Suite 1,261 → 1,271.

**2026-08-28** — There are two dispatchers, and the escalation rule was in one
of them

- **`lypning run -c 'print({3,1,2})'` answered `{3, 1, 2}`** where CPython
  answers `{1, 2, 3}` — at exit 0, through the binary. So did
  `x = float('nan'); print(x in [x])` (`False` against `True`) and
  `print(9007199254740993 / 3)` (`…330.5` against `…331.0`). All three are
  refused by tier 1 by name, and every kind was already in the escalation table.
- **The table was only in `engines.py`.** `engines.dispatch` is the Python
  dispatcher, which is what `lypning conformance` measures through its `mixture`
  arm. `main.rs::dispatch` is the Rust one — what `lypning run` executes and what
  `lypning bench` times — and it handed every tier-1 refusal to lypning-mp
  without looking at the kind. **The correctness gate tested a dispatcher users
  do not run, and the cost gate ran a dispatcher nothing checked.**
- **`route.rs` now owns `ONLY_CPYTHON_KINDS` and the Rust dispatcher reads it.**
  `finish` surfaces the refusal kind it already had in hand, so the chain can
  choose the next tier instead of assuming it. The Python copy is held to the
  Rust table by a test that reads it out of the source, the way
  `micropython_modules()` already does.
- **A capability gap still falls through.** `print(2**70)` is a `bigint`
  refusal and MicroPython has arbitrary-precision integers, so it must still
  reach the cheaper tier — escalating everything would be safe and slow, and the
  table's whole value is that it does not. Pinned.
- Bytes exactly unchanged at 1,024,208. Suite 1,232 → **1,261**. Corpus
  unmoved: UNSAFE 2, IDEAL 1512, mixture MISMATCH 1.
- Found by a survey workflow whose first run produced nothing — five agents did
  the analysis and all five failed a nested output schema five times over. The
  same three axes returned it on a flat one.

**2026-08-28** — The whitespace-split rule lived twice, and the copy that said
otherwise was the complete one

- **`str.rsplit(None, 2)` refused; `bytes.rsplit(None, 2)` answered.** The rule
  — leading and trailing whitespace never make an empty field, a spent
  `maxsplit` hands back the remainder verbatim, and from the far end a bounded
  `rsplit` keeps the *leading* whitespace — was implemented twice. The bytes
  copy's own comment read *"this is the only place the rule lives"*. It was not,
  and the str copy was missing a case.
- **One implementation now, `split_ws_each`, used by both.** Not `bytes_split`
  called on `s.as_bytes()`: the two whitespace sets genuinely differ — `str`
  splits on U+00A0, U+2000, U+3000, `\x1c` and `\x85`, `bytes` on ASCII only —
  so the rule is shared and the predicate is not. `rsplit` walks backwards
  instead of building a reversed copy, which the bytes version had needed.
- **Measured: bytes exactly unchanged at 1,024,208, and the corpus gained.**
  Tier-1 MATCH 1076 → **1077**, refusals 569 → **568**, IDEAL 1511 → **1512**.
  The string-methods grid went from 325 refusals to **261** — 64 programs that
  used to refuse now answer.
- **Performance, A/B against the pre-refactor binary, 25 interleaved rounds:**
  +3% on a microbenchmark that does nothing but whitespace-split, +2% on a mixed
  method workload, against a same-binary control reading −2%. Getting there took
  two attempts: decoding a character per position to ask one question cost
  **21%**, and an intermediate vector of ranges cost most of what was left.
- **It aborted the process before it did any of that.** The first version
  advanced the scan one *byte* at a time, walked into the middle of a multi-byte
  character and made `&str` slicing panic — `'café'.split()` exited 134 on a
  SIGABRT. A 728-program grid had passed it, because every subject was ASCII or
  whitespace and none held a non-space multi-byte character. The unit readers
  now return a width, which makes the mistake unrepresentable; the grid and the
  pins carry the missing characters.
- **And the forcing function the twins never had.** A parametrised differential
  runs all twelve shared method names against both `str` and `bytes` and
  requires each to match CPython — proven load-bearing by reintroducing one of
  the five drifts in a scratch build and watching it fail.

**2026-08-28** — An unused import was enough to reach a tier that answers wrongly

- **The two escalation tables did not cover the same ground.**
  `engines.ONLY_CPYTHON_REFUSALS` is the *runtime* half: it fires on a refusal
  tier 1 actually emitted. But tier 1 only runs when the classifier sends the
  program there, so a program whose **first** blocker is an ordinary capability
  gap goes straight to lypning-mp — tier 1 never refuses, the runtime table
  never sees the kind, and the tier answers at exit 0:

  ```
  import math                      # an unused import is enough
  x = float("nan")
  print(x in [x])                  # CPython True, lypning-mp False
  ```

  Same shape for `import math` plus `print(9007199254740993 / 3)`:
  `3002399751580331.0` against `3002399751580330.5`.
- **Both are visible in the source, so the static half can catch what the
  runtime half cannot.** `route.rs` now marks a `float("nan")` literal and a
  division whose literal operand is past 2\*\*53.
- **Measured cost: zero corpus programs.** The rules match the *AST*, not the
  text — and the only two corpus entries carrying that text hold it inside a
  string, a regex pattern in one and a literal being string-replaced in the
  other. A regex estimate over the source said "one program each"; it was
  counting strings.
- Found by an adversarial review of this session's diff. Its rebuttal agent was
  right that the escalation fires for the population it was built for, right
  that the routing gate would flag a corpus program of this shape as UNSAFE, and
  right that one of the three examples was invalid (a set-order comparison run
  without `PYTHONHASHSEED` pinned). The other two reproduce, and they are why
  this is a change and not a note.
- Routing unmoved: 1056 lypning / 388 lypning-mp / 202 cpython, UNSAFE 2,
  IDEAL 1511, mixture MISMATCH 1.

**2026-08-28** — A vanished arity check, and five ways a `range` was not a range

From a 39-agent grid campaign over comprehension scope, slicing, conversions,
`print`/`repr` and `range` — every finding independently reproduced before it
was acted on.

- **`[v for v, in [(1, 2)]]` printed `[(1, 2)]`; CPython raises
  `ValueError: too many values to unpack (expected 1)`.** A trailing comma after
  a *single* name makes a one-element tuple target, and `target_list` collapsed
  a one-item list back to a bare name — so no unpacking happened and the arity
  check vanished with it. A program CPython stops with an exception ran to
  completion and printed plausible wrong data. The parenthesized `(v,)` and two
  names `a, b,` were always right, which is what kept it quiet; it reached
  statement for-loops and all four comprehension forms.
- **`1.0 in range(5)` and `True in range(5)` answered `False`.** A range holds
  integers but `in` asks about *values*, and `1.0 == 1`. Matching only
  `Value::Int` missed both; a non-integral float is still `False`.
- **`range(0) == range(1, 1)` answered `False`.** Two ranges are equal when they
  describe the same *sequence*, not when their three fields match — both are
  empty, and a one-element range's step is not observable
  (`range(1) == range(0, 1, 2)`).
- **A range is hashable**, keyed on that same normalised form so equal ranges
  collapse in a set. This was `TypeError: unhashable type: 'range'` at exit 1.
- **`.start`, `.stop` and `.step`** are ordinary attributes CPython exposes and
  raised `AttributeError`; `.index` and `.count` are real methods this engine
  does not implement and raised it too. Exit 1 is the program's own exit, which
  the chain does not retry — so all five died where a refusal would have been
  answered one spawn later. The attributes now answer; the two methods refuse,
  as every other unimplemented method does. An attribute a range genuinely does
  not have still matches CPython's `AttributeError`.
- Corpus unmoved: UNSAFE 2, IDEAL 1511, mixture MISMATCH 1. 6,000 fuzz programs,
  one counterexample, and it is the known musl `libm` `pow` difference.

**2026-08-28** — Valid Python the parser does not know is a capability gap, not
a syntax error

- **`print((n := 1))` exited 1 with a `SyntaxError`.** So did `[*xs, 3]`,
  `{*s, 3}` and `x[0:1, 2]` — all four valid programs CPython runs. Exit 1 is
  the *program's* own exit, which the chain does not retry, so each simply died
  where a refusal would have been answered one spawn later.
- **The line was already drawn, in the other direction.**
  `docs/HILLCLIMB.md` iteration 14 deliberately turned `unsupported: token` into
  an exit-1 `SyntaxError` for bytes like `$` that cannot begin a token in *any*
  Python program — a SyntaxError is terminal, so spending a spawn to be told by
  CPython what lypning already knew was waste. The converse had no such care.
  Syntax the parser can **recognise and name** now refuses, exactly as `async`,
  `kwonly` and `nonlocal` already did; genuinely invalid syntax still exits 1.
- **`**` in a dict display is not swept up with it.** `{**d, 'b': 2}` is dict
  merging, and it already worked — only the `*` set-unpacking form beside it
  does not. Pinned both ways, along with `print(1 +` and `print($p)` still
  exiting 1.
- The classifier already contained all four (`route` reports `syntax` and sends
  them to CPython), so no dispatch outcome changes. What changes is the binary
  run directly, and what the conformance tier-1 arm would score if a corpus
  program ever hit one — a MISMATCH under invariant 1.

**2026-08-28** — A SIGABRT on a large range, and a correction to this morning's
NaN sort

- **`range(-2**62, 2**62)[:1]` aborted the process.** That range has 2\*\*63
  elements and the length was computed in i64, so the subtraction wrapped to
  `i64::MIN` and `slice_span`'s `clamp(0, n)` panicked on *min > max*. Exit 134,
  a SIGABRT — the one outcome the dispatcher cannot route onward, and one that
  takes an embedding host's process with it. `range_len` now computes in i128,
  which holds every length an i64 range can have.
- **Three quieter faults fell out of the same overflow.** `len()` of such a
  range answered `0` (the wrapped count, tidied by a `.max(0)`) and now raises
  CPython's own `OverflowError: Python int too large to convert to C ssize_t`.
  Indexing raised a spurious `IndexError`, and slicing built a range from
  `st * step` that had wrapped to a *negative* step, so
  `list(range(0, 4, 2)[::2**62])` answered `[]` where CPython answers `[0]`.
  Both refuse now: a range holds three i64s and cannot represent CPython's
  answer, which is `range(0, 4, 9223372036854775808)`.
- **A correction to a change made this morning.** `order` was changed to treat
  a NaN as *neither less nor greater* rather than raising, and
  `sorted([nan, 1.0])` was pinned as matching CPython. It did — for two
  elements, which is one comparison, where any consistent comparator agrees.
  It does not in general: `sorted([3, 1, float('nan'), 2])` is `[1, 2, 3, nan]`
  in CPython and was `[1, 3, nan, 2]` here.
- **Because a NaN stops the comparator being an order at all.** Every
  comparison against one is false, so *not less* holds in both directions and
  which element moves depends on the sequence of questions the algorithm asks.
  CPython's answer is timsort's, and no fix to the comparison can close that —
  so a sort over a NaN is now `unsupported: nan-order`. Answering wrongly at
  exit 0 is worse than the TypeError it used to raise, which is why this could
  not be left as it was. `min` and `max` are unaffected and stay correct: they
  are linear scans asking one question per element.
- Corpus unmoved: UNSAFE 2, IDEAL 1511, mixture MISMATCH 1.

**2026-08-28** — The bytes methods are a copy of the str methods, and the copy
had drifted in five places

A 1,938-program grid over six subjects, seventeen argument shapes and seventeen
methods. The str original is correct in every one of these; only the twin is
wrong.

- **162 divergences were a suffix nothing in CPython prints.** Every bytes
  `TypeError` read *"a bytes-like object is required, not 'str' (in
  bytes.split())"*. `str(e)` is what a program prints, so the annotation is the
  whole message as far as the caller is concerned.
- **`find`/`rfind`/`index`/`rindex`/`count` have their own wording**, because
  those five also accept a single integer byte value: *"argument should be
  integer or bytes-like object, not 'str'"*.
- **`startswith` and `endswith` did not take a tuple of prefixes** — the point
  of the method. `b'abc'.startswith((b'a',))` is `True` in CPython and was a
  `TypeError` here; `str.startswith` has taken one since it was written.
- **`b''.join(1)`** said *"'int' object is not iterable"* where CPython — and
  `str.join` four hundred lines up, with the same `map_err` — say *"can only
  join an iterable"*. `join` also now names the failing item's index and type.
- **Two were answers, not messages.** `b'abc'.split(b'')` returned `[b'abc']`
  where CPython raises `ValueError: empty separator` (`str.split('')` already
  raised it). And `in` converted with `as u8`, which **truncates**: `300 in
  b'abc'` tested byte 44 and answered `False`, `-1` tested 255. Both are a
  `ValueError` in CPython.
- Grid after: **1,938 programs, 0 divergences, 816 refusals** — the refusals are
  `bytes.count`, `.index`, `.partition` and friends, which are not implemented,
  which is a coverage number and never a defect. Corpus unmoved: UNSAFE 2,
  IDEAL 1511, mixture MISMATCH 1.

**2026-08-28** — A keyword argument could silently refill a parameter the
positionals had already filled

- **`f(1, 2, a=9)` ran the function with `a=9` and `b=2`.** CPython raises
  *"got multiple values for argument 'a'"*; the binder looked the name up and
  inserted over the top, so the body executed on data the caller never passed
  together — at exit 0, with a plausible answer. Found by a 116-program grid
  over eight signatures against fourteen call shapes. The check is on the
  parameter's *used* bit, so every legal way of filling the same parameters
  still works.
- **`def f(a, *c, d)` is the same feature as `def f(a, *, d)`, which is
  refused.** This spelling fell through and recorded `d` as an ordinary
  positional parameter, which the binder cannot represent — it derives the
  positional count as `names.len() - star - dstar` and then slices
  `names[..npos]` from the *front*, which only holds while `*args` and `**kw`
  come last. `f(1, 2, d=3)` said *"unexpected keyword argument 'd'"* and
  `f(1, 2, 3)` raised `UnboundLocalError`. Neither was a refusal, so neither
  could be answered one spawn later. Now both spellings refuse.
- **`reversed()` reversed an iterator.** CPython needs `__reversed__`, or
  `__len__` and `__getitem__` together, so `list(reversed(iter([1, 2])))` is a
  TypeError there and answered `[2, 1]` here — the rarer divergence, where the
  engine *succeeds* and CPython refuses. `reversed({1, 2})` had been refused as
  a set-order exposure, which was a spawn spent on nothing: CPython never gets
  far enough to iterate, so it now gives CPython's exact TypeError.
- **An iterator's type name is not reproducible, so messages that would print
  one refuse.** CPython has a family — `list_iterator`, `tuple_iterator`, and
  `str_ascii_iterator`, which is `str_iterator` for a non-ASCII string. Same
  reason `repr()` of an iterator already refuses.
- Grids after: arg-binding 116 programs, dict-ops 350, iteration 458,
  string-methods 1,547, unpacking 198, control-flow 103 — **0 divergences**.
  Corpus unmoved: UNSAFE 2, IDEAL 1511, mixture MISMATCH 1.

**2026-08-28** — 1,611 divergences in the comparison operators, in three shapes

A 5,460-program grid over every ordering operator across 26 operand values. All
three shapes were silent — exit 0, a plausible answer, a wrong one.

- **1,461 named the wrong operator.** Every ordering comparison derived its
  answer from a single `Ordering`, so every one of them reported `'<'`:
  `0 <= ''` said *"`'<'` not supported between instances of `'int'` and
  `'str'`"*. CPython names the operator you wrote, at every depth — a sequence
  compares element-wise and hands the *original* operator to the first
  differing pair, so `[1] <= ['a']` says `'<='`. Rewritten as CPython's
  `list_richcompare` rather than an `Ordering` plus a mapping. `sorted` still
  says `'<'`, because that is the comparison sort makes.
- **120 were a NaN short-circuit that ran before the type check.** An ordering
  over a NaN is False because IEEE 754 says the relation does not hold — but
  only between values that *have* an ordering to fail. `'' < float('nan')` is a
  TypeError in CPython and was `False` here: an exception silently turned into
  a value. The same root cause reached the other direction, where
  `sorted([nan, 1.0])`, `min` and `max` raised *"cannot order NaN"* for three
  results CPython computes.
- **30 were `is`, and no amount of computing fixes those.** CPython answers
  identity on immutables from *interning*: `0 is 0` and `'ab' is 'ab'` are True
  because the compiler folded two constants into one object, while
  `int('1000') is 1000` is False for the same values. The answer depends on
  where the value came from. Now `unsupported: identity` — and `value.rs` has
  carried the comment *"refusing beats guessing either way"* since it was
  written, while returning `false`.
- **The refusal is narrowed to the question that cannot be answered.**
  `x is None`, `is True`, `is False`, two unequal values, `[1] is [1]`, and
  `x is x` for anything carrying an `Rc` all still answer.
- Grid after: **5,460 programs, 0 divergences, 48 refusals.** The corpus is
  unmoved — 569 tier-1 refusals before and after, UNSAFE 2, mixture MISMATCH 1.

**2026-08-28** — Two silent wrong answers in the Rust core, from one grid and
one guard

- **`7.0 // 1e-308` answered `nan`; CPython answers `inf`.** The float
  floor-division path guarded on `(x / y).is_finite()` and returned `nan` when
  it was not. That is right for `float('inf') // 2.5`, which *is* `nan`, and
  wrong for a finite pair whose quotient merely overflows. CPython never looks
  at the quotient — it takes `fmod` first, and the two cases separate
  themselves there, because `fmod(inf, y)` is `nan` while `fmod(7.0, 1e-308)`
  is an ordinary small number. Removing the guard is the whole fix: Rust's
  `f64::floor` is total, so the code below it already produced both answers. A
  390-program grid over the overflow neighbourhood: **98 divergences → 0**.
- **`type(2).__name__` raised AttributeError; CPython says `int`.** A dunder is
  part of the data model, so `AttributeError` for one is not a fact about the
  program — it is a false claim about Python. And it arrives at **exit 1, the
  program's own exit, which the chain does not retry**: unlike a refusal it
  cannot be answered one spawn later, so the program simply died.
  `e.__class__.__name__` and `len.__doc__` failed the same way — three measured
  MISMATCHes on the tier-1 arm. An unimplemented `__x__` is now
  `unsupported: dunder-attr`.
- **Deliberately a wildcard, not a list.** A list of the dunders CPython has is
  incomplete the moment someone uses the next one, and incomplete here means a
  silent wrong answer where over-broad means a process spawn. `(2).__dict__`
  refuses even though CPython raises `AttributeError` for it too — one spawn,
  and then the same error the program would have got.
- **`dunder-attr` is not escalated to CPython.** MicroPython gets `__name__`
  and `__class__` right, so this is a mixed kind, and the rule set last change
  holds: where a kind is mixed, split it in the engine rather than escalate the
  whole of it.
- Same run after both: the corpus is unmoved — UNSAFE 2, IDEAL 1511, LATE 90,
  WASTED 43, mixture MISMATCH 1. Every affected program already routed to
  CPython on an unrelated blocker, which is exactly why the corpus could not
  have found either defect.

**2026-08-28** — The session-start hook reported that capture was dead while it
was running

- **It was the third hook to lose the same arm.** A hook finds the package one
  of three ways: the `lypning` console script, the source tree via
  `$CLAUDE_PROJECT_DIR/src`, or a bare `python3 -m lypning`. In a checkout of
  lypning *itself* only the middle one works — and that is the session most
  worth capturing, because it is the one editing the engine. The capture and
  harvest hooks were given that arm earlier this session; `lypning-session-start.sh`
  never had it.
- **So it announced the opposite of the truth.** Its `additionalContext` read
  *"hooks are installed but the package is not importable. Capture and routing
  are inert this session"* — into a session where capture had already logged
  **457 invocations and 334 sightings**. Invariant 5 is why nobody noticed: a
  hook never fails a session, so a broken one goes quiet rather than loud. Its
  shim refresh had been failing silently the same way, which is the half that
  actually stops the feed when a container is recycled.
- **Two tests, because the arm has now been lost three times.** One asserts every
  shipped hook carries it; the other asserts `.claude/hooks/` still matches
  `assets/claude/hooks/`, since the tree carries the hooks twice and a fix
  applied to one copy is a fix this repository runs and no user gets, or the
  reverse.

**2026-08-28** — Two more constructs the classifier declines, and a refusal kind
that was two kinds

- **UNSAFE 4 → 2.** `hashlib.algorithms_guaranteed` and the `strict_mode=`
  keyword are the two commit-barrier constructs a parser can actually see, and
  they cost **one corpus program each** to route away. The third barrier entry
  is a regex whose pattern is only a string until it is compiled, so it stays —
  and stays the live reproduction in `tests/test_routing.py`. IDEAL 1509 → 1511.
- **`bigint` was doing two jobs.** Eleven refusals share the name; ten of them
  mean *Python would use a bignum here*, and lypning-mp **is** MicroPython,
  which has arbitrary-precision integers — it answers all ten correctly. The
  eleventh, `int / int` past 2\*\*53, means *the quotient needs rounding I cannot
  do exactly*, and MicroPython does the same lossy conversion and answers
  wrongly. Escalating the shared kind sent all eleven to CPython to rescue one.
  It is now `unsupported: int-div-precision`, and only that one escalates.
- **A correction.** `ONLY_CPYTHON_REFUSALS` was documented as costing "zero
  spawns on this corpus" because every kind in it was one MicroPython never got
  right. That was true of seven of the ten and false of three — `bigint` 10,
  `set-order` 4, `repr-unicode` 1. The comment now carries the per-kind
  measurement instead of the summary: five programs get slower, nine wrong
  answers become right.

**2026-08-28** — The mixture arm was scored against a reference it did not share
an environment with

- **One arm skipped `_env_for`, and it was the arm that measures what a user
  actually runs.** Every other arm — and the CPython reference itself — is
  handed `PYTHONHASHSEED=0`, `LC_ALL=C.UTF-8` and a capture log redirected into
  the sandbox. The mixture arm called `engines.dispatch`, which had no `env`
  parameter to hand it to, so its children inherited the battery's own
  environment instead.
- **It made the battery disagree with itself.** `print(min({(1,"z"),(1,"a")},
  key=lambda t: t[0]))` returns whichever element set iteration reached first,
  and without the pinned seed CPython randomises that per process. The entry
  flipped between MATCH and MISMATCH from run to run — showing up in the
  accepted-mismatch ledger as a *regression* on one run and as *no longer
  reproduces* on the next, which is the ledger's two loudest signals firing at
  random. Ten battery runs on that program now give one verdict.
- **The locale half was the more dangerous one.** Under `LC_ALL=C` every
  non-ASCII byte decodes to U+FFFD, so two engines printing *different*
  non-ASCII compare equal — a MISMATCH scored MATCH. `engines.run` has carried
  that comment since the bug was found; the mixture arm was outside it.
- **`dispatch` and `route` now take `env` and hand it to every child**,
  including the tier reached after a fall-through. Same run, after: the mixture
  arm is at **MISMATCH 1**, and that one is the last-ULP `pow` difference in
  musl's libm.

**2026-08-28** — A correct refusal was being turned into a wrong answer by the
tier below it

- **Tier 1 refuses; tier 2 answers wrongly; the user sees the wrong answer at
  exit 0.** The fall-through assumes the next tier down is at least as correct
  as the one that refused. Measured over the corpus the run loaded (2,239
  programs, 2026-08-28): tier 1 refuses **569** programs and **25** of those are
  then answered wrongly by lypning-mp. `n=float('nan'); print(n in [n])` is the
  short one — tier 1 declines it by name (`nan-identity`), MicroPython prints
  `False`, and CPython prints `True`.
- **The grader called this WASTED**, whose definition ended *"and the chain
  still produces the right answer"*. It graded the tier that was **named**, not
  the tier that **answered**. `score_route` now walks the fall-through chain, so
  seven routes that read as spare process spawns read as what they are. UNSAFE
  went 4 → 11 on no code change: the number was wrong, not the router.
- **A refusal says why, and some reasons rule out every tier but CPython.**
  `engines.ONLY_CPYTHON_REFUSALS` names ten kinds — `nan-identity`, `bigint`,
  `set-order`, `dict-view`, `exception-chaining`, `repr-unicode`,
  `percent-format` among them — where the refusal exists *because* the
  behaviour is subtle, which is the same reason a second reimplementation gets
  it wrong. A capability gap (`decorator`, `class`, `generator`) still falls
  through one tier at a time, because escalating one of those would pay a
  CPython spawn on every occurrence and buy nothing.
- **Same run, after: UNSAFE 11 → 4 and the mixture arm's MISMATCH 8 → 3.** Five
  wrong answers gone, measured on the dispatcher and not only on the grader,
  with IDEAL (1509), LATE (90) and WASTED (43) all unmoved. The ledger loses six
  more `mixture` lines and keeps one.

**2026-08-28** — The classifier declines the tier that would get it wrong

- **UNSAFE 7 → 4, and the three it closed were closed by not routing there.**
  lypning-mp is a third-party binary whose defects cannot be fixed in this tree,
  so the only lever left is the classifier: when the *source* shows a program
  would trip on a known one, send it to CPython. `route.rs` gained a
  `MICROPYTHON_UNSAFE` table naming three constructs — `random.seed(`,
  `.__module__`, and `pathlib`'s `.parts` — each a family in
  `.github/known-mismatches.json`.
- **Construct-level, not module-level, and that is the whole design.** `random`
  without a seed is reproducible, `Path.name` is right on that tier, and `.parts`
  on an unrelated object is an ordinary attribute. Measured over the corpus the
  run loaded (2,239 programs, 2026-08-28): the three constructs move **25**
  programs to CPython, against 133 for routing all of `pathlib` away. Twenty-five
  extra spawns buys three UNSAFE, and UNSAFE is a gate where LATE is a budget.
- **It cost nothing it was supposed to cost.** Same run: IDEAL 1505 → 1509 and
  WASTED 46 → 43, because those programs' cheapest *matching* tier was CPython
  all along; LATE 88 → 90. The ledger lost its three `mixture` lines and
  `known-mismatches.py` still exits 0 — 56 observed, 56 accepted. The
  `lypning-mp` lines stay: the tier is still wrong, it is just no longer asked.

**2026-08-25** — `sorted(…, reverse=True)` was reversing the ties · [#16]

- **A silent wrong answer in the Rust core, in one of the most ordinary lines an
  agent writes.** `sorted(counts, key=lambda k: counts[k], reverse=True)` — top-N
  by frequency — returned tied keys in the wrong order at exit 0. Python's sort
  is stable descending as well as ascending, and `sort_values` implemented
  `reverse=True` as `idx.reverse()` over a finished ascending sort, which
  reverses the ties along with everything else. CPython reverses the input, sorts,
  and reverses again; so does this now.
- **Three instruments were blind to it.** The corpus was at MISMATCH 0 over this
  bug for its whole life and is still at 0 after the fix; `perf` measures time,
  and a wrong answer arrives just as fast; the fuzzer generates random keys,
  which are mostly distinct. The defect is invisible without ties — and 67 corpus
  programs contain a keyed sort while **none of them diverge**, because 46 use
  tuple keys with an explicit tiebreaker.
- Pinned as six cases and as a grid over key functions with 1, 2, 3 and 5
  distinct values across lengths 0–13, cross-checking `sorted` against
  `list.sort`. Both were **run against the broken binary first** — 5 failures
  there, 0 after. Two seed corpus entries cover the idiom going forward.
- **`key=None` raised TypeError and `reverse=None` was obeyed** — the same
  mistake from both sides, at all four call sites (`sorted`, `list.sort`, `min`,
  `max`). `None` is the default for `key=`, and it is how an optional key gets
  spelled, so `sorted(xs, key=chooser)` with a `None` chooser died at exit 1 —
  which the dispatcher does not treat as a refusal, so nothing rescued it.
  `reverse=` goes through `__index__` in CPython; read for truthiness it turned
  a TypeError into an ascending sort at exit 0. Both pinned, and both checked
  against the broken binary first.
- **Keyword arguments were silently ignored across the builtins and container
  methods.** `'xax'.strip(chars='x')` returned `'xax'`, `'a'.ljust(width=5)`
  returned `'a'`, `{'a':1}.get('b', default=2)` returned `None`, `bool(x=1)`
  returned `False`, `int(x='5')` returned `0` — all at exit 0, all a TypeError in
  CPython. The allow-lists of which parameters may be named are now enumerated by
  asking CPython 3.11, and everything else raises with its exact wording.
- **The half-wired half was worse.** `str.split` read `maxsplit=` and not `sep=`,
  so `'a,b'.split(sep=',')` split on whitespace and answered `['a,b']`;
  `sum(xs, start=10)` ignored the start and summed from zero;
  `round(2.5, None)` and `round(number=2.5)` raised where CPython answers.
- **`int(s, 0)` aborted the interpreter.** `i64::from_str_radix` panics outside
  radix 2–36, so every out-of-range base exited **134** — neither 0 nor 90, so
  the dispatcher hands the Rust abort straight back to the caller. Base 0 is now
  implemented, leading-zero rule included, and verified as a 252-cell grid.
- Pinned as a 60-cell keyword grid comparing values *and* messages. Binary
  995,528 B, still 8 blocks; every arm's MISMATCH count and the whole routing
  table unchanged.
- **Malformed calls were answered instead of raising.** Extra positional
  arguments were dropped and missing ones defaulted, so `'ab'.strip('a','b')`
  returned `'b'`, `len([1],[2])` returned `1`, `chr(65,66)` returned `'A'`,
  `divmod(1,2,3)` returned `(0,1)` — nineteen cases, all at exit 0 — and
  `[1].insert(0)` put a `None` **into the list**. Arity tables now bound both
  ends, with the floor counting positionals only so `round(number=2.5)` still
  works. Fifty-five error-message *wordings* still differ from CPython's and are
  recorded rather than fixed: both interpreters raise, which is the part that
  matters.
- **`bytes` was the unswept side, and six defects lived there.** `bytes.rsplit`
  was `split` under a different name — `from_right` reached the splitter and was
  discarded; the whitespace set omitted `\x0b`, which Rust excludes and Python
  counts; whitespace splitting with `maxsplit` was wrong at both ends;
  `find`/`startswith`/`endswith` ignored `start` and `end`; and `hex(sep)`
  dropped the separator. Gridded at 1,342 cells. The `str` side was gridded the
  same way and is clean — it *refuses* the one case bytes got wrong silently.
- **Six more, each its own root cause.** `9.0 // 0.7` answered `11.0` (CPython
  corrects the floor when the discarded fraction exceeds half a unit — 623-cell
  grid); `True | False` answered `1` (bool overrides three bitwise operators, and
  only when both operands are bool); `list.index`/`tuple.index` ignored `start`
  and `stop`; `zip(a, b, strict=True)` **silently removed the guard it was asked
  to enforce** and is now refused; and `max({-1,1}, key=abs)` leaked this
  engine's set order, which the existing set-order guard did not cover — refused
  now only when a tie actually occurs, so `sorted(s, key=len)` still answers.
- Found by a six-lens fan-out at the Rust core that returned **32 verified silent
  wrong answers**, each adversarially re-run and minimised by a second agent.
  Twelve fixed; the remaining twenty are enumerated in `docs/HILLCLIMB.md` rather
  than half-done.
- **`try/except/else` ran the else clause on `break` and on `continue`.** The
  clause runs only when the body falls off the end — `break`, `continue` and
  `return` all leave without reaching it. Any flow at all used to run it, so
  `while True: try: break / else: print(...)` printed, and a `continue` printed
  once per iteration. Side effects are the ordinary reason to write an else
  clause, so this executed arbitrarily much code CPython does not. `finally` was
  already right and still runs on every path.
- **A bare `raise` inside a handler could not re-raise** — it answered
  `RuntimeError: No active exception to reraise`, which is correct only outside
  one. The interpreter now keeps a stack of the exceptions its enclosing handlers
  are handling, so a try/except nested inside a handler does not lose the outer.
- **`KeyError` was quoted at one construction site and not the other**, so
  `str(KeyError('f'))` was `f` while `repr()` of a lookup KeyError was
  `KeyError("'k'")`. Both now carry the key's repr.
- Pinned as an 18-case control-flow grid that includes the well-formed paths, so
  a fix that simply stopped running the else clause fails it. Binary unchanged at
  1,003,720 B.
- **Slicing and indexing swept as a 10,990-cell grid: zero silent wrong
  answers.** Ten receivers against every combination of start, stop and step. On
  the highest-traffic surface after `print`, the existing implementation is
  exactly CPython — recorded because it is the first sweep in five iterations to
  find none. Two real gaps did surface: `range` could not be SLICED at all
  (indexing worked; slicing raised a TypeError at exit 1, which the dispatcher
  does not treat as a refusal, so nothing rescued a construct CPython answers),
  and two index-error messages named types this subset does not have —
  `bytes` said "bytearray".
- **300 of 1,026 answerable format specs disagreed with CPython**, found by
  gridding the whole mini-language cross-product one program per spec. Six root
  causes: the `0` flag must set the FILL even when an alignment is given
  (`format(5, '<04')` was `'5   '`); zero padding is group-aware
  (`format(5, '09,')` was `'000000005'`); `,` and `_` were ignored for `g` and
  `%`; a precision with an empty presentation type was ignored, so
  `format(123456.789, '.4')` answered the whole repr; a precision on an integer
  type is a ValueError and was ignored; and `#` with a zero precision and
  grouping put the decimal point after the leading digit.
- **The `%` operator does not share the integer-precision rule** — `'%.2d' % 5`
  is `'05'`, a minimum digit count the mini-language cannot spell — so
  `format_value` and `format_value_pct` are now two entry points over one body.
  Two existing pins caught the conflation in the minute it was written.
- **Nested replacement fields took the wrong argument.**
  `"{:.{}f}".format(3.14159, 2)` raised and `"{:{}}".format(3.0, 5)` answered
  `'3e+00'`: the spec was expanded before the outer field claimed its argument,
  and the recursion restarted the auto-numbering counter. Explicit numbering was
  always correct, which is why it survived every hand-written example.
- **`round(5.0, -1)` answered `10.0`** — Rust breaks ties away from zero where
  Python breaks them to even, and only the negative-ndigits branch lacked the
  correction. `round(int, -n)` is now implemented rather than refused, in integer
  arithmetic so that ints past 2**53 keep their digits.
- All of the above at **1,007,816 B, 8 blocks — the binary did not grow.**
- **The operator matrix found four more.** `True in b"ab"` raised where CPython
  answers False — the `bool`-is-a-subclass-of-`int` slip already pinned for
  `bytes.find(False)` and never looked at for `in`; `{1} in {1}` raised where
  CPython converts the set to a frozenset and answers False; `'ab' % [1]` raised
  because the leftover-argument check exempted only `dict`, where CPython exempts
  anything that subscripts; and `b"%d" % 5` — PEP 461 bytes formatting, not
  implemented — became a **TypeError** rather than a refusal, so valid Python died
  at exit 1 with nothing to rescue it. 2,040 cells, now 0 differ.
- **Three lenses swept clean and recorded as such**: laziness and side-effect
  ordering (generator expressions, `map`, `filter`, `zip`, `enumerate` all lazy;
  `sum` eager; `any`/`all` short-circuiting identically), dict and set detail
  (33 cases including equal-key collapse), and `repr`/`str` (120 cases, 110 run,
  10 correctly refused, 0 differ).
- **The tier-1 arm is down to one mismatch, and that one is not ours** —
  `1.797e308 ** 0.5` off by a single ulp, measured against the library arm and
  proved to be musl libm against glibc libm, the same source compiled twice.
- **Dict views never reached the mutation guard that already existed.**
  `for k in g.keys(): del g[k]` emptied the dict and answered normally where
  CPython raises RuntimeError. A bare `for k in g` was guarded; the three views
  snapshotted into a plain vector and threw the dict away, leaving nothing to
  compare against. Both paths now build the same guarded iterator.
- **Every exception reported the wrong class.** `type_name` answered
  `"Exception"` for all twenty-four exception classes, so
  `'Exception' object has no attribute …` and
  `unsupported operand type(s) for +: 'Exception' and 'int'` both named a type
  the program had not used.
- **`e.__context__` claimed not to exist**, which is a claim about Python rather
  than about the program — and AttributeError is exit 1, so a handler inspecting
  the context died instead of being answered one spawn later. Refused now.
- **The accepted-mismatch ledger was rebuilt from measurement.**
  `.github/known-mismatches.json` named twelve; the arms report **fifty-nine**
  (48 lypning-mp, 10 mixture, 1 lypning), because the corpus grew 44% in a day
  and nobody had re-derived it. Every entry now carries a root-cause **family**
  and a reason: 59 entries, **27 families, 0 unclassified**, and the scorer exits
  0 — every mismatch is one the ledger names and every one it names still
  reproduces. Fifty-nine lines read as fifty-nine problems and they are
  twenty-seven; grouping says which would close together, and makes a fixed
  family a block of lines to delete rather than an unexplained drop in a number.
- A survey of **lypning-mp** verified 34 further divergences, recorded in
  `docs/HILLCLIMB.md` rather than fixed: that tier's sort is genuinely unstable,
  `round(2.5, 0)` is `3.0`, `isinstance(True, int)` is `False`, `json.loads`
  ignores its hooks, and `Path('/a/b').parts` drops the root. None fires through
  the dispatcher today, which is a fact about the corpus rather than about the
  tier.

**2026-08-25** — The classifier can see `os.path` · [#15]

- **`os.path.basename` routed to CPython for a function the engine has always
  had.** The module check in `route.rs` resolved only a bare name for a base, so
  every dotted path one level deeper fell into the method table and was blocked
  as `method: .basename()`. Fourteen `os.path` functions were invisible to the
  classifier that way. `resolve_module` now walks the path a step at a time, and
  a step counts only when it lands on a module — so `os.environ.get` stays a
  method, which it is.
- Measured over 1305 graded programs: **IDEAL 1190 → 1204, LATE 83 → 69**, and
  **14 programs stopped paying a CPython spawn** (12 to lypning, 2 to
  lypning-mp). WASTED, UNSAFE and every arm's MISMATCH count were unchanged, and
  the binary is identical to the byte — routing is parse-time.
- The cost this closes is not the spawn. `lypning route` is what the skill tells
  an agent to trust, and the prompting study watched agents replace working
  `os.path.splitext` calls with hand-rolled `rfind` to satisfy a tier that had
  already run them.
- `docs/LYPNING.md` §4's account of the fourth UNSAFE route was **wrong and is
  corrected**: `py-9b16a7261b96` dies at exit 1 with a traceback on
  `type(e).__module__`, not at exit 0 with wrong output.
  `.github/known-mismatches.json` had it right.
- **LATE counted 19 programs that were routed correctly.** A program that does
  not parse has an empty stdout and a non-zero exit on every tier, so each
  scored MATCH for producing nothing and the cheapest was graded the ideal
  destination for a program none of them can run — the difference is CPython's
  message, which lives on stderr and is not compared. The grader now skips a
  tier whose match was a shared failure, on `syntax` routes only and only when
  that tier exited non-zero; a tier that exited 0 with real output answered, and
  a classifier calling *that* a syntax error stays visible as LATE. Routing
  reads **IDEAL 1223, LATE 50** over 1305 graded programs, with correct-on-first-
  try unchanged at 97.5% — those programs always reached the right answer on the
  first spawn.
- **`decorator` and `generator` were listed as constructs no MicroPython-derived
  runtime has.** It has both, in the language. Ten more programs stopped paying a
  CPython spawn, and **WASTED did not move by one** — the imports are checked
  before the blocker kind, so `@functools.lru_cache` is still decided by
  `import functools`. `async` stays CPython-only for the opposite reason to the
  one recorded: `async def` parses there, but `asyncio` does not exist.
- Across the three routing changes: **IDEAL 1190 → 1233, LATE 83 → 40, programs
  routed to CPython 132 → 108**, at zero bytes — the binary is identical
  throughout — and with every arm's MISMATCH count unchanged.

**2026-08-25** — `%`-formatting agrees with CPython, and the numbers are re-measured · [#14]

- **The `%` conversion grid goes to zero.** A grid over conversion × flags ×
  width × precision × value reported **8,346 differing cells of 29,100**; it now
  reports none. The spec was assembled in the wrong order (`+0f` is valid,
  `0+f` is a ValueError, and the zero-pad flag was emitted first as if it were an
  alignment), `%5s` leaned left where CPython leans right, the `0x` prefix landed
  after the zero fill instead of before it, `#` dropped the decimal point it
  exists to keep, and `%c` was wrong four ways at once — including refusing
  `'%c' % 'a'`.
- `%.Nd` — minimum digits, which `format()` cannot spell — is **refused**, and
  only for the values where the precision actually adds digits. `'%.2d' % 42`
  still answers.
- **Every performance figure in `README.md` and `docs/LYPNING.md` §1, §2, §4 and
  §8 was re-measured** on 2026-08-25 against a corpus of 1551 programs, with all
  three engines built. The mixture answers 1305 of 1305 at **0.302x** of
  CPython's cost, a 69.8% saving.
- **The MicroPython tier was built for the first time in this tree, and it is
  red** — 11 mismatches, four of them the known commit-barrier defect. Six of the
  rest arrived because the *corpus* grew: last session's differential probes for
  the Rust core were harvested into it, and they find the same defect families in
  lypning-mp. Enumerated by identity in `.github/known-mismatches.json`, which
  the scorer now passes.

**2026-08-24** — The allocator, and fifty silent wrong answers · [#13]

- **The allocator was the workload.** Callgrind said 43.9% of instructions on a
  hot loop were inside musl's mallocng. A size-classed free-list allocator over
  bump-allocated chunks replaces it for the binary only — never for the C ABI,
  which must not impose an allocator on its host. `perf` TOTAL fell by a third;
  `str-concat`, blamed on a quadratic copy for this project's whole history, was
  really 32,104 `mmap`/`munmap` calls and is now 13x faster.
- **Static rather than static-PIE**, which is one CheerpX device block; boxing
  the error payload, which is 45 KB more and made every `R<T>` return in
  registers. The binary is smaller than it started and 8 blocks either way.
- **About fifty wrong answers at exit 0**, none of which any gate could see:
  Python's whitespace is not Rust's, `splitlines` splits on eleven boundaries,
  `str.count` ignored its bounds, six methods clamped a `start` CPython does not,
  all six case methods disagreed, and `json.loads` **answered malformed
  documents**. Each is pinned against live CPython, and the string bounds are
  pinned as a 44,352-cell grid rather than a list — a list is what failed to find
  the bug the first time.
- One MISMATCH is left open and named rather than papered over: the case methods
  still differ on 55 codepoints because CPython 3.11 ships Unicode 14.0 and the
  Rust toolchain ships a later one. `docs/HILLCLIMB.md` proposes the branch.

**2026-08-24** — Every markdown file the docs cite opens as rendered · [#12]

- `docs/PROMPTING.md` and `docs/HILLCLIMB.md` are in README §9's table and were
  cited across the docs, and the site published neither. Both now have a page,
  and `site/build.py` refuses to build if any `docs/*.md` has none.
- A backtick citation of markdown the site does *not* publish — a study prompt,
  a skill, an asset README — now resolves to the blob view that renders it.
  Upstream provenance paths stay plain code: they are not in this tree to open,
  and a citation that 404s is worse than one that does not move.
- `site/build.py --check` grew the assertion that catches this class of failure:
  an unlinked citation is not a dead link, it is grey text, so `check_links`
  could never see it. Bare names like `SKILL.md` are held to it too.

**2026-08-23** — Split the MicroPython gate, and fix what it turned out to hide · [#11]

- The job could not build for four consecutive runs and rendered identically
  whether the tier had answered a program wrongly or `musl.libc.org` had stopped
  answering. It is now two: **does it build**, which is blocking and can only
  redden when every precondition held and the build still produced no binary;
  and **does it agree with CPython**, which runs on the built binaries and
  *skips*, visibly, when there are none.
- `BuildResult.unavailable` splits a precondition this machine does not meet
  from a build that ran and broke — until now both printed `FAILED` and exited
  1. A failed download is classified by the fetcher's own exit code, not by a
  second network probe: the failure that started this (`curl: (35) Recv
  failure`) happens while a TCP connect to the same host still succeeds.
  `lypning build --skip-unavailable` is what a gate uses to tell them apart.
- Accepted mismatches are enumerated by **identity** in
  `.github/known-mismatches.json`, never by count: a count lets one defect be
  fixed while another appears and keeps the tick green. Measured with the tier
  built over the 1430 programs then loaded — the commit barrier, three
  self-referential entries that `import lypning`, and one that was not a
  refusal at all.
- That last one: **`base64` was not validating.** `validate=True` was accepted
  and ignored, so `b64decode(b"a!Gk=", validate=True)` returned `b'hi'` where
  CPython raises, and `b64encode("hi")` encoded a `str` the same way. Not a
  message difference — the tier answering "is this valid base64" wrongly, on a
  module the classifier routes to on sight, and invisible to the `core` job
  because the tier is absent there.
- `lib/base64.py` gains `_scan`, a transcription of CPython's error detection
  **only** — the decode stays on the C function, so the happy path is still one
  C call. It closes the message text too. Checked by brute force rather than by
  reading the C: every string up to length 6 over ``aG=!\n-`` plus 60,000
  random ones, **0 divergences**, and all eleven `base64` cases run against a
  built lypning-mp. `Discontinuous padding not allowed` was found that way and
  by nothing else. The strict messages are CPython 3.11's; the case says so.
- **The `binascii` model in `tests/test_shims.py` was wrong** in the direction
  that hides work — it wrapped CPython's decoder and lower-cased the message,
  modelling a divergence that does not exist while hiding one that does. Now a
  transcription of `extmod/modbinascii.c`. The tier grew 1,216 B, still 3
  device blocks.

**2026-08-23** — `base64.b64decode` raises the class CPython raises · [#9]

- The MicroPython tier's shim raised a bare `ValueError` where CPython raises
  `binascii.Error`, so `type(e).__name__` disagreed. Found by a harvested corpus
  entry that prints it — and it surfaced as an **UNSAFE route**, not a MISMATCH,
  because the classifier had already sent the program to that tier.
- `Error(ValueError)` with `__module__ = "binascii"` in the class body, so the
  qualified name agrees too. Verified on a real MicroPython 1.22.1 rather than
  reasoned about: the shipped shim raises `binascii.Error` there.
- `tests/test_shims.py` could not have caught it: the shim run imported
  CPython's `binascii` and got `Error` for free. It now models MicroPython's,
  whose defining feature is an **absence**.
- Also recorded, and *not* fixable from a shim: **MicroPython builtin types have
  no `__module__`**. `TypeError.__module__` is `'builtins'` on CPython and an
  `AttributeError` there. That is what makes one corpus entry exit 1, and it is
  a MISMATCH on that tier until the runtime grows the attribute.

**2026-08-23** — How far a prompt can push an agent into the subset · [#10]

- Nine prompt treatments × 3–4 independent agents × 26 deterministic tasks:
  **884 generated programs**, all kept, all routed by lypning's own parser and
  run against CPython. **66.3% → 88.5%**, and 88.5% *is the ceiling* — three
  tasks are outside the subset for any natural solution. Six treatments answer
  100% of what is feasible. **0 MISMATCH, 0 wrong answers.**
- **The cheapest saturating prompt carries no feature list**: 744 bytes of
  motive ties the generated capability tables, the rewrite cookbook, both, and
  both plus the engine in a verify loop. The one-sentence nudge is the least
  reproducible prompt measured — 11.5 pp between replicates, against 0.0 pp for
  every saturating one.
- Cost beats coverage: the mixture's bill over the same tasks falls **0.470x →
  0.169x** of CPython, because a program that leaves the subset costs a wasted
  classification *plus* a full spawn. The price is ~1.4 lines per program.
- `SKILL.md` scored **81.7%** — second-weakest of the nine — so it gains a §1a
  on writing *for* the subset rather than working *on* lypning. Not measured,
  and says so; `study/prompts/skill.md` keeps the text T3 actually scored.
- **Every one of the classifier's false negatives was `os.path`**: 35 of 884
  sent past tier 1 that tier 1 then ran correctly, all `.getsize()`,
  `.splitext()` and `.basename()`. `walk_expr` resolves a module attribute only
  when the base is a bare name. Not fixed here — it would invalidate the
  study's own measurements — and written up in `docs/LYPNING.md` §4, because an
  agent told to trust `lypning route` rewrites working code to satisfy it.
- **Using lypning as a library is invisible to lypning's capture.** Both feeds
  watch for a process; `lypning_run()` spawns none. Written up in
  `docs/CAPTURE.md`; `study/hosts/capture.h` is the forty-line workaround and
  argues the fix belongs in the C ABI.
- All five hosts — C, C++, Rust, Node, Python — driven over one shared set of
  393 programs and **agreeing byte for byte, refusal path included**. Corpus
  1037 → 1430.
- ⚠️ That fold moved conformance's tier-1 coverage 61.4% → 69.9% with the engine
  unchanged. Those points are this study's own output; exclude
  `tests/corpus/sightings/lypning-prompting-study.jsonl` before quoting corpus
  coverage as a field number.
- Re-scored against the merged engine after [#7]: **not one of the 884 rows
  moved.**

**2026-08-23** — A hillclimb loop, the instrument it needs, and six defects it
found · [#7]

- `lypning perf` — a per-construct diagnostic ranked by **ratio × how much of
  the corpus types the construct**, which is not the same list as ratio alone.
- `for line in sys.stdin` was **quadratic** in the size of stdin: 22,422 ms →
  21 ms at 50,000 lines. No gate could see it — 19 corpus entries carry a stdin
  sample and the largest is 38 bytes.
- Six correctness defects, five silent at exit 0: `isinstance` on an exception
  disagreed with CPython five ways; `except OSError` missed an `IOError`; and
  `lypning run` printed *nothing* where CPython printed the answer, whenever a
  program read stdin and then refused.
- `$`, `` ` ``, `?` and a bare `!` are a `SyntaxError` rather than a refusal —
  5 programs from UNSUPPORTED to MATCH, for zero bytes.
- Binary unchanged at 1,045,176 B across every commit. Corpus 842 → 1037.
- `.claude/skills/hillclimb/SKILL.md` and `docs/HILLCLIMB.md` are the loop and
  its ledger, including the four steps that did not work.

**2026-08-21** — The documentation leads with a measurement taken today · [#6]

- `README.md` and `docs/LYPNING.md` opened on an upstream table whose headline
  claim had already failed to reproduce twice. Both now open on a run taken in
  this tree, with the reversal stated where the claim used to be.
- A logo — the thundercloud for the lightning the name came from — and the
  dataflow drawn: shim or hook, the classifier, the three tiers.
- Every `docs/*.html` link on the landing page pointed at a GitHub 404. The
  link check skipped them because it skips absolute URLs; it does not now.

**2026-08-21** — Embeddable: a C ABI, and five hosts over it · [#5]

- `lypning build --lib` produces `liblypning.so`/`.a` and headers, so a harness
  can run a program **in its own process**. On the programs lypning accepts
  that removes the spawn, and the spawn was 96% of a one-liner's cost.
- One crate, lib + bin: `main.rs` and `capi.rs` are both consumers of the same
  `embed::run`, because a second implementation of the refusal contract is how
  a MISMATCH reaches a release.
- Nine ways an embedded program could kill its host, closed — unguarded value
  recursion in `==`, `<`, `in`, `sorted`, tuple dict keys and `json.dumps`; a
  long flat `1+1+1+…` spine; `"a" * (10**14)`; a NUL byte in the source.
- Two of those were wrong *answers* rather than crashes: a `break` in a
  `finally` swallowed a refusal, and `os.mkdir` reported a run as reversible
  when it was not.

**2026-08-20** — A case declares which CPython its oracle must be · [#4]

- CI went red on 3.9 and 3.10 with four failures never seen locally: the floor
  the package advertises had never been run before it was advertised. These
  suites use a live CPython as the oracle, and four cases ask a question an
  older one cannot express. A case now names its minimum and is skipped below
  it, rather than deleted.

**2026-08-20** — The site publishes itself · [#2], [#3]

- A Pages workflow that enables Pages, and — when that turned out to need
  repository-admin rights a `GITHUB_TOKEN` does not have — the one-time manual
  step written down, with the build left loudly red until someone does it.

---

## 0.1.0 — 2026-08-20 · the extraction · [#1]

First release. The two runtimes lifted out of [DeepResearch.se][ds], where they
were entangled with its npm scripts, its `tests/corpus/`, its `.claude/` wiring
and its shell scripts, and turned into a standalone installable package.

- **The three tiers and the router**: the Rust subset, the MicroPython variant
  with its frozen shim stdlib, and CPython — with a classifier that asks the
  Rust core's own parser which tier can take a program, and a dispatcher that
  falls onward on exit `90` and on nothing else.
- **`lypning` is an interpreter**: `-c PROG`, `FILE` and `-` exec straight into
  the Rust core, so anything that calls `python3` can call this instead.
- **A CLI**: `run`, `route`, `build`, `status`, `doctor`, `install`,
  `uninstall`, `shim`, `hook`, `conformance`, `fuzz`, `bench`, `corpus-time`,
  `gate`, `harvest`, `corpus` — every one with `--json`.
- **The corpus**, 839 harvested and seeded programs, moved into package assets.
- **Renamed throughout** — see the table below.
- **Zero runtime dependencies**, enforced by a test rather than a rule.

---

## Tracked defects

Recorded here rather than waived, because widening a capability table to make a
number green converts a loud failure into a silent one. `README.md` §5 and
`.github/workflows/ci.yml` both point at this section.

- **`lypning conformance` does not end at 0 on the `lypning-mp` arm**, and the
  largest class is one defect: MicroPython streams stdout, so a program that
  prints before reaching an unsupported construct has already committed those
  bytes when it exits 90. The Rust core stages output and discards it on
  refusal; the MicroPython tier cannot, and the dispatcher covers for it.
  Reproduction in `docs/LYPNING.md` §6. Blocking again once that tier grows a
  commit barrier.

  The count is **not** the 2 this file used to state. Measured 2026-08-23 with
  the tier actually built, **over the 1037-program corpus of that hour**:
  MISMATCH 8, UNSAFE 3. Three of those are the barrier defect above; the rest
  are gaps this tier had never been run against, exposed by a corpus that had
  grown 842 → 1037 the same day. It is **1430 now** ([#10]), so that pair is
  already a reading about a smaller corpus and not a current fact.
  `tests/test_routing.py` pins `contract:` as the only shape an UNSAFE route may
  take, so one that goes wrong any other way fails there rather than joining a
  count. Re-measure before quoting either number — and note that CI's
  MicroPython job builds that tier over the network and often cannot, in which
  case it reports nothing at all rather than a number.
- **`float ** float` is one ULP off on some arguments.** Both engines call
  their libm's `pow`; the core is static musl and the reference here is glibc,
  and glibc's is correctly rounded on these where musl's is not. `lypning fuzz`
  reproduces it at seed 1817614320. Closing it means a correctly-rounded `pow`
  or refusing float `**` — both decisions, not fixes.
- **`repr(float)` breaks an exact shortest-repr tie the other way.** CPython's
  dtoa rounds the last digit to even; Rust's rounds up. 0 of 2996 random
  doubles differ; 2 of 10 hand-picked ties do.

---

## Before the name

lypning was built inside [DeepResearch.se][ds] — a privacy-research platform
whose agent runs shell commands in an **in-browser CheerpX Linux VM**. That
sandbox is the reason this project exists: its root filesystem streams block by
block over a WebSocket, so `python3 --version` costs **8,573 ms cold** against
87 ms warm, and the exec ceiling is 30 s. Cost there tracks bytes and file
opens, nothing else — which is the cost model both runtimes are still optimised
against today.

Two components were built for it, and both were renamed on extraction:

| upstream | here | what it is |
|---|---|---|
| `pygram` | `lypning-mp` | the MicroPython variant with the frozen shim stdlib |
| `mopy` | `lypning` | the Rust subset, and the Mixture-of-Pythons router |

`mopy` was short for *Mixture of Pythons*, which is still what the design is
called. The current name comes from lightning; `docs/logo.svg` is the
thundercloud that says so.

**2026-08-20** — Both runtimes close their contract holes, then generate the
cases the corpus never covered · [PR #485][u485]. The last upstream work on
either; the extraction happened the same day.

**2026-08-19** — A differential fuzzer over the Rust subset's own declared
subset, and the 105 gaps it found. It still ships, as `lypning fuzz`.

**2026-08-16** — **`mopy` arrives** · [PR #478][u478] — a Rust Python subset
and the Mixture-of-Pythons router. Re-measured against a corpus that grew from
420 to 472 programs within the day · [PR #481][u481]; that pair of numbers is
why no document in this repository quotes a remembered corpus size.

**2026-08-15** — The compiler-optimisation lane closed with measurements rather
than opinions · [PR #453][u453]. The corpus becomes per-session and survives the
container · [PR #451][u451], [#460][u460].

**2026-08-14** — **`pygram` lands** · [PR #432][u432] — a 390 KB Python for the
sandbox that opens zero files. Then 22% off the binary, every change measured
· [PR #434][u434]; a **stock MicroPython control** so an optimisation can be
judged at all · [PR #435][u435]; and the first measurement in a real VM, where
the frozen stdlib streams zero bytes and CPython wedges · [PR #444][u444].

**2026-08-13** — `pygram` begins, and the first commit contains **no
interpreter**: a charter, a subset spec, a conformance runner, a size gate and a
seed corpus — 1,856 lines of instrument before a line of the thing it measures.
The exit-`90` refusal contract and the MATCH / UNSUPPORTED / MISMATCH split,
which are invariants 1 and 2 today, are specified there on day one. MicroPython
is picked on evidence two commits later and a daemon design dropped on the same
evidence; `docs/RESEARCH.md` is that survey.

**2026-07-24** — `python3 --version` is measured at **8,573 ms cold** against
87 ms warm inside the sandbox, and written down. Three weeks before either
runtime exists — the number came first, and both were built for it. It is
`docs/SANDBOX-PERFORMANCE.md` today.

**2026-07-04** — DeepResearch.se's first commit.

---

[#25]: https://github.com/kristerhedfors/lypning/pull/25
[#26]: https://github.com/kristerhedfors/lypning/pull/26
[#27]: https://github.com/kristerhedfors/lypning/pull/27
[#28]: https://github.com/kristerhedfors/lypning/pull/28
[#32]: https://github.com/kristerhedfors/lypning/pull/32
[#33]: https://github.com/kristerhedfors/lypning/pull/33
[#34]: https://github.com/kristerhedfors/lypning/pull/34
[#35]: https://github.com/kristerhedfors/lypning/pull/35
[#36]: https://github.com/kristerhedfors/lypning/pull/36
[#37]: https://github.com/kristerhedfors/lypning/pull/37
[#38]: https://github.com/kristerhedfors/lypning/pull/38
[#39]: https://github.com/kristerhedfors/lypning/pull/39
[#41]: https://github.com/kristerhedfors/lypning/pull/41
[#42]: https://github.com/kristerhedfors/lypning/pull/42
[#47]: https://github.com/kristerhedfors/lypning/pull/47
[#49]: https://github.com/kristerhedfors/lypning/pull/49
[#52]: https://github.com/kristerhedfors/lypning/pull/52
[#53]: https://github.com/kristerhedfors/lypning/pull/53
[#54]: https://github.com/kristerhedfors/lypning/pull/54
[#56]: https://github.com/kristerhedfors/lypning/pull/56
[7dc0d26]: https://github.com/kristerhedfors/lypning/commit/7dc0d26
[6a77a18]: https://github.com/kristerhedfors/lypning/commit/6a77a18
[4aa8209]: https://github.com/kristerhedfors/lypning/commit/4aa8209
[49c8aa2]: https://github.com/kristerhedfors/lypning/commit/49c8aa2
[#1]: https://github.com/kristerhedfors/lypning/pull/1
[#2]: https://github.com/kristerhedfors/lypning/pull/2
[#3]: https://github.com/kristerhedfors/lypning/pull/3
[#4]: https://github.com/kristerhedfors/lypning/pull/4
[#5]: https://github.com/kristerhedfors/lypning/pull/5
[#6]: https://github.com/kristerhedfors/lypning/pull/6
[#7]: https://github.com/kristerhedfors/lypning/pull/7
[#9]: https://github.com/kristerhedfors/lypning/pull/9

[#10]: https://github.com/kristerhedfors/lypning/pull/10
[#11]: https://github.com/kristerhedfors/lypning/pull/11
[#12]: https://github.com/kristerhedfors/lypning/pull/12
[#13]: https://github.com/kristerhedfors/lypning/pull/13
[#14]: https://github.com/kristerhedfors/lypning/pull/14
[#15]: https://github.com/kristerhedfors/lypning/pull/15
[#16]: https://github.com/kristerhedfors/lypning/pull/16
[#24]: https://github.com/kristerhedfors/lypning/pull/24
[ds]: https://github.com/kristerhedfors/deepresearch.se
[u432]: https://github.com/kristerhedfors/deepresearch.se/pull/432
[u434]: https://github.com/kristerhedfors/deepresearch.se/pull/434
[u435]: https://github.com/kristerhedfors/deepresearch.se/pull/435
[u444]: https://github.com/kristerhedfors/deepresearch.se/pull/444
[u451]: https://github.com/kristerhedfors/deepresearch.se/pull/451
[u453]: https://github.com/kristerhedfors/deepresearch.se/pull/453
[u460]: https://github.com/kristerhedfors/deepresearch.se/pull/460
[u478]: https://github.com/kristerhedfors/deepresearch.se/pull/478
[u481]: https://github.com/kristerhedfors/deepresearch.se/pull/481
[u485]: https://github.com/kristerhedfors/deepresearch.se/pull/485
