# lypning under opencode

`lypning.js` in this directory is a capture plugin. opencode discovers
`{plugin,plugins}/*.{ts,js}` under its config directories by itself, so the file
being here *is* the installation — there is no `opencode.json` entry, and
lypning deliberately does not add one (that would mean rewriting your JSONC,
comments and all, with a parser this package is not allowed to depend on).

It was written by `lypning install --harness opencode`. Remove it with
`lypning uninstall --harness opencode`, or just delete the file.

## What it does

- **Observes.** On `tool.execute.before` for the `bash` tool it screens the
  command and, if it could contain a python program, appends one line to
  `$LYPNING_LOG`. On `tool.execute.after` it appends the exit code.
- **Puts the shim on PATH.** `shell.env` prepends `$LYPNING_HOME/bin`, so the
  `python3` a command resolves is the logging shim.
- **Tells the agent to route.** `tool.definition` appends the routing paragraph
  to the `bash` tool's description — its own baked-in copy, held to the
  shipped prompt by `tests/test_harness_opencode.py`; it does not call
  `lypning hook opencode-context`.
- **Publishes.** On `session.idle` and on `dispose` it runs
  `lypning harvest --export --quiet`.

It never rewrites a command, never denies one, and never throws — in opencode,
throwing from `tool.execute.before` *is* the deny mechanism, so an unhandled
exception in a capture plugin would refuse your command.

## The one thing that fails silently

A `python3` shim only helps if commands actually resolve to it, and that is not
guaranteed everywhere: opencode's V2 bash tool passes no environment at all and
carries a TODO to add plugin env augmentation later, the `!command` session
shell is a login shell whose own PATH wins, and only macOS was measured.

So the plugin **proves it instead of assuming it**. Once per instance it
resolves `python3` under the same environment and checks the result is in the
shim directory. If it is not, it stops injecting PATH, writes one line to
stderr, and appends a `{"kind":"note"}` record to the log — which
`lypning doctor` surfaces, because that is the only route by which a failure
inside Bun reaches the Python side.

An unreached shim and an uninstalled shim have the same symptom — an empty log —
which is exactly why it is made loud.

## Berget AI

Berget Code ships opencode agents, so this is the harness to wire if that is
what you are using. Berget's own `@bergetai/opencode-auth` plugin implements
only the `auth` and `config` hooks; this one implements none of those, so the
two coexist without conflict — they are simply two files in the plugin
directory.

## Verify it against your install

Do not assume any of the above still holds; opencode moves fast. As of
2026-09-02 this was written against `opencode-ai` 1.18.26.

```
1. lypning status                # shim state, PATH problem, log path and size,
                                 #   which harness wiring is present, which
                                 #   engines are built
2. lypning doctor                # 0 FAIL
3. Run one `python3 -c 'print(1)'` and one heredoc through the agent.
4. Confirm two new lines in $LYPNING_LOG, each carrying "host":"opencode".
5. lypning harvest --export --json
   → `files` names the session file it wrote: YOUR session id, not
     unknown.jsonl. (`--dry-run --json` prints counts only, no file name.)
6. Quote the counts those runs print, WITH THE DATE. Never a remembered number.
```

## Off switches

`LYPNING_CAPTURE=0` disables capture entirely (the hooks still run and do
nothing). `LYPNING_HARVEST=0` is honoured by the Claude Code Stop hook and the
OpenHands `SessionEnd` hook, and NOT by this plugin: it runs
`lypning harvest --export --quiet` on idle and dispose regardless
(github.com/kristerhedfors/lypning/issues/44). To stop the export under
opencode, remove the plugin. A foreign `lypning.js` at the plugin's path is
left alone and reported; `--force` does not move it aside (issues/43).
