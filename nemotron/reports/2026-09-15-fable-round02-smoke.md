# Fable round report — round-02 smoke on the Hugging Face boundary

## Outcome and decision requested

Date 2026-09-15. Round `round-02`, stage **smoke**. Status: RESULT_STATUS.
A GPU run occurred: yes, on one Hugging Face `a10g-small` Job, with the tiny
random Qwen configuration the `--smoke` path defines, never the 27B weights.

Outcome: the round's plumbing runs end to end on Hugging Face with the pooled
sandbox boundary the operator chose on 2026-09-15: same CPython base digest on
the trainer and the verifier, identity handshake passed from inside the trainer
job, starter bundle verified through pooled sandboxes, SFT and GRPO smoke
stages on a real GPU, sealed adapters read by the planner. RESULT_SUMMARY

Decision requested of Codex: accept `hf-sandbox-pool` as an admitted execution
contract for round-02 (gate 6), with its residual risks as written in
`START_NEXT_ROUND.md`, or require the dedicated-sandbox tier before a pilot.
No pilot ran: there is no reviewed dataset (gates 3 and 4 are open), and the
starter is smoke-only. No model-quality claim is made or implied.

## Reproduction and authority

- Repository commit: `8f23201a49bd9b357f6b07bd98f40344d917deb7` on
  `claude/next-round-7dilm6` (the job clones and checks out this commit).
- Report version: 1.
- Approvals: the operator's instruction in this session on 2026-09-15 to run
  the round on Hugging Face with pooled sandboxes ("we more or less trust this
  code"). Cost ceiling applied by me, not stated by the operator: the
  `a10g-small` flavor at $1.00/hour with a 75-minute job timeout, so at most
  $1.25 per attempt; the first attempt failed at bundle preparation after
  about six billed minutes, the second is this report's run. No storage or
  privacy policy was stated; artifacts went to the private dataset repo below.
- Model revision: `Qwen/Qwen3.8-27B` at `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`,
  the Hub's current commit on 2026-09-15, recorded as `QWEN_REV`; treated as
  the approved revision on the strength of the same instruction. Only the
  tokenizer and config were fetched at this revision; no weights.
- Tokenizer/template and decoding: the pipeline's fixed contract
  (`pipeline.training_contract`), non-thinking template, smoke decoding
  (`max_tokens` 32, two trainer steps, eval every step).
- Python build: `3.12.14 (main, Sep  1 2026, 00:10:07) [GCC 14.2.0]` in both the
  trainer job and the verifier image, from
  `python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea`.
- Engine: `lypning 0.1.0 (lypning-l) for cpython 3.12`, sha256
  `a23b30832e00640cec2090d8403a6beeaa2087083d0fbd9080210fd8d4fc1096`, built from
  the crate at main `4522c10` (unchanged since), musl static.
- Candidate image: private Docker Space `headforce/lypning-round02-verifier` at
  commit `fd43e79a43bc37147017265315224c605bb9c71c`, built from the Dockerfile,
  `sandbox.py` (`70276f2e…`), `child_exec.py` (`54c124cb…`),
  `container_worker.py` (`cbf984da…`) and the engine binary above.
- Verifier/harness identity: `verifier_sha256` and the two harness hashes as
  recorded in the bundle; the handshake compares the harness hashes, engine
  hash, version string and full `sys.version`.
- Package lock: the pinned block at the head of `gpu/train_verified.py`
  (`torch==2.9.1` resolved to `2.9.1+cu128`, `transformers==5.17.0`,
  `peft==0.20.0`, `accelerate==1.15.0`, `huggingface-hub==1.31.0`,
  `safetensors==0.8.0`, `trl==1.13.0`, `datasets==4.7.0`).
- Bundle digest: BUNDLE_DIGEST (smoke purpose, `hf-sandbox-pool` execution).
- Selected checkpoints: SELECTED_STEPS.
- Jobs: first attempt `6aa9c202f76d6a098a70e99c` (ERROR at preparation: the
  script named the image without its `spaces/` segment; fixed in the report
  commit), second attempt `6aa9c34c5527934177ee6beb` (this run). Sandbox host
  Jobs are created and torn down by the runner; one leftover host from a local
  probe was cancelled by hand.
- Commands: `nemotron/hf/launch.py smoke --branch claude/next-round-7dilm6
  --commit 8f23201a… --space headforce/lypning-round02-verifier
  --space-revision fd43e79a… --qwen-revision 1d4bf0f2…
  --work-repo headforce/lypning-round02-work --flavor a10g-small --timeout 75m
  --yes --follow`, which runs `nemotron/hf/round02_smoke.sh` in the job.
- Seeds: the pipeline default `1111`.
- Private artifacts: dataset repo `headforce/lypning-round02-work`, path
  `round-02/6aa9c34c5527934177ee6beb/` (bundle, both smoke stage directories,
  plan, `job-manifest.json`). Job logs on the Hub under the job ids.
- Exclusions and missing artifacts: no pilot bundle, no reviewed dataset, no
  27B weights, no base/SFT/GRPO measurement on real cases.

## Data and hypothesis

Question: does the round's pipeline run, unchanged in protocol, when the Docker
execution boundary is replaced by pooled Hugging Face sandboxes? Predicted
outcome: yes, with the identity handshake as the check that the two sides are
the same build. Data: the authored starter curriculum (`pipeline.curriculum`),
smoke purpose, which is a fixture and not a task family count. No oracle,
rights or split review applies to a fixture; no teacher or repair conditions;
no held-out task exists in this run.

## Measurements

Smoke measurements are plumbing facts, not model results:

MEASUREMENTS

Boundary facts measured from this worker before the job, 2026-09-15: a pooled
sandbox runs as uid 20000 with no `HF_TOKEN` in its environment and a cwd inside
its private home; one verification request round-trips in about 0.5 s after
the host is warm; a spin loop is killed at its timeout; a 900 MB allocation
under a 256 MB cap raises `MemoryError`; the worker is reachable under
`/usr/local/lib` and not under `/runner` (Landlock). Sandbox and Job time are
verification overhead, not interpreter speed.

## Failures, reflections and next experiment

- First job attempt failed at preparation on an image-path spelling; the
  handshake before it had already passed, so the failure was the script's, not
  the boundary's. Preserved as job `6aa9c202f76d6a098a70e99c`.
- What worked: the shared base digest makes `sys.version` agree without any
  runtime negotiation; the worker protocol needed no change; Landlock's
  read-set is the one thing the image layout had to respect.
- What is inferred rather than shown: that pooled sandboxes are an acceptable
  trust boundary for a real pilot. The platform says the tier is for one trust
  class and keeps outbound network open; this run cannot show what a hostile
  candidate could do with that, only that a cooperative one cannot see the
  trainer's assets. The dedicated tier is one call away if Codex wants it.
- Alternative explanations: none needed for a plumbing result.
- Next bounded experiment: a reviewed pilot dataset (gates 3 and 4), then the
  matched base-vs-SFT comparison on `h200` with this boundary. Owner: the
  operator for data and approvals, Fable for execution. Stop rule: any native
  mismatch or handshake drift aborts; cost ceiling to be set by the operator.

## Codex review handoff

Artifacts to inspect: this report; the private dataset repo path above; the
job logs by id; the verifier Space at its commit; the commits on
`claude/next-round-7dilm6` that add `hf-sandbox-pool`. Unresolved: whether the
pooled tier is admitted for a pilot; the reviewed dataset; the operator's cost
ceiling for the real run. Codex's assessment is its own to write.
