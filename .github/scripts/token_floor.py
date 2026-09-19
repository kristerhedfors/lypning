"""Count the supervised tokens an SFT schedule will expose, for nothing, in CI.

`run()` refuses a schedule below `MIN_SUPERVISED_TOKENS`, and it refuses it
after the tokenizer download, on a metered GPU, once base weights are already
coming down: the cheapest possible check sits at the most expensive possible
place. The count needs the Qwen tokenizer and nothing else -- a few megabytes,
not 55.6 GB of weights -- so a plain runner with `HF_TOKEN` can answer it
exactly, and `--plan`'s byte upper bound stops being the only answer available
before the money is spent.

Exactly means over the population `run()` counts over, which is not the bank
file. `training.prepare` puts the authored bank through
`training_data.split_cases` at the preparation seed and `run()` keeps
`split == "train"`, so counting `train.jsonl` whole would be a different
measurement wearing the same name: the dev and test cases live in that file
too, and the run never trains on them. The identity block prints the bank's row
count and the population's beside it, and the floor is compared against the
population. This calls `split_cases` itself, and the split seed follows the
schedule seed
because `round02_pilot.sh` drives review, preparation and training from one
`SEED`; `--split-seed` pins a single split for every schedule seed, which is
the `BUNDLES_FROM` case, where the bundle was prepared by an earlier job. Rows
that already carry a `split` -- a prepared bundle's cases -- are filtered, not
re-split. The count is a count of the checkout's bank at the printed
`bank sha256`: a metered job that downloads another bank has another number.

Every number here comes from the functions the stage itself calls. `messages`,
`sft_batches`, `supervised_tokens` and `supervised_plan` are imported;
`build_examples` is compiled out of `training/gpu/lypning_lora.py` because that
module imports torch at module scope (see `borrow`), and its source digest is
printed beside the count so a drift in the borrowed code is visible in the same
log. Only the SFT row construction is copied, because it is inline in `run()`
and not a function; `copied_segment_bytes` checks that copy for equality
against the in-tree expression in `supervised_plan`.

Exit 0 means the count was made, whether it clears the floor or not: a schedule
refused here is the deliverable, not a broken job. A non-zero exit is an input
problem -- an unresolvable revision, a missing wheel, a `build_examples` that
can no longer be borrowed -- and never a verdict on the schedule.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import json
import os
import symtable
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "training" / "gpu"))
sys.path.insert(0, str(ROOT / "training"))

from pipeline.jsonio import sha256_of                                   # noqa: E402
from pipeline.training import chat_prompt_token_ids, messages           # noqa: E402
from pipeline.training_contract import (BASE_MODEL, GPU_VERSIONS,       # noqa: E402
    MIN_SUPERVISED_TOKENS, MIN_TRAIN_CASES, PROTOCOL_TRAIN_SEEDS)
from pipeline.training_data import split_cases                          # noqa: E402
from pipeline.training_types import TrainingError                       # noqa: E402
from train_verified import supervised_plan                              # noqa: E402
from verified_stages import sft_batches, supervised_tokens              # noqa: E402

LORA = ROOT / "training" / "gpu" / "lypning_lora.py"
#: What `training/hf/round02_pilot.sh` would submit today: STEPS=250, and
#: `--batch-size 4` from the same script's SFT arguments.
PILOT_STEPS = 250
#: A grid wide enough that the answer is read off it rather than extrapolated.
DEFAULT_STEPS = (100, 150, 200, PILOT_STEPS, 300, 350, 400, 500)


def require(name):
    """One readable line instead of a traceback when the runner lacks a wheel."""
    try:
        return __import__(name)
    except ImportError:
        raise SystemExit("%s is not installed; .github/workflows/token-floor.yml is the "
                         "environment this expects: transformers==%s, huggingface-hub==%s, jinja2"
                         % (name, GPU_VERSIONS["transformers"], GPU_VERSIONS["huggingface-hub"]))


def borrow(path, name):
    """Compile one function out of a module that cannot be imported here.

    `lypning_lora` imports torch, peft and transformers at module scope and
    calls `torch.cuda.is_available()` on the way past, none of which a free
    runner should be made to install to count tokens. Re-typing
    `build_examples` instead would produce a different measurement wearing the
    same name, so this executes the module's own definition and refuses to
    proceed if that definition has grown a reference to module state -- at
    which point importing it properly is the only honest option.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(nodes) != 1:
        raise SystemExit("%s: expected exactly one def %s, found %d" % (path, name, len(nodes)))
    segment = ast.get_source_segment(source, nodes[0])
    free = _nonlocal_names(segment, name) - set(dir(builtins))
    if free:
        raise SystemExit("%s now reads module state %s; import it instead of borrowing it"
                         % (name, sorted(free)))
    module = ast.fix_missing_locations(ast.Module(body=[nodes[0]], type_ignores=[]))
    namespace = {}
    exec(compile(module, str(path), "exec"), namespace)                 # noqa: S102
    return namespace[name], hashlib.sha256(segment.encode("utf-8")).hexdigest()


def _nonlocal_names(source, name):
    """Every name the function reads from outside itself, nested defs included."""
    table = symtable.symtable(source, "<borrowed>", "exec")
    found = set()
    stack = [c for c in table.get_children() if c.get_name() == name]
    while stack:
        scope = stack.pop()
        for symbol in scope.get_symbols():
            if symbol.is_global() or symbol.is_free():
                found.add(symbol.get_name())
        stack.extend(scope.get_children())
    return found


def load_bank(path):
    """Every row of a bank directory's `train.jsonl`, or of a single JSONL file.

    The rows, not the population: `train_split` derives that, because the file
    named `train.jsonl` is the bank the round trains *from*, not the split it
    trains *on*.
    """
    path = Path(path)
    if path.is_dir():
        path = path / "train.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows, path, hashlib.sha256(path.read_bytes()).hexdigest()


def train_split(rows, seed):
    """The cases `run()` counts over, by calling the function that assigns them.

    `training.prepare` splits an authored bank with
    `training_data.split_cases(cases, seed)` and `run()` keeps `split ==
    "train"`; an authored bank carries no `split` key, so re-deriving it here is
    the only way to count the same population. A prepared bundle's cases carry
    the assignment already and are filtered instead -- re-splitting those would
    answer a question about a bundle that was never prepared. A file that mixes
    the two shapes is neither, and is refused rather than guessed at.
    """
    if is_labelled(rows):
        return ([row for row in rows if row["split"] == "train"],
                "split == train, as labelled in the file")
    if any("split" in row for row in rows):
        raise SystemExit("bank mixes rows with and without a split assignment; it is neither an "
                         "authored bank nor a prepared bundle, and the population is undefined")
    try:
        assigned = split_cases(rows, seed)
    except (TrainingError, KeyError) as exc:
        raise SystemExit("split_cases refuses this bank, so no schedule over it is the one run() "
                         "would build: %s: %s" % (type(exc).__name__, exc))
    return ([case for case in assigned if case["split"] == "train"],
            "split_cases(rows, seed=%d), split == train" % seed)


def is_labelled(rows):
    """Whether the file already carries the assignment, so no seed can move it."""
    return bool(rows) and all("split" in row for row in rows)


def split_label(key):
    """How a population identifies itself in a column heading."""
    return "as labelled" if key is None else "split seed %d" % key


def sft_rows(cases):
    """The SFT rows `run()` builds, copied because they are inline, not a function.

    `train_verified.run()` builds exactly this list immediately before calling
    `build_examples`; keep the two identical or the count is of another
    curriculum. `copied_segment_bytes` re-derives the same assistant segment
    over `supervised_plan`'s own schedule, so a divergence shows up as a failed
    cross-check rather than as a plausible number.
    """
    return [{"case_id": case["case_id"],
             "messages": messages(case) + [{"role": "assistant",
                 "content": "```python\n" + case["reference"].rstrip() + "\n```"}]}
            for case in cases]


def plan_args(steps, batch_size, seed, max_seq, max_new_tokens):
    """The `--plan` argument surface `supervised_plan` and `schedule` read."""
    return SimpleNamespace(stage="sft", smoke=False, steps=steps, batch_size=batch_size,
                           seed=seed, eval_every=25, max_new_tokens=max_new_tokens,
                           max_seq=max_seq, lr=None)


def upper_bound(cases, steps, batch_size, seed, max_seq, max_new_tokens):
    """The byte bound `--plan` prints today, from `--plan`'s own function."""
    bundle = {"cases": [dict(case, split="train") for case in cases]}
    return supervised_plan(plan_args(steps, batch_size, seed, max_seq, max_new_tokens), bundle)


def copied_segment_bytes(cases, steps, batch_size, seed):
    """`supervised_plan`'s bound, recomputed over the rows `sft_rows` builds.

    `supervised_plan` schedules the cases themselves in place of their examples,
    so the same call reproduces its schedule exactly, and summing this script's
    own assistant segments over it must land on the same integer. It is the one
    check available on the copied row construction: equality says the copy still
    says what the in-tree expression says.
    """
    segments = {row["case_id"]: len((row["messages"][-1]["content"] + "<|im_end|>").encode("utf-8"))
                for row in sft_rows(cases)}
    batches = sft_batches(cases, cases, steps, batch_size, seed)
    return sum(segments[case["case_id"]] for batch in batches for case in batch)


def exact_tokens(cases, examples, steps, batch_size, seed):
    """What `run()` computes as `planned_tokens`, by calling what `run()` calls."""
    return supervised_tokens(sft_batches(cases, examples, steps, batch_size, seed))


def minimum_steps(cases, examples, batch_size, seed, floor, limit):
    """The smallest `--steps` whose schedule clears the floor, or None by `limit`.

    A schedule is a prefix of every longer schedule at the same seed, so the
    count rises with steps and bisection is sound; the boundary is re-measured
    on both sides anyway, because an assumption about someone else's RNG is
    worth exactly one confirming call.
    """
    if exact_tokens(cases, examples, limit, batch_size, seed) < floor:
        return None
    low, high = 1, limit
    while low < high:
        mid = (low + high) // 2
        if exact_tokens(cases, examples, mid, batch_size, seed) >= floor:
            high = mid
        else:
            low = mid + 1
    below = exact_tokens(cases, examples, low - 1, batch_size, seed) if low > 1 else 0
    return {"steps": low, "tokens": exact_tokens(cases, examples, low, batch_size, seed),
            "tokens_one_step_below": below,
            "monotone": below < floor}


def tokenizer_identity(tok):
    """The `tokenizer_sha256` the run's own manifest records, so the count has one."""
    return sha256_of({"vocab": tok.get_vocab(), "template": tok.chat_template,
                      "special_tokens": tok.special_tokens_map})


def cache_inventory(cache_dir):
    """Every file this job pulled, with its size: the proof no weights came down."""
    files = []
    for base, _dirs, names in os.walk(cache_dir):
        for name in names:
            path = Path(base) / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                files.append((str(path.relative_to(cache_dir)), path.stat().st_size))
            except OSError:
                continue
    return sorted(files)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bank-path", default=str(ROOT / "training" / "data" / "bank_v2"),
                   help="bank directory holding train.jsonl, or the JSONL itself")
    p.add_argument("--hub-bank", help="path inside the private dataset repo, e.g. banks/v2")
    p.add_argument("--hub-repo", default=os.environ.get("WORK_REPO", "lypning-round02-artifacts"),
                   help="dataset repo for --hub-bank; bare names resolve against whoami")
    p.add_argument("--model", default=os.environ.get("QWEN_MODEL", BASE_MODEL))
    p.add_argument("--revision", default=os.environ.get("QWEN_REV", ""),
                   help="40-character Hub commit; resolved from the Hub when omitted")
    p.add_argument("--steps", type=int, action="append", help="repeatable; defaults to a grid")
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--seed", type=int, action="append",
                   help="repeatable; defaults to the protocol seeds")
    p.add_argument("--split-seed", type=int,
                   help="preparation seed for split_cases; omitted, each schedule seed splits "
                        "with itself, as round02_pilot.sh's single SEED does")
    p.add_argument("--max-seq", type=int, default=4096)
    p.add_argument("--max-new-tokens", type=int, default=1024)
    p.add_argument("--limit-steps", type=int, default=4096, help="search ceiling for the minimum")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    steps_grid = sorted(set(args.steps or DEFAULT_STEPS))
    seeds = tuple(args.seed or PROTOCOL_TRAIN_SEEDS)
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set; the tokenizer may be gated", file=sys.stderr)

    build_examples, borrowed_sha = borrow(LORA, "build_examples")

    bank_path = args.bank_path
    if args.hub_bank:
        require("huggingface_hub")
        from huggingface_hub import HfApi, hf_hub_download
        api = HfApi(token=token or None)
        repo = args.hub_repo if "/" in args.hub_repo else "%s/%s" % (
            api.whoami()["name"], args.hub_repo)
        bank_path = hf_hub_download(repo, args.hub_bank.strip("/") + "/train.jsonl",
                                    repo_type="dataset", token=token or None)
        print("== bank from the Hub: %s %s" % (repo, args.hub_bank))
    rows, bank_file, bank_sha = load_bank(bank_path)

    def split_seed_of(seed):
        """The preparation seed that decided the split this schedule trains on.

        `None` when the file carries the assignment already: there is one
        population then, and no seed of ours moves it.
        """
        if is_labelled(rows):
            return None
        return seed if args.split_seed is None else args.split_seed

    populations = {}
    for seed in seeds:
        key = split_seed_of(seed)
        if key not in populations:
            populations[key] = train_split(rows, key)

    print("== identity")
    print("   %-26s %s" % ("model", args.model))
    revision = args.revision.strip()
    if not revision:
        require("huggingface_hub")
        from huggingface_hub import HfApi
        revision = HfApi(token=token or None).model_info(args.model).sha or ""
    if len(revision) != 40:
        print("   refusing a revision that is not a 40-character commit: %r" % revision,
              file=sys.stderr)
        return 2
    print("   %-26s %s" % ("revision", revision))
    print("   %-26s %s" % ("bank", bank_file))
    print("   %-26s %s" % ("bank sha256", bank_sha))
    print("   %-26s %d row(s), which is the bank and not the population"
          % ("bank file", len(rows)))
    for key in populations:
        subset, basis = populations[key]
        print("   %-26s %d  (floor %d)  %s"
              % ("train cases run() sees", len(subset), MIN_TRAIN_CASES, basis))
    print("   %-26s %s" % ("build_examples sha256", borrowed_sha))
    print("   %-26s %s" % ("python", sys.version.split()[0]))

    transformers = require("transformers")
    # jinja2 renders the chat template, and it is a torch dependency rather than
    # a transformers one: the metered job gets it because it installs torch, and
    # this one only has it because the workflow names it. Asking here costs one
    # import and turns the alternative -- an ImportError raised from inside
    # `apply_chat_template`, forty lines further down and after the identity
    # block has already printed -- into the line above.
    require("jinja2")
    from transformers import AutoTokenizer
    print("   %-26s %s  (experiment pins %s)"
          % ("transformers", transformers.__version__, GPU_VERSIONS["transformers"]))
    if transformers.__version__.split("+", 1)[0] != GPU_VERSIONS["transformers"]:
        print("   the tokenizer is not the pinned one; treat the count as indicative", file=sys.stderr)

    tok = AutoTokenizer.from_pretrained(args.model, revision=revision, token=token or None)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    print("   %-26s %s" % ("tokenizer sha256", tokenizer_identity(tok)))
    print("   %-26s %s" % ("vocabulary", len(tok)))
    terminator = tok.encode("<|im_end|>", add_special_tokens=False)
    agrees = tok.eos_token_id is not None and terminator == [tok.eos_token_id]
    print("   %-26s %s  (eos %s, <|im_end|> %s)"
          % ("run() eos admission", "PASS" if agrees else "FAIL", tok.eos_token_id, terminator))

    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        inventory = cache_inventory(HF_HUB_CACHE)
        print("== downloaded (%d file(s), %.1f MB total)"
              % (len(inventory), sum(size for _n, size in inventory) / 1e6))
        for name, size in inventory:
            print("   %10d  %s" % (size, name))
    except Exception as exc:                                            # noqa: BLE001
        print("   cache inventory unavailable: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)

    # The workflow log is world-readable, so this stays a statistic: a count and
    # a longest prompt, never a case id or a line of the bank.
    # Over every bank row, which is the superset of the train and dev cases
    # run() admits: this one is split-independent on purpose, so a row too long
    # for the budget is reported whichever split it lands in.
    print("\n== prompt budget (run() refuses before any of this)")
    # `tokenize=True` answers `len()` in two different currencies: a list of
    # token ids under transformers 4.x, a `BatchEncoding` of two keys under the
    # 5.x this workflow pins. Counting the call rather than its ids reads every
    # prompt as two tokens, so the counting goes through the one extraction the
    # stage itself uses -- `chat_prompt_token_ids` -- and a shape that is
    # neither raises there. The shape is exercised once, on the row whose
    # length is about to be counted, so a third shape refuses here with a line
    # rather than a traceback forty rows into the sweep.
    if not rows:
        print("the bank file holds no rows, so there is no prompt budget to measure",
              file=sys.stderr)
        return 2
    try:
        sample = chat_prompt_token_ids(tok, messages(rows[0]))
    except TrainingError as exc:
        print("the prompt budget cannot be measured: %s" % exc, file=sys.stderr)
        return 2
    print("   %-26s %s of token ids, counted through chat_prompt_token_ids"
          % ("chat template shape", type(sample).__name__))
    prompts = [len(chat_prompt_token_ids(tok, messages(c))) for c in rows]
    over = sum(length + args.max_new_tokens > args.max_seq for length in prompts)
    print("   %-26s %d bank row(s) over --max-seq %d with --max-new-tokens %d"
          % ("prompt + completion", over, args.max_seq, args.max_new_tokens))
    print("   %-26s mean %.1f, max %d" % ("prompt tokens", sum(prompts) / len(prompts), max(prompts)))

    built = {}
    for key in populations:
        examples, dropped = build_examples(tok, sft_rows(populations[key][0]), args.max_seq)
        print("   %-26s %s: %d built, %d dropped over --max-seq"
              % ("supervised examples", split_label(key), len(examples), dropped))
        if dropped or not examples:
            print("   run() raises here: SFT rows over token limit; the schedule is not "
                  "the question yet", file=sys.stderr)
            return 0
        per_example = [supervised_tokens([[example]]) for example in examples]
        print("   %-26s %s: mean %.1f, min %d, max %d"
              % ("supervised tokens/example", split_label(key), sum(per_example) / len(per_example),
                 min(per_example), max(per_example)))
        built[key] = examples

    def population(seed):
        """The (cases, examples) pair `run()` builds for a schedule at this seed."""
        key = split_seed_of(seed)
        return populations[key][0], built[key]

    print("\n== supervised tokens by --steps, at --batch-size %d (floor %d)"
          % (args.batch_size, MIN_SUPERVISED_TOKENS))
    if len(populations) == 1:
        print("   every column, and the plan bound, counts over one population (%s)"
              % split_label(split_seed_of(seeds[0])))
    else:
        print("   each seed column counts over the split run() would train on at that seed "
              "(%s); the plan bound is over %s"
              % (", ".join("seed %d: %s" % (s, split_label(split_seed_of(s))) for s in seeds),
                 split_label(split_seed_of(seeds[0]))))
    print("   %6s %10s %s %12s  %s" % ("steps", "exposures",
                                       " ".join("%12s" % ("seed " + str(s)) for s in seeds),
                                       "plan bound", "verdict"))
    for steps in steps_grid:
        exact = []
        for seed in seeds:
            subset, examples = population(seed)
            exact.append(exact_tokens(subset, examples, steps, args.batch_size, seed))
        bound = upper_bound(population(seeds[0])[0], steps, args.batch_size, seeds[0],
                            args.max_seq, args.max_new_tokens)
        print("   %6d %10d %s %12d  %s"
              % (steps, steps * args.batch_size,
                 " ".join("%12d" % value for value in exact),
                 bound["supervised_token_upper_bound"],
                 "PASS" if min(exact) >= MIN_SUPERVISED_TOKENS else "REFUSED"))

    steps = steps_grid[-1]
    primary = population(seeds[0])[0]
    probe = upper_bound(primary, steps, args.batch_size, seeds[0], args.max_seq, args.max_new_tokens)
    mine = copied_segment_bytes(primary, steps, args.batch_size, seeds[0])
    print("   plan bound cross-check: %s (plan %d, this script's copied rows %d)"
          % ("agrees" if mine == probe["supervised_token_upper_bound"] else "DIVERGED",
             probe["supervised_token_upper_bound"], mine))

    pilot = []
    for seed in seeds:
        subset, examples = population(seed)
        pilot.append(exact_tokens(subset, examples, PILOT_STEPS, args.batch_size, seed))
    print("\n== the submit the pilot script would make, --steps %d --batch-size 4" % PILOT_STEPS)
    print("   %-26s %s" % ("supervised tokens",
                           ", ".join("seed %d: %d" % pair for pair in zip(seeds, pilot))))
    print("   %-26s %s  (floor %d)" % ("run() would",
                                       "TRAIN" if min(pilot) >= MIN_SUPERVISED_TOKENS
                                       else "REFUSE before the first optimizer step",
                                       MIN_SUPERVISED_TOKENS))

    print("\n== the smallest --steps that clears the floor")
    for seed in seeds:
        subset, examples = population(seed)
        found = minimum_steps(subset, examples, args.batch_size, seed, MIN_SUPERVISED_TOKENS,
                              args.limit_steps)
        if found is None:
            print("   seed %-6d no schedule below --steps %d reaches %d"
                  % (seed, args.limit_steps, MIN_SUPERVISED_TOKENS))
            continue
        print("   seed %-6d --steps %d -> %d tokens (one step below: %d)%s"
              % (seed, found["steps"], found["tokens"], found["tokens_one_step_below"],
                 "" if found["monotone"] else "  NOT MONOTONE: bisection assumption broken"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
