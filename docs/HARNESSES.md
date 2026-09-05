# Harnesses — wiring the loop into opencode and the OpenHands SDK

An adapter appends `bash_command` records (and, where it can, `exit` and
`note` — `CAPTURE.md`, *The raw record*) to `$LYPNING_LOG` and runs `lypning
harvest --export` at idle or session end; nothing downstream reads the harness.
Detail ships as `assets/{opencode,openhands}/README.md`; checks: §C9, §C10 of
`docs/VERIFICATION.md`.

## 1. The two, and why

| | opencode | OpenHands SDK |
|---|---|---|
| repo, licence | `sst/opencode`, MIT (`LICENSE`, read 2026-09-02); **first-party** for berget.ai — Berget Code ships opencode agents, and its `@bergetai/opencode-auth` plugin implements only `auth` and `config`, so the two files coexist | `OpenHands/software-agent-sdk`, MIT (same); bring-your-own OpenAI-compatible endpoint |
| what we install, and merge into | one auto-discovered plugin file (TypeScript / Bun); nothing | one ambiently-discovered plugin directory (Python); nothing |
| the hook feed fires | `tool.execute.before`, in-process in Bun, before the command; the PATH shim reaches its shell with the self-check in §3; it could rewrite the command and deliberately does not (§5) | `PostToolUse`, after it — exit code and output arrive attached; denied commands are lost; the shim reaches its shell; no rewrite is possible |

Not adapted: goose, Codex CLI, gemini-cli, Cline, Open Interpreter,
smolagents, Continue, Qwen Code, aider, Roo Code (Apache-2.0); Crush
(FSL-1.1-MIT, not MIT); gptme (MIT; a persistent in-process IPython, no spawn
to remove); mini-swe-agent (MIT, one `bash` tool; `CLAUDE.md` invariant 4).

## 2. What is measured

Unmeasured: how many python one-liners either harness types — their prompts
push toward dedicated tools. `harvest.host_counts()` counts them on your own
sessions (library-only: `lypning harvest --json` prints no per-host breakdown;
only hook-feed records carry `host`); quote it with date and model. Their
records are never model-attributed (`CAPTURE.md`, the `unattributed` row).

## 3. opencode

```
lypning install --harness opencode [--user] [--dry-run]
# → `+ write <config>/plugin/lypning.js  — new` on a clean tree; exit 0
```

**Writes exactly one file:** `<config>/plugin/lypning.js`, where `<config>` is
`<project>/.opencode` or, with `--user`, `$OPENCODE_CONFIG_DIR` →
`$XDG_CONFIG_HOME/opencode` → `~/.config/opencode`.

opencode discovers `{plugin,plugins}/*.{ts,js}` by itself, so the file being
there is the installation; ownership is a marker line in the file's own header
(`harness.opencode.PLUGIN_MARKER`). A file of that name without the marker is
skipped — `. skip … NOT a lypning plugin — left alone; move it aside yourself
(--force moves only a foreign python3 shim, not this file)`: `install.apply`
threads `--force` to the shim only (`lypning doctor`'s WARN on that state
promises otherwise and is wrong — issue #43). The dry-run states the cost it
cannot undo: opencode writes `.gitignore`, `package.json` and `node_modules/`
into every config directory it scans; uninstall removes only `lypning.js`.

The plugin prepends `$LYPNING_HOME/bin` to the tool's `PATH` via `shell.env`
and checks once per instance that `python3` resolves into the shim directory;
if not, it stops injecting and appends a `{"kind":"note"}` record, which
`lypning doctor` shows as `WARN harness note` for 7 days
(`cli._recent_capture_note`). `LYPNING_HARVEST=0` is inert here: the plugin
runs `lypning harvest --export --quiet` on `session.idle` and `dispose`
unconditionally, and `cli.cmd_harvest` never consults
`capture.harvest_enabled` (issue #44).

## 4. OpenHands

```
lypning install --harness openhands [--user] [--dry-run]
# → three `+ write` lines under <root>/.openhands/plugins/lypning/; exit 0
```

**Writes exactly one directory:** `<root>/.openhands/plugins/lypning/`,
containing `.claude-plugin/plugin.json`, `hooks/hooks.json` and a `README.md`.
Uninstall keeps a directory whose manifest is not ours: `not ours — left alone`.

**Never writes `.openhands/hooks.json`, at either scope.** `HookConfig.load()`
is **first-match-wins and not merged**: it reads
`<workspace>/.openhands/hooks.json`, and only if that is absent,
`~/.openhands/hooks.json`. A file written by lypning would therefore not add its
hooks to yours — it would hide yours, or be hidden by them — and the format has
no owner marker, so an uninstall could not be exact.
`assets/openhands/hooks.fragment.json` is the paste-it-yourself copy.

### Three wiring decisions, each of which fails silently

- **`PostToolUse`, not `PreToolUse`, and never both.** Two hooks on one tool
  write two records per call; the harvester counts distinct occurrence keys, so
  `count` would silently double — and `conformance --plan` steers by `count`.
  **The cost is real:** commands typed and then *denied* never reach
  `PostToolUse`, and under Claude Code those are deliberately captured, because
  a refused command is still evidence of what the model reaches for. That
  evidence is given up here in exchange for the exit code and output.
- **`SessionEnd` is synchronous.** The SDK terminates outstanding async hook
  processes at session end, so an async harvest is one that gets killed partway
  through writing. It is also best-effort — it runs only if the conversation
  closes cleanly — which is why `PostToolUse` appends durably as it goes and
  `SessionEnd` is a roll-up rather than the only write. It can fire
  mid-conversation too, so the export has to be idempotent; it is (a union by
  key).
- **The matcher is exactly `terminal`.** That is the registry key the SDK
  derives from the tool class name. `TerminalTool` appears in the SDK's own
  docstring examples and is stale; a matcher spelled that way matches nothing,
  forever, and says nothing about it.

`0` proceeds. **`2` blocks the agent.** Any other non-zero is logged and
proceeds. Stdout is additionally parsed as JSON, where `{"decision": "deny"}`
and `{"continue": false}` each block. So every lypning entry point returns `0`
and only `0`, and none emits a `decision` key — OpenHands would honour
`"allow"` — the same power the Claude Code hook declines, for the same reason.

Berget AI: the SDK wraps litellm, so `LLM(model="openai/<id>",
base_url="https://api.berget.ai/v1", api_key=...)` works, as does
`LLM.load_from_env(prefix="LLM_")`. Model ids move; date what you quote.

## 5. Routing is not capture, and only one of them is automatic

**Capture needs nothing from the agent.** The shim logs and then `exec`s the
real CPython; its whole invariant is that the wrapped run is byte-identical to
an unwrapped one. It therefore delivers **no speedup at all**.

**Speed requires the agent to type `lypning`**, because `lypning run` is the
dispatcher that owns the exit-90 fall-through, the stdin replay and the commit
barrier. So the agent has to be told, and each harness has a different place to
tell it:

| harness | surface | state |
|---|---|---|
| Claude Code | `SessionStart` → `hookSpecificOutput.additionalContext` | shipped, working |
| opencode | `tool.definition` on the `bash` tool, appended to its description | shipped; the plugin carries the paragraph itself and never calls `lypning hook opencode-context`, which exists for a host wanting the text one exec later |
| opencode | `AGENTS.md` or the `instructions` config key | yours to write; we will not write a file you own |
| OpenHands | `SessionStart` hook stdout → `additionalContext` | shipped, **unverified on that event** (§6) |

One asset, `assets/prompt/routing.md`, is used by all three
([`PROMPTING.md`](PROMPTING.md)). Routing is not automatic in either harness
though both could do it — opencode by rewriting the command, either one by
pointing `python3` at `lypning run`
(`tests/test_harness_opencode.py::test_the_plugin_ships_no_router`): a second
router is a second exit-90 implementation (invariant 2), a substituted
`python3` costs the user something they had (invariant 7), an opencode rewrite
lands before the permission scan, and density is unmeasured (§2).

## 6. Verified, refuted, unverified

Established 2026-09-02 against `opencode-ai` 1.18.26 and `openhands-sdk`
1.44.1. The claim → status → test map, the unverified row included, is the
`harnesses` section of `tests/verification/claims.json`;
`tests/test_verification.py::test_every_claim_map_entry_resolves` fails when a
named test is gone; a row naming no test is that day's manual check, to be
re-checked, not quoted forward. Refuted — must not appear in code:

- opencode's **`permission.ask` is declared in the plugin type and dispatched
  nowhere.** A capture plugin built on it would do nothing, silently, forever.
- The bash tool is **not** a login shell, so prepending `export PATH=…` to the
  command string is unnecessary and harmful — it would put the rewrite in the
  transcript and change which permission patterns match.
- The tool id is `bash`, not `shell`.
- `installation.update-available` is hyphenated; an underscore never matches.
- opencode's prompts *do* steer python usage, in both directions, per model.

## 7. Verify on your install

```bash
lypning doctor | grep harness   # → `OK harness opencode wired in project scope`, or `NOTE harness opencode not wired — \`lypning install --harness opencode\``; `status --json` has the same under "harnesses"
lypning install --harness opencode --dry-run; echo $?   # → the one file it would write, or the `skip` line in §3; 0
echo '{"tool_name":"terminal","tool_input":{"command":"python3 -c 1"},"tool_response":{"exit_code":0},"session_id":"oh"}' | LYPNING_LOG=/tmp/h.jsonl lypning hook openhands-post-tool-use; tail -1 /tmp/h.jsonl   # → {"continue":true,"suppressOutput":true}, then one record with "host":"openhands","exit_code":0
lypning harvest --export --json   # → "files": [{"path": ".../tests/corpus/sightings/<YOUR session>.jsonl", …}], not unknown.jsonl
```

Then run one `python3 -c 'print(1)'` and one heredoc through the live harness
and confirm two new `$LYPNING_LOG` lines carrying `"host":"<harness>"`.

## 8. Off switches

`LYPNING_CAPTURE=0` disables capture under every harness; the hooks still run
and answer, doing nothing. `LYPNING_HARVEST=0` stops the automatic export under
Claude Code and OpenHands (`capture.harvest_enabled`), not opencode (§3, #44).
Hook answers and install actions, byte-exact: `docs/VERIFICATION.md` §C9, §C10.
