# The first Qwen LoRA run — decided, before it is paid for

Platform decided by the user 2026-09-12: **Hugging Face for everything** —
training on HF Jobs, weights on the Hub, inference for evaluation on HF. The
GCP design in `gpu/` (a3-highgpu-8g, SPOT, a $60 cap) is retired; what survives
from it is the config and the spend discipline, not the machine.

Everything below is fixed before any adapter exists, and every number is either
read from a live API today or computed here with its arithmetic shown.

## 1. One H200, not eight A100s

`hf jobs hardware`, read 2026-09-12 and re-read unchanged 2026-09-13:

| flavor | accelerator | $/hr |
|---|---|---|
| `a100-large` | 1× A100 (80 GB) | 2.50 |
| `rtx-pro-6000` | 1× RTX PRO 6000 (96 GB) | 2.75 |
| **`h200`** | **1× H200 (141 GB), 23 vCPU, 256 GB RAM, 3 TB disk** | **5.00** |
| `a100x8` | 8× A100 (640 GB) | 20.00 |
| `h200x8` | 8× H200 (1128 GB) | 40.00 |

The checkpoint is **55.56 GB = 51.75 GiB** (`model.safetensors.index.json`,
`total_size` 55,562,855,904; 1,199 tensors of which 333 are `model.visual`) —
read from the Hub API on 2026-09-13. The card is 141 GB = **131.5 GiB**, so both
sides of this sum are in GiB below; the first version of it mixed units.

**Re-derived 2026-09-13 against `gpu/lypning_lora.py` as written** — not against
the retired NeMo YAML, whose module set is different. The adapter is rank-16 over
every text-tower `nn.Linear` except `lm_head`, which is **116,727,808** trainable
parameters (counted from `config.json`: 16 full-attention layers, 48
gated-delta-net layers, 64 MLPs, hidden 5120, `head_dim` 256, `intermediate_size`
17408). The earlier ~122.8M matched no configuration in the tree.

```
                                          GiB
weights, BF16, one device                51.75
adapter + grads + AdamW m,v, fp32         1.74   (116.7M x 16 B; PEFT upcasts
                                                  the adapter, and the earlier
                                                  12 B/param omitted gradients)
activations, checkpointed, seq 2048, mb 1  ~2     (64 stored layer inputs,
                                                  2048 x 5120 x 2 B = 1.34 GiB,
                                                  plus one layer recomputed)
logits + cross-entropy over a 248,320
  vocabulary at seq 2048                   ~5     (1.89 GiB of fp32 logits, plus
                                                  the softmax and its backward)
                                         -----
train peak                                ~61    of 131.5
```

The generation phase is a **different** peak and the first version of this table
did not have a line for it. At `--gen-group 4 x --samples 16` there are 64
concurrent sequences of roughly 3,250 positions: the 16 full-attention layers
hold **12.7 GiB** of KV cache (2 x 4 kv heads x 256 head_dim x 2 B per token per
layer) and the 48 gated-delta-net layers hold **9.0 GiB** of fp32 recurrent state
(48 x 128 x 128 per layer per sequence, `mamba_ssm_dtype: float32`). So:

```
generate peak  =  51.75 + 12.7 + 9.0  =  ~74 GiB   of 131.5
```

**It fits on one GPU with room to spare in both phases, so there is no sharding**
— which is the conclusion the first table reached, for partly different reasons.
`distributed: fsdp2` comes out of the config: FSDP2 over one device is machinery
that can fail and cannot help. That removes the largest class of config error in
the whole run, and it costs $5.00/hr instead of $20 or $40. The term that grows
if anything is turned up is the generation cache, and it grows linearly in
`--gen-group x --samples`: at 8 x 16 it is ~44 GiB and still fits.

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

**What is actually implemented, checked 2026-09-13 — read this before launching.**
`gpu/lypning_lora.py` is the only executable form of this step, and it differs
from the paragraph above in two ways that change what the operator has to type
and what the run costs.

*Not vLLM.* The script serves both arms from HF `transformers`
(`Qwen3_5ForConditionalGeneration`, `attn_implementation="sdpa"`,
`model.generate`), not from vLLM with `--enable-lora`. That is fine for the
confound — it is still **one stack for both arms** — but "one vLLM instance" is
not what runs, and vLLM's throughput is not what the hour is budgeted against.

*Not one Job.* `--base-arm` is a flag on the whole invocation, so the control arm
is a **second** `hf jobs run`, with a second ~20-minute pull of the 55.56 GB
checkpoint. "A third run, and it costs nothing extra: it is the same Job" is true
of the vLLM design and false of the script: the second arm costs its own download
plus its own generation, call it **$2–3 more**. The way to hold that down is
`--no-train --base-arm` ordering and nothing else; there is no shared-container
path today.

*And the script's own deadline is not the platform's.* `--gen-deadline-s` defaults
to **8400 s**, 2.3× the one hour §3 budgets. Pass `--timeout` and
`--gen-deadline-s` together and make the second smaller than the first, or the
container is killed while the script still believes it has time to start another
group. Completions are uploaded after every group, so what is lost is the tail
and the final manifest rather than the run — but a partial arm is not a
denominator (`stats.completeness` flags it) and both arms must be equally
complete or the pairing is over a set neither of them finished.

## 3. What the whole run costs

| step | where | cost |
|---|---|---|
| sample the SFT set, 93 pool cases | HF router, novita | ~$5 |
| train, rank-16 LoRA | `hf jobs run --flavor h200` | ~$5 |
| evaluate the tuned arm | `hf jobs run --flavor h200` | ~$5 |
| evaluate the base arm (`--base-arm`, its own Job and its own 55.56 GB pull) | `hf jobs run --flavor h200` | ~$2–3 |
| grade, compare, safety-gate | here | $0 |
| | **total** | **~$17–18** |

Every step is bounded: sampling by `--max-spend` (which needs `NTX_PRICE_IN` and
`NTX_PRICE_OUT` exported or it is a no-op), both Jobs by `--timeout`.

## 4. The stop conditions, unchanged from PREREGISTRATION.md

The run is **abandoned, not rescued**, if verified on-policy SFT yields fewer
than 150 examples — **counted on the rewrite population**, which is what that
threshold was always about and what `sample.json` now reports as
`sft_examples_on_task`; see `PREREGISTRATION.md` §2(g), which projects from the
recorded draws that this threshold is at real risk — or the safety gate fails on
the tuned arm: `conformance`
MISMATCH 0 and routing UNSAFE 0 on the engine both arms are graded at, and
`nt refusals --run <eval>` MISMATCH 0 over the tuned model's own output. A pass
rate bought with a silent wrong answer is a loss.

And the rule is the conjunction on the 70 non-degenerate cases: paired bootstrap
CI lower bound above 0 **and** exact McNemar p < 0.05 — which, with nothing lost,
needs **at least six cases to flip**.
