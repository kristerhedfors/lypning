---
name: round02-preflight
description: Every failure a round-02 job has actually died of, and the free check that catches each one before the meter starts — bank identity, reference reproducibility, the supervised-token floor, preparation wall-clock, the sleeping verifier Space, orphaned pool hosts, pool hosts idling out between SFT evaluations, the eval-2 prefill at its longest prompt, a `round02/**` push that rebuilds the verifier Space, and the follower that expires before the round. TRIGGER before dispatching round-02 or any billed HF job, after a round fails, when a result looks too much like an older one, or on requests like "why did the round die", "is the bank right", "can this schedule even train", "what should I check before submitting". SKIP for authoring cases (`training-cases`), cutting or sealing a bank (`training-bundle`), reading a finished job's artifacts (`round02-evidence`), and the launch route and cost model itself (`round02-launch`).
---

# Before the meter starts

Almost every paid round-02 job to 2026-09-26 died or was cancelled before its
planned result, and no eval-2 arm has completed on bank v3. The exceptions are
the a10g smokes, the h200 hardware smoke, the 2026-09-16 run's dev/test read,
and the seed-1111 finish's two test arms (`training/RAMP.md` §6, "Rungs that
have run clean"). **Nearly every cause was knowable for nothing beforehand**,
and in several cases the number that would have shown it was already printed
in a log nobody compared to anything. The billed attempts themselves — job
ids, dates, dollars, stage reached — are ledgered once, in `training/RAMP.md`
§6; this skill keeps only failure mode → free check.

This skill is that comparison, written down. Each row is a failure that
actually happened, dated, with the check that prevents it. Run the sequence at
the end before dispatching; it costs about twenty minutes of free CI.

## The ledger of what has gone wrong

| # | What happened | Ledger | The free check |
|---|---|---|---|
| 1 | A round trained on **the wrong bank** and reported a result. `round02_bank.py` uploaded the committed `bank_v2` to whatever `BANK_PATH` named, so bootstrap overwrote the carved bank before the job read it | `RAMP.md` §6 (2026-09-19 wrong-bank row) | `bank.json` manifest; the job refuses a bank that is not the one it names |
| 2 | One case's reference did not reproduce in the verifier image (`locale.setlocale`, no locales in `python:3.12-slim`) — `last_stage: prepare` | `RAMP.md` §6 (2026-09-19 locale row) | exclude clock/locale/tz families by name at carve time |
| 3 | `--steps 250` exposed **46,535 tokens** against a 50,000 floor. `run()` refuses that *after* 55.6 GB of weights | `RAMP.md` §6 (2026-09-18 token-floor row); would have repeated per seed | `token-floor.yml`, free, tokenizer only |
| 4 | Two rounds died at the wall. The cost was **preparation**, not SFT: ~4 s/case through the 16-sandbox pool, so an 8,370-case pilot is 5.5 h before a single optimizer step | `RAMP.md` §6 (2026-09-20 prepare rows) | cap cases per family; count the projection before submitting |
| 5 | The verifier Space was `SLEEPING`, nothing woke it, bootstrap waited out its whole 20-minute budget and failed a **free** job — skipping the billed one behind it | $0, but no round could start on any second attempt | `round02_space.py` requests a restart once |
| 6 | Three `cpu-basic` pool hosts outlived the trainer that died | small, unnoticed | `hf-stop.yml` |
| 7 | `--score-workers 64` against 16 hosts x 4 = **exactly 64 slots**. The pool RAISES rather than waits when every host is full, and stage two adopted stage one's warm hosts at their true occupancy | a pilot bundle that was already built; `RAMP.md` §6 (2026-09-20 exact-fit row) | capacity must exceed scorers by one host; `prepare` now releases the pool in a `finally` |
| 8 | Two SFT jobs in a row both died in an SFT evaluation on sandbox 503s. Pool hosts shut down after 600 s without a sandbox while SFT trained 25–40 minutes between evaluations. The second attempt only had a longer retry budget (#123), so it failed later and cost more | `RAMP.md` §6 (2026-09-24 rows) | #124: host idle timeout 3 h, pool closed on every exit. Before retrying, classify: a 503 that persists for a host already used is not an outage. Free check: compare `HOST_IDLE_TIMEOUT` in `hf_sandbox_runner.py` with the gap between SFT evaluations |
| 9 | The arm-A pilot's SFT completed, then the base test arm aborted on one base-model draw that reached a known `lypning-l` bug. The policy aborted an arm on any engine mismatch, and Step 2's grade had already seen one on base-model outputs | `RAMP.md` §6 (2026-09-24 pilot row) | #126: a mismatch draw is counted (reward 0), with a 1% bound per arm. Free check: compare the mismatch rate already measured in a grade with the draws the arm plans |
| 10 | The seed-1111 finish: CUDA OOM in the prefill of base-eval2 chunk index 26 (the 27th of 51). One 434-token prompt padded 256 sequences to 111,104 prefill tokens; the largest prefill ever run was 48,384 (189 × 256), and the h200 smoke measured only short starter prompts | `RAMP.md` §6 (2026-09-25 finish row); both test arms were done, no eval-2 arm finished | PR #130: `--eval-prefill-tokens` (default 48,384) draws an oversize chunk in parts. Free check: `.github/scripts/eval2_shape.py` (`eval2-shape.yml`) prints the chunk padded prompt lengths — Actions `36196784628`, 2026-09-25: p50 129, p90 164, max 434 — and max × 256 against the largest prefill ever run shows the OOM without a GPU |
| 11 | A push to `round02/eval2-shape`, meant only to run the free shape reader, also triggered `round02.yml`, whose bootstrap rebuilds the verifier Space from the pushed commit's engine. Bootstrap skipped a byte-identical upload and the run was then cancelled, so no Space build started and the head stayed arm A's | $0, but a moved head would have made the seed-1111 finish unfinishable | Only `stage=finish` holds the Space (`SPACE_HOLD`). Name no branch `round02/**` unless you mean to bootstrap (a `[submit-pilot]`/`[submit-smoke]` marker in the head commit also bills); run readers with `gh workflow run` on main; check the Space head with `finish_preflight.py` before a finish |

## The exact-fit pool, which reads like a perfect fit

`16 hosts x 4 sandboxes = 64 slots` and `--score-workers 64` looks like the
shape that uses the machine fully. It is the one shape with no recovery.

`SandboxPool.create` does not queue. When every tracked host is full it calls
`_provision_hosts(1)`, and that **raises** against `max_hosts`:

```
Pool needs 1 more host(s) but max_hosts=16 allows only 0 more.
```

A round's stages are separate processes sharing one named pool. That is
deliberate — stage two attaches to stage one's warm hosts instead of paying the
boot again — but `_discover_hosts` reads each adopted host's *live sandbox
count from the host itself*. So stage two inherits whatever stage one had not
finished reaping, asks for 64 of 64, and dies asking for host seventeen.

Two things were wrong and both are fixed:

- **Nothing ever called `close()`.** The hosts outlived the process that booted
  them with no one to hand them back. `prepare` now calls `release_runner` in a
  `finally`, so the failure path releases too — otherwise a retry meets the
  pool it just filled.
- **The guard admitted equality.** `capacity < score_workers` let the exact fit
  through. A multi-host pool now needs a full host of slack. A single-host pool
  does not: it has no cross-host packing and is the smoke shape.

> The cruelty of this one is *when* it fires: at stage two, **after** the pilot
> bundle is built. The expensive half is paid for and then thrown away.

`test_scorers_exactly_equal_to_pool_capacity_are_refused` and
`test_the_round_the_workflow_would_actually_launch_is_admissible` both fail
against the code as it was — the second reads `round02.yml`'s own numbers,
because the scorer count lives in YAML and the rule that admits it lives in
Python, and nothing else relates the two.

## The two that are about instruments, not infrastructure

**A guard that cannot fire.** `train_verified.py` checked prompt length with
`len(tok.apply_chat_template(..., tokenize=True))`. Under transformers 5 that
returns a `BatchEncoding` whose `len()` is **2** — its key count — so the check
read `2 + max_new_tokens > max_seq` and admitted a prompt of any length. It was
correct under 4.x and went vacuous on upgrade. It could rot because it sat
inline in `run()`, behind `import torch`, where no test could reach it.

> Ask of any gate: *what input makes it fail?* If you cannot name one, it is
> decoration. `test_prompt_budget.py` fails 12/12 against the unfixed code.

**A check in the wrong environment.** The per-case reference verification in
`bank_publish.py --verify-references` ran on `ubuntu-latest`; the verifier runs
in `launch.BASE_IMAGE`, a `python:3.12-slim`. `ubuntu-latest` ships locales the
slim image does not, so that check dropped 4 of the 34 bad references and kept
**every** `locale` case — including the one that then killed a round.

**Closed on 2026-09-20.** `verify_references.py` runs the same check inside the
pinned image (`docker run`, no `pip install` — `training/pipeline` is
stdlib-only, so it cannot drift by resolving a wheel), and `bank_publish.py`
now takes its `--reference-report` rather than redoing the check on whatever
runner it is standing on. `sandbox.run_python` spawns `sys.executable`, so
putting the script in the container IS the fix.

The manifest records `references_verified_in`, not a bare boolean: `true` was
true of the check that missed the case, and a later reader needs to know which
machine answered. A report cut against a different union is refused.

Family exclusion stays, and is not redundant: depending on the clock or the tz
database is a property of the *construct*, which no per-case run in any single
image can see.

## The sequence

All six checks run as one job, so the list cannot be half-remembered:

```bash
gh workflow run "round-02 preflight (every free check, before the meter)" \
  --ref main -f bank_path=banks/v3-20260920b -f steps=300
```

In order, and each exits non-zero on the failure it prevents:

1. **The bank is the bank it says it is** — counts against `bank.json`. A bank
   with no manifest is refused, not trusted.
2. **The supervised-token floor** — `token_floor.py --require-clears`, exact,
   tokenizer only, no weights.
3. **Can a round on this bank detect anything** — `nt bank-native --mix-only`.
4. **Nothing is still running** from a previous round.
5. **Every completed seed on this bank is the same arm** — `arm_check.py`.
   A complete S4 is 1111, 2222 and 3333, and *nothing related three manifests
   to each other*: `s0_inventory` could print one, no code compared two. Seeds
   run across an engine fix or a re-cut bank would be combined by hand with
   nothing to say they were different experiments. It enforces bank, Space
   revision, model revision, the training and evidence dose, and **sandbox
   density** — `native` is host-load dependent, so packing changes what the
   label means. It does NOT enforce host count (a cost ceiling) or `commit`
   (a workflow fix and an engine change are indistinguishable from here, so it
   shows the difference and lets a reader judge).
6. **`main` points where this run checked** — compares `round02.yml`'s
   `BANK_PATH` on `origin/main` against the bank just verified, because an edit
   that silently did not apply once sent a dispatch at the wrong bank.

Before an evaluation stage, also run the free eval-2 shape read (row 10) and
compare its max padded length × 256 with `--eval-prefill-tokens`; before a
finish, confirm the Space head is the pilot's revision (row 11) and that no
`VERIFIER_MODULES` file has changed since the pilot (`training/ENGINE_BUMP.md`
§5). Then check
that `training/RAMP.md` has reached the rung you are about to bill.

Then dispatch the round. The individual pieces are still there if you want one
of them alone: `bank-gates.yml`, `token-floor.yml`, `hf-stop.yml`.

Afterwards, read the Hub and not the follower:

```bash
gh workflow run "hf status" --ref main -f job_log=<HF job id> -f tail=150
```

## Reading a failure without misreading it

- **A red X on the submit job after ~6 h means the stream ended, not the round.**
  GitHub caps a hosted job at 360 minutes; a pilot is submitted for 480. The HF
  job keeps running and still uploads. `hf-status.yml` is the side that knows.
- **GitHub will not serve an in-progress job's log.** Use `hf_job_log.py`; twice
  a round's cause of death was readable only from the Hub.
- **`job-manifest.json` is written last**, on success and failure alike, so its
  absence means "still running", not "wrote nothing".
- **A result identical to an older one is a bug, not a replication.** The
  wrong-bank round reproduced a previous arm byte for byte — `correct 0.93011063011063`
  twice — and that identity was the only thing that gave it away.

## Traps in doing the work

Each of these cost real time here, and none is about the model.

- `[ test ] && assign` under `set -e` is a statement whose exit status is the
  test's: a false boolean input silently ends the step.
- A digits-only id rule refuses every sharded batch this repository produces
  (`<run>-<shard>`, `readapt-<source>-<run>`).
- `if cap:` skips the validation written for `cap == 0`. Use `is not None`.
- A bare `subprocess.run` of a reference is **not** the runner: it misses each
  test's input files, argv and the hash seed, and reported 572 failures where
  `synth.Runner` finds 34.
- `gh run delete --yes` is not a flag; `cmd --bad-flag && echo ok` prints `ok`.
- Conflict blocks are **diff3** here: splitting on `=======` alone leaks the
  `||||||| base` section into the file.
- The repository is **public**. `bundle.json` carries the cases; printing one
  into an Actions log publishes the held-out benchmark, and deleting the run
  afterwards does not reach what already scraped it.

## The kernel is part of the arm, not a speed setting

Transformers falls back to reference PyTorch when a kernel package is absent
and says so in the log:

```
`chunk_gated_delta_rule` is falling back to its reference PyTorch implementation
because `flash-linear-attention` is not installed.
```

That is not only slow — 48 of this model's layers are gated-delta-net — it is a
**different arm**. `STATUS.md` §2 records a kernel swap on identical weights
moving ΔSLR by +1.57pp, larger than either adapter of 2026-09-14 moved it. So
`flash-linear-attention==0.5.2` is pinned in `GPU_VERSIONS` beside torch, at
the version `STATUS.md` records for the v1 run of record.

`causal_conv1d` is deliberately absent: PyPI ships it as an sdist only, so
adding it means an nvcc build on a metered job that can hang or exit 123. Its
fallback line stays in the log, which is the honest state — two of the four
fallbacks are gone and two remain, visibly.

**Grep the round's log for `falling back` before trusting a comparison
between arms.** And do it even when the pin says you need not: on 2026-09-20 a
round ran with `flash-linear-attention==0.5.2` pinned AND installed —
`runtime_versions` passed, because it reads distribution metadata — while
transformers reported it "not installed" and ran all 48 gated-delta-net layers
on the reference path anyway. The distribution was there; the module would not
import. A pin a fallback can satisfy is not a pin.

`kernel_state()` now asks the question transformers asks and writes the answer
into the run record, where the probe/GRPO contract compares it. It also reports
`NTX_USE_FLA`, which `train_verified.run` sets to `0` — an importable kernel
can still be deliberately unused, and those are two different reasons for the
same reference path.

## What no check here can tell you

That training installs the effect. A bank with headroom can host a result; it
cannot promise one. And one seed job is one replicate — a complete S4 result is
1111, 2222 and 3333, read from three manifests and never inferred from one.
Check 5 now refuses to bill a seed that would not join the others, but it
cannot tell you the three were worth running.
