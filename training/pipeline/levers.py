"""Which lever can remove a refusal: the engine, the model, or neither.

`ASSESSMENT.md` §4 split this repository's refused captures four ways — the
repository working on itself, a fallback that is correct to keep, a capability
the engine could serve, and a residue — and concluded that the model lever is a
*subset* of the engine one, because a model can only rewrite around a refusal
that has a native equivalent, and every such refusal is one the engine could
also remove by serving the construct. That split decides how the programme's
budget divides between two levers. It was made once, in prose, by judgement per
kind, and nothing in the tree could re-make it.

This module is that split as code, and it is deliberately not a classifier.
Three layers are mechanical and decide themselves; everything else is a frozen
`DECLARED` table of one line per refusal *family*, each carrying the reason it
was bucketed and whether §4 is its source or this tree proposed it. A family no
layer reaches and no declaration names lands in `other` with basis `undeclared`
and shows up in the review queue rather than being absorbed into a third of a
bucket — the invariant-1 shape: loud, not silent.

THE BUCKET IS A FUNCTION OF `(kind, detail)` AND NOTHING ELSE. 205 of the 643
refused programs on this tree mention this package by name, and they include
most of the `class definition` entries and a third of the `import subprocess`
ones — §4 counts those in other buckets, because its words are "a judgement
call per kind". Routing on program text would put one refusal kind in two
buckets depending on who typed the program, which is not a split between two
levers. The program is reported as a column and never read as a rule.

The closed-refusal list is IMPORTED, through `refusals.closed_kinds()`, never
restated: a second copy of it is what `refusals.py`'s own docstring calls an
invariant-1 violation shipped as a feature. `_check_declarations` asserts at
import that no declaration names a kind on that list, so a declaration cannot
quietly shadow the engine's own statement about itself.

The same table reads the eval-2 draws (`unit: "draw"`), and reaching them takes
a join, not a second reader: `eval2_rows.row_for` says which draws are
correct-but-fallback and how they cluster but records no refusal, while the
legality replay records the refusal as `"<kind>: <detail>"` and none of the
population labels. `draw_refusals` joins the two on `(corpus_id, draw)` —
the key `eval2_rows` itself already uses — and hands the result to the same
`classify`. That is what makes the ladder's rung S0b a command rather than a
judgement call re-made on another device: the table that decides the private
rows is this table, reviewed here, versioned by `RULE`.

Library code does not print (root `CLAUDE.md` invariant 8): every function here
returns data or a string, and `cli.py` renders it.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: The four buckets of `ASSESSMENT.md` §4, in its order.
SELF_REFERENTIAL = "self-referential"
LEGITIMATE_FALLBACK = "legitimate-fallback"
ENGINE_ADDRESSABLE = "engine-addressable"
OTHER = "other"
BUCKETS = (SELF_REFERENTIAL, LEGITIMATE_FALLBACK, ENGINE_ADDRESSABLE, OTHER)

#: Bumped whenever a bucket's meaning or a reviewed declaration verdict changes,
#: the way `refusals.GRADER` is.
#: Every result carries it and `compare` refuses a pair that disagrees, so two
#: tables can never be read against each other across a redefinition.
RULE = 2

#: Where a declaration came from. `S4` means the family is named in
#: `ASSESSMENT.md` §4's own per-bucket kind list, so the declaration transcribes
#: a judgement already made and reviewed; `NEW` means this tree proposed it and
#: it is owed a review. `RULE` means the subject is a repair rule in
#: `repair_rules.RULES`: the rewrite runs natively or the rule is withdrawn, so
#: the rule is the proof of the verdict, and the row is matched by the rule
#: existing rather than by a capture-corpus family -- a rule the corpus never
#: exercised still needs its verdict on record before its pairs reach a model.
#: The counts are printed per bucket, so a reader always knows how much of a
#: table is transcription and how much is new judgement.
FROM_SECTION_4 = "s4"
NEW_HERE = "new"
FROM_RULE = "rule"

#: The package whose own imports are self-referential, read from this tree
#: rather than spelled, so a rename cannot orphan the layer.
OWN_PACKAGE = "lypning"

_RE_NAMED_GROUP = "re: named group (?P<name>...)"

# --------------------------------------------------------------------------
# The declared table: the part that is NOT derivable.
#
# One row per family key, `(family, bucket, provenance, why)`. Historical review
# groups stay in their original order so rule changes have a legible diff; the
# bucket field, not physical location, is authoritative. `why` is one line and
# answers only "why this bucket", never "what this construct is".
#
# The honest limit this table exists to make visible: nothing in a refusal
# record distinguishes "no reimplementation may serve this" from "this engine
# has not served it yet", and that distinction is the whole difference between
# the fallback and engine-addressable buckets. `bigint` is the sharpest case —
# a kind the engine's own closed list used to carry and no longer does, which
# is a bucket boundary that moved because somebody wrote code.
# --------------------------------------------------------------------------
DECLARED: Tuple[Tuple[str, str, str, str], ...] = (
    # ---- self-referential: this repository working on itself ----------------
    ("module-attr: sys.path", SELF_REFERENTIAL, FROM_SECTION_4,
     "read to find this checkout; the engine has no import machinery to serve it"),
    ("module-attr: sys.version", SELF_REFERENTIAL, FROM_SECTION_4,
     "interpreter identity, asked by this repository about its own runs"),
    ("module-attr: sys.executable", SELF_REFERENTIAL, FROM_SECTION_4,
     "interpreter identity: which binary is running this checkout"),
    ("module-attr: sys.version_info", SELF_REFERENTIAL, NEW_HERE,
     "interpreter identity, the structured form of sys.version"),
    ("module-attr: sys.prefix", SELF_REFERENTIAL, NEW_HERE,
     "interpreter identity: where this installation lives"),
    ("module-attr: sys.flags", SELF_REFERENTIAL, NEW_HERE,
     "interpreter identity: how this interpreter was invoked"),
    ("module-attr: sys.stdlib_module_names", SELF_REFERENTIAL, NEW_HERE,
     "interpreter identity: this build's own stdlib set"),
    ("module-attr: glob.__file__", SELF_REFERENTIAL, NEW_HERE,
     "asks where a stdlib module is installed, which is this checkout's layout"),
    ("module-attr: re.__file__", SELF_REFERENTIAL, NEW_HERE,
     "asks where a stdlib module is installed, which is this checkout's layout"),
    ("module: sysconfig", SELF_REFERENTIAL, NEW_HERE,
     "the build configuration of the interpreter running this checkout"),
    ("module: platform", SELF_REFERENTIAL, NEW_HERE,
     "identity of the machine this checkout runs on"),
    ("module: __future__", SELF_REFERENTIAL, NEW_HERE,
     "asked about this checkout's own source compatibility, not about a task"),

    # ---- legitimate fallback: semantics, I/O, environment, nondeterminism ---
    ("module: subprocess", LEGITIMATE_FALLBACK, FROM_SECTION_4,
     "spawns processes; serving it would put the engine in the process business"),
    ("builtin: eval", LEGITIMATE_FALLBACK, FROM_SECTION_4,
     "evaluates arbitrary source, which is CPython's own compiler"),
    ("builtin: open() with a non-str path", LEGITIMATE_FALLBACK, FROM_SECTION_4,
     "a raw descriptor is the process's file table, not a value the engine holds"),
    ("module: urllib", LEGITIMATE_FALLBACK, FROM_SECTION_4,
     "network and URL retrieval reach outside the program"),
    ("module: time", LEGITIMATE_FALLBACK, FROM_SECTION_4,
     "wall clock: the answer differs every run, so no oracle pins it"),
    ("module: socket", LEGITIMATE_FALLBACK, NEW_HERE,
     "network endpoints reach outside the program"),
    ("module: http", LEGITIMATE_FALLBACK, NEW_HERE,
     "serves and fetches over the network"),
    ("module: threading", LEGITIMATE_FALLBACK, NEW_HERE,
     "concurrency the engine does not model, and scheduling is not reproducible"),
    ("module: inspect", LEGITIMATE_FALLBACK, NEW_HERE,
     "reads CPython's own frames and source, which no reimplementation owns"),
    ("module: locale", LEGITIMATE_FALLBACK, NEW_HERE,
     "the answer depends on the environment's locale, not on the program"),
    ("module: warnings", LEGITIMATE_FALLBACK, NEW_HERE,
     "CPython's own warning machinery and its filters"),
    ("module: tempfile", LEGITIMATE_FALLBACK, NEW_HERE,
     "creates filesystem state with names that differ every run"),
    ("module: shutil", LEGITIMATE_FALLBACK, NEW_HERE,
     "mutates the filesystem outside the program's own output"),
    ("module: resource", LEGITIMATE_FALLBACK, NEW_HERE,
     "reads and sets process limits, which belong to the host"),
    ("module: _ctypes", LEGITIMATE_FALLBACK, NEW_HERE,
     "loads native libraries into the process"),
    ("builtin: exec", LEGITIMATE_FALLBACK, NEW_HERE,
     "executes arbitrary source, which is CPython's own compiler"),
    ("builtin: compile", LEGITIMATE_FALLBACK, NEW_HERE,
     "produces CPython bytecode, which is CPython's own compiler"),
    ("builtin: __import__", LEGITIMATE_FALLBACK, NEW_HERE,
     "the import machinery the engine deliberately does not have"),
    ("builtin: getattr", ENGINE_ADDRESSABLE, NEW_HERE,
     "computed attribute lookup is deterministic over values the engine already holds"),
    ("builtin: hash", LEGITIMATE_FALLBACK, NEW_HERE,
     "CPython defines the value, and it is randomised per process"),
    ("builtin: dir", ENGINE_ADDRESSABLE, NEW_HERE,
     "introspection over the engine's own values is deterministic"),

    ("builtin: iter(callable, sentinel)", ENGINE_ADDRESSABLE, NEW_HERE,
     "a deterministic language protocol with a native equivalent"),
    ("module-attr: os.system", LEGITIMATE_FALLBACK, NEW_HERE,
     "spawns a shell"),
    ("module-attr: os.pipe", LEGITIMATE_FALLBACK, NEW_HERE,
     "allocates kernel file descriptors"),
    ("module-attr: os.stat", LEGITIMATE_FALLBACK, NEW_HERE,
     "reads filesystem metadata that differs per host and per run"),
    ("module-attr: os.walk", LEGITIMATE_FALLBACK, NEW_HERE,
     "filesystem traversal order is not reproducible, as glob-order already is"),
    ("os-listdir: os.listdir() order is filesystem-defined and not reproducible",
     LEGITIMATE_FALLBACK, NEW_HERE,
     "the engine's own detail says the order is not reproducible"),
    ("pathlib: PosixPath.glob", LEGITIMATE_FALLBACK, NEW_HERE,
     "the glob-order argument the engine's closed list already makes for glob()"),
    ("module-attr: random.Random", ENGINE_ADDRESSABLE, NEW_HERE,
     "the captured uses are explicitly seeded deterministic MT19937 streams"),
    ("module-attr: random.sample", ENGINE_ADDRESSABLE, NEW_HERE,
     "the captured uses seed the stream before this deterministic selection"),
    ("module-attr: sys.stdin.buffer", LEGITIMATE_FALLBACK, NEW_HERE,
     "the raw byte stream under the text layer, owned by the host's I/O stack"),
    ("module-attr: sys.stdout.buffer", LEGITIMATE_FALLBACK, NEW_HERE,
     "the raw byte stream under the text layer, owned by the host's I/O stack"),
    ("module-attr: sys.stdin.newlines", LEGITIMATE_FALLBACK, NEW_HERE,
     "CPython's own universal-newline bookkeeping on a stream"),
    ("module-attr: sys.stdout.newlines", LEGITIMATE_FALLBACK, NEW_HERE,
     "CPython's own universal-newline bookkeeping on a stream"),
    ("module-attr: sys.get_int_max_str_digits", LEGITIMATE_FALLBACK, NEW_HERE,
     "the bignum policy of the interpreter, not a construct to serve"),
    ("bigint: int() of a string past sys.get_int_max_str_digits(), where CPython raises ValueError",
     LEGITIMATE_FALLBACK, NEW_HERE,
     "CPython's own conversion limit and the exact ValueError it raises"),
    ("bigint: str() of an integer past sys.get_int_max_str_digits(), where CPython raises ValueError",
     LEGITIMATE_FALLBACK, NEW_HERE,
     "CPython's own conversion limit and the exact ValueError it raises"),
    ("bigint: an integer past 64 bits where this engine needs a machine word",
     ENGINE_ADDRESSABLE, NEW_HERE,
     "arbitrary precision is an engine capability and this boundary has already moved once"),
    ("bigint: math.factorial() past 20!", ENGINE_ADDRESSABLE, NEW_HERE,
     "pure arbitrary-precision arithmetic over an explicit value"),

    ("float-sum: sum() over floats where CPython 3.11, 3.12 and 3.14 round differently",
     LEGITIMATE_FALLBACK, NEW_HERE,
     "the engine's own detail says CPython versions disagree, so no oracle pins it"),
    ("file-tell: tell() just past a bare \\r on a stream that ends a line there",
     LEGITIMATE_FALLBACK, NEW_HERE,
     "CPython's own buffered-reader bookkeeping is the observable"),
    ("file-tell: tell() after the stream has been iterated", LEGITIMATE_FALLBACK, NEW_HERE,
     "CPython's own buffered-reader bookkeeping is the observable"),
    ("format: locale-aware 'n' format type", LEGITIMATE_FALLBACK, NEW_HERE,
     "the answer depends on the environment's locale, not on the program"),
    ("repr: repr() of a builtin_function_or_method", LEGITIMATE_FALLBACK, NEW_HERE,
     "the text embeds a CPython address and its own object model"),
    ("repr: repr() of a method_descriptor", LEGITIMATE_FALLBACK, NEW_HERE,
     "the text names CPython's own object model (no address: that is the bound form)"),
    ("dunder-attr: type.__qualname__, which is part of Python's data model",
     ENGINE_ADDRESSABLE, NEW_HERE,
     "a deterministic attribute of a type the engine models"),
    ("method: .__qualname__()", ENGINE_ADDRESSABLE, NEW_HERE,
     "a deterministic attribute of a value the engine models"),
    ("mkdir: os.mkdir() with a mode argument", LEGITIMATE_FALLBACK, NEW_HERE,
     "the resulting mode depends on the process umask"),
    ("csv: a row from a file that has been written since it was opened",
     LEGITIMATE_FALLBACK, NEW_HERE,
     "the answer depends on host buffering, not on the program"),
    ("re: ~ on a RegexFlag, whose inverted mask CPython spells version-dependently",
     LEGITIMATE_FALLBACK, NEW_HERE,
     "the engine's own detail says the spelling is CPython-version-dependent"),
    ("re: bad character range",
     ENGINE_ADDRESSABLE, NEW_HERE,
     "a deterministic parser rejection the engine can report on its regex surface"),
    ("re: nothing to repeat",
     ENGINE_ADDRESSABLE, NEW_HERE,
     "a deterministic parser rejection the engine can report on its regex surface"),
    ("base64: b64decode() over data carrying an alphabet character after the padding that closes a quad",
     LEGITIMATE_FALLBACK, NEW_HERE,
     "the observable is binascii's own acceptance of malformed input"),
    ("base64: b64decode() over data with incorrect padding", LEGITIMATE_FALLBACK, NEW_HERE,
     "the observable is the exact binascii.Error text CPython raises"),
    ("base64: base64.b64decode(validate=…) that is not literally False, None or 0: "
     "a truthy validate selects binascii's strict mode, whose every rejection is a "
     "binascii.Error message this engine does not write",
     LEGITIMATE_FALLBACK, NEW_HERE,
     "the engine's own detail says every rejection is a binascii message it does not write"),
    ("base64: base64.b64encode() over a str", LEGITIMATE_FALLBACK, NEW_HERE,
     "the observable is the exact TypeError CPython raises"),
    ("str-method: str.casefold() of U+00B5", ENGINE_ADDRESSABLE, NEW_HERE,
     "a static Unicode table, the same kind of table as the Unicode regex surface"),
    ("type: type() of a RegexFlag", LEGITIMATE_FALLBACK, NEW_HERE,
     "CPython spells the flag repr version-dependently, as the `~` row already says"),
    ("argument: keyword strict", ENGINE_ADDRESSABLE, NEW_HERE,
     "a deterministic builtin call shape over explicit inputs"),

    # ---- engine-addressable: a capability the engine could serve ------------
    ("class: class definition", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "a language construct with a native equivalent; no environment is read"),
    ("module: itertools", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "pure computation over iterables, and every result is deterministic"),
    ("module: unicodedata", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "a static table lookup; the cost is table bytes, not semantics"),
    ("module: math", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "pure computation; partly served since 2026-09-12, and the capture predates it"),
    ("module: binascii", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "pure byte transformation with a fixed answer"),
    ("module: datetime", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "arithmetic over explicit values; only the clock reads the environment"),
    ("module: textwrap", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "pure string computation with a fixed answer"),
    ("decorator: decorated definition", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "a language construct with a native equivalent; no environment is read"),
    ("module: struct", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "pure byte packing with a fixed answer"),
    ("module-attr: io.StringIO", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "an in-memory stream; nothing outside the program is touched"),
    ("generator: yield expression", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "a language construct with a native equivalent; no environment is read"),
    ("module: argparse", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "parses argv, which the engine already receives"),
    ("module-attr: csv.writer", ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "pure formatting over values the program already holds"),
    (_RE_NAMED_GROUP, ENGINE_ADDRESSABLE, FROM_SECTION_4,
     "a regex feature with a fixed answer, already collapsed to one row by §4"),
    ("recursion: call depth beyond 180", ENGINE_ADDRESSABLE, NEW_HERE,
     "180 is THIS engine's own stack budget (eval.rs MAX_DEPTH), not CPython's"),
    ("recursion: repr nested deeper than 500", ENGINE_ADDRESSABLE, NEW_HERE,
     "500 is THIS engine's own nesting budget (err.rs MAX_NEST), not CPython's"),
    ("builtin: EnvironmentError", ENGINE_ADDRESSABLE, NEW_HERE,
     "an alias for OSError, whose type this table already calls servable"),
    ("walrus: assignment expression", ENGINE_ADDRESSABLE, NEW_HERE,
     "a language construct with a native equivalent; no environment is read"),
    ("nonlocal: nonlocal declaration", ENGINE_ADDRESSABLE, NEW_HERE,
     "a language construct with a native equivalent; no environment is read"),
    ("module: fnmatch", ENGINE_ADDRESSABLE, NEW_HERE,
     "pure pattern matching over strings the program already holds"),
    ("module: fractions", ENGINE_ADDRESSABLE, NEW_HERE,
     "exact rational arithmetic over explicit values"),
    ("module: statistics", ENGINE_ADDRESSABLE, NEW_HERE,
     "pure computation over values the program already holds"),
    ("module: zlib", ENGINE_ADDRESSABLE, NEW_HERE,
     "pure byte transformation; note compress() output tracks the zlib build"),
    ("module: string", ENGINE_ADDRESSABLE, NEW_HERE,
     "static constants and pure template substitution"),
    # ---- declared 2026-09-19 from the repair rules, which are their proof ----
    # `repair_rules.RULES` rewrites each of these into the served subset and the
    # rewrite verifies natively, so the used surface is expressible there by
    # construction. A rule for a module this table called a fallback would be
    # the two loops disagreeing; `test_repair_rules.py` holds the seam.
    ("module: functools", ENGINE_ADDRESSABLE, FROM_RULE,
     "reduce over values the program holds; rule_functools inlines it natively"),
    ("module: operator", ENGINE_ADDRESSABLE, FROM_RULE,
     "named forms of operators the engine already evaluates; rule_operator inlines them"),
    ("module: copy", ENGINE_ADDRESSABLE, FROM_RULE,
     "copies of plain values the engine holds; rule_copy rewrites them natively"),
    ("module: heapq", ENGINE_ADDRESSABLE, FROM_RULE,
     "heap operations over a list, pure; rule_heapq rewrites them natively"),
    ("module: bisect", ENGINE_ADDRESSABLE, FROM_RULE,
     "binary search over a list the program holds; rule_bisect rewrites it natively"),
    ("module: array", ENGINE_ADDRESSABLE, FROM_RULE,
     "a typed sequence over held numbers; rule_array serves it as a list"),
    ("module: decimal", ENGINE_ADDRESSABLE, FROM_RULE,
     "only the integer-valued surface, which is the integer; rule_decimal serves that and refuses the rest"),
    ("module: calendar", ENGINE_ADDRESSABLE, FROM_RULE,
     "civil-calendar arithmetic; rule_calendar's prelude is differential-tested against CPython"),
    ("module: types", ENGINE_ADDRESSABLE, NEW_HERE,
     "names for objects the engine already has, once it serves the constructs"),
    ("module: dataclasses", ENGINE_ADDRESSABLE, NEW_HERE,
     "a class-definition convenience, so it rides on the class row above"),
    ("module: ast", ENGINE_ADDRESSABLE, NEW_HERE,
     "parsing python source is what this engine already does"),
    ("module-attr: hashlib.new", ENGINE_ADDRESSABLE, NEW_HERE,
     "a digest over bytes the program holds; the answer is fixed"),
    ("module-attr: hashlib.blake2b", ENGINE_ADDRESSABLE, NEW_HERE,
     "a digest over bytes the program holds; the answer is fixed"),
    ("module-attr: hashlib.algorithms_guaranteed", ENGINE_ADDRESSABLE, NEW_HERE,
     "a static set the engine could state once it serves the digests"),
    ("module-attr: base64.encodebytes", ENGINE_ADDRESSABLE, NEW_HERE,
     "pure byte transformation beside the b64 forms already served"),
    ("module-attr: base64.standard_b64encode", ENGINE_ADDRESSABLE, NEW_HERE,
     "pure byte transformation beside the b64 forms already served"),
    ("module-attr: base64._bytes_from_decode_data", ENGINE_ADDRESSABLE, NEW_HERE,
     "a private helper, but pure byte handling with a fixed answer"),
    ("module-attr: math.log", ENGINE_ADDRESSABLE, NEW_HERE,
     "pure computation, beside the math the engine already serves"),
    ("module-attr: pathlib.PurePosixPath", ENGINE_ADDRESSABLE, NEW_HERE,
     "pure path algebra; the Pure forms deliberately touch no filesystem"),
    ("csv: csv.reader() over a list", ENGINE_ADDRESSABLE, NEW_HERE,
     "the engine's own detail says only a file object and stdin are served"),
    ("collections: defaultdict() with a type default_factory", ENGINE_ADDRESSABLE, NEW_HERE,
     "the engine's own detail says only the builtin types are served"),
    ("type: type() of a ValueError", ENGINE_ADDRESSABLE, NEW_HERE,
     "naming an exception class, which the engine already raises"),
    ("type: type() of a TypeError", ENGINE_ADDRESSABLE, NEW_HERE,
     "naming an exception class, which the engine already raises"),
    ("type: type() of a NameError", ENGINE_ADDRESSABLE, NEW_HERE,
     "naming an exception class, which the engine already raises"),
    ("type: type() of a OSError", ENGINE_ADDRESSABLE, NEW_HERE,
     "naming an exception class, which the engine already raises"),
    ("type: type() of a OverflowError", ENGINE_ADDRESSABLE, NEW_HERE,
     "naming an exception class, which the engine already raises"),
    ("type: type() of a FileNotFoundError", ENGINE_ADDRESSABLE, NEW_HERE,
     "naming an exception class, which the engine already raises"),

    ("type: type() of a dict_keys", ENGINE_ADDRESSABLE, NEW_HERE,
     "naming a type the engine already produces"),
    ("type: type() of a function", ENGINE_ADDRESSABLE, NEW_HERE,
     "naming a type the engine already produces"),
    ("method: .name()", ENGINE_ADDRESSABLE, NEW_HERE,
     "an attribute on a value the engine already holds"),
    ("str-method: str.center()", ENGINE_ADDRESSABLE, NEW_HERE,
     "pure string computation with a fixed answer"),
    ("int-method: int.conjugate()", ENGINE_ADDRESSABLE, NEW_HERE,
     "pure numeric computation with a fixed answer"),
    ("float-method: float.hex()", ENGINE_ADDRESSABLE, NEW_HERE,
     "pure numeric formatting with a fixed answer"),
    ("builtin: frozenset", ENGINE_ADDRESSABLE, NEW_HERE,
     "a value constructor; only its repr order is closed, and that kind is separate"),
    ("builtin: bytearray", ENGINE_ADDRESSABLE, NEW_HERE,
     "a mutable byte buffer held entirely inside the program"),
    ("builtin: issubclass", ENGINE_ADDRESSABLE, NEW_HERE,
     "a class-hierarchy query, riding on the class row above"),
    ("format: integer format code applied to float", ENGINE_ADDRESSABLE, NEW_HERE,
     "the observable is a fixed TypeError the engine could raise"),
    ("format: float format code applied to str", ENGINE_ADDRESSABLE, NEW_HERE,
     "the observable is a fixed TypeError the engine could raise"),
    ("re: \\w, \\d, \\s, \\b, \\B, \\W, \\D or \\S on a non-ASCII pattern or subject",
     ENGINE_ADDRESSABLE, NEW_HERE,
     "a static Unicode table, the same table the unicodedata row needs"),
    ("re: re.IGNORECASE with a non-ASCII pattern or subject", ENGINE_ADDRESSABLE, NEW_HERE,
     "a static Unicode table, the same table the unicodedata row needs"),
    ("re: bytes pattern or subject", ENGINE_ADDRESSABLE, NEW_HERE,
     "the engine already matches over text; bytes is the same algorithm"),
    ("re: backreference \\1..\\99", ENGINE_ADDRESSABLE, NEW_HERE,
     "a regex feature with a fixed answer"),
    ("re: negative lookbehind", ENGINE_ADDRESSABLE, NEW_HERE,
     "a regex feature with a fixed answer"),
)


def _check_declarations(closed_kinds_fn):
    # type: (Any) -> None
    """Refuse a table that shadows the engine's own list, or repeats itself.

    A declaration naming a closed kind is the second copy `refusals.py` forbids:
    it would let this table contradict the engine's statement about itself, and
    the contradiction would be silent, which is the invariant-1 shape.
    """
    seen = set()
    for family_key, bucket, provenance, why in DECLARED:
        if family_key in seen:
            raise ValueError("levers: duplicate declaration: " + family_key)
        seen.add(family_key)
        if bucket not in BUCKETS:
            raise ValueError("levers: unknown bucket %r for %s" % (bucket, family_key))
        if provenance not in (FROM_SECTION_4, NEW_HERE, FROM_RULE):
            raise ValueError("levers: unknown provenance %r for %s" % (provenance, family_key))
        if not why:
            raise ValueError("levers: declaration without a reason: " + family_key)
    try:
        closed = closed_kinds_fn()
    except Exception:  # no engine here; the check runs wherever one exists
        return
    for family_key, _bucket, _provenance, _why in DECLARED:
        kind = family_key.split(":", 1)[0]
        if kind in closed:
            raise ValueError(
                "levers: %s declares a kind the engine's own closed list owns (%s); "
                "import that list, never restate it" % (family_key, kind))


def closed() -> "frozenset[str]":
    """The kinds no reimplementation may answer, through `refusals`.

    One hop further from `lypning.engines.ONLY_CPYTHON_REFUSALS` and still zero
    copies of it. Empty when the engine is not importable, which the result
    records rather than hiding: see `engine_note`.
    """
    from . import refusals

    try:
        return refusals.closed_kinds()
    except Exception:
        return frozenset()


def stdlib_names() -> "Optional[frozenset[str]]":
    """What this interpreter ships, which is what the `not-stdlib` layer asks.

    Returns **None**, not an empty set, when it cannot answer —
    `sys.stdlib_module_names` arrived in 3.10 and this tree still declares
    `>=3.9`. The distinction is the whole safety of the layer: an empty set
    makes `top not in stdlib` true for *every* import, which would silently
    reclassify every stdlib module as third-party and land it in the fallback
    bucket. Measured on this tree, that moves 94 entries out of
    engine-addressable without a single row entering the review queue — a wrong
    table that reports itself as complete, which is the failure mode this module
    is otherwise built to prevent. None makes the layer decide nothing instead,
    and the result records that it abstained.
    """
    names = getattr(sys, "stdlib_module_names", None)
    return frozenset(names) if names else None


def _top_module(detail: str) -> Optional[str]:
    """The top-level package name of an `import X.Y` detail, or None."""
    if not detail.startswith("import "):
        return None
    name = detail[len("import "):].strip()
    if not name:
        return None
    return name.split(".")[0]


def _construct(detail: str) -> str:
    """Strip a leading `pattern <src>: ` or `template <src>: ` echo of the user's
    own text, leaving the construct the engine actually named.

    The engine writes the source it was given back into the detail, and it
    escapes only control characters — so the user's spaces, parentheses and
    words survive. Two things went wrong before this existed, both measured on
    hand-built details this corpus happens not to contain yet: a pattern holding
    `" ("` truncated the key inside the user's text, collapsing a backreference
    and a lookbehind into one row keyed `re: pattern 'a`; and a pattern
    containing the words "named group" was bucketed AS a named group, carrying
    that declaration's reason, however it actually failed. Keying on the
    construct and not on the echo closes both, and a user's text can no longer
    decide a bucket.
    """
    for prefix in ("pattern ", "template "):
        if detail.startswith(prefix) and ": " in detail:
            return detail.rsplit(": ", 1)[1].strip()
    return detail


def family(kind: str, detail: str) -> str:
    """The ranking and declaration key: one feature, one row.

    Three collapses, each because the raw detail scatters one feature across
    several rows:

    - `module: import urllib.request` -> `module: urllib`, because the bucket is
      a property of the package, not of the submodule reached for.
    - `re: pattern '(?P<a>\\d+)': named group (?P<name>...)` -> one row, because
      the detail embeds the user's own pattern. §4 already collapsed it.
    - a trailing parenthetical is dropped: `sys.path (lypning has no import
      machinery)` is the engine explaining itself, and keying on it would couple
      this table to the engine's wording.
    """
    if kind == "module":
        top = _top_module(detail)
        if top is not None:
            return "module: " + top
    construct = _construct(detail)
    if kind == "re" and "named group" in construct:
        return _RE_NAMED_GROUP
    head = construct.split(" (")[0].strip()
    return "%s: %s" % (kind, head)


_DECLARED_INDEX = dict((row[0], row) for row in DECLARED)


def classify(kind: str, detail: str, *, closed_kinds=None, stdlib=None) -> Dict[str, str]:
    """Bucket one refusal. Returns `{bucket, basis, evidence, family}`.

    Layer order, first match wins, and the order is load-bearing:

    1. `own-package` — an import of this package. It runs first because those
       captures are this repository working on itself and belong in no lever's
       backlog.
    2. `engine-closed-list` — the kind is on the engine's own list. It runs
       before every declaration so that the engine's statement about itself
       always wins: `math: math.fmod() outside its domain` is closed even
       though `module: math` is engine-addressable, because the closed list
       speaks about kinds and the declaration about families.
    3. `not-stdlib` — an import of something this interpreter does not ship. No
       engine serves a third-party package, so it is fallback by construction.
    4. `declared` — the reviewed table above.
    5. `undeclared` — nothing reaches it; it lands in `other` and in the queue.
    """
    if closed_kinds is None:
        closed_kinds = closed()
    if stdlib is None:
        stdlib = stdlib_names()
    key = family(kind, detail)
    top = _top_module(detail) if kind == "module" else None
    if top == OWN_PACKAGE:
        return {"bucket": SELF_REFERENTIAL, "basis": "own-package",
                "evidence": OWN_PACKAGE, "family": key}
    if kind in closed_kinds:
        return {"bucket": LEGITIMATE_FALLBACK, "basis": "engine-closed-list",
                "evidence": kind, "family": key}
    if stdlib is not None and top is not None and top not in stdlib:
        return {"bucket": LEGITIMATE_FALLBACK, "basis": "not-stdlib",
                "evidence": top, "family": key}
    row = _DECLARED_INDEX.get(key)
    if row is not None:
        return {"bucket": row[1], "basis": "declared", "evidence": row[3],
                "family": key, "provenance": row[2]}
    return {"bucket": OTHER, "basis": "undeclared", "evidence": "", "family": key}


def refusal_of(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise one row of any supported population, or None if it carries no refusal.

    Two shapes, one spelling. `nt classify` writes `outcome == "refused"` with
    `info.kind` / `info.detail`; a legality replay writes the same pair as
    `blocker = "<kind>: <detail>"` (`refusals.grade_against_engine`), which is
    what the eval-2 draw rows carry. Neither is parsed twice.
    """
    if "kind" in record and "outcome" not in record:
        # Already normalised — `draw_refusals` did the join. Passing a joined
        # record back through the two parsers below would re-derive nothing.
        return dict(record)
    if record.get("outcome") == "refused":
        info = record.get("info") or {}
        kind, detail = info.get("kind"), info.get("detail")
        if not kind:
            return None
        entry = record.get("entry") or {}
        return {"kind": kind, "detail": detail or "",
                "unit_id": entry.get("id"), "program": entry.get("program") or "",
                "day": (entry.get("first_seen") or "")[:10] or None,
                "source": entry.get("source"), "count": entry.get("count"),
                "group": None}
    blocker = record.get("blocker")
    if blocker and ": " in blocker:
        kind, detail = blocker.split(": ", 1)
        return {"kind": kind, "detail": detail,
                "unit_id": record.get("case_id"), "program": record.get("program") or "",
                "day": None, "source": record.get("status"), "count": None,
                "group": record.get("split_group") or record.get("family")}
    return None


def draw_refusals(draw_rows: Iterable[Dict[str, Any]],
                  replay_rows: Iterable[Dict[str, Any]],
                  *, status: Optional[str] = None,
                  programs: Optional[Dict[Any, str]] = None) -> Dict[str, Any]:
    """Rung S0b's population: an eval-2 draw joined to its replay verdict.

    THIS JOIN IS THE WHOLE COMMAND, and it is not optional, because neither side
    carries what the other has. `eval2_rows.row_for` writes `status`, `family`
    and `split_group` — which draws are correct-but-fallback, and how they
    cluster — and no refusal at all. `refusals.on_policy` writes the refusal
    (`blocker`, which is `"<kind>: <detail>"`) keyed `(case_id, sample)`, and
    none of the population labels. `eval2_rows.rows` already joins them on
    exactly this key to decide `native`; this joins them again to ask *which
    refusal*.

    `programs` is optional and maps a draw to the program text, for the
    `mentions_own_package` column only — never for a bucket.

    Returns the joined records and, separately, the counts that must not be read
    as zeros: draws whose replay row is absent, and draws whose replay recorded
    no refusal. A correct-but-fallback draw with no refusal on record is a hole
    in the evidence, not a family with no mass.
    """
    index = {}
    for row in replay_rows:
        index[(row.get("case_id"), row.get("sample"))] = row
    out: List[Dict[str, Any]] = []
    considered = 0
    unmatched = 0
    without_refusal = 0
    for row in draw_rows:
        if status is not None and row.get("status") != status:
            continue
        considered += 1
        # `corpus_id`, not `case_id`: `eval2_rows.row_for` writes `case_id` as
        # the case's SOURCE id and keeps the attempt's own id in `corpus_id`,
        # and the replay is keyed by the attempt's. `eval2_rows.rows` joins on
        # exactly that, so this joins on exactly that. Getting it wrong is silent
        # in the worst way — every draw simply fails to match and the table comes
        # out empty — which is why the counts below are returned and printed.
        key = (row.get("corpus_id") or row.get("case_id"), row.get("draw"))
        replay = index.get(key)
        if replay is None:
            unmatched += 1
            continue
        blocker = replay.get("blocker")
        if not blocker or ": " not in blocker:
            without_refusal += 1
            continue
        kind, detail = blocker.split(": ", 1)
        out.append({"kind": kind, "detail": detail,
                    "unit_id": row.get("corpus_id") or row.get("case_id"),
                    "program": (programs or {}).get(key) or "",
                    "day": None, "source": row.get("status"),
                    "count": None,
                    "group": row.get("split_group") or row.get("family")})
    return {"records": out, "considered": considered, "unmatched": unmatched,
            "without_refusal": without_refusal}


def engine_note(engine: Dict[str, Any]) -> str:
    """One line on what the engine contributed, including when it contributed nothing."""
    if not engine.get("available"):
        return ("lypning is not importable here: the closed layer is EMPTY and every "
                "kind it would have decided falls through to the declarations or to "
                "the review queue. This table is not comparable with one built beside "
                "an engine.")
    return ("engine: %d closed kinds, stdlib of %s"
            % (engine["closed_kinds"], engine["stdlib_from"]))


def table(records: Iterable[Dict[str, Any]], *, source: str, unit: str = "entry",
          independence: str = "first-seen-day", loaded: Optional[int] = None) -> Dict[str, Any]:
    """The bucket table. Library code does not print (invariant 8)."""
    closed_kinds = closed()
    stdlib = stdlib_names()
    _check_declarations(lambda: closed_kinds)

    seen_rows = 0
    carried = 0
    fams: Dict[str, Dict[str, Any]] = {}
    for record in records:
        seen_rows += 1
        found = refusal_of(record)
        if found is None:
            continue
        carried += 1
        verdict = classify(found["kind"], found["detail"],
                           closed_kinds=closed_kinds, stdlib=stdlib)
        key = verdict["family"]
        row = fams.get(key)
        if row is None:
            row = {"family": key, "kind": found["kind"], "bucket": verdict["bucket"],
                   "basis": verdict["basis"], "evidence": verdict["evidence"],
                   "provenance": verdict.get("provenance"),
                   "example_detail": found["detail"], "units": 0, "weight": 0,
                   "mentions_own_package": 0,
                   "_programs": set(), "_days": set(), "_sources": set(),
                   "_groups": set(), "_ids": []}
            fams[key] = row
        row["units"] += 1
        row["weight"] += int(found["count"] or 1)
        if found["program"]:
            row["_programs"].add(found["program"])
            if OWN_PACKAGE in found["program"]:
                row["mentions_own_package"] += 1
        if found["day"]:
            row["_days"].add(found["day"])
        else:
            row["_days"].add("?%d" % row["units"])  # no day: its own singleton
        if found["source"]:
            row["_sources"].add(found["source"])
        if found["group"]:
            row["_groups"].add(found["group"])
        if found["unit_id"] and len(row["_ids"]) < 3:
            row["_ids"].append(found["unit_id"])

    families: List[Dict[str, Any]] = []
    for row in fams.values():
        groups = row.pop("_groups")
        days = row.pop("_days")
        independent = len(groups) if (independence == "family" and groups) else len(days)
        out = dict(row)
        out["programs"] = len(row.pop("_programs"))
        out["sources"] = len(row.pop("_sources"))
        out["example_ids"] = row.pop("_ids")
        out["days"] = len([d for d in days if not d.startswith("?")])
        out["independent"] = independent
        out["score"] = independent * out["units"]
        families.append(out)
    families.sort(key=lambda r: (BUCKETS.index(r["bucket"]), -r["units"], r["family"]))

    buckets: Dict[str, Dict[str, int]] = {}
    for name in BUCKETS:
        rows = [r for r in families if r["bucket"] == name]
        buckets[name] = {
            "units": sum(r["units"] for r in rows),
            "families": len(rows),
            "programs": sum(r["programs"] for r in rows),
            "weight": sum(r["weight"] for r in rows),
            "declared_s4": len([r for r in rows if r["provenance"] == FROM_SECTION_4]),
            "declared_new": len([r for r in rows if r["provenance"] == NEW_HERE]),
        }

    matched = set(r["family"] for r in families) | rule_matched()
    return {
        "rule": RULE,
        "source": source,
        "unit": unit,
        "independence": independence,
        "loaded": seen_rows if loaded is None else loaded,
        "refusals": carried,
        "engine": {
            "available": bool(closed_kinds),
            "closed_kinds": len(closed_kinds),
            "stdlib_from": ("%d.%d" % (sys.version_info[0], sys.version_info[1])
                            if stdlib is not None else None),
        },
        "buckets": buckets,
        "families": families,
        "undeclared": [r for r in families if r["basis"] == "undeclared"],
        "declared_unused": sorted(row[0] for row in DECLARED if row[0] not in matched),
    }


def rule_matched() -> "frozenset[str]":
    """Declarations whose subject is a repair rule, matched by the rule existing.

    Their family never has to appear in the capture corpus -- the rule is the
    proof -- but a declaration outliving its rule is exactly the orphan
    `declared_unused` exists to catch, so the match is against the rule list.
    """
    from .repair_rules import MODULES

    return frozenset(row[0] for row in DECLARED
                     if row[2] == FROM_RULE and row[0].split(": ", 1)[1] in MODULES)


def rank(result: Dict[str, Any], *, bucket: str = ENGINE_ADDRESSABLE,
         limit: int = 0) -> List[Dict[str, Any]]:
    """The build order: independent x units, descending.

    Entries alone is the wrong key and the table shows why: a family whose nine
    entries are five programs on two days is one afternoon's work, not recurring
    evidence, and the raw count cannot tell the two apart.
    """
    rows = [r for r in result["families"] if r["bucket"] == bucket]
    rows.sort(key=lambda r: (-r["score"], -r["units"], r["family"]))
    return rows[:limit] if limit else rows


def compare(result: Dict[str, Any], expected: Dict[str, int]) -> Dict[str, Any]:
    """This table against §4's four totals: agreement, or the per-bucket delta.

    A bucket §4's table did not yield is `missing`, and a comparison with any
    missing bucket **never agrees**. The earlier form skipped those buckets and
    then took `all()` over what was left, so a §4 table that had been renamed,
    reformatted or half-parsed came back as "reproduces §4 on all four buckets"
    — an agreement about a comparison that did not happen, which is worse than
    no comparison at all. An empty `expected` is the same bug at its limit and is
    now the same answer.
    """
    delta = {}
    missing = []
    for name in BUCKETS:
        if name in expected:
            delta[name] = result["buckets"][name]["units"] - expected[name]
        else:
            missing.append(name)
    agrees = (not missing) and all(v == 0 for v in delta.values())
    return {"agrees": agrees, "delta": delta, "missing": missing,
            "expected": dict(expected)}


def section4_totals(path: str) -> Dict[str, int]:
    """The four totals, parsed out of `ASSESSMENT.md` §4's own markdown table.

    The document is the fixture, so its table and this module cannot drift apart
    unnoticed — the pinning test reads the same rows a reader does.
    """
    wanted = {"self-referential": SELF_REFERENTIAL,
              "legitimate fallback": LEGITIMATE_FALLBACK,
              "engine-addressable": ENGINE_ADDRESSABLE,
              "other": OTHER}
    totals: Dict[str, int] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            # Anchored at the START of the first cell, never a substring of
            # the line. §4 writes `self-referential: this repository working on
            # itself`, so the label is the head of its own cell. Matching a
            # substring of the whole line let any other table in the document
            # whose first cell merely contained "other" supply that bucket's
            # count, and the earlier row won silently.
            head = cells[0].split(":", 1)[0].strip()
            name = wanted.get(head)
            if name is None or name in totals:
                continue
            try:
                totals[name] = int(cells[1])
            except ValueError:
                pass
    return totals


def declared_rows(result: Dict[str, Any], *, provenance: Optional[str] = None
                  ) -> List[Dict[str, Any]]:
    """Every family a declaration decided, heaviest first."""
    rows = [r for r in result["families"] if r["basis"] == "declared"]
    if provenance is not None:
        rows = [r for r in rows if r["provenance"] == provenance]
    rows.sort(key=lambda r: (-r["units"], r["family"]))
    return rows


def undeclared_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The review queue: families no layer and no declaration reaches."""
    rows = list(result["undeclared"])
    rows.sort(key=lambda r: (-r["units"], r["family"]))
    return rows


# --------------------------------------------------------------------------
# Renderers. Strings, never prints (invariant 8).
# --------------------------------------------------------------------------

def _head(result: Dict[str, Any], today: str) -> List[str]:
    return ["loaded %d rows from %s, %d carry a refusal (%s)"
            % (result["loaded"], result["source"], result["refusals"], today),
            engine_note(result["engine"]),
            "unit %s   independence %s   rule %d"
            % (result["unit"], result["independence"], result["rule"]), ""]


def report(result: Dict[str, Any], *, today: str, limit: int = 6) -> str:
    """§4's shape, regenerated, with the declaration surface shown beside it."""
    lines = _head(result, today)
    lines.append("%-22s %6s %9s %9s %9s %8s"
                 % ("bucket", "units", "families", "from-s4", "new-here", "programs"))
    for name in BUCKETS:
        row = result["buckets"][name]
        lines.append("%-22s %6d %9d %9d %9d %8d"
                     % (name, row["units"], row["families"], row["declared_s4"],
                        row["declared_new"], row["programs"]))
    lines.append("")
    for name in BUCKETS:
        rows = [r for r in result["families"] if r["bucket"] == name][:limit]
        if not rows:
            continue
        lines.append("%s — largest families" % name)
        for row in rows:
            lines.append("  %6d  %-52s %s" % (row["units"], row["family"][:52], row["basis"]))
        lines.append("")
    if result["undeclared"]:
        lines.append("%d famil%s in the review queue (`--undeclared`), %d entries"
                     % (len(result["undeclared"]),
                        "y" if len(result["undeclared"]) == 1 else "ies",
                        sum(r["units"] for r in result["undeclared"])))
    else:
        lines.append("review queue empty: every family is decided by a layer or a declaration")
    if result["declared_unused"]:
        lines.append("%d declaration(s) matched nothing here: %s"
                     % (len(result["declared_unused"]),
                        ", ".join(result["declared_unused"][:6])))
    return "\n".join(lines)


def rank_report(rows: Sequence[Dict[str, Any]], *, bucket: str, independence: str,
                limit: int = 20) -> str:
    """The build order, with every column kept apart so a reader sees what drives it."""
    lines = ["%s, ranked by independent x units (independence: %s)" % (bucket, independence),
             "a rank is a description of what blocks programs, never a promise that the",
             "construct can be served correctly — the reason column is the declaration's.",
             "",
             "%-50s %6s %6s %6s %6s %s"
             % ("family", "units", "progs", "indep", "score", "basis")]
    for row in rows[:limit] if limit else rows:
        lines.append("%-50s %6d %6d %6d %6d %s"
                     % (row["family"][:50], row["units"], row["programs"],
                        row["independent"], row["score"], row["basis"]))
    if limit and len(rows) > limit:
        lines.append("... %d more" % (len(rows) - limit))
    return "\n".join(lines)


def vector(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A descriptive family vector: stable labels and counts, never a build order."""
    order = {name: i for i, name in enumerate(BUCKETS)}
    return sorted(result["families"], key=lambda row: (order[row["bucket"]], row["family"]))


def vector_report(rows: Sequence[Dict[str, Any]], *, independence: str,
                  limit: int = 0) -> str:
    """Render family-level evidence without a steering score or priority order."""
    shown = rows[:limit] if limit else rows
    lines = ["descriptive refusal vector (independence: %s)" % independence,
             "families are grouped by reviewed bucket and sorted by name; this is not a rank",
             "or a build order.", "",
             "%-22s %-48s %6s %6s %s"
             % ("bucket", "family", "units", "indep", "basis")]
    for row in shown:
        lines.append("%-22s %-48s %6d %6d %s"
                     % (row["bucket"], row["family"][:48], row["units"],
                        row["independent"], row["basis"]))
    if limit and len(rows) > limit:
        lines.append("... %d more" % (len(rows) - limit))
    return "\n".join(lines)


def declared_report(rows: Sequence[Dict[str, Any]]) -> str:
    """Every declaration a reviewer must audit, with the programs it rules on."""
    lines = ["%-46s %-20s %5s %4s  %s"
             % ("family", "bucket", "units", "from", "why")]
    for row in rows:
        lines.append("%-46s %-20s %5d %4s  %s"
                     % (row["family"][:46], row["bucket"], row["units"],
                        row["provenance"], row["evidence"][:72]))
        if row["example_ids"]:
            lines.append("%-46s %s" % ("", " ".join(row["example_ids"])))
    return "\n".join(lines)


def undeclared_report(rows: Sequence[Dict[str, Any]]) -> str:
    """The review queue, largest first."""
    if not rows:
        return "review queue empty."
    lines = ["%d famil%s no layer and no declaration reaches, %d entries."
             % (len(rows), "y" if len(rows) == 1 else "ies",
                sum(r["units"] for r in rows)),
             "Each needs a DECLARED row or a mechanical layer. Until then they are `other`.",
             "",
             "%-52s %6s  %s" % ("family", "units", "example detail")]
    for row in rows:
        lines.append("%-52s %6d  %s"
                     % (row["family"][:52], row["units"], row["example_detail"][:64]))
    return "\n".join(lines)


def compare_report(result: Dict[str, Any], cmp: Dict[str, Any]) -> str:
    """Whether this table reproduces §4, and where it does not."""
    if cmp.get("missing"):
        return ("could NOT compare against ASSESSMENT.md §4: its table yielded no "
                "count for %s. This is a failure to read the document, not an "
                "agreement with it." % ", ".join(cmp["missing"]))
    if cmp["agrees"]:
        return "reproduces ASSESSMENT.md §4 on all four buckets."
    lines = ["does NOT reproduce ASSESSMENT.md §4; the delta is this table minus §4:"]
    for name in BUCKETS:
        if name not in cmp["delta"]:
            continue
        lines.append("  %-22s %+5d   (here %d, §4 %d)"
                     % (name, cmp["delta"][name], result["buckets"][name]["units"],
                        cmp["expected"][name]))
    lines.append("")
    lines.append("A delta is not automatically an error: §4 bucketed by judgement per kind")
    lines.append("and this table bucketed by three mechanical layers plus a declared row.")
    lines.append("Read `--declared` and `--undeclared` to see which families differ.")
    return "\n".join(lines)
