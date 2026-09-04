"""The route ledger: the refusals a static walker provably could not see.

`lypning route` is a static analysis over the subset's own parser, and it is
exact about everything it can see. What it cannot see is VALUE. ``print(2**10)``
and ``print(2**100)`` share every token, every import and every construct; one
runs on the 1 MB core and the other exits 90 with ``bigint``. The same is true
of ``set-order``, ``nan-identity``, ``int-div-precision`` and ``float-sum``: the
route is CLEAN, the program runs, and the tier refuses partway through anyway.

Those runtime refusals are the only refusals nobody has a list of.
``conformance --plan`` ranks the shipped corpus, which is a fixed set of
programs somebody already harvested. This module records what REAL sessions hit
on this machine, so `lypning routes` is a second build order for the larger
spectrum variant, drawn from live traffic rather than from the corpus.

**The invariant this module exists to hold: the ledger is WRITE-ONLY with
respect to routing.** Nothing here is consulted by :func:`lypning.engines.route`
or by either dispatcher, and nothing here may ever become a cache, a Bloom
filter or a hint. The moment a machine-local file can move a route,
``lypning conformance`` is grading one laptop rather than one build, and the two
dispatchers — the Python one and the Rust one — gain a way to disagree that no
test could see. It is not a performance feature and was never measured as one: a
wasted route costs 1.21 ms and every store read costs more than that.

**What a write may cost: nothing.** One ``json.dumps`` and one ``os.write`` to a
descriptor opened ``O_APPEND``, payload under one page so the append is atomic
and parallel sessions need no lock, every exception swallowed — the same posture
as :func:`lypning.capture.append_record`, and for the same reason. A full disk,
a read-only ``$LYPNING_HOME``, a store somebody edited by hand: all of them are
silent no-ops. ``LYPNING_ROUTES=0`` turns it off entirely.

**Invalidation is the hard part.** The hillclimb adds a capability to a variant
every iteration, and a record learned before that is a claim about a binary that
no longer exists — ``bigint`` refused by yesterday's build is not evidence about
today's. So line 1 of each file is a header naming the engine the records were
learned against (its ``cap-*`` set and its binary's size and mtime), and
:func:`load` discards the WHOLE file when that header does not describe the
engine as it is now. Discarding is cheap precisely because nothing on the hot
path depends on the store: the worst case is that a few programs are re-learned
the next time somebody runs them.

The invalidation is deliberately CONSERVATIVE in one direction. The write path
never reads the store's contents — it cannot, or it would be a store read in the
dispatcher — so a record appended after a rebuild but before the next
:func:`load` sits under the old header and is discarded with the rest. That
over-discards and never under-discards, which is the right way round: an
over-discard costs a re-learn, an under-discard puts a dead binary's refusal in
a build order.

**It under-counts, and that is a known hole.** Only the Python dispatcher
(:func:`lypning.engines.dispatch`) writes here. ``lypning run`` — the Rust
dispatcher — does not, because the core is frozen at 8 device blocks and a
second writer is a second thing to keep in lockstep. Every count this module
renders is therefore a floor, and :func:`render` says so rather than presenting
it as a total.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import engines, paths

#: Header schema version. Bumped when the record shape changes — an old header
#: then fails to match a new one and the file is discarded, which is exactly the
#: migration this store wants (there is nothing in it worth migrating).
VERSION = 1

#: ``LYPNING_ROUTES=0`` disables the writer, the way ``LYPNING_CAPTURE=0``
#: disables both capture feeds. Readers still work — turning the ledger off must
#: not make `lypning routes` claim there is nothing to see.
ENV = "LYPNING_ROUTES"

#: The exact cap, enforced where reading is allowed: :func:`load` stops here and
#: :func:`compact` truncates to it.
MAX_RECORDS = 4096

#: The cap the WRITER enforces, in bytes, because counting records is a read and
#: the write path does not read. It is the record cap at a generous 128 bytes
#: apiece; past it the append is a no-op rather than unbounded growth.
MAX_BYTES = MAX_RECORDS * 128

#: One page. An ``O_APPEND`` write of at most this lands whole, so two sessions
#: appending at once interleave as records and never shred one.
MAX_LINE = 4096

#: The refusal detail is prose and can be long; the ledger keeps the head of it
#: only, as a reminder of which refusal this was, never as data to parse.
DETAIL_MAX = 96

#: A refusal kind is one hyphenated token (`bigint`, `set-order`). Bounded on
#: the way in AND on the way out, because the file between the two is on disk
#: and hand-editable — see `_clean`.
KIND_MAX = 64

#: An invocation count is a count. A hand-edited store holding a 400-digit
#: integer must not get a 400-digit column.
MAX_N = 1 << 40


def enabled() -> bool:
    """``LYPNING_ROUTES=0`` disables the writer, and so does ``LYPNING_CAPTURE=0``.

    The second one is the point. ``LYPNING_CAPTURE=0`` is documented as turning
    the capture harness off, and a user who set it believes recording stopped.
    This is a second feed writing program digests under ``$LYPNING_HOME``, and
    adding it without honouring the opt-out already in the README would be a
    consent regression under invariant 7 — the user would keep a switch that no
    longer covers what it says it covers. A narrower switch may be added; the
    broader one may never quietly narrow.
    """
    if os.environ.get("LYPNING_CAPTURE", "1").strip() == "0":
        return False
    return os.environ.get(ENV, "1").strip() != "0"


def store_path(engine: str, root: Optional[Path] = None) -> Path:
    """``$LYPNING_HOME/routes/<engine>.jsonl``.

    One file per engine and not one shared file, for the reason the sightings
    directory has: the header describes ONE engine, and a shared file could not
    be invalidated without discarding what a sibling had learned.
    """
    base = Path(root) if root is not None else paths.routes_dir()
    return base / (engine + ".jsonl")


def digest(program: str) -> str:
    """The record id: 12 hex of blake2b over the program text.

    EXACT identity, and deliberately not a feature key or a skeleton. A feature
    key was measured and is unsound: ``print(2**10)`` and ``print(2**100)`` share
    every feature anything cheap could extract, and they have opposite outcomes —
    which is the whole reason this ledger exists. A digest can only ever say
    "this same program did this again", and that is all it is asked.
    """
    return hashlib.blake2b(program.encode(), digest_size=6).hexdigest()


def binary_stamp(path: Optional[Path]) -> str:
    """``<size>:<mtime_ns>`` for a binary, ``""`` when there is not one.

    Not a content hash: this runs on the write path, and hashing a megabyte to
    write 120 bytes would be the one expensive thing in a module whose entire
    argument is that it is free. Size and mtime move together on every rebuild
    cargo does, which is the event the header has to notice.
    """
    try:
        if path is None:
            return ""
        st = Path(path).stat()
        return "%d:%d" % (st.st_size, st.st_mtime_ns)
    except OSError:
        return ""


def header(engine: str, binary: Optional[Path] = None) -> Dict[str, Any]:
    """Line 1: which engine, exactly, these records were learned against.

    ``binary`` is passed by the writer — which already knows the path the run
    used — and discovered by the reader, which does not. They agree because
    :func:`lypning.engines.run` resolves the same binary :func:`engines.find`
    does.
    """
    if binary is None:
        try:
            binary = engines.find(engine)
        except Exception:
            binary = None
    return {"v": VERSION, "engine": engine,
            "caps": sorted(engines.VARIANT_CAPS.get(engine, ())),
            "bin": binary_stamp(binary)}


def record(program: str, kind: str, detail: str,
           when: Optional[datetime] = None) -> Dict[str, Any]:
    """One ledger record. ``n`` is 1 here; only :func:`compact` ever folds."""
    day = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return {"id": digest(program), "kind": str(kind or "")[:KIND_MAX],
            "detail": str(detail or "")[:DETAIL_MAX], "n": 1, "t": day}


# --- the write path ----------------------------------------------------------


def note(engine: str, program: str, kind: str, detail: str, *,
         binary: Optional[Path] = None, when: Optional[datetime] = None,
         path: Optional[Path] = None) -> bool:
    """Append one record. Never raises, never blocks, never reads the store.

    True only when a record actually landed; every other outcome — disabled,
    unwritable, capped, malformed — is False and silent, because a caller is in
    the middle of dispatching somebody's program and a ledger failure is not
    theirs to hear about.

    The one thing this learns about the store is its SIZE, from the ``fstat`` on
    the descriptor it is about to write to: it decides header-or-no-header and
    enforces the cap. That is metadata about a file this function is already
    holding open for writing, and it can move no route — see the module
    docstring on why reading the store's CONTENT here would be a different thing
    entirely.
    """
    try:
        if not enabled():
            return False
        line = json.dumps(record(program, kind, detail, when),
                          separators=(",", ":"), ensure_ascii=False) + "\n"
        target = Path(path) if path is not None else store_path(engine)
        paths.ensure_dir(target.parent)
        fd = os.open(str(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            size = os.fstat(fd).st_size
            if size >= MAX_BYTES:
                return False
            if size == 0:
                # Header and first record in ONE write, or a second session
                # could append a record between them and land it above the
                # header it belongs to.
                line = json.dumps(header(engine, binary),
                                  separators=(",", ":"), ensure_ascii=False) + "\n" + line
            data = line.encode("utf-8")
            if len(data) > MAX_LINE:
                return False
            os.write(fd, data)
        finally:
            os.close(fd)
        return True
    except Exception:
        return False


# --- the read path -----------------------------------------------------------


@dataclass
class Store:
    """One engine's ledger, as it loaded. ``stale`` is a hole, not a zero."""

    engine: str
    path: Path
    records: List[Dict[str, Any]] = field(default_factory=list)
    head: Optional[Dict[str, Any]] = None
    present: bool = False
    stale: bool = False
    #: The file is there and could not be read. NOT the same fact as `not
    #: present`, and the whole rendering contract turns on the difference: an
    #: absent store means nothing was recorded, an unreadable one means we do
    #: not know what was recorded. Reporting the second as the first hides an
    #: IO or permission fault for as long as it lasts.
    unreadable: bool = False
    #: More records on disk than `MAX_RECORDS` let us load. `compact` must
    #: refuse to rewrite a store in this state — it would persist the
    #: truncation as the whole file.
    truncated: bool = False

    @property
    def invocations(self) -> int:
        return sum(int(r.get("n") or 0) for r in self.records)

    @property
    def programs(self) -> int:
        return len({str(r.get("id")) for r in self.records})


def _is_record(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    rid = obj.get("id")
    if not isinstance(rid, str) or len(rid) != 12:
        return False
    try:
        int(rid, 16)
    except ValueError:
        return False
    return isinstance(obj.get("kind"), str)


#: Control characters, including the ESC that starts a terminal escape
#: sequence. A refusal kind and detail are engine prose and never contain one.
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _clean(text: str, cap: int) -> str:
    """Bound a field from the store and strip anything a terminal would obey.

    `record` caps these on the way in, but this is the way OUT and the two are
    not the same trust boundary. The file is on disk, editable by hand, and
    `Result.refusal` reads its kind from the first stderr line merely CONTAINING
    the marker — looser than the `refused` gate — so a program can put bytes of
    its choosing in here. Every later `lypning routes` prints them, long after
    and out of context from the run that produced them, which is the one thing
    a persisted record must not let a program do.
    """
    return _CTRL.sub("", text)[:cap]


def _normal(r: Dict[str, Any]) -> Dict[str, Any]:
    n = r.get("n")
    return {"id": _clean(str(r["id"]), 12),
            "kind": _clean(str(r.get("kind") or ""), KIND_MAX),
            "detail": _clean(str(r.get("detail") or ""), DETAIL_MAX),
            "n": n if isinstance(n, int) and 0 < n <= MAX_N else 1,
            "t": _clean(str(r.get("t") or ""), 10)}


def load(engine: str, path: Optional[Path] = None) -> Store:
    """One engine's records, or nothing at all if the header has gone stale.

    A missing file is ``present=False``; a file that exists and cannot be read
    is ``present=True, unreadable=True``, which is a DIFFERENT fact and is
    rendered as one. Either way the renderer says what it does not know rather
    than printing a zero: an empty ledger means nothing is KNOWN, never that
    every route was right.
    """
    target = Path(path) if path is not None else store_path(engine)
    store = Store(engine=engine, path=target)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return store
    except OSError:
        # It is there and we cannot read it. Say that, and never "no routes
        # learned yet" — a store of 3,000 records behind a permission fault is
        # not a store that was never written.
        store.present = True
        store.unreadable = True
        return store
    store.present = True
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return store
    try:
        head = json.loads(lines[0])
    except ValueError:
        head = None
    if not isinstance(head, dict) or "engine" not in head:
        # No header at all: a hand-edited or truncated file. Discard it whole
        # rather than read records nothing identifies.
        store.stale = True
        return store
    store.head = head
    want = header(engine)
    if [head.get(k) for k in ("v", "engine", "caps", "bin")] != \
            [want[k] for k in ("v", "engine", "caps", "bin")]:
        store.stale = True
        return store
    for ln in lines[1:]:
        if len(store.records) >= MAX_RECORDS:
            store.truncated = True
            break
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        if _is_record(obj):
            store.records.append(_normal(obj))
    return store


def load_all(engines_: Optional[Sequence[str]] = None) -> List[Store]:
    """Every spectrum variant's store, in spectrum order."""
    names = list(engines_) if engines_ is not None else list(engines.SPECTRUM)
    return [load(e) for e in names]


# --- aggregation -------------------------------------------------------------


@dataclass
class Kind:
    """One refusal kind, folded across every store."""

    kind: str
    programs: int
    invocations: int
    #: False when the kind is in :data:`lypning.engines.ONLY_CPYTHON_REFUSALS`:
    #: a reimplementation gets it wrong by construction, so it is a thing to
    #: refuse exactly, never a thing to plan against.
    implementable: bool
    detail: str = ""


def kinds(stores: Sequence[Store]) -> List[Kind]:
    """Refusal kinds over every store, most invocations first."""
    ids: Dict[str, set] = {}
    hits: Dict[str, int] = {}
    detail: Dict[str, str] = {}
    for s in stores:
        for r in s.records:
            k = r["kind"]
            ids.setdefault(k, set()).add(r["id"])
            hits[k] = hits.get(k, 0) + r["n"]
            if k not in detail and r["detail"]:
                detail[k] = r["detail"]
    out = [Kind(kind=k, programs=len(ids[k]), invocations=hits[k],
                implementable=k not in engines.ONLY_CPYTHON_REFUSALS,
                detail=detail.get(k, ""))
           for k in ids]
    out.sort(key=lambda x: (-x.invocations, -x.programs, x.kind))
    return out


# --- maintenance (never on a program's path) ---------------------------------


def _rewrite(store: Store, records: Sequence[Dict[str, Any]]) -> bool:
    """Replace one store's file with a fresh header and ``records``.

    Temp file plus ``os.replace``, so a reader either sees the whole old file or
    the whole new one. Only ever called from `lypning routes` — never from a run.
    """
    try:
        paths.ensure_dir(store.path.parent)
        tmp = store.path.with_name(store.path.name + ".tmp-%d" % os.getpid())
        body = [json.dumps(header(store.engine), separators=(",", ":"), ensure_ascii=False)]
        body += [json.dumps(r, separators=(",", ":"), ensure_ascii=False) for r in records]
        tmp.write_text("\n".join(body) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(store.path))
        return True
    except OSError:
        return False


def compact(stores: Optional[Sequence[Store]] = None) -> List[Tuple[str, int, int]]:
    """Fold duplicate ids into ``n``. ``(engine, before, after)`` per store.

    The ONLY place folding happens. A run appends and does nothing else, because
    folding means reading the file first, and a program's path does not read
    this file.

    Folding is ALL it does. Three states are skipped rather than rewritten,
    because `_rewrite` replaces the file with what `load` returned and in each
    of them that would destroy something:

    * **unreadable** — we do not know what is in there, so we may not replace it.
    * **truncated** — `load` stopped at `MAX_RECORDS`; rewriting would persist
      the truncation and silently drop every record past the cap.
    * **stale** — the file honestly says "discarded, the engine changed".
      Rewriting it with a current header converts that into "empty" and loses
      the reason. `--clear` is how a user throws a stale store away, and it
      says so.

    A skipped store reports ``(engine, before, before)`` — no change — so a
    caller can never read a loss as a fold.
    """
    stores = load_all() if stores is None else stores
    out: List[Tuple[str, int, int]] = []
    for s in stores:
        if not s.present:
            continue
        if s.unreadable or s.truncated or s.stale:
            out.append((s.engine, len(s.records), len(s.records)))
            continue
        before = len(s.records)
        folded: Dict[str, Dict[str, Any]] = {}
        for r in s.records:
            prev = folded.get(r["id"])
            if prev is None:
                folded[r["id"]] = dict(r)
            else:
                prev["n"] += r["n"]
                prev["t"] = max(prev["t"], r["t"])
        rows = sorted(folded.values(), key=lambda r: (-r["n"], r["id"]))[:MAX_RECORDS]
        if _rewrite(s, rows):
            out.append((s.engine, before, len(rows)))
    return out


def clear(stores: Optional[Sequence[Store]] = None) -> List[str]:
    """Delete every store file. Returns the engines whose file was removed."""
    stores = load_all() if stores is None else stores
    gone: List[str] = []
    for s in stores:
        try:
            s.path.unlink()
            gone.append(s.engine)
        except OSError:
            continue
    return gone


def forget(record_id: str, stores: Optional[Sequence[Store]] = None) -> List[Tuple[str, int]]:
    """Drop every record with this id. ``(engine, dropped)`` per store touched."""
    stores = load_all() if stores is None else stores
    out: List[Tuple[str, int]] = []
    for s in stores:
        if not s.present:
            continue
        keep = [r for r in s.records if r["id"] != record_id]
        dropped = len(s.records) - len(keep)
        if dropped and _rewrite(s, keep):
            out.append((s.engine, dropped))
    return out


# --- reporting (invariant 8: these return strings, they do not print) --------

#: Said wherever a total is shown, and it names the RUST BINARY's own
#: dispatcher — `<binary> run`, `main.rs::dispatch` — which is the one that does
#: not write. The Python `lypning run` does write; it is the only thing that
#: does. Getting these two the wrong way round tells a user who was just
#: recorded that nothing records them, so the sentence is spelled out rather
#: than shortened. The undercount is real either way: the crate binary is what
#: an installed chain actually execs, so every number here is a floor, and a
#: floor presented as a total is a claim of completeness this feature cannot
#: make.
UNDERCOUNT = ("Only the Python dispatcher (`lypning run`) feeds this ledger. The Rust "
              "binary's own\ndispatcher — what an installed chain execs — writes nothing "
              "here, so every count\nUNDER-counts. That is a known hole, not a claim of "
              "completeness.")

_UNREADABLE = ("routes — UNREADABLE (%s).\n"
               "\n"
               "  The store is there and could not be read. That is NOT the same fact as\n"
               "  an empty ledger: what it holds is unknown, not nothing. Check the\n"
               "  permissions on the path below before reading any count here as a total.")

_HOLE = ("routes — no routes learned yet.\n"
         "\n"
         "  Nothing has been recorded, which means nothing is KNOWN here — never that\n"
         "  every route was right. A record is written only when a CLEAN static route\n"
         "  was followed by a RUNTIME refusal from the tier it named, and either that\n"
         "  has not happened on this machine yet or the store was discarded as stale.\n")


def to_obj(stores: Sequence[Store]) -> Dict[str, Any]:
    """The whole view as plain data, for ``--json``."""
    rows = kinds(stores)
    return {
        "dir": str(paths.routes_dir()),
        "loaded": sum(len(s.records) for s in stores),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "undercounts": True,
        "stores": [{"engine": s.engine, "path": str(s.path), "present": s.present,
                    "stale": s.stale, "unreadable": s.unreadable,
                    "truncated": s.truncated, "records": len(s.records),
                    "programs": s.programs, "invocations": s.invocations,
                    "header": s.head} for s in stores],
        "kinds": [{"kind": k.kind, "programs": k.programs, "invocations": k.invocations,
                   "implementable": k.implementable, "detail": k.detail} for k in rows],
    }


def render(stores: Sequence[Store], plan: bool = False) -> str:
    """The human view: which capability gaps real runs actually hit."""
    rows = kinds(stores)
    loaded = sum(len(s.records) for s in stores)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not loaded:
        # "Nothing was recorded" is a claim, and it is false when a store is
        # sitting right there unread. Say which hole this is.
        bad = [x.engine for x in stores if x.unreadable]
        out = [(_UNREADABLE % ", ".join(bad)) if bad else _HOLE.rstrip("\n"), ""]
        for s in stores:
            state = ("UNREADABLE — the file is there and could not be read"
                     if s.unreadable else
                     "discarded as stale — the engine changed since these were learned"
                     if s.stale else "no file yet" if not s.present else "empty")
            out.append("  %-13s %s" % (s.engine + ":", state))
        out += ["", "loaded 0 records on %s." % today, UNDERCOUNT]
        return "\n".join(out) + "\n"

    out = ["routes — value-dependent refusals, learned from runs on this machine",
           "",
           "A record is written only where a CLEAN static route was followed by a RUNTIME",
           "refusal from the tier it named. That is the gap `lypning route` provably cannot",
           "see: print(2**10) and print(2**100) are the same program to a static walker.",
           ""]
    out.append("  %-13s %8s %9s %13s  %s" % ("engine", "records", "programs", "invocations", "store"))
    for s in stores:
        if s.unreadable:
            out.append("  %-13s UNREADABLE — cannot say what it holds  (%s)"
                       % (s.engine + ":", s.path))
            continue
        if not s.present or s.stale:
            why = ("discarded as stale" if s.stale else "no routes learned yet")
            out.append("  %-13s %s  (%s)" % (s.engine + ":", why, s.path))
            continue
        out.append("  %-13s %8d %9d %13d  %s%s"
                   % (s.engine + ":", len(s.records), s.programs, s.invocations, s.path,
                      "  [TRUNCATED at %d — more on disk]" % MAX_RECORDS if s.truncated else ""))
    out.append("")
    shown = [k for k in rows if k.implementable] if plan else rows
    if plan:
        out.append("build order — the kinds a larger variant could actually implement,")
        out.append("ranked by invocations, which is what the refusals cost.")
    else:
        out.append("refusal kinds, most invocations first:")
    out.append("")
    out.append("  %-22s %9s %12s  %s" % ("kind", "programs", "invocations", "note"))
    for k in shown:
        note_ = "" if k.implementable else "NOT IMPLEMENTABLE — no Rust variant may answer this"
        out.append(("  %-22s %9d %12d  %s"
                    % (k.kind, k.programs, k.invocations, note_)).rstrip())
    if not shown:
        out.append("  (every kind recorded is one a reimplementation gets wrong — nothing to plan)")
    blocked = [k for k in rows if not k.implementable]
    if blocked and not plan:
        out.append("")
        out.append("NOT IMPLEMENTABLE means the kind is in engines.ONLY_CPYTHON_REFUSALS: it exists")
        out.append("because a reimplementation gets it wrong, so it is a thing to refuse exactly")
        out.append("and never a thing to plan against. `lypning oracle` holds the evidence.")
    out += ["", "loaded %d record(s) from %s on %s."
            % (loaded, paths.routes_dir(), today), UNDERCOUNT]
    return "\n".join(out) + "\n"


def status_line(stores: Optional[Sequence[Store]] = None) -> str:
    """The one line `lypning status` shows. A hole when empty, never a zero."""
    stores = load_all() if stores is None else stores
    loaded = sum(len(s.records) for s in stores)
    stale = [s.engine for s in stores if s.stale]
    bad = [s.engine for s in stores if s.unreadable]
    cut = [s.engine for s in stores if s.truncated]
    if bad and not loaded:
        # An unreadable store is not an empty one, and `status` is the line most
        # likely to be the only thing anyone reads.
        body = "UNREADABLE: %s" % ", ".join(bad)
    elif not loaded:
        body = "no routes learned yet"
    else:
        body = "%d record(s), %d kind(s)" % (loaded, len(kinds(stores)))
        if bad:
            body += "  [UNREADABLE: %s]" % ", ".join(bad)
    if stale:
        body += "  [%s discarded as stale]" % ", ".join(stale)
    if cut:
        body += "  [%s truncated at %d]" % (", ".join(cut), MAX_RECORDS)
    return "routes       %s  (%s)" % (body, paths.routes_dir())
