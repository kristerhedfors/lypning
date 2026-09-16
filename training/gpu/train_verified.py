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
See training/TRAINING.md for staged acceptance gates and held-out evaluation.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.jsonio import append_jsonl, sha256_of, write_json
from pipeline.training_metrics import CheckpointGate
from pipeline.training import ISOLATED_KINDS, TrainingError, Verifier, execution_runner, load_bundle, messages

from pipeline.training_contract import (BASE_MODEL, CONTRACT_VERSION, adapter_identity,
    decoding, model_config_identity, probe_contract, probe_report, runtime_versions,
    seal_adapter, source_identity, validate_probe)
from verified_evaluation import evaluate
from verified_stages import balanced_cases, train_sft, train_grpo


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=("sft", "probe", "grpo", "eval"))
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
    p.add_argument("--batch-size", type=int, default=4, help="SFT effective batch only")
    p.add_argument("--generations", type=int, default=4, help="GRPO/probe draws per train prompt")
    p.add_argument("--probe", type=Path, help="admitted probe.json from the exact RL starting policy")
    p.add_argument("--eval-draws", type=int, default=4, help="matched-seed first-draft evaluation draws")
    p.add_argument("--eval-sequences", type=int, default=64,
                   help="sequences per generate call in evaluation: cases per chunk = this // draws")
    p.add_argument("--score-workers", type=int, default=16, help="concurrent verifier scorings per chunk")
    p.add_argument("--greedy", action="store_true", help="eval-only diagnostic; not checkpoint selection")
    p.add_argument("--warmup-ratio", type=float, default=0.1)
    p.add_argument("--max-no-signal", type=int, default=20, help="abort RL after this many uninformative groups")
    p.add_argument("--max-seq", type=int, default=4096)
    p.add_argument("--max-new-tokens", type=int, default=1024)
    p.add_argument("--seed", type=int, default=1111)
    p.add_argument("--eval-split", choices=("dev", "test", "all"), default="dev",
                   help="all: every case of a benchmark bundle, stage eval only")
    return p


def evaluation_cases(bundle, eval_split):
    """The cases stage eval measures: one split, or a benchmark bundle whole."""
    if eval_split == "all":
        return list(bundle["cases"])
    return [c for c in bundle["cases"] if c["split"] == eval_split]


def adapter_lineage_admitted(experiment, bundle, stage):
    """An adapter belongs to the bundle it was trained on, with one exception.

    A benchmark bundle is never trained on, so the only adapter it can ever
    meet was trained elsewhere: stage eval on a benchmark accepts an adapter
    whose own experiment was a pilot, and the eval record keeps that
    adapter's training bundle digest (`adapter_info["experiment"]`). Every
    other stage, and every other bundle, keeps the exact-lineage rule.
    """
    if experiment.get("bundle_digest") == bundle.get("digest"):
        return True
    return (stage == "eval" and bundle.get("purpose") == "benchmark"
            and experiment.get("purpose") == "pilot")


def preflight(args):
    if int(os.environ.get("WORLD_SIZE", "1")) != 1:
        raise TrainingError("this runner supports one process/GPU; do not launch with torchrun")
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise TrainingError("--revision must be an immutable model commit, not main")
    if args.output.exists():
        raise TrainingError("--output already exists")
    if min(args.steps, args.eval_every, args.patience, args.rank, args.batch_size,
           args.max_seq, args.max_new_tokens, args.generations, args.eval_draws, args.max_no_signal,
           args.eval_sequences, args.score_workers) <= 0 or (args.lr is not None and (not math.isfinite(args.lr) or args.lr <= 0)):
        raise TrainingError("training lengths, rank, learning rate and batches must be positive")
    if args.stage in ("grpo", "probe") and args.generations < 2:
        raise TrainingError("GRPO needs at least two generations per prompt")
    if args.stage == "grpo" and bool(args.adapter) == bool(args.from_base):
        raise TrainingError("GRPO needs --adapter or explicit --from-base, exclusively")
    if args.stage == "grpo" and args.batch_size != 4:
        raise TrainingError("GRPO uses --generations; --batch-size is SFT-only")
    if args.stage == "sft" and (args.adapter or args.from_base):
        raise TrainingError("SFT starts from the pinned base; adapters belong to GRPO/eval")
    if args.stage != "eval" and args.eval_split != "dev":
        raise TrainingError("test split cannot select a checkpoint")
    if not math.isfinite(args.warmup_ratio) or not 0 <= args.warmup_ratio < 1:
        raise TrainingError("--warmup-ratio must be in [0, 1)")
    if args.greedy and args.stage != "eval":
        raise TrainingError("--greedy is only an evaluation diagnostic")
    if args.from_base and args.stage != "grpo":
        raise TrainingError("--from-base is only a GRPO ablation")
    bundle = load_bundle(args.bundle, args.engine)
    if not args.smoke and bundle.get("purpose") not in ("pilot", "benchmark"):
        raise TrainingError("smoke data cannot launch a real run; prepare an admitted pilot bundle")
    if bundle.get("purpose") == "benchmark" and args.stage != "eval":
        raise TrainingError("a benchmark bundle is evaluated whole, never trained on; only stage eval accepts it")
    if args.eval_split == "all" and bundle.get("purpose") != "benchmark":
        raise TrainingError("--eval-split all evaluates a benchmark bundle whole; a pilot is measured per split")
    if not args.smoke and sys.platform != "linux" and not args.plan:
        raise TrainingError("real runs require the isolated Linux worker, not macOS diagnostic limits")
    if not args.plan and not args.isolated_worker:
        raise TrainingError("generated-code execution requires --isolated-worker; see TRAINING.md")
    if not args.plan and bundle.get("execution", {}).get("kind") not in ISOLATED_KINDS:
        raise TrainingError("generation (including smoke) requires an isolated execution bundle; re-prepare with --execution-image")
    if not args.smoke and bundle["limits"]["memory_mb"] == 0 and not args.plan:
        raise TrainingError("memory cap disabled: only --smoke may use this bundle")
    adapter = adapter_identity(args.adapter, args.revision) if args.adapter else None
    if adapter and not adapter_lineage_admitted(adapter["experiment"], bundle, args.stage):
        raise TrainingError("adapter trained with a different experiment/split")
    if adapter and bool(adapter["experiment"].get("smoke")) != args.smoke:
        raise TrainingError("adapter and model must both be smoke or both be real")
    if adapter and adapter["experiment"]["args"]["rank"] != args.rank and args.stage == "grpo":
        raise TrainingError("GRPO continues the existing adapter rank; --rank does not resize it")
    if args.stage == "grpo" and not args.smoke:
        if not args.probe:
            raise TrainingError("GRPO needs an admitted --probe from the exact starting policy")
        contract = probe_contract(bundle, args.revision, adapter,
            decoding(schedule(args)["max_tokens"]), args.seed, args.generations, args.smoke)
        validate_probe(args.probe, contract, [c["case_id"] for c in bundle["cases"] if c["split"] == "train"])
    return bundle, adapter


def schedule(args):
    """One source of effective values for execution, dry plans and manifests."""
    return {"steps": 2 if args.smoke else args.steps,
            "eval_every": 1 if args.smoke else args.eval_every,
            "max_tokens": min(32, args.max_new_tokens) if args.smoke else args.max_new_tokens,
            "learning_rate": args.lr or (2e-5 if args.stage == "sft" else 1e-6)}


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


def run(args, bundle, adapter_info):
    verifier = Verifier(args.engine, **bundle["limits"], identity=bundle["identity"],
                        runner=execution_runner(bundle["execution"], bundle["identity"]))
    versions = runtime_versions()
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
    if not args.smoke and not torch.cuda.is_bf16_supported():
        raise TrainingError("this experiment requires native BF16 support")
    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    tok = AutoTokenizer.from_pretrained(BASE_MODEL, revision=args.revision)
    tok.padding_side = "left"
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    if tok.eos_token_id is None or tok.encode("<|im_end|>", add_special_tokens=False) != [tok.eos_token_id]:
        raise TrainingError("Qwen assistant terminator must equal tokenizer EOS for SFT/TRL agreement")
    train_cases = [c for c in bundle["cases"] if c["split"] == "train"]
    dev_cases = evaluation_cases(bundle, args.eval_split)
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
    if args.stage in ("sft", "grpo"):
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
    elif args.stage in ("sft", "grpo"):
        model = core.attach_lora(model, args.rank, 2 * args.rank, 0.0)
    if args.stage in ("sft", "grpo"):
        core.check_adapted_modules(model)
    model.config.pad_token_id = tok.pad_token_id
    model.generation_config.pad_token_id = tok.pad_token_id
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {"base_model": BASE_MODEL, "revision": args.revision, "stage": args.stage,
                "bundle_digest": bundle["digest"], "adapter": adapter_info,
                "smoke": args.smoke, "seed": args.seed,
                "effective": schedule(args),
                "contract_version": CONTRACT_VERSION,
                "enable_thinking": False, "presence_penalty": 0.0,
                "eos_token_id": tok.eos_token_id,
                "decoding": decoding(schedule(args)["max_tokens"], greedy=args.greedy),
                "tokenizer_sha256": sha256_of({"vocab": tok.get_vocab(), "template": tok.chat_template,
                    "special_tokens": tok.special_tokens_map}),
                "model_config_sha256": model_config_identity(model.config.to_dict()),
                "hardware": {"device": device, "dtype": str(dtype), "cuda": torch.version.cuda,
                    "gpu": torch.cuda.get_device_name() if device == "cuda" else None},
                "code_sha256": source_identity(Path(__file__).resolve().parents[1]),
                "args": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                "versions": versions}
    if adapter_info:
        prior = adapter_info["experiment"]
        for key in ("tokenizer_sha256", "model_config_sha256", "enable_thinking"):
            if prior.get(key) != manifest[key]:
                raise TrainingError("adapter runtime contract changed: " + key)
    if args.stage == "grpo" and not args.smoke:
        probe_manifest = json.loads(args.probe.with_name("experiment.json").read_text())
        for key in ("tokenizer_sha256", "model_config_sha256", "versions", "eos_token_id"):
            if probe_manifest.get(key) != manifest[key]:
                raise TrainingError("probe runtime contract changed: " + key)
    write_json(args.output / "experiment.json", manifest)
    effective = schedule(args)
    max_tokens = effective["max_tokens"]
    policy = decoding(max_tokens, greedy=args.greedy)
    if args.stage == "probe":
        metrics, records = evaluate(model, tok, train_cases, verifier, policy,
            args.output / "probe-rollouts.jsonl", 0, torch,
            seed=args.seed, draws=args.generations, return_records=True,
            sequences_per_call=args.eval_sequences, score_workers=args.score_workers)
        contract = probe_contract(bundle, args.revision, adapter_info, policy,
                                  args.seed, args.generations, args.smoke)
        write_json(args.output / "probe.json", probe_report(records, contract))
        write_json(args.output / "metrics.json", metrics)
        return
    def measure(step):
        return evaluate(model, tok, dev_cases, verifier, policy,
                        args.output / "evaluations.jsonl", step, torch,
                        seed=args.seed, draws=1 if args.greedy else args.eval_draws,
                        sequences_per_call=args.eval_sequences, score_workers=args.score_workers)
    baseline = measure(0)
    if args.stage == "eval":
        write_json(args.output / "metrics.json", baseline)
        return

    # Save every candidate separately; 'best.json' selects one without deleting
    # evidence. Baseline checkpoint 0 remains available if training regresses.
    def save(step):
        path = args.output / ("adapter-%d" % step)
        core.save_adapter(model, str(path))
        write_json(path / "experiment.json", dict(manifest, checkpoint_step=step))
        write_json(path / "seal.json", seal_adapter(path))
    save(0)
    gate = CheckpointGate(baseline, args.patience)
    write_json(args.output / "best.json", gate.report())
    def checkpoint(step):
        metrics = measure(step)
        save(step)
        stop = gate.observe(step, metrics)
        write_json(args.output / "best.json", gate.report())
        return stop

    if args.stage == "sft":
        train_sft(model, tok, args, train_cases, examples, core, torch, effective, checkpoint)
    else:
        train_grpo(model, tok, args, bundle, train_cases, verifier, effective, policy, checkpoint)
    write_json(args.output / "best.json", gate.report())


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        bundle, adapter = preflight(args)
        if args.plan:
            print(json.dumps({"stage": args.stage, "model": BASE_MODEL, "revision": args.revision,
                              "purpose": bundle["purpose"], "effective": schedule(args),
                              "decoding": decoding(schedule(args)["max_tokens"], greedy=args.greedy),
                              "enable_thinking": False,
                              "limits": bundle["limits"], "memory_policy": bundle["memory_policy"],
                              "adapter": adapter, "training_started": False,
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
