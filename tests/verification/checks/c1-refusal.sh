#!/bin/sh
# tool: lypning
# docs/VERIFICATION.md §C1 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c1-refusal.txt; tests/verification/refresh.sh runs every one.
lypning -c 'import subprocess'; echo $?
lypning -c 'import subprocess' 2>/dev/null | wc -c | tr -d ' '
"${LYPNING_HOME:-$HOME/.lypning}/bin/lypning"-l -c 'import subprocess'; echo $?
lypning -c 'print(1); import subprocess' 2>/dev/null | wc -c | tr -d ' '   # the commit barrier
lypning -c 'import sys; sys.exit(90)'; echo $?      # 90 without the line: the program's own
lypning -c 'x = 1/0'; echo $?                       # not a refusal: returned unchanged
grep -ho 'unsupported("[a-z-]*' src/lypning/assets/rust/src/*.rs | sort -u | wc -l | tr -d ' '
