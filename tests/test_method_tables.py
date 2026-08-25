"""The Rust name tables must stay sorted, because a binary search reads them.

`methods.rs` resolves an attribute and `builtins.rs` resolves a name with
`table.binary_search(&name)`. That is the
right shape for a lookup on the hottest path in the interpreter — a `.foo()` is
in most corpus programs, and `STR_METHODS` alone is dozens of entries — and it
has one failure mode: on an unsorted table the search does not fall back to a
scan, it **misses**. A method that exists then raises `AttributeError` and exits
1 where CPython answers, which is a MISMATCH and not a refusal (CLAUDE.md
invariant 1).

Nothing in the Rust crate can catch that: there is no `cargo test` in CI, and a
`debug_assert` is compiled out of the release build that ships. So the guard
lives here, where the suite that does run can see it. This file parses the
tables out of the source rather than duplicating them — a copy of the list would
be a second thing to keep in step, which is the bug it is trying to prevent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RUST = Path(__file__).resolve().parents[1] / "src" / "lypning" / "assets" / "rust" / "src"
#: Every source that holds a binary-searched table, and the table names in it.
#: A table read some other way is not covered here and does not need to be.
SOURCES = {
    RUST / "methods.rs": r"const (\w+_(?:METHODS|MISSING)): &\[&str\] = &\[(.*?)\];",
}
# `builtins.rs` is deliberately NOT here. Its BUILTINS and EXCEPTIONS tables were
# converted to binary search too and it bought nothing measurable — a builtin
# call costs ~0.65 us and the scan it replaced was a few tens of nanoseconds of
# that — so the change was reverted rather than kept for the ordering constraint
# it would impose forever. `docs/HILLCLIMB.md` iteration 4 has the numbers.

METHODS = RUST / "methods.rs"


def _tables():
    found = {}
    for path, pattern in SOURCES.items():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(pattern, text, re.S):
            names = re.findall(r'"([^"]+)"', m.group(2))
            found["%s:%s" % (path.name, m.group(1))] = names
    return found


def test_the_tables_were_found_at_all():
    # A parse that silently matches nothing turns every assertion below into a
    # loop over an empty list, which passes and means nothing.
    tables = _tables()
    assert "methods.rs:STR_METHODS" in tables
    assert len(tables) >= 8, sorted(tables)


@pytest.mark.parametrize("name", sorted(_tables()))
def test_every_binary_searched_table_is_sorted(name):
    names = _tables()[name]
    assert names == sorted(names), (
        "%s is not in sorted order, and it is read with "
        "binary_search — which means a method in it can be MISSED. "
        "Out of place: %s" % (name, [a for a, b in zip(names, sorted(names)) if a != b])
    )


@pytest.mark.parametrize("name", sorted(_tables()))
def test_no_table_repeats_a_name(name):
    names = _tables()[name]
    assert len(names) == len(set(names)), name


@pytest.mark.parametrize("path", sorted(SOURCES, key=str))
def test_binary_search_is_still_what_reads_them(path):
    # If a lookup goes back to a linear scan the sortedness requirement is gone
    # for that file, and this guard should go with it rather than sit here
    # enforcing a rule nothing depends on any more.
    assert "binary_search" in path.read_text(encoding="utf-8"), path.name



# --- the case-mapping refusal tables, checked against CPython itself ----------
#
# `casefold_differs` and `titlecase_differs` in `methods.rs` are the codepoints
# where Rust's `to_lowercase` / `to_uppercase` are NOT the mapping CPython
# applies, so `str.casefold()`, `.title()` and `.capitalize()` refuse on them
# rather than answer. They are the same shape as `route.rs`'s capability table
# and carry the same risk: a table that describes what someone WISHED the
# runtime did turns a loud refusal into a silent wrong answer (CLAUDE.md
# invariant 1).
#
# So they are checked against the oracle rather than against a copy. The two
# directions are not symmetric and the messages say which is which: a MISSING
# codepoint is a wrong answer at exit 0, an EXTRA one is only over-refusal.

_RANGE_RE = re.compile(r"'\\u\{([0-9a-f]+)\}'\.\.='\\u\{([0-9a-f]+)\}'")
_SINGLE_RE = re.compile(r"'\\u\{([0-9a-f]+)\}'(?!\.\.)")


def _predicate_set(fn: str) -> set:
    """The codepoints a `matches!`-based predicate in methods.rs covers."""
    src = METHODS.read_text(encoding="utf-8")
    m = re.search(
        r"fn %s\(c: char\) -> bool \{\s*matches!\(c,(.*?)\)\s*\}" % fn, src, re.S
    )
    assert m, (
        "%s is gone from methods.rs — if the refusal was replaced by a real "
        "implementation, delete this test with it" % fn
    )
    body = m.group(1)
    out = set()
    for lo, hi in _RANGE_RE.findall(body):
        out |= set(range(int(lo, 16), int(hi, 16) + 1))
    for one in _SINGLE_RE.findall(body):
        out.add(int(one, 16))
    return out


def _codepoints_where(differs) -> set:
    return {
        c
        for c in range(0x110000)
        if not 0xD800 <= c <= 0xDFFF and differs(chr(c))
    }


@pytest.mark.parametrize(
    "fn,differs",
    [
        ("casefold_differs", lambda ch: ch.casefold() != ch.lower()),
        ("titlecase_differs", lambda ch: ch.title() != ch.upper()),
    ],
)
def test_case_refusal_table_matches_cpython(fn, differs):
    claimed = _predicate_set(fn)
    real = _codepoints_where(differs)
    missing = sorted(real - claimed)
    extra = sorted(claimed - real)
    assert not missing, (
        "%s does not cover %d codepoint(s) CPython maps differently, so the "
        "method ANSWERS where it should refuse — a wrong answer at exit 0. "
        "First few: %s" % (fn, len(missing), [hex(c) for c in missing[:8]])
    )
    assert not extra, (
        "%s covers %d codepoint(s) CPython maps the same way, so the method "
        "refuses where it could answer. Safe, but it is coverage given away for "
        "nothing. First few: %s" % (fn, len(extra), [hex(c) for c in extra[:8]])
    )
