# Capture — the two feeds, the harvest, and the privacy rules

lypning's two subset tiers only have to run the python that actually gets run —
the one-liners a coding agent reaches for a hundred times a day. This is how
those invocations are captured from live sessions and merged into the committed
corpus, `src/lypning/assets/corpus/corpus.jsonl`, which is the target both
tiers are built against.

Nothing here guesses at what python "should" support. The corpus is a
recording.

Everything below is wired by one command — `lypning install`, which merges the
hooks into `.claude/settings.json` and installs the shim (`README.md` §3). This
document is what that command wires, and why. `--harness` wires the same two
feeds into opencode or the OpenHands SDK instead;
[HARNESSES.md](HARNESSES.md) is what differs there.

## The two feeds

| Feed | Catches | Misses |
| --- | --- | --- |
| `python-shim` on `$PATH` | every process that actually reached an interpreter, including nested ones, subshells, pipelines, Makefiles, `uv run`, and anything a script spawns | programs that never ran (typed then abandoned), and program text held in a `.py` file rather than on the command line |
| the command-string hook | the full command STRING — heredocs, `Write`-then-run patterns, and (under Claude Code) commands that fail before exec | anything not issued through the harness's shell tool |

The second feed is spelled once per harness, and the differences matter:

| Host | Where | Fires |
| --- | --- | --- |
| `claude` | `.claude/hooks/lypning-capture.sh` (PreToolUse, `Bash`) | **before** the command runs — so it catches programs typed and then denied, which are still evidence of what the model reaches for |
| `openhands` | `hooks/hooks.json` in an ambiently-loaded plugin (PostToolUse, `terminal`) | **after** — so denied commands are lost, and the exit code and output are gained |
| `opencode` | `plugin/lypning.js` (`tool.execute.before`, `bash`) | **before**, in-process in Bun, so there is no fork per tool call at all |

Every record carries a `host` field naming which of those wrote it, and
`lypning harvest --json` counts by it. That field is the only instrument this
package has for the question it refuses to answer from priors: how many python
one-liners a given harness actually types. It is deliberately not part of the
corpus path — the harvester does not read it and a sighting does not carry it —
because a measurement that changed what got published would be one nobody could
trust.

**Both feeds watch for a PROCESS, and that is their shared blind spot.** A host
that links `liblypning` and calls `lypning_run()` — any host in the table in
[EMBEDDING.md](EMBEDDING.md) §4 — spawns nothing, so neither feed sees it: a
host can run ten thousand programs and the corpus will not grow by one. The
Python host is the worst case, because it *is* a python process, so the shim
logs the driver script and none of the programs it ran, and one sighting where
there should be hundreds reads as a working feed. Until the C ABI grows a
capture hook, an embedding host that wants its programs captured has to append
the record itself; [PROMPTING.md](PROMPTING.md) §7 has a working example of the
shape, one per host, and the count each produced.

Both feeds append to the same log, one JSON object per line:

```
$LYPNING_LOG              # default ~/.lypning/invocations.jsonl
```

That log is outside the repo, and these containers are ephemeral. So a third
step publishes it into the tree: `.claude/hooks/lypning-harvest.sh` (Stop) writes
`tests/corpus/sightings/<session>.jsonl` in the project. **The hooks never run
`git`** — a hook that made commits would fight the session's own git work — so
staging that directory is yours to do, or not. Those files are the durable
evidence; the corpus is derived from them.

```
python-shim ─┐
hook ────────┼─> ~/.lypning/invocations.jsonl ─┐
             │                                ├─> --export ─> tests/corpus/sightings/<session>.jsonl ─┐
~/.claude/projects/**/*.jsonl ────────────────┘                                                       │
                                                                                                      ├─> `lypning harvest` ─> assets/corpus/corpus.jsonl
tests/corpus/sightings/*.jsonl (every session that ever ran) ─────────────────────────────────────────┤
assets/corpus/corpus.jsonl (existing) ────────────────────────────────────────────────────────────────┘
```

## Rationale for per-session files

The Stop hook originally folded the log straight into the corpus
and **that still lost the data**. One shared file that every session rewrites
conflicts across branches by construction, and merging it was never worth it to
a session whose PR was about something else. Measured 2026-08-14, over the 19
branches cut since the corpus first landed:

* 2 carried any harvested growth, and neither had reached `main`;
* `corpus.jsonl` had exactly **one** commit, with every `first_seen` inside one
  36-minute window;
* so **17 sessions'** python was captured, harvested, and thrown away.

A per-session path has exactly one writer, so no two branches can conflict, and
an unrelated PR carries an **added** file rather than a rewritten one. Two
consequences worth knowing:

* A sighting key had to stop being a bare log line number — line 1 means
  something different in every container. Keys are namespaced by session, which
  is also what lets the fold read a session's live log and its own published
  file without counting the same invocation twice.
* `corpus.jsonl` is now DERIVED. Run `lypning harvest` to regenerate it from
  the accumulated sightings; no individual session has to.

## Install

```sh
lypning shim install            # copies the shim to ~/.lypning/bin/{python,python3}
lypning shim status             # what is installed, is it current, is it on PATH
lypning shim uninstall
```

`lypning install` does this as one of its three pieces; the subcommand above is
the shim on its own.

Idempotent — re-running refreshes the installed copy and says whether anything
changed. It **refuses** to overwrite a target that is not one of our shims
(that would silently swap one interpreter for another); pass `--force` to move
the incumbent aside as `<name>.lypning-backup`, which `lypning shim uninstall`
restores.

The shim only ever runs if its directory is first on `$PATH`:

```sh
export PATH="$HOME/.lypning/bin:$PATH"
```

`lypning shim status` says so loudly when the shim is installed but shadowed,
because that failure and "not installed at all" have the same symptom — an
empty log. `--bin-dir DIR` installs somewhere else; `$LYPNING_HOME` moves the
whole state directory.

### The hook

`lypning install` merges the block below into `.claude/settings.json` — append
only, unrelated keys and hooks untouched, backed up first, and `--dry-run`
prints the exact diff before anything is written. To add it by hand instead,
this is the `PreToolUse` entry:

```json
"PreToolUse": [
  {
    "matcher": "Bash",
    "hooks": [
      {
        "type": "command",
        "command": "sh \"$CLAUDE_PROJECT_DIR/.claude/hooks/lypning-capture.sh\""
      }
    ]
  }
]
```

In place, alongside what is already configured:

```json
{
  "hooks": {
    "SessionStart": [ /* … unchanged … */ ],
    "UserPromptSubmit": [ /* … unchanged … */ ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "sh \"$CLAUDE_PROJECT_DIR/.claude/hooks/lypning-capture.sh\""
          }
        ]
      }
    ]
  }
}
```

The hook prints `{"continue":true,"suppressOutput":true}` on every path,
including its own failures, and exits 0. It carries **no** `permissionDecision`
field on purpose: answering `allow` here would auto-approve every Bash command
in the session, which is a much bigger change than a capture harness is allowed
to make.

Both claims were checked against the installed CLI (Claude Code 2.1.42), not
assumed. Its hook-output schema declares `continue` (boolean, optional,
"Whether Claude should continue after hook (default: true)"), `suppressOutput`
(boolean, optional, "Hide stdout from transcript"), `stopReason` (used only
when `continue` is false), and `hookSpecificOutput`. For PreToolUse,
`hookSpecificOutput.permissionDecision` is `.optional()`, and the dispatcher
reads it as `if (out.hookSpecificOutput?.hookEventName === "PreToolUse" &&
out.hookSpecificOutput.permissionDecision) { … }` — with no
`hookSpecificOutput` at all, `permissionBehavior` is never assigned and the
normal permission flow runs untouched. `{"continue":true,"suppressOutput":true}`
is therefore exactly "observe and get out of the way".

### Cost

This runs before every Bash tool call in the repo, and hardly any of them are
python, so the no-match path has to be nearly free. Measured here, 20
iterations per case:

| Payload | Per call |
| --- | --- |
| non-python (`ls -la`) | **2.7 ms** |
| non-python that looks busy (`make build && git status`) | **3.4 ms** |
| python (`python3 -c "print(1+1)"`) | **66 ms** |
| *(rejected design: spawn the interpreter first, decide after)* | *73 ms on every command* |

The difference is entirely in where the decision happens. The hook reads stdin
with the `read` builtin, screens the payload with a `case` statement, and
spawns `lypning hook pre-tool-use` only when that screen matches — so the
interpreter start is paid only by commands that can actually produce a corpus
entry.

The shell screen is deliberately broader than the regexes in `capture.py` that
follow it (an over-match costs one wasted spawn; a miss loses a corpus entry
forever), and those regexes remain the precise filter, so a loose screen can
never put noise in the log. Runners are screened by name (`uv run`, `pipx
run`, …) rather than on `" run "`, which every `make run …` would otherwise
trip.

## Harvest

```sh
lypning harvest                 # derive the corpus from every sightings file + the live log
lypning harvest --export        # publish THIS session's sightings, write no corpus
lypning harvest --dry-run       # report, write nothing
lypning harvest --transcripts   # also scan Claude Code transcripts
lypning harvest --json          # the same report, machine-readable
```

The corpus it writes is `src/lypning/assets/corpus/corpus.jsonl` in a checkout
and `$LYPNING_HOME/corpus.jsonl` in a wheel install, where the assets are
read-only; `lypning status` prints which. The transcript root is
`$LYPNING_TRANSCRIPTS`, else `~/.claude/projects`.

`--export` is what the Stop hook runs. It writes only
`tests/corpus/sightings/<session>.jsonl` — never the corpus — as a union by
sighting key, so it is idempotent at every turn boundary and a session that ran
no python writes nothing. Records are redacted, seed-guarded and size-capped
before they are written, because these files are committed.

One record per DISTINCT program:

```json
{"id":"py-0a1b2c3d4e5f","program":"print(1)","argv_tail":[],"source":"shim","first_seen":"2026-08-13T21:00:00.000Z","count":12,"stdin_sample":null}
```

| Field | Meaning |
| --- | --- |
| `id` | `py-` + 12 hex of sha256 over the NORMALIZED program text |
| `program` | the actual python source, after redaction (see below) |
| `argv_tail` | argv after the program (`python -c PROG a b` → `["a","b"]`) |
| `source` | strongest provenance seen: `shim` > `hook` > `transcript` > `manual` |
| `first_seen` | earliest timestamp across all sightings |
| `count` | number of distinct sightings, never decreasing |
| `stdin_sample` | `null` unless known — the shim must not read stdin, so only hand-curated (`manual`) records ever carry one |

Normalization for the dedup hash unifies line endings, strips per-line trailing
whitespace, and drops surrounding blank lines. It does **not** touch
indentation: in Python that is syntax, and two programs indented differently
are two programs.

Re-running is safe. Counts come from stable sighting keys (log line number,
transcript `tool_use` id) rather than being incremented, so a second harvest
over the same inputs produces a byte-identical file and does not even touch it.
Records the current inputs no longer mention — a rotated log, a hand-written
`manual` entry — are carried over untouched.

Skipped: invocations with no inline source (`python script.py`, `python -m
json.tool`, `python --version`), empty programs, and anything over 64 KiB.

`source: "hook"` extends the shim/transcript/manual set: it is the
PreToolUse-captured variant of the transcript class (the same command string,
seen earlier and more reliably), and it is ranked above `transcript` for
exactly that reason.

## Privacy

**The log is not safe to publish. The corpus is committed.** A captured `-c`
program is arbitrary text from this repo's working sessions — file contents,
paths, occasionally a token pasted into a one-liner.

* `~/.lypning/invocations.jsonl` stays local. It is not in the repo and must not
  be committed, pasted into an issue, or attached to a PR. The published
  `tests/corpus/sightings/*.jsonl` files ARE committed, and go through the same
  redaction and seed guard as the corpus before they are written.
* **A live credential is redacted by VALUE, not only by shape.** The patterns
  below catch a secret that announces itself with a prefix; the most dangerous
  ones here do not (a Cloudflare API token is 53 characters of unprefixed
  base62). The harvester runs inside the container that holds them, so it matches
  the literal value of every credential-named env var — exact, so it cannot
  false-positive on anything but the secret — and writes
  `[REDACTED env <NAME> <n> chars]`. Naming the variable rather than the value is
  deliberate: it says which credential to rotate without restating it.
* `harvest.py` redacts before writing. Every program, argv tail, and stdin
  sample is matched against the repo's canonical credential patterns — the same
  set as `SECRET_PATTERNS` (OpenAI `sk-`, Berget `sk_ber_`, Groq `gsk_`,
  AWS `AKIA`, GitHub `ghp_`/`github_pat_`, Google `AIza`, Slack `xox*`, PEM
  private-key blocks) — and matches become `[REDACTED <prefix> <n> chars]`.
  Redaction happens BEFORE the hash, so the `id` is a function of the text that
  actually gets written, and re-harvesting the raw log cannot fork a record.
  The marker cannot re-match, so redaction is idempotent.
* `SECRET_PATTERNS` in `harvest.py` is the canonical list in this package.
* Redaction is a backstop, not a promise. Read the diff on a corpus refresh —
  a secret in a shape nobody has a pattern for still reads as ordinary program
  text.
* `LYPNING_CAPTURE=0` disables both the shim's logging and the hook.

## Environment

| Variable | Effect |
| --- | --- |
| `LYPNING_LOG` | log path (default `~/.lypning/invocations.jsonl`; falls back to `$TMPDIR/lypning-mp-<uid>/` when `$HOME` is unwritable, then gives up silently) |
| `LYPNING_CAPTURE=0` | disable capture entirely; the shim still execs python |
| `LYPNING_CAPTURE_EXIT=1` | shim waits for the child instead of exec-ing it, adding an `{"kind":"exit"}` record with `exit_code` and `wall_ms` |
| `LYPNING_HARVEST=0` | keep capturing; stop the Stop hook publishing sightings |
| `LYPNING_SESSION_ID` | the session tag, ours and harness-independent. Read first, ahead of whatever id the host harness exports; both feeds consult the same list, so one session's records cannot split across two tags |
| `LYPNING_HOME` | state directory (default `~/.lypning`) — the shim's bin dir, the log, the build trees |
| `LYPNING_TRANSCRIPTS` | transcript root for the harvest (default `~/.claude/projects`) |

## Shim guarantees

The wrapped run is transparent: stdin/stdout/stderr are inherited untouched
(nothing in the shim reads stdin), and because it `exec`s, the interpreter's
exit code and signal behaviour are its own — a `SIGTERM`ed run is reported as a
signal death, not as exit 143 from a shell.

**Exit code and wall time are therefore NOT captured by default.** An `exec`
leaves no at-exit path, so the record is written pre-exec with
`"exit_code":null,"wall_ms":null`. `LYPNING_CAPTURE_EXIT=1` buys them back by
waiting for the child, at the price of one extra process in the middle: a
signal that kills the interpreter then surfaces as exit `128+n` rather than
propagating as a signal death. Correctness of the wrapped run outranks
completeness of the log, so that mode is opt-in.

Logging never fails the command. If `$HOME` is unwritable it falls back to
`$TMPDIR`; if that fails too it logs nothing and runs python anyway. The
interpreter is resolved by scanning `$PATH` minus the shim's own directory and
skipping any file carrying the shim's marker, so it can never exec into itself
however many copies are installed.

It costs about **9 ms** per invocation (measured here, 30 iterations:
13.8 ms bare → 22.5 ms through the shim). The fork budget is what that buys:
one `date`, one `awk` to encode the record, and the append. The PATH scan
resolves absolute entries as text and reads candidates with the shell's own
`read` builtin, so it adds no processes at all — an earlier version that ran
`cd`+`pwd` per PATH directory and called `dirname` cost 15.5 ms instead. This
is a dev-environment capture harness: `LYPNING_CAPTURE=0` removes the logging
cost, and `lypning shim uninstall` removes the shim.

## Tests

```sh
python3 -m pytest tests/test_harvest.py
```

Covers dedup, normalization, redaction, command extraction (quoting, heredocs,
runners), re-run idempotency, and the durability path — per-session export,
key namespacing, and the no-double-count property of the fold.

`pytest` picks this file up from `tests/`.
