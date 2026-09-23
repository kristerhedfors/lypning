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
  sft         `verified_stages.train_sft`, the real per-step path, for 2
              warm-up steps and then 20 timed steps at batch 4 on synthetic
              rows whose assistant turn is ~1,024 tokens (a target row is a
              model draw, and a draw is at most max_new_tokens long): seconds
              per step and peak CUDA memory.

The prompts are the in-tree starter tasks (`pipeline.curriculum`), which are
public fixture text; no bank is downloaded, no verifier Space is needed, and
nothing case-level is printed or written: `hwsmoke.json` holds aggregates and
the projection (`projection.py`) of the approved arm-A job over them.
"""
from __future__ import annotations

import argparse
import json
import os
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

SCHEMA = 1
#: One pilot-shaped call: 16 prompts x 16 draws = the dev/eval-2 chunk at 256.
GENERATION_DRAWS = 16
GENERATION_BATCHES = (256, 128)
SFT_WARMUP_STEPS, SFT_TIMED_STEPS, SFT_BATCH = 2, 20, 4


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


def measure_generation(model, tok, torch, sequences, max_new_tokens, seed, scratch):
    """One `generate` call of `sequences` through `evaluate`; aggregates only."""
    from verified_evaluation import evaluate

    cases = prompts(sequences // GENERATION_DRAWS)
    row = {"sequences": sequences, "prompts": len(cases), "draws": GENERATION_DRAWS,
           "max_new_tokens": max_new_tokens}
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    started = time.monotonic()
    try:
        _, records = evaluate(model, tok, cases, NoVerifier(), decoding(max_new_tokens),
                              Path(scratch) / ("generation-%d.jsonl" % sequences), 0, torch,
                              seed=seed, draws=GENERATION_DRAWS, return_records=True,
                              sequences_per_call=sequences, score_workers=1)
    except torch.cuda.OutOfMemoryError:
        row.update(error="out-of-memory", peak_memory_bytes=torch.cuda.max_memory_allocated())
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


def measure_sft(model, tok, torch, core, seed, row_tokens, max_seq, scratch):
    """`train_sft` itself: warm-up steps, then timed steps, on max-length-ish rows."""
    from verified_stages import supervised_tokens, train_sft

    program = synthetic_program(tok, row_tokens)
    rows = [{"case_id": c["case_id"], "messages": messages(c) + [
        {"role": "assistant", "content": assistant_turn(program)}]} for c in prompts(SFT_BATCH)]
    examples, dropped = core.build_examples(tok, rows, max_seq)
    if dropped or len(examples) != SFT_BATCH:
        raise SystemExit("hwsmoke: synthetic SFT rows exceed --max-seq %d" % max_seq)
    out = {"batch_size": SFT_BATCH, "warmup_steps": SFT_WARMUP_STEPS, "steps": SFT_TIMED_STEPS,
           "row_tokens": max(len(e["input_ids"]) for e in examples),
           "supervised_tokens_per_step": supervised_tokens([examples])}

    def steps(count, name):
        args = SimpleNamespace(batch_size=SFT_BATCH, seed=seed, warmup_ratio=0.1,
                               output=Path(scratch) / name)
        args.output.mkdir(parents=True, exist_ok=True)
        effective = {"steps": count, "eval_every": count, "learning_rate": 1e-4}
        train_sft(model, tok, args, [], [], core, torch, effective, lambda step: None,
                  batches=[list(examples) for _ in range(count)])

    try:
        steps(SFT_WARMUP_STEPS, "sft-warmup")
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        started = time.monotonic()
        steps(SFT_TIMED_STEPS, "sft-timed")
        torch.cuda.synchronize()
    except torch.cuda.OutOfMemoryError:
        out.update(error="out-of-memory", peak_memory_bytes=torch.cuda.max_memory_allocated())
        torch.cuda.empty_cache()
        return out
    seconds = time.monotonic() - started
    out.update(seconds=round(seconds, 2), seconds_per_step=round(seconds / SFT_TIMED_STEPS, 3),
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
              "status": "started"}
    path = args.output / "hwsmoke.json"

    def save(**update):
        report.update(update)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    save()
    # `train_verified.run`'s loading path, in its order, with nothing forked.
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
    started = time.monotonic()
    snapshot = tv.download_base(args.revision)
    download = time.monotonic() - started
    started = time.monotonic()
    model = tv.load_base_model(snapshot, torch.bfloat16)
    torch.cuda.synchronize()
    load = time.monotonic() - started
    model = tv.attach_fresh_lora(model, args.rank, args.seed, core)
    adapted, trainable = core.check_adapted_modules(model)
    binding = tv.refuse_bound_kernels(model)
    model.config.pad_token_id = tok.pad_token_id
    model.generation_config.pad_token_id = tok.pad_token_id
    save(versions=runtime_versions(), kernels=kernels, kernel_binding=binding,
         gpu=torch.cuda.get_device_name(), gpu_memory_total_bytes=torch.cuda.get_device_properties(0).total_memory,
         download_seconds=round(download, 1), load_seconds=round(load, 1),
         lora={"rank": args.rank, "adapted_modules": adapted, "trainable_params": trainable},
         weights_memory_bytes=torch.cuda.memory_allocated(), status="measuring")
    with tempfile.TemporaryDirectory(prefix="hwsmoke-") as scratch:
        generation = []
        for sequences in GENERATION_BATCHES:
            generation.append(measure_generation(model, tok, torch, sequences, args.max_new_tokens,
                                                 args.seed, scratch))
            save(generation=generation)
            core.log("hwsmoke generation %s" % json.dumps(public_view(generation[-1])))
        sft = measure_sft(model, tok, torch, core, args.seed, args.sft_row_tokens, args.max_seq, scratch)
        save(sft=sft)
        core.log("hwsmoke sft %s" % json.dumps(public_view(sft)))
    failed = [g["sequences"] for g in generation if g.get("error")]
    verdict = {"eval_sequences_256_fits": 256 not in failed, "sft_batch_4_fits": not sft.get("error")}
    try:
        planned = projection.from_hwsmoke(report)
    except ValueError as exc:
        planned = {"error": str(exc)}
    save(verdict=verdict, projection=planned,
         status="complete" if all(verdict.values()) else "failed")
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
