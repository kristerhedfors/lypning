#!/bin/sh
# tool: lypning routes
# docs/VERIFICATION.md §C11 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c11-routes.txt; tests/verification/refresh.sh runs every one.
lypning routes | head -1
lypning run -c 'print(2**100)'; echo $?    # the Python dispatcher: a clean route, then bigint at runtime
lypning routes | grep -E '^  (engine|lypning|kind|bigint)'
lypning conformance --limit 50 --json | python3 -c 'import json,sys; d=json.load(sys.stdin); d.pop("seconds"); print(json.dumps(d, sort_keys=True))' | shasum
lypning routes --clear
LYPNING_ROUTES=0 lypning conformance --limit 50 --json | python3 -c 'import json,sys; d=json.load(sys.stdin); d.pop("seconds"); print(json.dumps(d, sort_keys=True))' | shasum
