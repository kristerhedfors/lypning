"""Model-side helpers for `train_verified.py`: examples, LoRA, the gradient smoke.

WHAT THIS IS FOR
    The pieces of the verified trainer that need torch, peft or transformers at
    import time: building completion-only SFT examples, attaching and checking
    the rank-16 LoRA on Qwen/Qwen3.8-27B (architecture class `qwen3_5`), saving
    it, and the 4-layer gradient smoke that runs before the 55 GB checkpoint is
    touched. `train_verified.py` is the only caller.

    It used to be a whole one-file runner as well -- train, generate, upload --
    for the HF Jobs path that `training/RUNBOOK.md` describes. Nothing launches
    that path any more (`training/hf/round02_pilot.sh` runs `train_verified.py`),
    and every line of it was inside `code_sha256`, so it was removed before the
    next arm's first seed rather than between two seeds.

    `build_examples` is BORROWED by file: `.github/scripts/token_floor.py` and
    `s4_target_floor.py` compile it out of this source without importing torch
    and record the sha256 of its source segment. Its body is kept byte-identical
    so that sha does not move; it must keep reading no module state.

THE TWO TRAPS THIS FILE IS SHAPED AROUND
    1. AutoModelForCausalLM maps qwen3_5 to Qwen3_5ForCausalLM "for VLM
       compatibility". That class names its weights `model.layers.*`; the
       checkpoint names them `model.language_model.layers.*`, there is no
       conversion mapping, and the result is a silently RANDOM 27B model. This
       file names Qwen3_5ForConditionalGeneration, whose key set matches the
       checkpoint exactly (verified: 0 missing, 0 unexpected, 15 `mtp.*` tensors
       ignored by the class's own _keys_to_ignore_on_load_unexpected).
    2. A LoRA on a module a fused kernel consumes by WEIGHT is silently bypassed:
       it trains, it merges, and it changes nothing. `*.out_proj` was excluded
       here for that reason until 2026-09-13, on a rationale copied from the
       earlier model's cookbook -- where it is correct, because that model's
       layers pass `outproj_weight=self.out_proj.weight` into the kernel
       (SWITCH.md).
       Qwen3.8 (architecture class `qwen3_5`) does not. In the implementation this file actually loads,
       transformers' Qwen3_5GatedDeltaNet, the kernel takes query/key/value/g/
       beta and returns `core_attn_out`, and the projection is an ordinary module
       call afterwards: `output = self.out_proj(core_attn_out)`, modeling_qwen3_5
       .py:662, declared `nn.Linear` at :540. So a LoRA there applies normally,
       and excluding it froze the 48 widest projections in the text tower (1.51B
       params) and cut the adapter by 8.0%.
       The belief is now a MEASUREMENT rather than a list: phase 1 requires every
       targeted leaf name to carry a nonzero gradient on a 4-layer random model.
       If a future kernel does start swallowing one of these weights, that fails
       on cpu-basic for a tenth of a cent instead of producing a quietly weaker
       adapter for $5.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

T0 = time.time()


def log(msg: str) -> None:
    print("[%7.1fs] %s" % (time.time() - T0, msg), flush=True)


# ---------------------------------------------------------------------------
# flash-linear-attention is optional, and the torch reference is this
# experiment's declared kernel. transformers resolves chunk_gated_delta_rule at
# IMPORT time: hub kernel, then the `fla` package, then a pure-torch reference
# in its own source. Reaching the reference means making `import fla` fail
# before transformers is imported -- and before anything else imports fla, or
# the blocker is inert (`kernel_block`, which `train_verified.run` installs
# first and checks). Installing it again here is idempotent; it covers an
# import of this module from anywhere else.
#
# Without a CUDA device fla's Triton kernels raise "0 active drivers" --
# measured on cpu-basic, 2026-09-12 -- so the CPU smoke needs fla out of the
# way too, whatever NTX_USE_FLA says.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kernel_block  # noqa: E402
import torch  # noqa: E402

_want_fla = os.environ.get("NTX_USE_FLA", "1") != "0"
_have_gpu = torch.cuda.is_available()
if not (_want_fla and _have_gpu):
    kernel_block.install()
    log("gated-delta-net will use the torch reference (NTX_USE_FLA=%s, cuda=%s)"
        % (os.environ.get("NTX_USE_FLA", "1"), _have_gpu))

from peft import LoraConfig, get_peft_model  # noqa: E402
from transformers import AutoConfig, Qwen3_5ForConditionalGeneration  # noqa: E402

from pipeline.training_contract import BASE_MODEL  # noqa: E402

# Every nn.Linear in the text tower except `lm_head`. Written out rather than
# given as a regex so that the set is readable and so that a name that appears in
# a future checkpoint is NOT silently adapted.
TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj",              # 16 full-attention layers
    "in_proj_qkv", "in_proj_a", "in_proj_b", "in_proj_z",  # 48 gated-delta-net layers
    "out_proj",                                          # 48, and see trap 2 above
    "gate_proj", "up_proj", "down_proj",                 # 64 MLPs
]
# The vision tower plays no part in generating python and is 333 of the 1,199
# tensors. None of its leaf names (`qkv`, `proj`, `linear_fc1`, `linear_fc2`)
# appear above, so this is belt and braces -- and it stays for that reason: it is
# what stops a future widening of TARGET_MODULES from reaching it.
EXCLUDE_MODULES = [".*visual.*"]


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def build_examples(tok, rows, max_seq):
    """(input_ids, labels) with everything up to the assistant turn masked out.

    The prompt is rendered with the model's own chat template and
    enable_thinking=False -- the same two settings the held-out generation uses
    below and the same the baseline was measured under. Training on a prompt
    shaped differently from the one at inference is the quietest way to spend a
    GPU-hour on nothing.
    """
    out, truncated = [], 0
    for r in rows:
        msgs = r["messages"]
        if msgs[-1]["role"] != "assistant":
            raise ValueError("%s: last message is %r, not assistant" % (r.get("case_id"), msgs[-1]["role"]))
        prompt = tok.apply_chat_template(msgs[:-1], tokenize=False,
                                         add_generation_prompt=True, enable_thinking=False)
        full = prompt + msgs[-1]["content"] + "<|im_end|>"
        pids = tok(prompt, add_special_tokens=False)["input_ids"]
        fids = tok(full, add_special_tokens=False)["input_ids"]
        if len(fids) > max_seq:
            truncated += 1
            continue
        # Masking the first len(prompt) tokens is only completion-only loss if the
        # prompt's tokenisation IS a prefix of the whole turn's. A merge across
        # that boundary -- the tokeniser fusing the template's last character with
        # the completion's first -- shifts the mask by a token, which either
        # trains on the last prompt token or drops the first completion token, and
        # does it silently on a loss curve that looks perfectly healthy. It costs
        # one comparison to find out, here, before the weights download.
        if fids[:len(pids)] != pids:
            raise ValueError(
                "%s: the tokeniser merges across the prompt/completion boundary, "
                "so masking %d tokens is not completion-only loss"
                % (r.get("case_id"), len(pids)))
        labels = list(fids)
        for i in range(min(len(pids), len(labels))):
            labels[i] = -100
        if all(x == -100 for x in labels):
            raise ValueError("%s: every label masked" % r.get("case_id"))
        out.append({"case_id": r.get("case_id", ""), "input_ids": fids, "labels": labels})
    return out, truncated


def collate(batch, pad_id, device):
    n = max(len(b["input_ids"]) for b in batch)
    ids, lab, am = [], [], []
    for b in batch:
        k = n - len(b["input_ids"])
        ids.append(b["input_ids"] + [pad_id] * k)
        lab.append(b["labels"] + [-100] * k)
        am.append([1] * len(b["input_ids"]) + [0] * k)
    t = lambda x, d=torch.long: torch.tensor(x, dtype=d, device=device)  # noqa: E731
    return {"input_ids": t(ids), "labels": t(lab), "attention_mask": t(am)}


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
def tiny_config(cfg):
    """The real config, shrunk. Same layer_types pattern, same module names, same
    gating and rope structure -- the things phase 1 is testing -- at 1/2000 the
    parameters."""
    t, v = cfg.text_config, cfg.vision_config
    t.num_hidden_layers = 4
    t.layer_types = ["linear_attention"] * 3 + ["full_attention"]
    t.hidden_size, t.intermediate_size = 512, 1024
    t.head_dim, t.num_attention_heads, t.num_key_value_heads = 64, 8, 2
    t.linear_num_key_heads, t.linear_num_value_heads = 2, 4
    t.linear_key_head_dim, t.linear_value_head_dim = 32, 32
    t.vocab_size = 1024
    t.rope_parameters["mrope_section"] = [3, 3, 2]
    v.depth, v.hidden_size, v.intermediate_size, v.num_heads, v.out_hidden_size = 2, 64, 128, 2, 512
    cfg.image_token_id, cfg.video_token_id = 5, 6
    cfg.vision_start_token_id, cfg.vision_end_token_id = 7, 8
    return cfg


def attach_lora(model, rank, alpha, dropout):
    lc = LoraConfig(r=rank, lora_alpha=alpha, lora_dropout=dropout, bias="none",
                    task_type="CAUSAL_LM", target_modules=TARGET_MODULES,
                    exclude_modules=EXCLUDE_MODULES,
                    # The model is loaded from a snapshot DIRECTORY, so peft
                    # records that local path as the base model and writes it
                    # into the README it generates -- which the Hub then rejects
                    # ("is not valid. Use a model id"). The first checkpoint
                    # upload is at --save-every, so a run dies ten steps in,
                    # after the 55 GB pull and the training that mattered.
                    # Naming the base model here is the one place it is known.
                    base_model_name_or_path=BASE_MODEL)
    return get_peft_model(model, lc)


def save_adapter(pm, out_dir):
    """`save_pretrained`, with the base model named as the Hub knows it.

    peft writes a README whose `base_model:` comes from
    `peft_config.base_model_name_or_path`, and the model was loaded from a
    snapshot DIRECTORY, so that field is a local path like
    `/root/.cache/huggingface/hub/models--Qwen--Qwen3.8-27B/snapshots/1d4bf0f...`.
    The Hub rejects it -- "is not valid. Use a model id" -- and because the first
    checkpoint is at `--save-every`, the run dies ten steps in, AFTER the 55 GB
    pull and after the training that mattered. Setting it in `LoraConfig` is not
    enough on its own: `get_peft_model` re-reads the loaded model's
    `name_or_path`, so it is re-stamped here, immediately before every write.
    """
    for cfg in pm.peft_config.values():
        cfg.base_model_name_or_path = BASE_MODEL
    pm.save_pretrained(out_dir)


def check_adapted_modules(pm):
    """Invariant, asserted on the object rather than read off the config."""
    adapted = sorted({n.rsplit(".lora_A", 1)[0] for n, _ in pm.named_modules() if n.endswith("lora_A")})
    bad = [a for a in adapted if "visual" in a]
    if bad:
        raise SystemExit("LoRA landed on %d forbidden module(s): %s" % (len(bad), bad[:5]))
    if not adapted:
        raise SystemExit("LoRA landed on nothing -- target_modules matched no module")
    leaves = {}
    for a in adapted:
        leaves[a.split(".")[-1]] = leaves.get(a.split(".")[-1], 0) + 1
    # A name in TARGET_MODULES that matched nothing is a typo or a renamed
    # checkpoint, and PEFT does not fail on it -- it just adapts less of the
    # model than the manifest says. Named here rather than counted later.
    missed = [name for name in TARGET_MODULES if name not in leaves]
    if missed:
        raise SystemExit("target_modules matched no module for: %s" % missed)
    trainable = sum(p.numel() for p in pm.parameters() if p.requires_grad)
    log("adapted %d modules %s; trainable %.1fM params"
        % (len(adapted), json.dumps(leaves, sort_keys=True), trainable / 1e6))
    return len(adapted), trainable


def set_train_mode(pm):
    pm.config.text_config.use_cache = False
    pm.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    pm.enable_input_require_grads()
    pm.train()


def set_gen_mode(model):
    """Gradient checkpointing forces use_cache=False. Leaving it off here turns
    every generated token into a full re-prefill, which is not a wrong answer,
    only a bill. The flag lives on text_config, not on config."""
    if hasattr(model, "gradient_checkpointing_disable"):
        model.gradient_checkpointing_disable()
    model.config.text_config.use_cache = True
    model.eval()


# ---------------------------------------------------------------------------
# phase 1: the whole pipeline at 1/2000 scale, on this machine, before 55 GB
# ---------------------------------------------------------------------------
def smoke(device, dtype, args):
    """One real optimiser step and a batched sample on a 4-layer random model.

    `args.optimizer` is the caller's AdamW keyword set -- `train_sft`'s own
    `SFT_OPTIMIZER` -- so the step this takes is the step training will take.
    It used to hard-code (0.9, 0.95) and weight decay 0.1, a second definition
    that agreed with nothing the verified trainer ran.
    """
    log("phase 1: smoke on a 4-layer random model (%s, %s)" % (device, dtype))
    cfg = tiny_config(AutoConfig.from_pretrained(BASE_MODEL, revision=getattr(args, "revision", None)))
    torch.manual_seed(0)
    m = Qwen3_5ForConditionalGeneration(cfg).to(device=device, dtype=dtype)
    pm = attach_lora(m, args.rank, args.alpha, args.lora_dropout)
    check_adapted_modules(pm)
    set_train_mode(pm)
    opt = torch.optim.AdamW([p for p in pm.parameters() if p.requires_grad],
                            lr=args.lr, **args.optimizer)
    ids = torch.randint(10, 1000, (1, 96), device=device)
    lab = ids.clone()
    lab[:, :32] = -100
    am = torch.ones_like(ids)
    out = pm(input_ids=ids, labels=lab, attention_mask=am)
    loss = out.loss.detach()
    if not torch.isfinite(loss):
        raise SystemExit("smoke: loss is %r" % float(loss))
    out.loss.backward()
    got = sum(1 for p in pm.parameters() if p.requires_grad and p.grad is not None)
    # lora_B starts at zero, so on the first step lora_A sees no gradient and
    # roughly half the adapter tensors are legitimately flat. Zero of them
    # nonzero means the graph never reached the adapter -- which is exactly what
    # a LoRA on a module the kernel bypasses looks like.
    nz = sum(1 for p in pm.parameters()
             if p.requires_grad and p.grad is not None and float(p.grad.abs().sum()) > 0)
    if got == 0 or nz == 0:
        raise SystemExit("smoke: %d adapter tensors carried a gradient, %d of them nonzero" % (got, nz))
    # PER LEAF, and this is the check that replaced a belief. A LoRA on a module
    # whose weight a fused kernel consumes directly still trains and still merges;
    # it just never reaches the loss, and the only visible symptom is an adapter
    # that does less than its parameter count says. lora_B is zero-initialised, so
    # on the first step every reached module has exactly one nonzero tensor
    # (lora_B) and one flat one (lora_A) -- a leaf with ZERO nonzero tensors is a
    # leaf the graph did not reach. `out_proj` is the one this run cares about
    # (trap 2), and the check is over every targeted name so the next one is
    # caught the same way.
    live = {}
    for name, param in pm.named_parameters():
        if ".lora_B" not in name or not param.requires_grad:
            continue
        leaf = name.split(".lora_B")[0].split(".")[-1]
        hit = param.grad is not None and float(param.grad.abs().sum()) > 0
        live[leaf] = live.get(leaf, 0) + (1 if hit else 0)
    dead = sorted(leaf for leaf, n in live.items() if n == 0)
    if dead:
        raise SystemExit(
            "smoke: LoRA on %s carried NO gradient -- the forward pass does not go "
            "through the module, so an adapter there would train and merge and "
            "change nothing. Remove it from TARGET_MODULES." % dead)
    opt.step()
    opt.zero_grad(set_to_none=True)
    log("smoke: loss %.4f, %d adapter tensors with grads, %d of them nonzero; "
        "every targeted leaf reached the loss: %s"
        % (float(loss), got, nz, json.dumps(live, sort_keys=True)))
    merged = pm.merge_and_unload()
    set_gen_mode(merged)
    p_ids = torch.randint(10, 1000, (2, 16), device=device)
    p_am = torch.ones_like(p_ids)
    p_am[0, :4] = 0
    with torch.no_grad():
        g = merged.generate(input_ids=p_ids, attention_mask=p_am, do_sample=True,
                            temperature=args.temperature, top_p=args.top_p, top_k=args.top_k,
                            max_new_tokens=16, min_new_tokens=16, num_return_sequences=2,
                            pad_token_id=0)
    if tuple(g.shape) != (4, 32):
        raise SystemExit("smoke: generate returned %s, expected (4, 32)" % (tuple(g.shape),))
    log("smoke: merge + batched left-padded sampling ok, shape %s" % (tuple(g.shape),))
    del pm, m, merged, opt
    if device == "cuda":
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
