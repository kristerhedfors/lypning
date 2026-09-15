# LoRA pipeline

**The target model is `Qwen/Qwen3.8-27B`.** The directory and its `nt` CLI
predate the target switch of 2026-09-11; `SWITCH.md` is the one place the
earlier model is named, and it lists the numbers from that era that must never
be read as Qwen's. Every measurement in this tree is Qwen's unless its own
provenance line says otherwise.

The current lypning-l-first training design and commands are in
[TRAINING.md](TRAINING.md): verified SFT, then evidence-gated execution-RL, with
matched base/SFT controls, multi-input tests and source/family-held-out evaluation.
Start the other-device Claude Code session with
[START_NEXT_ROUND.md](START_NEXT_ROUND.md). See
[DATA_PRODUCTION.md](DATA_PRODUCTION.md) for the complete evidence/review/learning
loop, [NEXT_ROUND.md](NEXT_ROUND.md) for the manual probe/train/eval sequence and
[L-TRAINING-ROADMAP.md](L-TRAINING-ROADMAP.md) for runtime priorities. The new
`training-prepare` and `gpu/train_verified.py` path is separate from the frozen
rewrite experiment documented below. Its GPU integration still needs a hardware
smoke; no quality improvement is claimed from implementing the trainer.

The original pipeline's four stages:

1. **Corpus** — failing generation cases in, executable acceptance tests out.
2. **Eval** — pass@1 over a frozen held-out split, with a bootstrap 95% CI.
3. **Training** — Qwen rank-16 LoRA; the standalone historical runner is
   `gpu/lypning_lora.py`.
4. **Sweep** — one row per run, a win defined before any run happens.

Corpus/evaluation and SFT tooling exist. Controlled training experiments and
the release-quality sweep remain gated on verified data, runtime correctness
and a comparable baseline; see the current design for those gates.

## One-line commands

```bash
./nt classify           # run the real corpus through CPython + both engines, once
./nt harvest            # step 1: sources -> corpus.jsonl + drops.jsonl
./nt split              # freeze the 70/30 stratified held-out split
./nt verify             # re-check the frozen split against its lock
./nt leaks --sft data/sft/v1   # does a training target already pass a held-out case
./nt sample --dry-run   # the pool, the draw count, the ceiling cost; spends nothing
./nt probe              # one cheap request; is the backend reachable
./nt eval --baseline    # step 2, detached under tmux; returns immediately
./nt status             # one screen: corpus, baseline, running, spend, ETA
./nt results            # leaderboard by delta vs baseline
./nt slices RUN         # pass rate per stratum — the blended number hides the headroom
./nt show RUN --failed-only   # the programs, and why each failed
```

`./nt eval` hands the job to tmux and
returns a run id; `status` and `results` are the only two commands needed after.
The separate verified-training runner runs in the foreground on its isolated
worker so a scheduler/operator owns its lifetime and budget.

## Step 1 — the corpus, and what it refuses

A case is `(prompt, optional reference solution, executable acceptance test,
failure category, provenance)`. It is kept only if its test **runs** and
**discriminates**. Four gates, in `pipeline/acceptance.py`:

| gate | what it demands | drop reason if it fails |
|---|---|---|
| `runs` | the test executes, no harness error, inside its timeout | `runs` |
| `satisfiable` | the reference solution passes it | `satisfiable` |
| `discriminates` | the empty program fails it, **and** so does the recorded failing generation | `discriminates` |
| `stable` | the same program gets the same verdict twice | `stable` |

A candidate carrying nothing executable is dropped as `no-test`; one with neither
a reference nor a recorded failure is dropped as `no-witness`, because nothing
present can show its test works. Every drop is written to `data/drops.jsonl` with
the gate that rejected it. Nothing is ever patched to make it fit.

The third gate is the one that pays for itself: the failing generation is a
negative control you already have, and a test it passes is measuring something
other than the bug you harvested.

**Sources** are adapters (`pipeline/adapters.py`). Shipped: `lypning` (the real
thing — see below), `study` (this repository's 26-task bank), `evalfail` (the
failures of a step-2 run), and `jsonl` (anything else, with `--map dst->src` to
rename fields). A new source is one function.

### The `lypning` source: what "previously-failing" means here

A failing case is a python invocation **this runtime refused** — exit 90, one
`unsupported:` line, and a full CPython spawn instead of an in-process answer.
`./nt classify` runs every entry of `assets/corpus/corpus.jsonl` through CPython
and both engines and writes the verdict down once; the harvester reads that file.
Skip rules (absolute paths, battery-spawning, nondeterminism) are imported from
`lypning.conformance`, not reimplemented.

Two case shapes come out, and the split is the point:

- **rewrite** — the refusal may be dodgeable. The case asks for an equivalent
  program that stays on the engine. The original is the *negative control*: it is
  correct and still fails the routing leg. There is no reference solution, so the
  `satisfiable` gate records `unproven-no-reference` rather than pretending.
- **ceiling** — the refusal cannot be dodged (arbitrary-precision integers,
  `os.listdir` order, the modules nobody should reimplement). Falling back IS the
  right answer, so `require_tier1` is false, the original is the *reference*, and
  the case is scored on correctness alone.

Stratification is by the engine's own refusal kind (`module`, `bigint`,
`set-order`, `builtin`, …) rather than a taxonomy invented here, so the strata
stay aligned with `conformance --plan` — the build order those refusals rank.

### The `lypning` test kind

Two axes, and the order is the whole point:

1. **Correctness on CPython, checked first.** A failure here ends the attempt.
2. **Routing**, only once the answer is known to be right.

Because the one thing this project exists to prevent is a plausible wrong answer
produced to stay inside the subset. A model that hand-rolls SHA-256 rather than
importing `hashlib` must score zero, and it does. The ceiling cases are the
counterweight: without them "stay in the subset" has nothing pulling against it.

An engine that *runs* a program and disagrees with CPython is reported as
`engine-mismatch` — invariant 1, always a bug — and never as a model failure.

```bash
./nt harvest --source 'jsonl:path=/path/to/failures.jsonl'
./nt harvest --source 'evalfail:attempts=runs/baseline-.../attempts.jsonl'
```

Native record shape for the `jsonl` adapter — `test` may also be given directly:

```json
{"prompt": "...", "reference": "...", "failing_program": "...",
 "category": "wrong-output", "expect_stdout": "42\n", "stdin": null,
 "argv": [], "files": {"in.csv": "a,b\n", "blob.bin": {"base64": "AAH+/w=="}}}
```

Three test kinds: `stdout` (exact or regex, after a **named** normalization),
`script` (an arbitrary stdlib checker that sees stdout, stderr, exit code and the
files the program left behind), and `pytest` (a test file run against
`solution.py`).

### Three populations, and the mixture that is not a default

The corpus is not one task, and which one a case is comes from **its own test**
(`sample.population`), never from its name:

| population | the test | the right answer | trained on |
|---|---|---|---|
| **rewrite** | `lypning`, `require_tier1` true | rewrite it into the subset | yes |
| **ceiling** | `lypning`, `require_tier1` false | keep the import, take the fallback | yes, one target per case |
| **unobserved** | any other kind | no engine is ever asked | no |

Left alone, the mixture is decided by yield, and yield runs backwards: a ceiling
case passes whenever the model can copy, an unobserved case is a plain coding
task the stock model already answers, and the rewrite cases — the ones the
fine-tune exists for — are the hard ones. So the pool is chosen instead.
`nt sample` prints it before it spends anything, and drops four kinds of case:
one that is the same question as a held-out case, one whose population is not
the task, one the engine now RUNS (the program the prompt hands over passes the
case's own test, so the sampler would happily learn to echo it — the train-side
twin of the degenerate held-out cases), and one that nothing passes.

Ceiling cases stay, and keep exactly one target each. They are the counterweight:
train only on "rewrite it" and the model learns to rewrite everything, which is
the damage the ceiling cases in the held-out set exist to catch. But a second
ceiling target is the same answer typed twice — by this repository's own
similarity ceiling, the one it uses to call two cases the same question.

`nt sample` claims its output directory in `backend.json` and refuses one that
another model drew: rejection sampling is on-policy by construction, `draws.jsonl`
is append-only and `fold_draws` reads all of it, so two models pointed at one
directory is off-policy training data with nothing in the report to show it.
`--resume` continues an interrupted run without redrawing what it already has.

`sample.json` reports the mixture and names the rewrite rows separately, because
the abandon threshold in `PREREGISTRATION.md` is a claim about the population the
experiment is about, and a total that counts ceiling rows can clear it while that
population does not.

## Step 1b — the frozen split

70/30, stratified by failure category, assigned by `sha256(salt || case_id)`
rather than a shuffle: no RNG state to carry, same corpus gives the same split.

Frozen means checkable. `data/holdout.lock.json` records every held-out case id
with the SHA-256 of its canonical record, plus a manifest hash over the list.
`./nt verify` recomputes both and fails on any drift, and **step 2 refuses to run
against a drifted split**. After the freeze, corpus growth lands in train only.
`--refreeze` moves the held-out set and says out loud that it has voided every
baseline.

## Step 2 — pass@1, and four things it refuses to do

1. **Measure a drifted split.** Verified before the first request.
2. **Score a harness error as a failed program.** A 500 from the server, a
   timeout talking to it, a sandbox failure — recorded as `harness_error`,
   excluded from the denominator, and counted in the summary. A flaky endpoint
   must not read as a worse model.
3. **Hide the prompt.** The exact template is hashed into every summary.
   `./nt results` marks any run whose prompt or held-out manifest differs from
   the baseline's as `n/c` — not comparable — instead of subtracting two
   different measurements.
4. **Lose work.** Every attempt is appended and fsynced as it lands, so a run
   killed mid-flight — a spot preemption, a closed laptop — resumes from what it
   has instead of re-spending.

The interval is a percentile bootstrap over **cases**, 10,000 resamples, seeded.
A Wilson interval is printed beside it and is not decoration: at small n the
percentile bootstrap degenerates, and with every case passing it reports a
zero-width interval, which is false. When the two disagree the sample is too
small for the question, and that is the finding.

**The win rule, fixed before any training run:** a run counts only if its CI
**lower bound** clears the baseline **point estimate**.

## The sandbox

Every generated program gets its own temp cwd, an environment scrubbed of
credentials (`HF_TOKEN` included), a wall-clock timeout enforced by killing the
process **group**, a CPU rlimit, an address-space cap, a file-size cap that also
bounds stdout, and — where the kernel allows — its own empty network namespace
via `unshare -n`.

It is a **net, not a sandbox**. The filesystem is shared: a program naming an
absolute path can still write to it, and nothing here can undo that. Run harvests
and evals in a throwaway checkout.

## Backends

One OpenAI-shaped door, so the baseline and every later arm are measured by
identical code:

```bash
export NTX_BASE_URL=http://127.0.0.1:8000/v1     # vLLM serving the BF16 checkpoint
export NTX_MODEL=Qwen/Qwen3.8-27B
export NTX_API_KEY=...                            # falls back to HF_TOKEN
./nt probe && ./nt eval --baseline --price-hour 3.69 --max-spend 25
```

Cost is tracked both ways — per token (`NTX_PRICE_IN`/`NTX_PRICE_OUT`) and per
GPU-hour (`--price-hour`) — because a hosted endpoint bills one way and a box you
rented bills the other, and the spend cap has to see their sum.

Serve with a reasoning parser so thinking is split out of `content`
(`--reasoning-parser qwen3` on vLLM); the extractor also handles a server that
hands thinking back inline. Sampling defaults are `pipeline.backends`'s
(temperature 1.0, top_p 0.95); `--no-thinking` sets
`chat_template_kwargs.enable_thinking=false`.

## Zero dependencies

Steps 1 and 2 are stdlib only, including the HTTP client. They run on a laptop, in
CI, and inside the GPU container, and the one thing that must not differ between
those three is the verdict. A dependency is a version that can differ. Step 3 is
where NeMo AutoModel and torch arrive, and they stay on the GPU box.

```bash
uv run --with pytest pytest nemotron/tests -q
```

## What is committed

`data/corpus.jsonl`, `data/drops.jsonl`, `data/harvest.json`,
`data/holdout.lock.json` and `data/baseline.json` — the evidence. `runs/` and the
materialized `train.jsonl`/`holdout.jsonl` are derived and ignored.

## Numbers in this file

There are none, deliberately. Corpus sizes and pass rates change every harvest;
`./nt status` prints the count it actually loaded. Any number quoted in a commit
message or a report carries its run id and its date.
