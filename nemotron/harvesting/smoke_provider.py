"""No-network, no-key fixture provider for real OpenCode container CI."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        tool_used = any(m.get("role") == "tool" for m in data.get("messages", []))
        message = {"role": "assistant", "content": "Created the fixture."}
        reason = "stop"
        if not tool_used:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_fixture", "type": "function", "function": {"name": "bash",
                "arguments": json.dumps({"command": "python3 -c \"from pathlib import Path; Path('main.py').write_text('print(42)\\n'); print(42)\"",
                                         "description": "Create deterministic smoke fixture"})}}]}
            reason = "tool_calls"
        response = {"id": "chatcmpl-fixture", "object": "chat.completion", "created": 1,
                    "model": "qwen-3.8-27b", "choices": [{"index": 0, "message": message,
                    "finish_reason": reason}], "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
        if data.get("stream"):
            delta = dict(message)
            if "tool_calls" in delta:
                delta["tool_calls"][0]["index"] = 0
            chunk = dict(response, object="chat.completion.chunk", choices=[{
                "index": 0, "delta": delta, "finish_reason": None}])
            final = dict(response, object="chat.completion.chunk", choices=[{
                "index": 0, "delta": {}, "finish_reason": reason}])
            body = ("data: " + json.dumps(chunk) + "\n\ndata: " + json.dumps(final) + "\n\ndata: [DONE]\n\n").encode()
            content_type = "text/event-stream"
        else:
            body, content_type = json.dumps(response).encode(), "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
