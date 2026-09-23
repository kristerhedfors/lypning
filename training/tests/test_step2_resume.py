"""The explicit resume of a stopped paid run: same experiment, remainder only, one ceiling.

Everything here is decidable for free. A fake provider answers each request
deterministically from its (case, draw, arm), so a run stopped by one
ambiguous transport failure and then resumed can be compared, file for file,
with the same run uninterrupted -- and a resume that requested anything but
the remainder, spent past the chain's ceiling, counted a request twice or
printed a case would show up as a difference here.
"""
from __future__ import annotations

from decimal import Decimal
import importlib.util
import itertools
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pipeline import positive_control_generate as gen
from pipeline import positive_control_grade as grade
from pipeline import positive_control_resume as res
from pipeline.backends import BackendError, ChatBackend
from pipeline.jsonio import read_jsonl, sha256_of
from pipeline.training_types import Score, TrainingError

SCRIPTS = Path(__file__).resolve().parents[2] / ".github" / "scripts"
PRIVATE = "PRIVATE_CASE_"
SPEC = "SUBSET SPEC"
SHARD = {"shard_index": 1, "shard_count": 2, "skip_prefix": 192, "cases": 1355}
FILES = ("rows.jsonl", "report.json", "sft.jsonl", "sft-report.json", "public-report.json")


def load(name):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_id(n, rung="full-1of2"):
    return "%s-%s-%d" % (rung, "a" * 40, 35828368891 + n)


def cases(n=8):
    out = []
    for i in range(n):
        control = i % 4 == 3
        out.append({"case_id": "%s%d" % (PRIVATE, i), "family": "%sfam-%d" % (PRIVATE, i % 4),
                    "task": "task %d" % i, "split_group": "g%d" % (i % 3),
                    "population": "fallback-control" if control else "coverage",
                    "capabilities": [], "split": "train", "tests": [{}, {}]})
    return out


def admission(cs, **conformance):
    return {
        "source_commit": "a" * 40, "candidate_image": "sha256:" + "b" * 64,
        "case_fingerprints": {c["case_id"]: sha256_of(c) for c in cs},
        "conformance": dict({"engine_sha256": "c" * 64, "base_image": "python@sha256:" + "d" * 64,
                             "unbuilt": [], "damage": [], "loaded": 10,
                             "engines": {"lypning-l": {"counts": {"mismatch": 0, "total": 10}}}},
                            **conformance),
        "references": {"engine_sha256": conformance.get("engine_sha256", "c" * 64),
                       "base_image": conformance.get("base_image", "python@sha256:" + "d" * 64),
                       "python": "3.12.7", "cases": len(cs),
                       "counts": {"correct-native": len(cs)}},
    }


def recipe(commit):
    return {"training/worker/Dockerfile.verifier": "blob-" + commit[:1]}


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    ticks = itertools.count(0, 0.01)
    monkeypatch.setattr(gen, "time", SimpleNamespace(monotonic=lambda: next(ticks),
                                                     sleep=lambda _: None))


class Provider:
    """Deterministic answers keyed by request; scripted failures by call number."""

    def __init__(self, cs, fail=None, tag="", bad_usage=None):
        self.by_task = {c["task"]: c["case_id"] for c in cs}
        self.fail = fail or {}
        self.bad_usage = bad_usage or set()
        self.tag = tag
        self.calls = []

    def key(self, messages, seed):
        arm = "subset-spec" if SPEC in messages[0]["content"] else "bare"
        return res.request_key(self.by_task[messages[1]["content"]], seed - 1111, arm)

    def __call__(self, messages, **kwargs):
        key = self.key(messages, kwargs["seed"])
        self.calls.append(key)
        if len(self.calls) in self.fail:
            raise self.fail[len(self.calls)]
        usage = {"prompt_tokens": 100 + len(key), "completion_tokens": 7 + kwargs["seed"] % 5}
        if len(self.calls) in self.bad_usage:
            usage = {}
        return SimpleNamespace(
            text="```python\nprint(%r)\n```" % (self.tag + key), finish_reason="stop",
            reasoning=None, raw={"model": gen.MODEL, "usage": usage,
                                 "choices": [{"message": {"content": "x"}}]})


def backend(provider):
    chat = ChatBackend(gen.PROVIDER, gen.MODEL, max_retries=0, timeout_s=120)
    chat.complete = provider
    return chat


TRANSPORT = BackendError("giving up after 0 retries: PRIVATE_CASE_ in a connection error")


def paid_run(root, run, cs, provider, *, ceiling=1, resume=None, samples=2, workers=1, adm=None):
    """One generation as `step2_generate` leaves it: paid/ plus shard.json and admission."""
    base = Path(root) / "positive-control" / run
    adm = adm or admission(cs)
    result = gen.generate(cs, SPEC, backend(provider), base / "paid", ceiling_usd=ceiling,
                          admission=adm, workers=workers, samples=samples,
                          requests_per_minute=120, max_seconds=18000, resume=resume)
    (base / "paid" / "shard.json").write_text(json.dumps(SHARD, sort_keys=True) + "\n")
    (base / "admission.json").write_text(json.dumps(adm, sort_keys=True) + "\n")
    return result


def link(root, run):
    return res.read_link(run, Path(root) / "positive-control" / run / "paid", recipe)


def resume_of(root, named, this):
    links = res.chain_links(named, lambda r: link(root, r))
    return {"links": links, "run_id": this, "shard": SHARD, "recipe_of": recipe}


def keys(cs, samples=2):
    return [res.request_key(c["case_id"], d, a) for c, d, a in gen.request_order(cs, samples)]


@pytest.fixture
def stopped(tmp_path):
    """A run stopped on its 11th request by one ambiguous transport failure."""
    cs = cases()
    provider = Provider(cs, fail={11: TRANSPORT})
    result = paid_run(tmp_path, run_id(0), cs, provider)
    assert result["complete"] is False and result["completed"] == 10
    assert result["failure_types"] == ["provider-transport"]
    return cs, tmp_path, provider


# --- validation -------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "", "full-0of2", "smoke-%s-1" % ("a" * 40), "full-4of2-%s-1" % ("a" * 40),
    "full-0of2-%s-1/../x" % ("a" * 40), "targets-%s-1 " % ("a" * 40), "targets-ABC-1",
    "../positive-control/targets-%s-1" % ("a" * 40),
])
def test_a_resume_run_id_must_have_the_recorded_shape(bad):
    with pytest.raises(res.ResumeError):
        res.parse_run_id(bad)


def test_a_resume_names_a_run_of_this_rung_and_shard_and_never_the_smoke():
    shard = {"shard_index": 1, "shard_count": 2, "skip_prefix": 192}
    assert res.check_target(run_id(0), run_id(1), "full", shard) == ("full", 1, 2)
    unsharded = {"shard_index": 0, "shard_count": 1, "skip_prefix": 0}
    for rung in ("targets", "budget20", "confirmatory"):
        res.check_target(run_id(0, rung), run_id(1, rung), rung, unsharded)
    refusals = [
        (run_id(0, "full-0of2"), run_id(1), "full", shard, "shard 0 of 2"),
        (run_id(0, "targets"), run_id(1), "full", shard, "a targets run"),
        (run_id(0), run_id(0), "full", shard, "cannot resume itself"),
        (run_id(0, "targets"), run_id(1, "smoke"), "smoke", unsharded, "not resumable"),
    ]
    for named, this, rung, sh, words in refusals:
        with pytest.raises(res.ResumeError, match=words):
            res.check_target(named, this, rung, sh)


def test_the_workflow_validates_before_anything_and_fetches_before_the_build(capsys):
    text = (SCRIPTS.parent / "workflows" / "step2-control.yml").read_text()
    assert "      resume_run_id:\n" in text
    assert "  STEP2_RESUME_RUN_ID: ${{ inputs.resume_run_id }}" in text
    validate = text.index("step2_resume.py validate")
    fetch = text.index("step2_resume.py download")
    assert validate < text.index("step2_shard.py") < text.index("step2_bank_plan.py") < fetch
    assert fetch < text.index("lypning build") < text.index("step2_generate.py")
    # The typed id reaches a shell only through the environment.
    assert text.count("inputs.resume_run_id") == 2
    assert "if: inputs.resume_run_id != ''" in text
    script = load("step2_resume")
    env = {"STEP2_RUN_ID": run_id(1), "STEP2_RUNG": "full", "STEP2_SHARD_INDEX": "1",
           "STEP2_SHARD_COUNT": "2", "STEP2_SKIP_PREFIX": "192"}
    assert script.main(["validate"], dict(env)) == 0
    assert json.loads(capsys.readouterr().out) == {"resume": False}
    assert script.main(["validate"], dict(env, STEP2_RESUME_RUN_ID=run_id(0))) == 0
    assert script.main(["validate"], dict(env, STEP2_RESUME_RUN_ID=run_id(0, "full-0of2"))) == 1
    assert "refused during validate" in capsys.readouterr().err


# --- identity -----------------------------------------------------------------------------

def moved(field, current):
    changed = dict(current)
    changed[field] = {"moved": True}
    return changed


@pytest.mark.parametrize("field", list(res.IDENTITY))
def test_a_resume_of_another_experiment_is_refused_naming_the_field(stopped, field):
    cs, root, _ = stopped
    prior = link(root, run_id(0))
    with pytest.raises(res.ResumeError) as refused:
        res.check_identity([prior], moved(field, prior["identity"]))
    assert str(refused.value) == "run %s differs from this run in %s" % (run_id(0), field)
    assert PRIVATE not in str(refused.value)


def test_every_identity_field_is_pinned_by_a_refusal_test():
    marks = test_a_resume_of_another_experiment_is_refused_naming_the_field.pytestmark
    fields = next(mark.args[1] for mark in marks if mark.name == "parametrize")
    assert tuple(fields) == res.IDENTITY


@pytest.mark.parametrize("change, field", [
    (dict(adm=lambda cs: admission(cs, engine_sha256="e" * 64)), "engine_sha256"),
    (dict(adm=lambda cs: admission(cs, base_image="python@sha256:" + "f" * 64)), "base_image"),
    (dict(spec=SPEC + " edited"), "spec_sha256"),
    (dict(samples=1), "requests, samples"),
    (dict(recipe=lambda commit: {"x": "moved"}), "candidate_recipe"),
    (dict(shard=dict(SHARD, skip_prefix=0)), "shard"),
])
def test_generation_refuses_a_different_experiment_before_any_output_or_call(stopped, change, field):
    cs, root, _ = stopped
    resume = resume_of(root, run_id(0), run_id(1))
    if "recipe" in change:
        resume["recipe_of"] = change["recipe"]
    if "shard" in change:
        resume["shard"] = change["shard"]
    provider = Provider(cs)
    out = root / "resumed"
    with pytest.raises(res.ResumeError) as refused:
        gen.generate(cs, change.get("spec", SPEC), backend(provider), out, ceiling_usd=14,
                     admission=change.get("adm", admission)(cs), workers=1,
                     samples=change.get("samples", 2), resume=resume)
    assert field in str(refused.value) and PRIVATE not in str(refused.value)
    assert provider.calls == [] and not out.exists()


# --- the remainder ------------------------------------------------------------------------

def test_the_remainder_is_planned_minus_settled_and_names_the_ambiguous_request(stopped):
    cs, root, provider = stopped
    planned = keys(cs)
    ambiguous = provider.calls[10]
    state = res.plan([link(root, run_id(0))], planned, "14")
    assert state["remaining"] == planned[10:]
    assert list(state["re_requested"]) == [ambiguous]
    assert state["re_requested"][ambiguous][0]["run_id"] == run_id(0)
    assert Decimal(state["re_requested"][ambiguous][0]["usd"]) == gen.Budget(1, None).reservation
    assert set(state["prior_rows"]) == set(planned[:10])
    assert state["superseded"] == []


def test_a_resume_requests_exactly_the_remainder_and_records_the_re_request(stopped):
    cs, root, first = stopped
    provider = Provider(cs)
    result = paid_run(root, run_id(1), cs, provider, ceiling=14,
                      resume=resume_of(root, run_id(0), run_id(1)))
    planned = keys(cs)
    assert provider.calls == planned[10:]
    assert result["complete"] is True
    assert (result["completed"], result["planned"]) == (len(planned), len(planned))
    assert (result["completed_in_run"], result["requested_in_run"]) == (22, 22)
    assert result["re_requested"] == 1 and result["superseded"] == 0
    paid = root / "positive-control" / run_id(1) / "paid"
    spend = read_jsonl(paid / "spend.jsonl")
    again = [e for e in spend if e["event"] == "re-request"]
    assert [e["request"] for e in again] == [first.calls[10]]
    assert again[0]["prior"][0]["run_id"] == run_id(0)
    # The re-request sits right after its own reservation, before any response.
    at = spend.index(again[0])
    assert spend[at - 1] == dict(event="reserved", request=first.calls[10],
                                 usd=str(gen.Budget(1, None).reservation))
    audit = json.loads((paid / "resume.json").read_text())
    assert audit["remaining"] == 22 and len(audit["re_requested"]) == 1
    manifest = json.loads((paid / "manifest.json").read_text())
    assert manifest["resume"]["resumed_from"] == [run_id(0)]
    # The prior evidence was only read.
    assert link(root, run_id(0))["result"]["completed"] == 10


def test_with_four_in_flight_the_remainder_is_whatever_did_not_settle(tmp_path):
    """The paid path runs four workers: requests in flight at the failure drain and settle."""
    cs = cases()
    first = Provider(cs, fail={7: TRANSPORT})
    paid_run(tmp_path, run_id(0), cs, first, workers=4)
    prior = link(tmp_path, run_id(0))
    settled = {e["request"] for e in prior["spend"] if e["event"] == "settled"}
    planned = keys(cs)
    provider = Provider(cs)
    result = paid_run(tmp_path, run_id(1), cs, provider, ceiling=14, workers=4,
                      resume=resume_of(tmp_path, run_id(0), run_id(1)))
    assert sorted(provider.calls) == sorted(k for k in planned if k not in settled)
    assert first.calls[6] in provider.calls
    assert result["complete"] and result["completed"] == 32 == len(settled) + len(provider.calls)


def test_a_complete_run_is_graded_never_resumed(tmp_path):
    cs = cases()
    assert paid_run(tmp_path, run_id(0), cs, Provider(cs))["complete"]
    provider = Provider(cs)
    with pytest.raises(res.ResumeError, match="grade it, do not resume it"):
        paid_run(tmp_path, run_id(1), cs, provider, ceiling=14,
                 resume=resume_of(tmp_path, run_id(0), run_id(1)))
    assert provider.calls == []


def test_a_received_but_rejected_response_is_superseded_by_the_resumed_one(tmp_path):
    cs = cases()
    # Call 5 returns a response whose usage breaks the contract: persisted, never settled.
    paid_run(tmp_path, run_id(0), cs, Provider(cs, bad_usage={5}, tag="first:"))
    prior = link(tmp_path, run_id(0))
    assert len(prior["completions"]) == 5 and prior["result"]["completed"] == 4
    provider = Provider(cs, tag="second:")
    result = paid_run(tmp_path, run_id(1), cs, provider, ceiling=14,
                      resume=resume_of(tmp_path, run_id(0), run_id(1)))
    assert result["complete"] and result["superseded"] == 1 and result["re_requested"] == 1
    chain = [prior, link(tmp_path, run_id(1))]
    union = res.union_completions(chain, chain[-1]["result"])
    rejected = res.row_key(prior["completions"][4])
    kept = [r for r in union if res.row_key(r) == rejected]
    assert len(kept) == 1 and "second:" in kept[0]["completion"]
    assert len(union) == len({res.row_key(r) for r in union}) == 32


# --- the ceiling --------------------------------------------------------------------------

def test_the_ceiling_is_the_chains_total_and_every_run_keeps_its_own_dollars(tmp_path):
    cs = cases()
    reservation = gen.Budget(1, None).reservation
    first = paid_run(tmp_path, run_id(0), cs, Provider(cs, fail={11: TRANSPORT}))
    spent0 = Decimal(first["charged_or_reserved_usd"])
    # One ambiguous reservation is retained in the prior's dollars.
    assert spent0 > reservation
    # A second link that stops on its own transport failure.
    second = paid_run(tmp_path, run_id(1), cs, Provider(cs, fail={3: TRANSPORT}), ceiling="14",
                      resume=resume_of(tmp_path, run_id(0), run_id(1)))
    assert second["complete"] is False and second["completed"] == 12
    assert second["completed_in_run"] == 2
    assert Decimal(second["run_ceiling_usd"]) == Decimal(14) - spent0
    spent1 = Decimal(second["charged_or_reserved_usd"])
    assert Decimal(second["chain_charged_or_reserved_usd"]) == spent0 + spent1
    assert second["resumed_from"] == [{"run_id": run_id(0), "charged_or_reserved_usd": str(spent0),
                                       "completed_in_run": 10}]
    # Third link: the chain grows, each run with its own dollars and completions.
    third = paid_run(tmp_path, run_id(2), cs, Provider(cs), ceiling="14",
                     resume=resume_of(tmp_path, run_id(1), run_id(2)))
    assert third["complete"] is True and third["completed"] == third["planned"] == 32
    assert third["re_requested"] == 1  # the second link's ambiguous request
    chain = third["resumed_from"]
    assert [e["run_id"] for e in chain] == [run_id(0), run_id(1)]
    assert [Decimal(e["charged_or_reserved_usd"]) for e in chain] == [spent0, spent1]
    assert sum(e["completed_in_run"] for e in chain) + third["completed_in_run"] == 32
    assert (Decimal(third["chain_charged_or_reserved_usd"]) ==
            spent0 + spent1 + Decimal(third["charged_or_reserved_usd"]))
    assert Decimal(third["run_ceiling_usd"]) == Decimal(14) - spent0 - spent1
    assert Decimal(third["chain_charged_or_reserved_usd"]) <= 14
    # What the total leaves is this run's whole budget: less than one
    # reservation is a spend limit before the first call.
    provider = Provider(cs)
    short = paid_run(tmp_path, run_id(3), cs, provider, ceiling=str(spent0 + reservation / 2),
                     resume=resume_of(tmp_path, run_id(0), run_id(3)))
    assert short["reason"] == "spend limit" and provider.calls == []
    assert Decimal(short["chain_charged_or_reserved_usd"]) == spent0
    # A ceiling the chain has already used up is refused before any output.
    with pytest.raises(res.ResumeError, match="nothing is left"):
        paid_run(tmp_path, run_id(4), cs, provider, ceiling=str(spent0),
                 resume=resume_of(tmp_path, run_id(0), run_id(4)))
    assert provider.calls == [] and not (tmp_path / "positive-control" / run_id(4)).exists()


def test_a_ledger_that_disagrees_with_its_result_is_refused(stopped):
    cs, root, _ = stopped
    result = root / "positive-control" / run_id(0) / "paid" / "result.json"
    data = json.loads(result.read_text())
    data["charged_or_reserved_usd"] = "0.01"
    result.write_text(json.dumps(data))
    with pytest.raises(res.ResumeError, match="disagree on dollars"):
        res.plan([link(root, run_id(0))], keys(cs), "14")


def test_a_recorded_chain_that_differs_from_its_runs_evidence_is_refused(stopped):
    cs, root, _ = stopped
    paid_run(root, run_id(1), cs, Provider(cs), ceiling=14,
             resume=resume_of(root, run_id(0), run_id(1)))
    result = root / "positive-control" / run_id(1) / "paid" / "result.json"
    data = json.loads(result.read_text())
    data["resumed_from"][0]["charged_or_reserved_usd"] = "0.0001"
    result.write_text(json.dumps(data))
    with pytest.raises(res.ResumeError, match="chain's record of run"):
        res.chain_links(run_id(1), lambda r: link(root, r))


# --- grading the union --------------------------------------------------------------------

class Verifier:
    """Deterministic, varied verdicts from the program text alone."""

    def score(self, case, program):
        n = sum(map(ord, program or ""))
        if case["population"] == "fallback-control":
            return Score(1, "correct-control", 0, 2) if n % 3 else Score(0, "incorrect", 0, 2)
        if n % 4 == 0:
            return Score(0, "incorrect", 0, 2)
        return Score(1, "correct-native", 2, 2) if n % 2 else Score(1, "correct-fallback", 0, 2)


def graded(paid, cs, out, chain=None):
    return grade.grade_files(cs, paid / "completions.jsonl", Verifier(), out, samples=2, workers=2,
                             run_id="same", lineage={"engine_sha256": "c" * 64},
                             target_arms=("subset-spec",), chain=chain)


def test_the_grade_of_a_resumed_union_is_the_grade_of_the_uninterrupted_run(stopped, tmp_path):
    cs, root, _ = stopped
    paid_run(root, run_id(1), cs, Provider(cs), ceiling=14,
             resume=resume_of(root, run_id(0), run_id(1)))
    whole = tmp_path / "whole"
    assert paid_run(whole, run_id(9), cs, Provider(cs))["complete"]
    graded(whole / "positive-control" / run_id(9) / "paid", cs, tmp_path / "g-whole")
    paid = root / "positive-control" / run_id(1) / "paid"
    chain = [link(root, run_id(0)), link(root, run_id(1))]
    graded(paid, cs, tmp_path / "g-resumed", chain)
    for name in FILES:
        assert ((tmp_path / "g-resumed" / name).read_bytes() ==
                (tmp_path / "g-whole" / name).read_bytes()), name
    # Without its chain a resumed run is refused, never graded on its own rows.
    with pytest.raises(TrainingError, match="graded over its chain"):
        graded(paid, cs, tmp_path / "g-alone")
    # A request settled in two runs of a chain is refused, not deduplicated.
    twice = [link(root, run_id(0)), link(root, run_id(1))]
    extra = twice[0]["completions"][0]
    twice[1]["completions"].append(extra)
    twice[1]["spend"] += [{"event": "reserved", "request": res.row_key(extra), "usd": "0.1"},
                          {"event": "settled", "request": res.row_key(extra), "usd": "0"}]
    twice[1]["result"] = dict(twice[1]["result"], completed_in_run=23,
                              charged_or_reserved_usd=twice[1]["result"]["charged_or_reserved_usd"])
    with pytest.raises(res.ResumeError):
        res.union_completions(twice, twice[1]["result"])


def test_the_grade_script_reads_the_chain_it_downloaded(stopped, tmp_path):
    cs, root, _ = stopped
    paid_run(root, run_id(1), cs, Provider(cs), ceiling=14,
             resume=resume_of(root, run_id(0), run_id(1)))
    private = tmp_path / "step2-downloaded"
    import shutil
    shutil.copytree(root / "positive-control" / run_id(1), private)
    shutil.copytree(root / "positive-control" / run_id(0) / "paid",
                    private / "chain" / run_id(0) / "paid")
    script = load("step2_grade")
    chain = script.resumed_chain(private, run_id(1), recipe)
    assert [c["run_id"] for c in chain] == [run_id(0), run_id(1)]
    assert script.resumed_chain(root / "positive-control" / run_id(0), run_id(0), recipe) is None
    text = (SCRIPTS / "step2_grade.py").read_text()
    assert "chain=chain" in text


# --- merging a resumed shard --------------------------------------------------------------

def test_the_merge_accepts_a_resumed_shard_as_one_shard(tmp_path, monkeypatch, capsys):
    m = load("step2_merge")
    cs = cases()
    hub = tmp_path / "hub"
    parts = {"shard-a": cs[0::2], "resumed": cs[1::2]}
    ids = {"shard-a": run_id(5, "full-0of2"), "resumed": run_id(1)}
    assert paid_run(hub, ids["shard-a"], parts["shard-a"], Provider(cs))["complete"]
    stopped_first = paid_run(hub, run_id(0), parts["resumed"],
                             Provider(cs, fail={3: TRANSPORT}))
    assert not stopped_first["complete"]
    assert paid_run(hub, ids["resumed"], parts["resumed"], Provider(cs), ceiling=14,
                    resume=resume_of(hub, run_id(0), ids["resumed"]))["complete"]
    for name, run in ids.items():
        base = hub / "positive-control" / run
        chain = [link(hub, run_id(0)), link(hub, run)] if name == "resumed" else None
        graded(base / "paid", parts[name], base / "grade", chain)
    # The uninterrupted single run over all cases is what the merge must equal.
    whole = tmp_path / "whole"
    paid_run(whole, run_id(9), cs, Provider(cs))
    graded(whole / "positive-control" / run_id(9) / "paid", cs, tmp_path / "g-whole")

    fake = SimpleNamespace(
        HfApi=lambda token: SimpleNamespace(
            whoami=lambda: {"name": "owner"},
            repo_info=lambda *a, **k: SimpleNamespace(private=True, sha="a" * 40)),
        snapshot_download=lambda *a, **k: str(hub))
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)
    monkeypatch.setenv("HF_TOKEN", "token")
    monkeypatch.setenv("STEP2_MERGE_RUNS", "%s %s" % (ids["shard-a"], ids["resumed"]))
    monkeypatch.setenv("STEP2_RUN_ID", "full-merged-x-1")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    (tmp_path / "step2-bank").mkdir()
    (tmp_path / "step2-bank" / "train.jsonl").write_text("")
    monkeypatch.setitem(sys.modules, "step2_grade", SimpleNamespace(
        target_arms=lambda value: ("subset-spec",), token_counter=lambda: (None, None)))
    monkeypatch.setitem(sys.modules, "step2_shard", SimpleNamespace(
        FULL_CASES=len(cs), cases_from_env=lambda bank, environ: cs))
    monkeypatch.setattr(m, "git_recipe", recipe)
    assert m.main() == 0
    out = capsys.readouterr()
    assert PRIVATE not in out.out + out.err
    merged = tmp_path / "step2-grade"
    for name in ("rows.jsonl", "report.json", "public-report.json"):
        assert (merged / name).read_bytes() == (tmp_path / "g-whole" / name).read_bytes(), name
    lineage = json.loads((merged / "sft-report.json").read_text())["lineage"]["shards"]
    assert [s.get("resumed_from") for s in lineage] == [None, [run_id(0)]]

    # The stopped run itself is never a shard: it is incomplete.
    monkeypatch.setenv("STEP2_MERGE_RUNS", "%s %s" % (ids["shard-a"], run_id(0)))
    monkeypatch.setenv("STEP2_RUN_ID", "full-merged-x-2")
    assert m.main() == 1
    err = capsys.readouterr().err
    assert "generation is absent or incomplete" in err and PRIVATE not in err


# --- the log ------------------------------------------------------------------------------

def test_no_line_the_resume_prints_carries_a_case(stopped, tmp_path, monkeypatch, capsys):
    cs, root, _ = stopped
    script = load("step2_resume")
    temp = tmp_path / "runner"
    (temp / "step2-bank").mkdir(parents=True)
    (temp / "step2-bank" / "train.jsonl").write_text("")
    env = {"STEP2_RUN_ID": run_id(1), "STEP2_RUNG": "full", "STEP2_SHARD_INDEX": "1",
           "STEP2_SHARD_COUNT": "2", "STEP2_SKIP_PREFIX": "192", "STEP2_CASES": "1355",
           "STEP2_SAMPLES": "2", "STEP2_CEILING_USD": "14", "STEP2_MAX_SECONDS": "14400",
           "STEP2_RPM": "30", "STEP2_RESUME_RUN_ID": run_id(0), "RUNNER_TEMP": str(temp),
           "GITHUB_SHA": "a" * 40}
    monkeypatch.setattr(script, "fetcher", lambda environ: lambda runs: root)
    monkeypatch.setitem(sys.modules, "step2_merge", SimpleNamespace(git_recipe=recipe))
    monkeypatch.setattr(script, "cases_from_env", lambda rows, environ: cs, raising=False)
    import step2_shard
    monkeypatch.setattr(step2_shard, "cases_from_env", lambda rows, environ=None: cs)
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC)
    monkeypatch.setattr(script, "SPEC", spec)
    assert script.main(["download"], env) == 0
    out = capsys.readouterr()
    report = json.loads(out.out)
    assert report["remaining"] == 22 and report["re_requested"] == 1
    assert report["prior_completed"] == 10 and report["resumed_from"] == [run_id(0)]
    assert report["remaining_projection"]["rpm"] == 30
    assert PRIVATE not in out.out + out.err
    # The copy is read-only evidence under the runner, never under the prior's path.
    assert (temp / "step2-resume" / run_id(0) / "paid" / "result.json").is_file()

    # A mismatch and a library failure are refused with a field or a type, never a case.
    spec.write_text(SPEC + " edited")
    import shutil
    shutil.rmtree(temp / "step2-resume")
    assert script.main(["download"], env) == 1
    err = capsys.readouterr().err
    assert "differs from this run in spec_sha256" in err and PRIVATE not in err

    def private_failure(*a, **k):
        raise KeyError(PRIVATE + "0")
    monkeypatch.setattr(script, "account", private_failure)
    shutil.rmtree(temp / "step2-resume")
    assert script.main(["download"], env) == 1
    err = capsys.readouterr().err
    assert "KeyError" in err and PRIVATE not in err


def test_generation_reads_the_chain_the_download_step_copied(stopped, tmp_path, monkeypatch):
    cs, root, _ = stopped
    script = load("step2_generate")
    env = {"STEP2_RUN_ID": run_id(1), "STEP2_RUNG": "full", "STEP2_SHARD_INDEX": "1",
           "STEP2_SHARD_COUNT": "2", "STEP2_SKIP_PREFIX": "192", "STEP2_CASES": "1355",
           "RUNNER_TEMP": str(tmp_path)}
    assert script.resume_from_env(env) is None
    import shutil
    shutil.copytree(root / "positive-control" / run_id(0) / "paid",
                    tmp_path / "step2-resume" / run_id(0) / "paid")
    monkeypatch.setitem(sys.modules, "step2_merge", SimpleNamespace(git_recipe=recipe))
    resume = script.resume_from_env(dict(env, STEP2_RESUME_RUN_ID=run_id(0)))
    assert [link["run_id"] for link in resume["links"]] == [run_id(0)]
    assert resume["shard"] == SHARD and resume["run_id"] == run_id(1)
    with pytest.raises(res.ResumeError):
        script.resume_from_env(dict(env, STEP2_RESUME_RUN_ID=run_id(0, "full-0of2")))


def test_the_results_a_resume_prints_carry_no_case(stopped):
    cs, root, _ = stopped
    result = paid_run(root, run_id(1), cs, Provider(cs), ceiling=14,
                      resume=resume_of(root, run_id(0), run_id(1)))
    assert PRIVATE not in json.dumps(result)
    state = res.plan([link(root, run_id(0))], keys(cs), "14")
    assert PRIVATE not in json.dumps(res.summary(state))


# --- the full rung's rate and the shard gate ----------------------------------------------

def shard_env(**kw):
    base = {"STEP2_RUNG": "full", "STEP2_CASES": "1355", "STEP2_SAMPLES": "4",
            "STEP2_MAX_SECONDS": "14400", "STEP2_CEILING_USD": "14",
            "STEP2_SHARD_INDEX": "0", "STEP2_SHARD_COUNT": "2", "STEP2_SKIP_PREFIX": "192"}
    base.update(kw)
    return base


@pytest.mark.parametrize("rpm, ok", [("30", True), ("35", True), ("45", True), ("29", False),
                                     ("46", False), ("60", False), ("x", False)])
def test_the_full_rungs_rate_is_configurable_down_to_30(rpm, ok, capsys):
    shard = load("step2_shard")
    assert shard.main(shard_env(STEP2_RPM=rpm)) == (0 if ok else 1)
    out = capsys.readouterr()
    if ok:
        plan = json.loads(out.out)
        assert plan["rpm"] == int(rpm)
        # Sized by the measured rate, scaled down -- never faster than measured.
        assert plan["generation_minutes"] == pytest.approx(4656 / (43.8 * int(rpm) / 45), abs=.1)
    else:
        assert "STEP2_RPM" in out.err


def test_a_resume_is_sized_on_its_remainder_not_the_whole_shard(capsys):
    shard = load("step2_shard")
    # $12 cannot cover a fresh shard, but a resume's ceiling is checked on what remains.
    assert shard.main(shard_env(STEP2_CEILING_USD="12")) == 1
    capsys.readouterr()
    assert shard.main(shard_env(STEP2_CEILING_USD="12", STEP2_RESUME_RUN_ID=run_id(0, "full-0of2"))) == 0
    assert json.loads(capsys.readouterr().out)["resume"] is True
    # Grading still covers the whole shard.
    assert shard.main(shard_env(STEP2_SHARD_COUNT="1", STEP2_SHARD_INDEX="0",
                                STEP2_RESUME_RUN_ID=run_id(0, "full-0of1"))) == 1
    assert "grading needs" in capsys.readouterr().err
    remainder = shard.generation(714, max_seconds=14400, rpm=30)
    assert shard.refusals(remainder, 14 - 9.68351838, resume=False) == []
    assert shard.refusals(remainder, 1.0) and "ceiling" in shard.refusals(remainder, 1.0)[0]
