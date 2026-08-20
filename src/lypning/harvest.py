"""Turn captured invocations into committed evidence.

The invariant this module exists to hold: **an export is a pure function of its
inputs.** Running it twice over the same log produces a byte-identical file and
does not touch the one on disk. That is what makes it safe to fire on every Stop
— every turn boundary — and it is why counts are *derived* from stable sighting
keys rather than incremented: an increment would drift a little further from the
truth on each run, and a hook that rewrites a file every turn is a hook that
shows up in `git status` forever.

WHAT IT WRITES, AND WHAT IT REFUSES TO. The export publishes
``tests/corpus/sightings/<session>.jsonl`` and nothing else. It does **not**
commit — a hook that made commits would fight the session's own git work — and
it does **not** write ``corpus.jsonl``. That second refusal is the expensive
lesson. The Stop hook originally folded the log straight into the corpus, and
the data was still lost: one shared file that every session rewrites conflicts
across branches by construction, and merging it was never worth it to a session
whose PR was about something else. Measured over the 19 branches cut since the
corpus landed, 2 carried any growth and neither reached main — 17 sessions'
python was captured, harvested and thrown away. One writer per path cannot
conflict, and an unrelated PR carries an ADDED file rather than a rewritten one.
The corpus is DERIVED from the accumulated sightings by :func:`fold_into_corpus`,
which no individual session has to run.

PRIVACY. These files are committed. Every program and argv tail goes through
:func:`redact` first, and a sighting that cannot be redacted safely is dropped
rather than published. Redaction happens BEFORE the key is computed, so the key
is a function of the text that actually gets written and re-harvesting the raw
log cannot fork a record.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import corpus, paths
from .capture import looks_pythonish

# Provenance ranking. A shim record proves the program actually RAN, which is
# stronger evidence than a command string that merely mentions python. The hook
# is the PreToolUse-captured variant of the transcript class — the same command
# string, seen earlier and more reliably — so it ranks above it.
SOURCE_RANK = {"shim": 3, "hook": 2, "transcript": 1, "manual": 0, corpus.SEED: -1}

# Largest program kept. A multi-megabyte heredoc is a data blob, not a one-liner
# the runtime has to be fast at.
MAX_PROGRAM_BYTES = 64 * 1024


# --- the record --------------------------------------------------------------


@dataclass(frozen=True)
class Sighting:
    """One distinct program, with everything known about how it was seen.

    Not one log line: the published record shape is per-program, and ``count``
    is the number of distinct *occurrences* folded into it. Frozen, because a
    merge is a new record rather than a mutation of one already written.
    """

    key: str
    program: str
    argv_tail: Tuple[str, ...] = ()
    source: str = "hook"
    session: Optional[str] = None
    first_seen: str = ""
    count: int = 1
    stdin_sample: Optional[str] = None

    def to_obj(self) -> Dict[str, Any]:
        """The published line. ``key`` and ``id`` are the same value on purpose:
        ``key`` is what a sightings file is unioned on, ``id`` is what
        :mod:`lypning.corpus` reads, and writing both means neither reader has to
        know about the other."""
        return {
            "key": self.key,
            "id": self.key,
            "program": self.program,
            "argv_tail": list(self.argv_tail),
            "source": self.source,
            "session": self.session,
            "first_seen": self.first_seen,
            "count": self.count,
            "stdin_sample": self.stdin_sample,
        }

    @classmethod
    def from_obj(cls, obj: Dict[str, Any]) -> Optional[Sighting]:
        if not isinstance(obj, dict):
            return None
        program = obj.get("program")
        if not isinstance(program, str) or not program:
            return None
        key = obj.get("key") or obj.get("id")
        if not isinstance(key, str) or not key:
            key = sighting_key(program)
        argv = obj.get("argv_tail")
        count = obj.get("count", 1)
        source = obj.get("source")
        first = obj.get("first_seen")
        stdin = obj.get("stdin_sample")
        session = obj.get("session")
        return cls(
            key=key,
            program=program,
            argv_tail=tuple(str(a) for a in argv) if isinstance(argv, (list, tuple)) else (),
            source=source if isinstance(source, str) and source in SOURCE_RANK else "transcript",
            session=session if isinstance(session, str) and session else None,
            first_seen=first if isinstance(first, str) else "",
            count=count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 1,
            stdin_sample=stdin if isinstance(stdin, str) else None,
        )

    def entry(self) -> corpus.Entry:
        """As a corpus record. The key is already the corpus id."""
        return corpus.Entry(
            id=self.key,
            program=self.program,
            argv_tail=self.argv_tail,
            source=self.source,
            first_seen=self.first_seen,
            count=self.count,
            stdin_sample=self.stdin_sample,
        )


# --- normalisation -----------------------------------------------------------


def normalise(program: str) -> str:
    """Dedup key text: line endings unified, per-line trailing whitespace and
    outer blank lines dropped.

    Indentation is NOT touched. In Python that is syntax, and two programs
    indented differently are two programs. The shim sees ``-c $'\\nimport os'``
    often enough that keying on the raw text would file one program twice.
    """
    if not isinstance(program, str):
        return ""
    lines = [ln.rstrip(" \t") for ln in program.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def sighting_key(program: str) -> str:
    """``py-`` + 12 hex of sha256 over the NORMALISED text. Same function as
    :func:`corpus.program_id`, so a sighting and a corpus record agree."""
    return corpus.program_id(normalise(program))


# --- redaction ---------------------------------------------------------------
#
# The log is not safe to publish and the sightings are committed, so this runs
# on everything before it is written. It is a backstop, not a promise: a secret
# in a shape nobody has a pattern for still reads as ordinary program text.

# The names a credential is bound to, matched case-insensitively. The VALUE that
# follows is what gets replaced, never the name — naming the variable says which
# credential to rotate without restating it (docs/CAPTURE.md).
_NAME = r"(api[_-]?key|token|secret|passwd|password)"

# `name = "value"`, `name: value`, `"name": "value"`, `NAME=value`, `name == v`.
# The doubled `=` is allowed so a comparison against a literal secret is caught
# too; it costs a mangled comparison at worst.
_ASSIGNED = re.compile(
    r"(?i)(" + _NAME + r"[\"']?\s*(?:==|[:=]|=>)\s*)"
    r"(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;)\]}]+)"
)

# `--token VALUE`, where the value is the next word rather than an assignment.
_FLAGGED = re.compile(
    r"(?i)(--?" + _NAME + r"\s+)(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;)\]}]+)"
)

# `Authorization: Bearer <token>` — the one credential that announces itself by
# a keyword rather than by a name it is bound to.
_BEARER = re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9._~+/=-]{4,})")

# Credential shapes with a recognisable prefix. Kept in sync with the repo's
# canonical scan-secrets list. Written so this file does not self-match: every
# literal prefix is followed by a bracketed class that is not a member of itself.
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{24,}"),
    re.compile(r"sk_ber_[A-Za-z0-9_-]{8,}"),
    re.compile(r"gsk_[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gh[sour]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"GOCSPX-[A-Za-z0-9_-]{10,}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[bpoas]-[A-Za-z0-9-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

# Env var names that hold a credential, and the sentinels that sit in such a
# variable without being one. `proxy-injected` is this environment's: the real
# tokens are supplied per request, and several variables share that one string.
_SECRET_ENV_NAME = re.compile(r"(?i)key|token|secret|password|passwd|credential|auth|_pat$|dsn|webhook")
_PLACEHOLDERS = frozenset(
    ["proxy-injected", "placeholder", "changeme", "redacted", "not-set", "unset", "none"]
)
_MIN_ENV_SECRET = 12
# An address, not a token. `GIT_AUTHOR_EMAIL` is selected by an `*AUTH*` name and
# holds a public address; redacting it would mangle every program that mentions
# the committer.
_EMAIL_VALUE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_ENV_SECRETS_CACHE = None  # type: Optional[List[Tuple[str, str]]]


def env_secret_values(env: Optional[Dict[str, str]] = None) -> List[Tuple[str, str]]:
    """The credential VALUES this process can actually see, longest first.

    A pattern only catches a secret with a recognisable shape, and the dangerous
    ones here have none — a Cloudflare API token is 53 characters of unprefixed
    base62, indistinguishable from a hash or a chunk of test data. But the
    harvester runs inside the container that HOLDS those secrets, so it does not
    have to guess: it matches the literal value, which cannot false-positive on
    anything except the secret itself. Values are compared and never written;
    the marker names the variable instead.

    Longest first, so a secret containing another as a substring is replaced
    whole rather than leaving a fragment of itself behind.
    """
    src = env if env is not None else os.environ
    out: List[Tuple[str, str]] = []
    for name, raw in src.items():
        if not _SECRET_ENV_NAME.search(name):
            continue
        value = str(raw or "")
        if len(value) < _MIN_ENV_SECRET:
            continue
        if value.startswith("/"):
            continue  # a path (…_FILE)
        if value.isdigit():
            continue  # a file descriptor or a count
        # A credential is one opaque token; these shapes never are, and all of
        # them live under credential-matching names in real containers.
        if re.search(r"\s", value):
            continue  # a sentence or a flag list
        if "://" in value:
            continue  # a URL — GIT_CONFIG_KEY_1 is `url.https://…insteadOf`
        if re.match(r"^[a-z][a-z0-9]*(\.[a-z0-9]+)+$", value):
            continue  # a dotted config key — GIT_CONFIG_KEY_0
        if _EMAIL_VALUE.match(value):
            continue
        if value.lower() in _PLACEHOLDERS:
            continue
        out.append((name, value))
    out.sort(key=lambda nv: (-len(nv[1]), nv[0]))
    return out


def _env_secrets() -> List[Tuple[str, str]]:
    global _ENV_SECRETS_CACHE
    if _ENV_SECRETS_CACHE is None:
        _ENV_SECRETS_CACHE = env_secret_values()
    return _ENV_SECRETS_CACHE


# A credential-shaped run of characters: long, opaque, mixed. Env var names
# (`GITHUB_TOKEN`) and dotted identifiers are excluded, which is what keeps
# `os.environ["SOME_LONG_TOKEN_NAME"]` from reading as a leak.
_OPAQUE = re.compile(r"[A-Za-z0-9_\-+/=]{24,}")

# The label used in a marker, and the one hit that is not a pattern name.
UNSAFE = "unsafe"

# A marker this module already wrote. Its space-free form is by construction a
# run of 24+ characters from the opaque class, so a residual scan that did not
# excise it would read every successfully redacted BARE value as a leftover
# credential — and drop the very sighting redaction had just made safe.
_MARKER_TEXT = re.compile(r"\[REDACTED[^\]\n]*\]")


def _looks_opaque(token: str) -> bool:
    if len(token) < 24:
        return False
    if re.match(r"^[A-Z0-9_]+$", token):
        return False  # an env var name in caps
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


def _residual(text: str) -> bool:
    """Is a credential name still sitting next to something opaque?

    The check that decides whether a sighting can be published at all. Redaction
    handles the shapes it knows; this catches the leftovers — a secret spread
    over a concatenation, a value in a form no pattern describes — by looking
    for the one thing they have in common with the ones we do catch: a
    credential word within reach of a long opaque token.
    """
    text = _MARKER_TEXT.sub("[]", text)
    for m in re.finditer(r"(?i)" + _NAME + r"|Bearer", text):
        window = text[m.end(): m.end() + 64]
        for tok in _OPAQUE.findall(window):
            if _looks_opaque(tok):
                return True
    return False


def _marker(label: str, length: int, quoted: bool) -> str:
    """``[REDACTED token 24 chars]``, or a space-free form for a bare value.

    A bare value sits where a space would change the token count — a shell word,
    an unquoted flag argument — so it gets hyphens instead. Neither form can
    re-match a credential pattern, which is what makes redaction idempotent.
    """
    body = "REDACTED {0} {1} chars".format(label, length)
    return "[" + (body if quoted else body.replace(" ", "-")) + "]"


def _replace_value(match, hits: List[str]) -> str:
    head, name, value = match.group(1), match.group(2), match.group(3)
    quote = value[0] if value[:1] in ("'", '"') else ""
    inner = value[1:-1] if quote and len(value) >= 2 and value[-1] == quote else value
    if not inner or inner.startswith("[REDACTED"):
        return match.group(0)
    label = name.lower().replace("-", "_")
    hits.append(label)
    return head + quote + _marker(label, len(inner), bool(quote)) + quote


def redact(program: str) -> Tuple[str, List[str]]:
    """Scrub credentials out of a program. Returns the text and what was hit.

    These are real commands from real sessions, so the value is replaced rather
    than the whole program dropped — a one-liner that happens to carry a token
    is still evidence of what python the agent runs. The hit list is what makes
    it auditable: a human reading a sightings diff can see that a ``password``
    was scrubbed here and an ``env GITHUB_TOKEN`` there without the values ever
    being written down.

    Exact env values go first: a live credential must not survive on the grounds
    that no pattern happened to describe it, and going first means a shaped
    secret that is also in the environment is NAMED rather than just prefixed.
    Idempotent — no marker can re-match.
    """
    if not isinstance(program, str) or not program:
        return "", []
    hits: List[str] = []
    out = program

    for name, value in _env_secrets():
        if value not in out:
            continue
        out = out.replace(value, _marker("env " + name, len(value), True))
        hits.append("env " + name)

    out = _ASSIGNED.sub(lambda m: _replace_value(m, hits), out)
    out = _FLAGGED.sub(lambda m: _replace_value(m, hits), out)

    def _bearer(m):
        if m.group(2).startswith("[REDACTED"):
            return m.group(0)
        hits.append("bearer")
        return m.group(1) + _marker("bearer", len(m.group(2)), False)

    out = _BEARER.sub(_bearer, out)

    for rx in SECRET_PATTERNS:
        def _shaped(m):
            hits.append(m.group(0)[:4])
            return _marker(m.group(0)[:4], len(m.group(0)), False)

        out = rx.sub(_shaped, out)

    if _residual(out):
        hits.append(UNSAFE)
    return out, hits


# A credential name standing alone as one argv element. The VALUE is then the
# next element, which is the one shape no single-string scan can see.
_ARGV_NAME = re.compile(r"(?i)^--?" + _NAME + r"$|^" + _NAME + r"$")


def redact_argv(argv: Sequence[Any]) -> Tuple[Tuple[str, ...], List[str]]:
    """Redact an argv tail as a SEQUENCE, not as a list of independent strings.

    ``python x.py --password hunter2`` splits the credential across two
    elements: neither one contains both the name and the value, so scanning them
    separately sees a harmless flag followed by a harmless word and publishes
    the secret intact. The position is the only thing that carries the meaning,
    so an element following a credential-named one is replaced whole. The cost
    is a mangled ``--secret true``; the alternative is a published token.
    """
    out: List[str] = []
    hits: List[str] = []
    after_name = False
    for raw in argv:
        text, hit = redact(str(raw))
        if after_name and text and not text.startswith("[REDACTED"):
            hits.append("argv value")
            text = _marker("argv value", len(text), False)
        else:
            hits.extend(hit)
        after_name = bool(_ARGV_NAME.match(str(raw)))
        out.append(text)
    return tuple(out), hits


def is_safe(hits: Sequence[str]) -> bool:
    """A sighting whose redaction left a credential-shaped residue is dropped
    rather than published. Losing one captured one-liner costs a corpus entry;
    publishing a live token costs a rotation and a bad afternoon."""
    return UNSAFE not in hits


# --- what is worth keeping ---------------------------------------------------

_KNOWN_KEYS: Optional[Set[str]] = None


def known_keys(refresh: bool = False) -> Set[str]:
    """Every key the shipped corpus already holds, harvested and seed alike.

    Both spellings of a corpus record's identity are included: the stored ``id``
    and the id recomputed from the program text. The seed corpus is keyed by
    hand-written slugs, so without the recomputation a seed program would look
    unseen and re-enter the observed record — which is exactly how an early
    harvest ended up with 138 of its 197 "observed" programs byte-identical to
    seeds. Expectation must not inflate the frequency table that ranks work.
    """
    global _KNOWN_KEYS
    if _KNOWN_KEYS is None or refresh:
        keys: Set[str] = set()
        try:
            for e in corpus.load_default():
                keys.add(e.id)
                keys.add(e.fingerprint)
        except Exception:
            pass  # no corpus shipped yet: the guard is lost, the harvest is not
        _KNOWN_KEYS = keys
    return _KNOWN_KEYS


def is_interesting(program: str, known: Optional[Set[str]] = None) -> bool:
    """Is this program worth publishing?

    Three rejections, in cost order: a program that is empty once normalised
    (``python3 -c ''``, a heredoc with a blank body), a program that is only
    ``pass`` (what a session runs to check an interpreter *exists*, which says
    nothing about the python it runs), and anything the corpus already holds
    under the same key — republishing that would add a line to a committed file
    to say something already committed.
    """
    return not _why_uninteresting(program, known_keys() if known is None else known)


def _why_uninteresting(program: str, known: Set[str]) -> str:
    """The same three tests, naming which one fired. ``""`` means keep it."""
    text = normalise(program)
    if not text:
        return "empty"
    if all(ln.strip() in ("", "pass") for ln in text.split("\n")):
        return "trivial"
    if sighting_key(text) in known:
        return "known"
    return ""


# --- pulling programs out of a shell command ---------------------------------

_PY_WORD = re.compile(r"^(?:python[0-9.]*|py)$")
_SEPARATORS = frozenset([";", "|", "||", "&&", "&", "(", ")", "\n", "{", "}"])
_RUNNERS = frozenset(["uv", "pipx", "poetry", "hatch", "pdm", "rye"])

# A heredoc delimiter is a shell word. Anything else — a stray quote picked up
# from a `<<` that lives INSIDE a quoted string, which is how a command that
# merely TALKS about a heredoc gets mistaken for one — is not a heredoc.
_HEREDOC_DELIM = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# `(?:^|[^<])` keeps a herestring (`<<<word`) from reading as a heredoc.
_HEREDOC_OPEN = re.compile(r"(?:^|[^<])<<(-?)\s*(?:'([^']*)'|\"([^\"]*)\"|([A-Za-z_][A-Za-z0-9_]*))")
_PY_DELIM = re.compile(r"(?i)^(?:PY|PYTHON|PYEOF|EOFPY)$")


def split_heredocs(command: str) -> Tuple[str, List[Tuple[str, str, str]]]:
    """Split heredoc bodies out, so a body is never tokenised as shell words.

    Returns the command with the bodies removed, plus ``(delim, body, header)``
    per heredoc — the header being the line that opened it, which is what says
    whether the body was fed to an interpreter.
    """
    lines = str(command).split("\n")
    heredocs: List[Tuple[str, str, str]] = []
    kept: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _HEREDOC_OPEN.search(line)
        delim = (m.group(2) or m.group(3) or m.group(4) or "") if m else ""
        if not m or not _HEREDOC_DELIM.match(delim):
            kept.append(line)
            i += 1
            continue
        dash = m.group(1) == "-"
        body: List[str] = []
        j = i + 1
        while j < len(lines):
            # `<<-DELIM` strips leading TABS from the body and the terminator;
            # the strip() on the comparison is deliberate slack for commands
            # written by hand.
            probe = re.sub(r"^\t+", "", lines[j]) if dash else lines[j]
            if probe.strip() == delim:
                break
            body.append(probe)
            j += 1
        heredocs.append((delim, "\n".join(body), line))
        # m.start() can point one char BEFORE `<<` because of the leading group.
        kept.append(line[: m.start() + (0 if m.group(0).startswith("<<") else 1)])
        i = j + 1  # skip body + terminator
    return "\n".join(kept), heredocs


def shell_tokens(text: str) -> List[str]:
    """Minimal shell word splitter: enough to find ``-c <program>`` without
    being fooled by quoting. Unquoted operators come back as their own tokens.

    Not a shell parser and not trying to be — it never expands anything, which
    is the point: a captured command is data, and evaluating it to read it would
    be the single worst thing this package could do.
    """
    tokens: List[str] = []
    cur: List[str] = []
    started = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "'":
            started = True
            end = text.find("'", i + 1)
            if end == -1:
                cur.append(text[i + 1:])
                i = n
            else:
                cur.append(text[i + 1: end])
                i = end + 1
            continue
        if c == '"':
            started = True
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n and text[j + 1] in '"\\$`\n':
                    if text[j + 1] != "\n":
                        cur.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == '"':
                    break
                cur.append(text[j])
                j += 1
            i = j + 1
            continue
        if c == "\\" and i + 1 < n:
            if text[i + 1] == "\n":
                i += 2
                continue
            started = True
            cur.append(text[i + 1])
            i += 2
            continue
        if c in " \t":
            if started:
                tokens.append("".join(cur))
            cur, started = [], False
            i += 1
            continue
        if c in "\n;&|()":
            if started:
                tokens.append("".join(cur))
            cur, started = [], False
            op = c
            if c in "&|" and i + 1 < n and text[i + 1] == c:
                op = c + c
                i += 1
            tokens.append(op)
            i += 1
            continue
        started = True
        cur.append(c)
        i += 1
    if started:
        tokens.append("".join(cur))
    return tokens


def _at(tokens: Sequence[str], i: int) -> str:
    return tokens[i] if 0 <= i < len(tokens) else ""


def extract_with_tails(command: str) -> List[Tuple[str, List[str]]]:
    """Every embedded program with the argv that followed it.

    ``python -c PROG a b`` yields ``("PROG", ["a", "b"])``. Heredocs come last
    and carry no tail, because their argv sits on the opening line where this
    parser has already stopped looking.
    """
    if not isinstance(command, str) or not command:
        return []
    found: List[Tuple[str, List[str]]] = []
    stripped, heredocs = split_heredocs(command)
    tokens = shell_tokens(stripped)

    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in _RUNNERS and _at(tokens, i + 1) == "run":
            i += 1
            # `uv run python -c …` and `uv run -c …` both continue from here;
            # the latter is not a thing, but the scan is cheap.
            if _PY_WORD.match(_at(tokens, i + 1)):
                i += 1
        elif not _PY_WORD.match(t):
            i += 1
            continue
        j = i + 1
        while j < len(tokens):
            a = tokens[j]
            if a in _SEPARATORS:
                break
            if a == "-c":
                prog = _at(tokens, j + 1)
                if prog.strip():
                    tail: List[str] = []
                    k = j + 2
                    while k < len(tokens) and tokens[k] not in _SEPARATORS:
                        tail.append(tokens[k])
                        k += 1
                    found.append((prog, tail))
                break
            if a == "-m" or not a.startswith("-"):
                break  # a module or a script path: no inline source
            j += 1
        i += 1

    for delim, body, header in heredocs:
        if not body.strip():
            continue
        if not looks_pythonish(header) and not _PY_DELIM.match(delim):
            continue
        found.append((body, []))
    return found


def extract_from_command(command: str) -> List[str]:
    """Just the programs: ``-c`` arguments (both quote styles), heredoc bodies,
    and the same through a ``uv``/``pipx``/``poetry``/``hatch``/``pdm``/``rye
    run`` wrapper. Order is stable, heredocs last."""
    return [prog for prog, _ in extract_with_tails(command)]


# --- inputs ------------------------------------------------------------------
#
# A raw occurrence is (occurrence key, program, argv_tail, source, session, ts).
# The occurrence key is what makes a count stable: the log is append-only, so a
# line number does not move, and a transcript's tool_use id never changes. The
# key is namespaced by SESSION because the scope of a line number is one log in
# one container — `#12` alone repeats everywhere. That namespacing is also what
# lets a session's live log and its own published file be read in the same
# harvest without counting one invocation twice.

_Raw = Tuple[str, str, List[str], str, Optional[str], str]


def _read_text(path: Path) -> str:
    try:
        if not path.is_file():
            return ""
        with open(str(path), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _raws_from_log(text: str) -> List[_Raw]:
    out: List[_Raw] = []
    for n, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue  # a half-written append is a lost sighting, not a crash
        if not isinstance(rec, dict):
            continue
        session = rec.get("session")
        session = session if isinstance(session, str) and session else None
        ts = rec.get("ts") if isinstance(rec.get("ts"), str) else ""
        tag = session or "invocations"
        kind = rec.get("kind")
        if kind == "python_invocation":
            program = rec.get("program")
            if not isinstance(program, str) or not program.strip():
                continue  # `python script.py`, `-m json.tool`, `--version`
            argv = rec.get("argv_tail")
            tail = [str(a) for a in argv] if isinstance(argv, list) else []
            out.append(("shim:{0}#{1}".format(tag, n), program, tail, "shim", session, ts))
        elif kind == "bash_command" and isinstance(rec.get("command"), str):
            for idx, (program, tail) in enumerate(extract_with_tails(rec["command"])):
                out.append(("hook:{0}#{1}#{2}".format(tag, n, idx), program, tail, "hook", session, ts))
        # {"kind":"exit"} carries no program — it exists for timing analysis.
    return out


def _aggregate(raws: Iterable[_Raw]) -> List[Sighting]:
    """Fold occurrences into one Sighting per distinct program.

    ``count`` is the number of DISTINCT occurrence keys, never an increment, so
    a second pass over the same inputs produces the same number. Occurrences are
    sorted first, so which text and session represent a program does not depend
    on the order the inputs happened to be read in.
    """
    seen: Set[str] = set()
    groups: Dict[str, List[_Raw]] = {}
    for raw in sorted(raws, key=lambda r: r[0]):
        if raw[0] in seen:
            continue
        seen.add(raw[0])
        groups.setdefault(sighting_key(raw[1]), []).append(raw)

    out: List[Sighting] = []
    for key in sorted(groups):
        occ = groups[key]
        stamps = [r[5] for r in occ if r[5]]
        tail = next((r[2] for r in occ if r[2]), [])
        source = max((r[3] for r in occ), key=lambda s: SOURCE_RANK.get(s, 0))
        out.append(Sighting(
            key=key,
            program=occ[0][1],
            argv_tail=tuple(tail),
            source=source,
            session=occ[0][4],
            first_seen=min(stamps) if stamps else "",
            count=len(occ),
        ))
    return out


def parse_log(path: Optional[Path] = None) -> List[Sighting]:
    """The JSONL both feeds append to, as sightings. Never raises.

    One record per distinct program, sorted by key. A missing log is the normal
    state of a session that never ran python and yields nothing.
    """
    target = Path(path) if path is not None else paths.log_path()
    return _aggregate(_raws_from_log(_read_text(target)))


def _transcript_files(roots: Iterable[Path]) -> List[Path]:
    found: List[Path] = []
    for root in roots:
        try:
            if root.is_file():
                found.append(root)
                continue
            for dirpath, dirnames, filenames in os.walk(str(root)):
                dirnames.sort()
                for name in sorted(filenames):
                    if name.endswith(".jsonl"):
                        found.append(Path(dirpath) / name)
        except OSError:
            continue
    return sorted(set(found))


def transcript_root() -> Path:
    """``$LYPNING_TRANSCRIPTS``, else Claude Code's own ``~/.claude/projects``."""
    env = os.environ.get("LYPNING_TRANSCRIPTS", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(os.path.expanduser("~")) / ".claude" / "projects"


def scan_transcripts(paths: Any = None) -> List[Sighting]:
    """Bash ``tool_use`` inputs from Claude Code transcripts.

    The optional third feed, and the only one that reaches backwards: a session
    that ran before the shim was installed still contributes through its
    transcript. Everything about it is best-effort — the root is often absent
    entirely, and a harvest must not care.

    ``paths`` may be a directory, a file, or an iterable of either. The key is
    the ``tool_use`` id, which never changes, so a re-scan cannot double-count.
    """
    if paths is None:
        roots = [transcript_root()]
    elif isinstance(paths, (str, Path)):
        roots = [Path(paths)]
    else:
        try:
            roots = [Path(p) for p in paths]
        except TypeError:
            return []
    raws: List[_Raw] = []
    for file in _transcript_files(roots):
        session = file.name[: -len(".jsonl")] or None
        for line in _read_text(file).split("\n"):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            message = ev.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            ts = ev.get("timestamp") if isinstance(ev.get("timestamp"), str) else ""
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use" or block.get("name") != "Bash":
                    continue
                command = (block.get("input") or {}).get("command") if isinstance(block.get("input"), dict) else None
                if not isinstance(command, str) or not looks_pythonish(command):
                    continue
                block_id = block.get("id") if isinstance(block.get("id"), str) else "noid"
                for idx, (program, tail) in enumerate(extract_with_tails(command)):
                    raws.append((
                        "transcript:{0}#{1}#{2}".format(file.name, block_id, idx),
                        program, tail, "transcript", session, ts,
                    ))
    return _aggregate(raws)


# --- publishing --------------------------------------------------------------


def session_id() -> str:
    """This session, as far as the environment will say."""
    return (os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or "").strip()


def session_filename(session: Optional[str]) -> str:
    """A session id as a filename. Ids are uuids in practice; anything else is
    flattened rather than trusted, because this becomes a path."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session or "unknown"))[:80]
    return (safe or "unknown") + ".jsonl"


def serialise(sightings: Sequence[Sighting]) -> str:
    """The published form: sorted by key, one compact JSON object per line.

    Sorted rather than appended, so two runs that saw the same programs produce
    the same bytes whatever order the log happened to be in.
    ``ensure_ascii=False`` matches :func:`corpus.write` — a third of these
    programs contain non-ASCII text, and escaping it would rewrite every line
    the first time anything touched the file.
    """
    buf = []
    for s in sorted(sightings, key=lambda x: x.key):
        buf.append(json.dumps(s.to_obj(), separators=(",", ":"), ensure_ascii=False))
        buf.append("\n")
    return "".join(buf)


def read_sightings(path: Path) -> List[Sighting]:
    """A published file back as sightings. A corrupt line is dropped rather than
    allowed to fail the export."""
    out: List[Sighting] = []
    for line in _read_text(Path(path)).split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        s = Sighting.from_obj(obj)
        if s is not None:
            out.append(s)
    return out


def _combine(old: Sighting, new: Sighting) -> Sighting:
    """Merge two records of one program. Idempotent in both directions.

    ``count`` is max, not sum: the counts on both sides are derived from the
    same occurrence keys, so summing them would double the record every time the
    export ran. Max also means a rotated-away log never shrinks a published
    count. The stored text and argv win, so a hand-corrected record survives.
    """
    stamps = [t for t in (old.first_seen, new.first_seen) if t]
    return replace(
        old,
        source=old.source if SOURCE_RANK.get(old.source, 0) >= SOURCE_RANK.get(new.source, 0) else new.source,
        session=old.session or new.session,
        first_seen=min(stamps) if stamps else "",
        count=max(old.count, new.count),
        argv_tail=old.argv_tail or new.argv_tail,
        stdin_sample=old.stdin_sample if old.stdin_sample is not None else new.stdin_sample,
    )


def _clean(s: Sighting, known: Set[str]) -> Tuple[Optional[Sighting], List[str], str]:
    """Redact, guard and re-key one sighting. Returns the record, what redaction
    hit, and — when it must not be published — WHY.

    The reason is carried rather than discarded because the counts are the only
    signal a human gets: a run that drops one program is routine, and a run that
    drops hundreds as ``known`` means something is executing the corpus back
    into the log, which is a feedback loop, not hygiene. Re-keying after
    redaction is what keeps the key a function of the text that gets written.
    """
    program, hits = redact(s.program)
    if not is_safe(hits):
        return None, hits, "unredactable"
    if len(program.encode("utf-8")) > MAX_PROGRAM_BYTES:
        return None, hits, "oversized"
    why = _why_uninteresting(program, known)
    if why:
        return None, hits, why
    tail, tail_hits = redact_argv(s.argv_tail)
    if not is_safe(tail_hits):
        return None, hits, "unredactable"  # a tail we cannot clean is not published
    hits.extend(tail_hits)
    return replace(s, key=sighting_key(program), program=program, argv_tail=tail), hits, ""


@dataclass
class Export:
    """What one export did, for the reporter and the tests."""

    files: List[Tuple[Path, int, int, bool]]  # path, added, total, changed
    gathered: int = 0
    added: int = 0
    redactions: int = 0
    skipped: Dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> List[Tuple[Path, int, int, bool]]:
        return [f for f in self.files if f[3]]

    def to_obj(self) -> Dict[str, Any]:
        """For ``lypning harvest --json``. Paths as strings, nothing else."""
        return {
            "gathered": self.gathered,
            "added": self.added,
            "redactions": self.redactions,
            "skipped": dict(self.skipped or {}),
            "files": [
                {"path": str(p), "added": a, "total": t, "changed": c}
                for p, a, t, c in self.files
            ],
        }


def _export(project: Optional[Path] = None, *, log: Optional[Path] = None) -> Export:
    gathered = parse_log(log)
    known = known_keys()
    root = paths.sightings_dir(project)

    by_session: Dict[str, List[Sighting]] = {}
    redactions = 0
    skipped: Dict[str, int] = {}
    for s in gathered:
        kept, hits, why = _clean(s, known)
        if kept is None:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        redactions += len(hits)
        session = kept.session or session_id() or "unknown"
        by_session.setdefault(session, []).append(replace(kept, session=session))

    files: List[Tuple[Path, int, int, bool]] = []
    added = 0
    for session in sorted(by_session):
        path = root / session_filename(session)
        by_key = {s.key: s for s in read_sightings(path)}
        new_here = 0
        for s in by_session[session]:
            if s.key in by_key:
                by_key[s.key] = _combine(by_key[s.key], s)
            else:
                by_key[s.key] = s
                new_here += 1
        body = serialise(list(by_key.values()))
        before = _read_text(path)
        changed = body != before
        if changed:
            try:
                paths.ensure_dir(path.parent)
                tmp = path.with_name(path.name + ".tmp")
                with open(str(tmp), "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(body)
                os.replace(str(tmp), str(path))
            except OSError:
                changed = False  # unwritable tree: the log keeps the evidence
        added += new_here
        files.append((path, new_here, len(by_key), changed))

    return Export(files=files, gathered=len(gathered), added=added,
                  redactions=redactions, skipped=skipped)


def export_sightings(project: Optional[Path] = None, *, quiet: bool = True) -> Tuple[Optional[Path], int, int]:
    """Publish this container's captures as ``sightings/<session>.jsonl``.

    Returns ``(path, added, total)``: the file for this session (or the first
    one written, when the log spans sessions), how many keys were new, and how
    many records the file now holds. ``path`` is None when nothing was written
    at all — a session that ran no python, which is most of them.

    Union by key and sorted, so re-running over the same log rewrites nothing:
    an unchanged file is not even opened for writing. It writes no corpus and
    makes no commit; staging is left to a pre-commit hook, which adds this one
    directory to a commit the session was making anyway.

    ``quiet`` suppresses the one summary line, which goes to stderr — the hook's
    stdout carries a protocol response and must not be polluted with anything
    else.
    """
    result = _export(project)
    mine = session_filename(session_id()) if session_id() else None
    written = result.changed
    path: Optional[Path] = None
    total = 0
    for entry in written:
        if path is None or (mine and entry[0].name == mine):
            path, total = entry[0], entry[2]
    if not quiet and written:
        try:
            sys.stderr.write("lypning: exported {0} new sighting(s) to {1}\n".format(
                result.added, ", ".join(str(f[0]) for f in written)))
        except Exception:
            pass
    return path, result.added, total


# --- deriving the corpus -----------------------------------------------------


def collect(project: Optional[Path] = None, *, log: Optional[Path] = None,
            transcripts: bool = False) -> List[Sighting]:
    """Everything this checkout knows about, merged by key.

    Three inputs, in increasing order of durability: this container's live log,
    optionally the Claude Code transcripts, and the PUBLISHED sightings of every
    session that has ever run here — the last being the only one that outlives a
    container, and therefore the one the corpus is actually derived from.

    A session's live log and its own published file describe the same
    invocations, so they collide by design; :func:`_combine` takes the max of
    two counts rather than the sum, which is what stops one invocation being
    counted twice when both are read in the same pass.
    """
    merged: Dict[str, Sighting] = {}
    groups = [parse_log(log)]
    if transcripts:
        groups.append(scan_transcripts())
    root = paths.sightings_dir(project)
    try:
        files = sorted(p for p in root.iterdir() if p.suffix == ".jsonl" and p.is_file())
    except OSError:
        files = []  # a fresh checkout has published nothing yet
    for file in files:
        groups.append(read_sightings(file))
    for group in groups:
        for s in group:
            cur = merged.get(s.key)
            merged[s.key] = s if cur is None else _combine(cur, s)
    return [merged[k] for k in sorted(merged)]



def fold_into_corpus(sightings: Sequence[Sighting],
                     corpus_path: Optional[Path] = None) -> Tuple[int, int]:
    """Merge sightings into the corpus. Returns ``(added, total)``.

    This is the DERIVE step — ``lypning harvest`` run deliberately, never a
    hook. It is the one place the corpus is written, and it is separate from the
    export for the reason in the module docstring: a shared file that every
    session rewrites is a file whose growth never survives a merge.

    Counts are combined with max rather than sum, for the same reason as
    :func:`_combine`: both sides count the same occurrence keys, so a fold that
    summed would inflate the corpus a little more on every run. Existing records
    are never dropped — a hand-curated entry and a rotated-away log both
    survive.
    """
    target = Path(corpus_path) if corpus_path is not None else paths.corpus_write_file()
    paths.ensure_dir(target.parent)
    existing = corpus.load(target)
    by_id: Dict[str, corpus.Entry] = {e.id: e for e in existing}
    added = 0
    for s in sightings:
        program, hits = redact(s.program)
        if not is_safe(hits) or not normalise(program):
            continue
        if len(program.encode("utf-8")) > MAX_PROGRAM_BYTES:
            continue
        tail, tail_hits = redact_argv(s.argv_tail)
        if not is_safe(tail_hits):
            continue
        entry = replace(s, key=sighting_key(program), program=program, argv_tail=tail).entry()
        cur = by_id.get(entry.id)
        if cur is None:
            by_id[entry.id] = entry
            added += 1
            continue
        stamps = [t for t in (cur.first_seen, entry.first_seen) if t]
        by_id[entry.id] = replace(
            cur,
            source=cur.source if SOURCE_RANK.get(cur.source, 0) >= SOURCE_RANK.get(entry.source, 0) else entry.source,
            first_seen=min(stamps) if stamps else "",
            count=max(cur.count, entry.count),
            argv_tail=cur.argv_tail or entry.argv_tail,
            stdin_sample=cur.stdin_sample if cur.stdin_sample is not None else entry.stdin_sample,
        )
    records = [by_id[k] for k in sorted(by_id)]
    body = "".join(
        json.dumps(e.to_obj(), separators=(",", ":"), ensure_ascii=False) + "\n" for e in records
    )
    # Compared before writing, so a fold that changes nothing leaves the file's
    # mtime alone and `git status` stays clean.
    if body != _read_text(target):
        corpus.write(records, target)
    return added, len(records)


# --- reporting ---------------------------------------------------------------


def render(result: Export, *, log: Optional[Path] = None,
           corpus_counts: Optional[Tuple[int, int]] = None) -> str:
    """The harvest summary. ASCII only — this goes to a terminal whose encoding
    we do not control, and a capture report is not worth a UnicodeEncodeError.
    """
    rows: List[Tuple[str, str]] = [
        ("log", str(log if log is not None else paths.log_path())),
        ("sightings", "{0} program(s)".format(result.gathered)),
    ]
    if result.redactions:
        rows.append(("redacted", "{0} credential(s)".format(result.redactions)))
    if result.skipped:
        # Never silent, and itemised: dropping hundreds as `known` means
        # something is executing the corpus back into the log — a reopened
        # feedback loop, not routine hygiene — and a rising `unredactable` count
        # means the log is collecting secrets nobody has a pattern for.
        rows.append(("dropped", ", ".join(
            "{0} {1}".format(n, why) for why, n in sorted(result.skipped.items()))))
    if corpus_counts is not None:
        rows.append(("corpus", "{0} new, {1} total".format(corpus_counts[0], corpus_counts[1])))
    width = max(len(k) for k, _ in rows)
    out = ["harvest", "=" * 7]
    for k, v in rows:
        out.append("{0}  {1}".format(k.ljust(width), v).rstrip())
    if not result.files:
        out.append("")
        out.append("nothing to publish")
        return "\n".join(out) + "\n"
    out.append("")
    for path, added, total, changed in result.files:
        out.append("{0}  {1:>4} new  {2:>5} total  {3}".format(
            str(path), added, total, "written" if changed else "unchanged"))
    return "\n".join(out) + "\n"
