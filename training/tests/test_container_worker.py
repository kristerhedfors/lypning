"""The verifier Space stays live without exposing its execution protocol."""
from __future__ import annotations

from io import BytesIO

from pipeline.container_worker import HealthHandler


def test_health_endpoint_is_content_free_and_other_paths_are_closed():
    handler = object.__new__(HealthHandler)
    handler.path, handler.wfile = "/healthz", BytesIO()
    status, headers = [], []
    handler.send_response = status.append
    handler.send_header = lambda key, value: headers.append((key, value))
    handler.end_headers = lambda: None
    handler.do_GET()
    assert status == [200] and handler.wfile.getvalue() == b"ok\n"
    assert ("Cache-Control", "no-store") in headers

    handler.path = "/identity"
    errors = []
    handler.send_error = errors.append
    handler.do_GET()
    assert errors == [404], "execution identity must not be exposed over HTTP"
