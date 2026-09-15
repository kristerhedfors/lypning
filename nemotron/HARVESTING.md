# Cerebras / OpenCode project harvesting

This lane collects **observations from bounded one-shot project sessions** for a
future data review. It does not launch training, change Fable's frozen round, or
admit generated code automatically. The goal is more correct
lypning-l-compatible Python, with no individual speedup threshold. The aggregate
runtime performance premise is not a label for any generated project.

## Task catalog and generation regime

`harvesting/tasks.json` contains original, repository-authored specifications for
distinct small standard-library Python projects. Every entry has a stable `id`,
semantic `family`, intended `capabilities`, complete `prompt`, and `rights_basis`.
Capabilities are **targets to investigate**, not claims of native support.
Prompts specify deterministic observables and edge cases without revealing a
reference implementation. They avoid private repositories, customer inputs,
third-party assets, package downloads and external services.

Each task asks OpenCode to build a project and run its own tests. **One-shot means
one initial project request, not one model completion.** An agent may call tools,
make several model requests and repair code within that session. Preserve the
initial generated version and subsequent tool/source chronology when available;
do not relabel a repaired final project as an unaided first-draft solution.
Generated self-tests and a passing exit status are evidence of what the agent
checked, not an independently correct oracle.

The task prompt itself is an ordinary Python task. Any wrapper system prompt,
OpenCode agent instructions, capability hints or installed harness are a separate
recorded condition. A compatibility-conditioned run is not interchangeable with
the ordinary-task evaluation regime. The catalog is an initial collection
fixture, not a representative benchmark, an admitted training split, or enough
independent families to satisfy the existing pilot gate by itself.

## Running and scaling

The manual `.github/workflows/harvest.yml` workflow keeps harvesting separate
from training and supports these bounded inputs:

| Input | Default | Allowed range | Meaning |
| --- | --- | --- | --- |
| `projects` | 4 | 1–12 | Number of catalog tasks, in catalog order |
| `parallelism` | 4 | 1–4 | Maximum simultaneous project jobs |
| `rounds` | 1 | 1–4 | Independent sessions per selected task, not new families |

After the workflow is available on `main`, a small pilot can be dispatched with
the command below. The workflow rejects other refs before providing provider
credentials to the harvest jobs.

```bash
gh workflow run harvest.yml --repo kristerhedfors/lypning \
  --ref main -f projects=4 -f parallelism=4 -f rounds=1
```

The provider model is fixed to `qwen-3.8-27b`, and the worker pins OpenCode to
`opencode-ai@1.18.31`. Record the provider's returned model identity as well:
a hosted model name is not an immutable checkpoint revision. If that model is
unavailable, stop and report it instead of silently substituting another model.

Each project is limited to 24 provider requests, 2,048 completion tokens per
request and 49,152 reserved output tokens in total, with a 256 KiB request-body
limit. The project worker has a 15-minute deadline and its Actions job a
25-minute timeout. There are no automatic workflow retries; any SDK request
retry consumes the same proxy request budget. These bounds apply per session,
so multiplying projects by rounds multiplies the batch ceiling. Output-token
reservations are not actual usage or a bound on the provider's billed input
tokens; the request-byte limit separately bounds uploaded request payloads.

The job invokes `python -m harvesting.runner` under `PYTHONPATH=src:nemotron`, with
`--task-index`, `--round` and a new `--output` directory under `work/harvest`.
Run it only on the dedicated disposable worker boundary, not inside a personal
checkout with credentials. Each job produces its own archive and snapshot.
Actions artifacts are retained for 14 days: download approved evidence before
expiry if it is to survive for the next review. Retention is bounded, not a
promise that GitHub permanently preserves every observation.

Use only the repository's existing `CEREBRAS_API_KEY` Actions secret for trusted
provider access. Never copy its value into a prompt, generated project, debug
log, uploaded artifact or training record. The model-facing execution boundary
must not receive repository credentials or the upstream API key. A trusted
provider gateway and disposable project workers keep those responsibilities
separate. The agent and trusted proxy run in different containers on an internal
network. The agent receives no host mounts, repository token, Docker socket or
upstream provider key. Only the trusted proxy can attach the upstream secret.
This boundary is not proof against container/kernel vulnerabilities; use an
approved disposable Actions runner and inspect the pilot before scaling it.

Concurrency is not a spending limit: bound projects, calls, tokens, output bytes,
wall time and retries as well. Rate-limit responses, missing models, transport
errors, timeouts and incomplete projects remain explicit outcomes. Do not
silently replace the requested model or resume forever. Start with a small pilot,
inspect usage and evidence completeness, then explicitly increase a bounded
batch. API token ceilings are ceilings, not predictions of billing.

## What the archive must mean

The controller pauses the worker before collecting its `/work` tmpfs, keeping
the mounted project and output available without further agent writes. It reads
bounded Docker archive streams but **never extracts untrusted paths onto the
host**. Collected file paths are metadata only; safe regular-file bytes become
content-addressed blobs. The per-job artifact has this layout:

```text
task.json                 Authored task and run context
manifest.json             Controller state, caps, identities and file records
blobs/<full-sha256>        Collected bytes addressed by each record's sha256
observations.jsonl        Context record and indexed file observations
evidence/                 lypning.evidence snapshot of observations.jsonl
```

`manifest.json` records have `collection` values such as `project`, `output` or
`proxy`, original `path` metadata, sizes and either a blob `sha256` or an explicit
omission reason. The `output` collection includes available OpenCode event
streams, invocation hooks, session exports, stderr and `worker.json`; the proxy
collection includes its separate request/response ledger. Files are not restored
to original names automatically. Inspect them by their records and digest, not
by executing or unpacking generated paths. A record's `redacted` flag indicates
replacement of an exact provider-key occurrence before hashing; those bytes are
then a redacted derivative, not an untouched original.

All records remain `trainable=false` with correctness unknown. A successful
Actions job or `worker_reported_completion` means the collection path completed,
**not that OpenCode succeeded, self-tests passed, or the program is correct**.
Inspect the untrusted worker's reported exit code, events and export statuses,
plus trusted provider outcomes and collection errors. Worker-owned logs can be
altered by code in that worker; the separate proxy ledger gives another source
of observations, not a correctness oracle. Omission flags and quarantined
snapshot records must survive any later import.

Retain the authorized project specification and catalog revision; requested and
provider-reported model identities; run, shard, attempt and OpenCode session IDs;
tool/provider versions; generation settings and limits; source artifacts and
their full hashes; available session/tool events and stdout/stderr; provider
usage; termination reason; and explicit missing/truncated evidence indicators.
Unknown identities or outcomes stay unknown. Preserve failed, unsupported,
unselected and timed-out attempts within the stated retention limits. A
deduplicated source can have multiple distinct execution contexts and outcomes.

Do not upload credential stores, arbitrary runner home directories, repository
configuration or host environment dumps. GitHub artifact access and retention
are not the same as a private local archive; review the repository visibility
and content policy before retaining raw sessions there. Redaction checks cannot
prove that every possible secret was removed. These authored tasks avoid private
source by construction but generated outputs still need review.

## From observations to a later training round

1. Validate the run manifest and file hashes. Record incomplete/missing evidence
   rather than manufacturing success labels. Store the authorized artifacts in a
   new private `work/` directory, never Fable's active round directory.
2. Validate the provided `evidence/` snapshot of file observations using
   `lypning.evidence`. This is an index of collected files and context, not an
   assertion that each Python source was executed. If deriving additional
   producer events from JSONL captures or OpenCode exports, keep the originals,
   supply a stable origin naming the run/session/log generation, and use a new
   origin for rewritten logs. Arbitrary OpenCode exports are not automatically
   equivalent to lypning capture records: an adapter must preserve provenance
   and distinguish actual observations from inferred fields.
3. Review intent, provenance, rights and independent correctness expectations.
   Extract an ordinary task and executable reference only where the existing
   verifier's observable contract can represent them. Multi-file behavior or
   filesystem effects are not automatically supported by the current
   stdout-oriented training contract. Agent-generated tests may suggest cases,
   but cannot be the sole independent correctness basis.
4. Link capture-derived cases to real evidence IDs with `review.origin=captured`
   and all required review assertions. The prompt's authored origin does not
   make a captured candidate an independently authored reference. Group all
   variants, attempts and repairs of the same task/source lineage before
   splitting. Duplicating a catalog prompt across many workers is not additional
   family coverage.
5. Run the existing review and preparation gates in a **new** reviewed bundle.
   Verify CPython correctness first and native compatibility second in the
   approved candidate boundary. A valid native refusal is useful coverage
   evidence; a native mismatch is a blocker, never a training target. Timings
   neither admit nor reject an otherwise correct-compatible case.
6. Give the next training owner the new archive and review/bundle manifests as a
   proposal. Do not mutate Fable's model, frozen data, split, probe, adapters or
   evaluation inputs. A later round can use reviewed compatible references,
   controls, repair research and coverage witnesses as distinct views.

The existing non-executing review command, once genuine reviewed cases and a
valid capture snapshot exist, is:

```bash
PYTHONPATH=src:nemotron python -m pipeline.data_loop \
  --cases work/harvest-review/candidate-cases.jsonl \
  --snapshot work/harvest-review/evidence \
  --purpose pilot --seed 1111 \
  --output work/harvest-review/reviewed-v1
```

This command does not invent test oracles or waive the pilot's independence and
population requirements. See [DATA_PRODUCTION.md](DATA_PRODUCTION.md),
[START_NEXT_ROUND.md](START_NEXT_ROUND.md) and
[TRAINING.md](TRAINING.md) for the existing admission and manual-training gates.
