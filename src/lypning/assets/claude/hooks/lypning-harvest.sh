#!/bin/sh
# lypning-capture: Stop hook — persist the session's captures before teardown.
#
# WHY THIS EXISTS. The capture log lives at $HOME/.lypning/invocations.jsonl,
# which is OUTSIDE the repository, and these containers are ephemeral. Capture
# itself worked from the day it shipped — but nothing ever moved the log into
# the tree, so every session's evidence died with its container. Upstream's
# corpus was committed exactly once and had not grown since: 197 programs, all
# first seen inside one 36-minute window.
#
# WHAT IT WRITES, AND WHY NOT THE CORPUS. This hook first folded the log into
# the single corpus file, and that still lost the data: one file that every
# session rewrites conflicts across branches by construction, and the merge was
# never worth it to a session whose PR was about something else. Measured over
# the 19 branches cut after the corpus landed: 2 carried any growth, and neither
# reached main — 17 sessions' python was captured, harvested, and thrown away.
#
# So it publishes tests/corpus/sightings/<session>.jsonl instead: one writer per
# path, an ADDED file rather than a rewritten one, no possible conflict with
# another branch. The corpus is DERIVED from those files by `lypning harvest`,
# which no longer has to be run by every session.
#
# It does NOT commit — a hook that makes commits would fight the session's own
# git work. Staging is left to the repository's own pre-commit hook, which adds
# only that one directory and only to a commit the session was making anyway.
#
# SAFE TO RE-RUN: the export is a union by sighting key, so running it twice
# over the same inputs produces a byte-identical file and does not touch it.
# Firing on every Stop is therefore idempotent, and a session that ran no python
# writes nothing at all.
#
# It NEVER blocks and NEVER fails the session: it prints
# {"continue":true,"suppressOutput":true} and exits 0 on every path, including
# its own failures.
#
# Environment:
#   LYPNING_LOG        log path (default $HOME/.lypning/invocations.jsonl)
#   LYPNING_HOME       state dir (default $HOME/.lypning) — where that log lives
#   LYPNING_CAPTURE=0  disable capture entirely (this hook then does nothing)
#   LYPNING_HARVEST=0  keep capturing, but never harvest automatically
ok() {
  printf '{"continue":true,"suppressOutput":true}\n'
  exit 0
}

[ "${LYPNING_CAPTURE:-1}" = "0" ] && ok
[ "${LYPNING_HARVEST:-1}" = "0" ] && ok

# Stop fires on every turn boundary, so the no-work path must be cheap. Bail
# before spawning anything when there is no log to fold in — the overwhelmingly
# common case in a session that never touched python. The default is spelled the
# same way lypning/paths.py spells it, in two parameter expansions and no forks;
# a divergence here would silently stop harvesting rather than fail loudly.
LOG="${LYPNING_LOG:-${LYPNING_HOME:-$HOME/.lypning}/invocations.jsonl}"
[ -s "$LOG" ] || ok

# Only now is reading stdin worth it. The Stop event carries `cwd`, which is how
# `lypning hook stop` locates the project when $CLAUDE_PROJECT_DIR is unset.
LYPNING_HOOK_PAYLOAD=""
while IFS= read -r _l || [ -n "$_l" ]; do
  LYPNING_HOOK_PAYLOAD="$LYPNING_HOOK_PAYLOAD$_l"
  _l=""
done

# The console script first, `python3 -m lypning` when it is not on PATH — the
# same two-arm dispatch as lypning-capture.sh, and for the same reason: a
# non-zero exit means the name `lypning` was the Rust core rather than the CLI,
# since `lypning hook stop` returns 0 on every path by design.
#
# `hook stop` runs the equivalent of `lypning harvest --export --quiet`: it
# publishes this session's sightings and never writes the corpus. stdout is
# redirected to stderr so hook output can never be mistaken for the hook's JSON
# protocol response, which must be the only thing on stdout.
if command -v lypning >/dev/null 2>&1; then
  printf '%s' "$LYPNING_HOOK_PAYLOAD" | lypning hook stop >&2 2>&1 && ok
fi
if command -v python3 >/dev/null 2>&1; then
  printf '%s' "$LYPNING_HOOK_PAYLOAD" | python3 -m lypning hook stop >&2 2>&1 || true
fi

ok
