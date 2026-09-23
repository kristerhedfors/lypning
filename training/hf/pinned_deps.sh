#!/usr/bin/env bash
# The GPU script's own pinned dependencies, installed inside an HF Job.
#
# One installer for every round-02 GPU job script (pilot, hardware smoke,
# split eval-2), so "the same deps as the pilot" is true by construction:
# `training/gpu/train_verified.py`'s PEP 723 header is the single source of
# truth, and this reads it rather than restating it. Run from the checkout root.
#
# A dropped download is not a failed run: pip retries its own connections,
# and the whole install is retried with a growing pause (job 6aaa49a9,
# 2026-09-16, died at exit 123 on one broken pipe three minutes in). Four
# failures exit 123, which the caller's `set -e` passes on unchanged.
set -euo pipefail
DEPS=$(python3 - <<'PYEOF'
import re
head = open("training/gpu/train_verified.py").read().split("# ///")[1]
print(" ".join(re.findall(r'"([^"]+)"', head.split("dependencies")[1])))
PYEOF
)
for attempt in 1 2 3 4; do
  # shellcheck disable=SC2086
  if pip install -q --no-cache-dir --retries 10 --timeout 120 $DEPS; then break; fi
  if [ "$attempt" = 4 ]; then echo "== pip install failed 4 times"; exit 123; fi
  echo "== pip install attempt $attempt failed; retrying in $((attempt * 30))s"
  sleep $((attempt * 30))
done
python3 -c 'import torch, transformers, peft, trl, huggingface_hub; print("== torch", torch.__version__, "cuda", torch.cuda.is_available(), "| transformers", transformers.__version__, "| trl", trl.__version__, "| hub", huggingface_hub.__version__)'
# The kernel is part of the arm (`STATUS.md` §2). transformers binds the
# gated-delta rule at import, so ask now, in minute one, rather than in the
# first trainer stage an hour later; `train_verified.run` asks again in-process.
NTX_USE_FLA=0 PYTHONPATH="${PYTHONPATH:-}:training/gpu" python3 -c 'import sys, kernel_block; why = kernel_block.refusal(); print("== kernel:", why or "torch reference"); sys.exit(1 if why else 0)'
