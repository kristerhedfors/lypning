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
#: its prose; this table decides 193 entries mechanically (111 own-package, 73
#: on the engine's closed list, 9 not stdlib) and 450 through `DECLARED`, and it
#: lands 41 more entries in the fallback bucket and 36 fewer in the
#: engine-addressable one. Every one of the 26 families §4 itemises reproduces
#: unit for unit in §4's own bucket; the whole disagreement lives inside the 235
#: entries §4 counted but never named. The test does NOT demand agreement — it demands that
#: the disagreement stays the one a reviewer looked at. A new capture, a reworded
#: engine detail or an edited declaration moves a number here and fails, which is
#: the point.
SECTION_4_DELTA = {
    "self-referential": 0,
    "legitimate-fallback": +41,
    "engine-addressable": -36,
    "other": -5,
}

#: Per bucket: (families, declarations citing §4, declarations new here). The
#: four totals of `SECTION_4_DELTA` cannot see two equal-sized families trading
#: buckets, nor a `new` row relabelled `s4`; this can.
SECTION_4_COMPOSITION = {
    "self-referential": (13, 3, 9),
    "legitimate-fallback": (87, 5, 54),
    "engine-addressable": (59, 14, 45),
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
    assert result["engine"]["stdlib_from"].startswith("3.")
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
