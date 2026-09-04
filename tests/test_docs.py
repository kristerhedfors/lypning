"""Documentation truth, checked by executing the claims rather than reading them.

This repository has shipped a dangling documentation reference three times —
``tests/test_shims.py``, ``tests/test_cookbook.py`` and
``tests/test_syntax_scan.py`` were each promised by a document before they
existed — and the README's command reference silently lost a whole subcommand
(``corpus-time``) when it was added. Both failures are invisible to every other
gate in the project: a stale sentence compiles, links, passes conformance and
benches at exactly the same speed.

So the checkable half of "the docs are true" is checked here:

* every subcommand the CLI has is in the README's command reference, and every
  command that reference names still exists;
* every relative link and every file path a document points at resolves;
* every ``§N`` cross-reference lands on a heading that is actually there.

What this file deliberately does NOT check is prose — whether a number is still
the number, whether an explanation is still the explanation. That is
:file:`CLAUDE.md`'s job and a reader's. The rule this file enforces is narrower
and absolute: **a document may not name something that is not there.**

The failure messages say which direction to fix in, because both are legitimate:
a command missing from the README is a documentation bug, and a README row for a
command that was deleted is the same bug pointing the other way.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lypning import cli

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
DOCS = sorted((ROOT / "docs").glob("*.md"))
SKILL_DIR = ROOT / "src" / "lypning" / "assets" / "claude" / "skills" / "lypning"

#: Every markdown file this project owns and is therefore accountable for.
#: ``assets/micropython/README.md`` is included: it ships in the wheel and is
#: read by whoever rebuilds that tier.
OWNED = (
    [README, ROOT / "CLAUDE.md", ROOT / "CHANGELOG.md"]
    + DOCS
    + sorted(SKILL_DIR.glob("*.md"))
    + [ROOT / "src" / "lypning" / "assets" / "micropython" / "README.md"]
)


def _readme_command_rows() -> set:
    """The commands named in README §4's reference table.

    Parsed from the table rather than from prose, because the table is the
    promise: it is what a reader scans to find out what exists.
    """
    text = README.read_text(encoding="utf-8")
    start = text.index("## 4. Command reference")
    end = text.index("\n## 5.", start)
    section = text[start:end]
    found = set()
    for row in re.finditer(r"^\|\s*`lypning ([a-z-]+)", section, re.M):
        found.add(row.group(1))
    return found


def test_every_cli_subcommand_is_in_the_readme_command_reference():
    documented = _readme_command_rows()
    missing = sorted(set(cli.COMMANDS) - documented)
    assert not missing, (
        "README.md §4 does not list %s — the CLI grew a command and the reference "
        "did not. Add a row, or delete the command." % ", ".join(missing))


def test_the_readme_command_reference_names_no_command_that_is_gone():
    documented = _readme_command_rows()
    # `-c` and a bare FILE are interpreter mode, not subcommands, and the table
    # documents them with the same `lypning ...` shape; they are not in COMMANDS
    # and must not be demanded of it.
    extra = sorted(documented - set(cli.COMMANDS) - {"-c", "run", "route"})
    assert not extra, (
        "README.md §4 documents %s, which `lypning --help` does not offer. Either "
        "the command was removed and the row should go, or it was renamed."
        % ", ".join(extra))


def test_json_is_offered_exactly_where_the_readme_says_it_is():
    """§4: every reporting subcommand takes ``--json``; ``run`` and ``hook`` do not."""
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None)
               and "conformance" in getattr(a, "choices", {}))
    for name, p in sub.choices.items():
        has = any("--json" in (a.option_strings or []) for a in p._actions)
        if name in ("run", "hook"):
            assert not has, ("`lypning %s` grew --json; README §4 says it is one of the "
                             "two commands with a caller-defined output" % name)
        else:
            assert has, ("`lypning %s` has no --json; README §4 promises it for every "
                         "subcommand that reports something" % name)


def test_the_readme_doc_table_covers_docs_exactly():
    """§9's table is the index. A document missing from it is a document nobody
    opens; a row for a document that was deleted sends the reader nowhere."""
    text = README.read_text(encoding="utf-8")
    listed = {m.group(1) for m in re.finditer(r"^\|\s*`(docs/[A-Z0-9-]+\.md)`", text, re.M)}
    actual = {"docs/%s" % p.name for p in DOCS}
    assert listed == actual, (
        "README.md §9's doc table and docs/ disagree — only in the table: %s; "
        "only on disk: %s" % (sorted(listed - actual) or "none",
                              sorted(actual - listed) or "none"))


@pytest.mark.parametrize("doc", OWNED, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_markdown_links_resolve(doc):
    """``[text](target.md)`` must land on a file, or it is a 404 in a repository."""
    text = doc.read_text(encoding="utf-8")
    broken = []
    for m in re.finditer(r"\]\(([^)\s#]+\.md)(?:#[^)]*)?\)", text):
        target = m.group(1)
        if target.startswith(("http://", "https://", "//")):
            continue
        # Try the link as written, relative to the document, and relative to
        # docs/ — the three ways these documents actually spell each other.
        for base in (doc.parent, ROOT, ROOT / "docs"):
            if (base / target).exists():
                break
        else:
            broken.append(target)
    assert not broken, "%s links to missing file(s): %s" % (
        doc.relative_to(ROOT), ", ".join(sorted(set(broken))))


@pytest.mark.parametrize("doc", OWNED, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_test_file_a_document_promises_exists(doc):
    """The failure this project has actually shipped, three times.

    A document that says "pinned by ``tests/test_x.py``" and no such file is
    worse than silence: it reads as evidence. Fix in whichever direction is
    true — write the test, or stop claiming it.
    """
    text = doc.read_text(encoding="utf-8")
    missing = sorted({m.group(1) for m in re.finditer(r"`(tests/test_\w+\.py)`", text)
                      if not (ROOT / m.group(1)).exists()})
    missing += sorted({m.group(1) for m in re.finditer(r"\b(tests/test_\w+\.py)\b", text)
                       if not (ROOT / m.group(1)).exists()})
    assert not missing, "%s promises a test file that does not exist: %s" % (
        doc.relative_to(ROOT), ", ".join(sorted(set(missing))))


def _headings(path: Path) -> set:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"#{1,6}\s+(\d+[a-z]?)[.)]?\s", line)
        if m:
            out.add(m.group(1))
    return out


@pytest.mark.parametrize("doc", OWNED, ids=lambda p: str(p.relative_to(ROOT)))
def test_section_cross_references_land_on_a_heading(doc):
    """``docs/X.md §7`` must be a section X actually has.

    Renumbering a document is the cheapest possible way to make a dozen other
    documents lie, and nothing else in the project would notice.
    """
    text = doc.read_text(encoding="utf-8")
    bad = []
    for m in re.finditer(r"`([A-Za-z0-9_./-]+\.md)`\s*§\s*(\d+[a-z]?)", text):
        target, section = m.groups()
        for base in (doc.parent, ROOT, ROOT / "docs"):
            p = base / target
            if p.exists():
                if section not in _headings(p):
                    bad.append("%s §%s" % (target, section))
                break
        else:
            bad.append("%s (no such file)" % target)
    assert not bad, "%s points at a section that is not there: %s" % (
        doc.relative_to(ROOT), ", ".join(sorted(set(bad))))


ASSETS = ROOT / "src" / "lypning" / "assets"
EMBEDDING = ROOT / "docs" / "EMBEDDING.md"


def _embedding_section_4() -> str:
    text = EMBEDDING.read_text(encoding="utf-8")
    start = text.index("\n## 4.")
    end = text.index("\n## 5.", start)
    return text[start:end]


def _quickstarts_on_disk() -> set:
    """Every ``quickstart.<ext>`` (or ``quickstart/main.<ext>``) under assets,
    as ``assets/...`` paths. Build products have no source extension and drop
    out; ``.o``/``.d`` are excluded by name so a stray object cannot count."""
    found = set()
    for pattern in ("examples/*/quickstart.*", "examples/*/examples/quickstart.*",
                    "*/quickstart.*", "*/quickstart/main.*", "*/Sources/quickstart/main.*"):
        for p in ASSETS.glob(pattern):
            if p.is_file() and p.suffix not in (".o", ".d", ".pyc"):
                found.add("assets/" + p.relative_to(ASSETS).as_posix())
    return found


def test_every_host_quickstart_is_documented():
    """``docs/EMBEDDING.md`` section 4 is the one place the hosts are counted
    (CLAUDE.md: the number of hosts is stated once). So it must name every
    quickstart that exists, and every quickstart it names must exist -- a ninth
    binding that lands without its row is a binding nobody finds, and a row
    for a directory that was deleted sends the reader to a 404. The second
    half: a host directory without a quickstart is a host without the one file
    every other host has, and :file:`tests/test_hosts.py` cannot drive it.
    """
    section = _embedding_section_4()
    named = {m.group(0) for m in re.finditer(r"assets/[\w./-]+", section)}
    documented = {p for p in named if re.search(r"quickstart(\.\w+|/main\.\w+)$", p)}
    on_disk = _quickstarts_on_disk()
    assert documented == on_disk, (
        "docs/EMBEDDING.md section 4 and src/lypning/assets disagree on the quickstarts "
        "-- only in the table: %s; only on disk: %s"
        % (sorted(documented - on_disk) or "none", sorted(on_disk - documented) or "none"))
    # Every host directory has one: the example dirs by construction, and every
    # top-level assets directory the table names (the header dir is the ABI,
    # not a host).
    host_dirs = {"assets/examples/%s" % d.name for d in (ASSETS / "examples").iterdir() if d.is_dir()}
    host_dirs |= {"assets/" + p.split("/")[1] for p in named
                  if p.count("/") >= 1 and p.split("/")[1] not in ("include", "examples")}
    without = sorted(d for d in host_dirs
                     if not any(q.startswith(d + "/") for q in on_disk))
    assert not without, (
        "host directories with no quickstart: %s -- every host has one, and it is "
        "the file tests/test_hosts.py drives" % ", ".join(without))


# --- the names, and the words that describe a chain this tree does not have --

#: The three append-only ledgers, plus the two history documents that quote
#: them: every dated narrative of an earlier spelling of the chain lives here,
#: and the positional words below are the vocabulary of that history.
LEDGERS = {"BENCH-LEDGER.md", "HILLCLIMB.md", "PAPER.md", "RESEARCH.md", "CHANGELOG.md"}


def _upstream_names() -> list:
    """The two project names this package was extracted under, read from
    README §8 — the credit paragraph is their one sanctioned home in prose,
    so it is the one place a test may learn them from without spelling them."""
    text = README.read_text(encoding="utf-8")
    start = text.index("## 8. Credit")
    end = text.index("\n## 9.", start)
    names = re.findall(r"\*\*`([a-z]+)`\*\*", text[start:end])
    assert len(names) == 2, "README §8 names the two upstream projects in bold code"
    return names


@pytest.mark.parametrize("doc", OWNED, ids=lambda p: str(p.relative_to(ROOT)))
def test_the_upstream_names_appear_only_where_the_credit_says(doc):
    """CLAUDE.md invariant 9: the two upstream names appear in exactly three
    places — README §8, CHANGELOG's *Before the name*, and the historical corpus
    JSONL. This test is the grep, and it never spells them: it reads them from
    §8, so a document that copies the credit paragraph elsewhere fails here
    rather than in a reviewer's memory."""
    if doc.name in ("README.md", "CHANGELOG.md"):
        pytest.skip("a sanctioned home")
    text = doc.read_text(encoding="utf-8")
    found = sorted(n for n in _upstream_names() if re.search(r"\b%s\b" % re.escape(n), text))
    assert not found, "%s spells an upstream project name (%s); the credit in README §8 " \
        "and CHANGELOG's 'Before the name' are its only homes in prose" % (
            doc.relative_to(ROOT), ", ".join(found))


#: Words that place an engine by position in a chain the code does not have.
#: Engines are spelled as engine strings — the members of
#: ``engines.ENGINE_ORDER`` — and the oracle is "measured, never routed to".
POSITIONAL_TIER_WORDS = re.compile(
    r"\btier [12]\b|\btier-[12]\b|\bmiddle tier\b|\bsecond tier\b|\bMicroPython tier\b|"
    r"\bthree interpreters\b|\bthree tiers\b|\bboth tiers\b|\btwo subset tiers\b", re.I)


@pytest.mark.xfail(strict=False, reason="the documents are being rewritten one PR at a "
                   "time; this turns green as each lands and the marker comes off last")
@pytest.mark.parametrize("doc", [d for d in OWNED if d.name not in LEDGERS],
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_no_document_places_an_engine_by_tier_number(doc):
    """`tier 1`, `middle tier`, `MicroPython tier`, `three interpreters`: each
    describes the chain of a dated CHANGELOG entry, not the one in
    ``engines.ENGINE_ORDER``. The ledgers keep the words; nothing else may."""
    hits = ["%d: %s" % (n, line.strip())
            for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1)
            if POSITIONAL_TIER_WORDS.search(line)]
    assert not hits, "%s places an engine by position:\n%s" % (
        doc.relative_to(ROOT), "\n".join(hits[:12]))


@pytest.mark.parametrize("doc", OWNED, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_test_node_id_a_document_cites_exists(doc):
    """``tests/test_x.py::test_name`` must be a function that file defines.

    The file-level check above catches a test file that was never written; a
    citation by node id makes a stronger claim — THIS test pins THIS sentence —
    and a renamed function breaks it just as silently.
    """
    text = doc.read_text(encoding="utf-8")
    missing = []
    for m in re.finditer(r"\b(tests/test_\w+\.py)::(\w+)", text):
        path, name = m.groups()
        target = ROOT / path
        if not target.is_file():
            missing.append("%s (no such file)" % m.group(0))
        elif not re.search(r"^\s*(?:async\s+)?def\s+%s\s*\(" % re.escape(name),
                           target.read_text(encoding="utf-8"), re.M):
            missing.append(m.group(0))
    assert not missing, "%s cites a test that does not exist: %s" % (
        doc.relative_to(ROOT), ", ".join(sorted(set(missing))))
