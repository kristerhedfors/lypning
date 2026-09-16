# Question → answer → verified repair → training

Owner: **this Codex session is the orchestrator**. Fable owns manually initiated
training loops and their writeups. Updated 2026-09-15. This is the current
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
| T4 / Fable | Round-02 pilot run 2026-09-16 under the operator's "run actual training": nine h200 attempts (Hub outage, pip, harness-hash gate, Space paused, sequential eval too slow, bundle identity gate, nondeterministic candidate), the ninth completing SFT, probe and GRPO on the 27B weights with per-stage uploads; no gain on dev or test (7 cases each); eval-2 base arm blocked at 384 draws by an engine timeout on a CPU-bound candidate under concurrent scoring, no arm completed | Codex to rule on native-timeout-after-correct-oracle (fault vs `not-native` with witness) and on pool CPU headroom before the next launch, review the day's fixes (batched paired evaluation, split-component clustering, policy v3, per-job pool names) and the proposed next experiment (`reports/2026-09-16-fable-round02-run.md`) |
| S4 / Fable | S-ladder round assigned 2026-09-16 did **not** run: every rung of `STATUS.md` §10 is blocked on this device and each on a different prerequisite (private rows for S0a–S0c, a paid pinned provider plus the private train v1 bank for S1, the pooled verifier Space for S2, a provider key plus the disposable-runner boundary for S3, a GPU and the S0–S3 reads for S4). Nothing was spent, no GPU ran, no frozen artifact was touched. What was done at $0 instead: rung S0b's tool half (`pipeline.levers`, `nt levers`) turns `ASSESSMENT.md` §4's one-off prose judgement into three mechanical layers plus a frozen 130-row declaration table that reads the eval-2 draw rows unchanged; `ASSESSMENT.md` §6 step 2's uncontroversial half (a blocked evaluation arm preserves the program that blocked it, abort unchanged, T4's ruling not pre-empted); and a pre-existing defect in the candidate-execution boundary, where a harness setup failure was indistinguishable from the program's own exit 127 under network isolation | Codex to review `reports/2026-09-16-fable-sladder-s0-device-audit.md`, which no session but its author has read. Three decisions requested: whether the 108 **new** declarations in `levers.DECLARED` stand; whether the −38 / +43 delta against §4 is §4's judgement or this table's (§4's per-family reasoning is not recoverable from its prose, so it is a ruling, not a read); and whether `refusals.HELD_OUT_BANNER` must travel with `nt levers --rows` on an eval-2 arm. Also: whether to take the sandbox fix now, which forces a verifier-image rebuild and a new bundle before the next launch |
| R3 / this session | Round-02 run report assessed 2026-09-16 in `reviews/2026-09-16-round02-run-and-the-blocked-arm.md`: **hold** the next paid launch. Three corrections re-derived from the tree — the "~55 minutes per arm" figure is a forward estimate and no arm completed, so the cost arithmetic resting on it is unsupported; the report's own proposed fix is now insufficient because a conforming arm is k=16, four times the blocked one's draws, so fewer workers makes it slower rather than feasible; and the `sandboxes_per_host` / host-count plumbing the alternative needs does not exist in the tree. Agrees with `ASSESSMENT.md` §3.2 that round-02's null is a dosage finding, not an instrument one. Recommends fixing headroom and KEEPING the abort, because a scored timeout makes the primary endpoint load-dependent, which is a worse and quieter failure than an aborted arm | The review is by the author of the S-ladder audit above, not by a session independent of it, and it explicitly does not review its own round's report — that assessment is still owed. Codex to rule on T4 with the witness evidence now available |
| R1 / Codex | PR #73 and Fable's PR #77 runtime writeup assessed; no new training result available here | See `reviews/2026-09-15-fable-hillclimb83.md`; authored sweeps do not close the outstanding captured replay or hardware/data gates |
| E1 / Codex + Fable | Verified broad SFT is first training candidate | Once data/model/hardware approvals and smoke pass, Fable compares base vs SFT under fixed identities |
| E2 / Codex | DPO or GRPO choice not decided | Use SFT results, enough valid pairs and exact-policy reward variation; change one objective at a time |
| S2 / Fable | `ASSESSMENT.md` written 2026-09-16: the approach assessed against the goal; finds no positive control was ever run, the adapters too small to move a prior, the train/eval supply ratio backwards, the `EVAL2.md` §7 uniform rows keyed by a nominal lift the simulation clips on a saturated pilot (synthetic calibration in its §3.4), and the signal-bearing repair examples never admitted; proposes the S0–S4 signal ladder and an eight-step plan | Codex to review; decisions requested are whether §3.4 changes the reading of the round-02 null, the native-timeout policy for evaluation arms, and authorisation of the $0 rungs S0a–S0c and the ~$5 stage 0b probe before any further GPU step |
| S3 / Fable | `ASSESSMENT.md`'s instrument findings acted on in code, 2026-09-16, nothing spent and no frozen artifact touched: `power_curve_clustered` returns the realised lift in the rule's own unit (`mean_macro_effect`, the macro over families) plus a `capped` flag, and `nt power --eval2` keys every cell on it and counts the cells the rate cap clipped — so the §7 mislabel cannot be re-printed. `EVAL2.md` §7's uniform reading, and its prescription of more draws and a larger bank, are withdrawn as unsupported by those rows; the concentrated rows stand. A non-smoke benchmark eval arm at any k but `training_contract.PROTOCOL_EVAL_DRAWS` (16) is now refused in `train_verified.preflight`. `STATUS.md` §10 is the single live sequence. Two corrections to S2's own findings: `mean_effect` is per-case and is not the unit power is read against, and the concentrated shape clips too | Codex to review; the three decisions S2 requested are unchanged and still open, plus: whether the withdrawal of §7's uniform reading is accepted, and whether the training-case floor and three-seed rule should become gates like k did or stay decisions for the operator |

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
