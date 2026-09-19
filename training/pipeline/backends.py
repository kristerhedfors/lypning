"""One OpenAI-shaped door, so the same eval code measures every arm.

The baseline, every sweep candidate, and any adapter you serve later all go
through ``POST {base}/chat/completions``. That is not laziness about
abstractions — it is the only way the baseline number stays comparable. vLLM
serving the BF16 checkpoint, vLLM serving base+LoRA, and a hosted endpoint all
speak it, so switching arms is one environment variable and never a code path.

Stdlib ``urllib`` on purpose (see ``training/README.md``): this module runs on the
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

#: Who we say we are. An edge that sees `Python-urllib/3.x` may refuse the
#: request before the key is ever checked; see `complete` for what that cost.
USER_AGENT = "lypning/0.1 (+https://github.com/kristerhedfors/lypning)"


class BackendError(RuntimeError):
    """Ours or the server's — never the generated program's."""


def extra_body(top_k: Optional[int] = None, min_p: Optional[float] = None) -> Dict[str, Any]:
    """The sampling knobs the OpenAI schema lacks, as vLLM/SGLang read them.

    The SDK's ``extra_body`` merges its keys into the top level of the request
    body — there is no ``extra_body`` field on the wire, and a server handed one
    would ignore it without a word, which is a top-k of "whatever the server
    defaults to" recorded as 20. So this returns the top-level keys, and only
    the ones the sampling block carries: an absent knob is left to the server,
    never sent as a guess (`EVAL2.md` §5).
    """
    out: Dict[str, Any] = {}
    if top_k is not None:
        out["top_k"] = int(top_k)
    if min_p is not None:
        out["min_p"] = float(min_p)
    return out


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
        top_k: Optional[int] = None,
        min_p: Optional[float] = None,
        reasoning_effort: Optional[str] = None,
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
        payload.update(extra_body(top_k=top_k, min_p=min_p))
        if stop:
            payload["stop"] = stop
        if enable_thinking is not None:
            # Qwen's chat template reads this; vLLM/SGLang forward it.
            payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
        if reasoning_effort is not None:
            # The hosted form of the same switch. Cerebras serves Qwen3.8 with
            # reasoning ON by default and puts the thinking in a separate
            # channel, leaving `content` EMPTY: a call without this returns
            # successfully with nothing in it, which is how the first bank run
            # spent sixty calls for zero tasks. `harvesting/proxy.py` pins it
            # too. Not sent unless asked for, like every other knob here.
            payload["reasoning_effort"] = reasoning_effort
        body = json.dumps(payload).encode("utf-8")
        # Naming ourselves is not politeness, it is the difference between a
        # working key and an unexplainable one. Measured 2026-09-19 against
        # Cerebras: the same request, same key, same body, differs only in this
        # header and returns either `content` or HTTP 403 with Cloudflare error
        # 1010 -- "the owner has banned your client based on its signature",
        # which `urllib`'s default `Python-urllib/3.x` earns. A 403 from an edge
        # is indistinguishable from a rejected key at the call site, so this
        # cost four CI dispatches and a secret rotation before it was found.
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
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
