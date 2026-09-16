# Codex assessment: question-production pilot comparison

Measured 2026-09-15 on repository revision
`d5e04433011f874cffc9cc26c5be4bfd047dcbca`, exact provider model
`qwen-3.8-27b`, OpenCode 1.18.31. Runs:
[medium](https://github.com/kristerhedfors/lypning/actions/runs/35023619011) and
[none](https://github.com/kristerhedfors/lypning/actions/runs/35024032962).
Both used the same six authored domain prompts requesting 20 proposals each,
temperature 0.7, top-p 0.8, six calls/session, 8,192 completion tokens/call and
49,152 reserved output tokens/session. These are sequential single-round arms,
not a randomized replicated quality estimate or a training experiment.

## Results, counted from preserved artifacts

| Arm | Successful provider responses | Reported input tokens | Reported output tokens | Length-truncated responses | Structurally valid proposals |
| --- | ---: | ---: | ---: | ---: | ---: |
| medium | 6 | 25,712 | 49,152 | 6 | 0 |
| none | 33 | 290,760 | 81,157 | 1 | 63 |

All six medium jobs returned green collection status but produced no question
files. Provider responses reported 8,192 reasoning tokens each, no answer
content/tool calls and `finish_reason=length`. OpenCode stopped without an error;
the earlier worker-health check mistook that for sufficient completion. This
is a delivery/instrumentation failure, not evidence that all reasoning is bad.

The nonthinking arm retained four question banks: text (five valid records),
numbers (20), encodings (18 valid plus one malformed final line), and automation
(20). Records and algorithms produced no final bank. Four jobs ended with
`request_budget_exhausted`; two were green under the old collection checks.
No upstream API failure was recorded. Partial files, generator code, tool events,
provider ledgers and session exports remain in the archives; they were inspected
as data, never executed on the host. Reported usage is not a billing reconciliation.

The first parser draft wrongly required string IDs even though the generation
instruction had not specified the ID type. The final inspector accepts legacy
nonnegative integer IDs without rewriting original records, and rejects booleans.
The table uses that corrected structural contract. No lexical prompt duplicate
was found among the valid records by whitespace/case normalization; this says
nothing about semantic independence or benchmark overlap.

## Content review: examples of why schema validity is not admission

- Numbers, id 1, reverses a base-conversion string but also prohibits leading
  zeros. Input N=10, B=10 exposes the contradiction: reversal requires `01`.
  Clarify the rule before constructing independent expectations.
- Automation, id 1, alternates between a left rotation and moving the last word
  to the front, and gives a K=1 edge case inconsistent with rotating a one-word
  prefix. Do not train a teacher's guessed resolution as the original task.
- Encodings, id 1, asks for arbitrary raw bytes and explicitly says no length
  limit, contrary to the bounded request and the current text-oriented verifier.
  Keep as a capability/contract-extension proposal or author a clearly marked
  hex-text derivative; do not silently narrow and relabel the original.
- Text, `q01-slugify`, mixes a precise Unicode White_Space claim with a long
  normalization pipeline and invalid-UTF8 stderr/exit behavior. It requires
  independent Unicode semantics and a wider observable contract before admission.

These are sampled findings, not a completed semantic review of all 63 proposals.
No proposal, answer or repair from these runs has been admitted to training.

## Decision: revise the collection recipe, retain the evidence

The next bounded profile is `question-proposals`: reasoning off, twelve requests
of at most 4,096 tokens each, unchanged 49,152-token session ceiling. Request five
concise questions per producer, explicit field types and bounded input contracts.
This reduces per-answer length and leaves more tool turns for creation/checking;
it is an engineering hypothesis requiring a new pilot, not a measured improvement.
The original comparison profiles remain reproducible and are not silently edited.

The controller now checks the expected question file/count/schema and provider
completion status before returning a green job. Missing/partial/truncated output
fails delivery while preserving every available artifact. Project collection also
requires a Python deliverable. The standalone inspector retains raw line identity,
original types and unknown metadata and never executes generator code.

Next owner/action: Codex validates the new profile, then independently reviews
semantics and task lineage. Review related bank seeds/derivatives together and
maintain across-round held-out exclusions; six repeated domain producers do not
automatically satisfy the independent-family pilot gate. Only then propose a new
reviewed reference bundle for Fable. Do not increase budgets or retry indefinitely.

Private local evidence is under `work/questions-35023619011/` and
`work/questions-35024032962/`. These directories do not transfer through Git;
Actions artifacts expire after 14 days, so use approved private preservation.
