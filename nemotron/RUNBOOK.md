# The first Qwen LoRA run — decided, before it is paid for

Platform decided by the user 2026-09-12: **Hugging Face for everything** —
training on HF Jobs, weights on the Hub, inference for evaluation on HF. The
GCP design in `gpu/` (a3-highgpu-8g, SPOT, a $60 cap) is retired; what survives
from it is the config and the spend discipline, not the machine.

Everything below is fixed before any adapter exists, and every number is either
read from a live API today or computed here with its arithmetic shown.

## 1. One H200, not eight A100s

`hf jobs hardware`, read 2026-09-12:

| flavor | accelerator | $/hr |
|---|---|---|
| `a100-large` | 1× A100 (80 GB) | 2.50 |
| `rtx-pro-6000` | 1× RTX PRO 6000 (96 GB) | 2.75 |
| **`h200`** | **1× H200 (141 GB)** | **5.00** |
| `a100x8` | 8× A100 (640 GB) | 20.00 |
| `h200x8` | 8× H200 (1128 GB) | 40.00 |

The checkpoint is **55.6 GB** (`model.safetensors.index.json`, `total_size`,
1,199 tensors of which 333 are `model.visual`). Rank-16 LoRA over everything but
the vision tower is ~122.8M trainable parameters. So:

```
weights, BF16                  55.6 GB
LoRA params + AdamW m,v + fp32   1.5 GB   (122.8M × 12 B)
activations, checkpointed, 2048    ~4 GB
                               -------
                                ~61 GB   on a 141 GB card
```

**It fits on one GPU with more than twice the room it needs, so there is no
sharding.** `distributed: fsdp2` comes out of the config: FSDP2 over one device
is machinery that can fail and cannot help. That removes the largest class of
config error in the whole run, and it costs $5.00/hr instead of $20 or $40.

Budget: ~20 min to pull 55.6 GB, ~40 min to train a few hundred examples for a
few epochs. **One hour, $5.00**, bounded by a platform-enforced `--timeout`. The
`a100-large` at $2.50 would leave 24 GB for activations and the optimizer, which
is enough on paper and is not worth the risk of discovering otherwise at 55 GB
of download in.

## 2. Both arms come out of ONE vLLM instance, and this is not a detail

**The confound the platform decision just created.** The current baseline was
generated on **novita's** serving stack through the HF router. A fine-tuned arm
served from our own vLLM would differ from it in kernels, sampling
implementation and tokenizer handling — so a measured delta would be part weights
and part stack, with nothing to separate them.

So the evaluation is **one Job, one vLLM process, two arms**: base
`Qwen/Qwen3.8-27B` loaded with `--enable-lora`, generating the held-out split
twice — once with no adapter, once with ours — same kernels, same seed, same
decode budget. It writes completions to a file and stops. Grading is CPU work
and happens here for free (`nt grade`), which is what that command exists for.

**Consequence, stated plainly: `qwen38-regrade-20260912` is NOT the reference the
fine-tune is measured against.** It stays as the evidence for the engine-drift
null test in `PREREGISTRATION.md` §3b, where it is exactly the right instrument
and where the arms really are the same stack. The fine-tune's reference is the
no-adapter arm of the eval Job, measured on the stack the tuned arm is measured
on. A third run, and it costs nothing extra: it is the same Job.

Eval cost: 2 × 1,184 generations at ≤2048 new tokens on one H200, well inside an
hour. **$5.00.**

## 3. What the whole run costs

| step | where | cost |
|---|---|---|
| sample the SFT set, 117 clean train cases | HF router, novita | ~$6 |
| train, rank-16 LoRA | `hf jobs run --flavor h200` | ~$5 |
| evaluate both arms | `hf jobs run --flavor h200` | ~$5 |
| grade, compare, safety-gate | here | $0 |
| | **total** | **~$16** |

Every step is bounded: sampling by `--max-spend` (which needs `NTX_PRICE_IN` and
`NTX_PRICE_OUT` exported or it is a no-op), both Jobs by `--timeout`.

## 4. The stop conditions, unchanged from PREREGISTRATION.md

The run is **abandoned, not rescued**, if verified on-policy SFT yields fewer
than 150 examples, or the safety gate fails on the tuned arm: `conformance`
MISMATCH 0 and routing UNSAFE 0 on the engine both arms are graded at, and
`nt refusals --run <eval>` MISMATCH 0 over the tuned model's own output. A pass
rate bought with a silent wrong answer is a loss.

And the rule is the conjunction on the 70 non-degenerate cases: paired bootstrap
CI lower bound above 0 **and** exact McNemar p < 0.05 — which, with nothing lost,
needs **at least six cases to flip**.
