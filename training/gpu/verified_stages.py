"""Optimizer stages, separated from loading, admission and evaluation.

Imports remain CPU-only until a stage is explicitly executed.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import random

from pipeline.jsonio import append_jsonl
from pipeline.mismatch_policy import ENGINE_MISMATCH_FILE, MismatchScoring
from pipeline.training import Reward, TrainingError, messages
from pipeline.training_contract import learning_rate

#: THE SFT optimiser: one definition for `train_sft` and for the gradient smoke
#: that runs before it. These are the values `train_sft` has always stepped --
#: torch's AdamW defaults with weight decay 0.01 -- kept rather than "fixed" to
#: the (0.9, 0.95) / 0.1 the smoke and the retired runner used, because moving
#: them would make arm A drift from what it was declared as, for a knob nobody
#: measured here. Weight decay is minor; what matters is that it is ONE value
#: across arms and that `experiment.json` says which (`sft_optimizer`).
SFT_OPTIMIZER = {"betas": (0.9, 0.999), "eps": 1e-8, "weight_decay": 0.01}
#: Gradient clipping for SFT and GRPO alike, stated rather than inherited from a
#: library default that can move under a pin bump.
MAX_GRAD_NORM = 1.0


def balanced_cases(cases):
    """Equal family mass without treating cloned task IDs as independent evidence."""
    groups = {}
    for case in cases:
        groups.setdefault(case["family"], []).append(case)
    size = max(map(len, groups.values()))
    return [g[i % len(g)] for g in groups.values() for i in range(size)]


def sft_batches(cases, examples, steps, batch_size, seed):
    """Deterministic family cycles: every family appears before any repeats.

    Within a family one example is taken per cycle, WITHOUT replacement: each
    family is a seeded shuffled queue that is popped and reshuffled only once
    it is empty, so every example of a family is seen before any is seen twice.
    Across families this preserves equal family mass without the round-02
    failure mode where with-replacement draws could omit a small family
    entirely during a short run; within a family it removes the same failure
    one level down -- `rng.choice` saw only about 63% of a large family's
    distinct cases in one pass's worth of draws.

    Only positions are drawn, never values, so the schedule depends on the
    family of each item and nothing inside it: `supervised_plan` relies on
    that to plan with cases where `run()` trains on their examples.
    """
    if len(cases) != len(examples):
        raise TrainingError("SFT cases/examples differ; do not silently change the curriculum")
    families = {}
    for case, example in zip(cases, examples):
        families.setdefault(case["family"], []).append(example)
    if not families:
        raise TrainingError("SFT needs training families")
    rng = random.Random(seed)
    schedule = []
    needed = int(steps) * int(batch_size)
    keys = list(families)
    queues = {family: [] for family in keys}
    while len(schedule) < needed:
        cycle = keys[:]
        rng.shuffle(cycle)
        for family in cycle:
            if not queues[family]:
                queues[family] = list(range(len(families[family])))
                rng.shuffle(queues[family])
            schedule.append(families[family][queues[family].pop()])
            if len(schedule) == needed:
                break
    return [schedule[i:i + batch_size] for i in range(0, needed, batch_size)]


def informative_cases(rollouts_path, cases):
    """The train cases whose probe group had reward variation (0 < p < 1).

    "Informative" is `probe_report`'s own definition -- more than one distinct
    reward among the NON-truncated draws -- so the filter and the admission
    gate cannot disagree about which cases count. A group whose draws all agree
    has zero advantage under GRPO and contributes no gradient; the probe has
    already paid to find those groups, so skipping them is free. Order is the
    input order, and an empty result refuses rather than training on nothing.
    """
    rewards = {}
    for line in Path(rollouts_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if not row["truncated"]:
                rewards.setdefault(row["case_id"], set()).add(row["reward"])
    kept = [case for case in cases if len(rewards.get(case["case_id"], ())) > 1]
    if not kept:
        raise TrainingError("the probe found no informative train case; nothing for GRPO to learn from")
    return kept


def supervised_tokens(batches):
    """The exact number of assistant tokens the planned SFT schedule exposes."""
    return sum(sum(token != -100 for token in example["labels"][1:])
               for batch in batches for example in batch)


def train_sft(model, tok, args, train_cases, examples, core, torch, effective, checkpoint,
              batches=None):
    steps, every = effective["steps"], effective["eval_every"]
    device = model.device
    core.set_train_mode(model)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=effective["learning_rate"], **SFT_OPTIMIZER)
    batches = batches or sft_batches(train_cases, examples, steps, args.batch_size, args.seed)
    seen_tokens = 0
    for step, batch in enumerate(batches, 1):
        lr = learning_rate(step, steps, effective["learning_rate"], args.warmup_ratio)
        for group in optimizer.param_groups:
            group["lr"] = lr
        nlabels = sum(sum(x != -100 for x in e["labels"][1:]) for e in batch)
        seen_tokens += nlabels
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
        # The pre-clip norm is the one number that tells a flat loss from a
        # starved one (norm near zero) or a clipped-every-step one (norm pinned
        # above the threshold); it was computed every step and thrown away.
        grad_norm = torch.nn.utils.clip_grad_norm_(params, MAX_GRAD_NORM, error_if_nonfinite=True)
        optimizer.step()
        append_jsonl(args.output / "loss.jsonl", {"step": step, "loss": loss_sum, "learning_rate": lr,
            "grad_norm": float(grad_norm),
            "supervised_tokens": nlabels, "supervised_tokens_total": seen_tokens})
        if step % every == 0 or step == steps:
            # A registered dose trains to completion. Selection is post hoc and
            # reads the saved checkpoints afterwards, so an evaluation that
            # looks bad mid-run is evidence, never a reason to stop producing
            # it: seed 1111's GRPO stopped at 15 of a registered 20 on three
            # rejected checks, and the missing adapter cannot be recovered.
            checkpoint(step)


def train_grpo(model, tok, args, bundle, train_cases, verifier, effective, policy, checkpoint):
    import torch
    steps, every = effective["steps"], effective["eval_every"]
    max_tokens = effective["max_tokens"]
    device = model.device.type
    from datasets import Dataset
    from transformers import TrainerCallback
    from trl import GRPOConfig, GRPOTrainer
    loss_path = args.output / "loss.jsonl"
    # An engine-mismatch completion scores 0 and is counted, never an abort
    # (`pipeline.mismatch_policy`, 2026-09-24); its witness goes to the stage's
    # PRIVATE engine-mismatches.jsonl. The 1% bound is per RUN, on the
    # registered draws (steps x prompts x generations): per step it would be
    # 1% of 32 draws, so a single mismatch would end the run exactly as the
    # abort did. A mismatch rewards 0 although CPython accepted the program,
    # which pushes the policy away from whatever reached the engine bug; the
    # bound keeps that push to at most 1% of the run's draws, and the witness
    # is how the bug gets fixed instead.
    scoring = MismatchScoring(verifier, args.output / ENGINE_MISMATCH_FILE,
                              planned=steps * args.grpo_prompts * args.generations)

    class CheckpointCallback(TrainerCallback):
        """Refuse a non-finite gradient, save on the cadence, log every step.

        It does not clip: the trainer already has, at `max_grad_norm`, before
        this hook runs. A norm taken at +inf changes no gradient and still
        raises on a NaN or an inf, which is the only job left here.
        """
        def on_pre_optimizer_step(self, args, state, control, **kwargs):
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad],
                                          math.inf, error_if_nonfinite=True)
            return control

        def on_step_end(self, args, state, control, **kwargs):
            # Selection only; `should_training_stop` is deliberately not set.
            if state.global_step % every == 0 or state.global_step == steps:
                checkpoint(state.global_step)
            return control

        def on_log(self, args, state, control, logs=None, **kwargs):
            # TRL keeps `log_history` in memory and `save_strategy="no"` never
            # writes it, so a GRPO stage left no per-step record at all and the
            # grpo option of `loss-summary.yml` had nothing to read. Numbers
            # only -- the file sits beside SFT's `loss.jsonl` and is read the
            # same way, and a log key must never become a channel for case text.
            row = {key: value for key, value in sorted((logs or {}).items())
                   if isinstance(value, (int, float)) and not isinstance(value, bool)}
            # Per-step rows only: the trainer's end-of-run summary (train_loss,
            # train_runtime, ...) has no `loss`, and a reader that takes the
            # last row as the last step would read it as one.
            if "loss" in row:
                # Cumulative over the run: a count, never which draws.
                append_jsonl(loss_path, dict(row, step=state.global_step,
                                             engine_mismatches=scoring.count))
            return control
    # One optimizer step = `grpo_prompts` prompt groups of `generations` draws,
    # one sequence per forward (per-example, as SFT: no padding-free packing).
    # TRL's generation batch defaults to per-device x accumulation, so the
    # whole step's completions are drawn and scored together and each group
    # stays contiguous for `Reward`'s group checks.
    sequences = args.grpo_prompts * args.generations
    config = GRPOConfig(
        output_dir=str(args.output / "trainer"), max_steps=steps,
        learning_rate=effective["learning_rate"], per_device_train_batch_size=1,
        gradient_accumulation_steps=sequences, num_generations=args.generations,
        max_grad_norm=MAX_GRAD_NORM,
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
    reward = Reward(bundle["cases"], scoring, args.output / "blocked-witnesses.jsonl",
                    eos_token_id=tok.eos_token_id,
                    rollout_path=args.output / "rollouts.jsonl",
                    generations=args.generations,
                    score_workers=args.score_workers)
    expected_prompts = [tok.apply_chat_template(messages(c), tokenize=False,
                        add_generation_prompt=True, enable_thinking=False) for c in train_cases]
    trainer = GRPOTrainer(model=model, args=config, processing_class=tok,
                          train_dataset=Dataset.from_list([
                              {"prompt": messages(c), "case_id": c["case_id"]} for c in balanced_cases(train_cases)]),
                          reward_funcs=reward, callbacks=[CheckpointCallback()])
    actual_prompts = [tok.apply_chat_template(messages(c), tokenize=False,
                      add_generation_prompt=True, chat_template=trainer.chat_template,
                      **trainer.chat_template_kwargs) for c in train_cases]
    if actual_prompts != expected_prompts:
        raise TrainingError("TRL changed the task prompt template; SFT/RL/eval must agree")
    trainer.train()
