"""Verify a stdlib unit against CPython, then label it with the cheapest engine.

The corpus this builds is a "standard library" of lypning-compatible code: one
self-contained, function-only program per CPython surface the Rust engine
refuses. Nothing imports these files — the engine has no local imports and no
``exec``, so a unit can only ever be INLINED into the program that needs it.
They are reference material a model reproduces, and the label on each one says
which engine will actually run what it reproduces.

Three rules decide everything in this module.

**CPython is the oracle, live, on every run.** A unit CPython rejects is never a
row, and the bytes CPython printed are the only definition of the right answer.
Reasoning about what CPython does is how silent divergence ships.

**A comparison is between bytes.** :attr:`lypning.engines.Result.stdout` is
decoded with universal-newline translation, and applying that to both sides of a
comparison is how line endings became an axis on which no engine could be caught
disagreeing (issue #50). Everything here keys on ``stdout_raw``; the report
decodes, and only for a reader.

**A refusal is coverage; a wrong answer is a bug.** Exit 90 with the contract
line means "outside my subset", and labelling moves to the next engine —
invariant 1's UNSUPPORTED. An engine that exits 0 with different bytes is a
MISMATCH: the unit is rejected and the reason recorded, and no relabelling makes
it go away. That is the same invariant read from the corpus side.

Library code does not print (invariant 8). Every function here returns data;
``pipeline.cli`` renders it. No ``date.today()`` either — ``first_seen`` is
passed in, because a row that changes on the second run fails the
``git diff --exit-code`` at the end of the workflow.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import (Any, AbstractSet, Dict, Iterable, List, Mapping, Optional,
                    Sequence, Tuple)

from dataclasses import dataclass, field

from lypning import engines as lyp
from . import engines as eng

#: The line that splits a unit's inlinable half from its cases. Exactly this
#: text, exactly once — a unit with two of them has two answers to "what does a
#: model reproduce", and a unit with none has no cases to check.
UNIT_SEPARATOR = "# --- cases ---"

#: The row schema tag. Bumped when the key order or a key's meaning changes, so
#: a merged corpus can never silently mix two shapes.
SCHEMA = "lypning-stdlib-unit/1"

#: The drops ledger's own tag. A drop is a record, not an absence: the whole
#: point of the ledger is that a candidate which did not make it leaves a row
#: saying why, in the vocabulary of :data:`DROP_REASONS`.
DROPS_SCHEMA = "lypning-stdlib-drop/1"

ID_PREFIX = "lys-"

#: The row keys, in the one order they are ever written. ``sort_keys=False``
#: everywhere downstream, so this tuple IS the serialisation.
ROW_KEYS: Tuple[str, ...] = (
    "schema", "id", "name", "fills", "reference", "requires", "requires_static",
    "route_agrees", "caps", "naive_kind", "naive_detail", "doc", "helpers",
    "cases", "source_sha256", "producer", "first_seen",
)

DROP_KEYS: Tuple[str, ...] = (
    "schema", "name", "path", "reason", "detail", "requires", "requires_static",
    "naive_kind", "naive_detail", "fills", "reference", "source_sha256",
    "first_seen",
)

#: Why a candidate is not a row. A closed vocabulary because the drops ledger is
#: read by the repair stage, which decides what to regenerate from this word:
#:
#: ``malformed``      the file is not a unit — no header, no separator, no cases.
#: ``cpython-rejected`` CPython did not exit 0. The unit is broken, full stop.
#: ``mismatch``       an engine exited 0 with bytes CPython did not print, or
#:                    crashed on a program CPython ran. Invariant 1: fatal.
#: ``cpython-only``   no engine ran it. A recorded gap, not a failure — and the
#:                    one drop reason that is not a defect in the unit.
#: ``closed-kind``    the unit fills something no reimplementation may answer.
#: ``engine-missing`` an engine cheaper than the one that answered was not
#:                    available, so the label would understate what the unit
#:                    needs. Not a verdict about the unit at all.
DROP_REASONS: Tuple[str, ...] = (
    "malformed", "cpython-rejected", "mismatch", "cpython-only", "closed-kind",
    "engine-missing",
)

#: The cheapest engine, and the order everything is tried in. Imported, never
#: spelled: invariant 9 says a variant's name lives in one place, and a literal
#: here would be a second one that could drift.
ENGINE_ORDER: Tuple[str, ...] = tuple(lyp.ENGINE_ORDER)
SPECTRUM: Tuple[str, ...] = tuple(lyp.SPECTRUM)
CPYTHON: str = lyp.CPYTHON


class UnitError(ValueError):
    """A malformed unit file. One line, no traceback — the CLI renders it."""


# --- parsing -----------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    """One unit file, split into the parts the row and the checks need."""

    name: str
    path: str
    doc: str
    fills: Tuple[str, ...]
    reference: str
    helpers: str
    cases: str
    source: str


def _header_value(text: str, tag: str) -> Optional[str]:
    """The value of a ``# <tag>: ...`` header line, or None if absent.

    Only lines above the separator are considered, and only ones that begin the
    line: a ``# fills:`` inside a docstring example is prose, not a header.
    """
    for line in text.splitlines():
        if line.startswith(UNIT_SEPARATOR):
            break
        stripped = line.strip()
        if stripped.startswith("# " + tag + ":"):
            return stripped[len("# " + tag + ":"):].strip()
    return None


def _docstring_of(text: str, path: str) -> str:
    """The module docstring, read by compiling the file rather than by regex.

    ``ast`` is the stdlib's own parser, so a docstring with a separator line or a
    ``# fills:`` inside it parses as what it is. A file that does not parse is
    malformed here rather than at the CPython run, which is a better error.
    """
    import ast

    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as exc:
        raise UnitError("%s: does not parse: %s" % (path, exc)) from None
    doc = ast.get_docstring(tree, clean=False)
    if not doc:
        raise UnitError("%s: no module docstring" % path)
    return doc


def parse_unit(path: str, text: str) -> Unit:
    """One unit file into a :class:`Unit`, or :class:`UnitError` saying why not.

    Structural only. Whether the unit is CORRECT is a question for CPython, and
    it is asked in :func:`label_unit`; a file that passes here has the right
    shape and nothing more.
    """
    name = Path(path).stem
    if not text.strip():
        raise UnitError("%s: empty file" % path)
    doc = _docstring_of(text, path)

    count = sum(1 for line in text.splitlines() if line.rstrip() == UNIT_SEPARATOR)
    if count != 1:
        raise UnitError("%s: expected exactly one %r line, found %d"
                        % (path, UNIT_SEPARATOR, count))
    head: List[str] = []
    tail: List[str] = []
    seen = False
    for line in text.splitlines(True):
        if not seen and line.rstrip() == UNIT_SEPARATOR:
            seen = True
            continue
        (tail if seen else head).append(line)
    helpers = "".join(head)
    cases = "".join(tail)
    if not cases.strip():
        raise UnitError("%s: nothing below %r — a unit with no cases checks "
                        "nothing" % (path, UNIT_SEPARATOR))

    raw_fills = _header_value(text, "fills")
    if raw_fills is None:
        raise UnitError("%s: no '# fills:' header" % path)
    fills = tuple(part.strip() for part in raw_fills.split(",") if part.strip())
    if not fills:
        raise UnitError("%s: '# fills:' is empty" % path)

    raw_ref = _header_value(text, "reference")
    if raw_ref is None:
        raise UnitError("%s: no '# reference:' header" % path)
    reference = "" if raw_ref == "-" else raw_ref
    if reference and not all(part.isidentifier() for part in reference.split(".")):
        raise UnitError("%s: '# reference: %s' is not a module name" % (path, raw_ref))

    return Unit(name=name, path=path, doc=doc, fills=fills, reference=reference,
                helpers=helpers, cases=cases, source=text)


def unit_paths(root: "str | os.PathLike") -> List[Path]:
    """Every candidate unit file under ``root``, sorted. Deterministic by name.

    ``sorted`` rather than ``iterdir`` order: the directory order is the
    filesystem's, which is exactly the class of thing invariant 3's sibling rule
    about ``glob-order`` says never to let into an output.
    """
    d = Path(root)
    if not d.is_dir():
        return []
    return sorted((p for p in d.glob("*.py") if p.is_file()), key=lambda p: p.stem)


def load_units(root: "str | os.PathLike") -> List[Unit]:
    """Every unit under ``root``, parsed and sorted by name.

    Raises :class:`UnitError` on the first malformed file. The verify command
    does not use this — it wants a drops row per bad file rather than one
    exception for the batch — but a test or a hand check does, and it is the
    documented entry point.
    """
    return [parse_unit(str(p), p.read_text(encoding="utf-8")) for p in unit_paths(root)]


# --- running -----------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """One execution of one unit on one engine.

    ``stdout`` is BYTES, deliberately: it is the side of the comparison that
    decides a MISMATCH, and the decoded string on :class:`lypning.engines.Result`
    has already had its line endings rewritten. ``stderr`` is text because
    nothing compares it byte for byte — only its shape (empty or not) and, for a
    refusal, the one line it must carry.
    """

    engine: str
    exit_code: int
    stdout: bytes
    stderr: str
    kind: str = ""
    detail: str = ""


def engine_of_binary(binary: "str | os.PathLike") -> str:
    """The engine name a binary path spells, or ``cpython``.

    Through :func:`lypning.engines.parse_binary_name`, which is the one parser
    for ``<engine>[-<target>]`` — so ``lypning-l-i686`` is the wider variant and
    not a stranger. Anything it does not recognise is the reference interpreter,
    which is what ``/usr/bin/python3.11`` is.
    """
    name, _target = lyp.parse_binary_name(str(binary))
    return name or CPYTHON


def run_source(source: str, binary: "str | os.PathLike", timeout_s: float = 30.0,
               engine: str = "") -> Run:
    """Run a whole unit source on one binary and keep what it wrote.

    Through :func:`lypning.engines.run` rather than a second ``subprocess``
    call: that function already holds the two things this needs and a fresh
    call site would get wrong — ``capture_output`` in BYTES on both sides, and
    :func:`lypning.engines.child_env`, which makes every path-like variable
    absolute so the reference and the engine see one environment (issue #57).

    ``pipeline.refusals.probe`` does not fit here and is not called: it runs with
    ``text=True``, which decodes before anything can compare, and it answers
    only "was this refused" — it never captures the stdout a MISMATCH is found
    in. :func:`pipeline.refusals.grade_against_engine` does compare, but through
    ``sandbox.run_python``, whose comparison is over decoded strings for the same
    reason. Both are right for their own populations; neither can see a CRLF.

    Stdin is closed, not inherited. ``route`` reports most units as
    ``reads_stdin`` (the heuristic is generous by design), and a unit that did
    read stdin would block forever against an inherited terminal.
    """
    name = engine or engine_of_binary(binary)
    res = lyp.run(name, source, binary=Path(binary), stdin="", timeout=timeout_s)
    kind, detail = ("", "")
    if res.returncode == lyp.UNSUPPORTED_EXIT:
        parsed = eng.parse_refusal(res.stderr)
        if parsed is not None:
            _engine, kind, detail = parsed
    return Run(engine=name, exit_code=res.returncode, stdout=res.stdout_bytes,
               stderr=res.stderr, kind=kind, detail=detail)


def is_refusal(run: Run) -> bool:
    """Exit 90, exactly one well-formed line on stderr, and nothing on stdout.

    All three, because each one alone is satisfied by something that is not a
    refusal. ``python3 -c 'import sys; sys.exit(90)'`` is a program choosing its
    own exit code and 90 is as available to it as any other number; a program
    that printed the contract line itself would be indistinguishable on stderr
    alone; and stdout must be empty because a refusal is ATOMIC — the engine
    decides before it runs anything, so a program that refuses has printed
    nothing. Reading any one of the three as "outside my subset" would send a
    half-completed program to the next engine to be run again (invariant 2).

    The line itself is parsed by :func:`pipeline.engines.parse_refusal`, the
    single parser for the contract line; a second regex here is a second thing
    to keep in step with the engine.
    """
    if run.exit_code != lyp.UNSUPPORTED_EXIT:
        return False
    if run.stdout:
        return False
    lines = [ln for ln in (run.stderr or "").strip().splitlines() if ln.strip()]
    if len(lines) != 1:
        return False
    parsed = eng.parse_refusal(lines[0])
    if parsed is None:
        return False
    engine, kind, detail = parsed
    if not (kind and detail):
        return False
    # The engine writes its OWN name at the head of its own refusal (invariant
    # 9). A `lypning-l` binary answering in the core's name is a real defect and
    # is not quietly accepted here. A Run built without an engine name — a test
    # fixture, a hand-made record — is not asked the question.
    return not run.engine or engine == run.engine


def _stderr_shape(run: Run) -> str:
    """"" or "nonempty" — all of stderr that a comparison may key on.

    Exact stderr text cannot be compared across interpreters (a traceback names
    a file and a line the other never had), but "CPython wrote nothing and the
    engine wrote something" is a divergence a reader must see.
    """
    return "" if not (run.stderr or "").strip() else "nonempty"


# --- labelling ---------------------------------------------------------------


@dataclass(frozen=True)
class Label:
    """What ran the unit, what the router predicted, and why it was rejected."""

    requires: str
    requires_static: str
    route_agrees: bool
    runs: Tuple[Run, ...]
    mismatch: str
    naive_kind: str
    naive_detail: str
    #: The ``cap-*`` features the unit needs, derived from what the router saw it
    #: import plus a measured ``bigint`` refusal. Sorted; empty for a core unit.
    caps: Tuple[str, ...] = ()


def naive_program(reference: str) -> str:
    """The two-line program whose refusal IS the gap the unit closes.

    Measured, not asserted. A unit that claims to fill ``textwrap`` is only
    filling something if reaching for the real ``textwrap`` stops the engine, and
    the refusal it gets is the exact sentence a model will see. The second line
    exists so the import is not the whole program: an engine that pruned an
    unused import would answer a different question than the one asked.
    """
    return "import %s\nprint(\"imported %s\")\n" % (reference, reference)


def _caps_for(engine: str, imports: Sequence[str], core_kind: str) -> Tuple[str, ...]:
    """The ``cap-*`` features this unit needs, out of what its engine offers.

    Derived from :data:`lypning.engines.VARIANT_CAPS` rather than from a table
    written here: a cap is named after the module it serves (``cap-re`` serves
    ``re``), so intersecting the variant's caps with what the router saw the unit
    import names the ones it actually uses. ``cap-bigint`` has no module, so it
    is read off the measured refusal instead — the cheapest engine saying
    ``bigint`` IS the unit needing it.

    The variant's whole cap set would also be true and would say nothing: every
    ``lypning-l`` unit would carry all eight, and the column exists to
    distinguish them.
    """
    offered = tuple(lyp.VARIANT_CAPS.get(engine, ()))
    wanted = set()
    tops = set(m.split(".")[0] for m in imports)
    for cap in offered:
        if cap.startswith("cap-") and cap[len("cap-"):] in tops:
            wanted.add(cap)
    if core_kind == "bigint" and "cap-bigint" in offered:
        wanted.add("cap-bigint")
    return tuple(sorted(wanted))


def label_unit(unit: Unit, cpython: str, engines: Mapping[str, str],
               timeout_s: float = 30.0) -> Label:
    """CPython first, then each engine cheapest-first. The label is the answer.

    In this order, and the order is the product:

    1. CPython. A non-zero exit means the unit is broken and ``mismatch`` says
       so; the oracle is never overruled.
    2. Each engine in :data:`lypning.engines.ENGINE_ORDER`, cheapest first.
    3. The first engine that exits 0 with CPython's stdout BYTES, and the same
       stderr shape, is ``requires``.
    4. An engine that exits 0 with different bytes — or that crashes on a
       program CPython ran — is a MISMATCH. Fatal: the unit is rejected, the
       reason is recorded, and labelling stops. Never relabel around it
       (invariant 1).
    5. An engine that refuses cleanly is coverage; move to the next.
    6. Nothing ran it: ``requires`` is ``cpython``, a recorded gap and not a row.

    ``engines`` maps engine name to binary path. A name missing from it is not
    "this unit does not need it" — it is a cheaper engine that was never asked,
    and a label taken without it would understate what the unit needs. So a unit
    answered by an engine with an unasked cheaper one below it is rejected with
    ``engine-missing`` rather than labelled optimistically.
    """
    runs: List[Run] = []
    ref = run_source(unit.source, cpython, timeout_s=timeout_s, engine=CPYTHON)
    runs.append(ref)
    static, imports = _route_of(unit.source, engines)
    if ref.exit_code != 0:
        first = (ref.stderr or "").strip().splitlines()
        return Label(requires=CPYTHON, requires_static=static,
                     route_agrees=static == CPYTHON, runs=tuple(runs),
                     mismatch="cpython exited %d: %s"
                              % (ref.exit_code, first[-1] if first else "(no stderr)"),
                     naive_kind="", naive_detail="")

    requires = CPYTHON
    mismatch = ""
    skipped: List[str] = []
    core_kind = ""
    for name in ENGINE_ORDER:
        if name == CPYTHON:
            break
        binary = engines.get(name)
        if not binary:
            skipped.append(name)
            continue
        got = run_source(unit.source, binary, timeout_s=timeout_s, engine=name)
        runs.append(got)
        if name == SPECTRUM[0]:
            core_kind = got.kind
        if is_refusal(got):
            continue
        if got.exit_code != 0:
            mismatch = ("%s exited %d on a program cpython ran: %s"
                        % (name, got.exit_code,
                           _one_line(got.stderr) or "(no stderr)"))
            break
        if got.stdout != ref.stdout:
            mismatch = ("MISMATCH: %s exited 0 with different bytes than cpython: %s"
                        % (name, _diff_note(ref.stdout, got.stdout)))
            break
        if _stderr_shape(got) != _stderr_shape(ref):
            mismatch = ("MISMATCH: %s wrote stderr where cpython wrote none: %s"
                        % (name, _one_line(got.stderr)))
            break
        requires = name
        break

    if not mismatch and requires != CPYTHON and skipped:
        cheaper = [n for n in skipped if ENGINE_ORDER.index(n) < ENGINE_ORDER.index(requires)]
        if cheaper:
            mismatch = ("engine not available: %s — a cheaper engine was never "
                        "asked, so this label would understate the unit"
                        % ", ".join(cheaper))

    naive_kind, naive_detail = _naive_refusal(unit, engines, timeout_s)
    caps = _caps_for(requires, imports, core_kind)
    return Label(requires=requires, requires_static=static,
                 route_agrees=static == requires, runs=tuple(runs),
                 mismatch=mismatch, naive_kind=naive_kind, naive_detail=naive_detail,
                 caps=caps)


def _one_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    return lines[-1][:200] if lines else ""


def _diff_note(want: bytes, got: bytes) -> str:
    """Where two outputs first differ, spelled in ``repr`` so a CR is visible.

    The whole reason the comparison is over bytes is that ``\\r\\n`` and ``\\n``
    must not read alike; a note that decoded them would hide exactly what it was
    asked to report.
    """
    n = min(len(want), len(got))
    i = 0
    while i < n and want[i] == got[i]:
        i += 1
    lo = max(0, i - 20)
    return ("%d vs %d bytes, first differ at %d: cpython %r, engine %r"
            % (len(want), len(got), i, want[lo:i + 20], got[lo:i + 20]))


def _route_of(source: str, engines: Mapping[str, str]) -> Tuple[str, Tuple[str, ...]]:
    """``(engine, imports)`` as the Rust classifier predicts them.

    The router is asked through the CORE binary, which is where ``route`` lives;
    with no core built there is nothing to ask and everything routes to CPython,
    which is :func:`lypning.engines.route`'s own documented degradation.
    """
    core = engines.get(SPECTRUM[0])
    r = lyp.route(source, binary=Path(core) if core else None)
    return r.engine, tuple(r.imports)


def _naive_refusal(unit: Unit, engines: Mapping[str, str],
                   timeout_s: float) -> Tuple[str, str]:
    """The refusal a naive ``import <reference>`` gets on the cheapest engine.

    Empty for a unit whose reference is ``-``: it fills a language gap, not a
    module, so there is no import to try and an invented one would be a claim
    rather than a measurement. Empty too when the cheapest engine SERVES the
    module — which is a fact worth recording as an empty field rather than
    hiding: the unit fills an attribute of a served module, not the module.
    """
    if not unit.reference:
        return ("", "")
    for name in ENGINE_ORDER:
        if name == CPYTHON:
            break
        binary = engines.get(name)
        if not binary:
            continue
        got = run_source(naive_program(unit.reference), binary,
                         timeout_s=timeout_s, engine=name)
        if is_refusal(got):
            return (got.kind, got.detail)
        return ("", "")
    return ("", "")


def closed_kind_violation(unit: Unit, label: Label, closed: AbstractSet[str]) -> str:
    """Why this unit may never ship, or "" — the gate over the closed kinds.

    ``lypning.engines.ONLY_CPYTHON_REFUSALS`` is not a backlog. Each kind in it
    is a claim that NO reimplementation short of CPython gets the construct
    right: ``math``'s message text, ``json``'s float repr, ``random``'s MT19937
    stream, the observable order of a ``set``. The engine's job on those is to
    keep refusing.

    That makes them the one thing this corpus must never contain. Every other
    unit here fails loudly when it is wrong — CPython is run on every case and a
    wrong byte is a rejected unit. A unit over a closed kind fails QUIETLY: a
    pure-Python ``math.log`` agrees with CPython on the twelve values its cases
    print and diverges in the last place on the thirteenth, a set-ordering
    helper agrees under this machine's hash seed, a hand-rolled ``json`` float
    repr agrees until it meets ``0.1 + 0.2``. The cases cannot catch it, and a
    model that reproduced it inline would ship a silent wrong answer wearing the
    shape of a feature. Invariant 1 exists to keep a wrong answer loud; this gate
    is that invariant applied to the corpus, before the corpus exists.

    Two checks, one measured and one declared:

    * the MEASURED one — the refusal a naive ``import <reference>`` actually got.
      If the engine's own word for the gap is a closed kind, the unit is filling
      a closed kind whatever its header says.
    * the DECLARED one — the leading segment of each ``# fills:`` name, against
      the same set. ``math.log`` names ``math``; ``json.dumps`` names ``json``.
      Also ``<seg>-order`` and ``<seg>-method``, which is how ``set.union``
      reaches ``set-order`` and ``set-method``.

    ``dict-view`` is deliberately NOT reached by that suffix rule, though
    ``dict.`` would match it: ``dict.fromkeys`` is a legitimate gap the engine
    refuses as ``dict-method``, and a gate that rejected it would cost a good
    unit to catch nothing. A dict-view unit is caught the measured way or by its
    cases, and this is the honest limit of the declared half.
    """
    if label.naive_kind and label.naive_kind in closed:
        return ("closed kind %s: `import %s` refuses with a kind no "
                "reimplementation may answer"
                % (label.naive_kind, unit.reference or "-"))
    for target in unit.fills:
        seg = target.split(".")[0].strip()
        if not seg:
            continue
        for candidate in (seg, seg + "-order", seg + "-method"):
            if candidate in closed:
                return ("closed kind %s: `# fills: %s` targets what only CPython "
                        "may answer" % (candidate, target))
    return ""


# --- rows --------------------------------------------------------------------


def source_sha256(source: str) -> str:
    """The unit's identity: sha256 over the stripped source.

    Stripped so that a trailing newline added by an editor is not a new unit,
    and over the SOURCE rather than over the parsed parts so that a change to
    the parser cannot renumber a corpus that did not change.
    """
    return hashlib.sha256(source.strip().encode("utf-8")).hexdigest()


def unit_id(source: str) -> str:
    return ID_PREFIX + source_sha256(source)[:12]


def unit_row(unit: Unit, label: Label, producer: str, first_seen: str) -> Dict[str, Any]:
    """One corpus row, keys already in :data:`ROW_KEYS` order.

    ``first_seen`` is passed in and never taken from the clock: a library that
    called ``date.today()`` would rewrite every row it re-verified, and the
    workflow ends in ``git diff --exit-code``.
    """
    sha = source_sha256(unit.source)
    row = {
        "schema": SCHEMA,
        "id": ID_PREFIX + sha[:12],
        "name": unit.name,
        "fills": list(unit.fills),
        "reference": unit.reference,
        "requires": label.requires,
        "requires_static": label.requires_static,
        "route_agrees": bool(label.route_agrees),
        "caps": list(label.caps),
        "naive_kind": label.naive_kind,
        "naive_detail": label.naive_detail,
        "doc": unit.doc,
        "helpers": unit.helpers,
        "cases": unit.cases,
        "source_sha256": sha,
        "producer": producer,
        "first_seen": first_seen,
    }
    return _ordered(row, ROW_KEYS)


def drop_row(name: str, path: str, reason: str, detail: str,
             unit: Optional[Unit] = None, label: Optional[Label] = None,
             first_seen: str = "") -> Dict[str, Any]:
    """One drops-ledger row. ``reason`` is one of :data:`DROP_REASONS`."""
    if reason not in DROP_REASONS:
        raise ValueError("not a drop reason: %r" % (reason,))
    row = {
        "schema": DROPS_SCHEMA,
        "name": name,
        "path": path,
        "reason": reason,
        "detail": detail,
        "requires": label.requires if label else "",
        "requires_static": label.requires_static if label else "",
        "naive_kind": label.naive_kind if label else "",
        "naive_detail": label.naive_detail if label else "",
        "fills": list(unit.fills) if unit else [],
        "reference": unit.reference if unit else "",
        "source_sha256": source_sha256(unit.source) if unit else "",
        "first_seen": first_seen,
    }
    return _ordered(row, DROP_KEYS)


def _ordered(row: Dict[str, Any], keys: Tuple[str, ...]) -> Dict[str, Any]:
    """The same mapping with its keys in ``keys`` order, and only those keys.

    Python keeps insertion order, so this is the whole of the "fixed key order"
    rule — :func:`row_line` then dumps with ``sort_keys=False`` and gets it.
    """
    missing = [k for k in keys if k not in row]
    if missing:
        raise ValueError("row is missing %s" % ", ".join(missing))
    return dict((k, row[k]) for k in keys)


def row_line(row: Mapping[str, Any]) -> str:
    """One row as it is written: fixed order, compact, UTF-8 kept as UTF-8.

    Deliberately NOT :func:`pipeline.jsonio.canon`, which sorts keys. The row
    order is part of this format's contract (it puts the label next to the name
    and the source at the end, so a human reads a corpus without a tool), and
    sorting would silently reorder it. Everything else — the separators, the
    ``ensure_ascii=False``, one line per record — is the same serialisation, for
    the same reason: a record written twice is the same bytes twice.
    """
    return json.dumps(row, sort_keys=False, ensure_ascii=False,
                      separators=(",", ":"))


def rows_text(rows: Iterable[Mapping[str, Any]]) -> str:
    """Every row, one per line, with one trailing newline and no other."""
    out = "".join(row_line(r) + "\n" for r in rows)
    return out


def write_rows(path: "str | os.PathLike", rows: Sequence[Mapping[str, Any]]) -> int:
    """Write rows atomically. A half-written corpus is worse than no corpus.

    Atomic the same way :func:`pipeline.jsonio.write_jsonl` is — that function
    cannot be reused only because it serialises through ``canon``, which sorts.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(rows_text(rows))
        os.replace(tmp, str(p))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return len(rows)


# --- the verify pass ---------------------------------------------------------


@dataclass(frozen=True)
class Verification:
    """What one verify pass decided. Rows ship; drops are the ledger."""

    rows: Tuple[Dict[str, Any], ...] = ()
    drops: Tuple[Dict[str, Any], ...] = ()
    #: ``name -> Label`` for every unit that parsed, so a caller can report on a
    #: unit without re-running three engines.
    labels: Dict[str, Label] = field(default_factory=dict)

    @property
    def mismatches(self) -> List[Dict[str, Any]]:
        return [d for d in self.drops if d["reason"] == "mismatch"]


def verify_units(paths: Sequence[Path], cpython: str, engines: Mapping[str, str],
                 first_seen: str, producer: str = "authored",
                 closed: Optional[AbstractSet[str]] = None,
                 timeout_s: float = 30.0) -> Verification:
    """Label every unit, and say for each one whether it is a row or a drop.

    The order of the checks is the order of the costs: parse (free), CPython
    (one spawn), the engines (one spawn each, cheapest first), then the closed
    gate over what was measured. Rows come back sorted by name and drops with
    them, so two runs over the same tree write the same bytes.
    """
    closed_set = frozenset(closed) if closed is not None else frozenset(
        lyp.ONLY_CPYTHON_REFUSALS)
    rows: List[Dict[str, Any]] = []
    drops: List[Dict[str, Any]] = []
    labels: Dict[str, Label] = {}
    for p in sorted(paths, key=lambda q: (q.stem, str(q))):
        name = p.stem
        try:
            unit = parse_unit(str(p), p.read_text(encoding="utf-8"))
        except (UnitError, OSError, UnicodeDecodeError) as exc:
            drops.append(drop_row(name, str(p), "malformed", str(exc),
                                  first_seen=first_seen))
            continue
        label = label_unit(unit, cpython, engines, timeout_s=timeout_s)
        labels[name] = label
        if label.mismatch:
            reason = "mismatch"
            if label.mismatch.startswith("cpython exited"):
                reason = "cpython-rejected"
            elif label.mismatch.startswith("engine not available"):
                reason = "engine-missing"
            drops.append(drop_row(name, str(p), reason, label.mismatch, unit, label,
                                  first_seen))
            continue
        violation = closed_kind_violation(unit, label, closed_set)
        if violation:
            drops.append(drop_row(name, str(p), "closed-kind", violation, unit,
                                  label, first_seen))
            continue
        if label.requires == CPYTHON:
            drops.append(drop_row(name, str(p), "cpython-only",
                                  "no engine ran it: a recorded gap, not a row",
                                  unit, label, first_seen))
            continue
        rows.append(unit_row(unit, label, producer, first_seen))
    rows.sort(key=lambda r: (r["name"], r["id"]))
    drops.sort(key=lambda r: (r["name"], r["reason"]))
    return Verification(tuple(rows), tuple(drops), labels)


# --- assembly ----------------------------------------------------------------


@dataclass(frozen=True)
class Assembly:
    rows: Tuple[Dict[str, Any], ...] = ()
    #: ``(id, name)`` of every row dropped because an earlier file had its id.
    duplicates: Tuple[Tuple[str, str], ...] = ()
    #: ``(id, name_kept, name_seen)`` where one id arrived under two names —
    #: the same source committed twice. Loud, because a corpus that silently
    #: kept one of them would answer "how many units" differently every merge.
    collisions: Tuple[Tuple[str, str, str], ...] = ()


def assemble(row_groups: Sequence[Sequence[Mapping[str, Any]]]) -> Assembly:
    """Merge row files, de-duplicate by id, sort by name.

    First writer wins, because the groups arrive in the order the caller named
    them and that order is the only statement of precedence there is. A
    duplicate id under a DIFFERENT name is reported separately: identical source
    under two names is a corpus that would double-count itself.
    """
    seen: Dict[str, str] = {}
    out: List[Dict[str, Any]] = []
    dupes: List[Tuple[str, str]] = []
    collisions: List[Tuple[str, str, str]] = []
    for group in row_groups:
        for row in group:
            rid = str(row.get("id") or "")
            name = str(row.get("name") or "")
            if rid in seen:
                dupes.append((rid, name))
                if seen[rid] != name:
                    collisions.append((rid, seen[rid], name))
                continue
            seen[rid] = name
            out.append(_ordered(dict(row), ROW_KEYS))
    out.sort(key=lambda r: (r["name"], r["id"]))
    return Assembly(tuple(out), tuple(dupes), tuple(collisions))


# --- reporting ---------------------------------------------------------------


def _tally(rows: Sequence[Mapping[str, Any]], key: str) -> List[Tuple[str, int]]:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[str(row.get(key) or "")] = counts.get(str(row.get(key) or ""), 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def report(rows: Sequence[Mapping[str, Any]],
           drops: Sequence[Mapping[str, Any]] = ()) -> str:
    """The corpus in one screen. A STRING: invariant 8, the CLI prints it.

    Every count here is of the rows in hand, computed now — there is no
    remembered total anywhere in this module (invariant 3). The caller that
    prints this is the one that knows which command and which day produced it.
    """
    lines: List[str] = []
    lines.append("%d units" % len(rows))
    if rows:
        lines.append("")
        lines.append("engine        units")
        for engine in ENGINE_ORDER:
            n = sum(1 for r in rows if r.get("requires") == engine)
            if n:
                lines.append("  %-11s %4d" % (engine, n))
        disagree = [r for r in rows if not r.get("route_agrees")]
        lines.append("")
        lines.append("router agrees %d/%d" % (len(rows) - len(disagree), len(rows)))
        for r in disagree:
            lines.append("  %-28s route said %s, ran on %s"
                         % (r.get("name"), r.get("requires_static"), r.get("requires")))
        caps: Dict[str, int] = {}
        for r in rows:
            for cap in r.get("caps") or ():
                caps[cap] = caps.get(cap, 0) + 1
        if caps:
            # The column is as wide as the widest name, never a constant: a cap
            # one character over a fixed pad shunts its own count out of the
            # column and reads as a different number.
            width = max(14, max(len(c) for c in caps))
            lines.append("")
            lines.append("caps")
            for cap, n in sorted(caps.items(), key=lambda kv: (-kv[1], kv[0])):
                lines.append("  %-*s %4d" % (width, cap, n))
        kinds = [(k, n) for k, n in _tally(rows, "naive_kind") if k]
        if kinds:
            width = max(14, max(len(k) for k, _ in kinds))
            lines.append("")
            lines.append("gap each unit closes, as the engine words it")
            for kind, n in kinds:
                lines.append("  %-*s %4d" % (width, kind, n))
        fills = sum(len(r.get("fills") or ()) for r in rows)
        lines.append("")
        lines.append("%d CPython names filled across %d units" % (fills, len(rows)))
        lines.append("")
        lines.append("unit                          engine       fills")
        for r in rows:
            names = ", ".join(r.get("fills") or ())
            if len(names) > 60:
                names = names[:57] + "..."
            lines.append("  %-28s %-12s %s" % (r.get("name"), r.get("requires"), names))
    if drops:
        lines.append("")
        lines.append("%d dropped" % len(drops))
        for reason, n in _tally(drops, "reason"):
            lines.append("  %-16s %4d" % (reason, n))
        lines.append("")
        for d in drops:
            lines.append("  %-28s %-16s %s"
                         % (d.get("name"), d.get("reason"),
                            str(d.get("detail") or "")[:120]))
    return "\n".join(lines)


def resolve_engines(pairs: Sequence[str] = ()) -> Tuple[Dict[str, str], List[str]]:
    """``(name -> binary, missing)`` for the Rust spectrum.

    ``pairs`` are ``NAME=PATH`` strings from the command line and win over
    discovery. Discovery is :func:`pipeline.engines.engine_path`, which honours
    ``LYPNING_HOME`` and the ``NTX_ENGINE_*`` overrides the rest of this package
    uses, and falls back to the package's own finder.
    """
    found: Dict[str, str] = {}
    for name in ENGINE_ORDER:
        if name == CPYTHON:
            continue
        p = eng.engine_path(name)
        if not p:
            q = lyp.find_variant(name)
            p = str(q) if q else None
        if p:
            found[name] = p
    for pair in pairs:
        if "=" not in pair:
            raise ValueError("--engine wants NAME=PATH, got %r" % (pair,))
        name, path = pair.split("=", 1)
        name = name.strip()
        if name not in ENGINE_ORDER or name == CPYTHON:
            raise ValueError("not an engine in the spectrum: %r" % (name,))
        found[name] = path.strip()
    missing = [n for n in ENGINE_ORDER if n != CPYTHON and n not in found]
    return found, missing


def resolve_cpython(override: Optional[str] = None) -> Optional[str]:
    """The real CPython, never the capture shim.

    :func:`lypning.engines.find_cpython` walks past anything carrying the shim
    marker — a verify pass that measured the shim would be measuring a shell
    script.
    """
    if override:
        return override
    p = lyp.find_cpython()
    return str(p) if p else None
