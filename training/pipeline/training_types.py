"""Shared training failures and scores, independent of GPU libraries."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

#: The `VerificationBlocked.kind` of a native run that disagreed with a clean
#: CPython oracle: an engine bug (root ``CLAUDE.md`` invariant 1).
ENGINE_MISMATCH = "engine mismatch"


class TrainingError(ValueError):
    """Invalid experiment, not a bad model answer."""


def witness_digest(witness):
    """sha256[:12] of a witness's canonical JSON: names it without quoting it."""
    text = json.dumps(witness, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=repr)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def case_ref(case_id):
    """A case named in a message without quoting its id: ``case <sha256[:12]>``.

    A case id is private (it names the task), and a message reaches the GPU
    job's log, which the round follower streams publicly. Whoever holds the
    bank finds the case by hashing its ids the same way.
    """
    return "case " + hashlib.sha256(str(case_id).encode("utf-8")).hexdigest()[:12]


class VerificationBlocked(TrainingError):
    """An oracle, harness, or engine failure must not become an RL reward.

    Its ``str()`` is PUBLIC: it reaches Actions logs and the GPU job's log,
    which the round follower streams world-readably. So the message is only a
    fixed ``kind`` and, when there is detail, the ``digest`` of it; the detail
    itself -- case id, test index, expected and observed output, harness text
    -- is ``witness``, which callers persist to a PRIVATE file only. Run
    35854009245 (2026-09-23) printed a case id and its expected stdout into a
    public log through this message; that is the leak this shape closes.
    """

    def __init__(self, kind, witness=None):
        self.kind = str(kind)
        self.witness = witness
        self.digest = witness_digest(witness) if witness is not None else None
        super().__init__(self.kind if self.digest is None
                         else "%s (witness %s)" % (self.kind, self.digest))

    def __reduce__(self):
        return (type(self), (self.kind, self.witness))


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
