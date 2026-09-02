# Harnesses — wiring the loop into something other than Claude Code

**What this is.** lypning's self-improvement loop — capture, harvest, gate, step
([`FORKING.md`](FORKING.md)) — needs a host that will tell it what programs the
agent typed. Claude Code was the first such host and for a long time the only
one. This document covers the two others the package now installs into, why
those two, and exactly what each install writes and refuses to write.

**The short version of the contract.** Strip away the per-harness detail and the
loop consumes one thing: an append-only JSONL file at `$LYPNING_LOG`, carrying
two record shapes, plus a periodic `lypning harvest --export`. Everything
downstream — `harvest`, `conformance`, `--plan`, `fuzz`, `bench` — reads the
corpus and needs nothing from any harness at all. That is why an adapter is
small, and why adding a third is not a redesign.

---

## 1. The two, and why

Both are MIT, verified from the LICENSE file rather than from a badge or a
GitHub API guess — several popular harnesses are Apache-2.0, and one is
source-available under a licence whose name contains "MIT" while not being it.

| | opencode | OpenHands SDK |
|---|---|---|
| repo | `sst/opencode` | `OpenHands/software-agent-sdk` |
| licence | MIT | MIT |
| language | TypeScript / Bun | Python |
| berget.ai | **first-party** — Berget Code ships opencode agents | bring-your-own OpenAI-compatible endpoint |
| what we install | one auto-discovered plugin file | one ambiently-discovered plugin directory |
| what we merge into | nothing | nothing |
| can observe every shell command | yes, in-process | yes, via hooks |
| can rewrite a command | yes — deliberately unused | no |
| PATH shim reaches its shell | yes, with a caveat (§3) | yes |

**opencode is the berget.ai answer.** Berget Code's agents *are* opencode
agents, so if that is the product you are using, this is the harness to wire.
Berget's own `@bergetai/opencode-auth` plugin implements only the `auth` and
`config` hooks; lypning's implements neither, so the two coexist as two files in
a plugin directory with nothing to conflict over.

**OpenHands is the best integration surface in the field.** Its hook system is
a near-superset of Claude Code's and is *explicitly* interoperable with it — the
SDK unwraps a `{"hooks": {...}}` wrapper and converts PascalCase keys, with a
source comment saying that is for compatibility with existing Claude Code hook
files. Three properties matter beyond that:

- **`"async": true`** makes a hook a fire-and-forget `Popen` with output to
  `DEVNULL`. That is the strongest possible form of "a capture hook never
  blocks": structurally incapable of it, not merely careful.
- **`PostToolUse` carries the observation** — the command *and* its exit code
  and output. Claude Code's `PreToolUse` yields the program only, and
  `conformance` then has to spawn CPython to learn what it should have printed.
  Here that ground truth arrives attached, from a run the user paid for anyway.
- **Plugins are discovered ambiently**, so the install edits no file the user
  owns. Invariant 7 is satisfied by construction rather than by careful merging.

### What was considered and rejected

- **Apache-2.0, so out on the licence:** goose, Codex CLI, gemini-cli, Cline,
  Open Interpreter, smolagents, Continue, Qwen Code, aider, Roo Code.
- **Crush** (`charmbracelet/crush`) is **FSL-1.1-MIT, not MIT** — source
  available, converting to MIT on a rolling two-year delay per version. Its file
  is `LICENSE.md`, not `LICENSE`, which is why a naive fetch of `…/main/LICENSE`
  404s and it gets mis-reported as unlicensed. Shipping a hook that calls
  lypning would sit comfortably inside its permitted purposes; it fails the MIT
  requirement, not a usability one.
- **gptme** is genuinely MIT and Python, but its `python` tool is a *persistent
  in-process IPython*. It has already removed the spawn cost a different way, so
  lypning has nothing to win on the workload it targets.

### mini-swe-agent: a measurement rig, not an integration

MIT, and one tool — `bash`. No grep tool, no glob tool, no edit tool to absorb
the work, so essentially all of its action stream is shell and `python3 -c` is
its default reach for anything structured. That makes it the cleanest available
instrument for measuring lypning's *ceiling*, and subclassing its local
environment to observe every action is about ten lines.

It ships no adapter here and is mentioned only so nobody has to rediscover it.
If you do run it against this corpus: **running the battery is running an
agent's edit history.** Read invariant 4 in `CLAUDE.md` first and run it behind
the net.

---

## 2. The number this document will not give you

**How many python one-liners either harness actually types is unmeasured.**
That is the number that decides whether lypning is worth wiring into them at
all, and it is not knowable from reading their prompts.

What *is* known cuts both ways. Both designs push away from one-liners:
OpenHands' system prompt says each action is expensive and asks the agent to
combine bash commands and use `sed`/`grep`, and dedicated `grep`, `glob`,
`file_editor` and `apply_patch` tools absorb the read-parse-edit work that in a
barer harness becomes `python3 -c`. opencode steers the same way for file
munging, and — worse for anyone hoping for one number — it selects one of
several system prompts by matching on the model id, and those prompts *disagree
about python*: one tells the agent to use `python3 -c` for one-off computation,
another tells it not to use Python to read or write files. A rate measured
under one model does not transfer to another.

What is left after the dedicated tools take their share is the computational
tail — arithmetic, JSON and YAML reshaping, date math, hashing, ad-hoc parsing
— which is exactly where interpreter startup is the whole cost. Its volume is a
guess.

**So the adapter is the instrument.** Every record now carries a `host` field,
and `lypning harvest --json` reports counts per host. Run it, read the number
off your own sessions, and quote it with its date and its model id — never a
remembered one (invariant 3).

One structural point in lypning's favour, for OpenHands specifically: in its v0
design Python ran in a long-lived Jupyter kernel, where lypning had nothing to
offer. That is gone. In v1 every Python invocation is a fresh `execve` out of
bash, which is precisely the shape lypning is built for.

---

## 3. opencode

```
lypning install --harness opencode [--user] [--dry-run]
```

**Writes exactly one file:** `<config>/plugin/lypning.js`, where `<config>` is
`<project>/.opencode` or, with `--user`, `$OPENCODE_CONFIG_DIR` →
`$XDG_CONFIG_HOME/opencode` → `~/.config/opencode`.

opencode discovers `{plugin,plugins}/*.{ts,js}` by itself, so the file being
there *is* the installation. Ownership is decided by a marker line in the file's
own header, so there is no state of ours to drift; a file of that name without
the marker is **refused, not overwritten**, and `--force` moves it aside rather
than deleting it.

**Deliberately not written:** an `opencode.json` `"plugin"` entry. It is not
needed, and adding one would mean parsing and rewriting a user's JSONC —
comments and all — with a parser this package is not allowed to acquire
(invariant 6).

**A cost we cause and cannot undo, stated in the dry-run:** opencode writes a
`.gitignore`, a `package.json` and a `node_modules/` into every config directory
it scans, on its next start. If our install is what created `.opencode/`, that
is a directory that will fill up, and `lypning uninstall` removes only
`lypning.js`.

### The PATH shim, and why the plugin argues with itself about it

The plugin prepends `$LYPNING_HOME/bin` to the shell's `PATH` via `shell.env`,
which is what lets the second capture feed — the one that proves a program
actually *ran* — work here. That was measured working on the bash tool. It is
also exactly the kind of thing that stops working silently:

- opencode's V2 bash tool passes no environment at all and carries a TODO to add
  plugin env augmentation "once V2 plugin hooks exist". The day that ships,
  `shell.env` stops firing for bash, with no error.
- The `!command` session-shell path uses a login shell whose parent PATH wins.
- Only macOS was measured. On Linux the tool runs `bash -c`, which does not
  source `~/.bashrc` when non-interactive — a different failure mode. Windows is
  unread, and the plugin **refuses to inject there** rather than guess.

So the plugin proves it instead of assuming it: once per instance it resolves
`python3` under the same environment and checks the answer is in the shim
directory. If not, it stops injecting, writes one line to stderr, and appends a
`{"kind":"note"}` record — which `lypning doctor` surfaces, because that is the
only route by which a failure inside Bun reaches the Python side. An unreached
shim and an uninstalled shim have the same symptom, an empty log, and that
symptom has cost this project a day of capture before.

---

## 4. OpenHands

```
lypning install --harness openhands [--user] [--dry-run]
```

**Writes exactly one directory:** `<root>/.openhands/plugins/lypning/`,
containing `.claude-plugin/plugin.json`, `hooks/hooks.json` and a `README.md`.
Every local conversation scans its plugin roots, detects that layout, and merges
the hooks in. Uninstall removes the directory only if its manifest says
`"name": "lypning"`.

**Never writes `.openhands/hooks.json`, at either scope.** `HookConfig.load()`
is **first-match-wins and not merged**: it reads
`<workspace>/.openhands/hooks.json`, and only if that is absent,
`~/.openhands/hooks.json`. A file written by lypning would therefore not add its
hooks to yours — it would **hide** yours, or be hidden by them. The format
carries no per-entry ownership marker either, so a later uninstall could not
remove exactly ours, and an uninstall that cannot be exact is one that costs the
user something they had. `assets/openhands/hooks.fragment.json` is the
paste-it-yourself copy for anyone who wants that route anyway, in the same role
`assets/claude/settings.fragment.json` has always played.

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

### Exit codes are not the Unix convention here

`0` proceeds. **`2` blocks the agent.** Any other non-zero is logged and
proceeds. Stdout is additionally parsed as JSON, where `{"decision": "deny"}`
and `{"continue": false}` each block. So every lypning entry point returns `0`
and only `0`, and none emits a `decision` key — OpenHands would honour
`"allow"`, which is precisely why we do not send it. It is the same power the
Claude Code hook declines to take, for the same reason.

### Berget AI

Not a blessed integration — Berget ships opencode, not OpenHands — but the SDK
is a thin wrapper over litellm, so any OpenAI-compatible endpoint works:

```python
LLM(model="openai/zai-org/GLM-5.2",
    base_url="https://api.berget.ai/v1",
    api_key=SecretStr(os.environ["BERGET_API_KEY"]))
```

The `openai/` prefix is what routes litellm to the OpenAI-compatible driver.
`LLM.load_from_env(prefix="LLM_")` maps `LLM_MODEL`, `LLM_BASE_URL` and
`LLM_API_KEY`. A model id litellm does not recognise falls back to default
context limits rather than erroring, so set them explicitly.

**Model ids move; date every one you quote.** As of **2026-09-02**,
`https://api.berget.ai/v1/models` listed these as tool-capable and stable:
`zai-org/GLM-5.2`, `google/gemma-4-31B-it`,
`mistralai/Mistral-Small-3.2-24B-Instruct-2506`. `moonshotai/Kimi-K3` and
`zai-org/GLM-5.3-Flash` were eval-state, and `openai/gpt-oss-120b` deprecated.
Note that Berget's own CLI reads chat models from a `/v1/models/chat` endpoint,
which is a different and shorter list than plain `/v1/models` — that one also
carries rerankers, speech and embedding models.

---

## 5. Routing is not capture, and only one of them is automatic

This is the part most likely to be got wrong.

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
| opencode | `tool.definition` on the `bash` tool, appended to its description | shipped |
| opencode | `AGENTS.md` or the `instructions` config key | yours to write; we will not write a file you own |
| OpenHands | `SessionStart` hook stdout → `additionalContext` | shipped, **unverified on that event** (§6) |

One asset, `assets/prompt/routing.md`, is used by all three, so the text cannot
drift into three variants. See [`PROMPTING.md`](PROMPTING.md) for what it is
made of and what is and is not measured about it.

**Routing is deliberately not automatic**, in either new harness, even though
both could do it — opencode by rewriting the command, either by pointing the
shell's `python3` at `lypning run`. Four reasons, in order of weight:

1. **Invariant 2.** The exit-90 contract is the one thing here that has only
   ever broken *silently*. A router that is not `engines.dispatch` is a second
   implementation of it that must never diverge.
2. **Invariant 7.** Silently substituting a user's `python3` for a different
   interpreter is the definition of costing them something they had.
3. In opencode a rewrite lands *before* the permission scan, so it changes which
   permission patterns match — a rewrite can flip a command from prompting to
   auto-approved.
4. Density is unmeasured (§2). Routing before measuring is optimising noise.

---

## 6. Verified, refuted, and unverified

Everything below was established on **2026-09-02**, against `opencode-ai`
1.18.26 and `openhands-sdk` 1.44.1. Both move fast. Re-check rather than quote
forward.

**Verified — depended on in code.** opencode: MIT; the plugin loader's
one-export-per-module requirement; `bash` as the exposed tool id; plugin
auto-discovery with no config entry; the config directory order; field-mutation
propagating where container-reassignment is dropped; `shell.env` winning on the
bash tool; `session.idle`; `tool.definition` firing for every tool. OpenHands:
MIT; the six hook event names; the exact `PostToolUse` payload; the
`OPENHANDS_*` environment variables; the exit-code semantics; `"async": true`
behaviour; `HookConfig.load()` being first-match-wins; ambient plugin discovery;
`terminal` as the registry key; and that lypning's existing
`{"continue":true,"suppressOutput":true}` is accepted verbatim.

**Refuted — must not appear in code, and does not.**

- opencode's **`permission.ask` is declared in the plugin type and dispatched
  nowhere.** A capture plugin built on it would do nothing, silently, forever.
- The bash tool is **not** a login shell, so prepending `export PATH=…` to the
  command string is unnecessary and harmful — it would put the rewrite in the
  transcript and change which permission patterns match.
- The tool id is `bash`, not `shell`.
- `installation.update-available` is hyphenated; an underscore never matches.
- opencode's prompts *do* steer python usage, in both directions, per model.

**Unverified — documented, not implemented.** `additionalContext` on OpenHands
`SessionStart` (harmless if ignored, and the `AGENTS.md` route is the fallback);
the required fields of the plugin manifest beyond `name`; `SessionEnd` under
abnormal termination such as SIGKILL or container teardown; the PTY variant of
`shell.env`; Linux and Windows PATH behaviour; and `event.type` being an open
string set — which is why the plugin strict-equals the one type it needs and
never switches exhaustively.

### Verify it against your own install

The gates cannot see most of this. `build --rust` has zero overlap with it,
`conformance` reads exactly the same before and after — which is the point, and
also the danger — and `doctor` only sees what the harness modules report. So:

```
1. lypning status                # shim state per name, PATH problem, log path/size,
                                 #   which harness wiring is present in which scope,
                                 #   which engines are built
2. lypning doctor                # 0 FAIL
3. Run one `python3 -c 'print(1)'` and one heredoc through the harness.
4. Confirm two new lines in $LYPNING_LOG, each carrying "host":"<harness>".
5. lypning harvest --export --dry-run --json
   → confirm the session file it would write is named by YOUR session id,
     not unknown.jsonl.
6. Quote the counts those runs print, WITH THE DATE. Never a remembered number.
```

## 7. Off switches

`LYPNING_CAPTURE=0` disables capture under every harness; the hooks still run
and answer, doing nothing. `LYPNING_HARVEST=0` keeps capturing but stops the
automatic export. Neither needs a file edited, and neither is a reason to
uninstall.
