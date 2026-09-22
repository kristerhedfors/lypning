"""Free planning for PLAN Step 2 on the actual pilot's training split.

This module never generates or executes a program. Its public output contains
counts, hashes and token totals only; the private bank stays on the CI worker.
A provider quote is an estimate, not permission to spend or a hard bill cap.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .jsonio import sha256_of
from .training import chat_prompt_token_ids, messages
from .training_data import split_cases, validate_cases, validate_pilot
from .training_types import TrainingError

MODEL = "qwen-3.8-27b"
MODEL_REPO = "Qwen/Qwen3.8-27B"
SAMPLES = 16
MAX_TOKENS = 2048
PRICE_IN = .99
PRICE_OUT = 1.49
PRICE_SOURCE = "https://www.cerebras.ai/pricing"


def population(rows, seed=1111):
    validate_cases(rows)
    # Re-derive a raw bank; never trust an arbitrary pre-labelled JSONL file.
    if any("split" in row for row in rows):
        raise TrainingError("positive control expects the raw bank, not a prepared split")
    assigned = split_cases(rows, seed)
    validate_pilot(assigned)
    return [row for row in assigned if row["split"] == "train"]


def arm_messages(case, spec=None):
    result = messages(case)
    if spec:
        result[0]["content"] += "\n\n" + spec.strip()
    return result


def plan(rows, spec, token_ids, *, seed=1111):
    if not isinstance(spec, str) or not spec.strip():
        raise TrainingError("subset spec must be nonempty")
    cases = population(rows, seed)
    arms = {}
    for name, extra in (("bare", None), ("subset-spec", spec)):
        counts = [len(token_ids(arm_messages(c, extra))) for c in cases]
        if max(counts) + MAX_TOKENS > 32768:
            raise TrainingError("a prompt exceeds the declared 32768-token planning window")
        input_total = sum(counts) * SAMPLES
        output_max = len(cases) * SAMPLES * MAX_TOKENS
        arms[name] = {"calls": len(cases) * SAMPLES, "input_tokens": input_total,
                      "max_input_tokens": max(counts), "output_token_allowance": output_max,
                      "cost_at_output_allowance_usd": round((input_total * PRICE_IN + output_max * PRICE_OUT) / 1e6, 4)}
    input_total = sum(a["input_tokens"] for a in arms.values())
    calls = sum(a["calls"] for a in arms.values())
    return {
        "stage": "S1 / PLAN Step 2", "state": "planned; no generation",
        "provider": "https://api.cerebras.ai/v1", "model": MODEL,
        "model_identity_limit": "provider model ID; no claim of immutable served weights or kernels",
        "bank_cases": len(rows), "train_cases": len(cases),
        "families": len({c["family"] for c in cases}),
        "populations": dict(Counter(c["population"] for c in cases)),
        "split_seed": seed, "population": "split_cases(raw bank, 1111), train only",
        "case_set_sha256": sha256_of(sorted(c["case_id"] for c in cases)),
        "bank_sha256": sha256_of(rows),
        "system_sha256": hashlib.sha256(messages(cases[0])[0]["content"].encode()).hexdigest(),
        "subset_spec_sha256": hashlib.sha256(spec.encode()).hexdigest(),
        "samples_per_case_per_arm": SAMPLES, "arms": arms, "calls": calls,
        "sampling": {"temperature": .7, "top_p": .8, "max_tokens": MAX_TOKENS,
                     "reasoning_effort": "none", "seed": seed,
                     "top_k": "omitted: hosted endpoint does not promise it"},
        "pricing": {"input_per_million_usd": PRICE_IN, "output_per_million_usd": PRICE_OUT,
                    "source": PRICE_SOURCE, "checked": "2026-09-22",
                    "limit": "listed token rates, before tax; provider template/usage may differ; no retries included"},
        "input_only_usd": round(input_total * PRICE_IN / 1e6, 4),
        "cost_at_output_allowance_usd": round(sum(a["cost_at_output_allowance_usd"] for a in arms.values()), 4),
        "cost_scenarios_usd": {str(n): round((input_total * PRICE_IN + calls * n * PRICE_OUT) / 1e6, 4)
                               for n in (128, 256, 512, 1024)},
        "decision": "native family macro gain >= 0.10 and correctness delta >= -0.02: distillation; otherwise contrastive",
        "metric_policy": "all train families; floor 1 as pilot/dev, paired source/family bootstrap; report controls separately",
        "remaining": ["candidate conformance and reference verification", "reviewed bounded generation runner",
                      "operator dollar ceiling if the plan exceeds the recorded ~$5 estimate"],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bank", type=Path)
    ap.add_argument("--spec", type=Path, default=Path("training/prompts/subset-spec.md"))
    ap.add_argument("--revision", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    import re
    if not re.fullmatch("[0-9a-f]{40}", args.revision):
        raise TrainingError("tokenizer revision must be an immutable 40-hex commit")
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(MODEL_REPO, revision=args.revision, trust_remote_code=False)
    rows = [json.loads(line) for line in args.bank.read_text().splitlines() if line.strip()]
    result = plan(rows, args.spec.read_text(), lambda msgs: chat_prompt_token_ids(tok, msgs))
    result["tokenizer_revision"] = args.revision
    result["bank_file_sha256"] = hashlib.sha256(args.bank.read_bytes()).hexdigest()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
