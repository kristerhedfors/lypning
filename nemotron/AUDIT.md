# Audit of the measurement pipeline — 2026-09-11

Six independent lenses over `nemotron/pipeline/`, every finding then handed to two
refute-by-default skeptics. **52 findings raised, 33 survived, 19 refuted.** Every
surviving finding below was confirmed by running a repro.

This pipeline is the instrument that decides whether a LoRA run worked. Its 62
passing tests caught none of this, which is the point: a green suite measures the
cases someone thought of.

## Surviving findings


### corrupts-a-reported-number

**`data-integrity/broken-corpus-cases-unpassable`** — `/home/user/lypning/nemotron/pipeline/lypning_source.py:160`  
12 of the 249 corpus cases have an `expect_stdout` that the case's OWN 'correct' original program does not reproduce on CPython — frozen `datetime.now()` timestamps, frozen benchmark timings and PIDs, a captured `sys.modules` listing, and hash-order-dependent set/dict-view reprs. `classify_entry` captured one sample of a nondeterministic program as ground truth and `conf.is_nondeterministic` did not catch it; the `discriminates` gate could not catch it either, because a refused: case is *expected* to have its negative fail.

> Deflates every reported pass rate. 4 of the 12 are in the frozen 74-case held-out set (ntx-4c3ce026fadf, ntx-5607e7b17b22, ntx-62818bbeb6cf, ntx-fa3622ac596b) and all 4 are recorded as failures in the baseline: 'baseline pass@1 35.1% CI [24.3,45.9]' is 26/74; over the 70 answerable cases it is 37.1%. 3 of the 4 score 0/16 even at k=16, so they are dead weight in the rewrite slice too; the 4th (ntx-4c3ce026fadf) scores 5/16 by coin flip. The remaining 8 are in train, where they pad sample.json's 'refused: 972' / 'stdout: 650' rejection counts and deflate the reported 83/175 yield.

**`data-integrity/engine-mismatch-scored-as-model-failure`** — `/home/user/lypning/nemotron/pipeline/evaluate.py:263`  
`_one` treats a `lypning`-kind verdict with reason `engine-mismatch` as an ordinary failed attempt (`passed=False`) and hands it to `classify()`, which has no engine-mismatch branch and falls through to `wrong-output`. This is CLAUDE.md invariant 1 inverted: acceptance.py's own docstring promises a MISMATCH 'is reported under its own category so it can never be quietly counted as the model getting something wrong', and evaluate.py does exactly that.

> Deflates the reported rewrite-slice numbers of runs/headroom-k16. CONFIRMED: 3 attempts carry reason `engine-mismatch` — all three are the engine bug `NameError: name '__file__' is not defined` under lypning on programs whose CPython output was already verified correct — and all 3 are stored with failure_category='wrong-output', i.e. they are inside the reported 'wrong-output: 664'. Case ntx-0e7d23426c28 is reported as 0/16 when 2 of its 16 draws were CPython-correct. Recomputing the refused slice with mismatches counted as what they are (not the model's failure) moves pass@1 11.5% -> 11.9% and pass@16 26.9% -> 28.8% (one whole case), CI [5.6,18.5] -> [5.9,18.9]. It also silently mixes 12 more engine bugs into sample.json's 'rejected_by_reason' as if they were model failures.

**`data-integrity/hashseed-nullified-by-dash-E`** — `/home/user/lypning/nemotron/pipeline/sandbox.py:229`  
`run_python` spawns CPython with `-E`, which makes the interpreter ignore every PYTHON* environment variable — including the `PYTHONHASHSEED="0"` set nine lines earlier at sandbox.py:120 with the comment "a generated program must not be flaky on dict order" — so set/frozenset/dict-view iteration order is re-randomized on every single acceptance run.

> Every pass@1/pass@k in the repo is a coin flip on order-sensitive cases and is not reproducible. CONFIRMED: case ntx-4c3ce026fadf, same stored program, 12 identical `run_test` calls -> [True,False,False,True,False,False,False,True,True,True,False,True]. It is scored 5/16 in runs/headroom-k16 purely by luck. Re-grading the identical completions (runs/replay-check) moved headroom-k16 pass@1 from 35.98% to 35.81% and the rewrite-slice numbers with it; direction is random per run, so 'rewrite slice pass@1 11.5% CI [5.6,18.5]' and 'baseline pass@1 35.1%' are both irreproducible rather than merely biased. It also poisoned harvest: 6 corpus cases have an expect_stdout that no deterministic run can reproduce (see broken-corpus-cases).

**`data-integrity/literal-min-chars-hole-plus-shortest-wins`** — `/home/user/lypning/nemotron/pipeline/sample.py:190`  
`_LITERAL_MIN_CHARS = 12` turns the cheat guard off entirely for any case whose expected output is under 12 characters, and `best = sorted({...}, key=lambda p: (len(p), p))[:keep]` then deterministically picks the SHORTEST passing draw — which for exactly those cases is the hardcoded literal. 'Shortest wins' is not a neutral tiebreak here; it is a cheat-seeking selector.

> Corrupts both 'rejection sampling: 154 SFT examples' (they are not all 'verified to compute', contradicting sample.py's own module docstring) and '53 draws rejected as literal-output' (an undercount). CONFIRMED: 66 of the 154 SFT rows are for cases where the guard structurally cannot fire; in 20 of them the whole expected output appears verbatim in the accepted program. The unambiguous one is ntx-9e31f5a2cb59 (original: `import datetime; print(datetime.date(2026,8,13).isoformat())`): 7 distinct passing programs were drawn, including an 805-char genuine calendar implementation, and shortest-wins selected `print('2026-08-13')` and `import os\nprint('2026-08-13')` as BOTH SFT targets. The training set therefore teaches the exact degenerate behaviour the pipeline claims to reject.

**`end-to-end/hashseed-nullified-by-dash-e`** — `/home/user/lypning/nemotron/pipeline/sandbox.py:229`  
The sandbox sets PYTHONHASHSEED=0 (sandbox.py:120, comment: "a generated program must not be flaky on dict order") but launches CPython with `-E`, which makes the interpreter ignore every PYTHON* environment variable — so hash randomization stays ON for every CPython leg of every test, and nine corpus cases whose stdout depends on set/dict iteration order are graded by coin flip.

> Deflates and de-randomizes the headroom-k16 slice numbers. 13 of 249 corpus cases produce varying stdout over 5 runs and a 14th (ntx-817398a22375) is stable-but-never-equal to its frozen expect_stdout; 5 of those 14 are in the 74-case holdout (ntx-4c3ce026fadf 0.3125, ntx-62818bbeb6cf 0.0, ntx-817398a22375 0.0, ntx-5607e7b17b22 0.0, ntx-fa3622ac596b 0.0). Dropping them: rewrite-slice pass@1 11.5% -> 12.1%, pass@16 26.9% -> 27.7% over 47 not 52 cases; overall pass@1 36.0% -> 38.1%, pass@16 48.6% -> 50.7%. It also makes the run unreproducible: runs/replay-check re-graded the identical 1184 programs and got 35.81% instead of 35.98% (two attempts on ntx-4c3ce026fadf flipped from pass to fail).

**`end-to-end/literal-output-false-positives`** — `/home/user/lypning/nemotron/pipeline/sample.py:59`  
`looks_like_literal_output` flags any passing draw whose source contains the expected output text, which also matches every legitimate rewrite whose *input data* or *own print strings* are literals — so the "53 literal-output cheats" are mostly not cheats, and five cases lose every passing draw they had.

> Deflates `cases_with_a_verified_solution` (83/175), `yield_rate` (47.4%) and `sft_examples` (154), and the reported claim "53 draws rejected as literal-output cheats" is itself wrong. Five cases have kept=0 solely because all their passing draws were flagged: ntx-f1c84fcfef51 (14 draws), ntx-8a82c3c28486 (12), ntx-e5401ce687e9 (11), ntx-9c253e9f64b1 (8), ntx-98623b899f5b (4). Restoring them takes 83 -> up to 88 (yield 47.4% -> 50.3%) and 154 -> up to ~164 SFT examples. At least 29 of the 53 are unambiguous false positives: ntx-f1c84fcfef51 and ntx-e5401ce687e9 are full filesystem-scanning rewrites whose flagged "output lines" are the banner strings the program prints (`=== main-transcript only ===`); ntx-98623b899f5b's flagged lines are the `<a>`/`</a>` tag literals the contextmanager rewrite must emit. Only ntx-0008b4872433's `print('4.0 2 3 3.1416 3.0')` is a clear cheat.

**`end-to-end/refused-relabelled-wrong-output`** — `/home/user/lypning/nemotron/pipeline/classify.py:57`  
`classify()` has no arm for the `refused` verdict reason, so it falls through to the final `return "wrong-output"` — a program that produced CPython's exact answer and merely fell back to CPython is filed in summary.json as having answered wrong.

> summary.json's `failures_by_category` is wrong in both committed runs, and in the direction that flatters the engine story. headroom-k16 reports `wrong-output: 664`; the true composition is 285 wrong-output, 376 refused (correct answer, wrong route), 3 engine-mismatch — the reported figure is 2.3x too high and hides that fallback, not incorrectness, is the dominant failure mode. data/baseline.json reports `wrong-output: 23` when only 9 attempts actually produced wrong output (14 were refused).

**`gates-and-freeze/discriminates-gate-accepts-an-approximation`** — `/home/user/lypning/nemotron/pipeline/acceptance.py:325`  
The `discriminates` gate only requires that `EMPTY_PROGRAM` fails and that the recorded failing program fails. For a rewrite case the recorded failure fails on the *routing* leg (exit 90), never on the answer, so the only real discrimination evidence is that an empty program prints nothing. 39 corpus cases (13 held out) have an expected stdout of six characters or fewer — 'True\n', '3\n', '1\n' — which any guess reproduces.

> The kept case ntx-873541ebb304 (prompt: rewrite `n=float('nan'); print(n in [n])`, expect 'True\n') is 'solved' in data/sft/v1/sft.jsonl by a program that substitutes `x = float("1.0")` for the NaN — it changes what the program computes, which the prompt and the project's skill both forbid, and is marked `"verified": true`. Inflates the reported rejection-sampling numbers (83/175 cases yielded, 154 SFT examples) and puts an approximation into the training set, which is the exact failure mode the fine-tune is supposed to prevent.

**`gates-and-freeze/pythonhashseed-defeated-by-dash-E`** — `/home/user/lypning/nemotron/pipeline/sandbox.py:227`  
`_scrubbed_env` sets PYTHONHASHSEED=0 "so a generated program must not be flaky on dict order", but every CPython run is launched as `[sys.executable, "-E", "-s", entry]` and `-E` tells CPython to ignore all PYTHON* environment variables — so hash randomization (and PYTHONIOENCODING/PYTHONDONTWRITEBYTECODE) is live for every reference capture, every gate run and every graded attempt.

> Set/frozenset repr order is random per process, so any case whose expected stdout contains a multi-element set repr is a coin flip that no model controls. Directly deflates AND randomizes the reported baseline pass@1 35.1% [24.3,45.9] over 74 and the headroom-k16 rewrite slice (11.5% pass@1 / 26.9% pass@16 over 52). Measured: of 5 broken held-out cases, ntx-4c3ce026fadf passed 5/16 draws purely on hash-seed luck — that one case supplies 0.31 of the slice's 6.0 total pass mass (~5%). The CI resamples cases and treats each case's observed mean as fixed, so this noise is invisible in the reported interval and makes two runs of the same model non-comparable.

**`gates-and-freeze/stable-gate-cannot-see-an-unsatisfiable-test`** — `/home/user/lypning/nemotron/pipeline/acceptance.py:341`  
No gate ever asks whether the test can be passed by anything. For the 178/249 cases with no reference, `satisfiable` is recorded as "unproven-no-reference" and skipped, and `stable` compares `run_test(test, witness)` twice where `witness = reference or failing_program` — i.e. the *known-failing* program. Two consecutive failures satisfy `stable`, so a test that nothing on earth can pass is stamped kept.

> 5 of the 74 held-out cases are broken: ntx-5607e7b17b22, ntx-62818bbeb6cf, ntx-fa3622ac596b never reproduce their own expect_stdout (timing/pid text, set order, datetime.now()), and ntx-817398a22375 (2/6) and ntx-4c3ce026fadf (3/6) are coin flips. All 5 are in the "refused" slice. Baseline pass@1 is deflated: reported 26/74 = 35.1%, true 26/71 = 36.6% (37.7% if the two coin flips are also excluded). headroom-k16 rewrite slice is deflated: reported 11.5% / 26.9% over 52, true 12.2% / 28.6% over 49. 10 such cases exist corpus-wide.

**`lypning-kind/engine-mismatch-counted-as-a-model-failure`** — `nemotron/pipeline/acceptance.py:410`  
The module docstring promises an engine/CPython disagreement 'is reported under its own category so it can never be quietly counted as the model getting something wrong', but `_run_lypning` returns `Verdict(passed=False, reason='engine-mismatch')`, and `evaluate._one` (evaluate.py:261-263) records it as `passed=False` with `failure_category=classify(...)`, which falls through classify.py's chain to 'wrong-output'. Nothing downstream (`summarize_run`, `cmd_slices`) ever separates it — grep shows the string 'engine-mismatch' exists only at acceptance.py:410.

> Deflates the reported `rewrite slice pass@1 11.5%` (3 of the 832 refused-slice attempts in headroom-k16 are engine-mismatch; excluding them gives 11.6%) and corrupts `failures_by_category`: the reported 664 'wrong-output' failures include 3 lypning bugs. This is a direct invariant-1 violation in the metric: an engine regression reads as a model regression, so a fine-tuned run that trips a new MISMATCH would be scored as a worse model.

**`lypning-kind/eval-path-has-no-literal-output-guard`** — `nemotron/pipeline/acceptance.py:380`  
`_run_lypning` grades a program purely on byte-identical stdout, and the project's own degenerate-solution guard `sample.looks_like_literal_output` is called ONLY from the rejection sampler (sample.py:139); `evaluate._one` (evaluate.py:256-263) never calls it. So `print(<the expected output>)` is a full pass — it is correct on CPython AND routes tier-1 — and is counted in every reported pass@1/pass@k.

> Inflates `baseline pass@1 35.1%`: 1 of the 26 passing held-out attempts is a verbatim literal cheat (`print(repr('\"a\",\"b\"\\r\\n'))` for ntx-3238ce407be8), so the honest figure is 25/74 = 33.8%. The same hole is open on the headroom run and on any future fine-tuned run, where a model trained to avoid refusals has every incentive to widen it — the sampler itself rejected 53 draws for this exact reason, none of which the evaluator would have caught.

**`lypning-kind/tooling-cases-become-unpassable-rewrite-targets`** — `nemotron/pipeline/lypning_source.py:86`  
`is_tooling` decides that a case drives lypning itself by looking at the refusal *detail*, not at the program. A program like `import json,glob,re,sys; sys.path.insert(0,'src'); from lypning import conformance ...` refuses first on `glob-order` or `class`, so the detail never starts with 'lypning' and the filter never fires. The case is then built as a rewrite target whose prompt asks for a version that stays inside the subset — impossible, because `import lypning` is itself a `module` refusal.

> Deflates the reported `rewrite slice pass@1 11.5% / pass@16 26.9%`: 4 of the 52 held-out rewrite cases (ntx-24db310243ea, ntx-5607e7b17b22, ntx-a43e51de8535, ntx-a73f6bc5c905) are lypning-tooling programs and score 0/16 each; removing them gives 12.5% / 29.2%. 11 more sit in the 175-case training split, so they also drag the reported rejection-sampling yield (83/175).

**`sandbox/capture-files-inside-the-program-cwd`** — `nemotron/pipeline/sandbox.py:216`  
`.ntx-stdout`, `.ntx-stderr` and `solution.py` are written into the program's own working directory whenever `scratch_dir` is None — which is every one of the 249 corpus cases (kinds `stdout` and `lypning` never pass one) — so the harness's own files are part of what the program observes, and the program can delete or truncate the file its stdout is being captured into.

> Held-out case ntx-f72ddbdfe8f7 is literally `import os; print(len(os.listdir(".")))` with expect_stdout "3" — the 3 is the harness's own file count, not a property of the task. Its ground truth is a harness artifact, and the only way a model can pass it while staying tier-1 is to hardcode `print(3)`, i.e. the exact "literal-output cheat" the sampler is built to reject. It is scored 0 in every reported run, so it deflates the baseline 35.1% and the rewrite slice by 1/74 = 1.35pp for a reason that is the harness's, not the model's — and if a model ever did emit `print(3)` it would inflate them instead. Separately, a program that removes `.ntx-stdout` yields stdout="" with exit 0, harness_error=None and RunResult.ok True — real output silently replaced by nothing. sandbox.py's own scratch_dir docstring (lines 190-194) describes this failure exactly, but the fix is wired only into the `script` kind, of which the corpus has zero cases.

**`sandbox/dash-E-defeats-pythonhashseed`** — `nemotron/pipeline/sandbox.py:229`  
The CPython child is launched with `-E`, which makes CPython ignore every PYTHON* variable the harness just set — including the `PYTHONHASHSEED=0` set on line 120 with the comment "a generated program must not be flaky on dict order" — so every run gets a fresh random hash seed and set/frozenset/dict-view iteration order is a coin flip.

> Directly corrupts the reported pass@1 numbers. Replaying the SAME 1184 attempts of run "headroom-k16" against the same tests gives pass@1 0.3598 vs 0.3581 (runs/headroom-k16 vs runs/replay-check) — 2 attempts flip on case ntx-4c3ce026fadf, whose stdout is 50/50 under the sandbox (measured 6/6 split over 12 runs). Worse, because each case's expect_stdout was frozen from one random seed at harvest time, 3 of the 74 held-out cases (ntx-4c3ce026fadf, ntx-62818bbeb6cf, ntx-817398a22375 = 4.1% of the denominator) can no longer be passed by a correct program at all: an honest `print({1,2,3,'a','b'})`-style solution passed 0/12 in my measurement. That DEFLATES the baseline 35.1% and the rewrite-slice 11.5% / pass@16 26.9% by up to ~4 points and misattributes the loss to the model. In the other direction it INFLATES the cheat count and contaminates SFT: rejection sampling can only accept programs that hardcode the recorded arbitrary ordering, and 2 of the 83 yielded train cases (ntx-a689050ef620, ntx-6008c9a5b0ef -> 4 of the 154 "verified" SFT examples) are exactly that — the pipeline is teaching a random seed's set order as "CPython's answer". Same `-E` also silently voids PYTHONUNBUFFERED (buffered stdout is lost when a program is SIGKILLed at timeout), PYTHONIOENCODING and PYTHONDONTWRITEBYTECODE.

**`statistics/partial-run-wins-the-baseline-comparison`** — `nemotron/pipeline/cli.py:522`  
cmd_results guards comparability on the holdout manifest and prompt hash only; it never checks that the run actually evaluated the whole held-out set, so a run that covered a subset of the 74 cases is printed with a delta and can be declared a WIN — and cmd_promote will then make it the baseline with no check at all.

> The `delta` and `win` columns of `nt results`, and hence the go/no-go verdict on the fine-tune. Direction: inflates. An eval killed by the spend cap, a preemption, or a case whose every draw is a harness_error drops the unfinished (typically slower/harder) cases out of the denominator; the surviving subset reads high. The summary already carries cases_evaluated=34 / cases_planned=74 and progress.json carries state="aborted" — both are ignored. Note this is not hypothetical plumbing: Evaluation.run() returns None for every remaining work item once self._aborted is set (evaluate.py:221-222) and then calls self.summarize() anyway, so a spend-capped run writes a summary.json that lands straight in this table.

**`statistics/results-ignores-sampling-config`** — `nemotron/pipeline/cli.py:513`  
The comparability guard in cmd_results checks holdout_manifest_sha256 and prompt_sha but not the sampling block (enable_thinking, max_tokens, samples, temperature), which is right there in summary['sampling'] — so runs that differ only in decode budget are subtracted from each other and presented as deltas.

> The `delta` column of `nt results` as it stands today. `nt results` currently prints `baseline-nemotron35-bf16  28.4%  -6.8pp` against the t12k baseline; those two runs are the same model, same prompt, same split, and differ ONLY in max_tokens (4096 vs 12288). The -6.8pp is a token-budget effect labelled as a model delta. Likewise headroom-k16's +0.8pp is a thinking-off, 2048-token, k=16 run differenced against a thinking-on, 12288-token, k=1 baseline. Direction: makes incomparable, in whichever direction the decode budget happens to fall — and it will do exactly this to the fine-tune's delta if the tuned run is served with a different budget. cmd_grade makes it worse by writing `"sampling": {"replayed": True}` (cli.py:353), discarding the record entirely, and cmd_results still compares such a run.


### would-corrupt-a-future-number

**`data-integrity/literal-guard-defeatable-and-absent-from-eval`** — `/home/user/lypning/nemotron/pipeline/sample.py:54`  
`looks_like_literal_output` only does verbatim substring matching, so any encoding of the expected output defeats it completely; and `evaluate.Evaluation._one` (evaluate.py:255) never calls it at all, so the headline pass@1 the fine-tune is graded on has no anti-cheat guard whatsoever.

> Directly inflates the metric the whole project optimizes. CONFIRMED: of the 38 held-out cases the base model never solved in 16 draws, I took the 18 with no stdin/files/argv and a bounded expect_stdout, and 18/18 were passed by a 3-char-chunked string literal AND 18/18 by `chr()` arithmetic — full `run_test` pass (tier-1 on lypning included), with `looks_like_literal_output` returning "" on every one. Same result 40/40 on unsolved TRAIN cases, where the cheat would additionally be written into sft.jsonl as a 'verified' target. A LoRA that learns this one trick moves rewrite-slice pass@1 from 11.5% toward 100% with zero capability gain, and nothing in the pipeline would notice.

**`data-integrity/prompt-signature-blind-to-render-contract`** — `/home/user/lypning/nemotron/pipeline/evaluate.py:78`  
`prompt_signature()` hashes only SYSTEM_PROMPT and USER_TEMPLATE. `render_contract` produces roughly a third of the user message that is actually sent and is not hashed, so the module docstring's guarantee #3 — 'The exact template is hashed into the summary; change it and the hash changes, and a later run that does not match the baseline's hash is flagged rather than compared' — is false.

> Makes runs silently incomparable. Every run in runs/ carries prompt_sha cbb7be44937a6b41; I replaced render_contract with a completely different contract, verified the rendered user message changed, and prompt_signature() returned cbb7be44937a6b41 unchanged. Any future candidate arm whose contract wording differs will be compared to the 35.1% baseline as if the prompt were identical, and `nt compare`'s WIN/no verdict will be drawn across two different prompts.

**`end-to-end/stable-gate-is-vacuous-for-rewrite-cases`** — `/home/user/lypning/nemotron/pipeline/acceptance.py:344`  
The `stable` gate compares pass/fail verdicts rather than outputs, and for a rewrite case the witness is the recorded *failing* program — so it only ever confirms that a known-failing program still fails, and admits cases whose expected stdout is a wall-clock timestamp, a wall-clock timing, or a pid.

> Puts permanently unpassable cases into the frozen holdout denominator, deflating every pass rate measured on it now and in the future. ntx-fa3622ac596b prints `datetime.now().isoformat()`; ntx-5607e7b17b22 prints millisecond timings; both are in the holdout, both score exactly 0.0 in headroom-k16, and no generated program can ever pass them. Three more of the same kind sit in train (ntx-f721dbb16b39 datetime, ntx-5ef1a234038c timings, ntx-8721e64bc8eb pid), where they permanently cost rejection-sampling yield. Together with the hash-order cases this is 14 of 249 corpus entries, 5 of 74 holdout cases.

**`gates-and-freeze/lock-hashes-provenance-so-enrichment-bricks-the-eval`** — `/home/user/lypning/nemotron/pipeline/split.py:96`  
The lock stores `sha256_of(by_id[i])` over the WHOLE case record, including `negatives` and the `gates` dict harvest bolts on at harvest.py:120 — but schema.py's own contract is that "enriching a case with another hard negative must not make it a different case". `freeze()` on the non-refreeze path only checks that held-out ids are still *present*, so `nt split` prints "already frozen" and exits 0 while `verify()` (and therefore every `nt eval` and `nt grade`) now fails permanently.

> The documented improvement loop — run the eval, re-harvest its failures with the `evalfail` adapter so each case gains its negative control — deadlocks the measurement: the only way out is `--refreeze`, which the tool itself says voids the baseline 35.1% and every delta against it. Also means any wording change in the gate-report strings invalidates the lock for all 74 held-out cases.

**`gates-and-freeze/no-literal-output-guard-in-evaluate`** — `/home/user/lypning/nemotron/pipeline/evaluate.py:256`  
`sample.py` rejects the degenerate `print("<expected output>")` solution via `looks_like_literal_output`, but `Evaluation._one` and `cmd_grade` grade with bare `run_test` and apply no such filter. Every held-out case — all 74, including the `lypning` kind, where printing the literal both reproduces CPython and routes tier-1 — is passed by a program that only writes the expected bytes.

> The metric has no defence against output memorisation, and the SFT set is built from the same corpus with the same test form. Any post-fine-tune pass@1 on the 74-case holdout (the number the whole project exists to move) is inflatable to 100% by memorisation, and 24 of the 74 would not even be caught by the sampler's own filter if it were ported over. Makes the baseline-vs-tuned delta uninterpretable rather than wrong today.

**`gates-and-freeze/sample-train-cases-skips-split-verify`** — `/home/user/lypning/nemotron/pipeline/sample.py:70`  
`train_cases` reads the lock but never calls `split.verify`, and its contamination check `leaked = held & {c['id'] for c in train}` is tautologically empty because `train` was just defined as the cases whose id is NOT in `held`. `evaluate.load_holdout` does call verify; `nt sample` does not. So a held-out case whose prompt or test drifted (its id is content-derived, so any drift renames it) silently becomes a training case.

> Direct holdout-into-SFT contamination path. `nt eval` refuses on the same corpus, but `nt sample` proceeds, so the 83/175 yield and the 154 SFT examples can include held-out tasks — and every post-fine-tune pass@1 measured against the 74-case holdout would be inflated by an unknown amount with no error anywhere in the logs. Note the pipeline already produces exactly this drift: a re-harvest re-captures expect_stdout with hash randomization live (finding 1), which changes the test and therefore the id.

**`lypning-kind/refusal-contract-is-never-actually-checked`** — `nemotron/pipeline/acceptance.py:403`  
`_run_lypning` decides 'this was a refusal' from `r.exit_code == eng.REFUSAL_EXIT` alone. It never checks that stdout is empty and never checks that stderr carries a parseable `<engine>: unsupported: <kind>: <detail>` line — `eng.parse_refusal` is not called anywhere in acceptance.py. The harvest path does check (`lypning_source.classify_entry` rejects 'exit 90 without a refusal line (contract broken)'), so the eval path is strictly weaker than the harvest path on the invariant it depends on. It also cannot tell an engine refusal from a program that exits 90 under its own power.

> Any future contract regression — an engine that emits partial stdout before refusing, or refuses without its line — is scored as a clean refusal, i.e. as a model failure, silently deflating every rewrite-slice pass@1 instead of raising a harness error. Today the binaries are clean (verified over 113 real refusals), so no already-reported number is wrong; the check that would keep it that way does not exist.

**`sandbox/unshare-runtime-failure-scored-as-the-program`** — `nemotron/pipeline/sandbox.py:220`  
`netns_available()` probes `unshare -n -- true` once per process and caches the answer in a module global forever; the answer is never re-checked and is never recorded on the RunResult (despite the docstring at line 187 claiming "The result records which way it went"). If unshare succeeds at probe time and fails later — namespace limit, cgroup pressure, a different host — the wrapper's own exit 1 and its stderr are attributed to the generated program.

> A failing isolation wrapper is scored as `reason="exit"`, i.e. a model failure, for every remaining case in the process: that would deflate pass@1 to ~0 and the summary would show harness_errors=0, so the run would look real. The opposite miss is silent: if the one-shot probe returns False transiently, the whole run executes with no network isolation and nothing in attempts.jsonl or summary.json records it, so a program that reaches the network can pass a case it should fail (inflates) and the run is not comparable with an isolated one.

**`statistics/paired-delta-drops-cases-silently`** — `nemotron/pipeline/stats.py:101`  
paired_delta intersects the two runs' case sets and reports only n_pairs; it never reports, or flags, how many cases were present in one run and not the other.

> The `paired delta ... over N cases` line of `nt compare`. Direction: arbitrary, and biased whenever the missing cases are non-random (an aborted run stops on the slow/hard cases; a case whose every draw errored disappears entirely). Comparing the full 74-case baseline against a run that only reached 20 cases prints "paired delta +15.0pp over 20 cases" with no hint that 54 cases were discarded; the reader has no way to tell a 74-pair comparison from a 20-pair one except by noticing the count.

**`statistics/pass-at-k-draw-weighted-vs-case-weighted`** — `nemotron/pipeline/stats.py:141`  
pass_at_k reports pass_at_1 = total_passes/total_draws (draw-weighted) while every other site reports mean-of-per-case-means (case-weighted), sets k = max(draws) even when draws are ragged, and computes headroom against the draw-weighted figure — so the headroom column does not equal the pass@k minus the pass@1 printed beside it, and "pass@16" is really pass@min(draws) for the short cases.

> runs/*/summary.json's `pass_at_k.pass_at_1` and `pass_at_k.headroom`, and the `pass@k` / `headroom` columns of `nt slices` — i.e. the reported "pass@16 26.9%" and "+15.4pp" that justify funding the SFT run. Direction: pass@k is deflated (a case with 2 draws is counted as if it had had 16 chances) while pass@1 is simultaneously inflated toward the cases that happened to complete all their draws; headroom is therefore wrong in an unsigned direction and summary.json ends up carrying two different numbers both named pass@1. Today all five runs happen to have a perfectly uniform k=16, so the published 26.9%/+15.4pp are correct; a single harness_error or a spend-cap abort breaks it, because summarize_run drops harness_error attempts from the denominator by design (evaluate.py:295-299).

**`statistics/significant-is-one-sided`** — `nemotron/pipeline/stats.py:123`  
paired_delta sets significant = (lo > 0.0), so a paired interval lying entirely BELOW zero — an unambiguous regression — is reported as not significant, and cmd_compare prints "not separable from noise" for it.

> The verdict line of `nt compare`. Direction: hides regressions. A tuned model that is significantly WORSE than the baseline on the paired test is reported as indistinguishable from it, which is precisely the failure the skill's "correctness outranks the route" rule is meant to catch. The live `nt compare headroom-k16 replay-check` already sits on the boundary (delta -0.169pp, CI [-0.507pp, +0.000pp], printed "not separable from noise"); one more flipped case and the CI is strictly negative and still printed that way.


### robustness

**`data-integrity/extract-empty-fence-returns-empty-string`** — `/home/user/lypning/nemotron/pipeline/extract.py:58`  
An empty fenced block yields `("", "fenced-python")`, and both call sites guard with `if program is None` (evaluate.py:251, sample.py:132), so the empty string is not caught: it is written to a temp file, executed, and graded as a wrong answer instead of as no-code.

> Corrupts the reported diagnostics of runs/headroom-k16: extraction_by_kind is reported as {'fenced-python': 1184} (100% clean formatting) when 2 of those attempts extracted an empty program, and those 2 are inside the reported 'wrong-output: 664' rather than in a no-code bucket. Pass@1 is unaffected today only because no corpus case has an empty expect_stdout; a case that did would have empty programs scored as PASSES.

**`sandbox/process-group-kill-is-not-a-containment`** — `nemotron/pipeline/sandbox.py:287`  
The process group is killed only on the timeout path (line 252), and `_kill_group` kills exactly the child's own group, so (a) a program that exits 0 leaving background children leaks them forever — no kill is ever attempted — and (b) a grandchild that calls `os.setsid()` leaves the group and survives the timeout SIGKILL. RLIMIT_NPROC is also not applied at all by default (`nproc=0`, line 108), so there is no bound on how many such processes a program may leave behind.

> Leaked spinners and memory hogs accumulate across a 1184-attempt run and steal CPU from later cases, whose wall-clock timeouts then fire — each such timeout is scored as a failed program, deflating pass@1 and pass@16 in a way that depends on run order and is invisible in the summary (harness_errors stays 0). Also falsifies the module docstring's "a wall-clock timeout enforced by killing the whole process *group*": survivors keep the inherited .ntx-stdout fd and keep writing into a directory the harness has already rmtree'd.

**`sandbox/truncation-is-never-detected`** — `nemotron/pipeline/sandbox.py:135`  
`RunResult.truncated` can never be True: RLIMIT_FSIZE is set to exactly `output_cap` (line 104), so the capture file can never grow past the cap, and `_read_capped` only reports truncation when `size > cap`. The program is killed/errored at the cap instead, and no consumer of RunResult reads `truncated` anyway (grep finds only the two definition sites).

> A program whose stdout exceeds the 8MB cap is scored as a failing program (exit 120/1 with an OSError traceback) rather than as an over-long output, so the failure is classified as the model's; and the one flag that was supposed to make over-long output visible is dead. No currently reported number is affected — no corpus case comes near 8MB — but the classification of any future large-output case is wrong by construction.

**`statistics/mcnemar-on-fractional-means-is-not-mcnemar`** — `nemotron/pipeline/stats.py:113`  
gained/lost are counted as after[c] > before[c] on fractional per-case means, so for any k>1 comparison _mcnemar is fed a sign test on continuous jitter, not McNemar's discordant binary pairs; the arithmetic inside _mcnemar is exact and correct, its input is not what its name claims.

> The `McNemar p=` figure in `nt compare` whenever either run has samples>1 — which is every headroom-style run. Direction: the p-value answers "did the pass fraction move at all in this direction", so a 3/16 -> 4/16 wobble counts as a full case-level gain; magnitude is discarded and the discordant-pair count is inflated by pure resampling noise. Splitting headroom-k16 in half by draw (same configuration, same model, true delta zero) already yields gained=6, lost=11 — seventeen 'discordant pairs' manufactured entirely by sampling jitter. The p-value stays roughly calibrated only because gains and losses are symmetric under the null; it will mis-rank real effects because it ignores how far each case moved.

**`statistics/slices-bootstrap-depends-on-dict-order`** — `nemotron/pipeline/cli.py:617`  
cmd_slices feeds stats.summarize a list built from per_case.values() — dict insertion order, i.e. the order threads happened to finish and append to attempts.jsonl — while random.choices picks by index, so the seeded bootstrap is a function of the row order, not only of the scores. summarize_run sorts (evaluate.py:300) and paired_delta sorts (stats.py:101); this one site does not.

> The `95% CI (bootstrap)` column of `nt slices`, including the published rewrite-slice interval [5.6%, 18.5%]. Direction: non-reproducible rather than systematically biased — permuting the lines of an unchanged attempts.jsonl moves the refused-slice upper bound from 18.5% to 18.4% and the saturated-slice upper bound from 99.2% to 100.0%. Two evals with byte-identical per-case results but different thread completion order publish different intervals, which contradicts stats.py's own docstring promise of a seeded, reproducible interval.


## Refuted (19) — do not "fix" these

- `statistics/beats-ignores-baseline-sampling-error` — beats() compares the candidate's CI lower bound to the baseline's POINT estimate, treating a number measured on 74 cases (SE ~5.5pp) as if it were kno
- `statistics/bootstrap-percentile-index-off-by-one` — bootstrap_ci takes means[int(0.025*resamples)] and means[int(0.975*resamples)], i.e. the 251st and 9751st order statistics of 10,000 — both endpoints
- `sandbox/verdict-harness-error-property-misses-four-sites` — `Verdict.harness_error` only reports a harness error when an underlying RunResult carries one, but four call sites build `Verdict(False, "harness-erro
- `sandbox/setup-io-oserror-escapes-run_python` — Only `mkdtemp` and the Popen block are wrapped; `entry_path.write_text` (line 214) and `materialize`'s `write_text`/`write_bytes` (lines 156-160) let
- `sandbox/preexec-fn-under-threads` — `preexec_fn` is used for every spawn, and every caller of run_python that produces a reported number (Evaluation.run, sample_targets) drives it from a
- `gates-and-freeze/case-id-embeds-the-engine-chain` — `to_candidate` writes `"engines": list(eng.DEFAULT_CHAIN)` and `"require_tier1": not is_ceiling(...)` into the test dict, and `schema.case_id` hashes
- `lypning-kind/ceiling-slice-is-transcription-not-fallback` — The `ceiling` slice does not measure fallback behaviour at all: `_run_lypning` returns `pass` for a ceiling case on CPython correctness alone, and eve
- `lypning-kind/harness-files-pollute-the-program-cwd` — For the `lypning` kind both the CPython reference run and every engine run are made with `keep_workdir=None` and no `scratch_dir`, so `sandbox.run_pyt
- `lypning-kind/is_ceiling-misses-the-ceilings-its-own-docstring-names` — `CEILING_KINDS = ('bigint',)` and `CEILING_DETAILS` is consulted only for `kind in ('module-attr','builtin')`, but both module docstrings define the c
- `lypning-kind/ceiling-cases-never-check-the-route-so-hand-rolling-is-invisible` — A ceiling case returns pass on CPython correctness alone and never runs an engine, so the test cannot distinguish the behaviour it exists to reward (f
- `data-integrity/extract-empty-reasoning-bypasses-strip` — `text = content if reasoning is not None else strip_reasoning(content or "")` treats a *present but empty* reasoning field (`reasoning == ""`, which i
- `data-integrity/fold-draws-dedupe-is-exact-string-only` — `{d["program"] for d in ds}` dedupes on byte equality only, so two draws that differ by a single blank line or a comment both survive and both become
- `data-integrity/train-cases-leak-assert-is-vacuous` — `train = [c for c in cases if c['id'] not in held]` followed by `leaked = held & {c['id'] for c in train}` can never be non-empty — the set difference
- `data-integrity/render-contract-argv-unquoted-and-silent-on-the-engine` — `render_contract` builds the command line by bare string concatenation of argv, so a shell-metacharacter argument is rendered as if it were shell synt
- `data-integrity/extract-nested-fence-and-last-block-wins` — `_FENCE` is non-greedy and stops at the first closing ```, so a program containing a fence inside a string or docstring is truncated at that point; an
- `end-to-end/engine-mismatch-scored-as-model-failure` — acceptance.py's docstring promises a MISMATCH "is reported under its own category so it can never be quietly counted as the model getting something wr
- `end-to-end/eval-has-no-cheat-guard` — sample.py rejects a verified program that carries the expected output as a literal, but evaluate.py applies no such guard — so a literal-output cheat
- `end-to-end/ceiling-slice-is-copy-paste` — A ceiling case's prompt is built by `_describe()` as "Produce exactly the output this program produces ... ```python <the original program>```" and it
- `end-to-end/baseline-arm-not-comparable` — The baseline arm (thinking on, max_tokens 12288, k=1) spent 24 of its 74 attempts hitting the token cap with finish_reason="length" and no extractable

## Corpus recovery: measured, and recommended against

Five agents each took one class of discarded corpus entry and measured, by running
it, how many become real cases. **All five recommend against their own class.** The
corpus does not need to be bigger; its existing 74 held-out cases need to mean what
they say.

| class | entries | usable | become cases | recommend |
|---|---|---|---|---|
| filenotfound | 1201 | 139 | **7** | no |
| no-stdout (unusable, reason "CPython gives no clean answer: exit 0: ") | 205 | 158 | **3** | no |
| json-decode | 111 | 88 | **0** | no |
| import-and-syntax | 78 | 130 | **57** | no |
| indexerror | 62 | 36 | **7** | no |


---

# Completeness critic

What the six lenses and the recovery study missed. It found the two
highest-consequence items in the whole audit — the decode-budget false positive
and the unrecorded arm identity — and neither came from a lens.

I have enough. Writing up what the audit and recovery study missed.

---

# 1. The code path no lens examined: `pipeline/backends.py`

**Nobody looked at it.** All 33 surviving findings and all 19 refutations cite `sandbox.py`, `acceptance.py`, `stats.py`, `cli.py`, `sample.py`, `evaluate.py`, `lypning_source.py`, `extract.py`, `classify.py`, `split.py`. `backends.py` appears exactly once, in passing, inside a refutation (`backends.py:143`). The test suite instantiates `ChatBackend("http://x/v1","m")` once as a constructor for a GPU-cost assertion (`tests/test_harvest_eval.py:160-167`) and never calls `complete()`. Also untouched: `jsonio.py`, `adapters.py`, `harvest.py`.

This matters more than the others because backends.py is where **arm identity** is decided — the one thing a fine-tune experiment cannot get wrong.

### 1a. The arm's identity is a free-text string the operator typed, and it does not survive into the comparison

`ChatBackend.identity()` (`backends.py:78-80`) returns `{"base_url", "model"}` — both echoes of what the caller *requested*. `_to_completion` (`backends.py:133-148`) reads `choices[0]` and `usage` and **discards `data["model"]`**, the field in which the server reports what actually served the request. `Completion.raw` (`backends.py:45`) is declared and never populated, so the response is not retained either. Then `summarize_run` (`evaluate.py:311`) copies only `meta["backend"]["model"]` into summary.json — **`base_url` is dropped**. And `cmd_results` (`cli.py:513-522`) gates comparability on `holdout_manifest_sha256` and `prompt_sha` only.

Measured on the shipped runs:

```
baseline-nemotron35-bf16-t12k  base_url ev8eognh8prrpgs7...  model "nemotron"
headroom-k16                   base_url ebro2v0fa9zgvjs9...  model "nemotron"
```

Two **different HF endpoints**, both reported as model `nemotron`, both printed in the same `nt results` table with a delta. Nothing recorded — not checkpoint, revision, adapter name, quantization, server version, or chat template — distinguishes the base model from base+LoRA except a string. For the go/no-go on a LoRA, `nt results` can print `win: yes` where the only recorded difference between the two arms is what the operator typed after `--model`.

### 1b. Resume silently blends arms — and the blend is undetectable afterwards

`Evaluation.run()` (`evaluate.py:189`) unconditionally overwrites `meta.json` with the *current* settings; `_completed_keys()` (`evaluate.py:184-186`) then keys resumable work on `(case_id, sample)` **only**. The per-attempt record (`evaluate.py:224-227`) carries `run_id, case_id, sample, category_prior, ts` — **no model, no base_url, no sampling block**.

So `nt eval --run-id X` re-run after a server restart, an endpoint swap, an adapter that silently failed to load, or a changed `--max-tokens`/`--no-thinking`, resumes X, keeps the old attempts, and writes a meta.json that claims the *new* configuration for all of them. There is no check and no residue. This is the single cheapest way to produce a fraudulent LoRA win by accident, and it is one tmux reconnection away.

### 1c. Smaller, real

- `complete()` retries `TimeoutError`/`URLError` (`backends.py:127-131`) with no idempotency: a 600 s request that succeeded server-side but timed out client-side is re-billed and re-sampled.
- `probe()` (`backends.py:153-158`) checks reachability, never that the served model is the requested one.
- `harvest()` writes `corpus.jsonl` with `write_jsonl` (`harvest.py:131`) — a **full overwrite** of only the current sources' output. `split.py:15` promises "growth only ever lands in train"; harvest has no merge with the existing corpus, so `nt harvest --source evalfail` truncates the corpus and `freeze()` then raises on the missing held-out ids. The documented improvement loop is not implemented on the harvest side.
- `harvest.py:59-60` upgrades `category` only from `"unobserved"`, so category is **first-writer-wins across `--source` order**. `evalfail` yields classify.py vocabulary (`wrong-output`) while `lypning` yields `refused:module`. Flipping the order of two `--source` flags changes a case's category, which is the stratification key (`split.py:42`) and the slice key (`cli.py` `cmd_slices`). Same corpus, different holdout on a refreeze, different slice table always.

---

# 2. The weakest link nobody considered: the **generation** step. Decode budget is scored as model capability.

Every lens started at `sandbox.py` or later. Nothing examined what happens between the prompt and the program. Here is what is there.

`Completion.finish_reason` is recorded on every attempt (`evaluate.py:245`) and **read by nothing**. `grep -rn finish_reason pipeline/ tests/` returns four lines, all writes. `summarize_run` does not aggregate it; `render_summary` does not print it; `nt results` and `nt compare` cannot see it.

Measured on the shipped runs:

| run | truncated (`finish_reason="length"`) | pass rate on truncated | pass rate on `stop` |
|---|---|---|---|
| `baseline-nemotron35-bf16` (4096, thinking on) | **43 / 74 (58%)** | **0 / 43** | 21/31 = 68% |
| `baseline-...-t12k` (12288, thinking on) — **the published 35.1%** | **24 / 74 (32%)** | **0 / 24** | 26/50 = 52% |
| `headroom-k16` (2048, thinking off) | 11 / 1184 | 0 / 11 | — |
| `stock-nothinking` (12288, off) | 0 / 74 | — | 26/74 |

**One third of the published baseline's denominator is the model exhausting its thinking budget, scored as a model failure.** In the 12288 run all 24 land in `failures_by_category` as `no-code` — indistinguishable from "the model declined to write code". In the 4096 run, 2 truncations were extracted as `fenced-python` and filed as **`syntax-error`**, because `_FENCE` at `extract.py:23` terminates on `(?:```|\Z)` and therefore **accepts an unclosed fence**: a program chopped mid-line is graded as the model writing broken Python. In `headroom-k16`, 11 truncations are filed as `wrong-output` (7), `syntax-error` (3), `runtime-error` (1). `extract.py`'s own docstring says `no-code` exists because "the model rambled" and "the model wrote a bug" need different fixes; truncation is a third thing, and it is silently distributed across both.

The audit found that `cmd_results` ignores the `sampling` block and therefore prints `-6.8pp` for a pure token-budget difference. It did not identify **why** that lever exists or how strong it is. It is this: decode budget converts directly into scored pass rate at 0.6–1.4 cases per truncation avoided, and the conversion is invisible in every report.

**And the residual noise is larger than anyone stated.** `baseline-...-t12k` and `stock-nothinking` are the *same checkpoint, same prompt, same holdout*, differing only in `enable_thinking`. Both score exactly 26/74:

```
$ nt compare baseline-nemotron35-bf16-t12k stock-nothinking
paired delta +0.0pp   95% CI [-9.5, +9.5]pp   over 74 cases
gained 6   lost 6   McNemar p=1.0000   not separable from noise
```

**12 of 74 cases flip under a null comparison.** That is the instrument's floor: `gained 6 / lost 6` is what "no change whatsoever" looks like on this holdout.

### The second unexamined link: the freeze guarantees id-disjointness, not independence

`schema.case_id` hashes `(prompt, test)` (`schema.py:22-23`); `split.verify` (`split.py:108-124`) only checks that each locked held-out id is still present with an unchanged sha. **Nothing anywhere checks that a train case is not a near-duplicate of a held-out case** — and the corpus is capture-derived from agents who run near-identical one-liners repeatedly. Measured on the shipped 249:

- 27 of 74 held-out cases have a train neighbour at ≥0.85 prompt similarity; 19 at ≥0.90; 11 at ≥0.95.
- `ntx-fa3622ac596b` (holdout) and `ntx-f721dbb16b39` (train) have **byte-identical prompts**. They are different cases only because the frozen `expect_stdout` is two `datetime.now()` captures 12 seconds apart. Nondeterministic capture *manufactures* cross-split twins.
- `ntx-5d3a1e41f193` (holdout) vs `ntx-511c19ed88fc` (train): same program modulo one space, **identical `expect_stdout` `'builtins\n'`**. A real, passable, memorizable pair.
- `ntx-4bf956899386` (holdout) vs `ntx-a690c8ee4e7e` (train): same program, identical expect, **and the train twin is already one of the 154 rows in `data/sft/v1/sft.jsonl`**.

Three exploitable pairs today — small. But the mechanism is completely unguarded, it grows with every capture session, and `nt sample` (which already cannot detect leakage; its check is tautological per the existing finding) will train on them.

`adapters.evalfail_adapter` (`adapters.py:97-122`) makes it worse by design: it reads an eval's `attempts.jsonl`, which covers **only the 74 held-out cases**, and re-emits them as candidates. The only documented growth loop in the project feeds held-out cases back into the corpus builder, gaining `negatives` and possibly a rewritten `category`, both of which are inside `sha256_of(case)` and therefore break the lock forever. There is no adapter that harvests *train* failures at all, and `nt sample`'s 2800 draws over train are never harvested.

---

# 3. The most likely false positive and false negative

## FALSE POSITIVE: the LoRA stops the model thinking, and truncated-to-zero becomes answered

This is near-certain to happen, not hypothetical. `sample_targets` draws with `enable_thinking=False` (`sample.py:99`) and writes assistant turns as a bare ```` ```python ```` block with no reasoning (`sample.py:196`). The SFT set therefore teaches exactly one behaviour above all others: **answer immediately, do not think**. The tuned model will have a near-zero truncation rate by construction.

If the tuned arm is then served against the published thinking-on baseline (which is what `nt results` invites — it compares against `data/baseline.json` and checks neither `sampling` nor `backend`), 24 of 74 baseline cases scored 0 for budget exhaustion become answerable. Measured recovery on exactly those 24 cases: the *same unmodified model* with thinking off passes 3 of them (+4.1pp), and reaches 5 at pass@16 (+6.8pp). That is free movement toward the win threshold with zero capability gain — and it arrives labelled "the fine-tune fixed 3 cases".

Runner-up, and it compounds: the memorization channel. `Evaluation._one` (`evaluate.py:256`) applies no anti-cheat filter (existing finding), the SFT set already contains a byte-equivalent twin of a held-out case, and shortest-wins selection already put literal-output programs into training (existing finding). A LoRA that learns "print the literal" moves the rewrite slice toward 100%.

**Defended?** No, in all three directions. `cmd_results` checks manifest + prompt hash only; `cmd_promote` checks nothing; `finish_reason` is unread; `summary.json` has no `base_url`; nothing compares near-duplicates across the split.

**How to defend, cheaply:**
1. Refuse to compute a delta unless `summary["sampling"]` matches the baseline's on `enable_thinking`, `max_tokens`, `temperature`, `top_p`, `samples` — the block is already in the file; add four lines to `cli.py:513-522`.
2. Aggregate `finish_reason` into `summarize_run` and print `truncated N` in `render_summary`; refuse a WIN verdict when the two arms' truncation rates differ by more than a couple of points. A pass rate computed over a denominator where one third is budget exhaustion is not a capability number.
3. Record the server's `data["model"]` and the full `base_url` in every attempt and in `summary.json`; make resume abort when the recorded backend or sampling of existing attempts differs from the current one.
4. Run the tuned arm at the baseline's exact decode settings, and additionally run the **unmodified base model through the same serving stack in the same session** as a null arm. If the null arm moves, the instrument moved.

## FALSE NEGATIVE: the holdout cannot resolve a plausible LoRA effect at all

Simulated against the real per-case baseline data (74 cases, rewrite slice 52 cases, 43 of them currently at 0), asking how many newly-solved rewrite cases it takes before `nt results` prints `win`:

```
newly solves  4 cases (+ 5.4pp) -> WIN   0% of the time
newly solves  6 cases (+ 8.1pp) -> WIN   0%
newly solves  8 cases (+10.8pp) -> WIN   0%
newly solves 10 cases (+13.5pp) -> WIN 100%
newly solves 12 cases (+16.2pp) -> WIN 100%
```

**The headline verdict cannot fire below roughly +11pp on the whole holdout — the rewrite slice would have to go 11.5% → ~30%, nearly tripling.** A LoRA that genuinely fixes 6 of 43 dead rewrite cases — a good result for a brief tune on 154 examples — is reported as "no run clears the bar". And the decision threshold (10 newly-solved cases) sits barely above the instrument's own null flip magnitude (6 gained under a true-zero comparison): signal-to-noise at the decision boundary is about 1.7×.

Aggravating it: the win rule is computed on the **blended 74**, while the experiment's hypothesis is about the **52-case rewrite slice**. Diluting the target slice with 14 ceiling cases at 92% and 8 saturated cases at 96.9% — populations no LoRA can move — throws away roughly 30% of the denominator as pure ballast. And the existing findings subtract further: 5 held-out cases are unpassable by construction, 4 more are lypning-tooling programs at 0/16, 3 engine-mismatches are filed as model failures — ~12 of 74 slots that no fine-tune can ever move, all sitting in the rewrite slice, all inside the denominator of the number the bar is set on.

**Defended?** No. `stats.beats` is a pre-registered rule (defensible, and the earlier lens correctly refuted the claim that it is anti-conservative), but nobody computed its **power**, and the pre-registered quantity is the wrong slice.

**How to defend:** pre-register the **rewrite slice pass@1** as the primary endpoint with the paired test (`paired_delta`, which is far more powerful here) as the primary instrument and the unpaired rule as a secondary; fix the sign bug in `paired_delta.significant` first (existing finding — it currently reports a strictly-negative CI as "not separable"); drop the unpassable and tooling cases from the denominator *before* the tuned run so the change is not post-hoc; and state the MDE in the run plan. If the honest MDE is +11pp on 74 cases, either widen the holdout before training or accept up front that the experiment can only detect a large effect.

---

# 4. Fix before spending on 8×H100

### Blockers — a tuned run measured today is uninterpretable regardless of outcome

1. **`sandbox.py` `-E` kills `PYTHONHASHSEED=0`** (`sandbox.py:104-120` vs the `[sys.executable,"-E","-s",entry]` spawn). Highest-confidence, most-reported, and it poisons *three* things at once: it randomizes every reported number, it froze 10–14 corpus cases to an unreproducible expect_stdout, and it forces rejection sampling to accept programs that hardcode one seed's set order into the SFT set. Everything downstream is measured through it. One character.
2. **Anti-cheat on the eval path.** `evaluate._one` (`evaluate.py:256`) and `cmd_grade` never call `looks_like_literal_output`; the guard exists only in the sampler. The metric the LoRA is graded on has no defence against the exact behaviour a route-optimizing tune is most likely to learn, and 18/18 unsolved held-out cases were demonstrably passed by a chunked string literal. Fix before training, not after — the same hole also governs which SFT targets get accepted.
3. **Record and enforce arm identity** (§1a/1b). Server-reported model + full base_url on every attempt and in summary.json; resume aborts on a config change. Cheapest item on this list and the only one that defends against an accidentally fraudulent win.
4. **Gate the delta on the `sampling` block, and surface `finish_reason`** (§2, §3-FP). Four lines in `cli.py:513-522` plus a Counter in `summarize_run`. Without it the decode budget is a free ±7pp lever on the headline.
5. **Purge the dead denominator before the tuned run.** The 5 unpassable cases, the 4 lypning-tooling rewrite cases, and the `engine-mismatch` → `wrong-output` misfiling (`evaluate.py:261-263`, `classify.py:57`). ~12 of 74 slots that no fine-tune can move, concentrated in the target slice. Doing this *after* seeing the tuned result is unpublishable; doing it now costs a re-freeze that has not yet been spent against.
6. **Pre-register the endpoint and state the power** (§3-FN). Rewrite slice, paired instrument, MDE written down. Free. Skipping it means paying for a run that cannot answer its own question.
7. **Fix `paired_delta.significant = (lo > 0.0)`** (`stats.py`). A one-character fix that currently hides regressions — the exact failure the project's correctness-first rule exists to catch, and the live `nt compare` is already sitting on that boundary.
8. **Contamination check before building the SFT set** (§2): reject any train case within a similarity threshold of a held-out case, and stop `evalfail` from re-ingesting held-out cases. Three pairs today, one already in `sft.jsonl`; the cost of shipping it is that the post-tune number cannot be defended.

### Worth doing, but after the money is committed

9. `cmd_results` should require `cases_evaluated == cases_planned`, and `cmd_promote` should require anything at all.
10. `stats.pass_at_k` draw-weighted vs case-weighted mismatch (correct today only because every run happens to have uniform k=16 — it breaks on the first `harness_error`).
11. `cmd_slices` bootstrap depends on thread completion order (`per_case.values()` unsorted while `summarize_run` and `paired_delta` both sort) — non-reproducible intervals, no bias.
12. `_run_lypning` never checks the refusal contract (empty stdout, parseable line); the eval path is strictly weaker than the harvest path on the invariant it depends on.
13. `harvest()` is destructive and category is `--source`-order dependent (§1c). Matters the next time the corpus grows, not for this run.
14. `classify()` has no `refused` arm, so `failures_by_category` reports 664 `wrong-output` where the truth is 285 wrong / 376 refused / 3 engine-mismatch. Diagnostic only — but it is the number that would tell you *whether the tune worked on route or on correctness*, so fix it before reading the tuned run's diagnostics.
15. Unclosed-fence acceptance in `extract.py:23`, empty-fence → `""` not `None`, `preexec_fn` under threads, `RunResult.truncated` dead, leaked process groups.

### Do not fix

Everything in the REFUTED list, and none of the four recovery studies — all four recommend against themselves (7, 2, 0 and 57-but-contaminated cases), and each adds a second copy of a seeding rule the host repo deliberately keeps in one place. The corpus does not need to be bigger before this experiment. It needs its existing 74 cases to mean what they say.
