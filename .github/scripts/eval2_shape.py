"""Print the prompt-length shape of a finish's evaluation chunks, aggregates only.

The seed-1111 finish (HF job 6ab6a20c6b030d633f691a95, 2026-09-25) ran out of
CUDA memory in the prefill of one base-eval2 `generate` call, 2.5 hours in:
256 sequences left-padded to the longest prompt of their 16-case chunk. This
reads the pilot's bundles and the failed stage's row count from the private
artifact repo, renders every prompt exactly as `verified_evaluation.evaluate`
does, and prints per-split chunk statistics: how many chunks, the padded
prompt length per chunk (quantiles and max), and where the failed stage
stopped. Never a case id, task or program: lengths and counts only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "training"))
from pipeline.training import messages  # noqa: E402
from pipeline.training_contract import BASE_MODEL  # noqa: E402

REPO = "headforce/lypning-round02-artifacts"
PILOT = os.environ.get("PILOT_JOB", "6ab52a686b030d633f68e503")
FAILED = os.environ.get("FAILED_JOB", "6ab6a20c6b030d633f691a95")
DRAWS, SEQUENCES = 16, 256


def quantiles(values):
    s = sorted(values)
    pick = lambda q: s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]  # noqa: E731
    return {"n": len(s), "min": s[0], "p50": pick(0.5), "p90": pick(0.9), "p99": pick(0.99), "max": s[-1]}


def main():
    from huggingface_hub import HfApi, hf_hub_download
    from transformers import AutoTokenizer

    api = HfApi()
    rev = api.repo_info(REPO, repo_type="dataset").sha
    get = lambda p: hf_hub_download(REPO, p, repo_type="dataset", revision=rev)  # noqa: E731
    tok = AutoTokenizer.from_pretrained(BASE_MODEL, revision=os.environ["QWEN_REV"])

    def lengths(cases):
        texts = [tok.apply_chat_template(messages(c), tokenize=False, add_generation_prompt=True,
                                         enable_thinking=False) for c in cases]
        return [len(ids) for ids in tok(texts, add_special_tokens=False)["input_ids"]]

    per_chunk = SEQUENCES // DRAWS
    out = {}
    pilot = json.load(open(get("round-02/%s/pilot/bundle.json" % PILOT)))
    eval2 = json.load(open(get("round-02/%s/eval2/bundle.json" % PILOT)))
    for name, cases in (("pilot-dev", [c for c in pilot["cases"] if c["split"] == "dev"]),
                        ("pilot-test", [c for c in pilot["cases"] if c["split"] == "test"]),
                        ("eval2", list(eval2["cases"]))):
        lens = lengths(cases)
        chunk_max = [max(lens[i:i + per_chunk]) for i in range(0, len(lens), per_chunk)]
        out[name] = {"cases": len(cases), "prompt": quantiles(lens), "chunk_padded": quantiles(chunk_max),
                     "chunk_padded_tokens": quantiles([m * SEQUENCES for m in chunk_max])}
        if name == "eval2":
            eval2_chunks = chunk_max
    try:
        rows = sum(1 for _ in open(get("round-02/%s/base-eval2/evaluations.jsonl" % FAILED)))
    except Exception as exc:                                   # noqa: BLE001
        rows = None
        print("failed stage rows unreadable: %s" % type(exc).__name__)
    try:
        exp = json.load(open(get("round-02/%s/base-eval2/experiment.json" % FAILED)))
        out["max_new_tokens"] = (exp.get("effective") or {}).get("max_tokens")
    except Exception as exc:                                   # noqa: BLE001
        print("failed stage experiment unreadable: %s" % type(exc).__name__)
    if rows is not None:
        done = rows // SEQUENCES
        order = sorted(range(len(eval2_chunks)), key=lambda i: -eval2_chunks[i])
        window = range(max(0, done - 1), min(len(eval2_chunks), done + 3))
        out["failed_stage"] = {
            "rows": rows, "chunks_scored": done, "chunks_total": len(eval2_chunks),
            "window": [{"chunk": i, "padded": eval2_chunks[i], "rank_by_length": order.index(i) + 1}
                       for i in window],
            "chunks_longer_than_known_fit": sum(1 for m in eval2_chunks
                                                if m > out["pilot-dev"]["chunk_padded"]["max"]),
        }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
