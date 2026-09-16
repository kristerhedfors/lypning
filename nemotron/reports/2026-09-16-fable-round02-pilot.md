# Fable round report — round-02 pilot, first attempt

## Outcome and decision requested

Date 2026-09-16. Round `round-02`, stage **pilot**. Status: **blocked on
billing**; no training happened. A GPU run was started (job
`6aaa202cf76d6a098a70f7e1`, h200, commit `4867b62…`) and was cancelled by the
platform about three minutes in, during dependency install, with no message; at
the same time the serverless provider answered HTTP 402 ("You have depleted your
monthly included credits") on 796 of the 1,024 pilot-draw calls. Every paid
step of the round is blocked until the account carries pre-paid credits.

Decision requested of the operator: add credits, then either relaunch the same
job and pilot draw as they stand, or read the early signal below first and
decide whether this round's training is still worth its cost.

## The early signal, from what did run

The pilot draw reached 15 of the 64 training-bank cases with 228 clean draws
before the credits ran out (run `eval-20260916-044656`, k = 16, decoding per
`EVAL2.md` §5, `prompt_sha d23e9420b5812443`, provider novita, engine
fingerprint `2e079e786a655ab6`, 2026-09-16). On those:

| Population | Cases | Clean draws | Correct | Correct-and-native |
|---|---|---|---|---|
| coverage | 12 | 192 | 97.4% | 96.9% |
| fallback-control | 3 | 36 | 100.0% | 100.0% |

Read with care: fifteen cases is a glimpse, not a base rate. But it is
consistent with the 11-case probe of the same day (10 of 11 correct and native
at one draw) and with the pre-registration's own falsifier (`EVAL2.md` §9): on
ordinary captured tasks the base model already writes correct programs the
engine runs natively almost every time, and even the three tasks whose captured
reference had to fall back were solved natively by the model on every draw.
If the 300-case eval-2 base rate lands where these fifteen do, there is no
+3pp of correct-and-native headroom for an adapter to earn on this population,
and the programme's remaining headroom is in the engine (what it refuses) and in
the tail of harder tasks the capture never sees, not in the model.

## Reproduction and authority

- Repository commit: `4867b62fa3c002f5bf9eda037dca861b18f2d7d0` (the job) and
  `bffbdbc…` (the frozen bank identities, `EVAL2.md` §11).
- Approvals: the operator's instruction of 2026-09-16 ("run the next round of
  fine-tuning"). Cost ceiling applied by me and stated before spending: about
  $5 each for the pilot draw and the 0b probe, h200 at $5/hour with a six-hour
  cap, at most three GPU attempts, about $100 in total. Actual spend this
  attempt: the h200 job ran about three minutes; the provider calls that
  answered were within the account's included credits.
- Banks: eval-2 v1 (300 cases, sha256 `46cff1d7…`) and train v1 (64 cases,
  sha256 `31edda65…`), frozen 2026-09-16, stored privately in
  `headforce/lypning-round02-work` under `banks/2026-09-16-eval2-v1/`
  (commit `7b10488b…`). Data review was done by authoring and solving agents,
  not by Codex; `ORCHESTRATION.md` row T2 says so.
- Verifier Space: `headforce/lypning-round02-verifier` at
  `6d287057e491e9edc22b677e2bf0dd5200f9b40a` (worker reports its identity on
  every response); handshake and four execution witnesses passed from this
  worker on 2026-09-16.
- Model revision: `Qwen/Qwen3.8-27B` at `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`.
- Exclusions: no adapter, no base-dev, no eval-2 base rate on the task-first
  path; the power curve printed from the truncated draw (`EVAL2.md` §7) is
  provisional and is not used to size anything.

## Failures, reflections and next experiment

- The billing stop was invisible to the launcher: the job's status carried no
  message, and only the provider's 402 named the cause. The pipeline's rows
  adapter also counted the 796 excluded attempts as incorrect draws, which made
  the first power curve read a 21% base rate; that is a reporting bug to fix
  (rows must skip harness-errored attempts), not a model number.
- Next: credits, then the full 64-case pilot draw (about $1 at the observed
  token rate), the eval-2 base rate on the task-first path, and only then a
  decision on SFT. If the base rate is at or above 90% correct-and-native, the
  pre-registration says stop training on this population.

## Codex review handoff

Artifacts: this report, `EVAL2.md` §11, the private bank path above, the
cancelled job id, run `eval-20260916-044656` in the private legacy tree
(`work/eval2/legacy-pilot`, not in git). Unresolved: billing; whether the
round proceeds to training at all given the early signal.
