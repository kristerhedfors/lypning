"""``docs/COOKBOOK.md``, executed.

The invariant this file exists to hold: **a recipe on that page is true of the
binary in this tree, or the suite is red.** The cookbook tells an agent how to
turn a program a Rust variant refuses into one it runs, and a rewrite that is
merely PLAUSIBLE is worse than no cookbook at all — it reads well, it gets
copied, and it quietly answers a different question. So every recipe is parsed
out of the page and both halves of it are run, under the engine it names and
under CPython.

Three assertions per recipe, each catching a different way a recipe rots:

  1. the **before** still exits 90 with the contract line the page states.
     Catches a recipe documenting a gap that has since been CLOSED, which would
     leave the page telling people to work around something that now works.
     Reported as **OBSOLETE**, because the fix is to delete the recipe, not to
     reopen the gap.
  2. the **after** matches CPython on the engine — stdout and exit code.
     Catches a rewrite that swapped one unsupported construct for another.
  3. the **before** and the **after** print the same thing under CPython, and
     that thing is what the ``# after`` label says it ``prints:``. Catches what
     neither of the others can see: a rewrite that runs perfectly and computes
     something else. This is the assertion that makes the page a cookbook
     rather than a list of trivia, and upstream it caught draft recipes that
     read fine.

Two more, for the routing facts the page states: a ``served_by=`` recipe's
before must run unchanged, CPython-identical, on the larger variant it names;
and the number of recipe assertions skipped for an unbuilt engine is asserted,
not hidden — a partial spectrum fails, a wholly unbuilt one skips with the
count in its reason.

The recipes are parsed rather than copied. A copy drifts, and then the suite is
green about a page nobody is testing; the page is the artifact under test.

Assertion 3 needs no engine, so it runs everywhere — a page rots by drifting
from CPython at least as often as by drifting from a variant. Assertions 1 and
2 resolve their binary at call time, because the autouse fixture in
``conftest`` moves ``$LYPNING_HOME`` and where an engine resolves from is not
knowable at import.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from lypning import engines
from lypning import UNSUPPORTED_EXIT

DOC = Path(__file__).resolve().parents[1] / "docs" / "COOKBOOK.md"

#: A recipe targets the floor of the Rust spectrum unless its marker says
#: ``engine=``; the marker may name any member of the spectrum and nothing else,
#: so a typo there is an error and never a silent "skipped".
DEFAULT_ENGINE = engines.SPECTRUM[0]
_ENGINES = engines.SPECTRUM

#: The ``# after`` label carries the stdout the rewrite produces, after
#: ``prints:``; a second line is written ``\n``. Compared byte for byte.
_PRINTS_RE = re.compile(r"prints: ?(.*)$")

_MARKER_RE = re.compile(r"<!--\s*recipe\s+([^>]*?)\s*-->")
_ATTR_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.S)

#: ``<engine>: unsupported: <kind>: <detail>``, split into its parts. Spelled
#: again here rather than imported: the point of this suite is to read the line
#: the binary actually printed, so it may not share a regex with the code that
#: decides what a refusal is.
_CONTRACT_RE = re.compile(
    r"^(?P<engine>[\w.-]+): unsupported: (?P<kind>[\w-]+): (?P<detail>.+)$", re.M)


@dataclass
class Recipe:
    """One entry on the page: a refusal, the program that provokes it, the rewrite."""

    id: str
    kind: str
    detail: str
    engine: str = DEFAULT_ENGINE
    before: str = ""
    after: str = ""
    #: ``served_by=lypning-l`` — a larger variant that runs the *before*
    #: unchanged. A routing fact the page states and this suite checks, so that
    #: a recipe the floor refuses is not misread as obsolete when its sibling
    #: serves it.
    served_by: str = ""
    #: What the after prints, from its label line; ``None`` when the label is
    #: silent, which the suite treats as a defect on an executed recipe.
    prints: Optional[str] = None
    stdin: str = ""
    argv: List[str] = field(default_factory=list)
    #: ``equivalent=no`` opts out of assertion 3, and a recipe may only do that
    #: when the before form cannot be run to completion at all — the heap recipe
    #: exists precisely because its input does not fit.
    equivalent: bool = True
    #: ``min_python=3.10`` — the oldest CPython whose own syntax and builtins can
    #: express this recipe's BEFORE. Assertion 3 compares the two halves under
    #: CPython, and on an older interpreter the before does not run there either,
    #: so the comparison has nothing to say. Skipped rather than failed: the
    #: recipe is not wrong, the oracle simply predates the feature the recipe is
    #: about. ``zip(strict=)`` is 3.10, ``except*`` is 3.11.
    min_python: tuple = ()
    #: ``synthetic=yes`` marks a before that is illustrative rather than runnable
    #: (it names a placeholder). It skips execution entirely and is the one
    #: escape hatch; keep it rare and justified on the page.
    synthetic: bool = False


def parse_cookbook(md: str) -> List[Recipe]:
    """Pull the recipes out of the page.

    The convention the page uses: a marker comment carrying the machine-readable
    half — which contract line the before must produce, and what the program
    needs to run — followed by exactly two fenced ``python`` blocks, the first
    commented ``# before`` and the second ``# after``.

    Deliberately strict: a malformed marker raises rather than skipping. A suite
    that silently checks fewer recipes than the page contains is the exact
    failure this file exists to prevent, and it would look green.
    """
    recipes: List[Recipe] = []
    markers = list(_MARKER_RE.finditer(md))
    for i, m in enumerate(markers):
        attrs: Dict[str, str] = {}
        for a in _ATTR_RE.finditer(m.group(1)):
            raw = a.group(2)
            attrs[a.group(1)] = json.loads(raw) if raw.startswith('"') else raw
        for required in ("id", "kind", "detail"):
            if not attrs.get(required):
                raise ValueError("recipe marker without a %s: %s" % (required, m.group(1)))
        rid = attrs["id"]

        engine = attrs.get("engine", DEFAULT_ENGINE)
        if engine not in _ENGINES:
            raise ValueError("recipe %s names %r, which is not in the spectrum"
                             % (rid, engine))
        served_by = attrs.get("served_by", "")
        if served_by and (served_by not in _ENGINES
                          or _ENGINES.index(served_by) <= _ENGINES.index(engine)):
            raise ValueError("recipe %s says %r serves it, which is not a larger "
                             "variant than %r" % (rid, served_by, engine))

        # The blocks belonging to this recipe are the ones before the next marker.
        end = markers[i + 1].start() if i + 1 < len(markers) else len(md)
        blocks = _BLOCK_RE.findall(md[m.end():end])
        if len(blocks) != 2:
            raise ValueError("recipe %s needs exactly two python blocks, found %d"
                             % (rid, len(blocks)))
        for block, label in zip(blocks, ("# before", "# after")):
            if not block.startswith(label):
                raise ValueError("recipe %s: a block is not %r" % (rid, label))
        prints = _PRINTS_RE.search(blocks[1].split("\n", 1)[0])

        recipes.append(Recipe(
            id=rid,
            kind=attrs["kind"],
            detail=attrs["detail"],
            engine=engine,
            served_by=served_by,
            prints=prints.group(1).replace("\\n", "\n") if prints else None,
            before=_strip(blocks[0]),
            after=_strip(blocks[1]),
            stdin=attrs.get("stdin", ""),
            argv=json.loads(attrs["argv"]) if "argv" in attrs else [],
            equivalent=attrs.get("equivalent") != "no",
            min_python=tuple(
                int(n) for n in attrs.get("min_python", "").split(".") if n
            ),
            synthetic=attrs.get("synthetic") == "yes",
        ))
    return recipes


def _strip(block: str) -> str:
    """Drop the ``# before``/``# after`` label line and the fence's own newline."""
    return re.sub(r"^#[^\n]*\n", "", block).rstrip("\n")


RECIPES = parse_cookbook(DOC.read_text(encoding="utf-8")) if DOC.is_file() else []

#: Recipes that opt out of being executed, by id and reason. Pinned by name
#: rather than counted, because ``synthetic=yes`` and ``equivalent=no`` are the
#: only two attributes on the page that make a recipe stop being checked, and
#: they do it silently: the suite loses four tests and stays green. Same pattern
#: as ``UNCOVERED`` in ``tests/test_shims.py`` and ``_KNOWN_MISSES`` in
#: ``tests/test_syntax_scan.py`` — an exception costs a line here, which is the
#: only moment anybody will think about it.
OPT_OUTS: Dict[str, str] = {}

#: Parametrised by id, so a failure names the recipe rather than an index — the
#: report a docs failure needs is "which recipe", not "which loop iteration".
_ALL = pytest.mark.parametrize("recipe", RECIPES, ids=[r.id for r in RECIPES])
_RUNNABLE = [r for r in RECIPES if not r.synthetic]
_EXECUTED = pytest.mark.parametrize("recipe", _RUNNABLE, ids=[r.id for r in _RUNNABLE])
_COMPARED = [r for r in _RUNNABLE if r.equivalent]
_EQUIVALENT = pytest.mark.parametrize("recipe", _COMPARED, ids=[r.id for r in _COMPARED])
_SERVED_LIST = [r for r in _RUNNABLE if r.served_by]
_SERVED = pytest.mark.parametrize("recipe", _SERVED_LIST, ids=[r.id for r in _SERVED_LIST])

if not DOC.is_file():  # pragma: no cover - a wheel ships no docs/
    pytest.skip("docs/COOKBOOK.md is not in this tree", allow_module_level=True)


def _binary(engine: str) -> Path:
    """The named variant, or skip.

    Resolved per call, never at import: ``conftest`` moves ``$LYPNING_HOME``
    under ``tmp_path`` for every test, so the answer is only correct once a test
    is running. An absent variant is a skip here, and the count of what was
    skipped is asserted once, in the last test of this module.
    """
    b = engines.find(engine)
    if b is None:
        pytest.skip("%s is not built — the recipe assertions it owns were not "
                    "executed (`lypning build --rust`)" % engine)
    return b


def _cpython() -> Path:
    b = engines.find_cpython()
    if b is None:  # pragma: no cover - this suite runs under one
        pytest.skip("no reference CPython")
    return b


def _oracle_version() -> tuple:
    """The version of the CPython acting as the oracle — which is NOT necessarily
    the one running pytest. conftest strips ``$LYPNING_CPYTHON``, so the oracle
    is resolved off ``$PATH`` and a matrix job can run 3.9 while the shell's
    ``python3`` is 3.12, or the reverse."""
    out = subprocess.run(
        [str(_cpython()), "-c", "import sys;print('%d %d' % sys.version_info[:2])"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    try:
        return tuple(int(n) for n in out.stdout.split())
    except ValueError:  # pragma: no cover - an oracle that cannot say is not one
        return ()


def _require_oracle(recipe: Recipe) -> None:
    if recipe.min_python and _oracle_version() < recipe.min_python:
        pytest.skip(
            "%s needs CPython %s as the oracle; this one is %s"
            % (recipe.id,
               ".".join(str(n) for n in recipe.min_python),
               ".".join(str(n) for n in _oracle_version()))
        )


def _run(binary: Path, engine: str, program: str, recipe: Recipe) -> engines.Result:
    """One half of one recipe, in a temp cwd of its own.

    Its own cwd per call, not per recipe: the with-items recipe writes ``a.txt``
    and reads it back, so a shared directory would let one run answer from
    another run's files and a genuine divergence would compare equal. The same
    reason ``conformance`` gives a sandbox to every arm separately.
    """
    with tempfile.TemporaryDirectory(prefix="lypning-cookbook-") as cwd:
        return engines.run(
            engine, program, binary=binary, argv_tail=recipe.argv,
            stdin=recipe.stdin, cwd=cwd, timeout=20.0,
            env={
                # The same environment on both sides of every comparison. LC_ALL
                # is load-bearing: a recipe that prints non-ASCII would, under
                # `C`, decode to the same replacement characters on both engines
                # and agree for the wrong reason.
                "LC_ALL": "C.UTF-8",
                "PYTHONHASHSEED": "0",
                "PWD": cwd,
                # A cookbook run is not evidence of anything an agent typed.
                # `engines.run` sets LYPNING_CAPTURE=0; this is the other half.
                "LYPNING_LOG": str(Path(cwd) / "capture.jsonl"),
            },
        )


# --- the page itself ---------------------------------------------------------


def test_the_page_parses_and_is_not_empty():
    assert len(RECIPES) >= 20, (
        "only %d recipes parsed — did the marker format change?" % len(RECIPES))
    ids = [r.id for r in RECIPES]
    assert len(set(ids)) == len(ids), "duplicate recipe id"


def test_every_recipe_is_executed_unless_it_was_signed_off():
    """The escape hatches, pinned by name.

    ``synthetic=yes`` drops a recipe from all four executed assertions and
    ``equivalent=no`` drops it from the one no engine is needed for. Neither
    leaves a mark: the run is simply four tests shorter and still green, which
    is the exact failure this file exists to prevent, aimed at this file.
    """
    opted = {r.id for r in RECIPES if r.synthetic or not r.equivalent}
    assert opted == set(OPT_OUTS), (
        "recipes opting out of execution that nobody signed off: %s; signed off "
        "but no longer opting out: %s"
        % (sorted(opted - set(OPT_OUTS)), sorted(set(OPT_OUTS) - opted)))


def test_the_page_promises_this_suite_runs_it():
    # The page states the contract in prose. If that sentence moves, the file it
    # names is the one place that will not notice on its own.
    assert "tests/test_cookbook.py" in DOC.read_text(encoding="utf-8")


def test_a_marker_without_an_id_is_an_error_not_a_skip():
    with pytest.raises(ValueError, match="without a id"):
        parse_cookbook('<!-- recipe kind=module detail="x" -->\n'
                       "```python\n# before\npass\n```\n"
                       "```python\n# after\npass\n```\n")


def test_a_recipe_with_one_block_is_an_error():
    with pytest.raises(ValueError, match="exactly two python blocks"):
        parse_cookbook('<!-- recipe id=r kind=module detail="x" -->\n'
                       "```python\n# before\npass\n```\n")


def test_a_recipe_naming_an_engine_outside_the_spectrum_is_an_error():
    with pytest.raises(ValueError, match="not in the spectrum"):
        parse_cookbook('<!-- recipe id=r kind=module detail="x" engine=python -->\n'
                       "```python\n# before\npass\n```\n"
                       "```python\n# after\npass\n```\n")
    # The oracle is measured, never routed to — so no recipe can be about it.
    with pytest.raises(ValueError, match="not in the spectrum"):
        parse_cookbook('<!-- recipe id=r kind=module detail="x" engine=%s -->\n'
                       "```python\n# before\npass\n```\n"
                       "```python\n# after\npass\n```\n" % engines.MICROPYTHON)


def test_served_by_must_name_a_larger_variant_than_the_one_refusing():
    with pytest.raises(ValueError, match="not a larger variant"):
        parse_cookbook('<!-- recipe id=r kind=module detail="x" engine=%s served_by=%s -->\n'
                       "```python\n# before\npass\n```\n"
                       "```python\n# after\npass\n```\n"
                       % (engines.SPECTRUM[-1], engines.SPECTRUM[0]))


def test_the_marker_carries_the_run_inputs():
    (r,) = parse_cookbook('<!-- recipe id=r kind=module detail="x" stdin="a\\nb\\n" '
                          'argv=["7"] equivalent=no synthetic=yes -->\n'
                          "```python\n# before — a note\nbefore()\n```\n"
                          "```python\n# after — a note — prints: 1\\n2\nafter()\n```\n")
    assert (r.stdin, r.argv) == ("a\nb\n", ["7"])
    assert not r.equivalent and r.synthetic
    # The label line is stripped; the program is not; the label's `prints:` is kept.
    assert (r.before, r.after) == ("before()", "after()")
    assert r.prints == "1\n2"
    assert r.engine == DEFAULT_ENGINE and r.served_by == ""


def test_a_silent_after_label_parses_as_no_expectation():
    (r,) = parse_cookbook('<!-- recipe id=r kind=module detail="x" engine=%s served_by=%s -->\n'
                          "```python\n# before\nbefore()\n```\n"
                          "```python\n# after\nafter()\n```\n"
                          % (engines.SPECTRUM[0], engines.SPECTRUM[-1]))
    assert r.prints is None and r.served_by == engines.SPECTRUM[-1]


@_ALL
def test_every_recipe_states_a_contract_line_an_engine_could_print(recipe):
    """The kind and detail must be spellable in the line the engine writes.

    Not a fixed vocabulary of kinds — the engines own that list and it grows as
    the plan is worked through, so pinning it here would fail a correct recipe
    for a new kind. What is pinned is the shape: a kind with a space in it, or a
    detail carrying a newline, can never match the contract line, so assertion 1
    would fail for a reason that has nothing to do with the engine.
    """
    line = "%s: unsupported: %s: %s" % (recipe.engine, recipe.kind, recipe.detail)
    assert _CONTRACT_RE.match(line), (
        "%s: kind %r / detail %r cannot appear in a contract line"
        % (recipe.id, recipe.kind, recipe.detail))


# --- assertion 3: the rewrite is a rewrite -----------------------------------


@_EXECUTED
def test_the_rewrite_runs_under_cpython(recipe):
    res = _run(_cpython(), engines.CPYTHON, recipe.after, recipe)
    assert res.returncode == 0, (
        "%s: the rewrite fails under CPython:\n%s" % (recipe.id, res.stderr))


@_EQUIVALENT
def test_the_rewrite_computes_what_the_original_computed(recipe):
    """The assertion the other two cannot make, and it needs no engine."""
    _require_oracle(recipe)
    before = _run(_cpython(), engines.CPYTHON, recipe.before, recipe)
    after = _run(_cpython(), engines.CPYTHON, recipe.after, recipe)
    assert before.returncode == 0, (
        "%s: the ORIGINAL fails under CPython, so it is not a fair before:\n%s"
        % (recipe.id, before.stderr))
    assert after.stdout == before.stdout, (
        "%s: the rewrite answers a different question than the original"
        % recipe.id)


@_EXECUTED
def test_the_page_shows_what_the_rewrite_prints(recipe):
    """Every executed recipe's ``# after`` label ends in ``prints: <stdout>``,
    so a reader can eyeball a recipe without running it — and the suite holds
    the label to what CPython prints."""
    assert recipe.prints is not None, (
        "%s: the `# after` label does not say what the rewrite prints" % recipe.id)
    res = _run(_cpython(), engines.CPYTHON, recipe.after, recipe)
    want = recipe.prints + "\n" if recipe.prints else ""
    assert res.stdout == want, (
        "%s: the page says the rewrite prints %r, CPython printed %r"
        % (recipe.id, recipe.prints, res.stdout))


# --- assertions 1 and 2: the engine --------------------------------------------


@_EXECUTED
def test_the_before_is_still_refused_with_the_contract_line_the_page_states(recipe):
    res = _run(_binary(recipe.engine), recipe.engine, recipe.before, recipe)
    assert res.returncode == UNSUPPORTED_EXIT, (
        "%s: OBSOLETE RECIPE — the before form exits %d, not %d. If this gap was "
        "closed, delete the recipe rather than reopening it.\n%s"
        % (recipe.id, res.returncode, UNSUPPORTED_EXIT, res.stderr))
    line = _CONTRACT_RE.search(res.stderr or "")
    assert line is not None, (
        "%s: exit %d without a contract line:\n%s"
        % (recipe.id, UNSUPPORTED_EXIT, res.stderr))
    assert line.group("engine") == recipe.engine, (
        "%s: the refusal is signed %r, not %r"
        % (recipe.id, line.group("engine"), recipe.engine))
    assert line.group("kind") == recipe.kind, "%s: contract kind drifted" % recipe.id
    assert line.group("detail").startswith(recipe.detail), (
        '%s: contract detail drifted — page says "%s", binary says "%s"'
        % (recipe.id, recipe.detail, line.group("detail")))
    assert res.stdout == "", (
        "%s: a refusal must leave stdout untouched" % recipe.id)


@_EXECUTED
def test_the_rewrite_matches_cpython_on_the_engine(recipe):
    got = _run(_binary(recipe.engine), recipe.engine, recipe.after, recipe)
    want = _run(_cpython(), engines.CPYTHON, recipe.after, recipe)
    assert got.stdout == want.stdout, (
        "%s: the rewrite diverges from CPython on %s" % (recipe.id, recipe.engine))
    assert got.returncode == want.returncode, (
        "%s: the rewrite's exit code diverges on %s" % (recipe.id, recipe.engine))


@_SERVED
def test_the_before_runs_unchanged_on_the_variant_the_page_says_serves_it(recipe):
    """The routing fact behind ``served_by=``: the floor refuses the before,
    the larger variant runs it, CPython-identical. A recipe the floor refuses
    is therefore never OBSOLETE on the strength of its sibling running it."""
    got = _run(_binary(recipe.served_by), recipe.served_by, recipe.before, recipe)
    want = _run(_cpython(), engines.CPYTHON, recipe.before, recipe)
    assert got.returncode == 0, (
        "%s: the page says %s serves the before; it exited %d:\n%s"
        % (recipe.id, recipe.served_by, got.returncode, got.stderr))
    assert got.stdout == want.stdout, (
        "%s: the before diverges from CPython on %s" % (recipe.id, recipe.served_by))


# --- the degradation path -----------------------------------------------------


def test_an_unbuilt_variant_skips_the_recipes_it_owns_rather_than_failing(monkeypatch):
    """Exercised by pointing the finder at nothing, never by reasoning about it."""
    monkeypatch.setattr(engines, "find_variant", lambda engine: None)
    recipe = Recipe(id="r", kind="module", detail="x", engine=engines.SPECTRUM[-1])
    with pytest.raises(pytest.skip.Exception, match="is not built"):
        _binary(recipe.engine)


def _skips_for_unbuilt_engines() -> "tuple[list, list, int]":
    """The engines the page names, the ones not built here, and how many
    recipe assertions that removes from the run. Computed, not observed, so it
    does not depend on this test running last."""
    named = sorted({r.engine for r in _RUNNABLE} | {r.served_by for r in _SERVED_LIST})
    unbuilt = [e for e in named if engines.find(e) is None]
    skipped = (sum(2 for r in _RUNNABLE if r.engine in unbuilt)
               + sum(1 for r in _SERVED_LIST if r.served_by in unbuilt))
    return named, unbuilt, skipped


def test_the_skip_count_is_asserted_so_an_unbuilt_engine_is_not_a_pass():
    """A skipped recipe assertion verified nothing, and a green run with the
    spectrum unbuilt is green about nothing. `lypning build --rust` builds the
    spectrum as a unit, so a partial spectrum is a stale build and FAILS with
    the count; a wholly unbuilt one is the degradation path (a checkout with no
    cargo) and skips, with the count in its reason for the reader to see."""
    named, unbuilt, skipped = _skips_for_unbuilt_engines()
    if unbuilt and len(unbuilt) == len(named):
        pytest.skip("%d recipe assertions skipped: none of %s is built "
                    "(`lypning build --rust`)" % (skipped, ", ".join(named)))
    assert not unbuilt, (
        "%d recipe assertions skipped: %s not built while %s is — a partial "
        "spectrum is a stale build, not a degradation path (`lypning build --rust`)"
        % (skipped, ", ".join(unbuilt), ", ".join(e for e in named if e not in unbuilt)))
    assert skipped == 0
