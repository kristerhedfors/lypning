#!/bin/sh
# tool: venv lypning build
# docs/VERIFICATION.md §C13 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c13-wheel.txt; tests/verification/refresh.sh runs every one.
python3 -m venv /tmp/lypning-venv && /tmp/lypning-venv/bin/pip install -q 'setuptools>=77'
/tmp/lypning-venv/bin/pip install -q --no-build-isolation .; echo $?
cd /tmp && export LYPNING_HOME=/tmp/lypning-wheel
/tmp/lypning-venv/bin/lypning build --rust | grep -E '^lypning'; echo $?
/tmp/lypning-venv/bin/lypning status | sed -n '/^library/,/^$/p'; ls /tmp/lypning-wheel/build
/tmp/lypning-venv/bin/lypning lib; echo $?
/tmp/lypning-venv/bin/lypning install --dry-run --no-shim --project /tmp/lypning-wheel-proj | grep -E '^[+.~] (write|merge|skip)|"command"'
