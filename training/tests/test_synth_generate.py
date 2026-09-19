"""The generator against a scripted backend: bounds, resume, dedup, and the contract."""
from __future__ import annotations

import io
import json
import random
import urllib.request

import pytest

from pipeline import synth_generate as sg
from pipeline.backends import BackendError, ChatBackend, Completion
from pipeline.cli import main as cli_main
from pipeline.synth import validate_candidate


def tasks_json(n, prefix="task"):
    rows = [{"task": "%s %d: read integers from stdin and print something specific about them." % (prefix, i),
             "inputs": [{"stdin": "1 2\n"}, {"stdin": "3\n"}, {"stdin": "\n"}, {"argv": ["x"]}]}
            for i in range(n)]
    return json.dumps(rows)


def done(text, *, finish="stop", tokens=10):
    return Completion(text=text, reasoning=None, prompt_tokens=5, completion_tokens=tokens,
                      latency_s=0.0, finish_reason=finish)


class Scripted:
    """Answers in order; a BackendError in the script is raised."""
    model = sg.MODEL

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages, **kw):
        self.calls.append((messages, kw))
        if not self.replies:
            raise AssertionError("script exhausted after %d calls" % len(self.calls))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def budget(**over):
    kw = dict(max_calls=1000, max_output_tokens=10 ** 9, max_seconds=10 ** 6)
    kw.update(over)
    return sg.Budget(**kw)


def test_a_task_call_then_k_solve_calls_write_one_candidate_per_task():
    backend = Scripted([done(tasks_json(2))] + [done("print(1)")] * 6)
    rows = []
    summary = sg.generate(backend, rows.append, budget=budget(max_calls=7), seen=set(),
                          samples=3, rng=random.Random(1), today="2026-09-18")
    assert summary["written_this_run"] == 2 and summary["tasks_total"] == 2
    assert summary["stopped_because"] == "calls"
    assert [validate_candidate(r) for r in rows] == [None, None]
    assert all(r["generated_on"] == "2026-09-18" and r["model"] == sg.MODEL for r in rows)
    assert all(r["stratum"] in ("rewrite", "ceiling") for r in rows)
    assert all(r["domain"].startswith(r["stratum"] + ":") for r in rows)
    # Every call carries the reasoning switch: without it `content` is empty.
    assert all(kw["reasoning_effort"] == "none" for _, kw in backend.calls)
    assert backend.calls[0][1]["temperature"] == 0.9 and backend.calls[1][1]["temperature"] == 0.7


def test_a_seen_task_is_never_paid_for_twice():
    backend = Scripted([done(tasks_json(2))] + [done("print(1)")] * 3)
    seen = {json.loads(tasks_json(2))[0]["task"]}
    rows = []
    summary = sg.generate(backend, rows.append, budget=budget(max_calls=4), seen=seen, samples=3,
                          rng=random.Random(1))
    assert summary["ledger"]["duplicate_task"] == 1 and len(rows) == 1
    assert rows[0]["task"] in seen and len(seen) == 2


def test_fewer_than_two_programs_is_not_a_candidate():
    backend = Scripted([done(tasks_json(1)), done("print(1)"), done("", finish="length"), done("   ")])
    rows = []
    summary = sg.generate(backend, rows.append, budget=budget(max_calls=4), seen=set(), samples=3,
                          rng=random.Random(1))
    assert rows == [] and summary["ledger"]["too_few_samples"] == 1
    assert summary["ledger"]["truncated"] == 1 and summary["ledger"]["empty_content"] == 1


def test_six_barren_task_calls_stop_the_run():
    backend = Scripted([done("no json here")] * 6)
    summary = sg.generate(backend, lambda r: None, budget=budget(), seen=set(), rng=random.Random(1))
    assert summary["ledger"]["stopped_barren"] == 1 and summary["ledger"]["calls"] == 6
    assert summary["stopped_because"] == "provider"


def test_a_rejected_key_stops_at_once_and_a_rate_limit_does_not():
    backend = Scripted([BackendError("HTTP 403: nope")])
    summary = sg.generate(backend, lambda r: None, budget=budget(), seen=set(), rng=random.Random(1))
    ledger = summary["ledger"]
    assert ledger["http_403"] == 1 and ledger["stopped_rejected"] == 1 and ledger["task_call_failed"] == 1
    assert len(backend.calls) == 1
    backend = Scripted([BackendError("giving up after 2 retries: HTTP 429: slow"), done(tasks_json(1)),
                        done("print(1)"), done("print(2)")])
    rows = []
    summary = sg.generate(backend, rows.append, budget=budget(max_calls=3), seen=set(), samples=2,
                          rng=random.Random(1))
    assert summary["ledger"]["http_429"] == 1 and len(rows) == 1


def test_token_and_time_budgets_end_the_run_by_name():
    backend = Scripted([done(tasks_json(1), tokens=50), done("print(1)", tokens=50), done("print(2)", tokens=50)])
    summary = sg.generate(backend, lambda r: None, budget=budget(max_output_tokens=120), seen=set(),
                          samples=3, rng=random.Random(1))
    assert summary["stopped_because"] == "tokens"
    clock = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    b = sg.Budget(max_calls=10, max_output_tokens=10 ** 9, max_seconds=50, clock=lambda: next(clock))
    summary = sg.generate(Scripted([done(tasks_json(1))]), lambda r: None, budget=b, seen=set(),
                          rng=random.Random(1))
    assert summary["stopped_because"] == "time"


def test_every_task_call_is_charged_to_the_stratum_it_was_drawn_for():
    backend = Scripted([done("[]")] * 6)
    summary = sg.generate(backend, lambda r: None, budget=budget(), seen=set(), rng=random.Random(7))
    asked = {k: v for k, v in summary["ledger"].items() if k.startswith("asked_")}
    assert sum(asked.values()) == 6 and set(asked) <= {"asked_rewrite", "asked_ceiling"}
    # The draw itself is the preregistered 66:27, decided before any spend.
    rng = random.Random(3)
    rewrite = sum(rng.random() < sg.REWRITE_FRACTION for _ in range(10000))
    assert abs(rewrite / 10000 - 66 / 93) < 0.02


def test_parse_tasks_keeps_only_usable_shapes():
    good = {"task": "x" * 40, "inputs": [{"stdin": "a"}, {"argv": ["b"]}, {"files": {"f": "c"}}]}
    assert sg.parse_tasks("```json\n" + json.dumps([good]) + "\n```") == [good]
    assert sg.parse_tasks("prose " + json.dumps([good]) + " trailing") == [good]
    assert sg.parse_tasks(json.dumps([{"task": "short", "inputs": good["inputs"]}])) == []
    assert sg.parse_tasks(json.dumps([{"task": "x" * 40, "inputs": good["inputs"][:2]}])) == []
    assert sg.parse_tasks(json.dumps([{"task": "x" * 40, "inputs": [{"stdin": 1}, {"argv": [1]}, {"files": {"a": 1}}, 3]}])) == []
    assert sg.parse_tasks(json.dumps({"task": "x"})) == []
    assert sg.parse_tasks("[not json") == []
    assert sg.strip_fence("```python\nprint(1)\n```") == "print(1)"


def test_backend_from_env_strips_the_key_and_pins_the_model():
    assert sg.backend_from_env({}) is None
    assert sg.backend_from_env({"CEREBRAS_API_KEY": "  \n"}) is None
    backend = sg.backend_from_env({"CEREBRAS_API_KEY": "csk-abc\n"})
    assert isinstance(backend, ChatBackend)
    assert backend.api_key == "csk-abc" and backend.model == sg.MODEL
    assert backend.base_url == sg.CEREBRAS_BASE_URL


def test_the_door_sends_reasoning_effort_only_when_asked(monkeypatch):
    sent = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return Resp(json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    backend = ChatBackend(base_url="http://x/v1", model="m", api_key="k")
    backend.complete([{"role": "user", "content": "hi"}])
    backend.complete([{"role": "user", "content": "hi"}], reasoning_effort="none")
    assert "reasoning_effort" not in sent[0] and sent[1]["reasoning_effort"] == "none"


def test_seen_tasks_tolerates_rows_that_are_not_ours():
    assert sg.seen_tasks([{"task": "a"}, {"row": {}}, "x", {"task": 3}]) == {"a"}


def test_synth_generate_resumes_and_excludes(tmp_path, monkeypatch, capsys):
    out = tmp_path / "c.jsonl"
    prior = {"task": json.loads(tasks_json(2))[0]["task"], "inputs": [], "programs": []}
    out.write_text(json.dumps(prior) + "\n")
    banked = tmp_path / "banked.jsonl"
    banked.write_text(json.dumps({"task": "elsewhere"}) + "\n")
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-test\n")
    backend = Scripted([done(tasks_json(2))] + [done("print(1)")] * 3)
    monkeypatch.setattr(sg, "backend_from_env", lambda **kw: backend)
    assert cli_main(["synth-generate", "--output", str(out), "--exclude-tasks", str(banked),
                     "--max-calls", "4", "--samples", "3"]) == 0
    text = capsys.readouterr().out
    assert "resuming with 1 task(s) in this file, 1 already banked elsewhere" in text
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(rows) == 2 and rows[1]["task"] == json.loads(tasks_json(2))[1]["task"]
    summary = json.loads(text[text.index("{"):])
    assert summary["written_this_run"] == 1 and summary["ledger"]["duplicate_task"] == 1
    assert cli_main(["synth-generate", "--output", str(out), "--exclude-tasks", str(tmp_path / "nope")]) == 2
