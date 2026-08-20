"""The commit barrier, and the one tier that does not have it.

Routing onward is only sound if a tier that refuses left nothing behind.
lypning's Rust core stages stdout and discards it on exit 90, so a refusal is
observably a no-op. lypning-mp is MicroPython and streams, so a program that
prints before reaching an unsupported construct has already committed those
bytes when it refuses.

Both halves are pinned here. The first is a guarantee and must not regress. The
second is a KNOWN DEFECT, and it is asserted rather than skipped so that the day
someone gives lypning-mp a barrier, this test fails and says so — a defect that
quietly heals is a defect nobody updates the documentation for.

See docs/LYPNING.md §6.
"""

from __future__ import annotations

from lypning import UNSUPPORTED_EXIT, engines

# Prints, then refuses. The print must be reached before the refusal for either
# assertion below to mean anything.
PRINT_THEN_REFUSE_RUST = 'print("BEFORE")\nimport subprocess\n'
PRINT_THEN_REFUSE_MP = (
    'print("BEFORE")\n'
    "import unicodedata as u\n"
    "print(u.decomposition(chr(0xC0)))\n"
)

# The `lypning_bin` / `micropython_bin` fixtures rather than a module-level
# `skipif`: a mark is evaluated at IMPORT time, before the autouse fixture in
# conftest has moved `$LYPNING_HOME`, so in a wheel install — where the binary
# lives only under that dir — the mark saw a built engine and the test then ran
# against an unbuilt one and failed on exit 127. The fixtures resolve at call
# time, which is the only time the answer is true.


def test_rust_core_refuses_with_stdout_untouched(lypning_bin) -> None:
    r = engines.run(engines.LYPNING, PRINT_THEN_REFUSE_RUST)
    assert r.returncode == UNSUPPORTED_EXIT
    assert r.stdout == "", "the commit barrier let output escape before a refusal"
    assert r.stderr.startswith("lypning: unsupported: ")
    assert r.stderr.count("\n") == 1, "a refusal is exactly one line on stderr"


def test_micropython_tier_has_no_barrier(micropython_bin) -> None:
    """KNOWN DEFECT. If this fails, lypning-mp gained a barrier — update
    docs/LYPNING.md §6, the README, and conformance's `contract` verdict."""
    r = engines.run(engines.MICROPYTHON, PRINT_THEN_REFUSE_MP)
    assert r.returncode == UNSUPPORTED_EXIT
    assert r.stdout == "BEFORE\n", (
        "lypning-mp no longer leaks stdout before refusing — this is good news, "
        "but the documented defect and the conformance contract check are now stale"
    )


def test_dispatcher_contains_the_leak(micropython_bin) -> None:
    """The leak is invisible through `lypning run`: each tier's stdout is
    captured in the parent and dropped on exit 90, so the caller sees exactly
    one tier's output. This is what keeps the mixture arm clean, and it is a
    weaker guarantee than the engine holding the barrier itself — it holds only
    while the dispatcher is the one running the program."""
    d = engines.dispatch(PRINT_THEN_REFUSE_MP)
    assert d.result.returncode == 0
    assert d.result.stdout.count("BEFORE") == 1, "a refused tier's output was replayed"
    assert all(a.unsupported for a in d.attempts)
