"""Check the exact account-visible model without printing a key or error body."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .worker import MODEL
from .proxy import _NoRedirect


def main():
    secret = os.environ.get("CEREBRAS_API_KEY", "").strip()
    if not secret:
        raise SystemExit("CEREBRAS_API_KEY is not configured")
    request = urllib.request.Request("https://api.cerebras.ai/v1/models",
                                     headers={"Authorization": "Bearer " + secret,
                                              "User-Agent": "lypning-harvest/1"})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=30) as response:
            data = json.loads(response.read(1024 * 1024))
        if MODEL not in {entry.get("id") for entry in data.get("data", [])}:
            raise SystemExit("Requested qwen-3.8-27b unavailable; no model substitution allowed")
    except urllib.error.HTTPError as exc:
        # Status and coarse response type help distinguish account/API errors
        # from an HTML edge-policy response, without revealing bodies or headers.
        kind = "JSON" if exc.headers.get("Content-Type", "").startswith("application/json") else "non-JSON"
        raise SystemExit("Cerebras model preflight failed: HTTP " + str(exc.code) + " (" + kind +
                         "). Check the Actions secret and account access; no credentials or response body logged.")
    except Exception as exc:
        raise SystemExit("Cerebras model preflight failed: " + type(exc).__name__ +
                         "; no credentials or exception text logged.")
    print("Account exposes qwen-3.8-27b; no generation tokens consumed by preflight")


if __name__ == "__main__":
    main()
