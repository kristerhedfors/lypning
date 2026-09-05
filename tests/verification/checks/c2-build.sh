#!/bin/sh
# tool: lypning build
# docs/VERIFICATION.md §C2 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c2-build.txt; tests/verification/refresh.sh runs every one.
lypning build --rust -v > build.txt; echo $?
grep -E '^(engine|lypning|installed)|unsupported contract' build.txt
lypning build --lib -v | grep -E '^lypning lib|unsupported contract'; echo $?
