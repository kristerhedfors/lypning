"""Matched-seed decoding without disturbing optimizer/rollout RNG state.

GPU packages are supplied/imported only when executing, never for --plan.
"""
from __future__ import annotations

from dataclasses import asdict

from pipeline.jsonio import append_jsonl
from pipeline.training import messages, program_from_completion
from pipeline.training_contract import complete, draw_seed
from pipeline.training_metrics import summarize


def evaluate(model, tokenizer, cases, verifier, policy, output, step, torch,
             *, seed=1111, draws=4, return_records=False):
    from transformers import GenerationConfig

    was_training = model.training
    checkpointing = model.is_gradient_checkpointing
    old_cache = model.config.text_config.use_cache
    old_generation = model.generation_config
    records = []
    # TRL 1.13 trims and masks using the tokenizer EOS, not a list from the
    # model's serving configuration. Use that same single stop in every arm.
    eos = tokenizer.eos_token_id
    config = GenerationConfig(**policy, eos_token_id=eos, disable_compile=True,
                              pad_token_id=tokenizer.pad_token_id,
                              bos_token_id=tokenizer.bos_token_id)
    try:
        model.generation_config = config
        if checkpointing:
            model.gradient_checkpointing_disable()
        model.config.text_config.use_cache = True
        model.eval()
        # Evaluation cadence must not change subsequent stochastic training.
        with torch.random.fork_rng():
            for case in cases:
                text = tokenizer.apply_chat_template(messages(case), tokenize=False,
                    add_generation_prompt=True, enable_thinking=False)
                batch = tokenizer(text, return_tensors="pt", add_special_tokens=False).to(model.device)
                for draw in range(draws):
                    sample_seed = draw_seed(seed, case["case_id"], draw)
                    torch.manual_seed(sample_seed)
                    with torch.no_grad():
                        ids = model.generate(**batch, generation_config=config)
                    tail = ids[0, batch["input_ids"].shape[1]:].tolist()
                    completion = tokenizer.decode(tail, skip_special_tokens=True)
                    truncated = not complete(tail, eos)
                    program = None if truncated else program_from_completion(completion)
                    score = verifier.score(case, program)
                    row = dict(asdict(score), step=step, case_id=case["case_id"],
                        family=case["family"], population=case["population"],
                        split_group=case.get("split_group", case["family"]),
                        capabilities=case.get("capabilities", []), draw=draw, seed=sample_seed,
                        completion=completion, completion_tokens=len(tail), truncated=truncated,
                        correct=score.correct, native=score.native)
                    records.append(row)
                    append_jsonl(output, row)
    finally:
        model.generation_config = old_generation
        model.config.text_config.use_cache = old_cache
        if checkpointing:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.train(was_training)
    return (summarize(records), records) if return_records else summarize(records)
