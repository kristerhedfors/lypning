"""lypning — the Coding Harness Interpreter Optimizer.

A spectrum of Rust interpreters followed by CPython, with a classifier that
picks one per program. Every Rust variant refuses the same way — exit ``90``
and one line on stderr — which makes a wrong route cost one wasted spawn
instead of a wrong answer."""

from __future__ import annotations

__version__ = "0.1.0"

UNSUPPORTED_EXIT = 90
"""Exit code every Rust variant uses for "this program is outside my subset"."""

__all__ = ["__version__", "UNSUPPORTED_EXIT"]
