"""Secret-holding, fixed-upstream gateway for disposable OpenCode harvest jobs.

The agent receives neither the provider key nor arbitrary outbound HTTP access.
Every admitted call reserves its entire output allowance, even on upstream
failure; concurrent calls cannot overspend either configured call/token bound.
Logs are private observations, never correctness labels or training admission.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


UPSTREAM = "https://api.cerebras.ai/v1/chat/completions"
MAX_BODY_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
ALLOWED_FIELDS = frozenset({
    "model", "messages", "temperature", "top_p", "seed", "stop", "tools",
    "tool_choice", "parallel_tool_calls", "response_format", "reasoning_effort",
    "presence_penalty", "frequency_penalty", "max_tokens", "max_completion_tokens",
    "stream", "stream_options", "user",
})


@dataclass(frozen=True)
class ProxyConfig:
    model: str
    max_requests: int
    max_output_tokens: int
    total_output_tokens: int
    ledger: Path
    upstream_timeout: float = 120.0

    def __post_init__(self) -> None:
        if not self.model or any(
            type(value) is not int or value <= 0
            for value in (self.max_requests, self.max_output_tokens, self.total_output_tokens)
        ):
            raise ValueError("an exact model and positive integer budgets are required")
        if not 0 < self.upstream_timeout <= 300:
            raise ValueError("upstream timeout must be in (0, 300]")


class Rejected(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code


def _unique_object(pairs: Any) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        # Never forward the Authorization header to a provider redirect target.
        return None


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address: tuple, config: ProxyConfig, api_key: str) -> None:
        if not api_key or "\n" in api_key or "\r" in api_key:
            raise ValueError("a provider key is required")
        self.config = config
        self._api_key = api_key
        self._lock = threading.Lock()
        self.requests = 0
        self.reserved_output_tokens = 0
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        self._ledger_fd = os.open(str(config.ledger), flags, 0o600)
        info = os.fstat(self._ledger_fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            os.close(self._ledger_fd)
            raise ValueError("ledger must be a private regular file")
        try:
            super().__init__(address, _Handler)
        except BaseException:
            os.close(self._ledger_fd)
            raise

    def server_close(self) -> None:
        super().server_close()
        with self._lock:
            if self._ledger_fd is not None:
                os.close(self._ledger_fd)
                self._ledger_fd = None

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(self._api_key, "[REDACTED_PROVIDER_KEY]")
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, dict):
            return {self.redact(key): self.redact(item) for key, item in value.items()}
        return value

    def record(self, event: dict) -> None:
        raw = (json.dumps(self.redact(event), ensure_ascii=True, allow_nan=False) + "\n").encode()
        with self._lock:
            if self._ledger_fd is None:
                raise RuntimeError("ledger is closed")
            remaining = memoryview(raw)
            while remaining:
                written = os.write(self._ledger_fd, remaining)
                remaining = remaining[written:]

    def prepare(self, body: Any) -> tuple:
        if not isinstance(body, dict) or set(body) - ALLOWED_FIELDS:
            raise Rejected(400, "invalid_request_fields")
        if body.get("model") != self.config.model:
            raise Rejected(400, "model_not_allowed")
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            raise Rejected(400, "messages_required")
        if "stream" in body and type(body["stream"]) is not bool:
            raise Rejected(400, "invalid_stream")
        if "n" in body or ("max_tokens" in body and "max_completion_tokens" in body):
            raise Rejected(400, "invalid_token_limit")
        requested = body.get("max_completion_tokens", body.get("max_tokens", self.config.max_output_tokens))
        if type(requested) is not int or requested <= 0:
            raise Rejected(400, "invalid_token_limit")
        tokens = min(requested, self.config.max_output_tokens)
        with self._lock:
            if self.requests >= self.config.max_requests:
                raise Rejected(429, "request_budget_exhausted")
            if self.reserved_output_tokens + tokens > self.config.total_output_tokens:
                raise Rejected(429, "output_budget_exhausted")
            self.requests += 1
            self.reserved_output_tokens += tokens
            call_id = self.requests
        outgoing = dict(body)
        outgoing.pop("max_completion_tokens", None)
        outgoing.pop("stream_options", None)
        outgoing["max_tokens"] = tokens
        outgoing["stream"] = False
        return call_id, outgoing

    def upstream(self, body: dict) -> dict:
        """Only this trusted method sees provider credentials; tests replace it."""
        request = urllib.request.Request(
            UPSTREAM, json.dumps(body, allow_nan=False).encode(), method="POST",
            headers={"Authorization": "Bearer " + self._api_key,
                     "Content-Type": "application/json", "Accept": "application/json"},
        )
        # Ignore environment proxy settings: they could receive the real key.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        deadline = time.monotonic() + self.config.upstream_timeout
        with opener.open(request, timeout=self.config.upstream_timeout) as response:
            chunks = []
            length = 0
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError("upstream deadline")
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
                if length > MAX_RESPONSE_BYTES:
                    raise ValueError("upstream response too large")
        result = _decode(b"".join(chunks))
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list):
            raise ValueError("invalid upstream response")
        return result


def _sse(response: dict) -> bytes:
    base = {key: response[key] for key in ("id", "created", "model", "system_fingerprint") if key in response}
    base["object"] = "chat.completion.chunk"
    chunks = []
    for choice in response["choices"]:
        delta = dict(choice.get("message", {}))
        if isinstance(delta.get("tool_calls"), list):
            delta["tool_calls"] = [dict(tool, index=index) for index, tool in enumerate(delta["tool_calls"])]
        index = choice.get("index", 0)
        chunks.append(dict(base, choices=[{"index": index, "delta": delta, "finish_reason": None}]))
        chunks.append(dict(base, choices=[{"index": index, "delta": {}, "finish_reason": choice.get("finish_reason")}]))
    if "usage" in response:
        chunks.append(dict(base, choices=[], usage=response["usage"]))
    return b"".join(b"data: " + json.dumps(chunk, ensure_ascii=True).encode() + b"\n\n"
                    for chunk in chunks) + b"data: [DONE]\n\n"


class _Handler(BaseHTTPRequestHandler):
    server: ProxyServer
    protocol_version = "HTTP/1.0"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _reply(self, status: int, payload: Any, streaming: bool = False) -> None:
        data = _sse(payload) if streaming else json.dumps(payload, ensure_ascii=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._reply(200, {"status": "ok"}) if self.path == "/health" else self._reply(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._reply(404, {"error": "not_found"})
            return
        call_id = None
        outgoing = None
        started = time.monotonic()
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                raise Rejected(400, "content_length_required")
            try:
                length = int(lengths[0])
            except ValueError:
                raise Rejected(400, "invalid_content_length")
            if not 0 < length <= MAX_BODY_BYTES:
                raise Rejected(413, "request_too_large")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise Rejected(400, "incomplete_request")
            try:
                body = _decode(raw)
            except (ValueError, UnicodeError, RecursionError):
                raise Rejected(400, "invalid_json")
            call_id, outgoing = self.server.prepare(body)
            # Fail closed if evidence cannot be recorded before spending tokens.
            self.server.record({"kind": "request", "call_id": call_id, "time_ns": time.time_ns(),
                                "request": outgoing, "requested_stream": body.get("stream", False),
                                "correctness": "unknown", "trainable": False})
            response = self.server.redact(self.server.upstream(outgoing))
            self.server.record({"kind": "response", "call_id": call_id, "status": "completed",
                                "elapsed_seconds": time.monotonic() - started, "response": response,
                                "correctness": "unknown", "trainable": False})
            self._reply(200, response, streaming=body.get("stream", False))
        except Rejected as exc:
            self._reply(exc.status, {"error": {"code": exc.code}})
        except Exception:
            # Exception messages and provider error bodies may contain secrets.
            if call_id is not None:
                try:
                    self.server.record({"kind": "response", "call_id": call_id, "status": "failed",
                                        "elapsed_seconds": time.monotonic() - started,
                                        "correctness": "unknown", "trainable": False})
                except Exception:
                    pass
            try:
                self._reply(502, {"error": {"code": "upstream_or_evidence_failed"}})
            except (OSError, TimeoutError):
                pass


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-requests", type=int, required=True)
    parser.add_argument("--max-output-tokens", type=int, required=True)
    parser.add_argument("--total-output-tokens", type=int, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args(argv)
    config = ProxyConfig(args.model, args.max_requests, args.max_output_tokens,
                         args.total_output_tokens, args.ledger)
    key = os.environ.pop("CEREBRAS_API_KEY", "")
    try:
        server = ProxyServer((args.host, args.port), config, key)
    except (OSError, ValueError):
        parser.error("cannot start proxy: check private ledger, key, and listen address")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
