"""lypning — Mixture of Pythons.

Three interpreters, cheapest first, and a classifier that picks one per program:

  ``lypning``     a from-scratch Python subset in Rust, ~10k lines, no crates
  ``lypning-mp``  a MicroPython variant with a frozen shim stdlib
  ``python3``     the real thing, for everything the first two refuse

Every tier refuses the same way — exit ``90`` and one line on stderr — which is
what makes them interchangeable and what makes a wrong route cost one wasted
spawn instead of a wrong answer.

See ``docs/LYPNING.md`` for the design and ``docs/MICROPYTHON.md`` for the cost
model both runtimes are optimised against.
"""

__version__ = "0.1.0"

UNSUPPORTED_EXIT = 90
"""Exit code every tier uses for "this program is outside my subset"."""

__all__ = ["__version__", "UNSUPPORTED_EXIT"]
