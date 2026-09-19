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
import hashlib
import importlib
import json
import os

import pytest

from pipeline import levers
from pipeline.schema import make_case

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLASSIFIED = os.path.join(ROOT, "data", "classified.jsonl")
ASSESSMENT = os.path.join(ROOT, "ASSESSMENT.md")
SOURCE = os.path.join(ROOT, "pipeline", "levers.py")

#: The reviewed disagreement between this table and `ASSESSMENT.md` §4, as
#: measured on this tree on 2026-09-17 after Codex reviewed the S-ladder audit.
#: §4 bucketed by judgement per kind and could not be re-derived from
#: its prose; this table decides 193 entries mechanically (111 own-package, 73
#: on the engine's closed list, 9 not stdlib) and 450 through `DECLARED`, and it
#: lands 21 more entries in the fallback bucket and 16 fewer in the
#: engine-addressable one. Every one of the 26 families §4 itemises reproduces
#: unit for unit in §4's own bucket; the whole disagreement lives inside the 235
#: entries §4 counted but never named. The test does NOT demand agreement — it demands that
#: the disagreement stays the one a reviewer looked at. A new capture, a reworded
#: engine detail or an edited declaration moves a number here and fails, which is
#: the point.
SECTION_4_DELTA = {
    "self-referential": 0,
    "legitimate-fallback": +21,
    "engine-addressable": -16,
    "other": -5,
}

#: Per bucket: (families, declarations citing §4, declarations new here). The
#: four totals of `SECTION_4_DELTA` cannot see two equal-sized families trading
#: buckets, nor a `new` row relabelled `s4`; this can.
SECTION_4_COMPOSITION = {
    "self-referential": (13, 3, 9),
    "legitimate-fallback": (74, 5, 41),
    "engine-addressable": (72, 14, 58),
    "other": (0, 0, 0),
}

#: Every family whose declaration cites `ASSESSMENT.md` §4 as its source. A
#: citation is a claim about another document, so it is pinned like one.
SECTION_4_CITED = (
    "builtin: eval",
    "builtin: open() with a non-str path",
    "class: class definition",
    "decorator: decorated definition",
    "generator: yield expression",
    "module-attr: csv.writer",
    "module-attr: io.StringIO",
    "module-attr: sys.executable",
    "module-attr: sys.path",
    "module-attr: sys.version",
    "module: argparse",
    "module: binascii",
    "module: datetime",
    "module: itertools",
    "module: math",
    "module: struct",
    "module: subprocess",
    "module: textwrap",
    "module: time",
    "module: unicodedata",
    "module: urllib",
    "re: named group (?P<name>...)",
)

#: Three families §4 names in shorter words than the engine's own detail. Each
#: maps to the phrase §4 actually prints, so the citation check stays literal
#: rather than being loosened for everybody.
SECTION_4_ABBREVIATIONS = {
    "builtin: open() with a non-str path": "`open()` of a descriptor",
    "generator: yield expression": "`yield`",
    "re: named group (?P<name>...)": "named regex groups",
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


#: Below Python 3.10 there is no `sys.stdlib_module_names`, so the `not-stdlib`
#: layer abstains and the nine entries it decides here land in the review queue
#: instead. That is the module behaving correctly — an incomplete table saying
#: so — but every constant below was measured with the layer working, so the
#: tests that assert a complete table would read as a regression rather than as
#: the abstention they are.
STDLIB_LAYER = pytest.mark.skipif(
    levers.stdlib_names() is None,
    reason="no sys.stdlib_module_names: the not-stdlib layer abstains, "
           "so the table is incomplete by design and its constants do not apply")


#: How many closed kinds must appear together before a string, or a file, is
#: treating the list as a list rather than mentioning one kind in passing. Three
#: is enough: `refusals.closed_kinds()` holds 17, and no sentence here names
#: three of them and means prose.
RESTATEMENT = 3


def _tokens(text):
    """Words of a string, punctuation stripped, so `identity,` reads as a token.

    Substring matching was tried and is useless on English: `del` is inside
    "model", `math` inside "math.factorial", `random` inside "randomised".
    Exact tokens separate a mention from a copy.
    """
    out = set()
    for word in text.replace(",", " ").replace("'", " ").replace('"', " ").split():
        out.add(word.strip(".:;()[]{}`"))
    return out


def test_the_closed_list_is_not_restated():
    """No literal, and no collection of literals, re-spells the engine's list.

    Equality on one constant was not enough, and that is the finding this
    docstring records: a whole second copy written as one implicitly
    concatenated string — `frozenset("del dict-view ... set-order".split())` —
    folds to a SINGLE constant that equals no kind, and passed. So the guard
    asks the question that actually matters, in both spellings a copy can take:
    does any one string carry several of the kinds, and does the file as a whole
    carry several of them as bare constants?
    """
    closed = levers.closed()
    if not closed:
        pytest.skip("no engine here; the list this test guards is empty")
    with open(SOURCE, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    bare = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        text = node.value
        carried = _tokens(text) & closed
        assert len(carried) < RESTATEMENT, (
            "levers.py line %d spells %d of the engine's closed kinds in one string "
            "(%r). That is a second copy of lypning.engines.ONLY_CPYTHON_REFUSALS. "
            "Import it, never restate it." % (node.lineno, len(carried), sorted(carried)))
        if text in closed:
            bare.append((node.lineno, text))

    assert len(bare) < RESTATEMENT, (
        "levers.py names %d closed kinds as bare string constants (%r) — a copy of "
        "the engine's own list spread over several literals." % (len(bare), bare))


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
    """Invariant 8: modules return data, cli.py renders it.

    Three spellings, because matching only `print(...)` on a bare Name left
    `_emit = print; _emit(x)` and `sys.stdout.write(x)` both passing while the
    module wrote to stdout twice per call.
    """
    with open(SOURCE, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) \
                and node.value.id == "print":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases.add(target.id)
    banned = {"print"} | aliases
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            assert node.func.id not in banned, (
                "levers.py prints at line %d via %s" % (node.lineno, node.func.id))
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("write", "writelines"):
            stream = node.func.value
            if isinstance(stream, ast.Attribute) and isinstance(stream.value, ast.Name):
                assert not (stream.value.id == "sys" and stream.attr in ("stdout", "stderr")), (
                    "levers.py writes to sys.%s at line %d" % (stream.attr, node.lineno))


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


@STDLIB_LAYER
def test_every_family_is_decided(result):
    """The review queue is empty and no declaration is orphaned.

    Both halves fail loudly on purpose. A family nothing reaches is a judgement
    nobody made; a declaration nothing matches is a judgement whose subject
    moved, usually because an engine detail was reworded.
    """
    assert result["undeclared"] == [], [r["family"] for r in result["undeclared"]]
    assert result["declared_unused"] == []


@STDLIB_LAYER
def test_the_disagreement_with_section_4_is_the_reviewed_one(result):
    totals = levers.section4_totals(ASSESSMENT)
    assert set(totals) == set(levers.BUCKETS), totals
    comparison = levers.compare(result, totals)
    assert comparison["delta"] == SECTION_4_DELTA, (
        "the split moved against ASSESSMENT.md §4. That is not automatically wrong, "
        "but it is a new disagreement and it needs a reviewer, not a new constant.")


@STDLIB_LAYER
def test_the_delta_alone_cannot_see_a_paired_swap(result):
    """Aggregate totals are blind to two equal-sized families trading buckets.

    `module: time` and `module: textwrap` are both 8 units on this tree and sit
    in different buckets; swapping them inverts the lever call on both and moves
    no total at all. So the per-bucket composition is pinned too, not just the
    four sums.
    """
    counted = {}
    for name in levers.BUCKETS:
        row = result["buckets"][name]
        counted[name] = (row["families"], row["declared_s4"], row["declared_new"])
    assert counted == SECTION_4_COMPOSITION, (
        "a family changed bucket or provenance without moving a bucket total. "
        "That is exactly the edit the delta cannot see, so it is pinned here.")


def test_a_declaration_cannot_borrow_section_4s_authority(result):
    """`s4` means §4 named this family. It is a citation, and citations are pinned.

    Marking a family `s4` that §4 never named would let this tree's own judgement
    wear a reviewed judgement's authority, and the per-bucket counts a reader
    uses to tell the two apart would still add up.
    """
    cited = sorted(r["family"] for r in levers.declared_rows(result, provenance="s4"))
    assert cited == sorted(SECTION_4_CITED), (
        "the set of families claiming ASSESSMENT.md §4 as their source changed.")
    body = open(ASSESSMENT, "r", encoding="utf-8").read()
    section4 = body.split("## 4.")[1].split("## 5.")[0]
    for name in cited:
        token = SECTION_4_ABBREVIATIONS.get(name) or name.split(": ", 1)[1]
        assert token in section4, (
            "%s claims §4 as its source, but §4 does not name %r" % (name, token))


@STDLIB_LAYER
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


def _capture(kind, detail, day, ident):
    return {"outcome": "refused", "info": {"kind": kind, "detail": detail},
            "entry": {"id": ident, "program": detail, "first_seen": day + "T00:00:00Z",
                      "source": "hook", "count": 1}}


def test_the_ranking_is_independent_times_units():
    """`score` comes out of `table()`, not out of the fixture.

    The first form handed `rank()` pre-computed scores, so it tested the sort
    and not the formula: replacing `score = independent * units` with
    `score = units` left it green while the build order silently became the raw
    count — the exact thing the module's docstring says the count cannot do.
    """
    records = (
        # nine entries of one family, all on two days: one afternoon's re-runs.
        [_capture("module", "import itertools", "2026-09-0%d" % (1 + i % 2), "a%d" % i)
         for i in range(9)]
        # eight of another, spread over five days: recurring evidence.
        + [_capture("module", "import textwrap", "2026-09-1%d" % (i % 5), "b%d" % i)
           for i in range(8)]
    )
    result = levers.table(records, source="synthetic")
    by_family = dict((r["family"], r) for r in result["families"])
    assert by_family["module: itertools"]["units"] == 9
    assert by_family["module: itertools"]["independent"] == 2
    assert by_family["module: itertools"]["score"] == 18
    assert by_family["module: textwrap"]["units"] == 8
    assert by_family["module: textwrap"]["independent"] == 5
    assert by_family["module: textwrap"]["score"] == 40
    order = [r["family"] for r in levers.rank(result)]
    assert order[:2] == ["module: textwrap", "module: itertools"], (
        "nine entries over two days must not outrank eight over five")
    for row in result["families"]:
        assert row["score"] == row["independent"] * row["units"]


def test_the_s0b_join_uses_the_key_eval2_rows_uses():
    """Rung S0b is a JOIN, and the key is `corpus_id`, not `case_id`.

    `eval2_rows.row_for` writes `case_id` as the case's SOURCE id and keeps the
    attempt's own id in `corpus_id`; the replay is keyed by the attempt's. Using
    `case_id` matched nothing on a real run and produced an EMPTY table with
    every bucket at zero — measured on `qwen38-baseline-k16`, 2026-09-16, where
    197 correct-fallback draws all failed to match. That is the failure this
    test exists for, and it is why the unmatched count is returned rather than
    the table simply coming out small.
    """
    drawn = [{"case_id": "src-1", "corpus_id": "py-1", "draw": 0,
              "status": "correct-fallback", "family": "f", "split_group": "g1"},
             {"case_id": "src-1", "corpus_id": "py-1", "draw": 1,
              "status": "correct-fallback", "family": "f", "split_group": "g2"},
             {"case_id": "src-2", "corpus_id": "py-2", "draw": 0,
              "status": "correct-native", "family": "f2", "split_group": "g3"}]
    replay = [{"case_id": "py-1", "sample": 0, "verdict": "UNSUPPORTED",
               "detail": "module", "blocker": "module: import itertools"},
              {"case_id": "py-1", "sample": 1, "verdict": "UNSUPPORTED",
               "detail": "module", "blocker": "module: import unicodedata"},
              {"case_id": "py-2", "sample": 0, "verdict": "MATCH"}]

    joined = levers.draw_refusals(drawn, replay, status="correct-fallback")
    assert joined["considered"] == 2, "the native draw must not be considered"
    assert joined["unmatched"] == 0, "the join key must be corpus_id"
    assert len(joined["records"]) == 2

    result = levers.table(joined["records"], source="runs/x", unit="draw",
                          independence="family", loaded=len(drawn))
    assert result["unit"] == "draw" and result["loaded"] == 3
    assert result["buckets"][levers.ENGINE_ADDRESSABLE]["units"] == 2


def test_a_fallback_draw_with_no_refusal_is_reported_and_never_a_zero():
    """A hole in the evidence is not a family with no mass."""
    drawn = [{"corpus_id": "py-1", "draw": 0, "status": "correct-fallback",
              "family": "f", "split_group": "g"},
             {"corpus_id": "py-2", "draw": 0, "status": "correct-fallback",
              "family": "f", "split_group": "g"}]
    replay = [{"case_id": "py-2", "sample": 0, "verdict": "UNSUPPORTED"}]  # no blocker
    joined = levers.draw_refusals(drawn, replay, status="correct-fallback")
    assert joined["considered"] == 2
    assert joined["unmatched"] == 1
    assert joined["without_refusal"] == 1
    assert joined["records"] == []


def test_the_family_independence_branch_is_the_split_group():
    """`unit="draw"` clusters by split group, not by capture day — S0b's basis.

    Collapsing the branch to `len(days)` left every test green while the draw
    table silently counted a proxy that draw rows do not even carry.
    """
    records = [{"kind": "module", "detail": "import itertools", "unit_id": "c%d" % i,
                "program": "", "day": None, "source": "correct-fallback",
                "count": None, "group": "g%d" % (i % 2)} for i in range(3)]
    by_family = levers.table(records, source="x", unit="draw", independence="family")
    assert by_family["families"][0]["independent"] == 2, "two split groups over three draws"
    by_day = levers.table(records, source="x", unit="draw")
    assert by_day["families"][0]["independent"] == 3, "no day: each draw its own singleton"


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
    """Which interpreter answered, or None when none could.

    The `not-stdlib` layer's answer belongs to the interpreter running the tool,
    so the result says which one it was — and says `None` rather than a version
    when the layer abstained, because an abstention that reports a version reads
    as an answer.
    """
    answered = result["engine"]["stdlib_from"]
    if levers.stdlib_names() is None:
        assert answered is None
    else:
        assert answered.startswith("3.")
    assert result["rule"] == levers.RULE


def test_compare_never_agrees_with_a_table_it_could_not_read(result):
    """An un-comparison is not an agreement.

    The earlier form skipped absent buckets and then took `all()` over what was
    left, so a §4 table that had been renamed, reformatted or half-parsed came
    back as "reproduces §4 on all four buckets" — and `--strict` exited 0 on it.
    """
    nothing = levers.compare(result, {})
    assert nothing["agrees"] is False
    assert nothing["missing"] == list(levers.BUCKETS)
    assert "could NOT compare" in levers.compare_report(result, nothing)

    partial = levers.compare(result, {levers.SELF_REFERENTIAL: 195})
    assert partial["agrees"] is False, "one matching bucket is not four"
    assert levers.ENGINE_ADDRESSABLE in partial["missing"]


def test_section_4_totals_read_the_cell_and_not_the_line(tmp_path):
    """Another table's row must not supply a bucket's count.

    The first form matched a bucket name anywhere in the line, so any row in the
    document whose first cell merely contained "other" won — earlier rows first,
    silently. Both halves are pinned: a decoy before §4 and a decoy inside it.
    """
    body = open(ASSESSMENT, "r", encoding="utf-8").read()
    decoy = "| another view of the same corpus | 42 | nothing |\n"
    before = tmp_path / "before.md"
    before.write_text(body.replace("## 4.", decoy + "\n## 4.", 1), encoding="utf-8")
    assert levers.section4_totals(str(before)) == levers.section4_totals(ASSESSMENT)

    inside = tmp_path / "inside.md"
    inside.write_text(body.replace("| other ", decoy + "| other ", 1), encoding="utf-8")
    assert levers.section4_totals(str(inside))[levers.OTHER] == 5


def _run_cli(argv, capsys):
    from pipeline import cli

    code = cli.main(argv)
    return code, capsys.readouterr()


@STDLIB_LAYER
def test_the_command_reports_what_it_loaded(capsys):
    """Invariant 3: the tool prints the count it loaded, with its source."""
    code, out = _run_cli(["levers"], capsys)
    assert code == 0
    assert "carry a refusal" in out.out
    assert "classified.jsonl" in out.out
    for name in levers.BUCKETS:
        assert name in out.out


def test_strict_fails_on_a_section_4_it_could_not_read(tmp_path, capsys):
    """`--strict` must not exit 0 on a comparison that did not happen."""
    empty = tmp_path / "no-table.md"
    empty.write_text("# nothing here\n", encoding="utf-8")
    code, out = _run_cli(["levers", "--against", str(empty), "--strict"], capsys)
    assert code == 1
    assert "could NOT compare" in out.out


def test_a_missing_against_file_is_a_usage_error(tmp_path, capsys):
    code, out = _run_cli(["levers", "--against", str(tmp_path / "nope.md")], capsys)
    assert code == 2
    assert "no such file" in out.err


def test_status_without_draws_is_a_usage_error(capsys):
    """`--status` labels draws; the capture population has none."""
    code, out = _run_cli(["levers", "--status", "correct-fallback"], capsys)
    assert code == 2
    assert "draw rows" in out.err


def test_rows_without_replay_is_a_usage_error(tmp_path, capsys):
    """Half the join is not the join, and an empty table is not an answer."""
    rows = tmp_path / "rows.jsonl"
    rows.write_text("", encoding="utf-8")
    code, out = _run_cli(["levers", "--rows", str(rows)], capsys)
    assert code == 2
    assert "carries no refusal" in out.err


def test_a_draw_table_carries_the_held_out_banner(tmp_path, capsys):
    """A build order read off a benchmark is test-set steering.

    `refusals.py` carries this banner for exactly this reason, and this command
    can be pointed at an eval-2 arm, so it carries the same one.
    """
    rows = tmp_path / "rows.jsonl"
    rows.write_text(json.dumps({"corpus_id": "py-1", "draw": 0, "family": "f",
                                "split_group": "g", "status": "correct-fallback"}) + "\n",
                    encoding="utf-8")
    replay = tmp_path / "replay.jsonl"
    replay.write_text(json.dumps({"case_id": "py-1", "sample": 0,
                                  "verdict": "UNSUPPORTED",
                                  "blocker": "module: import itertools"}) + "\n",
                      encoding="utf-8")
    code, out = _run_cli(["levers", "--rows", str(rows), "--replay", str(replay),
                          "--status", "correct-fallback"], capsys)
    assert code == 0
    assert "HELD-OUT SET" in out.out
    assert "1 carried a refusal" in out.out

    code, out = _run_cli(["levers", "--rows", str(rows), "--replay", str(replay),
                          "--status", "correct-fallback", "--rank"], capsys)
    assert code == 2
    assert "refusing --rank" in out.err and "--vector" in out.err

    code, out = _run_cli(["levers", "--rows", str(rows), "--replay", str(replay),
                          "--status", "correct-fallback", "--vector"], capsys)
    assert code == 0
    assert "descriptive refusal vector" in out.out
    assert "not a rank" in out.out and "score" not in out.out


def test_an_engine_that_is_not_a_file_is_refused_before_anything_is_replayed(tmp_path,
                                                                            capsys):
    """Rung S0b's `--engine` was never existence-checked, and that was the hole.

    `engine = args.engine or eng.engine_path(...)` then `if not engine` passes a
    nonexistent path, because a path is a truthy string. Every program then
    graded ERROR, no draw carried a refusal, and the vector printed EMPTY at
    exit 0 — while the considered population GREW, because `--run` re-derives
    `native` from this binary. On `runs/stock-nothinking`, 14 correct-fallback
    draws with the built engine became 26 with a broken one, all of them
    refusal-less. A vector of nothing is the one answer rung S0b must never be
    able to publish by accident.
    """
    code, out = _run_cli(["levers", "--run", "stock-nothinking",
                          "--status", "correct-fallback", "--vector",
                          "--engine", str(tmp_path / "not-built")], capsys)
    assert code == 2
    assert out.err.strip() == "not a file: %s" % (tmp_path / "not-built")
    assert out.out == ""


def test_absent_draw_rows_are_a_usage_error_and_never_an_empty_vector(tmp_path, capsys):
    """The `--rows`/`--replay` half of the same hole: `read_jsonl` answers `[]`."""
    rows = tmp_path / "rows.jsonl"
    rows.write_text(json.dumps({"corpus_id": "py-1", "draw": 0, "family": "f",
                                "split_group": "g", "status": "correct-fallback"}) + "\n",
                    encoding="utf-8")
    missing = tmp_path / "missing.jsonl"
    for rows_arg, replay_arg in ((missing, rows), (rows, missing)):
        code, out = _run_cli(["levers", "--rows", str(rows_arg), "--replay",
                              str(replay_arg), "--vector"], capsys)
        assert code == 2
        assert out.err.strip() == "not a file: %s" % missing
        assert out.out == ""


def test_an_engine_that_cannot_execute_is_a_failed_replay_and_not_a_vector(tmp_path,
                                                                          capsys):
    """The `is_file` check above is not enough on its own.

    An engine that EXISTS but cannot execute passes it, and then reproduces the
    whole defect one step later: every program grades ERROR, no draw carries a
    refusal, and the empty vector prints at exit 0 — over a population this
    binary inflated, because `--run` re-derives `native` from the same failed
    replay. Nothing graded is not a refusal vector of zero.
    """
    engine = tmp_path / "not-executable"
    engine.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    engine.chmod(0o644)
    code, out = _run_cli(["levers", "--run", "stock-nothinking",
                          "--status", "correct-fallback", "--vector", "--limit", "0",
                          "--engine", str(engine)], capsys)
    assert code == 1
    assert "incomplete replay" in out.err and "not a refusal vector" in out.err
    assert "ERROR" in out.err
    assert "descriptive refusal vector" not in out.out


@pytest.fixture()
def draw_tree(tmp_path, monkeypatch):
    """A one-attempt run in a temp tree: rung S0b's shape without the corpus.

    The guards below are all reachable by replaying `runs/stock-nothinking`, as
    the two tests above do, but that costs a process per recorded program and a
    recorded run that a fresh checkout may not have. One synthetic attempt takes
    the same code path — `legality.replay` really runs the engine on it — so
    these tests can say which engine graded what.
    """
    (tmp_path / "data").mkdir()
    case = dict(make_case(prompt="p a", test={"kind": "stdout", "expect_stdout": "1\n"},
                          category="unobserved", source_id="a",
                          tags=["family:fa", "group:g", "population:coverage"]),
                id="ntx-a")
    (tmp_path / "data" / "corpus.jsonl").write_text(json.dumps(case) + "\n",
                                                    encoding="utf-8")
    for run_id, attempts in (("r1", [{"case_id": "ntx-a", "sample": 0,
                                      "program": "print(1)", "passed": True}]),
                             ("rempty", [])):
        d = tmp_path / "runs" / run_id
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"sampling": {"seed": 7}}),
                                     encoding="utf-8")
        (d / "attempts.jsonl").write_text(
            "".join(json.dumps(a) + "\n" for a in attempts), encoding="utf-8")
    monkeypatch.setenv("NTX_ROOT", str(tmp_path))
    from pipeline import cli
    importlib.reload(cli)
    yield tmp_path
    monkeypatch.delenv("NTX_ROOT", raising=False)
    importlib.reload(cli)


def _unexecutable(tmp_path):
    """An engine that exists — so `is_file` passes — and cannot be executed."""
    engine = tmp_path / "not-executable"
    engine.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    engine.chmod(0o644)
    return engine


def test_a_present_but_empty_attempts_file_publishes_no_vector(draw_tree, capsys):
    """`--run` checked `.exists()`, and a 0-byte `attempts.jsonl` exists.

    Nothing is replayed, nothing is graded, no route prints a warning, and the
    old guard's second term (`joined["considered"]`) is 0 — so the whole
    descriptive vector printed, banner and all, at exit 0 over a run that had
    recorded no attempt at all.
    """
    code, out = _run_cli(["levers", "--run", "rempty", "--status", "correct-fallback",
                          "--vector", "--limit", "0"], capsys)
    assert code == 1
    assert "no refusal to publish" in out.err and "read of nothing" in out.err
    assert out.out == ""


def test_a_status_filter_that_matches_no_draw_publishes_no_vector(draw_tree, capsys):
    """`--status correct-native` under a failed replay: the guard's blind spot.

    Every program grades ERROR, so `native` is False on every row and no draw
    is correct-native. `considered` is therefore 0, which falsified the old
    guard's second term — the one route where a warning was printed on stderr
    and the empty vector still went to stdout at exit 0.
    """
    code, out = _run_cli(["levers", "--run", "r1", "--status", "correct-native",
                          "--vector", "--limit", "0",
                          "--engine", str(_unexecutable(draw_tree))], capsys)
    assert code == 1
    assert "ERROR 1" in out.err
    # The incomplete-replay guard answers this one, and names the ERROR that
    # made the population empty; the backstop below it never has to.
    assert "incomplete replay" in out.err and "1 ERROR" in out.err
    assert out.out == ""


def test_an_engine_that_runs_but_is_not_lypning_publishes_no_vector(draw_tree, capsys):
    """An engine can execute, grade, and still be the wrong program entirely.

    `/bin/echo` accepts the arguments and exits 0 with the wrong stdout, which
    grades MISMATCH rather than ERROR — so `errors` is 0 and neither the ERROR
    warning nor the old guard sees anything. The draw is considered, carries no
    refusal, and the vector printed empty at exit 0 beside a MISMATCH line.
    """
    code, out = _run_cli(["levers", "--run", "r1", "--status", "correct-fallback",
                          "--vector", "--limit", "0", "--engine", "/bin/echo"], capsys)
    assert code == 1
    assert "MISMATCH 1" in out.err
    assert "incomplete replay" in out.err and "1 MISMATCH" in out.err
    assert out.out == ""


def test_two_empty_draw_files_publish_no_vector(tmp_path, capsys):
    """The `--rows`/`--replay` route: both files exist, both are 0 bytes.

    `is_file` passes twice, `read_jsonl` answers `[]` twice, and the join of
    nothing with nothing rendered the full vector at exit 0 — the form a
    reviewer reproduced by hand before this guard existed.
    """
    rows, replay = tmp_path / "rows.jsonl", tmp_path / "replay.jsonl"
    rows.write_text("", encoding="utf-8")
    replay.write_text("", encoding="utf-8")
    code, out = _run_cli(["levers", "--rows", str(rows), "--replay", str(replay),
                          "--vector", "--limit", "0"], capsys)
    assert code == 1
    assert "0 draw row(s) loaded" in out.err and "0 carried a refusal" in out.err
    assert out.out == ""


def test_the_read_of_nothing_is_refused_in_every_render_mode(tmp_path, capsys):
    """One guard, not one per renderer — `--json` returned 0 before the render.

    A refusal that only covers `--vector` is not a refusal: `--json` is the mode
    a script reads, and the default report, `--declared` and `--undeclared` all
    describe the same empty population. `--rank` stays a usage error at 2,
    because a wrong flag is answered before a failed read.
    """
    rows, replay = tmp_path / "rows.jsonl", tmp_path / "replay.jsonl"
    rows.write_text("", encoding="utf-8")
    replay.write_text("", encoding="utf-8")
    base = ["levers", "--rows", str(rows), "--replay", str(replay)]
    for mode in ([], ["--json"], ["--vector"], ["--declared"], ["--undeclared"]):
        code, out = _run_cli(base + mode, capsys)
        assert code == 1, mode
        assert "no refusal to publish" in out.err, mode
        assert out.out == "", mode
    code, out = _run_cli(base + ["--rank"], capsys)
    assert code == 2
    assert "refusing --rank" in out.err
    assert out.out == ""


def test_one_refusal_is_enough_to_publish_the_draw_vector(tmp_path, capsys):
    """The guard must not be satisfiable by refusing everything.

    A single draw that carries a single refusal is a vector one record backs,
    which is the whole admission rule — so it still renders, with the held-out
    banner, at exit 0.
    """
    rows = tmp_path / "rows.jsonl"
    rows.write_text(json.dumps({"corpus_id": "py-1", "draw": 0, "family": "f",
                                "split_group": "g", "status": "correct-fallback"}) + "\n",
                    encoding="utf-8")
    replay = tmp_path / "replay.jsonl"
    replay.write_text(json.dumps({"case_id": "py-1", "sample": 0,
                                  "verdict": "UNSUPPORTED",
                                  "blocker": "module: import itertools"}) + "\n",
                      encoding="utf-8")
    code, out = _run_cli(["levers", "--rows", str(rows), "--replay", str(replay),
                          "--status", "correct-fallback", "--vector"], capsys)
    assert code == 0
    assert "1 carried a refusal" in out.out
    assert "HELD-OUT SET" in out.out
    assert "descriptive refusal vector" in out.out
    assert "no refusal to publish" not in out.err


def test_the_refusal_says_which_read_of_nothing_happened(tmp_path, capsys):
    """Three rows that each carry a refusal, and a `--status` matching none.

    The counts have to separate the cases, because the note that carried them
    went to stdout and a refusal leaves stdout empty. `loaded` alone would put
    "3 draw row(s) loaded … 0 carried a refusal" on stderr over a file in which
    every row carries one, which is the same confusion the guard exists to
    prevent, moved into the diagnostic.
    """
    rows, replay = tmp_path / "rows.jsonl", tmp_path / "replay.jsonl"
    rows.write_text("".join(
        json.dumps({"corpus_id": "py-%d" % i, "draw": 0, "family": "f",
                    "split_group": "g%d" % i, "status": "correct-native"}) + "\n"
        for i in range(3)), encoding="utf-8")
    replay.write_text("".join(
        json.dumps({"case_id": "py-%d" % i, "sample": 0, "verdict": "UNSUPPORTED",
                    "blocker": "module: import itertools"}) + "\n"
        for i in range(3)), encoding="utf-8")
    code, out = _run_cli(["levers", "--rows", str(rows), "--replay", str(replay),
                          "--status", "correct-fallback", "--vector", "--limit", "0"],
                         capsys)
    assert code == 1
    assert "3 draw row(s) loaded" in out.err
    assert "0 match status correct-fallback" in out.err
    assert out.out == ""
    # And the same three rows under the status they do carry are a vector.
    code, out = _run_cli(["levers", "--rows", str(rows), "--replay", str(replay),
                          "--status", "correct-native", "--vector", "--limit", "0"],
                         capsys)
    assert code == 0
    assert "3 draw(s) match status correct-native; 3 carried a refusal" in out.out


def test_a_join_on_the_wrong_key_is_not_a_census_without_refusals(tmp_path, capsys):
    """A replay whose rows match nothing, against one that matches and is bare.

    Both read nothing and both exit 1, and the operator's next move differs:
    one is a key to fix, the other is a census that dropped `blocker`. Only the
    unmatched count tells them apart on stderr.
    """
    row = json.dumps({"corpus_id": "py-1", "draw": 0, "family": "f",
                      "split_group": "g", "status": "correct-fallback"}) + "\n"
    rows = tmp_path / "rows.jsonl"
    rows.write_text(row, encoding="utf-8")
    wrong_key = tmp_path / "wrong.jsonl"
    wrong_key.write_text(json.dumps({"case_id": "py-other", "sample": 0,
                                     "verdict": "UNSUPPORTED",
                                     "blocker": "module: import itertools"}) + "\n",
                         encoding="utf-8")
    bare = tmp_path / "bare.jsonl"
    bare.write_text(json.dumps({"case_id": "py-1", "sample": 0,
                                "verdict": "OK"}) + "\n", encoding="utf-8")
    base = ["levers", "--rows", str(rows), "--status", "correct-fallback",
            "--vector", "--limit", "0"]
    code, out = _run_cli(base + ["--replay", str(wrong_key)], capsys)
    assert code == 1
    assert "incomplete join" in out.err and "1 unmatched" in out.err
    assert out.out == ""
    code, out = _run_cli(base + ["--replay", str(bare)], capsys)
    assert code == 1
    assert "incomplete join" in out.err and "1 without a refusal" in out.err
    assert out.out == ""


def test_s0b_freezes_the_population_and_pins_the_explicit_engine(tmp_path, capsys,
                                                                monkeypatch):
    """The pilot rows define the 171; today's engine only supplies refusal kinds."""
    from pipeline import cli, legality

    run = tmp_path / "runs" / "pilot"
    run.mkdir(parents=True)
    (run / "attempts.jsonl").write_text(
        json.dumps({"case_id": "py-1", "sample": 0, "program": "import itertools"}) + "\n",
        encoding="utf-8")
    population = tmp_path / "population.jsonl"
    population.write_text(json.dumps({
        "case_id": "source-1", "corpus_id": "py-1", "draw": 0,
        "family": "f", "split_group": "g", "status": "correct-fallback",
    }) + "\n", encoding="utf-8")
    engine = tmp_path / "lypning-l"
    engine.write_text("#!/bin/sh\necho 'lypning 0.1.0 (lypning-l) for cpython 3.12'\n",
                      encoding="utf-8")
    engine.chmod(0o755)
    digest = hashlib.sha256(engine.read_bytes()).hexdigest()

    monkeypatch.setattr(cli, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(cli, "_case_context", lambda *_: ({}, {}))

    def replay(attempts, explicit, **_kwargs):
        assert explicit == str(engine)
        assert [(a["case_id"], a["sample"]) for a in attempts] == [("py-1", 0)]
        return {"rows": [{"case_id": "py-1", "sample": 0,
                           "verdict": "UNSUPPORTED",
                           "blocker": "module: import itertools"}],
                "tally": {}, "details": {}, "blockers": {}, "programs": 1}

    monkeypatch.setattr(legality, "replay", replay)
    base = ["levers", "--run", "pilot", "--population-rows", str(population),
            "--status", "correct-fallback", "--engine", str(engine),
            "--require-engine-sha256", digest, "--vector"]
    code, out = _run_cli(base + ["--expect-draws", "1"], capsys)
    assert code == 0
    assert "@ replay engine sha256 %s" % digest in out.out
    assert "1 draw(s) match status correct-fallback; 1 carried a refusal" in out.out
    assert "descriptive refusal vector" in out.out

    code, out = _run_cli(base + ["--expect-draws", "171"], capsys)
    assert code == 1
    assert "expected exactly 171" in out.err
    assert "descriptive refusal vector" not in out.out

    wrong = "0" * 64
    bad = [word if word != digest else wrong for word in base]
    code, out = _run_cli(bad + ["--expect-draws", "1"], capsys)
    assert code == 1
    assert "required %s" % wrong in out.err
    assert out.out == ""


def test_a_partial_draw_join_is_not_publishable(tmp_path, capsys):
    rows = tmp_path / "rows.jsonl"
    rows.write_text(json.dumps({"corpus_id": "py-1", "draw": 0,
                                "status": "correct-fallback"}) + "\n",
                    encoding="utf-8")
    replay = tmp_path / "replay.jsonl"
    replay.write_text("", encoding="utf-8")
    code, out = _run_cli(["levers", "--rows", str(rows), "--replay", str(replay),
                          "--status", "correct-fallback", "--vector"], capsys)
    assert code == 1
    assert "incomplete join" in out.err and "1 unmatched" in out.err
    assert "descriptive refusal vector" not in out.out


def test_eval2_rows_refuses_an_engine_that_is_not_a_file(tmp_path, capsys):
    """The escape hatch that S0's handoff used to offer, closed at the source.

    `native` is read off this replay and `status` is read off `native`, so this
    binary IS the population the rows define. A path that is not a file is a
    truthy string: every program would grade ERROR, every draw would be written
    non-native, and the rows would be a population produced by a replay that ran
    nothing. `levers` already answers 2 here; this is the same usage error and
    must answer alike.
    """
    code, out = _run_cli(["eval2-rows", "eval-does-not-matter",
                          "--engine", str(tmp_path / "no-such-binary"),
                          "--output", str(tmp_path / "rows.jsonl")], capsys)
    assert code == 2
    assert "not a file" in out.err
    assert out.out == ""
    assert not (tmp_path / "rows.jsonl").exists(), "a refused run writes no rows"


def test_eval2_rows_refuses_a_replay_that_did_not_grade(tmp_path, monkeypatch, capsys):
    """The step past `is_file`: a regular file that will not execute.

    No `+x` bit, wrong architecture, a text placeholder — each grades every
    program ERROR, which writes `native` False on every row and every correct
    draw as `correct-fallback`. That is the same population from a replay that
    ran nothing, and a hand-transferred binary losing its execute bit is the
    ordinary way to arrive at it. The rows must not be written.
    """
    import importlib

    monkeypatch.setenv("NTX_ROOT", str(tmp_path))
    from pipeline import cli

    from pipeline.jsonio import write_jsonl

    importlib.reload(cli)
    try:
        (tmp_path / "data").mkdir()
        write_jsonl(tmp_path / "data" / "corpus.jsonl", [
            dict(make_case(prompt="p", test={"kind": "stdout", "expect_stdout": "1\n"},
                           category="unobserved", source_id="a",
                           tags=["family:fa", "group:g", "population:coverage"]),
                 id="ntx-a")])
        run = tmp_path / "runs" / "r1"
        run.mkdir(parents=True)
        (run / "meta.json").write_text(json.dumps({"sampling": {"seed": 7}}))
        write_jsonl(run / "attempts.jsonl",
                    [{"case_id": "ntx-a", "sample": 0, "program": "print(1)", "passed": True}])
        # A regular file, so `is_file` admits it, that cannot be executed.
        engine = tmp_path / "unexecutable-engine"
        engine.write_text("#!/bin/sh\nexit 0\n")
        engine.chmod(0o644)
        out_path = tmp_path / "rows.jsonl"
        code, out = _run_cli(["eval2-rows", "r1", "--engine", str(engine),
                              "--output", str(out_path)], capsys)
        assert code == 1
        assert "did not grade (ERROR)" in out.err
        assert out.out == ""
        assert not out_path.exists(), "a population from an ungraded replay is not written"
    finally:
        monkeypatch.delenv("NTX_ROOT", raising=False)
        importlib.reload(cli)


def test_eval2_rows_stamps_the_binary_it_replayed_not_the_installed_chain(capsys):
    """Invariant 3's other half: the number carries the identity that produced it.

    `identity()["fingerprint"]` hashes whichever `lypning-l`/`lypning` this host
    has installed. For an explicit historical `--engine` that names binaries the
    replay never executed, and this line is the only provenance the rows file
    carries — `eval2_rows.row_for` records a verdict and no identity. So the
    printed sha256 must be the argument's own.
    """
    from pipeline import cli, engines as eng

    src = ast.parse(open(cli.__file__, "r", encoding="utf-8").read())
    fn = next(n for n in ast.walk(src)
              if isinstance(n, ast.FunctionDef) and n.name == "cmd_eval2_rows")
    body = ast.dump(fn)
    assert "binary_identity" in body, "the rows must name the bytes that graded them"
    assert "fingerprint" not in body, (
        "the installed-chain fingerprint cannot identify an explicit --engine")

    here = eng.binary_identity(__file__)
    assert here["path"] == __file__ and len(here["sha256"]) == 64


def test_a_rule_declaration_outliving_its_rule_is_orphaned(monkeypatch):
    """`rule` provenance is matched by the rule existing, never by a corpus family.

    The reverse of `test_repair_rules.test_no_rule_rewrites_a_module_the_engine_serves`:
    that one fails when the engine catches up with a rule, this one fails when a
    rule is withdrawn and its verdict is left behind as a judgement with no subject.
    """
    from pipeline import repair_rules

    assert any(row[2] == levers.FROM_RULE for row in levers.DECLARED)
    assert "module: bisect" in levers.rule_matched()
    monkeypatch.setattr(repair_rules, "MODULES",
                        tuple(m for m in repair_rules.MODULES if m != "bisect"))
    assert "module: bisect" not in levers.rule_matched()
    assert "module: heapq" in levers.rule_matched()
