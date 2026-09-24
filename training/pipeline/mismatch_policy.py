"""An engine-mismatch draw is a counted, zero-scored draw, up to 1% of a run.

`Verifier.score` raises `VerificationBlocked(ENGINE_MISMATCH)` when a program's
native run disagrees with a clean CPython oracle. That is an engine bug (root
``CLAUDE.md`` invariant 1), and it is the draw's own outcome, not ours: one
model draw that happens to reach a lypning-l bug says nothing about the
harness. Aborting on it ended the seed-1111 arm-A pilot (HF job
6ab52a686b030d633f68e503, 2026-09-24) in its base test arm, after all of SFT,
on one base-model draw -- and with the engine frozen for arm A such draws
recur, so eval-2 (~25,700 draws) would have met the same abort.

So everywhere a draw is scored -- the Step 2 grade
(`positive_control_grade`), GPU evaluation (`verified_evaluation`) and GRPO's
reward (`verified_stages.train_grpo`) -- such a draw is:

- **counted**, with status ``engine-mismatch``: reward 0, neither correct nor
  native, so it counts against the arm that drew it (conservative, and
  symmetric: every arm is scored by this same rule), and never an SFT target;
- **witnessed privately**: its program, case and the verifier's detail go to
  ``engine-mismatches.jsonl`` beside the stage's rows, which is uploaded to the
  private work repository only; the bug is filed from there;
- **bounded**: once engine-mismatch draws exceed 1% of the run's draws the run
  fails with `EngineMismatchBound`, whose message is two counts and nothing
  else. Past that the rows describe the engine, not the model.

Every other `VerificationBlocked` kind -- harness, runner, refusal protocol,
transport, identity drift -- still aborts exactly as before; those are ours.

The GPU arms keep one more carve-out, `native_timeout`: a native TIMEOUT after
a correct oracle is raised under the same kind, but ledger row T4
(`ORCHESTRATION.md`, closed 2026-09-17) ruled that it stays a hard abort,
because scoring it would make the primary endpoint depend on host load. This
module does not reopen that ruling; `counted_on_gpu` is where it is kept.
"""
from __future__ import annotations

import threading

from .jsonio import append_jsonl
from .training_types import ENGINE_MISMATCH, Score, TrainingError, VerificationBlocked

#: The status of a draw whose native run disagreed with a clean CPython oracle.
#: `Score.correct` is False for it, so it counts against both rates, and the
#: target builder admits only correct-native / correct-control.
ENGINE_MISMATCH_STATUS = "engine-mismatch"
#: The PRIVATE file beside a stage's rows that holds each such draw's witness;
#: written only when there is one, so a clean run's files are unchanged.
ENGINE_MISMATCH_FILE = "engine-mismatches.jsonl"
#: A run fails once engine-mismatch draws exceed this percentage of its draws.
ENGINE_MISMATCH_BOUND_PERCENT = 1


class EngineMismatchBound(VerificationBlocked):
    """More engine mismatches than a run may absorb. Its message is counts only.

    A `VerificationBlocked`, so every caller that treats a block as the end of
    a stage treats this one the same way; it carries no witness, because the
    witnesses are already in the private file.
    """


def engine_mismatches(rows):
    """How many rows are engine mismatches: the only public fact about them."""
    return sum(r.get("status") == ENGINE_MISMATCH_STATUS for r in rows)


def over_mismatch_bound(count, total):
    """True when `count` of `total` draws exceeds the bound; integers, no float edge."""
    return count * 100 > total * ENGINE_MISMATCH_BOUND_PERCENT


def check_mismatch_bound(count, total):
    if over_mismatch_bound(count, total):
        raise EngineMismatchBound("engine-mismatch draws %d of %d exceed the %d%% bound"
                                  % (count, total, ENGINE_MISMATCH_BOUND_PERCENT))


def native_timeout(exc):
    """True when an engine-mismatch block is the native run timing out.

    `Verifier._observed` is (exit, stdout, stderr, timed_out, truncated,
    memory_exceeded, encoding_error); an unreadable witness is not a timeout.
    """
    detail = exc.witness if isinstance(exc.witness, dict) else {}
    observed = detail.get("observed")
    return isinstance(observed, (list, tuple)) and len(observed) > 3 and observed[3] is True


def counted_on_gpu(exc):
    """Whether a GPU arm scores this block as a draw rather than aborting on it.

    An engine mismatch, except a native timeout (ledger row T4, see above).
    """
    return exc.kind == ENGINE_MISMATCH and not native_timeout(exc)


def mismatch_score(case, exc):
    """The zero-reward `Score` an engine-mismatch draw is recorded as."""
    detail = exc.witness if isinstance(exc.witness, dict) else {}
    return Score(0.0, ENGINE_MISMATCH_STATUS, total_tests=len(case.get("tests") or ()),
                 failed_test=detail.get("test"))


def witness_row(case, program, exc, **context):
    """A PRIVATE witness row: the program, the case's tests and the verifier's detail.

    Read with `.get` throughout, as `verified_evaluation.blocked_witness` does:
    a KeyError here would replace the evidence it exists to keep.
    """
    return dict(context, case_id=case.get("case_id"), family=case.get("family"),
                population=case.get("population"),
                split_group=case.get("split_group") or case.get("family"),
                program=program, tests=case.get("tests"), kind=exc.kind,
                digest=exc.digest, witness=exc.witness)


class MismatchScoring:
    """A verifier whose engine-mismatch draws are scored instead of raised (GRPO).

    Wraps `Verifier.score` for `pipeline.training.Reward`, which aborts on every
    block it sees: a counted mismatch never reaches it, and so scores 0 like
    any other wrong draw. Its witness is appended to `path` (PRIVATE), the
    count is `count`, and once the count exceeds the bound on `planned` -- the
    run's registered draws, steps x prompts x generations -- `EngineMismatchBound`
    is raised from the draw that crossed it. Against the planned total, not
    the draws so far: the bound is on the run, and one mismatch in a 32-draw
    step would otherwise be 3% and end the run exactly as before.

    Wrapping keeps `pipeline/training.py` -- hashed into `verifier_sha256`,
    which prepared bundles and the adapters trained on them are bound to --
    byte-identical.
    """

    def __init__(self, verifier, path, planned):
        if type(planned) is not int or planned < 1:
            raise TrainingError("the engine-mismatch bound needs the run's planned draw count")
        self.verifier, self.path, self.planned = verifier, path, planned
        self.count = 0
        self._lock = threading.Lock()

    def score(self, case, program):
        try:
            return self.verifier.score(case, program)
        except VerificationBlocked as exc:
            if not counted_on_gpu(exc):
                raise
            with self._lock:
                self.count += 1
                count = self.count
                if self.path is not None:
                    append_jsonl(self.path, witness_row(case, program, exc,
                                                        source="rollout", mismatch=count))
            check_mismatch_bound(count, self.planned)
            return mismatch_score(case, exc)
