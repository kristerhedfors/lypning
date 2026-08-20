"""Getting the capture shim onto ``$PATH`` — and refusing to do it blindly.

The shim is a POSIX-sh wrapper named ``python`` and ``python3`` that logs one
JSON line per invocation and then execs the real interpreter. Installing it is
three lines of ``cp``; the whole reason this module exists is the fourth line,
the one that decides whether the copy is allowed to happen at all.

Two invariants:

**Never shadow a real interpreter by accident.** A file already sitting at
``<bin>/python3`` that is not one of our shims is presumed to be somebody's
interpreter — a venv, a pyenv stub, a distro symlink — and clobbering it fails
later, somewhere else, as a different bug every time. :func:`install` refuses
unless ``force=True``, and with force it *moves* the file to
``<path>.lypning-backup`` so :func:`uninstall` can put it back.

**An installed shim that never runs is worse than none.** It reads as working
while the corpus stays empty, so :func:`render` shouts when the install
directory is missing from ``$PATH`` or sits behind a real python on it.

Idempotent by construction: installing copies the current shim over whatever
shim is there and reports ``current`` or ``stale`` for what it replaced, so a
SessionStart hook can run it every session for the price of one ``cp``.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from . import paths
from .engines import SHIM_MARKER

SHIM_NAMES = ("python", "python3")
"""Both names, always. A shim on only one of them is a coin flip per script."""

BACKUP_SUFFIX = ".lypning-backup"
TMP_SUFFIX = ".lypning-tmp"

# The marker sits in the shim's header comment. Twelve lines is the same window
# the sh installer used and the same order of magnitude the shim itself scans
# when it looks for other copies of itself on $PATH — the three have to agree or
# a shim can exec into a shim.
MARKER_LINES = 12


class ShimError(RuntimeError):
    """A refusal or an I/O failure. Carries the report produced so far."""

    def __init__(self, message: str, lines: Optional[Sequence[str]] = None) -> None:
        super().__init__(message)
        self.lines: List[str] = list(lines or [])


def is_shim(path: Path | str) -> bool:
    """True when ``path`` is one of our shims.

    Read as bytes and capped at the first :data:`MARKER_LINES` lines: the
    candidates include real interpreters, which are ELF, and a decode of those
    would be a pointless exception on the hot path of every scan.
    """
    p = Path(path)
    try:
        if not p.is_file():
            return False
        with open(p, "rb") as fh:
            head = b"".join(fh.readline() for _ in range(MARKER_LINES))
    except OSError:
        return False
    return SHIM_MARKER.encode() in head


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


@dataclass
class ShimState:
    """What occupies one target path, and what we may do about it."""

    name: str
    path: Path
    state: str  # "current" | "stale" | "foreign" | "absent"
    backup: Optional[Path] = None

    @property
    def installed(self) -> bool:
        return self.state in ("current", "stale")

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "state": self.state,
            "backup": str(self.backup) if self.backup else None,
        }


def _bin(bin_dir: Path | str | None) -> Path:
    return Path(bin_dir) if bin_dir else paths.bin_dir()


def status(bin_dir: Path | str | None = None) -> List[ShimState]:
    """Classify every target path. Reads only; safe on an unwritable dir."""
    d = _bin(bin_dir)
    src = paths.SHIM_SRC
    out: List[ShimState] = []
    for name in SHIM_NAMES:
        target = d / name
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        b = backup if backup.exists() else None
        if is_shim(target):
            state = "current" if (src.is_file() and _same_bytes(src, target)) else "stale"
        elif target.exists() or target.is_symlink():
            state = "foreign"
        else:
            state = "absent"
        out.append(ShimState(name, target, state, b))
    return out


def install(bin_dir: Path | str | None = None, *, force: bool = False) -> List[str]:
    """Copy the shim to every target name. All targets or none.

    The foreign check happens for all names *before* the first byte is written:
    a run that installs ``python`` and then refuses ``python3`` leaves ``$PATH``
    resolving two different interpreters for the same source line, which is the
    exact failure mode the refusal exists to prevent.
    """
    src = paths.SHIM_SRC
    if not src.is_file():
        raise ShimError("shim source not found at %s" % src)
    d = _bin(bin_dir)
    states = status(d)

    blocked = [s for s in states if s.state == "foreign"]
    if blocked and not force:
        names = ", ".join(str(s.path) for s in blocked)
        raise ShimError(
            "REFUSING: %s exists and is not a lypning shim.\n"
            "  It looks like a real interpreter. Re-run with --force to move it aside\n"
            "  (to <path>%s), or pick a bin dir earlier on PATH." % (names, BACKUP_SUFFIX)
        )

    lines: List[str] = []
    try:
        paths.ensure_dir(d)
    except OSError as e:
        raise ShimError("cannot create %s: %s" % (d, e), lines)

    for s in states:
        target = s.path
        if s.state == "foreign":
            backup = target.with_name(target.name + BACKUP_SUFFIX)
            try:
                # Move, never copy: leaving the original in place would mean two
                # files claiming to be python3 and a restore that silently picks
                # the wrong one.
                os.replace(str(target), str(backup))
            except OSError as e:
                raise ShimError("cannot move %s aside: %s" % (target, e), lines)
            lines.append("backed up %s -> %s" % (target, backup))
        tmp = target.with_name(target.name + TMP_SUFFIX)
        try:
            shutil.copyfile(str(src), str(tmp))
            os.chmod(str(tmp), 0o755)
            os.replace(str(tmp), str(target))
        except OSError as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise ShimError("failed to install %s: %s" % (target, e), lines)
        if s.state == "current":
            lines.append("unchanged %s (already the current shim)" % target)
        elif s.state == "stale":
            lines.append("refreshed %s (was a stale shim)" % target)
        else:
            lines.append("installed %s" % target)
    return lines


def uninstall(bin_dir: Path | str | None = None) -> List[str]:
    """Remove our shims and restore anything ``--force`` moved aside.

    Never touches the log: the captured programs outlive the harness that
    captured them, and an uninstall that deleted them would be unrecoverable.
    """
    d = _bin(bin_dir)
    lines: List[str] = []
    removed = 0
    for s in status(d):
        target = s.path
        if s.installed:
            try:
                target.unlink()
            except OSError as e:
                lines.append("could not remove %s: %s" % (target, e))
                continue
            removed += 1
            lines.append("removed %s" % target)
            backup = target.with_name(target.name + BACKUP_SUFFIX)
            if backup.exists():
                try:
                    os.replace(str(backup), str(target))
                    lines.append("restored %s (from %s)" % (target, BACKUP_SUFFIX))
                except OSError as e:
                    lines.append("could not restore %s: %s" % (target, e))
        elif s.state == "foreign":
            lines.append("left alone %s (not a lypning shim)" % target)
    if removed == 0:
        lines.append("nothing to uninstall in %s" % d)
    lines.append("note: the log at %s was NOT deleted." % paths.log_path())
    return lines


# --- PATH sanity -------------------------------------------------------------


def _path_dirs() -> List[Path]:
    out: List[Path] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        out.append(Path(entry or "."))
    return out


def _resolved(p: Path) -> str:
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def path_problem(bin_dir: Path | str | None = None) -> Optional[str]:
    """``None`` when the shim will actually run, else why it will not.

    Two distinct failures with the same symptom (an empty log): the directory is
    not on ``$PATH`` at all, or it is on it behind a directory that already has
    a real interpreter, in which case the shim is installed and shadowed.
    """
    d = _bin(bin_dir)
    want = _resolved(d)
    dirs = _path_dirs()
    idx = None
    for i, entry in enumerate(dirs):
        if _resolved(entry) == want:
            idx = i
            break
    if idx is None:
        return "%s is NOT on PATH — the shim will never run" % d
    for i, entry in enumerate(dirs[:idx]):
        for name in SHIM_NAMES:
            cand = entry / name
            if cand.is_file() and os.access(str(cand), os.X_OK) and not is_shim(cand):
                return (
                    "%s is on PATH but BEHIND %s — that interpreter wins and the "
                    "shim never runs" % (d, cand)
                )
    return None


def render(states: Iterable[ShimState]) -> str:
    """The human report. The only place this module is allowed to be chatty."""
    states = list(states)
    d = states[0].path.parent if states else paths.bin_dir()
    label = {
        "current": "installed (current)",
        "stale": "installed (STALE — re-run the installer)",
        "foreign": "NOT ours — a different file occupies this path",
        "absent": "not installed",
    }
    out = [
        "shim source : %s%s" % (paths.SHIM_SRC, "" if paths.SHIM_SRC.is_file() else "  (MISSING)"),
        "install dir : %s" % d,
    ]
    for s in states:
        line = "  %-8s %s" % (s.name + ":", label.get(s.state, s.state))
        if s.backup:
            line += "  [backup: %s]" % s.backup.name
        out.append(line)
    out.append("log file    : %s" % paths.log_path())
    problem = path_problem(d)
    if problem:
        # Loud on purpose. A silently shadowed shim reads as a working install
        # for as long as it takes somebody to wonder why the corpus is empty.
        out.append("PATH        : WARNING — %s" % problem)
        out.append("              fix: export PATH=\"%s:$PATH\"" % d)
    else:
        out.append("PATH        : ok — %s is on PATH ahead of any real interpreter" % d)
    return "\n".join(out)
