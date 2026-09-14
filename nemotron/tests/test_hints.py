"""The hint must reach the draw and nothing else, and the prompt must not move.

Context distillation is an ASYMMETRY, and an asymmetry has exactly one way to
fail silently: the hint leaks into the training row or the eval, and then the
model is measured on a prompt that tells it the answer. Nothing about the
resulting number would look wrong. These are the tripwires.
"""

from __future__ import annotations

import pytest

from pipeline import evaluate, hints


CASE = {
    "id": "c1",
    "prompt": "Print the square root of 16.",
    "test": {"kind": "lypning", "expect_stdout": "4.0\n", "require_tier1": True},
    "negatives": [{"detail": "module: import math", "program": "import math\nprint(math.sqrt(16))"}],
}


def test_the_hint_never_moves_the_prompt_signature():
    """`nt grade` refuses to compare two arms whose signature differs.

    The hint goes on the USER TURN of a rendered message list, never into
    SYSTEM_PROMPT or USER_TEMPLATE — the two strings the signature hashes. If a
    future edit puts it in the template, every arm drawn before it becomes
    incomparable with every arm after, and this is the cheapest place to notice.
    """
    before = evaluate.prompt_signature()
    hints.render_hinted_messages(CASE, "HINTED TEXT", evaluate.render_messages)
    assert evaluate.prompt_signature() == before
    assert "HINTED TEXT" not in evaluate.SYSTEM_PROMPT
    assert "HINTED TEXT" not in evaluate.USER_TEMPLATE


def test_render_messages_is_not_mutated_by_rendering_a_hint():
    """The SFT row calls the same function moments later; a shared dict would leak."""
    bare = evaluate.render_messages(CASE)
    hinted = hints.render_hinted_messages(CASE, "HINTED TEXT", evaluate.render_messages)
    again = evaluate.render_messages(CASE)
    assert bare == again
    assert "HINTED TEXT" in hinted[-1]["content"]
    assert "HINTED TEXT" not in again[-1]["content"]
    assert hinted[-1]["content"].startswith(bare[-1]["content"])


def test_the_bare_prompt_says_nothing_about_the_subset():
    """The premise of the whole change, pinned so it cannot drift unnoticed.

    If the eval prompt ever starts naming lypning, the hinted/unhinted asymmetry
    stops being an asymmetry and `hints`'s docstring stops being true.
    """
    text = " ".join(m["content"] for m in evaluate.render_messages(CASE)).lower()
    for word in ("lypning", "subset", "unsupported", "refus"):
        assert word not in text


def test_a_refusal_the_engine_no_longer_gives_yields_no_hint(monkeypatch):
    """A remembered refusal goes stale in one direction: the engine keeps learning."""
    monkeypatch.setattr(hints.refusals, "probe", lambda program, engine, **kw: None)
    hint, why = hints.hint_for(CASE, "e", "lypning", [])
    assert hint is None
    assert "now runs" in why


def test_a_case_with_no_negative_yields_no_hint():
    hint, why = hints.hint_for({"id": "c", "negatives": []}, "e", "lypning", [])
    assert hint is None
    assert why == "no recorded negative"


def test_the_hint_quotes_what_the_engine_says_now_not_what_was_recorded(monkeypatch):
    monkeypatch.setattr(hints.refusals, "probe",
                        lambda program, engine, **kw: {"kind": "re", "detail": "import re"})
    hint, why = hints.hint_for(CASE, "e", "lypning-l", [])
    # The row records `module: import math`; the engine says `re: import re`.
    assert "lypning-l: unsupported: re: import re" in hint
    assert "import math" not in hint
    assert why.startswith("re")


def test_an_exact_detail_beats_a_same_kind_recipe():
    """A worked, confident, irrelevant rewrite is worse than none."""
    recipes = [
        {"id": "wrong", "kind": "module", "detail": "import subprocess", "before": "b", "after": "a"},
        {"id": "right", "kind": "module", "detail": "import re", "before": "b", "after": "a"},
    ]
    assert hints.recipe_for("module", "import re", recipes)["id"] == "right"
    assert hints.recipe_for("module", "import itertools", recipes)["id"] == "wrong"
    assert hints.recipe_for("builtin", "anything", recipes) is None


def test_the_cookbook_in_this_tree_parses_into_usable_recipes():
    recipes = hints.load_cookbook()
    assert len(recipes) >= 10
    for r in recipes:
        assert r["id"] and r["kind"] and r["before"] and r["after"]
        assert r["before"] != r["after"]


def test_negatives_survive_both_spellings():
    """Rows arrive as real lists and as repr strings depending on the writer."""
    assert hints.negatives_of({"negatives": [{"program": "x"}]})[0]["program"] == "x"
    assert hints.negatives_of({"negatives": "[{'program': 'x'}]"})[0]["program"] == "x"
    assert hints.negatives_of({"negatives": "not a literal"}) == []
    assert hints.negatives_of({}) == []
