"""The corpus this project is actually about: real captured python invocations.

A "previously-failing case" here is a program lypning **refused** — exit 90, one
`unsupported:` line, and a full CPython spawn instead of an in-process answer.
There are hundreds of them in `assets/corpus/corpus.jsonl`, they were typed by
real agents in real sessions, and each one carries its own reason.

WHAT A CASE BECOMES. Two shapes, decided by whether the refusal is dodgeable:

*Trainable* — the refusal has an exact substitution (`Counter` -> a dict,
`re.sub(r"\\s+"," ",s)` -> `" ".join(s.split())`, `csv` -> `split(",")`). The case
asks for an equivalent program that stays on the engine; the original program is
the negative control, because it is *correct* and still fails the routing leg.
There is no reference solution, so the `satisfiable` gate records
`unproven-no-reference` rather than pretending: whether a tier-1 rewrite exists
is exactly what the eval finds out.

*Ceiling* — arbitrary-precision integers, `os.listdir` order, and the modules no
one should ever hand-roll (hashlib and friends). Falling back IS the right
answer, so `require_tier1` is false and the case is scored on correctness alone.
The original program is the *reference* here, not a negative. These are the
control that keeps the metric honest: without them "stay in the subset" has no
counterweight, and a model that fabricates a 100-digit integer to avoid a
fallback would score as an improvement.

THE SKIP RULES ARE THE REPOSITORY'S OWN. `absolute_paths`, `spawns_a_battery`
and `is_nondeterministic` are imported from `lypning.conformance` rather than
reimplemented. Two copies of a rule that decides whether a program may be
executed is one copy too many, and the second is the one that goes stale.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from . import engines as eng
from .jsonio import read_jsonl
from .sandbox import RunResult, run_python

# Refusals no rewrite may dodge, so the case is scored on correctness alone.
# `bigint` and the order-defined ones are decided while the program runs and no
# parser can see them coming; the modules are the ones where reimplementing to
# dodge an import is the failure mode, not the win.
CEILING_KINDS = ("bigint",)
NEVER_HANDROLL = (
    "hashlib", "zlib", "unicodedata", "subprocess", "threading", "socket",
    "ssl", "random", "secrets", "sqlite3", "pickle", "struct", "ctypes",
)
CEILING_DETAILS = ("os.listdir", "os.walk", "os.stat", "sys.path", "sys.version")

PROMPT = """The following Python program is correct, but this runtime refuses it and \
falls back to a slower interpreter:

```python
{program}
```

It was refused because: {reason}

Rewrite it so it produces byte-identical output without that construct. Use only \
plain built-in Python — no imports beyond os and sys, and no `os` attributes \
beyond `os.path`. Do not change what the program computes and do not approximate \
any answer: a wrong result is far worse than a fallback. If the program cannot be \
written without that construct, return it unchanged."""

CEILING_PROMPT = """Write a Python program for this task:

{task}

Correctness is the only thing that matters. Do not approximate, and do not \
reimplement a standard algorithm by hand to avoid an import — use the right \
module."""


def _repo_rules():
    """Import the repository's own skip rules. Lazy, so the rest stays standalone."""
    from lypning import conformance as conf  # noqa: WPS433
    return conf


# Programs that drive lypning itself. They are real captured usage and they
# belong in the corpus that measures the engine, but they are not a *generation*
# target: "rewrite this so it does not import lypning" has no answer.
def is_tooling(kind: str, detail: str) -> bool:
    return kind in ("module", "module-attr") and detail.replace(
        "import ", "").replace("from ", "").strip().startswith("lypning")


def is_ceiling(kind: str, detail: str) -> bool:
    if kind in CEILING_KINDS:
        return True
    if kind == "module" and any(m in detail for m in NEVER_HANDROLL):
        return True
    if kind in ("module-attr", "builtin") and any(d in detail for d in CEILING_DETAILS):
        return True
    return False


def _run(program: str, entry: Dict[str, Any], binary: Optional[str],
         timeout_s: float) -> RunResult:
    return run_python(
        program,
        argv=entry.get("argv_tail") or [],
        stdin=entry.get("stdin_sample"),
        timeout_s=timeout_s,
        interpreter=[binary] if binary else None,
    )


def classify_entry(
    entry: Dict[str, Any],
    *,
    chain: Sequence[str] = eng.DEFAULT_CHAIN,
    timeout_s: float = 10.0,
) -> Tuple[str, Dict[str, Any]]:
    """(outcome, info). Outcome is one of skip / tier1 / refused / unusable.

    ``tier1`` means an engine already runs it: nothing to learn, not a case.
    ``unusable`` means CPython itself could not give a clean answer to compare
    against — a crash, a timeout, or no output at all — so there is nothing to
    hold a rewrite to.
    """
    conf = _repo_rules()
    program = entry.get("program") or ""
    argv = [str(a) for a in (entry.get("argv_tail") or [])]

    if conf.is_nondeterministic(entry):
        return "skip", {"reason": "output cannot be equal on two interpreters"}
    battery = conf.spawns_a_battery(program)
    if battery:
        return "skip", {"reason": battery}
    outside = conf.absolute_paths(program)
    for a in argv:
        outside.extend(p for p in conf.absolute_paths(a) if p not in outside)
    if outside:
        return "skip", {"reason": "absolute path outside the sandbox: %s" % outside[0]}

    ref = _run(program, entry, None, timeout_s)
    if ref.harness_error:
        return "skip", {"reason": "harness: %s" % ref.harness_error}
    if ref.timed_out or ref.exit_code != 0 or not ref.stdout.strip():
        return "unusable", {"reason": "CPython gives no clean answer: %s" % ref.brief(120)}

    for name in chain:
        binary = eng.engine_path(name)
        if binary is None:
            return "skip", {"reason": "engine not built: %s" % name}
        r = _run(program, entry, binary, timeout_s)
        if r.harness_error:
            return "skip", {"reason": "harness: %s" % r.harness_error}
        if r.exit_code != eng.REFUSAL_EXIT:
            if r.stdout != ref.stdout or r.exit_code != ref.exit_code:
                return "skip", {"reason": "engine disagrees with CPython (a MISMATCH, "
                                          "not a training case): %s" % name}
            return "tier1", {"engine": name}
    parsed = eng.parse_refusal(r.stderr)
    if not parsed:
        return "skip", {"reason": "exit 90 without a refusal line (contract broken)"}
    _, kind, detail = parsed
    return "refused", {"kind": kind, "detail": detail,
                       "expect_stdout": ref.stdout, "expect_exit": ref.exit_code}


def to_candidate(entry: Dict[str, Any], info: Dict[str, Any]) -> Dict[str, Any]:
    kind, detail = info["kind"], info["detail"]
    ceiling = is_ceiling(kind, detail)
    test: Dict[str, Any] = {
        "kind": "lypning",
        "expect_stdout": info["expect_stdout"],
        "expect_exit": info["expect_exit"],
        "engines": list(eng.DEFAULT_CHAIN),
        "require_tier1": not ceiling,
        "timeout_s": 10,
    }
    if entry.get("argv_tail"):
        test["argv"] = [str(a) for a in entry["argv_tail"]]
    if entry.get("stdin_sample") is not None:
        test["stdin"] = entry["stdin_sample"]

    program = entry["program"]
    if ceiling:
        # The original IS the reference: falling back is the right answer, and
        # the case exists to catch a model that fakes one to avoid it.
        return {
            "prompt": CEILING_PROMPT.format(task=_describe(program, kind, detail)),
            "reference": program,
            "test": test,
            "category": "ceiling:" + kind,
            "source": "lypning-corpus",
            "source_id": entry["id"],
            "tags": ["shape:ceiling", "refusal:%s" % kind],
            "notes": "%s: %s" % (kind, detail),
        }
    return {
        "prompt": PROMPT.format(program=program.strip(), reason="%s: %s" % (kind, detail)),
        "reference": None,
        "test": test,
        "category": "refused:" + kind,
        "source": "lypning-corpus",
        "source_id": entry["id"],
        # The original is correct and still fails the routing leg: the negative
        # control the discriminates gate needs, already in hand.
        "negative": program,
        "negative_detail": "%s: %s" % (kind, detail),
        "tags": ["shape:rewrite", "refusal:%s" % kind],
        "notes": "%s: %s" % (kind, detail),
    }


def _describe(program: str, kind: str, detail: str) -> str:
    """A ceiling case's prompt has to state the task, and all we have is the code."""
    return ("Produce exactly the output this program produces, for the same "
            "inputs:\n\n```python\n%s\n```" % program.strip())
