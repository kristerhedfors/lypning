"""Get the program out of the reply, or say honestly that there wasn't one.

Nemotron 3.5 Lightning is a reasoning model with thinking ON by default, so a
raw completion is reasoning followed by an answer. Two ways that arrives: a
server started with ``--reasoning-parser nemotron_v3`` splits it into
``reasoning_content`` and ``content`` for us; anything else hands over one string
with the thinking still inline. Both are handled, and the extractor is the same
either way — which matters, because a baseline measured through one server and a
candidate measured through the other must not differ because of the parser.

``no-code`` is a real outcome, not an error. A reply with no extractable program
scores zero like any other failure and is labelled so, because "the model
rambled" and "the model wrote a bug" need different fixes.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)
_DANGLING_OPEN = re.compile(r"<think>.*\Z", re.S | re.I)
_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)(?:```|\Z)", re.S)
_CODEY = re.compile(
    r"^\s*(?:import |from |def |class |print\(|if |for |while |with |try:|#!|__|[A-Za-z_]\w*\s*=)",
    re.M,
)


def strip_reasoning(text: str) -> str:
    """Remove ``<think>`` spans, including one left unclosed by a length cap."""
    out = _THINK_BLOCK.sub("", text or "")
    # An unterminated <think> means the model was cut off mid-reasoning: there is
    # no answer after it, so dropping the tail loses nothing and keeps a stray
    # fence inside the reasoning from being mistaken for the program.
    if "</think>" not in out:
        out = _DANGLING_OPEN.sub("", out)
    else:
        out = out.split("</think>")[-1]
    return out.strip()


def extract_program(content: str, reasoning: Optional[str] = None) -> Tuple[Optional[str], str]:
    """Return (program, how). ``program`` is None when nothing was extractable.

    ``how`` is one of ``fenced-python``, ``fenced-bare``, ``bare-text``, ``none``
    and is recorded on every attempt: a run whose pass rate moved because the
    model stopped using code fences is a formatting change, not a capability one.
    """
    text = content if reasoning is not None else strip_reasoning(content or "")
    text = (text or "").strip()
    if not text:
        return None, "none"

    blocks = [(lang.lower(), body) for lang, body in _FENCE.findall(text)]
    python = [b for lang, b in blocks if lang in ("python", "py", "python3")]
    if python:
        return python[-1].strip("\n"), "fenced-python"
    bare = [b for lang, b in blocks if lang == ""]
    if bare:
        return bare[-1].strip("\n"), "fenced-bare"
    if blocks:                      # fenced, but in some other language
        return None, "none"
    if _CODEY.search(text):
        return text, "bare-text"
    return None, "none"


def compiles(program: str) -> Optional[str]:
    """None if it parses, else the SyntaxError message. Never executes anything."""
    try:
        compile(program, "<generated>", "exec")
        return None
    except SyntaxError as exc:
        return "%s: line %s" % (exc.msg, exc.lineno)
    except ValueError as exc:       # e.g. embedded NUL
        return str(exc)
