#!/bin/sh
# tool: lypning gate
# docs/VERIFICATION.md §C6 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c6-gate.txt; tests/verification/refresh.sh runs every one.
lypning gate; echo $?
lypning gate "${LYPNING_HOME:-$HOME/.lypning}/bin/lypning"-l | grep -E '^  ok   size|^PASS|^FAIL'; echo $?
lypning gate /no/such/binary; echo $?
