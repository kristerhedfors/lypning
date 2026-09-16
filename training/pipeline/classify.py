"""One coarse label per failure, because the split is stratified by it.

Buckets are deliberately few. Stratification with a hundred labels and forty
cases is not stratification, it is a partition into singletons; these seven
survive small n and still separate the kinds of fix a model needs to learn.
A label is derived from what the run *did*, never from what the case is called.

Two labels say something about the attempt and nothing about the task, so they
are attempt-only (:data:`ATTEMPT_CATEGORIES`) and never become a case category.
"""

from __future__ import annotations

import re
from typing import Optional

from . import engines as eng
from .acceptance import Verdict
from .sandbox import RunResult

CATEGORIES = (
    "no-code",        # nothing parseable came back
    "syntax-error",   # it did not compile
    "runtime-error",  # it raised
    "timeout",        # it did not stop
    "resource",       # it was killed: memory, file size, CPU rlimit
    "wrong-output",   # it ran clean and answered wrong
    "checker",        # the acceptance checker rejected it for its own reason
    "unobserved",     # harvested as a task; no generation has failed it yet
)

# Labels an ATTEMPT can carry that a CASE never can — which is why they are kept
# out of CATEGORIES, so that `is_category` still says no and a harvest coerces
# them away. An engine/CPython disagreement is this repository's bug (invariant
# 1) and "not-genuine" is a statement about one completion; admitting either as a
# case category would file our own defect as a task to train against.
ATTEMPT_CATEGORIES = (
    "engine-mismatch",  # an engine ran it and disagreed with CPython
    "not-genuine",      # it reproduced the expected bytes without computing them
)

# Reasons that settle the label on their own, before anything reads a stream.
# The `lypning` kind attaches the CPython reference run to these verdicts and
# that run PASSED, so the sniffing below would be reading a *correct* program's
# stderr and would answer out of it.
_BY_REASON = {
    "timeout": "timeout",
    "checker-timeout": "timeout",
    "engine-mismatch": "engine-mismatch",
    "not-genuine": "not-genuine",
}

_TRACEBACK = re.compile(r"^Traceback \(most recent call last\):", re.M)
_SYNTAX = re.compile(r"^(SyntaxError|IndentationError|TabError):", re.M)
_MEMORY = re.compile(r"^(MemoryError|RecursionError)", re.M)


def classify(verdict: Verdict, *, had_code: bool = True) -> str:
    """Label a failed attempt. Calling this on a pass is a programming error."""
    if not had_code:
        return "no-code"
    if verdict.reason == "refused":
        # The engine names its own refusals and that name is the stratification
        # key. Falling through instead files every refusal as a wrong answer.
        return eng.refusal_category(verdict.detail)
    settled = _BY_REASON.get(verdict.reason)
    if settled is not None:
        return settled
    run: Optional[RunResult] = verdict.run or verdict.check
    stderr = (run.stderr if run is not None else "") or ""
    if run is not None and run.timed_out:
        return "timeout"
    if _SYNTAX.search(stderr):
        return "syntax-error"
    if _MEMORY.search(stderr):
        return "resource"
    if run is not None and run.signal in (9, 24, 25, 31):  # KILL, XCPU, XFSZ, SYS
        return "resource"
    if _TRACEBACK.search(stderr):
        return "runtime-error"
    if verdict.reason in ("stdout", "stdout-re"):
        return "wrong-output"
    if verdict.reason == "exit":
        return "runtime-error" if stderr.strip() else "wrong-output"
    if verdict.reason in ("checker", "pytest"):
        return "checker"
    return "wrong-output"


# The engine names its own refusals (`module`, `bigint`, `set-order`, `builtin`,
# ...), and those names are the stratification key for this corpus. They are
# admitted under a prefix rather than copied into CATEGORIES, because the list
# belongs to `conformance --plan` and a copy here would drift from the build
# order it ranks.
_PREFIXES = ("refused:", "ceiling:")


def is_category(name: str) -> bool:
    if name in CATEGORIES:
        return True
    return any(name.startswith(p) and len(name) > len(p) for p in _PREFIXES)


def stratum(name: str) -> str:
    """The coarse bucket a category belongs to, for a report that has to fit."""
    for p in _PREFIXES:
        if name.startswith(p):
            return p.rstrip(":")
    return name
