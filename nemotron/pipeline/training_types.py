"""Shared training failures and scores, independent of GPU libraries."""
from __future__ import annotations

from dataclasses import dataclass


class TrainingError(ValueError):
    """Invalid experiment, not a bad model answer."""


class VerificationBlocked(TrainingError):
    """An oracle, harness, or engine failure must not become an RL reward."""


@dataclass(frozen=True)
class Score:
    reward: float
    status: str
    native_tests: int = 0
    total_tests: int = 0
    refusals: tuple = ()
    failed_test: int = None

    @property
    def correct(self):
        return self.status in ("correct-native", "correct-fallback", "correct-control")

    @property
    def native(self):
        return self.correct and self.total_tests > 0 and self.native_tests == self.total_tests
