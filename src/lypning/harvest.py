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

import bisect
import hashlib
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

    ``models`` is the per-model histogram of those occurrences, and it counts
    only the ones a model could be resolved for: ``sum(models) <= count``, and
    the difference is the unattributed hole. Occurrences that could not be
    joined — a shim record from outside any session, a log line written before
    the id was captured — are simply absent from it. No ``unknown`` bucket is
    stored, because a hole named in a committed record is a hole the next reader
    treats as data.

    That ``<=`` is a promise the merges keep deliberately, not one the
    arithmetic happens to allow: see :func:`_count_at_least_the_models`, which
    is what stops two records with different models producing a record whose
    hole is negative.

    ``extra`` is the same forward-compatibility bucket :class:`corpus.Entry`
    carries, and it is not decoration. These files are committed and are read
    and REWRITTEN by whatever version of lypning a session happens to be
    running: without it, an older harvest silently strips every key a newer one
    added, one Stop hook at a time.
    """

    key: str
    program: str
    argv_tail: Tuple[str, ...] = ()
    source: str = "hook"
    session: Optional[str] = None
    first_seen: str = ""
    count: int = 1
    stdin_sample: Optional[str] = None
    models: corpus.Models = ()
    extra: Dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def to_obj(self) -> Dict[str, Any]:
        """The published line. ``key`` and ``id`` are the same value on purpose:
        ``key`` is what a sightings file is unioned on, ``id`` is what
        :mod:`lypning.corpus` reads, and writing both means neither reader has to
        know about the other."""
        obj: Dict[str, Any] = {
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
        # Written only when there is something to write: every sightings line
        # committed before this field existed must keep the bytes it has.
        if self.models:
            obj["models"] = corpus.models_to_obj(self.models)
        for k, v in self.extra.items():
            if k not in obj:
                obj[k] = v
        return obj

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
        extra = {k: v for k, v in obj.items() if k not in _SIGHTING_KEYS}
        return cls(
            key=key,
            program=program,
            argv_tail=tuple(str(a) for a in argv) if isinstance(argv, (list, tuple)) else (),
            source=source if isinstance(source, str) and source in SOURCE_RANK else "transcript",
            session=session if isinstance(session, str) and session else None,
            first_seen=first if isinstance(first, str) else "",
            count=count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 1,
            stdin_sample=stdin if isinstance(stdin, str) else None,
            models=corpus.models_from_obj(obj.get("models")),
            extra=extra,
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
            models=self.models,
        )


#: Every key :meth:`Sighting.from_obj` consumes. Anything else on the line is an
#: extra and is carried through untouched. (Not to be confused with
#: :func:`known_keys` below, which is about corpus ids, not record fields.)
_SIGHTING_KEYS = frozenset((
    "key", "id", "program", "argv_tail", "source", "session", "first_seen",
    "count", "stdin_sample", "models",
))


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


# --- which model issued it ---------------------------------------------------
#
# The PreToolUse payload carries no model, and no CLAUDE_* variable exposes one,
# so the hook cannot know which model typed the command it is logging — and must
# not try, because it runs before every Bash call in the session and opening a
# transcript there would put a multi-megabyte read on the hot path (invariant
# 5). What the payload does carry is `tool_use_id`, which capture.py writes
# down. The join happens HERE, on the cold path, once per harvest.
#
# It joins because a Claude Code transcript's assistant record carries
# `message.model` on the SAME record whose `message.content` holds the
# `tool_use` block, so the id is an exact key and needs no heuristic at all.
#
# The catch, measured on this machine on 2026-09-02: `transcript_path` always
# names the MAIN session file, but a Bash call issued by a SUBAGENT has its
# tool_use block only under `<session>/subagents/**/agent-*.jsonl`, and most of
# the python captured in this project comes from subagents. An index built from
# `transcript_path` alone would therefore leave the majority of it unresolved —
# and a time join over the main file alone would do something worse, attributing
# a subagent's program to the parent loop's model, which is exactly the silent
# wrong answer this join exists to avoid. Both are indexed, which is safe
# because a tool_use id is unique across transcripts: over this machine's 152
# transcript files on 2026-09-02, 2751 distinct ids and not one of them in two
# files, so the union of two indexes cannot collide and a re-scan cannot
# double-count.
#
# THE COLD PATH IS NOT A FREE PATH. `_export` runs on every Stop — every turn
# boundary — and the log it reads is append-only, so the set of transcripts a
# harvest asks about never shrinks: it accumulates one more session for as long
# as the log lives. Re-indexing all of them from byte zero every time cost 1.86 s
# per turn boundary on a copy of this machine's log on 2026-09-02 (750 records,
# 4 sessions, 39.1 MB of transcript trees, best of 9 interleaved runs) against
# 0.70 s for the same log with nothing to join at all — and that gap is a
# function of the log's AGE, not of the work the turn did. So the index is
# incremental, and what makes an incremental read correct here is the one
# property these files have: a transcript is append-only JSONL. Bytes already
# read never change, which makes a byte offset a complete description of what
# has been seen, and the delta at the next turn boundary is usually a few
# kilobytes and often nothing at all. Warm, the same harvest measured 0.97 s.


_TIMESTAMP = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z?\Z")


def _canonical_ts(ts: Any) -> Optional[str]:
    """One fixed-width spelling of an instant, or None for anything else.

    The timeline is searched with :mod:`bisect`, so the time ordering IS
    whatever ``<`` does to the stored strings — and ISO-8601 is only
    lexicographic when every field is the same width. Here they are not. A
    transcript stamp carries milliseconds; the shim's carries whole seconds on
    any host whose ``date`` has no ``%3N``, which is every BSD and therefore
    this one. Compared raw, ``"…:24Z" > "…:24.900Z"`` — ``Z`` sorts after ``.``
    — and a search for "the model speaking at …:24" walks 900 ms PAST the
    instant it was asked about and returns whoever spoke next. That is a
    silently wrong model, which is the worst answer this join can give.

    So the fraction is padded to six digits and the zone letter is dropped, and
    a compare is a compare again. Anything not of this shape at all — an offset
    other than ``Z``, a date-only string, a number — yields None, and the
    callers turn None into "no model" rather than into a guess: an unattributed
    occurrence is a hole the report names, a wrongly attributed one is a claim
    nobody can see is false.
    """
    if not isinstance(ts, str):
        return None
    m = _TIMESTAMP.match(ts.strip())
    if not m:
        return None
    return m.group(1) + "." + (m.group(2) or "")[:6].ljust(6, "0")


@dataclass(frozen=True)
class _ModelIndex:
    """What one session's transcripts say about which model ran what.

    Two lookups, because the two feeds know different things. A hook occurrence
    has a ``tool_use`` id and joins EXACTLY. A shim occurrence — a nested spawn
    the hook never saw — has only a timestamp, and joins against the timeline:
    the latest assistant record at or before it.

    Every stamp on both sides of that compare goes through
    :func:`_canonical_ts` first, and the timeline holds nothing else. The two
    feeds do not agree on how many digits a second has, and a plain compare
    between them is not a time ordering — see there.
    """

    by_id: Dict[str, str]
    timeline: List[Tuple[str, str]]  # (canonical timestamp, model), sorted

    def for_id(self, tool_use_id: Optional[str]) -> Optional[str]:
        return self.by_id.get(tool_use_id) if tool_use_id else None

    def at(self, ts: Optional[str]) -> Optional[str]:
        """The model that was speaking at ``ts``, or None.

        Weaker than :meth:`for_id` and knowingly so: the shim record names no
        thread, so when a subagent and its parent were both active this can pick
        the wrong one. It is used only where there is no id to join on, and a
        miss yields None rather than the nearest guess.
        """
        key = _canonical_ts(ts)
        if key is None or not self.timeline:
            return None
        i = bisect.bisect_right(self.timeline, (key, "\uffff")) - 1
        return self.timeline[i][1] if i >= 0 else None


_EMPTY_INDEX = _ModelIndex({}, [])


def _model_of(message: Any) -> Optional[str]:
    """``message.model``, when it names a model that could have issued a program.

    ``<synthetic>`` is the CLI's own spelling for an assistant record it wrote
    without asking a model. Filing programs under it would put a name in the
    corpus that no one can slice on, and it is measurably the only pseudo-value
    in play — so anything not a plain, non-empty, non-``<`` string is "no
    model", not a model.
    """
    if not isinstance(message, dict):
        return None
    model = message.get("model")
    if not isinstance(model, str) or not model or model.startswith("<"):
        return None
    return model


def _scan_transcript(text: str) -> Tuple[Dict[str, str], List[Tuple[str, str]]]:
    """What a slice of transcript says: ids to models, and timeline points.

    A slice, not a file, because the caller reads only the bytes appended since
    last time. It is line-oriented and order-free, which is what lets the answer
    for a whole file be the union of the answers for its pieces.
    """
    by_id: Dict[str, str] = {}
    timeline: List[Tuple[str, str]] = []
    for line in text.split("\n"):
        # Every assistant record names a model and no other record does, so this
        # substring test skips the bulk of a transcript before json.loads sees
        # it. These files run to megabytes and most of that is user turns.
        if '"model"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        message = ev.get("message")
        model = _model_of(message)
        if model is None:
            continue
        ts = _canonical_ts(ev.get("timestamp"))
        if ts:
            timeline.append((ts, model))
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            block_id = block.get("id")
            if isinstance(block_id, str) and block_id:
                by_id[block_id] = model
    return by_id, timeline


# --- the index cache ---------------------------------------------------------
#
# It is a CACHE and never a source of truth, and every branch below is written
# to that rule: missing, unreadable, half-written, written by a version that
# spelled the schema differently, describing a file that has been truncated or
# replaced — each of those falls back to reading the file from byte zero, which
# is exactly what this module did before the cache existed. A cache miss costs
# one re-read. It cannot cost a model, it cannot invent one, and it cannot
# raise: this runs inside a Stop hook, and invariant 5 says a hook fails no
# session, including on its own failures.
#
# It lives under $LYPNING_HOME, never in the repository. Deleting it is always
# safe and costs one slow harvest.

#: Bumped when the stored shape changes. An older or newer number is not
#: migrated — it is ignored, and the transcripts are read again.
_CACHE_VERSION = 1

#: How much of the head of a transcript is hashed to prove it is still the same
#: file. Enough to cover a session's opening records, cheap enough to read on
#: every harvest of every file.
_CACHE_HEAD_BYTES = 4096


def _index_cache_path() -> Path:
    return paths.state_dir() / "model-index.json"


def _head_digest(path: Path, offset: int) -> str:
    """A fingerprint of bytes already consumed, or ``""`` if they cannot be read.

    The stat shape says a file is the same file; this says its CONTENT is still
    the content that produced the stored offset. An append cannot change it — a
    transcript only ever grows at the end — so a head that has moved means the
    path was rewritten rather than appended to, and resuming at the old offset
    would splice one file's records onto another's. mtime is deliberately not
    stored beside it: a stamp is the weakest signal available here, and it
    answers the same question worse than reading the bytes does.
    """
    n = min(offset, _CACHE_HEAD_BYTES)
    if n <= 0:
        return ""
    try:
        with open(str(path), "rb") as fh:
            head = fh.read(n)
    except (OSError, ValueError):
        return ""
    if len(head) < n:
        return ""
    return hashlib.sha256(head).hexdigest()[:32]


def _read_after(path: Path, offset: int) -> Tuple[str, int]:
    """The COMPLETE lines after ``offset``, and the offset just past them.

    Never the whole tail. The CLI is appending to these files while this runs,
    so the last line is routinely half a line; consuming it would index a
    truncated record AND move the offset past bytes that were never really
    read, and the rest of that line would never be seen again. Only up to the
    final newline is taken, and the remainder is read next time, whole.

    An offset is therefore always just past a newline and so always on a
    character boundary, which is what makes decoding a slice of a UTF-8 file
    safe at all.
    """
    try:
        with open(str(path), "rb") as fh:
            fh.seek(offset)
            chunk = fh.read()
    except (OSError, ValueError):
        return "", offset
    cut = chunk.rfind(b"\n")
    if cut < 0:
        return "", offset
    return chunk[:cut + 1].decode("utf-8", "replace"), offset + cut + 1


def _cache_entry_ok(entry: Any) -> bool:
    """Is this stored entry the shape this version reads?

    Total and cheap, and the reason a corrupt cache costs time and nothing else:
    whatever does not answer yes here is read again from byte zero. It is not
    paranoia about our own writes — it is that the file is on disk, outlives
    this version, and is edited by anyone with an editor.
    """
    if not isinstance(entry, dict):
        return False
    for name in ("offset", "ino", "dev"):
        value = entry.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return False
    if not isinstance(entry.get("head"), str):
        return False
    ids = entry.get("ids")
    if not isinstance(ids, dict):
        return False
    for key, value in ids.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return False
    timeline = entry.get("timeline")
    if not isinstance(timeline, list):
        return False
    for point in timeline:
        # Two strings, or bisect compares a str to an int and raises.
        if not (isinstance(point, list) and len(point) == 2
                and isinstance(point[0], str) and isinstance(point[1], str)):
            return False
    return True


class _IndexCache:
    """Where each transcript was read up to, and what it had said by then.

    One JSON file under ``$LYPNING_HOME``, keyed by absolute transcript path.
    Private to this module: nothing else reads it, so the schema needs no
    compatibility story beyond "a version we do not recognise is not read".

    Concurrency is last-writer-wins on purpose. Two sessions harvesting at the
    same moment both write the whole file, and the loser's newly indexed bytes
    are simply not there next time — one re-read, no wrong answer. The write
    itself is atomic (uniquely named temp file, then :func:`os.replace`), so a
    reader always finds one writer's complete state and never half of two.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = _index_cache_path() if path is None else Path(path)
        self.files: Dict[str, Dict[str, Any]] = {}
        self.dirty = False
        self._load()

    def _load(self) -> None:
        try:
            with open(str(self.path), "r", encoding="utf-8") as fh:
                blob = json.load(fh)
        except Exception:
            return  # absent, unreadable, half-written: index everything
        if not isinstance(blob, dict) or blob.get("version") != _CACHE_VERSION:
            return
        files = blob.get("files")
        if not isinstance(files, dict):
            return
        for key, entry in files.items():
            if isinstance(key, str) and _cache_entry_ok(entry):
                self.files[key] = entry

    def index(self, path: Path) -> _ModelIndex:
        """One transcript, reading only what has been appended since last time."""
        key = str(path)
        try:
            st = os.stat(str(path))
        except (OSError, ValueError):
            # ValueError as well as OSError: a path out of a JSON log is a
            # string this module did not choose, and one with a NUL in it is a
            # ValueError from the C call rather than a missing file.
            return _EMPTY_INDEX
        entry = self.files.get(key)
        if entry is not None and (
                entry["ino"] != st.st_ino or entry["dev"] != st.st_dev
                or st.st_size < entry["offset"]
                or entry["head"] != _head_digest(path, entry["offset"])):
            # A different inode, a file that has SHRUNK, or a head that moved:
            # this path is not the file the offset describes. Resuming inside it
            # would index bytes that were never read as though they had been.
            entry = None
        if entry is None:
            entry = {"offset": 0, "ino": st.st_ino, "dev": st.st_dev,
                     "head": "", "ids": {}, "timeline": []}
            self.files[key] = entry
            self.dirty = True
        if st.st_size > entry["offset"]:
            text, offset = _read_after(path, entry["offset"])
            if offset != entry["offset"]:
                ids, timeline = _scan_transcript(text)
                entry["ids"].update(ids)
                entry["timeline"].extend([t, m] for t, m in timeline)
                entry["timeline"].sort()
                entry["offset"] = offset
                entry["head"] = _head_digest(path, offset)
                self.dirty = True
        return _ModelIndex(entry["ids"], [(t, m) for t, m in entry["timeline"]])

    def save(self) -> None:
        """Persist, or fail to and say nothing.

        Silent by design: an unwritable state dir, a full disk and a racing
        writer all mean the same thing here, which is that the next harvest
        reads a transcript it could have skipped. Nothing a session does is
        allowed to depend on this having worked.
        """
        if not self.dirty:
            return
        self._prune()
        tmp = None
        try:
            paths.ensure_dir(self.path.parent)
            tmp = self.path.with_name("{0}.{1}.tmp".format(self.path.name, os.getpid()))
            with open(str(tmp), "w", encoding="utf-8") as fh:
                json.dump({"version": _CACHE_VERSION, "files": self.files},
                          fh, separators=(",", ":"))
            os.replace(str(tmp), str(self.path))
            self.dirty = False
        except Exception:
            if tmp is not None:
                try:
                    os.unlink(str(tmp))
                except OSError:
                    pass

    def _prune(self) -> None:
        """Forget transcripts that are no longer on disk.

        Without this the file is a record of every session that ever ran here
        rather than a cache of the ones that still exist, and it grows without
        anything ever removing a line.
        """
        for key in list(self.files):
            if not os.path.exists(key):
                del self.files[key]


def _model_index(transcript: Optional[str], cache: Optional[_IndexCache] = None) -> _ModelIndex:
    """Index one session: the transcript the hook recorded, plus its subagents.

    The subagent tree is found by name — ``<dir>/<session>/subagents/`` beside
    the main file — and walked whole, which covers both layouts the CLI writes
    (``agent-<id>.jsonl`` directly, and ``workflows/wf_*/agent-<id>.jsonl``). A
    file that is not there costs nothing; a layout that changes again costs one
    unattributed session, never a wrong attribution.

    ``cache`` is shared by the caller across every session in one harvest,
    because it is one file on disk and reading and writing it once per harvest
    is the point. Called without one, this loads and saves its own.
    """
    if not transcript:
        return _EMPTY_INDEX
    own = cache is None
    if cache is None:
        cache = _IndexCache()
    main = Path(transcript)
    files = [main] if main.is_file() else []
    files.extend(_transcript_files([main.parent / main.stem / "subagents"]))
    if not files:
        return _EMPTY_INDEX
    by_id: Dict[str, str] = {}
    timeline: List[Tuple[str, str]] = []
    for file in files:
        index = cache.index(file)
        by_id.update(index.by_id)  # ids are unique per file, so this cannot clash
        timeline.extend(index.timeline)
    if own:
        cache.save()
    timeline.sort()
    return _ModelIndex(by_id, timeline)


# --- inputs ------------------------------------------------------------------
#
# A raw occurrence is (occurrence key, program, argv_tail, source, session, ts,
# model). The model is LAST because :func:`_aggregate` indexes this tuple
# positionally and inserting a field renumbers every one of those reads.
# The occurrence key is what makes a count stable: the log is append-only, so a
# line number does not move, and a transcript's tool_use id never changes. The
# key is namespaced by SESSION because the scope of a line number is one log in
# one container — `#12` alone repeats everywhere. That namespacing is also what
# lets a session's live log and its own published file be read in the same
# harvest without counting one invocation twice.

_Raw = Tuple[str, str, List[str], str, Optional[str], str, Optional[str]]


def _read_text(path: Path) -> str:
    try:
        if not path.is_file():
            return ""
        with open(str(path), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _decode_log(text: str) -> List[Tuple[int, Dict[str, Any]]]:
    """The log as (line number, record). The line number is half the occurrence
    key, so it is carried rather than recomputed: the log is append-only, so a
    line number never moves."""
    out: List[Tuple[int, Dict[str, Any]]] = []
    for n, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue  # a half-written append is a lost sighting, not a crash
        if isinstance(rec, dict):
            out.append((n, rec))
    return out


def _raws_from_log(text: str) -> List[_Raw]:
    records = _decode_log(text)

    # The transcripts are read ONCE per distinct path, before the loop that
    # needs them: a session of several hundred log lines must not re-index a
    # multi-megabyte transcript once per line. Only the hook feed records a
    # transcript path, so the map from session to path is built from the hook
    # records and then used by the shim ones — which carry a session and a
    # timestamp and nothing else. A session that appears only through the shim
    # has no path, no index, and no model, which is the correct answer.
    #
    # And only the paths something actually asks about are read. This runs on
    # every Stop, and a session's transcript tree is tens of megabytes: indexing
    # one for a log whose records carry no id and no shim invocation would cost
    # most of a second per turn boundary and answer no question. A log written
    # by a version that did not record `tool_use_id` therefore costs exactly
    # what it cost before — not even the index cache is opened. The paths that
    # ARE asked about are read incrementally, from where the last harvest
    # stopped; see the cache above.
    by_session: Dict[str, str] = {}
    needed: Set[str] = set()
    shim_sessions: Set[str] = set()
    for _, rec in records:
        session = rec.get("session")
        session = session if isinstance(session, str) and session else None
        transcript = rec.get("transcript")
        if isinstance(transcript, str) and transcript:
            if session:
                by_session.setdefault(session, transcript)
            if isinstance(rec.get("tool_use_id"), str) and rec.get("tool_use_id"):
                needed.add(transcript)
        elif session and rec.get("kind") == "python_invocation":
            shim_sessions.add(session)
    for session in shim_sessions:
        transcript = by_session.get(session)
        if transcript:
            needed.add(transcript)
    indexes: Dict[str, _ModelIndex] = {}
    if needed:
        # One load and at most one store of the index cache per harvest, not
        # per session: it is a single file, and every session in this log
        # reads and updates the same one.
        cache = _IndexCache()
        indexes = {t: _model_index(t, cache) for t in sorted(needed)}
        cache.save()

    out: List[_Raw] = []
    for n, rec in records:
        session = rec.get("session")
        session = session if isinstance(session, str) and session else None
        ts = rec.get("ts") if isinstance(rec.get("ts"), str) else ""
        tag = session or "invocations"
        kind = rec.get("kind")
        transcript = rec.get("transcript")
        if not isinstance(transcript, str) or not transcript:
            # The shim writes no transcript path; its session is the only way
            # back to one, and it only leads anywhere if a hook record in the
            # same log named it.
            transcript = by_session.get(session or "", "")
        index = indexes.get(transcript, _EMPTY_INDEX)
        if kind == "python_invocation":
            program = rec.get("program")
            if not isinstance(program, str) or not program.strip():
                continue  # `python script.py`, `-m json.tool`, `--version`
            argv = rec.get("argv_tail")
            tail = [str(a) for a in argv] if isinstance(argv, list) else []
            # No id exists for a nested spawn — the hook never saw it — so this
            # is the time join, and it is the weaker of the two.
            model = index.at(ts)
            out.append(("shim:{0}#{1}".format(tag, n), program, tail, "shim", session, ts, model))
        elif kind == "bash_command" and isinstance(rec.get("command"), str):
            tool_use_id = rec.get("tool_use_id")
            # The exact join, and only the exact join: a log line written before
            # the id was captured stays unattributed rather than being handed
            # the model that happened to be speaking at the time.
            model = index.for_id(tool_use_id if isinstance(tool_use_id, str) else None)
            # One command, one model: every program extracted from it was typed
            # by whoever typed the command.
            for idx, (program, tail) in enumerate(extract_with_tails(rec["command"])):
                out.append(("hook:{0}#{1}#{2}".format(tag, n, idx), program, tail, "hook",
                            session, ts, model))
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
        # One vote per distinct occurrence, and none at all from an occurrence
        # whose model could not be resolved: this histogram is a subset of
        # `count`, never a partition of it.
        models: Dict[str, int] = {}
        for r in occ:
            if r[6]:
                models[r[6]] = models.get(r[6], 0) + 1
        out.append(Sighting(
            key=key,
            program=occ[0][1],
            argv_tail=tuple(tail),
            source=source,
            session=occ[0][4],
            first_seen=min(stamps) if stamps else "",
            count=len(occ),
            models=tuple(sorted(models.items())),
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
            # No join at all on this feed: the record that holds the tool_use
            # block is the record that names the model that emitted it.
            model = _model_of(message)
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
                        program, tail, "transcript", session, ts, model,
                    ))
    return _aggregate(raws)


def host_counts(log: Optional[Path] = None) -> Dict[str, Dict[str, int]]:
    """How many records each harness put in the log, by record kind.

    The numerator of the question this package cannot answer from priors: how
    many python one-liners a given harness actually types. It is deliberately
    NOT part of the corpus path — :func:`_raws_from_log` does not read ``host``
    and :class:`Sighting` does not carry it — because a measurement that
    changed what gets published would be a measurement nobody could trust.

    The denominator is not here either. Logging every tool call would put shell
    history with nothing to do with python into a log that gets published; the
    opt-in ``LYPNING_CAPTURE_CALLS=1`` records a bare ``tool_call`` with no
    command text for anyone who wants a rate.
    """
    path = Path(log) if log is not None else paths.log_path()
    out = {}  # type: Dict[str, Dict[str, int]]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue  # a half-written append is a lost sighting, not a crash
        if not isinstance(rec, dict):
            continue
        host = rec.get("host")
        host = host if isinstance(host, str) and host else "unknown"
        kind = rec.get("kind")
        kind = kind if isinstance(kind, str) and kind else "unknown"
        out.setdefault(host, {})
        out[host][kind] = out[host].get(kind, 0) + 1
    return out


# --- publishing --------------------------------------------------------------


def session_id() -> str:
    """This session, as far as the environment will say.

    Reads :data:`lypning.capture.SESSION_ENV` so the export tags a session the
    same way both feeds did — one list, one answer, whichever harness is host.
    """
    from . import capture

    return (capture.session_env() or "").strip()


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


def _count_at_least_the_models(count: int, models: corpus.Models) -> int:
    """``count``, raised to whatever the model histogram can already prove.

    ``sum(models) <= count`` is the promise every record makes to its readers:
    ``count - sum(models)`` is the unattributed hole, and a negative hole is not
    a hole. The per-key max in the two merges below cannot keep that promise on
    its own. It is bounded by a scalar max of the counts only when both sides
    carry the SAME model keys — and two sessions that ran one program under two
    different models carry disjoint ones, which is not a corner case but the
    ordinary shape of a corpus that outlives a model release.

    When the keys are disjoint the occurrence sets behind them are disjoint too,
    which means the scalar max was UNDERCOUNTING: each side simply never saw the
    other's runs, and taking the larger of two partial views throws away the
    evidence in the smaller one. So this is a CORRECTION, not a fudge — where
    the histogram accounts for more occurrences than the count does, the
    histogram is the better evidence and the count follows it up.

    It preserves everything the merges are built on. Idempotent: merging a
    record with itself re-derives the same sum, which the max absorbs.
    Commutative: neither argument is privileged. And silent on every record
    captured before models existed — an empty histogram sums to zero and can
    raise nothing, so no committed line moves.
    """
    return max(count, sum(n for _, n in models))


def _combine(old: Sighting, new: Sighting) -> Sighting:
    """Merge two records of one program. Idempotent in both directions.

    ``count`` is max, not sum: the counts on both sides are derived from the
    same occurrence keys, so summing them would double the record every time the
    export ran. Max also means a rotated-away log never shrinks a published
    count. The stored text and argv win, so a hand-corrected record survives.

    ``models`` follows ``count`` exactly, per model, and for the same reason —
    :func:`corpus._combine` sums the same-looking field because a corpus merge
    IS summation, and copying that call here would reintroduce the doubling on
    every export. The two are deliberately not the same line of code.

    The two maxes are then reconciled by :func:`_count_at_least_the_models`,
    which is where the pair stops being able to disagree.
    """
    stamps = [t for t in (old.first_seen, new.first_seen) if t]
    extra = dict(new.extra)
    extra.update(old.extra)  # the published record's own unknown keys win
    models = corpus.merge_models(old.models, new.models, max)
    return replace(
        old,
        models=models,
        extra=extra,
        source=old.source if SOURCE_RANK.get(old.source, 0) >= SOURCE_RANK.get(new.source, 0) else new.source,
        session=old.session or new.session,
        first_seen=min(stamps) if stamps else "",
        count=_count_at_least_the_models(max(old.count, new.count), models),
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
        # Max, like the count beside it and unlike corpus._combine: this fold
        # re-reads the same sightings on every run, so a sum here would inflate
        # the corpus a little more each time. And the same reconciliation as in
        # :func:`_combine`, for the same reason: a per-key max over two disjoint
        # key sets can out-count a scalar max of the counts, and the record that
        # went out with sum(models) > count would tell its readers that its
        # unattributed hole was negative.
        models = corpus.merge_models(cur.models, entry.models, max)
        by_id[entry.id] = replace(
            cur,
            source=cur.source if SOURCE_RANK.get(cur.source, 0) >= SOURCE_RANK.get(entry.source, 0) else entry.source,
            first_seen=min(stamps) if stamps else "",
            count=_count_at_least_the_models(max(cur.count, entry.count), models),
            models=models,
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
