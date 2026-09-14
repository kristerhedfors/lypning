"""``docs/DIFFERENCES.md`` against the tables it describes.

The document is a list of names: the builtins that resolve, the modules each
engine serves, the attributes it serves of the four modules it serves in part,
the capabilities that separate the two engines, and the refusal kinds that skip
the larger one. Every one of those lists already exists in the crate, so the
document is a COPY — and this repository has shipped a stale copy of exactly
these lists twice: ``docs/SUBSET.md`` §3 and ``docs/LYPNING.md`` §3 both named
``collections`` and ``pathlib`` as what ``lypning-l`` adds for as long as it
took ``cap-re``, ``cap-csv``, ``cap-glob``, ``cap-base64``, ``cap-hashlib`` and
``cap-bigint`` to land. Nothing noticed, because a stale sentence compiles,
links, routes and benches at exactly the same speed.

So the copy is held to the original here, in the direction that matters: a name
in the document must be a name in the crate, and a name in the crate must be in
the document. The failure message says which side moved, because both are
legitimate — a capability that landed without its row is a documentation bug,
and a row for a capability that was removed is the same bug pointing the other
way.

What this file does not check is the prose around the tables. That is
``CLAUDE.md``'s job and a reader's, as in ``tests/test_docs.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lypning import engines, routing

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "DIFFERENCES.md"
RUST = ROOT / "src" / "lypning" / "assets" / "rust" / "src"

_QUOTED = re.compile(r'"([^"]*)"')


def _text(name: str) -> str:
    return (RUST / name).read_text(encoding="utf-8")


def _string_const(file: str, name: str) -> list:
    """The quoted strings of a ``const NAME: &[&str] = &[…];`` array.

    Read rather than restated, for the reason ``routing.only_cpython_kinds``
    is: a copy kept in the test would be checked against itself.
    """
    m = re.search(r"const %s: &\[&str\] =\s*&\[(?P<body>.*?)\];" % re.escape(name),
                  _text(file), re.S)
    assert m, "%s: no `const %s` array — this test reads it by name" % (file, name)
    return _QUOTED.findall(m.group("body"))


def _core_modules() -> list:
    """``modules.rs:MODULES`` as the build with no ``cap-*`` feature sees it.

    The table is spelled once per feature combination, and the one guarded by
    ``cfg(not(any(…)))`` is the core's — which is also the set both engines
    share, since a capability only ever adds.
    """
    m = re.search(r"#\[cfg\(not\(any\((?:[^)]|\)[^\]])*?\)\)\)\]\s*"
                  r"pub const MODULES: &\[&str\] = &\[(?P<body>.*?)\];",
                  _text("modules.rs"), re.S)
    assert m, "modules.rs: no `cfg(not(any(…)))` MODULES table — this test reads the core's by that guard"
    return _QUOTED.findall(m.group("body"))


def _caps() -> dict:
    """``route.rs:CAPS`` as ``{cap: [modules…]}``."""
    m = re.search(r"pub const CAPS: &\[\(&str, &\[&str\], &\[&str\]\)\] = &\[(?P<body>.*?)\n\];",
                  _text("route.rs"), re.S)
    assert m, "route.rs: no `pub const CAPS` table"
    out = {}
    for row in re.finditer(r'\("(?P<cap>[^"]+)",\s*&\[(?P<mods>[^\]]*)\]', m.group("body")):
        out[row.group("cap")] = _QUOTED.findall(row.group("mods"))
    return out


def _module_attrs_row(module: str) -> list:
    """One module's row of ``route.rs:MODULE_ATTRS``, by name."""
    m = re.search(r"pub const MODULE_ATTRS: &\[\(&str, &\[&str\]\)\] = &\[(?P<body>.*?)\n\];",
                  _text("route.rs"), re.S)
    assert m, "route.rs: no `pub const MODULE_ATTRS` table"
    row = re.search(r'\(\s*"%s",\s*&?\[?(?P<names>[^\]]*)\]?' % re.escape(module),
                    m.group("body"), re.S)
    assert row, "route.rs: MODULE_ATTRS has no %r row" % module
    return _QUOTED.findall(row.group("names"))


def _crate_kinds() -> set:
    """Every refusal kind spelled anywhere in the crate."""
    kinds = set()
    for p in sorted(RUST.glob("*.rs")):
        text = p.read_text(encoding="utf-8")
        kinds |= set(re.findall(r'unsupported\(\s*"([a-z-]+)"', text))
        kinds |= set(re.findall(r'\bblock\("([a-z-]+)"', text))
        # `main.rs` writes the CLI's own refusals through `err::refusal_line`
        # rather than through `unsupported`: the option is rejected before there
        # is an interpreter to raise in.
        kinds |= set(re.findall(r'refusal_line\(\s*"([a-z-]+)"', text))
    return kinds


# --- the document ------------------------------------------------------------


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _fences() -> list:
    """The plain ``` blocks, in order. The word lists are fences because a list
    of 38 names is unreadable as prose and unparseable as a table."""
    return re.findall(r"\n```\n(.*?)\n```\n", _doc(), re.S)


def _table_rows(after: str) -> list:
    """``[[cell, …], …]`` for the first markdown table after ``after``."""
    text = _doc()
    i = text.index(after)
    rows = []
    for line in text[i:].splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            rows.append(cells)
        elif rows:
            break
    return rows


def _names(cell: str) -> list:
    return re.findall(r"`([^`]+)`", cell)


def _fail(what: str, doc: set, crate: set) -> str:
    return ("docs/DIFFERENCES.md and the crate disagree about %s — only in the "
            "document: %s; only in the crate: %s. A name the crate gained is a "
            "row the document owes; a name it lost is a row that must go."
            % (what, sorted(doc - crate) or "none", sorted(crate - doc) or "none"))


def test_the_builtin_list_is_the_engines_own():
    doc = set(_fences()[0].split())
    crate = set(_string_const("builtins.rs", "BUILTINS"))
    assert doc == crate, _fail("the builtins that resolve (§4.1)", doc, crate)


def test_the_exception_list_is_the_engines_own():
    doc = set(_fences()[1].split())
    crate = set(_string_const("builtins.rs", "EXCEPTIONS"))
    assert doc == crate, _fail("the exception names that resolve (§4.1)", doc, crate)


def test_the_module_list_is_the_engines_own():
    doc = set(_fences()[2].split())
    crate = set(_core_modules())
    assert doc == crate, _fail("the modules both engines serve (§4.2)", doc, crate)


def test_the_capability_table_is_route_caps():
    rows = _table_rows("| capability | what it adds | source |")
    doc = {_names(r[0])[0] for r in rows[1:]}
    crate = set(_caps())
    assert doc == crate, _fail("the capabilities lypning-l adds (§5)", doc, crate)
    assert doc == set(engines.VARIANT_CAPS[engines.LYPNING_L]), _fail(
        "the capabilities lypning-l adds (§5)", doc,
        set(engines.VARIANT_CAPS[engines.LYPNING_L]))


def test_every_capability_row_names_the_module_it_serves():
    """A row that named the wrong module would send a reader to the engine that
    refuses it. ``cap-bigint`` is the row that is not a module and says so."""
    caps = _caps()
    wrong = []
    for row in _table_rows("| capability | what it adds | source |")[1:]:
        cap = _names(row[0])[0]
        named = set(_names(row[1]))
        for module in caps[cap]:
            if module not in named:
                wrong.append("%s serves %s and the row does not name it" % (cap, module))
        if not caps[cap] and "no module" not in row[1]:
            wrong.append("%s serves no module and the row does not say so" % cap)
    assert not wrong, "docs/DIFFERENCES.md §5: " + "; ".join(wrong)


@pytest.mark.parametrize("module,source", [
    ("base64", ("route.rs", "BASE64_SERVED")),
    ("glob", ("route.rs", "GLOB_SERVED")),
    ("hashlib", ("hashlib.rs", "SERVED")),
    ("csv", None),
])
def test_the_partial_surfaces_are_the_served_lists(module, source):
    """§4.3 is the list a reader uses to decide whether a program costs a spawn,
    so a name missing from it is a spawn nobody predicted."""
    rows = {_names(r[0])[0]: r for r in _table_rows("| module | served | source |")[1:]}
    assert module in rows, "docs/DIFFERENCES.md §4.3 has no `%s` row" % module
    doc = set(_names(rows[module][1]))
    crate = set(_module_attrs_row("csv") if source is None else _string_const(*source))
    assert doc == crate, _fail("what lypning-l serves of `%s` (§4.3)" % module, doc, crate)


def test_the_escalation_list_is_only_cpython_kinds():
    """§6 is the one place these are enumerated in prose. A kind that leaves the
    table and stays in the document sends a reader looking for a spawn that no
    longer happens; one that lands and is not written down is undocumented
    behaviour in the only document that claims to list it."""
    body = _doc().split("## 6.")[1].split("## 7.")[0]
    doc = set(re.findall(r"`([a-z-]+)`", body.split("One construct")[0]))
    crate = set(routing.only_cpython_kinds())
    doc -= {"lypning-l", "lypning-mp", "route.rs:ONLY_CPYTHON_KINDS",
            "engines.ONLY_CPYTHON_REFUSALS", "lypning oracle", "os.scandir",
            "is", "glob", "int('1000') is 1000", "True"}
    assert doc == crate, _fail("the refusals that skip lypning-l (§6)", doc, crate)
    assert "`async`" in body and set(routing.cpython_only_constructs()) == {"async"}, (
        "route.rs:CPYTHON_ONLY_KINDS is %s and §6 names `async` alone"
        % routing.cpython_only_constructs())


def test_no_refusal_kind_the_document_names_is_invented():
    """``unsupported: <kind>`` in prose must be a kind the crate raises. The
    kinds are the vocabulary a caller branches on; a misspelt one in the one
    document that lists them is a caller matching on nothing."""
    named = set(re.findall(r"unsupported: ([a-z-]+)", _doc()))
    crate = _crate_kinds()
    assert named <= crate, (
        "docs/DIFFERENCES.md names refusal kinds the crate does not raise: %s"
        % sorted(named - crate))
