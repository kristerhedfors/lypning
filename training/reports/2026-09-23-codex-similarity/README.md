# Are Codex/GPT-written captured programs materially similar to Claude-written ones? (2026-09-23)

**Verdict: NOT materially similar. All three pre-registered sub-criteria fail (A, B and C).** Under the rule fixed before any data was seen, GPT-written captured programs do not qualify to join the capture-tier training material for Qwen3.8-27B.

## Method (fixed before data)
- **Yardstick:** the variation between Claude models. C1 = claude-opus-5, C2 = claude-fable-5-1, C = C1 ∪ C2. G = programs from the Codex host, with all GPT models pooled.
- **(A) Static comparison.** Four distributions: capture_quality verdict/first-failing-rule mix, AST-node-size deciles, stdlib import histogram, construct histogram. Each uses base-2 JSD. G passes a distribution iff JSD(G,C) <= JSD(C1,C2). Bootstrap 90% CI: 1,000 resamples, seed 7. Scripts: `/tmp/sim/measure.py`, with results in `/tmp/sim/result.json`.
- **(B) Blind rating.** Three blind LLM judges scored four rubric dimensions on a 1-5 scale: correctness_plausibility, idiom, self_contained and subset_fit. An item's score is the mean over the four dimensions, then over the three judges; a pool's score is the mean over its items. G passes iff |G - C| <= |C1 - C2| + 0.25. The bootstrap resamples items within each pool (1,000 resamples, seed 7). Script: `/tmp/sim/combine.py`, with results in `/tmp/sim/combine_result.json`.
- **(C) Distinguishability.** Judges guessed the author (gpt or claude). The balanced sample is all 13 G items against 13 Claude items drawn at random with seed 5 (8 C1, 5 C2). The test is an exact one-sided binomial against 0.5. The primary statistic is the per-item majority vote of the three judges (n = 26 independent guesses). I chose it before computing any result. Per-judge results and pooled results are reported alongside it. G passes iff the result is not significant at alpha 0.05 OR accuracy <= 0.60. The original fallback, "no better than C1-vs-C2 accuracy", cannot be computed because judges only guessed gpt or claude. It is not used.
- **Handling of programs:** static analysis only, and nothing was executed. Claude and Codex programs were redacted the same way (harvest.redact + is_safe). Comments naming a tool, model or host were checked for: none were found, so no program was altered.

## Pool sizes
| Pool | all set (A) | tier-A | blind sample (B, C) |
|---|---|---|---|
| C1 claude-opus-5 | 4,064 | 186 | 40 (from 171 after dropping 15 that overlap bank/eval-2) |
| C2 claude-fable-5-1 | 862 | 24 | 24 (all) |
| C3 claude-opus-5-5 | 343 | 9 | not sampled (below 20) |
| G Codex, all GPT | 444 | 13 | 13 (all): 5.3-codex 6, 5.5 2, 5.6-sol 2, 5.4 1, 6-astra 1, 5.2-codex 1 |

Inputs: 5,673 Claude program occurrences and 474 Codex program occurrences in the log as of 2026-09-23. No GPT model reaches n >= 30 in tier-A, so there are no per-model results for B or C.

## (A) Static: FAIL
Results on the all set. The pass rule compares point values.

| Distribution | JSD(G,C) [90% CI] | JSD(C1,C2) [90% CI] | Result |
|---|---|---|---|
| verdict mix | 0.1363 [0.1154, 0.1648] | 0.0112 [0.0087, 0.0191] | FAIL |
| node deciles | 0.0130 [0.0093, 0.0265] | 0.0076 [0.0054, 0.0152] | FAIL (passes only against the loosest Claude pair, C1-C3 0.0139) |
| stdlib imports | 0.1198 [0.1124, 0.1418] | 0.0538 [0.0497, 0.0689] | FAIL |
| constructs | 0.0816 [0.0667, 0.1007] | 0.0149 [0.0115, 0.0231] | FAIL |

- **Permutation null:** the permutation-null means are 0.004-0.012, and every G-C excess has p <= 0.003. The differences are real, not an artefact of unequal pool sizes.
- **Every GPT model fails:** gpt-5.3-codex, gpt-5.4, gpt-6-astra and gpt-5.5 each fail verdict, stdlib and constructs. gpt-6-astra comes closest.
- **Tier-A only:** node deciles FAIL (0.2363 vs 0.1851), stdlib PASS (0.1898 vs 0.3367), constructs FAIL (0.2231 vs 0.0816), and verdict is not applicable. The permutation null is 0.09-0.18 at n = 13/24, so this set cannot support a decision.
- **What differs:** GPT programs use more type hints (0.122 vs about 0.008), f-strings (0.171 vs 0.04-0.065), dataclasses, decorators and classes, and fewer comprehensions. They also have more third-party imports (0.189 vs about 0.02), read more files (0.41 vs 0.24-0.28), write fewer files (0.158 vs 0.30-0.35) and import from the repo less (0.052 vs about 0.17).

## (B) Blind rating: FAIL (narrowly)
| Pool | n | mean score |
|---|---|---|
| G | 13 | 3.801 |
| C1 | 40 | 3.429 |
| C2 | 24 | 3.278 |
| C | 64 | 3.372 |

- |G - C| = **0.429**, 90% CI [0.213, 0.663].
- |C1 - C2| = 0.151, 90% CI [0.015, 0.444].
- Threshold = 0.151 + 0.25 = **0.401**. 0.429 > 0.401, so it fails. In 48.4% of bootstrap resamples the gap exceeds the threshold, so this failure is marginal and not robust.
- **Direction:** judges rated G higher than Claude. The criterion counts absolute distance, so a gap in either direction fails. The gap comes mostly from idiom (G 3.72 vs C 2.92) and self_contained (2.90 vs 2.35). correctness_plausibility (4.46 vs 4.35) and subset_fit (4.13 vs 3.86) are closer.
- **Per judge, G vs C:** 3.81 vs 3.45, 3.92 vs 3.32, 3.67 vs 3.34. All three judges rank G highest.

## (C) Distinguishability: FAIL
Balanced sample of 13 G vs 13 Claude. At least 18 of 26 correct is needed for p < 0.05.

| Judge | correct / 26 | accuracy | one-sided p | G recall | Claude recall |
|---|---|---|---|---|---|
| majority vote (primary) | 20 | 0.769 | 0.0047 | 0.615 | 0.923 |
| judge 1 | 20 | 0.769 | 0.0047 | 0.615 | 0.923 |
| judge 2 | 21 | 0.808 | 0.0012 | 0.846 | 0.769 |
| judge 3 | 20 | 0.769 | 0.0047 | 0.538 | 1.000 |
| pooled 78 guesses (not independent) | 61/78 | 0.782 | 2.8e-7 | | |

- **Result:** accuracy is significant and above 0.60, so C fails.
- **Robust to the Claude subset drawn:** across 1,000 alternative draws, majority accuracy has median 0.769 and 5th percentile 0.692, and 98.1% of draws are significant.
- **Balanced accuracy over all 77 items:** 0.72-0.77 per judge.
- **Rate of "gpt" majority guesses:** G 0.615, C1 0.15, C2 0.00.
- **Which GPT items were caught:** all 6 gpt-5.3-codex items were flagged. The 1 gpt-6-astra, 1 gpt-5.4 and 2 gpt-5.5 items were all taken for Claude. That is anecdotal at this n.
- **Judge agreement on the guess:** 0.66 (judges 1-2), 0.92 (1-3), 0.61 (2-3).

## Verdict
| Criterion | Result |
|---|---|
| A (static) | FAIL (3-4 of 4 distributions, robust) |
| B (rating) | FAIL (0.429 > 0.401, marginal) |
| C (distinguishability) | FAIL (0.769, p = 0.0047) |
| Overall (A AND B AND C) | **NOT materially similar** |

A is the decisive criterion. It has the largest samples, and its failure is far outside both the Claude-to-Claude yardstick and the permutation null. C agrees with it independently. B alone would be inconclusive.

## Caveats
- **Small samples:** tier-A has G = 13 and C2 = 24. B and C have little power, and B's failure sits on the edge of its bootstrap distribution. No per-GPT-model results for B or C, and no C3 results.
- **Judges share a model family with half the pools:** all three judges are Claude-family LLMs. They may prefer Claude-written code or recognise it. Here they rated G higher, which argues against a self-preference bias in B, but in C they may detect Claude's style more than GPT's (Claude recall 0.77-1.00 vs G recall 0.54-0.85). The three judges are not independent: judges 1 and 3 agree on 92% of guesses.
- **Static only:** nothing was executed, so runtime correctness and behaviour under lypning were not measured.
- **The task mix differs, not just the writer:** 84% of Codex occurrences ran outside this repository, and capture_quality counts another repo's local imports as third-party. This inflates the verdict and stdlib gaps. As a diagnostic, G restricted to Codex calls made inside lypning (n = 75) still fails all four A distributions (verdict 0.057 vs 0.011, nodes 0.031 vs 0.010, stdlib 0.173 vs 0.053, constructs 0.050 vs 0.015). The construct gap (type hints, f-strings, dataclasses) looks like authorial style, not context.
- **Deviations from the spec:** in B/C, C1 excluded 15 bank/eval-2-overlapping programs so that eval-2 text never reached the judges; this was not in the spec. Programs typed by several Claude models remain in each of those pools, which makes the yardstick stricter. Contamination was reported, not filtered (C1 0.154, C2 0.036, G 0.090).
- **The CI for the C1-C2 gap in B is wide** ([0.015, 0.444]). Even a looser Claude yardstick would leave B undecided, not passed. A and C are unaffected by this.

## Files
The scripts and aggregate results in this directory are the ones that ran, copied unchanged from the scratch directory `/tmp/sim`. They read and write there. The scratch files holding program bodies (`pools.json`, the judge batches and `key.json`) are not committed. The judges were three blind Claude subagents, run by the orchestrating session on 2026-09-23.

## Decision
Codex/GPT-written programs stay excluded from the capture tier. This is already the default in `nt capture-export`: other hosts and vendors need `--host`/`--allow-other-vendors`. Re-run this test before relaxing that default, with the same criterion and more GPT tier-A programs.
