# Refactor design — 2026-09-11

Four architects with deliberately opposed starting biases, three judges each,
scored on defects-designed-out, seams, migration realism, house fit and RESTRAINT.

**The minimal-change advocate won.** Its brief was to argue that a major refactor
is not warranted, and all three judges agreed — 9/10 on restraint from each.

## Ranking

| architecture | mean /50 | verdict |
|---|---|---|
| `minimal-change` — Three records, one grader (a 4-file change, not a refactor) | **43.3** | ADOPTED AS SPINE |
| `by-invariant` — Four records and a net: a provenance-typed measurement pipel | **38.0** | grafted / dropped |
| `by-lifecycle` — Four steps on one kernel: the Arm, the Outcome, and the Coho | **37.0** | grafted / dropped |
| `by-dependency` — Layers by effect, with the measurement's identity as a type | **34.3** | grafted / dropped |

## What each judge said was worth stealing

- **from `minimal-change`** — Putting the held-out filter in harvest.collect() rather than in the evalfail adapter. I verified this is a true chokepoint: schema.make_case has exactly one caller (harvest.collect:65) and collect has exactly one caller (harvest.harvest), so ~8 lines there bind every adapter that exists and every adapter anyone writes later. It closes two audit findings at once — the evalfail held-out re-ingest AND `lock-hashes-provenance-so-enrichment-bricks-the-eval` — because a locked case that never reaches the merge can never gain a negative or a rewritten category, and therefore sha256_of(case) cannot move. It is the one place in the plan where a boundary does strictly more work than a check would, and it costs almost nothing.

- **from `minimal-change`** — The asymmetric typing rule — type what is COMPUTED (outcome, arm, comparison), never type what is HASHED (the case record, the lock) — paired with its practical twin, the held-out chokepoint in harvest.collect(). I verified the chokepoint: make_case is called from exactly one library site (harvest.py:65 inside collect), and collect has exactly one library caller (harvest.py:104). Everything else reaches a case through those two lines. Filtering locked ids there genuinely closes defect 8 for every adapter that exists and every adapter anyone writes later, at a cost of ~8 lines and one extra argument — and it does so without touching adapters.py or the bytes of the record whose sha256 is inside holdout.lock.json. The rejection of records.py is argued from the code, not from taste: split.freeze stores sha256_of(whole case record) including the gates dict harvest bolts on, and the audit's lock-hashes-provenance finding is the warning shot that a dataclass migration would have fired for real.

- **from `minimal-change`** — Step 0: write tests/test_recorded.py BEFORE touching anything (re-summarize all five runs in runs/ from attempts.jsonl, assert pass_rate byte-identical, assert split.verify is True), `git mv summary.json summary.as-reported.json` so the published number stays in the tree beside every later correction, and then the rule that exactly two of the eleven steps are allowed to move a number and each updates the pin in the same commit with the moved value written down. That makes every change to a reported number a diff someone signed, and it is worth stealing even if the whole refactor is rejected — it costs 60 lines and is the only thing standing between "we fixed the instrument" and "we moved the baseline and nobody noticed." Second-best, and the more original: the rule in the rejection section — type the things that are COMPUTED (outcome, arm, verdict), leave alone the things that are HASHED (the case, the lock) — which is earned from split.freeze storing sha256_of(the whole case record) and is the correct reason not to write records.py.

- **from `by-invariant`** — Arm as a record whose only constructor is a witnessed probe (`Arm.confirm()` reading `data["model"]` off a real response), plus the resume key becoming `(arm_id, case_id, sample)` and `fold` refusing a multi-arm attempts file. This is stealable on its own in a day without any of the rest: `backends._to_completion` already drops `data["model"]` and `Completion.raw` is already a declared-but-never-populated field, so the plumbing is a two-line change; `_completed_keys()` is one set comprehension; `summarize_run` already copies `meta["backend"]["model"]` and would just copy the whole block. It is the only change here that defends against an accidentally fraudulent LoRA win — I verified the two shipped runs really do carry different HF endpoints (ev8eognh8… vs ebro2v0fa9…) under the same model string "nemotron", and `nt results` really does difference them. Second-best, and also stealable alone: `store.merge(existing_corpus, incoming, lock)` as the *only* writer of corpus.jsonl, taking the existing corpus as a required argument — that makes harvest's truncating `write_jsonl` (harvest.py:131) unwritable rather than merely fixed.

- **from `by-invariant`** — The closed Outcome algebra with a total Reason→category table, plus typing fold's denominator to Passed|ModelFailure only. I checked the code path: `Verdict.harness_error` is a property over two optional RunResults (acceptance.py:88-93), so all eight `Verdict(False,"harness-error",...)` sites that pass a *clean* RunResult report None — acceptance.py:399 `Verdict(False,"harness-error","engine not built: %s" % name, run=ref)` is the worst: with the oracle/engines absent (the host repo's own default!), every attempt in an eval is scored `passed=False` and then classify() falls through its if-chain to "wrong-output". Making HarnessError and EngineMismatch *constructors* rather than string reasons, and attaching the report category to the Reason instead of re-deriving it downstream, kills four confirmed audit findings (#3, #4, refused-relabelled-wrong-output, engine-mismatch-as-model-failure) in ~110 lines, with no data migration and no re-measurement. Steal this even if the rest is rejected.

- **from `by-invariant`** — Step 0: build tests/golden/ FIRST, against unmodified code, re-folding all five runs in runs/ plus data/baseline.json and pinning every published number to the last digit — then require that every migration step which is supposed to move a number move it in that file explicitly, with the old value and the reason in the diff. This costs ~120 lines, requires zero reorganization, is the only part of the plan that survives rejecting the rest, and it is the repository's own invariant 3 ("never quote a remembered number") turned into a test instead of a habit. Runner-up worth stealing independently: Arm.confirm() — an arm identity that can only be constructed from an observed server response, with base_url + served_model + adapter + sampling + prompt_sha hashed into arm_id, and the resume key becoming (arm_id, case_id, sample). That one record kills defects 1, 2 and half of 5, and it does not need the package tree to work.

- **from `by-lifecycle`** — `Arm.observe(backend, sampling, prompt_sha)` — identity by probe, not by echo. `ChatBackend.identity()` (backends.py:78-80) returns the two strings the operator typed; `_to_completion` throws away `data["model"]`; `summarize_run` (evaluate.py:311) copies only `meta["backend"]["model"]` into summary.json, so base_url never reaches a comparison. I confirmed it on disk: `baseline-nemotron35-bf16*` and `stock-nothinking` are served from ev8eognh8prrpgs7…, `headroom-k16` from ebro2v0fa9zgvjs9…, and all four summaries say model "nemotron". Putting base_url + the *server's* reported model + the whole sampling block into one hash that every attempt carries is about forty lines, needs none of the package tree, and kills the cheapest route to an accidental LoRA "win". Steal Cohort (the hashed, named denominator with declared k and per-id exclusion reasons) second — it is what turns the comparability rule from four lines inside a renderer (cli.py:513-522) into a value that cannot be constructed.

- **from `by-lifecycle`** — Migration step 0: pin the recorded evidence as golden tests (249 cases, the 74 lock ids, baseline.json's pass_rate, byte-identical `nt results`/`slices`/`compare`/`status` renders over the five runs in runs/) BEFORE touching any code, and then predict in writing which published number each subsequent step must move, landing the golden update in the same commit as the fix. It is independent of the whole tree — it would have caught most of the 33 findings on its own, and it converts "a refactor must not change a number" into "a number may not change silently", which is the only property a 3,463-line instrument actually needs. The deepest structural idea is second: making HarnessFault a variant rather than Verdict.harness_error a derived property.

- **from `by-lifecycle`** — The closed `Outcome` sum in `kernel/outcome.py`: merge `acceptance.Verdict` + `classify.CATEGORIES` + `classify()`'s if-chain into one type where `HarnessFault` is a *variant* rather than a property derived from two optional RunResults, and the reason->category map is a dict keyed by every member of the reason enum with no default arm (assertable at import on py39, pinned by a totality test). That one change kills defects 3, 4 and 10 at once and repairs the two published runs' `failures_by_category` — and it is implementable against today's `acceptance.py`/`classify.py` in an afternoon with none of the package tree. Steal it even if the tree is rejected. Second-best and nearly free: migration step 0, pinning `nt results` / `nt slices` / `nt status` renders against the five recorded runs as golden files *before* touching anything, with every predicted number change (664 -> 285/376/3) written down before the step that causes it and landed in the same commit as its golden.

- **from `by-dependency`** — The pure grader validated by replay: make grading a total function `(TestSpec, Observation, engine Observations) -> Outcome` where Outcome is a closed sum (HarnessFailure | EngineMismatch | NoProgram | Graded), then prove it by replaying the 1,184 programs already stored in runs/headroom-k16/attempts.jsonl through the old and new graders and requiring identical verdicts outside the named nondeterministic cases. I checked: the programs really are in the attempts records, and failure_category is stored too, so both the equivalence gate and the v1->Outcome reader are actually feasible. That one change kills defects 3, 4, the divergence half of 6, and makes 10 visible — and it does not need five layers to land. Second steal: step 1's XFAIL_EDGES allowlist seeded with exactly the four known wrong-way arrows, so every later step is measured by that list shrinking. That is the best migration odometer in the proposal.

- **from `by-dependency`** — Make grading a total function of a recorded Observation — `judge/` may not import `effects/` — and prove it by replaying the 1,184 recorded programs of runs/headroom-k16 through the old and new graders for verdict equality. That one clause collapses the three divergent grading paths that exist today (evaluate._one, sample.one, cmd_grade's hand-rolled 30-line reimplementation with its own lazy imports of run_test/classify/extract_program) into one owner, and it makes grading testable without spawning — which is precisely why 62 green tests caught none of the 33 findings. Worth stealing even with the five-layer tree thrown away: judge/grade.py + model/outcome.py + the replay harness is ~400 lines and carries defects 3, 4, 6-divergence and 10 on its own.

- **from `by-dependency`** — "judge/ may not import effects/", i.e. one pure grader that is a total function from a recorded Observation to a closed Outcome sum, with counts_in_denominator defined once on the sum. This single clause is what kills audit findings 3, 4, 6-divergence and 10 simultaneously, and it is worth stealing even if the five-layer tree is rejected entirely: today acceptance.py interleaves spawning and deciding (_run_lypning spawns, compares, and invents the free-text reason that classify.py then re-derives a label from and falls through on), and that interleaving is the direct cause of Verdict(False,"harness-error",msg) scoring as a model failure, of "refused"/"engine-mismatch" landing in wrong-output, and of the eval path and the sampler grading by different rules. Second-best, and nearly as stealable: the migration's verification-by-replay — re-grade the 1,184 recorded programs of runs/headroom-k16 through the old and new graders and require identical verdicts on the deterministic subset, with the 13 nondeterministic cases named and excluded. That converts "the refactor preserved behaviour" from an opinion into a measurement, on a tree whose 62 green tests caught none of 33 findings.


## Costs the winning proposal states about itself

1. Three of the audit's highest-consequence items are untouched by every line of this. The `-E` flag, the dead denominator, the near-duplicates: one character, one refreeze, one similarity function. If anyone reads "we restructured the pipeline" as "we fixed the instrument", this proposal has done net harm. It is step zero of the fix list, not the fix list.

2. Four more files. 17 -> 20 modules, +327 lines. A reader who knew that `classify` lived in classify.py now has to learn that it lives in outcome.py, and there is no way to make that free.

3. The sum type is a costume. Python 3.9 with no `match` and no runtime `X | Y` means Outcome is a dataclass with a `kind` string and three constructors. The guarantee is "you cannot construct the ambiguous thing at runtime", not "a checker proved it". Someone determined enough can build the dataclass directly and defeat the whole mechanism; the only defence is that the constructors are the obvious path and there is a test.

4. Closing the reason set converts a silent misfiling into a crash. Add a test kind, forget its category, and a long eval raises mid-run instead of quietly filing it as wrong-output. The totality test catches it in CI first, but this is a genuinely new failure mode on a path that spends money.

5. Arm-keyed resume can cost real money. An endpoint that reports a version suffix, or a serving stack restarted onto a slightly different model string, will refuse to resume and re-spend a whole run. I am leaving `--resume-anyway` as a deliberate hole, and it records the blend explicitly in the attempts — but a loud hole is still a hole, and the pressure to use it will be highest at exactly the moment it matters most.

6. `nt results` gets worse to read before it gets better. The comparability guard will mark most existing pairs n/c: the leaderboard stops printing deltas until runs are re-measured at matched settings. That is correct and it will feel like a regression, and it lands on a project whose reason to exist is a delta.

7. Re-summarizing rewrites five committed summary.json files. They are derived and re-derivable, but they are also what was reported. Mitigated by keeping summary.as-reported.json beside them, which is five more files in the tree and a naming convention someone has to remember.

8. Concurrency cost, right now. Six of these modules are being patched by other agents as I write. Steps 1 and 2 touch acceptance.py, evaluate.py, sample.py and cli.py — four of the busiest. Every step is small and independently verifiable precisely because of this, but the merge cost is real and it is paid by whoever lands second.

9. The plan declines to fix cli.py's length. 660 lines is still the biggest file in the tree, and someone will reasonably want it split. I am betting that 14 greppable commands in one file beats 14 files plus an import graph, and that bet is a judgement call, not a proof.

---

# THE PLAN — reorganizing `/home/user/lypning/nemotron`

**Spine:** the `minimal-change` proposal (mean 43.3). Its central argument is correct and is adopted without dilution: 3,463 lines across 17 modules with an acyclic DAG is not a structurally broken codebase, and the three grander proposals each spend 600–1,400 new lines buying a directory tree that closes no defect the flat tree cannot close. What is grafted from the runners-up is every idea a judge named as stealable *independently of its tree* — the witnessed `Arm`, the hashed `Cohort`, `EngineMismatch` as a variant rather than a reason, `store.merge` taking the existing corpus as a required argument, and verification-by-replay over the 1,184 recorded programs. What is dropped is named in §6.

Two things this plan concedes up front, because the `minimal-change` author was right to lead with them:

- The highest-consequence finding in the audit (`-E` nullifying `PYTHONHASHSEED`, which randomizes every number in the repo) is a one-character fix inside `sandbox.py` that **no architecture reaches**. It is step 1 here, ahead of all structure, and it is the reason the migration order differs from every panelist's.
- Restructuring is not fixing the instrument. This plan is step zero of the audit's blocker list, not the blocker list.

---

## 1. Target tree

```
nemotron/
  nt                      UNCHANGED, byte for byte: exec python3 -m pipeline.cli "$@"
  nt-gpu                  UNCHANGED: cds into gpu/, drives gcloud, imports no pipeline code
  gpu/                    UNCHANGED: lora_rank16.yaml, launch.sh, reap.sh, budget.py — step 3's compute lives here
  data/                   UNCHANGED ON DISK, plus two sidecars (§5)
  runs/                   append-only history; summary.json re-derived, summary.as-reported.json kept beside it
  pipeline/
    __init__.py      15   VERSION and the four-step map. Unchanged.
    jsonio.py       107   THE canonical serializer; why a frozen split is checkable at all. Unchanged.
    sandbox.py     ~300   the net: temp cwd, scrubbed env, rlimits, group kill, netns. +5: drop `-E`, record isolation_used.
    engines.py      ~75   engine paths + the refusal grammar. +16: check_refusal_contract(RunResult).
    extract.py      ~95   completion -> program. Empty fence returns None; truncation is its own no-program reason.
    outcome.py     ~150   NEW (absorbs classify.py). The Outcome algebra and the TOTAL reason->category map.
    acceptance.py  ~400   the four gates and the four test kinds. Returns Outcome; checks the refusal contract.
    schema.py        80   the case record and its identity hash. A plain dict, deliberately NOT a type (§3).
    lypning_source.py ~220 classify_entry, is_ceiling, is_tooling (now decided on the program), the two prompts.
    adapters.py     197   four adapters, one function each. Unchanged — the held-out filter is not their job.
    harvest.py     ~180   collect / merge / gate / ledger. Merges into the corpus; the held-out chokepoint.
    split.py       ~230   freeze / verify / materialize, + Cohort (the named denominator), + the neighbour report.
    arm.py         ~110   NEW. The Arm record: base_url, requested vs SERVED model, sampling, prompt_sha, arm_sha.
    backends.py    ~175   the one OpenAI-shaped door. Retains data["model"] and the raw response.
    grade.py       ~130   NEW. The ONE grading step: completion -> program -> run_test -> Outcome -> attempt record.
    evaluate.py    ~280   the run driver: threads, arm-keyed resume, spend cap, progress, fold. Grading body removed.
    sample.py      ~190   rejection sampling and fold_draws. Grading body removed; the cheat predicate moves to grade.py.
    stats.py       ~155   bootstrap, Wilson, paired delta, pass@k — all over a Cohort. `beats` leaves.
    compare.py     ~130   NEW. Comparison | NotComparable. The only place a delta or a WIN can be constructed.
    mixture.py      ~90   NEW (step 3 seam). Compose an SFT file from train cases under a named, hashed spec.
    sweep.py       ~120   NEW (step 4 seam). One row per run: {mix_sha, arm_sha, cohort_sha, run_id, comparison}.
    cli.py         ~660   19 subcommands, one parser, one dispatch. Loses the ~101 lines of logic that never belonged.
  tests/
    conftest.py, test_sandbox.py                     unchanged
    test_acceptance.py ~95                           + a pin on the gate-report STRINGS (they are inside the lock hash)
    test_outcome.py   ~90   NEW                      totality of reason->category; a harness error cannot be a failure
    test_extract.py   ~50   split from test_extract_stats.py
    test_stats.py     ~70   split, + ragged-k and two-sided significance
    test_harvest.py  ~110   split, + merge-not-overwrite, + the held-out chokepoint, + order-independent category
    test_evaluate.py ~110   split, + arm-keyed resume
    test_split.py    ~133   + the neighbour report, + Cohort exclusions
    test_arm.py       ~60   NEW  an Arm cannot be an echo; two base_urls are two arm_shas
    test_grade.py     ~70   NEW  eval, sample and replay produce the same attempt for the same completion
    test_compare.py   ~90   NEW  no delta and no WIN exists outside a Comparison
    test_recorded.py  ~90   NEW  the golden pin: re-fold the 5 runs in runs/, assert pass_rate does not move
    test_replay.py    ~60   NEW  the 1,184 recorded programs grade identically old vs new
```

Net: **17 modules -> 22** (one deleted, six added, two of them empty seams for steps 3 and 4), **3,463 -> ~3,850 lines**, of which ~260 are checks the audit says are missing. Nothing is renamed. Nothing becomes a package. No import path outside `pipeline/` changes.

---

## 2. Where each of the 17 modules lands

| current | lines | lands |
|---|---|---|
| `__init__.py` | 15 | unchanged |
| `jsonio.py` | 107 | unchanged. Already the single writer and already correct. |
| `sandbox.py` | 295 | stays (`~300`). Two edits: remove `-E` from the spawn (keep `-s`); record `isolation_used` and the interpreter argv on `RunResult`, so the netns/hash-seed posture is a fact a test can assert instead of a docstring claim. |
| `engines.py` | 59 | stays (`~75`). `+check_refusal_contract(r)` — exit 90 **and** empty stdout **and** exactly one parseable line. Today `parse_refusal` is never called from `acceptance.py`. |
| `extract.py` | 77 | stays (`~95`). Empty fence -> `None`; unclosed fence + `finish_reason == "length"` -> a named truncated reason rather than a chopped program graded as a syntax error. |
| `classify.py` | 79 | **DELETED.** `CATEGORIES` becomes the closed vocabulary in `outcome.py`; `is_category`/`stratum` move verbatim; the 40-line `classify()` if-chain is replaced by a total dict lookup; the three stderr regexes survive as a secondary refinement consulted only for the `exit` reason, where they are legitimate. The silent `return "wrong-output"` fall-through does not survive. |
| `schema.py` | 80 | stays, one import line changed (`.classify` -> `.outcome`). The case record stays a plain dict (§3). |
| `lypning_source.py` | 215 | stays (`~220`). `is_tooling` decides on the program (`import lypning`, `sys.path.insert(0,'src')`), not on the refusal detail. |
| `adapters.py` | 197 | unchanged, deliberately. The `evalfail` held-out leak is closed at the chokepoint so no adapter — present or future — can reintroduce it. |
| `harvest.py` | 146 | stays (`~180`). `collect(sources, *, existing, lock)` — both new arguments **required**: it merges into the loaded corpus instead of replacing it, and it refuses to emit any candidate whose `case_id` is in the lock's holdout. Category upgrade is tie-broken on sorted `source_id`, not on `--source` argv order. |
| `split.py` | 139 | stays (`~230`). `+Cohort` (ordered ids, declared k, exclusions with a reason each, `cohort_sha`); `+neighbours()` (difflib over normalized prompts, cross-split). `freeze`/`verify`/`materialize` verbatim. |
| `acceptance.py` | 413 | stays (`~400`). Out: the `Verdict` dataclass and its `harness_error` property (~14 lines) to `outcome.py`. In: the real refusal-contract check in `_run_lypning` (~10 lines). The four `_run_*`, the four gates and `NORMALIZERS` do not move. |
| `backends.py` | 158 | stays (`~175`). `_to_completion` keeps `data["model"]` and populates the already-declared-and-never-populated `Completion.raw`. `identity()` is deleted; `probe()` returns what answered, not just that something did. |
| `evaluate.py` | 330 | stays (`~280`). `_one`'s grading body (~45) -> `grade.py`. `_completed_keys` is re-keyed. `summarize_run` folds over a `Cohort` and refuses a multi-arm attempts file. Prompt rendering stays here; `prompt_signature` now hashes the rendered message including `render_contract`. |
| `sample.py` | 230 | stays (`~190`). Grading body (~45) -> `grade.py`; `looks_like_literal_output` + thresholds (~30) -> `grade.py`; `train_cases` calls `split.verify` and takes its ids from `split.train_ids()`. Selection stops being shortest-wins (`key=(len(p), p)`); it prefers the draw with the lowest cheat evidence, then the median length. |
| `stats.py` | 162 | stays (`~155`). `beats` -> `compare.py`. `pass_at_k` takes a `Cohort`: k is declared, case-weighted everywhere, ragged draws are an error rather than a silent `k = max(draws)`. `paired_delta.significant` becomes two-sided and reports the dropped-case count. |
| `cli.py` | 761 | stays (`~660`), one file. Leaves: the comparability block in `cmd_results` (~25) and the unguarded WIN tail of `cmd_compare` (~8) -> `compare.py`; `_per_case` (9) -> `compare.py` as the single sorted per-case aggregator (which kills the dict-order bootstrap defect as a side effect); `cmd_grade`'s 35-line grading loop and its two `__import__("pipeline.evaluate", ...)` calls -> `grade.py`; `cmd_slices`' per-case aggregation (~20) -> `compare.py`. Arrives: ~25 lines of parser for `nt arms`, `nt neighbours`, `nt cohort`, `nt mixture`, `nt sweep`. |

**Why `cli.py` is not split.** Its 761 lines are 14 command functions, a 100-line parser, one reporter and four formatting helpers; `grep -n '^def cmd_' cli.py` prints the entire surface in 14 lines. Splitting into `cli/` costs three things — "what commands exist" stops being greppable, the tmux relauncher's `[sys.executable, "-m", "pipeline.cli", "eval", "--foreground", ...]` argv reconstruction gains a second place to drift, and every cli-located defect becomes a cross-file change without becoming impossible. The problem with `cli.py` is not its length; it is that ~101 of its lines are library logic, and those 101 lines are exactly where three audit defects live. Removing them is the whole fix.

---

## 3. First-class types, and what each makes unrepresentable

House rules bind: Python 3.9, no `match`, no runtime `X | Y`. Every "sum type" below is one frozen dataclass with a constrained `kind` field plus module-level constructors that are the only public way to build one, and a totality test. The guarantee is *"you cannot construct the ambiguous thing by the obvious path, and CI catches the unobvious one"* — not *"a checker proved it"*.

### 3.1 `outcome.Outcome` (`outcome.py`)

Four constructors, no others: `Passed(route)`, `Failed(reason)`, `HarnessError(where, detail)`, `EngineMismatch(engine, disagreement)`. `Failed` validates `reason` against a frozen `REASONS` set at construction. `category_for(reason)` is a total dict; a test asserts every member of `REASONS` has an entry.

Makes unrepresentable:
- **Defect 3** — a harness error scored as a model failure. `HarnessError` has no `passed` field and no reason, so the four sites that today write `Verdict(False, "harness-error", msg)` with no `RunResult` (worst: `acceptance.py:399`, `"engine not built"`, which with the oracle absent by default scores *every* attempt as a model failure) cannot be written.
- **Defect 4** — a reason with no category silently becoming `wrong-output`. Construction raises; `classify()`'s fall-through is gone.
- **Defect: engine-mismatch-as-model-failure** — `EngineMismatch` is a separate variant, not a `Failed` reason. It cannot enter the model denominator because `summarize_run` folds `Passed | Failed` only and reports mismatches on their own line. This is host-repo invariant 1 made structural. *(Grafted from `by-lifecycle`/`by-dependency`; `minimal-change` had it as a reason with an `engine-bug` category, which still flows through `passed=False`.)*
- **Defect: refused-relabelled-wrong-output** — `refused` is a reason with its own category. `headroom-k16`'s reported `wrong-output: 664` becomes 285 wrong-output / 376 refused / 3 engine-mismatch.

### 3.2 `arm.Arm` (`arm.py`)

`base_url`, `requested_model`, **`served_model`** (read off `data["model"]` of a real response), sampling block, `prompt_sha`, `arm_sha` over all of it. The only constructor is `Arm.confirm(backend, sampling, prompt_sha)`, which performs a probe; an Arm cannot be built from what the operator typed. `--arm-unconfirmed` exists for gateways that do not report a model, and stamps the run permanently.

Makes unrepresentable:
- **Defect 1** — two shipped runs recording model `"nemotron"` from two different HF endpoints, with `base_url` dropped from `summary.json`. `arm_sha` differs; `grade.py` is the only writer of attempt records and writes `arm_sha` on every one.
- **Defect 2** — resume blending arms. `_completed_keys` keys on `(arm_sha, case_id, sample)`, so another arm's attempts are not completed work; `summarize_run` refuses to fold attempts carrying more than one `arm_sha`.

The field split is load-bearing for step 4: two sweep rows should differ in `served_model` and agree in sampling. Comparability reads the two halves differently on purpose.

*Judge-flagged, and taken:* `arm_sha` hashes **generation** identity only. The engine-chain fingerprint and the sandbox/judge version go in a separate `harness_sha`. Rebuilding the `lypning` binary — which happens constantly in this repo — must invalidate *grading*, not force re-*generation* on a spend-capped arm. Resume keys on `arm_sha`; comparability guards on both.

### 3.3 `split.Cohort` + `compare.Comparison` / `compare.NotComparable`

`Cohort` = ordered case ids + declared k + an exclusion list carrying a reason per id + `cohort_sha` over the id list only. A statistic is a function of a Cohort and per-case means; `stats` has no entry point taking a raw attempt list. `comparable(a, b)` returns a `Comparison` or a `NotComparable`; only a `Comparison` carries `delta`, `win`, `paired` and `coverage`, and `stats.beats(dict, float)` ceases to exist.

Makes unrepresentable:
- **Defect 5** — `cmd_compare`'s trailing WIN line with no guard at all, and `cmd_results` guarding on manifest + prompt only. `NotComparable` reasons: split drift, prompt drift (now over the *rendered* message, so `render_contract` is inside the hash), sampling-block mismatch, unconfirmed arm, `cohort_sha` mismatch, and `cases_evaluated != cases_planned` — which is what `cmd_promote` should have been checking and never did.
- **Defect 11** — draw-weighted pass@1 beside case-weighted, and `k = max(draws)` on ragged draws. k is declared by the Cohort; ragged is an error.
- **Defect: paired-delta-drops-cases-silently** — `coverage` is a field of the `Comparison`, not a footnote, so "+15.0pp over 20 cases" cannot be printed without "54 of 74 discarded".
- **Defect 10, partly** — truncation rate is a **mandatory reported field** of every `Comparison`.

*Judge-flagged, and taken:* truncation-rate divergence is **not** an `n/c` disqualifier. It is a dependent variable — an outcome of the arm — and the audit's own most-likely false positive (a LoRA trained on no-think data that stops rambling) would be permanently `n/c` against the thinking-on baseline, suppressing the confound and the treatment effect together. Instead: the delta prints, the truncation rates print beside it, a truncation-stratified secondary prints, and the **WIN badge is withheld** (`win: held — truncation 32% vs 2%`) until the arms are re-run at matched budgets. The comparison exists; the go/no-go does not fire on a decode-budget lever.

### 3.4 Deliberately NOT a type: the case record

`split.freeze` stores `sha256_of(by_id[i])` over the **whole** case record, including the `gates` dict `harvest` bolts on at `harvest.py:120`. A dataclass migration re-serializes that record; a re-serialization moves 74 hashes; moved hashes void a baseline that cost real money. The audit's `lock-hashes-provenance-so-enrichment-bricks-the-eval` finding is the warning shot a records.py refactor would have fired for real.

**The rule, stated once:** *type what is COMPUTED (outcome, arm, cohort, comparison); never type what is HASHED (the case, the lock).* Every new field goes on the **attempt** and **summary** records, never on the case.

### 3.5 Defects that stay ordinary bugs — say so plainly

| audit item | why no type reaches it |
|---|---|
| `-E` nullifies `PYTHONHASHSEED` | one character in `sandbox.py`. Step 1, ahead of all structure. |
| 12 corpus cases whose own correct program cannot reproduce their `expect_stdout` | a data fact. Handled by a Cohort exclusion + a train-side quarantine sidecar, not by a boundary. |
| 5 unpassable + 4 lypning-tooling held-out cases | same: a declared exclusion with a written reason, made *before* the tuned run. |
| defect 6, the cheat guard's false premise | the prompt hands the model the original program and therefore its literals. Subtracting prompt literals makes the predicate better, not sound. It stays a threshold and a judgement call. |
| defect 9, near-duplicates | `neighbours()` is a report and a sidecar exclusion list. `0.85` is still a threshold somebody picked. |
| `is_tooling` sniffing the refusal detail | a predicate fix inside an existing function. |
| `discriminates` / `stable` gates being vacuous | real gate-logic work, scoped out of this refactor (§6). |
| leaked process groups, `RunResult.truncated` dead, `preexec_fn` under threads | ordinary sandbox bugs. |

---

## 4. Migration — eleven steps, each independently verifiable

Every step is one commit, one day or less, no flag day. Six of these modules are being patched concurrently; steps that touch `acceptance.py`, `evaluate.py`, `sample.py` and `cli.py` are each a single mechanical move so a rebase is a conflict in one file, not a merge of two designs.

Run from `/home/user/lypning`. `T` below abbreviates `uv run --with pytest pytest nemotron/tests -q`.

**0. PIN, fold-only.** `nemotron/tests/test_recorded.py`: re-fold all five runs in `runs/` **from recorded `passed` flags in `attempts.jsonl`** (no re-grading — the grader is still hash-randomized at this point) and assert each `pass_rate` matches its committed `summary.json`; assert `split.verify(data/corpus.jsonl)` is True; assert the 74 lock ids and `manifest_sha256` as literal constants. `git mv runs/*/summary.json runs/*/summary.as-reported.json` and regenerate `summary.json` so the published numbers stay in the tree beside every later correction.
**Verify:** `T` green; `./nemotron/nt verify` exit 0.

**1. `-E`.** Remove `-E` from the spawn at `sandbox.py:229`; record `isolation_used` and interpreter argv on `RunResult`. Write `tests/test_sandbox.py::test_hash_seed_is_honoured` — 12 runs of a set-repr program produce one distinct stdout.
**Verify:** `T` green, new test green. `./nemotron/nt show ntx-4c3ce026fadf --run-test x12` prints one distinct stdout, not the measured `[True,False,False,True,...]`. **This step is before the golden pin can ever gate on grading**, which is the sequencing error all three judges flagged in three different proposals.

**2. CENSUS, no fix.** `nt neighbours` and `nt cohort --census` land as read-only reports: the cross-split similarity table, the nondeterministic-expect_stdout scan, the tooling-program scan, the `finish_reason` aggregate.
**Verify:** `./nemotron/nt neighbours` reproduces the audit's 27 / 19 / 11 at 0.85 / 0.90 / 0.95 and names `ntx-fa3622ac596b` ↔ `ntx-f721dbb16b39` as byte-identical; `./nemotron/nt cohort --census` reports 24/74 truncated on `baseline-nemotron35-bf16-t12k`.

**3. OUTCOME.** `outcome.py` in, `classify.py` out. Pure relabelling.
**Verify:** `T`; `test_recorded.py` unchanged and green (no `pass_rate` moves); `./nemotron/nt summarize headroom-k16` reports 285 wrong-output / 376 refused / 3 engine-mismatch instead of `wrong-output: 664`, and `data/baseline.json` reports 9 wrong-output / 14 refused instead of 23.

**4. ENGINE-MISMATCH LEAVES THE DENOMINATOR.** Separate commit, because it moves a number. `summarize_run` folds `Passed | Failed` only.
**Verify:** rewrite-slice pass@1 `11.5% -> 11.9%`, pass@16 `26.9% -> 28.8%`, CI `[5.6,18.5] -> [5.9,18.9]` — exactly the audit's prediction. The pin is updated **in the same commit**, with old and new values in the diff.

**5. GRADE — the riskiest step.** `grade.py` absorbs the three drifted grading copies: `evaluate._one`'s body, `sample.one`'s body, and `cmd_grade`'s 35-line reimplementation with its two `__import__("pipeline.evaluate", ...)` calls and its `"sampling": {"replayed": True}`. The cheat predicate moves in, prompt-aware, and emits `cheat_suspected` as an **attempt field, report-only** — it does not change `passed` (see §6).
**Verify:** `tests/test_replay.py` re-grades all 1,184 recorded programs of `runs/headroom-k16/attempts.jsonl` through the old grader and the new one and requires identical Outcomes on every case. *This is the only verification in the plan that re-runs the acceptance tests, and it is why step 1 had to be first: before the `-E` fix, a pure replay of identical programs already moved `35.98% -> 35.81%`.* Grafted from `by-dependency`, which two judges named as its best idea.

**Why this is the riskiest step:** it is the one place where a behaviour change can hide inside a move. Three copies that have already drifted are being collapsed into one; whichever copy's behaviour wins, some call site changes. The replay gate is the mitigation, and it is a measurement rather than an opinion — but it covers the eval path's 1,184 programs, not the sampler's 2,800 draws, so `sample`'s arm is verified only by `test_grade.py` asserting the three paths produce the same attempt for the same completion.

**6. ARM.** `arm.py` in; `backends._to_completion` keeps `data["model"]` and populates `Completion.raw`; `identity()` deleted; `prompt_signature` hashes the rendered message including `render_contract`; resume re-keys on `(arm_sha, case_id, sample)`; `summarize_run` refuses a multi-arm fold. Legacy attempts with no `arm_sha` fold under `legacy:<digest(meta.backend, meta.sampling)>` with `arm_confirmed: false`.
**Verify:** `./nemotron/nt arms` lists the five recorded runs and shows `baseline-*` on `ev8eognh8prrpgs7…` and `headroom-k16` on `ebro2v0fa9zgvjs9…` as **two arms**, where `nt results` differenced them. A `prompt_signature` test: replacing `render_contract` changes the hash (today it does not).

**7. CHOKEPOINT + MERGE.** `collect(sources, *, existing, lock)` — both arguments required. Merges into the loaded corpus (`harvest.py:131`'s truncating `write_jsonl` becomes unwritable, not merely fixed — grafted from `by-invariant`); drops any candidate whose `case_id` is in the lock's holdout, with a ledger reason; category tie-break on sorted `source_id`. `make_case` has exactly one caller (`harvest.py:65`) and `collect` exactly one (`harvest.py:104`) — verified — so ~10 lines here bind every adapter that exists and every adapter anyone writes later.
**Verify:** `./nemotron/nt harvest --source evalfail:runs/headroom-k16` leaves all 249 cases present, adds negatives to train cases only, drops every held-out id with reason `held-out`, and `./nemotron/nt verify` still exits 0 — the deadlock the audit reproduced.

**8. COHORT.** `split.Cohort` + `stats` re-expressed over it + `nt cohort --declare` writing `data/cohort.v1.json`: the 74 lock ids minus the 5 unpassable, minus the 4 lypning-tooling, each exclusion with a written reason and an audit citation. `summarize_run`, `cmd_slices` and `paired_delta` all fold through the same sorted per-case aggregator.
**Verify:** `./nemotron/nt summarize --cohort v1` re-folds `data/baseline.json`'s **existing** attempts over the declared cohort and prints old and new side by side. The declaration is committed **before** any tuned run exists, so it is pre-registration, not post-hoc.

**9. COMPARE.** `compare.py` in; `stats.beats` out; `cmd_results`, `cmd_compare`, `cmd_promote` and `cmd_slices` all route through `comparable()`. `paired_delta.significant` becomes two-sided.
**Verify:** `./nemotron/nt results` prints `n/c` for `baseline-nemotron35-bf16` against the t12k baseline with reason `sampling: max_tokens 4096 vs 12288` — the `-6.8pp` the audit showed is a token-budget effect. `./nemotron/nt compare headroom-k16 replay-check` no longer prints "not separable from noise" for a strictly-negative interval.

**10. SEAMS.** `mixture.py` and `sweep.py` land with their records, their hashes, their CLI surfaces and no logic. `nt mixture --list` and `nt sweep --plan` print empty tables.
**Verify:** `T`; `./nemotron/nt sweep --plan` exits 0 with an empty table and a `cohort_sha` echoed from step 8.

---

## 5. `data/` and `runs/` — what survives

**Everything survives. There is no re-freeze and no re-spend.**

- **`data/corpus.jsonl`** — 249 records, bytes unchanged. `schema.py` changes one import line. No field is added, removed, renamed or reordered. The gate-report strings gain a pinning test in `test_acceptance.py` because they are inside `sha256_of(case)` and a reworded string would silently void 74 hashes.
- **`data/holdout.lock.json`** — unchanged. `manifest_sha256` keeps its value; all five runs' `holdout_manifest_sha256` keeps matching. **The plan explicitly declines to narrow the lock's hash scope**, which `by-lifecycle` proposed as its step 7 and which its own judge correctly called redundant: once `harvest.collect` refuses to emit a locked id, no adapter can enrich a held-out case, so the wide hash scope never bites again. Buying a one-way door to defend a path already closed is a bad trade.
- **`runs/*/attempts.jsonl`** — never rewritten. Attempts with no `arm_sha` fold under a `legacy:` arm marked unconfirmed. That compatibility branch lives as long as the baseline is worth reading; it is debt, and it is cheaper than re-spending.
- **`runs/*/summary.json`** — derived and regenerated at steps 3, 4, 6 and 8. `summary.as-reported.json` is kept beside each one from step 0, so every published figure stays in the tree next to its correction.
- **`data/baseline.json`** — **re-derived, not re-measured.** This is the central move. The five unpassable and four tooling cases leave the *denominator* via the Cohort's declared exclusion list, not via a re-freeze; the pass rate is re-folded from the attempts that were already paid for. `26/74 = 35.1%` becomes a Cohort-v1 figure over ~65 cases from the same recorded draws. Nothing goes back to the GPU.
- **Two new sidecars, both keyed by case id, neither touching a case record:** `data/quarantine.json` (the 14 nondeterministic / unpassable ids with their measured evidence) and `data/contamination.json` (cross-split near-duplicate pairs above threshold, consumed by `sample`). Sidecars rather than fields precisely because a field would move a hash.

**What is worth re-deriving, and why.** Only summaries, and only because they are wrong: `wrong-output: 664` is 2.3× too high and hides that *fallback, not incorrectness*, is the dominant failure mode — which is the single number that would tell you whether a tune worked on route or on correctness. Re-deriving it costs a `nt summarize` and changes no evidence.

**What is not worth it.** A re-freeze. It would cost the baseline and buy nothing the Cohort does not buy for free, and `freeze()`'s own error message already says `--refreeze` "invalidates every baseline".

---

## 6. What this plan deliberately does NOT do

- **No package tree.** The three grander proposals spend 600–1,400 net new lines and 34–40 modules on layers (`kernel/`, `stages/`, `judge/`, `effects/`). Every defect they close, this plan closes in the flat tree. On 3,463 lines, "where is X" costing a directory walk is a real ergonomic loss with no corresponding guarantee. The most likely way this refactor fails is by being grander than the problem.
- **No `cli/` package.** §2 states the argument: 14 greppable commands in one file beat 14 files plus an import graph, and the tmux relauncher's hard-coded module path gains no second place to drift. That is a judgement call, not a proof, and `cli.py` stays the biggest file in the tree.
- **No `records.py`, no dataclass for the case.** §3.4. Type what is computed; never type what is hashed.
- **`counts_in_denominator` is NOT true only for `Graded`.** `by-dependency` proposed exactly that, and two of its own judges caught it: it silently drops every no-code attempt, taking the published baseline from `26/74 = 35.1%` to `26/50 = 52%` — a +17pp shift introduced by a type definition and listed nowhere in its behaviour changes. Truncation gets a census, a mandatory field on every `Comparison`, and a withheld WIN badge. It does **not** get denominator exclusion. Whether it belongs in the denominator is a contested measurement policy, and burying a policy in a type's single definition point is the opposite of the virtue that type was supposed to have.
- **The cheat guard does not change `passed` on the eval path — yet.** It writes `cheat_suspected` on every attempt and `nt summarize` reports it. Enforcement is a flag (`--strict-cheat`) whose default is flipped in the pre-registration for the tuned run, decided *before* that run. Reasons: the audit's own predicate has ~29/53 measured false positives, its premise is false for this corpus (the prompt hands the model the original program and therefore its literals), and enforcing it silently would move the published 35.1% by one attempt with no diff anyone signed. The blocker the audit actually names — cheats entering the SFT set — is closed in `sample.py`, where the guard already lives and where selection stops being shortest-wins.
- **`sandbox.py`'s other bugs are not in scope:** leaked process groups, `os.setsid()` escaping the timeout kill, `RunResult.truncated` being unreachable, `preexec_fn` under threads, capture files written into the program's own cwd. All real, all ordinary, none reached by a boundary. Step 1 takes `-E` and nothing else because `-E` poisons every number and the rest do not.
- **The `discriminates` and `stable` gates are not rewritten.** Both are vacuous (`stable` compares a known-failing program against itself twice; `discriminates` only requires that `EMPTY_PROGRAM` fails). Fixing them is real gate-logic work that re-runs the harvest and changes which cases exist — i.e. a re-freeze. This plan handles their *consequences* with the quarantine sidecar and the Cohort, and leaves the gates for a separate PR that can afford the refreeze.
- **No corpus growth.** All five recovery studies recommended against themselves (7, 3, 0, 57-but-contaminated, 7 usable cases). The corpus does not need to be bigger; its existing 74 held-out cases need to mean what they say.
- **The MDE is not fixed by any of this.** The holdout cannot resolve below roughly +11pp, and a good result — a LoRA that fixes 6 of 43 dead rewrite cases — is reported as "no run clears the bar". That is a sample-size fact. This architecture's only contribution is that `sweep/plan` is the place where you must write the MDE down before spending.

---

Files referenced: `/home/user/lypning/nemotron/AUDIT.md`, `/home/user/lypning/nemotron/pipeline/{sandbox,acceptance,classify,harvest,split,schema,backends,evaluate,sample,stats,cli}.py`, `/home/user/lypning/nemotron/data/holdout.lock.json`, `/home/user/lypning/nemotron/runs/`.
