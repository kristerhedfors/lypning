#!/bin/sh
# tool: lypning conformance
# docs/VERIFICATION.md §C3 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c3-conformance.txt; tests/verification/refresh.sh runs every one.
lypning conformance --mixture both; echo $?
lypning conformance --plan > plan.txt; echo $?; head -3 plan.txt
lypning conformance --engine lypning-mp --limit 5 | grep '^note'; echo $?
