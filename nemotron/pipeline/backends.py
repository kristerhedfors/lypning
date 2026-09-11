"""One OpenAI-shaped door, so the same eval code measures every arm.

The baseline, every sweep candidate, and any adapter you serve later all go
through ``POST {base}/chat/completions``. That is not laziness about
abstractions — it is the only way the baseline number stays comparable. vLLM
serving the BF16 checkpoint, vLLM serving base+LoRA, and a hosted endpoint all
speak it, so switching arms is one environment variable and never a code path.

Stdlib ``urllib`` on purpose (see ``nemotron/README.md``): this module runs on the
eval box, in CI, and inside the GPU container, and a pinned HTTP client is one
more thing that can differ between them.

RETRIES. 429 and 5xx are retried with exponential backoff and jitter; 4xx other
than 429 is returned immediately, because retrying a malformed request just
spends the budget slower. A request that exhausts its retries raises, and the
eval records the attempt as a harness error — never as a failed program.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_TIMEOUT_S = 600.0


class BackendError(RuntimeError):
    """Ours or the server's — never the generated program's."""


@dataclass
class Completion:
    text: str
    reasoning: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    finish_reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatBackend:
    base_url: str
    model: str
    api_key: Optional[str] = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_retries: int = 5
    # Per 1M tokens. Zero when you are renting the GPU by the hour instead —
    # the sweep's dollar column then comes from wall time, not from these.
    price_in: float = 0.0
    price_out: float = 0.0

    @classmethod
    def from_env(cls, **over: Any) -> "ChatBackend":
        base = over.pop("base_url", None) or os.environ.get("NTX_BASE_URL")
        model = over.pop("model", None) or os.environ.get("NTX_MODEL")
        if not base or not model:
            raise BackendError(
                "set NTX_BASE_URL and NTX_MODEL (or pass --base-url/--model).\n"
                "  local vLLM : NTX_BASE_URL=http://127.0.0.1:8000/v1\n"
                "  HF router  : NTX_BASE_URL=https://router.huggingface.co/v1"
            )
        key = over.pop("api_key", None) or os.environ.get("NTX_API_KEY") or os.environ.get("HF_TOKEN")
        return cls(
            base_url=base.rstrip("/"), model=model, api_key=key,
            price_in=float(os.environ.get("NTX_PRICE_IN", over.pop("price_in", 0.0)) or 0.0),
            price_out=float(os.environ.get("NTX_PRICE_OUT", over.pop("price_out", 0.0)) or 0.0),
            **over,
        )

    def identity(self) -> Dict[str, Any]:
        """What must be recorded next to every number this backend produced."""
        return {"base_url": self.base_url, "model": self.model}

    def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 1.0,
        top_p: float = 0.95,
        max_tokens: int = 4096,
        seed: Optional[int] = None,
        enable_thinking: Optional[bool] = None,
        stop: Optional[List[str]] = None,
    ) -> Completion:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }
        if seed is not None:
            payload["seed"] = seed
        if stop:
            payload["stop"] = stop
        if enable_thinking is not None:
            # Nemotron's chat template reads this; vLLM/SGLang forward it.
            payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key

        started = time.time()
        last: Optional[str] = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(
                self.base_url + "/chat/completions", data=body, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return self._to_completion(data, time.time() - started)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                last = "HTTP %s: %s" % (exc.code, detail)
                if exc.code != 429 and exc.code < 500:
                    raise BackendError(last) from None
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last = "%s: %s" % (type(exc).__name__, exc)
            if attempt < self.max_retries:
                time.sleep(min(60.0, 2.0 ** attempt) * (0.5 + random.random()))
        raise BackendError("giving up after %d retries: %s" % (self.max_retries, last))

    @staticmethod
    def _to_completion(data: Dict[str, Any], latency: float) -> Completion:
        try:
            choice = data["choices"][0]
            msg = choice.get("message") or {}
        except (KeyError, IndexError):
            raise BackendError("no choices in response: %s" % json.dumps(data)[:300]) from None
        usage = data.get("usage") or {}
        return Completion(
            text=msg.get("content") or "",
            reasoning=msg.get("reasoning_content"),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_s=latency,
            finish_reason=choice.get("finish_reason") or "",
        )

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.price_in + completion_tokens * self.price_out) / 1e6

    def probe(self) -> Dict[str, Any]:
        """Cheapest possible round trip. Run before a long eval, not after."""
        c = self.complete([{"role": "user", "content": "Reply with: ok"}], max_tokens=8,
                          temperature=0.0, enable_thinking=False)
        return {"ok": True, "latency_s": round(c.latency_s, 2),
                "reply": c.text.strip()[:40], "model": self.model}
