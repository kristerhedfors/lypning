#!/bin/sh
# tool: lypning oracle
# docs/VERIFICATION.md §C12 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c12-oracle.txt; tests/verification/refresh.sh runs every one.
lypning status | sed -n '/^oracles/,/^$/p'
lypning doctor | grep lypning-mp; echo $?
lypning conformance --engine lypning-mp --limit 5 | grep '^note'
lypning gate | grep target
lypning oracle | sed -n '1p;$p'; echo $?
lypning oracle --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["engine"], d["built"], d["divergences"], len(d["families"]))'
