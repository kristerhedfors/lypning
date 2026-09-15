from __future__ import annotations

import concurrent.futures
import http.client
import json
import stat
import threading
from contextlib import contextmanager

import pytest

from harvesting.proxy import MAX_BODY_BYTES, ProxyConfig, ProxyServer, Rejected


KEY = "private-provider-secret"


@contextmanager
def running(tmp_path, **kwargs):
    config = ProxyConfig("qwen-exact", kwargs.pop("max_requests", 3),
                         kwargs.pop("max_output_tokens", 100),
                         kwargs.pop("total_output_tokens", 300), tmp_path / "proxy.jsonl")
    server = ProxyServer(("127.0.0.1", 0), config, KEY)
    seen = []

    def upstream(body):
        seen.append(body)
        return {"id": "chat-test", "model": body["model"], "created": 1,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": KEY,
                    "tool_calls": [{"id": "tool1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]},
                             "finish_reason": "tool_calls"}], "usage": {"completion_tokens": 10}}

    server.upstream = upstream
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, seen
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def call(server, body=None, method="POST", path="/v1/chat/completions", headers=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    if body is None:
        body = {"model": "qwen-exact", "messages": [{"role": "user", "content": "build a task"}]}
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    connection.request(method, path, data, headers or {"Content-Type": "application/json"})
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def test_clamps_tokens_and_removes_secrets(tmp_path):
    with running(tmp_path) as (server, seen):
        status, raw = call(server, {"model": "qwen-exact", "messages": [{"role": "user", "content": KEY}],
                                    "max_completion_tokens": 500})
        assert status == 200
        assert KEY.encode() not in raw
        assert seen[0]["max_tokens"] == 100
        assert seen[0]["stream"] is False
        assert "max_completion_tokens" not in seen[0]
    ledger = tmp_path / "proxy.jsonl"
    assert KEY not in ledger.read_text()
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600
    events = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [event["kind"] for event in events] == ["request", "response"]
    assert all(event["correctness"] == "unknown" and event["trainable"] is False for event in events)


def test_streaming_is_openai_sse_including_tools_and_usage(tmp_path):
    with running(tmp_path) as (server, seen):
        status, raw = call(server, {"model": "qwen-exact", "messages": [{}], "stream": True,
                                    "stream_options": {"include_usage": True}})
        assert status == 200
        frames = raw.decode().strip().split("\n\n")
        assert frames[-1] == "data: [DONE]"
        chunks = [json.loads(frame.removeprefix("data: ")) for frame in frames[:-1]]
        assert chunks[0]["choices"][0]["delta"]["tool_calls"][0]["index"] == 0
        assert chunks[1]["choices"][0]["finish_reason"] == "tool_calls"
        assert chunks[2]["usage"]["completion_tokens"] == 10
        assert seen[0]["stream"] is False
        assert "stream_options" not in seen[0]


@pytest.mark.parametrize("body", [
    {"model": "different", "messages": [{}]},
    {"model": "qwen-exact", "messages": [], "max_tokens": 10},
    {"model": "qwen-exact", "messages": [{}], "max_tokens": True},
    {"model": "qwen-exact", "messages": [{}], "max_tokens": -1},
    {"model": "qwen-exact", "messages": [{}], "stream": "true"},
    {"model": "qwen-exact", "messages": [{}], "base_url": "https://evil.invalid"},
    {"model": "qwen-exact", "messages": [{}], "max_tokens": 1, "max_completion_tokens": 2},
    b'{"model":"qwen-exact","model":"qwen-exact","messages":[{}]}',
    b'{"model":"qwen-exact","messages":[{}],"temperature":NaN}',
])
def test_rejects_invalid_requests_without_upstream(tmp_path, body):
    with running(tmp_path) as (server, seen):
        assert call(server, body)[0] == 400
        assert not seen
        assert server.requests == 0


def test_deny_endpoint_and_oversize(tmp_path):
    with running(tmp_path) as (server, seen):
        assert call(server, path="/v1/models")[0] == 404
        assert call(server, method="GET", path="/health")[0] == 200
        assert call(server, method="GET")[0] == 404
        assert call(server, headers={"Content-Length": str(MAX_BODY_BYTES + 1)})[0] == 413
        assert not seen


def test_failed_calls_consume_budget_and_do_not_leak_errors(tmp_path):
    with running(tmp_path, max_requests=1) as (server, seen):
        def failure(_):
            raise RuntimeError(KEY)
        server.upstream = failure
        status, raw = call(server)
        assert status == 502 and KEY.encode() not in raw
        assert server.requests == 1 and server.reserved_output_tokens == 100
        assert call(server)[0] == 429
    assert KEY not in (tmp_path / "proxy.jsonl").read_text()
    assert json.loads((tmp_path / "proxy.jsonl").read_text().splitlines()[-1])["status"] == "failed"


def test_concurrent_reservations_are_atomic(tmp_path):
    with running(tmp_path, max_requests=100, total_output_tokens=250) as (server, seen):
        def reserve(_):
            try:
                return server.prepare({"model": "qwen-exact", "messages": [{}], "max_tokens": 100})[0]
            except Rejected:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(reserve, range(100)))
        assert sorted(value for value in results if value is not None) == [1, 2]
        assert server.requests == 2
        assert server.reserved_output_tokens == 200


def test_public_ledger_rejected(tmp_path):
    ledger = tmp_path / "public.jsonl"
    ledger.touch(mode=0o644)
    ledger.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        ProxyServer(("127.0.0.1", 0), ProxyConfig("qwen", 1, 1, 1, ledger), KEY)
