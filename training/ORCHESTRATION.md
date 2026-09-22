# Question → answer → verified repair → training

Owner: **this Codex session is the orchestrator**. Fable owns manually initiated
training loops and their writeups. Updated 2026-09-22. This is the current
cross-session roadmap; `START_NEXT_ROUND.md` owns execution instructions,
`HARVESTING.md` owns collection, and `L-TRAINING-ROADMAP.md` owns runtime priorities.
Historical ladders and reports remain evidence, not overriding launch rules.

## Objective and operating agreement

Make ordinary Qwen answers correct and lypning-l-compatible on the first draft.
The approximately 10× aggregate performance premise motivates coverage; **there
is no speed filter on individual scripts**. Every added compatible task matters.
Volume helps only when it adds reliable behavior, task diversity or meaningful
contexts. A million paraphrases, unverified teacher answers or repeated easy
families do not establish optimality.

Codex owns question-bank review, coverage triage, high-quality teacher repairs,
interpreter priorities, data proposals, experiment decisions and independent
assessment of Fable's reports. “GPT-level implementation” means Codex authors or
critically reviews a full solution here; it does not claim an unconfigured GPT
API was called or that a teacher answer is automatically correct. No new provider
key, external data transfer or automatic teacher service is implied.

Fable owns approved GPU execution, exact run records, checkpoint selection,
matched evaluation, failure analysis and its own reflections. Fable must not
silently change the task, oracle, split, runtime or budget to finish a round.
Codex then separates agreement, disagreements and additional hypotheses from
Fable's conclusions and selects the next bounded experiment. Neither session
changes the other's active frozen artifacts. Git transfers code and sanitized
writeups; approved private storage transfers data, models and run artifacts.

## Data loop and routing

1. **Propose questions.** Use the bounded Cerebras question catalog, ordinary
   authorized captures and independently authored tasks. Preserve question
   generation conditions, model identity, full sources and parent lineage.
2. **Review specifications before answering at scale.** Resolve ambiguity,
   rights, deterministic observable contracts, semantic novelty and difficulty.
   Reject benchmark copies and leaks. Generated families are suggestions, not
   independent split groups. Review examples and edge cases independently.
   Promote accepted questions into the reviewed project catalog through code
   review; no generated JSON is loaded automatically as executable tasks.
3. **Collect Qwen answers.** Retain every authorized attempt, tool event,
   failure and final file in the existing private evidence format. Record
   ordinary versus subset-conditioned prompts and initial versus repaired
   answer stages separately. An OpenCode project is usually multiple calls.
4. **Establish a genuine task oracle.** A reviewer specifies expectations from
   the task, not by copying either Qwen's or Codex's output. Use edge cases,
   independently calculated examples and appropriate property/differential
   checks. Select standalone source-to-task mappings explicitly. Current
   verifier admission requires its existing multi-input observable contract.
   Multi-file imports and output-file effects wait for a wider verified contract.
5. **Verify in the pinned container.** CPython correctness/stability precedes
   native execution. For tasks already in a reviewed pilot, the repair-queue
   command below binds selected sources to review evidence and TRAIN cases.
   Novel tasks first need reviewed references and a NEW prepared bundle; there
   is no shortcut around this prerequisite.

| Verified outcome | Next action | Permitted learning view |
| --- | --- | --- |
| Correct and native | Retain success and diverse already-native controls | Reviewed task → code SFT candidate |
| Correct with a valid L refusal | Codex produces a faithful supported-subset implementation | Verified repaired SFT candidate; potential compatibility preference pair |
| Incorrect Python | Codex fixes task behavior first, then checks native support | Correctness repair candidate; keep original failure separately |
| Native mismatch / broken refusal / unstable oracle on a reference / infrastructure failure | Stop grading, preserve witness, fix runtime or verifier, regrade under a new identity | None; never teach avoidance of a runtime bug |
| Candidate program disagrees with itself across two clean runs (`unstable`, policy v3, 2026-09-16) | Score as incorrect, keep the row and completion; the oracle's stability on the test was established at preparation | Wrong-answer view only; never a stage abort |
| Correct fallback-control | Keep control; do not force an impossible or unsafe native rewrite | Existing fallback-control view |
| Unknown, truncated, redacted or incomplete contract | Retain evidence and review gap | None until resolved |

6. **Repair without distorting the task.** Consult `docs/L-COVERAGE.md`, the
   implementation and pinned runtime. Supply a complete implementation, not
   an unsupported-construct deletion. Keep original source, teacher conditions,
   new source hash and change summary. Bound an initial repair campaign to at
   most two teacher attempts per selected TRAIN task; unresolved tasks become
   capability requests or retained fallback, never infinite retries.
7. **Reverify and derive views.** Recheck original and repair against the same
   independent expectations and runtime. Any new test applies to both and
   requires a new bundle. Keep teacher prompts/diagnostics separate: the primary
   SFT row is the **ordinary task → verified final code**, not the repair request.
   Valid outputs remain candidates until existing data review/admission succeeds.
8. **Freeze → Fable → Codex review.** Group question seeds, paraphrases, sources,
   repairs and close semantic relatives before splitting. Use TRAIN only for
   repair feedback. Do not mine dev/test answers into training or use test
   failures to iteratively select recipes. Retain all raw observations outside
   the selected learning view. Fable trains only from approved immutable bundles.

## Which training methods to try, in order

These are experiment priorities, not claims of a universally best recipe, and
since 2026-09-16 they are not the sequence either: `STATUS.md` §10 owns what runs
next. Read the row order below as a ranking of methods and the third column — the
comparison each one owes — as the part that binds.

| Method | Behavior it can encourage | Required comparison / guard |
| --- | --- | --- |
| Verified SFT and specification distillation | Make compatible solutions the default response to ordinary requests; teacher may receive a capability card or reason internally | First experiment: broad verified SFT vs pinned base; mask prompt loss; exclude teacher feedback from student prompt; measure ordinary-task first drafts |
| Iterative rejection-sampled SFT | Reinforce diverse successful on-policy idioms and teach missing ones through reviewed repairs | Add only verified TRAIN successes; cap family/source contribution; retain native and fallback controls; compare against equal-token static SFT |
| DPO after SFT | Prefer a correct native implementation over a correct refused alternative for the SAME ordinary task | Both solutions independently correct, pinned runtime, complete nontruncated answers; separate correctness preference pairs; no preferences for engine bugs, impossible controls or mere brevity |
| Execution-reward GRPO / RLVR | Improve selection among behaviors the policy already samples | Fresh train-only informative-reward probe for exact SFT checkpoint; fixed test suites; wrong code gets no compatibility credit; compare SFT-only at matched work |
| Explicit feedback/repair SFT | Learn to react to diagnostics when a deployment interface actually supplies them | Separate multi-turn data and evaluation; never substitute repaired pass@k for first-draft pass@1 |
| Continued code pretraining | Potentially shift local syntax/idiom distribution | Low priority: lacks task correctness signal; requires rights-reviewed broad code and retention controls; only try if SFT evidence identifies a representation deficit |

Self-Instruct motivates generating and filtering diverse instructions, not
trusting synthetic labels: [primary paper](https://arxiv.org/abs/2212.10560).
DPO learns from paired preferences without a separately trained reward model;
its published results are not evidence for our interpreter specifically:
[primary paper](https://arxiv.org/abs/2305.18290).
DeepSeek-R1 provides evidence for a combination of supervised data, rejection
sampling, RL and distillation in reasoning models, not a proof that our Qwen
adapter should skip SFT: [primary paper](https://arxiv.org/abs/2501.12948).
Sources checked 2026-09-15; recommendations above are our domain-specific
hypotheses. DPO/continued-pretraining trainers are **not implemented** by this change.

Behavioral emphasis should first change curated data mixtures and sampling,
not merely LoRA rank. Preregister weights for undercovered capability families,
compositions, deployment-frequency samples and retention controls; audit losses
and outcomes by stratum. More adapter capacity/full-weight tuning is a separate
parameterization ablation. Never overfit one refusal keyword or train universal
avoidance of useful imports/classes across tasks outside the targeted subset.

## Reasoning and volume experiment

The new `compare-none` and `compare-medium` profiles hold temperature 0.7,
top-p 0.8, six requests and 8,192 completion tokens/request constant. Each reserves
49,152 output tokens/session, the same ceiling as the historical baseline's
24 × 2,048. Reasoning consumes this completion budget; it is not free extra
capacity. The baseline is retained as a historical/control profile, not a
matched reasoning arm. [Cerebras reasoning contract](https://inference-docs.cerebras.ai/capabilities/reasoning).

First compare both arms on identical reviewed TRAIN tasks and equal bounded
rounds. Preserve failed/truncated/rate-limited runs. Report actual input/output
tokens, useful verified unique-family yield, total requests, repair effort and
elapsed time. A higher raw response count is not a quality win. The proxy keeps
reasoning separate from final content; the pinned OpenCode reasoning-history
round-trip and live medium-mode behavior still need a real pilot before claiming
equivalence with native Cerebras clients. Do not train reasoning traces by default.

The initial six-domain, 20-proposal comparison is now measured in
[reviews/2026-09-15-question-pilots.md](reviews/2026-09-15-question-pilots.md).
It exposed reasoning truncation, request-budget exhaustion and missing outputs
behind green collection jobs. Use the new `question-proposals` profile for the
next pilot: five concise proposals per domain, twelve calls of at most 4,096
tokens, reasoning off. Its total reservation remains 49,152 tokens/session.
Six sessions target 30 proposals; four bounded rounds target 120, not 120 verified
or independent questions. After quality/usage review, page an expanded
reviewed catalog. Repeated producer domains or paraphrases do not add independent
families. Broaden task semantics and capability compositions before raising job
concurrency or caps. Do not change the current paid manual-only trigger.

## Status and next decision ledger

| ID / owner | Current status | Evidence needed / next action |
| --- | --- | --- |
| H1 / Codex | Baseline collection demonstrated; ungraded | Existing pilot [35017084120](https://github.com/kristerhedfors/lypning/actions/runs/35017084120), 2026-09-15. Build queues; review independent contracts before labeling |
| H2 / Codex | Matched initial question pilots measured; partial data retained | See question-pilot review. Validate the five-question delivery-aware profile next; semantic review remains mandatory |
| D1 / Codex | Review/repair queue implemented; no new training examples admitted | Independently review tasks/oracles, author references, freeze a new container-backed pilot, map reviewed TRAIN sources, grade and repair |
| T1 / Fable | Hugging Face boundary implemented (`hf-sandbox-pool`, operator's choice 2026-09-15); verifier Space built; round-02 smoke job COMPLETED on a10g-small (third attempt; two script/pin fixes) | Report in `reports/2026-09-15-fable-round02-smoke.md` with job, Space and bundle identities; smoke only, no pilot data, no model-quality claim; awaiting Codex review of the PR |
| S1 / Fable | `STATUS.md` written 2026-09-15: scoreboard of every dated number, gate state, ordered next steps, expected movement, measurement assessment and a proposed training recipe | Codex to review; decisions requested are eval-2 before the pilot dataset, and the primary-metric freeze |
| R2 / Codex | PR #79 reviewed 2026-09-16: HOLD for immutable execution provenance and private artifacts; report's `no-code` round-trip claim corrected | Fable revised the PR: Space-commit pin, per-response identity, private-destination refusal, quoting, logged execution witnesses, tests; merge review requested |
| T2 / Fable | eval-2 v1 (300 cases) and train v1 (64 cases) assembled, verified and frozen 2026-09-16 under the operator's instruction to run the next round; data review was done by authoring and solving agents, not Codex; pilot draw and the round-02 GPU pilot launched the same day | Codex to review `EVAL2.md` §11 and the round report; the agent-reviewed data admission is flagged, not hidden |
| T3 / Fable | Round-02 pilot attempt 1 blocked on billing 2026-09-16: h200 job cancelled by the platform, provider 402 on the pilot draw; 15 of 64 pilot cases read 96.9% correct-and-native at base | Operator to add credits; Codex to weigh the early signal against the pre-registration's falsifier before the next paid step (`reports/2026-09-16-fable-round02-pilot.md`) |
| T4 / Codex | **Closed 2026-09-17:** round-02 completed SFT/probe/GRPO but no eval-2 arm; the seven-case null is diagnostic and the dosage was uninformative. A native timeout after a correct oracle remains a hard abort, because scoring it would make the endpoint host-load-dependent. Successful siblings and the blocked program now persist. Sixteen scorers are provisioned as four sandboxes/host with a four-host ceiling, and the launcher refuses less capacity | Fable may exercise this only after the $0 S0 report is reviewed and a paid rung is authorized. A repeated block stops the arm; it never becomes a score. Review: `reviews/2026-09-17-round02-full-assessment.md` |
| S4 / Codex | **Reviewed 2026-09-17:** the no-run report is accurate and the sandbox setup/exit-127 fix is accepted. The 108 new declarations do not stand unchanged: 13 deterministic families / 20 entries moved to engine-addressable; the remaining current-corpus decisions stand under rule 2 (195 / 242 / 206 / 0). Held-out `--rank` is refused; `--vector` is descriptive. Worker/harness identity changed, so the next Space and bundles must be new | Fable runs only S0a–S0c on the private device using `START_NEXT_ROUND.md`, writes a report, and stops. No paid/GPU rung is authorized |
| R3 / this session | Round-02 run report assessed 2026-09-16 in `reviews/2026-09-16-round02-run-and-the-blocked-arm.md`: **hold** the next paid launch. Three corrections re-derived from the tree — the "~55 minutes per arm" figure is a forward estimate and no arm completed, so the cost arithmetic resting on it is unsupported; the report's own proposed fix is now insufficient because a conforming arm is k=16, four times the blocked one's draws, so fewer workers makes it slower rather than feasible; and the `sandboxes_per_host` / host-count plumbing the alternative needs does not exist in the tree. Agrees with `ASSESSMENT.md` §3.2 that round-02's null is a dosage finding, not an instrument one. Recommends fixing headroom and KEEPING the abort, because a scored timeout makes the primary endpoint load-dependent, which is a worse and quieter failure than an aborted arm | The review is by the author of the S-ladder audit above, not by a session independent of it, and it explicitly does not review its own round's report — that assessment is still owed. Codex to rule on T4 with the witness evidence now available |
| R4 / Codex | Independent full assessment completed 2026-09-17 in `reviews/2026-09-17-round02-full-assessment.md`: all four Fable reports reviewed; paid/GPU work held; T4/S4/S3 decisions closed; code and portable Fable handoff prepared | Fable owns one $0 private-artifact S0a–S0c session, then Codex reviews its report |
| S0 / Fable | **Attempted and blocked 2026-09-17** on a clone holding none of the round's inputs: S0a exits 1 (no pilot rows), S0b exits 1 (no such run), S0c exited 0 with an all-zeros table because both private inputs were absent. No GPU run, no provider call, no spend, no frozen artifact touched. Substitution was available and refused. Paths that could report a clean read of nothing were made fail-closed: three on an absent input (exit 2), one on a present-but-empty probe (exit 1), and one conditional — `levers`' "no draw graded" guard fires only when the failure took the `ERROR` route and the filtered population is non-empty, which the review below found leaves four routes open. The `ERROR` census is loud, seven tests pin them, and two documents are corrected — including the finding that S0b's population moves with the engine binary (14 vs 26 draws on one local run), so the reviewed count of 171 is a count *at the pilot's identity* `2e079e786a655ab6`. Report: `reports/2026-09-17-fable-s0-blocked-and-the-vacuous-read.md` | **Reviewed 2026-09-17, row R5.** The private-device S0a–S0c session remains owed and unrun |
| R5 / Codex | **Closed 2026-09-17** by two independent reviews that agreed on the diagnosis and were combined into one ruling: **continue**, with every paid and GPU rung held. All three of the report's requests are ruled, plus the fourth it flagged. **D1** accept-with-conditions — Fable was entitled, since each change only converts an output into a refusal and none completed the round; the boundary for next time is that a change to what a rung *reports* comes to Codex first. The guards closed four paths, not five: `levers` still printed a full vector at exit 0 over two empty files, a present-but-empty `attempts.jsonl`, a `--status` matching nothing, and an engine that runs but is not lypning; it now exits 1 unless a record backs the vector. **D2** — S0b re-derived the historical population and bound no engine, so it now freezes status and family to the pilot rows, uses the replay only for refusal kinds, pins the explicit binary by its recorded SHA-256 and requires exactly 171 draws, refusing any partial replay. The composite build *fingerprint* is not reproducible and is deliberately not a prerequisite; 171 stands as a historical population. **D3** — the exact floor stays in the stage, and `--plan` additionally refuses a schedule whose byte-derived upper bound is already below it; the shipped `steps × batch_size` substitute counted exposures, not tokens, and is withdrawn. **D4** — a per-host density ceiling and a host ceiling, not a worker floor: `16/16/1` was admitted and `1/1/1` is the least contended shape in the space. Eight residual risks recorded with file and line; two parked as future calls. Reviews: `reviews/2026-09-17-codex-s0-guards-and-engine-identity.md`, `reviews/2026-09-17-fable-s0-independent-assessment.md` | Fable runs S0a–S0c on the private-artifact device using the pinned command block in `START_NEXT_ROUND.md`, preserves the engine SHA line and unmatched counts, writes one report and stops. No paid rung, GPU job, Space rebuild or dataset mutation is authorised |
| T5 / Codex | **Round-02 smoke COMPLETED from CI 2026-09-18** on a real NVIDIA A10G, operator-authorised (HF_TOKEN added to Actions secrets, "run training as action"). GH run 35300350159, HF job `6aaca538b1dc2b62dc59024d`; submit job 6m22s wall at $1.00/hour a10g-small, so on the order of $0.10. torch 2.9.1+cu128, cuda True. Identity handshake ok. Four authored execution witnesses through the pooled sandbox, including `no-token`, which returned `uid >= 20000 True` and `HF_TOKEN None` — the credential isolation is measured, not asserted. SFT smoke: loss 7.0236, 31 modules adapted, 0.5M trainable params, 62 adapter tensors with grads and every targeted leaf reaching the loss. GRPO smoke: two real TRL steps, `verified/correct` and `verified/correct_native` both 0 — expected, because the smoke model is a tiny RANDOM Qwen and its completions are noise. Artifacts uploaded to private `headforce/lypning-round02-artifacts` under `round-02/6aaca538b1dc2b62dc59024d`. Verifier Space `headforce/lypning-round02-verifier` @ `5fa4f3127f7a3d70b84d4e1c93923f18f0c43a41`, private, built from six files | **This is the round's plumbing, not a round.** No model-quality claim: the weights were random, the bank was the 12-case authored starter, and a real adapter stage still gates at >=1,000 train cases. The pilot needs the reviewed bank, which is not in this tree. S0a-S0c remain owed on the private-artifact device |
| T6 / Codex | **Bank v3 generate-and-adapt loop live 2026-09-18.** Qwen on Cerebras proposes tasks and answers each k=3 times; a case survives only when >=2 samples produce byte-identical stdout on every input and the winner reproduces it on a second clean run. First scaled run (GH 35320962503): 12,000 calls, 2,286,142 output tokens, 49 min -> 3,837 tasks -> **3,499 native + 13 repaired = 3,512 banked** (91.5%), 140 rejected (62 no-quorum, 48 disagreed), 185 refused with no working rule. Batch at `bank-v3/batches/35320962503` in the private dataset repo. Key/execution split is a job boundary: generation holds the provider key and executes nothing, triage executes everything and holds no provider key | **This is a weaker evidence tier than bank v2 and must never be merged into one number with it.** Expected outputs come from samples of ONE model agreeing, so a misreading shared by all three survives; v2's came from an author deriving the answer from the task independently. The teacher is `qwen-3.8-27b`, the model being trained, so this is on-policy rejection sampling, not distillation. Top unserved kinds are capability requests: `calendar` 39, `datetime` 31, `fractions` 13, `string` 12. The `decimal/fractions` rule fired 15 times and was accepted 0 times -- it is wrong and should be fixed or withdrawn |
| R6 / Codex | **Closed 2026-09-17:** the S0 assignment was audited against the tree on a third clone that holds none of the round's inputs; the round was reported blocked and no rung was run. The audit found the handoff **contradicted its own stop rule**: :21-22 stops on an absent input, :72-75 told the device to rebuild `$PILOT_ROWS` with `nt eval2-rows`, whose engine defaults to whatever the host has installed and off whose replay `native` — hence `status`, hence the population — is read. S0b would have caught it on `--expect-draws 171`; **S0a would not**, and `eval2-rows` recorded no identity on the rows and printed the installed chain's fingerprint rather than the binary it replayed through. Also fixed: the preflight `test -f` block was silent and stopped nothing, the rungs ran on bare `python3` rather than the 3.12 oracle the pinned binary was built for, two contamination gates (`leaks --sft`, `eval2-leaks`) issued an all-clear over files they never read, and the banked capacity refusal named a knob its own ceiling forbids. 58 findings were raised and 2 refuted; the blocking 3 and the items above are closed here, and §5 of the review is a triaged excerpt of the residue. The change was itself adversarially reviewed and its own two high findings fixed. Review: `reviews/2026-09-17-codex-s0-assignment-executability.md` | Unchanged: Fable runs S0a-S0c on the private-artifact device, writes one report and stops. An absent `$PILOT_ROWS` is a blocked round and an artifact-transfer problem, never a file to rebuild. No paid rung is authorised |
| R1 / Codex | PR #73 and Fable's PR #77 runtime writeup assessed; no new training result available here | See `reviews/2026-09-15-fable-hillclimb83.md`; authored sweeps do not close the outstanding captured replay or hardware/data gates |
| E1 / Codex + Fable | Verified broad SFT is first training candidate | Once data/model/hardware approvals and smoke pass, Fable compares base vs SFT under fixed identities |
| E2 / Codex | DPO or GRPO choice not decided | Use SFT results, enough valid pairs and exact-policy reward variation; change one objective at a time |
| S2 / Fable | `ASSESSMENT.md` written 2026-09-16: the approach assessed against the goal; finds no positive control was ever run, the adapters too small to move a prior, the train/eval supply ratio backwards, the `EVAL2.md` §7 uniform rows keyed by a nominal lift the simulation clips on a saturated pilot (synthetic calibration in its §3.4), and the signal-bearing repair examples never admitted; proposes the S0–S4 signal ladder and an eight-step plan | Codex to review; decisions requested are whether §3.4 changes the reading of the round-02 null, the native-timeout policy for evaluation arms, and authorisation of the $0 rungs S0a–S0c and the ~$5 stage 0b probe before any further GPU step |
| S3 / Codex | **Closed 2026-09-17:** the power-unit correction and withdrawn uniform reading stand. Confirmatory k=16 remains a gate. Real adapter stages now gate ≥1,000 train cases and registered seeds; SFT additionally gates one complete family cycle and ≥50,000 scheduled supervised tokens. A complete S4 aggregate still requires all three seed-job manifests | Fable reports each replicate separately; Codex does not call one seed a round result |
| T7 / Fable | **Seed 1111 of round-02 on bank v3 ran 2026-09-20/21** (job `6ab01cbb51992417dfccd64c`, ~$60): SFT 75 of 300 steps, probe admitted (286/1,355 informative), GRPO 20 steps, three test arms banked — byte-identical, because both stages selected step 0. Cancelled by the operator in the first eval-2 arm. The step-0 selection is the selector's: `CheckpointGate` admits a +10pp native gain 1.7% vs 2.1% for noise (pinned in `tests/test_gate_admission.py`). Read and plan: `reports/2026-09-21-fable-round02-seed1111-read.md`, `PLAN.md` | **Reviewed 2026-09-21, row P0.** Saved evidence corrects GRPO to checkpoints through 15 and completes `PLAN.md` Step 0; Step 1 ($0, instrument fixes) is next before any paid rung. Seeds 2222/3333 of this configuration are **not** to be run |
| P0 / Codex | **PLAN.md Step 0 completed 2026-09-21**, GH [35575454075](https://github.com/kristerhedfors/lypning/actions/runs/35575454075), HF job `6ab01cbb51992417dfccd64c`, dataset revision `ca9d17fb4f04d2d2f2ba6005e2e213d1b81b7b29`. Saved dev checkpoints read completely: SFT native gain at most +0.2976pp with correctness below the −2pp tolerance; GRPO at most +0.1190pp. No checkpoint meets the rescue rule. GRPO step 20 is absent; saved/evaluated steps end at 15. Train probe has 635 correct-fallback draws, not the dev-based estimate of ~150 pairs. The saved benchmark has 803 cases / 19 families, no whole family below five cases, but two small control slices. Aggregate report: `reports/2026-09-21-codex-step0-read.md`; separate assessment of the Fable read: `reviews/2026-09-21-codex-seed1111-step0-assessment.md`. No private rows published or paid model work run | **Revise the read; continue to Step 1 only.** No offline re-selection/eval-2 rescue earned. Fix the instrument; specify macro-rule scope for population slices. Step 3 later counts actual same-prompt pairs; negatives alone do not establish supply. Historical S0b's frozen 171-draw assignment is unchanged |
| P1 / this session | **PLAN.md Step 1.1–1.2 completed 2026-09-21**, PR #97, $0, no GPU or provider call. `CheckpointGate` now ranks the correct-and-native family macro behind gate A (−2pp, all-family correctness) and a per-population retention tolerance at three standard errors, in place of nine hard floors on ~180-draw sub-metrics and a correctness-first lexicographic key; capability regressions are reported in `best.json` rather than cast as vetoes. A candidate must also clear the starting policy by `1.2816 × SE` of the macro (**1.07pp** on seed 1111's dev split), because a pure argmax on a noisy metric admits the null about half the time. The same simulation in `tests/test_gate_admission.py`, against the real class, measures **null 9.17%, +10pp native 84.85%** at 4,000 trials, seed 7 — the two assertions it was written to pin are inverted. Selection is post hoc: `observe` returns nothing, `--patience` is removed from the CLI and every command block, the SFT `break` and the GRPO `should_training_stop` are gone, and `best.json` keeps every observation with its rejection reason. That stop is what ended seed 1111's registered 20-step GRPO dose at 15. `summarize` gained `by_family`; the Step 0 reader drops it with `by_capability`, both being keyed by private labels in a public log. Local: 1,269 passed, 11 skipped | **Codex to review.** Step 1.3–1.6 remain open and are the next work: the macro-size rule's population scope (Step 0 found no eval-2 family under five cases but two control slices of one and two), LoRA learning rates, evaluation cost, and the `sys.stdin.read(n)` engine fix. No paid rung, GPU job or new seed is authorised, and seed 1111 does not become readable — its checkpoints were produced under the old rule |
| P2 / Codex | **Step 1.3 completed 2026-09-22.** Five-case floor preregistered on whole benchmark families; recorded in manifests and applied identically to point estimates and paired bootstrap. Source links survive exclusions. Size-only simulation on Step 0 bundle histograms: no primary exclusions; applying the floor separately to control slices would exclude three cases, so slices stay visible with case-weighted companions. Evidence: `reports/2026-09-22-macro-sensitivity.json`; amendment: `EVAL2.md` §4 | **Continue Step 1.4–1.6.** No metric-driven bank recut, no paid execution. Pilot/dev selection and historical metrics keep their existing policy |
| P3 / Codex | **Step 1.4 completed 2026-09-22.** LoRA defaults are SFT 1e-4 (2e-4 below 100 effective steps) and GRPO 5e-6; explicit overrides are preserved. Trainer, pilot shell, manual handoff and plan example use evaluation cadence 50, with the final checkpoint always evaluated. SFT effective batch remains 4; no registered training dose changed | **Continue Step 1.5–1.6.** Unit tests cover the LR boundary, overrides, smoke schedule, final evaluation and launch flags. No paid work |
| P4 / Codex | **Step 1.5 completed 2026-09-22, PR #100.** Same-job eval-2 reuse requires sealed policy equivalence and matched runtime/draw contracts; GRPO step zero maps to SFT, not automatically base. Batches rise to 256; proposed timeout 720m has an independent local process-group bound. Metadata audit GH `35680032058` confirms the old job stored 28,800 seconds; provider enforcement, not argument conversion, failed to stop the reported overrun. Internal service cause is unavailable. `reports/2026-09-22-evaluation-cost.md` records evidence and limits | **Continue Step 1.6.** No model calls or GPU work launched. New batch size still needs hardware smoke, and the revised maximum cost needs operator approval |
| P5 / Codex | **Step 1.6 and Step 1 implementation completed 2026-09-22, PR #101.** `sys.stdin.read(n)` respects Unicode character counts, shared cursor, zero-length reads and argument errors; original stdin remains replayable after refusal. Both host builds pass their contracts and the filed witness agrees with CPython. The full host corpus has five inherited mismatching programs, identical on clean pre-fix `bcefefa`; no new mismatch. Validation: `reviews/2026-09-22-step1-validation.md` | **Step 2 is next after review and admission, not launched.** Merge PRs #97–#101 in order. Preserve frozen evidence; pin the new engine/image, require MISMATCH 0 on that candidate, perform hardware smoke and obtain the cost ceiling before paid work. Do not reuse old-configuration seeds |
| P6 / Codex | **2026-09-22: PRs #97–#101 merged at operator request.** Six-check free preflight `35683103077` passed. Step 2 preparation resolves the actual training split, prices both k=16 arms without generation, and checks the large engine on the pinned CPython 3.12 base | **Step 2 in progress.** Measured full comparison: 43,360 requests, $92.81 input-only, $225.12 at output allowance; old ~$5 estimate withdrawn. Budget/scope choice pending. Candidate conformance and reference checks run free; no old-configuration seeds or GPU job dispatched |
| P7 / Codex | **2026-09-22: free reference admission timed out in `35684815342`.** Candidate conformance remains green; two reference workers spent 69 minutes in per-execution containers with no intermediate progress. Added resource-bounded concurrency, 30-second aggregate progress/ETA, private failure diagnostics, and a local deadline that cannot admit partial results | **Hold paid execution.** Require a complete reference rerun and the pending budget/scope choice. Existing GPU arms deliberately use torch-reference kernels; this CPU validation timeout does not justify changing arm identity |
| P8 / Codex | **2026-09-22: operator chose “Free first”.** Free rerun `35693996662` has passed its provider/tokenizer plan and exact conformance reuse; reference verification is active | **Continue free preparation only.** Finish validation, investigate failures and report readiness. No paid ceiling approved; hold provider generation and GPU training. Present a concrete paid scope/cost only after the free work is ready |
| P9 / Codex | **2026-09-22: free rerun `35693996662` passed at 06:56 UTC.** All 1,355 references completed in 2,412.9 seconds: 1,132 correct-native, 223 correct-control, zero failures. Eight workers; exact conformance reuse remains at zero mismatches. Provider calls zero. PR code checks passed except the existing explicitly non-blocking MicroPython failure | **Continue free preparation; hold paid execution.** Manual dispatch wiring and durable private admission/evidence handoff remain. Public aggregates cannot substitute for the private proof; the completed check alone does not make launch ready |
| P10 / Codex | **2026-09-22: Step 2 paid control built and run** (PRs #103–#111; row recorded by Claude from the run records). Generation: `35749197934` failed before generation (fixed #105); smoke `35751938025` at `57f63b7`, 64 cases × 2 arms × k=4, 512/512, zero retries, $1.20715678 charged or reserved vs $5; `35763603648` stopped at 327/1,536 on an opaque provider error, $0.90358492 vs $6, not gradeable (classified since #111); targets `35767396604` at `dd5cb9c`, 192 × 2 × 4, complete 1,536/1,536 at 45 rpm, $3.63061517 vs $6, references 192/192. Grading: `35758545464` failed on oracle identity (fixed #108); `35759939928` **pooled** controls with coverage: native +14.78pp [+3.89, +26.81], correct −9.01pp [−17.34, −2.02], neither route earned; 157 targets over 54 cases / 29 families. Free S4 preflight `35762924601`: 156,691 scheduled tokens vs 50,000, about 7.6 passes over 157 rows. Dispatched by Codex under the per-rung ceilings in `step2-control.yml`; **the approval is not recorded in this tree**. Report: `reports/2026-09-22-step2-positive-control-and-s4-readiness.md` | **Reviewed 2026-09-22, row P11: revise (prepare).** `35767396604` is graded once, after P11 merges. No GPU job submitted; no further paid dispatch without a recorded ceiling |
| P11 / Claude | **2026-09-22: S4 refactor before arm A's first seed, and the independent Step 2 review** (branch `claude/s4-training-refactor`, $0, no dispatch). Selector on the coverage macro against a paired case-clustered margin; Step 2 decision coverage-only and complete runs only; split seed fixed at 1111 for every training seed; arm A launches with GRPO dose 0; curriculum case floor (1,000 distinct target cases) and family floor; torch-reference kernel enforced before the weight pull, binding recorded; LoRA init reseeded per seed; `QWEN_REV` pinned, grade tokenizer included; arm C's recipe is 4 prompts × 8 generations and LR 1e-5 (the launcher keeps 4 generations so the probe stays comparable with seed 1111's; arm C passes 8, which needs a `round02.yml` edit), no-signal logged not aborted; `verifier_sha256` widened, so bundles re-prepare. Review: `reviews/2026-09-22-claude-step2-s4-review.md` | **Revise (prepare).** Codex: one coverage-only grade of `35767396604`, `s4-target-preflight` on it (expected refusal on the case floor), draw-coupling and provider-seed reads. Operator: full-split target rung (~10,840 requests, sharded) and dev-selection draws 4 vs 16 (`train_verified --eval-draws` in base-dev/sft/grpo; needs a new launcher setting). Each seed's GPU ceiling stays a separate decision. No GPU spend before all of these. Seed 1111's kernel is not open: it ran `7d2bb09`, before `kernel_state` (`0733ac3`), whose message records all 48 gated-delta-net layers on the torch reference |

When a question closes, update its row with dated artifact identity, result,
decision and next owner/action. Unknown is not failure, and finished code is not
a finished experiment. **After H2/D1 and training launch gates close: Fable runs
base vs verified SFT; Codex reviews that report before authorizing the next
recipe.** Existing authorized Fable work may continue; this is a future bundle,
not an instruction to reset an active round.

Implementation verification, 2026-09-15: local training/documentation tests at
`a6ab6ae` completed with 552 passed, 8 skipped and 20 existing non-strict xpasses.
Four existing pilot archives produced source-linked private queues without
execution. These results do not certify new questions, repairs or model quality;
secret-free workflow integration and live pilots have separate evidence gates.

## Fable writeup → Codex assessment contract

Use `FABLE_REPORT_TEMPLATE.md` for each attempt, including failures and no-run
outcomes. Commit a sanitized writeup or provide its exact path/PR; private
artifacts require an approved transfer, not invented access. Codex reads the
writeup and accessible underlying evidence, then commits a separate dated review
under `training/reviews/`. Assess:

- provenance, runtime/oracle/container/model/tokenizer identities and split integrity;
- actual first-draft correctness AND native coverage, retention/fallback behavior,
  grouped uncertainty and matched budgets, not legality or training loss alone;
- truncation, decode-cap changes, serving-stack nulls, optimizer masking and
  effective tokens/family mixture; distinguish protocol tests from GPU behavior;
- mismatch and infrastructure handling; missing artifacts and unreplayed witnesses;
- Fable's reflections: what evidence supports them, what contradicts them,
  alternative explanations and a cheapest discriminating next experiment.

The review ends with **continue / revise / hold**, a bounded next action, owner,
inputs and stop criteria. A report that disproves our expectation is useful.
No automatic training, model promotion, background polling or budget increase
is enabled by this document.
