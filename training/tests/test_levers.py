"""What must stay true of the lever split, pinned.

The one that matters is `test_the_closed_list_is_not_restated`: this module
buckets refusals, and the engine's own list of refusals no reimplementation may
answer is a statement the engine makes about itself. A second copy of it here
would let this table contradict the engine silently, which is the shape root
`CLAUDE.md` invariant 1 exists to prevent. The rest pin that the table
partitions, that the disagreement with `ASSESSMENT.md` §4 is the one that was
reviewed rather than a new one, and that the eval-2 draw rows take the same path
as the local capture — which is the whole claim behind rung S0b being a command.
"""

from __future__ import annotations

import ast
import json
import os

import pytest

from pipeline import levers

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLASSIFIED = os.path.join(ROOT, "data", "classified.jsonl")
ASSESSMENT = os.path.join(ROOT, "ASSESSMENT.md")
SOURCE = os.path.join(ROOT, "pipeline", "levers.py")

#: The reviewed disagreement between this table and `ASSESSMENT.md` §4, as
#: measured on this tree on 2026-09-16 and recorded in the round report of the
#: same date. §4 bucketed by judgement per kind and could not be re-derived from
#: its prose; this table bucketed 195 entries mechanically and the rest through
#: `DECLARED`, and it lands 43 more entries in the fallback bucket and 38 fewer
#: in the engine-addressable one. The test does NOT demand agreement — it
#: demands that the disagreement stays the one a reviewer looked at. A new
#: capture, a reworded engine detail or an edited declaration moves a number
#: here and fails, which is the point.
SECTION_4_DELTA = {
    "self-referential": 0,
    "legitimate-fallback": +43,
    "engine-addressable": -38,
    "other": -5,
}


@pytest.fixture(scope="module")
def classified():
    if not os.path.exists(CLASSIFIED):
        pytest.skip("no classified.jsonl in this tree")
    with open(CLASSIFIED, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


@pytest.fixture(scope="module")
def result(classified):
    return levers.table(classified, source=CLASSIFIED, loaded=len(classified))


def test_the_closed_list_is_not_restated():
    """No literal in this module spells a kind the engine's own list owns."""
    closed = levers.closed()
    if not closed:
        pytest.skip("no engine here; the list this test guards is empty")
    with open(SOURCE, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    spelled = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in closed:
                spelled.add(node.value)
    assert not spelled, (
        "levers.py spells %s, which lypning.engines.ONLY_CPYTHON_REFUSALS already "
        "owns. Import that list, never restate it." % sorted(spelled))


def test_no_declaration_shadows_the_engines_own_list():
    closed = levers.closed()
    if not closed:
        pytest.skip("no engine here")
    for family_key, _bucket, _provenance, _why in levers.DECLARED:
        assert family_key.split(":", 1)[0] not in closed, family_key


def test_declarations_are_well_formed_and_unique():
    levers._check_declarations(levers.closed)
    keys = [row[0] for row in levers.DECLARED]
    assert len(keys) == len(set(keys))


def test_library_does_not_print():
    """Invariant 8: modules return data, cli.py renders it."""
    with open(SOURCE, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "print", "levers.py prints at line %d" % node.lineno


def test_the_buckets_partition_the_refusals(result, classified):
    refused = len([r for r in classified if r.get("outcome") == "refused"])
    assert result["refusals"] == refused
    assert result["loaded"] == len(classified)
    assert sum(b["units"] for b in result["buckets"].values()) == refused
    seen = {}
    for row in result["families"]:
        assert row["family"] not in seen
        seen[row["family"]] = row["bucket"]
    assert sum(r["units"] for r in result["families"]) == refused


def test_every_family_is_decided(result):
    """The review queue is empty and no declaration is orphaned.

    Both halves fail loudly on purpose. A family nothing reaches is a judgement
    nobody made; a declaration nothing matches is a judgement whose subject
    moved, usually because an engine detail was reworded.
    """
    assert result["undeclared"] == [], [r["family"] for r in result["undeclared"]]
    assert result["declared_unused"] == []


def test_the_disagreement_with_section_4_is_the_reviewed_one(result):
    totals = levers.section4_totals(ASSESSMENT)
    assert set(totals) == set(levers.BUCKETS), totals
    comparison = levers.compare(result, totals)
    assert comparison["delta"] == SECTION_4_DELTA, (
        "the split moved against ASSESSMENT.md §4. That is not automatically wrong, "
        "but it is a new disagreement and it needs a reviewer, not a new constant.")


def test_section_4_totals_still_sum_to_the_population(result):
    totals = levers.section4_totals(ASSESSMENT)
    assert sum(totals.values()) == result["refusals"]


def test_the_family_key_collapses_the_noisy_details():
    assert levers.family("module", "import urllib.request") == "module: urllib"
    assert levers.family("module", "import lypning.corpus") == "module: lypning"
    assert (levers.family("module-attr", "sys.path (lypning has no import machinery)")
            == "module-attr: sys.path")
    first = levers.family("re", "pattern '(?P<a>\\d+)': named group (?P<name>…)")
    second = levers.family("re", "pattern '(?P<k>\\w+)=(?P<v>\\d+)': named group (?P<name>…)")
    assert first == second


def test_the_program_text_is_never_the_rule(result):
    """§4 bucketed per kind; a program mentioning this package is not evidence.

    `class definition` is the case that proves it: most of those captures do
    mention the package, and they are engine-addressable all the same.
    """
    rows = dict((r["family"], r) for r in result["families"])
    row = rows["class: class definition"]
    assert row["bucket"] == levers.ENGINE_ADDRESSABLE
    assert row["mentions_own_package"] > 0


def test_the_oracle_is_evidence_and_not_a_layer(result):
    """A module the oracle serves may still be a fallback, and one it does not
    serve may still be engine-addressable. If either stops being true the column
    has quietly become a rule."""
    rows = dict((r["family"], r) for r in result["families"])
    assert rows["module: tempfile"]["oracle_serves"] is True
    assert rows["module: tempfile"]["bucket"] == levers.LEGITIMATE_FALLBACK
    assert rows["module: itertools"]["oracle_serves"] is False
    assert rows["module: itertools"]["bucket"] == levers.ENGINE_ADDRESSABLE


def test_the_closed_list_outranks_a_declaration(result):
    """The engine's statement about a kind wins over a family declaration.

    `module: math` is engine-addressable and `math: <a domain error>` is closed,
    on the same module, which is only coherent if the kind layer runs first.
    """
    rows = dict((r["family"], r) for r in result["families"])
    assert rows["module: math"]["bucket"] == levers.ENGINE_ADDRESSABLE
    closed_math = [r for r in result["families"]
                   if r["kind"] == "math" and r["basis"] == "engine-closed-list"]
    assert closed_math and closed_math[0]["bucket"] == levers.LEGITIMATE_FALLBACK


def test_the_ranking_is_independent_times_units():
    rows = {
        "families": [
            {"family": "a", "bucket": levers.ENGINE_ADDRESSABLE, "units": 9,
             "score": 18, "independent": 2},
            {"family": "b", "bucket": levers.ENGINE_ADDRESSABLE, "units": 8,
             "score": 40, "independent": 5},
            {"family": "c", "bucket": levers.LEGITIMATE_FALLBACK, "units": 99,
             "score": 99, "independent": 1},
        ]
    }
    order = [r["family"] for r in levers.rank(rows)]
    assert order == ["b", "a"], "more entries on fewer days must not outrank"


def test_draw_rows_take_the_same_path_as_a_capture():
    """Rung S0b's population, in the shape `refusals.grade_against_engine` writes.

    If this diverges from the classified path, S0b stops being the same table
    and becomes a second judgement made somewhere else.
    """
    draw = {"case_id": "c1", "draw": 0, "status": "correct-fallback",
            "split_group": "g1", "program": "import itertools",
            "verdict": "UNSUPPORTED", "detail": "module",
            "blocker": "module: import itertools"}
    captured = {"outcome": "refused",
                "info": {"kind": "module", "detail": "import itertools"},
                "entry": {"id": "py-1", "program": "import itertools",
                          "first_seen": "2026-09-01T00:00:00Z", "source": "hook"}}
    assert (levers.classify("module", "import itertools")["bucket"]
            == levers.ENGINE_ADDRESSABLE)
    from_draw = levers.refusal_of(draw)
    from_capture = levers.refusal_of(captured)
    assert from_draw["kind"] == from_capture["kind"]
    assert from_draw["detail"] == from_capture["detail"]
    assert from_draw["group"] == "g1"

    result = levers.table([draw], source="rows.jsonl", unit="draw",
                          independence="family")
    assert result["unit"] == "draw"
    assert result["independence"] == "family"
    assert result["buckets"][levers.ENGINE_ADDRESSABLE]["units"] == 1
    assert result["families"][0]["independent"] == 1


def test_a_row_carrying_no_refusal_is_counted_but_not_bucketed():
    """A correct-and-native draw has no refusal. It must not read as a zero."""
    result = levers.table([{"case_id": "c", "status": "correct-native"}],
                          source="rows.jsonl", unit="draw")
    assert result["loaded"] == 1
    assert result["refusals"] == 0
    assert result["families"] == []


def test_it_degrades_without_an_engine(classified, monkeypatch):
    """§C12's shape: test the absent-engine path by removing the engine.

    Without it the closed layer decides nothing, and the kinds it would have
    decided must surface in the review queue rather than being re-bucketed by a
    declaration that was never allowed to name them.
    """
    monkeypatch.setattr(levers, "closed", lambda: frozenset())
    monkeypatch.setattr(levers, "oracle_modules", lambda: frozenset())
    result = levers.table(classified, source=CLASSIFIED, loaded=len(classified))
    assert result["engine"]["available"] is False
    assert "not importable" in levers.engine_note(result["engine"])
    assert result["undeclared"], "the closed kinds must become a review queue, not a guess"
    undeclared_kinds = set(r["kind"] for r in result["undeclared"])
    assert "glob-order" in undeclared_kinds
    assert sum(b["units"] for b in result["buckets"].values()) == result["refusals"]


def test_the_result_records_which_interpreter_answered_the_stdlib_layer(result):
    assert result["engine"]["stdlib_from"].startswith("3.")
    assert result["rule"] == levers.RULE


def test_compare_refuses_nothing_silently(result):
    comparison = levers.compare(result, {})
    assert comparison["agrees"] is True
    assert "no §4 table found" in levers.compare_report(result, comparison)
