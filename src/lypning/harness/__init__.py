"""Which harnesses lypning can wire itself into, and the contract each keeps.

One module per harness, holding **knowledge** — where its config lives, what
its tool is called, which of its event fields hold the command — and no
**mechanism**. Every module builds :class:`install.Action` / :class:`install.Plan`
and is executed by :func:`install.apply`; none of them defines a second applier,
a second record builder, or a second dispatcher.

That split is what keeps a second harness from costing what the first one did.
The genuinely hard machinery in :mod:`lypning.install` — ``merge_hooks`` and
``strip_hooks`` — exists because ``.claude/settings.json`` is a file the user
owns and has opinions about. Neither harness added here has that problem:
opencode discovers a plugin file with no config entry, and OpenHands discovers a
plugin directory ambiently. So a module here that ever needs
``Action(kind="merge")`` does not belong here; ``apply``'s merge branch is
Claude-settings-specific, and the invariant-7 guarantees around a merge have to
be re-derived, never assumed.

The names here are a different namespace from the engine strings (invariant 9):
an engine is ``"lypning"``, ``"lypning-mp"`` or ``"cpython"`` and says what ran
a program; a harness name says who asked.
"""

from __future__ import annotations

import importlib
from typing import Any, Tuple


#: Every harness with a module in this package. Static rather than discovered:
#: a name here is a promise that the module keeps the contract below, and a
#: directory scan would happily pick up a half-written one.
NAMES: Tuple[str, ...] = ("claude", "opencode", "openhands")

#: What ``--harness`` means when nobody said. Claude Code, because it is what
#: this package has always installed into — and because auto-detecting from a
#: config directory that happens to exist would install into a harness the user
#: never named, which is exactly the surprise invariant 7 exists to prevent.
DEFAULT = "claude"


def load(name: str) -> Any:
    """The module for one harness. Imported lazily, so an install into one
    harness never pays for the others' imports."""
    if name not in NAMES:
        raise ValueError("unknown harness %r (known: %s)"
                         % (name, ", ".join(NAMES)))
    return importlib.import_module("." + name, __name__)


def resolve(spec: str) -> Tuple[str, ...]:
    """A ``--harness`` value as the harnesses it names.

    Accepts a comma list and the word ``all``. Raises :class:`ValueError` on an
    unknown name rather than skipping it: a typo that silently installed nothing
    would look exactly like a successful install.
    """
    spec = (spec or "").strip()
    if not spec:
        return (DEFAULT,)
    if spec == "all":
        return NAMES
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if part not in NAMES:
            raise ValueError("unknown harness %r (known: %s, all)"
                             % (part, ", ".join(NAMES)))
        if part not in out:
            out.append(part)
    return tuple(out) or (DEFAULT,)
