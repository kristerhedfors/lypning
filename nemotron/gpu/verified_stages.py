"""Optimizer stages, separated from loading, admission and evaluation.

Imports remain CPU-only until a stage is explicitly executed.
"""
from __future__ import annotations

import random

from pipeline.jsonio import append_jsonl
from pipeline.training import Reward, TrainingError, messages
from pipeline.training_contract import learning_rate


def balanced_cases(cases):
    """Equal family mass without treating cloned task IDs as independent evidence."""
    groups = {}
    for case in cases:
        groups.setdefault(case["family"], []).append(case)
    size = max(map(len, groups.values()))
    return [g[i % len(g)] for g in groups.values() for i in range(size)]



def train_sft(model, tok, args, train_cases, examples, core, torch, effective, checkpoint):
    steps, every = effective["steps"], effective["eval_every"]
    device = model.device
    core.set_train_mode(model)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=effective["learning_rate"], weight_decay=0.01)
    rng = random.Random(args.seed)
    families = {}
    for case, example in zip(train_cases, examples):
        families.setdefault(case["family"], []).append(example)
    for step in range(1, steps + 1):
        # Sample family then case: no duplicated-row weighting artefacts.
        batch = [rng.choice(rng.choice(list(families.values()))) for _ in range(args.batch_size)]
        lr = learning_rate(step, steps, effective["learning_rate"], args.warmup_ratio)
        for group in optimizer.param_groups:
            group["lr"] = lr
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
        torch.nn.utils.clip_grad_norm_(params, 1.0, error_if_nonfinite=True)
        optimizer.step()
        append_jsonl(args.output / "loss.jsonl", {"step": step, "loss": loss_sum, "learning_rate": lr,
            "supervised_tokens": nlabels})
        if (step % every == 0 or step == steps) and checkpoint(step):
            break


def train_grpo(model, tok, args, bundle, train_cases, verifier, effective, policy, checkpoint):
    import torch
    steps, every = effective["steps"], effective["eval_every"]
    max_tokens = effective["max_tokens"]
    device = model.device.type
    from datasets import Dataset
    from transformers import TrainerCallback
    from trl import GRPOConfig, GRPOTrainer
    class DevGate(TrainerCallback):
        def on_pre_optimizer_step(self, args, state, control, **kwargs):
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad],
                                          1.0, error_if_nonfinite=True)
            return control

        def on_step_end(self, args, state, control, **kwargs):
            if state.global_step % every == 0 or state.global_step == steps:
                control.should_training_stop = checkpoint(state.global_step)
            return control
    config = GRPOConfig(
        output_dir=str(args.output / "trainer"), max_steps=steps,
        learning_rate=effective["learning_rate"], per_device_train_batch_size=1,
        gradient_accumulation_steps=args.generations, num_generations=args.generations,
        max_completion_length=max_tokens, temperature=policy["temperature"],
        top_p=policy["top_p"], top_k=policy["top_k"], min_p=policy["min_p"],
        repetition_penalty=policy["repetition_penalty"],
        generation_kwargs={"eos_token_id": tok.eos_token_id, "num_beams": 1},
        # transformers 5 folded the ratio into `warmup_steps`: a float in
        # [0, 1) is a ratio of total steps, an int is exact steps. The pinned
        # TRL rejects `warmup_ratio`; found by the first GPU smoke, 2026-09-15.
        warmup_steps=float(args.warmup_ratio), lr_scheduler_type="linear",
        # No KL: disabling a warm-start adapter would anchor to BASE, not
        # SFT. Correctness/development gates provide the first guardrail.
        chat_template_kwargs={"enable_thinking": False}, beta=0.0,
        loss_type="dr_grpo", scale_rewards="none", mask_truncated_completions=True,
        bf16=device == "cuda", use_cpu=device == "cpu", seed=args.seed,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        save_strategy="no", report_to="none", logging_steps=1,
        remove_unused_columns=False)
    reward = Reward(bundle["cases"], verifier, args.output / "blocked-witnesses.jsonl",
                    eos_token_id=tok.eos_token_id,
                    rollout_path=args.output / "rollouts.jsonl",
                    generations=args.generations, max_no_signal=0 if args.smoke else args.max_no_signal)
    expected_prompts = [tok.apply_chat_template(messages(c), tokenize=False,
                        add_generation_prompt=True, enable_thinking=False) for c in train_cases]
    trainer = GRPOTrainer(model=model, args=config, processing_class=tok,
                          train_dataset=Dataset.from_list([
                              {"prompt": messages(c), "case_id": c["case_id"]} for c in balanced_cases(train_cases)]),
                          reward_funcs=reward, callbacks=[DevGate()])
    actual_prompts = [tok.apply_chat_template(messages(c), tokenize=False,
                      add_generation_prompt=True, chat_template=trainer.chat_template,
                      **trainer.chat_template_kwargs) for c in train_cases]
    if actual_prompts != expected_prompts:
        raise TrainingError("TRL changed the task prompt template; SFT/RL/eval must agree")
    trainer.train()
