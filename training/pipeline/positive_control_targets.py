"""Fold graded paired draws into a private rejection-sampling SFT set.

The positive-control grader, rather than this module, executes programs.  This
module accepts only its exactly paired rows, joins them back to the raw private
completions, keeps programs with the population's required verdict, and renders
the original bare task prompt as the training input.

Which arm's draws are harvested is an explicit, recorded choice.  The reviewed
default is ``("subset-spec",)``: conditioned draws, verifier-filtered, trained
behind the bare prompt -- context distillation, where the subset specification
selected the target but is not present when it is learned or evaluated.
``("bare",)`` is rejection sampling on the model's own draws under the training
prompt (ReST-EM, arXiv 2312.06585; RFT, arXiv 2308.01825).  The arms go into
``sft-report.json``, and the trainer admits a row only from an arm it names.

Within a case, draws are kept for diversity, not arrival: programs are
de-duplicated by normalised AST (``ast.dump``: formatting and comments do not
make a second example) before the per-case cap, in a fixed draw-then-arm order.
A target whose supervised segment cannot fit the evaluation's generation budget
is dropped, because training on an answer the model is never allowed to finish
teaches it an answer it cannot produce at evaluation.
"""
from __future__ import annotations

import ast
from collections import Counter
import hashlib

from .jsonio import sha256_of
from .positive_control_generate import request_order
from .training import messages, program_from_completion
from .training_types import TrainingError

#: Every arm the paired generator draws, in the canonical harvest order.
ARMS = ("bare", "subset-spec")

#: The reviewed target source (Codex's report): conditioned draws only.
DEFAULT_ARMS = ("subset-spec",)

#: The evaluation's generation budget (``train_verified --max-new-tokens``).
MAX_NEW_TOKENS = 1024

#: What the trainer appends to the assistant turn and supervises with it
#: (``lypning_lora.build_examples``).
TERMINATOR = "<|im_end|>"


def _keys(rows):
    return {(row.get("case_id"), row.get("draw"), row.get("arm")) for row in rows}


def normalise_arms(arms):
    """A non-empty, duplicate-free subset of ARMS, in canonical order."""
    if isinstance(arms, str) or not arms:
        raise TrainingError("target arms must be a non-empty sequence of arm names")
    arms = list(arms)
    if len(arms) != len(set(arms)) or not set(arms) <= set(ARMS):
        raise TrainingError("target arms must be distinct members of %s" % (ARMS,))
    return tuple(arm for arm in ARMS if arm in arms)


def assistant_turn(program):
    return "```python\n" + program.rstrip() + "\n```"


def ast_key(program):
    """Identity of a program up to formatting and comments.

    ``ast.dump`` omits line and column attributes by default, so two draws
    differing only in whitespace, comments or redundant parentheses map to one
    key.  A program this interpreter cannot parse keeps its exact text as its
    key: the grader already ran it, so dropping it here would be a silent veto.
    """
    try:
        return "ast:" + ast.dump(ast.parse(program))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return "text:" + program


def fits(content, max_new_tokens, token_count=None):
    """Whether one supervised segment fits the generation budget.

    With a tokenizer, the segment is counted as the trainer builds it (content
    plus the terminator).  Without one, UTF-8 bytes are the bound: under
    byte-level BPE a segment never costs more tokens than it has bytes, so the
    byte rule never admits an over-long target.  It may drop one that would
    have fit, and the report says which rule ran.
    """
    segment = content + TERMINATOR
    if token_count is not None:
        return int(token_count(segment)) <= max_new_tokens
    return len(segment.encode("utf-8")) <= max_new_tokens


def build_targets(cases, completions, graded, *, samples, coverage_keep=4,
                  control_keep=1, run_id="", lineage=None, arms=DEFAULT_ARMS,
                  max_new_tokens=MAX_NEW_TOKENS, token_count=None, tokenizer=None):
    """Return ``(sft_rows, report)`` from a complete paired private run.

    Coverage cases contribute only correct-native programs.  The
    fallback-control population contributes correct-control programs, preserving
    examples where refusing native execution is the right behavior.  Only draws
    from ``arms`` are harvested.  Programs over the length budget are dropped,
    then de-duplicated within case by normalised AST, then capped per case.
    ``token_count`` maps a string to its token count under the trained model's
    tokenizer, named by ``tokenizer`` (repo@revision) in the report; without it
    the conservative byte bound applies.
    """
    if (type(coverage_keep) is not int or coverage_keep < 1 or
            type(control_keep) is not int or control_keep < 1):
        raise TrainingError("target retention caps must be positive integers")
    if type(max_new_tokens) is not int or max_new_tokens < 1:
        raise TrainingError("max_new_tokens must be a positive integer")
    if (token_count is None) != (tokenizer is None):
        raise TrainingError("a token count must name its tokenizer, and only then")
    arms = normalise_arms(arms)
    by_case = {case["case_id"]: case for case in cases}
    if len(by_case) != len(cases):
        raise TrainingError("target cases must have unique ids")
    expected = {(c["case_id"], draw, arm)
                for c, draw, arm in request_order(cases, samples)}
    if (len(completions) != len(expected) or len(graded) != len(expected) or
            _keys(completions) != expected or _keys(graded) != expected):
        raise TrainingError("targets require complete, exactly paired generation and grade rows")
    completion_by_key = {(row["case_id"], row["draw"], row["arm"]): row
                         for row in completions}
    grade_by_key = {(row["case_id"], row["draw"], row["arm"]): row
                    for row in graded}
    if len(completion_by_key) != len(completions) or len(grade_by_key) != len(graded):
        raise TrainingError("duplicate generation or grade key")

    eligible = Counter()
    rejected = Counter()
    selected = []
    for case in sorted(cases, key=lambda row: row["case_id"]):
        population = case["population"]
        want = "correct-native" if population == "coverage" else "correct-control"
        limit = coverage_keep if population == "coverage" else control_keep
        seen, shapes = set(), set()
        # Draw-major, then canonical arm order: fixed before any outcome, and
        # with two arms each draw index offers both before the next is read.
        for draw in range(samples):
            for arm in arms:
                key = (case["case_id"], draw, arm)
                score = grade_by_key[key]
                raw = completion_by_key[key]
                if score.get("status") != want:
                    rejected[str(score.get("status") or "missing-status")] += 1
                    continue
                if population == "fallback-control" and score.get("native"):
                    rejected["control-became-native"] += 1
                    continue
                program = program_from_completion(raw.get("completion"))
                if not program:
                    raise TrainingError("a passing grade has no unambiguous program: %s" % (key,))
                content = assistant_turn(program)
                # Before dedup and cap, so an unusable draw never holds a slot.
                if not fits(content, max_new_tokens, token_count):
                    rejected["over-max-new-tokens"] += 1
                    continue
                digest = hashlib.sha256(program.encode("utf-8")).hexdigest()
                if digest in seen:
                    rejected["duplicate-program"] += 1
                    continue
                seen.add(digest)
                shape = ast_key(program)
                if shape in shapes:
                    rejected["duplicate-ast"] += 1
                    continue
                shapes.add(shape)
                eligible[population] += 1
                if len(shapes) > limit:
                    rejected["over-retention-cap"] += 1
                    continue
                selected.append({
                    "case_id": case["case_id"],
                    "messages": messages(case) + [{"role": "assistant", "content": content}],
                    "population": population,
                    "family": case["family"],
                    "source": {"run_id": run_id, "arm": arm, "draw": draw,
                               "program_sha256": digest},
                })
    selected.sort(key=lambda row: (row["case_id"], row["source"]["draw"],
                                   ARMS.index(row["source"]["arm"])))
    populations = Counter(row["population"] for row in selected)
    source = " and ".join({"bare": "bare-prompt", "subset-spec": "conditioned"}[arm]
                          for arm in arms)
    report = {
        "schema": 1,
        "run_id": run_id,
        "lineage": dict(lineage or {}),
        "cases": len(cases),
        "samples_per_arm": samples,
        "arms": list(arms),
        "coverage_keep": coverage_keep,
        "control_keep": control_keep,
        "rows": len(selected),
        "cases_with_targets": len({row["case_id"] for row in selected}),
        "families_with_targets": len({row["family"] for row in selected}),
        "populations": dict(sorted(populations.items())),
        "arm_rows": dict(sorted(Counter(row["source"]["arm"] for row in selected).items())),
        "eligible_before_cap": dict(sorted(eligible.items())),
        "rejected": dict(sorted(rejected.items())),
        "length_policy": {"max_new_tokens": max_new_tokens,
                          "measure": "tokens" if token_count is not None else "utf8-bytes",
                          "tokenizer": tokenizer,
                          "segment": "assistant turn plus " + TERMINATOR},
        "case_set_sha256": sha256_of(sorted(by_case)),
        "sft_sha256": sha256_of(selected),
        "prompt_policy": ("bare training prompt; conditioned subset specification absent"
                          if "subset-spec" in arms else "bare training prompt, as drawn"),
        "selection_policy": {
            "coverage": source + " correct-native",
            "fallback-control": source + " correct-control with zero fully-native outcomes",
            "diversity": "normalised-AST dedup before the per-case cap; draw-then-arm order",
        },
    }
    return selected, report
