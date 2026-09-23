"""Hardware smoke: measure, on the pilot's GPU, what the pilot's clock is made of.

Runs inside an HF Job (`round02_hwsmoke.sh`), never on a laptop. It loads
Qwen/Qwen3.8-27B at the pinned revision through `train_verified`'s OWN loading
functions -- `block_fused_kernels`, `refuse_import_binding`, `load_tokenizer`,
`download_base`, `load_base_model`, `attach_fresh_lora`, `refuse_bound_kernels`,
in `run`'s order -- so the model measured is the model every stage trains and
evaluates, torch-reference gated-delta kernel enforced, LoRA r16 attached. Then:

  generation  ONE `generate` call of 256 sequences (16 prompts x 16 draws, the
              dev-selection and eval-2 shape at --eval-sequences 256) and one
              of 128, through `verified_evaluation.evaluate` itself -- the
              pilot's chunking, left padding, decoding (max_new_tokens 1024,
              thinking off) and seeding -- with a verifier that scores nothing:
              wall clock, generated tokens, sequences/min, peak CUDA memory.
  generation_full_length
              the 256 call again with `min_new_tokens` = max_new_tokens, so
              every sequence decodes all 1,024 tokens. A call lasts as long as
              its LONGEST completion, and the starter tasks are short enough
              that the pilot-decoding call may never show what a bank chunk
              with one truncated draw costs; this is that call's wall clock,
              and its peak memory is the full-length KV cache of 256
              sequences -- the out-of-memory question the pilot actually asks.
  sft         `verified_stages.train_sft`, the real per-step path, for 2
              warm-up steps and then 20 timed steps at batch 4 on synthetic
              rows whose assistant turn is ~1,024 tokens (a target row is a
              model draw, and a draw is at most max_new_tokens long): seconds
              per step and peak CUDA memory.
  sft_typical the same at ~256-token assistant turns, 10 timed steps: a target
              row is a graded draw, and seed 1111's draws averaged ~155
              tokens, so this is the lower reading of the SFT clock.

Every generation call runs BEFORE any optimizer step, so the pilot-decoding
completion lengths are the untrained adapter's, as in the pilot's base arm.
`hwsmoke.json` is rewritten atomically before and after every measurement,
naming the one in progress, and a measurement that fails -- out of memory or
anything else -- is recorded and the next one still runs; SIGTERM from the
in-container `timeout` is recorded as `terminated` before the job's upload trap.

The prompts are the in-tree starter tasks (`pipeline.curriculum`), which are
public fixture text; no bank is downloaded, no verifier Space is needed, and
nothing case-level is printed or written: `hwsmoke.json` holds aggregates and
the projection (`projection.py`) of the approved arm-A job over them.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for extra in (ROOT / "training", ROOT / "training" / "gpu", HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import projection  # noqa: E402
import train_verified as tv  # noqa: E402
from pipeline.curriculum import starter_cases  # noqa: E402
from pipeline.public_view import public_view  # noqa: E402
from pipeline.training import assistant_turn, messages  # noqa: E402
from pipeline.training_contract import BASE_MODEL, decoding, runtime_versions  # noqa: E402
from pipeline.training_types import Score  # noqa: E402

SCHEMA = 2
#: One pilot-shaped call: 16 prompts x 16 draws = the dev/eval-2 chunk at 256.
GENERATION_DRAWS = 16
GENERATION_BATCHES = (256, 128)
#: The forced full-length call: the pilot's largest batch.
FULL_LENGTH_BATCH = 256
SFT_WARMUP_STEPS, SFT_TIMED_STEPS, SFT_BATCH = 2, 20, 4
#: The lower SFT reading: assistant-turn tokens and timed steps.
SFT_TYPICAL_ROW_TOKENS, SFT_TYPICAL_STEPS = 256, 10


class Terminated(BaseException):
    """SIGTERM from the in-container `timeout`. A BaseException, so no
    measurement's error capture can swallow it."""


def _terminate(signum, frame):
    raise Terminated()


def error_of(exc, torch):
    """A measurement's failure as one short line; no case text exists here."""
    text = str(exc)
    if isinstance(exc, torch.cuda.OutOfMemoryError) or "out of memory" in text.lower():
        return "out-of-memory"
    return "%s: %s" % (type(exc).__name__, (text.splitlines() or [""])[0][:200])


def write_atomically(path, report):
    """A SIGTERM or KILL mid-write must not leave half a JSON for the upload trap."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class NoVerifier:
    """Scores nothing. The smoke measures generation; there is no pool here."""

    def score(self, case, program):
        return Score(0.0, "unscored")


def prompts(count):
    """`count` starter tasks, cycled with fresh case ids if there are fewer."""
    cases = starter_cases()
    return [dict(cases[i % len(cases)], case_id="%s-%d" % (cases[i % len(cases)]["case_id"], i))
            for i in range(count)]


def synthetic_program(tok, target_tokens):
    """A plain Python program whose fenced assistant turn is at least `target_tokens` long."""
    lines = []
    while True:
        lines.extend("value_%04d = sum(range(%d))  # accumulate a running total\n" % (len(lines) + k, k)
                     for k in range(16))
        program = "".join(lines) + "print(value_0000)\n"
        if len(tok(assistant_turn(program), add_special_tokens=False)["input_ids"]) >= target_tokens:
            return program


def measure_generation(model, tok, torch, sequences, max_new_tokens, seed, scratch, full_length=False):
    """One `generate` call of `sequences` through `evaluate`; aggregates only.

    `full_length` adds `min_new_tokens` = `max_new_tokens` to the pilot's
    decoding, so every sequence decodes to the cap: the worst-case call.
    """
    from verified_evaluation import evaluate

    cases = prompts(sequences // GENERATION_DRAWS)
    row = {"sequences": sequences, "prompts": len(cases), "draws": GENERATION_DRAWS,
           "max_new_tokens": max_new_tokens, "full_length": bool(full_length)}
    policy = decoding(max_new_tokens)
    if full_length:
        policy = dict(policy, min_new_tokens=max_new_tokens)
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    started = time.monotonic()
    try:
        _, records = evaluate(model, tok, cases, NoVerifier(), policy,
                              Path(scratch) / ("generation-%d-%d.jsonl" % (sequences, bool(full_length))),
                              0, torch, seed=seed, draws=GENERATION_DRAWS, return_records=True,
                              sequences_per_call=sequences, score_workers=1)
    except Exception as exc:  # noqa: BLE001 -- recorded, and the next measurement still runs
        row.update(error=error_of(exc, torch), peak_memory_bytes=torch.cuda.max_memory_allocated())
        torch.cuda.empty_cache()
        return row
    torch.cuda.synchronize()
    seconds = time.monotonic() - started
    tokens = [r["completion_tokens"] for r in records]
    row.update(seconds=round(seconds, 2), generated_tokens=sum(tokens),
               max_completion_tokens=max(tokens), mean_completion_tokens=round(sum(tokens) / len(tokens), 1),
               truncated=sum(1 for r in records if r["truncated"]),
               sequences_per_minute=round(sequences * 60.0 / seconds, 2),
               generated_tokens_per_second=round(sum(tokens) / seconds, 1),
               peak_memory_bytes=torch.cuda.max_memory_allocated())
    return row


def measure_sft(model, tok, torch, core, seed, row_tokens, max_seq, scratch, timed_steps=SFT_TIMED_STEPS,
                name="sft"):
    """`train_sft` itself: warm-up steps, then timed steps, on synthetic rows of `row_tokens`."""
    from verified_stages import supervised_tokens, train_sft

    out = {"batch_size": SFT_BATCH, "warmup_steps": SFT_WARMUP_STEPS, "steps": timed_steps,
           "assistant_tokens": row_tokens}
    try:
        program = synthetic_program(tok, row_tokens)
        rows = [{"case_id": c["case_id"], "messages": messages(c) + [
            {"role": "assistant", "content": assistant_turn(program)}]} for c in prompts(SFT_BATCH)]
        examples, dropped = core.build_examples(tok, rows, max_seq)
    except Exception as exc:  # noqa: BLE001
        out.update(error=error_of(exc, torch))
        return out
    if dropped or len(examples) != SFT_BATCH:
        out.update(error="synthetic SFT rows exceed --max-seq %d" % max_seq)
        return out
    out.update(row_tokens=max(len(e["input_ids"]) for e in examples),
               supervised_tokens_per_step=supervised_tokens([examples]))

    def steps(count, name):
        args = SimpleNamespace(batch_size=SFT_BATCH, seed=seed, warmup_ratio=0.1,
                               output=Path(scratch) / name)
        args.output.mkdir(parents=True, exist_ok=True)
        effective = {"steps": count, "eval_every": count, "learning_rate": 1e-4}
        train_sft(model, tok, args, [], [], core, torch, effective, lambda step: None,
                  batches=[list(examples) for _ in range(count)])

    try:
        steps(SFT_WARMUP_STEPS, name + "-warmup")
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        started = time.monotonic()
        steps(timed_steps, name + "-timed")
        torch.cuda.synchronize()
    except Exception as exc:  # noqa: BLE001 -- recorded, and the next measurement still runs
        out.update(error=error_of(exc, torch), peak_memory_bytes=torch.cuda.max_memory_allocated())
        torch.cuda.empty_cache()
        return out
    seconds = time.monotonic() - started
    out.update(seconds=round(seconds, 2), seconds_per_step=round(seconds / timed_steps, 3),
               peak_memory_bytes=torch.cuda.max_memory_allocated())
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--revision", required=True, help="the pinned 40-character Qwen commit")
    p.add_argument("--output", type=Path, required=True, help="new directory for hwsmoke.json")
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--seed", type=int, default=1111)
    p.add_argument("--max-new-tokens", type=int, default=1024, help="the pilot's (train_verified default)")
    p.add_argument("--max-seq", type=int, default=4096, help="the pilot's (train_verified default)")
    p.add_argument("--sft-row-tokens", type=int, default=1024,
                   help="assistant-turn tokens per synthetic SFT row (a draw is at most max_new_tokens)")
    args = p.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"schema": SCHEMA, "job": os.environ.get("JOB_ID", "local"),
              "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                       text=True).stdout.strip() or None,
              "flavor": os.environ.get("ACCELERATOR", ""), "base_model": BASE_MODEL,
              "qwen_revision": args.revision, "rank": args.rank, "seed": args.seed,
              "enable_thinking": False, "decoding": decoding(args.max_new_tokens),
              "status": "started", "in_progress": "kernels"}
    path = args.output / "hwsmoke.json"

    def save(**update):
        report.update(update)
        write_atomically(path, report)

    save()
    previous = signal.signal(signal.SIGTERM, _terminate)
    try:
        return measure(args, report, save)
    except Terminated:
        save(status="terminated", error="SIGTERM (the job's timeout) during %s" % report.get("in_progress"))
        return 124
    except BaseException as exc:
        # The loaders refuse with TrainingError/SystemExit; say which, and where.
        save(status="failed", error="%s: %s" % (type(exc).__name__, (str(exc).splitlines() or [""])[0][:300]))
        raise
    finally:
        signal.signal(signal.SIGTERM, previous)


def measure(args, report, save):
    """Load as `train_verified.run` loads, then every measurement, saving between them."""
    # `train_verified.run`'s loading path, in its order, with nothing forked.
    # The pinned-version check first, as `run` does: it refuses, and refusing
    # after the 55 GB pull is paying for the answer twice.
    versions = runtime_versions()
    kernels = tv.block_fused_kernels()
    import lypning_lora as core
    import torch
    from transformers import set_seed

    tv.refuse_import_binding()
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        save(status="failed", error="CUDA with native BF16 is required")
        return 1
    set_seed(args.seed)
    tok = tv.load_tokenizer(args.revision)
    save(in_progress="download")
    started = time.monotonic()
    snapshot = tv.download_base(args.revision)
    download = time.monotonic() - started
    save(in_progress="load", download_seconds=round(download, 1))
    started = time.monotonic()
    model = tv.load_base_model(snapshot, torch.bfloat16)
    torch.cuda.synchronize()
    load = time.monotonic() - started
    model = tv.attach_fresh_lora(model, args.rank, args.seed, core)
    adapted, trainable = core.check_adapted_modules(model)
    binding = tv.refuse_bound_kernels(model)
    model.config.pad_token_id = tok.pad_token_id
    model.generation_config.pad_token_id = tok.pad_token_id
    save(versions=versions, kernels=kernels, kernel_binding=binding,
         gpu=torch.cuda.get_device_name(), gpu_memory_total_bytes=torch.cuda.get_device_properties(0).total_memory,
         load_seconds=round(load, 1),
         lora={"rank": args.rank, "adapted_modules": adapted, "trainable_params": trainable},
         weights_memory_bytes=torch.cuda.memory_allocated(), status="measuring")
    with tempfile.TemporaryDirectory(prefix="hwsmoke-") as scratch:
        # Every generation call precedes every optimizer step: the pilot-decoding
        # lengths are the fresh adapter's, not those of one trained on synthetic rows.
        generation = []
        for sequences in GENERATION_BATCHES:
            save(in_progress="generation-%d" % sequences)
            generation.append(measure_generation(model, tok, torch, sequences, args.max_new_tokens,
                                                 args.seed, scratch))
            save(generation=generation)
            core.log("hwsmoke generation %s" % json.dumps(public_view(generation[-1])))
        save(in_progress="generation-full-length-%d" % FULL_LENGTH_BATCH)
        full = measure_generation(model, tok, torch, FULL_LENGTH_BATCH, args.max_new_tokens,
                                  args.seed, scratch, full_length=True)
        save(generation_full_length=full)
        core.log("hwsmoke generation_full_length %s" % json.dumps(public_view(full)))
        save(in_progress="sft")
        sft = measure_sft(model, tok, torch, core, args.seed, args.sft_row_tokens, args.max_seq, scratch)
        save(sft=sft)
        core.log("hwsmoke sft %s" % json.dumps(public_view(sft)))
        save(in_progress="sft-typical")
        typical = measure_sft(model, tok, torch, core, args.seed, SFT_TYPICAL_ROW_TOKENS, args.max_seq,
                              scratch, timed_steps=SFT_TYPICAL_STEPS, name="sft-typical")
        save(sft_typical=typical)
        core.log("hwsmoke sft_typical %s" % json.dumps(public_view(typical)))
    report.pop("in_progress", None)
    failed = [g["sequences"] for g in generation + [full] if g.get("error")]
    verdict = {"eval_sequences_256_fits": 256 not in failed, "sft_batch_4_fits": not sft.get("error")}
    projections = {}
    for reading in projection.READINGS:
        try:
            projections[reading] = projection.from_hwsmoke(report, reading=reading)
        except Exception as exc:  # noqa: BLE001 -- a missing measurement, already recorded
            projections[reading] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    named = [("generation-%d" % g["sequences"], g) for g in generation]
    named += [("generation_full_length", full), ("sft", sft), ("sft_typical", typical)]
    errors = [name for name, row in named if row.get("error")]
    save(verdict=verdict, errors=errors, projection=projections.pop("measured"),
         projection_bounds=projections,
         status="complete" if all(verdict.values()) and not errors else "failed")
    return 0 if report["status"] == "complete" else 1

if __name__ == "__main__":
    sys.exit(main())
