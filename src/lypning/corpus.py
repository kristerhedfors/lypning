"""The harvested corpus: load it, merge it, describe it.

The invariant this module exists to hold: **a corpus file is a review artifact,
so loading and rewriting one must produce the same bytes.** The corpus grows by
harvest, one appended sighting at a time, and every growth is read by a human in
a diff. A rewrite that re-ordered keys, re-escaped a non-ASCII program or
re-sorted the lines would turn a three-line harvest into an 839-line diff that
nobody reviews — and an unreviewed corpus is where a captured API key lives
forever. Hence one normal form, enforced in both directions: sorted by id, one
compact JSON object per line, ``ensure_ascii=False``, trailing newline, and
unknown keys carried through verbatim rather than dropped.

The second invariant is :func:`merge`. Sightings arrive from several sessions at
once and the same program is captured in several of them; merging must not care
what order the files are read in (commutative) and re-merging an already merged
set must not inflate the counts (idempotent). Both fall out of one rule:
*identical records are the same sighting and collapse; distinct records for one
id sum.*

Nothing here executes a program or looks for an engine — this is the one module
that is pure data, so it works identically whether or not anything is built.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from . import paths

# The key order of the normal form. Anything else a record carries is an extra
# and is written after these, in the order it was read.
FIELD_ORDER = (
    "id",
    "program",
    "argv_tail",
    "source",
    "first_seen",
    "count",
    "stdin_sample",
)

# `stdin` is the seed corpus' spelling of `stdin_sample`; `key` is the sightings'
# spelling of `id`. Both are consumed rather than carried, so a merged file has
# one name per concept.
_ALIASES = {"stdin": "stdin_sample", "key": "id"}

SHIM = "shim"
HOOK = "hook"
SEED = "seed"

# A first_seen that sorts after every real ISO-8601 timestamp, so "missing" loses
# the earliest-wins comparison to any record that actually has one.
_NEVER = "\uffff"


def program_id(program: str) -> str:
    """The corpus key: content-addressed, so the same program is one entry.

    Twelve hex digits of sha256 — 48 bits over a corpus of a few thousand
    programs, which is collision-free by six orders of magnitude and still short
    enough to read in a diff.
    """
    return "py-" + hashlib.sha256(program.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Entry:
    """One captured program.

    Frozen and hashable: :func:`merge` uses record equality itself as the "same
    sighting" test, so an Entry must be usable as a set member. ``argv_tail`` is
    therefore a tuple, and ``extra`` — the keys this version of the schema does
    not know about — is excluded from equality: it is carried for the writer's
    benefit, not part of the record's identity.
    """

    id: str
    program: str
    argv_tail: Tuple[str, ...] = ()
    source: str = SHIM
    first_seen: str = ""
    count: int = 1
    stdin_sample: str | None = None
    extra: Dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def fingerprint(self) -> str:
        """The id this program *should* have, recomputed from the program text.

        The key is taken over the *stripped* program while the program is stored
        raw: a shim invocation arrives as ``-c $'\\nimport os'`` often enough
        (77 of the 678 shipped records) that keying on the raw text would file
        the same program twice under two ids. Whitespace around a program is not
        part of the program. Every shipped harvested id satisfies this; the seed
        corpus does not, because its keys are slugs a human chose.
        """
        return program_id(self.program.strip())

    @property
    def lines(self) -> int:
        """Program length in lines — the corpus' one size metric."""
        return len(self.program.splitlines())

    @property
    def is_oneliner(self) -> bool:
        """``python3 -c`` fodder: the shape the Rust core exists to make fast."""
        return self.lines <= 1

    # --- serialisation -------------------------------------------------------

    @classmethod
    def from_obj(cls, obj: Dict[str, Any], *, default_source: str = SHIM) -> Entry | None:
        """Build from a decoded JSON object, or None if it is not a record.

        ``default_source`` is what a record without a ``source`` key gets: the
        seed corpus has no such key because every one of its records is a seed,
        and guessing per record instead would be guessing."""
        if not isinstance(obj, dict):
            return None
        program = obj.get("program")
        if not isinstance(program, str):
            return None
        known: Dict[str, Any] = {}
        extra: Dict[str, Any] = {}
        for k, v in obj.items():
            name = _ALIASES.get(k, k)
            if name in FIELD_ORDER:
                # An alias never overwrites the canonical spelling if both are
                # present; a sighting carries key *and* id and they agree.
                known.setdefault(name, v)
            else:
                extra[k] = v
        ident = known.get("id")
        argv = known.get("argv_tail")
        count = known.get("count", 1)
        stdin = known.get("stdin_sample")
        return cls(
            id=ident if isinstance(ident, str) and ident else program_id(program.strip()),
            program=program,
            argv_tail=tuple(str(a) for a in argv) if isinstance(argv, (list, tuple)) else (),
            source=known.get("source") if isinstance(known.get("source"), str) else default_source,
            first_seen=known.get("first_seen") if isinstance(known.get("first_seen"), str) else "",
            count=count if isinstance(count, int) and not isinstance(count, bool) else 1,
            stdin_sample=stdin if isinstance(stdin, str) else None,
            extra=extra,
        )

    def to_obj(self) -> Dict[str, Any]:
        """The record in normal-form key order, extras last and unchanged."""
        obj: Dict[str, Any] = {
            "id": self.id,
            "program": self.program,
            "argv_tail": list(self.argv_tail),
            "source": self.source,
            "first_seen": self.first_seen,
            "count": self.count,
            "stdin_sample": self.stdin_sample,
        }
        for k, v in self.extra.items():
            if k not in obj:
                obj[k] = v
        return obj


# --- loading -----------------------------------------------------------------


def _decode(line: str, default_source: str) -> Entry | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None  # a truncated append is a lost sighting, not a crash
    return Entry.from_obj(obj, default_source=default_source)


def load(path: Path | str, *, default_source: str = SHIM) -> List[Entry]:
    """Every record in one JSONL file, in file order. Never raises.

    A corpus file is appended to by a shell shim and by hooks that can be killed
    mid-write, so a half-written last line is expected rather than exceptional:
    it is skipped, and so is a file that has gone missing. The caller of a
    describe-the-corpus function has no useful response to an exception.
    """
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    out: List[Entry] = []
    for line in text.split("\n"):
        e = _decode(line, default_source)
        if e is not None:
            out.append(e)
    return out


def load_default() -> List[Entry]:
    """The shipped corpus: harvested plus seed, deduped by id.

    The seed corpus is hand-written test vectors keyed by slug; the harvested one
    is content-addressed. They do not collide, but they are merged rather than
    concatenated so that a seed program that later shows up in a harvest under
    its ``py-`` id still counts once.
    """
    harvested = load(paths.CORPUS_FILE)
    seed = load(paths.SEED_CORPUS_FILE, default_source=SEED)
    return merge(harvested, seed)


def load_sightings(dir: Path | str | None = None) -> List[Entry]:
    """Every session file under the sightings directory, merged.

    One file per session is what keeps concurrent harvests from conflicting
    (see :func:`paths.sightings_dir`), so reading them means reading all of them.
    A missing directory is the normal state of a fresh checkout: it yields no
    sightings, not an error.
    """
    root = Path(dir) if dir is not None else paths.sightings_dir()
    try:
        files = sorted(p for p in root.iterdir() if p.suffix == ".jsonl" and p.is_file())
    except OSError:
        return []
    return merge(*[load(p) for p in files])


# --- merging -----------------------------------------------------------------


def _rank(e: Entry) -> Tuple[str, str, str, str, str]:
    """A total, symmetric order over records of one id: earliest sighting wins.

    Used to pick which of two records supplies the fields that cannot be summed.
    Every component is order-independent, and the tuple is total down to the
    program text, so no pair of distinct records can tie and be resolved by
    which one was read first — that is what makes merge commutative.
    """
    return (e.first_seen or _NEVER, e.source, e.stdin_sample or "",
            " ".join(e.argv_tail), e.program)


def _combine(a: Entry, b: Entry) -> Entry:
    winner = a if _rank(a) <= _rank(b) else b
    first = min(a.first_seen or _NEVER, b.first_seen or _NEVER)
    return replace(
        winner,
        count=a.count + b.count,
        first_seen="" if first == _NEVER else first,
    )


def merge(*groups: Iterable[Entry]) -> List[Entry]:
    """Union by id: counts sum, the earliest ``first_seen`` survives, sorted by id.

    Commutative and idempotent, and the two together are the whole point. A
    record that is byte-identical to one already merged is the *same* sighting
    seen twice — two sessions exported the same line — and collapses; only
    records that actually differ contribute their counts. Without the collapse,
    re-merging a corpus with itself would double every count, and the harvest
    would drift a little further from the truth on each run.
    """
    by_id: Dict[str, Entry] = {}
    seen: set = set()
    for group in groups:
        for e in group:
            if e in seen:
                continue
            seen.add(e)
            cur = by_id.get(e.id)
            by_id[e.id] = e if cur is None else _combine(cur, e)
    return [by_id[k] for k in sorted(by_id)]


# --- writing -----------------------------------------------------------------


def write(entries: Sequence[Entry], path: Path | str) -> int:
    """Write the normal form. Returns the number of records written.

    ``ensure_ascii=False`` is not cosmetic: a third of the corpus contains
    non-ASCII program text, and escaping it would rewrite 215 lines of
    ``corpus.jsonl`` the first time anything called this. Compact separators and
    the fixed key order come from the same requirement — that
    ``write(load(p), p)`` is a no-op at the byte level.

    The write goes through a temporary file in the same directory and an
    ``os.replace``: this function is aimed at the file it just read, and a
    harvest interrupted halfway through the rewrite must leave the old corpus
    intact rather than half of a new one.
    """
    target = Path(path)
    ordered = sorted(entries, key=lambda e: e.id)
    buf = []
    for e in ordered:
        buf.append(json.dumps(e.to_obj(), separators=(",", ":"), ensure_ascii=False))
        buf.append("\n")
    data = "".join(buf)
    if target.parent and str(target.parent):
        paths.ensure_dir(target.parent)
    tmp = target.with_name(target.name + ".tmp")
    with open(str(tmp), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(data)
    os.replace(str(tmp), str(target))
    return len(ordered)


# --- describing --------------------------------------------------------------

_BUILTINS = frozenset(n for n in dir(builtins) if not n.startswith("_"))


def _bound_names(tree: ast.AST) -> set:
    """Names the program binds itself.

    A program that does ``list = open(p).read().split()`` is not "using the
    ``list`` builtin", and counting it as one would make the builtin histogram
    describe variable naming instead of subset coverage.
    """
    bound: set = set()

    def add_target(node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            bound.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for el in node.elts:
                add_target(el)
        elif isinstance(node, ast.Starred):
            add_target(node.value)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign,)):
            for t in node.targets:
                add_target(t)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.For)):
            add_target(node.target)
        elif isinstance(node, ast.AsyncFor):
            add_target(node.target)
        elif isinstance(node, ast.comprehension):
            add_target(node.target)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                add_target(node.optional_vars)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                bound.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
    return bound


def _imports_and_builtins(program: str) -> Tuple[List[str], List[str]] | None:
    """Module roots and builtin references in one program, or None if it will not
    parse under this CPython — a 3.12 program read by a 3.9 is unparsed, not
    empty, and the difference has to reach the report."""
    try:
        tree = ast.parse(program)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None
    mods: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.append(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has no module root to attribute.
            if node.module and not node.level:
                mods.append(node.module.split(".")[0])
    bound = _bound_names(tree)
    names = [
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name)
        and isinstance(n.ctx, ast.Load)
        and n.id in _BUILTINS
        and n.id not in bound
    ]
    return mods, names


@dataclass
class Stats:
    """What the corpus is made of. Counts are of entries, never of invocations:
    ``count`` says how often a program was *run*, which is a different question
    and would let one hot one-liner dominate every histogram."""

    total: int = 0
    by_source: Dict[str, int] = field(default_factory=dict)
    oneliners: int = 0
    multiline: int = 0
    median_len: int = 0
    p90_len: int = 0
    max_len: int = 0
    with_argv: int = 0
    with_stdin: int = 0
    top_imports: List[Tuple[str, int]] = field(default_factory=list)
    top_builtins: List[Tuple[str, int]] = field(default_factory=list)
    unparsed: int = 0


def _pct(values: Sequence[int], q: float) -> int:
    """Nearest-rank percentile. Line counts are integers and an interpolated
    median of 3.5 lines describes nothing."""
    if not values:
        return 0
    n = len(values)
    i = int(q * n)
    if i >= n:
        i = n - 1
    return values[i]


def _rank_counts(counter: Dict[str, int], top: int) -> List[Tuple[str, int]]:
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return items[:top] if top > 0 else items


def stats(entries: Sequence[Entry], *, top: int = 12) -> Stats:
    """Describe a corpus. Occurrence counts, not program counts: a program that
    imports ``os`` twice contributes two, which is what a coverage question about
    the parser wants to know."""
    s = Stats(total=len(entries))
    lengths: List[int] = []
    imports: Dict[str, int] = {}
    names: Dict[str, int] = {}
    for e in entries:
        s.by_source[e.source] = s.by_source.get(e.source, 0) + 1
        if e.is_oneliner:
            s.oneliners += 1
        else:
            s.multiline += 1
        lengths.append(e.lines)
        if e.argv_tail:
            s.with_argv += 1
        if e.stdin_sample:
            s.with_stdin += 1
        parsed = _imports_and_builtins(e.program)
        if parsed is None:
            s.unparsed += 1
            continue
        mods, used = parsed
        for m in mods:
            imports[m] = imports.get(m, 0) + 1
        for n in used:
            names[n] = names.get(n, 0) + 1
    lengths.sort()
    s.median_len = _pct(lengths, 0.5)
    s.p90_len = _pct(lengths, 0.9)
    s.max_len = lengths[-1] if lengths else 0
    s.by_source = dict(sorted(s.by_source.items(), key=lambda kv: (-kv[1], kv[0])))
    s.top_imports = _rank_counts(imports, top)
    s.top_builtins = _rank_counts(names, top)
    return s


def _pct_of(part: int, whole: int) -> str:
    return "   -  " if not whole else "{:5.1f}%".format(100.0 * part / whole)


def render_stats(s: Stats) -> str:
    """The one function here that formats. ASCII only — this goes to a terminal
    whose encoding we do not control, and a corpus report is not worth a
    UnicodeEncodeError."""
    def num(n: int, pct: bool = False) -> str:
        return "{:>6}{}".format(n, "  " + _pct_of(n, s.total) if pct else "")

    rows: List[Tuple[str, str]] = [("entries", num(s.total))]
    for src, n in s.by_source.items():
        rows.append(("  " + src, num(n, True)))
    rows.extend([
        ("one-liners", num(s.oneliners, True)),
        ("multi-line", num(s.multiline, True)),
        ("length (lines)", "median {}  p90 {}  max {}".format(s.median_len, s.p90_len, s.max_len)),
        ("with argv", num(s.with_argv)),
        ("with stdin", num(s.with_stdin)),
        ("unparsed here", num(s.unparsed)),
    ])
    width = max((len(k) for k, _ in rows), default=0)
    out = ["corpus", "=" * 6]
    for k, v in rows:
        out.append("{}  {}".format(k.ljust(width), v).rstrip())

    def column(title: str, items: Sequence[Tuple[str, int]]) -> List[str]:
        w = max([len(title)] + [len(k) for k, _ in items]) if items else len(title)
        col = [title, "-" * len(title)]
        for k, n in items:
            col.append("{}  {:>5}".format(k.ljust(w), n))
        return col

    left = column("top imports", s.top_imports)
    right = column("top builtins", s.top_builtins)
    lw = max((len(x) for x in left), default=0)
    out.append("")
    for i in range(max(len(left), len(right))):
        a = left[i] if i < len(left) else ""
        b = right[i] if i < len(right) else ""
        out.append((a.ljust(lw) + "    " + b).rstrip())
    return "\n".join(out) + "\n"
