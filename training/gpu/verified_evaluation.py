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

A chunk is scored while the next one generates (`ScoringStage`), so the pool's
time hides behind the GPU's instead of adding to it; `overlapped=False`
(`train_verified --serial-scoring`) is the old generate-then-score loop, kept
for diagnosis. Both write the same bytes.

A draw whose native run disagrees with a clean oracle is an engine bug, and
since 2026-09-24 it is a counted draw, not an abort (`pipeline.mismatch_policy`):
status ``engine-mismatch``, reward 0, neither correct nor native; its witness
goes to the stage's private ``engine-mismatches.jsonl`` in (case, draw) order;
and the evaluation fails with `EngineMismatchBound` once such draws exceed 1%
of its planned draws. The seed-1111 arm-A pilot (HF job
6ab52a686b030d633f68e503) aborted in its base test arm on one such draw.
Every other block still aborts as before.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import threading

from pipeline.jsonio import append_jsonl
from pipeline.mismatch_policy import (check_mismatch_bound, counted_on_gpu, mismatch_score,
                                      witness_row)
from pipeline.training import messages, program_from_completion
from pipeline.training_contract import complete, draw_seed
from pipeline.training_metrics import summarize
from pipeline.training_types import VerificationBlocked

#: Sequences one `generate` call carries; a chunk is this many cases × draws.
SEQUENCES_PER_CALL = 256
#: Concurrent verifier scorings; one pooled sandbox host serves 50.
SCORE_WORKERS = 16


def blocked_witness(verifier, witness_path, step):
    """Score one draw, and preserve the program if verification blocks.

    Returns ``(score, mismatch)``: `mismatch` is the engine-mismatch block a
    counted draw was scored from (`mismatch_policy.counted_on_gpu`), else
    None. Its witness is written at emission, in (case, draw) order, by
    `ScoringStage` -- not here, where threads finish in any order.

    The reward stage has written a witness and re-raised since it was built
    (`pipeline.training.Reward.score_one`); the evaluation arm only re-raised,
    so when the round-02 base arm blocked on a native timeout after a correct
    oracle (2026-09-16, job 6aaa8746) the exception string survived and the
    program that caused it did not. A second occurrence would have been as
    unexplained as the first.

    For every block that is not a counted draw the raise stands: the stage
    still aborts, no score moves and no gate moves. That includes a native
    TIMEOUT after a correct oracle, which ledger row T4 (`ORCHESTRATION.md`,
    closed 2026-09-17) kept a hard abort because scoring it would make the
    endpoint depend on host load; the 2026-09-24 mismatch policy leaves that
    ruling as it found it.
    """

    def score_one(pending):
        case, draw, _tail, _completion, _truncated, program = pending
        try:
            return verifier.score(case, program), None
        except VerificationBlocked as exc:
            if counted_on_gpu(exc):
                # The draw's own outcome: scored, counted, witnessed on emission.
                return mismatch_score(case, exc), exc
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
                    "program": program, "error": str(exc), "tests": case.get("tests"),
                    # The detail the public message no longer carries; this
                    # file is uploaded to the private repository only.
                    "kind": getattr(exc, "kind", None), "witness": getattr(exc, "witness", None)})
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


class ScoringStage:
    """Scores one generated chunk while the next chunk generates.

    Evaluation was generate-then-score, one chunk after the other, so every
    minute the verifier pool spent on a chunk was a minute the GPU sat idle:
    on the h200 smoke's numbers (HF job 6ab4582d6b030d633f68c90e, 2026-09-24)
    the stated scoring constant was 195 of the realistic pilot's 764 minutes
    (`training/hf/projection.py`). Overlapped, chunk i is scored on the pool
    while the caller generates chunk i+1, and the two meet once that
    `generate` returns.

    What is recorded does not change. Every chunk goes through `score_chunk`,
    the same code in both modes, and chunks are scored one at a time in chunk
    order, so rows reach the output in (case, draw) order exactly as before
    and the witness is still written by the draw that blocked. Nothing here
    touches torch: the scoring threads draw no RNG, and decoding and token
    handling stay on the caller's thread, so generation's RNG sequence is the
    serial one.

    Bounded: one chunk is scored while one generates. `submit` refuses while a
    chunk is still in flight, so the caller must `finish` it first, and that
    wait is the backpressure. A scoring failure surfaces from `finish`, after
    the `generate` that overlapped it has returned; the chunk generated
    meanwhile is never scored or written, so the file, the witness and the
    exception are what the serial loop would have left, one call later.
    `close` runs on every path: queued scorings are cancelled and every thread
    is joined, so none outlives `evaluate`. A chunk still in flight when that
    happens (an interrupt mid-`generate`) is written whole or not at all, as
    the serial loop interrupted mid-scoring writes nothing of its chunk: its
    cancelled draws must not read as rows that were never drawn, with no
    witness to say why. (A SIGTERM with no handler ends the process outright,
    threads included.)
    """

    def __init__(self, verifier, witness_path, step, score_workers, *, planned,
                 mismatch_path=None, overlapped=True):
        self.overlapped = bool(overlapped)
        self.step = step
        # The bound is on the evaluation's PLANNED draws, so a count past it is
        # final: no later chunk can bring it back under, and stopping saves the
        # GPU the rest of an arm that has already failed.
        self.planned = int(planned)
        self.mismatch_path = mismatch_path
        self.mismatches = 0
        self._score_one = blocked_witness(verifier, witness_path, step)
        self._pool = ThreadPoolExecutor(max_workers=max(1, int(score_workers)),
                                        thread_name_prefix="eval-score")
        self._stage = (ThreadPoolExecutor(max_workers=1, thread_name_prefix="eval-stage")
                       if self.overlapped else None)
        self._running = None
        # `close` sets `_abandoned` under `_emitting`; a chunk emits under it
        # too, so a chunk is either fully written before teardown or not at all.
        self._emitting = threading.Lock()
        self._abandoned = False

    def score_chunk(self, pending, emit):
        """Score every draw of a chunk, emit the successes in order, re-raise the first block.

        One blocked score must not erase every successful row of the same
        generated chunk. All work is still awaited and the arm still aborts
        with the original exception -- the first in (case, draw) order, not
        the first to finish; successful siblings are durable evidence, not a
        completed arm.

        An engine-mismatch draw is a success here: its row is emitted with the
        rest, its witness appended to `mismatch_path` in the same order, and
        only once the whole chunk is written is the bound checked, so the
        rows, the witness file and the abort are the serial loop's in both modes.
        """
        futures = [self._pool.submit(self._score_one, item) for item in pending]
        failure = None
        scores = []
        for future in futures:
            try:
                scores.append(future.result())
            except Exception as exc:
                scores.append(None)
                if failure is None:
                    failure = exc
        with self._emitting:
            if self._abandoned:
                # Torn down mid-chunk: `failure` may be our own cancellation.
                return
            for item, scored in zip(pending, scores):
                if scored is None:
                    continue
                score, mismatch = scored
                emit(item, score)
                if mismatch is not None:
                    self.mismatches += 1
                    if self.mismatch_path is not None:
                        # PRIVATE: uploaded to the private work repository only.
                        case, draw, _tail, _completion, _truncated, program = item
                        append_jsonl(self.mismatch_path, witness_row(
                            case, program, mismatch, source="evaluation", step=self.step, draw=draw))
        if failure is not None:
            raise failure
        check_mismatch_bound(self.mismatches, self.planned)

    def submit(self, pending, emit):
        """Serial: score the chunk now. Overlapped: start scoring it and return."""
        if self._running is not None:
            raise RuntimeError("a chunk is still being scored; finish it first")
        if not self.overlapped:
            self.score_chunk(pending, emit)
            return
        self._running = self._stage.submit(self.score_chunk, pending, emit)

    def finish(self):
        """Wait for the chunk in flight, if any; its exception is raised here."""
        running, self._running = self._running, None
        if running is not None:
            running.result()

    def close(self):
        """Abandon the chunk in flight, cancel every scoring not yet started, join every thread."""
        with self._emitting:
            self._abandoned = True
        self._pool.shutdown(wait=True, cancel_futures=True)
        if self._stage is not None:
            self._stage.shutdown(wait=True, cancel_futures=True)


def evaluate(model, tokenizer, cases, verifier, policy, output, step, torch,
             *, seed=1111, draws=4, return_records=False, witness_path=None,
             sequences_per_call=SEQUENCES_PER_CALL, score_workers=SCORE_WORKERS,
             min_family_cases=1, overlapped=True, mismatch_path=None):
    """Generate and score every draw of `cases`; `overlapped=False` is the serial loop.

    The two modes write the same rows in the same order and the same witness,
    and raise the same exception (`ScoringStage`); only the wall clock differs.
    `witness_path` keeps the program of a block that aborts the arm;
    `mismatch_path` keeps each counted engine-mismatch draw's (both PRIVATE).
    """
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

    def rows_of(sample_seed):
        # Called for one chunk at a time, in (case, draw) order: on this
        # thread when serial, on the one scoring-stage thread when overlapped.
        def emit(item, score):
            case, draw, tail, completion, truncated, program = item
            row = dict(asdict(score), step=step, case_id=case["case_id"],
                family=case["family"], population=case["population"],
                split_group=case.get("split_group", case["family"]),
                capabilities=case.get("capabilities", []), draw=draw, seed=sample_seed,
                completion=completion, completion_tokens=len(tail), truncated=truncated,
                correct=score.correct, native=score.native)
            records.append(row)
            append_jsonl(output, row)
        return emit

    cases = list(cases)
    stage = ScoringStage(verifier, witness_path, step, score_workers, overlapped=overlapped,
                         planned=len(cases) * int(draws), mismatch_path=mismatch_path)
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
            for chunk in chunked(cases, draws, sequences_per_call):
                try:
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
                except Exception:
                    # The serial loop scored the previous chunk before it began
                    # this one: that chunk's rows land, and its failure, if it
                    # had one, is the exception the caller sees.
                    stage.finish()
                    raise
                # The previous chunk's scoring overlapped this generation; it
                # finishes, and raises, before this chunk's scoring begins.
                stage.finish()
                stage.submit(pending, rows_of(sample_seed))
            stage.finish()
    finally:
        stage.close()
        model.generation_config = old_generation
        model.config.text_config.use_cache = old_cache
        if old_padding is not None:
            tokenizer.padding_side = old_padding
        if checkpointing:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.train(was_training)
    metrics = summarize(records, min_family_cases=min_family_cases)
    return (metrics, records) if return_records else metrics
