"""Separating the two negatives a selected step 0 leaves behind.

Seed 1111 ran 300 SFT steps on bank v3 and `best.json` selected step 0: no
checkpoint beat its own baseline. "The loss fell and did not transfer" and
"nothing was learned" are both consistent with that, and they have different
fixes -- the first points at the SFT rows and the gate, the second at the
learning rate and the dose. `loss.jsonl` is the only per-step record that
tells them apart, and nothing read it.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location(
        "loss_summary", ROOT / ".github" / "scripts" / "loss_summary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows(losses):
    return [{"step": i, "loss": v, "learning_rate": 2e-5,
             "supervised_tokens": 100, "supervised_tokens_total": 100 * i}
            for i, v in enumerate(losses, 1)]


def test_a_falling_loss_is_distinguished_from_a_flat_one():
    m = load()
    falling = m.summarise(rows([4.0, 3.5, 3.0, 2.0, 1.5, 1.0]))
    assert falling["drop_across_halves"] > 0
    assert falling["loss_min"] == 1.0 and falling["loss_min_step"] == 6

    flat = m.summarise(rows([4.0, 4.01, 3.99, 4.0, 4.02, 3.98]))
    assert abs(flat["drop_across_halves"]) < 0.05
    # Noise must not read as learning: the halves differ, just not meaningfully.
    assert flat["loss_min"] < flat["loss_first"]


def test_the_minimum_is_reported_with_the_step_that_reached_it():
    """A loss that fell and rebounded is a third story, and the step says so."""
    m = load()
    stats = m.summarise(rows([4.0, 2.0, 1.0, 2.5, 3.0, 3.5]))
    assert stats["loss_min"] == 1.0
    assert stats["loss_min_step"] == 3, "the minimum's step separates a rebound from a plateau"
    assert stats["loss_last"] > stats["loss_min"]


def test_rows_without_a_usable_loss_are_not_an_answer():
    m = load()
    assert m.summarise([]) is None
    assert m.summarise([{"step": 1, "loss": None}]) is None


def test_the_summary_prints_no_case_and_could_not():
    """The follower streams stdout into a PUBLIC Actions log.

    Printing a bundle into one published held-out eval-2 cases on 2026-09-18,
    which deleting the run did not undo. Every field here is an aggregate over
    steps, so no case id, program or expected stdout can reach a log through
    it -- asserted on the key set rather than trusted.
    """
    m = load()
    stats = m.summarise(rows([4.0, 3.0]))
    for key in stats:
        assert key in ("steps", "loss_first", "loss_last", "loss_min", "loss_min_step",
                       "mean_first_half", "mean_second_half", "drop_across_halves",
                       "supervised_tokens_total", "learning_rate_first",
                       "learning_rate_last"), key
    for value in stats.values():
        assert isinstance(value, (int, float, type(None))), value
