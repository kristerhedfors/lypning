"""One coarse label per failure, because the split is stratified by it.

Buckets are deliberately few. Stratification with a hundred labels and forty
cases is not stratification, it is a partition into singletons; these seven
survive small n and still separate the kinds of fix a model needs to learn.
A label is derived from what the run *did*, never from what the case is called.
"""

from __future__ import annotations

import re
from typing import Optional

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

_TRACEBACK = re.compile(r"^Traceback \(most recent call last\):", re.M)
_SYNTAX = re.compile(r"^(SyntaxError|IndentationError|TabError):", re.M)
_MEMORY = re.compile(r"^(MemoryError|RecursionError)", re.M)


def classify(verdict: Verdict, *, had_code: bool = True) -> str:
    """Label a failed attempt. Calling this on a pass is a programming error."""
    if not had_code:
        return "no-code"
    if verdict.reason in ("timeout", "checker-timeout"):
        return "timeout"
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


def is_category(name: str) -> bool:
    return name in CATEGORIES
