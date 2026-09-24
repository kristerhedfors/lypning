"""An engine mismatch on a Step 2 draw is a counted status, never a public quote.

Run 35854009245 (2026-09-23) graded full shard 1 of 2 and died on one draw
whose native run reported a SyntaxError CPython did not: the abort's message
carried the case id and its expected stdout into a public Actions log. This
pins both halves of the fix: the exception text is a kind and a digest only,
and a grade records such a draw as ``engine-mismatch`` -- not correct, never a
target, its witness in a private file -- up to a 1% bound.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from pipeline import positive_control_grade as grade
from pipeline.jsonio import read_jsonl
from pipeline.training import Verifier as RealVerifier
from pipeline.training_types import ENGINE_MISMATCH, Score, VerificationBlocked

PRIVATE = "PRIVATE_CASE_"
SECRET_STDOUT = "My &lt;Page&gt; &amp; Title\n"
SCRIPTS = Path(__file__).resolve().parents[2] / ".github" / "scripts"


def cases(n=12):
    out = []
    for i in range(n):
        control = i % 4 == 3
        out.append({"case_id": "%s%d" % (PRIVATE, i), "family": "%sfam-%d" % (PRIVATE, i % 5),
                    "task": "task %d" % i, "split_group": "g%d" % (i % 3),
                    "population": "fallback-control" if control else "coverage",
                    "capabilities": ["cap-%d" % (i % 2)], "split": "train",
                    "tests": [{"stdout": SECRET_STDOUT}, {"stdout": SECRET_STDOUT}]})
    return out


def completions(rows, samples=2):
    return [{"case_id": c["case_id"], "draw": d, "arm": a, "seed": 1111 + d,
             "completion": "```python\nprint(%r)\n```" % ("%s/%d/%s" % (c["case_id"], d, a)),
             "finish_reason": "length" if (d + len(c["case_id"])) % 7 == 0 else "stop",
             "usage": {"completion_tokens": 8 + d}}
            for c in rows for d in range(samples) for a in ("bare", "subset-spec")]


class Verifier:
    """Deterministic, varied verdicts; `mismatch` names programs that block."""

    def __init__(self, mismatch=()):
        self.mismatch = set(mismatch)

    def score(self, case, program):
        if program in self.mismatch:
            raise VerificationBlocked(ENGINE_MISMATCH, {
                "case_id": case["case_id"], "test": 1, "expected_stdout": SECRET_STDOUT,
                "observed": [1, "", "SyntaxError: expected ')', found 'is'\n",
                             False, False, False, False]})
        n = sum(map(ord, program))
        if case["population"] == "fallback-control":
            if n % 3:
                return Score(1, "correct-control", 0, 2, ((0, "lypning-l: unsupported: x: y"),))
            return Score(0, "incorrect", 0, 2, (), 1)
        if n % 5 == 0:
            return Score(0, "unstable", 0, 2, (), 0)
        if n % 4 == 0:
            return Score(0, "incorrect", 0, 2, (), 1)
        if n % 2:
            return Score(1, "correct-native", 2, 2)
        return Score(0.25, "correct-fallback", 1, 2, ((1, "lypning-l: unsupported: x: y"),))


def run_grade(out, verifier=None, rows=None):
    rows = rows or cases()
    return grade.grade(rows, completions(rows), verifier or Verifier(), out, samples=2,
                       workers=3, run_id="fixture-run", lineage={"engine_sha256": "e" * 64})


def program_of(case_id, draw, arm):
    return "print(%r)" % ("%s/%d/%s" % (case_id, draw, arm))


#: sha256 of every file `grade` wrote for `run_grade` with no mismatch, taken
#: from origin/main at 73dea6c -- before engine-mismatch grading existed. A
#: run with no engine mismatch must grade exactly as it did then, with one
#: stated exception: since 2026-09-24 `summarize` reports `engine_mismatches`
#: in every slice, zero included, so report.json carries that count and
#: nothing else new (`test_report_json_differs_only_by_the_mismatch_count`).
GOLDEN = {
    'public-report.json': '6cadb35137af77a9c42d0eb00006d60f9c98a0426ccf1816d73fd18160cd0168',
    'report.json': 'f0b27baa922e4d7b78a2c2d9ce143a3e86bb3fb0432bb3905fcba3d955edfcfd',
    'rows.jsonl': '1519f6f4ea8792641729f902e58d909d1df81d28c668187a4bac0ba26a0bf93c',
    'sft-report.json': '30ca2fcd72f8c8b5aaa2fab613c7c90d41900ad070ce7c6025e63a111c745133',
    'sft.jsonl': '1949e41e44480d366b9dd97d76178aa2d57358d8db56cd7c94194d57d5e3463e',
}
#: report.json at 73dea6c, before `summarize` reported the count.
REPORT_BEFORE_THE_COUNT = 'c4ea60a59d986f328b6b8aa93645eb17c267a85f75499fc09e3159ada54a3b34'


def test_a_grade_without_mismatch_is_byte_identical_to_before(tmp_path):
    out = tmp_path / "g"
    run_grade(out)
    written = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()}
    assert written == GOLDEN


def test_report_json_differs_only_by_the_mismatch_count(tmp_path):
    """Strip every `engine_mismatches: 0` from the metrics: the old bytes return."""
    from pipeline.jsonio import write_json
    out = tmp_path / "g"
    run_grade(out)
    report = json.loads((out / "report.json").read_text())
    stripped = []

    def strip(node):
        if isinstance(node, dict):
            if "statuses" in node:
                assert node.pop("engine_mismatches") == 0
                stripped.append(node)
            for value in node.values():
                strip(value)

    strip(report["metrics"])
    assert stripped and "engine_mismatches" not in json.dumps(report)
    write_json(tmp_path / "before.json", report)
    assert hashlib.sha256((tmp_path / "before.json").read_bytes()).hexdigest() == REPORT_BEFORE_THE_COUNT


def load(name):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result(stdout="", **kwargs):
    from pipeline.sandbox import RunResult
    defaults = {"exit_code": 0, "stdout": stdout, "stderr": "", "duration_s": 0.01}
    defaults.update(kwargs)
    return RunResult(**defaults)


def private_case():
    return {"case_id": PRIVATE + "incident", "family": PRIVATE + "fam", "population": "coverage",
            "split": "train", "task": "escape the title",
            "tests": [{"stdin": "", "stdout": SECRET_STDOUT}]}


def assert_private(exc, *secrets):
    """Nothing of the case in str, repr or the formatted exception."""
    import traceback
    shown = str(exc) + repr(exc) + "".join(traceback.format_exception(type(exc), exc, None))
    for secret in (PRIVATE, SECRET_STDOUT.strip(), "&lt;") + secrets:
        assert secret not in shown, secret


# -- the message ---------------------------------------------------------------

def test_an_engine_mismatch_names_a_kind_and_a_digest_never_the_case():
    """The incident, replayed: native SyntaxError where CPython printed the title."""
    native = result(exit_code=1, stderr="Traceback ...\nSyntaxError: expected ')', found 'is'\n")
    runs = iter([result(SECRET_STDOUT), result(SECRET_STDOUT), native])
    verifier = RealVerifier("/engine", runner=lambda *a, **k: next(runs))
    with pytest.raises(VerificationBlocked) as caught:
        verifier.score(private_case(), "print(title)")
    exc = caught.value
    assert exc.kind == ENGINE_MISMATCH
    assert str(exc) == "engine mismatch (witness %s)" % exc.digest and len(exc.digest) == 12
    assert_private(exc, "SyntaxError", "print(title)")
    # The detail is all still there, on the attribute callers persist privately.
    assert exc.witness["case_id"] == PRIVATE + "incident"
    assert exc.witness["expected_stdout"] == SECRET_STDOUT
    assert "SyntaxError" in exc.witness["observed"][2]


def _raise_os_error(*a, **k):
    raise OSError("spawn %s" % PRIVATE)


@pytest.mark.parametrize("runner,kind", [
    (lambda *a, **k: result(harness_error="materialize %s/input.txt" % PRIVATE), "harness"),
    (_raise_os_error, "runner failed"),
])
def test_harness_and_runner_detail_stays_on_the_witness(runner, kind):
    with pytest.raises(VerificationBlocked) as caught:
        RealVerifier("/engine", runner=runner).score(private_case(), "print(1)")
    assert caught.value.kind == kind and str(caught.value).startswith(kind)
    assert_private(caught.value)
    assert PRIVATE in json.dumps(caught.value.witness)


def test_a_bad_refusal_does_not_name_the_case():
    runs = iter([result(SECRET_STDOUT), result(SECRET_STDOUT),
                 result(exit_code=90, stderr="not the refusal line\n")])
    with pytest.raises(VerificationBlocked) as caught:
        RealVerifier("/engine", runner=lambda *a, **k: next(runs)).score(private_case(), "x=1")
    assert caught.value.kind == "refusal protocol"
    assert_private(caught.value)
    assert caught.value.witness["case_id"] == PRIVATE + "incident"


def test_a_plain_block_is_its_message_and_digests_are_stable():
    assert str(VerificationBlocked("container protocol mismatch")) == "container protocol mismatch"
    one = VerificationBlocked(ENGINE_MISMATCH, {"b": [1, 2], "a": "x"})
    two = VerificationBlocked(ENGINE_MISMATCH, {"a": "x", "b": (1, 2)})
    assert one.digest == two.digest and str(one) == str(two)
    import pickle
    again = pickle.loads(pickle.dumps(one))
    assert (again.kind, again.witness, str(again)) == (one.kind, one.witness, str(one))


def test_the_rl_reward_writes_the_witness_privately_and_raises_a_clean_message(tmp_path):
    from pipeline.training import Reward
    case = private_case()

    class Blocking:
        def score(self, case, program):
            raise VerificationBlocked(ENGINE_MISMATCH, {"case_id": case["case_id"],
                                                        "expected_stdout": SECRET_STDOUT})

    path = tmp_path / "blocked-witnesses.jsonl"
    reward = Reward([case], Blocking(), path)
    with pytest.raises(VerificationBlocked) as caught:
        reward(["```python\nprint(1)\n```"], [case["case_id"]])
    assert_private(caught.value)
    row = json.loads(path.read_text())
    assert row["kind"] == ENGINE_MISMATCH and row["witness"]["expected_stdout"] == SECRET_STDOUT
    assert row["case_id"] == case["case_id"] and PRIVATE not in row["error"]
    # A foreign id in the batch is refused without being quoted.
    with pytest.raises(Exception) as foreign:
        reward(["x"], [PRIVATE + "other"])
    assert PRIVATE not in str(foreign.value)


# -- the grade -----------------------------------------------------------------

def test_a_mismatched_draw_is_counted_excluded_and_kept_privately(tmp_path):
    rows = cases(30)                         # 120 graded draws: one mismatch is under 1%
    bad = program_of(rows[0]["case_id"], 1, "subset-spec")
    public = run_grade(tmp_path / "g", Verifier({bad}), rows)
    out = tmp_path / "g"
    graded = read_jsonl(out / "rows.jsonl")
    hit = [r for r in graded if r["status"] == grade.ENGINE_MISMATCH_STATUS]
    assert len(hit) == 1 and hit[0]["case_id"] == rows[0]["case_id"]
    assert hit[0]["correct"] is False and hit[0]["native"] is False
    assert hit[0]["score"]["failed_test"] == 1 and hit[0]["score"]["total_tests"] == 2
    # Public: the count, and the status among the arm's statuses. Nothing else.
    assert public["engine_mismatches"] == 1
    assert public["arms"]["subset-spec"]["statuses"][grade.ENGINE_MISMATCH_STATUS] == 1
    assert grade.ENGINE_MISMATCH_STATUS not in public["arms"]["bare"]["statuses"]
    assert public["targets"]["rejected"][grade.ENGINE_MISMATCH_STATUS] == 1
    text = (out / "public-report.json").read_text()
    assert PRIVATE not in text and SECRET_STDOUT.strip() not in text and "SyntaxError" not in text
    assert json.loads((out / "report.json").read_text())["engine_mismatches"] == 1
    # Never a target.
    assert bad not in (out / "sft.jsonl").read_text()
    # Private: the witness, with the program and the engine's own words.
    kept = read_jsonl(out / grade.ENGINE_MISMATCH_FILE)
    assert len(kept) == 1 and kept[0]["program"] == bad and kept[0]["draw"] == 1
    assert kept[0]["witness"]["expected_stdout"] == SECRET_STDOUT and len(kept[0]["digest"]) == 12


def test_a_clean_grade_writes_no_witness_file(tmp_path):
    public = run_grade(tmp_path / "g")
    assert not (tmp_path / "g" / grade.ENGINE_MISMATCH_FILE).exists()
    assert "engine_mismatches" not in public


def test_the_one_percent_bound_fails_the_grade_with_counts_only(tmp_path):
    rows = cases(30)
    bad = {program_of(rows[0]["case_id"], 0, "bare"), program_of(rows[1]["case_id"], 0, "bare")}
    with pytest.raises(grade.EngineMismatchBound) as caught:
        run_grade(tmp_path / "g", Verifier(bad), rows)
    assert str(caught.value) == "engine-mismatch draws 2 of 120 exceed the 1% bound"
    # No grade that looks whole; the witnesses are kept for the failure upload.
    assert not (tmp_path / "g" / "rows.jsonl").exists()
    assert len(read_jsonl(tmp_path / "g" / grade.ENGINE_MISMATCH_FILE)) == 2


def test_the_bound_is_exclusive_at_one_percent():
    assert not grade.over_mismatch_bound(0, 0)
    assert not grade.over_mismatch_bound(1, 100) and grade.over_mismatch_bound(2, 100)
    assert grade.over_mismatch_bound(1, 99)
    assert not grade.over_mismatch_bound(108, 10840)


def test_any_other_block_still_aborts_the_grade(tmp_path):
    class Harness(Verifier):
        def score(self, case, program):
            raise VerificationBlocked("harness", {"harness_error": PRIVATE})

    with pytest.raises(VerificationBlocked, match="^harness") as caught:
        run_grade(tmp_path / "g", Harness())
    assert_private(caught.value)
    assert not (tmp_path / "g" / "rows.jsonl").exists()


# -- the merge -----------------------------------------------------------------

def test_the_merge_aggregates_the_status_as_the_grader_does(tmp_path):
    m = load("step2_merge")
    rows = cases(30)
    bad = program_of(rows[2]["case_id"], 0, "subset-spec")
    run_grade(tmp_path / "g", Verifier({bad}), rows)
    graded = read_jsonl(tmp_path / "g" / "rows.jsonl")
    public = m.aggregate(rows, completions(rows), graded, tmp_path / "again", samples=2,
                         run_id="fixture-run", lineage={"engine_sha256": "e" * 64},
                         target_arms=("subset-spec",))
    assert public["engine_mismatches"] == 1
    for name in ("rows.jsonl", "report.json", "sft.jsonl", "sft-report.json", "public-report.json"):
        assert (tmp_path / "again" / name).read_bytes() == (tmp_path / "g" / name).read_bytes(), name
    # Shards each under the bound cannot sum past it, but the union is
    # re-checked because the union is what trains.
    worse = [dict(r, status=grade.ENGINE_MISMATCH_STATUS, correct=False, native=False)
             if r["case_id"] in (rows[0]["case_id"], rows[1]["case_id"]) else r for r in graded]
    with pytest.raises(grade.EngineMismatchBound):
        m.aggregate(rows, completions(rows), worse, tmp_path / "worse", samples=2,
                    run_id="fixture-run", lineage={}, target_arms=("subset-spec",))


# -- the grade script's log ----------------------------------------------------

def test_the_grade_script_prints_a_type_and_digest_and_keeps_the_rest(tmp_path, monkeypatch, capsys):
    g = load("step2_grade")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    (tmp_path / "step2-grade").mkdir()
    (tmp_path / "step2-grade" / grade.ENGINE_MISMATCH_FILE).write_text('{"case_id": "%s"}\n' % PRIVATE)

    def boom(root):
        raise VerificationBlocked(ENGINE_MISMATCH, {"case_id": PRIVATE + "x",
                                                    "expected_stdout": SECRET_STDOUT})

    monkeypatch.setattr(g, "grade_run", boom)
    assert g.main() == 1
    out = capsys.readouterr()
    shown = out.out + out.err
    assert PRIVATE not in shown and SECRET_STDOUT.strip() not in shown
    assert "engine mismatch" not in shown          # not even the safe message: type only
    assert "VerificationBlocked" in out.err
    failure = tmp_path / g.FAILURE_DIR
    detail = json.loads((failure / "failure.json").read_text())
    assert detail["digest"] in out.err and detail["witness"]["case_id"] == PRIVATE + "x"
    trace = (failure / "traceback.txt").read_text()
    assert hashlib.sha256(trace.encode()).hexdigest()[:12] == detail["digest"]
    assert (failure / grade.ENGINE_MISMATCH_FILE).read_text().startswith('{"case_id"')

    # Any exception at all, whatever its text.
    monkeypatch.setattr(g, "grade_run", lambda root: {}["%s-key" % PRIVATE])
    assert g.main() == 1
    out = capsys.readouterr()
    assert "KeyError" in out.err and PRIVATE not in out.out + out.err


def test_a_failed_grade_uploads_its_evidence_privately_and_only_then(tmp_path, monkeypatch):
    from types import SimpleNamespace
    calls = []
    fake = SimpleNamespace(HfApi=lambda token: SimpleNamespace(
        whoami=lambda: {"name": "owner"},
        repo_info=lambda *a, **k: SimpleNamespace(private=True),
        upload_folder=lambda **k: calls.append(k)))
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("HF_TOKEN", "t")
    monkeypatch.setenv("STEP2_RUN_ID", "run-1")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    up = load("step2_grade_upload")
    up.main(["--failure"])
    assert calls == []                              # nothing kept, nothing uploaded
    (tmp_path / "step2-grade-failure").mkdir()
    up.main(["--failure"])
    assert calls[0]["path_in_repo"] == "positive-control/run-1/grade-failure/42"
    up.main([])
    assert calls[1]["path_in_repo"] == "positive-control/run-1/grade"
    workflow = (SCRIPTS.parent / "workflows" / "step2-control-grade.yml").read_text()
    assert "if: failure()" in workflow and "step2_grade_upload.py --failure" in workflow


# -- review follow-ups (2026-09-23) --------------------------------------------

def test_witnesses_found_before_another_block_aborts_the_grade_are_kept(tmp_path):
    """The failure upload copies them; a re-grade must not be the only way back."""
    rows = cases(30)
    bad = program_of(rows[0]["case_id"], 0, "bare")

    class ThenHarness(Verifier):
        seen = 0

        def score(self, case, program):
            if program == bad:
                return Verifier({bad}).score(case, program)
            ThenHarness.seen += 1
            if ThenHarness.seen > 40:
                raise VerificationBlocked("harness", {"harness_error": PRIVATE})
            return Verifier().score(case, program)

    rows_sorted = sorted(rows, key=lambda c: c["case_id"] != rows[0]["case_id"])
    with pytest.raises(VerificationBlocked, match="^harness"):
        grade.grade(rows_sorted, completions(rows_sorted), ThenHarness(), tmp_path / "g",
                    samples=2, workers=1, run_id="fixture-run", lineage={})
    assert not (tmp_path / "g" / "rows.jsonl").exists()
    kept = read_jsonl(tmp_path / "g" / grade.ENGINE_MISMATCH_FILE)
    assert [w["program"] for w in kept] == [bad]


def test_reference_admission_names_a_case_by_digest_only():
    """`validate_reference_scores` runs in training-prepare and bundle load on the GPU job."""
    from dataclasses import asdict
    from pipeline.training_data import validate_reference_scores
    from pipeline.training_types import TrainingError, case_ref
    case = dict(private_case(), tests=[{"stdout": "a"}, {"stdout": "b"}, {"stdout": "c"}])
    scores = {case["case_id"]: asdict(Score(.25, "correct-fallback", 0, 3))}
    with pytest.raises(TrainingError) as caught:
        validate_reference_scores([case], scores)
    assert case_ref(case["case_id"]) in str(caught.value)
    assert_private(caught.value)
    scores[case["case_id"]] = asdict(Score(1, "correct-native", 3, 3))
    case["population"] = "fallback-control"
    with pytest.raises(TrainingError) as caught:
        validate_reference_scores([case], scores)
    assert_private(caught.value)


def test_the_trainer_never_prints_a_key_error_s_key(monkeypatch, capsys):
    """A KeyError's text is its key; on the GPU job that key can be a case id."""
    spec = importlib.util.spec_from_file_location(
        "verified_gpu_keyerror_test",
        Path(__file__).resolve().parents[2] / "training" / "gpu" / "train_verified.py")
    tv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tv)

    def preflight(args):
        return {}[PRIVATE + "missing"]

    monkeypatch.setattr(tv, "preflight", preflight)
    assert tv.main(["eval", "--bundle", "b.json", "--output", "o", "--engine", "e",
                    "--revision", "0" * 40]) == 1
    out = capsys.readouterr()
    assert "training blocked: KeyError" in out.err
    assert PRIVATE not in out.out + out.err
