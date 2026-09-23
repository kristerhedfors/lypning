"""What a PUBLIC log may print of a private training artifact.

The repository is public, so every Actions log is world-readable, and a CI job
that prints a `metrics.json`, `best.json` or `training_report` object out of
the private dataset repository publishes it. Most of such an object is already
aggregate. `PRIVATE_KEYS` names what is not: `case_clusters`
(`training_metrics.case_clusters`) carries per-case draw, correct and native
counts, including eval-2's, and the operator decided on 2026-09-23 that they
stay private. The artifacts themselves keep the key unchanged -- offline
re-selection pairs evaluations through it -- and only what is PRINTED loses it.

Every public printer calls `public_view` on the object it is about to print,
and `training/tests/test_public_view.py` holds the `.github/scripts` printers
and `round02_pilot.sh` to that. Stdlib only, no I/O: it returns a copy.
"""
from __future__ import annotations

__all__ = ["PRIVATE_KEYS", "public_view"]

# A key named here is removed at every depth, whatever it holds.
PRIVATE_KEYS = frozenset({"case_clusters"})


def public_view(value):
    """A copy of `value` without any `PRIVATE_KEYS` entry, at any nesting.

    Mappings and sequences are walked; a tuple comes back as a list, which is
    what JSON makes of it anyway. Anything else is returned as it is.
    """
    if isinstance(value, dict):
        return {k: public_view(v) for k, v in value.items() if k not in PRIVATE_KEYS}
    if isinstance(value, (list, tuple)):
        return [public_view(v) for v in value]
    return value
