# Nemotron 3.5 Lightning — LoRA pipeline

Target: `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16` (30B total, 3B active,
hybrid Mamba-2 + MoE + attention, reasoning on by default). The NVFP4 release is
inference-only and is not the training checkpoint.

Four steps, built strictly in order, because each one is the measuring instrument
for the next:

1. **Corpus** — failing generation cases in, executable acceptance tests out.
2. **Eval** — pass@1 over a frozen held-out split, with a bootstrap 95% CI.
3. **Training** — rank-16 LoRA via NeMo AutoModel, data composition swept.
4. **Sweep** — one row per run, a win defined before any run happens.

**Steps 1 and 2 are built. Steps 3 and 4 are not started**, by instruction: no
training config is proposed until there is a baseline to beat.

## One-line commands

```bash
./nt harvest            # step 1: sources -> corpus.jsonl + drops.jsonl
./nt split              # freeze the 70/30 stratified held-out split
./nt verify             # re-check the frozen split against its lock
./nt probe              # one cheap request; is the backend reachable
./nt eval --baseline    # step 2, detached under tmux; returns immediately
./nt status             # one screen: corpus, baseline, running, spend, ETA
./nt results            # leaderboard by delta vs baseline
./nt show RUN --failed-only   # the programs, and why each failed
```

Nothing long-running runs in the foreground. `./nt eval` hands the job to tmux and
returns a run id; `status` and `results` are the only two commands needed after.

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

**Sources** are adapters (`pipeline/adapters.py`). Shipped: `study` (this
repository's 26-task bank), `evalfail` (the failures of a step-2 run — the loop
that actually produces "previously-failing cases"), and `jsonl` (anything else,
with `--map dst->src` to rename fields). A new source is one function.

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
export NTX_MODEL=nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16
export NTX_API_KEY=...                            # falls back to HF_TOKEN
./nt probe && ./nt eval --baseline --price-hour 3.69 --max-spend 25
```

Cost is tracked both ways — per token (`NTX_PRICE_IN`/`NTX_PRICE_OUT`) and per
GPU-hour (`--price-hour`) — because a hosted endpoint bills one way and a box you
rented bills the other, and the spend cap has to see their sum.

Serving recipe from the model card (vLLM `v0.27.1`, single H100 80GB, BF16):
`--reasoning-parser nemotron_v3` splits thinking out of `content` for us. Sampling
follows the card: temperature 1.0, top_p 0.95. `--no-thinking` sets
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
