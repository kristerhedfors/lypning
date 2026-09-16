"""Ask a model for a stdlib unit, and keep the evidence whether or not it worked.

This is the generation half of the stdlib corpus. :mod:`pipeline.stdlib` is the
judging half — it runs CPython and the engines and decides what is a row. Nothing
here decides anything about correctness: a candidate that leaves this module has
the right SHAPE and no more, and the verify stage is the only thing allowed to
say it is right.

Four rules shape the module.

**The prompt is the product.** A model told only "write pure-Python stdlib code"
reaches for a class, a generator, a decorator, ``str.translate`` and
``dict.fromkeys`` on its first try, and every one of those is an exit 90. So the
system prompt carries the measured subset rules, and it renders the engine names,
the variant capabilities and the closed list FROM
:mod:`lypning.engines` rather than spelling them — a prompt with a hand-typed
closed list is a prompt that drifts away from the engine the moment the engine
moves, and the drift is silent because a prompt has no test that fails.

**The closed list is a gate here too, not only advice.**
:data:`lypning.engines.ONLY_CPYTHON_REFUSALS` names the kinds where a
plausible-looking reimplementation delivers a silent wrong answer. The prompt
says so, and then :func:`stdlib.closed_kind_violation` is run over the candidate's
own ``# fills:`` header before the file is written, so a target the prompt failed
to deter costs one wasted completion rather than three verify spawns and a row
that must be caught later.

**Every draw is evidence.** ``training/DATA_PRODUCTION.md`` step 5 — "Keep raw
generation/verification evidence even when it contributes no training loss" — is
the reason ``draws.jsonl`` records the raw completion of every rollout, including
the ones that produced nothing. A rollout that contributed no candidate still
says what the model reached for, and that is the only measurement of whether the
prompt is working.

**Draws are a ceiling, not a count.** ``draws_per_target`` bounds the retries a
target may cost, and drawing stops at the first structurally valid candidate. A
target that lands on the first call costs one call; the budget the dispatcher
reads is a worst case, not a prediction.

Library code does not print (invariant 8): every function returns data and
``pipeline.cli`` renders it. No clock either — ``resolved_on`` is passed in, the
way ``first_seen`` is in :mod:`pipeline.stdlib`, so a plan written twice on the
same day is the same bytes twice and ``--fake`` is reproducible.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import (Any, Dict, Iterable, List, Mapping, Optional, Sequence,
                    Tuple)

from lypning import engines as lyp

from . import stdlib
from .backends import ChatBackend, Completion
from .extract import extract_program

# --- what is pinned, and when it was measured --------------------------------

#: Berget AI, OpenAI-compatible. Overridden by ``NTX_BASE_URL``.
DEFAULT_BASE_URL = "https://api.berget.ai/v1"

#: The fallback model, and the day the provider's listing said it was the newest
#: active GLM it hosts. Invariant 3: the number and the date travel together.
#: Resolution is LIVE — this is what is used when ``{base}/models`` cannot be
#: reached, and the plan records that it fell back.
PINNED_MODEL = "zai-org/GLM-5.3-Flash"
PINNED_ON = "2026-09-16"

#: Published price per 1M tokens on ``PINNED_ON``, in ``PINNED_PRICE_CURRENCY``.
#: A published price is not a bill: it does not include the provider's own
#: rounding, a minimum charge, or the tokens a retry spends.
PINNED_PRICE_IN = 0.25
PINNED_PRICE_OUT = 0.50
PINNED_PRICE_CURRENCY = "EUR"

#: Substring a model id must carry to be a candidate, matched case-insensitively.
MODEL_MATCH = "glm"

#: The producer stamped on anything the offline provider wrote. A row carrying
#: this id is a shape test, never corpus content.
FAKE_MODEL = "fake/stdlib-generate"

PLAN_SCHEMA = "lypning-stdlib-plan/1"
DRAW_SCHEMA = "lypning-stdlib-draw/1"
TARGETS_SCHEMA = "lypning-stdlib-targets/1"

#: One round. Repair exists to spend a recorded refusal line, not to loop until
#: something passes: a second round would be a search over the verifier, and a
#: unit found that way is a unit fitted to the checks. The workflow says so too.
REPAIR_ROUNDS = 1

DEFAULT_MAX_TOKENS = 6144
DEFAULT_DRAWS_PER_TARGET = 2
DEFAULT_BATCHES = 2
DEFAULT_TARGETS_PER_BATCH = 4
DEFAULT_TIMEOUT_S = 300.0

#: A model id is not a checkpoint. Recorded next to every draw so a corpus built
#: on two different days can be split by what answered, the same way
#: ``corpus.Entry.models`` records provenance rather than asserting identity.
PRODUCER_NOTE = "a hosted model id, not an immutable checkpoint revision"

#: Chars per token, for the budget arithmetic only. A rule of thumb, NOT a
#: tokenizer: the real count is the provider's, and it is recorded per draw.
CHARS_PER_TOKEN = 4.0

#: Why a draw produced no candidate. Generation's own vocabulary, deliberately
#: separate from :data:`pipeline.stdlib.DROP_REASONS` — those are verdicts about
#: a unit that exists, these are about a completion that did not become one.
DRAW_DROP_REASONS: Tuple[str, ...] = (
    "no-code", "malformed", "closed-kind", "duplicate", "provider-error",
)


class GenerateError(RuntimeError):
    """Ours or the provider's — never a verdict about a candidate."""


# --- targets -----------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """One CPython surface worth filling, with the weight that says why.

    ``programs``/``sightings``/``sole_gap`` are the census columns, and
    ``measured_on`` is the day they were counted. They are carried through to the
    plan so the dispatcher sees what the batch is buying, and they are never
    recomputed here — invariant 3: a remembered corpus number is quoted with the
    command and the date that produced it, and that pair lives in the targets
    file, not in this module.
    """

    slug: str
    module: str
    reference: str
    names: Tuple[str, ...]
    programs: int = 0
    sightings: int = 0
    sole_gap: int = 0
    measured_on: str = ""
    fillable: str = ""
    caution: str = ""
    #: Names already filled by a committed unit, filled in by :func:`diff_targets`.
    covered_names: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug, "module": self.module, "reference": self.reference,
            "names": list(self.names), "programs": self.programs,
            "sightings": self.sightings, "sole_gap": self.sole_gap,
            "measured_on": self.measured_on, "fillable": self.fillable,
            "caution": self.caution, "covered_names": list(self.covered_names),
        }


def target_from_dict(raw: Mapping[str, Any]) -> Target:
    names = tuple(str(n).strip() for n in (raw.get("names") or ()) if str(n).strip())
    slug = str(raw.get("slug") or "").strip()
    if not slug:
        raise GenerateError("a target has no slug: %s" % json.dumps(dict(raw))[:200])
    if not names:
        raise GenerateError("target %s lists no CPython names" % slug)
    return Target(
        slug=slug,
        module=str(raw.get("module") or "").strip(),
        reference=str(raw.get("reference") or "").strip(),
        names=names,
        programs=int(raw.get("programs") or 0),
        sightings=int(raw.get("sightings") or 0),
        sole_gap=int(raw.get("sole_gap") or 0),
        measured_on=str(raw.get("measured_on") or ""),
        fillable=str(raw.get("fillable") or ""),
        caution=str(raw.get("caution") or ""),
        covered_names=tuple(str(n) for n in (raw.get("covered_names") or ())),
    )


@dataclass(frozen=True)
class Targets:
    """The parsed targets file: what may be asked for, and what may never be."""

    targets: Tuple[Target, ...] = ()
    never: Tuple[Dict[str, Any], ...] = ()
    closed_kinds: Tuple[str, ...] = ()
    measured_on: str = ""
    census: Dict[str, Any] = field(default_factory=dict)


def load_targets(path: "Optional[str]" = None) -> Targets:
    """Parse the targets file, and check its closed list against the engine's.

    The file restates :data:`lypning.engines.ONLY_CPYTHON_REFUSALS` so a reader
    sees why each kind is off limits without holding two documents open. A
    restatement can drift, so it is CHECKED rather than trusted: a kind the
    engine added and the file does not name would otherwise become a target
    nobody excluded, which is the silent-wrong-answer failure the closed list
    exists to prevent. The mismatch is loud and stops the plan.
    """
    p = Path(path) if path else _default_targets_path()
    try:
        raw = json.loads(Path(p).read_text(encoding="utf-8"))
    except OSError as exc:
        raise GenerateError("cannot read targets %s: %s" % (p, exc)) from None
    except ValueError as exc:
        raise GenerateError("targets %s is not JSON: %s" % (p, exc)) from None
    if raw.get("schema") != TARGETS_SCHEMA:
        raise GenerateError("targets %s: schema is %r, wanted %r"
                            % (p, raw.get("schema"), TARGETS_SCHEMA))

    never = tuple(dict(n) for n in (raw.get("never_target") or ()))
    declared = tuple(str(k) for k in (raw.get("closed_kinds") or ()))
    engine_closed = frozenset(lyp.ONLY_CPYTHON_REFUSALS)
    extra = sorted(set(declared) - engine_closed)
    missing = sorted(engine_closed - set(declared))
    if extra or missing:
        raise GenerateError(
            "targets %s: its closed list is not the engine's. "
            "the engine names %s that the file does not; the file names %s that "
            "the engine does not. Fix the file — never the other way round "
            "(invariant 1)."
            % (p, ", ".join(missing) or "nothing", ", ".join(extra) or "nothing"))

    banned = set()
    for entry in never:
        for name in (entry.get("modules") or ()):
            banned.add(str(name))
    targets = []
    for item in (raw.get("targets") or ()):
        t = target_from_dict(item)
        if t.module in banned or t.reference in banned:
            raise GenerateError("targets %s: %s targets %s, which the same file "
                                "marks never-target" % (p, t.slug, t.module))
        targets.append(t)
    if not targets:
        raise GenerateError("targets %s lists no targets" % p)
    slugs = [t.slug for t in targets]
    dupes = sorted(set(s for s in slugs if slugs.count(s) > 1))
    if dupes:
        raise GenerateError("targets %s: duplicate slug(s) %s" % (p, ", ".join(dupes)))
    return Targets(tuple(targets), never, declared,
                   str(raw.get("measured_on") or ""), dict(raw.get("census") or {}))


def _default_targets_path() -> Path:
    return Path(__file__).resolve().parents[1] / "stdlib" / "targets.json"


def covered_names(units_dir: "Optional[str]") -> Dict[str, str]:
    """``cpython name -> unit name`` for every name a committed unit already fills.

    Read from the unit files themselves, not from a list kept beside them: the
    ``# fills:`` header is the one statement of what a unit covers, and a second
    copy would be a second thing to forget to update.

    A malformed file under ``units_dir`` is skipped rather than fatal. This
    function answers "what is already covered", and a file that does not parse
    covers nothing; it is the verify stage's job to be loud about it, and being
    loud twice would block a plan on a defect the plan cannot fix.
    """
    out: Dict[str, str] = {}
    if not units_dir:
        return out
    for p in stdlib.unit_paths(units_dir):
        try:
            unit = stdlib.parse_unit(str(p), p.read_text(encoding="utf-8"))
        except (stdlib.UnitError, OSError, UnicodeDecodeError):
            continue
        for name in unit.fills:
            out.setdefault(name, unit.name)
    return out


@dataclass(frozen=True)
class TargetDiff:
    """What is still open, and what a committed unit already answers."""

    todo: Tuple[Target, ...] = ()
    #: ``(slug, unit names that cover it)`` for a target with nothing left.
    done: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()


def diff_targets(targets: Sequence[Target], covered: Mapping[str, str]) -> TargetDiff:
    """Drop targets a committed unit already fills; narrow the ones half-filled.

    A target is done when every name it lists is already filled. A target with
    SOME names filled stays open with the filled ones recorded in
    ``covered_names``, because the prompt must name them: a model asked to fill
    ``itertools`` without being told ``islice`` is taken writes ``islice`` again,
    and the duplicate costs a completion and an assemble-time collision.
    """
    todo: List[Target] = []
    done: List[Tuple[str, Tuple[str, ...]]] = []
    for t in targets:
        hit = tuple(n for n in t.names if n in covered)
        if len(hit) == len(t.names):
            done.append((t.slug, tuple(sorted(set(covered[n] for n in hit)))))
            continue
        todo.append(Target(
            slug=t.slug, module=t.module, reference=t.reference, names=t.names,
            programs=t.programs, sightings=t.sightings, sole_gap=t.sole_gap,
            measured_on=t.measured_on, fillable=t.fillable, caution=t.caution,
            covered_names=hit))
    return TargetDiff(tuple(todo), tuple(done))


# --- resolving the model -----------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    """Which model answered the question "what is the newest active GLM".

    ``source`` is ``listing`` (the provider said so), ``env`` (``NTX_MODEL``
    overrode it), ``pinned`` (the listing was unreachable) or ``fake`` (offline).
    ``note`` is written into the plan and rendered by the CLI, so a run that fell
    back says so where the dispatcher reads it rather than only in a log.
    """

    model: str
    base_url: str
    resolved_on: str
    source: str
    note: str = ""
    candidates: Tuple[Dict[str, Any], ...] = ()
    excluded: Tuple[Dict[str, Any], ...] = ()


def _get_json(url: str, api_key: Optional[str], timeout_s: float) -> Dict[str, Any]:
    """One GET, no retries. The fallback is better than spending on a retry.

    :class:`pipeline.backends.ChatBackend` is the one HTTP client for GENERATION
    and is used for it — it is not reused here because it speaks exactly one
    verb on exactly one path (``POST {base}/chat/completions``) and a listing is
    neither. Its retry policy is also wrong for this call: a completion that
    fails has to be retried or the batch loses a rollout, whereas a listing that
    fails has a recorded, correct answer already — the pin — and retrying only
    delays it.
    """
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _model_date(entry: Mapping[str, Any]) -> str:
    """An ISO date for a model listing entry, or "" when it states none.

    Several spellings because the field is the provider's, not a standard:
    OpenAI's own listing carries ``created`` as epoch seconds and Berget adds a
    release date. ``time.gmtime`` here converts a number the provider sent; it
    never reads the clock.
    """
    for key in ("released_at", "release_date", "released", "created_at"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:10]
    created = entry.get("created")
    if isinstance(created, (int, float)) and created > 0:
        return time.strftime("%Y-%m-%d", time.gmtime(float(created)))
    return ""


def _inactive_reason(entry: Mapping[str, Any]) -> str:
    """Why this model may not be chosen, or "" when nothing says it may not.

    Absence is not deprecation: an entry that states no lifecycle at all is
    treated as active, because refusing everything unlabelled would make the
    resolver fall back on a listing that is perfectly fine.
    """
    for key in ("lifecycle_status", "status", "state", "lifecycle"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip().lower() not in ("", "active", "available", "ready", "ga"):
            return "%s=%s" % (key, value.strip())
    if entry.get("deprecated") is True:
        return "deprecated=true"
    eol = entry.get("end_of_life") or entry.get("eol") or entry.get("deprecation_date")
    if isinstance(eol, str) and eol.strip():
        # An end-of-life date at all is disqualifying, not only one in the past:
        # a model that retires during the run is not a model this corpus should
        # record as its producer.
        return "end_of_life=%s" % eol.strip()[:10]
    return ""


def resolve_model(base_url: str = "", api_key: Optional[str] = None,
                  resolved_on: str = "", offline: bool = False,
                  match: str = MODEL_MATCH, pinned: str = PINNED_MODEL,
                  override: Optional[str] = None,
                  timeout_s: float = 30.0) -> Resolution:
    """The newest active model whose id carries ``match``, asked of the provider.

    Live, and then recorded. The pin is a fallback and never the only path,
    because a hosted catalogue moves: the id pinned here was the newest active
    GLM on ``PINNED_ON``, and a run three months later that quietly used it would
    be recording a stale producer for every row it wrote.

    An explicit ``override`` (``NTX_MODEL`` or ``--model``) wins over both. It is
    an operator naming a model on purpose, and a resolver that overruled that
    would be a resolver the operator cannot steer.
    """
    base = (base_url or os.environ.get("NTX_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    if offline:
        return Resolution(model=FAKE_MODEL, base_url=base, resolved_on=resolved_on,
                          source="fake",
                          note="offline provider: nothing was fetched and nothing "
                               "was spent")
    if override:
        return Resolution(model=override, base_url=base, resolved_on=resolved_on,
                          source="env",
                          note="named explicitly; the provider listing was not "
                               "consulted")
    try:
        data = _get_json(base + "/models", api_key, timeout_s)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
            ValueError) as exc:
        return Resolution(
            model=pinned, base_url=base, resolved_on=resolved_on, source="pinned",
            note="%s/models unreachable (%s: %s) — fell back to the id pinned on "
                 "%s, which was the newest active %s the provider listed that day. "
                 "It may no longer be." % (base, type(exc).__name__,
                                           str(exc)[:160], PINNED_ON, match.upper()))

    entries = data.get("data") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return Resolution(model=pinned, base_url=base, resolved_on=resolved_on,
                          source="pinned",
                          note="%s/models answered without a 'data' list — fell "
                               "back to the id pinned on %s" % (base, PINNED_ON))

    candidates: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        mid = str(entry.get("id") or "")
        if match.lower() not in mid.lower():
            continue
        row = {"id": mid, "released": _model_date(entry)}
        reason = _inactive_reason(entry)
        if reason:
            row["excluded"] = reason
            excluded.append(row)
            continue
        candidates.append(row)

    if not candidates:
        return Resolution(
            model=pinned, base_url=base, resolved_on=resolved_on, source="pinned",
            note="%s/models listed no active model matching %r%s — fell back to "
                 "the id pinned on %s"
                 % (base, match,
                    " (%d excluded: %s)" % (len(excluded),
                                            ", ".join("%s %s" % (e["id"], e["excluded"])
                                                      for e in excluded[:4]))
                    if excluded else "",
                    PINNED_ON),
            excluded=tuple(excluded))

    # Newest first; the id breaks a tie so two models released the same day
    # resolve the same way on every run.
    candidates.sort(key=lambda r: (r["released"], r["id"]), reverse=True)
    chosen = candidates[0]
    note = "chosen from %d active match(es) at %s/models" % (len(candidates), base)
    if chosen["id"] != pinned:
        note += "; this is NOT the id pinned on %s (%s)" % (PINNED_ON, pinned)
    if not chosen["released"]:
        note += "; the listing stated no release date, so 'newest' fell back to id order"
    return Resolution(model=chosen["id"], base_url=base, resolved_on=resolved_on,
                      source="listing", note=note, candidates=tuple(candidates),
                      excluded=tuple(excluded))


# --- the plan ----------------------------------------------------------------


def split_batches(targets: Sequence[Target], batches: int,
                  per_batch: int) -> List[List[Target]]:
    """Deal ``per_batch`` targets into at most ``batches`` batches, in order.

    Dealt off the front rather than round-robin: the targets file is ordered by
    corpus weight, so the first batch is the one worth running when only one is
    run. ``batches * per_batch`` is the whole spend knob, and a short target list
    yields fewer batches rather than padding one.
    """
    if batches < 1 or per_batch < 1:
        raise GenerateError("batches and targets-per-batch must both be >= 1")
    out: List[List[Target]] = []
    i = 0
    while i < len(targets) and len(out) < batches:
        out.append(list(targets[i:i + per_batch]))
        i += per_batch
    return out


def estimate(n_targets: int, draws_per_target: int, max_tokens: int,
             prompt_chars: int, price_in: float, price_out: float,
             currency: str) -> Dict[str, Any]:
    """The worst-case token and money ceiling for a whole run.

    Every number here is a CEILING, and each one is worth saying what it is not:

    * ``calls`` assumes every target exhausts its draws. Drawing stops at the
      first valid candidate, so the real count is lower — usually much lower.
    * ``completion_tokens`` is ``max_tokens`` per call, which is what the request
      permits, not what the model emits.
    * ``prompt_tokens`` is characters over :data:`CHARS_PER_TOKEN`. A rule of
      thumb, not a tokenizer; the real per-call counts are recorded in the draws
      ledger by the provider, and those are the ones to quote afterwards.
    * ``cost`` is the published price on :data:`PINNED_ON` times those ceilings.
      A published price is not a bill: it excludes the provider's rounding, any
      minimum charge, and the tokens a transport retry spends inside
      ``ChatBackend``.
    * none of it bounds the repair stage, which is one further bounded round over
      whatever the verify stage dropped.
    """
    calls = max(0, n_targets) * max(0, draws_per_target)
    prompt_tokens = int(calls * prompt_chars / CHARS_PER_TOKEN)
    completion_tokens = calls * max(0, max_tokens)
    cost = (prompt_tokens * price_in + completion_tokens * price_out) / 1e6
    return {
        "calls_max": calls,
        "prompt_tokens_max": prompt_tokens,
        "completion_tokens_max": completion_tokens,
        "tokens_max": prompt_tokens + completion_tokens,
        "cost_max": round(cost, 4),
        "currency": currency,
        "price_in_per_m": price_in,
        "price_out_per_m": price_out,
        "priced_on": PINNED_ON,
        "note": "every figure is a ceiling: draws stop at the first valid "
                "candidate, max_tokens is what the request permits rather than "
                "what the model emits, prompt tokens are chars/%g and not a "
                "tokenizer count, and a published price is not a bill. The "
                "repair stage is one further bounded round and is not included."
                % CHARS_PER_TOKEN,
    }


def build_plan(targets: Targets, covered: Mapping[str, str], resolution: Resolution,
               resolved_on: str, batches: int = DEFAULT_BATCHES,
               per_batch: int = DEFAULT_TARGETS_PER_BATCH,
               draws_per_target: int = DEFAULT_DRAWS_PER_TARGET,
               max_tokens: int = DEFAULT_MAX_TOKENS,
               units_dir: str = "", targets_path: str = "",
               price_in: float = PINNED_PRICE_IN,
               price_out: float = PINNED_PRICE_OUT,
               currency: str = PINNED_PRICE_CURRENCY) -> Dict[str, Any]:
    """The plan JSON every later stage reads. Pure data, written by the CLI."""
    diff = diff_targets(targets.targets, covered)
    dealt = split_batches(diff.todo, batches, per_batch)
    planned = [t for group in dealt for t in group]
    prompt_chars = len(system_prompt())
    if planned:
        prompt_chars += max(len(target_prompt(t)) for t in planned)
    return {
        "schema": PLAN_SCHEMA,
        "model": resolution.model,
        "base_url": resolution.base_url,
        "resolved_on": resolved_on,
        "model_source": resolution.source,
        "model_note": resolution.note,
        "model_candidates": [dict(c) for c in resolution.candidates],
        "model_excluded": [dict(c) for c in resolution.excluded],
        "producer_note": PRODUCER_NOTE,
        "targets_file": targets_path,
        "units_dir": units_dir,
        "targets_measured_on": targets.measured_on,
        "census": dict(targets.census),
        "max_tokens": max_tokens,
        "draws_per_target": draws_per_target,
        "repair_rounds": REPAIR_ROUNDS,
        "batches": [{"batch": i, "targets": [t.to_dict() for t in group]}
                    for i, group in enumerate(dealt)],
        "already_covered": [{"slug": slug, "units": list(units)}
                            for slug, units in diff.done],
        "open_not_planned": [t.slug for t in diff.todo[len(planned):]],
        "budget": estimate(len(planned), draws_per_target, max_tokens, prompt_chars,
                           price_in, price_out, currency),
    }


def plan_batch(plan: Mapping[str, Any], batch: int) -> List[Target]:
    """The targets of one batch of a plan, or a loud error naming what exists."""
    if plan.get("schema") != PLAN_SCHEMA:
        raise GenerateError("not a plan: schema is %r" % (plan.get("schema"),))
    for group in (plan.get("batches") or ()):
        if int(group.get("batch", -1)) == int(batch):
            return [target_from_dict(t) for t in (group.get("targets") or ())]
    have = ", ".join(str(g.get("batch")) for g in (plan.get("batches") or ())) or "none"
    raise GenerateError("plan has no batch %s (it has: %s)" % (batch, have))


# --- the prompt --------------------------------------------------------------

#: Constructs both engines refuse, as ``kind: detail`` and the source that gets
#: it. Measured on 2026-09-16 against the binaries built that session; the engine
#: name is deliberately absent from every line, because each variant writes its
#: OWN name at the head of its refusal (invariant 9) and a literal here would be
#: a second, drifting copy of one of them.
_REFUSED = """\
    class Foo: ...                class: class definition
    yield / yield from            generator: yield expression
    @decorator                    decorator: decorated definition
    async def / await             async: async def
    (n := 5)                      walrus: assignment expression (:=)
    nonlocal                      nonlocal: nonlocal declaration
    from m import *               import: star import
    from . import x               import: relative import
    except*                       except-star: except* group
    del l[1:3]                    del: del of this target form
    def f(a, *, b)                kwonly: keyword-only parameters
    [0, *a, 3] in a display        unpack: * in a list display
    f'{x=}'                       fstring: self-documenting {x=} field
    1j                            complex: complex literal
    ... / Ellipsis                ellipsis: Ellipsis literal
    f.__name__ / __file__         dunder-attr / dunder-missing
    f.x = 1 on a function         setattr: assignment to .x on a function
    recursion deeper than 180     recursion: call depth beyond 180
    raise ValueError()            exception: ValueError() with no arguments
    raise RuntimeError('a', 2)    exception: RuntimeError() with 2 arguments
    eval / exec / compile         builtin: eval
    getattr hasattr setattr dir callable    builtin: getattr
    bytearray / memoryview        builtin: bytearray
    str.center()                  str-method: str.center()
    str.translate() / maketrans() str-method: str.translate()
    str.expandtabs()              str-method: str.expandtabs()
    str.isdecimal / isidentifier / isprintable / isascii / istitle
    dict.fromkeys()               dict-method: dict.fromkeys()
    bytes.fromhex()               bytes-method: bytes.fromhex()
    float.fromhex()               float-method: float.fromhex()
    x.__len__() and every explicit dunder call
    zip(..., strict=True)         argument: keyword strict
    str.format_map()              str-method: str.format_map()
    import <anything not served>  module: import <name>
      -> so `try: import x / except ImportError:` CANNOT be written
    import mylib (a local .py)    module: import mylib   <- NO local imports
    math.log and the transcendentals         module-attr: math.log
    sys.path / sys.version / sys.modules     module-attr: sys.path
    os.listdir(...) even inside sorted()     os-listdir
    os.environ (read, write or `in`)         environ
    random.random() unseeded                 random
    print of a set with more than one element    set-order
    Counter+Counter, Counter.elements, defaultdict(lambda: ...)
    re lookahead (?=...)          re: lookahead
    json.dumps(default=...)       json: json.dumps(default=...)
    Path.iterdir / Path.glob / Path.resolve  pathlib
    glob.glob() outside sorted()/len()       glob-order"""

_SUPPORTED = """\
def (nested, mutual recursion, posonly `a, /, b`, annotations), default args,
*args, **kwargs, f(*a, **k), multiple ** splats; lambda with defaults;
if/elif/else and ternaries; for, for/else, while, while/else, break, continue;
try/except/else/finally, bare except, `except (A, B) as e`, bare raise,
`raise X from Y`; `with open(...) as f` and multi-item with; assert; `del name`
and `del d[k]`; global; every import form for a SERVED module; star-unpacking in
assignment targets (`a, *b = seq`), in call args and in `{**d}`; chained and
augmented assignment; tuple unpacking; list/dict/set comprehensions including
nested ones, several `for`, several `if`, a ternary inside; generator
EXPRESSIONS passed to sum/any/all/sorted/max/min/list/join, and
`g = (x for x in r); next(g)`; closures that READ an enclosing scope; f-strings
including nesting, !r, {v:.2f}, {255:x}, {5:03d}; % formatting and .format();
slicing including [::-1] and slice ASSIGNMENT `l[0:2] = [9]`; bytes literals,
.hex(), .decode(), str.encode(); int/float arithmetic, //, %, **, bitwise,
chained comparisons, is, in; 1_000_000, 0xff, 0o17, 0b101; shadowing and binding
builtins (`f = len`, `a = l.append`, `str.upper` as a sorted() key);
sys.exit(n); input().

Working str methods: upper lower strip lstrip rstrip split rsplit splitlines
join replace find rfind index rindex startswith endswith count ljust rjust zfill
partition rpartition casefold title swapcase capitalize encode format isnumeric
islower isupper isalnum isalpha isdigit isspace removeprefix removesuffix."""

_IDIOMS = """\
NO CLASSES, so state is a dict or a list passed by reference:

    def reader_new(text):
        return {"text": text, "pos": 0}
    def reader_next(st):
        st["pos"] = st["pos"] + 1
        return st["text"][st["pos"] - 1]

NO nonlocal, so a closure cell is a one-element list:

    def outer():
        cell = [0]
        def bump():
            cell[0] = cell[0] + 1
        bump()
        return cell[0]

NO generators, so return a materialised list. NO recursion past 180 frames, so
every tree walk and divide-and-conquer carries an EXPLICIT stack.

NO custom exceptions (`class E(Exception)` refuses) and most builtin exception
names are not even bound. Raise `ValueError("message")` and nothing else: one
argument, non-empty."""


def engine_capabilities() -> str:
    """The two engines and what separates them, rendered from the engine itself.

    Read off :data:`lypning.engines.SPECTRUM` and
    :data:`lypning.engines.VARIANT_CAPS` so that a variant that gains a
    capability gains it in the prompt on the same commit. Typing the seven module
    names here would be a fourth place they live and the only one with no test.
    """
    lines: List[str] = []
    for name in lyp.SPECTRUM:
        caps = tuple(lyp.VARIANT_CAPS.get(name, ()))
        if not caps:
            lines.append("    %-12s the frozen core. Imports only sys, os, os.path, "
                         "posixpath, io, json, math, random; every integer stays "
                         "inside signed 64-bit." % name)
        else:
            extra = ", ".join(c[len("cap-"):] if c.startswith("cap-") else c
                              for c in caps)
            lines.append("    %-12s adds %s. Anything it adds costs a wider engine, "
                         "so reach for it only when the unit genuinely needs it."
                         % (name, extra))
    lines.append("    %-12s neither engine runs it. NOT a corpus row — a recorded "
                 "gap, and a wasted rollout." % lyp.CPYTHON)
    return "\n".join(lines)


def closed_list() -> str:
    """The closed kinds, rendered from :data:`lypning.engines.ONLY_CPYTHON_REFUSALS`."""
    kinds = sorted(lyp.ONLY_CPYTHON_REFUSALS)
    wrapped: List[str] = []
    line = "   "
    for kind in kinds:
        if len(line) + len(kind) + 2 > 76:
            wrapped.append(line.rstrip().rstrip(","))
            line = "   "
        line += " " + kind + ","
    if line.strip():
        wrapped.append(line.rstrip().rstrip(","))
    return "\n".join(wrapped)


def system_prompt() -> str:
    """The whole of what the model is told about the subset. The product.

    Assembled at call time rather than frozen into a constant so the rendered
    halves — the engine names, the variant capabilities, the closed list, the
    unit separator — come from the code that owns them on every call.
    """
    core = lyp.SPECTRUM[0]
    return """You write ONE self-contained Python file: a "unit" for a corpus of \
pure-Python replacements for CPython standard-library surfaces that a restricted \
Python engine refuses to run.

The engine is a real interpreter with a real, measured subset. A program outside \
that subset is REFUSED: it exits 90 and prints exactly one line on stderr shaped
`<engine>: unsupported: <kind>: <detail>` — for example the core engine answers
`%s: unsupported: class: class definition`. A refused unit is a wasted rollout.
Nothing you write may be outside the subset.

THE ENGINES

%s

REFUSED BY BOTH ENGINES. Do not write any of these — they were measured, not
guessed, on %s:

%s

`__name__` DOES work: `if __name__ == "__main__":` runs on both engines.

SUPPORTED, confirmed by 1,361 probes:

%s

THE TWO IDIOMS YOU WILL NEED CONSTANTLY

%s

THE CLOSED LIST — NEVER FILL THESE

These refusal kinds are ones where no reimplementation short of CPython is
right. A pure-Python `math.log`, a set-ordering helper or a JSON float repr looks
correct and delivers a silent wrong answer:

%s

A unit that targets one of them is REJECTED by an acceptance gate. If the surface
you were asked for can only be built out of one of these, say so in one sentence
INSTEAD of writing a file, and write no code block at all.

THE FILE FORMAT — exactly this, in this order

    \"\"\"One line naming the gap this fills.

    Prose: which CPython surface, what is covered, what deliberately is NOT,
    and any divergence from CPython that is accepted and why.
    \"\"\"
    # fills: <comma-separated dotted CPython names, non-empty>
    # reference: <one importable CPython module name, or - for a language gap>

    <helper function definitions — the inlinable part, definitions only>

    %s
    <statements that print deterministic values>

Rules that are checked mechanically:
  - `# fills:` and `# reference:` are required header comments, in that order,
    below the docstring and above the first definition.
  - `%s` appears EXACTLY once.
  - Above it: definitions only, no top-level side effects.
  - Below it: prints only, and the output must be byte-identical on every run on
    every machine. No clocks, no pids, no addresses, no unseeded random, no set
    iteration order, no filesystem order, no temp paths in the output.
  - The cases are the test. They are run on CPython and on each engine and the
    BYTES are compared, so print enough to catch a wrong answer: edge cases,
    empty inputs, boundary values, and `repr()` where whitespace matters.
  - Helper names should match CPython's where they can: `fill`, `wrap`, `dedent`
    read better than `tw_fill`. Private helpers start with `_`.
  - Nothing imports this file. It is reference material a model reproduces
    INLINE, so it must be complete on its own.

OUTPUT

Emit exactly ONE fenced python code block containing the whole file, and nothing
outside it — no preamble, no explanation, no second block.""" % (
        core, engine_capabilities(), PINNED_ON, _REFUSED, _SUPPORTED, _IDIOMS,
        closed_list(), stdlib.UNIT_SEPARATOR, stdlib.UNIT_SEPARATOR)


def target_prompt(target: Target) -> str:
    """What this one unit must fill, with the corpus weight that justifies it."""
    lines = ["Write the unit for: %s" % target.slug, ""]
    lines.append("CPython names to fill: %s" % ", ".join(target.names))
    if target.reference:
        lines.append("# reference: %s" % target.reference)
    else:
        lines.append("# reference: -   (this fills a language gap, not a module)")
    if target.covered_names:
        lines.append("")
        lines.append("ALREADY FILLED by another unit — do NOT write these again, and "
                     "do not list them in `# fills:`: %s"
                     % ", ".join(target.covered_names))
    if target.fillable:
        lines.append("")
        lines.append("What is reachable inside the subset: %s" % target.fillable)
    if target.caution:
        lines.append("")
        lines.append("CAUTION: %s" % target.caution)
    if target.measured_on:
        lines.append("")
        lines.append("Why this surface: %d corpus programs, %d sightings, %d "
                     "programs where it is the SOLE reason the engine refused "
                     "(measured %s)."
                     % (target.programs, target.sightings, target.sole_gap,
                        target.measured_on))
    lines.append("")
    lines.append("Cover what you can do exactly and say in the docstring what you "
                 "deliberately left out. A narrow unit that is byte-exact beats a "
                 "wide one that is nearly right: the cases are compared against "
                 "CPython byte for byte, and a single wrong byte rejects the whole "
                 "unit.")
    return "\n".join(lines)


def repair_prompt(target: Target, source: str, verdict: str,
                  refusals: Sequence[Tuple[str, str]] = ()) -> str:
    """Feed back the exact thing the engine or the oracle said, and nothing else.

    The recorded line IS the feedback. Paraphrasing it would teach the model a
    vocabulary the engine does not use, and the kind/detail pair is what
    ``conformance --plan`` is ordered by — the same words the rest of the project
    reasons in.
    """
    lines = ["This unit was REJECTED. Fix it and return the whole corrected file.",
             "", "What rejected it:", "    %s" % verdict]
    if refusals:
        lines.append("")
        lines.append("The engines answered, verbatim:")
        for engine, line in refusals:
            lines.append("    %-12s %s" % (engine, line))
        lines.append("")
        lines.append("A refusal names a construct you must not use. Replace it with "
                     "something in the supported list — do not work around it with "
                     "another refused construct.")
    lines.append("")
    lines.append("The names it must still fill: %s" % ", ".join(target.names))
    lines.append("")
    lines.append("The file as written:")
    lines.append("")
    lines.append("```python")
    lines.append(source.rstrip("\n"))
    lines.append("```")
    lines.append("")
    lines.append("Return one fenced python block with the whole corrected file and "
                 "nothing else.")
    return "\n".join(lines)


# --- providers ---------------------------------------------------------------


@dataclass
class FakeBackend:
    """A deterministic offline provider. No clock, no random, no network.

    It returns a canned, well-formed unit derived only from the target's slug, so
    the whole staged pipeline — generate, verify, repair, assemble — runs on a
    dispatch with no secret and no spend, and runs the SAME way twice. What it
    returns is deliberately not an answer to the target: it fills a name in its
    own namespace and its docstring says it is a stand-in, so a fake row can
    never be mistaken for corpus content. The dry run asserts on the shape of the
    pipeline, which is what a dry run can honestly check.
    """

    model: str = FAKE_MODEL
    base_url: str = "fake://offline"

    def identity(self) -> Dict[str, Any]:
        return {"base_url": self.base_url, "model": self.model}

    def complete(self, messages: Sequence[Mapping[str, str]], **kw: Any) -> Completion:
        user = ""
        for m in messages:
            if m.get("role") == "user":
                user = str(m.get("content") or "")
        slug = _slug_of_prompt(user)
        text = "```python\n" + fake_unit_source(slug) + "```\n"
        return Completion(
            text=text, reasoning=None,
            # Derived from the strings, not measured: a fake usage count that
            # moved between runs would make the ledger look like a live one.
            prompt_tokens=int(sum(len(str(m.get("content") or "")) for m in messages)
                              / CHARS_PER_TOKEN),
            completion_tokens=int(len(text) / CHARS_PER_TOKEN),
            latency_s=0.0, finish_reason="stop",
        )


def _slug_of_prompt(user: str) -> str:
    """The slug out of a target or repair prompt — the fake's only input."""
    for line in user.splitlines():
        if line.startswith("Write the unit for: "):
            return line[len("Write the unit for: "):].strip()
    return "offline"


def fake_unit_source(slug: str) -> str:
    """A well-formed unit that the core engine runs and CPython agrees with.

    Distinct per slug, so two fake candidates are two ids rather than one
    de-duplicated row — the assemble stage's de-duplication is one of the things
    a dry run is meant to exercise.
    """
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in slug) or "offline"
    return (
        '"""Offline stand-in for %s, written by the fake provider.\n'
        '\n'
        'This is what `--fake` produces: a file with the SHAPE of a unit and none\n'
        'of its content. It fills a name in its own namespace, it answers no\n'
        'CPython surface, and it exists so the staged pipeline can be exercised\n'
        'end to end with no secret and no spend. Never merge it.\n'
        '"""\n'
        '# fills: fake.%s\n'
        '# reference: -\n'
        '\n'
        '\n'
        'def _checksum(values):\n'
        '    """A deterministic fold. No clock, no random, no set iteration."""\n'
        '    total = 0\n'
        '    for v in values:\n'
        '        total = (total * 31 + v) %% 1000003\n'
        '    return total\n'
        '\n'
        '\n'
        'def roundtrip(text):\n'
        '    out = []\n'
        '    for ch in text:\n'
        '        out.append(ch)\n'
        '    return "".join(out)\n'
        '\n'
        '\n'
        '%s\n'
        'print(_checksum([1, 2, 3]))\n'
        'print(_checksum([ord(c) for c in "%s"]))\n'
        'print(roundtrip("%s"))\n'
        'print(len(roundtrip("")))\n'
    ) % (slug, safe, stdlib.UNIT_SEPARATOR, safe, safe)


def backend_for(plan: Mapping[str, Any], fake: bool = False,
                api_key: Optional[str] = None,
                timeout_s: float = DEFAULT_TIMEOUT_S) -> Any:
    """The provider this run talks to: the fake one, or the one HTTP client.

    ``ChatBackend`` is not re-implemented and not wrapped — it already holds the
    retry policy (429 and 5xx with jitter, 4xx returned immediately) and the
    stdlib-only ``urllib`` transport this package runs everywhere. The model and
    base url come from the PLAN rather than the environment, because the plan is
    where the resolution was recorded and a second reading of ``NTX_MODEL`` could
    answer differently than the run it is part of.
    """
    if fake:
        return FakeBackend()
    model = str(plan.get("model") or "")
    base = str(plan.get("base_url") or "")
    if not model or not base:
        raise GenerateError("the plan names no model or no base_url")
    return ChatBackend.from_env(base_url=base, model=model, api_key=api_key,
                                timeout_s=timeout_s)


# --- drawing -----------------------------------------------------------------


def _seed_for(slug: str, draw: int) -> int:
    """A stable seed per (target, draw). Reproducible where the server honours it.

    Advisory at a hosted provider and authoritative on vLLM/SGLang; either way it
    is recorded, so a draw that cannot be reproduced is one whose seed was
    ignored rather than one that was never seeded.
    """
    h = 0
    for ch in "%s#%d" % (slug, draw):
        h = (h * 1000003 + ord(ch)) & 0x7FFFFFFF
    return h


def _temperature_for(draw: int) -> float:
    """Near-greedy first, then wider. A retry at the same temperature is a re-run.

    Draw 0 is the cheap attempt and wants the model's best guess. A retry only
    helps if it samples somewhere else, so it does.
    """
    return 0.2 if draw == 0 else 0.8


@dataclass(frozen=True)
class Draw:
    """One rollout: what was asked, what came back, and what became of it."""

    row: Dict[str, Any]
    source: str = ""
    unit: Optional[stdlib.Unit] = None
    drop_reason: str = ""
    drop_detail: str = ""

    @property
    def kept(self) -> bool:
        return self.unit is not None and not self.drop_reason


@dataclass(frozen=True)
class Generation:
    """One batch's outcome. ``draws`` is the ledger; ``written`` is the candidates."""

    draws: Tuple[Dict[str, Any], ...] = ()
    written: Tuple[Tuple[str, str], ...] = ()   # (slug, path)
    #: ``(slug, reason, detail)`` for a target no draw answered.
    unanswered: Tuple[Tuple[str, str, str], ...] = ()
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _draw_row(batch: int, target: Target, draw: int, backend: Any, kind: str,
              max_tokens: int, temperature: float, seed: int) -> Dict[str, Any]:
    ident = backend.identity() if hasattr(backend, "identity") else {}
    return {
        "schema": DRAW_SCHEMA,
        "kind": kind,
        "batch": batch,
        "target": target.slug,
        "module": target.module,
        "draw": draw,
        "model": str(ident.get("model") or ""),
        "base_url": str(ident.get("base_url") or ""),
        "temperature": temperature,
        "seed": seed,
        "max_tokens": max_tokens,
        "finish_reason": "",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "latency_s": 0.0,
        "how": "",
        "kept": False,
        "drop_reason": "",
        "drop_detail": "",
        "candidate": "",
        "source_sha256": "",
        "completion": "",
        "reasoning": "",
    }


def _closed_gate(unit: stdlib.Unit) -> str:
    """The DECLARED half of the closed-kind gate, run before a file is written.

    :func:`pipeline.stdlib.closed_kind_violation` is the one implementation of
    this gate and it is called here with an empty label, which exercises exactly
    the half that needs no measurement: the leading segment of every
    ``# fills:`` name against the closed set. The MEASURED half — the refusal a
    naive ``import <reference>`` gets — needs an engine and belongs to the verify
    stage, which has one. Catching what can be caught here saves three spawns and
    a row that would have to be dropped later.
    """
    empty = stdlib.Label(requires="", requires_static="", route_agrees=False,
                         runs=(), mismatch="", naive_kind="", naive_detail="")
    return stdlib.closed_kind_violation(unit, empty, frozenset(lyp.ONLY_CPYTHON_REFUSALS))


def draw_candidate(backend: Any, target: Target, batch: int, draw: int,
                   out_dir: "Optional[str]", max_tokens: int,
                   messages: Optional[Sequence[Mapping[str, str]]] = None,
                   kind: str = "generate", seen: Optional[Mapping[str, str]] = None,
                   ) -> Draw:
    """One rollout, recorded whatever happens to it.

    A provider error is a recorded draw too, not an exception: a batch that lost
    one target to a 500 should still write the other three, and the ledger should
    say which one was lost and why. Only a malformed PLAN raises.
    """
    temperature = _temperature_for(draw)
    seed = _seed_for(target.slug, draw)
    row = _draw_row(batch, target, draw, backend, kind, max_tokens, temperature, seed)
    msgs = list(messages) if messages is not None else [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": target_prompt(target)},
    ]
    try:
        completion = backend.complete(msgs, temperature=temperature, top_p=0.95,
                                      max_tokens=max_tokens, seed=seed)
    except Exception as exc:                       # noqa: BLE001 — recorded, not raised
        row["drop_reason"] = "provider-error"
        row["drop_detail"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        return Draw(row=row, drop_reason="provider-error", drop_detail=row["drop_detail"])

    row["finish_reason"] = completion.finish_reason
    row["prompt_tokens"] = completion.prompt_tokens
    row["completion_tokens"] = completion.completion_tokens
    row["latency_s"] = round(completion.latency_s, 3)
    row["completion"] = completion.text
    row["reasoning"] = completion.reasoning or ""

    source, how = extract_program(completion.text, completion.reasoning)
    row["how"] = how
    if not source:
        row["drop_reason"] = "no-code"
        row["drop_detail"] = ("no fenced python block in the completion"
                              + ("; it was cut off at max_tokens"
                                 if completion.finish_reason == "length" else ""))
        return Draw(row=row, drop_reason="no-code", drop_detail=row["drop_detail"])
    source = source.rstrip("\n") + "\n"
    row["source_sha256"] = stdlib.source_sha256(source)

    path = str(Path(out_dir or ".") / (target.slug + ".py"))
    try:
        unit = stdlib.parse_unit(path, source)
    except stdlib.UnitError as exc:
        row["drop_reason"] = "malformed"
        row["drop_detail"] = str(exc)[:300]
        return Draw(row=row, source=source, drop_reason="malformed",
                    drop_detail=row["drop_detail"])

    violation = _closed_gate(unit)
    if violation:
        row["drop_reason"] = "closed-kind"
        row["drop_detail"] = violation
        return Draw(row=row, source=source, unit=unit, drop_reason="closed-kind",
                    drop_detail=violation)

    if seen is not None and row["source_sha256"] in seen:
        row["drop_reason"] = "duplicate"
        row["drop_detail"] = "byte-identical to %s" % seen[row["source_sha256"]]
        return Draw(row=row, source=source, unit=unit, drop_reason="duplicate",
                    drop_detail=row["drop_detail"])

    row["kept"] = True
    row["candidate"] = path
    return Draw(row=row, source=source, unit=unit)


def generate_batch(plan: Mapping[str, Any], batch: int, out_dir: str, backend: Any,
                   draws_per_target: int = 0, max_tokens: int = 0) -> Generation:
    """One batch of the plan: draw until a target lands, then move on.

    ``draws_per_target`` is a CEILING. A target answered on the first draw costs
    one call; only a target whose draw produced nothing usable spends another.
    That is the difference between a budget the dispatcher reads and a bill it
    receives, and it is why the plan's estimate says what it is not.

    The candidate files are written here rather than returned for the CLI to
    write, the same way :func:`pipeline.stdlib.write_rows` writes: writing is not
    printing, and a batch that died halfway should still leave the candidates it
    had already earned on disk for the verify stage to judge.
    """
    targets = plan_batch(plan, batch)
    per = draws_per_target or int(plan.get("draws_per_target") or DEFAULT_DRAWS_PER_TARGET)
    tokens = max_tokens or int(plan.get("max_tokens") or DEFAULT_MAX_TOKENS)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    written: List[Tuple[str, str]] = []
    unanswered: List[Tuple[str, str, str]] = []
    seen: Dict[str, str] = {}
    calls = 0
    p_tok = 0
    c_tok = 0
    for target in targets:
        last = ("", "")
        for draw in range(max(1, per)):
            got = draw_candidate(backend, target, batch, draw, str(out), tokens,
                                 seen=seen)
            rows.append(got.row)
            calls += 1
            p_tok += int(got.row.get("prompt_tokens") or 0)
            c_tok += int(got.row.get("completion_tokens") or 0)
            if got.kept and got.source:
                path = out / (target.slug + ".py")
                path.write_text(got.source, encoding="utf-8")
                seen[got.row["source_sha256"]] = target.slug
                written.append((target.slug, str(path)))
                last = ("", "")
                break
            last = (got.drop_reason, got.drop_detail)
        if last[0]:
            unanswered.append((target.slug, last[0], last[1]))
    return Generation(tuple(rows), tuple(written), tuple(unanswered), calls, p_tok, c_tok)


# --- repair ------------------------------------------------------------------


#: Drop reasons a repair round may act on. ``cpython-only`` is a unit no engine
#: ran, ``mismatch`` one an engine ran differently, ``malformed`` one that never
#: had the shape. ``closed-kind`` is deliberately absent: a unit rejected by the
#: closed gate must not be regenerated, because "try again" over a closed kind is
#: exactly the search that produces a plausible silent wrong answer.
#: ``engine-missing`` is absent too — it is a statement about the runner, not the
#: unit, and regenerating would answer the wrong question.
REPAIRABLE: Tuple[str, ...] = ("cpython-only", "mismatch", "malformed")


@dataclass(frozen=True)
class RepairJob:
    """One rejected unit, its verdict, and the refusal lines measured just now."""

    target: Target
    path: str
    source: str
    verdict: str
    refusals: Tuple[Tuple[str, str], ...] = ()


def measured_refusals(source: str, engines: Mapping[str, str],
                      timeout_s: float = 30.0) -> Tuple[Tuple[str, str], ...]:
    """Each engine's own refusal line for this source, measured now.

    The drops ledger records the VERDICT ("no engine ran it") but not the line,
    because the label that produced it is not serialised. Re-running is two
    spawns and gives the exact sentence the model has to act on, which is the
    whole content of the feedback. Reconstructing the line from the verdict would
    be inventing the engine's words, and invariant 9 is about exactly that: the
    engine writes its own name and its own kind, and nothing else may write them
    for it.
    """
    out: List[Tuple[str, str]] = []
    for name in stdlib.ENGINE_ORDER:
        if name == stdlib.CPYTHON:
            continue
        binary = engines.get(name)
        if not binary:
            continue
        run = stdlib.run_source(source, binary, timeout_s=timeout_s, engine=name)
        if stdlib.is_refusal(run):
            out.append((name, (run.stderr or "").strip().splitlines()[0].strip()))
        elif run.exit_code != 0:
            # Not a refusal, so not the contract line: this is the program's own
            # failure and it is quoted as such, never dressed up as one.
            tail = [ln.strip() for ln in (run.stderr or "").strip().splitlines() if ln.strip()]
            out.append((name, "exit %d (this is the program's own failure, not a "
                              "refusal): %s" % (run.exit_code, tail[-1][:200] if tail else "")))
    return tuple(out)


def repair_jobs(drops: Sequence[Mapping[str, Any]], units_dir: str,
                plan: Mapping[str, Any], engines: Optional[Mapping[str, str]] = None,
                timeout_s: float = 30.0) -> Tuple[List[RepairJob], List[Tuple[str, str]]]:
    """``(jobs, skipped)`` — one job per repairable drop whose source is on disk.

    ``skipped`` carries ``(name, why)`` so the CLI can say what was passed over
    rather than leaving a silently shorter list. A drop whose unit file is gone
    is skipped, not invented: the repair prompt shows the model its own file, and
    a repair without it would be a fresh generation wearing a repair's name.
    """
    by_slug: Dict[str, Target] = {}
    for group in (plan.get("batches") or ()):
        for raw in (group.get("targets") or ()):
            t = target_from_dict(raw)
            by_slug[t.slug] = t
    jobs: List[RepairJob] = []
    skipped: List[Tuple[str, str]] = []
    for drop in drops:
        name = str(drop.get("name") or "")
        reason = str(drop.get("reason") or "")
        if reason not in REPAIRABLE:
            skipped.append((name, "reason %s is not repairable" % (reason or "?")))
            continue
        path = Path(units_dir) / (name + ".py")
        if not path.is_file():
            skipped.append((name, "no candidate file at %s" % path))
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            skipped.append((name, "unreadable: %s" % exc))
            continue
        target = by_slug.get(name)
        if target is None:
            fills = tuple(str(n) for n in (drop.get("fills") or ())) or (name,)
            target = Target(slug=name, module=str(drop.get("reference") or ""),
                            reference=str(drop.get("reference") or ""), names=fills)
        refusals = measured_refusals(source, engines, timeout_s) if engines else ()
        jobs.append(RepairJob(target=target, path=str(path), source=source,
                              verdict=str(drop.get("detail") or reason),
                              refusals=refusals))
    return jobs, skipped


def repair_batch(jobs: Sequence[RepairJob], out_dir: str, backend: Any,
                 max_tokens: int = DEFAULT_MAX_TOKENS) -> Generation:
    """ONE round. One call per job, no retry, no second pass.

    :data:`REPAIR_ROUNDS` is 1 and this function does not loop, so "bounded"
    is a property of the code and not only of the caller. A job that comes back
    unusable stays unanswered and is reported; the next attempt is a new
    dispatch, which a human authorises.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    written: List[Tuple[str, str]] = []
    unanswered: List[Tuple[str, str, str]] = []
    seen: Dict[str, str] = {}
    p_tok = 0
    c_tok = 0
    for job in jobs:
        messages = [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": target_prompt(job.target)},
            {"role": "assistant", "content": "```python\n" + job.source.rstrip("\n") + "\n```"},
            {"role": "user", "content": repair_prompt(job.target, job.source,
                                                      job.verdict, job.refusals)},
        ]
        got = draw_candidate(backend, job.target, -1, 0, str(out), max_tokens,
                             messages=messages, kind="repair", seen=seen)
        got.row["verdict"] = job.verdict
        got.row["refusals"] = ["%s %s" % (e, line) for e, line in job.refusals]
        rows.append(got.row)
        p_tok += int(got.row.get("prompt_tokens") or 0)
        c_tok += int(got.row.get("completion_tokens") or 0)
        if got.kept and got.source:
            path = out / (job.target.slug + ".py")
            path.write_text(got.source, encoding="utf-8")
            seen[got.row["source_sha256"]] = job.target.slug
            written.append((job.target.slug, str(path)))
        else:
            unanswered.append((job.target.slug, got.drop_reason or "unchanged",
                               got.drop_detail))
    return Generation(tuple(rows), tuple(written), tuple(unanswered), len(jobs),
                      p_tok, c_tok)


# --- ledgers -----------------------------------------------------------------


def write_draws(path: "str", rows: Sequence[Mapping[str, Any]], append: bool = False) -> int:
    """Append-or-write the draws ledger. Every rollout, kept or not.

    ``training/DATA_PRODUCTION.md`` step 5: keep raw generation evidence even
    when it contributes nothing. A ledger that held only the successes would
    answer "how good is the prompt" with a number that cannot go down.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, sort_keys=False, ensure_ascii=False,
                              separators=(",", ":")) + "\n" for r in rows)
    with p.open("a" if append else "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return len(rows)


def generation_report(gen: Generation, what: str = "generate") -> str:
    """One screen of what a batch did. A STRING — invariant 8, the CLI prints it."""
    lines = ["%s: %d call(s), %d candidate(s) written"
             % (what, gen.calls, len(gen.written))]
    if gen.written:
        lines.append("")
        for slug, path in gen.written:
            lines.append("  %-28s %s" % (slug, path))
    kept = sum(1 for r in gen.draws if r.get("kept"))
    lines.append("")
    lines.append("%d draw(s) recorded, %d kept, %d contributed nothing"
                 % (len(gen.draws), kept, len(gen.draws) - kept))
    reasons: Dict[str, int] = {}
    for r in gen.draws:
        why = str(r.get("drop_reason") or "")
        if why:
            reasons[why] = reasons.get(why, 0) + 1
    for why, n in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append("  %-16s %4d" % (why, n))
    if gen.unanswered:
        lines.append("")
        lines.append("%d target(s) no draw answered:" % len(gen.unanswered))
        for slug, why, detail in gen.unanswered:
            lines.append("  %-28s %-16s %s" % (slug, why, str(detail)[:100]))
    lines.append("")
    lines.append("tokens this batch: %d prompt, %d completion — reported by the "
                 "provider, not estimated" % (gen.prompt_tokens, gen.completion_tokens))
    return "\n".join(lines)


def plan_report(plan: Mapping[str, Any]) -> str:
    """The plan in one screen: what resolved, what is planned, what it may cost."""
    lines: List[str] = []
    lines.append("model      %s" % plan.get("model"))
    lines.append("base_url   %s" % plan.get("base_url"))
    lines.append("resolved   %s via %s" % (plan.get("resolved_on"),
                                           plan.get("model_source")))
    if plan.get("model_note"):
        lines.append("           %s" % plan.get("model_note"))
    lines.append("           %s" % plan.get("producer_note"))
    cands = plan.get("model_candidates") or []
    excluded = plan.get("model_excluded") or []
    if cands or excluded:
        lines.append("")
        lines.append("what the provider listed")
        for c in cands:
            lines.append("  %-34s %s" % (c.get("id"), c.get("released") or "(no date)"))
        for c in excluded:
            lines.append("  %-34s excluded: %s" % (c.get("id"), c.get("excluded")))

    done = plan.get("already_covered") or []
    lines.append("")
    lines.append("%d target(s) a committed unit already fills — dropped" % len(done))
    for d in done:
        lines.append("  %-28s %s" % (d.get("slug"), ", ".join(d.get("units") or ())))

    batches = plan.get("batches") or []
    planned = sum(len(b.get("targets") or ()) for b in batches)
    lines.append("")
    lines.append("%d batch(es), %d target(s)" % (len(batches), planned))
    for b in batches:
        for t in (b.get("targets") or ()):
            covered = t.get("covered_names") or []
            note = " (%d name(s) already filled)" % len(covered) if covered else ""
            lines.append("  batch %-3s %-24s %s%s"
                         % (b.get("batch"), t.get("slug"),
                            ", ".join(t.get("names") or ())[:60], note))
    over = plan.get("open_not_planned") or []
    if over:
        lines.append("")
        lines.append("%d open target(s) this dispatch does NOT cover: %s"
                     % (len(over), ", ".join(over)))

    budget = plan.get("budget") or {}
    lines.append("")
    lines.append("budget ceiling for this dispatch")
    lines.append("  calls          <= %d  (%d target(s) x %d draw(s))"
                 % (budget.get("calls_max", 0), planned,
                    plan.get("draws_per_target", 0)))
    lines.append("  prompt tokens  <= %d" % budget.get("prompt_tokens_max", 0))
    lines.append("  output tokens  <= %d  (%d per call)"
                 % (budget.get("completion_tokens_max", 0), plan.get("max_tokens", 0)))
    lines.append("  total tokens   <= %d" % budget.get("tokens_max", 0))
    lines.append("  cost           <= %.4f %s at %.2f in / %.2f out per M, "
                 "published %s"
                 % (budget.get("cost_max", 0.0), budget.get("currency", ""),
                    budget.get("price_in_per_m", 0.0), budget.get("price_out_per_m", 0.0),
                    budget.get("priced_on", "")))
    lines.append("  repair rounds   = %d" % plan.get("repair_rounds", REPAIR_ROUNDS))
    lines.append("")
    lines.append("what those numbers are NOT: %s" % budget.get("note", ""))
    return "\n".join(lines)
