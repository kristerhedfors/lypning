#!/usr/bin/env bash
# Runs as root on the GPU box at boot. Order matters: the two kill-switches are
# armed BEFORE anything that could hang, because a startup script that dies on
# line 40 must still leave a box that turns itself off.
set -uo pipefail
exec > >(tee -a /var/log/ntx-startup.log) 2>&1

M="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
meta() { curl -sf -H "Metadata-Flavor: Google" "$M/$1" || echo "${2:-}"; }

RUN_ID=$(meta NTX_RUN_ID run); CAP=$(meta NTX_CAP_USD 60); RATE=$(meta NTX_RATE 29.52)
GCS=$(meta NTX_GCS); MAX_MIN=$(meta NTX_MAX_MIN 120); EPOCHS=$(meta NTX_EPOCHS 3)
export HF_TOKEN=$(meta NTX_HF_TOKEN)
STATE=/var/run/ntx; mkdir -p $STATE /mnt/data /mnt/ckpt /opt/ntx

# ---- Layer 1: the kernel timer. Nothing below can cancel it.
shutdown -h "+${MAX_MIN}" "ntx: hard cap ${MAX_MIN}m" &
echo "armed: shutdown in ${MAX_MIN} min"

# ---- Layer 2: the spend guard + dead-man's switch, as its own process.
gsutil -q cp "$GCS/code/budget.py" /opt/ntx/budget.py
cat > /usr/local/bin/ntx-flush <<'EOF'
#!/bin/sh
# Called by the guard just before it powers off: save whatever exists.
gsutil -q -m rsync -r /mnt/ckpt "$(cat /var/run/ntx/gcs)/ckpt" 2>/dev/null
gsutil -q -m rsync -r /mnt/data/out "$(cat /var/run/ntx/gcs)/out" 2>/dev/null
EOF
chmod +x /usr/local/bin/ntx-flush
echo "$GCS/$RUN_ID" > $STATE/gcs
nohup python3 /opt/ntx/budget.py --rate-per-hour "$RATE" --cap-usd "$CAP" \
      --state-dir $STATE >> /var/log/ntx-budget.log 2>&1 &
echo "armed: spend guard, cap \$$CAP at \$$RATE/hr"

phase() { echo "$1" > $STATE/phase; touch $STATE/heartbeat
          gsutil -q cp $STATE/phase "$GCS/$RUN_ID/phase" 2>/dev/null || true
          echo "=== phase: $1"; }
beat()  { while sleep 30; do touch $STATE/heartbeat
          gsutil -q cp $STATE/spend.json "$GCS/$RUN_ID/spend.json" 2>/dev/null || true
          done; }
beat & BEAT=$!
halted() { [ -f $STATE/HALT ]; }

# ---- Phase 1: inputs. Resume-aware: a restart after preemption re-uses GCS.
phase fetch
gsutil -q -m cp -r "$GCS/code" /opt/ntx/code
gsutil -q -m cp -r "$GCS/data/*" /mnt/data/ || true
gsutil -q -m rsync -r "$GCS/$RUN_ID/ckpt" /mnt/ckpt 2>/dev/null || true
pip install -q --no-input nemo-automodel vllm==0.27.1 2>&1 | tail -2

# ---- Phase 2: train. ep_size must equal nproc-per-node; both are 8.
phase train
if ! halted; then
  automodel /opt/ntx/code/lora_rank16.yaml --nproc-per-node 8 \
    2>&1 | tail -200 || echo "TRAIN FAILED (rc=$?)"
  gsutil -q -m rsync -r /mnt/ckpt "$GCS/$RUN_ID/ckpt"
fi

# ---- Phase 3: generate. The box NEVER grades: the acceptance tests are CPU
# work and cost 12x more per second here than they do anywhere else.
phase generate
if ! halted; then
  ADAPTER=$(ls -d /mnt/ckpt/*/ 2>/dev/null | tail -1)
  vllm serve nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16 \
    --served-model-name cand --enable-lora --lora-modules "cand-lora=${ADAPTER}" \
    --max-model-len 8192 --reasoning-parser nemotron_v3 \
    --tensor-parallel-size 1 --data-parallel-size 8 \
    --host 127.0.0.1 --port 8000 > /var/log/ntx-vllm.log 2>&1 &
  for i in $(seq 1 60); do curl -sf localhost:8000/health >/dev/null && break; sleep 10; done
  mkdir -p /mnt/data/out
  python3 /opt/ntx/code/generate.py \
      --base-url http://127.0.0.1:8000/v1 --model cand-lora \
      --cases /mnt/data/holdout.jsonl --k 16 --no-thinking \
      --out /mnt/data/out/completions.jsonl
  gsutil -q -m rsync -r /mnt/data/out "$GCS/$RUN_ID/out"
fi

# ---- Phase 4: leave. The cheapest phase and the one people forget.
phase done
gsutil -q cp $STATE/spend.json "$GCS/$RUN_ID/spend.json" 2>/dev/null || true
python3 - <<EOF | gsutil -q cp - "$GCS/ledger.jsonl.$RUN_ID"
import json,os
s={}
try: s=json.load(open("$STATE/spend.json"))
except Exception: pass
print(json.dumps({"run_id":"$RUN_ID","spend_usd":s.get("spend_usd",0.0),
                  "elapsed_s":s.get("elapsed_s",0)}))
EOF
gsutil -q compose "$GCS/ledger.jsonl" "$GCS/ledger.jsonl.$RUN_ID" "$GCS/ledger.jsonl" 2>/dev/null \
  || gsutil -q cp "$GCS/ledger.jsonl.$RUN_ID" "$GCS/ledger.jsonl"
kill $BEAT 2>/dev/null
shutdown -h now "ntx: work complete"
