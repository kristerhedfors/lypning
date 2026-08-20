#!/bin/sh
# lypning: SessionStart hook — say which engines exist, and refresh the shim.
#
# Two jobs, both of which have to happen once per container rather than once per
# install:
#
#   1. **Refresh the capture shim.** `.claude/` is committed and survives; the
#      shim lives in $LYPNING_HOME/bin (~/.lypning/bin), which does not. A shim
#      installed in a previous session is not on this session's PATH, so without
#      this the shim feed silently stops the first time the container is
#      recycled. `lypning shim install` is idempotent and REFUSES to overwrite
#      an interpreter that is not one of ours, so re-running it costs nothing
#      and cannot swap a python out from under the session. LYPNING_CAPTURE=0
#      turns the whole harness off, including this.
#
#   2. **Report the engine build state in one line.** An agent that does not know
#      the Rust core is missing will quote a routing decision that never
#      happened — everything falls through to CPython when nothing is built, and
#      that looks exactly like a working mixture with disappointing numbers. The
#      line is injected as SessionStart additionalContext, so it reaches the
#      model without adding a line to the transcript.
#
# The lypning-mp tier requires a build with network access and is ABSENT by
# default. That is a status line, never an error: the report names it as not
# built and the session carries on.
#
# It never fails the session: it prints {"continue":true,"suppressOutput":true}
# and exits 0 on every path, including its own failures.
#
# Environment:
#   LYPNING_CAPTURE=0  disable the capture harness (no shim refresh; still reports)
#   LYPNING_HOME       state dir holding the built binaries (default $HOME/.lypning)
ok() {
  printf '{"continue":true,"suppressOutput":true}\n'
  exit 0
}

# SessionStart fires once per session, so one spawn is affordable here in a way
# it is not in lypning-capture.sh. Nothing below reads stdin: the SessionStart
# event carries nothing this hook needs.

if [ "${LYPNING_CAPTURE:-1}" != "0" ]; then
  # Same two-arm dispatch as the other hooks: the console script first, and
  # `python3 -m lypning` when the name `lypning` was the Rust core rather than
  # the CLI (the core reads `shim` as a script path and exits non-zero).
  if command -v lypning >/dev/null 2>&1; then
    lypning shim install >&2 2>&1 || true
  elif command -v python3 >/dev/null 2>&1; then
    python3 -m lypning shim install >&2 2>&1 || true
  fi
fi

command -v python3 >/dev/null 2>&1 || ok

# The report is composed and SERIALISED by python, not by the shell: engine
# paths are attacker-adjacent strings (they come from $PATH and $LYPNING_BIN)
# and hand-rolling JSON escaping in sh is how a hook starts emitting a payload
# the dispatcher cannot parse. If anything at all goes wrong the substitution
# comes back empty and ok() answers instead.
report=$(python3 - 2>/dev/null <<'LYPNING_REPORT_PY'
import json

def respond(text):
    print(json.dumps({
        "continue": True,
        "suppressOutput": True,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        },
    }))

try:
    import lypning
    from lypning import engines
except Exception:
    # Installed hooks outliving an uninstalled package is a normal state, not a
    # broken one. Say so and stop; do not guess at paths.
    respond("lypning: hooks are installed but the package is not importable "
            "(`pip install lypning`). Capture and routing are inert this session.")
    raise SystemExit(0)

try:
    have = engines.available()
    tiers = [e for e in engines.ENGINE_ORDER if e != engines.CPYTHON]
    built = [e for e in tiers if have.get(e)]
    missing = [e for e in tiers if not have.get(e)]

    parts = ["built: " + (", ".join(built) if built else "none")]
    if missing:
        parts.append("not built: " + ", ".join(missing))
    real = have.get(engines.CPYTHON)
    parts.append("cpython: " + (str(real) if real else "not found"))
    line = "lypning %s — %s." % (lypning.__version__, "; ".join(parts))

    if not built:
        line += (" Nothing to route to, so every program falls through to"
                 " CPython and any speed claim is meaningless: run `lypning"
                 " build` first.")
    elif engines.MICROPYTHON in missing:
        line += (" The lypning-mp tier needs a build with network access"
                 " (`lypning build --micropython`) and is absent by default;"
                 " the mixture works without it, one tier shallower.")
    respond(line)
except Exception:
    respond("lypning: engine state could not be determined this session.")
LYPNING_REPORT_PY
)

case "$report" in
  '{'*) printf '%s\n' "$report" ; exit 0 ;;
esac

ok
