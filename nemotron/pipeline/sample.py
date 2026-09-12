"""Build SFT targets the only way that can be trusted here: by execution.

THE PROBLEM THIS SOLVES. The corpus has 175 training cases and not one reference
solution — a tier-1 rewrite is exactly what nobody has written. You cannot
supervise on targets you do not have, and a target written by a judge model
would be graded by the same kind of thing that wrote it.

So the targets are *sampled and verified*: draw k completions from the base model
at temperature, run each through the real acceptance test, and keep what passes.
Every training example is then a program that provably reproduces CPython's
output and provably runs on the engine. On-policy, verified by execution, and
free of any grader.

The method also scopes the corpus for free. A case with no tier-1 solution yields
nothing however hard you sample, so it simply does not enter training — which
settles by measurement the question of which refusals are fair targets and which
are engine coverage gaps.

TWO GUARDS, both of which this would be worthless without.

*Contamination.* The held-out split is frozen and must never be sampled from.
:func:`train_cases` reads the lock and asserts disjointness; a held-out id
reaching the sampler raises rather than warns.

*The degenerate solution.* The acceptance test asks for byte-identical stdout, so
`print("<the expected output>")` passes it. Rejection sampling would happily
accept that and then teach the model to do it.

DISCRIMINATION, AND WHAT IT CANNOT DO. Telling "computed the answer" from
"recited the answer" is not a question about a program's text, and a guard that
asks it syntactically gets it wrong in both directions. This corpus's tasks hand
the model a correct program and ask for it back without one construct, so a
faithful rewrite quotes the original's literals by construction; asking "does a
line of the expected output appear in the source" then convicts the honest
program — a rewrapping of a string the prompt supplied is built out of that
string. Over the recorded eval attempts that question fired ten times: once on a
recitation, and nine times on hand-written reimplementations of `textwrap.fill`.

:func:`discriminate` asks a behavioural question instead. Where a case has an
input, mutate it, run the *original* program under CPython for the new answer,
and run the candidate: a program that computes follows the input, and a program
that recites keeps printing what it memorized. That is proof, not evidence.

Where a case has no input at all — `print(datetime.date(2026, 8, 13).isoformat())`
— there is nothing to perturb, and no test whatever separates computing from
printing the constant: the two programs are the same function. That is recorded
as ``undecidable`` rather than guessed at, with the textual evidence
(:func:`carried`, which asks the answerable direction — is this *literal* a
verbatim run of the answer, counterweighted by what the prompt already gave)
reported alongside so the caller can decide. :func:`fold_draws` decides one way
— it drops a draw whose source spells out the answer — and every such decision is
written down and counted, because a high count is a finding about the corpus.
"""

from __future__ import annotations

import ast
import base64 as _b64
import binascii as _binascii
import concurrent.futures
import datetime as _dt
import re as _re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import split as splitmod
from .acceptance import NORMALIZERS, run_test
from .backends import BackendError, ChatBackend
from .evaluate import render_messages
from .extract import extract_program
from .jsonio import append_jsonl, read_jsonl, write_json, write_jsonl
from .sandbox import DEFAULT_TIMEOUT_S, RunResult, run_python

# ------------------------------------------------------------ discrimination
#
# A carried run shorter than this is as likely to be coincidence as evidence:
# every program contains "0" and "1" somewhere.
_CARRY_MIN_RUN = 3

# Above this share of the answer carried as data, the program is reciting. Half
# is not a tuning knob: below it the program still had to compute the rest.
_RECITE_FRACTION = 0.5

# Folding stops here. A carrier longer than any plausible answer is not worth
# materializing, and `"x" * 10**9` in a program we fold is a denial of service
# on ourselves.
_FOLD_LIMIT = 65536


def _fold(node: ast.AST) -> Optional[Tuple[str, str]]:
    """``(kind, text)`` for an expression that only spells literals, else None.

    ``kind`` is ``"s"`` for a string and ``"n"`` for a number, because the two
    compose differently: strings concatenate, numbers add. Arithmetic is
    deliberately not folded — `6*7` is what computing looks like, and a folder
    that evaluated it would charge `print(6*7)` with hiding a 42. What *is*
    folded is re-spelling: concatenation, repetition, join, `repr` or `str` of a
    literal, an f-string with no substitutions. Those change how the answer is
    written down without changing that it was written down.
    """
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, str):
            return ("s", value)
        if value is True or value is False or value is None:
            return ("n", str(value))
        if isinstance(value, (int, float)):
            return ("n", repr(value))
        return None
    if isinstance(node, ast.JoinedStr):
        parts: List[str] = []
        for item in node.values:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None             # a substitution: something was computed
            parts.append(item.value)
        return _as_str("".join(parts))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold(node.left), _fold(node.right)
        if left is None or right is None or left[0] != "s" or right[0] != "s":
            return None
        return _as_str(left[1] + right[1])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        for text_node, count_node in ((node.left, node.right), (node.right, node.left)):
            folded = _fold(text_node)
            if folded is None or folded[0] != "s":
                continue
            if (isinstance(count_node, ast.Constant)
                    and isinstance(count_node.value, int)
                    and not isinstance(count_node.value, bool)
                    and 0 < count_node.value
                    and len(folded[1]) * count_node.value <= _FOLD_LIMIT):
                return _as_str(folded[1] * count_node.value)
        return None
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("repr", "str", "ascii")
            and len(node.args) == 1 and not node.keywords):
        folded = _fold(node.args[0])
        if folded is None:
            return None
        if folded[0] == "n" or node.func.id == "str":
            return _as_str(folded[1])
        return _as_str(repr(folded[1]) if node.func.id == "repr" else ascii(folded[1]))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join" and len(node.args) == 1 and not node.keywords):
        sep = _fold(node.func.value)
        if sep is None or sep[0] != "s":
            return None
        arg = node.args[0]
        if not isinstance(arg, (ast.List, ast.Tuple, ast.Set)):
            return None
        parts = []
        for element in arg.elts:
            folded = _fold(element)
            if folded is None or folded[0] != "s":
                return None
            parts.append(folded[1])
        return _as_str(sep[1].join(parts))
    return None


def _as_str(text: str) -> Optional[Tuple[str, str]]:
    return None if len(text) > _FOLD_LIMIT else ("s", text)


def carriers(program: str) -> List[str]:
    """Every string this program's source spells out, re-spellings folded.

    A docstring is skipped: it is the one string a program states without
    claiming anything about it.
    """
    try:
        tree = ast.parse(program)
    except (SyntaxError, ValueError):
        return []
    skip = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            skip.add(id(body[0].value))
    found: List[str] = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        folded = _fold(node)
        if folded is not None and folded[1]:
            found.append(folded[1])
    return found


def _given_text(prompt: str) -> str:
    """What the task handed over, with source escapes read as the bytes they are.

    A prompt quotes a program, so a newline inside one of its string literals
    reaches the model as the two characters ``\\n``. A rewrite that keeps that
    literal writes the newline, and without this the guard would charge it with
    smuggling something the task supplied.
    """
    unescaped = prompt
    for source, actual in (("\\n", "\n"), ("\\t", "\t"), ("\\r", "\r"),
                           ('\\"', '"'), ("\\'", "'"), ("\\\\", "\\")):
        unescaped = unescaped.replace(source, actual)
    return prompt + "\n" + unescaped


_FENCE = _re.compile(r"```(?:python)?\n(.*?)```", _re.S)


def _given_pieces(prompt: str) -> "frozenset":
    """The literals the task's own program spells out."""
    pieces = set()
    for block in _FENCE.findall(prompt or ""):
        try:
            tree = ast.parse(block)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                folded = _fold(node)
                if folded is not None and folded[1]:
                    pieces.add(folded[1])
    return frozenset(pieces)


def _assemblable(text: str, pieces: "frozenset") -> bool:
    """Is this string just the task's own literals, put next to each other?

    `print("<a>")` is the honest rewrite of a context manager that prints
    ``"<" + name + ">"`` for ``name = "a"``: the model folded the task's own
    literals, which is the rewrite, not a recitation. Whitespace is free because
    it is `print`'s, not the model's. What this cannot be talked into is
    assembling `2026-08-13` out of `2026`, `8` and `13` — the separators were
    never handed over, and the answer is the arrangement.
    """
    if not pieces or len(text) > _FOLD_LIMIT:
        return False
    reach = [False] * (len(text) + 1)
    reach[0] = True
    for i in range(len(text)):
        if not reach[i]:
            continue
        if text[i].isspace():
            reach[i + 1] = True
        for piece in pieces:
            if text.startswith(piece, i):
                reach[i + len(piece)] = True
    return reach[len(text)]


def carried(program: str, expected_stdout: str, prompt: str = "") -> Tuple[float, str]:
    """How much of the answer this program carries as data, and what the worst is.

    The question is asked in the direction that can be answered. Not "does a line
    of the output appear in the source": a program that rewraps a string contains
    that string, and every line of its output is a piece of it, so that question
    convicts the honest rewrite. The answerable direction is "is this literal a
    verbatim run of the answer" — a literal that is one was not computed; it was
    written down.

    The counterweight is the prompt. This corpus hands the model a program and
    asks for it back without one construct, so the literals already standing in
    the task are the model's to keep: printing a string the prompt gave is a
    faithful rewrite. Without a prompt there is no way to tell those apart, so
    only a whole answer carried as one datum is charged.
    """
    want = expected_stdout or ""
    body = want.rstrip("\n")
    if not body:
        return 0.0, ""
    given = _given_text(prompt)
    pieces = _given_pieces(prompt)
    mask = bytearray(len(want))
    charged: List[str] = []
    # Only what the program spells in full. `"ab" + "cd"` folds to three runs of
    # the answer, and charging the two halves as well as the whole would make
    # the same recitation cost more for having been typed differently.
    runs = set(t for t in carriers(program) if len(t) >= _CARRY_MIN_RUN and t in want)
    for text in sorted(runs, key=len, reverse=True):
        if any(text != other and text in other for other in runs):
            continue
        if prompt and (text in given or text.strip() in given
                       or _assemblable(text, pieces)):
            continue                    # the task handed this over
        if not prompt and text.strip() != body.strip():
            continue                    # no counterweight: only total recitation
        start = 0
        while True:
            at = want.find(text, start)
            if at < 0:
                break
            for i in range(at, at + len(text)):
                mask[i] = 1
            start = at + 1
        charged.append(text)
    covered = sum(mask)
    if not covered or not charged:
        return 0.0, ""
    fraction = min(1.0, covered / float(len(body)))
    worst = max(charged, key=len)
    return fraction, "%d%% of the answer is spelled in the source (%r%s)" % (
        round(100 * fraction), worst[:60], "..." if len(worst) > 60 else "")


def looks_like_literal_output(program: str, expected_stdout: str,
                              prompt: str = "") -> str:
    """Why this completion recites the answer rather than computing it, or "".

    Textual evidence, and evidence is all it is: :func:`discriminate` is the
    behavioural test, and it is proof wherever the case has an input to perturb.
    """
    fraction, detail = carried(program, expected_stdout, prompt)
    return detail if fraction >= _RECITE_FRACTION else ""


# The mutations tried, in order, until one changes what the original program
# prints. Rotating within a character class keeps the input's *shape* — a digit
# stays a digit, a word stays a word — so the original program still parses it;
# a mutation it cannot read proves nothing and is discarded, not charged.
_MUTATIONS = ("digits", "words", "letters", "duplicate-line", "bytes")

_TOKEN = _re.compile(r"[A-Za-z]+|[0-9]+")


@dataclass
class Discrimination:
    """Whether this program computed the answer, recited it, or cannot be told."""

    verdict: str          # "computes" | "recites" | "undecidable"
    method: str           # how it was decided
    detail: str = ""
    carried: float = 0.0  # share of the answer spelled in the source, always measured


def _rotate(text: str, digits: bool, letters: bool, protect: "frozenset") -> str:
    def one(match) -> str:
        token = match.group(0)
        if token in protect:
            return token            # a name the program asks for, not data
        if token[0].isdigit():
            return "".join(str((int(c) + 1) % 10) for c in token) if digits else token
        if not letters:
            return token
        out = []
        for ch in token:
            base = 97 if ch.islower() else 65
            out.append(chr((ord(ch) - base + 1) % 26 + base))
        return "".join(out)
    return _TOKEN.sub(one, text)


def _duplicate_line(text: str) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip():
            return "\n".join(lines[:i + 1] + [line] + lines[i + 1:])
    return text


def _bump_bytes(content: Dict[str, Any]) -> Dict[str, Any]:
    try:
        raw = bytearray(_b64.b64decode(content["base64"], validate=True))
    except (KeyError, ValueError, _binascii.Error):
        return content
    if not raw:
        return content
    raw[-1] = (raw[-1] + 1) % 256
    return {"base64": _b64.b64encode(bytes(raw)).decode("ascii")}


def _mutate_inputs(test: Dict[str, Any], mutation: str, original: str = "") -> Dict[str, Any]:
    """A changed input of the same shape.

    Shape is the point. A mutation the original program cannot read proves
    nothing, so digits stay digits and words stay words, and ``words`` leaves
    alone every token the original program's own source names — those are the
    schema it asks for (a JSON key, a flag), not the data it was given.
    """
    out = dict(test)
    if mutation == "duplicate-line":
        if test.get("stdin"):
            out["stdin"] = _duplicate_line(test["stdin"])
        return out
    if mutation == "bytes":
        files = test.get("files") or {}
        out["files"] = dict(
            (rel, _bump_bytes(content) if isinstance(content, dict) else content)
            for rel, content in files.items())
        return out

    digits = mutation in ("digits", "words")
    letters = mutation in ("letters", "words")
    protect = frozenset(_TOKEN.findall(original)) if mutation == "words" else frozenset()
    if test.get("stdin") is not None:
        out["stdin"] = _rotate(test["stdin"], digits, letters, protect)
    if test.get("argv"):
        out["argv"] = [_rotate(str(a), digits, letters, protect) for a in test["argv"]]
    files = test.get("files") or {}
    if files:
        # Binary files are left to the `bytes` mutation: rotating base64 text
        # makes a different file, not a perturbed one.
        out["files"] = dict(
            (rel, _rotate(content, digits, letters, protect) if isinstance(content, str)
             else content)
            for rel, content in files.items())
    return out


def _run_program(test: Dict[str, Any], program: str) -> RunResult:
    """Run under CPython with this test's inputs. Same sandbox as acceptance."""
    return run_python(
        program,
        argv=test.get("argv") or [],
        stdin=test.get("stdin"),
        files=test.get("files") or {},
        timeout_s=float(test.get("timeout_s", DEFAULT_TIMEOUT_S)),
        mem_mb=int(test.get("mem_mb", 1024)),
    )


def _original_program(case: Dict[str, Any]) -> Optional[str]:
    """The program the task is a rewrite of: a reference, or the refused original."""
    if case.get("reference"):
        return case["reference"]
    for neg in case.get("negatives") or []:
        if neg.get("program"):
            return neg["program"]
    return None


def perturbation(case: Dict[str, Any]) -> Dict[str, Any]:
    """A mutated input for this case and what the original program then prints.

    Costs at most one CPython run per mutation plus one, and depends only on the
    case, so compute it once per case and hand it to :func:`discriminate` for
    every draw.

    ``{"ok": False, "reason": ...}`` when this case cannot be perturbed — which
    is most of them, and is a fact about the corpus worth reporting rather than
    a failure. The reason is specific on purpose: "no input" is a property of the
    task, while "the original program does not reproduce the recorded answer" is
    a broken case and should be read as one.
    """
    test = case.get("test") or {}
    if "expect_stdout" not in test:
        return {"ok": False, "reason": "no expect_stdout to recompute"}
    if test.get("normalize", "exact") not in NORMALIZERS:
        return {"ok": False, "reason": "unknown normalize: %r" % test.get("normalize")}
    if not (test.get("stdin") or test.get("argv") or test.get("files")):
        return {"ok": False, "reason": "no input to perturb"}
    original = _original_program(case)
    if not original:
        return {"ok": False, "reason": "no original program to recompute the answer"}

    norm = NORMALIZERS[test.get("normalize", "exact")]
    want = norm(test["expect_stdout"])
    want_exit = test.get("expect_exit", 0)
    base = _run_program(test, original)
    if base.harness_error:
        return {"ok": False, "reason": "harness: %s" % base.harness_error}
    if base.timed_out or norm(base.stdout) != want:
        # The instrument has to be checked before it is used: an original that
        # does not reproduce the recorded answer cannot supply a new one.
        return {"ok": False,
                "reason": "the original program does not reproduce the recorded answer"}

    for mutation in _MUTATIONS:
        mutated = _mutate_inputs(test, mutation, original)
        r = _run_program(mutated, original)
        if r.harness_error or r.timed_out:
            continue
        if want_exit is not None and r.exit_code != want_exit:
            continue                    # the original cannot read this input
        if norm(r.stdout) == want:
            continue                    # nothing observable changed
        return {"ok": True, "mutation": mutation, "test": mutated,
                "expect_stdout": r.stdout, "was": test["expect_stdout"]}
    return {"ok": False, "reason": "no mutation changed the original's output"}


def discriminate(case: Dict[str, Any], program: str, *,
                 perturbed: Optional[Dict[str, Any]] = None) -> Discrimination:
    """Did this program compute the answer or recite it? See the module docstring.

    Proof first: where the case has an input, the mutated input decides and the
    textual evidence does not get a vote — that is what keeps an honest rewrite
    of a task whose output is a rearrangement of its own input from being
    convicted of quoting it.
    """
    test = case.get("test") or {}
    want = test.get("expect_stdout", "") or ""
    fraction, detail = carried(program, want, case.get("prompt", "") or "")

    if perturbed is None:
        perturbed = perturbation(case)
    if perturbed.get("ok"):
        norm = NORMALIZERS[test.get("normalize", "exact")]
        r = _run_program(perturbed["test"], program)
        if r.harness_error:
            return Discrimination("undecidable", "harness", r.harness_error, fraction)
        got = norm(r.stdout) if not r.timed_out else None
        if got == norm(perturbed["expect_stdout"]):
            return Discrimination("computes", "perturbation",
                                  "follows a %s-mutated input" % perturbed["mutation"],
                                  fraction)
        if got == norm(test.get("expect_stdout", "")):
            return Discrimination("recites", "perturbation",
                                  "prints the recorded answer for a %s-mutated input"
                                  % perturbed["mutation"], fraction)
        # It neither followed the input nor repeated itself. A program can be
        # wrong on an input it was never asked about without being a recitation,
        # so this proves nothing either way and must not be charged as if it did.
        return Discrimination("undecidable", "perturbation-inconclusive",
                              "diverges under a %s-mutated input" % perturbed["mutation"],
                              fraction)

    if fraction >= _RECITE_FRACTION:
        return Discrimination("recites", "carriage", detail, fraction)
    return Discrimination("undecidable", "no-perturbation",
                          perturbed.get("reason", ""), fraction)


def perturbations(cases: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """One perturbation per case, computed up front. Deterministic, no threads."""
    return dict((c["id"], perturbation(c)) for c in cases)


def train_cases(data_dir: Path, *, allow_leaks: bool = False) -> List[Dict[str, Any]]:
    """The training split, with every case that is a held-out case absent.

    THE OLD ASSERTION HERE WAS TAUTOLOGICAL: it filtered by held id and then
    asserted no held id had survived the filter, which is true however leaky the
    split is. It is kept below because the lock still has to be there, and it is
    no longer the check that matters.

    The check that matters is `splitmod.cross_split_leaks`. Disjoint ids do not
    make two cases independent: the corpus is capture-derived, so the same agent
    hitting the same wall twice produces two entries that differ in a variable
    name, and `freeze` puts one in each split. Measured 2026-09-12, 27 of 74
    held-out cases have a train neighbour at >= 0.85 prompt similarity and three
    rows of the first SFT set were verified solutions to held-out cases.

    `allow_leaks` exists for one purpose — the reporter that prints what is being
    dropped — and never for sampling.
    """
    lock = splitmod.load_lock(data_dir)
    if lock is None:
        raise ValueError("the split is not frozen; refusing to sample")
    held = {e["id"] for e in lock["holdout"]}
    cases = read_jsonl(data_dir / "corpus.jsonl")
    train = [c for c in cases if c["id"] not in held]
    leaked = held & {c["id"] for c in train}
    if leaked:
        raise AssertionError("held-out cases reached the sampler: %s" % sorted(leaked)[:5])
    if allow_leaks:
        return train
    holdout = [c for c in cases if c["id"] in held]
    report = splitmod.cross_split_leaks(train, holdout)
    drop = {row["id"] for row in report["leaks"]}
    return [c for c in train if c["id"] not in drop]


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sample_targets(
    backend: ChatBackend,
    cases: Sequence[Dict[str, Any]],
    out_dir: Path,
    *,
    k: int = 16,
    keep: int = 2,
    temperature: float = 1.0,
    top_p: float = 0.95,
    max_tokens: int = 2048,
    enable_thinking: bool = False,
    concurrency: int = 16,
    price_hour: float = 0.0,
    max_spend: float = 0.0,
    progress=None,
) -> Dict[str, Any]:
    """Draw k, verify each, and keep the ``keep`` that best survive discrimination."""
    out_dir.mkdir(parents=True, exist_ok=True)
    draws_path = out_dir / "draws.jsonl"
    # Up front and single-threaded: a perturbation depends only on the case, and
    # the run that proves it usable must not be paid for once per draw.
    perturbed = perturbations(cases)
    started = __import__("time").time()
    lock = threading.Lock()
    spend = [0.0]
    aborted: List[str] = []

    def budget() -> float:
        return spend[0] + price_hour * (__import__("time").time() - started) / 3600.0

    def one(job: Tuple[Dict[str, Any], int]) -> Optional[Dict[str, Any]]:
        case, idx = job
        if aborted:
            return None
        try:
            comp = backend.complete(
                render_messages(case), temperature=temperature, top_p=top_p,
                max_tokens=max_tokens, enable_thinking=enable_thinking, seed=1000 + idx,
            )
        except BackendError as exc:
            return {"case_id": case["id"], "draw": idx, "harness_error": str(exc)[:300]}
        program, how = extract_program(comp.text, comp.reasoning)
        rec: Dict[str, Any] = {
            "case_id": case["id"], "draw": idx, "how": how,
            "completion_tokens": comp.completion_tokens,
            "cost_usd": backend.cost(comp.prompt_tokens, comp.completion_tokens),
        }
        if program is None:
            return dict(rec, kept=False, reason="no-code")
        verdict = run_test(case["test"], program)
        if verdict.harness_error:
            return dict(rec, harness_error=verdict.harness_error)
        if not verdict.passed:
            return dict(rec, kept=False, reason=verdict.reason, program=program)
        disc = discriminate(case, program, perturbed=perturbed.get(case["id"]))
        rec = dict(rec, passed=True, program=program, discrimination=asdict(disc))
        if disc.verdict == "recites":
            return dict(rec, kept=False, reason="recites", detail=disc.detail)
        return dict(rec, kept=True, reason="pass")

    jobs = [(c, i) for c in cases for i in range(k)]
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for rec in pool.map(one, jobs):
            if rec is None:
                continue
            with lock:
                append_jsonl(draws_path, rec)
                spend[0] += rec.get("cost_usd", 0.0)
                done += 1
                if max_spend and budget() > max_spend and not aborted:
                    aborted.append("spend cap: $%.2f > $%.2f" % (budget(), max_spend))
            if progress:
                progress(done, len(jobs), rec)

    return fold_draws(out_dir, cases, k=k, keep=keep,
                      spend=round(budget(), 4), aborted=(aborted[0] if aborted else None))


def _passed_acceptance(draw: Dict[str, Any]) -> bool:
    """Did this draw reproduce CPython's answer? Kept or rejected only by the guard."""
    if draw.get("passed") or draw.get("kept"):
        return True
    # Draws recorded before the guard wrote `passed`: these two reasons are only
    # reachable after the acceptance test has already passed.
    return draw.get("reason") in ("literal-output", "recites") and bool(draw.get("program"))


def _rank(draw: Dict[str, Any], fraction: float) -> Tuple[int, float, int, str]:
    """Order the passing draws for this case: proof, then evidence, then length.

    Length is last and only breaks ties, because selecting the shortest passing
    program is a cheat-seeking rule: wherever the answer can be recited, the
    recitation is the shortest program that passes. Within one evidence class it
    is the right tiebreak — the short program is the one that used the
    substitution instead of working around it.
    """
    proven = 0 if (draw.get("discrimination") or {}).get("verdict") == "computes" else 1
    program = draw["program"]
    return (proven, round(fraction, 3), len(program), program)


def fold_draws(out_dir: Path, cases: Sequence[Dict[str, Any]], *,
               k: int, keep: int, spend: float = 0.0,
               aborted: Optional[str] = None) -> Dict[str, Any]:
    """Turn the draws into an SFT file and a yield report. Pure; safe to re-run.

    The textual guard is applied *here*, not baked into the draws file, so that
    re-folding re-decides with the current guard in both directions — a draw the
    sampler rejected is re-admitted if today's guard clears it. A behavioural
    verdict is not re-decided: it cost a subprocess and this function runs none.
    """
    by_id = {c["id"]: c for c in cases}
    draws = [d for d in read_jsonl(out_dir / "draws.jsonl") if not d.get("harness_error")]
    passing: Dict[str, List[Tuple[Tuple[int, float, int, str], Dict[str, Any]]]] = {}
    counts: Dict[str, List[int]] = {}
    reasons: Dict[str, int] = {}
    verdicts: Dict[str, int] = {}

    for d in draws:
        cid = d["case_id"]
        case = by_id.get(cid)
        if case is None:
            continue                    # a draw for a case this fold was not given
        c = counts.setdefault(cid, [0, 0])
        c[1] += 1
        if not (_passed_acceptance(d) and d.get("program")):
            reasons[d.get("reason", "?")] = reasons.get(d.get("reason", "?"), 0) + 1
            continue
        recorded = d.get("discrimination") or {}
        fraction, _ = carried(d["program"], case["test"].get("expect_stdout", ""),
                              case.get("prompt", "") or "")
        recites = fraction >= _RECITE_FRACTION or (
            recorded.get("verdict") == "recites" and recorded.get("method") == "perturbation")
        if recites:
            reasons["recites"] = reasons.get("recites", 0) + 1
            continue
        verdict = recorded.get("verdict") or "undecidable"
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        c[0] += 1
        passing.setdefault(cid, []).append((_rank(d, fraction), d))

    rows: List[Dict[str, Any]] = []
    for cid, ranked in sorted(passing.items()):
        case = by_id[cid]
        seen = set()
        for _, d in sorted(ranked, key=lambda item: item[0]):
            program = d["program"]
            if program in seen:
                continue
            seen.add(program)
            if len(seen) > keep:
                break
            rows.append({
                "case_id": cid,
                "category": case["category"],
                "messages": render_messages(case) + [
                    {"role": "assistant", "content": "```python\n%s\n```" % program.strip()}],
                "program": program,
                "verified": True,
                "discrimination": (d.get("discrimination") or {}).get("verdict", "undecidable"),
            })
    write_jsonl(out_dir / "sft.jsonl", rows)

    solved = [cid for cid, (p, _) in counts.items() if p > 0]
    undecidable = [r for r in rows if r["discrimination"] != "computes"]
    report = {
        "sampled_at": _now(), "k": k, "keep": keep,
        "cases": len(counts),
        "cases_with_a_verified_solution": len(solved),
        "yield_rate": (len(solved) / len(counts)) if counts else 0.0,
        "sft_examples": len(rows),
        "draws": len(draws),
        "draw_pass_rate": sum(p for p, _ in counts.values()) / max(1, len(draws)),
        "rejected_by_reason": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "discrimination": {
            "kept_draws_by_verdict": dict(sorted(verdicts.items(), key=lambda kv: -kv[1])),
            # Not a defect: most of this corpus is a closed program with nothing
            # to perturb, where reciting and computing are the same function and
            # only the textual guard has anything to say. Reported so that the
            # weight resting on it is visible rather than assumed.
            "sft_rows_on_textual_evidence_only": len(undecidable),
        },
        "spend_usd": spend,
        "aborted": aborted,
        "by_category": _by_category(by_id, counts),
    }
    write_json(out_dir / "sample.json", report)
    return report


def _by_category(by_id, counts) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for cid, (p, n) in counts.items():
        cat = by_id[cid]["category"] if cid in by_id else "?"
        e = out.setdefault(cat, {"cases": 0, "solved": 0})
        e["cases"] += 1
        if p > 0:
            e["solved"] += 1
    for e in out.values():
        e["yield"] = e["solved"] / e["cases"]
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["cases"]))
