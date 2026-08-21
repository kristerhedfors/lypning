"""Importing programs other repositories have published.

The invariant this module exists to hold: **an import is someone ELSE'S
evidence, so it may add to the corpus and may never add to this repository's
sightings.** ``tests/corpus/sightings/<session>.jsonl`` is a record of what ran
*here*, one writer per session, and every provenance claim the project makes
rests on that being literally true — ``docs/`` reasons from the corpus about
what agents type, and :mod:`lypning.harvest` ranks sources because a shim record
is stronger evidence than a transcript line. Folding an imported program into a
session file would forge the one field nobody can check afterwards: it would
assert that a program ran in a container that never existed. So the import ends
at :func:`harvest.fold_into_corpus`, which is the only place the corpus is
written and already does redaction, the size guard and max-not-sum counting.
Nothing in this module opens :func:`paths.sightings_dir`.

AN IMPORTED RECORD MAY NOT CLAIM WHAT AN IMPORT CANNOT KNOW. A published line
is somebody else's assertion about a machine this one has never seen, and the
moment a registry names a URL it is an assertion a stranger controls. Four
fields are claims rather than content and every one of them is cut down on the
way in by :func:`_as_imported`: ``stdin_sample`` is dropped outright,
``source`` is capped at the weakest provenance that still means "observed",
``count`` is clamped, and ``first_seen`` has to parse as a plausible instant or
it is discarded. This is the only place that trimming happens, so a record
reaching the fold has already stopped claiming.

THE SAME GATE A LOCAL HARVEST APPLIES. The fold is not the whole of what a
local export does: :func:`harvest._clean` stands in front of it and drops the
empty program, the ``pass``-only program and the program the corpus already
holds. An import runs the identical function over identical records, because
``README.md`` §3c and ``docs/CAPTURE.md`` both promise "the same fold a local
``lypning harvest`` uses" and a promise the code does not keep is worse than no
promise. Skipping it once already cost this corpus records that no local export
would have published.

DISCOVERY IS BY SHAPE, NOT BY NAME. A published collection is recognised by
sampling its first few lines and asking whether they are JSON objects carrying a
``program`` string — never by the directory it sits in. Matching on a name would
mean every source had to be told what to call its evidence, and the first
repository that disagreed would be silently invisible, which is the failure mode
this package cannot afford: an import that finds nothing looks exactly like an
import from a source that has nothing.

A FETCHED TREE IS DATA, NOT CODE. It is somebody's repository, cloned because a
line in a JSON file named a URL. Nothing in it is ever executed: no build, no
setup, no ``git`` hooks (``-c core.hooksPath=/dev/null`` on every invocation),
and ``GIT_TERMINAL_PROMPT=0`` so a URL we cannot read fails in a second instead
of blocking a session on a credential prompt nobody is there to answer. The
clone lands in state — ``~/.lypning/sources`` — because a working tree under
site-packages is one ``pip uninstall`` has never heard of, and one under a
checkout's ``assets/`` is a nested repository in ``git status`` forever.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from . import corpus, harvest, paths

# Largest single collection file considered. Past this it is a data dump that
# happens to end in .jsonl, not a session's published programs, and reading it
# would cost more memory than the whole corpus is worth.
MAX_SOURCE_BYTES = 32 * 1024 * 1024

# Most records taken from one collection file. The byte cap bounds the file and
# this bounds what comes out of it, which are two different quantities: a
# hand-written .jsonl of one-byte programs is inside the byte cap and still a
# few million Sighting objects, and every one of them is held in memory at once
# because the merge is a dict. A source that publishes more distinct programs
# than this is a dump, not a session's evidence.
MAX_SOURCE_RECORDS = 20000

# Two bounds, because a walk over somebody else's repository has two costs and
# one number cannot hold both. A source is an arbitrary repository, so neither
# ceiling may depend on the source behaving — and neither may be low enough for
# an ordinary one to reach, because a walk that stops early stops in whichever
# directory sorts first and reports the source as publishing nothing.
#
# Entries WALKED, directories included. This is the cheap axis: a directory is
# one `scandir` the walk had to make to know the entry exists, and an entry that
# is not a candidate costs a suffix test on a name. So the ceiling is set where
# a tree stops being a repository — a generated corpus, a vendored monorepo, an
# artefact directory nobody meant to publish — and nowhere near where an
# ordinary source lives: this repository walks a few hundred.
MAX_WALK_ENTRIES = 200000

# Files OPENED. This is the expensive axis and the one a bound on matches would
# miss: `looks_like_collection` is a read, and the two name tests in front of it
# are guesses, so a source with ten thousand files called `corpus.jsonl` costs
# ten thousand reads before a single one is rejected. Past this many published
# collections in one tree it is a dump rather than a session's evidence, and the
# per-file record cap has nothing to say about how many files there are.
MAX_SHAPE_TESTS = 4096

# Deepest directory the walk descends to. A path can nest until the filesystem
# says no, and a symlink loop is not the only way to get there: a fetched tree
# carrying its own vendored checkouts is enough. The bound is on depth rather
# than on total work because the file limit already bounds the work; this bounds
# the recursion that produces it.
MAX_DEPTH = 24

# The strongest provenance an imported record may keep. `shim` and `hook` are
# claims about THIS harness having watched a program run — the shim ranks above
# everything because it proves execution — and an import has watched a file.
# `transcript` is the weakest claim that still means "somebody observed this",
# which is exactly what a published line is. It is a ceiling and never a floor:
# a record declaring something weaker (`manual`, a seed) keeps what it declared.
# Because the fold keeps the incumbent on a tie, a downgraded import can never
# overwrite the provenance of a program this repository has seen for itself.
IMPORT_SOURCE_CEILING = "transcript"

# Largest count an imported record may claim. The count is not decoration: it is
# the frequency table `lypning corpus --stats` prints and the build order
# `conformance --plan` hands to whoever writes the next opcode. `fold_into_corpus`
# merges counts with max, so an inflated number is PERMANENT — re-importing a
# corrected upstream cannot lower it, and neither can anything short of editing
# the corpus by hand. The clamp keeps the ordering an honest count carries and
# discards the tail no honest count reaches: `lypning corpus --stats` on
# 2026-08-21 loaded 842 entries — what a reader sees, the harvested corpus and
# the seed together, not either file's line count — and the largest count over
# them was 45.
MAX_IMPORT_COUNT = 100

# Ten minutes. A shallow clone of a large repository over a slow link is
# minutes; anything past this is a hang, and a hang inside a Stop hook would be
# a session that never ends.
FETCH_TIMEOUT = 600

# Directories a walk must not descend into. `.git` first and for two reasons:
# it is most of the bytes, and a packed object that happens to decompress into
# something ending in .jsonl is not a published collection.
_PRUNE = frozenset((
    ".git", "node_modules", "target", ".venv", "venv", "__pycache__",
    "dist", "build",
))

# `scheme://…`, and the scp-like `git@host:path` that git accepts and urlparse
# does not. Anything else is a path on this machine.
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_SCP_LIKE = re.compile(r"^[^/\s:]+@[^/\s:]+:")

# How many lines decide whether a file is a collection. Enough that a .jsonl of
# something else does not pass on one lucky line, few enough that the test costs
# a single read of the head of the file.
_SAMPLE_LINES = 5


# --- a source ----------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """One place programs can be imported from.

    Frozen because a source is a line in a registry file, not state: resolving
    one produces a path, it does not mutate the source.
    """

    name: str
    location: str
    note: str = ""

    @property
    def is_url(self) -> bool:
        """Does this location have to be fetched, or can it just be read?

        The one question that decides whether git runs at all, so it is answered
        by the shape of the string rather than by trying and seeing: a typo in a
        path must not become a network request to a host somebody registered.
        """
        text = str(self.location or "").strip()
        return bool(_URL_SCHEME.match(text) or _SCP_LIKE.match(text))


def slug(location: str) -> str:
    """A cache directory name for a location: readable, and collision-free.

    The readable half is the last path element, which is what makes
    ``~/.lypning/sources`` browsable by a human wondering what got cloned. It is
    not enough on its own — two forks of one project share a repository name,
    and landing both in ``sources/lypning`` would have the second import quietly
    read the first one's tree. So the full location is hashed and the digest
    appended: identical locations always agree, different ones never do.
    """
    text = str(location or "").strip()
    base = text.rstrip("/")
    if base.endswith(".git"):
        base = base[: -len(".git")]
    base = base.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", base).strip("-._")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return (safe[:48] or "source") + "-" + digest


def _split_env_locations(value: str) -> List[str]:
    """Split ``$LYPNING_SOURCES`` on ``os.pathsep``, then put the URLs back.

    On POSIX the separator is ``:``, which is also the character inside every
    URL the variable is allowed to hold — a naive split turns
    ``https://host/repo`` into ``https`` and ``//host/repo``, and the first is a
    relative path that does not exist while the second is a location nobody
    named. Two rejoins fix both spellings git accepts: a bare scheme followed by
    something starting ``//``, and a ``user@host`` fragment followed by a path.
    A path containing a literal colon is unrepresentable here, which is the
    price of honouring the separator every other PATH-shaped variable uses.
    """
    parts = [p for p in str(value or "").split(os.pathsep)]
    out: List[str] = []
    for part in parts:
        if out and re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*$", out[-1]) and part.startswith("//"):
            out[-1] = out[-1] + ":" + part
            continue
        if out and re.match(r"^[^/\s:]+@[^/\s:]+$", out[-1]) and part:
            out[-1] = out[-1] + ":" + part
            continue
        out.append(part)
    return [p.strip() for p in out if p.strip()]


def load_sources(path: Optional[Path] = None) -> List[Source]:
    """The registry, then whatever ``$LYPNING_SOURCES`` adds.

    Never raises. A registry that is missing, unreadable or half-written yields
    what could be read from it — usually nothing — because a malformed data file
    must not be the reason a user cannot import from the location they just put
    in an environment variable. The environment comes last and is never
    deduplicated against the file: naming a source twice costs one wasted walk,
    and silently dropping the one the user typed by hand costs an import they
    asked for.
    """
    target = Path(path) if path is not None else paths.SOURCES_FILE
    out: List[Source] = []
    try:
        with open(str(target), "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = None
    entries = data.get("sources") if isinstance(data, dict) else None
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, dict):
            continue
        location = item.get("location")
        if not isinstance(location, str) or not location.strip():
            continue
        name = item.get("name")
        note = item.get("note")
        out.append(Source(
            name=name.strip() if isinstance(name, str) and name.strip() else slug(location),
            location=location.strip(),
            note=note if isinstance(note, str) else "",
        ))
    for location in _split_env_locations(os.environ.get("LYPNING_SOURCES", "")):
        out.append(Source(name=slug(location), location=location, note="$LYPNING_SOURCES"))
    return out


# --- fetching ----------------------------------------------------------------


def _git_env() -> Dict[str, str]:
    """The environment every git call here runs under.

    ``GIT_TERMINAL_PROMPT=0`` and an emptied credential helper are the whole
    point: a private URL in a registry must fail in a second, not block forever
    on a prompt that nothing in a hook or a CI runner will ever answer. The
    three ``GIT_DIR``-family variables are dropped because a session's own git
    plumbing sets them, and inheriting one would point ``-C <clone>`` back at
    the repository we are running inside — the import would then "update" the
    user's checkout.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    return env


def _git(args: Sequence[str], *, timeout: int):
    """Run git with hooks disabled. Returns the CompletedProcess, or None.

    ``core.hooksPath=/dev/null`` is not belt-and-braces. A fetched tree can
    carry ``.git/hooks`` content of its own, and every later command in that
    clone would run it; a clone is data, and data does not get to execute.
    """
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "credential.helper="]
    cmd.extend(str(a) for a in args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              check=False, env=_git_env())
    except (OSError, subprocess.SubprocessError):
        return None


# The line of git's stderr that says what went wrong, as opposed to the several
# that say what it was attempting. `Cloning into '<dest>'` is written before the
# failure and is the FIRST line, so taking the first line reports a path back to
# a user who wanted to know that the repository does not exist.
_GIT_ERROR_LINE = re.compile(r"^(fatal|error|remote|warning):", re.IGNORECASE)


def _reason(result, fallback: str) -> str:
    """A git failure as one short line. Full stderr is a paragraph of advice
    about credentials and helpers, and a report is not the place for it."""
    if result is None:
        return fallback
    lines = [ln.strip() for ln in (result.stderr or result.stdout or "").split("\n") if ln.strip()]
    named = next((ln for ln in lines if _GIT_ERROR_LINE.match(ln)), "")
    # The last line when nothing announced itself: git's own summary sits at the
    # end, and the progress it wrote on the way there sits above it.
    return ((named or (lines[-1] if lines else ""))[:120] or fallback)


def _same_remote(a: str, b: str) -> bool:
    def canon(u: str) -> str:
        u = str(u or "").strip().rstrip("/")
        return u[: -len(".git")] if u.endswith(".git") else u
    return canon(a) == canon(b)


def fetch(source: Source, *, dest: Optional[Path] = None, timeout: int = FETCH_TIMEOUT,
          offline: bool = False) -> Tuple[Optional[Path], str]:
    """Resolve a source to a directory. Returns ``(path, note)`` or ``(None, reason)``.

    Never raises, because one unreachable source must not cost the import every
    other source. The reason is carried instead and ends up in the report, which
    is the only way a user finds out that the collection they expected is
    missing rather than empty.

    A local location is read where it lies and never copied. A URL is shallow
    cloned once and thereafter updated in place; a fetch that fails over a cache
    that already exists resolves to the cache anyway and says so — yesterday's
    programs are worth more than an import that refuses to run on a train.
    """
    location = str(source.location or "").strip()
    if not location:
        return None, "no location"

    if not source.is_url:
        local = Path(location).expanduser()
        if local.is_dir() or local.is_file():
            try:
                return local.resolve(), "local"
            except OSError:
                return local, "local"
        return None, "no such path"

    target = Path(dest) if dest is not None else paths.sources_cache_dir() / slug(location)
    is_clone = (target / ".git").exists()

    if offline:
        # Deliberately not "try git and fall back": offline means no process
        # touches the network, so that a user who asked for it can be sure.
        return (target, "cached") if is_clone else (None, "offline")

    if is_clone:
        remote = _git(["-C", str(target), "remote", "get-url", "origin"], timeout=60)
        url = (remote.stdout or "").strip() if remote is not None and remote.returncode == 0 else ""
        if not _same_remote(url, location):
            # The slug carries a hash of the location, so this is a corrupted or
            # hand-edited cache rather than a collision. Refuse rather than
            # delete: the directory is under the user's home and we did not put
            # what is in it there.
            return None, "cache holds a different repository"
        pulled = _git(["-C", str(target), "fetch", "--depth", "1", "origin"], timeout=timeout)
        if pulled is None or pulled.returncode != 0:
            return target, "cached (fetch failed: %s)" % _reason(pulled, "git unavailable")
        reset = _git(["-C", str(target), "reset", "--hard", "FETCH_HEAD"], timeout=timeout)
        if reset is None or reset.returncode != 0:
            return target, "cached (update failed)"
        return target, "updated"

    if target.exists():
        try:
            occupied = any(target.iterdir())
        except OSError:
            occupied = True
        if occupied:
            return None, "cache directory is not a clone"

    try:
        paths.ensure_dir(target.parent)
    except OSError:
        return None, "cache directory is not writable"
    # No `--recurse-submodules`: a submodule is a second URL the registry never
    # named, and this import fetches only what a human wrote down.
    cloned = _git(["clone", "--depth", "1", location, str(target)], timeout=timeout)
    if cloned is None or cloned.returncode != 0:
        return None, _reason(cloned, "git unavailable")
    return target, "cloned"


# --- finding published collections -------------------------------------------


def looks_like_collection(path: Path) -> bool:
    """Is this .jsonl a published collection of programs?

    Decided by SHAPE. Every sampled line must be a JSON object with a non-empty
    ``program`` string — the one field the record type is *for*, and the one no
    other JSONL in a repository has by accident. This test is the whole reason
    the importer works against a source that named its published directory
    something other than what this repository calls it, and it is why adding a
    source is a URL rather than a URL plus a path.

    Every sampled line, not any: a log file with one python-shaped record in it
    would pass an ``any`` test and then contribute several thousand records that
    are not programs.
    """
    seen = 0
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    return False
                if not isinstance(obj, dict):
                    return False
                program = obj.get("program")
                if not isinstance(program, str) or not program:
                    return False
                seen += 1
                if seen >= _SAMPLE_LINES:
                    break
    except OSError:
        return False
    return seen > 0


def _rel_base(root: Path) -> Path:
    """The directory ancestor names are measured from: one above the walk root.

    One above, so the root's OWN name is part of the chain. Pointing a source
    straight at a published directory — ``--from ../other/tests/corpus/sightings``
    — is the obvious thing to type, and measuring from the root itself would
    leave that walk with an empty ancestor chain and find nothing at all.
    """
    parent = root.parent
    return root if parent == root else parent


def _is_candidate(path: Path, rel_base: Path) -> bool:
    """Worth opening FIRST — a priority, not a filter.

    This used to be the filter, and it made "discovery is by shape" false: a
    ``.jsonl`` whose name held no ``corpus`` and whose parents held no
    ``sightings`` was never opened, so the shape test never ran on it. That is
    not a corner case. ``lypning install --collect-only --sightings DIR`` exists
    precisely so a repository can publish somewhere that suits it, and the first
    thing tried here — a repository publishing to ``.lypning/programs`` — was
    invisible to the importer that told it to. The two halves of the feature
    contradicted each other, and the failure was the quiet kind: zero files
    found reads exactly like a source that has nothing to give.

    So every ``.jsonl`` is shape-tested now, and these two name tests only
    decide what gets tested before the open budget runs out. A source that
    publishes where anyone would guess is still found first and still cheap; one
    that publishes anywhere else is found too. The ``sightings`` test looks at
    every ancestor rather than the immediate parent because a rolled-up
    directory nests.
    """
    if "corpus" in path.name.lower():
        return True
    try:
        parts = path.parent.relative_to(rel_base).parts
    except ValueError:
        parts = path.parent.parts
    return any(part.lower() == "sightings" for part in parts)


def _within_cap(path: Path) -> bool:
    """Is this file small enough to read at all?

    Its own function because the guard has to hold on BOTH paths into
    :func:`discover` and again inside :func:`read_collection`. It used to live
    inside the walk loop only, which left ``--from /path/to/dump.jsonl`` — the
    spelling that names a file directly — going straight from the five-line
    shape test to reading every line of a multi-gigabyte dump. A cheap test that
    is only cheap because it reads five lines is not a size guard.
    """
    try:
        return path.stat().st_size <= MAX_SOURCE_BYTES
    except OSError:
        return False


def _inside(path: Path, root: Path) -> bool:
    """Does this candidate actually live in the tree we are walking?

    ``os.walk`` does not descend into symlinked directories, but ``stat`` and
    ``open`` follow symlinked FILES without comment, so a fetched tree that
    publishes ``sightings/imported.jsonl -> ../../../../.lypning/invocations.jsonl``
    hands the importer the unredacted capture log — which ``docs/CAPTURE.md``
    says in as many words is not safe to publish — and, one fold later, a
    committed file. A ``~/.claude/projects/**`` transcript is the same shape and
    the same story. Both satisfy the shape test, because they are exactly the
    record type the shape test is looking for.

    So every symlink the walk hands back is refused outright, whatever it points
    at. Refusing one that points inside the tree costs the import nothing — the
    file it names is walked in its own right — and asking where it leads instead
    would put the answer in the hands of the thing being screened. The
    resolve-and-compare after it is a second question, not the same one: it
    catches a candidate whose PARENT left the tree, which ``os.walk`` does not
    descend into today and which a change to how this walk descends would
    otherwise turn into a read outside the tree with nothing here noticing.
    """
    try:
        if os.path.islink(str(path)):
            return False
        path.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def _depth(dirpath: str, base: Path) -> int:
    """How far below the walk root this directory sits. 0 is the root itself."""
    try:
        rel = os.path.relpath(dirpath, str(base))
    except (OSError, ValueError):
        return MAX_DEPTH  # unrelatable to the root: descend no further from it
    return 0 if rel == os.curdir else rel.count(os.sep) + 1


@dataclass(frozen=True)
class Discovery:
    """What one walk found, and whether it was allowed to finish.

    Two fields rather than a list of paths, because a walk that stopped at a
    bound and a source that publishes nothing produce the same empty list, and
    that confusion is the failure this module's docstring says it cannot afford:
    an import that finds nothing looks exactly like an import from a source that
    has nothing. ``truncated`` is the only thing that separates them, so it is
    carried out of the walk, into :class:`ImportResult` and onto the report
    rather than being a decision made and forgotten here. A bound that quietly
    returns fewer files is worse than no bound at all — the import still says
    ``ok``, and what it lost was somebody's whole collection.
    """

    files: List[Path] = field(default_factory=list)
    truncated: bool = False


def discover(root: Path, *, entries: int = MAX_WALK_ENTRIES,
             opens: int = MAX_SHAPE_TESTS) -> Discovery:
    """Every published collection under a tree, and whether the walk finished.

    Sorted, deduplicated, never raises. The bounds are :data:`MAX_WALK_ENTRIES`
    on what the walk looks at, :data:`MAX_SHAPE_TESTS` on what it opens, and
    :data:`MAX_DEPTH` on how far down it goes; each constant carries why it sits
    where it does. Hitting any of the three sets ``truncated`` on the result,
    which is what makes the cost of a limit being hit a short report rather than
    a source that has silently gone quiet.
    """
    base = Path(root)
    try:
        base_real = base.resolve()
    except OSError:
        base_real = base

    if base.is_file():
        # A location naming one file is a location a human wrote down, so this
        # path does not screen symlinks — following the link the user typed is
        # the point of typing it. The size cap is not optional either way.
        # No name test at all: a location naming one file is a location a human
        # wrote down, and refusing to read it because of what it is called would
        # be answering a question they already answered.
        ok = _within_cap(base) and looks_like_collection(base)
        return Discovery([base] if ok else [], False)

    rel_base = _rel_base(base)
    found: List[Path] = []
    likely: List[Path] = []
    rest: List[Path] = []
    walked = 0
    truncated = False
    stop = False
    try:
        for dirpath, dirnames, filenames in os.walk(str(base)):
            # The directory counts as an entry too. A tree of a million empty
            # directories costs nothing per file and is still a tree somebody
            # can hand us, so a bound that only counted files would not bound it.
            walked += 1
            if walked > entries:
                truncated = stop = True
                break
            # In place, because os.walk reads this list back to decide where to
            # descend; rebinding it would prune nothing.
            dirnames[:] = sorted(d for d in dirnames if d not in _PRUNE and not d.startswith(".git"))
            if _depth(dirpath, base) >= MAX_DEPTH and dirnames:
                # Files at this level still count, children do not — and a tree
                # that publishes below here has had part of itself hidden, which
                # is the same lie as a stopped walk and is reported the same way.
                dirnames[:] = []
                truncated = True
            for name in sorted(filenames):
                walked += 1
                if walked > entries:
                    truncated = stop = True
                    break
                path = Path(dirpath) / name
                if path.suffix != ".jsonl":
                    continue
                # Collected now, opened later. The shape test is the decider and
                # it must run on every .jsonl, but the open budget may not
                # stretch to all of them — so the walk gathers and the pass
                # below spends, likeliest first.
                (likely if _is_candidate(path, rel_base) else rest).append(path)
            if stop:
                # Only the two exhausted budgets end the walk. A directory
                # pruned for depth is one branch cut, not a reason to abandon
                # the siblings that are still within every bound there is.
                break
    except OSError:
        pass  # a tree that vanished mid-walk yields what it had already given

    # Likeliest first, so a budget that runs out spends what it had on the files
    # a source most probably published to — and says so rather than reporting
    # the remainder as absent.
    opened = 0
    for path in likely + rest:
        if opened >= opens:
            truncated = True
            break
        opened += 1
        if not _inside(path, base_real) or not _within_cap(path):
            continue
        if looks_like_collection(path):
            found.append(path)
    return Discovery(sorted(set(found)), truncated)


# --- what an imported record is allowed to claim ------------------------------


# An instant, in the spellings a published record actually uses: the `Z` suffix
# `datetime.fromisoformat` does not accept before 3.11, a numeric offset with or
# without its colon, and a fractional part of any length. Matching by shape
# rather than handing the string to a parser is what makes "1970-01-01",
# "yesterday" and "" all fail the same way, which is the point — this is a
# plausibility test, not a date library.
_ISO_INSTANT = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?"
    r"\s*(?:([Zz])|([+-])(\d{2}):?(\d{2}))?$")

# Before this, nothing a harness of this shape could have recorded existed, so
# no honest `first_seen` is under it and every fabricated one a poisoner reaches
# for — the epoch, year one, a negative-looking stamp — is.
_FIRST_SEEN_FLOOR = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)

# A day, because a source whose clock is ahead of this one is not lying and a
# record stamped in the next century is.
_FIRST_SEEN_SKEW = datetime.timedelta(days=1)


def _instant(text: Any) -> Optional[datetime.datetime]:
    """A published timestamp as a UTC datetime, or None if it is not one."""
    m = _ISO_INSTANT.match(str(text or "").strip())
    if m is None:
        return None
    try:
        when = datetime.datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5)), int(m.group(6)),
            int((m.group(7) or "0").ljust(6, "0")[:6]), tzinfo=datetime.timezone.utc)
    except ValueError:
        return None  # month 13, day 32: shaped like an instant, is not one
    if m.group(9):
        sign = -1 if m.group(9) == "-" else 1
        offset = datetime.timedelta(hours=int(m.group(10)), minutes=int(m.group(11)))
        when -= sign * offset  # the stated offset back off, leaving UTC
    return when


def _believable_first_seen(text: Any) -> str:
    """The record's ``first_seen`` if it could be one, otherwise ``""``.

    ``""`` and not "now". :func:`harvest.fold_into_corpus` merges stamps with
    ``min``, so a fabricated ``1970-01-01`` does not just arrive wrong, it wins
    and keeps winning: the corpus record is pinned to it and no later import can
    move it. Substituting the current time instead would fix the ordering by
    telling the same kind of lie in the other direction — this repository does
    not know when somebody else's program first ran. ``""`` is the spelling
    ``min`` already skips, and it is the honest one: unknown.
    """
    when = _instant(text)
    if when is None:
        return ""
    now = datetime.datetime.now(datetime.timezone.utc)
    if when < _FIRST_SEEN_FLOOR or when > now + _FIRST_SEEN_SKEW:
        return ""
    return str(text).strip()


# The rank the ceiling sits at, resolved once: the cap is a comparison on every
# record read out of every file.
_CEILING_RANK = harvest.SOURCE_RANK[IMPORT_SOURCE_CEILING]


def _as_imported(s: harvest.Sighting) -> harvest.Sighting:
    """One published record, reduced to what an import can honestly assert.

    Everything trimmed here is a CLAIM rather than content, and every claim on
    a published line is written by whoever controls the source. The program text
    is content and survives — redaction and the size guard are the fold's job
    and it does them. These four are not content:

    ``stdin_sample`` is dropped, not redacted, not screened. The field means
    "what a session piped into this program HERE", which an import cannot
    honestly claim about a program that ran somewhere else, and carrying it
    costs twice over. :func:`harvest.fold_into_corpus` redacts ``program`` and
    ``argv_tail`` and copies ``stdin_sample`` through untouched, so an
    unredacted credential in that field lands verbatim in a committed file. And
    :func:`conformance._run_entry` screens ``program`` and ``argv_tail`` with
    ``absolute_paths()`` and skips an entry that names a path outside its temp
    cwd — it does not screen stdin, which it reads and feeds to CPython and to
    every engine arm. A source publishing ``{"program": "…open(sys.stdin.read()
    .strip(), 'w')…", "stdin_sample": "<a path in this checkout>"}`` therefore
    gets a file in this repository truncated by the next `lypning conformance`,
    and `lypning bench` on the same path. Nothing about that needs the source to
    be hostile on purpose. There is no version of this field an import can
    verify, so there is nothing to keep.

    ``source`` is capped at :data:`IMPORT_SOURCE_CEILING`, ``count`` at
    :data:`MAX_IMPORT_COUNT`, and ``first_seen`` has to survive
    :func:`_believable_first_seen`; each constant carries the reason it exists.
    All three are one-way: this never raises a record's claim, only lowers it.
    """
    count = s.count if isinstance(s.count, int) and not isinstance(s.count, bool) else 1
    return replace(
        s,
        stdin_sample=None,
        source=(IMPORT_SOURCE_CEILING
                if harvest.SOURCE_RANK.get(s.source, 0) > _CEILING_RANK else s.source),
        count=max(1, min(count, MAX_IMPORT_COUNT)),
        first_seen=_believable_first_seen(s.first_seen),
    )


def read_collection(path: Path) -> List[harvest.Sighting]:
    """A published file back as sightings. A corrupt line is dropped, never fatal.

    Read line by line rather than whole, and stopped at
    :data:`MAX_SOURCE_RECORDS`: the byte cap is re-checked here rather than
    assumed, because :func:`discover` is not the only caller and a file named
    directly used to reach this function unmeasured.

    Provenance is NOT whatever the record declares. Every record goes through
    :func:`_as_imported` at the moment it becomes a :class:`harvest.Sighting`,
    which is the earliest point at which one exists — so no caller of this
    function, present or future, has to remember to disarm one.
    """
    out: List[harvest.Sighting] = []
    if not _within_cap(Path(path)):
        return out
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if len(out) >= MAX_SOURCE_RECORDS:
                    break
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                s = harvest.Sighting.from_obj(obj)
                if s is not None:
                    out.append(_as_imported(s))
    except OSError:
        return out
    return out


# --- the import --------------------------------------------------------------


@dataclass
class ImportResult:
    """What one import did, for the reporter and the tests."""

    sources: List[Dict[str, Any]] = field(default_factory=list)
    gathered: int = 0
    new: int = 0
    added: int = 0
    total: int = 0
    corpus: str = ""
    dry_run: bool = False
    #: The sources whose walk stopped at a bound instead of at the end of the
    #: tree. Named rather than counted, because the question a reader has next
    #: is which collection they are missing, and the answer starts with whose.
    #: Empty is the ordinary case and the one that means the counts above are
    #: the whole of what these sources publish.
    truncated: List[str] = field(default_factory=list)

    @property
    def resolved(self) -> List[Dict[str, Any]]:
        return [s for s in self.sources if s.get("resolved")]

    def to_obj(self) -> Dict[str, Any]:
        """For ``lypning collect --json``. Paths as strings, nothing else."""
        return {
            "sources": [dict(s) for s in self.sources],
            "gathered": self.gathered,
            "new": self.new,
            "added": self.added,
            "total": self.total,
            "corpus": self.corpus,
            "dry_run": self.dry_run,
            "truncated": list(self.truncated),
        }


def _publishable(sightings: Sequence[harvest.Sighting],
                 known: Set[str]) -> List[harvest.Sighting]:
    """The records an import may publish, redacted and re-keyed, in order.

    This calls :func:`harvest._clean` — the private one, deliberately, because
    it IS the gate a local ``lypning harvest`` puts in front of the fold and a
    second implementation of it is a second implementation that drifts. The fold
    now runs the content half of that gate itself, so the two agree about what
    is a program; what it cannot run is the IDENTITY half, because a record the
    corpus already holds is exactly what a fold is merging. That last test is
    what this function adds: a program the corpus already holds re-enters as an
    observation under a key the seed corpus spells differently, which is how an
    expectation somebody typed by hand turns into evidence that agents type it
    (see :func:`harvest.known_keys`, which exists because that happened).

    Importing without this gate is not theoretical either: the import run before
    this function existed put records into the corpus that no local export could
    have produced, one of them an unexpanded shell variable with shell
    redirections captured as ``argv_tail``, which is not a Python program at all.

    Running it here also means ``--dry-run`` can quote the number the real run
    will produce rather than an optimistic one, because these are the very
    records the fold is then handed.
    """
    out: List[harvest.Sighting] = []
    for s in sightings:
        cleaned, _hits, _why = harvest._clean(s, known)
        if cleaned is not None:
            out.append(cleaned)
    return out


def import_sources(sources: Sequence[Source], *, corpus_path: Optional[Path] = None,
                   dry_run: bool = False, offline: bool = False) -> ImportResult:
    """Fetch, discover, read, merge, fold. Returns what happened.

    The merge is by key across every source, so a program two repositories both
    published is one record with the higher count rather than two — the same
    max-not-sum rule :func:`harvest._combine` holds for sessions, and for the
    same reason: both sides are counting the same occurrences. The key is
    recomputed rather than taken from the record; see the merge loop for what
    trusting a foreign one costs.

    Between the merge and the fold sits :func:`_publishable`, which is the gate
    a local export applies — so an import is the same fold reached the same way,
    which is what ``README.md`` §3c says it is.

    Nothing here writes a sighting. The imported programs go to
    :func:`harvest.fold_into_corpus` and stop there; see the module docstring
    for why publishing them as this session's evidence would be a lie rather
    than a shortcut.
    """
    target = Path(corpus_path) if corpus_path is not None else paths.corpus_write_file()
    result = ImportResult(corpus=str(target), dry_run=dry_run)

    merged: Dict[str, harvest.Sighting] = {}
    for source in sources:
        path, note = fetch(source, offline=offline)
        walk = discover(path) if path is not None else Discovery()
        files = walk.files
        if walk.truncated:
            result.truncated.append(source.name)
        programs = 0
        for file in files:
            for s in read_collection(file):
                programs += 1
                # Re-keyed on OUR key function, never merged on the one the
                # record declares. A key is only a dedup handle if both sides
                # compute it the same way, and another repository's does not
                # have to: measured against the source this package was
                # extracted from, most of the declared keys disagreed with
                # `sighting_key` — it keys on the raw program text where this
                # tree keys on the normalised text, and its seed records are
                # keyed by hand-written slugs. Merging on what the file said
                # would have reported far more distinct programs than there are,
                # and then handed the fold the same program under two spellings.
                # Re-run `lypning collect --dry-run` if you want that ratio as a
                # number: it is a property of whatever is registered today.
                key = harvest.sighting_key(s.program)
                s = replace(s, key=key)
                cur = merged.get(key)
                merged[key] = s if cur is None else harvest._combine(cur, s)
        result.sources.append({
            "name": source.name,
            "location": source.location,
            "resolved": str(path) if path is not None else None,
            "files": len(files),
            "programs": programs,
            "note": note,
            "truncated": walk.truncated,
        })

    sightings = [merged[k] for k in sorted(merged)]
    result.gathered = len(sightings)

    # What "already known" means, and it is two things. The corpus the fold is
    # about to write is one of them, and only that file can answer for it. The
    # corpus a READER sees is the larger one in both shapes this package ships
    # in: in a checkout it also holds the seed records, and in a wheel it also
    # holds the shipped corpus the fold may not write into.
    #
    # The seed half is the one with a price already paid. A seed program is an
    # expectation somebody typed by hand, keyed by a slug rather than by
    # content, so a fold left to itself does not recognise one and re-files it
    # as an observation; :func:`harvest.known_keys` exists because an early
    # harvest reported most of its "observed" programs when they were
    # byte-identical to seeds, and expectation must not inflate the frequency
    # table that ranks work. Passing this set to the gate below is what keeps
    # that from happening again through the import door.
    corpus_ids: Set[str] = set()
    try:
        corpus_ids = {e.id for e in corpus.load(target)}
    except Exception:
        pass  # no corpus written yet: everything is new, which is true
    known = set(corpus_ids)
    if corpus_path is None:
        known |= harvest.known_keys()

    # The gate, then the counts over what survived it. Both counts are taken on
    # the key a program would be PUBLISHED under — redaction rewrites the text
    # and therefore the id, and counting the raw form would report a program as
    # new and then file it as another. The gate has already done that rewrite,
    # so these keys are the ones the fold will use.
    keep = _publishable(sightings, known)
    publishable = {s.key for s in keep}
    result.new = len(publishable - known)

    if dry_run:
        would = publishable - corpus_ids
        result.added = len(would)
        result.total = len(corpus_ids) + len(would)
        return result

    result.added, result.total = harvest.fold_into_corpus(keep, target)
    return result


# --- reporting ---------------------------------------------------------------


def render(result: ImportResult) -> str:
    """The import summary. ASCII only, for the reason :func:`harvest.render`
    gives: this goes to a terminal whose encoding we do not control, and a
    report is not worth a UnicodeEncodeError.
    """
    resolved = len(result.resolved)
    rows: List[Tuple[str, str]] = [
        ("sources", "{0} of {1} resolved".format(resolved, len(result.sources))),
        ("gathered", "{0} distinct program(s)".format(result.gathered)),
        ("new", "{0} not already in the corpus".format(result.new)),
        ("corpus", "{0} added, {1} total".format(result.added, result.total)),
        ("file", result.corpus + ("  (dry run - not written)" if result.dry_run else "")),
    ]
    # There is deliberately no "added exceeded new" line here. `_publishable`
    # drops every record whose key is in `known`, and `known` holds the ids of
    # the corpus being written plus, in the shape that has them, the seed and
    # shipped keys — so `new` counts exactly the records handed to the fold and
    # `added` can only be that number or fewer. A branch for a case the gate has
    # made unreachable reads to the next person as a case that still happens,
    # and sends them looking for the path that produces it.

    if result.truncated:
        # A walk that stopped at a bound and a source with nothing to publish
        # return the same empty list, and this is the only line that tells them
        # apart. It says INCOMPLETE rather than a count because the number of
        # collections it did not reach is exactly what it did not stay to learn.
        rows.append(("truncated", "{0}: the walk stopped at a limit, so these counts "
                                  "are INCOMPLETE".format(", ".join(result.truncated))))
    width = max(len(k) for k, _ in rows)
    out = ["collect", "=" * 7]
    for k, v in rows:
        out.append("{0}  {1}".format(k.ljust(width), v).rstrip())

    if not result.sources:
        out.append("")
        out.append("no sources configured")
        return "\n".join(out) + "\n"

    out.append("")
    name_width = max(len(str(s.get("name") or "")) for s in result.sources)
    for s in result.sources:
        # The marker rides in the note column and next to the counts it
        # qualifies, because a per-source count that is short is read one row at
        # a time and a warning three rows up is read once.
        note = str(s.get("note") or "") + (" [walk truncated]" if s.get("truncated") else "")
        out.append("{0}  {1:>4} file(s)  {2:>6} program(s)  {3}".format(
            str(s.get("name") or "").ljust(name_width),
            s.get("files") or 0, s.get("programs") or 0, note))
        # The location on its own line rather than in the row: a git URL is
        # sixty characters and would push the counts off the side of a terminal,
        # and the counts are what a reader came for.
        out.append("  " + str(s.get("location") or ""))
    return "\n".join(out) + "\n"
