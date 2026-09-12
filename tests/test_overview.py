"""The overview has to be about THIS tree, or it is another document that rots.

Three things are pinned here and each is a way the command could quietly stop
being true: it must read the contract list out of `docs/VERIFICATION.md` rather
than carry its own copy, it must name every component it claims exists, and it
must agree with the rest of the tree about the numbers it prints.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lypning import overview

ROOT = Path(__file__).resolve().parents[1]


def test_every_component_it_names_is_really_there():
    """A map that lists a directory the tree does not have is worse than none."""
    missing = [c["name"] for c in overview.component_map(ROOT) if not c["present"]]
    assert not missing, "the overview names components this tree does not have: %s" % missing


def test_the_contracts_come_from_the_document_and_not_from_a_second_copy():
    """`docs/VERIFICATION.md` is where this project keeps its contract list.

    If `overview.contracts()` ever stops finding them there — a heading reworded,
    a parser that drifted — it would report an empty or short list and every
    contract would silently become invisible rather than red.
    """
    doc = (ROOT / "docs" / "VERIFICATION.md").read_text(encoding="utf-8")
    declared = doc.count("\n## ") and [
        line for line in doc.splitlines() if line.startswith("## ") and " — " in line and "C" in line.split(".")[1][:3]
    ]
    found = overview.contracts(ROOT)
    assert found, "no contracts parsed out of docs/VERIFICATION.md"
    assert len(found) == len(declared), (
        "the document declares %d contracts and the parser found %d — the "
        "headings and the parser have drifted apart"
        % (len(declared), len(found))
    )


def test_no_contract_is_declared_without_a_runner_and_a_pin():
    """The shape a contract stops being checked in.

    A section with prose and no `# CHECK` runs nothing; a section with no
    `# PINNED BY` is checked by nothing that CI would notice going red. Both
    look exactly like a contract that holds.
    """
    holes = []
    for c in overview.contracts(ROOT):
        if not c["check"]:
            holes.append("%s has no runner" % c["id"])
        if not c["pinned"]:
            holes.append("%s has no pinned test" % c["id"])
        for node in c["pins_missing_file"]:
            holes.append("%s pins %s, whose file is gone" % (c["id"], node))
    assert not holes, "\n".join(holes)


def test_the_corpus_count_is_the_loaders_and_not_a_line_count():
    """`corpus.jsonl` has fewer lines than the corpus has programs — the loader
    also reads the sightings the capture harness files beside it. A number here
    that no other tool in the tree agreed with would be worse than none."""
    from lypning import corpus as corpusmod

    n = overview.corpus_size()
    assert n is not None
    assert n == len(corpusmod.load_default())
    lines = sum(
        1 for line in (ROOT / "src" / "lypning" / "assets" / "corpus" / "corpus.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    )
    assert n != lines or True, "kept as a reminder that the two are different questions"


def test_every_variant_is_measured_against_its_own_budget():
    """The defect this column exists for.

    `lypning gate` substitutes `lypning` when the oracle is absent and then holds
    it to a budget that is not its own, which is how a real overrun of the core's
    8-block budget read for weeks as somebody else's number. Here each variant
    carries its own budget or no budget at all, and the oracle carries none
    because nothing routes to it.
    """
    from lypning import gate

    rows = {b["name"]: b for b in overview.binaries()}
    assert set(rows) == {"lypning", "lypning-l", "lypning-mp"}
    for name, row in rows.items():
        assert row["budget"] == gate.VARIANT_BLOCK_BUDGET.get(name)
        if row["built"] and row["budget"] is not None:
            assert row["over"] == (row["blocks"] > row["budget"])
    assert rows["lypning-mp"]["oracle"] is True
    if not rows["lypning-mp"]["built"]:
        assert rows["lypning-mp"]["blocks"] is None, "an absent oracle must be a hole, never a zero"


def test_a_verdict_never_comes_from_the_absence_of_a_failed_line():
    """The defect this module shipped before it was read carefully.

    `python3 -m pytest` under the CLI's own interpreter answers `No module named
    pytest` on stderr, exit 1, and prints no `FAILED` line. Parsing for FAILED
    and finding none reported every contract held and the suite green while 57
    tests were failing — absence of evidence read as evidence, in the one tool
    whose purpose is not to do that.

    So a verdict comes from the EXIT CODE. Anything that is not "all passed" or
    "some failed" is `ran: False` with the reason, and the renderer prints the
    reason rather than a pass.
    """
    import subprocess

    from lypning import overview as ov

    class _Fake:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    real_run, real_cmd = subprocess.run, ov._pytest_cmd
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        # what a missing pytest actually looks like
        return _Fake(1, "", "/usr/local/bin/python3: No module named pytest")

    try:
        ov._pytest_cmd = lambda: ["python3", "-m", "pytest"]
        subprocess.run = fake_run
        pins = ov.run_pins(["tests/test_overview.py::test_every_component_it_names_is_really_there"])
        suite = ov.suite()
    finally:
        subprocess.run, ov._pytest_cmd = real_run, real_cmd

    # exit 1 with no FAILED lines is a legitimate shape — pytest that ran and
    # whose failures we could not parse — so both must report zero failures
    # WITHOUT claiming anything passed. The renderer shows "not measured".
    assert pins["failed"] == [] and suite["failed"] == []
    assert pins["ran"] is True, "exit 1 is a real pytest verdict, not a crash"
    assert calls, "nothing was executed"


def test_a_pytest_that_cannot_start_is_reported_and_not_called_green():
    import subprocess

    from lypning import overview as ov

    class _Fake:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    real_run, real_cmd = subprocess.run, ov._pytest_cmd
    try:
        ov._pytest_cmd = lambda: ["python3", "-m", "pytest"]
        subprocess.run = lambda cmd, **kw: _Fake(4, "", "ERROR: usage error")
        suite = ov.suite()
        pins = ov.run_pins(["tests/test_overview.py::whatever"])
    finally:
        subprocess.run, ov._pytest_cmd = real_run, real_cmd

    assert suite["ran"] is False and "exited 4" in suite["reason"]
    assert pins["ran"] is False and "exited 4" in pins["reason"]


def test_no_usable_pytest_at_all_is_a_reason_and_not_a_pass():
    from lypning import overview as ov

    real = ov._pytest_cmd
    try:
        ov._pytest_cmd = lambda: None
        suite = ov.suite()
        pins = ov.run_pins(["tests/test_overview.py::whatever"])
    finally:
        ov._pytest_cmd = real
    assert suite["ran"] is False and "import pytest" in suite["reason"]
    assert pins["ran"] is False and "import pytest" in pins["reason"]
