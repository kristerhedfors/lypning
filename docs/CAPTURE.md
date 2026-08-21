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
document is what that command wires, and why. The capture half stands alone:
`lypning install --collect-only` wires the two hooks and nothing else, which is
how a repository that never builds an engine still contributes the python its
agents type — see *Collecting from other repositories* below.

## The two feeds

| Feed | Catches | Misses |
| --- | --- | --- |
| `python-shim` on `$PATH` | every process that actually reached an interpreter, including nested ones, subshells, pipelines, Makefiles, `uv run`, and anything a script spawns | programs that never ran (typed then abandoned), and program text held in a `.py` file rather than on the command line |
| `.claude/hooks/lypning-capture.sh` (PreToolUse, Bash) | the full command STRING before it runs — heredocs, `Write`-then-run patterns, commands that fail before exec | anything not issued through the Bash tool |

Both append to the same log, one JSON object per line:

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
                                                                                                      ├─> `lypning harvest` ┐
tests/corpus/sightings/*.jsonl (every session that ever ran) ─────────────────────────────────────────┤                     │
assets/corpus/corpus.jsonl (existing) ────────────────────────────────────────────────────────────────┘                     │
                                                                                                                            ├─> assets/corpus/corpus.jsonl
another repo, installed --collect-only ─> ITS published sightings ─┐                                                        │
$LYPNING_SOURCES, --from PATH ─────────────────────────────────────┴─> ~/.lypning/sources/<slug>/ ─> `lypning collect` ─────┘
                                                                       (found by SHAPE, never executed)
```

## Why per-session files, and not just the corpus

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
| `stdin_sample` | `null` unless known — the shim must not read stdin, so only hand-curated (`manual`) records ever carry one, and never an imported one (see *Collecting from other repositories*) |

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

## Collecting from other repositories

The corpus is a recording of what agents actually type, and this repository is
one workspace among many. A one-liner typed in somebody else's repo is exactly
the same kind of evidence as a one-liner typed here — it was just published
somewhere else. Nothing about that evidence requires an engine to produce it.

So collection is separable from the engine, and the separation is the point: a
repository can run the two capture hooks, publish per-session files, and never
build a binary, never install a shim, never route a single program through
anything. A repository that would have to compile a Rust core before it could
contribute programs will not contribute programs. This one asks it to merge two
hook entries into a file it already has.

### The three steps

**1. In the other repository, install the collecting half.**

```sh
lypning install --collect-only --sightings .lypning/programs --dry-run
lypning install --collect-only --sightings .lypning/programs
```

`--collect-only` keeps the PreToolUse and Stop hooks — the pair that records and
publishes — and drops the shim and the skill. Those two are engine wiring, and
this repository is not routing anything: nothing `--collect-only` installs needs
an engine, so `lypning status` there saying `not built` is the expected state
and not a problem to fix.

`--sightings DIR` chooses where the Stop hook publishes. The default,
`tests/corpus/sightings`, is right for this repository and wrong for nearly
every other one — a repo with no `tests/` does not want to grow one on our
account, and a directory a project did not choose is a directory it will delete.
The value is carried in the hook command itself, as an `LYPNING_SIGHTINGS='<dir>'`
prefix, rather than in a config file of ours that the repository would then own
forever. It is single-quoted (a path with a space in it stays one path), an
absolute path is used as given, and a relative one resolves against the project
root, so the wiring still means the same thing after the checkout moves.

Everything else is the ordinary install and unchanged by either flag:
`settings.json` is merged and never overwritten, backed up once before the first
modification, `--dry-run` prints the exact unified diff and writes nothing, and
`lypning uninstall` removes precisely what was added, prefix and all.

**2. That repository commits what its sessions publish.**

Same files, same shape, same rules as here: one writer per session so two
branches cannot conflict, an *added* file rather than a rewritten one, and every
record redacted, seed-guarded and size-capped before it is written. **No hook
runs `git`**, there either — whether those files get committed is that
repository's decision, exactly as it is this one's.

**3. Here, import them.**

```sh
lypning collect --list          # the registry, and what is already cached
lypning collect --dry-run       # everything computed, no corpus written
lypning collect                 # fetch, discover, read, fold
lypning collect --from ../sibling-checkout
lypning collect --offline       # never run git; import only what is cached
lypning collect --json
```

Exit 0 when at least one source resolved, 1 when none did. "Resolved nothing new"
is a success: an upstream that has not grown since the last import is the normal
case, not a failure.

### The registry

Sources live in `src/lypning/assets/corpus/sources.json`, beside the corpus and
shipped in the wheel with it, so a `pip` user's registry is the same list a
checkout has. Each entry is a name, a location and a note:

```json
{
  "sources": [
    {
      "name": "some-repo",
      "location": "https://github.com/example/some-repo",
      "note": "why this repository's programs are worth having"
    }
  ]
}
```

A location is either a git URL — shallow-cloned into
`$LYPNING_HOME/sources/<slug>`, never into the package tree, because a clone
under `site-packages` is a clone `pip uninstall` has never heard of — or a path
on this machine, which is read where it lies and never copied. The slug carries
a short hash of the full location, so two forks with the same repository name do
not land in one directory.

Adding a source is a one-line diff a reviewer can read. That is deliberate:
importing from a repository is a decision about whose programs the engines get
built against, and it should look like a decision.

`$LYPNING_SOURCES` adds locations without editing the file —
`os.pathsep`-separated, the same two forms, which is what a checkout sitting
next to this one wants. `--from LOCATION` (repeatable) replaces the registry
entirely for one run.

### Discovery is by shape, not by name

Nothing in the registry says *where* in a repository the programs are. The
importer walks the tree, considers every `.jsonl` that either sits under a
directory called `sightings` or is named `*corpus*.jsonl`, and then confirms by
reading it: the first few non-blank lines must parse as JSON objects carrying a
non-empty `program` string.

Matching on a directory name instead would mean every contributing repository
had to be told what to call its evidence, and the first one that disagreed would
be silently invisible — an import that reports zero files is indistinguishable
from an upstream that captured nothing. Shape has no such failure mode. It is
also what lets `--sightings` exist at all: a repository free to publish under any
name it likes is only free if nothing downstream is matching on that name.

The walk prunes `.git`, `node_modules`, `target`, `.venv`, `venv`,
`__pycache__`, `dist` and `build`, and is bounded in both file count and bytes,
because a source is a repository somebody else controls the size of.

### What is never run, and what is never written

**Nothing from a fetched tree is executed.** An imported program is corpus data —
text for the engines and CPython to be pointed at, later, deliberately, behind
the net described in `CLAUDE.md` — not a script to run now. `git` itself is
invoked with `-c core.hooksPath=/dev/null` so a fetched repository's own hooks
cannot run either, and with `GIT_TERMINAL_PROMPT=0` so a URL that needs a
credential fails immediately instead of hanging on a prompt in someone's CI.

**An import writes the corpus and only the corpus.** It never publishes into
`tests/corpus/sightings`, and that refusal is about provenance rather than
tidiness. Those files are one-writer-per-session evidence of what ran in *this*
repository; someone else's programs written into them would make every claim
they support a lie — the frequency table that ranks what to implement next, the
`first_seen` history, the session namespacing that keeps two branches from
conflicting. A program imported from elsewhere is not a record of anything that
happened in a session here.

**The gate and the redaction run on the way in, through the same code path as a
local harvest.** Imported sightings go through `harvest._clean` — the gate a
local export puts in front of the fold — and then to
`harvest.fold_into_corpus`, which is the only place in this package that writes
the corpus. So an import gets the same redaction before the hash, the same size
guard, the same three rejections a local export applies, and the same
max-not-sum counting — a program two sources both published lands once, with the
earliest `first_seen` and the strongest provenance either declared. There is no
second implementation of any of it, which is the only reason the privacy rules
below can be stated once and be true of both paths.

Two of those rejections are cheap hygiene: a program that normalises to nothing,
and a `pass`-only program, which is what a session runs to find out whether an
interpreter *exists* and therefore says nothing about the python it runs. The
third — already in the corpus — is the feedback-loop guard, and it is the reason
the gate has to sit in front of the fold rather than being left to it. A seed
record is an expectation somebody typed by hand, keyed by a slug rather than by
content, so a fold left to itself does not recognise one and re-files it as an
*observation*; the frequency table that ranks what to implement next then reads
its own wishes back as evidence. `harvest.known_keys` exists because that
happened once here, and importing is a second door into the same room.

**An import carries no `stdin_sample`, and that is a provenance argument rather
than a filter.** The field means "what a session piped into this program
*here*" — a claim about a local session, which an import is not. There is no
version of it this repository can verify, so there is nothing honest to keep and
the field is dropped outright rather than redacted. `source`, `count` and
`first_seen` are treated the same way and each has its own reason in
`collect.py`: a record can only ever lower its claim on the way in, never raise
it, and a `first_seen` that could not be one is recorded as unknown rather than
as now.

Every tool prints the number of programs it loaded and the number it added. That
is the number to quote — from that run, with its date. Import is a second way
the corpus grows, so a remembered size is now stale for two reasons instead of
one.

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
| `LYPNING_SIGHTINGS` | where the Stop hook publishes (default `<project>/tests/corpus/sightings`) — absolute as given, relative against the project root; `lypning install --sightings DIR` writes it into the hook command |
| `LYPNING_HOME` | state directory (default `~/.lypning`) — the shim's bin dir, the log, the build trees |
| `LYPNING_TRANSCRIPTS` | transcript root for the harvest (default `~/.claude/projects`) |
| `LYPNING_SOURCES` | extra `lypning collect` locations, `os.pathsep`-separated — appended to the registry, never replacing it |

## What the shim does and does not promise

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
