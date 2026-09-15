# Demand — the wish line, bundled helpers, and the data plan

> **Status (2026-09-15): a plan.** Nothing in this document is implemented.
> §6 is the order to build it in, one mechanism per step, and every number
> here is from the run named beside it — re-run before relying on one
> (`CLAUDE.md` invariant 3). What is decided is the shape: what the model is
> asked to type, what it is never asked to type, what each flow records, and
> which decision reads it.

Every instrument this project has for *what to build next* measures a
refusal. `conformance --plan` ranks the refusals in the corpus by what they
cost; `lypning routes` records the runtime refusals a static route provably
cannot see; the training loop's repair queue starts from "correct, with a
valid L refusal" (`nemotron/ORCHESTRATION.md`). A program that stayed inside
the subset is invisible to all three, and the prompting study says that is
where most of the demand went: prompting moved coverage from 66.3% to 88.5%
(2026-08-23, `docs/PROMPTING.md` §1), and every one of those 22 points is a
program whose author wanted `Counter`, `re.findall` or `pathlib` and wrote
around it. **The better the prompt, the less `--plan` sees.** The cookbook's
rewrite table (`docs/COOKBOOK.md`) is that hidden demand, reconstructed by
hand from what the refusals looked like before the prompt existed.

So far the subset has grown by two moves only: chop a piece off Python, or
implement everything but. This plan adds a third: let the model say what it
wanted, count it, and bundle the narrow thing it asked for — a function, not
a module — with the binary. Two mechanisms carry it, and one rule bounds both.

- **The wish line** (§1): a fixed sigil and free natural language in a
  comment, which CPython, the lexer and the router all ignore and the harvest
  reads. It records demand whether or not the program was refused.
- **Bundled helpers** (§2): a small vocabulary that is higher level than the
  stdlib, shipped as pure Python in the wheel (the reference) and as Rust on
  a variant (the fast path). Its use is counted the same way; it grows from
  wish lines and shrinks from the census.
- **The rule**: *nothing we entice the model to type may lack a
  CPython-runnable reference.* CPython running the wheel stays the oracle
  for every helper, so `MISMATCH` keeps its meaning (invariant 1) and the
  fall-through keeps working (invariant 2). A syntax of our own is admitted
  only under the same rule (§2d), and not before the census asks for it.

§3 is the data plan: for every flow the project has now, the record it
writes, the signal it carries today, what this plan adds to it, and which
decision reads it.

## 0. The run these numbers come from

```
lypning corpus --stats · 2026-09-15 · 17496b3 · Linux x86_64 · corpus 9064 programs
  hook 7235 (79.8%) · transcript 992 (10.9%) · shim 674 (7.4%) · seed 163 (1.8%)
  by model: claude-opus-5 53.6% · claude-fable-5-1 4.5% · unattributed 41.6%
  top imports the core does not serve: re 1500 · pathlib 1399 · collections 1013
lypning conformance --plan · the same run · 197 distinct blockers over 6316 programs
 ->cpy  blocks  blocker
   368     368  module-attr: sys.path          (import machinery: a refusal that must stay)
   129     129  module: import lypning         (this repository's own sessions — the mirror)
    97     426  module: import collections     (329 of the 426 route to lypning-l)
    90      90  module: import subprocess      (out of scope by design)
    80     496  module: import re              (416 route to lypning-l)
    23     803  module: import pathlib         (780 route to lypning-l)
    13      13  module: import yaml            (third-party: a wish, never a fake — §2b)
```

Read the two columns together: `collections`, `re` and `pathlib` block
1,725 programs on the core and route 200 of them to CPython, because the
router sends the rest to `lypning-l`. The rows that cost are the ones no capability can close —
`sys.path`, `subprocess`, this repository importing itself — and the long
tail of one-off modules. That is the shape §0 predicts for an instrument
that only sees refusals: what is left is what should stay refused, and the
demand that was prompted into the subset is not on the list at all.

The corpus is 92.4% multi-line on this run, and `transcript` is one program
in nine: part of any build order read from it is a mirror of the sessions
that work on this package (`hillclimb` skill §5). The wish line does not fix
that; the `models` column in every report below is there so a reader can see
whose demand it is.

## 1. The wish line

**One comment line, a fixed sigil, then natural language:**

```python
# want: re.findall for the digit runs
# want: Counter.most_common
# want: a way to group these rows by their first field
```

The whole content of the comment is the sigil `want:` followed by free text.
One wish per line, any number of lines per program, anywhere in it. The
recommended first word is the *thing* — a module, a function, a method, or
an idiom in plain words — because that is what the report clusters on (§4),
but the text is not parsed: a wish that clusters badly is still a wish.

**Why a comment.** It is the one carrier that costs nothing on every engine.
CPython discards it; `lex.rs` discards it; `route.rs` never sees it; the
fuzzer and `perf` cannot generate it. The alternatives each cost something
real: a magic import (`import __want_re__`) is a `ModuleNotFoundError` and
exit 1 on the reference; a string expression statement is an AST node the
walker and every pretty-printer keep; a CLI flag is a second thing to
remember, typed in a different place from the program. A comment is the only
form in which "outside regular Python" and "free on CPython" are the same
property.

**Three rules, each protecting a measurement.**

1. **A wish never has a consequence.** No route, refusal, output, exit code
   or timing depends on one, and nothing that routes reads one — the same
   write-only posture as the routes ledger (invariant 10, `routes.py`). The
   moment a wish could move a route, the model is being asked to route, and
   the count becomes a count of what it thinks moves routes.
2. **A wish is written beside the program, never instead of it.** The ask
   (below) keeps the two clauses `docs/PROMPTING.md` says must survive any
   edit: correctness outranks the tier, and a fall-back is free. So the
   model writes the program the way it wants — `import re` and all — and
   the wish adds the *narrow* piece behind the coarse refusal: `--plan` says
   `module: import re`; the wish says `re.findall for the digit runs`,
   which is the thing that can actually be bundled. On a program that was
   *not* refused, the wish is the hidden demand §0 is about.
3. **A wish is data, not an instruction, and it is redacted.** It is
   model-authored free text landing in a committed file: it goes through
   `harvest.redact` like the program it sits in, the report never executes
   or interprets it, and an agent reading a `wants` report treats a wish
   that reads like a command as a string. A wish saying `delete the corpus`
   is a row.

**The ask.** One sentence appended to `assets/prompt/routing.md`, the
paragraph every harness adapter injects (`docs/HARNESSES.md` §5), draft:

> If you reach for something you suspect the small interpreter lacks — or
> would have reached for it and wrote around it — say so in one comment line
> in the program, `# want: <what, in plain words>`, and write the program
> the way you want it anyway. The line changes nothing about how the program
> runs; it is how the interpreter learns what to grow.

It is unmeasured. `docs/PROMPTING.md` warns that asking an agent to *type*
something is a different ask from asking it to write plainly, and could
cost coverage; §5 says how to measure it before any rate is quoted.

**What the harvest does with it.** `harvest.extract_from_command` already
lifts the program text out of a shell command, a heredoc, a `Write`-then-run
pattern or a transcript block; the wish is a regex over that text
(`^\s*#\s*want:\s*(.+)$`, one match per line), taken *after* redaction and
stored on the sighting as `wants: [str]`. The field rides in the `extra`
bucket that `harvest.Sighting` and `corpus.Entry` already carry for exactly
this reason: an older harvest rewriting a sightings file keeps it
(`docs/CAPTURE.md`, *the raw record*). A wish is part of the program's text
and therefore part of its id — two programs that differ only by a wish are
two sightings, as they should be: one of them said something.

## 2. Bundled helpers — a vocabulary above the stdlib

**The cookbook's `# after` blocks are already the vocabulary, written
inline.** Every rewrite in the table the skill hands over (`lypning` skill
§1a) is a function the model is being asked to type out by hand each time:

| the rewrite the skill asks for | the helper it is |
|---|---|
| `d[x] = d.get(x, 0) + 1` in a loop | `counts(xs) -> dict` |
| `sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:k]` | `top(d, k) -> list` |
| `d.setdefault(k, []).append(v)` | `groups(pairs) -> dict` |
| `" ".join(s.split())` | `squash(s) -> str` |
| accumulate digit runs in a `for` | `digits(s) -> list` |
| `line.split(",")` over `splitlines()` | `rows(text, sep=",") -> list` |
| `os.path.splitext(os.path.basename(p))` | `stem(p)`, `ext(p)` |
| `divmod` on integer seconds | `hms(seconds) -> tuple` |
| a `for` loop with a membership test | `uniq(xs) -> list` |

Nine functions over builtin types, each a ten-line reference. The model
does not have to be taught to prefer them: a helper that is shorter than
the rewrite *and* shorter than the import it replaces is the path of least
resistance, and the census (§2c) says whether that held.

### 2a. Shipped twice, graded once

- **The reference:** one pure-Python module in the wheel, stdlib only
  (invariant 6), working name `lyp` — `src/lyp.py`, a second top-level name
  the wheel owns beside `lypning`. Short enough for a one-liner
  (`from lyp import counts, top`), and unambiguous in the census, where
  `import lypning` already means "a session working on this package" 809
  times on this run's corpus. The alternative, `lypning.lyp`, keeps one
  top-level name at the cost of eight characters in every one-liner; decide
  it at step 4 and do not revisit.
- **The fast path:** `assets/rust/src/lyp.rs` behind one `cap-lyp` feature
  on `lypning-l`, a helper at a time. The core stays frozen
  (`Cargo.toml`, `nemotron/L-TRAINING-ROADMAP.md`); whether a helper ever
  earns a place there is a block-budget decision the census informs, taken
  in the ledger and not here.
- **The grade:** CPython runs the reference, the variant runs the Rust, and
  `conformance` diffs them as it diffs everything — a helper that disagrees
  with its own reference is the same defect as a builtin that disagrees
  with CPython, and it is never fixed by editing the reference to match.
  `fuzz` draws from the variant's own tables, so the helpers enter the
  generator the day they enter the tables. Both dispatchers name `lyp` in
  their route tables (invariant 10) and `conformance --mixture both` says
  they agree.

**Where it works.** Only where the wheel is installed — which is exactly
where `routing.md` is injected, so the model that is told about helpers is
the model whose CPython has them. Anywhere else `import lyp` is a
`ModuleNotFoundError`, exit 1, as for any absent module (`docs/SUBSET.md`
§7 rule 4), and that is correct: an environment without the wheel has no
demand to record either.

### 2b. What a helper may be

The vocabulary is the abstraction language, and its grammar is the
following, or the helper is not admitted:

- **Total, deterministic, one answer.** For every input it has exactly one
  result or raises the same exception, message and all, in both
  implementations. It never exposes set iteration order, `os.listdir`
  order, an address, a clock or an environment — the refusals that are the
  design (`lypning` skill §5) hold inside a helper as outside it.
- **Builtins in, builtins out.** Arguments and results are `str`, `int`,
  `float`, `bytes`, `list`, `tuple`, `dict` and `bool`. No classes — the
  parser refuses them — and no callbacks, so the reference stays ten lines
  and its input space can be enumerated in a shell loop (`hillclimb` skill
  §3).
- **A narrow thing, never a fake of a wide one.** A wish for
  `yaml.safe_load for a flat mapping` is answered by a helper with *our*
  name and *our* reference, or not at all — never by shipping a module
  called `yaml`. A module-shaped fake keeps the expensive pattern alive
  (`docs/SUBSET.md` §5, the `subprocess` argument) and, worse, becomes a
  `MISMATCH` factory the day the real library is installed beside it.
  Third-party imports stay `ModuleNotFoundError`.
- **Nothing out of scope gets a door.** No helper runs a command, opens a
  socket, spawns a thread or sleeps. The cookbook already says where those
  go: the shell the caller is standing in.
- **Bytes stated.** Each helper's ledger entry says what it cost in bytes
  and whether the block count moved (`gate.DEVICE_BLOCK`), the same as any
  capability.

### 2c. The census, and demotion

`lypning corpus --stats` gains a `helpers` block: distinct programs, distinct
sessions and the `models` histogram per helper name, from the imports and
attribute names the corpus already indexes. Two rules, both stated so the
vocabulary cannot ratchet:

- **Promotion** is a wish cluster (§4) naming a helper-shaped thing in
  distinct programs from more than one session and more than one model,
  ranked against the refusals by the same table. One session's habit is
  not demand (`hillclimb` skill §5).
- **Demotion** is a helper no distinct program outside this repository's
  own sessions has used across the last several harvests: its Rust comes
  out, the bytes come back, and the reference stays in the wheel so the
  programs that did use it still run on CPython — a demoted helper is a
  route to `cpython`, never a broken program.

### 2d. A syntax of its own — the far end, and the rule that bounds it

A library is a language whose syntax is Python's, and it is the whole of
what this plan proposes to ship. A notation of our own — an `awk`-shaped
field language, a pipeline form, a shebang that selects it — would be
admitted only under the same rule as a helper: a pure-Python reference in
the wheel that `lypning run` can fall through to, so CPython stays the
oracle and a refusal stays free. The evidence that would justify it is a
census showing helpers *chained* often enough that a syntax buys something
a function call does not; until that row exists, the notation is a
speculation and the helpers are the language.

## 3. The data plan — every flow, and what it yields

The lifecycle, with the new pieces marked `+`:

```
type ──→ capture ──→ redact ──→ sightings ──→ corpus.jsonl ──→ grade ──┬→ --plan        refusals, ranked by ->cpy
         F1 F2 F3 F10           F4            +wants field     F5      └→ +wants        wishes × verdicts; helper census
                                                                              │
                          decide ←────────────────────────────────────────────┘
                          F12: one ledger step — implement in L · add a helper · demote one · write a recipe
                              │
                          verify: build --rust · conformance MISMATCH 0 · fuzz · gate blocks · dispatchers agree
                              │
                          feed back ─┬→ routing.md · cookbook · skill      F8 F9   what the next session is told
                                     ├→ hints.py recipes · question targets F11   what the teacher is shown
                                     └→ a NEW bundle identity              F11   never a label reused across engines
                              │
                          re-measure: study T9 (§5) · the census at the next harvest (§2c)
```

| flow | producer → record | demand it carries today | what this plan adds | who reads it |
|---|---|---|---|---|
| **F1** shim on `$PATH` | `python_invocation` → `$LYPNING_LOG` (`docs/CAPTURE.md`) | the program text, argv, a stdin sample | nothing at the feed: the wish is in the text already | `harvest` |
| **F2** command-string hooks (Claude `PreToolUse`, opencode, OpenHands) | `bash_command` → the same log | the full command, heredocs included | nothing at the feed; `extract_from_command` lifts the text and the wish with it | `harvest` |
| **F3** transcript scan | `harvest --transcripts` → sightings | programs typed in a session the hooks missed, with `models` | nothing at the feed | `harvest` |
| **F4** Stop-hook export and fold | `tests/corpus/sightings/<session>.jsonl` → `corpus.jsonl` | one record per distinct program, `count`, `models` | `wants: [str]` on the sighting, in `extra`; redacted; forward-compatible | `--plan`, `wants`, `--stats` |
| **F5** conformance | a verdict per program per engine; `report.routes` | `--plan`: refusals ranked by programs reaching CPython | `wants`: each wish joined with its program's verdict — MATCH is hidden demand, UNSUPPORTED names the narrow piece, MISMATCH is a bug as always | the ledger step (F12) |
| **F6** routes ledger | clean static route then runtime refusal → `$LYPNING_HOME/routes/` | the value-dependent refusals, live traffic | nothing — the ledger stays single-purpose and write-only; a wish is not a refusal | `lypning routes --plan` |
| **F7** fuzz | generated programs from the engine's tables | correctness on programs nobody typed | helpers enter the generator with the tables; the reference is the wheel's module under CPython | the gate |
| **F8** the injected paragraph | `assets/prompt/routing.md` via the harness adapters | the treatment every captured session ran under | the one sentence of §1; T9 in the study (§5) | `docs/PROMPTING.md` |
| **F9** cookbook | `<!-- recipe … -->` markers, tested | a rewrite per refusal kind | a `helper=` attribute naming the helper that replaces the `# after` block; the test asserts the two agree | `hints.py`, the skill |
| **F10** embedding hosts | `embed.Library.run` appends the record itself | programs a linked host ran | nothing at the feed; native hosts still owe their adapter (`docs/EMBEDDING.md`) | `harvest` |
| **F11** the training loop | question proposals → Qwen answers → evidence snapshots → review queue → verified repair → immutable bundle → SFT / DPO / GRPO → Fable report → Codex review | refusals on correct answers; `capability_targets` on proposals | wish clusters as an *input* to question authoring (never an oracle); wishes in Qwen's ordinary-task answers recorded as their own condition; the repair table's "correct with a valid L refusal" row gains "a wish names the narrow piece → capability request, ranked with the corpus wishes"; helpers shown by `hints.py` at sampling time only, trained on the bare prompt as now | `ORCHESTRATION.md`'s ledger |
| **F12** the hillclimb ledger | `docs/HILLCLIMB.md`, `L-TRAINING-ROADMAP.md` | `--plan` and `routes --plan` as the coverage gradient | `wants` as the second gradient beside `--plan`; the roadmap's "affected independent families" and "correctness-gated native gain" get a source that is not a refusal | whoever takes the next step |

**One decision this table does not make, deliberately.** Whether a helper
may appear in an ordinary-task SFT row is a training-regime decision: the
objective is compatible *first drafts* to prompts that never mention
lypning (`nemotron/DATA_PRODUCTION.md`), and a helper is only correct
where the wheel is installed. Until the deployment guarantees the wheel,
helpers stay in the deployment-conditioned regime and out of the
ordinary-task evaluation; the wish line, which changes nothing, is safe in
either. That is a row for `ORCHESTRATION.md`'s decision ledger, owned by
its orchestrator, not a default this document sets.

## 4. One table for a wish, a refusal and a helper

Rank rows, do not score them. The columns, in the order they sort:

| column | source | why it sorts here |
|---|---|---|
| distinct programs reaching CPython | `conformance.plan_cost` | what a refusal costs — the ranking `--plan` already uses |
| distinct programs that MATCHed and carried this wish | the `wants` join | the hidden demand; zero for a refusal without wishes |
| distinct sessions, distinct models | `models`, the sighting key's session tag | one session's habit is not demand |
| bytes, blocks | a spike build, or `—` | the tie-break, and the column the ledger must state |
| the bounded divergence list | written by hand before implementing | the "partial module is a MISMATCH generator" rule (`hillclimb` skill §3): if the inputs where a naive version would *differ* rather than refuse cannot be listed, the row is not ready |

Clustering is by the wish's first token, lower-cased, with `re.`, `os.` and
friends kept as prefixes — a reproducible, dumb grouping the report prints
so a reader can see where it was wrong. A wish is checked against the
*current* engine's tables at report time and bucketed **already served**
when it names something the variant now runs: `hints.py` found that 4 of
52 held-out repair prompts named a construct the engine had since learned,
and wishes age the same way. A wish is never deleted for aging; it is a
dated record of what a model wanted from the engine of that day.

## 5. Measuring the ask — study treatment T9

Add `T9 = T2 + the wish sentence` to `study/treatments.json` and run it the
way the nine were run (`docs/PROMPTING.md` §10), reporting four numbers:

1. **wish rate** — programs carrying at least one wish, and per task;
2. **coverage** — "runs on `lypning`" against T2's, the number the sentence
   could plausibly cost;
3. **MISMATCH and correctness** — must stay 0 and 100%; a sentence that
   buys a wrong answer is withdrawn whatever it buys in wishes;
4. **wish precision** — the share of wishes naming something the engine of
   that date lacked, against **already served** and **not a thing**.

A wish rate near zero under T9 means the sentence does not entice and the
sigil is wrong or buried; coverage below T2's means the sentence competes
with the motive, and the motive wins. Either result is written into
`docs/PROMPTING.md` with its date before the sentence ships in
`routing.md`.

## 6. The steps, in hillclimb shape

One mechanism per commit, each with the gates that can see it, in the
order that measures demand before paying bytes for it.

1. **The sigil and the field.** `harvest` extracts `wants` after redaction
   into `extra`; `corpus.Entry` reads it back; tests on a fixture log with a
   wish, a redacted wish and a heredoc wish. Zero engine bytes.
   Gates: `pytest`, `harvest --dry-run` on the fixture.
2. **The report.** `lypning wants` — a README §4 row and `--json`, which the
   doc tests enforce — joining wishes with the last conformance report's
   verdicts and printing the table of §4 with the corpus size it loaded.
   Gates: `pytest`, `docs/VERIFICATION.md` gains a contract for the join.
3. **The sentence, measured.** T9 in the study (§5); the numbers into
   `docs/PROMPTING.md`; then the sentence into `routing.md` and one line in
   the skill's §1a. Gates: the study's own, `tests/test_study.py`.
4. **The reference first.** `src/lyp.py` with the nine helpers of §2, a
   `helper=` attribute on each cookbook recipe it replaces, and the wheel
   test (`tests/test_packaging.py`) proving it ships. No Rust yet: `import
   lyp` refuses on both variants and reaches CPython, which is a
   measurement — the census now says which helpers are used before any
   byte is spent.
   Gates: `pytest`, `conformance` unchanged, `lypning doctor`.
5. **`cap-lyp` on `lypning-l`,** the used helpers only, one per commit:
   `lyp.rs`, the route tables in both dispatchers, the fuzz table, a
   differential grid per helper in the `_grid` shape the other capabilities
   use under `tests/`, the ledger entry with bytes and blocks.
   Gates: all four of the `hillclimb` skill §2, `--mixture both`.
6. **The census and the demotion rule** in `corpus --stats`, and the
   decision row in `ORCHESTRATION.md` on helpers in training (§3).

## 7. What this touches, and must not move

- **A wish never moves a route** (invariant 10). Nothing that routes reads
  the field, in either dispatcher, ever.
- **A helper's Rust never disagrees with its reference** (invariant 1), and
  a disagreement is fixed in the Rust or turned into a refusal — never by
  editing the reference, and never by widening a table.
- **No dependency** (invariant 6): the reference is stdlib only and lives in
  the wheel; the Rust links nothing.
- **No fake of anything out of scope** (`docs/SUBSET.md` §5): a helper is a
  narrow function with our name, never a stand-in for `subprocess`, a
  socket or a third-party module.
- **Bytes stated** (`hillclimb` skill §4c): every helper's cost is in its
  ledger entry, and the block count is the column that matters.
- **The corpus runs behind the net** (invariant 4): a wish is inside a
  program that may rewrite a repository, and `wants` runs over a
  conformance report, not over the programs.
- **No number here survives a re-run** (invariant 3): every count above is
  dated, and `wants` prints the corpus size it loaded like every other
  tool.
