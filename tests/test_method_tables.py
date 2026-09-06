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
    RUST / "re.rs": r"const (\w+_(?:METHODS|MISSING)): &\[&str\] = &\[(.*?)\];",
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


# --- the routing projection of a capability's method surface -----------------
#
# `route::CAP_METHODS` is the one table in the crate that names methods a
# capability serves and is compiled into the variant that does NOT serve them.
# It has to be: the binary that ROUTES is the core, and a `method:` blocker is
# the one kind whose meaning differs between variants, so the core is what has
# to decide whether the name is one `lypning-l` would answer. The three
# `known_method` functions read it rather than keeping a list each, exactly as
# `glob::SERVED` is `route::GLOB_SERVED` — but the DISPATCH tables next to them
# are still separate, and those are what the engine actually answers from.
#
# So this holds the routing row to the dispatch it stands for. The two
# directions are not symmetric: a row that CLAIMS a name nothing serves routes
# the program to a rung that refuses it — a wasted spawn, or exit 1 when the
# refusal lands past a committed barrier (#51) — while a row that MISSES a name
# blocks a program the capability was built to run, which is coverage given
# away. Both are bugs; only the first is a wrong exit code.

ROUTE_RS = RUST / "route.rs"


def _cap_methods() -> dict:
    """`route::CAP_METHODS`, as {module: [name, ...]}."""
    src = ROUTE_RS.read_text(encoding="utf-8")
    m = re.search(r"CAP_METHODS: &\[\(&str, &str\)\] = &\[(.*?)\n\];", src, re.S)
    assert m, "CAP_METHODS is gone from route.rs"
    body = re.sub(r"//[^\n]*", "", m.group(1))
    out = {}
    for mod, names in re.findall(r'\(\s*"(\w+)",\s*((?:"[^"]*"\s*\\?\s*)+)', body):
        text = "".join(re.findall(r'"([^"]*)"', names, re.S))
        # A `\` at end of line is Rust's line continuation: it eats the newline
        # and the indent that follows, so the words on either side of it are
        # separate words and the backslash is not one of them.
        out[mod] = text.replace("\\", " ").split()
    return out


def _named(path: Path, table: str) -> list:
    src = path.read_text(encoding="utf-8")
    # `=\s*&[`: rustfmt breaks the line after the `=` when the initialiser does
    # not fit, and a table that moved down one line is not a table that is gone.
    m = re.search(r"const %s: &\[&str\] =\s*&\[(.*?)\];" % table, src, re.S)
    assert m, "%s is gone from %s" % (table, path.name)
    return re.findall(r'"([^"]+)"', m.group(1))


def _get_attr_arms(path: Path) -> list:
    """Every attribute name `get_attr` answers with a literal match arm.

    The properties are computed at access and never bound, so they are in no
    table — the arms ARE the list, and reading them here is what keeps this
    check from being a copy of the thing it checks.
    """
    src = path.read_text(encoding="utf-8")
    i = src.index("pub fn get_attr")
    return re.findall(r'^\s*"(\w+)" =>', src[i:src.index("\n}\n", i)], re.M)


#: One row per module in `CAP_METHODS`, with the served surface it stands for
#: derived from that module's own dispatch. `cwd` is pathlib's one extra: it is
#: a classmethod on `Path` and reaches `pathlib::cwd` rather than `get_attr`.
#: `collections` subtracts instead of adding, because `Counter` is a tagged
#: `dict` and every name but one is already answered by the probe types
#: `route::known_method` walks — the row is the residue that is not.
#:
#: `hashlib` subtracts twice. The probe types answer `copy` and `update`, and
#: `hashlib::ROUTER_WITHHELD` names the rest: this table is read BY NAME and
#: cannot see the receiver, so `.name` in the row would admit `open(p).name`
#: for any program that imports `hashlib` — the exit-1 shape the second
#: assertion below is about. The withheld list lives in `hashlib.rs`, next to
#: the reason, so this is still a derivation and not a copy.
def _served() -> dict:
    probes = set(
        sum((_named(METHODS, t) for t in
             ("STR_METHODS", "LIST_METHODS", "DICT_METHODS", "SET_METHODS",
              "BYTES_METHODS")), [])
    )
    coll = set(_named(RUST / "collections.rs", "COUNTER_METHODS"))
    coll |= set(_named(RUST / "collections.rs", "DEFAULT_METHODS"))
    return {
        "collections": coll - probes,
        "hashlib": set(_named(RUST / "hashlib.rs", "HASH_ATTRS"))
        - probes
        - set(_named(RUST / "hashlib.rs", "ROUTER_WITHHELD")),
        "pathlib": set(_named(RUST / "pathlib.rs", "METHODS"))
        | set(_get_attr_arms(RUST / "pathlib.rs"))
        | {"cwd"},
        "re": set(_named(RUST / "re.rs", "PATTERN_METHODS"))
        | set(_named(RUST / "re.rs", "MATCH_METHODS"))
        | set(_get_attr_arms(RUST / "re.rs")),
    }


def test_the_routing_table_has_a_row_for_every_capability_that_bears_methods():
    # A parse that found nothing would make every assertion below vacuous, and
    # a capability that grew a method surface without a row here is the hole
    # this table exists to close. `base64` and `bigint` are absent on purpose:
    # neither adds a method name at all.
    assert (
        set(_cap_methods())
        == set(_served())
        == {"collections", "hashlib", "pathlib", "re"}
    )


@pytest.mark.parametrize("module", ["collections", "hashlib", "pathlib", "re"])
def test_the_routing_row_is_exactly_what_that_capability_serves(module):
    row = _cap_methods()[module]
    served = _served()[module]
    assert sorted(row) == row and len(set(row)) == len(row), (
        "CAP_METHODS[%r] is meant to be read as a sorted, duplicate-free word "
        "list: %r" % (module, row)
    )
    claimed = set(row)
    assert not claimed - served, (
        "CAP_METHODS[%r] claims %s, which %s.rs does not serve — the core will "
        "route a program using that name to lypning-l, which refuses it"
        % (module, sorted(claimed - served), module)
    )
    assert not served - claimed, (
        "%s.rs serves %s, which CAP_METHODS[%r] does not name — the walk blocks "
        "a program this capability exists to run"
        % (module, sorted(served - claimed), module)
    )
