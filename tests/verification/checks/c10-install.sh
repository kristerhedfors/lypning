#!/bin/sh
# tool: lypning install
# docs/VERIFICATION.md §C10 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c10-install.txt; tests/verification/refresh.sh runs every one.
P=$(mktemp -d) && git -C "$P" init -q && mkdir "$P/.claude"
printf '{"permissions":{"allow":["Bash(ls:*)"]},"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"echo mine"}]}]}}\n' > "$P/.claude/settings.json"
before=$(find "$P/.claude" -type f | sort | xargs shasum | shasum)
lypning install --dry-run --project "$P" | grep -E '^~|changes'; echo $?
[ "$before" = "$(find "$P/.claude" -type f | sort | xargs shasum | shasum)" ] && echo unchanged
lypning install --project "$P" | grep -E '^b|^~'; echo $?
grep -c 'echo mine' "$P/.claude/settings.json"; grep -c 'Bash(ls:' "$P/.claude/settings.json"
lypning install --project "$P" | grep settings.json; echo $?
lypning uninstall --project "$P" | grep -E 'settings.json|NOT deleted'; echo $?
grep -c lypning "$P/.claude/settings.json"; ls "$P/.claude"
lypning shim status | grep '^PATH'
