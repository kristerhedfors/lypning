"""The differential fuzzer: generate what lypning *claims*, then disagree about it.

The invariant this module exists to hold: **the corpus is a sample, not a
specification.** :mod:`lypning.conformance` grades the programs agents happened
to type, so "MISMATCH 0 over the programs we harvested" is evidence about
lypning's surface only in proportion to how much of that surface those programs
touch — a fraction nobody has measured and which is certainly not 1. The corpus
is the right thing to rank a build order by and the wrong thing to establish
correctness with, because it only ever covers ground someone happened to walk
over. This walks the rest.

So programs are *generated*, from lypning's own declared vocabulary: the
``BUILTINS`` table in ``builtins.rs`` and the seven method tables in
``methods.rs``. Those tables are not documentation — ``route.rs`` reads them to
decide statically whether lypning can take a program — so every probe is
something lypning asserts it handles.

**The oracle is exact, which is what makes this cheap.** lypning says for itself
whether it claimed a program (``docs/LYPNING.md`` §5, and invariant 2 in
``CLAUDE.md``):

  exit ``90`` + the contract line   REFUSED. Not a finding, ever. The generator
                                    wandered outside the subset and the design
                                    worked. Counted, and its ``kind`` histogram
                                    is coverage information like ``--plan``.
  identical stdout and exit code    AGREED.
  anything else                     a COUNTEREXAMPLE — lypning said it could and
                                    then disagreed. No judgement call is
                                    involved, which is what separates this from
                                    a fuzzer whose output needs triage.

Four things this file is careful about, each of which a naive port loses:

**The generator is typed.** It builds expressions that are valid by
construction from a grammar of the subset rather than emitting random text and
discarding the syntax errors. ``"abc".zfill(3)`` needs to know that ``zfill``
wants an int; without that, nine probes in ten are a ``TypeError`` and the run
spends its whole budget confirming that both interpreters can raise one, and the
deep expressions where the interesting disagreements live are never reached.

**One probe is exactly one output line.** Every expression is wrapped in
``print(repr(...))`` under a chain of named ``except`` clauses, so a probe emits
one line whatever it does: ``repr`` never emits a newline, and an exception
becomes a line naming its class rather than a traceback nobody can compare. The
chain is spelled out by name rather than ``except BaseException as e:
print(type(e).__name__)`` because ``type()`` of an exception is itself
``unsupported`` — the obvious wrapper makes lypning refuse every program where
anything raises, and the entire "both raise, but not the same thing" class goes
invisible while the run still reports a large number.

**The program goes in a file, never in argv.** A multi-probe program is
thousands of lines and past ``ARG_MAX``; upstream's ``-c`` version failed to
spawn for *both* engines, compared two empty stdouts, found them equal, and
reported twelve thousand passing probes having run none of them. A harness that
cannot run the program must not report a clean bill of health, so
:attr:`FuzzReport.ok` is false for a run that did not happen and a spawn failure
is itself a counterexample.

**Nothing is generated whose answer CPython does not fix.** No clock, no PRNG,
no ``id()``/``hash()``, no iteration over a set, no file or network I/O, no
``input``. A fuzzer that reports unspecified behaviour trains its reader to
skim, and the one real finding then goes past unread.

Reproducibility is not decoration. ``seed`` fixes the whole run —
:func:`generate` is a pure function of the :class:`random.Random` it is handed —
and every counterexample carries the seed of the program that produced it, so
``generate(random.Random(seed))`` reconstructs it byte for byte. A fuzzer whose
failures cannot be replayed is a random number generator.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import engines as eng
from .engines import CPYTHON, LYPNING

DEFAULT_ITERATIONS = 500
DEFAULT_TIMEOUT = 30.0

#: Probes per generated program. More than one so a spawn pair — CPython costs
#: ~20 ms of it — buys several comparisons; few enough that a refusal, which is
#: whole-program and discards the batch, throws little away.
PROBES_PER_PROGRAM = (1, 4)

#: How many distinct findings get the shrinker. Past this they are reported
#: unshrunk and said to be, because a shrink is a few dozen spawn pairs and a
#: run that has found fifty distinct bugs has already made its point.
SHRINK_LIMIT = 25

#: Spawn pairs one shrink may spend. The bound matters: a shrinker with no
#: budget on a deeply nested counterexample is how a fuzz run turns into an
#: afternoon.
SHRINK_BUDGET = 120

# Evidence is clipped. A single divergence can be a megabyte of stdout, and a
# report holding a hundred of those is a memory hazard rather than a report.
_CLIP = 4096
_STDERR_CLIP = 400


# --- what a finding is -------------------------------------------------------

#: stdout differs at exit 0 on both sides. The common shape, and always a bug.
OUTPUT = "output"
#: The engine exited non-zero where CPython exited 0, and it was not a refusal.
CRASH = "crash"
#: Exit 90 that is not a refusal: no contract line on stderr, or output on
#: stdout anyway. Invariant 2 of ``CLAUDE.md``, which has only ever broken
#: silently — a parser change that turns a refusal into a traceback still
#: compiles and still passes ``--version``.
CONTRACT = "contract"
#: The comparison did not happen: a timeout, a failed spawn, or a CPython that
#: could not run the generated program at all (which is a bug in the generator,
#: and must be as loud as a bug in the engine).
HARNESS = "harness"


# --- the vocabulary ----------------------------------------------------------
#
# Every name below appears in the Rust core's own tables. The TYPES are this
# file's addition — the Rust does not carry them, and the generator cannot work
# without them. A transcription rots, and it rots silently and in one direction:
# a method added to lypning and not here is surface that never gets generated,
# so the run still reports a large probe count and no findings and reads as
# evidence that the new method is correct. tests/test_fuzz.py parses the Rust
# and fails on the drift.

STR_METHODS: Dict[str, Tuple[str, ...]] = {
    "capitalize": (), "casefold": (), "count": ("str",), "encode": (),
    "endswith": ("str",), "find": ("str",), "format": (), "index": ("str",),
    "isalnum": (), "isalpha": (), "isdigit": (), "islower": (), "isnumeric": (),
    "isspace": (), "isupper": (), "join": ("strlist",), "ljust": ("smallint",),
    "lower": (), "lstrip": (), "partition": ("str",), "removeprefix": ("str",),
    "removesuffix": ("str",), "replace": ("str", "str"), "rfind": ("str",),
    "rindex": ("str",), "rjust": ("smallint",), "rpartition": ("str",),
    "rsplit": (), "rstrip": (), "split": (), "splitlines": (),
    "startswith": ("str",), "strip": (), "swapcase": (), "title": (),
    "upper": (), "zfill": ("smallint",),
}

LIST_METHODS: Dict[str, Tuple[str, ...]] = {
    "append": ("int",), "clear": (), "copy": (), "count": ("int",),
    "extend": ("intlist",), "index": ("int",), "insert": ("smallint", "int"),
    "pop": (), "remove": ("int",), "reverse": (), "sort": (),
}

DICT_METHODS: Dict[str, Tuple[str, ...]] = {
    "clear": (), "copy": (), "get": ("str",), "items": (), "keys": (),
    "pop": ("str",), "popitem": (), "setdefault": ("str",),
    "update": ("dict",), "values": (),
}

BYTES_METHODS: Dict[str, Tuple[str, ...]] = {
    "decode": (), "endswith": ("bytes",), "find": ("bytes",), "hex": (),
    "join": ("byteslist",), "lower": (), "lstrip": (),
    "replace": ("bytes", "bytes"), "rsplit": (), "rstrip": (), "split": (),
    "startswith": ("bytes",), "strip": (), "upper": (),
}

#: Tuples reach the grammar without a tuple literal anywhere — ``str.partition``
#: and ``divmod`` both return one — which is how the upstream pass found
#: ``"a-b".partition("-").count("x")`` raising AttributeError at exit 1 where
#: CPython answers 0.
TUPLE_METHODS: Dict[str, Tuple[str, ...]] = {"count": ("int",), "index": ("int",)}

#: Deliberately a SUBSET of lypning's set table. ``docs/LYPNING.md`` §3 refuses
#: anything that would expose set iteration order, so a probe that prints a set
#: is refused by construction and only the order-free operations are worth
#: generating: the results here are consumed by ``len``, ``sorted`` and the
#: comparison operators, never printed.
SET_METHODS: Dict[str, Tuple[str, ...]] = {
    "difference": ("set",), "intersection": ("set",), "issubset": ("set",),
    "issuperset": ("set",), "symmetric_difference": ("set",), "union": ("set",),
}

#: Split by RETURN type, which the table does not carry. Four of them answer a
#: set — usable only under ``len``/``sorted`` — and two answer a bool. Feeding a
#: predicate to ``len`` is a guaranteed TypeError that both engines report
#: identically, which is budget spent proving nothing.
_SET_ALGEBRA = ("difference", "intersection", "symmetric_difference", "union")
_SET_PREDICATES = ("issubset", "issuperset")

#: The builtins the generator emits. A strict subset of the Rust ``BUILTINS``
#: table, and strict on purpose: ``input`` and ``open`` are I/O, and ``type`` of
#: anything interesting is refused. ``print`` and ``repr`` are the wrapper's.
GENERATED_BUILTINS = frozenset((
    "abs", "all", "any", "bin", "bool", "bytes", "chr", "dict", "divmod",
    "enumerate", "filter", "float", "format", "hex", "int", "isinstance",
    "iter", "len", "list", "map", "max", "min", "next", "oct", "ord", "print",
    "range", "repr", "reversed", "round", "set", "sorted", "str", "sum",
    "tuple", "zip",
))

#: The exception classes a probe reports by name, leaves before their bases so
#: the first matching clause is the precise one. ``repr(e)`` would carry the
#: message too, but two interpreters legitimately word their messages
#: differently and every probe that raised would then read as a divergence; the
#: class is the part that is contractual.
REPORTED_EXCEPTIONS: Tuple[str, ...] = (
    "ZeroDivisionError", "OverflowError", "UnicodeDecodeError", "IndexError",
    "KeyError", "UnboundLocalError", "NotImplementedError", "StopIteration",
    "AssertionError", "AttributeError", "TypeError", "ValueError", "NameError",
    "ArithmeticError", "LookupError", "RuntimeError", "OSError",
)

#: The types the grammar can produce a value of. The four after ``bool`` are
#: internal shapes an argument slot asks for, never a probe's own type.
TYPES: Tuple[str, ...] = ("int", "float", "str", "bytes", "list", "dict", "tuple", "bool")


# --- the generator -----------------------------------------------------------


class _Grammar:
    """A typed expression generator over one :class:`random.Random`.

    Every method returns *source text* of the requested type. Nothing here
    executes anything, so a generator run is pure and cheap and the same seed
    reconstructs the same programs on any machine.
    """

    #: Depth cap. Past it only literals, so an expression always terminates and
    #: a program stays a size two interpreters will both parse quickly.
    MAX_DEPTH = 3

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    # -- primitives --

    def pick(self, xs: Sequence[str]) -> str:
        return xs[self.rng.randrange(len(xs))]

    def chance(self, p: float) -> bool:
        return self.rng.random() < p

    def num(self, lo: int, hi: int) -> int:
        return self.rng.randint(lo, hi)

    # -- literals --

    def int_lit(self) -> str:
        """Well inside i64, mostly.

        lypning is *right* to refuse a bignum (``docs/LYPNING.md`` §3) and a
        refusal teaches nothing, so the budget is not spent on them — but the
        boundary is exactly where a saturating cast hides, so a small share of
        literals sit on it deliberately. ``-9223372036854775808`` is not among
        them: that is unary minus applied to a literal past i64, so it is
        refused at lex time and takes the whole program with it. The bound is
        reached from the other side.
        """
        if self.chance(0.05):
            return self.pick(("2**62", "-2**62", "9223372036854775807",
                              "(-9223372036854775807 - 1)", "2**31", "-2**31"))
        if self.chance(0.25):
            return str(self.num(-3, 3))
        if self.chance(0.5):
            return str(self.num(-1000, 1000))
        return str(self.num(-(2 ** 31), 2 ** 31))

    def float_lit(self) -> str:
        """Built to walk the notation boundaries CPython switches at.

        Float repr is where a subset runtime is most likely to be quietly wrong
        — the switch to scientific notation at ``decpt <= -4 || decpt > 16`` is
        a rule you have to have read, not one that falls out of the host
        language's formatter — so the literal set is chosen to sit on every
        boundary rather than to look varied.
        """
        if self.chance(0.2):
            return repr(self.num(-1000000, 1000000) / 100.0)
        return self.pick((
            "0.0", "-0.0", "1.0", "10.0", "99.0", "100.0", "1000.0", "12345.0",
            "0.5", "-0.5", "1.5", "2.675", "0.1", "0.2", "1/3", "2/3",
            "1e15", "1e16", "1e17", "1e22", "1e-4", "1e-5", "1e100", "5e-324",
            "1.7976931348623157e308", "(0.1+0.2)", "float('inf')",
            "-float('inf')", "float('nan')",
        ))

    def str_lit(self) -> str:
        if self.chance(0.08):
            # The non-ASCII whitelist in `repr` is a refusal boundary worth
            # probing, not a place to look for output bugs. A small share.
            return self.pick(('"räksmörgås"', '"Ärlig"', '"åäö"', '"naïve"', '"日本"'))
        return self.pick((
            '""', '"a"', '"abc"', '"Hello World"', '" padded "', '"a b  c"',
            r'"\t\n"', '"AbC"', '"123"', '"12.5"', '"a,b,,c"', '"-"', '("x" * 3)',
            '"MiXeD cAsE"', '"...."', '"0"', r'"a\\b"', '"it\'s"', r'"line1\nline2"',
        ))

    def bytes_lit(self) -> str:
        return self.pick((r'b""', r'b"a"', r'b"abc"', r'b"\x00\xff"', r'b" x "', r'b"a,b"'))

    def int_list_lit(self) -> str:
        return "[" + ", ".join(self.int_lit() for _ in range(self.num(0, 5))) + "]"

    def str_list_lit(self) -> str:
        return "[" + ", ".join(self.str_lit() for _ in range(self.num(0, 4))) + "]"

    def bytes_list_lit(self) -> str:
        return "[" + ", ".join(self.bytes_lit() for _ in range(self.num(0, 3))) + "]"

    def byte_list_lit(self) -> str:
        """A list of ints in ``range(256)`` — the only thing ``bytes()`` takes."""
        return "[" + ", ".join(str(self.num(0, 255)) for _ in range(self.num(0, 4))) + "]"

    def list_lit(self, d: int) -> str:
        items = [self.gen("int" if self.chance(0.7) else self.pick(TYPES), d + 1)
                 for _ in range(self.num(0, 4))]
        return "[" + ", ".join(items) + "]"

    def dict_lit(self, d: int) -> str:
        parts = ["%s: %s" % (self.str_lit(),
                             self.gen("int" if self.chance(0.6) else self.pick(TYPES), d + 1))
                 for _ in range(self.num(0, 3))]
        return "{" + ", ".join(parts) + "}"

    def tuple_lit(self, d: int) -> str:
        n = self.num(1, 3)
        items = [self.gen("int" if self.chance(0.6) else self.pick(TYPES), d + 1)
                 for _ in range(n)]
        return "(" + ", ".join(items) + ("," if n == 1 else "") + ")"

    def set_lit(self) -> str:
        return "{" + ", ".join(self.int_lit() for _ in range(self.num(1, 4))) + "}"

    # -- the type-directed entry point --

    def gen(self, type_: str, d: int = 0) -> str:
        """An expression of *approximately* ``type_``.

        Approximately, and deliberately so. ``str.encode`` sits in
        ``STR_METHODS`` and answers bytes, ``str.split`` answers a list — so an
        expression the grammar labels ``str`` is sometimes not one, and the next
        layer up puts a str method on it. Tightening that to exact types would
        cost the two findings that came out of it: ``"0".encode().strip()`` is
        the receiver that showed ``bytes.isdigit`` raising AttributeError where
        CPython answers ``True``. The slop is bounded — a mistyped composition
        is a TypeError both engines agree on — and it buys the one thing a
        grammar cannot generate on purpose, which is a combination nobody
        thought of.
        """
        if d > self.MAX_DEPTH:
            return self.atom(type_)
        if type_ == "smallint":
            return str(self.num(0, 8))
        if type_ == "intlist":
            return self.int_list_lit()
        if type_ == "strlist":
            return self.str_list_lit()
        if type_ == "byteslist":
            return self.bytes_list_lit()
        if type_ == "set":
            return self.set_lit()
        if type_ == "int":
            return self.atom("int") if self.chance(0.55) else self.int_expr(d)
        if type_ == "float":
            return self.atom("float") if self.chance(0.55) else self.float_expr(d)
        if type_ == "str":
            return self.atom("str") if self.chance(0.5) else self.str_expr(d)
        if type_ == "bytes":
            return self.atom("bytes") if self.chance(0.6) else self.bytes_expr(d)
        if type_ == "list":
            return self.atom("list") if self.chance(0.5) else self.list_expr(d)
        if type_ == "dict":
            return self.atom("dict") if self.chance(0.7) else self.dict_expr(d)
        if type_ == "tuple":
            return self.atom("tuple") if self.chance(0.4) else self.tuple_expr(d)
        if type_ == "bool":
            return self.bool_expr(d)
        return self.gen(self.pick(TYPES), d)

    def atom(self, type_: str) -> str:
        if type_ == "int":
            return self.int_lit()
        if type_ == "float":
            return self.float_lit()
        if type_ == "str":
            return self.str_lit()
        if type_ == "bytes":
            return self.bytes_lit()
        if type_ == "list":
            return self.list_lit(self.MAX_DEPTH)
        if type_ == "dict":
            return self.dict_lit(self.MAX_DEPTH)
        if type_ == "tuple":
            return self.tuple_lit(self.MAX_DEPTH)
        if type_ == "bool":
            return self.pick(("True", "False"))
        if type_ == "smallint":
            return str(self.num(0, 8))
        if type_ == "set":
            return self.set_lit()
        return self.int_lit()

    def method(self, recv: str, table: Dict[str, Tuple[str, ...]], d: int) -> str:
        name = self.pick(tuple(table))
        args = ", ".join(self.gen(t, d + 1) for t in table[name])
        return "%s.%s(%s)" % (recv, name, args)

    # -- per-type expression forms --

    def int_expr(self, d: int) -> str:
        which = self.num(0, 11)
        if which == 0:
            op = self.pick(("+", "-", "*", "//", "%", "&", "|", "^", "<<", ">>"))
            return "(%s %s %s)" % (self.gen("int", d + 1), op, self.gen("int", d + 1))
        if which == 1:
            return "abs(%s)" % self.gen("int", d + 1)
        if which == 2:
            return "len(%s)" % self.gen(self.pick(("str", "list", "dict", "bytes", "tuple")), d + 1)
        if which == 3:
            return "int(%s)" % self.gen(self.pick(("float", "bool")), d + 1)
        if which == 4:
            return "int(%s)" % self.pick(('"42"', '"-7"', '"0"', '" 12 "',
                                          '"ff", 16', '"0b101", 2', '"z"'))
        if which == 5:
            return "round(%s)" % self.gen("float", d + 1)
        if which == 6:
            return "sum(%s)" % self.int_list_lit()
        if which == 7:
            return "ord(%s)" % self.pick(('"a"', '"Z"', '"0"', '" "'))
        if which == 8:
            return "%s(%s or [0])" % (self.pick(("min", "max")), self.int_list_lit())
        if which == 9:
            return "divmod(%s, %s)[%d]" % (self.gen("int", d + 1),
                                           self.pick(("3", "-3", "7", "10")), self.num(0, 1))
        if which == 10:
            return "-%s" % self.gen("int", d + 1)
        return "%s.%s(%s)" % (self.gen("str", d + 1),
                              self.pick(("find", "count", "rfind")), self.str_lit())

    def float_expr(self, d: int) -> str:
        which = self.num(0, 7)
        if which == 0:
            return "(%s %s %s)" % (self.gen("float", d + 1), self.pick(("+", "-", "*")),
                                   self.gen("float", d + 1))
        if which == 1:
            return "(%s / %s)" % (self.gen("int", d + 1), self.pick(("3", "7", "-3", "2", "10")))
        if which == 2:
            return "round(%s, %d)" % (self.gen("float", d + 1), self.num(0, 6))
        if which == 3:
            return "abs(%s)" % self.gen("float", d + 1)
        if which == 4:
            return "float(%s)" % self.pick(('"1.5"', '"-0.25"', '"1e10"', '"inf"',
                                            '"nan"', '"  2.5 "', '"x"'))
        if which == 5:
            return "float(%s)" % self.gen("int", d + 1)
        if which == 6:
            return "(%s ** %s)" % (self.gen("float", d + 1), self.pick(("2", "0.5", "-1", "0")))
        return "(%s %s %s)" % (self.gen("float", d + 1), self.pick(("//", "%")),
                               self.pick(("1.0", "2.5", "-3.0")))

    def str_expr(self, d: int) -> str:
        which = self.num(0, 12)
        if which == 0:
            return self.method(self.gen("str", d + 1), STR_METHODS, d)
        if which == 1:
            return "(%s + %s)" % (self.gen("str", d + 1), self.gen("str", d + 1))
        if which == 2:
            return "(%s * %d)" % (self.gen("str", d + 1), self.num(0, 3))
        if which == 3:
            return "str(%s)" % self.gen(
                self.pick(("int", "float", "bool", "list", "dict", "bytes", "tuple")), d + 1)
        if which == 4:
            return "repr(%s)" % self.gen(self.pick(("int", "float", "str", "bool")), d + 1)
        if which == 5:
            return "%s[%s]" % (self.gen("str", d + 1), self.slice())
        if which == 6:
            return "%s(%s)" % (self.pick(("hex", "oct", "bin")), self.gen("int", d + 1))
        if which == 7:
            return "chr(%s)" % self.pick(("65", "97", "48", "32", "955", "10"))
        if which == 8:
            spec = self.pick(('""', '"d"', '".2f"', '">8"', '"08.3f"', '"+"',
                              '"x"', '"e"', '"g"', '","', '"_"', '".0f"'))
            return "format(%s, %s)" % (self.gen(self.pick(("int", "float")), d + 1), spec)
        if which == 9:
            return '"{}".format(%s)' % self.gen(self.pick(("int", "float", "str")), d + 1)
        if which == 10:
            # Numeric interpolations only. A string subexpression would put a
            # double quote inside the braces — `f"{"abc"}"` — which is a
            # SyntaxError before CPython 3.12 (PEP 701), and a syntax error
            # fails the whole PROGRAM rather than one probe.
            inner = self._quote_free(self.gen(self.pick(("int", "float")), d + 1))
            if self.chance(0.5):
                return 'f"{%s}"' % inner
            return 'f"{%s:%s}"' % (inner, self.pick((".2f", "5d", "<6", "^7", "+.1f", "x", "e")))
        if which == 11:
            return "%s.decode()" % self.gen("bytes", d + 1)
        pat = self.pick(('"%d"', '"%s"', '"%.2f"', '"%5.2f"', '"%x"'))
        arg = "int" if pat in ('"%d"', '"%x"') else self.pick(("int", "float", "str"))
        return "(%s %% %s)" % (pat, self.gen(arg, d + 1))

    def bytes_expr(self, d: int) -> str:
        which = self.num(0, 4)
        if which == 0:
            return self.method(self.gen("bytes", d + 1), BYTES_METHODS, d)
        if which == 1:
            return "(%s + %s)" % (self.gen("bytes", d + 1), self.gen("bytes", d + 1))
        if which == 2:
            return "%s.encode()" % self.gen("str", d + 1)
        if which == 3:
            return "bytes(%s)" % self.byte_list_lit()
        return "%s[%s]" % (self.gen("bytes", d + 1), self.slice())

    def list_expr(self, d: int) -> str:
        which = self.num(0, 15)
        if which == 0:
            # The mutators return None, so the probe is wrapped to show the
            # LIST, which is where a divergence would be.
            return "(lambda _l: (%s, _l)[1])(%s)" % (
                self.method("_l", LIST_METHODS, d), self.int_list_lit())
        if which == 1:
            return "sorted(%s)" % self.pick((self.int_list_lit(), self.str_list_lit()))
        if which == 2:
            return "sorted(%s, key=len)" % self.str_list_lit()
        if which == 3:
            return "list(%s)" % self.pick((
                "range(%d)" % self.num(0, 6),
                "range(%d, %d)" % (self.num(-3, 3), self.num(3, 9)),
                "range(%d, %d, -%d)" % (self.num(0, 9), self.num(-3, 3), self.num(1, 3)),
            ))
        if which == 4:
            return "list(%s)" % self.gen(self.pick(("str", "dict", "bytes", "tuple")), d + 1)
        if which == 5:
            return "%s.split(%s)" % (self.gen("str", d + 1),
                                     "" if self.chance(0.5) else self.str_lit())
        if which == 6:
            return "%s[%s]" % (self.gen("list", d + 1), self.slice())
        if which == 7:
            return "(%s + %s)" % (self.gen("list", d + 1), self.int_list_lit())
        if which == 8:
            return "[x %s for x in %s]" % (self.pick(("* 2", "+ 1", "** 2", "% 3")),
                                           self.int_list_lit())
        if which == 9:
            return "[x for x in %s if x %s %d]" % (self.int_list_lit(),
                                                   self.pick((">", "<", ">=", "==")),
                                                   self.num(-3, 3))
        if which == 10:
            tail = "" if self.chance(0.5) else ", %d" % self.num(0, 3)
            return "list(enumerate(%s%s))" % (self.str_list_lit(), tail)
        if which == 11:
            return "list(zip(%s, %s))" % (self.int_list_lit(), self.str_list_lit())
        if which == 12:
            return "list(%s(%s, %s))" % (self.pick(("map", "filter")),
                                         self.pick(("abs", "bool", "str")), self.int_list_lit())
        if which == 13:
            return "list(reversed(%s))" % self.int_list_lit()
        if which == 14:
            return "sorted(%s, reverse=True)" % self.int_list_lit()
        return "sorted(%s)" % self.set_lit()

    def dict_expr(self, d: int) -> str:
        which = self.num(0, 4)
        if which == 0:
            return "(lambda _d: (%s, _d)[1])(%s)" % (
                self.method("_d", DICT_METHODS, d), self.dict_lit(d + 1))
        if which == 1:
            return "dict(%s)" % self.pick(("", "a=%s" % self.int_lit(),
                                           "a=%s, b=%s" % (self.int_lit(), self.int_lit())))
        if which == 2:
            return "dict(zip(%s, %s))" % (self.str_list_lit(), self.int_list_lit())
        if which == 3:
            return "{k: v for k, v in zip(%s, %s)}" % (self.str_list_lit(), self.int_list_lit())
        return self.dict_lit(d + 1)

    def tuple_expr(self, d: int) -> str:
        which = self.num(0, 4)
        if which == 0:
            return "%s.%s(%s)" % (self.gen("str", d + 1),
                                  self.pick(("partition", "rpartition")), self.str_lit())
        if which == 1:
            return "divmod(%s, %s)" % (self.gen("int", d + 1),
                                       self.pick(("3", "-3", "7", "10", "0")))
        if which == 2:
            return "tuple(%s)" % self.gen(self.pick(("list", "str", "dict")), d + 1)
        if which == 3:
            return "next(iter(%s))" % ("list(enumerate(%s))" % self.str_list_lit())
        return self.tuple_lit(d + 1)

    def bool_expr(self, d: int) -> str:
        which = self.num(0, 12)
        if which == 0:
            return "(%s %s %s)" % (self.gen("int", d + 1),
                                   self.pick(("<", "<=", ">", ">=", "==", "!=")),
                                   self.gen("int", d + 1))
        if which == 1:
            return "(%s %s %s)" % (self.gen("str", d + 1), self.pick(("<", ">", "==", "!=")),
                                   self.gen("str", d + 1))
        if which == 2:
            return "(%s %s %s)" % (self.gen("float", d + 1), self.pick(("<", ">", "==", "!=")),
                                   self.gen("float", d + 1))
        if which == 3:
            return "(%s == %s)" % (self.gen("int", d + 1), self.gen("float", d + 1))
        if which == 4:
            return "bool(%s)" % self.gen(self.pick(TYPES), d + 1)
        if which == 5:
            return "(%s in %s)" % (self.gen("str", d + 1), self.gen("str", d + 1))
        if which == 6:
            return "(%s in %s)" % (self.gen("int", d + 1), self.int_list_lit())
        if which == 7:
            return "%s(%s)" % (self.pick(("all", "any")), self.int_list_lit())
        if which == 8:
            return "isinstance(%s, %s)" % (
                self.gen(self.pick(TYPES), d + 1),
                self.pick(("int", "float", "str", "bytes", "list", "dict", "bool", "tuple")))
        if which == 9:
            return "(not %s)" % self.gen(self.pick(TYPES), d + 1)
        if which == 10:
            return "(%s %s %s)" % (self.gen("bool", d + 1), self.pick(("and", "or")),
                                   self.gen("bool", d + 1))
        if which == 11:
            # Sets, never their order: lypning refuses the order, not the set.
            return "(%s %s %s)" % (self.set_lit(), self.pick(("<=", ">=", "==", "!=")),
                                   self.set_lit())
        if self.chance(0.4):
            return "%s.%s(%s)" % (self.set_lit(), self.pick(_SET_PREDICATES), self.set_lit())
        return "(len(%s.%s(%s)) %s %d)" % (self.set_lit(), self.pick(_SET_ALGEBRA),
                                           self.set_lit(), self.pick(("==", ">")),
                                           self.num(0, 3))

    # -- helpers --

    def _quote_free(self, e: str) -> str:
        """An expression safe to interpolate into a double-quoted f-string.

        Before CPython 3.12 the enclosing quote cannot be reused inside the
        braces, so a subexpression carrying a string literal — ``int("ff", 16)``
        reaches the numeric slots — is a SyntaxError. That fails the whole file,
        so one bad probe costs every comparison in the program rather than one.
        """
        return self.int_lit() if ('"' in e or "'" in e) else e

    def slice(self) -> str:
        def part() -> str:
            return "" if self.chance(0.35) else str(self.num(-4, 5))
        if self.chance(0.25):
            return str(self.num(-3, 3))
        if self.chance(0.25):
            return "%s:%s:%s" % (part(), part(), self.pick(("1", "2", "-1", "-2", "3")))
        return "%s:%s" % (part(), part())

    def probe(self) -> str:
        """One expression to compare. ``bool`` twice: it is the cheapest way to
        reach the comparison operators, which no other slot generates."""
        return self.gen(self.pick(TYPES + ("bool", "bool")), 0)


def build_program(expressions: Sequence[str]) -> str:
    """Wrap expressions as one-line probes. See the module docstring for why the
    ``except`` chain is spelled out by name."""
    out: List[str] = []
    for e in expressions:
        out.append("try:")
        out.append("    print(repr(%s))" % e)
        for name in REPORTED_EXCEPTIONS:
            out.append("except %s:" % name)
            out.append('    print("! %s")' % name)
        out.append("except BaseException:")
        out.append('    print("! other")')
    return "\n".join(out) + "\n"


#: The probe line, read back. Greedy up to the trailing ``))``, so an expression
#: that is itself parenthesised survives the round trip.
_PROBE_RE = re.compile(r"^    print\(repr\((.*)\)\)$", re.M)


def expressions(program: str) -> List[str]:
    """The probe expressions in a program, in order. The inverse of
    :func:`build_program` for anything :func:`build_program` produced."""
    return _PROBE_RE.findall(program)


def generate(rng: random.Random, probes: Optional[int] = None) -> str:
    """One random program from the grammar. Pure: same ``rng`` state, same text."""
    g = _Grammar(rng)
    n = probes if probes is not None else g.num(*PROBES_PER_PROGRAM)
    return build_program([g.probe() for _ in range(max(1, n))])


# --- shrinking ---------------------------------------------------------------


def _receiver_atoms() -> Dict[str, Tuple[str, ...]]:
    """method name -> the smallest literal of every type that has that method."""
    out: Dict[str, Tuple[str, ...]] = {}
    for table, atoms in ((STR_METHODS, ('""', '"a"')),
                         (LIST_METHODS, ("[]", "[0]")),
                         (DICT_METHODS, ("{}",)),
                         (BYTES_METHODS, ('b""', 'b"a"')),
                         (TUPLE_METHODS, ("()",)),
                         (SET_METHODS, ("set()",))):
        for name in table:
            out[name] = out.get(name, ()) + atoms
    return out


_RECEIVER_ATOMS = _receiver_atoms()


def _parses(e: str) -> bool:
    """Is this candidate a Python expression at all?

    The structural rules below are text surgery and text surgery produces
    garbage: splitting ``(("AbC".swapcase() + "x") * 2)`` at its ``+`` leaves
    ``(("AbC".swapcase(``. The engine-backed predicate would reject that one
    spawn pair later — CPython cannot parse it either, so the candidate "fails"
    for the wrong reason — but a shrinker that proposes garbage spends its
    budget on it. :func:`compile` is the exact question, and it is free.
    """
    try:
        compile(e, "<candidate>", "eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return False
    return True


def candidates(e: str) -> List[str]:
    """Structural simplifications of one expression, smallest first.

    Never a rewrite that changes the *kind* of thing being computed: each
    candidate is a subexpression of the original or the original with one layer
    peeled, so the shrinker walks towards the leaf that actually broke rather
    than towards a different bug.
    """
    out: List[str] = []
    if e.startswith("(") and e.endswith(")"):
        out.append(e[1:-1])
    # Any balanced parenthesised subexpression, standing alone. A comma inside
    # means it is an argument list or a tuple, and its pieces are not
    # expressions of the same type.
    for i, ch in enumerate(e):
        if ch != "(":
            continue
        depth = 0
        for j in range(i, len(e)):
            if e[j] == "(":
                depth += 1
            elif e[j] == ")":
                depth -= 1
                if depth == 0:
                    inner = e[i + 1:j]
                    if inner and "," not in inner:
                        out.append(inner)
                    break
    trail = re.match(r"^(.*?)(\.\w+\([^()]*\)|\[[^\]]*\])$", e)
    if trail and trail.group(1):
        out.append(trail.group(1))
        # Replace the receiver with the smallest literal that could carry the
        # same method. Without this a finding stays wearing whatever expression
        # happened to produce its receiver — `("%5.2f" % 0.1).partition("")` and
        # `"räksmörgås".rpartition("")` are one bug reported twice — and the
        # report grows a line per nest instead of a line per bug.
        #
        # Driven by the method tables, not by a list of convenient literals. A
        # free choice of receiver is how a shrinker changes the subject: offered
        # `b"a"` for a receiver that was a str, it reduced a `partition("")`
        # finding into an unrelated `bytes.isdigit` one and reported the second
        # as if it were the first. Only receivers whose table actually holds
        # this method are proposed, and the predicate still has to accept it.
        name = re.match(r"\.(\w+)\(", trail.group(2))
        for atom in _RECEIVER_ATOMS.get(name.group(1) if name else "", ()):
            out.append(atom + trail.group(2))
    if e.startswith("-") or e.startswith("not "):
        out.append(re.sub(r"^(-|not )", "", e))
    binop = re.match(r"^(.+?) (//|\*\*|<<|>>|and|or|in|[-+*/%<>=!&|^]+) (.+)$", e)
    if binop:
        out.append(binop.group(1))
        out.append(binop.group(3))
    seen: Dict[str, None] = {}
    for c in out:
        c = c.strip()
        if c and c != e and len(c) < len(e) and _parses(c):
            seen.setdefault(c, None)
    return sorted(seen, key=len)


def shrink(program: str, still_fails: Callable[[str], bool], budget: int = SHRINK_BUDGET) -> str:
    """Reduce a counterexample to the smallest program that still disagrees.

    A 200-token counterexample nobody can read gets ignored, which makes
    shrinking the difference between a fuzzer that is used and one that is run
    once. Two passes, to fixpoint: drop whole probes, then simplify the
    expressions that are left — a generated expression is a nest four deep and
    the divergence is usually in one leaf, so ``(("AbC".swapcase() + "x") * 2)``
    should be reported as ``"AbC".swapcase()``.

    ``still_fails`` decides; this function never runs anything itself, which is
    what makes it testable without an engine. Every candidate is *verified* to
    still fail before it is accepted, so the shrinker cannot substitute a
    different failure for the one it was handed — the usual way a shrinker
    wastes an afternoon — and it only ever accepts a strictly shorter program,
    so it cannot loop.
    """
    exprs = expressions(program)
    if not exprs:
        return program
    best = list(exprs)
    spent = [0]

    def fails(cand: List[str]) -> bool:
        if not cand or spent[0] >= budget:
            return False
        spent[0] += 1
        return bool(still_fails(build_program(cand)))

    # 1. Drop probes. Halves first — a 4-probe program where only the last one
    #    diverges is two calls away from minimal, not four.
    improved = True
    while improved and len(best) > 1:
        improved = False
        half = len(best) // 2
        for cand in (best[:half], best[half:]):
            if cand and len(cand) < len(best) and fails(cand):
                best, improved = list(cand), True
                break
        if improved:
            continue
        for i in range(len(best)):
            cand = best[:i] + best[i + 1:]
            if cand and fails(cand):
                best, improved = cand, True
                break

    # 2. Simplify what is left, one probe at a time.
    improved = True
    while improved:
        improved = False
        for i, e in enumerate(best):
            for c in candidates(e):
                cand = list(best)
                cand[i] = c
                if fails(cand):
                    best, improved = cand, True
                    break
            if improved:
                break

    out = build_program(best)
    return out if len(out) <= len(program) else program


# --- the run -----------------------------------------------------------------


@dataclass
class Answer:
    """What one engine said. Held for both sides of every counterexample,
    because "lypning printed nothing" and "lypning printed the wrong thing" are
    different bugs and a report that shows only the diff cannot tell them apart.
    """

    stdout: str = ""
    rc: int = 0
    stderr: str = ""


@dataclass
class Counterexample:
    """A program lypning claimed and then got wrong. Always a bug."""

    program: str
    seed: int
    cpython: Answer
    engine: Answer
    kind: str = OUTPUT
    #: Identical findings collapsed. One broken method reached through a
    #: hundred generated nests is one bug, and a report that prints it a hundred
    #: times is a report nobody reads to the end.
    count: int = 1
    #: The shrinker ran on this one. False means it was past ``shrink_limit``
    #: and the program below is the nest as generated — a distinction worth
    #: printing, because "minimal" and "we ran out of budget" read the same.
    shrunk: bool = False

    @property
    def probes(self) -> List[str]:
        return expressions(self.program)


@dataclass
class FuzzReport:
    """``ran = agreed + refused + the findings``, and nothing is unaccounted for."""

    ran: int = 0
    agreed: int = 0
    refused: int = 0
    counterexamples: List[Counterexample] = field(default_factory=list)
    seconds: float = 0.0
    seed: int = 0
    iterations: int = 0
    engine: str = LYPNING
    binary: str = ""
    reference: str = ""
    #: The refusal ``kind`` histogram. Not a failure list — coverage, and the
    #: same build order ``conformance --plan`` prints, sampled from generated
    #: programs instead of harvested ones.
    refusal_kinds: Dict[str, int] = field(default_factory=dict)
    unbuilt: bool = False
    #: Why nothing was compared, when nothing was. ``unbuilt`` keeps its own
    #: flag because the fix for it is a build; this carries every other reason
    #: the pair could not be formed, and it is never empty on a run that
    #: happened.
    not_run: str = ""

    @property
    def ok(self) -> bool:
        """No counterexamples **and** the run actually happened.

        The second half is not pedantry. Two engines that both failed to spawn
        produce two empty stdouts, which compare equal; upstream's ``-c``
        version reported twelve thousand passing probes on a run where nothing
        executed. A harness that cannot run the program must never report a
        clean bill of health.
        """
        return self.ran > 0 and not self.counterexamples


def _env_for(cwd: Path) -> Dict[str, str]:
    """The environment both engines share.

    ``LYPNING_LOG`` is redirected into the sandbox rather than left pointing at
    the capture log: a 2000-iteration run is 2000 synthetic programs, and
    capturing them would fold generated text into the corpus as *observed*
    evidence and destroy the frequency table that ranks the build order.
    ``engines.run`` sets ``LYPNING_CAPTURE=0`` as the other half of that.
    """
    return {
        "LYPNING_LOG": str(cwd / "capture.jsonl"),
        "PWD": str(cwd),
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
    }


def _run_one(engine: str, program: str, binary: Optional[Path], timeout: float) -> eng.Result:
    """One program on one engine, in a cwd of its own.

    Its own cwd per *engine*, not per program: two engines sharing one would let
    the second read back whatever the first wrote and agree without having
    computed anything. The grammar generates no I/O at all, so this is a net
    rather than a sandbox — it exists so that the day someone teaches the
    generator ``open()``, the damage is confined and loud rather than silent.
    """
    cwd = Path(tempfile.mkdtemp(prefix="lypning-fuzz-%s-" % engine))
    try:
        # In a file, never in argv: a multi-probe program is thousands of lines
        # and past ARG_MAX, and a spawn that fails for BOTH engines compares two
        # empty stdouts as a match.
        src = cwd / "probe.py"
        src.write_text(program, encoding="utf-8")
        return eng.run(engine, binary=binary, script=src, cwd=cwd,
                       timeout=timeout, env=_env_for(cwd))
    finally:
        shutil.rmtree(cwd, ignore_errors=True)


def _clip(s: str, n: int = _CLIP) -> str:
    return s if len(s) <= n else s[:n] + "\n… (clipped)"


def _answer(r: eng.Result, stderr_clip: int = _STDERR_CLIP) -> Answer:
    return Answer(stdout=_clip(r.stdout), rc=r.returncode, stderr=_clip(r.stderr, stderr_clip))


def judge(engine_res: eng.Result, ref: Optional[eng.Result]) -> str:
    """One of ``""`` (agreed), ``"refused"``, or a counterexample ``kind``.

    ``ref`` is ``None`` when the engine refused and CPython was never asked —
    which is the point of asking the engine first: a refusal costs one cheap
    spawn instead of one cheap spawn plus a 20 ms CPython.
    """
    if engine_res.timed_out or (ref is not None and ref.timed_out):
        return HARNESS
    if engine_res.returncode == 127 and not engine_res.stdout:
        return HARNESS
    if engine_res.unsupported:
        # Invariant 2 of CLAUDE.md, fuzzed rather than assumed: exit 90 means
        # the contract line on stderr and NOTHING on stdout. A refusal that
        # printed half an answer first is a half-completed program the
        # dispatcher is about to run again on the next tier.
        if not engine_res.refused or engine_res.stdout:
            return CONTRACT
        return "refused"
    if ref is None:
        return HARNESS
    if ref.returncode != 0 or ref.returncode == 127:
        # CPython could not run what we generated. That is a bug in the
        # generator, and it has to be as loud as a bug in the engine: a
        # generator that emits SyntaxErrors quietly measures nothing.
        return HARNESS
    if engine_res.returncode != 0:
        return CRASH
    if engine_res.stdout != ref.stdout:
        return OUTPUT
    return ""


def run(
    iterations: int = DEFAULT_ITERATIONS,
    seed: Optional[int] = None,
    engine: str = LYPNING,
    timeout: float = DEFAULT_TIMEOUT,
    progress: Optional[Callable[[int, int], None]] = None,
    *,
    workers: Optional[int] = None,
    shrink_limit: int = SHRINK_LIMIT,
    generator: Optional[Callable[[random.Random], str]] = None,
) -> FuzzReport:
    """Generate, compare, shrink. Never raises for a disagreement.

    The whole run is a pure function of ``seed``: the programs are drawn from a
    :class:`random.Random` seeded with it, each one from a child seed drawn from
    the same stream, so ``--seed S --iterations N`` reproduces exactly the same N
    programs and every counterexample carries the child seed that produced it.

    Threads, not processes: every unit of work is a subprocess, so the GIL is
    released for the whole of it. Results are collected in submission order, so
    the report is deterministic even though the execution is not.
    """
    started = time.perf_counter()
    gen_fn = generator if generator is not None else (lambda rng: generate(rng))
    if seed is None:
        seed = random.Random().randrange(1, 2 ** 31)
    report = FuzzReport(seed=int(seed), iterations=max(0, int(iterations)), engine=engine)

    if engine == CPYTHON:
        # CPython is the oracle, so it cannot also be the arm: every program
        # would be compared with its own answer, agree, and the run would report
        # a clean bill of health over N programs it never disagreed about. That
        # is the same shape as the two-failed-spawns bug `ok` exists to catch —
        # a comparison that could not come out any other way — so it is refused
        # here rather than in the CLI alone, which a programmatic caller skips.
        report.not_run = ("cpython is the reference, not an engine under test — "
                          "fuzzing it would compare it with itself")
        report.seconds = time.perf_counter() - started
        return report

    binary = eng.find(engine)
    ref_bin = eng.find(CPYTHON)
    if binary is None or ref_bin is None:
        # "Not built" is a status line everywhere in this package, never a
        # crash. `ok` stays false because a run that did not happen is not a
        # pass, and the CLI turns that into an exit code with a fix in it.
        report.unbuilt = True
        report.seconds = time.perf_counter() - started
        return report
    report.binary, report.reference = str(binary), str(ref_bin)

    master = random.Random(report.seed)
    plan = [(master.randrange(1, 2 ** 31)) for _ in range(report.iterations)]
    programs = [(s, gen_fn(random.Random(s))) for s in plan]

    def compare(item: Tuple[int, str]) -> Tuple[int, str, eng.Result, Optional[eng.Result]]:
        child, program = item
        got = _run_one(engine, program, binary, timeout)
        # CPython only when the engine claimed the program. It is the expensive
        # half of the pair and a refusal makes its answer irrelevant.
        ref = None
        if not (got.unsupported and got.refused and not got.stdout):
            ref = _run_one(CPYTHON, program, ref_bin, timeout)
        return (child, program, got, ref)

    done = 0
    lock = threading.Lock()
    n_workers = workers if workers else min(8, (os.cpu_count() or 1) + 2)
    results: List[Tuple[int, str, eng.Result, Optional[eng.Result]]] = []
    if programs:
        with ThreadPoolExecutor(max_workers=max(1, n_workers)) as ex:
            futures = [ex.submit(compare, p) for p in programs]
            for f in futures:
                results.append(f.result())
                with lock:
                    done += 1
                    if progress is not None:
                        progress(done, len(programs))

    raw: List[Counterexample] = []
    for child, program, got, ref in results:
        report.ran += 1
        verdict = judge(got, ref)
        if verdict == "":
            report.agreed += 1
            continue
        if verdict == "refused":
            report.refused += 1
            kind = got.refusal[0] or "unknown"
            report.refusal_kinds[kind] = report.refusal_kinds.get(kind, 0) + 1
            continue
        raw.append(Counterexample(
            program=program, seed=child, kind=verdict,
            cpython=_answer(ref) if ref is not None else Answer(rc=-1),
            engine=_answer(got),
        ))

    report.counterexamples = _collapse(raw, engine, binary, ref_bin, timeout, shrink_limit)
    report.seconds = time.perf_counter() - started
    return report


def _key(c: Counterexample) -> Tuple[str, str, str, str]:
    return (c.kind, c.program, c.cpython.stdout, c.engine.stdout)


def _collapse(
    raw: Sequence[Counterexample],
    engine: str,
    binary: Optional[Path],
    ref_bin: Optional[Path],
    timeout: float,
    shrink_limit: int,
) -> List[Counterexample]:
    """Dedupe, shrink, dedupe again.

    Twice on purpose. The first pass is free and collapses the exact repeats a
    long run produces; the second is what actually merges a hundred different
    nests around one broken method, and it can only happen after the nests are
    gone. Shrinking is bounded by ``shrink_limit`` because it costs spawn pairs
    and a run that has found fifty distinct bugs has already made its point.
    """
    merged: Dict[Tuple[str, str, str, str], Counterexample] = {}
    for c in raw:
        prior = merged.get(_key(c))
        if prior is None:
            merged[_key(c)] = c
        else:
            prior.count += 1

    out: Dict[Tuple[str, str, str, str], Counterexample] = {}
    for i, c in enumerate(merged.values()):
        if i < shrink_limit and c.kind in (OUTPUT, CRASH, CONTRACT):
            small = shrink(c.program, _still_diverges(engine, binary, ref_bin, timeout, c.kind))
            got, ref = c.engine, c.cpython
            if small != c.program:
                # The answers belong to the program that is printed, so they are
                # re-taken rather than carried over from the nest.
                got = _answer(_run_one(engine, small, binary, timeout))
                ref = _answer(_run_one(CPYTHON, small, ref_bin, timeout))
            c = Counterexample(program=small, seed=c.seed, kind=c.kind, count=c.count,
                               cpython=ref, engine=got, shrunk=True)
        prior = out.get(_key(c))
        if prior is None:
            out[_key(c)] = c
        else:
            prior.count += c.count
    return sorted(out.values(), key=lambda c: (-c.count, c.kind, len(c.program)))


def _still_diverges(
    engine: str, binary: Optional[Path], ref_bin: Optional[Path], timeout: float, kind: str,
) -> Callable[[str], bool]:
    """The shrinker's predicate: does this candidate still fail *the same way*?

    Pinned to the original ``kind`` so a shrink cannot wander from an output
    disagreement into an unrelated crash and report the smaller, wrong thing.
    """
    def still_fails(candidate: str) -> bool:
        got = _run_one(engine, candidate, binary, timeout)
        ref = None
        if not (got.unsupported and got.refused and not got.stdout):
            ref = _run_one(CPYTHON, candidate, ref_bin, timeout)
        return judge(got, ref) == kind
    return still_fails


# --- rendering ---------------------------------------------------------------


def _pad(s: Any, n: int) -> str:
    s = str(s)
    return s if len(s) >= n else s + " " * (n - len(s))


def render(report: FuzzReport) -> str:
    """The human view. The only place in this module that formats for a terminal."""
    out: List[str] = []
    if report.not_run:
        return "fuzz: %s — nothing was fuzzed\n" % report.not_run
    if report.unbuilt:
        return ("fuzz: %s or the reference CPython is not built — nothing was fuzzed\n"
                % report.engine)

    out.append("fuzz %s against cpython — %d programs in %.1fs"
               % (report.engine, report.ran, report.seconds))
    out.append("  engine   %s" % (report.binary or "?"))
    out.append("  cpython  %s" % (report.reference or "?"))
    # The seed, always and near the top. A finding that cannot be replayed is
    # not a finding, it is an anecdote.
    out.append("  seed     %d   (replay: lypning fuzz --seed %d --iterations %d)"
               % (report.seed, report.seed, report.iterations))
    out.append("")
    out.append("ran %d   agreed %d   refused %d   counterexamples %d"
               % (report.ran, report.agreed, report.refused,
                  sum(c.count for c in report.counterexamples)))

    if report.refusal_kinds:
        top = sorted(report.refusal_kinds.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        # Coverage, not failure: this is which part of the subset the generator
        # walked into and lypning has not built yet.
        out.append("refusals %s" % "  ".join("%s:%d" % (k, n) for k, n in top))

    if report.counterexamples:
        out.append("")
        out.append("%d distinct counterexample(s):" % len(report.counterexamples))
        for c in report.counterexamples:
            out.append("")
            out.append("  %s x%d  seed %d%s"
                       % (_pad(c.kind, 8), c.count, c.seed,
                          "" if c.shrunk else "  (not shrunk — past the shrink limit)"))
            for e in c.probes:
                out.append("    %s" % e)
            out.append("      cpython  exit %d  %r" % (c.cpython.rc, c.cpython.stdout))
            out.append("      %s  exit %d  %r"
                       % (_pad(report.engine, 7), c.engine.rc, c.engine.stdout))
            if c.engine.stderr.strip():
                out.append("      stderr   %s" % c.engine.stderr.strip().splitlines()[0])

    out.append("")
    if not report.ran:
        out.append("nothing ran — this is NOT a clean bill of health")
    else:
        out.append("counterexamples %d — %s"
                   % (len(report.counterexamples), "ok" if report.ok else "FAIL"))
    return "\n".join(out) + "\n"
