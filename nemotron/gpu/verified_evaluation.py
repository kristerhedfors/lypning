"""Matched-seed decoding without disturbing optimizer/rollout RNG state.

GPU packages are supplied/imported only when executing, never for --plan.

Draws are generated in chunks and scored concurrently. One `generate` call
covers a chunk of cases with every draw of each (left-padded prompts,
``num_return_sequences=draws``) under one seed, and the completions of a chunk
are verified on a thread pool. The one-draw-per-call form could not finish: on
2026-09-16 the h200 pilot (job 6aaa4b2c) spent about 30 s per draw — a
1024-token generation on reference kernels plus thirteen pooled-sandbox
requests of verification at ~1.6 s each — so 300 cases × 16 draws × 2 arms was
days against a 6 h cap.

Pairing survives the batching. The chunking is a function of case order,
``draws`` and ``sequences_per_call``, and a chunk's seed a function of the run
seed and the case IDs it holds, so the base arm and the candidate arm draw the
same noise for the same chunk; both are recorded on every row. Records keep
(case, draw) order whatever thread finishes first.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from pipeline.jsonio import append_jsonl
from pipeline.training import messages, program_from_completion
from pipeline.training_contract import complete, draw_seed
from pipeline.training_metrics import summarize

#: Sequences one `generate` call carries; a chunk is this many cases × draws.
SEQUENCES_PER_CALL = 64
#: Concurrent verifier scorings; one pooled sandbox host serves 50.
SCORE_WORKERS = 16


def chunked(cases, draws, sequences_per_call):
    """Consecutive chunks of cases whose draws fit one `generate` call."""
    per_chunk = max(1, int(sequences_per_call) // max(1, int(draws)))
    return [cases[i:i + per_chunk] for i in range(0, len(cases), per_chunk)]


def chunk_seed(seed, chunk):
    """The seed a chunk is generated under: the run seed and the cases it holds."""
    return draw_seed(seed, "|".join(str(c["case_id"]) for c in chunk), 0)


def trim(tail, eos, pad):
    """A batched completion ends at its first EOS; trailing padding is not
    generation. A tail without EOS is returned whole and reads as truncated."""
    stops = eos if isinstance(eos, (list, tuple, set)) else [eos]
    for i, tok in enumerate(tail):
        if int(tok) in stops:
            return list(tail[:i + 1])
    if pad is not None and pad not in stops:
        while tail and int(tail[-1]) == pad:
            tail = tail[:-1]
    return list(tail)


def evaluate(model, tokenizer, cases, verifier, policy, output, step, torch,
             *, seed=1111, draws=4, return_records=False,
             sequences_per_call=SEQUENCES_PER_CALL, score_workers=SCORE_WORKERS):
    from transformers import GenerationConfig

    was_training = model.training
    checkpointing = model.is_gradient_checkpointing
    old_cache = model.config.text_config.use_cache
    old_generation = model.generation_config
    old_padding = getattr(tokenizer, "padding_side", None)
    records = []
    # TRL 1.13 trims and masks using the tokenizer EOS, not a list from the
    # model's serving configuration. Use that same single stop in every arm.
    eos = tokenizer.eos_token_id
    pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else eos
    settings = dict(policy, num_return_sequences=int(draws))
    config = GenerationConfig(**settings, eos_token_id=eos, disable_compile=True,
                              pad_token_id=pad, bos_token_id=tokenizer.bos_token_id)
    try:
        model.generation_config = config
        if checkpointing:
            model.gradient_checkpointing_disable()
        model.config.text_config.use_cache = True
        model.eval()
        # Prompts of one chunk are left-padded so every completion starts at
        # the same index; padding_side is restored for the trainer.
        tokenizer.padding_side = "left"
        # Evaluation cadence must not change subsequent stochastic training.
        with torch.random.fork_rng():
            for chunk in chunked(list(cases), draws, sequences_per_call):
                texts = [tokenizer.apply_chat_template(messages(case), tokenize=False,
                         add_generation_prompt=True, enable_thinking=False) for case in chunk]
                batch = tokenizer(texts, return_tensors="pt", padding=True,
                                  add_special_tokens=False).to(model.device)
                prompt_len = batch["input_ids"].shape[1]
                sample_seed = chunk_seed(seed, chunk)
                torch.manual_seed(sample_seed)
                with torch.no_grad():
                    ids = model.generate(**batch, generation_config=config)
                # `generate` groups its return sequences by input, in input order.
                pending = []
                for i, case in enumerate(chunk):
                    for draw in range(draws):
                        tail = trim(ids[i * draws + draw, prompt_len:].tolist(), eos, pad)
                        completion = tokenizer.decode(tail, skip_special_tokens=True)
                        truncated = not complete(tail, eos)
                        program = None if truncated else program_from_completion(completion)
                        pending.append((case, draw, tail, completion, truncated, program))
                workers = max(1, min(int(score_workers), len(pending)))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    scores = list(pool.map(lambda p: verifier.score(p[0], p[5]), pending))
                for (case, draw, tail, completion, truncated, program), score in zip(pending, scores):
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
        if old_padding is not None:
            tokenizer.padding_side = old_padding
        if checkpointing:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.train(was_training)
    return (summarize(records), records) if return_records else summarize(records)
