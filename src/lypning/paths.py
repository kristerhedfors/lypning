"""Where everything lives.

Two roots, and keeping them apart is the whole point of this module:

  * **assets** — read-only, inside the installed wheel. The Rust crate source,
    the MicroPython variant, the shim stdlib, the corpus, the Claude Code skill
    and hooks. Never written to.
  * **state** — writable, ``$LYPNING_HOME`` or ``~/.lypning``. Built binaries,
    the capture log, build work trees. Everything that a ``pip install --user``
    must not need write access to the site-packages tree for.

A source checkout is the one case where the two overlap: ``assets/rust/target``
is a perfectly good place for cargo to build when the tree is writable, so
:func:`build_dir` prefers it and falls back to state.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# --- assets (read-only, inside the package) ----------------------------------

PACKAGE_ROOT = Path(__file__).resolve().parent
ASSETS = PACKAGE_ROOT / "assets"

RUST_DIR = ASSETS / "rust"
MICROPYTHON_DIR = ASSETS / "micropython"
MICROPYTHON_LIB = MICROPYTHON_DIR / "lib"
CORPUS_DIR = ASSETS / "corpus"
SCRIPTS_DIR = ASSETS / "scripts"
SHIM_SRC = ASSETS / "shim" / "python-shim"
CLAUDE_ASSETS = ASSETS / "claude"
SKILL_SRC = CLAUDE_ASSETS / "skills" / "lypning"
HOOKS_SRC = CLAUDE_ASSETS / "hooks"

CORPUS_FILE = CORPUS_DIR / "corpus.jsonl"
SEED_CORPUS_FILE = CORPUS_DIR / "seed-corpus.jsonl"


def package_is_writable() -> bool:
    """True in a source checkout / editable install, false in a normal wheel.

    Writability alone is the wrong question: site-packages inside a virtualenv
    is writable too, and answering yes there puts cargo's ``target/`` — a
    gigabyte of object files ``pip uninstall`` has never heard of — inside the
    installed package. What is actually meant is "is this a checkout", and the
    thing only a checkout has is the repo's ``pyproject.toml`` above ``src/``.
    """
    for root in (PACKAGE_ROOT.parent, PACKAGE_ROOT.parent.parent):
        if (root / "pyproject.toml").is_file():
            return os.access(ASSETS, os.W_OK)
    return False


# --- state (writable, outside the package) -----------------------------------


def state_dir() -> Path:
    """``$LYPNING_HOME``, else ``~/.lypning``.

    Not ``XDG_DATA_HOME``: the capture log, the built binaries and the build
    work trees are one unit that a user wants to delete with a single ``rm -rf``,
    and the shim (POSIX sh, no Python) resolves the same path with two lines of
    parameter expansion.
    """
    env = os.environ.get("LYPNING_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(os.path.expanduser("~")) / ".lypning"


def bin_dir() -> Path:
    """Where built engine binaries are installed for the CLI to find."""
    return state_dir() / "bin"


def build_dir() -> Path:
    """Scratch space for cargo and the MicroPython make.

    Prefers the package tree when it is writable — that is a source checkout,
    where ``assets/rust/target`` is exactly where a developer expects to find
    the object files, and reusing it means ``pip install -e .`` and ``cargo
    build`` share a cache instead of each paying the first build.
    """
    if package_is_writable():
        return ASSETS
    return state_dir() / "build"


def corpus_write_file() -> Path:
    """Where a harvest is allowed to add to the corpus.

    In a checkout that is the shipped file itself: corpus growth is a commit,
    which is the whole point of harvesting. In a wheel it cannot be — assets are
    read-only, and a ``pip uninstall`` that leaves a rewritten corpus behind is
    worse than one that grows nothing. So the fold lands in state instead, and
    :func:`lypning.corpus.load_default` merges it back on the way out.
    """
    if package_is_writable():
        return CORPUS_FILE
    return state_dir() / "corpus.jsonl"


def log_path() -> Path:
    """The capture log. Must agree with the shim and the hooks, byte for byte."""
    env = os.environ.get("LYPNING_LOG", "").strip()
    if env:
        return Path(env).expanduser()
    return state_dir() / "invocations.jsonl"


def project_dir(start: Path | str | None = None) -> Path:
    """The repository the session is working in.

    ``$CLAUDE_PROJECT_DIR`` when Claude Code sets it, else the enclosing git
    work tree, else the current directory. Sightings and the project-local
    ``.claude/`` wiring are written relative to this.
    """
    env = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if env and Path(env).is_dir():
        return Path(env).resolve()
    base = Path(start).resolve() if start else Path.cwd().resolve()
    try:
        out = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return base


def sightings_dir(project: Path | str | None = None) -> Path:
    """One file per session, an ADDED path rather than a rewritten one.

    The corpus was a single file once and it cost the project 17 sessions of
    captured programs: every branch rewrote it, so every branch conflicted, and
    the merge was never worth it to a session whose work was about something
    else (docs/CAPTURE.md). One writer per path cannot conflict.
    """
    root = Path(project) if project else project_dir()
    return root / "tests" / "corpus" / "sightings"


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p
