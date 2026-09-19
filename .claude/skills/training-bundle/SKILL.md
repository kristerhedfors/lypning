---
name: training-bundle
description: Turn an authored lypning case bank into a verified, sealed training bundle and get it through every admission gate — training-prepare (local subprocess for fixtures, pooled HF sandbox for a pilot), data_loop review manifests, the plan gate, and the case/token/seed/k floors. Also covers publishing a bank to the private HF dataset repo so `round02_pilot.sh` can find it. TRIGGER on requests like "prepare the bundle", "why is preparation blocked", "make this bank a pilot", "run the plan gate", "training blocked: smoke data cannot launch a real run", "reference native/fallback label is stale", or "get the bank onto the VM". SKIP for authoring new cases (use `training-cases`) and for deciding whether a round should run at all — that is `STATUS.md` §10 and the operator, never this skill.
---

# Preparing and sealing a training bundle

`training-prepare` takes a bank of cases and produces a **bundle**: every
reference executed and verified, `population` decided by the engine rather than
by the label, a train/dev/test split grouped by family, and a digest that the
training stage pins. Nothing downstream trusts the bank; everything trusts the
bundle.

## The engine first, and pinned

The bundle records the engine's identity, so build it with the round's oracle
before preparing anything. A bundle prepared against a different CPython is a
different bundle:

```bash
export PYTHONPATH=src:training LYPNING_CAPTURE=0 LYPNING_HARVEST=0
ROUND_PYTHON=$(uv python find 3.12)
export LYPNING_CPYTHON="$ROUND_PYTHON"
export LYPNING_HOME="$PWD/work/round-02/engine-home"
"$ROUND_PYTHON" -m lypning build --rust --target host
export LYPNING_L_BIN="$LYPNING_HOME/bin/lypning-l"
"$LYPNING_L_BIN" --version      # must say: for cpython 3.12
```

Set `LYPNING_HOME` per session. Sessions share `~/.lypning/bin` otherwise, and
one session's build replaces the binary another is measuring.

## Smoke shape versus pilot shape

Preparation has two contracts and they are not interchangeable.

**Fixture / smoke** — local subprocess, no image, admitted only for reviewed CPU
fixtures. Good for checking a bank's shape fast:

```bash
"$ROUND_PYTHON" -m pipeline.cli training-prepare \
  --cases training/data/bank_v2/train.jsonl \
  --engine "$LYPNING_L_BIN" --output work/round-02/bankcheck
```

**Pilot / benchmark** — requires a review manifest *and* an isolated execution
image, because generated code will run. It cannot be done on a laptop without
the Hub, and `round02_pilot.sh` already does it inside the job:

```bash
python3 -m pipeline.data_loop --cases "$BANK_DIR/train.jsonl" \
  --purpose pilot --seed 1111 --output "$ROUND/reviewed-train"
python3 -m pipeline.cli training-prepare \
  --cases "$ROUND/reviewed-train/cases.jsonl" --review "$ROUND/reviewed-train/review.json" \
  --purpose pilot --execution-kind hf-sandbox-pool \
  --execution-image "hf.co/spaces/$SPACE_REPO" --execution-revision "$SPACE_REV" \
  --engine "$LYPNING_L_BIN" --output "$ROUND/pilot"
```

If you prepared with the fixture shape and then tried to train, you get
`training blocked: smoke data cannot launch a real run; prepare an admitted
pilot bundle`. That is the purpose field, not the data.

## Reading what preparation refuses

Every one of these is preparation being right. None is fixed by a flag.

| message | what it means | fix |
|---|---|---|
| `need at least three independently specified tests` | a case has fewer than 3 tests, or fewer than 2 distinct expected stdouts | group more inputs per case; a case whose tests all agree cannot fail |
| `duplicate case id or prompt` | two cases share a `task` | parameterise the task text so each case asks a different question |
| `reference native/fallback label is stale: <id>` | you labelled a case `fallback-control` that the engine serves, or the reverse | probe the binary (`"$E" -c "import x; ..."`, exit 90 is a refusal) and relabel; capabilities change, so re-probe rather than trusting a list |
| `smoke data cannot launch a real run` | the bundle's purpose is smoke | prepare with `--purpose pilot` plus `--review` and an execution image |

## What a good bundle looks like

```bash
python -c "
import json; from collections import Counter
b=json.load(open('work/round-02/bankcheck/bundle.json'))
print('digest', b['digest'][:16], 'cases', len(b['cases']))
for sp in ('train','dev','test'):
    cs=[c for c in b['cases'] if c['split']==sp]
    print(sp, len(cs), 'families', len({c['family'] for c in cs}),
          dict(Counter(c.get('population') for c in cs)))"
```

Check: **train >= 1,000**, every split carries both populations over at least
two families each, and the family count is the number you report as
independence — not the case count.

## The plan gate, and the one floor it cannot settle

```bash
"$ROUND_PYTHON" training/gpu/train_verified.py sft --plan \
  --bundle <bundle.json> --engine "$LYPNING_L_BIN" --output <new dir> \
  --revision <40-char Qwen commit> --steps 250 --batch-size 4 --seed 1111
```

`--plan` imports no torch and downloads nothing. It settles k=16, the >=1,000
train cases, the seed being one of 1111/2222/3333, and one complete family
cycle. It does **not** settle the >=50,000 supervised-token floor: that is
checked in `run()` after the tokenizer download, because counting assistant
tokens needs the tokenizer `--plan` exists to avoid. `preflight` only refuses
the certainly-too-small half, by summing the scheduled references' UTF-8 bytes
as a one-sided upper bound. **A passing plan is not a passing schedule** — the
exact count is recorded as `planned_supervised_tokens`, and that is the number a
report quotes. `steps x batch_size` counts example exposures and is not a
substitute.

## Getting the bank to the VM

`round02_pilot.sh` reads `$BANK_PATH` from the **private dataset repo**, not from
git: it wants `eval2.jsonl`, `train.jsonl` and any `evidence-*/` directories, and
prints `none (authored bank)` when there are no snapshots. Commit the bank to
git for review and history, then upload it from CI, where the token lives:

```python
from huggingface_hub import HfApi
api = HfApi(token=os.environ["HF_TOKEN"])
api.upload_folder(repo_id=WORK_REPO, repo_type="dataset",
                  folder_path="training/data/bank_v2", path_in_repo=BANK_PATH,
                  allow_patterns=["train.jsonl", "eval2.jsonl"])
```

The destination must already be **private**; the launcher refuses a public one
and never flips visibility. A GitHub Actions secret is write-only, so this
cannot be done from a laptop — it has to run in the job.

## Before you call it done

- `eval2-leaks` exits 0 between the two banks (see `training-cases`).
- The bundle digest is recorded wherever you quote its numbers.
- The report says cases **and** families, and says who reviewed the data.
- `STATUS.md` §10 still says this rung may run. A prepared bundle is not an
  authorisation, and no paid rung runs before the rung below it has been read.
