#!/usr/bin/env bash
# The one-liner that guarantees nothing is left running. Safe to run any time,
# safe to run twice, and the last line is the one to trust.
set -uo pipefail
echo "instances labelled ntx-autoreap=true:"
LIST=$(gcloud compute instances list --filter="labels.ntx-autoreap=true" \
        --format="value(name,zone,status)" 2>/dev/null)
if [ -z "$LIST" ]; then echo "  none"; echo; echo "CLEAN: no ntx instances exist."; exit 0; fi
echo "$LIST" | sed 's/^/  /'
while read -r NAME ZONE STATUS; do
  [ -z "$NAME" ] && continue
  echo "deleting $NAME ($ZONE, $STATUS)"
  gcloud compute instances delete "$NAME" --zone="$ZONE" --quiet || true
done <<< "$LIST"
REMAIN=$(gcloud compute instances list --filter="labels.ntx-autoreap=true" --format="value(name)" 2>/dev/null | wc -l)
if [ "$REMAIN" -eq 0 ]; then echo; echo "CLEAN: no ntx instances exist."; else
  echo; echo "WARNING: $REMAIN still listed — re-run." >&2; exit 1; fi
