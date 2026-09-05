# Capture — the two feeds, the harvest, and the privacy rules

Every Rust variant in `engines.SPECTRUM` is built against one recording: the
python coding agents actually typed, captured from live sessions and merged
into `src/lypning/assets/corpus/corpus.jsonl`. Nothing here guesses at what
python "should" support. `lypning install` wires everything below
(`README.md` §3); `--harness` wires the same two feeds into opencode or the
OpenHands SDK, and [HARNESSES.md](HARNESSES.md) owns what differs there. The
checks are `docs/VERIFICATION.md` §C9 (the hooks) and §C10 (install, shim).

## The two feeds

| Feed | Catches | Misses |
| --- | --- | --- |
| `python-shim` on `$PATH` | every process that actually reached an interpreter, including nested ones, subshells, pipelines, Makefiles, `uv run`, and anything a script spawns | programs that never ran (typed then abandoned), and program text held in a `.py` file rather than on the command line |
| the command-string hook | the full command STRING — heredocs, `Write`-then-run patterns, and (under Claude Code) commands that fail before exec | anything not issued through the harness's shell tool |

The hook feed is spelled once per harness (`HARNESSES.md` §1): `PreToolUse`
fires before the command, OpenHands' `PostToolUse` after it, opencode's
`tool.execute.before` in-process in Bun. Both feeds watch for a process, so a
host that links `liblypning` (`EMBEDDING.md` §4) is invisible to both and must
append the record itself.

Both feeds append one JSON object per line to `$LYPNING_LOG` (default
`~/.lypning/invocations.jsonl`; `capture.append_record`). The Stop hook
publishes that log into the tree: `lypning harvest --export` writes
`tests/corpus/sightings/<session>.jsonl` — one file per session, one writer per
path, so branches cannot conflict. The hooks never run `git`; staging that
directory is yours. Keys are session-namespaced — `shim:<session>#<line>`,
`hook:<session>#<line>#<idx>`, `transcript:<file>#<block_id>#<idx>`
(`harvest._raws_from_log`, `harvest.scan_transcripts`); with no session in
`capture.SESSION_ENV` the tag is `invocations` and the file `unknown.jsonl`
(`harvest.session_filename`). `corpus.jsonl` is derived from the sightings by
`lypning harvest`; no session has to run it. The shared-file design this
replaced is in `CHANGELOG.md` (2026-08-15).

## The raw record

What the feeds write, before any harvest — `capture.record_command`, the awk
block in `assets/shim/python-shim`, and `assets/opencode/lypning.js` from Bun:

| kind | writer | fields |
| --- | --- | --- |
| `bash_command` | hook (`capture.record_command`) | `kind`, `ts`, `session`, `cwd`, `tool`, `command`, `description`, `transcript`, `host`; Claude adds `tool_use_id` (and `agent_id`, `agent_type` from a subagent); OpenHands adds `exit_code`; opencode adds `run` (its `callID`) |
| `python_invocation` | shim, pre-exec | `kind`, `ts`, `session`, `cwd`, `shim`, `exe`, `pid`, `run`, `argv`, `program`, `module`, `script`, `argv_tail`, `stdin_pipe`, `stdin_kind`, `exit_code`, `wall_ms` — the last two `null` |
| `exit` | shim under `LYPNING_CAPTURE_EXIT=1`; opencode `tool.execute.after` | the shim shape with `exit_code` and `wall_ms` filled; from opencode `kind`, `ts`, `session`, `host`, `run`, `exit_code` |
| `note` | opencode plugin | `kind`, `ts`, `session`, `host`, `detail` — written once when its PATH self-check fails; `lypning doctor` shows it for 7 days (`cli._recent_capture_note`) |

`host` is `claude`, `openhands` or `opencode`; a shim record carries none, and
a missing `host` is read as Claude (`harvest._joinable`). The harvest reads
`kind`, `session`, `ts`, and `command` or `program`/`argv_tail`; `host` decides
only whether the model join may open a transcript; `exit` and `note` never
reach the corpus. `harvest.host_counts()` counts records by `host` and has no
CLI surface: `lypning harvest --json` prints no per-host counts.

## The hook contract

`lypning install` merges this `PreToolUse` entry into `.claude/settings.json`
— append only, backed up once, `--dry-run` prints the diff (`README.md` §3):

```json
"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
  "command": "sh \"$CLAUDE_PROJECT_DIR/.claude/hooks/lypning-capture.sh\""}]}]
```

The hook prints `{"continue":true,"suppressOutput":true}` on every path,
including its own failures, and exits 0. It carries **no** `permissionDecision`
field on purpose: answering `allow` here would auto-approve every Bash command
in the session, which is a much bigger change than a capture harness is allowed
to make.

The script reads the payload with the `read` builtin, screens it with a `case`
statement, and spawns `lypning hook pre-tool-use` only on a match, so the
no-match path — almost every Bash call — forks nothing.

The shell screen is deliberately broader than the regexes in `capture.py` that
follow it (an over-match costs one wasted spawn; a miss loses a corpus entry
forever), and those regexes remain the precise filter, so a loose screen can
never put noise in the log. Runners are screened by name (`uv run`, `pipx
run`, …) rather than on `" run "`, which every `make run …` would otherwise
trip.

No timing is quoted here: the two this document and the script header carried
disagreed, and neither named a run. Measure it yourself, quote the date, and
read the result as a ratio — the host is shared:

```bash
for C in 'ls -la' 'python3 -c 1'; do E='{"tool_name":"Bash","tool_input":{"command":"'"$C"'"}}'; time (for i in $(seq 20); do echo "$E" | LYPNING_LOG=/tmp/c.jsonl sh .claude/hooks/lypning-capture.sh >/dev/null; done); done   # no-match, then match
time (for i in $(seq 30); do python3 -c 1; done); time (for i in $(seq 30); do ~/.lypning/bin/python3 -c 1; done)   # bare, then through the shim
```

## Harvest

```bash
lypning harvest                 # every sightings file + the live log → the corpus; exit 0
lypning harvest --export        # publish THIS session's sightings; writes no corpus
lypning harvest --dry-run       # report, write nothing
lypning harvest --transcripts   # also scan Claude Code transcripts ($LYPNING_TRANSCRIPTS)
lypning harvest --json          # → {"gathered","added","redactions","skipped","files","mode","corpus"}
```

`cli.cmd_harvest` exits 0 on every path it reaches; a bad option is argparse's
2. `--dry-run --json` prints `{"mode":"dry-run","sightings":N,"corpus":<path>}`
and that `corpus` key is the only place the write location is printed:
`paths.corpus_write_file()` is the asset file in a checkout and
`$LYPNING_HOME/corpus.jsonl` in a wheel, while `lypning status` prints the
asset path `paths.CORPUS_FILE` either way. `--export` is a union by sighting
key — idempotent at every turn boundary; a session that ran no python writes
nothing. Records are redacted, seed-guarded and size-capped first, and the
report's `dropped` row itemises the rest: `empty`, `trivial`, `known` (in the
seed corpus already; a rising count means something is running the corpus back
into the log), `unredactable` (a credential-shaped residue no pattern cleaned),
`oversized` (`harvest.MAX_PROGRAM_BYTES`) — `harvest._clean`.

One record per DISTINCT program:

```json
{"id":"py-0a1b2c3d4e5f","program":"print(1)","argv_tail":[],"source":"shim","first_seen":"2026-08-13T21:00:00.000Z","count":12,"stdin_sample":null}
```

| Field | Meaning |
| --- | --- |
| `id` | `py-` + 12 hex of sha256 over the NORMALIZED program text |
| `program` | the actual python source, after redaction (see below) |
| `argv_tail` | argv after the program (`python -c PROG a b` → `["a","b"]`) |
| `source` | strongest provenance seen: `shim` > `hook` > `transcript` > `manual` > `seed` (`harvest.SOURCE_RANK`) |
| `first_seen` | earliest timestamp across all sightings |
| `count` | number of distinct sightings, never decreasing |
| `stdin_sample` | `null` unless known — the shim must not read stdin, so only hand-curated (`manual`) records ever carry one |
| `models` | which model issued it, as `{"<model-id>": n}` — **absent** when nothing could be attributed |

Normalization for the dedup hash unifies line endings, strips per-line trailing
whitespace, and drops surrounding blank lines. It does **not** touch
indentation: in Python that is syntax, and two programs indented differently
are two programs.

Skipped: invocations with no inline source (`python script.py`, `python -m
json.tool`, `python --version`), empty programs, and anything over 64 KiB.

Re-running is safe: counts come from the session-namespaced keys above, so a
second harvest over the same inputs writes a byte-identical file; records the
inputs stopped mentioning are carried over untouched.

## Which model issued it

`models` is a histogram over the same occurrences `count` counts, and it is a
SUBSET of them: `sum(models.values()) <= count`, and the difference is the
unattributed hole. An occurrence that could not be joined to a model
contributes to `count` and to nothing else. There is deliberately no `unknown`
bucket in the record — a hole named in a committed file is a hole the next
reader treats as data — so the size of it is reported rather than stored, as the
`unattributed` row of `lypning corpus --stats`. The key is omitted entirely when
the histogram is empty, which is what lets records captured before any of this
existed keep the bytes they have.

```bash
lypning corpus --stats --model claude-fable-5-1   # → the slice and the whole it came from, with an `unattributed` row
# → under `by model`: a row per model id, then `unattributed <count> <share>` — the whole corpus, 100.0%, on a tree with no transcript to join (2026-09-05)
python3 -c 'import json,sys; assert all(sum(r.get("models",{}).values())<=r["count"] for r in map(json.loads,open(sys.argv[1]))); print("ok")' src/lypning/assets/corpus/corpus.jsonl   # → ok
```

The hook writes down the payload's `tool_use_id` (and `agent_id`/`agent_type`
for a subagent's call) and reads no transcript; the join runs in `harvest.py`.
A **hook** occurrence joins on the id exactly, against the main transcript and
its `subagents/**/agent-*.jsonl` tree (`harvest._model_index`; an id is unique
across every transcript). A **transcript** occurrence has the model on the line
it parses. A **shim** occurrence has no id and joins on time — the latest
`assistant` record at or before its stamp — after both stamps are normalised to
one width (`harvest._canonical_ts`): the transcript writes milliseconds, the
shim whole seconds where `date` lacks `%3N`, and raw `…:24Z` sorts after
`…:24.900Z`. `message.model == "<synthetic>"` is no model.

Attribution joins Claude Code transcripts only: opencode and OpenHands hook
payloads carry no model and write no transcript, so their records
(`host != "claude"`) skip the join and stay unattributed. Known gap: a shim
record has no `host`, so another harness run inside a Claude session's shell is
time-joined to the Claude model (`harvest._joinable`).

Transcripts are append-only JSONL, so the index resumes from a stored byte
offset. `$LYPNING_HOME/model-index.json` is a cache, never a source of truth: a
mismatch on `(dev, inode)`, `size >= offset`, cache version, or the sha256 of
the last 4096 consumed bytes (`harvest._tail_digest`; tail, not head, because a
restored copy reproduces the opening records) re-reads the file from byte zero.
It is never committed and deleting it costs one cold harvest; the byte counts
of a cold and a warm harvest, dated 2026-09-02, are in the `harvest.py` module
comment. Force any of them — delete the file, `cp` the transcript onto a new
inode, `truncate` it below the stored offset — and the next `lypning harvest
--dry-run` re-reads from byte zero and prints the same `sightings` count.

The two merges are **not** the same function, and this is the trap:
`harvest._combine` and `fold_into_corpus` take the per-model **max**, because
both sides count the same occurrence keys and a sum would double the record on
every export; `corpus._combine` **sums**, because a corpus merge is summation,
protected from double counting by its identical-record collapse. Copying one
call site onto the other leaves the counts plausible and slowly wrong.

## Privacy

**The log is not safe to publish. The corpus is committed.** A captured `-c`
program is arbitrary text from this repo's working sessions — file contents,
paths, occasionally a token pasted into a one-liner.

* `~/.lypning/invocations.jsonl` and `~/.lypning/model-index.json` stay local.
  The published `tests/corpus/sightings/*.jsonl` files ARE committed, and go
  through the same redaction and seed guard as the corpus.
* **A live credential is redacted by VALUE, not only by shape.** The patterns
  below catch a secret that announces itself with a prefix; the most dangerous
  ones here do not (a Cloudflare API token is 53 characters of unprefixed
  base62). The harvester runs inside the container that holds them, so it matches
  the literal value of every credential-named env var — exact, so it cannot
  false-positive on anything but the secret — and writes
  `[REDACTED env <NAME> <n> chars]`. Naming the variable rather than the value is
  deliberate: it says which credential to rotate without restating it.
* `harvest.SECRET_PATTERNS` is the canonical shape list in this package and is
  not repeated here. A match becomes `[REDACTED <prefix> <n> chars]` BEFORE
  the hash, so the `id` is a function of the text that actually gets written
  and re-harvesting the raw log cannot fork a record; the marker cannot
  re-match, so redaction is idempotent.
* Redaction is a backstop, not a promise. Read the diff on a corpus refresh —
  a secret in a shape nobody has a pattern for still reads as ordinary program
  text.

## Environment

The fallback directory in the first row is named `lypning-mp-<uid>` for
historical reasons and has nothing to do with `lypning-mp`, the oracle —
measured, never routed to (`capture._fallback_log`).

| Variable | Effect |
| --- | --- |
| `LYPNING_LOG` | log path (default `~/.lypning/invocations.jsonl`; falls back to `$TMPDIR/lypning-mp-<uid>/` when `$HOME` is unwritable, then gives up silently) |
| `LYPNING_CAPTURE=0` | disable capture entirely; the shim still execs python |
| `LYPNING_CAPTURE_EXIT=1` | shim waits for the child instead of exec-ing it, adding an `{"kind":"exit"}` record with `exit_code` and `wall_ms` |
| `LYPNING_HARVEST=0` | keep capturing; stop the Stop hook publishing sightings |
| `LYPNING_SESSION_ID` | the session tag, ours and harness-independent. Read first, ahead of whatever id the host harness exports; both feeds consult the same list, so one session's records cannot split across two tags |
| `LYPNING_HOME` | state directory (default `~/.lypning`) — the shim's bin dir, the log, the build trees, the transcript index cache |
| `LYPNING_TRANSCRIPTS` | transcript root for the harvest (default `~/.claude/projects`) — the third feed's root; the model join instead follows the transcript path each hook record already carries |

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

Logging never fails the command: `$HOME` unwritable falls back to `$TMPDIR`,
and if that fails too the shim logs nothing and runs python anyway. The
interpreter is found by scanning `$PATH` minus the shim's own directory,
skipping any file with the shim's marker, so it never execs into itself.
`lypning shim status` is loud when the shim is installed but shadowed (that and
"not installed" share a symptom, an empty log): `docs/VERIFICATION.md` §C10.
Test the fallback: `LYPNING_LOG=/nonexistent/x.jsonl TMPDIR=/tmp/fb lypning hook
pre-tool-use` writes `/tmp/fb/lypning-mp-<uid>/invocations.jsonl`; with
`TMPDIR=/nonexistent` too it writes nothing and answers the protocol line, 0.

## Tests

The claim → test map for this document is the `capture` section of
`tests/verification/claims.json` (export idempotency, redaction, normalisation,
both joins, max-versus-sum, every stale-cache path, the hook protocol);
`tests/test_verification.py::test_every_claim_map_entry_resolves` fails when a
named test is gone.

## Verify

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"python3 -c 1"},"session_id":"x","tool_use_id":"t1"}' | LYPNING_LOG=/tmp/c.jsonl lypning hook pre-tool-use; echo $?; tail -1 /tmp/c.jsonl
# → {"continue":true,"suppressOutput":true} · 0 · one bash_command record with "host":"claude","tool_use_id":"t1"
PATH=~/.lypning/bin:$PATH LYPNING_LOG=/tmp/c.jsonl python3 -c 1; tail -1 /tmp/c.jsonl
# → one python_invocation record with "program":"1","exit_code":null,"wall_ms":null
lypning harvest --dry-run --json; echo $?   # → {"mode":"dry-run","sightings":N,"corpus":"<write path>"} · 0
```

The fixtures with the run of record's bytes are `docs/VERIFICATION.md` §C9.
