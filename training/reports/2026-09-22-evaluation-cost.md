# Step 1.5 — reuse unchanged policies and enforce the deadline inside the job

**Read 2026-09-22.** The prior launch's public Actions log,
[35527021744](https://github.com/kristerhedfors/lypning/actions/runs/35527021744),
prints `timeout: 480m` for HF job `6ab01cbb51992417dfccd64c`. The independent
[metadata-only audit 35680032058](https://github.com/kristerhedfors/lypning/actions/runs/35680032058)
reads the provider's raw job GET response, filtering out commands, environment,
secrets, status messages and logs. It returns:

```json
{"createdAt":"2026-09-20T17:49:47.232Z","startedAt":"2026-09-20T17:52:26.169Z","finishedAt":null,"timeout":28800,"timeoutSeconds":null,"stage":"CANCELED"}
```

The 480-minute request reached the service as 28,800 seconds. The
[pinned SDK conversion](https://github.com/huggingface/huggingface_hub/blob/v1.31.0/src/huggingface_hub/_jobs_api.py)
converts minutes to seconds; its `JobInfo` object drops the timeout field,
which is why the audit reads and filters the raw GET response. The configured
deadline from `startedAt` was **2026-09-21 01:52:26.169Z**. The frozen
[Fable report](2026-09-21-fable-round02-seed1111-read.md) records operator
cancellation at 05:41Z. The metadata exposes no finish timestamp, so that
cancellation time remains attributed to the report, not independently measured
by this audit.

**Finding:** the timeout was not lost by the workflow or misconverted by the
launcher. The provider did not enforce its recorded deadline as expected.
The internal service reason is not exposed; we cannot diagnose it from these
records. No new billed experiment is justified to reproduce an overrun.

The new launcher sends integer seconds to the provider and wraps the entire
bootstrap/stage process tree in GNU `timeout`, supplied by the pinned Debian
image. At the proposed 720-minute ceiling it sends TERM at 719 minutes, allowing
up to one minute for the pilot's EXIT upload trap, then KILL to the process
group by 720. The GitHub follower may still expire earlier without controlling
the HF job. This bounds the process lifetime inside the job; a platform that
keeps billing after its process exits is outside local enforcement. Current
pricing and the revised cost ceiling are in `../ROUND_READINESS.md`.

Duplicate eval-2 arms are reused only after the target loads under the same
runtime contract. A newly sealed SFT checkpoint zero records base equivalence
only after finite adapter tensors and zero B matrices are verified. GRPO
checkpoint zero records its exact parent-file identity, so a trained SFT parent
is never replaced by base. Complete case/draw coverage and stored metrics are
checked; `reuse.json` records source hashes and that no independent draw was
made. Trained or legacy adapters without proof run the ordinary evaluation.

Evaluation batches rise to 256 sequences in the trainer and launcher. This
changes chunking and therefore arm identity; all new arms must match, and a
real hardware smoke is still required before the next paid round. This session
ran neither model inference nor training. The metadata-only GitHub job made
read-only requests and downloaded no bank or model artifacts.
