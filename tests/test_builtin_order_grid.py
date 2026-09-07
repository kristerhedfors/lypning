"""`BUILTINS` is ordered by corpus frequency, and that order is load-bearing.

`builtins::builtin` walks the table from the front with `iter().find()` on every
builtin name a program reads — a builtin is by construction a miss in every
scope, so `eval::lookup` arrives there for each `print`, `open` and `len`. The
order is therefore the expected LENGTH of that walk, and nothing else: the names
are unique, so `find` returns the same entry whatever the order, and no reader
of the table depends on the sequence.

Which is exactly why the order is fragile. It looks like a list that wants
alphabetising, the two tables next to it in the same file are alphabetical, and
re-sorting it would compile, link, pass `conformance` and pass every other test
in this suite while quietly putting `print` back 28th. Two earlier attempts to
speed this scan up by changing its SHAPE both lost — `binary_search` in
`docs/HILLCLIMB.md` iterations 4 and 42, a first-byte split in 68 — so the order
is the only lever left on it and the one thing here worth guarding.

The guard is a PROPERTY, not a transcript of today's ranking: the corpus grows
every session (CLAUDE.md invariant 3) and adjacent names swap places without
meaning anything. What must keep holding is that the mean walk over the corpus
is far shorter than the alphabetical one, and that reordering never lost a name.
"""

from __future__ import annotations

import ast
import re
import warnings
from pathlib import Path

import pytest

from lypning import corpus

RUST = Path(__file__).resolve().parents[1] / "src" / "lypning" / "assets" / "rust" / "src"
BUILTINS_RS = RUST / "builtins.rs"

#: How much of the alphabetical walk the committed order may cost. Measured at
#: 4.64 against 25.31 compares — 18% — on 3,688 programs on 2026-09-07, so half
#: is a wide floor that a genuinely frequency-ordered table cannot fail and an
#: alphabetised one cannot pass.
BUDGET = 0.5


def _table(name: str) -> list[str]:
    text = BUILTINS_RS.read_text(encoding="utf-8")
    m = re.search(r"pub const %s: &\[&str\] = &\[(.*?)\];" % name, text, re.S)
    assert m, "%s was not found in builtins.rs — this file is parsing nothing" % name
    return re.findall(r'"([^"]+)"', m.group(1))


def _sightings() -> tuple[dict[str, int], int, int]:
    """How often each builtin name is READ across the corpus, and the corpus size.

    An `ast.Name` in the load context is what `builtin()` actually gets asked
    about; a program that does not parse is scanned for words instead, because
    dropping it would bias the count toward the programs CPython likes.
    """
    names = set(_table("BUILTINS"))
    rows = corpus.load_default()
    counts = {n: 0 for n in names}
    unparsed = 0
    # The corpus is real agent code and a good deal of it has `re` patterns in
    # plain strings, so parsing it emits CPython 3.12+ invalid-escape
    # SyntaxWarnings by the hundred. They are about the programs, not about this
    # test, and a suite that prints them teaches the reader to skim warnings.
    warnings.simplefilter("ignore", SyntaxWarning)
    for entry in rows:
        program = getattr(entry, "program", "") or ""
        try:
            tree = ast.parse(program)
        except (SyntaxError, ValueError, RecursionError):
            unparsed += 1
            for n in names:
                counts[n] += len(re.findall(r"\b%s\b" % re.escape(n), program))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in names:
                counts[node.id] += 1
    return counts, len(rows), unparsed


def _mean_walk(order: list[str], counts: dict[str, int]) -> float:
    total = sum(counts.values())
    assert total, "no builtin was named anywhere in the corpus — the count is broken"
    return sum(counts[n] * (order.index(n) + 1) for n in order) / total


def test_the_table_was_found_and_is_the_whole_namespace():
    # A regex that silently matches nothing turns every assertion below into a
    # loop over an empty list, which passes and means nothing.
    names = _table("BUILTINS")
    assert len(names) == len(set(names)), "duplicate entry: %s" % [
        n for n in names if names.count(n) > 1
    ]
    assert len(names) >= 39, names


def test_reordering_never_dropped_a_name():
    # The failure a reorder actually causes: a name falls out in the edit and
    # every program that uses it becomes `unsupported: builtin` — a routing
    # signal, so not a MISMATCH, but a silent hole in coverage that no other
    # test in this suite is looking for. Compared as a SET, which is the only
    # thing about this table that is allowed to be stable.
    assert set(_table("BUILTINS")) == {
        "abs", "all", "any", "bin", "bool", "bytes", "chr", "dict", "divmod",
        "enumerate", "filter", "float", "format", "hex", "input", "int",
        "isinstance", "iter", "len", "list", "map", "max", "min", "next", "oct",
        "open", "ord", "print", "range", "repr", "reversed", "round", "set",
        "sorted", "str", "sum", "tuple", "type", "zip",
    }


def test_the_table_is_not_alphabetical():
    names = _table("BUILTINS")
    assert names != sorted(names), (
        "BUILTINS has been re-alphabetised. It is ordered by corpus frequency "
        "because `builtin()` scans it from the front on every builtin name a "
        "program reads; see its doc comment and docs/HILLCLIMB.md."
    )


def test_the_walk_is_far_shorter_than_the_alphabetical_one():
    names = _table("BUILTINS")
    counts, loaded, unparsed = _sightings()
    here = _mean_walk(names, counts)
    alpha = _mean_walk(sorted(names), counts)
    assert here <= alpha * BUDGET, (
        "the committed order costs %.2f compares per builtin sighting against "
        "%.2f alphabetically over %d corpus programs (%d did not parse), which "
        "is %.0f%% of the alphabetical walk and the budget is %.0f%%. Re-count "
        "and re-order: `sorted(BUILTINS, key=lambda n: -counts[n])`."
        % (here, alpha, loaded, unparsed, 100 * here / alpha, 100 * BUDGET)
    )


@pytest.mark.parametrize("name", ["print", "open", "len"])
def test_the_three_commonest_names_are_at_the_front(name):
    # Not a claim about their exact ranks, which move. `print` is in most corpus
    # programs, `open` and `len` in a large minority, and between them they were
    # 66% of all sightings on 2026-09-07 — a table that has any of the three
    # outside its first handful of entries is not frequency-ordered whatever the
    # mean says.
    assert _table("BUILTINS").index(name) < 5


def test_a_scan_is_still_what_reads_it():
    # If the lookup ever becomes a binary search the sortedness requirement is
    # back and this whole file has to go with it, rather than sit here enforcing
    # the opposite rule. `tests/test_method_tables.py` makes the mirror-image
    # check for the tables that ARE searched.
    text = BUILTINS_RS.read_text(encoding="utf-8")
    body = text[text.index("pub fn builtin(name: &str)"):]
    body = body[: body.index("\n}\n")]
    assert "BUILTINS.iter().find(" in body, (
        "`builtin()` no longer scans BUILTINS from the front; the frequency "
        "order this file guards may not be what the new reader wants."
    )
