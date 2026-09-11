"""Where the engine binaries are, and how to read what they say when they refuse.

The refusal contract is the whole interface: exit **90**, exactly one
``<engine>: unsupported: <kind>: <detail>`` line on stderr, and nothing at all on
stdout. Any other non-zero exit is the program's own. This module parses that one
line and nothing else — if the contract ever changes, this is the single place
that has to notice.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REFUSAL_EXIT = 90
DEFAULT_CHAIN = ("lypning", "lypning-l")

# `lypning: unsupported: module: import re`
_REFUSAL = re.compile(r"^(?P<engine>[\w.-]+): unsupported: (?P<kind>[^:]+): (?P<detail>.*)$")


def engine_path(name: str) -> Optional[str]:
    """Resolve an engine binary. ``NTX_ENGINE_LYPNING_L`` overrides ``lypning-l``."""
    override = os.environ.get("NTX_ENGINE_" + name.upper().replace("-", "_"))
    if override:
        return override if Path(override).exists() else None
    home = Path(os.environ.get("LYPNING_HOME") or (Path.home() / ".lypning"))
    cand = home / "bin" / name
    if cand.exists():
        return str(cand)
    return shutil.which(name)


def available(chain=DEFAULT_CHAIN) -> Dict[str, Optional[str]]:
    return {name: engine_path(name) for name in chain}


def parse_refusal(stderr: str) -> Optional[Tuple[str, str, str]]:
    """(engine, kind, detail) from a refusal line, or None if this is not one."""
    for line in (stderr or "").strip().splitlines():
        m = _REFUSAL.match(line.strip())
        if m:
            return m.group("engine"), m.group("kind"), m.group("detail")
    return None


def refusal_category(stderr: str) -> str:
    """The stratification key: the engine's own word for why it stopped.

    `module`, `bigint`, `set-order`, `builtin`, `method`, `class`, `module-attr`
    and friends — roughly a dozen values over the whole corpus, which is the
    right granularity to stratify on. Inventing our own taxonomy here would
    drift from `conformance --plan`, and --plan is the build order.
    """
    parsed = parse_refusal(stderr)
    return "refused:" + parsed[1] if parsed else "refused:unknown"
