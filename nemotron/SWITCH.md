# Target model: Qwen/Qwen3.8-27B

Switched 2026-09-11 from `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16`.
Everything below was established from the checkpoint and from the NeMo AutoModel
source; nothing is taken from a model card.

## What the new target is

| | Nemotron 3.5 Lightning | **Qwen3.8-27B** |
|---|---|---|
| params | 30B total, **3B active** (MoE) | **~27.8B, DENSE** — 55.6 GB BF16, zero expert tensors |
| architecture | Mamba-2 + MoE + attention | gated-delta-net linear attention, `full_attention_interval: 4` |
| modality | text | **multimodal** — `model.visual` is 333 of 1199 tensors |
| MTP | yes | yes (`mtp_num_hidden_layers: 1`) |
| Automodel | `nemotron_v3` | `qwen3_5`, natively implemented |
| serving | one provider, **billing disabled** | **five providers**, two verified working on this account |

## Two things that actually change the plan

**Compute, up ~9x.** Dense means every parameter is active on every token. The
same step count costs roughly nine times the FLOPs of a 3B-active MoE. Memory is
unchanged (55.6 GB vs 62 GB, both need the 8-GPU node), but a "brief" run is no
longer twenty minutes. Any wall-clock or dollar estimate carried over from the
Nemotron plan is wrong by about an order of magnitude and must be re-derived from
a real run.

**Inference, down to pennies.** The Nemotron BF16 checkpoint was served by exactly
one provider whose pay-as-you-go was disabled, which is why measuring a baseline
meant standing up a dedicated $2.50/hr endpoint and waiting out a 62 GB download.
`Qwen/Qwen3.8-27B` is served by novita, cerebras, featherless-ai, ovhcloud and
deepinfra; novita and deepinfra are verified working on this account. The baseline
and the rejection sampling are now serverless and near-free.

## Pin the provider

Auto-routing picked `cerebras`, which answered with a Cloudflare 403. Worse than
the error: the same model, same prompt, run through two providers gave different
programs and 5.9 s against 87.8 s. A provider is part of the serving stack and
therefore part of the arm's identity — the audit's finding that arm identity is an
unrecorded free-text string, showing up in practice. **Always pin**:

```bash
export NTX_MODEL="Qwen/Qwen3.8-27B:novita"
```

## LoRA targets

`exclude_modules: ["*.out_proj", "*visual*"]`.

`*visual*` freezes a vision tower that plays no part in generating python.

**Corrected 2026-09-12.** This file previously also excluded `*.out_proj`, citing
`qwen3_5/model.py:430` as proof that the weight goes straight into the
gated-delta-net kernel. That citation was wrong: line 430 sits inside
`for linear in (...): nn.init.trunc_normal_(linear.weight, ...)`, a weight
initialiser, with `out_proj` listed beside four `nn.Linear` siblings. The real
call sites are `output = self.out_proj(core_attn_out)` at `cp_linear_attn.py:343`
and `:715` — an ordinary module call, so a LoRA there applies normally.

The Nemotron exclusion it was copied from IS correct for Nemotron:
`nemotron_v3/layers.py:454-455` really does pass `outproj_weight=self.out_proj.weight`
into a fused kernel. Qwen3.5 does not, and the two were conflated. The cost of
the error was ~56 of the widest projections in the model frozen for no reason,
weakening the very intervention the training run is meant to measure — found by
an independent agent re-checking a claim this file asserted as verified.

## What survives the switch, and what does not

**Survives — the pipeline was built model-agnostic and it held.** The corpus (it
describes lypning's subset, not any model), the frozen split (`case_id` hashes
prompt and test, never the model), every audit fix, the eval harness, and the
prompt-recovery work. The smoke test below ran end to end on Qwen with **zero code
changes**.

**Does not survive:**
- `data/baseline.json` — void. Re-measure, but only after the audit fixes land:
  measuring through a harness with 33 known defects would waste the number twice.
- `data/sft/v1/` — rejection-sampled from Nemotron, so off-policy for Qwen. The
  whole point of rejection sampling is that targets come from the model being
  trained. Must be re-sampled.
- `gpu/lora_rank16.yaml` — Nemotron-specific (`ep_size: 8`, `experts: gmm`,
  `dispatcher: deepep`, MTP repeated-layer overrides). Kept for reference;
  `gpu/lora_qwen38_27b.yaml` replaces it.
- The directory name `nemotron/`. Renaming is deferred: three workflows are
  reading absolute paths under it right now.

## Established by running

```
Qwen/Qwen3.8-27B:novita     finish=stop  296 tok  5.9s   -> avoided `import math`, got ceil(2.1) WRONG
Qwen/Qwen3.8-27B:deepinfra  finish=stop  112 tok  87.8s  -> used `import math`, correct, REFUSED
```

Both on the same corpus case, through the unmodified pipeline. They are the two
failure modes this project exists to tell apart: one traded correctness for the
route, the other kept correctness and paid a CPython spawn. The two-axis metric
reads them correctly on the new model without being told anything about it.
