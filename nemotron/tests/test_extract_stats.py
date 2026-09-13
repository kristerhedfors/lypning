from __future__ import annotations

import pytest

from pipeline.extract import compiles, extract_program, strip_reasoning
from pipeline import stats
from pipeline.stats import beats, bootstrap_ci, summarize, wilson_ci


def test_thinking_is_stripped_and_the_answer_block_wins():
    reply = "<think>first I'll try\n```python\nWRONG\n```\n</think>\n```python\nprint(1)\n```"
    assert extract_program(reply) == ("print(1)", "fenced-python")


def test_a_truncated_thought_yields_no_program_rather_than_the_draft():
    assert extract_program("<think>reasoning that got cut ```python\nDRAFT\n") == (None, "none")


def test_reasoning_content_from_the_server_is_not_re_stripped():
    prog, how = extract_program("```python\nprint(2)\n```", reasoning="separate thoughts")
    assert prog == "print(2)" and how == "fenced-python"


def test_the_last_python_block_is_the_answer():
    assert extract_program("```python\na\n```\ntext\n```python\nb\n```")[0] == "b"


def test_bare_code_and_bare_prose_are_told_apart():
    assert extract_program("import os\nprint(os.sep)")[1] == "bare-text"
    assert extract_program("I would use the os module for this.") == (None, "none")


def test_a_block_in_another_language_is_not_a_program():
    assert extract_program("```sql\nSELECT 1\n```") == (None, "none")


def test_compiles_reports_syntax_errors_without_running_anything():
    assert compiles("print(1)") is None
    assert "invalid syntax" in (compiles("def f(:") or "")
    assert compiles("import os; os.system('touch /tmp/NOPE')") is None


def test_bootstrap_is_reproducible_and_seeded():
    scores = [1, 0, 1, 1, 0, 1, 1, 1, 0, 1]
    assert bootstrap_ci(scores) == bootstrap_ci(scores)
    a = bootstrap_ci(scores, seed=1)
    b = bootstrap_ci(scores, seed=2)
    assert a["point"] == b["point"]


def test_bootstrap_brackets_the_point_estimate():
    ci = bootstrap_ci([1, 0] * 25)
    assert ci["lo"] <= ci["point"] <= ci["hi"] and ci["n"] == 50


def test_wilson_stays_wide_where_the_bootstrap_collapses():
    # The pathology this cross-check exists for: every case passing makes the
    # percentile bootstrap report a zero-width interval, which is not true.
    boot = bootstrap_ci([1] * 8)
    wil = wilson_ci(8, 8)
    assert boot["lo"] == boot["hi"] == 1.0
    assert wil["lo"] < 0.8
    assert summarize([1] * 8)["ci_disagreement"] > 0.3


def test_the_win_rule_needs_the_lower_bound_above_the_baseline_point():
    s = summarize([1] * 18 + [0] * 2)          # 90%, CI comfortably above 0.5
    assert beats(s, 0.50) and not beats(s, 0.95)


def test_empty_scores_do_not_raise():
    assert summarize([])["n_cases"] == 0


def test_paired_delta_separates_what_the_unpaired_rule_cannot():
    """The real situation: 52 cases, baseline 11.5%, four more solved and none lost.

    That is the effect size the held-out rewrite slice can actually produce, and
    the two instruments disagree on it. The paired interval excludes zero; the
    unpaired rule does not clear the baseline point, because it pays twice for
    the shared difficulty of 48 cases that did not move at all.
    """
    from pipeline.stats import beats, paired_delta, summarize
    before = {"c%d" % i: (1.0 if i < 6 else 0.0) for i in range(52)}
    after = dict(before)
    for i in range(6, 10):
        after["c%d" % i] = 1.0
    d = paired_delta(before, after)
    assert d["gained"] == 4 and d["lost"] == 0
    assert d["significant"], "paired bootstrap should exclude zero"
    assert not beats(summarize(list(after.values())),
                     sum(before.values()) / len(before)), \
        "the pre-registered unpaired rule should NOT fire at this effect size"


def test_mcnemar_is_exact_and_symmetric():
    from pipeline.stats import _mcnemar
    assert _mcnemar(0, 0) == 1.0
    assert _mcnemar(8, 1) == _mcnemar(1, 8)
    assert _mcnemar(8, 1) < 0.05 < _mcnemar(3, 1)


def test_pass_at_k_is_the_gap_between_can_and_does():
    from pipeline.stats import pass_at_k
    r = pass_at_k([(0, 16), (1, 16), (16, 16), (0, 16)])
    assert r["pass_at_k"] == 0.5
    assert abs(r["pass_at_1"] - 17 / 64) < 1e-9
    assert r["headroom"] > 0


def test_a_case_flip_is_solved_not_solved_and_not_a_move_in_its_mean():
    """The §3c amendment of 2026-09-13, pinned so it cannot revert silently.

    Until that date `paired_delta` called a case discordant when its per-case
    MEAN moved. At k=16 that counts one Bernoulli draw of noise the same as an
    outright acquisition, which left the pre-registered rule at 12% power
    against six previously-hopeless cases solved — the shape a rejection-sampling
    LoRA actually produces — and fired it on the naive all-74 denominator
    against pure engine drift.

    The rule now asks what §3 always said it asked: did this case cross from
    never-solved to solved. The mean-moved counts stay reported beside it so the
    alternative remains visible, and this pins BOTH — reverting the definition,
    or quietly dropping the second opinion, fails here.
    """
    # Six cases acquired outright; nothing lost a solution. Nine already-solved
    # cases jitter by one draw in each direction, which is what k=16 sampling
    # noise looks like and is the whole reason the old definition was blind.
    before = {"win%d" % i: 0.0 for i in range(6)}
    after = {"win%d" % i: 5 / 16 for i in range(6)}
    for i in range(5):
        before["up%d" % i], after["up%d" % i] = 0.5, 0.5 + 1 / 16
    for i in range(9):
        before["dn%d" % i], after["dn%d" % i] = 0.5, 0.5 - 1 / 16
    d = stats.paired_delta(before, after, resamples=400)

    # Amended: six acquisitions, nothing un-solved. This is the 2/2**6 floor
    # §3 has always described, and it fires.
    assert d["gained"] == 6 and d["lost"] == 0
    assert d["mcnemar_p"] == pytest.approx(2 / 2 ** 6)
    assert d["mcnemar_p"] < 0.05

    # Superseded: the same data, counted by mean-moved, is 11 up against 9 down
    # and cannot fire at any effect size. That gap is the amendment.
    assert d["moved_up"] == 11 and d["moved_down"] == 9
    assert d["mcnemar_p_mean_moved"] > 0.05


def test_the_conjunction_can_never_exceed_either_leg_it_is_made_of():
    """`power_curve` once simulated ONE leg of a two-leg rule, and a
    pre-registration was written on that number. This is the cheap invariant
    that would have caught it: a conjunction fires no more often than its
    rarer half, whatever the definitions underneath."""
    scores = [0.0] * 30 + [0.5] * 24 + [1.0] * 16
    rows = stats.power_curve(scores, 16, trials=40, resamples=200)["rows"]
    assert rows
    for r in rows:
        assert r["paired_power"] <= r["bootstrap_power"] + 1e-9, r
        assert r["paired_power"] <= r["mcnemar_power"] + 1e-9, r
