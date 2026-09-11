#!/usr/bin/env bash
# Rent 8xH100 on SPOT, run one signal experiment, and be gone. Layers 0 and 4.
#
# The two flags that matter more than anything else in this file:
#   --max-run-duration + --instance-termination-action=DELETE
# GCP enforces those in the control plane. They hold if the guest never boots,
# if the startup script has a typo, if the driver crashes, and if this session
# ends. Everything else is defence in depth behind them.
set -euo pipefail

RUN_ID="${RUN_ID:-ntx-$(date +%Y%m%d-%H%M%S)}"
ZONES="${ZONES:-us-central1-a us-central1-c us-east4-a europe-west4-b}"
MACHINE="${MACHINE:-a3-highgpu-8g}"
RATE_PER_HOUR="${RATE_PER_HOUR:-29.52}"      # 8 x $3.69 GPU-hr, SPOT
CAP_USD="${CAP_USD:-60}"                      # hard dollar cap for THIS run
TOTAL_CAP_USD="${TOTAL_CAP_USD:-150}"         # cumulative cap across all runs
GCS="${GCS:?set GCS=gs://your-bucket/ntx}"
EPOCHS="${EPOCHS:-3}"

MAX_MIN=$(python3 -c "print(int($CAP_USD / $RATE_PER_HOUR * 60))")

# ---- Layer 4: the cumulative ledger. "Just one more try" is how a $60 cap
# becomes $500, so the ledger is consulted before anything is created.
LEDGER="$GCS/ledger.jsonl"
SPENT=$(gsutil cat "$LEDGER" 2>/dev/null | python3 -c "
import sys,json
t=0.0
for l in sys.stdin:
    l=l.strip()
    if l:
        try: t+=float(json.loads(l).get('spend_usd',0))
        except Exception: pass
print('%.2f'%t)" || echo 0)
echo "cumulative spend so far: \$$SPENT of \$$TOTAL_CAP_USD"
if python3 -c "import sys; sys.exit(0 if $SPENT + $CAP_USD > $TOTAL_CAP_USD else 1)"; then
  echo "REFUSING: this run (\$$CAP_USD) would take cumulative spend past \$$TOTAL_CAP_USD." >&2
  echo "Raise TOTAL_CAP_USD deliberately, or reset the ledger, if that is what you want." >&2
  exit 2
fi

cat <<EOF
plan
  run            $RUN_ID
  machine        $MACHINE  SPOT
  rate           \$$RATE_PER_HOUR/hr
  cap            \$$CAP_USD  -> GCP deletes the instance after $MAX_MIN minutes
  cumulative     \$$SPENT + \$$CAP_USD of \$$TOTAL_CAP_USD
  artifacts      $GCS/$RUN_ID
EOF
if [ "${YES:-0}" != "1" ]; then
  echo; echo "dry run. re-run with YES=1 to actually create the instance."; exit 0
fi

for ZONE in $ZONES; do
  echo "trying $ZONE ..."
  if gcloud compute instances create "$RUN_ID" \
      --zone="$ZONE" --machine-type="$MACHINE" \
      --provisioning-model=SPOT \
      --instance-termination-action=DELETE \
      --max-run-duration="${MAX_MIN}m" \
      --boot-disk-size=1000GB --boot-disk-type=pd-ssd --boot-disk-auto-delete \
      --image-family=common-cu124-ubuntu-2204-py310 \
      --image-project=deeplearning-platform-release \
      --scopes=storage-rw \
      --labels=ntx-run="$RUN_ID",ntx-autoreap=true \
      --metadata-from-file=startup-script=startup.sh \
      --metadata=NTX_RUN_ID="$RUN_ID",NTX_CAP_USD="$CAP_USD",NTX_RATE="$RATE_PER_HOUR",NTX_GCS="$GCS",NTX_MAX_MIN="$MAX_MIN",NTX_EPOCHS="$EPOCHS",NTX_HF_TOKEN="${HF_TOKEN:-}" \
      2>/tmp/ntx-create.err; then
    echo "created $RUN_ID in $ZONE; auto-deletes in $MAX_MIN min"
    echo "$ZONE" > /tmp/ntx-zone
    echo
    echo "  nt gpu status     # phase, step, spend, minutes left"
    echo "  nt gpu down       # kill it now"
    exit 0
  fi
  tail -2 /tmp/ntx-create.err
done
echo "no capacity in any zone: $ZONES" >&2
exit 1
