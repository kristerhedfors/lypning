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
from pipeline.training_types import VerificationBlocked

#: Sequences one `generate` call carries; a chunk is this many cases × draws.
SEQUENCES_PER_CALL = 64
#: Concurrent verifier scorings; one pooled sandbox host serves 50.
SCORE_WORKERS = 16


def blocked_witness(verifier, witness_path, step):
    """Score one draw, and preserve the program if verification blocks.

    The reward stage has written a witness and re-raised since it was built
    (`pipeline.training.Reward.score_one`); the evaluation arm only re-raised,
    so when the round-02 base arm blocked on a native timeout after a correct
    oracle (2026-09-16, job 6aaa8746) the exception string survived and the
    program that caused it did not. A second occurrence would have been as
    unexplained as the first.

    This changes nothing about what the arm DOES: the raise stands, the stage
    still aborts, no score moves and no gate moves. Whether a native timeout
    after a correct oracle should instead be scored — with what status, and
    whether an arm should abort above some rate — is an open decision on
    `ORCHESTRATION.md`'s ledger (row T4) and is deliberately not taken here.
    Both rulings need the program, which is why the witness comes first.
    """

    def score_one(pending):
        case, draw, _tail, _completion, _truncated, program = pending
        try:
            return verifier.score(case, program)
        except VerificationBlocked as exc:
            # Every field is read with `.get`. A KeyError raised in here would
            # REPLACE the abort it is trying to document — the one exception
            # whose message is the whole point — with a KeyError naming a
            # bookkeeping field. `Verifier.score` can block before it ever
            # reads `tests` (a runner failure, a harness error, identity
            # drift), so a case without one is reachable on exactly the paths
            # this witness exists for.
            if witness_path is not None:
                append_jsonl(witness_path, {
                    "step": step, "case_id": case.get("case_id"), "draw": draw,
                    "family": case.get("family"), "population": case.get("population"),
                    "split_group": case.get("split_group") or case.get("family"),
                    "program": program, "error": str(exc), "tests": case.get("tests")})
            raise

    return score_one


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
             *, seed=1111, draws=4, return_records=False, witness_path=None,
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
                # Do not let one blocked score erase every successful row in
                # the same generated chunk. All work is still awaited and the
                # arm still aborts with the original exception; successful
                # siblings are durable evidence, not a completed arm.
                failure = None
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(blocked_witness(verifier, witness_path, step), item)
                               for item in pending]
                    scores = []
                    for future in futures:
                        try:
                            scores.append(future.result())
                        except Exception as exc:
                            scores.append(None)
                            if failure is None:
                                failure = exc
                for (case, draw, tail, completion, truncated, program), score in zip(pending, scores):
                    if score is None:
                        continue
                    row = dict(asdict(score), step=step, case_id=case["case_id"],
                        family=case["family"], population=case["population"],
                        split_group=case.get("split_group", case["family"]),
                        capabilities=case.get("capabilities", []), draw=draw, seed=sample_seed,
                        completion=completion, completion_tokens=len(tail), truncated=truncated,
                        correct=score.correct, native=score.native)
                    records.append(row)
                    append_jsonl(output, row)
                if failure is not None:
                    raise failure
    finally:
        model.generation_config = old_generation
        model.config.text_config.use_cache = old_cache
        if old_padding is not None:
            tokenizer.padding_side = old_padding
        if checkpointing:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.train(was_training)
    return (summarize(records), records) if return_records else summarize(records)
