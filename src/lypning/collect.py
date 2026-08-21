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

# Most files examined under one source. A source is an arbitrary repository, so
# the walk needs a ceiling that does not depend on the source behaving.
MAX_FILES = 4096

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
    """Worth opening at all.

    Two cheap name tests standing in front of the expensive shape test, so a
    repository full of .jsonl fixtures costs a directory listing rather than a
    read of every one of them. Both are guesses about where a source publishes;
    :func:`looks_like_collection` is what actually decides, so a guess that is
    too generous costs one open and a guess that is too narrow loses a
    collection — which is why the ``sightings`` test looks at every ancestor
    directory rather than only the immediate parent.
    """
    if path.suffix != ".jsonl":
        return False
    if "corpus" in path.name.lower():
        return True
    try:
        parts = path.parent.relative_to(rel_base).parts
    except ValueError:
        parts = path.parent.parts
    return any(part.lower() == "sightings" for part in parts)


def discover(root: Path, *, limit: int = MAX_FILES) -> List[Path]:
    """Every published collection under a tree. Sorted, deduplicated, never raises.

    The bound is on files *considered*, not on files matched: a walk over
    somebody else's repository is a walk over an unknown quantity, and the cost
    of the limit being hit is a note in a report, while the cost of no limit is
    an import that never returns.
    """
    base = Path(root)
    if base.is_file():
        ok = _is_candidate(base, _rel_base(base.parent)) and looks_like_collection(base)
        return [base] if ok else []

    rel_base = _rel_base(base)
    found: List[Path] = []
    considered = 0
    try:
        for dirpath, dirnames, filenames in os.walk(str(base)):
            # In place, because os.walk reads this list back to decide where to
            # descend; rebinding it would prune nothing.
            dirnames[:] = sorted(d for d in dirnames if d not in _PRUNE and not d.startswith(".git"))
            for name in sorted(filenames):
                if considered >= limit:
                    return sorted(set(found))
                path = Path(dirpath) / name
                if not _is_candidate(path, rel_base):
                    continue
                considered += 1
                try:
                    if path.stat().st_size > MAX_SOURCE_BYTES:
                        continue
                except OSError:
                    continue
                if looks_like_collection(path):
                    found.append(path)
    except OSError:
        pass  # a tree that vanished mid-walk yields what it had already given
    return sorted(set(found))


def read_collection(path: Path) -> List[harvest.Sighting]:
    """A published file back as sightings. A corrupt line is dropped, never fatal.

    Read line by line rather than whole: these files are bounded by
    :data:`MAX_SOURCE_BYTES`, which is large enough that slurping one to find
    out it is unparseable is a cost worth not paying.

    Provenance is whatever the record declares. It is not downgraded to mark it
    as imported, because it is not ours to relabel — a shim record from another
    repository still means a program that actually ran, and
    :data:`harvest.SOURCE_RANK` already ranks the spellings it does not know.
    """
    out: List[harvest.Sighting] = []
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                s = harvest.Sighting.from_obj(obj)
                if s is not None:
                    out.append(s)
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
        }


def _publishable_key(s: harvest.Sighting) -> Optional[str]:
    """The id this sighting would land under, or None if the fold will drop it.

    A restatement of :func:`harvest.fold_into_corpus`'s guards, and the only
    duplication in this module. It exists so ``--dry-run`` can quote the number
    the real run will produce instead of an optimistic one; the fold remains the
    single writer, and if the two ever disagree it is this function that is
    wrong. Redaction runs first because it changes the text and therefore the
    key — the corpus stores the redacted program, so that is the id to compare.
    """
    program, hits = harvest.redact(s.program)
    if not harvest.is_safe(hits) or not harvest.normalise(program):
        return None
    if len(program.encode("utf-8")) > harvest.MAX_PROGRAM_BYTES:
        return None
    _, tail_hits = harvest.redact_argv(s.argv_tail)
    if not harvest.is_safe(tail_hits):
        return None
    return harvest.sighting_key(program)


def import_sources(sources: Sequence[Source], *, corpus_path: Optional[Path] = None,
                   dry_run: bool = False, offline: bool = False) -> ImportResult:
    """Fetch, discover, read, merge, fold. Returns what happened.

    The merge is by key across every source, so a program two repositories both
    published is one record with the higher count rather than two — the same
    max-not-sum rule :func:`harvest._combine` holds for sessions, and for the
    same reason: both sides are counting the same occurrences. The key is
    recomputed rather than taken from the record; see the merge loop for what
    trusting a foreign one costs.

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
        files = discover(path) if path is not None else []
        programs = 0
        for file in files:
            for s in read_collection(file):
                programs += 1
                # Re-keyed on OUR key function, never merged on the one the
                # record declares. A key is only a dedup handle if both sides
                # compute it the same way, and another repository's does not
                # have to: measured against the source this package was
                # extracted from, 1403 of 2081 declared keys disagreed with
                # `sighting_key` — it keys on the raw program text where this
                # tree keys on the normalised text, and its seed records are
                # keyed by hand-written slugs. Merging on what the file said
                # would have reported 2081 programs where there are 790, and
                # then handed the fold the same program under two spellings.
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
        })

    sightings = [merged[k] for k in sorted(merged)]
    result.gathered = len(sightings)

    # Both counts are taken over the key a program would be PUBLISHED under —
    # redaction rewrites the text and therefore the id, and counting the raw
    # form would report a program as new and then file it as another.
    publishable = {k for k in (_publishable_key(s) for s in sightings) if k is not None}

    # Two different questions, so two different sets. `added` asks what the fold
    # will put in the FILE it is about to write, which only that file can
    # answer. `new` asks what the corpus a READER sees has not got, which is a
    # larger thing in both shapes this package ships in: in a checkout it also
    # holds the seed records, and in a wheel it also holds the shipped corpus
    # the fold may not write into.
    #
    # The seed half is the one with a price already paid. A seed program is an
    # expectation somebody typed by hand, keyed by a slug rather than by
    # content, so the fold does not recognise one and re-files it as an
    # observation; :func:`harvest.known_keys` exists because an early harvest
    # reported 138 of its 197 "observed" programs when they were byte-identical
    # to seeds, and expectation must not inflate the frequency table that ranks
    # work. The fold is not ours to change from here, so the import reports both
    # numbers and :func:`render` names the gap rather than leaving a reader to
    # decide which of the two is the broken one.
    corpus_ids: Set[str] = set()
    try:
        corpus_ids = {e.id for e in corpus.load(target)}
    except Exception:
        pass  # no corpus written yet: everything is new, which is true
    known = set(corpus_ids)
    if corpus_path is None:
        known |= harvest.known_keys()
    result.new = len(publishable - known)

    if dry_run:
        would = publishable - corpus_ids
        result.added = len(would)
        result.total = len(corpus_ids) + len(would)
        return result

    result.added, result.total = harvest.fold_into_corpus(sightings, target)
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
    if result.added > result.new:
        # Never silent. Added exceeding new means the fold wrote records for
        # programs the corpus a reader sees already holds — a seed record under
        # a hand-written slug, or, in a wheel, the shipped file the fold is not
        # allowed to write into. Both are worth a line: the first is how an
        # expectation quietly becomes an observation, and the second is how a
        # state corpus grows a copy of what already shipped.
        rows.append(("note", "{0} of those the corpus already holds under another key "
                             "or in the shipped file".format(result.added - result.new)))
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
        out.append("{0}  {1:>4} file(s)  {2:>6} program(s)  {3}".format(
            str(s.get("name") or "").ljust(name_width),
            s.get("files") or 0, s.get("programs") or 0, s.get("note") or ""))
        # The location on its own line rather than in the row: a git URL is
        # sixty characters and would push the counts off the side of a terminal,
        # and the counts are what a reader came for.
        out.append("  " + str(s.get("location") or ""))
    return "\n".join(out) + "\n"
