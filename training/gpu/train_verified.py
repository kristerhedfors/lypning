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
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.jsonio import append_jsonl, read_jsonl, sha256_of, write_json
from pipeline.training_metrics import BENCHMARK_MIN_FAMILY_CASES, CheckpointGate
from pipeline.evaluation_reuse import fresh_lora_is_noop, reuse_evaluation, reuse_step_zero
from pipeline.training import (ISOLATED_KINDS, TrainingError, Verifier,
    assistant_turn, chat_prompt_token_ids, execution_runner, load_bundle, messages,
    program_from_completion)

from pipeline.training_types import case_ref
from pipeline.training_contract import (BASE_MODEL, CONTRACT_VERSION, MIN_SUPERVISED_TOKENS,
    MIN_TRAIN_CASES, PROTOCOL_EVAL_DRAWS, PROTOCOL_TRAIN_SEEDS,
    adapter_files, adapter_identity, decoding, model_config_identity, probe_contract, probe_report,
    kernel_state, runtime_versions, seal_adapter, source_identity, validate_probe)
from verified_evaluation import evaluate
from verified_stages import (MAX_GRAD_NORM, SFT_OPTIMIZER, balanced_cases, informative_cases,
    sft_batches, supervised_tokens, train_sft, train_grpo)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=("sft", "probe", "grpo", "eval"))
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--sft-targets", type=Path,
                   help="SFT only: private, graded rejection targets (sft.jsonl); requires sibling sft-report.json")
    p.add_argument("--engine", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True, help="new directory, never overwrite")
    p.add_argument("--revision", required=True, help="immutable 40-character base-model Hub commit")
    p.add_argument("--adapter", type=Path, help="local adapter for GRPO warm start or evaluation")
    p.add_argument("--reuse-evaluation", type=Path, help="eval only: reuse a completed equivalent-policy arm")
    p.add_argument("--reuse-step0", type=Path,
                   help="SFT only: record step 0 from this job's base-dev evaluation when the fresh "
                        "LoRA is a verified no-op (evaluation_reuse.reuse_step_zero)")
    p.add_argument("--from-base", action="store_true", help="explicit GRPO-from-base ablation")
    p.add_argument("--plan", action="store_true", help="validate experiment without GPU/downloads")
    p.add_argument("--smoke", action="store_true", help="tiny random Qwen model, two real trainer steps")
    p.add_argument("--isolated-worker", action="store_true",
                   help="attest this is a disposable worker with no sensitive files/credentials")
    p.add_argument("--steps", type=int, default=100)
    p.add_argument("--eval-every", type=int, default=50)
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--lr", type=float, help="default SFT 1e-4 (2e-4 below 100 steps) / GRPO 1e-5")
    p.add_argument("--batch-size", type=int, default=4, help="SFT effective batch only")
    p.add_argument("--generations", type=int, default=8, help="GRPO/probe draws per train prompt")
    p.add_argument("--grpo-prompts", type=int, default=4,
                   help="GRPO prompt groups per optimizer step; times --generations sequences")
    p.add_argument("--grpo-informative-only", action="store_true",
                   help="GRPO only: train on the --probe's informative cases (0 < p < 1) alone")
    p.add_argument("--probe", type=Path, help="admitted probe.json from the exact RL starting policy")
    p.add_argument("--eval-draws", type=int, default=4, help="matched-seed first-draft evaluation draws")
    p.add_argument("--eval-sequences", type=int, default=256,
                   help="sequences per generate call in evaluation: cases per chunk = this // draws")
    p.add_argument("--score-workers", type=int, default=16, help="concurrent verifier scorings per chunk")
    p.add_argument("--greedy", action="store_true", help="eval-only diagnostic; not checkpoint selection")
    p.add_argument("--warmup-ratio", type=float, default=0.1)
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


#: The populations a pilot trains on; each must reach this many families.
CURRICULUM_POPULATIONS = ("coverage", "fallback-control")
MIN_CURRICULUM_FAMILIES_PER_POPULATION = 2


def curriculum_floor(cases):
    """The S4 dose floors, counted on what is TRAINED, not on what the bundle holds.

    `MIN_TRAIN_CASES` was checked against the bundle's train split, and a
    rejection-target curriculum is a subset of it: the smoke targets graded on
    2026-09-22 (Actions run 35759939928) were 157 rows over 54 cases, and would
    have passed a 1,000-case floor because the bank behind them splits to 1,355
    train cases at seed 1111 (run 35491218203, `round02.yml`). The floor
    means "at least this many distinct train cases are learned from", so it is
    counted on the curriculum's distinct cases. The family floor is
    `validate_pilot`'s per-split rule -- two independent families per
    population -- restated on the rows actually trained, so a target set that
    collapsed onto one family per population is not an admitted curriculum.
    Neither number is new and neither is lowered; both are the existing gates
    moved onto the population they were always about.

    Returns the counts and a list of problems, empty when the floors hold, so
    a free CI check (`.github/scripts/s4_target_floor.py`) can report what
    `preflight` refuses.
    """
    distinct = {c["case_id"]: c for c in cases}
    families = {population: sorted({c["family"] for c in distinct.values()
                                     if c.get("population") == population})
                for population in CURRICULUM_POPULATIONS}
    problems = []
    if len(distinct) < MIN_TRAIN_CASES:
        problems.append("the SFT curriculum reaches %d distinct train cases; at least %d required"
                        % (len(distinct), MIN_TRAIN_CASES))
    for population in CURRICULUM_POPULATIONS:
        if len(families[population]) < MIN_CURRICULUM_FAMILIES_PER_POPULATION:
            problems.append("the SFT curriculum reaches %d %s families; at least %d required"
                            % (len(families[population]), population,
                               MIN_CURRICULUM_FAMILIES_PER_POPULATION))
    return {"cases": len(distinct), "rows": len(cases),
            "families": len({c["family"] for c in distinct.values()}),
            "families_by_population": {k: len(v) for k, v in families.items()},
            "minimum_cases": MIN_TRAIN_CASES,
            "minimum_families_per_population": MIN_CURRICULUM_FAMILIES_PER_POPULATION,
            "problems": problems}


def preflight(args):
    if int(os.environ.get("WORLD_SIZE", "1")) != 1:
        raise TrainingError("this runner supports one process/GPU; do not launch with torchrun")
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise TrainingError("--revision must be an immutable model commit, not main")
    if args.output.exists():
        raise TrainingError("--output already exists")
    if min(args.steps, args.eval_every, args.rank, args.batch_size,
           args.max_seq, args.max_new_tokens, args.generations, args.eval_draws,
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
    if args.sft_targets is not None and args.stage != "sft":
        raise TrainingError("--sft-targets is only valid for SFT")
    if args.reuse_evaluation is not None and args.stage != "eval":
        raise TrainingError("--reuse-evaluation is only for standalone evaluation")
    if args.reuse_step0 is not None and args.stage != "sft":
        raise TrainingError("--reuse-step0 is only for SFT, whose step 0 is the unadapted base")
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
    curriculum_cases, _, _ = sft_curriculum(args, bundle)
    if not args.smoke and args.stage in ("sft", "probe", "grpo"):
        if len(train_cases) < MIN_TRAIN_CASES:
            raise TrainingError("real adapter stages require at least %d train cases; got %d"
                                % (MIN_TRAIN_CASES, len(train_cases)))
        if args.seed not in PROTOCOL_TRAIN_SEEDS:
            raise TrainingError("training seed must be one of the pre-registered seeds: %s"
                                % (", ".join(map(str, PROTOCOL_TRAIN_SEEDS))))
    # Only a target curriculum can be narrower than the bundle's train split;
    # an authored-reference curriculum IS that split, gated just above and by
    # `validate_pilot` when the bundle was prepared.
    if not args.smoke and args.stage == "sft" and args.sft_targets is not None:
        problems = curriculum_floor(curriculum_cases)["problems"]
        if problems:
            raise TrainingError("; ".join(problems))
    if (not args.smoke and args.stage == "sft"
            and args.steps * args.batch_size < len({case["family"] for case in curriculum_cases})):
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


def load_sft_targets(path, bundle):
    """Admit a private grader-produced target set without trusting its path."""
    path = Path(path)
    report_path = path.with_name("sft-report.json")
    rows = read_jsonl(path)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TrainingError("SFT targets need a readable sibling sft-report.json") from exc
    train = {c["case_id"]: c for c in bundle["cases"] if c["split"] == "train"}
    if (not rows or report.get("schema") != 1 or report.get("sft_sha256") != sha256_of(rows)
            or report.get("rows") != len(rows) or not report.get("run_id")
            or (report.get("lineage") or {}).get("engine_sha256") != bundle["identity"]["sha256"]):
        raise TrainingError("SFT target report, digest or engine lineage does not match the bundle")
    # The target source is the report's declared arm set, never inferred from
    # the rows it is meant to bound. A report that predates the field was
    # built from conditioned draws only, so its absence means exactly that.
    arms = report.get("arms", ["subset-spec"])
    if (not isinstance(arms, list) or not arms or len(arms) != len(set(arms))
            or not set(arms) <= {"bare", "subset-spec"}):
        raise TrainingError("SFT target report declares no admissible source arm set")
    cases, seen, populations = [], set(), set()
    for row in rows:
        case = train.get(row.get("case_id"))
        source = row.get("source") or {}
        content = ((row.get("messages") or [{}])[-1]).get("content")
        program = program_from_completion(content)
        digest = hashlib.sha256(program.encode("utf-8")).hexdigest() if program else None
        if (case is None or row.get("messages", [])[:-1] != messages(case) or not program
                or row.get("family") != case["family"] or row.get("population") != case["population"]
                or source.get("run_id") != report["run_id"] or source.get("arm") not in arms
                or source.get("program_sha256") != digest):
            raise TrainingError("SFT target row is not a graded target from a declared arm for its bare train prompt")
        key = (case["case_id"], digest)
        if key in seen:
            raise TrainingError("SFT targets repeat a program within one case")
        seen.add(key)
        populations.add(case["population"])
        cases.append(case)
    if populations != {"coverage", "fallback-control"}:
        raise TrainingError("SFT targets need both coverage and fallback-control retention rows")
    return cases, rows, report


def sft_curriculum(args, bundle):
    train_cases = [case for case in bundle.get("cases", []) if case.get("split") == "train"]
    if args.stage != "sft":
        return train_cases, [], None
    if args.sft_targets is not None:
        return load_sft_targets(args.sft_targets, bundle)
    return train_cases, None, None


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
    train_cases, rows, _ = sft_curriculum(args, bundle)
    scheduled_values = rows if rows is not None else train_cases
    batches = sft_batches(train_cases, scheduled_values, schedule(args)["steps"],
                          args.batch_size, args.seed)
    scheduled = [value for batch in batches for value in batch]
    return {"planned_exposures": len(scheduled),
            "supervised_token_upper_bound":
                sum(len(((row["messages"][-1]["content"] if rows is not None else
                          assistant_turn(row["reference"])) +
                         "<|im_end|>").encode("utf-8")) for row in scheduled)}


def curriculum_plan(args, bundle):
    """The curriculum's distinct rows beside the schedule's repeats, bounded above.

    `supervised_plan` sums over the SCHEDULE, every repeat counted again, which
    is what the floor prices. A small target set clears it by repetition alone:
    Actions run 35762924601 (2026-09-22) counted 156,691 scheduled tokens over
    157 rows, about 7.6 passes. The same bound over each distinct row once
    says how much there is to learn from, so the two are printed side by side.
    None where `supervised_plan` is None.
    """
    if args.stage != "sft" or args.smoke:
        return None
    train_cases, rows, _ = sft_curriculum(args, bundle)
    if rows is None:
        segments = [assistant_turn(c["reference"]) for c in train_cases]
    else:
        segments = [row["messages"][-1]["content"] for row in rows]
    floor = curriculum_floor(train_cases)
    return {"curriculum_rows": len(segments), "curriculum_cases": floor["cases"],
            "curriculum_families": floor["families"],
            "unique_supervised_token_upper_bound":
                sum(len((segment + "<|im_end|>").encode("utf-8")) for segment in segments)}


def schedule(args):
    """One source of effective values for execution, dry plans and manifests."""
    steps = 2 if args.smoke else args.steps
    # GRPO 1e-5: a LoRA wants roughly ten times the full-fine-tuning RL rate
    # ("LoRA Without Regret"; TRL's LoRA recipe), which puts it at 1e-5..5e-5.
    # 5e-6 is a full-fine-tuning number, and seed 1111's GRPO ran at it.
    default_lr = (2e-4 if steps < 100 else 1e-4) if args.stage == "sft" else 1e-5
    return {"steps": steps,
            "eval_every": 1 if args.smoke else args.eval_every,
            "max_tokens": min(32, args.max_new_tokens) if args.smoke else args.max_new_tokens,
            "learning_rate": args.lr if args.lr is not None else default_lr}


#: The ceiling on sequences one GRPO optimizer step may carry. The dose is set
#: by --steps; widening a step past this trades update count for batch size
#: without anyone having decided to.
MAX_GRPO_SEQUENCES = 32


def grpo_geometry(args):
    """Prompts x generations per GRPO step, validated where `preflight` does not.

    Seed 1111's GRPO took ONE prompt group of 4 per optimizer step, so each
    update followed one task's reward variance -- or none, since a group whose
    draws all agree carries no gradient at all (DAPO's dynamic sampling). Four
    prompts of eight draws average that out at 32 sequences a step; the
    product is bounded so a flag cannot quietly move a step into another batch
    regime. Called by `main` for every stage, so a bad value fails on --plan;
    None for a stage that is not GRPO.
    """
    if args.grpo_informative_only and (args.stage != "grpo" or args.probe is None):
        raise TrainingError("--grpo-informative-only filters a GRPO stage by its --probe; give both")
    if args.stage != "grpo":
        return None
    prompts, generations = int(args.grpo_prompts), int(args.generations)
    if prompts < 1 or generations < 2:
        raise TrainingError("GRPO needs at least one prompt and two generations per step")
    if prompts * generations > MAX_GRPO_SEQUENCES:
        raise TrainingError("a GRPO step of %d prompts x %d generations exceeds %d sequences"
                            % (prompts, generations, MAX_GRPO_SEQUENCES))
    return {"prompts_per_step": prompts, "generations": generations,
            "sequences_per_step": prompts * generations,
            "informative_only": bool(args.grpo_informative_only)}


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
            raise TrainingError("prompt + completion budget exceeds --max-seq: " + case_ref(case["case_id"]))


# THE LOADING PATH, one definition. `run` below and the hardware smoke
# (`training/hf/hwsmoke.py`) both call these, in this order, so what the smoke
# measures is the model every stage trains and evaluates -- not a neighbour of
# it loaded by a second copy of the code. Each is a function of the revision
# and nothing in a bundle, so the smoke needs no bank.
def block_fused_kernels():
    """Force the torch-reference gated-delta rule; returns `kernel_state()`.

    Must run before anything imports transformers or `fla`.
    """
    # Block fused kernels before importing transformers, preserving the existing
    # exact Qwen class and per-leaf LoRA gradient smoke checks.
    os.environ["NTX_USE_FLA"] = "0"
    # The BLOCKER goes in before anything can import `fla` -- including the
    # observation below, which used to import it itself and so disarmed the
    # blocker on any image where fla imported (`kernel_block`). A blocked name
    # already in `sys.modules` cannot be unloaded, so that is a refusal: the
    # torch reference is this arm's declared kernel, not a preference.
    import kernel_block
    too_late = kernel_block.install()
    if too_late:
        raise TrainingError("fla was imported before its blocker; the torch-reference "
                            "kernel cannot be enforced: " + ", ".join(too_late))
    # AFTER the switch and the blocker, BEFORE transformers is imported, so what
    # is recorded is the state this run actually had. Pinning the distribution
    # does not settle it: on 2026-09-20 the pin held and transformers still ran
    # all 48 gated-delta-net layers on the reference path. `kernel_state` asks in
    # a child interpreter, so asking cannot change the answer here.
    return kernel_state()


def refuse_import_binding():
    """Refuse a gated-delta rule that transformers bound to anything but the reference."""
    import kernel_block
    from transformers import Qwen3_5ForConditionalGeneration

    # transformers resolves the gated-delta rule when the modeling module is
    # IMPORTED, so the binding the loaded model will have is already decided
    # here. Refuse NOW, before the tokenizer, the smoke and the 55 GB pull --
    # the post-load read below is the record; this is the cheap refusal.
    import_binding = kernel_block.module_kernels(
        sys.modules.get(Qwen3_5ForConditionalGeneration.__module__))
    if not kernel_block.reference_only(import_binding):
        raise TrainingError("gated-delta-net resolved to something other than the torch "
                            "reference at import: " + json.dumps(import_binding, sort_keys=True))


def load_tokenizer(revision):
    """The pinned tokenizer, left-padded, with the EOS the SFT labels and TRL agree on."""
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(BASE_MODEL, revision=revision)
    tok.padding_side = "left"
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    if tok.eos_token_id is None or tok.encode("<|im_end|>", add_special_tokens=False) != [tok.eos_token_id]:
        raise TrainingError("Qwen assistant terminator must equal tokenizer EOS for SFT/TRL agreement")
    return tok


def download_base(revision):
    """The pinned checkpoint's local snapshot directory (the 55 GB pull)."""
    from huggingface_hub import snapshot_download

    return snapshot_download(BASE_MODEL, revision=revision,
                             allow_patterns=["*.json", "*.jinja", "*.txt", "*.safetensors"])


def load_base_model(path, dtype):
    """Qwen3_5ForConditionalGeneration on GPU 0, refused on any key mismatch."""
    from transformers import Qwen3_5ForConditionalGeneration

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
    return model


def attach_fresh_lora(model, rank, seed, core):
    """A new rank-`rank` adapter drawn from `seed`, checked on the object."""
    from transformers import set_seed

    # Reseed IMMEDIATELY before the adapter is initialised. `set_seed` above
    # is followed by the gradient smoke, which reseeds to 0 and consumes a
    # fixed amount of randomness, so without this every protocol seed drew
    # the same `lora_A` and seeds 1111/2222/3333 differed only in batch
    # order -- three replicates of one initialisation, not three replicates.
    set_seed(seed)
    return core.attach_lora(model, rank, 2 * rank, 0.0)


def refuse_bound_kernels(model):
    """The kernel the loaded model will CALL; refused unless it is the torch reference."""
    import kernel_block

    # What the loaded model will CALL, read off the model after load: the
    # switch and the blocker are requests, this is the answer. Refused rather
    # than only recorded when it is not the torch reference, because a run on
    # another kernel is another arm (`STATUS.md` §2), and reading that from the
    # manifest after the dose has been paid for is too late.
    kernel_binding = kernel_block.bound_kernels(model)
    if not kernel_block.reference_only(kernel_binding):
        raise TrainingError("gated-delta-net is not bound to the torch reference: "
                            + json.dumps(kernel_binding, sort_keys=True))
    return kernel_binding


def run(args, bundle, adapter_info):
    verifier = Verifier(args.engine, **bundle["limits"], identity=bundle["identity"],
                        runner=execution_runner(bundle["execution"], bundle["identity"],
                                                stage="grpo"))
    versions = runtime_versions()
    effective = schedule(args)
    kernels = block_fused_kernels()
    import lypning_lora as core
    import torch
    from peft import PeftModel
    from transformers import AutoConfig, Qwen3_5ForConditionalGeneration, set_seed

    refuse_import_binding()

    if not args.smoke and not torch.cuda.is_available():
        raise TrainingError("CUDA required for a real 27B run")
    if not args.smoke and not torch.cuda.is_bf16_supported():
        raise TrainingError("this experiment requires native BF16 support")
    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    tok = load_tokenizer(args.revision)
    train_cases = [c for c in bundle["cases"] if c["split"] == "train"]
    dev_cases = evaluation_cases(bundle, args.eval_split)
    check_prompt_budget(tok, train_cases + dev_cases, args.max_new_tokens, args.max_seq)
    examples = []
    planned_sft_batches = None
    planned_tokens = None
    if args.stage == "sft":
        train_cases, rows, target_report = sft_curriculum(args, bundle)
        if rows is None:
            rows = [{"case_id": c["case_id"], "messages": messages(c) + [{"role": "assistant",
                     "content": assistant_turn(c["reference"])}]}
                    for c in train_cases]
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
        # The smoke steps the SAME optimiser definition `train_sft` uses and
        # samples under the run's own decoding, so a pass says something about
        # the configuration that trains rather than about a neighbour of it.
        smoke_decoding = decoding(effective["max_tokens"])
        core.smoke(device, dtype, SimpleNamespace(
            revision=args.revision, rank=args.rank, alpha=2 * args.rank,
            lora_dropout=0.0, lr=effective["learning_rate"],
            temperature=smoke_decoding["temperature"], top_p=smoke_decoding["top_p"],
            top_k=smoke_decoding["top_k"], optimizer=SFT_OPTIMIZER))
    if args.smoke:
        cfg = smoke_config(AutoConfig.from_pretrained(BASE_MODEL, revision=args.revision),
                           len(tok), core.tiny_config)
        # The random smoke BASE must reload identically, independently of the
        # training seed or RNG consumed by the preceding gradient probe.
        torch.manual_seed(0)
        model = Qwen3_5ForConditionalGeneration(cfg).to(device=device, dtype=dtype)
        set_seed(args.seed)
    else:
        model = load_base_model(download_base(args.revision), dtype)
    if args.adapter:
        # Continue the SAME adapter so its saved weights include the SFT warm
        # start and reload on the pinned base without a hidden merged parent.
        model = PeftModel.from_pretrained(model, str(args.adapter), is_trainable=args.stage == "grpo")
    elif args.stage in ("sft", "grpo"):
        model = attach_fresh_lora(model, args.rank, args.seed, core)
    if args.stage in ("sft", "grpo"):
        core.check_adapted_modules(model)
    kernel_binding = refuse_bound_kernels(model)
    model.config.pad_token_id = tok.pad_token_id
    model.generation_config.pad_token_id = tok.pad_token_id
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {"base_model": BASE_MODEL, "revision": args.revision, "stage": args.stage,
                "bundle_digest": bundle["digest"],
                # `adapter_lineage_admitted` admits a pilot-trained adapter on
                # the eval-2 benchmark by THIS field; unwritten, every SFT
                # adapter was refused at sft-eval2, a stage no job had reached.
                "purpose": bundle["purpose"], "adapter": adapter_info,
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
                # Which job ran this stage: step-0 reuse admits only an
                # evaluation written by the SAME job (`reuse_step_zero`).
                "job_id": os.environ.get("JOB_ID"),
                "sft_targets": ({"run_id": target_report["run_id"],
                                 "sft_sha256": target_report["sft_sha256"],
                                 "rows": target_report["rows"],
                                 "lineage": target_report["lineage"]}
                                if args.stage == "sft" and target_report else None),
                "args": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                "versions": versions, "kernels": kernels, "kernel_binding": kernel_binding,
                # Declared, not implied: a fresh adapter's `lora_A` is drawn
                # from `seed` (reseeded just before `attach_lora`), and SFT
                # steps exactly this optimiser definition.
                "lora_init": ({"seed": args.seed, "basis": "set_seed-before-attach_lora"}
                              if args.stage in ("sft", "grpo") and not args.adapter else None),
                "sft_optimizer": (dict(SFT_OPTIMIZER, name="AdamW", max_grad_norm=MAX_GRAD_NORM)
                                  if args.stage == "sft" else None),
                "grpo_geometry": grpo_geometry(args)}
    if adapter_info:
        prior = adapter_info["experiment"]
        for key in ("tokenizer_sha256", "model_config_sha256", "enable_thinking"):
            if prior.get(key) != manifest[key]:
                raise TrainingError("adapter runtime contract changed: " + key)
    if args.stage == "grpo" and not args.smoke:
        probe_manifest = json.loads(args.probe.with_name("experiment.json").read_text())
        for key in ("tokenizer_sha256", "model_config_sha256", "versions", "kernels",
                    "kernel_binding", "eos_token_id"):
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
    baseline = None
    if args.reuse_step0 is not None:
        # Before any optimizer step: a fresh LoRA with finite tensors and zero B
        # IS the base, so base-dev's draws are step 0's. No proof, no reuse.
        baseline = reuse_step_zero(args.reuse_step0, args.output, manifest, dev_cases,
                                   fresh_lora_is_noop(model))
        core.log("sft step 0 %s" % ("reused from base-dev; provenance saved in reuse.json"
                                    if baseline is not None else "measured: no no-op proof"))
    if baseline is None:
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
        rl_cases = train_cases
        if args.grpo_informative_only:
            # Offline dynamic sampling: a case the probe drew all-pass or
            # all-fail is a group whose advantages are all zero, so every
            # step spent on it is a step with no gradient.
            rl_cases = informative_cases(args.probe.with_name("probe-rollouts.jsonl"), train_cases)
        train_grpo(model, tok, args, bundle, rl_cases, verifier, effective, policy, checkpoint)
    write_json(args.output / "best.json", gate.report())


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        bundle, adapter = preflight(args)
        geometry = grpo_geometry(args)
        if args.plan:
            # The two supervised-dose numbers are printed, not left to be
            # inferred from steps x batch-size, which counts example exposures
            # and not tokens; and the bound is an upper bound, so reading it as
            # a pass is the one mistake this line exists to prevent.
            planned = supervised_plan(args, bundle) or {"planned_exposures": None,
                                                        "supervised_token_upper_bound": None}
            unique = curriculum_plan(args, bundle) or {}
            print(json.dumps({"stage": args.stage, "model": BASE_MODEL, "revision": args.revision,
                              "purpose": bundle["purpose"], "effective": schedule(args),
                              "decoding": decoding(schedule(args)["max_tokens"], greedy=args.greedy),
                              "enable_thinking": False,
                              "limits": bundle["limits"], "memory_policy": bundle["memory_policy"],
                              "adapter": adapter, "training_started": False,
                              "grpo_geometry": geometry,
                              "planned_exposures": planned["planned_exposures"],
                              "supervised_token_upper_bound": planned["supervised_token_upper_bound"],
                              "curriculum_rows": unique.get("curriculum_rows"),
                              "curriculum_cases": unique.get("curriculum_cases"),
                              "unique_supervised_token_upper_bound":
                                  unique.get("unique_supervised_token_upper_bound"),
                              "bundle_digest": bundle["digest"], "target": bundle["identity"],
                              "cases": {s: sum(c["split"] == s for c in bundle["cases"])
                                        for s in ("train", "dev", "test")}}, indent=2))
        else:
            run(args, bundle, adapter)
        return 0
    except KeyError as exc:
        # A KeyError's text IS its key, and a missing key here is as likely a
        # case id as a field name; this log is streamed publicly (round
        # follower), so it gets the type and a digest of the key, never the key.
        print("training blocked: KeyError (key %s)"
              % hashlib.sha256(repr(exc.args).encode("utf-8")).hexdigest()[:12], file=sys.stderr)
        return 1
    except (TrainingError, OSError) as exc:
        print("training blocked: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
