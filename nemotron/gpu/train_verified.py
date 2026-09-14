# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "torch==2.9.1", "transformers==5.17.0", "peft==0.20.0",
#   "accelerate==1.15.0", "huggingface-hub==1.31.0", "safetensors==0.8.0",
#   "trl==1.13.0", "datasets==4.7.0",
# ]
# ///
"""Task-first Qwen3.8 SFT / execution-RL. Requires this checkout, not just this file.

No cloud submission or uploads. --plan validates locally without importing torch.
Use an isolated disposable worker: the subprocess runner is not a security jail.
See nemotron/TRAINING.md for staged acceptance gates and held-out evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.jsonio import append_jsonl, sha256_of, write_json
from pipeline.training import (Reward, TrainingError, Verifier, load_bundle,
                               messages, program_from_completion)

BASE_MODEL = "Qwen/Qwen3.8-27B"


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=("sft", "grpo", "eval"))
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--engine", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True, help="new directory, never overwrite")
    p.add_argument("--revision", required=True, help="immutable 40-character base-model Hub commit")
    p.add_argument("--adapter", type=Path, help="local adapter for GRPO warm start or evaluation")
    p.add_argument("--from-base", action="store_true", help="explicit GRPO-from-base ablation")
    p.add_argument("--plan", action="store_true", help="validate experiment without GPU/downloads")
    p.add_argument("--smoke", action="store_true", help="tiny random Qwen model, two real trainer steps")
    p.add_argument("--isolated-worker", action="store_true",
                   help="attest this is a disposable worker with no sensitive files/credentials")
    p.add_argument("--steps", type=int, default=100)
    p.add_argument("--eval-every", type=int, default=10)
    p.add_argument("--patience", type=int, default=3)
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--lr", type=float, help="default SFT 2e-5 / GRPO 1e-6")
    p.add_argument("--batch-size", type=int, default=4, help="SFT effective batch / GRPO generations")
    p.add_argument("--max-seq", type=int, default=4096)
    p.add_argument("--max-new-tokens", type=int, default=1024)
    p.add_argument("--seed", type=int, default=1111)
    p.add_argument("--eval-split", choices=("dev", "test"), default="dev")
    return p


def adapter_identity(path, revision):
    path = path.resolve(strict=True)
    manifest_path = path / "experiment.json"
    if not manifest_path.exists():
        raise TrainingError("warm-start adapter needs its experiment.json (base/split provenance)")
    manifest = json.loads(manifest_path.read_text())
    if manifest["revision"] != revision:
        raise TrainingError("adapter/base-model revision mismatch")
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in path.iterdir() if p.suffix in (".json", ".safetensors")}
    if "adapter_model.safetensors" not in files:
        raise TrainingError("adapter weights missing")
    return {"sha256": sha256_of(files), "experiment": manifest}


def preflight(args):
    if int(os.environ.get("WORLD_SIZE", "1")) != 1:
        raise TrainingError("this runner supports one process/GPU; do not launch with torchrun")
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise TrainingError("--revision must be an immutable model commit, not main")
    if args.output.exists():
        raise TrainingError("--output already exists")
    if min(args.steps, args.eval_every, args.patience, args.rank, args.batch_size,
           args.max_seq, args.max_new_tokens) <= 0 or (args.lr is not None and args.lr <= 0):
        raise TrainingError("training lengths, rank, learning rate and batches must be positive")
    if args.stage == "grpo" and args.batch_size < 2:
        raise TrainingError("GRPO needs at least two generations per prompt")
    if args.stage == "grpo" and bool(args.adapter) == bool(args.from_base):
        raise TrainingError("GRPO needs --adapter or explicit --from-base, exclusively")
    if args.stage == "sft" and (args.adapter or args.from_base):
        raise TrainingError("SFT starts from the pinned base; adapters belong to GRPO/eval")
    if args.stage != "eval" and args.eval_split != "dev":
        raise TrainingError("test split cannot select a checkpoint")
    bundle = load_bundle(args.bundle, args.engine)
    if not args.plan and not args.isolated_worker:
        raise TrainingError("generated-code execution requires --isolated-worker; see TRAINING.md")
    if not args.smoke and bundle["limits"]["memory_mb"] == 0 and not args.plan:
        raise TrainingError("memory cap disabled: only --smoke may use this bundle")
    adapter = adapter_identity(args.adapter, args.revision) if args.adapter else None
    if adapter and adapter["experiment"]["bundle_digest"] != bundle["digest"]:
        raise TrainingError("adapter trained with a different experiment/split")
    if adapter and adapter["experiment"].get("smoke") and not args.smoke:
        raise TrainingError("a tiny-model smoke adapter is not a production warm start")
    if adapter and adapter["experiment"]["args"]["rank"] != args.rank and args.stage == "grpo":
        raise TrainingError("GRPO continues the existing adapter rank; --rank does not resize it")
    return bundle, adapter


def balanced_cases(cases):
    """Equal family mass without treating cloned task IDs as independent evidence."""
    groups = {}
    for case in cases:
        groups.setdefault(case["family"], []).append(case)
    size = max(map(len, groups.values()))
    return [g[i % len(g)] for g in groups.values() for i in range(size)]


def smoke_config(config, vocab_size, shrink):
    """Shrink layers, NOT the token space used by real prompts/completions.

The legacy random-ID gradient smoke shrinks vocab to 1024. Applying that config
to the real Qwen tokenizer would index beyond the embedding table immediately.
"""
    names = ("image_token_id", "video_token_id", "vision_start_token_id", "vision_end_token_id")
    special_ids = {name: getattr(config, name) for name in names}
    config = shrink(config)
    config.text_config.vocab_size = vocab_size
    for name, value in special_ids.items():
        setattr(config, name, value)
    return config


def evaluate(model, tokenizer, cases, verifier, max_tokens, output, step, torch):
    was_training = model.training
    checkpointing = model.is_gradient_checkpointing
    old_cache = model.config.text_config.use_cache
    if checkpointing:
        model.gradient_checkpointing_disable()
    model.config.text_config.use_cache = True
    model.eval()
    records = []
    try:
        for case in cases:
            text = tokenizer.apply_chat_template(messages(case), tokenize=False,
                                                 add_generation_prompt=True, enable_thinking=False)
            batch = tokenizer(text, return_tensors="pt", add_special_tokens=False).to(model.device)
            with torch.no_grad():
                ids = model.generate(**batch, do_sample=False, max_new_tokens=max_tokens,
                                     pad_token_id=tokenizer.pad_token_id)
            tail = ids[0, batch["input_ids"].shape[1]:]
            completion = tokenizer.decode(tail, skip_special_tokens=True)
            # A syntactically closed block cut before EOS is still a truncated rollout.
            eos = model.generation_config.eos_token_id or tokenizer.eos_token_id
            eos = eos if isinstance(eos, list) else [eos]
            program = program_from_completion(completion) if int(tail[-1]) in eos else None
            score = verifier.score(case, program)
            records.append({"step": step, "case_id": case["case_id"], "family": case["family"],
                            "completion": completion, "reward": score.reward, "status": score.status,
                            "correct": score.reward > 0,
                            "native": score.reward > 0 and score.native_tests == score.total_tests})
            append_jsonl(output, records[-1])
    finally:
        model.config.text_config.use_cache = old_cache
        if checkpointing:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.train(was_training)
    # Family macro average; correctness comes first, never a blended surrogate.
    families = {r["family"] for r in records}
    def macro(key):
        return sum(sum(r[key] for r in records if r["family"] == f) /
                   sum(r["family"] == f for r in records) for f in families) / len(families)
    return (macro("correct"), macro("native"))


def run(args, bundle, adapter_info):
    # Block fused kernels before importing transformers, preserving the existing
    # exact Qwen class and per-leaf LoRA gradient smoke checks.
    os.environ["NTX_USE_FLA"] = "0"
    import lypning_lora as core
    import torch
    from huggingface_hub import snapshot_download
    from peft import PeftModel
    from transformers import AutoConfig, AutoTokenizer, Qwen3_5ForConditionalGeneration, set_seed

    if not args.smoke and not torch.cuda.is_available():
        raise TrainingError("CUDA required for a real 27B run")
    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    tok = AutoTokenizer.from_pretrained(BASE_MODEL, revision=args.revision)
    tok.padding_side = "left"
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    train_cases = balanced_cases([c for c in bundle["cases"] if c["split"] == "train"])
    dev_cases = [c for c in bundle["cases"] if c["split"] == args.eval_split]
    # Token limits are admission checks, not permission to silently drop long
    # examples or slice the task away. Run these before downloading 27B weights.
    for case in train_cases + dev_cases:
        prompt_ids = tok.apply_chat_template(messages(case), tokenize=True,
                                              add_generation_prompt=True, enable_thinking=False)
        if len(prompt_ids) + args.max_new_tokens > args.max_seq:
            raise TrainingError("prompt + completion budget exceeds --max-seq: " + case["case_id"])
    examples = []
    if args.stage == "sft":
        rows = [{"case_id": c["case_id"], "messages": messages(c) + [{"role": "assistant",
                 "content": "```python\n" + c["reference"].rstrip() + "\n```"}]} for c in train_cases]
        examples, dropped = core.build_examples(tok, rows, args.max_seq)
        if dropped or not examples:
            raise TrainingError("SFT rows over token limit; do not silently change the curriculum")
    if args.stage != "eval":
        core.smoke(device, dtype, SimpleNamespace(
            revision=args.revision, rank=args.rank, alpha=2 * args.rank,
            lora_dropout=0.0, lr=args.lr or 1e-6, temperature=1.0, top_p=0.95, top_k=0))
    if args.smoke:
        cfg = smoke_config(AutoConfig.from_pretrained(BASE_MODEL, revision=args.revision),
                           len(tok), core.tiny_config)
        # The random smoke BASE must reload identically, independently of the
        # training seed or RNG consumed by the preceding gradient probe.
        torch.manual_seed(0)
        model = Qwen3_5ForConditionalGeneration(cfg).to(device=device, dtype=dtype)
        set_seed(args.seed)
    else:
        path = snapshot_download(BASE_MODEL, revision=args.revision,
                                 allow_patterns=["*.json", "*.jinja", "*.txt", "*.safetensors"])
        model, loading = Qwen3_5ForConditionalGeneration.from_pretrained(
            path, dtype=dtype, attn_implementation="sdpa", device_map={"": 0},
            output_loading_info=True)
        if any(loading.get(k) for k in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")):
            raise TrainingError("checkpoint loading failed: " + str(loading))
        checkpoint = set(json.loads((Path(path) / "model.safetensors.index.json").read_text())["weight_map"])
        have = set(model.state_dict())
        ignore = [re.compile(p) for p in (model._keys_to_ignore_on_load_unexpected or [])]
        if have - checkpoint or any(not any(p.search(k) for p in ignore) for k in checkpoint - have):
            raise TrainingError("checkpoint/model class key mismatch")
    if args.adapter:
        # Continue the SAME adapter so its saved weights include the SFT warm
        # start and reload on the pinned base without a hidden merged parent.
        model = PeftModel.from_pretrained(model, str(args.adapter), is_trainable=args.stage == "grpo")
    elif args.stage != "eval":
        model = core.attach_lora(model, args.rank, 2 * args.rank, 0.0)
    if args.stage != "eval":
        core.check_adapted_modules(model)
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {"base_model": BASE_MODEL, "revision": args.revision, "stage": args.stage,
                "bundle_digest": bundle["digest"], "adapter": adapter_info,
                "smoke": args.smoke, "seed": args.seed,
                "code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                (Path(__file__), Path(core.__file__))},
                "args": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                "versions": {p: importlib.metadata.version(p) for p in
                             ("torch", "transformers", "peft", "accelerate", "trl", "datasets")}}
    write_json(args.output / "experiment.json", manifest)
    verifier = Verifier(args.engine, **bundle["limits"], identity=bundle["identity"])
    max_tokens = min(32, args.max_new_tokens) if args.smoke else args.max_new_tokens
    def measure(step):
        return evaluate(model, tok, dev_cases, verifier, max_tokens,
                        args.output / "evaluations.jsonl", step, torch)
    best = measure(0)
    if args.stage == "eval":
        write_json(args.output / "metrics.json", {"correct": best[0], "correct_native": best[1]})
        return

    # Save every candidate separately; 'best.json' selects one without deleting
    # evidence. Baseline checkpoint 0 remains available if training regresses.
    def save(step):
        path = args.output / ("adapter-%d" % step)
        core.save_adapter(model, str(path))
        write_json(path / "experiment.json", manifest)
    save(0)
    best_step, stale = 0, 0
    write_json(args.output / "best.json", {"step": best_step, "correct": best[0],
                                           "correct_native": best[1]})
    def checkpoint(step):
        nonlocal best, best_step, stale
        score = measure(step)
        save(step)
        if score > best:
            best, best_step, stale = score, step, 0
        else:
            stale += 1
        write_json(args.output / "best.json", {"step": best_step, "correct": best[0],
                                               "correct_native": best[1]})
        return stale >= args.patience

    steps = 2 if args.smoke else args.steps
    every = 1 if args.smoke else args.eval_every
    if args.stage == "sft":
        core.set_train_mode(model)
        params = [p for p in model.parameters() if p.requires_grad]
        optimizer = torch.optim.AdamW(params, lr=args.lr or 2e-5, weight_decay=0.01)
        rng = random.Random(args.seed)
        for step in range(1, steps + 1):
            batch = [rng.choice(examples) for _ in range(args.batch_size)]
            nlabels = sum(sum(x != -100 for x in e["labels"][1:]) for e in batch)
            optimizer.zero_grad(set_to_none=True)
            loss_sum = 0.0
            for example in batch:
                inputs = core.collate([example], tok.pad_token_id, device)
                loss = model(**inputs).loss
                if not torch.isfinite(loss):
                    raise TrainingError("non-finite SFT loss")
                count = sum(x != -100 for x in example["labels"][1:])
                (loss * count / nlabels).backward()
                loss_sum += float(loss.detach()) * count / nlabels
            # On the first step require every targeted projection to participate.
            if step == 1:
                live = {}
                for name, param in model.named_parameters():
                    if ".lora_B" in name:
                        leaf = name.split(".lora_B")[0].split(".")[-1]
                        live[leaf] = live.get(leaf, False) or (param.grad is not None and bool(param.grad.abs().sum() > 0))
                if not live or not all(live.values()):
                    raise TrainingError("dead adapter projections: " + str(live))
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            optimizer.step()
            append_jsonl(args.output / "loss.jsonl", {"step": step, "loss": loss_sum})
            if (step % every == 0 or step == steps) and checkpoint(step):
                break
    else:
        from datasets import Dataset
        from transformers import TrainerCallback
        from trl import GRPOConfig, GRPOTrainer
        class DevGate(TrainerCallback):
            def on_step_end(self, args, state, control, **kwargs):
                if state.global_step % every == 0 or state.global_step == steps:
                    control.should_training_stop = checkpoint(state.global_step)
                return control
        config = GRPOConfig(
            output_dir=str(args.output / "trainer"), max_steps=steps,
            learning_rate=args.lr or 1e-6, per_device_train_batch_size=1,
            gradient_accumulation_steps=args.batch_size, num_generations=args.batch_size,
            max_completion_length=max_tokens, temperature=1.0, top_p=0.95,
            # No KL: disabling a warm-start adapter would anchor to BASE, not
            # SFT. Correctness/development gates provide the first guardrail.
            chat_template_kwargs={"enable_thinking": False}, beta=0.0,
            loss_type="dr_grpo", scale_rewards="none", mask_truncated_completions=True,
            bf16=device == "cuda", use_cpu=device == "cpu", seed=args.seed,
            gradient_checkpointing_kwargs={"use_reentrant": False},
            save_strategy="no", report_to="none", logging_steps=1,
            remove_unused_columns=False)
        reward = Reward(bundle["cases"], verifier, args.output / "blocked-witnesses.jsonl",
                        eos_token_id=tok.eos_token_id, rollout_path=args.output / "rollouts.jsonl")
        expected_prompts = [tok.apply_chat_template(messages(c), tokenize=False,
                            add_generation_prompt=True, enable_thinking=False) for c in train_cases]
        trainer = GRPOTrainer(model=model, args=config, processing_class=tok,
                              train_dataset=Dataset.from_list([
                                  {"prompt": messages(c), "case_id": c["case_id"]} for c in train_cases]),
                              reward_funcs=reward, callbacks=[DevGate()])
        actual_prompts = [tok.apply_chat_template(messages(c), tokenize=False,
                          add_generation_prompt=True, chat_template=trainer.chat_template,
                          **trainer.chat_template_kwargs) for c in train_cases]
        if actual_prompts != expected_prompts:
            raise TrainingError("TRL changed the task prompt template; SFT/RL/eval must agree")
        trainer.train()
    write_json(args.output / "best.json", {"step": best_step, "correct": best[0],
                                           "correct_native": best[1]})


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        bundle, adapter = preflight(args)
        if args.plan:
            print(json.dumps({"stage": args.stage, "model": BASE_MODEL, "revision": args.revision,
                              "bundle_digest": bundle["digest"], "target": bundle["identity"],
                              "cases": {s: sum(c["split"] == s for c in bundle["cases"])
                                        for s in ("train", "dev", "test")}}, indent=2))
        else:
            run(args, bundle, adapter)
        return 0
    except (TrainingError, OSError, KeyError) as exc:
        print("training blocked: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
