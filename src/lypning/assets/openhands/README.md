# lypning under OpenHands

This directory is a plugin for the OpenHands agent SDK. OpenHands loads plugins
ambiently — every local conversation scans its plugin roots, detects this
layout, reads `hooks/hooks.json` and merges those hooks in — so the directory
being here *is* the installation. **No file you own was edited.**

It was written by `lypning install --harness openhands`. Remove it with
`lypning uninstall --harness openhands`, or delete the directory.

## What the hooks do

| event | what it does |
|---|---|
| `SessionStart` | hands the agent the routing paragraph and one line saying which engines are actually built |
| `PostToolUse` on `terminal` | screens the command and, if it could contain python, appends one line to `$LYPNING_LOG` — with the exit code attached |
| `SessionEnd` | folds this session's log into `tests/corpus/sightings/` |

Each exits 0 on every path, including its own failures, and none emits a
`decision` key. OpenHands reads exit code 2 as *block the agent* and honours
`{"decision": "allow"}` in stdout; lypning sends neither, for the same reason
its Claude Code hook sends no `permissionDecision` — bypassing a permission
prompt is far more than a capture harness may do.

## Why `hooks.json` is here and not in `.openhands/`

`HookConfig.load()` is **first-match-wins and not merged**: it reads
`<workspace>/.openhands/hooks.json`, and only if that is absent,
`~/.openhands/hooks.json`. So a `hooks.json` written by lypning would not add
its hooks to yours — it would **hide** yours, or be hidden by them. The format
carries no per-entry ownership marker either, so a later uninstall could not
remove exactly ours.

An uninstall that cannot be exact is one that costs you something you had, so
lypning refuses that route and installs a plugin directory instead. If you want
the hooks in your own `hooks.json` anyway, `hooks.fragment.json` in the lypning
package is the paste-it-yourself copy, and it says which parts must be merged
rather than replaced.

## Three wiring decisions that each fail silently

- **`PostToolUse`, not `PreToolUse`, and never both.** Two hooks on one tool
  write two records per call, and the harvester counts distinct occurrence keys
  — so the `count` that `lypning conformance --plan` steers by would silently
  double. The cost of this choice is real and worth knowing: commands typed and
  then *denied* never reach `PostToolUse`, and those are evidence of what the
  model reaches for. What it buys is the observation — exit code and output,
  from a CPython run you paid for anyway.
- **`SessionEnd` is synchronous.** The SDK's `AsyncProcessManager` terminates
  outstanding async hook processes at session end, so an async harvest is one
  that gets killed partway through writing.
- **The matcher is exactly `terminal`.** That is the registry key the SDK
  derives from the tool class name. `TerminalTool` appears in the SDK's own
  docstring examples and is stale; a matcher spelled that way matches nothing,
  forever, and says nothing about it.

`SessionEnd` is also best-effort by construction — it runs only if the
conversation closes cleanly, so a SIGKILL or a container teardown skips it.
That is why `PostToolUse` appends durably as it goes and `SessionEnd` is a
roll-up rather than the only write.

## Known unknowns

Written against `openhands-sdk` 1.44.1 as of 2026-09-02. Two things were not
established and are worth re-checking against your install:

- The required fields of the plugin manifest were never enumerated. `name` is
  certainly required; `version` and `description` are supplied on the
  assumption they are at worst ignored.
- `additionalContext` on `SessionStart` is documented for `PreToolUse` and read
  generically by the executor, but was never observed firing on `SessionStart`.
  It is harmless if ignored — an unrecognised key beside a `continue` that is
  understood — but if the routing paragraph never reaches your agent, this is
  why, and an `AGENTS.md` line is the fallback.

## Verify it against your install

```
1. lypning status                # shim state, PATH problem, log path and size,
                                 #   which harness wiring is present, which
                                 #   engines are built
2. lypning doctor                # 0 FAIL
3. Run one `python3 -c 'print(1)'` and one heredoc through the agent.
4. Confirm two new lines in $LYPNING_LOG, each carrying "host":"openhands".
5. lypning harvest --export --dry-run --json
   → confirm the session file it would write is named by YOUR session id,
     not unknown.jsonl.
6. Quote the counts those runs print, WITH THE DATE. Never a remembered number.
```

Every hook command here is a PATH-resolved console script. If `lypning` is not
on the PATH the agent server inherits, they run, do nothing, and report success
— `lypning doctor` checks for exactly that.

## Off switches

`LYPNING_CAPTURE=0` disables capture entirely. `LYPNING_HARVEST=0` keeps
capturing but stops `SessionEnd` publishing.
