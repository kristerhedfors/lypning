"""The stdlib corpus as a measurable SFT arm: the prompt, and the overlap gate.

WHY THIS FILE EXISTS. Two claims carry the arm and neither is self-evident from
reading `stdlib_sft.py`. The first is that a unit cannot be a bank case, which
is a property of `training_data.validate_cases` and is asserted here against
the real function rather than restated in a docstring. The second is that the
overlap gate can actually see a benchmark's families — it could not, until
2026-09-19: the reduction split a held-out token on `.` only, so the
hyphenated families `synth.family_of` produces (`textwrap-fill`) matched
nothing and the gate reported a clean bank while every unit overlapped it. A
gate that cannot fire is the defect this programme keeps paying for.
"""

from __future__ import annotations

import pytest

from pipeline import stdlib_sft
from pipeline.training_data import validate_cases
from pipeline.training_types import TrainingError


def unit(name="textwrap_wrap", reference="textwrap", fills=("textwrap.wrap",)):
    return {"schema": stdlib_sft.SCHEMA, "id": "lys-" + name, "name": name,
            "reference": reference, "fills": list(fills),
            "helpers": "def wrap(s, w):\n    return [s[:w]]", "cases": 'print(wrap("abcd", 2))',
            "requires": "lypning-l", "source_sha256": "a" * 64}


def test_a_unit_cannot_be_a_bank_case_which_is_why_this_arm_exists():
    """Asserted against the real validator, not claimed in prose.

    A unit has no stdin, no argv and one fixed stdout. `validate_cases` needs
    three independently specified tests whose outputs differ, because a case
    its tests cannot discriminate cannot fail a wrong program.
    """
    case = {"case_id": "x", "family": "f", "source_group": "f", "capabilities": ["c"],
            "task": "t", "reference": "print(1)", "population": "coverage",
            "provenance": "p", "review": {}, "tests": [{"stdin": "", "stdout": "1\n"}]}
    with pytest.raises(TrainingError) as exc:
        validate_cases([case])
    assert "three" in str(exc.value)


def test_the_prompt_determines_the_completion():
    """The checks ride in the task, so a model cannot answer with its own."""
    row = stdlib_sft.to_row(unit())
    assert 'print(wrap("abcd", 2))' in row["task"]
    assert "without importing textwrap" in row["task"]
    assert row["reference"].startswith("def wrap")
    assert "# --- cases ---" in row["reference"]
    # Prompt hygiene, as `training.messages` keeps for the bank.
    for forbidden in ("lypning", "engine", "exit 90", "unsupported", "refus"):
        assert forbidden not in row["task"].lower()


def test_a_builtin_unit_is_not_told_to_avoid_an_import_it_never_had():
    """`dict.fromkeys` arrives with no module; the module clause has no referent."""
    row = stdlib_sft.to_row(unit(name="dictmethods_fromkeys", reference="", fills=("dict.fromkeys",)))
    assert "without importing" not in row["task"]
    assert "from scratch" in row["task"]
    assert row["stdlib"]["module"] == ""


@pytest.mark.parametrize("held,why", [
    ("textwrap", "a bare capability label"),
    ("textwrap.fill", "a dotted surface"),
    ("textwrap-fill", "a synth.family_of family, which hyphenates"),
    ("textwrap-wrap-long-words", "a family with several hyphens"),
])
def test_the_gate_sees_a_benchmark_family_in_every_spelling_it_arrives_in(held, why):
    """The 2026-09-19 defect: only the dotted spelling reduced, so none matched."""
    result = stdlib_sft.sft_rows([unit()], held_out=[held])
    assert result["rows"] == [], why
    assert result["overlap"] == {"textwrap": ["textwrap_wrap"]}, why
    assert result["dropped"] == ["textwrap_wrap"]


def test_a_module_the_benchmark_does_not_reach_is_kept():
    result = stdlib_sft.sft_rows([unit(), unit(name="base64_codec", reference="base64",
                                             fills=("base64.b32encode",))],
                                 held_out=["textwrap-fill"])
    assert [r["stdlib"]["unit"] for r in result["rows"]] == ["base64_codec"]
    assert result["modules"] == ["base64"]


def test_allow_overlap_keeps_them_and_says_so_where_a_reader_will_see_it():
    kept = stdlib_sft.sft_rows([unit()], held_out=["textwrap"], allow_overlap=True)
    assert len(kept["rows"]) == 1 and kept["dropped"] == []
    assert "KEPT (--allow-overlap)" in stdlib_sft.render(kept, allow_overlap=True)
    # The warning about reading the result as contaminated belongs to the run
    # that HELD units back, which is the one whose reader still has a choice.
    held = stdlib_sft.sft_rows([unit()], held_out=["textwrap"])
    assert "contaminated" in stdlib_sft.render(held)


def test_an_arm_with_nothing_left_to_mix_says_so_rather_than_reporting_zero_rows():
    """Zero rows is a finding about the bank, not a clean read of an empty corpus."""
    result = stdlib_sft.sft_rows([unit()], held_out=["textwrap"])
    assert result["rows"] == []
    assert "NOTHING TO MIX" in stdlib_sft.render(result)


def test_a_blank_capability_label_does_not_reach_every_builtin_unit():
    """An empty held-out token reduces to "", which must not match a module-less unit."""
    result = stdlib_sft.sft_rows([unit(name="d", reference="", fills=("dict.fromkeys",))],
                                 held_out=["", "  "])
    assert len(result["rows"]) == 1 and result["overlap"] == {}


def test_a_row_carries_the_provenance_that_lets_an_arm_be_read_afterwards():
    row = stdlib_sft.to_row(unit())
    assert row["stdlib"]["unit_id"] == "lys-textwrap_wrap"
    assert row["stdlib"]["fills"] == ["textwrap.wrap"]
    assert row["stdlib"]["source_sha256"] == "a" * 64
    assert row["case_id"].startswith("lys-sft-")


def test_a_unit_missing_what_a_row_needs_is_refused_by_name():
    with pytest.raises(TrainingError) as exc:
        stdlib_sft.to_row({"schema": stdlib_sft.SCHEMA, "id": "x", "fills": ["a.b"], "cases": "print(1)"})
    assert "helpers" in str(exc.value)


def test_a_corpus_of_another_schema_is_refused_rather_than_read_as_empty():
    with pytest.raises(TrainingError):
        stdlib_sft.sft_rows([dict(unit(), schema="something-else/9")])


def test_the_shipped_corpus_converts_and_its_overlap_is_the_measured_one():
    """Over the real corpus, so a change to it fails here rather than in a round."""
    import json
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "stdlib", "stdlib.jsonl")
    if not os.path.isfile(path):
        pytest.skip("no stdlib corpus in this tree")
    with open(path, encoding="utf-8") as fh:
        units = [json.loads(line) for line in fh if line.strip()]
    clean = stdlib_sft.sft_rows(units)
    assert len(clean["rows"]) == len(units)
    assert stdlib_sft.BUILTIN in clean["modules"], "the three builtin units must be labelled"
    # The families a bank-v3 round holds out reach more than half the corpus.
    bank_v3_like = ["textwrap-fill", "bisect-bisect-left", "datetime-date-arithmetic",
                    "statistics-median", "itertools-groupby", "collections-deque"]
    mixed = stdlib_sft.sft_rows(units, held_out=bank_v3_like)
    assert mixed["dropped"], "a bank-v3 benchmark overlaps this corpus; the gate must see it"
    assert set(mixed["overlap"]) == {"textwrap", "bisect", "datetime", "statistics",
                                     "itertools", "collections"}
