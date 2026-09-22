# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "torch==2.9.1", "transformers==5.17.0", "peft==0.20.0",
#   "accelerate==1.15.0", "huggingface-hub==1.31.0", "safetensors==0.8.0",
#   "trl==1.13.0", "datasets==4.7.0",
#   "flash-linear-attention==0.5.2",
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
from pipeline.training_metrics import BENCHMARK_MIN_FAMILY_CASES, CheckpointGate
from pipeline.evaluation_reuse import fresh_lora_is_noop, reuse_evaluation
from pipeline.training import (ISOLATED_KINDS, TrainingError, Verifier,
    chat_prompt_token_ids, execution_runner, load_bundle, messages)

from pipeline.training_contract import (BASE_MODEL, CONTRACT_VERSION, MIN_SUPERVISED_TOKENS,
    MIN_TRAIN_CASES, PROTOCOL_EVAL_DRAWS, PROTOCOL_TRAIN_SEEDS,
    adapter_files, adapter_identity, decoding, model_config_identity, probe_contract, probe_report,
    kernel_state, runtime_versions, seal_adapter, source_identity, validate_probe)
from verified_evaluation import evaluate
from verified_stages import balanced_cases, sft_batches, supervised_tokens, train_sft, train_grpo


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=("sft", "probe", "grpo", "eval"))
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--engine", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True, help="new directory, never overwrite")
    p.add_argument("--revision", required=True, help="immutable 40-character base-model Hub commit")
    p.add_argument("--adapter", type=Path, help="local adapter for GRPO warm start or evaluation")
    p.add_argument("--reuse-evaluation", type=Path, help="eval only: reuse a completed equivalent-policy arm")
    p.add_argument("--from-base", action="store_true", help="explicit GRPO-from-base ablation")
    p.add_argument("--plan", action="store_true", help="validate experiment without GPU/downloads")
    p.add_argument("--smoke", action="store_true", help="tiny random Qwen model, two real trainer steps")
    p.add_argument("--isolated-worker", action="store_true",
                   help="attest this is a disposable worker with no sensitive files/credentials")
    p.add_argument("--steps", type=int, default=100)
    p.add_argument("--eval-every", type=int, default=50)
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--lr", type=float, help="default SFT 1e-4 (2e-4 below 100 steps) / GRPO 5e-6")
    p.add_argument("--batch-size", type=int, default=4, help="SFT effective batch only")
    p.add_argument("--generations", type=int, default=4, help="GRPO/probe draws per train prompt")
    p.add_argument("--probe", type=Path, help="admitted probe.json from the exact RL starting policy")
    p.add_argument("--eval-draws", type=int, default=4, help="matched-seed first-draft evaluation draws")
    p.add_argument("--eval-sequences", type=int, default=256,
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
    if min(args.steps, args.eval_every, args.rank, args.batch_size,
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
    if args.reuse_evaluation is not None and args.stage != "eval":
        raise TrainingError("--reuse-evaluation is only for standalone evaluation")
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
    train_cases = [case for case in bundle.get("cases", []) if case.get("split") == "train"]
    if not args.smoke and args.stage in ("sft", "probe", "grpo"):
        if len(train_cases) < MIN_TRAIN_CASES:
            raise TrainingError("real adapter stages require at least %d train cases; got %d"
                                % (MIN_TRAIN_CASES, len(train_cases)))
        if args.seed not in PROTOCOL_TRAIN_SEEDS:
            raise TrainingError("training seed must be one of the pre-registered seeds: %s"
                                % (", ".join(map(str, PROTOCOL_TRAIN_SEEDS))))
    if (not args.smoke and args.stage == "sft"
            and args.steps * args.batch_size < len({case["family"] for case in train_cases})):
        raise TrainingError("SFT schedule is shorter than one complete family cycle")
    planned = supervised_plan(args, bundle)
    if planned and planned["supervised_token_upper_bound"] < MIN_SUPERVISED_TOKENS:
        raise TrainingError(
            "SFT schedule exposes at most %d supervised tokens -- an upper bound over the "
            "%d scheduled references' UTF-8 bytes -- and the floor is %d, so run() will "
            "certainly refuse this schedule after the tokenizer download. A bound ABOVE "
            "the floor is not a pass: it is only the absence of this certain failure, and "
            "the exact count is still taken in run()."
            % (planned["supervised_token_upper_bound"], planned["planned_exposures"],
               MIN_SUPERVISED_TOKENS))
    # k is pre-registered for the confirmatory arm, and the runner's default is
    # not it. Refusing here costs nothing; the round-02 pilot spent an arm
    # finding this out, and a wider interval than the effect is not a cheaper
    # measurement but a measurement of nothing.
    if (not args.smoke and bundle.get("purpose") == "benchmark" and args.stage == "eval"
            and not args.greedy and args.eval_draws != PROTOCOL_EVAL_DRAWS):
        raise TrainingError(
            "eval-2 is pre-registered at --eval-draws %d (EVAL2.md section 4); %d is a "
            "different instrument, not a cheaper one. Pass --smoke for a wiring check."
            % (PROTOCOL_EVAL_DRAWS, args.eval_draws))
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


def supervised_plan(args, bundle):
    """What the SFT schedule will expose, bounded above, with nothing downloaded.

    The exact floor cannot move here: counting supervised tokens needs
    `build_examples`, hence the Hub tokenizer that `--plan` exists to avoid. A
    one-sided bound can, because none of its three inputs need the model.
    `sft_batches` picks by family and index and never looks inside what it
    carries, so passing the cases in place of their examples yields the very
    schedule `run()` will train on; every case is guaranteed a non-empty
    `reference` at load (`pipeline/training_data.py`); and the supervised
    segment is that reference in a fenced block plus the assistant terminator,
    which under byte-level BPE can never cost more tokens than it has UTF-8
    bytes. The sum is over the SCHEDULE -- every repeat counted again -- not the
    exposure count times the longest reference, which is looser by a factor of
    six on the in-tree proxy corpus (`training/data/corpus.jsonl`, measured
    2026-09-17: 147 of 517 rows carry a reference, assistant-segment bytes mean
    407.8 and max 2,536) and would admit schedules the floor certainly refuses.

    Returns None where no supervised dose is planned or the floor does not
    apply, so the caller cannot mistake "not applicable" for a bound of zero.
    """
    if args.stage != "sft" or args.smoke:
        return None
    train_cases = [case for case in bundle.get("cases", []) if case.get("split") == "train"]
    batches = sft_batches(train_cases, train_cases, schedule(args)["steps"],
                          args.batch_size, args.seed)
    scheduled = [case for batch in batches for case in batch]
    return {"planned_exposures": len(scheduled),
            "supervised_token_upper_bound":
                sum(len(("```python\n" + case["reference"].rstrip() + "\n```<|im_end|>")
                        .encode("utf-8")) for case in scheduled)}


def schedule(args):
    """One source of effective values for execution, dry plans and manifests."""
    steps = 2 if args.smoke else args.steps
    default_lr = (2e-4 if steps < 100 else 1e-4) if args.stage == "sft" else 5e-6
    return {"steps": steps,
            "eval_every": 1 if args.smoke else args.eval_every,
            "max_tokens": min(32, args.max_new_tokens) if args.smoke else args.max_new_tokens,
            "learning_rate": args.lr if args.lr is not None else default_lr}


def metric_policy(bundle):
    return {"min_family_cases": BENCHMARK_MIN_FAMILY_CASES
            if bundle.get("purpose") == "benchmark" else 1}


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


def check_prompt_budget(tok, cases, max_new_tokens, max_seq):
    """Refuse any case whose prompt plus its completion budget exceeds --max-seq.

    Token limits are admission checks, not permission to silently drop long
    examples or slice the task away. Run this before downloading 27B weights.

    It is a module-level function because it has to be testable without a GPU:
    inline in `run()` it was reachable only behind `import torch`, and it spent
    a release counting `len()` of a `BatchEncoding` -- two keys -- against
    `max_seq`, which admitted every prompt of every length. `chat_prompt_token_ids`
    owns the shape; this owns the arithmetic; the tests can now reach both.
    """
    for case in cases:
        prompt_ids = chat_prompt_token_ids(tok, messages(case))
        if len(prompt_ids) + max_new_tokens > max_seq:
            raise TrainingError("prompt + completion budget exceeds --max-seq: " + case["case_id"])


def run(args, bundle, adapter_info):
    verifier = Verifier(args.engine, **bundle["limits"], identity=bundle["identity"],
                        runner=execution_runner(bundle["execution"], bundle["identity"],
                                                stage="grpo"))
    versions = runtime_versions()
    effective = schedule(args)
    # Block fused kernels before importing transformers, preserving the existing
    # exact Qwen class and per-leaf LoRA gradient smoke checks.
    os.environ["NTX_USE_FLA"] = "0"
    # AFTER the switch is set and BEFORE transformers is imported, so what is
    # recorded is the state this run actually had. Pinning the distribution
    # does not settle it: on 2026-09-20 the pin held and transformers still ran
    # all 48 gated-delta-net layers on the reference path.
    kernels = kernel_state()
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
    check_prompt_budget(tok, train_cases + dev_cases, args.max_new_tokens, args.max_seq)
    examples = []
    planned_sft_batches = None
    planned_tokens = None
    if args.stage == "sft":
        rows = [{"case_id": c["case_id"], "messages": messages(c) + [{"role": "assistant",
                 "content": "```python\n" + c["reference"].rstrip() + "\n```"}]} for c in train_cases]
        examples, dropped = core.build_examples(tok, rows, args.max_seq)
        if dropped or not examples:
            raise TrainingError("SFT rows over token limit; do not silently change the curriculum")
        planned_sft_batches = sft_batches(train_cases, examples, effective["steps"],
                                          args.batch_size, args.seed)
        planned_tokens = supervised_tokens(planned_sft_batches)
        if not args.smoke and planned_tokens < MIN_SUPERVISED_TOKENS:
            raise TrainingError("SFT schedule exposes %d supervised tokens; at least %d required"
                                % (planned_tokens, MIN_SUPERVISED_TOKENS))
    if args.stage in ("sft", "grpo"):
        core.smoke(device, dtype, SimpleNamespace(
            revision=args.revision, rank=args.rank, alpha=2 * args.rank,
            lora_dropout=0.0, lr=effective["learning_rate"], temperature=1.0, top_p=0.95, top_k=0))
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
                "planned_supervised_tokens": planned_tokens,
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
                "versions": versions, "kernels": kernels}
    if adapter_info:
        prior = adapter_info["experiment"]
        for key in ("tokenizer_sha256", "model_config_sha256", "enable_thinking"):
            if prior.get(key) != manifest[key]:
                raise TrainingError("adapter runtime contract changed: " + key)
    if args.stage == "grpo" and not args.smoke:
        probe_manifest = json.loads(args.probe.with_name("experiment.json").read_text())
        for key in ("tokenizer_sha256", "model_config_sha256", "versions", "kernels",
                    "eos_token_id"):
            if probe_manifest.get(key) != manifest[key]:
                raise TrainingError("probe runtime contract changed: " + key)
    manifest["metric_policy"] = metric_policy(bundle)
    write_json(args.output / "experiment.json", manifest)
    max_tokens = effective["max_tokens"]
    policy = decoding(max_tokens, greedy=args.greedy)
    if args.stage == "probe":
        metrics, records = evaluate(model, tok, train_cases, verifier, policy,
            args.output / "probe-rollouts.jsonl", 0, torch,
            seed=args.seed, draws=args.generations, return_records=True,
            witness_path=args.output / "eval-blocked-witnesses.jsonl",
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
                        witness_path=args.output / "eval-blocked-witnesses.jsonl",
                        sequences_per_call=args.eval_sequences, score_workers=args.score_workers,
                        **metric_policy(bundle))
    if args.reuse_evaluation is not None and reuse_evaluation(
            args.reuse_evaluation, args.output, manifest, dev_cases):
        core.log("eval reused equivalent policy; provenance saved in reuse.json")
        return
    baseline = measure(0)
    if args.stage == "eval":
        write_json(args.output / "metrics.json", baseline)
        return

    # Save every candidate separately; 'best.json' selects one without deleting
    # evidence. Baseline checkpoint 0 remains available if training regresses.
    def save(step):
        path = args.output / ("adapter-%d" % step)
        core.save_adapter(model, str(path))
        saved = dict(manifest, checkpoint_step=step)
        if step == 0 and args.stage == "sft":
            # A freshly attached standard LoRA is base-equivalent only when
            # every adapter tensor is finite and all B matrices are zero.
            if fresh_lora_is_noop(model):
                saved["policy_equivalence"] = {"adapter_sha256": None, "basis": "finite-zero-lora-b"}
        elif step == 0 and args.stage == "grpo" and args.adapter:
            if adapter_files(path) == adapter_files(args.adapter):
                saved["policy_equivalence"] = {"adapter_sha256": adapter_info["sha256"],
                                               "basis": "identical-parent-files"}
        write_json(path / "experiment.json", saved)
        write_json(path / "seal.json", seal_adapter(path))
    save(0)
    gate = CheckpointGate(baseline)
    write_json(args.output / "best.json", gate.report())
    def checkpoint(step):
        metrics = measure(step)
        save(step)
        gate.observe(step, metrics)
        write_json(args.output / "best.json", gate.report())
        # THE ONLY PROGRESS THIS STAGE EMITS. `train_sft` writes a row per step
        # to `loss.jsonl` and `round02_pilot.sh` uploads per STAGE, so between
        # the stage banner and the stage's end a reader has nothing: not the
        # step, not the rate, not whether the wall clock will be met. Two rounds
        # died at that wall with no way to have seen it coming, and on
        # 2026-09-20 a live round ran 70 minutes of SFT during which the only
        # honest answer about its progress was "unreadable".
        #
        # `core.log` stamps elapsed seconds, so two of these lines give the rate
        # and the rate gives the finish. Numbers only: the follower streams this
        # into a PUBLIC Actions log, so nothing case-level may pass through here.
        core.log("%s step %d/%d %s  selected=%d"
                 % (args.stage, step, effective["steps"],
                    " ".join("%s=%.4g" % (k, v) for k, v in sorted(metrics.items())
                             if isinstance(v, (int, float)) and not isinstance(v, bool)),
                    gate.best_step))

    if args.stage == "sft":
        train_sft(model, tok, args, train_cases, examples, core, torch, effective, checkpoint,
                  batches=planned_sft_batches)
    else:
        train_grpo(model, tok, args, bundle, train_cases, verifier, effective, policy, checkpoint)
    write_json(args.output / "best.json", gate.report())


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        bundle, adapter = preflight(args)
        if args.plan:
            # The two supervised-dose numbers are printed, not left to be
            # inferred from steps x batch-size, which counts example exposures
            # and not tokens; and the bound is an upper bound, so reading it as
            # a pass is the one mistake this line exists to prevent.
            planned = supervised_plan(args, bundle) or {"planned_exposures": None,
                                                        "supervised_token_upper_bound": None}
            print(json.dumps({"stage": args.stage, "model": BASE_MODEL, "revision": args.revision,
                              "purpose": bundle["purpose"], "effective": schedule(args),
                              "decoding": decoding(schedule(args)["max_tokens"], greedy=args.greedy),
                              "enable_thinking": False,
                              "limits": bundle["limits"], "memory_policy": bundle["memory_policy"],
                              "adapter": adapter, "training_started": False,
                              "planned_exposures": planned["planned_exposures"],
                              "supervised_token_upper_bound": planned["supervised_token_upper_bound"],
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
