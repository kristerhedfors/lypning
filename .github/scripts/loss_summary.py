"""Did the training loss move? The one question a selected step 0 leaves open.

WHY THIS EXISTS. On 2026-09-20 seed 1111 ran 300 SFT steps and 20 GRPO steps on
bank v3 and `best.json` selected **step 0** for both -- no checkpoint beat its
own baseline at any eval point. That is a clean negative, but it does not say
WHICH negative, and the two have different fixes:

  loss fell, nothing transferred  -> the objective is learnable and the reward
                                     or the eval does not see it; look at the
                                     SFT row construction and the gate
  loss flat                       -> nothing was learned at all; look at the
                                     learning rate, the dose, the data

`train_sft` writes a row per step to `loss.jsonl` -- step, loss, learning_rate,
supervised_tokens, supervised_tokens_total -- and nothing reads it. It is the
only per-step record the round keeps.

WHY A SUMMARY AND NOT THE FILE. `s0_inventory.py` may print only its `SMALL`
tuple, and its rule is the right one: *"Never widen this to anything that holds
task text, a reference program or an expected stdout; count its lines instead,
or compute the statistic in the job and print only the statistic."* This
computes the statistic. Every number it prints is an aggregate over steps; no
case id, no program and no expected output can reach the log through it, which
matters because the follower streams into a PUBLIC Actions log.

Free and read-only: it downloads one JSONL of numbers and prints a summary.
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def summarise(rows):
    """First/last/min loss, the trend across halves, and the token totals."""
    losses = [r["loss"] for r in rows if isinstance(r.get("loss"), (int, float))]
    if not losses:
        return None
    half = max(1, len(losses) // 2)
    first_half = sum(losses[:half]) / half
    second_half = sum(losses[half:]) / max(1, len(losses) - half)
    return {
        "steps": len(rows),
        "loss_first": losses[0],
        "loss_last": losses[-1],
        "loss_min": min(losses),
        "loss_min_step": rows[losses.index(min(losses))].get("step"),
        "mean_first_half": first_half,
        "mean_second_half": second_half,
        # The sign that separates the two negatives. A drop here with a
        # selected step 0 means the objective was learnable and did not
        # transfer; no drop means nothing was learned at all.
        "drop_across_halves": first_half - second_half,
        "supervised_tokens_total": rows[-1].get("supervised_tokens_total"),
        "learning_rate_first": rows[0].get("learning_rate"),
        "learning_rate_last": rows[-1].get("learning_rate"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job", help="the HF job id whose round to read")
    ap.add_argument("--stage", default="sft", help="sft or grpo")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(token=token)
    repo = "%s/%s" % (api.whoami()["name"],
                      os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))
    if not api.repo_info(repo, repo_type="dataset").private:
        raise SystemExit("refusing to read rounds from a public repository")

    path = "round-02/%s/%s/loss.jsonl" % (args.job, args.stage)
    try:
        local = hf_hub_download(repo, path, repo_type="dataset", token=token)
    except Exception as exc:                                      # noqa: BLE001
        print("could not read %s: %s" % (path, type(exc).__name__), file=sys.stderr)
        return 1
    with open(local, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]

    print("== %s" % path)
    stats = summarise(rows)
    if stats is None:
        print("   no loss rows; the stage wrote nothing to read")
        return 1
    for key in ("steps", "loss_first", "loss_last", "loss_min", "loss_min_step",
                "mean_first_half", "mean_second_half", "drop_across_halves",
                "supervised_tokens_total", "learning_rate_first", "learning_rate_last"):
        value = stats[key]
        print("   %-24s %s" % (key, "%.6g" % value if isinstance(value, float) else value))

    # Stated rather than left to the reader: this is the whole point of the run.
    drop = stats["drop_across_halves"]
    scale = abs(stats["loss_first"]) or 1.0
    if drop > 0.05 * scale:
        print("\n   THE LOSS FELL. With step 0 selected, the objective was learnable and\n"
              "   did not transfer to the eval -- look at the SFT rows and the gate.")
    else:
        print("\n   THE LOSS DID NOT FALL MEANINGFULLY. Nothing was learned at all --\n"
              "   look at the learning rate, the dose and the data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
