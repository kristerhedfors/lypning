"""Explicit, audited resume of a paid positive-control run that stopped.

The generator takes zero retries: a request, once reserved, is never retried
implicitly, because a transport failure is ambiguous about whether it was
charged. So one such failure stops the run, the run is partial, and a partial
run is never graded (`positive_control_grade.generation_complete`). A rerun
from scratch would pay again for every completion the stopped run holds.

A resume is the third answer: a NEW run, dispatched by hand, that names the
stopped one (`resume_run_id` in `step2-control.yml`) and

* proves it is the same experiment, field by field (`IDENTITY`), naming the
  first field that differs;
* requests exactly planned minus completed-with-a-response. "Completed" means
  SETTLED in that run's own ledger: a response arrived and its usage, model
  and thinking-off contract were accepted. The ambiguous request -- reserved,
  never settled -- is requested again, explicitly: this module lists it, the
  new ledger records a ``re-request`` event beside its reservation, and the
  prior reservation stays counted as spent;
* spends against ONE ceiling for the whole chain: the ceiling a resume is
  dispatched with is the shard's total, and every earlier run's own
  charged-or-reserved dollars count against it;
* writes a result whose completed/planned refer to the union, with the
  ``resumed_from`` chain, each run with its own dollars. It is complete only
  when the union covers every planned request exactly once.

The prior evidence is read, never written: nothing a resume stores lands under
an earlier run id. A resumed run can itself be resumed; the chain grows.

Every message here is safe to print in a public Actions log -- run ids, field
names, counts and dollars, never a case id or text.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

from .jsonio import read_jsonl, sha256_of
from .training_types import TrainingError

#: The recorded private run-id shape of every rung a resume may continue:
#: ``<rung>[-<i>of<n>]-<40-hex commit>-<Actions run id>`` (`STEP2_RUN_ID` in
#: `step2-control.yml`). The smoke is not resumable: at 512 requests a rerun
#: costs less than the audit a resume needs.
RUN_ID = re.compile(r"(?P<rung>targets|budget20|confirmatory|full-(?P<index>[0-3])of(?P<count>[2-4]))"
                    r"-(?P<commit>[0-9a-f]{40})-(?P<run>[0-9]{1,20})")
RESUMABLE_RUNGS = ("targets", "budget20", "confirmatory", "full")

#: What makes a resumed run the same experiment as the run it continues --
#: everything the prior run's manifest, admission and shard record that bears
#: on what a completion is. The tokenizer is not here because generation never
#: loads one: it prices the public plan, and the prompt is sent as text.
IDENTITY = ("rung", "shard", "requests", "case_set_sha256", "case_fingerprints", "spec_sha256",
            "provider", "samples", "sampling", "max_retries", "engine_sha256", "base_image",
            "oracle_python", "candidate_recipe")
#: The subset known before the candidate is built; checked early for free.
PLANNED_IDENTITY = ("rung", "shard", "requests", "case_set_sha256", "case_fingerprints",
                    "spec_sha256", "samples")


class ResumeError(TrainingError):
    """A refusal whose message is safe to print: run ids, fields, counts, dollars."""


def request_key(case_id, draw, arm):
    """The ledger's key for one request (`positive_control_generate`)."""
    return "%s/%d/%s" % (case_id, draw, arm)


def row_key(row):
    return request_key(row.get("case_id"), row.get("draw"), row.get("arm"))


def parse_run_id(run_id):
    """``(rung, shard_index, shard_count)`` of a resumable run id, or a refusal."""
    match = RUN_ID.fullmatch(run_id or "")
    if not match:
        raise ResumeError("resume_run_id must be a recorded targets, budget20, confirmatory "
                          "or full-<i>of<n> positive-control run id")
    if match.group("index") is None:
        return match.group("rung"), 0, 1
    return "full", int(match.group("index")), int(match.group("count"))


def shard_prefix(run_id):
    """The ``<rung>[-<i>of<n>]`` every run of one rung and shard starts with."""
    parse_run_id(run_id)
    return RUN_ID.fullmatch(run_id).group("rung")


def check_not_forked(chain_ids, manifests):
    """Refuse a resume of a run that another run has already resumed.

    ``chain_ids`` is the chain about to be resumed; ``manifests`` maps every
    stored run of the same rung and shard to its ``paid/manifest.json``. A
    resume's manifest names the runs it resumed before its first call, so a
    resume that spent anything -- even one cancelled before it wrote a result
    -- is seen here. Resuming an earlier run of a chain again would buy what
    its successor already bought and spend that successor's dollars a second
    time, outside the chain's ceiling: resume the latest run instead.
    """
    chain = set(chain_ids)
    for run in sorted(manifests):
        if run in chain:
            continue
        manifest = manifests[run] if isinstance(manifests[run], dict) else {}
        record = manifest.get("resume")
        if record is None:
            continue
        resumed = record.get("resumed_from") if isinstance(record, dict) else None
        if not isinstance(resumed, list) or not all(isinstance(r, str) for r in resumed):
            raise ResumeError("run %s records a malformed resume" % run)
        overlap = [r for r in resumed if r in chain]
        if overlap:
            raise ResumeError("run %s already resumed run %s; resume the latest run of that "
                              "chain, never an earlier one" % (run, overlap[-1]))


def check_target(resume_run_id, run_id, rung, shard):
    """Refuse a resume of another rung or shard, or of this very run."""
    if rung not in RESUMABLE_RUNGS:
        raise ResumeError("the %s rung is not resumable; rerun it" % rung)
    prior_rung, index, count = parse_run_id(resume_run_id)
    if prior_rung != rung:
        raise ResumeError("run %s is a %s run, not %s" % (resume_run_id, prior_rung, rung))
    if (index, count) != (shard["shard_index"], shard["shard_count"]):
        raise ResumeError("run %s is shard %d of %d, not shard %d of %d"
                          % (resume_run_id, index, count, shard["shard_index"], shard["shard_count"]))
    if resume_run_id == run_id:
        raise ResumeError("a run cannot resume itself")
    return prior_rung, index, count


def identity(run_id, manifest, shard, recipe_of):
    """The `IDENTITY` of one run, from what it recorded before its first call."""
    try:
        admission = manifest["admission"]
        rung = parse_run_id(run_id)[0]
        return {
            "rung": rung, "shard": dict(shard), "requests": manifest["requests"],
            "case_set_sha256": manifest["case_set_sha256"],
            "case_fingerprints": admission["case_fingerprints"],
            "spec_sha256": manifest["spec_sha256"], "provider": manifest["provider"],
            "samples": manifest["samples"], "sampling": manifest["sampling"],
            "max_retries": manifest["max_retries"],
            "engine_sha256": admission["conformance"]["engine_sha256"],
            "base_image": admission["conformance"]["base_image"],
            "oracle_python": admission["references"]["python"],
            "candidate_recipe": recipe_of(admission["source_commit"]),
        }
    except (KeyError, TypeError) as exc:
        raise ResumeError("run %s lacks its manifest or admission lineage" % run_id) from exc


def planned_identity(run_id, cases, spec_sha256, samples, shard, requests):
    """The `PLANNED_IDENTITY` of the run about to start, from its inputs alone."""
    return {"rung": parse_run_id(run_id)[0], "shard": dict(shard), "requests": requests,
            "case_set_sha256": sha256_of(cases),
            "case_fingerprints": {c["case_id"]: sha256_of(c) for c in cases},
            "spec_sha256": spec_sha256, "samples": samples}


def check_identity(links, current, fields=IDENTITY):
    """Every run of the chain is `current`'s experiment; else name run and fields."""
    for link in links:
        moved = [field for field in fields if link["identity"].get(field) != current.get(field)]
        if moved:
            raise ResumeError("run %s differs from this run in %s" % (link["run_id"], ", ".join(moved)))


def read_link(run_id, paid, recipe_of):
    """One run of a chain from its private ``paid/`` directory. Read-only."""
    parse_run_id(run_id)
    paid = Path(paid)
    try:
        manifest = json.loads((paid / "manifest.json").read_text(encoding="utf-8"))
        result = json.loads((paid / "result.json").read_text(encoding="utf-8"))
        shard = json.loads((paid / "shard.json").read_text(encoding="utf-8"))
        completions = read_jsonl(paid / "completions.jsonl")
        spend = read_jsonl(paid / "spend.jsonl")
    except (OSError, ValueError) as exc:
        raise ResumeError("run %s is missing its manifest, result, shard or ledger" % run_id) from exc
    return {"run_id": run_id, "manifest": manifest, "result": result, "shard": shard,
            "completions": completions, "spend": spend,
            "identity": identity(run_id, manifest, shard, recipe_of)}


def _usd(value, run_id):
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ResumeError("run %s records a malformed dollar amount" % run_id) from exc
    if not amount.is_finite() or amount < 0:
        raise ResumeError("run %s records a malformed dollar amount" % run_id)
    return amount


def own_completed(result):
    """How many requests a run itself settled; a resumed run's `completed` is its union."""
    return result.get("completed_in_run", result.get("completed")) if result.get("resumed_from") \
        else result.get("completed")


def account(link):
    """One run's ledger, recomputed and checked against the run's own result.

    Returns the settled rows by key, the unsettled reservations by key (the
    ambiguous ones), the keys of rows persisted but never settled, and the
    run's charged-or-reserved dollars: every settled cost plus every
    reservation that was never released -- exactly what `Budget.charged`
    holds when the run ends.
    """
    run = link["run_id"]
    reserved, settled = {}, {}
    for event in link["spend"]:
        kind, key = event.get("event"), event.get("request")
        if kind == "reserved":
            if key in reserved:
                raise ResumeError("run %s reserved one request twice" % run)
            reserved[key] = _usd(event.get("usd"), run)
        elif kind == "settled":
            if key not in reserved or key in settled:
                raise ResumeError("run %s settles a request it never reserved, or twice" % run)
            settled[key] = _usd(event.get("usd"), run)
        elif kind != "re-request":
            raise ResumeError("run %s has an unknown ledger event" % run)
    charged = sum(settled.values(), Decimal(0)) + sum(
        (usd for key, usd in reserved.items() if key not in settled), Decimal(0))
    result = link["result"]
    if charged != _usd(result.get("charged_or_reserved_usd"), run):
        raise ResumeError("run %s: ledger and result disagree on dollars charged or reserved" % run)
    if own_completed(result) != len(settled):
        raise ResumeError("run %s: ledger and result disagree on completed requests" % run)
    rows, persisted = {}, set()
    for row in link["completions"]:
        key = row_key(row)
        if key in rows or key in persisted:
            raise ResumeError("run %s holds two completions of one request" % run)
        if key in settled:
            rows[key] = row
        else:
            persisted.add(key)
    if len(rows) != len(settled):
        raise ResumeError("run %s settles a request with no completion" % run)
    ambiguous = {key: usd for key, usd in reserved.items() if key not in settled}
    return rows, ambiguous, persisted, charged


def chain_links(named, read):
    """The named prior run and its own chain, oldest first.

    ``read(run_id)`` returns one link. The chain a resumed run recorded is
    re-read from each run's own evidence and must agree with it.
    """
    last = read(named)
    recorded = last["result"].get("resumed_from") or []
    links = []
    for entry in recorded:
        links.append(read(entry.get("run_id")))
    links.append(last)
    check_chain_record(links[:-1], recorded)
    ids = [link["run_id"] for link in links]
    if len(ids) != len(set(ids)):
        raise ResumeError("a run appears twice in the chain")
    return links


def check_chain_record(links, recorded):
    """A `resumed_from` record names these runs, in order, with their own dollars."""
    if [entry.get("run_id") for entry in recorded] != [link["run_id"] for link in links]:
        raise ResumeError("the recorded chain names other runs than the evidence holds")
    for link, entry in zip(links, recorded):
        _, _, _, charged = account(link)
        if (_usd(entry.get("charged_or_reserved_usd"), link["run_id"]) != charged or
                entry.get("completed_in_run") != own_completed(link["result"])):
            raise ResumeError("the chain's record of run %s differs from its own evidence"
                              % link["run_id"])


def plan(links, keys, ceiling_usd):
    """What a resume of ``links`` (oldest first) must request, and may spend.

    ``keys`` are this run's planned request keys in request order. Identity
    is checked by the caller (`check_identity`) before this is trusted.
    """
    planned = list(keys)
    if len(planned) != len(set(planned)) or not planned:
        raise ResumeError("the planned request set is empty or repeats a request")
    wanted = set(planned)
    done, ambiguous, superseded, chain = {}, {}, [], []
    prior = Decimal(0)
    for link in links:
        rows, open_, persisted, charged = account(link)
        run = link["run_id"]
        if not (set(rows) | set(open_) | persisted) <= wanted:
            raise ResumeError("run %s holds a request outside this run's plan" % run)
        for key, row in rows.items():
            if key in done:
                raise ResumeError("run %s completed a request an earlier run already completed" % run)
            done[key] = row
        for key, usd in open_.items():
            ambiguous.setdefault(key, []).append({"run_id": run, "usd": str(usd)})
        superseded += [{"request": key, "run_id": run} for key in sorted(persisted)]
        chain.append({"run_id": run, "charged_or_reserved_usd": str(charged),
                      "completed_in_run": len(rows)})
        prior += charged
    remaining = [key for key in planned if key not in done]
    if not remaining:
        raise ResumeError("run %s already holds every planned request; grade it, do not resume it"
                          % links[-1]["run_id"])
    ceiling = _usd(ceiling_usd, "this")
    run_ceiling = ceiling - prior
    if run_ceiling <= 0:
        raise ResumeError("the chain has charged or reserved $%s of the $%s ceiling; "
                          "nothing is left to resume with" % (prior, ceiling))
    return {
        "chain": chain, "prior_usd": prior, "ceiling_usd": ceiling, "run_ceiling_usd": run_ceiling,
        "planned": len(planned), "prior_rows": done, "remaining": remaining,
        # Unsettled in an earlier run and still wanted: requested again, on the record.
        "re_requested": {key: ambiguous[key] for key in remaining if key in ambiguous},
        # Persisted but never settled, and wanted again: this run's response replaces it.
        "superseded": [item for item in superseded if item["request"] in set(remaining)],
    }


def summary(state):
    """Aggregates of a resume plan for a public log: counts and dollars only."""
    return {"resumed_from": [entry["run_id"] for entry in state["chain"]],
            "prior_completed": len(state["prior_rows"]), "planned": state["planned"],
            "remaining": len(state["remaining"]), "re_requested": len(state["re_requested"]),
            "superseded": len(state["superseded"]),
            "prior_charged_or_reserved_usd": str(state["prior_usd"]),
            "ceiling_usd": str(state["ceiling_usd"]), "run_ceiling_usd": str(state["run_ceiling_usd"])}


def union_completions(links, result):
    """The completions a resumed run's grade reads: every planned request once.

    ``links`` is the recorded chain, oldest first, then the resumed run
    itself. Each run contributes only what it settled, so a request that was
    ambiguous in one run and answered in a later one is counted once -- the
    later response -- and a request settled twice is refused, not deduplicated.
    """
    recorded = result.get("resumed_from")
    if not recorded or not links:
        raise ResumeError("a resumed run's grade needs its recorded chain")
    if links[-1]["result"] != result:
        raise ResumeError("the last run of the chain is not the resumed run")
    check_chain_record(links[:-1], recorded)
    check_identity(links[:-1], links[-1]["identity"])
    union = {}
    for link in links:
        rows, _, _, _ = account(link)
        for key, row in rows.items():
            if key in union:
                raise ResumeError("run %s completed a request another run of the chain completed"
                                  % link["run_id"])
            union[key] = row
    if not (len(union) == result.get("completed") == result.get("planned")):
        raise ResumeError("the chain's union does not match the resumed result's completed/planned")
    return list(union.values())
