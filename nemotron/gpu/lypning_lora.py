# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "torch==2.9.1",
#     "transformers==5.17.0",
#     "peft==0.20.0",
#     "accelerate==1.15.0",
#     "huggingface-hub==1.31.0",
#     "hf-xet==1.6.0",
#     "safetensors==0.8.0",
#     "flash-linear-attention==0.5.2",
# ]
# ///
"""Rank-16 LoRA on Qwen/Qwen3.8-27B, then held-out completions. One file, one GPU.

WHAT THIS IS FOR
    Move the model toward python that the lypning Rust subset interpreter can RUN
    rather than python it refuses, without trading correctness for it. This file
    does the two things that need a GPU -- train the adapter, generate the
    held-out completions -- and nothing else. Grading needs the lypning engine
    binaries and a sandbox; that is CPU work, it happens on the operator's box
    for free via `nt grade`, and a GPU-hour spent on it is a GPU-hour burned.

WHY ONE GPU
    The whole SFT set is ~64k tokens per epoch (measured, 154 examples, mean 413
    tokens). Three epochs is ~191k tokens. Dense 27.4B at 8 FLOPs/param/token is
    ~4e16 FLOPs -- minutes on one H200, whatever the parallelism. The 8-GPU
    recipe beside this one exists because 55.6 GB did not fit on one 80 GB card;
    an H200's 141 GB removes that constraint and with it FSDP, torchrun, NCCL
    and every way those three fail at minute 40 of a rented node.

PHASE ORDER IS COST ORDER
    Each phase is cheaper to fail than the one after it, and nothing downloads
    55 GB until the inputs have been read, the tokenizer has rendered every
    example, a write to the output repo has already succeeded, and the exact
    training and generation code has run end to end on a 4-layer random model on
    this GPU. `--verify` stops after that smoke test; it is the same file, run
    on cpu-basic for a tenth of a cent.

THE TWO TRAPS THIS FILE IS SHAPED AROUND
    1. AutoModelForCausalLM maps qwen3_5 to Qwen3_5ForCausalLM "for VLM
       compatibility". That class names its weights `model.layers.*`; the
       checkpoint names them `model.language_model.layers.*`, there is no
       conversion mapping, and the result is a silently RANDOM 27B model. This
       file names Qwen3_5ForConditionalGeneration, whose key set matches the
       checkpoint exactly (verified: 0 missing, 0 unexpected, 15 `mtp.*` tensors
       ignored by the class's own _keys_to_ignore_on_load_unexpected).
    2. `*.out_proj` on the gated-delta-net layers is consumed by the kernel
       directly, so a LoRA there is silently bypassed. It is not in the target
       list, and `check_adapted_modules` asserts that after the fact rather than
       trusting the list.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import json
import math
import os
import random
import re
import sys
import time
import traceback

T0 = time.time()


def log(msg: str) -> None:
    print("[%7.1fs] %s" % (time.time() - T0, msg), flush=True)


import torch  # noqa: E402

# ---------------------------------------------------------------------------
# flash-linear-attention is the riskiest line in the dependency header, and the
# only one that is optional. transformers resolves chunk_gated_delta_rule at
# IMPORT time: hub kernel, then the `fla` package, then a pure-torch reference
# in its own source. The reference is correct and slower, and reaching it means
# making `import fla` fail before transformers is imported.
#
# Two reasons to reach it. Without a CUDA device fla's Triton kernels raise
# "0 active drivers" -- measured on cpu-basic, 2026-09-12 -- so the whole
# --verify path is unreachable on CPU unless fla is out of the way. And on the
# GPU, if the kernel signature has drifted from what this transformers expects,
# the fix has to be a relaunch flag rather than a rebuilt script, because the
# retry costs a second 55 GB download either way.
# ---------------------------------------------------------------------------
class _Blocked(importlib.abc.MetaPathFinder):
    def __init__(self, names): self.names = set(names)

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.names:
            raise ImportError("blocked: " + fullname)
        return None


_want_fla = os.environ.get("NTX_USE_FLA", "1") != "0"
_have_gpu = torch.cuda.is_available()
if not (_want_fla and _have_gpu):
    sys.meta_path.insert(0, _Blocked(["fla", "fla_core"]))
    log("gated-delta-net will use the torch reference (NTX_USE_FLA=%s, cuda=%s)"
        % (os.environ.get("NTX_USE_FLA", "1"), _have_gpu))

from huggingface_hub import HfApi, snapshot_download  # noqa: E402
from peft import LoraConfig, get_peft_model  # noqa: E402
from transformers import AutoConfig, AutoTokenizer, Qwen3_5ForConditionalGeneration  # noqa: E402

try:
    import fla  # noqa: F401
    KERNELS = "fla %s" % getattr(fla, "__version__", "?")
except Exception:
    KERNELS = "torch reference"

BASE_MODEL = "Qwen/Qwen3.8-27B"

# Every nn.Linear in the text tower except `out_proj` and `lm_head`. Written out
# rather than given as a regex so that the set is readable and so that a name
# that appears in a future checkpoint is NOT silently adapted.
TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj",              # 16 full-attention layers
    "in_proj_qkv", "in_proj_a", "in_proj_b", "in_proj_z",  # 48 gated-delta-net layers
    "gate_proj", "up_proj", "down_proj",                 # 64 MLPs
]
# Inert given the list above -- PEFT will say so, and that warning is the point:
# it is evidence that the target list, not the exclusion, is doing the work. It
# stays because it is what stops a future widening of TARGET_MODULES from
# quietly putting a LoRA where the kernel cannot see it.
EXCLUDE_MODULES = [".*out_proj", ".*visual.*"]


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def read_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


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
        labels = list(fids)
        for i in range(min(len(pids), len(labels))):
            labels[i] = -100
        if all(x == -100 for x in labels):
            raise ValueError("%s: every label masked" % r.get("case_id"))
        out.append({"case_id": r.get("case_id", ""), "input_ids": fids, "labels": labels})
    return out, truncated


def split_by_case(examples, val_frac, seed):
    """Split on case_id, never on example: two samples of one case in different
    halves is a leak that makes the val loss say the run is fine."""
    if val_frac <= 0:
        return examples, []
    cases = sorted({e["case_id"] for e in examples})
    rnd = random.Random(seed)
    rnd.shuffle(cases)
    n_val = max(1, int(round(val_frac * len(cases))))
    val = set(cases[:n_val])
    return ([e for e in examples if e["case_id"] not in val],
            [e for e in examples if e["case_id"] in val])


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
                    exclude_modules=EXCLUDE_MODULES)
    return get_peft_model(model, lc)


def check_adapted_modules(pm):
    """Invariant, asserted on the object rather than read off the config."""
    adapted = sorted({n.rsplit(".lora_A", 1)[0] for n, _ in pm.named_modules() if n.endswith("lora_A")})
    bad = [a for a in adapted if a.split(".")[-1] == "out_proj" or "visual" in a]
    if bad:
        raise SystemExit("LoRA landed on %d forbidden module(s): %s" % (len(bad), bad[:5]))
    if not adapted:
        raise SystemExit("LoRA landed on nothing -- target_modules matched no module")
    leaves = {}
    for a in adapted:
        leaves[a.split(".")[-1]] = leaves.get(a.split(".")[-1], 0) + 1
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
    log("phase 1: smoke on a 4-layer random model (%s, %s)" % (device, dtype))
    cfg = tiny_config(AutoConfig.from_pretrained(BASE_MODEL))
    torch.manual_seed(0)
    m = Qwen3_5ForConditionalGeneration(cfg).to(device=device, dtype=dtype)
    pm = attach_lora(m, args.rank, args.alpha, args.lora_dropout)
    check_adapted_modules(pm)
    set_train_mode(pm)
    opt = torch.optim.AdamW([p for p in pm.parameters() if p.requires_grad],
                            lr=args.lr, betas=(0.9, 0.95), eps=1e-8, weight_decay=0.1)
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
    opt.step()
    opt.zero_grad(set_to_none=True)
    log("smoke: loss %.4f, %d adapter tensors with grads, %d of them nonzero" % (float(loss), got, nz))
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


# ---------------------------------------------------------------------------
# phase 3: train
# ---------------------------------------------------------------------------
def train(pm, train_ex, val_ex, args, pad_id, device, on_step, hist):
    set_train_mode(pm)
    params = [p for p in pm.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, betas=(0.9, 0.95), eps=1e-8,
                            weight_decay=args.weight_decay)
    per_epoch = max(1, math.ceil(len(train_ex) / args.global_batch))
    total = per_epoch * args.epochs
    warm = min(args.warmup_steps, max(1, total // 4))
    log("training: %d examples, global batch %d, %d steps/epoch, %d epochs, %d steps, %d warmup"
        % (len(train_ex), args.global_batch, per_epoch, args.epochs, total, warm))

    def lr_at(step):
        if step < warm:
            return args.lr * (step + 1) / warm
        p = (step - warm) / max(1, total - warm)
        return args.min_lr + 0.5 * (args.lr - args.min_lr) * (1 + math.cos(math.pi * p))

    rnd = random.Random(args.seed)
    order = []
    for _ in range(args.epochs):
        e = list(range(len(train_ex)))
        rnd.shuffle(e)
        order.extend(e)
    step = 0
    # `hist` is the caller's list and is appended to IN PLACE. Rebinding it here
    # -- `hist = []` then returning it -- is what made every mid-run progress.json
    # and every crash upload carry "history": [], i.e. no loss curve at exactly
    # the moment the loss curve is the evidence you need.
    for s in range(total):
        for g in opt.param_groups:
            g["lr"] = lr_at(s)
        chunk = order[s * args.global_batch:(s + 1) * args.global_batch]
        if not chunk:
            break
        tot_loss, ntok = 0.0, 0
        for i in range(0, len(chunk), args.micro_batch):
            batch = [train_ex[j] for j in chunk[i:i + args.micro_batch]]
            b = collate(batch, pad_id, device)
            out = pm(**b)
            n = int((b["labels"] != -100).sum())
            (out.loss * n).backward()
            tot_loss += float(out.loss.detach()) * n
            ntok += n
        for p in params:
            if p.grad is not None:
                p.grad /= max(1, ntok)
        gn = float(torch.nn.utils.clip_grad_norm_(params, args.clip))
        opt.step()
        opt.zero_grad(set_to_none=True)
        step += 1
        loss = tot_loss / max(1, ntok)
        hist.append({"step": step, "loss": loss, "lr": lr_at(s), "grad_norm": gn,
                     "tokens": ntok, "t": round(time.time() - T0, 1)})
        log("step %3d/%d  loss %.4f  lr %.2e  gnorm %.2f  tok %d%s"
            % (step, total, loss, lr_at(s), gn, ntok,
               ("  peakmem %.1fGB" % (torch.cuda.max_memory_allocated() / 2**30)) if device == "cuda" else ""))
        if args.save_every and step % args.save_every == 0:
            on_step(step, hist)
    if val_ex:
        pm.eval()
        with torch.no_grad():
            tl, tn = 0.0, 0
            for i in range(0, len(val_ex), args.micro_batch):
                b = collate(val_ex[i:i + args.micro_batch], pad_id, device)
                o = pm(**b)
                n = int((b["labels"] != -100).sum())
                tl += float(o.loss) * n
                tn += n
        log("validation loss %.4f over %d examples / %d tokens" % (tl / max(1, tn), len(val_ex), tn))
        hist.append({"step": step, "val_loss": tl / max(1, tn), "val_examples": len(val_ex)})
        pm.train()
    return hist


# ---------------------------------------------------------------------------
# phase 4: generate
# ---------------------------------------------------------------------------
def generate(model, tok, prompts, args, out_path, flush):
    """k samples per held-out case, written and uploaded as they land.

    Prompts are sorted by length so a batch is not paced by one outlier, and
    generation is grouped by case so num_return_sequences does the k-fold
    expansion inside one prefill instead of k of them.
    """
    set_gen_mode(model)
    tok.padding_side = "left"
    rendered = []
    for p in prompts:
        text = tok.apply_chat_template(p["messages"], tokenize=False,
                                       add_generation_prompt=True, enable_thinking=False)
        rendered.append((len(tok(text, add_special_tokens=False)["input_ids"]), p["case_id"], text))
    rendered.sort()
    eos = [tok.convert_tokens_to_ids("<|im_end|>"), tok.eos_token_id]
    eos = sorted({e for e in eos if e is not None})
    done = 0
    deadline = T0 + args.gen_deadline_s if args.gen_deadline_s else None
    # A bare `now > deadline` check at the group boundary has no margin: one group
    # of --gen-group x --samples sequences can run the full --max-new-tokens, and
    # starting one at deadline-minus-a-second means the platform --timeout kills
    # the container mid-group, losing that group AND the final uploads. So stop
    # when the NEXT group would not fit: the widest group seen so far, plus a
    # reserve for the closing completions + manifest pushes.
    worst_group_s = 0.0
    reserve_s = args.gen_reserve_s
    with open(out_path, "a", encoding="utf-8") as fh:
        for i in range(0, len(rendered), args.gen_group):
            if deadline and time.time() + worst_group_s + reserve_s > deadline:
                log("stopping before group %d: %.0fs left, widest group so far %.0fs + %.0fs reserve "
                    "-- %d/%d cases done, uploading what exists"
                    % (i // args.gen_group + 1, deadline - time.time(), worst_group_s, reserve_s,
                       done, len(rendered)))
                break
            group = rendered[i:i + args.gen_group]
            enc = tok([g[2] for g in group], return_tensors="pt", padding=True,
                      add_special_tokens=False).to(model.device)
            t1 = time.time()
            with torch.no_grad():
                out = model.generate(**enc, do_sample=True, temperature=args.temperature,
                                     top_p=args.top_p, top_k=args.top_k,
                                     max_new_tokens=args.max_new_tokens,
                                     num_return_sequences=args.samples,
                                     eos_token_id=eos, pad_token_id=tok.pad_token_id)
            new = out[:, enc["input_ids"].shape[1]:]
            for gi, (_, case_id, _) in enumerate(group):
                for s in range(args.samples):
                    seq = new[gi * args.samples + s]
                    keep = [int(t) for t in seq.tolist()]
                    if keep and keep[-1] in eos:
                        keep = keep[:-1]
                    while keep and keep[-1] == tok.pad_token_id:
                        keep.pop()
                    fh.write(json.dumps({"case_id": case_id, "sample": s,
                                         "text": tok.decode(keep, skip_special_tokens=True),
                                         "completion_tokens": len(keep)}, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            worst_group_s = max(worst_group_s, time.time() - t1)
            done += len(group)
            log("generated %d/%d cases (%d completions) in %.1fs, %d new tokens max"
                % (done, len(rendered), done * args.samples, time.time() - t1, int(new.shape[1])))
            flush(done, len(rendered))
    return done, len(rendered)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="cand-lora-r16-v1")
    ap.add_argument("--inputs-repo", default="headforce/ntx-lypning-lora-inputs")
    ap.add_argument("--inputs-revision", default="main")
    # REQUIRED, with no default, deliberately. A default here is a way to spend
    # a full-price run on the wrong data by omitting a flag: sft/v1 was
    # rejection-sampled from Nemotron, is off-policy for Qwen, and its first
    # record's target is the literal-output cheat `print(4.0, 2, 3, 3.1416, 3.0)`.
    ap.add_argument("--sft", required=True,
                    help="path INSIDE --inputs-repo, e.g. sft/v2/sft.jsonl. Never a local path.")
    ap.add_argument("--prompts", default="holdout_prompts.jsonl")
    ap.add_argument("--out-repo", default="headforce/qwen38-27b-lypning-lora-r16")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--global-batch", type=int, default=16)
    ap.add_argument("--micro-batch", type=int, default=1)
    ap.add_argument("--max-seq", type=int, default=2048)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--lora-dropout", type=float, default=0.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--min-lr", type=float, default=1e-5)
    ap.add_argument("--warmup-steps", type=int, default=5)
    ap.add_argument("--weight-decay", type=float, default=0.1)
    ap.add_argument("--clip", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=1111)
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--save-every", type=int, default=10)
    # These five are the sampling identity `nt grade` compares against the
    # baseline. Changing one makes the run incomparable, not merely different.
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--max-new-tokens", type=int, default=2048)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--top-k", type=int, default=0)
    ap.add_argument("--gen-group", type=int, default=4, help="held-out cases per generate() call")
    ap.add_argument("--gen-deadline-s", type=float, default=8400.0,
                    help="stop generating and upload at this elapsed time. Default leaves "
                         "40 min of headroom under --timeout 3h; set 0 to disable.")
    ap.add_argument("--gen-reserve-s", type=float, default=300.0,
                    help="wall-clock kept back from --gen-deadline-s for the closing uploads")
    ap.add_argument("--verify", action="store_true",
                    help="do everything that does not need the 55 GB checkpoint, then stop")
    ap.add_argument("--no-train", action="store_true", help="load the adapter from --out-repo instead")
    # The control arm, served by THIS container rather than by novita. The stored
    # baseline was measured through a provider, and SWITCH.md's own evidence is
    # that the provider is part of an arm's identity -- two providers gave
    # different programs for one prompt. So the paired delta against that
    # baseline carries a serving-stack confound. This flag removes it for the
    # price of a second generation phase, and it is a flag rather than an edit
    # precisely so that settling the question later costs no code change.
    ap.add_argument("--base-arm", action="store_true",
                    help="generate from the UNADAPTED base in this container: the control "
                         "arm for the confound the novita baseline cannot remove")
    ap.add_argument("--no-gen", action="store_true")
    args = ap.parse_args()

    api = HfApi()
    prefix = "verify/" if args.verify else "runs/%s/" % args.run_id
    work = os.environ.get("NTX_WORK", "/tmp/ntx")
    os.makedirs(work, exist_ok=True)
    comp_path = os.path.join(work, "completions.jsonl")

    # --- phase 0 -----------------------------------------------------------
    log("phase 0: preflight")
    log("torch %s  cuda %s  transformers %s  gated-delta-net kernels: %s" % (
        torch.__version__, torch.version.cuda, __import__("transformers").__version__, KERNELS))
    if torch.cuda.is_available():
        n = torch.cuda.device_count()
        log("%d GPU(s): %s" % (n, ", ".join(
            "%s %.0fGB" % (torch.cuda.get_device_name(i),
                           torch.cuda.get_device_properties(i).total_memory / 2**30) for i in range(n))))
        device, dtype = "cuda", torch.bfloat16
    elif args.verify:
        log("no GPU -- --verify runs the smoke on CPU in float32")
        device, dtype = "cpu", torch.float32
    else:
        raise SystemExit("no CUDA device: this job needs one and will not pretend otherwise")

    src = snapshot_download(args.inputs_repo, repo_type="dataset", revision=args.inputs_revision,
                            allow_patterns=[args.sft, args.prompts])
    sft_path = os.path.join(src, args.sft)
    sft_sha = hashlib.sha256(open(sft_path, "rb").read()).hexdigest()
    sft_rows = read_jsonl(sft_path)
    prompt_rows = read_jsonl(os.path.join(src, args.prompts))
    head = prompt_rows[0] if "case_id" not in prompt_rows[0] else {}
    prompts = prompt_rows[1:] if head else prompt_rows
    log("inputs: %s sha256 %s -> %d SFT examples (%d cases), %d held-out prompts, prompt_sha %s"
        % (args.sft, sft_sha[:16], len(sft_rows), len({r.get("case_id") for r in sft_rows}),
           len(prompts), head.get("prompt_sha", "?")))
    if not sft_rows or not prompts:
        raise SystemExit("inputs are empty")

    tok = AutoTokenizer.from_pretrained(BASE_MODEL)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    examples, truncated = build_examples(tok, sft_rows, args.max_seq)
    tr, va = split_by_case(examples, args.val_frac, args.seed)
    ntok = sum(len(e["input_ids"]) for e in examples)
    nlab = sum(sum(1 for x in e["labels"] if x != -100) for e in examples)
    log("tokenised: %d usable (%d over --max-seq %d), %d train / %d val, %d tokens, %d supervised"
        % (len(examples), truncated, args.max_seq, len(tr), len(va), ntok, nlab))
    for p in prompts:
        tok.apply_chat_template(p["messages"], tokenize=False,
                                add_generation_prompt=True, enable_thinking=False)
    log("all %d held-out prompts render under the chat template" % len(prompts))

    sampling = {"enable_thinking": False, "max_tokens": args.max_new_tokens,
                "samples": args.samples, "temperature": args.temperature,
                "top_p": args.top_p, "top_k": args.top_k, "seed": args.seed}
    manifest = {"run_id": args.run_id, "base_model": BASE_MODEL, "sampling": sampling,
                "args": vars(args), "prompt_sha": head.get("prompt_sha"),
                "holdout_manifest_sha256": head.get("holdout_manifest_sha256"),
                "sft": {"repo": args.inputs_repo, "path": args.sft, "revision": args.inputs_revision,
                        "sha256": sft_sha,
                        "examples": len(sft_rows), "usable": len(examples), "train": len(tr),
                        "val": len(va), "tokens": ntok, "supervised_tokens": nlab},
                "target_modules": TARGET_MODULES, "exclude_modules": EXCLUDE_MODULES,
                "torch": torch.__version__, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # `model` is the one header field `nt grade` copies into the run's arm
    # identity (meta.json backend.model), and SWITCH.md's standing complaint is
    # that arm identity is an unrecorded free-text string. So the SFT draw goes
    # IN it: two runs that differ only in which rejection sample they trained on
    # can then never be confused in `nt results`. Grade WITHOUT --model so this
    # string is what is recorded.
    arm = ("%s@%s (base, unadapted, in-container)" % (BASE_MODEL, args.run_id) if args.base_arm
           else "%s@%s (sft=%s#%s)" % (args.out_repo, args.run_id, args.sft, sft_sha[:8]))
    with open(comp_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(sampling={k: v for k, v in sampling.items()},
                                 model=arm, run_id=args.run_id, sft_sha256=sft_sha,
                                 sft_path=args.sft, base_model=BASE_MODEL,
                                 prompt_sha=head.get("prompt_sha")),
                            sort_keys=True) + "\n")
    manifest["arm"] = arm

    def put(local, name):
        api.upload_file(path_or_fileobj=local, path_in_repo=prefix + name,
                        repo_id=args.out_repo, repo_type="model")

    def put_bytes(obj, name):
        p = os.path.join(work, name.replace("/", "_"))
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, sort_keys=True, default=str)
        put(p, name)

    # The first write to the output repo happens here, before anything is
    # downloaded. A token that cannot write is a two-minute failure, not a
    # forty-minute one.
    put_bytes(manifest, "manifest.json")
    log("phase 0 ok: manifest written to %s/%smanifest.json" % (args.out_repo, prefix))

    hist = []
    state = {"phase": "smoke", "generated": 0, "planned": len(prompts)}

    def flush(done=None, planned=None):
        if done is not None:
            state["generated"], state["planned"] = done, planned
        put_bytes({"state": state, "history": hist, "elapsed_s": round(time.time() - T0, 1)},
                  "progress.json")
        if os.path.getsize(comp_path) > 0:
            put(comp_path, "completions.jsonl")

    try:
        # --- phase 1 ---------------------------------------------------------
        smoke(device, dtype, args)
        if args.verify:
            with open(comp_path, "a", encoding="utf-8") as fh:
                for p in prompts[:2]:
                    fh.write(json.dumps({"case_id": p["case_id"], "sample": 0,
                                         "text": "```python\nprint('verify')\n```",
                                         "completion_tokens": 9}, sort_keys=True) + "\n")
            state["phase"] = "verified"
            flush(2, len(prompts))
            log("VERIFY OK -- inputs read, prompts rendered, repo written, a LoRA over the "
                "same target set trained and sampled end to end. Stopping before the checkpoint.")
            return 0

        # --- phase 2 ---------------------------------------------------------
        state["phase"] = "download"
        flush()
        log("phase 2: downloading %s (~55 GB)" % BASE_MODEL)
        t = time.time()
        path = snapshot_download(BASE_MODEL, allow_patterns=["*.json", "*.jinja", "*.txt", "*.safetensors"])
        gb = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(path) for f in fs) / 2**30
        log("phase 2 ok: %.1f GB in %.0fs (%.0f MB/s)" % (gb, time.time() - t, gb * 1024 / (time.time() - t)))

        # --- phase 3 ---------------------------------------------------------
        state["phase"] = "load"
        flush()
        log("phase 3: loading Qwen3_5ForConditionalGeneration in bfloat16")
        t = time.time()
        model = Qwen3_5ForConditionalGeneration.from_pretrained(
            path, dtype=torch.bfloat16, attn_implementation="sdpa", device_map={"": 0})
        log("loaded in %.0fs, %.1f GB allocated" % (time.time() - t, torch.cuda.memory_allocated() / 2**30))
        # The one failure this job cannot afford is the quiet one: a class whose
        # weight names do not match the checkpoint's loads nothing, warns, and
        # trains a LoRA on top of 27B random numbers. Assert the key sets agree
        # rather than reading a warning out of a log nobody will re-read.
        with open(os.path.join(path, "model.safetensors.index.json"), encoding="utf-8") as fh:
            ckpt = set(json.load(fh)["weight_map"])
        have = set(model.state_dict().keys())
        ign = [re.compile(pat) for pat in (model._keys_to_ignore_on_load_unexpected or [])]
        missing = sorted(have - ckpt)
        unexpected = [k for k in sorted(ckpt - have) if not any(pat.search(k) for pat in ign)]
        if missing or unexpected:
            raise SystemExit("checkpoint/model key mismatch: %d missing %s, %d unexpected %s"
                             % (len(missing), missing[:4], len(unexpected), unexpected[:4]))
        log("weights: %d tensors loaded, 0 missing, 0 unexpected, %d ignored by design (mtp)"
            % (len(have), len(ckpt) - len(have)))
        if args.base_arm:
            # No adapter is attached at all. An arm that is meant to be the
            # unadapted model must not have gone anywhere near a LoRA -- merging
            # a zeroed one and unloading it again is a claim, not a control.
            pm = model
            log("phase 3 skipped: base arm, no adapter attached and nothing trained")
        elif args.no_train:
            # Attaching a fresh LoRA and then loading a second one on top of it
            # leaves two nested adapters and a warning nobody reads. On the
            # recovery path -- the one you run AFTER already losing a run -- take
            # the trained adapter and nothing else.
            from peft import PeftModel
            pm = PeftModel.from_pretrained(model, args.out_repo, subfolder=prefix + "adapter",
                                           is_trainable=False)
            n_ad, n_tr = check_adapted_modules(pm)
            manifest["adapted_modules"], manifest["trainable_params"] = n_ad, n_tr
            log("phase 3 skipped: adapter loaded from %s/%sadapter" % (args.out_repo, prefix))
        else:
            pm = attach_lora(model, args.rank, args.alpha, args.lora_dropout)
            n_ad, n_tr = check_adapted_modules(pm)
            manifest["adapted_modules"], manifest["trainable_params"] = n_ad, n_tr
            state["phase"] = "train"
            flush()

            def on_step(step, h):
                pm.save_pretrained(os.path.join(work, "adapter"))
                api.upload_folder(folder_path=os.path.join(work, "adapter"), repo_id=args.out_repo,
                                  path_in_repo=prefix + "adapter", repo_type="model",
                                  commit_message="%s adapter @ step %d" % (args.run_id, step))
                flush()

            train(pm, tr, va, args, tok.pad_token_id, device, on_step, hist)
            pm.save_pretrained(os.path.join(work, "adapter"))
            api.upload_folder(folder_path=os.path.join(work, "adapter"), repo_id=args.out_repo,
                              path_in_repo=prefix + "adapter", repo_type="model",
                              commit_message="%s final adapter" % args.run_id)
            log("phase 3 ok: adapter at %s/%sadapter" % (args.out_repo, prefix))

        # --- phase 4 ---------------------------------------------------------
        if args.no_gen:
            state["phase"] = "done-no-gen"
            flush()
            return 0
        state["phase"] = "generate"
        flush()
        log("phase 4: %s, generating %d x %d completions"
            % ("base arm, nothing to merge" if args.base_arm else "merging the adapter",
               len(prompts), args.samples))
        merged = model if args.base_arm else pm.merge_and_unload()
        done, planned = generate(merged, tok, prompts, args, comp_path, flush)
        state["phase"] = "done" if done == planned else "partial"
        manifest["generated_cases"], manifest["planned_cases"] = done, planned
        manifest["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        manifest["wall_s"] = round(time.time() - T0, 1)
        put_bytes(manifest, "manifest.json")
        flush(done, planned)
        log("phase 4 %s: %d/%d cases -> %s/%scompletions.jsonl"
            % (state["phase"], done, planned, args.out_repo, prefix))
        return 0
    except BaseException as exc:
        state["phase"] = "failed: %s: %s" % (type(exc).__name__, exc)
        log("FAILED in phase %r" % state["phase"])
        traceback.print_exc()
        try:
            put_bytes({"state": state, "history": hist, "traceback": traceback.format_exc(),
                       "elapsed_s": round(time.time() - T0, 1)}, "progress.json")
            if os.path.exists(comp_path) and os.path.getsize(comp_path) > 0:
                put(comp_path, "completions.jsonl")
        except Exception:
            traceback.print_exc()
        raise


if __name__ == "__main__":
    sys.exit(main())
