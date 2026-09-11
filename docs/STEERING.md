# Steering a model into the subset — the census, the feed, the training set, the factory

> **Status (2026-09-11): a plan.** Nothing in this document is built. Every
> number is from one run on this date, in this container, and is cited with
> that run; every mechanism it names either exists today (and says so) or is
> a proposal (and says so). The study it builds on is `docs/PROMPTING.md`
> (2026-08-23); the loop it extends is `docs/FORKING.md` §1.

The mixture already answers every program — `lypning run` falls onward on exit
90 and the caller gets CPython's answer one spawn late. What it cannot do is
change what the agent *typed*. This document plans the other half: find exactly
which programs a particular model types that leave the subset and did not have
to, turn those into verified examples, feed the examples back to that model at
the cheapest point in a session, and, once the example set is large and
reviewed, train a model on it — starting with a small open model that can run
beside the agent — so that the subset is something the model *writes* rather
than something it is *told about*.

The rule that outranks everything below is the one `CLAUDE.md` and the skill
already state: **correctness outranks the route, and the subset is a routing
decision, not a challenge.** A model steered so hard that it reimplements
SHA-256 to avoid `hashlib` (`docs/PROMPTING.md` §8) has been made worse, not
better. Every artefact here — the census, the examples, the training rows —
therefore carries the *fall back* answer as a first-class row, never only the
*rewrite* answer.

## 1. The question, and the two ways a program leaves the subset

A corpus program that does not run on the floor engine is UNSUPPORTED in one of
two ways, and the difference decides whether any prompt can help:

| class | what happened | seen by | prompt can help? |
|---|---|---|---|
| **through the classifier** (static) | `lypning route` read the program and sent it past `lypning` before execution: an import, a class, a decorator, a builtin the table lacks | `route.rs` / `engines.route`, one parse, no execution | yes — the refusal is a property of the *text* |
| **hard** (run-time) | the route was clean, the engine ran the program and refused partway: `bigint`, `set-order`, `os-listdir`, `nan-identity`, `int-div-precision`, `float-sum` | only by running it; recorded in the field by `lypning routes` (`docs/VERIFICATION.md` §C11) | mostly no — the refusal is a property of the *data* (`docs/PROMPTING.md` §6); `set-order` is the exception (`sorted(...)` is an exact rewrite) |

Measured 2026-09-11 (`lypning conformance --plan --json`, this container,
Python 3.11.15, x86_64 musl, 3688 programs loaded, 2504 graded, 1184 skipped
for naming an absolute path):

| engine | MATCH | UNSUPPORTED | MISMATCH | coverage |
|---|---:|---:|---:|---:|
| `lypning` | 1558 | 945 | 1 | 62.2% |
| `lypning-l` | 1996 | 507 | 1 | 79.7% |
| mixture | 2503 | 0 | 1 | 100.0% |

Of the 945 refusals on the floor, **756 are `module:`** (80%), 48 `module-attr`,
25 `builtin`, 22 `class`, 12 `decorator`; the six run-time kinds together are
**36** (3.8%). So on today's corpus the steerable population is almost entirely
the static class, and almost entirely imports — which is the same shape the
study found on its 26 tasks (`docs/PROMPTING.md` §4: six import-tempting tasks
carried the whole effect). The one MISMATCH is `py-ab7286f43b7a`,
`print(1.7976931348623157e308 ** 0.5)`, a last-digit float-repr disagreement
that also grades as the run's one UNSAFE route; it is an engine defect and
invariant 1 says it is never in scope for a prompt.

Three vocabularies, fixed here so the rest of the document can use them:

- **steerable** — refused on the cheapest rung that could serve it, *and* an
  exact subset-clean equivalent exists (the `docs/COOKBOOK.md` before/after
  shape: same stdout, same exit, under CPython). `Counter` → `d.get(k, 0) + 1`.
- **fall-back** — refused, and the correct program *is* the one that was
  typed: no exact rewrite exists, or the rewrite is a reimplementation
  (`hashlib`, `subprocess` that cannot be hoisted into the shell, `bigint`).
  The right steering for these is *none*, and the example set must say so.
- **engine work** — programs about lypning itself (`import lypning`, 65 today;
  `import pydantic_monty`, 13; `sys.path` manipulation, 31) and the 393
  programs the prompting study fed back into the corpus
  (`tests/corpus/sightings/lypning-prompting-study.jsonl`,
  `docs/PROMPTING.md` §7). Both are excluded from every census below: the first
  is not a one-liner a user's session types, the second is the treatment
  measuring itself.

## 2. Exactly which programs: the census

The answer to "which programs to provide" is a ranked table, per model,
regenerated after every hillclimb round — the same way `conformance --plan` is
a build order for *capabilities*, this is a build order for *examples*, and
for the same reason its counts shift after each step: a capability that lands
moves programs from this table into MATCH.

### 2.1 Inputs, all of which exist today

| input | where | what it contributes |
|---|---|---|
| the corpus, with `models` histograms | `corpus.load`, `lypning corpus --stats --model ID` (`CHANGELOG.md` 2026-09-02, #27) | the population, and the per-model slice |
| conformance verdicts per engine | `conformance.Report`; `plan` and `plan_cost` in `conformance.py` | `kind: detail` per refused program, and the ranking key: programs that reach CPython |
| the static route | `report.routes`, `engines.route` | static versus run-time: a refusal whose route named the engine that refused it is the hard class |
| the routes ledger | `lypning routes` | the hard class as live sessions hit it, per machine — a floor, never a total |
| the cookbook | `docs/COOKBOOK.md` markers (20 recipes on 2026-09-11, 15 of them `kind=module`) | whether a rewrite is already known for a `kind: detail` |

### 2.2 The gap that comes first: nothing is attributed

`lypning corpus --stats` on 2026-09-11: **`unattributed 3688 100.0%`**. The
join exists (`harvest._model_index`; `docs/CAPTURE.md`, *Which model issued
it*) but runs only against Claude Code transcripts on the machine that holds
them, and this tree has none. opencode and OpenHands payloads carry no model at
all, so their records can never be attributed (`docs/HARNESSES.md` §2). A
"per-model" census on today's committed corpus is therefore one row, and the
first milestone (§7, M0) is to make it more than one:

1. `lypning harvest --transcripts` on every machine that captured sessions,
   then commit the re-attributed sightings. Cost: one cold index per
   transcript; the cache after that (`$LYPNING_HOME/model-index.json`).
2. Add a `model` field to the opencode and OpenHands adapters *where the host
   exposes one* — unverified for both (`docs/HARNESSES.md` §6 is the place a
   verified claim goes); until then their rows stay unattributed and the census
   says so rather than guessing.
3. Never invent an `unknown` model bucket: the hole is reported, not stored
   (`docs/CAPTURE.md`, the `unattributed` row). The census inherits that rule.

### 2.3 The proposal: `lypning steer`

A library module (`steer.py`, returns data — invariant 8) and one subcommand
that renders it. Nothing that routes ever reads its output: the census is
write-only with respect to routing, exactly as the ledger is (invariant 10,
`docs/VERIFICATION.md` §C11), and for the same reason — a file that can move a
route is a file that can make two dispatchers disagree.

```
lypning steer                       # the census over the whole corpus; prints the count loaded
lypning steer --model ID            # one model's slice; unattributed rows are a hole, never a zero
lypning steer --json                # the same, for the pair pipeline (§3)
lypning steer --exclude-engine-work # drop `import lypning`, `pydantic_monty`, `sys.path`, the study sightings (default on)
```

For every graded program whose verdict on the cheapest rung that could serve it
is UNSUPPORTED, the census records:

| column | source | meaning |
|---|---|---|
| `feature` | `kind: detail`, as `plan` spells it | the first thing the program hit |
| `class` | route versus refusing engine | `static` or `runtime` |
| `blocks` / `reach_cpython` | `plan`, `plan_cost` | how many programs, and how many of them cost a CPython spawn |
| `models` | the corpus `models` histograms, summed over the row | who types this — the "which model evokes it" tag |
| `recipe` | cookbook marker with the same `kind` and a `detail` prefix | a known exact rewrite, by id, or `—` |
| `decision` | a curated table in `steer.py`, one row per `kind: detail` prefix | `rewrite` (recipe exists), `wanted` (steerable, no recipe yet — the build order for §3), `fallback` (do not steer), `engine-work` (excluded) |

The `decision` column is a hand-kept table and that is deliberate: whether
`import hashlib` has an exact rewrite is a judgement the project has already
made in prose (the skill's §1a; `docs/PROMPTING.md` §8) and the census should
carry that judgement as data rather than re-derive it per run. A `kind: detail`
with no row is rendered `unclassified`, never silently `wanted`.

### 2.4 Today's ranking, and the answer to the question as asked

The floor's refusals on 2026-09-11, ranked as `plan` ranks them (by CPython
reach, then count), with the decision each row would carry. This is the
current answer to *which programs*; it is one model-blind row because of §2.2,
and it moves every round.

| feature | blocks | rung that serves it | decision | the rewrite, or why not |
|---|---:|---|---|---|
| `module: import re` | 243 | cpython | **wanted** (simple patterns) / **fallback** (the rest) | `" ".join(s.split())`, `str.split/find/partition`, a character loop — for a fixed simple pattern only; `docs/COOKBOOK.md` has one `kind=re` recipe; `re` itself is deliberately never implemented (skill §4) |
| `module: import pathlib` | 147 | `lypning-l` | **rewrite** (recipe `pathlib-read-write`) or *let it ride* | `os.path.*` + `open` on the floor; on the chain that ships this costs one forked spawn, not CPython — low priority for a prompt, `docs/PROMPTING.md` §9 |
| `module: import lypning` | 65 | cpython | engine-work | excluded |
| `module: import collections` | 45 | `lypning-l` | **rewrite** (recipes `collections-counter`, `collections-defaultdict`) | `d.get(k, 0) + 1`, `setdefault` |
| `module: import subprocess` | 34 | cpython | **rewrite** when the command can be hoisted into the shell (recipes `subprocess-capture`, `subprocess-file-list`); **fallback** otherwise | a pipe into `lypning script.py` |
| `module-attr: sys.path` | 31 | cpython | engine-work | excluded |
| `module: import glob` | 28 | cpython | **fallback** | `os.listdir` order is refused (`os-listdir`); no exact rewrite exists on the floor |
| `module: import csv` | 23 | cpython | **rewrite** for unquoted rows (recipes `csv-*`); **fallback** for quoted/escaped input | `line.split(",")` is exact only when no field contains a comma or a quote |
| `class: class definition` | 22 | cpython | **wanted** | a `dict` or a `tuple` for a record; a bare function for a method — needs recipes and a judgement about when a class is the honest program |
| `module: import math` | 14 | cpython | **rewrite** (recipe `math-basic`) | `n ** 0.5`, `//`, `-(-a // b)`; `isqrt` as a binary search |
| `module: import hashlib` | 14 | `lypning-l` (`cap-hashlib`, #56) | *let it ride* | served one rung up since #56; never rewrite by hand |
| `module: import unicodedata` | 13 | cpython | **fallback** | needs the category tables the engine refuses to approximate (skill §5) |
| `builtin: exec` | 13 | cpython | **fallback** | not a one-liner shape a prompt should touch |
| `decorator:` | 12 | cpython | **wanted** | call the wrapped function directly; `dataclass` → a dict |
| run-time kinds (`bigint` 11, `set-order` 12, `int-div-precision` 4, `nan-identity` 4, `os-listdir` 3, `float-sum` 2) | 36 | cpython | **fallback**, except `set-order` → **rewrite** (`sorted(set(...))`) | decided by the data (`docs/PROMPTING.md` §6) |

Two readings of this table are wrong and the census must prevent both. A
row served by `lypning-l` is not a prompt's problem on the chain that ships —
the study measured `lypning` alone (`docs/PROMPTING.md` §9) and its
`Counter`/`pathlib` findings pre-date the larger rung. And a high `blocks`
count is not a high priority: `plan` ranks by CPython reach precisely because
`import pathlib` at 147 reaches CPython 0 times.

## 3. From a refused program to an example: the pair pipeline

An example is a **pair** — the program a model typed and the program it should
have typed — plus what the shell supplied and what both print. The cookbook
already defines and *executes* that shape (`docs/COOKBOOK.md`, *The marker*;
`tests/test_cookbook.py`, three assertions), so a pair is a cookbook recipe
with provenance, and nothing new has to be invented to verify one.

### 3.1 Where the `after` comes from

| source | how | scale |
|---|---|---|
| **recipe application** | a `wanted`/`rewrite` row whose recipe is mechanical (`Counter` → `dict.get`) is applied by an agent given the recipe and the program | most of §2.4 |
| **agent rewrite** | the study harness already asks Claude Code agents for programs against a task brief (`study/harness.py`, `study/gen_taskbrief.py`); the same loop, handed the *before* and the T5 cookbook prompt, produces an `after` | hundreds per run |
| **human** | the rows where the honest answer is a judgement (`class`, `re` beyond a fixed pattern) | tens |

Which model wrote the `after` is recorded (`rewrite_by`, §5) for the same
reason `models` is recorded on the corpus: a training set built by one model
rewriting another's programs is a fact about both.

### 3.2 The fixture problem, and the task shape that solves it

A corpus program is the *before* but almost never comes with what it read:
19 of 3688 carry a `stdin_sample` and 152 an `argv_tail` (2026-09-11, `corpus
--stats`), and the files a session's cwd held are gone. The study already
solved this once: `study/tasks.jsonl` carries `ask`, `stdin`, `argv`, `files`,
a `reference`, and an `expect_stdout` *derived* by `study/bless.py` rather than
typed. So the pipeline is **corpus → task → pair**:

1. cluster each `wanted`/`rewrite` row's programs by shape (the `plan` row's
   example ids are the seeds);
2. for each cluster, an agent writes a task in the `tasks.jsonl` shape — an
   `ask`, small fixtures, and the *before* as the `reference` — and never sees
   an expected output;
3. `study/bless.py` derives `expect_stdout` under CPython and records
   `tier1_feasible`;
4. the `after` is produced (§3.1) and verified (§3.3).

A cluster whose representative cannot be given a fixture that makes it run to
completion under CPython is dropped and counted, not guessed at.

### 3.3 The five gates a pair must pass

1. `before` and `after` print the same bytes and exit the same under CPython
   (the cookbook's assertion 3);
2. `after` is MATCH on the rung the row names (assertion 2), and `lypning
   route` sends it there (`docs/VERIFICATION.md` §C5);
3. `before` is still UNSUPPORTED with the row's `kind`/`detail` (assertion 1 —
   a pair whose `before` now runs is **obsolete**, which is how the set finds
   out that coverage landed, exactly as the cookbook does);
4. `after` is not a reimplementation: a length ratio over the `before` above a
   threshold, or a refused import replaced by a hand-written algorithm, fails
   the row into `fallback` for a human to look at;
5. redaction: `harvest.SECRET_PATTERNS` over both halves, and the pair is
   committed only after the same diff review the corpus gets
   (`docs/CAPTURE.md`, *Privacy*).

Every gate runs behind the net (`CLAUDE.md` invariant 4): a pair pipeline
executes agent-written programs by the hundred.

### 3.4 The rows that are not rewrites

The set is not "subset Python". It is the **routing judgement**, and a model
trained on rewrites alone learns the wrong lesson. Three labels, all present
in every export:

| label | `after` | teaches |
|---|---|---|
| `rewrite` | the exact subset program | the substitution |
| `fallback` | the `before`, unchanged, with the reason (`hashlib: no exact rewrite; one CPython spawn is the right cost`) | when *not* to contort |
| `inside` | the program as typed, already MATCH | keep doing this — the two-thirds of the corpus that was never a problem |

The `fallback` rows are the guard against `docs/PROMPTING.md` §8. Their share
in any export is a stated number, never zero.

## 4. How to feed the examples

### 4.1 What is already measured, and the limit it puts on this section

`docs/PROMPTING.md` §1: on 26 tasks, one model, 2026-08-23, a 744-byte
paragraph of *motive* (T2) reaches the feasibility ceiling; the 3.3 KB cookbook
(T5) reaches the same ceiling; the 5.7 KB capability brief and the tool loops
add nothing on coverage; and the skill handed over verbatim scored seven points
*worse* than the paragraph. So for that model on that battery, **examples buy
nothing over the motive**, and any plan that says otherwise is contradicting a
measurement. What the study could not see, and what this section is for:

- **other models.** T1's 11.5 pp replicate spread (`docs/PROMPTING.md` §3) is
  the tell: the vaguer the prompt, the more the outcome depends on who reads
  it. A model that cannot infer the subset from a motive paragraph is exactly
  the model examples are for — and a small open model is the one this plan
  trains (§6), so it is the one to measure first.
- **the field distribution**, which is heavier than the battery
  (`docs/PROMPTING.md` §9: 55.6% routed on the later corpus against 62.5% for
  the control cell).
- **cost.** Every byte of context is paid in every session, including the
  sessions that type no python. A generic cookbook in `SessionStart` is the
  wrong trade if a targeted, later, cheaper surface exists.

### 4.2 The surfaces

| surface | exists | feeds | when | cost per session | per-model? |
|---|---|---|---|---|---|
| `SessionStart` → `additionalContext` (Claude Code) | yes — `assets/prompt/routing.md` + the engine-state line (`capture.agent_context`, `docs/HARNESSES.md` §5) | motive paragraph, today; **proposal:** plus the top-K pairs from this model's census slice | once, before anything is typed | bytes × every session | the payload carries an *optional* `model` field (Claude Code hooks reference, read 2026-09-11; "doesn't always include it") — when absent, feed the model-blind top-K and say so |
| **`PostToolUse` on `Bash`, reactive** | no | **the one recipe** matching the refusal the command just produced: `unsupported: <kind>: <detail>` → `steer.suggest(kind, detail)` | only after a refusal, at the moment the model can use it | ~200 B, and only in sessions that left the subset | inherently: it answers the program *this* model just typed |
| the skill, §1a | yes — hand-written table | the same recipes, human-readable | on demand | none until loaded | no |
| `lypning rewrite -c PROG` | no | the engine answering for itself: the recipe(s) for what `route` says this program hits, no execution | when an agent has tools (`docs/PROMPTING.md` T7/T8) | one spawn | no |
| opencode `tool.definition` / OpenHands `SessionStart` | yes, the paragraph (`docs/HARNESSES.md` §5) | same as Claude Code's first row; the reactive row maps to opencode's `tool.execute.after` and OpenHands' `PostToolUse`, which already carry the exit code and output | as above | as above | no — unattributed hosts |

**The recommendation is the reactive surface first.** It is the only one whose
cost is zero on a session that stays inside the subset, the only one that is
per-model without needing the model's name, and the only one that delivers an
example at the moment it is relevant rather than at session start. Two things
must be verified on a live session before it is built, and the verification is
part of the milestone (§7, M3): that `PostToolUse` honours
`hookSpecificOutput.additionalContext` (the reference lists it as a
decision-control field; `docs/HARNESSES.md` §6's claim map is where the
verified answer goes), and what the `tool_response` for `Bash` carries — the
refusal line is on stderr, and if the hook cannot see stderr the trigger is
the shim's record instead.

The hook contract is unchanged by any of this (invariant 5): `continue: true`,
`suppressOutput: true`, exit 0 on every path, **no `permissionDecision`**, and
no `decision` key under OpenHands. A steering hook that could block is a
capture harness that has become something else.

### 4.3 The byte budget

Every feed carries a budget it is measured against, in bytes of prompt per
session and per refusal, next to the coverage it bought. The paragraph is 744
B and saturates on the study's model; a targeted `SessionStart` block is capped
at the cookbook's 3.3 KB (T5) because nothing larger was ever shown to help; a
reactive recipe is one marker's worth. A feed that exceeds its budget for a
gain inside the replicate noise is removed, not tuned.

### 4.4 Measuring a feed

Two new treatments in `study/treatments.json`, scored by `study/score.py`
unchanged, so every number lands in the same three columns (`correct`,
`route`, `verdict`) as the nine that exist:

- **T9 targeted** — T2 plus the top-K pairs from the model's census slice at
  `SessionStart`.
- **T10 reactive** — T2 plus one recipe delivered after each refusal, tools on,
  one revision allowed (T7's protocol).

Run per model and per harness — opencode's own prompts steer python usage per
model (`docs/HARNESSES.md` §6) — and on two batteries: the study's 26 tasks and
a held-out set drawn by §3.2 from the field census. A feed "works" when it
raises `tier1_win` on the *held-out* battery for *that model* with MISMATCH 0
and no rise in the reimplementation gate (§3.3, gate 4). A feed that raises
`route` but not `tier1_win` has taught the classifier's blind spots, not the
subset (`docs/PROMPTING.md` §6, finding 6a).

## 5. The training set

### 5.1 One row

One JSONL row per verified pair, at `study/steer/pairs.jsonl` — a review
artefact in the corpus's sense (`corpus.py`: one normal form, sorted, compact,
`ensure_ascii=False`, unknown keys carried), because it is derived from the
corpus and committed under the same privacy rules.

```json
{"id": "st-3f9a1c0e7b2d",
 "task": {"ask": "Print the three most frequent words on stdin, most frequent first.", "stdin": "a b a c b a\n", "argv": [], "files": {}},
 "before": "import sys, collections\nprint(collections.Counter(sys.stdin.read().split()).most_common(3))",
 "before_refusal": {"engine": "lypning", "kind": "module", "detail": "import collections", "class": "static"},
 "after": "import sys\nd = {}\nfor w in sys.stdin.read().split():\n    d[w] = d.get(w, 0) + 1\nprint(sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:3])",
 "after_verdict": {"engine": "lypning", "verdict": "MATCH"},
 "expect_stdout": "[('a', 3), ('b', 2), ('c', 1)]\n",
 "label": "rewrite",
 "recipe": "collections-counter",
 "evoked_by": {"<model id>": 7},
 "rewrite_by": "<model id or recipe or human>",
 "origin": {"corpus_id": "py-…", "source": "hook", "first_seen": "2026-…"},
 "verified": {"date": "2026-…", "commit": "…", "cpython": "3.11.15"}}
```

`evoked_by` is the tag the request asks for — *which model evokes this
script* — and it is the corpus's `models` histogram for the `before`, summed
over the cluster, carried verbatim: a subset of the occurrences, with the
unattributed hole reported at the file level (§2.2), never as a bucket in the
row. `label` is one of §3.4's three. A `fallback` row's `after` equals its
`before` and its `recipe` is the reason.

### 5.2 What it is for, and what a slice is

The whole file trains **any** model: it is the routing judgement, expressed as
programs, with the fall-back answer present at a stated share. A **particular
model's steering set** — the examples to *feed* it (§4) — is the slice
`evoked_by` contains that model, ranked by its census (§2). Train on the whole;
prompt with the slice.

### 5.3 Export

`study/factory/export.py` renders the rows into chat-format SFT JSONL, the
shape NeMo AutoModel and Unsloth both read (`messages` with `system`, `user`,
`assistant`):

- **system** — `assets/prompt/routing.md`, byte-identical to what the hooks
  inject. Training and prompting share one instruction, so a tuned model is
  evaluated under the same context it will be deployed with.
- **user** — the task, rendered as `study/gen_taskbrief.py` renders one: the
  ask, the exact stdin bytes, the argv, the files. Never the expected output.
- **assistant** — the `after` program. For a reasoning-tuned target the row
  carries a one-sentence rationale in the thinking channel (`Counter →
  dict.get: exact`; `hashlib: no exact rewrite, fall back`), because the model
  card for the first target (§6.2) defaults thinking on, and its fine-tuning
  guidance (Unsloth's Nemotron page, read 2026-09-11) says to keep a majority
  of reasoning examples when tuning a mixture-of-experts model or its reasoning
  degrades. Both the share and the source are stated in the export's header.
- **split** — train/eval by *task id*, never by row: two rows of one task are
  one fact, and a row-level split leaks the answer.
- **shares** — the export prints the `rewrite`/`fallback`/`inside` counts and
  refuses to write a file whose `fallback` share is zero.

## 6. The fine-tuning factory

### 6.1 Where it lives, and what it may depend on

`study/factory/`, not `src/lypning/`. The package ships with zero runtime
dependencies (invariant 6) and that does not change; the factory is research
code like the rest of `study/`, may depend on a trainer, and is never imported
by the package. The package's only contribution is the data (§5) and the
verification the data went through (§3.3).

The factory is one script with a `--backend` and a `--model`, because the
first two runs are a cheap one and an expensive one and they must be the same
code:

```
python3 study/factory/run.py --backend colab --gpu T4   --model <small>     --dry-run   # the pipeline, on a model the free tier fits
python3 study/factory/run.py --backend colab --gpu A100 --model nemotron-3.5-lightning  # the run that matters
python3 study/factory/run.py --backend local                                             # any box with a GPU and the trainer installed
python3 study/factory/eval.py --endpoint <openai-compatible URL> --model <id>           # the deliverable: scored like every other treatment
```

### 6.2 The first target, as read on 2026-09-11

NVIDIA Nemotron 3.5 Lightning, `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16`
on Hugging Face (released 2026-08-11 per NVIDIA's announcement; the model card
was read 2026-09-11):

| fact | as stated | consequence here |
|---|---|---|
| licence | OpenMDW License Agreement 1.1 | a tuned derivative can be published |
| size | 30B total, 3B active; hybrid Mamba-2 + mixture-of-experts with some attention layers | small enough to run beside an agent; large enough that a free Colab GPU does not hold it |
| context | up to 1M tokens; 256K on one 80 GB GPU | irrelevant for one-liners; the training rows are short |
| serving in BF16 | "1× H100 80GB (or 1× A100 80GB)" | the Colab arm needs an A100 runtime |
| LoRA on the same-sized sibling (Nemotron 3 Nano 30B-A3B), per Unsloth's page | about 60 GB VRAM for 16-bit LoRA; "does not fit on free Colab"; an A100 notebook is provided | the T4 dry-run uses a small model; only the A100 run trains Lightning |
| customisation NVIDIA names | NeMo AutoModel (LoRA or full SFT), NeMo Megatron Bridge, NeMo RL and NeMo Gym; Unsloth listed among the tools that run it | AutoModel is the named LoRA path; Unsloth's page did not list 3.5 Lightning explicitly on the day it was read — one probe decides which trainer, and the answer is dated in the factory's README |
| chat template | thinking on by default; a template kwarg turns it off | the export carries the reasoning channel (§5.3); evaluation runs both settings |

The model is a parameter and this table is a snapshot; the next Nemotron
(NVIDIA has said a trillion-parameter family is in training) changes the GPU
row, not the pipeline.

### 6.3 The Colab arm, and the "API key" that is not one

Google introduced a Colab CLI in June 2026 (developers.googleblog.com, read
2026-09-11) with `colab new` (provision, `--gpu T4` / `--gpu A100`), `colab
install`, `colab exec -f <script>` (non-interactive), `colab download` (retrieve
artefacts), `colab log` (a replayable notebook of the run) and `colab stop`.
That is enough for the factory: upload the export, run the trainer script,
download the adapter. **What the announcement does not say is how it
authenticates.** Nothing read on 2026-09-11 describes a Colab API key; the
plan assumes a one-time interactive login on the machine that drives the
factory, and M4 (§7) verifies that assumption before anything else is built
on it. If the CLI turns out to be OAuth-only, the factory runs from a logged-in
shell; if a key exists, it is read from the environment and never written to
a file this repository tracks.

What leaves the machine is the **export of committed, reviewed pairs** — never
the raw capture log, never `~/.lypning/model-index.json`, never a transcript.
Sending the export to Colab is publishing it (`docs/CAPTURE.md`, *Privacy*
already says the corpus is committed and therefore public; the export is a
subset of it).

### 6.4 The steps, and the loop they close

1. **export** — §5.3; the header states the shares and the split.
2. **train** — LoRA on the target with the chosen trainer; the adapter is the
   artefact, merged weights are optional, a GGUF conversion is what lets the
   result run locally beside an agent.
3. **evaluate** — the deliverable. The tuned model, served behind any
   OpenAI-compatible endpoint, is a *treatment*: `study/harness.py` generates
   its programs on the held-out battery, `study/score.py` scores them in the
   same three columns as T0–T10, and the report is one more row in
   `docs/PROMPTING.md` §1's table with the model named and the date.
4. **deploy into the loop** — wire the tuned model as an OpenHands or opencode
   model (`docs/HARNESSES.md` §4 shows the OpenAI-compatible shape) with capture
   on; its programs land in the corpus; `lypning corpus --stats --model
   <tuned>` and `lypning steer --model <tuned>` are the next census. **The
   tuned model's own field programs are the next training set's input**, which
   is the loop `docs/FORKING.md` §1 describes with one more arrow.

Acceptance for a tuned model, all three or it is not shipped:

- MISMATCH 0 and `correct` not below the same model untuned, on the held-out
  battery under CPython — a tuned model that is faster and wrong is the one
  outcome this project exists to prevent;
- `tier1_win` on the held-out battery at or above T2 for the *untuned* model —
  the tuning must beat the cheapest prompt, or the prompt wins;
- the reimplementation gate (§3.3, gate 4) does not fire more often than for
  the untuned model, and every `fallback` task in the battery is still answered
  with the import.

## 7. Milestones, each with its gate

| # | milestone | done when | gate command |
|---|---|---|---|
| M0 | attribution online | `lypning corpus --stats` prints more than one row under `by model` on at least one real machine's sightings, committed | `lypning corpus --stats` — the `unattributed` share, dated |
| M1 | the census | `lypning steer` renders §2.3's table over the corpus and by `--model`; `decision` rows cover every `kind: detail` in today's `plan`; a test pins that nothing on the routing path imports `steer` | `lypning steer --json` piped to the same digest check `docs/VERIFICATION.md` §C11 uses; a new `docs/VERIFICATION.md` section, C16, with its run of record |
| M2 | the pair pipeline | `study/steer/pairs.jsonl` exists with all three labels, every row through the five gates, the `fallback` share printed | `python3 study/steer/verify.py` — `N pairs, 0 failed, fallback share S%`; `git status` clean after |
| M3 | the feeds | the reactive `PostToolUse` recipe verified on a live session (both facts in §4.2 answered and recorded in `docs/HARNESSES.md` §6's claim map); T9 and T10 in `study/treatments.json`; scored on one model | `python3 study/score.py --report` — two new rows, MISMATCH 0 |
| M4 | the factory dry-run | `run.py --backend colab --gpu T4 --model <small> --dry-run` completes end to end: export, upload, train one epoch, download an adapter; the auth question in §6.3 answered and dated in `study/factory/README.md` | the adapter file exists and `eval.py` scores it, even if badly |
| M5 | Lightning | the A100 run on the full export; adapter and GGUF produced | `eval.py` — the three acceptance rows of §6.4, dated |
| M6 | the loop closed | the tuned model captured through a harness; `lypning steer --model <tuned>` renders its own residue | `lypning corpus --stats --model <tuned>` — a row, with a date |

M1 through M3 do not depend on M0 — the census runs model-blind today and
gains its columns when attribution arrives — and M4 depends on nothing but an
export, so it can be de-risked in parallel with M2 on the cookbook's twenty
recipes alone.

## 8. Risks, and what would make this plan wrong

- **Prompting is already saturated for the model that matters most.** If §4.4
  shows T9 and T10 inside the replicate noise for every model tried, the feed
  half of this plan reduces to the reactive hook (cheap, zero cost when unused)
  and the rest of the value is in §5–§6 alone. That is a fine outcome and the
  plan is written so it can be reached without building the rest.
- **The ceiling is the engine, not the prompt.** 88.5% on the study battery was
  the *feasibility* ceiling; on the field corpus the floor's coverage is 62.2%
  (2026-09-11) and most of the gap is imports the hillclimb keeps closing. A
  round that lands a capability moves programs out of the census, and a
  training set frozen before that round teaches a rewrite that is no longer
  needed. Gate 3 (§3.3) makes such rows loud; regenerate before each export.
- **Goodhart.** A model trained to stay inside the subset will, if the
  `fallback` share is small, learn to stay inside the subset at the expense of
  the answer. The share is printed, the export refuses zero, and the
  acceptance criteria grade correctness under CPython first.
- **The corpus is partly the study's own output** (`docs/PROMPTING.md` §7),
  and a census that counts it is measuring a treatment. Excluded by default;
  the exclusion is a flag so it can be shown either way.
- **Attribution has known holes.** A shim record is time-joined
  (`docs/CAPTURE.md`, *Which model issued it*), so a second harness run inside
  a Claude Code shell is filed under the Claude model; opencode and OpenHands
  are never attributed. `evoked_by` inherits every one of these and the census
  prints the unattributed share beside every model row.
- **The GPU row moves.** §6.2 is a snapshot with a date; the pipeline is
  parameterised on the model so the snapshot is the only thing that goes stale.
- **A pre-existing MISMATCH is on the board** (§1). It is not this plan's to
  fix, and it is not allowed into any example set: gate 2 requires MATCH.

## 9. Non-goals

- **No router reads any of this.** Not the census, not the pairs, not a tuned
  model's preferences: `route.rs` and `engines.dispatch` decide from the
  program text alone (`CLAUDE.md` invariant 10).
- **`re` is still not implemented.** The `import re` row is steered where a
  fixed simple pattern has an exact rewrite and left alone otherwise (skill
  §4).
- **No capability table is widened to make a pair verify.** An `after` that
  MISMATCHes is a bug report, never a training row (invariant 1).
- **No new runtime dependency**, and no third-party code under `src/`.
- **No number in this document is quoted forward.** Every table above names
  its run; the census, the export and the evaluation each print the count they
  loaded, and that is the number to use.

## 10. Sources read for §6, all on 2026-09-11

- NVIDIA model card, `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16`
  (huggingface.co/nvidia): licence, sizes, architecture, GPU rows, chat template.
- NVIDIA technical blog, *Nemotron 3.5 Lightning delivers fast, accurate
  specialized task execution for long-running agents* (developer.nvidia.com):
  the customisation tools and serving stacks named.
- NVIDIA newsroom and press coverage of the 2026-08-11 release: date and licence.
- Unsloth documentation, *Nemotron 3* (unsloth.ai/docs/models/nemotron-3):
  VRAM for LoRA on the 30B-A3B sibling, the free-tier limit, the reasoning-share
  guidance.
- Google Developers Blog, *Introducing the Google Colab CLI*
  (developers.googleblog.com): the command set; silent on authentication.
- Claude Code hooks reference (code.claude.com/docs/en/hooks): the optional
  `model` field on `SessionStart`; `additionalContext` as a decision-control
  field; silent on `PostToolUse`'s `tool_response` shape for `Bash`.
