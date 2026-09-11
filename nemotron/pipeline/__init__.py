"""Steps 1-2 of the Nemotron LoRA pipeline: corpus and evaluation.

Stdlib only, on purpose. These two steps run on a laptop, in CI, and on the
GPU box, and the one thing that must never differ between those three is the
verdict. A dependency is a version that can differ.

Read ``nemotron/README.md`` for the order things must be built in; read
:mod:`pipeline.acceptance` for the rule that decides what a case is.
"""

from __future__ import annotations

__all__ = ["VERSION"]

VERSION = "0.1.0"
