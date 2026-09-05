#!/bin/sh
# tool: lypning hook
# docs/VERIFICATION.md §C9 — the CHECK block, verbatim. Its run is
# tests/verification/expected/c9-hook.txt; tests/verification/refresh.sh runs every one.
E='{"tool_name":"Bash","tool_input":{"command":"python3 -c \"print(1)\""},"session_id":"s","cwd":"/tmp","tool_use_id":"t"}'
echo 'not json at all' | lypning hook pre-tool-use; echo $?
echo "$E" | LYPNING_LOG=/dev/null/invocations.jsonl lypning hook pre-tool-use; echo $?
echo "$E" | env -i PATH=/usr/bin:/bin HOME=/tmp sh src/lypning/assets/claude/hooks/lypning-capture.sh; echo $?
echo "$E" | LYPNING_HOME=/tmp/lypning-empty lypning hook pre-tool-use; echo $?
echo "$E" | LYPNING_LOG=/tmp/hook.jsonl lypning hook pre-tool-use; echo $?; tail -1 /tmp/hook.jsonl
echo '{"tool_name":"terminal","tool_input":{"command":"python3 -c 1"},"tool_response":{"exit_code":0},"session_id":"oh"}' | LYPNING_LOG=/tmp/hook.jsonl lypning hook openhands-post-tool-use; tail -1 /tmp/hook.jsonl
echo '{}' | lypning hook opencode-context | head -1
grep -rn '"permissionDecision"' src/lypning/ | wc -l | tr -d ' '
