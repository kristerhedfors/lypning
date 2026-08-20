#!/bin/sh
# lypning-capture: PreToolUse hook for the Bash tool.
#
# The python-shim (`lypning shim install`) catches every invocation that
# actually reaches an interpreter. This hook catches the COMMAND STRING — which
# is the only place some programs are visible at all: a heredoc body
# (`python3 <<'PY' … PY`), a `uv run` wrapper, or a Write-then-run pattern where
# the program is in a file the shim only ever sees as a path. Both feeds land in
# the same $LYPNING_LOG and are merged by `lypning harvest`.
#
# It NEVER blocks and NEVER decides permission: it prints
# {"continue":true,"suppressOutput":true} and exits 0 on every path, including
# its own failures. Deliberately no permissionDecision field — answering "allow"
# here would bypass the normal permission prompt for every Bash command in the
# session, which is a far bigger change than a capture harness is allowed to
# make. With no hookSpecificOutput at all the dispatcher never assigns a
# permission behaviour and the normal flow runs untouched.
#
# COST. This runs before EVERY Bash tool call in the repo, and almost none of
# them are python, so the no-match path must be nearly free. Upstream measured
# 54 ms per Bash command with the interpreter spawned first and the decision
# made afterwards, against ~3 ms with the decision made first. Everything below
# the payload read is therefore fork-free — the payload is read with the `read`
# builtin (not `cat`), screened with a `case` (not `grep`), and an interpreter
# is spawned ONLY once that screen matches. The screen is deliberately BROADER
# than the PYTHONISH regexes in lypning/capture.py that follow it: an over-match
# costs one wasted spawn, a miss loses a corpus entry forever. Those regexes
# stay the precise filter, so a loose screen can never put noise in the log.
#
# Environment:
#   LYPNING_LOG        log path (default $HOME/.lypning/invocations.jsonl)
#   LYPNING_CAPTURE=0  disable capture (the hook still answers, doing nothing)
ok() {
  printf '{"continue":true,"suppressOutput":true}\n'
  exit 0
}

[ "${LYPNING_CAPTURE:-1}" = "0" ] && ok

# The event JSON arrives on stdin, and the CLI below reads its event from stdin
# too — so the payload is read FIRST and handed back over a pipe. The read loop
# is a shell builtin: no `cat`, no subshell, no fork. Lines are concatenated
# because the payload is JSON, where a newline between tokens is insignificant
# whitespace (and in practice it arrives on one line anyway).
LYPNING_HOOK_PAYLOAD=""
while IFS= read -r _l || [ -n "$_l" ]; do
  LYPNING_HOOK_PAYLOAD="$LYPNING_HOOK_PAYLOAD$_l"
  _l=""
done
[ -n "$LYPNING_HOOK_PAYLOAD" ] || ok

# --- the cheap pre-screen (no forks) -----------------------------------------
# Note the payload is raw JSON, so a tab or newline inside the command arrives
# as the two characters \t or \n — none of these patterns depend on real
# whitespace. `*py*-c*` is the deliberately loose stand-in for capture.py's
# `py\s+-c`; the runners are listed by name rather than screening on " run "
# alone, which `make run …` would trip on nearly every build command.
case "$LYPNING_HOOK_PAYLOAD" in
  *python*) : ;;
  *py*-c*) : ;;
  *"uv run"* | *"pipx run"* | *"poetry run"* | *"hatch run"* | *"pdm run"* | *"rye run"*) : ;;
  *"<<"*)
    # A heredoc only interests us when a python-ish delimiter is in play; the
    # precise filter accepts PY / PYTHON / PYEOF / EOFPY, all containing "PY".
    case "$LYPNING_HOOK_PAYLOAD" in
      *PY*) : ;;
      *) ok ;;
    esac
    ;;
  *) ok ;;
esac

# --- past the screen: one interpreter spawn is now affordable -----------------
# The console script first, `python3 -m lypning` when it is not on PATH. The
# second arm also covers the case where the name `lypning` resolved to the RUST
# CORE instead of the CLI — the core reads `hook` as a script path and exits
# non-zero, while `lypning hook pre-tool-use` returns 0 on every path by design
# (lypning/capture.py). A non-zero exit therefore means it was not the CLI, so
# the fallback cannot double-log.
#
# stdout is redirected to stderr so nothing an interpreter prints — the CLI
# prints this same protocol line itself — can be mistaken for the hook's
# response. The ok() below is the only writer to stdout.
if command -v lypning >/dev/null 2>&1; then
  printf '%s' "$LYPNING_HOOK_PAYLOAD" | lypning hook pre-tool-use >&2 2>&1 && ok
fi
if command -v python3 >/dev/null 2>&1; then
  printf '%s' "$LYPNING_HOOK_PAYLOAD" | python3 -m lypning hook pre-tool-use >&2 2>&1 || true
fi

ok
