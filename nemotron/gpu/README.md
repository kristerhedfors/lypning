# Step 3: one 8×H100 signal run, and the money that cannot escape

The question this run answers is narrow on purpose: **does a brief rank-16 LoRA
move the held-out rewrite pass rate at all?** Not which mixture is best — that is
the sweep, and a sweep built on an unproven signal is a way to spend a lot of
money learning nothing.

## What the pre-flight already established

Measured 2026-09-11 on one A100, thinking off, before any training hardware was
rented. These numbers decide whether the run below is worth funding.

| | held-out rewrite slice (52 cases) |
|---|---|
| pass@1 | **11.5%** [5.6, 18.5] |
| pass@16 | **26.9%** |
| headroom | **+15.4pp** |

pass@1 is what the model *does*; pass@16 is what it *can* do. Rejection-sampling
SFT converts the second into the first and does nothing else, so that gap is the
entire budget this run has to work with. It is real but not large, and the plan
below is shaped around that fact rather than hoping.

Training data, from the same pre-flight: **83 of 175** train cases yielded at
least one verified solution → **154 SFT examples**, every one of them a program
that provably reproduces CPython's output and provably runs on the engine.

## Why the mixture must contain the ceiling cases

60 of those 154 examples are *ceiling* cases, where the right answer keeps the
import and accepts the fallback. They are not padding. Train only on rewrites and
the adapter learns one rule — "never import" — which is the exact failure this
project exists to prevent, and it would show up as a rising rewrite score and a
collapsing ceiling score. The ceiling slice is reported beside the rewrite slice
on every run for that reason, and a run that gains on one while losing the other
is not a win.

## The run

| | |
|---|---|
| machine | `a3-highgpu-8g` — 8×H100 80GB, **SPOT** |
| rate | ~$29.52/hr (8 × $3.69 GPU-hr) |
| adapter | rank 16, alpha 32, `exclude_modules: ["*.out_proj"]` |
| parallelism | FSDP2, `ep_size: 8` = `--nproc-per-node 8` |
| steps | 48 (~3 epochs over 154 examples at global batch 16) |
| checkpoint | every 25 steps to GCS |

`ep_size: 8` is not tunable: the cookbook's H100 recipe is single-node 8-GPU, and
the one-GPU variant in `dgx-station-recipes` assumes a GB300's memory rather than
an H100's 80 GB. Hyperparameters are **fixed** — when the sweep comes, the data
mixture is the only thing that moves, or a difference in pass rate is not
attributable to it.

**The box never grades.** It trains, serves, generates 16 completions per held-out
case, writes them to GCS and powers off. Grading needs the lypning engines and a
sandbox — CPU work that costs roughly twelve times more per second on eight H100s
than it does anywhere else. `nt grade RUN --completions …` runs it afterwards for
nothing, and re-runs for nothing after an engine rebuild.

## Spend: five layers, each assuming the one above has failed

The most expensive failure here is not a bad hyperparameter. It is an instance
nobody turned off: **$29.52/hr is $708/day.**

| layer | mechanism | survives |
|---|---|---|
| 0 | `--max-run-duration` + `--instance-termination-action=DELETE` | the guest never booting; this session ending |
| 1 | `shutdown -h +N`, armed before any work | the driver crashing, hanging, or being killed |
| 2 | `budget.py`: spend vs cap every 30 s, **plus a dead-man's switch** on the driver heartbeat | a wedged trainer — an NCCL deadlock bills the same as training |
| 3 | per-phase budgets in the driver | one phase overrunning eating the whole run |
| 4 | a cumulative ledger in GCS, consulted **before** launch | "just one more try" becoming $500 |

Layers 0 and 1 are the ones that matter, because they are the only two that do
not depend on any code of mine still working. `--max-run-duration` is computed
from the dollar cap, not set by hand:

```
minutes = floor(CAP_USD / RATE_PER_HOUR * 60)      # $60 / $29.52 -> 122 min
```

Spot preemption is polled every second at
`/computeMetadata/v1/instance/preempted`; the 30-second notice is far too short
to checkpoint 30B of anything, which is why `ckpt_every_steps: 25` does the real
work and the handler only flushes and marks state. A restart re-syncs `/mnt/ckpt`
from GCS and resumes.

## Driving it

```bash
nt-gpu plan     # prints projected cost and the auto-delete time. Creates nothing.
nt-gpu up       # launches; returns immediately with the instance and its kill time
nt-gpu status   # phase, spend so far, minutes until the box deletes itself
nt-gpu down     # delete every ntx instance now; safe to run twice, safe to run always
```

`nt-gpu plan` is a dry run unless `YES=1`. `nt-gpu down` ends by printing either
`CLEAN: no ntx instances exist.` or a non-zero exit — that last line is the one
to trust, and it is worth typing after every run whatever the logs say.

## Reading the result

```bash
nt grade cand-lora-v1 --completions out/completions.jsonl
nt slices cand-lora-v1          # rewrite / ceiling / saturated, separately
nt compare headroom-k16 cand-lora-v1
```

Two verdicts, and they are not the same question:

- **The pre-registered rule** (unpaired): the candidate's CI lower bound must
  clear the baseline point estimate. This is the rule fixed before any run, and
  it stays the headline.
- **The paired delta**, over the same 52 cases: far more sensitive, because two
  runs on one held-out set are not two independent samples and the shared
  difficulty of a case is variance the unpaired comparison pays for twice.

At this n they can disagree, and the disagreement is the finding. A +8/−1 swing
is unambiguous paired (McNemar p≈0.04) and marginal unpaired. Both are printed;
neither is quietly dropped.
