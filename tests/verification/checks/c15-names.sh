#!/bin/sh
# tool: grep
# docs/VERIFICATION.md §C15 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c15-names.txt; tests/verification/refresh.sh runs every one.
names=$(sed -n '/^## 8\. Credit/,/^## 9\./p' README.md | grep -o '\*\*`[a-z]*`\*\*' | tr -d '*`')
for n in $names; do grep -rlw --exclude-dir=.git --exclude-dir=target --exclude-dir=_site --exclude-dir=__pycache__ "$n" .; done | sort -u
grep -rnE 't[i]er [12]\b|t[i]er-[12]\b|middle t[i]er|second t[i]er|MicroPython t[i]er|the MicroPython var[i]ant|three interp[r]eters|three t[i]ers|both t[i]ers|two subset t[i]ers' README.md CLAUDE.md docs/*.md site/index.md src/lypning/cli.py | grep -vE '^docs/(BENCH-LEDGER|HILLCLIMB|PAPER|RESEARCH)\.md' | wc -l | tr -d ' '
python -m pytest -q tests/test_engines.py::test_no_engine_name_is_spelled_by_hand_outside_engines_py tests/test_docs.py -k 'upstream_names or node_id or tier_number or spelled_by_hand'; echo $?
