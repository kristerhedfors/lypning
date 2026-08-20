"""The conformance battery: the one number that must be zero.

Every corpus program is run twice — once by the real CPython, which is the
reference by definition, and once by each engine — and the two are compared on
stdout and exit code. Each engine's result is one of three things:

  ``MATCH``        stdout and exit code identical to CPython.
  ``UNSUPPORTED``  exit 90 with a ``<engine>: unsupported: <kind>: <detail>``
                   line on stderr. **Not a failure.** It is coverage, and
                   :func:`plan` turns it into the build order.
  ``MISMATCH``     anything else. Always a failure.

The asymmetry is the whole point: **a subset runtime that silently disagrees
with CPython is worse than no runtime at all**, because the agent that typed the
one-liner will not notice. A refusal it will notice, because the answer comes
from CPython instead and costs one extra spawn.

Two invariants this module exists to hold, beyond the verdicts themselves.

**Nothing the corpus does may touch the repository.** The corpus is harvested
from real agent sessions, so it is full of programs that rewrite ``src/`` and
``docs/`` — running it is running an agent's edit history. Every program gets
its own :func:`tempfile.mkdtemp` cwd (and a *separate* one per engine, so the
second engine cannot read back what the first wrote), every program naming an
absolute path is skipped rather than run, and the whole battery is bracketed by
a snapshot of the work tree — git's status *and* a digest and a copy of every
file already dirty — that restores and reports anything that changed anyway.
Digests rather than the set of dirty paths, because the set cannot see a second
change to a file that was already modified, which is the state every developer
runs this in. That last one is a net, not a sandbox: it cannot undo a write outside
the repository, it only makes the next occurrence loud. It is here because the
first measurement runs on the upstream project rewrote 34 tracked files and the
escape route was never pinned down — which is precisely the argument for a net.

**A timeout is never scored as a disagreement.** The reference run and the
engine runs share one deadline, so a program that is simply slow times out on
both sides and is dropped from the measurement instead of being recorded as an
engine that printed the wrong thing.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import corpus
from . import engines as eng
from . import paths
from .engines import CPYTHON, LYPNING, MICROPYTHON

MATCH = "MATCH"
UNSUPPORTED = "UNSUPPORTED"
MISMATCH = "MISMATCH"

#: The dispatcher, measured end to end as a synthetic fourth engine. It is not
#: a tier — it *uses* the tiers — but it is the arm a caller of ``lypning run``
#: actually experiences, so it is the arm CI gates on.
MIXTURE = "mixture"

VERDICTS = (MATCH, UNSUPPORTED, MISMATCH)

#: The arms measured when the caller names none, cheapest first. CPython is the
#: reference and would trivially match itself, so it is not an arm by default.
DEFAULT_ARMS = (LYPNING, MICROPYTHON, MIXTURE)

DEFAULT_TIMEOUT = 30.0

# stdout is kept on a Verdict only when it is evidence — a MISMATCH — and even
# then clipped. One harvested entry prints 18,454 lines; holding three copies of
# it per arm across 839 entries turns a report into a memory hazard.
_STDOUT_CLIP = 8192


# --- what cannot be compared -------------------------------------------------

# Programs that interrogate the INTERPRETER rather than compute something. By
# construction these can never match: an engine IS a different executable and is
# required not to claim a CPython version (docs/SUBSET.md). Reporting them as
# MISMATCH would permanently accuse an engine of a bug for behaving correctly,
# and — the expensive part — train the reader to expect a non-zero MISMATCH
# count, which is how a real divergence gets waved through.
#
# Matched against the program text rather than a hand-applied tag, because these
# arrive automatically from capture and nobody will tag them.
_INTERPRETER_SPECIFIC = tuple(re.compile(p) for p in (
    r"\bsys\s*\.\s*(?:version|version_info|executable|implementation|path|prefix"
    r"|base_prefix|maxsize|byteorder|flags)\b",
    # The strongest member of the class, not the weakest: it names the modules
    # that interpreter ships, and two conformant CPythons disagree — 305 names
    # on 3.11, 293 on 3.12. No corpus can pin it.
    r"\bsys\s*\.\s*stdlib_module_names\b",
    r"\bplatform\s*\.\s*\w+",
    r"\bdir\s*\(",
    r"\bos\s*\.\s*uname\b",
    r"__file__|__spec__|__loader__",
))

# Programs that ask about THIS RUN rather than about a computation: the wall
# clock, a process id, the inode behind a file descriptor, an unseeded PRNG. Two
# runs of the SAME interpreter disagree on these, so comparing them would fail
# forever and bury the real signal — `os.fstat(1).st_ino` prints the inode of
# whichever pipe this run was handed, and `datetime.now()` is never twice the
# same. The exit code still proves the program executed.
_RUN_SPECIFIC = tuple(re.compile(p) for p in (
    r"\b(?:datetime|date)\s*\.\s*(?:now|today|utcnow|utctimetuple)\b",
    r"\btime\s*\.\s*(?:time|time_ns|monotonic|monotonic_ns|perf_counter|perf_counter_ns"
    r"|process_time|process_time_ns|ctime|asctime|localtime|gmtime)\b",
    r"\bos\s*\.\s*(?:getpid|getppid|urandom|times|fstat|cpu_count|getcwd|getlogin)\b",
    r"\bst_(?:ino|dev|mtime|atime|ctime|nlink)\b",
    # A seeded stream is reproducible in principle, but only against the same
    # generator; an engine is not required to reproduce CPython's Mersenne
    # Twister, and the corpus tags both cases the same way.
    r"\b(?:random|secrets)\s*\.\s*\w+",
    r"\buuid\s*\.\s*uuid[14]\b",
    r"\btempfile\s*\.\s*(?:mkdtemp|mkstemp|NamedTemporaryFile|TemporaryDirectory|gettempdir)\b",
    # The address in a default repr, and the identity it comes from.
    r"\bid\s*\(",
))

# Quantities Python itself declines to specify, so two conformant
# implementations may legitimately disagree. This class must stay SMALL and each
# member must be justified by a written standard rather than by convenience —
# the temptation is to silence a real divergence by declaring it unspecified.
_IMPLEMENTATION_DEFINED = tuple(re.compile(p) for p in (
    # DEFLATE (RFC 1951) constrains the stream, never its length: 12 bytes under
    # CPython's zlib, 11 under MicroPython's deflate. Both are valid.
    r"len\s*\(\s*(?:zlib|gzip)\s*\.\s*compress",
))


def _tags(entry: Any) -> Tuple[str, ...]:
    """Corpus tags, which live in ``extra`` — the shipped schema has no column."""
    extra = getattr(entry, "extra", None) or {}
    tags = extra.get("tags")
    return tuple(str(t) for t in tags) if isinstance(tags, (list, tuple)) else ()


def is_nondeterministic(entry: Any) -> bool:
    """True when stdout cannot be compared at all, only the exit code.

    A wall clock and a seeded PRNG stream differ between two runs of the *same*
    interpreter, so demanding a match would fail these forever and bury the real
    signal. They are still worth running: the exit code proves the program
    executed.
    """
    tags = _tags(entry)
    if "nondeterministic" in tags or "seeded" in tags:
        return True
    return is_run_specific(entry) or is_interpreter_specific(entry)


def is_run_specific(entry: Any) -> bool:
    src = getattr(entry, "program", "") or ""
    return any(p.search(src) for p in _RUN_SPECIFIC)


def is_interpreter_specific(entry: Any) -> bool:
    src = getattr(entry, "program", "") or ""
    if "interpreter-specific" in _tags(entry):
        return True
    # `sys.argv` and `sys.stdin` are about the RUN, not the interpreter, and must
    # still be compared — so the patterns above name attributes, never bare `sys`.
    if any(p.search(src) for p in _INTERPRETER_SPECIFIC):
        return True
    return is_implementation_defined(entry)


def is_implementation_defined(entry: Any) -> bool:
    if "implementation-defined" in _tags(entry):
        return True
    src = getattr(entry, "program", "") or ""
    return any(p.search(src) for p in _IMPLEMENTATION_DEFINED)


_BRACE = re.compile(r"\{[^{}]*\}")


def only_set_order_differs(want: str, got: str) -> bool:
    """Do the two outputs differ ONLY by element order inside set displays?

    ``print({1, 2})`` may print ``{2, 1}``: the data model calls a set "an
    unordered collection", so two conformant implementations may disagree and
    the engine is not wrong. It is detected by comparing the OUTPUTS rather than
    by pattern-matching the source — a source regex would have to tell a set
    display from a dict display from an f-string brace from a ``{2,3}`` regex
    repeat, and would silently excuse a real divergence in any program that
    happened to contain a brace. Dict displays are excluded by the ``:`` test:
    dict order IS specified (insertion order, since 3.7).
    """
    want_parts = _BRACE.split(want)
    got_parts = _BRACE.split(got)
    if len(want_parts) != len(got_parts) or want_parts != got_parts:
        return False
    want_braces = _BRACE.findall(want)
    got_braces = _BRACE.findall(got)
    if not want_braces:
        return False
    saw_reorder = False
    for a, b in zip(want_braces, got_braces):
        if a == b:
            continue
        if ":" in a or ":" in b:
            return False
        if sorted(a[1:-1].split(", ")) != sorted(b[1:-1].split(", ")):
            return False
        saw_reorder = True
    return saw_reorder


def first_diff(want: str, got: str) -> str:
    """The first differing line, short enough to read in a terminal."""
    a = str(want).split("\n")
    b = str(got).split("\n")

    def clip(lines: List[str], i: int) -> str:
        if i >= len(lines):
            return "<no line>"
        s = lines[i]
        return repr(s[:90] + "…" if len(s) > 90 else s)

    for i in range(max(len(a), len(b))):
        if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None):
            return "line %d: want %s, got %s" % (i + 1, clip(a, i), clip(b, i))
    return "trailing whitespace only"


# --- what must not be run ----------------------------------------------------

# Two or more segments, not preceded by anything that would make the slash a
# division or a URL's `//`. Deliberately not a Python tokenizer: the path may sit
# in an f-string, a triple-quoted patch body or a `%`-format template, and all
# this has to decide is whether the program NAMES somewhere outside its temp cwd.
_ABS_PATH = re.compile(r"(?<![\w./~+-])/(?:[A-Za-z0-9_.+@-]+/)+[A-Za-z0-9_.+@-]*")

# The first segment must be a real root directory, or `/api/chat` and `/ISSN/`
# — URL routes and regex fragments, which no corpus is short of — would take a
# tenth of the corpus out of the measurement for nothing. The fixed set is
# unioned with the live root so the verdict does not drift between machines.
_ROOT_NAMES = frozenset((
    "bin", "boot", "dev", "etc", "home", "lib", "lib64", "media", "mnt", "opt",
    "proc", "root", "run", "sbin", "srv", "sys", "tmp", "usr", "var",
    "workspace", "Users", "Volumes", "private",
))


_ROOTS: Optional[frozenset] = None


def _live_roots() -> frozenset:
    global _ROOTS
    if _ROOTS is None:
        try:
            _ROOTS = frozenset(p.name for p in Path("/").iterdir())
        except OSError:
            _ROOTS = frozenset()
    return _ROOTS


def absolute_paths(program: str) -> List[str]:
    """Every absolute filesystem path the program names, in order of appearance.

    A program's text is fixed and a temp cwd is randomly named, so nothing here
    can be inside the directory the program is about to run in: an absolute path
    is by construction a path *out* of the sandbox. Three of the upstream
    corpus' seventeen could write.
    """
    roots = _ROOT_NAMES | _live_roots()
    out: List[str] = []
    for m in _ABS_PATH.finditer(program or ""):
        text = m.group(0)
        head = text.split("/")[1]
        if head in roots and text not in out:
            out.append(text)
    return out


# --- records -----------------------------------------------------------------


@dataclass
class Verdict:
    """One engine's answer for one program, and the evidence for it."""

    engine: str
    entry_id: str
    verdict: str
    kind: str = ""
    detail: str = ""
    expected_stdout: str = ""
    actual_stdout: str = ""
    expected_rc: int = 0
    actual_rc: int = 0
    wall_ns: int = 0

    @property
    def failed(self) -> bool:
        return self.verdict == MISMATCH

    def __str__(self) -> str:
        why = "%s: %s" % (self.kind, self.detail) if self.kind else self.detail
        return "%-11s %-8s %s: %s" % (self.verdict, self.engine, self.entry_id, why)


@dataclass
class EngineReport:
    """One arm's totals. ``coverage`` is percent MATCH of what was run."""

    engine: str
    match: int
    unsupported: int
    mismatch: int
    total: int
    coverage: float
    verdicts: List[Verdict] = field(default_factory=list)

    def failures(self) -> List[Verdict]:
        return [v for v in self.verdicts if v.verdict == MISMATCH]


@dataclass
class Skip:
    """A program that was deliberately not run, and why. Not a verdict."""

    entry_id: str
    reason: str


@dataclass
class RoutingError:
    """A route that sent a program somewhere it does not work.

    Being late is a cost in milliseconds and is not recorded here; being *wrong*
    is a cost in correctness. Only the classifier's fatal outcome — routed to an
    engine that MISMATCHES — and a route that could not be obtained at all are.
    The dispatcher usually recovers from the first (the mixture arm shows
    whether it did), which is exactly why the two are measured separately.
    """

    entry_id: str
    predicted: str
    ideal: str = ""
    detail: str = ""


@dataclass
class Report:
    engines: Dict[str, EngineReport]
    routing_errors: List[RoutingError]
    skipped: List[Skip]
    seconds: float
    damage: List[str] = field(default_factory=list)
    unbuilt: List[str] = field(default_factory=list)
    reference: str = ""
    total: int = 0

    @property
    def mismatches(self) -> int:
        return sum(r.mismatch for r in self.engines.values())

    @property
    def ok(self) -> bool:
        """Zero MISMATCH across every engine present — and an intact repository.

        Damage is part of the gate rather than a warning beside it: a run that
        rewrote the tree it was measuring produced its numbers against a moving
        target, so reporting ``ok`` for it would be reporting a measurement we
        cannot stand behind.
        """
        return self.mismatches == 0 and not self.damage


# --- the safety net ----------------------------------------------------------


def _git(root: Path, *args: str, timeout: float = 60.0) -> Optional[str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(root)] + list(args),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def dirty_paths(root: Path) -> Optional[Dict[str, str]]:
    """``{path: status}`` as git sees it, or None when this is not a work tree.

    ``-z`` rather than plain ``--porcelain`` because the plain form quotes and
    escapes paths containing spaces or non-ASCII, and a corpus harvested from
    agent sessions is exactly where such a path shows up.
    """
    out = _git(root, "status", "--porcelain", "-z")
    if out is None:
        return None
    fields = [f for f in out.split("\0") if f]
    seen: Dict[str, str] = {}
    i = 0
    while i < len(fields):
        rec = fields[i]
        i += 1
        if len(rec) < 4:
            continue
        status, path = rec[:2], rec[3:]
        seen[path] = status
        # A rename/copy carries its source as the next NUL-separated field.
        if "R" in status or "C" in status:
            i += 1
    return seen


# The snapshot keeps the BYTES of everything already dirty, so a file the
# developer was midway through editing can be put back exactly as it was. 64 MiB
# is the budget; past it the net keeps the digest and reports the change as
# unrestorable, which is still louder than not noticing it at all.
_SNAPSHOT_BUDGET = 64 * 1024 * 1024


def _expand(root: Path, rel: str) -> List[str]:
    """One git-status entry as the files it actually stands for.

    git collapses an untracked DIRECTORY into a single ``?? dir/`` entry, so
    every file the corpus creates inside one leaves the status line unchanged.
    That is not an exotic case — ``tests/`` is an untracked directory in a fresh
    checkout of this very repository. Expanded with git rather than with
    ``rglob`` so the ignore rules still decide what counts.
    """
    if not rel.endswith("/"):
        return [rel]
    out = _git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", rel)
    if out is None:
        return [rel.rstrip("/")]
    return [f for f in out.split("\0") if f]


def _digest(p: Path) -> str:
    """Content identity, or ``""`` when the path is absent. Never raises."""
    h = hashlib.sha256()
    try:
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


@dataclass
class _TreeState:
    """The work tree as it was before the corpus ran: git's view, and the bytes."""

    status: Dict[str, str] = field(default_factory=dict)
    digests: Dict[str, str] = field(default_factory=dict)
    saved: Dict[str, Path] = field(default_factory=dict)
    store: Optional[Path] = None

    def discard(self) -> None:
        if self.store is not None:
            shutil.rmtree(self.store, ignore_errors=True)
            self.store = None


def _fingerprint(root: Path, status: Dict[str, str]) -> Dict[str, str]:
    """``{path: digest}`` over every file the status entries stand for."""
    out: Dict[str, str] = {}
    for rel in sorted(status):
        for f in _expand(root, rel):
            out[f] = _digest(root / f)
    return out


def _snapshot(root: Path) -> Optional[_TreeState]:
    """What the tree looked like, in enough detail to detect a SECOND change.

    Comparing the SET of dirty paths — which is all this did until a probe
    caught it — cannot see the case that matters: a file that was already
    modified when the battery started stays `` M`` no matter what the corpus
    appends to it, so the one state a developer ever runs this in, mid-edit, was
    the one state where the net was blind. Digests close that. The saved bytes
    are what makes "restored" true afterwards: the pre-run content of an
    already-dirty file was never committed, so ``git checkout`` would replace
    the corpus' damage with the tool's own.
    """
    status = dirty_paths(root)
    if status is None:
        return None
    state = _TreeState(status=status, digests=_fingerprint(root, status))
    spent = 0
    for f in sorted(state.digests):
        src = root / f
        try:
            size = src.stat().st_size
        except OSError:
            continue
        if spent + size > _SNAPSHOT_BUDGET:
            continue
        if state.store is None:
            state.store = Path(tempfile.mkdtemp(prefix="lypning-conf-snap-"))
        dest = state.store / ("%d.blob" % len(state.saved))
        try:
            shutil.copyfile(src, dest)
        except OSError:
            continue
        state.saved[f] = dest
        spent += size
    return state


def _collateral(root: Path, before: _TreeState) -> Dict[str, str]:
    """``{path: digest_now}`` for every path whose bytes are not what they were.

    The union of what git calls dirty now and what it called dirty before: a
    corpus program that "helpfully" reverts an edited file to its committed
    content takes it *off* the status list, and that is a change like any other.
    """
    status = dirty_paths(root) or {}
    now = _fingerprint(root, status)
    for f in before.digests:
        if f not in now:
            now[f] = _digest(root / f)
    return {f: d for f, d in now.items() if before.digests.get(f, "") != d}


def _is_tracked(root: Path, rel: str) -> bool:
    return _git(root, "ls-files", "--error-unmatch", "--", rel) is not None


def _remove(target: Path) -> None:
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)
    else:
        target.unlink()


def _restore(root: Path, collateral: Dict[str, str], before: _TreeState) -> List[str]:
    """Undo what the corpus wrote. Returns the paths that could not be undone.

    A path that was ALREADY dirty is put back from the snapshot, never with
    ``git checkout``: its pre-run content is not in any commit, so checking it
    out would delete the developer's own uncommitted work in the name of
    protecting it. Only a path that was clean before goes back through git.
    """
    failed: List[str] = []
    fresh: List[str] = []
    for p in sorted(collateral):
        target = root / p
        # Never touch anything outside the tree that was snapshotted, whatever
        # git said — a symlink out of the tree is a path out of the tree.
        try:
            target.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            failed.append(p)
            continue
        if p not in before.digests:
            fresh.append(p)
            continue
        saved = before.saved.get(p)
        if saved is None and before.digests[p]:
            # Too large for the budget: say so rather than pretend.
            failed.append(p)
            continue
        try:
            if not before.digests[p]:
                _remove(target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(saved, target)
        except OSError:
            failed.append(p)

    tracked = [p for p in fresh if _is_tracked(root, p)]
    if tracked:
        if _git(root, "checkout", "--", *tracked) is None:
            failed.extend(tracked)
    for p in fresh:
        if p in tracked:
            continue
        try:
            _remove(root / p)
        except OSError:
            failed.append(p)
    return failed


def close_net(root: Path, before: Optional[_TreeState]) -> List[str]:
    """Diff the bracket, put back what the corpus wrote, and free the snapshot.

    Split out of :func:`run` so it can be called from a ``finally``. A battery
    that leaves by raising — one malformed entry, a ``KeyboardInterrupt`` on a
    long run — has run every entry before it, so it is the path where the tree
    is MOST likely to be dirty and was the one path with no restore at all. The
    exception still propagates; it just no longer takes the repository with it.

    Never raises on its own account: an exception from the net would replace the
    one the caller is already handling, and lose it.
    """
    if before is None:
        return []
    try:
        collateral = _collateral(root, before)
        if not collateral:
            return []
        failed = _restore(root, collateral, before)
        return sorted("%s%s" % (_damage_line(root, before, p, d),
                                "  [NOT RESTORED]" if p in failed else "")
                      for p, d in collateral.items())
    except Exception as e:  # noqa: BLE001 - see the docstring
        return ["the repository could not be checked or restored: %s: %s"
                % (type(e).__name__, e)]
    finally:
        before.discard()


def _damage_line(root: Path, before: _TreeState, path: str, digest: str) -> str:
    """How the path changed, in the vocabulary a reader can act on.

    A path absent from the snapshot was clean, not absent: git only lists what
    is dirty. Whether it existed is therefore a question for ``ls-files``, not
    for the snapshot.
    """
    known = path in before.digests
    if not digest:
        return "deleted  %s" % path
    if not known:
        return "%s %s" % ("modified" if _is_tracked(root, path) else "created ", path)
    if not before.digests[path]:
        return "created  %s" % path
    return "modified %s (it was ALREADY dirty before the run)" % path


# --- running one entry -------------------------------------------------------


def _clip(s: str) -> str:
    return s if len(s) <= _STDOUT_CLIP else s[:_STDOUT_CLIP] + "\n…[clipped]"


_UNSUPPORTED_RE = re.compile(r"^([\w.-]+): unsupported: ([\w-]+): (.+)$", re.M)


def _refusal(engine: str, stderr: str) -> Optional[Tuple[str, str]]:
    """``(kind, detail)`` from the shared refusal line, or None.

    The mixture arm relays whichever tier answered, so its line may carry any
    engine's name — but it must carry one of them, or the "refusal" is a program
    printing something that looks like one.
    """
    for m in _UNSUPPORTED_RE.finditer(stderr or ""):
        who = m.group(1)
        if who == engine or (engine == MIXTURE and who in eng.ENGINE_ORDER):
            return m.group(2), m.group(3)
    return None


def classify(ref: eng.Result, got: eng.Result, engine: str, entry: Any) -> Verdict:
    """Score one engine's run against the reference run of the same program.

    stdout and the exit code must match exactly — those are what a shell
    pipeline and an agent loop actually consume. stderr is compared only in one
    direction, and only when the reference itself wrote something: traceback
    text carries file paths, line numbers and interpreter internals that a
    subset runtime has no business reproducing byte for byte. What matters is
    that a program that fails under CPython also fails under the engine, and the
    exit code already says that.
    """
    entry_id = getattr(entry, "id", "")

    def v(verdict: str, kind: str = "", detail: str = "", evidence: bool = False) -> Verdict:
        return Verdict(
            engine=engine, entry_id=entry_id, verdict=verdict, kind=kind, detail=detail,
            expected_stdout=_clip(ref.stdout) if evidence else "",
            actual_stdout=_clip(got.stdout) if evidence else "",
            expected_rc=ref.returncode, actual_rc=got.returncode, wall_ns=got.wall_ns,
        )

    if got.timed_out:
        # The reference ran inside the same deadline and finished, so this is the
        # engine hanging where CPython did not — a divergence, not a slow program.
        return v(MISMATCH, "timeout", "no output within the deadline", evidence=True)
    if not got.binary:
        return v(MISMATCH, "unbuilt", "engine not available")

    refusal = _refusal(engine, got.stderr)
    if got.returncode == eng.UNSUPPORTED_EXIT:
        if refusal:
            if got.stdout:
                # A refusal is only interchangeable with the next tier's answer
                # because it leaves nothing behind. Output that already reached
                # stdout is the one thing the next tier cannot take back: the
                # caller gets the refusing tier's lines AND the answering tier's,
                # so a `… | wc -l` reads high while the exit code still looks
                # right — which is exactly how lypning-mp's tracebacks-to-stdout
                # went unnoticed (engines.py, build.check_refusal_contract).
                # Counting this as coverage would leave the battery blind to the
                # only failure mode the three-tier design cannot survive.
                return v(MISMATCH, "contract",
                         "refused after %d byte(s) had already reached stdout"
                         % len(got.stdout), evidence=True)
            return v(UNSUPPORTED, refusal[0], refusal[1])
        if ref.returncode != eng.UNSUPPORTED_EXIT:
            # Exit 90 without the contract line is itself a contract violation;
            # the alternative is silently counting a crash as coverage.
            return v(MISMATCH, "contract", "exit 90 with no `%s: unsupported: …` line" % engine,
                     evidence=True)
        # ...unless CPython exited 90 too, in which case 90 is the PROGRAM's own
        # exit code (`sys.exit(90)`) and the engine reproduced it. Scoring that
        # as a broken contract accuses an engine of a bug for agreeing with the
        # reference. Fall through and compare it like any other exit code.

    skip_stdout = is_nondeterministic(entry)
    if not skip_stdout and got.stdout != ref.stdout and not only_set_order_differs(ref.stdout, got.stdout):
        return v(MISMATCH, "stdout", first_diff(ref.stdout, got.stdout), evidence=True)
    if got.returncode != ref.returncode:
        return v(MISMATCH, "exit",
                 "exit %d, CPython gave %d" % (got.returncode, ref.returncode), evidence=True)
    if ref.stderr and not got.stderr:
        return v(MISMATCH, "stderr", "CPython reported an error, this engine was silent",
                 evidence=True)
    return v(MATCH, "", "stdout uncompared" if skip_stdout else "")


class _Sandbox:
    """A fresh cwd per run, removed afterwards, never the repository.

    Per *run*, not per entry: the reference and each engine get their own, or the
    second one to run would read back the file the first one created and "match"
    without having written anything.
    """

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __enter__(self) -> Path:
        self.path = Path(tempfile.mkdtemp(prefix="lypning-conf-%s-" % self.tag))
        return self.path

    def __exit__(self, *exc: Any) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def _env_for(cwd: Path) -> Dict[str, str]:
    """The environment every run shares — reference and engines alike.

    ``LYPNING_LOG`` is redirected into the sandbox rather than left pointing at
    the real capture log: a conformance run executes the whole corpus, and a
    capture of that would fold the test suite back into the corpus as observed
    evidence — the feedback loop that once made 138 of 197 "harvested" programs
    verbatim seed programs, all with the same count. Since the build order is
    ranked by those counts, the loop does not merely add noise, it ranks guesses
    above what an agent actually typed. ``engines.run`` sets ``LYPNING_CAPTURE=0``
    as the other half of that belt and braces.
    """
    return {
        "LYPNING_LOG": str(cwd / "capture.jsonl"),
        "PWD": str(cwd),
        "LC_ALL": "C.UTF-8",
        # Two runs of CPython must agree with each other before either can be a
        # reference for something else.
        "PYTHONHASHSEED": "0",
    }


@dataclass
class _EntryResult:
    entry_id: str
    verdicts: Dict[str, Verdict] = field(default_factory=dict)
    predicted: str = ""
    route_kind: str = ""
    route_detail: str = ""
    skip: Optional[Skip] = None


def _run_entry(
    entry: Any,
    arms: Sequence[str],
    binaries: Dict[str, Optional[Path]],
    ref_bin: Optional[Path],
    timeout: float,
) -> _EntryResult:
    out = _EntryResult(entry_id=getattr(entry, "id", ""))
    program = entry.program
    argv_tail = list(getattr(entry, "argv_tail", ()) or ())
    stdin = getattr(entry, "stdin_sample", None) or ""

    if "\0" in program or any("\0" in a for a in argv_tail):
        # An argv element cannot carry a NUL — the kernel's argv is NUL
        # terminated — so no interpreter on earth can be handed this entry, and
        # neither side of the comparison ever starts. It is a skip, not a
        # verdict: scoring "both failed to spawn" as MATCH would report agreement
        # between two runs that did not happen. The shim captures argv verbatim,
        # which is where such a record comes from.
        out.skip = Skip(out.entry_id, "NUL byte in the program or its argv: unspawnable")
        return out

    outside = absolute_paths(program)
    for a in argv_tail:
        # The shim captures a shell redirect as an argv element (`>`, `/tmp/f`),
        # and a program is free to open `sys.argv[1]`. Same rule.
        outside.extend(p for p in absolute_paths(a) if p not in outside)
    if outside:
        out.skip = Skip(out.entry_id, "absolute path outside the sandbox: %s" % outside[0])
        return out

    with _Sandbox("ref") as cwd:
        ref = eng.run(CPYTHON, program, binary=ref_bin, argv_tail=argv_tail, stdin=stdin,
                      cwd=cwd, timeout=timeout, env=_env_for(cwd))
    if ref.timed_out:
        # Same deadline on both sides, so a timeout here is the program being
        # slow, not an engine being wrong. Scoring it either way would be a lie.
        out.skip = Skip(out.entry_id, "reference timed out after %gs" % timeout)
        return out

    for arm in arms:
        if arm == CPYTHON:
            # The reference against itself: recorded so a caller who asks for the
            # arm gets its wall time, never to be interpreted as agreement.
            out.verdicts[arm] = Verdict(CPYTHON, out.entry_id, MATCH, "reference", "",
                                        expected_rc=ref.returncode, actual_rc=ref.returncode,
                                        wall_ns=ref.wall_ns)
            continue
        if arm == MIXTURE:
            with _Sandbox("mix") as cwd:
                d = eng.dispatch(program, argv_tail=argv_tail, stdin=stdin, cwd=cwd,
                                 timeout=timeout)
            got = d.result
            # End to end is what the caller pays: every refused tier plus the one
            # that answered.
            got.wall_ns = sum(a.wall_ns for a in d.attempts) + got.wall_ns
            out.predicted, out.route_kind, out.route_detail = (
                d.route.engine, d.route.kind, d.route.detail)
            out.verdicts[arm] = classify(ref, got, MIXTURE, entry)
            continue
        with _Sandbox(arm) as cwd:
            got = eng.run(arm, program, binary=binaries.get(arm), argv_tail=argv_tail,
                          stdin=stdin, cwd=cwd, timeout=timeout, env=_env_for(cwd))
        out.verdicts[arm] = classify(ref, got, arm, entry)
    return out


# --- the battery -------------------------------------------------------------


def run(
    entries: Optional[Sequence[Any]] = None,
    *,
    engines: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    timeout: float = DEFAULT_TIMEOUT,
    workers: Optional[int] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Report:
    """Run the whole battery and return the report. Never raises for a verdict.

    ``entries`` defaults to the shipped corpus, ``engines`` to every tier that is
    actually built plus the mixture — an engine that is not built is reported as
    an absent arm rather than as an arm that failed, because "not built" and
    "wrong" are different facts and only one of them is a bug.

    Threads, not processes: every unit of work is a subprocess, so the GIL is
    released for all of it.
    """
    started = time.perf_counter()
    root = paths.project_dir()
    before = _snapshot(root)

    pool: List[Any] = []
    arms: List[str] = []
    unbuilt: List[str] = []
    ref_bin: Optional[Path] = None
    reports: Dict[str, EngineReport] = {}
    routing_errors: List[RoutingError] = []
    skipped: List[Skip] = []
    # The net closes on EVERY path out of here, the ones that leave by raising
    # included: by then every entry before the failure has already run, so that
    # is the path where the tree is most likely to be holding a corpus program's
    # writes. Nothing between here and the finally may return early.
    try:
        pool = list(entries) if entries is not None else corpus.load_default()
        if limit is not None and limit >= 0:
            pool = pool[:limit]

        binaries: Dict[str, Optional[Path]] = {e: eng.find(e) for e in eng.ENGINE_ORDER}
        ref_bin = binaries.get(CPYTHON)

        wanted = list(engines) if engines is not None else list(DEFAULT_ARMS)
        for a in wanted:
            if a in (MIXTURE, CPYTHON) or binaries.get(a) is not None:
                arms.append(a)
            else:
                unbuilt.append(a)

        n_workers = workers if workers else min(8, os.cpu_count() or 1)
        results: List[_EntryResult] = []
        done = 0
        lock = threading.Lock()

        def tick(r: _EntryResult) -> _EntryResult:
            nonlocal done
            with lock:
                done += 1
                if progress is not None:
                    progress(done, len(pool))
            return r

        if pool:
            with ThreadPoolExecutor(max_workers=max(1, n_workers)) as ex:
                futures = [ex.submit(_run_entry, e, arms, binaries, ref_bin, timeout) for e in pool]
                for f in futures:
                    results.append(tick(f.result()))

        skipped = [r.skip for r in results if r.skip is not None]
        scored = [r for r in results if r.skip is None]

        for arm in arms:
            vs = [r.verdicts[arm] for r in scored if arm in r.verdicts]
            counts = {v: 0 for v in VERDICTS}
            for v in vs:
                counts[v.verdict] = counts.get(v.verdict, 0) + 1
            total = len(vs)
            reports[arm] = EngineReport(
                engine=arm,
                match=counts[MATCH], unsupported=counts[UNSUPPORTED], mismatch=counts[MISMATCH],
                total=total,
                coverage=(100.0 * counts[MATCH] / total) if total else 0.0,
                verdicts=vs,
            )

        routing_errors = _routing_errors(scored, arms)
    finally:
        damage = close_net(root, before)

    return Report(
        engines=reports,
        routing_errors=routing_errors,
        skipped=skipped,
        seconds=time.perf_counter() - started,
        damage=damage,
        unbuilt=unbuilt,
        reference=str(ref_bin or ""),
        total=len(pool),
    )


def _routing_errors(scored: Sequence[_EntryResult], arms: Sequence[str]) -> List[RoutingError]:
    """Only the fatal routing outcomes: routed somewhere wrong, or not routed."""
    order = [a for a in eng.ENGINE_ORDER if a in arms]
    out: List[RoutingError] = []
    for r in scored:
        if not r.predicted:
            continue
        if r.route_kind == "unbuilt":
            continue  # no classifier built at all: nothing here to grade
        if r.route_kind in ("route-failed", "route-unparseable"):
            out.append(RoutingError(r.entry_id, r.predicted, "",
                                    "%s: %s" % (r.route_kind, r.route_detail)))
            continue
        got = r.verdicts.get(r.predicted)
        if got is None:
            # A tier that exists but was not one of this run's arms is not an
            # error; a name that is not a tier at all means the classifier told
            # the dispatcher to run something that cannot be run.
            if r.predicted not in eng.ENGINE_ORDER:
                out.append(RoutingError(r.entry_id, r.predicted, "",
                                        "route named an engine that does not exist"))
            continue
        if got.verdict != MISMATCH:
            continue
        ideal = next((e for e in order if r.verdicts.get(e) and r.verdicts[e].verdict == MATCH), "")
        if not ideal:
            continue  # no engine ran it correctly: not the classifier's fault
        rescued = r.verdicts.get(MIXTURE)
        note = " (dispatcher recovered)" if rescued and rescued.verdict == MATCH else ""
        out.append(RoutingError(r.entry_id, r.predicted, ideal,
                                "%s: %s%s" % (got.kind, got.detail, note)))
    return out


# --- reporting ---------------------------------------------------------------


def plan(report: Report) -> List[Tuple[str, int, List[str]]]:
    """The build order: refusal features ranked by how much they unblock.

    ``(feature, blocks, example_ids)``, most blocking first. A program is blocked
    by the FIRST thing it hits, so the counts are a lower bound that shifts as
    features land — which is the point. Re-run after each one.

    Taken from the Rust core's arm when it is present: it is the tier the corpus
    is a build order *for*. The mixture arm never appears here, because a
    dispatcher that reaches CPython refuses nothing.
    """
    source = None
    for name in (LYPNING, MICROPYTHON):
        if name in report.engines:
            source = report.engines[name]
            break
    if source is None:
        return []
    blocks: Dict[str, int] = {}
    ids: Dict[str, List[str]] = {}
    for v in source.verdicts:
        if v.verdict != UNSUPPORTED:
            continue
        key = "%s: %s" % (v.kind, v.detail)
        blocks[key] = blocks.get(key, 0) + 1
        bucket = ids.setdefault(key, [])
        if len(bucket) < 4:
            bucket.append(v.entry_id)
    return [(k, blocks[k], ids[k]) for k in sorted(blocks, key=lambda k: (-blocks[k], k))]


# `render(report, plan=True)` shadows the name inside that function, so the
# function object is bound here, once, where it is still reachable.
_plan_rows = plan


def _pad(s: Any, n: int) -> str:
    s = str(s)
    return s if len(s) >= n else s + " " * (n - len(s))


def render(report: Report, plan: bool = False) -> str:
    """The human view. The only place in this module that formats for a terminal."""
    out: List[str] = []
    if plan:
        rows = _plan_rows(report)
        total = max((r.total for r in report.engines.values()), default=report.total)
        out.append("build order — %d distinct blockers over %d programs" % (len(rows), total))
        out.append("(a program is blocked by the FIRST thing it hits, so counts shift"
                   " as features land)")
        out.append("")
        for feature, blocks, ids in rows[:40]:
            out.append("%s  %s  e.g. %s" % (str(blocks).rjust(4), _pad(feature, 46),
                                            ", ".join(ids[:3])))
        if len(rows) > 40:
            # The tail is one or two programs each, but a list that stops
            # without saying so reads as the whole list.
            out.append("  … %d more blockers, %d programs between them"
                       % (len(rows) - 40, sum(b for _f, b, _i in rows[40:])))
        by_kind: Dict[str, int] = {}
        for feature, blocks, _ids in rows:
            kind = feature.split(":", 1)[0]
            by_kind[kind] = by_kind.get(kind, 0) + blocks
        if by_kind:
            out.append("")
            out.append("by kind:")
            for kind in sorted(by_kind, key=lambda k: (-by_kind[k], k)):
                out.append("%s  %s" % (str(by_kind[kind]).rjust(4), kind))
        return "\n".join(out) + "\n"

    n = max((r.total for r in report.engines.values()), default=0)
    out.append("conformance over %d corpus programs in %.1fs (reference: %s)"
               % (n, report.seconds, report.reference or "none"))
    out.append("")
    for name in report.unbuilt:
        out.append("note: %s is not built — that arm was not measured" % name)
    if report.unbuilt:
        out.append("")
    out.append("engine       MATCH  UNSUPPORTED  MISMATCH   coverage")
    for name, r in report.engines.items():
        out.append("%s %s  %s  %s   %s%%" % (
            _pad(name, 11), str(r.match).rjust(5), str(r.unsupported).rjust(11),
            str(r.mismatch).rjust(8), ("%.1f" % r.coverage).rjust(6)))

    shown = 0
    for name, r in report.engines.items():
        for v in r.failures():
            shown += 1
            if shown <= 25:
                out.append("  MISMATCH  %s %s: %s: %s"
                           % (_pad(name, 10), v.entry_id, v.kind, v.detail))
    if shown > 25:
        out.append("  … %d more" % (shown - 25))

    if report.skipped:
        out.append("")
        reasons: Dict[str, int] = {}
        for s in report.skipped:
            head = s.reason.split(":", 1)[0]
            reasons[head] = reasons.get(head, 0) + 1
        out.append("skipped %d not run: %s" % (
            len(report.skipped),
            ", ".join("%s (%d)" % (k, v) for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))))

    if report.routing_errors:
        out.append("")
        out.append("routing errors %d (must be 0) — a route that costs correctness, not time:"
                   % len(report.routing_errors))
        for e in report.routing_errors[:10]:
            out.append("  %s: predicted %s, ideal %s — %s"
                       % (e.entry_id, e.predicted, e.ideal or "none", e.detail))

    if report.damage:
        out.append("")
        n = len(report.damage)
        out.append("!! %d repository file%s changed by corpus programs and %s been"
                   " restored:" % (n, "" if n == 1 else "s",
                                   "has" if n == 1 else "have"))
        for d in report.damage[:20]:
            out.append("   %s" % d)
        if len(report.damage) > 20:
            out.append("   … and %d more" % (len(report.damage) - 20))
        out.append("   the run is a failure regardless of its verdicts")

    out.append("")
    out.append("MISMATCH %d — %s" % (report.mismatches, "ok" if report.ok else "FAIL"))
    return "\n".join(out) + "\n"
