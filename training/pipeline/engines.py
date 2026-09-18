"""Where the engine binaries are, and how to read what they say when they refuse.

The refusal contract is the whole interface: exit **90**, exactly one
``<engine>: unsupported: <kind>: <detail>`` line on stderr, and nothing at all on
stdout. Any other non-zero exit is the program's own. This module parses that one
line and nothing else — if the contract ever changes, this is the single place
that has to notice.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def refusal_bucket(stderr: str) -> str:
    """``kind: detail`` off a refusal line, with the engine name dropped.

    The bucket a synthesis queue is keyed on: ``module: import calendar`` is
    about the construct, and the engine that said it is the same for every row
    in the queue. Unparseable stderr buckets as ``unknown`` rather than as its
    own text, so a refusal that broke the contract cannot become a construct.
    """
    parsed = parse_refusal(stderr)
    return "%s: %s" % (parsed[1], parsed[2]) if parsed else "unknown"


def check_refusal_contract(exit_code: Optional[int], stdout: str, stderr: str,
                           engine: Optional[str] = None) -> Optional[str]:
    """None when this is a clean refusal, else which half of the contract broke.

    The contract (root ``CLAUDE.md`` invariant 2): exit 90, exactly one
    ``<engine>: unsupported: <kind>: <detail>`` line on stderr, and nothing at
    all on stdout. A 90 that wrote to stdout has half-run the program; a 90 with
    two stderr lines, or none, is not the grammar. Either is an engine bug and a
    witness, never a bucket. ``engine`` pins the name at the head of the line
    when the caller knows which variant it invoked.
    """
    if exit_code != REFUSAL_EXIT:
        return "exit %r is not %d" % (exit_code, REFUSAL_EXIT)
    if stdout:
        return "stdout is not empty on a refusal"
    lines = [ln for ln in (stderr or "").splitlines() if ln.strip()]
    if len(lines) != 1:
        return "expected exactly one refusal line, got %d" % len(lines)
    parsed = parse_refusal(lines[0])
    if not parsed:
        return "stderr is not a refusal line"
    if engine and parsed[0] != engine:
        return "refusal names %s, not %s" % (parsed[0], engine)
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


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _version_line(binary: str) -> str:
    """``lypning 0.1.0 (lypning) for cpython 3.11`` — the human half of the id."""
    try:
        p = subprocess.run([binary, "--version"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    out = (p.stdout or b"").decode("utf-8", "replace").strip()
    return out.splitlines()[0] if out else ""


def identity(chain=DEFAULT_CHAIN) -> Dict[str, Any]:
    """Which engine graded this run — tight enough that two cannot be confused.

    A pass rate on a `lypning` case is a statement about an ENGINE as much as
    about a model: every acceptance test asks whether lypning accepts the
    program, so the same completions score differently against two builds. That
    has already moved a published baseline once (40.37% -> 43.75%, the engine
    gaining math and type()), and nothing in a run's metadata recorded which
    engine it was. `stats.comparability` can only withhold a subtraction over a
    field that somebody wrote down, so this is the field.

    The binary's sha256 is the identity and the version line is the label. The
    hash rather than a version string because the version is `0.1.0` across
    every build this project has ever made, and rather than a git commit because
    the run may be graded from a wheel with no repository behind it. Two clean
    builds of one commit are byte-identical here, so the hash does not fire on a
    rebuild that changed nothing.

    `oracle_python` is the second interpreter in the room and belongs to the
    same fact. The engine is built for one CPython and answers as that one; the
    acceptance test's correctness leg runs the program on `sys.executable`. A
    run graded where those two differ is measuring the gap between them, and
    since the engine started carrying its reference CPython (``for cpython
    3.11``) both halves are recorded here.
    """
    from .jsonio import sha256_of

    engines: Dict[str, Any] = {}
    for name in chain:
        binary = engine_path(name)
        engines[name] = ({"sha256": "", "version": "", "found": False} if not binary
                         else {"sha256": _sha256_of_file(binary),
                               "version": _version_line(binary), "found": True})
    oracle = "%d.%d.%d" % sys.version_info[:3]
    # A chain where nothing was found is not an identity, it is the absence of
    # one, and it must not certify: hashing it yields a perfectly stable
    # fingerprint that every engine-less box computes alike, so two runs graded
    # against no engine at all would compare as "the same engine" and
    # `stats._engine` would withhold nothing. An empty fingerprint is the value
    # `_engine` already reads as unrecorded, which is what this is.
    found = [v["sha256"] for v in engines.values() if v["found"]]
    fingerprint = "" if not found else sha256_of(
        {"chain": {k: v["sha256"] for k, v in engines.items()},
         "oracle_python": oracle})[:16]
    return {"chain": engines, "oracle_python": oracle, "fingerprint": fingerprint}


def binary_identity(binary: Any) -> Dict[str, Any]:
    """The explicit binary a replay actually executes.

    :func:`identity` describes the installed chain.  That is the right arm
    identity for a normal evaluation, but it cannot identify an explicit
    ``--engine /path/to/old/lypning-l``: the path may not be installed anywhere
    in that chain.  S0b replays a historical population through exactly such a
    binary, so its evidence must name the bytes it executed rather than the
    unrelated binaries discoverable on the host.
    """
    path = str(binary)
    sha256 = _sha256_of_file(path)
    return {
        "path": path,
        "sha256": sha256,
        "version": _version_line(path) if sha256 else "",
        "oracle_python": "%d.%d.%d" % sys.version_info[:3],
    }
