#!/bin/sh
# tool: lypning conformance
# docs/VERIFICATION.md §C4 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c4-dispatchers.txt; tests/verification/refresh.sh runs every one.
lypning conformance --mixture both | grep -E '^(monotone|dispatchers|MISMATCH [0-9])'; echo $?
"${LYPNING_HOME:-$HOME/.lypning}/bin/lypning" route --next --after lypning --kind bigint -c 'print(2**100)'
"${LYPNING_HOME:-$HOME/.lypning}/bin/lypning" route --next --after lypning --kind set-order -c 'print({3, 1, 2})'
"${LYPNING_HOME:-$HOME/.lypning}/bin/lypning" route --json -c 'import collections'
"${LYPNING_HOME:-$HOME/.lypning}/bin/lypning" run -c 'print(2**100)'; echo $?    # the Rust dispatcher
lypning run -c 'print(2**100)'; echo $?                   # the Python dispatcher
