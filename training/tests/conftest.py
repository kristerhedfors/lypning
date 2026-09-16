from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pytest_addoption(parser):
    parser.addoption("--no-memory-limit", action="store_true",
                     help="reviewed local fixtures only: disable memory guards when the host sandbox denies ps")


@pytest.fixture(autouse=True)
def explicit_local_memory_optout(request, monkeypatch):
    if request.config.getoption("--no-memory-limit"):
        if sys.platform != "darwin":
            raise pytest.UsageError("--no-memory-limit is only for restricted macOS test hosts")
        from pipeline import sandbox
        monkeypatch.setitem(sandbox.run_python.__kwdefaults__, "mem_mb", 0)
        monkeypatch.setattr(sandbox, "memory_policy", lambda _: "none")
