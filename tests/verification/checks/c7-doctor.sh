#!/bin/sh
# tool: lypning doctor
# docs/VERIFICATION.md §C7 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c7-doctor.txt; tests/verification/refresh.sh runs every one.
lypning doctor; echo $?
lypning doctor --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["ok"], [c["name"] for c in d["checks"] if c["level"]=="FAIL"])'
