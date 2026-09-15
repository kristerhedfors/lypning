"""Check the exact account-visible model without printing a key or error body."""
from __future__ import annotations

import json
import os
import urllib.request

from .worker import MODEL
from .proxy import _NoRedirect


def main():
    secret = os.environ.get("CEREBRAS_API_KEY")
    if not secret:
        raise SystemExit("CEREBRAS_API_KEY is not configured")
    request = urllib.request.Request("https://api.cerebras.ai/v1/models",
                                     headers={"Authorization": "Bearer " + secret})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=30) as response:
            data = json.loads(response.read(1024 * 1024))
        if MODEL not in {entry.get("id") for entry in data.get("data", [])}:
            raise SystemExit("Requested qwen-3.8-27b unavailable; no model substitution allowed")
    except Exception:
        raise SystemExit("Cerebras model preflight failed; inspect provider account status (no credentials logged)")
    print("Account exposes qwen-3.8-27b; no generation tokens consumed by preflight")


if __name__ == "__main__":
    main()
