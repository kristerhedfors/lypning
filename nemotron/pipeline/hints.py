"""Context distillation: say the constraint while SAMPLING, never while training.

WHY THIS EXISTS, AND WHAT IT IS FIXING
    The run of record moved correctness by +4.92pp and subset legality by
    -1.00pp, 95% CI [-3.36, +1.51] — a null against a base-vs-base null of
    -0.22pp (`REVIEW.md` §1). The adapter learned to solve these tasks slightly
    better and learned nothing about the subset.

    Reading the prompt says why, and it is not subtle. `evaluate.render_messages`
    is two turns: "You are a precise Python programmer", and the task plus a
    runtime contract. **Neither mentions lypning, the subset, or a refusal**, and
    the SAME function drew the SFT targets. So every training example was a
    program selected for being subset-legal, paired with a prompt that never said
    so. Nothing in the gradient could point at the constraint, because the
    constraint was never in the input — only in the filter that chose which
    outputs survived. Rejection sampling on its own sharpens the base model's
    existing prior; it cannot install one the prompt never mentioned.

THE ASYMMETRY IS THE WHOLE TECHNIQUE
    Sample **with** the hint: the engine's own refusal line for this case, and a
    worked before/after recipe for that refusal kind out of `docs/COOKBOOK.md`.
    That raises the yield of subset-legal programs, and it raises it on exactly
    the cases where the base prior is worst — which is where a rejection sampler
    otherwise gets nothing to learn from.

    Train on the **bare** prompt, the one eval uses. The hint is deleted from the
    training row. What the gradient then has to explain is a program that stays
    inside the subset in response to a prompt that never asked for it, which is
    the behaviour deployment needs: the agent writing the one-liner does not know
    lypning exists.

    `render_hinted_messages` is therefore used at exactly ONE call site — the
    draw — and `render_messages` at the other two, the SFT row and the eval. Any
    change that leaks the hint into either of those is not context distillation;
    it is a prompt that will not be there when the number is taken, and it would
    move `prompt_signature`, which `nt grade` refuses to compare across.

THE REFUSAL IS PROBED, NOT REMEMBERED
    A case records the refusal it was harvested with, and that string goes stale
    in the one direction that matters: the engine keeps learning. 318 of the 443
    train cases carry `module: import math` and friends, and the engine has
    served `math` since 2026-09-12. A hint quoting a refusal the engine no longer
    gives teaches the model to route around something that already works, so the
    line is taken from the binary, now, and a case the engine now runs yields no
    hint at all.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import refusals

#: `<!-- recipe id=… kind=… detail="…" … -->` followed by a before block and an
#: after block. Only the four fields this module reads are parsed; the battery in
#: `tests/test_cookbook.py` owns the rest and executes them, which is what keeps
#: the pairs honest. Parsed rather than copied for the reason `CLAUDE.md` gives
#: for every table in this repository: a second copy is the one that goes stale.
_MARK = re.compile(r"<!--\s*recipe\s+(?P<attrs>[^>]*?)-->", re.S)
_ATTR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
_FENCE = re.compile(r"```python\n(.*?)```", re.S)


def parse_cookbook(md: str) -> List[Dict[str, str]]:
    """Every recipe as ``{id, kind, detail, before, after}``, in document order."""
    out: List[Dict[str, str]] = []
    marks = list(_MARK.finditer(md))
    for i, m in enumerate(marks):
        attrs = dict(_ATTR.findall(m.group("attrs")))
        end = marks[i + 1].start() if i + 1 < len(marks) else len(md)
        blocks = _FENCE.findall(md[m.end():end])
        if len(blocks) < 2:
            continue

        def unquote(v: str) -> str:
            if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                try:
                    return ast.literal_eval(v)
                except (ValueError, SyntaxError):
                    return v[1:-1]
            return v

        out.append({"id": unquote(attrs.get("id", "")),
                    "kind": unquote(attrs.get("kind", "")),
                    "detail": unquote(attrs.get("detail", "")),
                    "before": blocks[0].strip(), "after": blocks[1].strip()})
    return out


def load_cookbook(root: Optional[Path] = None) -> List[Dict[str, str]]:
    """Read `docs/COOKBOOK.md` from the repository this package sits in."""
    base = root or Path(__file__).resolve().parents[2]
    path = base / "docs" / "COOKBOOK.md"
    if not path.is_file():
        return []
    return parse_cookbook(path.read_text(encoding="utf-8"))


def negatives_of(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The recorded failing programs, whichever way the row stored them."""
    raw = case.get("negatives")
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []
    return [n for n in (raw or []) if isinstance(n, dict) and n.get("program")]


def live_refusal(case: Dict[str, Any], engine: str) -> Optional[Dict[str, str]]:
    """What THIS engine says about this case's negative today, or None.

    None is the honest answer in two different situations and the caller treats
    them the same: the case has no recorded negative to probe, and the engine has
    since learned to run the one it has. Both mean there is no refusal to quote,
    and a hint invented for either would be a hint about nothing.
    """
    for neg in negatives_of(case):
        got = refusals.probe(neg["program"], engine)
        if got:
            return got
    return None


def recipe_for(kind: str, detail: str,
               recipes: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """The worked pair closest to this refusal: exact detail first, then kind.

    Exact-detail beats same-kind because `module: import re` and `module: import
    subprocess` are one kind and two unrelated rewrites, and a recipe for the
    wrong one is worse than none — it is a confident, worked, irrelevant answer
    in the context window.
    """
    same_kind = [r for r in recipes if r["kind"] == kind]
    for r in same_kind:
        if r["detail"] == detail:
            return r
    return same_kind[0] if same_kind else None


HINT_TEMPLATE = """\
This program will be run by a Python interpreter that implements a SUBSET of \
Python. It executes the subset in-process and is fast; anything outside the \
subset it refuses, and the program is then re-run by CPython, which costs a \
process spawn. Both produce the same answer, so this is about cost, not \
correctness.

The obvious solution to this task is refused:

    {engine}: unsupported: {kind}: {detail}
{recipe}
Write a program that produces exactly the same output and stays inside the \
subset. If that is impossible for this task, write the ordinary Python — a \
wrong answer inside the subset is worth much less than a right answer outside \
it."""

_RECIPE_BLOCK = """
Here is a worked example of the same kind of rewrite:

```python
# refused
{before}
```

```python
# accepted, same output
{after}
```
"""


def render_hint(engine_name: str, kind: str, detail: str,
                recipe: Optional[Dict[str, str]]) -> str:
    return HINT_TEMPLATE.format(
        engine=engine_name, kind=kind, detail=detail,
        recipe=_RECIPE_BLOCK.format(**recipe) if recipe else "")


def render_hinted_messages(case: Dict[str, Any], hint: str,
                           render_messages: Any) -> List[Dict[str, str]]:
    """The bare messages with the hint appended to the USER turn.

    Appended to the user turn rather than the system prompt on purpose: the
    system prompt is one of the two strings `evaluate.prompt_signature` hashes,
    and an arm whose signature moved cannot be compared with one whose did not.
    This function must never be reachable from the eval or from the SFT row, and
    the signature is the tripwire that says so.
    """
    msgs = [dict(m) for m in render_messages(case)]
    msgs[-1]["content"] = msgs[-1]["content"] + "\n\n" + hint
    return msgs


def hint_for(case: Dict[str, Any], engine: str, engine_name: str,
             recipes: List[Dict[str, str]]) -> Tuple[Optional[str], str]:
    """``(hint, why)`` — ``why`` names the reason when there is no hint."""
    got = live_refusal(case, engine)
    if not got:
        return None, ("no recorded negative" if not negatives_of(case)
                      else "the engine now runs this case's negative")
    recipe = recipe_for(got["kind"], got["detail"], recipes)
    return (render_hint(engine_name, got["kind"], got["detail"], recipe),
            got["kind"] + (" +recipe:" + recipe["id"] if recipe else " (no recipe)"))
