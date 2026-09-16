# Target model: Qwen/Qwen3.8-27B

Switched 2026-09-11 from a 30B mixture-of-experts target by another vendor,
whose name this tree no longer spells. **This file is the home of that rule**,
and the rule has the shape of root `CLAUDE.md` invariant 9: a name that still
resolves to something is a name that can drift back into the code.

The earlier target's name survives in the frozen evidence only, where changing
it would falsify a record rather than tidy one:

| where it stays | why |
|---|---|
| the recorded run ids under `runs/` and their `meta.json` | a run id is the join key between a number and its evidence, here and in the private work dataset. Each run records its own `run_id`, so renaming the directory would make it disagree with the record inside it, and invariant 3 quotes every number with the run that produced it |
| the captured corpus and the sightings JSONL | program text an agent actually typed, pinned by hash. Rewriting it would falsify what was captured |
| `CHANGELOG.md` entries dated before the switch, and `AUDIT.md` and `REFACTOR.md` | dated records of what was measured and on which branch — including the free-text model string that the audit's arm-identity finding is *about* |

Everywhere else it is **the earlier target**, and it is never a live
identifier: no directory, module, variable, path or command spells it.
`training/tests/test_naming.py` is the grep, and it does not spell the name
either — it reads it off the recorded run ids, so the rule cannot rot into a
literal nobody maintains.

## Retired numbers

Measured on the earlier model, used as decision inputs for Qwen anyway, and
retired from `LADDER.md` §0 on 2026-09-14:

| number | where it came from | why it does not apply |
|---|---|---|
| pass@1 35.1%, rewrite slice 17.3% thinking-on vs 9.6% off | the earlier model on vLLM/A100, 2026-09-11 | different model, different serving stack. It was used to argue for thinking-on eval; for Qwen the thinking effect on this eval is **unmeasured** |
| "12 of 74 cases flip between thinking on/off on an identical checkpoint" | the same run | it was used as the noise floor. The Qwen noise floors are the two serving-stack nulls: **−0.22pp** and **+1.57pp** ΔSLR (`REVIEW.md` §1) |
| 505,576 thinking tokens vs 22,254 | the same run | the Qwen thinking budget is its own measurement |
Everything below was established from the checkpoint and from the NeMo AutoModel
source; nothing is taken from a model card.

## What the new target is

| | the earlier target | **Qwen3.8-27B** |
|---|---|---|
| params | 30B total, **3B active** (MoE) | **~27.8B, DENSE** — 55.6 GB BF16, zero expert tensors |
| architecture | Mamba-2 + MoE + attention | gated-delta-net linear attention, `full_attention_interval: 4` |
| modality | text | **multimodal** — `model.visual` is 333 of 1199 tensors |
| MTP | yes | yes (`mtp_num_hidden_layers: 1`) |
| Automodel | its own vendor text-architecture class | `qwen3_5`, natively implemented |
| serving | one provider, **billing disabled** | **five providers**, two verified working on this account |

## Two things that actually change the plan

**Compute, up ~9x.** Dense means every parameter is active on every token. The
same step count costs roughly nine times the FLOPs of a 3B-active MoE. Memory is
unchanged (55.6 GB vs 62 GB, both need the 8-GPU node), but a "brief" run is no
longer twenty minutes. Any wall-clock or dollar estimate carried over from the
earlier target's plan is wrong by about an order of magnitude and must be re-derived from
a real run.

**Inference, down to pennies.** The earlier target's BF16 checkpoint was served by exactly
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

`exclude_modules: ["*visual*"]` — the vision tower and nothing else. It plays no
part in generating python.

**Corrected 2026-09-12 in prose, 2026-09-13 in the code that runs.** This file
previously also excluded `*.out_proj` — the line above said so for a day after
the paragraph below retracted it — citing
`qwen3_5/model.py:430` as proof that the weight goes straight into the
gated-delta-net kernel. That citation was wrong: line 430 sits inside
`for linear in (...): nn.init.trunc_normal_(linear.weight, ...)`, a weight
initialiser, with `out_proj` listed beside four `nn.Linear` siblings. The real
call sites are `output = self.out_proj(core_attn_out)` at `cp_linear_attn.py:343`
and `:715` — an ordinary module call, so a LoRA there applies normally.

The exclusion it was copied from IS correct for the earlier target: that
model's Automodel layer file really does pass
`outproj_weight=self.out_proj.weight` into a fused kernel, so a LoRA there
would never apply. Qwen3.5 does not, and the two were conflated. The cost of
the error was 48 of the widest projections in the model — one per
gated-delta-net layer, counted from `model.safetensors.index.json` on
2026-09-13 — frozen for no reason,
weakening the very intervention the training run is meant to measure — found by
an independent agent re-checking a claim this file asserted as verified.

**And the code was still wrong on 2026-09-13.** `gpu/lypning_lora.py` — the file
`hf jobs` actually runs, as opposed to the retired NeMo YAML this correction was
written against — still omitted `out_proj` from `TARGET_MODULES`, still listed
`.*out_proj` in `EXCLUDE_MODULES`, and still carried the retracted rationale in
its docstring; the `verify/manifest.json` already on the Hub records that target
set. Re-checked against the implementation that file loads, HF transformers'
`Qwen3_5GatedDeltaNet`: `out_proj` is declared `nn.Linear(value_dim, hidden_size)`
at `modeling_qwen3_5.py:540` and called as `output = self.out_proj(core_attn_out)`
at `:662`, after the kernel has already returned — the kernel takes
query/key/value/g/beta and no weight. So a LoRA there applies. The exclusion
froze the 48 widest projections in the text tower (1,509,949,440 base parameters)
and cut the adapter from 116,727,808 to 108,077,056 trainable parameters, 8.0%.
It is fixed, and the belief is now a measurement: phase 1 of the smoke test
requires every targeted leaf name to carry a nonzero gradient on a 4-layer random
model, so the next kernel that swallows a weight fails on `cpu-basic` for a tenth
of a cent instead of producing a quietly weaker adapter for $5.

## What survives the switch, and what does not

**Survives — the pipeline was built model-agnostic and it held.** The corpus (it
describes lypning's subset, not any model), the frozen split (`case_id` hashes
prompt and test, never the model), every audit fix, the eval harness, and the
prompt-recovery work. The smoke test below ran end to end on Qwen with **zero code
changes**.

**Does not survive:**
- `data/baseline.json` — void. Re-measure, but only after the audit fixes land:
  measuring through a harness with 33 known defects would waste the number twice.
- `data/sft/v1/` — rejection-sampled from the earlier target, so off-policy for Qwen. The
  whole point of rejection sampling is that targets come from the model being
  trained. Must be re-sampled. `--name` defaulted to `v1` and `fold_draws` reads
  the whole of `draws.jsonl`, so the default invocation would have folded them
  back in: measured 2026-09-13, 1,488 of its 2,800 draws belong to all 93 cases
  of the current pool. `nt sample` now refuses a directory whose draws no
  `backend.json` claims, and refuses to resume one drawn from another model.
- `gpu/lora_rank16.yaml` — specific to the earlier target (`ep_size: 8`, `experts: gmm`,
  `dispatcher: deepep`, MTP repeated-layer overrides). Kept for reference;
  `gpu/lora_qwen38_27b.yaml` replaces it.
- The directory name. It spelled the earlier target and is now `training/`,
  renamed 2026-09-16 with every path, `PYTHONPATH`, workflow, command and
  citation moved with it. The deferral reason — three workflows reading paths
  under it — was the work, not a blocker.

## Established by running

```
Qwen/Qwen3.8-27B:novita     finish=stop  296 tok  5.9s   -> avoided `import math`, got ceil(2.1) WRONG
Qwen/Qwen3.8-27B:deepinfra  finish=stop  112 tok  87.8s  -> used `import math`, correct, REFUSED
```

Both on the same corpus case, through the unmodified pipeline. They are the two
failure modes this project exists to tell apart: one traded correctness for the
route, the other kept correctness and paid a CPython spawn. The two-axis metric
reads them correctly on the new model without being told anything about it.
