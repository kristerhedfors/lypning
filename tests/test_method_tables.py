"""The Rust method tables must stay sorted, because a binary search reads them.

`methods.rs` resolves an attribute with `table.binary_search(&name)`. That is the
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
METHODS = RUST / "methods.rs"

#: Only the tables `method_name` and `missing_method` binary-search. A table
#: read some other way is not covered here and does not need to be.
TABLE = re.compile(
    r"const (\w+_(?:METHODS|MISSING)): &\[&str\] = &\[(.*?)\];",
    re.S,
)


def _tables():
    text = METHODS.read_text(encoding="utf-8")
    found = {}
    for m in TABLE.finditer(text):
        names = re.findall(r'"([^"]+)"', m.group(2))
        found[m.group(1)] = names
    return found


def test_the_tables_were_found_at_all():
    # A parse that silently matches nothing turns every assertion below into a
    # loop over an empty list, which passes and means nothing.
    tables = _tables()
    assert "STR_METHODS" in tables
    assert len(tables) >= 8, sorted(tables)


@pytest.mark.parametrize("name", sorted(_tables()))
def test_every_binary_searched_table_is_sorted(name):
    names = _tables()[name]
    assert names == sorted(names), (
        "%s in methods.rs is not in sorted order, and it is read with "
        "binary_search — which means a method in it can be MISSED. "
        "Out of place: %s" % (name, [a for a, b in zip(names, sorted(names)) if a != b])
    )


@pytest.mark.parametrize("name", sorted(_tables()))
def test_no_table_repeats_a_name(name):
    names = _tables()[name]
    assert len(names) == len(set(names)), name


def test_binary_search_is_still_what_reads_them():
    # If the lookup goes back to a linear scan the sortedness requirement is
    # gone, and this file should go with it rather than sit here enforcing a
    # rule nothing depends on any more.
    assert "binary_search" in METHODS.read_text(encoding="utf-8")
