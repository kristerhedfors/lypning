"""``docs/COOKBOOK.md``, executed.

The invariant this file exists to hold: **a recipe on that page is true of the
binary in this tree, or the suite is red.** The cookbook tells an agent how to
turn a program a tier refuses into one it runs, and a rewrite that is merely
PLAUSIBLE is worse than no cookbook at all — it reads well, it gets copied, and
it quietly answers a different question. So every recipe is parsed out of the
page and both halves of it are run, under the tier it names and under CPython.

Three assertions per recipe, each catching a different way a recipe rots:

  1. the **before** still exits 90 with the contract line the page states.
     Catches a recipe documenting a gap that has since been CLOSED, which would
     leave the page telling people to work around something that now works.
     Reported as **OBSOLETE**, because the fix is to delete the recipe, not to
     reopen the gap.
  2. the **after** matches CPython on the tier — stdout and exit code. Catches a
     rewrite that swapped one unsupported construct for another.
  3. the **before** and the **after** print the same thing under CPython.
     Catches what neither of the others can see: a rewrite that runs perfectly
     and computes something else. This is the assertion that makes the page a
     cookbook rather than a list of trivia, and upstream it caught draft recipes
     that read fine.

The recipes are parsed rather than copied. A copy drifts, and then the suite is
green about a page nobody is testing; the page is the artifact under test.

Assertion 3 needs no engine, so it runs everywhere — a page rots by drifting
from CPython at least as often as by drifting from a tier, and the tier a recipe
names may not be built here. Assertions 1 and 2 resolve their binary at call
time, because the autouse fixture in ``conftest`` moves ``$LYPNING_HOME`` and
where an engine resolves from is not knowable at import.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import pytest

from lypning import engines
from lypning import UNSUPPORTED_EXIT

DOC = Path(__file__).resolve().parents[1] / "docs" / "COOKBOOK.md"

#: Every recipe on the page today targets the MicroPython tier — that is what
#: the page is a cookbook for. The marker may name another tier; nothing else is
#: a tier, and a typo there must not silently become "skipped".
DEFAULT_ENGINE = engines.MICROPYTHON
_ENGINES = (engines.LYPNING, engines.MICROPYTHON)

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
    stdin: str = ""
    argv: List[str] = field(default_factory=list)
    #: ``equivalent=no`` opts out of assertion 3, and a recipe may only do that
    #: when the before form cannot be run to completion at all — the heap recipe
    #: exists precisely because its input does not fit.
    equivalent: bool = True
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
            raise ValueError("recipe %s names %r, which is not a tier" % (rid, engine))

        # The blocks belonging to this recipe are the ones before the next marker.
        end = markers[i + 1].start() if i + 1 < len(markers) else len(md)
        blocks = _BLOCK_RE.findall(md[m.end():end])
        if len(blocks) != 2:
            raise ValueError("recipe %s needs exactly two python blocks, found %d"
                             % (rid, len(blocks)))
        for block, label in zip(blocks, ("# before", "# after")):
            if not block.startswith(label):
                raise ValueError("recipe %s: a block is not %r" % (rid, label))

        recipes.append(Recipe(
            id=rid,
            kind=attrs["kind"],
            detail=attrs["detail"],
            engine=engine,
            before=_strip(blocks[0]),
            after=_strip(blocks[1]),
            stdin=attrs.get("stdin", ""),
            argv=json.loads(attrs["argv"]) if "argv" in attrs else [],
            equivalent=attrs.get("equivalent") != "no",
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
OPT_OUTS: Dict[str, str] = {
    "json-load-large": "the before form cannot be run to completion at all — the "
                       "recipe exists precisely because its input does not fit",
}

#: Parametrised by id, so a failure names the recipe rather than an index — the
#: report a docs failure needs is "which recipe", not "which loop iteration".
_ALL = pytest.mark.parametrize("recipe", RECIPES, ids=[r.id for r in RECIPES])
_RUNNABLE = [r for r in RECIPES if not r.synthetic]
_EXECUTED = pytest.mark.parametrize("recipe", _RUNNABLE, ids=[r.id for r in _RUNNABLE])
_COMPARED = [r for r in _RUNNABLE if r.equivalent]
_EQUIVALENT = pytest.mark.parametrize("recipe", _COMPARED, ids=[r.id for r in _COMPARED])

if not DOC.is_file():  # pragma: no cover - a wheel ships no docs/
    pytest.skip("docs/COOKBOOK.md is not in this tree", allow_module_level=True)


def _tier(recipe: Recipe) -> Path:
    """The binary the recipe targets, or skip.

    Resolved per call, never at import: ``conftest`` moves ``$LYPNING_HOME``
    under ``tmp_path`` for every test, so the answer is only correct once a test
    is running. An absent tier is a skip and never a failure — that is the same
    degradation every other path in this package makes, and it is why this
    module can be checked out on a machine with no 32-bit toolchain.
    """
    b = engines.find(recipe.engine)
    if b is None:
        pytest.skip("%s is not built — the recipes it owns were not executed" % recipe.engine)
    return b


def _cpython() -> Path:
    b = engines.find_cpython()
    if b is None:  # pragma: no cover - this suite runs under one
        pytest.skip("no reference CPython")
    return b


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
                # is load-bearing: one recipe is about `casefold` and prints
                # Swedish, and under `C` both engines would decode to the same
                # replacement characters and agree for the wrong reason.
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


def test_a_recipe_naming_something_that_is_not_a_tier_is_an_error():
    with pytest.raises(ValueError, match="not a tier"):
        parse_cookbook('<!-- recipe id=r kind=module detail="x" engine=python -->\n'
                       "```python\n# before\npass\n```\n"
                       "```python\n# after\npass\n```\n")


def test_the_marker_carries_the_run_inputs():
    (r,) = parse_cookbook('<!-- recipe id=r kind=module detail="x" stdin="a\\nb\\n" '
                          'argv=["7"] equivalent=no synthetic=yes -->\n'
                          "```python\n# before — a note\nbefore()\n```\n"
                          "```python\n# after\nafter()\n```\n")
    assert (r.stdin, r.argv) == ("a\nb\n", ["7"])
    assert not r.equivalent and r.synthetic
    # The label line is stripped; the program is not.
    assert (r.before, r.after) == ("before()", "after()")
    assert r.engine == DEFAULT_ENGINE


@_ALL
def test_every_recipe_states_a_contract_line_an_engine_could_print(recipe):
    """The kind and detail must be spellable in the line the tier writes.

    Not a fixed vocabulary of kinds — the engines own that list and it grows as
    the plan is worked through, so pinning it here would fail a correct recipe
    for a new kind. What is pinned is the shape: a kind with a space in it, or a
    detail carrying a newline, can never match the contract line, so assertion 1
    would fail for a reason that has nothing to do with the tier.
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
    before = _run(_cpython(), engines.CPYTHON, recipe.before, recipe)
    after = _run(_cpython(), engines.CPYTHON, recipe.after, recipe)
    assert before.returncode == 0, (
        "%s: the ORIGINAL fails under CPython, so it is not a fair before:\n%s"
        % (recipe.id, before.stderr))
    assert after.stdout == before.stdout, (
        "%s: the rewrite answers a different question than the original"
        % recipe.id)


# --- assertions 1 and 2: the tier ---------------------------------------------


@_EXECUTED
def test_the_before_is_still_refused_with_the_contract_line_the_page_states(recipe):
    res = _run(_tier(recipe), recipe.engine, recipe.before, recipe)
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
def test_the_rewrite_matches_cpython_on_the_tier(recipe):
    got = _run(_tier(recipe), recipe.engine, recipe.after, recipe)
    want = _run(_cpython(), engines.CPYTHON, recipe.after, recipe)
    assert got.stdout == want.stdout, (
        "%s: the rewrite diverges from CPython on %s" % (recipe.id, recipe.engine))
    assert got.returncode == want.returncode, (
        "%s: the rewrite's exit code diverges on %s" % (recipe.id, recipe.engine))


# --- the degradation path -----------------------------------------------------


def test_an_unbuilt_tier_skips_the_recipes_it_owns_rather_than_failing(no_micropython):
    """Exercised by pointing the finder at nothing, never by reasoning about it."""
    recipe = Recipe(id="r", kind="module", detail="x", engine=engines.MICROPYTHON)
    with pytest.raises(pytest.skip.Exception, match="is not built"):
        _tier(recipe)
