#!/bin/sh
# tool: lypning lib
# docs/VERIFICATION.md §C14 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c14-lib.txt; tests/verification/refresh.sh runs every one.
lypning lib --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(sorted(d)); print(d["abi"], d["cli_abi"], d["error"])'
python3 - <<'PY'
from lypning import embed
lib = embed.Library()
o = lib.run("import subprocess")
print(lib.engine_name(), o.status_name, o.exit_code, o.stdout, o.stderr, o.committed, o.fall_onward)
o = lib.run("import sys; sys.exit(90)"); print(o.status_name, o.exit_code, o.fall_onward)
o = lib.run("while True: pass", step_limit=1000); print(o.status_name, o.exit_code, o.kind, o.fall_onward)
PY
lypning conformance --engine library --limit 20 | grep -E '^library|^MISMATCH'
LYPNING_LIB=/no/such.so lypning lib --json; echo $?
