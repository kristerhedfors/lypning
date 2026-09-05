#!/bin/sh
# tool: lypning route
# docs/VERIFICATION.md §C5 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c5-route.txt; tests/verification/refresh.sh runs every one.
lypning route -c 'print(1)'; echo $?
lypning route -c 'import collections; print(collections.Counter("aab"))' | cat -te
lypning route -c 'print(getattr(print, "__name__"))'; lypning -c 'print(getattr(print, "__name__"))'; echo $?
"${LYPNING_HOME:-$HOME/.lypning}/bin/lypning" route --spectrum
