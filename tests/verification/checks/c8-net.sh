#!/bin/sh
# tool: lypning conformance
# docs/VERIFICATION.md §C8 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c8-net.txt; tests/verification/refresh.sh runs every one.
git status --porcelain | wc -l | tr -d ' '
lypning conformance --limit 200 > /dev/null; echo $?
git status --porcelain | wc -l | tr -d ' '
lypning conformance --mixture both | grep '^skipped'
