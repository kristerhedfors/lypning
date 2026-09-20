---
name: round02-preflight
description: Every failure a round-02 job has actually died of, and the free check that catches each one before the meter starts — bank identity, reference reproducibility, the supervised-token floor, preparation wall-clock, the sleeping verifier Space, orphaned pool hosts, and the follower that expires before the round. TRIGGER before dispatching round-02 or any billed HF job, after a round fails, when a result looks too much like an older one, or on requests like "why did the round die", "is the bank right", "can this schedule even train", "what should I check before submitting". SKIP for authoring cases (`training-cases`), cutting or sealing a bank (`training-bundle`), reading a finished job's artifacts (`round02-evidence`), and the launch route and cost model itself (`round02-launch`).
---

# Before the meter starts

Six round-02 jobs have been paid for. **Not one produced a model-quality
result.** Every single cause was knowable for nothing beforehand, and in four
cases the number that would have shown it was already printed in a log nobody
compared to anything.

This skill is that comparison, written down. Each row is a failure that
actually happened, dated, with the check that prevents it. Run the sequence at
the end before dispatching; it costs about twenty minutes of free CI.

## The ledger of what has gone wrong

| # | What happened | Cost | The free check |
|---|---|---|---|
| 1 | A round trained on **the wrong bank** and reported a result. `round02_bank.py` uploaded the committed `bank_v2` to whatever `BANK_PATH` named, so bootstrap overwrote the carved bank before the job read it | ~$30, 6 h | `bank.json` manifest; the job refuses a bank that is not the one it names |
| 2 | One case's reference did not reproduce in the verifier image (`locale.setlocale`, no locales in `python:3.12-slim`) — `last_stage: prepare` | ~1 h of h200 | exclude clock/locale/tz families by name at carve time |
| 3 | `--steps 250` exposed **46,535 tokens** against a 50,000 floor. `run()` refuses that *after* 55.6 GB of weights | would have been 3 × $25 | `token-floor.yml`, free, tokenizer only |
| 4 | Two rounds died at the wall. The cost was **preparation**, not SFT: ~4 s/case through the 16-sandbox pool, so an 8,370-case pilot is 5.5 h before a single optimizer step | ~$55 | cap cases per family; count the projection before submitting |
| 5 | The verifier Space was `SLEEPING`, nothing woke it, bootstrap waited out its whole 20-minute budget and failed a **free** job — skipping the billed one behind it | $0, but no round could start on any second attempt | `round02_space.py` requests a restart once |
| 6 | Three `cpu-basic` pool hosts outlived the trainer that died | small, unnoticed | `hf-stop.yml` |
| 7 | `--score-workers 64` against 16 hosts x 4 = **exactly 64 slots**. The pool RAISES rather than waits when every host is full, and stage two adopted stage one's warm hosts at their true occupancy | a pilot bundle that was already built | capacity must exceed scorers by one host; `prepare` now releases the pool in a `finally` |

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
`bank_publish.py --verify-references` runs on `ubuntu-latest`; the verifier runs
in `launch.BASE_IMAGE`, a `python:3.12-slim`. `ubuntu-latest` ships locales the
slim image does not, so that check dropped 4 of the 34 bad references and kept
**every** `locale` case — including the one that then killed a round. Running it
inside the pinned image would close this properly and has not been done. Until
it is, exclusion is by family name, because depending on the clock or the
container is a property of the construct rather than of a case.

## The sequence

All five checks run as one job, so the list cannot be half-remembered:

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
5. **`main` points where this run checked** — compares `round02.yml`'s
   `BANK_PATH` on `origin/main` against the bank just verified, because an edit
   that silently did not apply once sent a dispatch at the wrong bank.

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
between arms.**

## What no check here can tell you

That training installs the effect. A bank with headroom can host a result; it
cannot promise one. And one seed job is one replicate — a complete S4 result is
1111, 2222 and 3333, read from three manifests and never inferred from one.
