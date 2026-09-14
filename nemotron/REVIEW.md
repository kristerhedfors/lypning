# Response to the LoRA training review (rev 2)

*Written 2026-09-14 against the review prepared the same day. Every claim below
was checked against the tree the review could not read; where the review is
right this says so and names the fix, where it is wrong this says which line of
which file makes it wrong, and where checking it turned up something the review
never mentioned that is here too.*

The review's own §0 is the reason this document exists: it read `README.md`
§7b, the changelog and `CLAUDE.md`, and it could not read `nemotron/`. That is a
real limit and it declares it honestly. It also means roughly a third of the
findings are about code whose contents were inferred. Four of those inferences
are wrong, one is stale by two revisions, and the largest one — the central
argument of §2.5 — is right, was cheap to test, and had already been recorded
in this repository's own data without anybody computing it.

---

## 0. The headline

**The review's central claim is correct. Acting on it does not rescue the
result — it explains it, and reattributes the gain.**

§2.5 argues that the pre-registered rule tested the wrong hypothesis: exact
McNemar on solved/not-solved asks whether the model *acquired* the ability to
solve new problems, while the project's question is a prior shift — given that
the model can already write a program, how often does it write one the engine
will run? That is right, and the fix it proposes (subset-legality rate over
programs, cluster-bootstrapped by task, with correctness, import-retention and
length as gates) is the right endpoint.

It was also computable for **$0**, because the run of record stored every
program it generated. No GPU, no tokens, no new sampling: 2,362 gradeable
programs already paid for, replayed through the pinned engine. That is now
`nt legality`, and §1 is what it says.

It does not say what the review expected it to say.

---

## 1. The endpoint the review asked for, measured on the run of record

`nt legality <base-run> <tuned-run>` replays every stored program through the
pinned engine and reports SLR per arm, the paired delta cluster-bootstrapped by
case, the by-kind refusal vector, and the three gates. It generates nothing, so
it costs CPU and no money at all.

Two things had to be true before the number meant anything, and one of them was
not: the engine has to build (§2), and both arms have to go through **one**
engine — `legality.compare` refuses two fingerprints in one comparison, so that
is enforced rather than hoped for.

### The result

```
subset-legality: qwen38-base-arm-v3 -> qwen38-lora-r16-v3   @ engine e7f9d99d69688a8b
  2362 programs graded, 74 cases, cluster-bootstrapped by case
  27 of them flagged not-genuine (a hard-coded literal is legal too)

  SLR   base 42.61%   tuned 41.47%
  dSLR  -1.00pp   95% CI [-3.36, +1.51]
  MDE   lower bound must exceed +3.00pp — NOT met

  gates (a failed gate voids the result; it does not discount it)
    PASS  A correctness non-regression   41.78% -> 46.71%, +4.92pp, floor -2.00pp
    PASS  B supported-import retention   95.26% -> 100.00% over 253 programs, retention 1.05
    PASS  C length non-inflation         471 -> 380 tokens, -19.3%, cap +20%
```

And the null test, which is what says the interval above can be believed. The
same base model, the same sampling, the same k — served once through a provider
and once in-container, which is as close to "two draws of one thing" as this
data has:

```
subset-legality: qwen38-baseline-k16 -> qwen38-base-arm-v3   @ engine e7f9d99d69688a8b
  SLR   base 42.88%   tuned 42.61%
  dSLR  -0.22pp   95% CI [-2.46, +2.03]
  gate A correctness  40.52% -> 41.78%, +1.26pp
```

| | ΔSLR | 95% CI | Δcorrectness |
|---|---|---|---|
| base → tuned (the experiment) | **−1.00pp** | [−3.36, +1.51] | **+4.92pp** |
| base → base (the null) | −0.22pp | [−2.46, +2.03] | +1.26pp |

Two intervals of almost the same width, both containing zero.

### What it says

**There is no prior shift to find.** ΔSLR is **−1.00pp**, its interval straddles
zero, and it does not come close to the +3pp the review proposes as a minimum
detectable effect. The tuned model does not write subset-legal python more often
than the base model does.

**The correctness gain is real and is not a legality gain.** Gate A moves
**41.78% → 46.71%, +4.92pp** over the same programs — the effect §6 recorded,
arrived at from the other side. So the +4.46pp of the run of record is the model getting more
answers *right*, not the model staying inside the subset more often. Those were
the two reasons a correctness pass rate can move, the review said the blended
number could not separate them, and separating them attributes the whole of it
to the first.

**It did not get there by cheating, and the gates are what say so.** Import
retention went *up*, not down — no "never import" collapse, which is the
degenerate maximum a legality endpoint is most exposed to. Tokens per program
went *down*, so the correctness gain was not bought with output length. Both
gates pass in the direction that costs nothing, which is worth stating because a
gate that passes for the wrong reason reads identically in a table.

**And the null says the interval is real.** Two draws of the *same* base model
through two serving stacks move ΔSLR by −0.22pp with an interval of almost
exactly the same width. The experiment's −1.00pp is not distinguishable from
that. The same null moves correctness by +1.26pp against the experiment's
+4.92pp — so the correctness endpoint separates these two arms by about four
times its own noise, and the legality endpoint does not separate them at all.
That contrast is the finding in one line.

**The by-kind vector shows motion the scalar hides.** The `class` kind fell by
27 programs and `module-attr` rose by 31: the tuned model reaches for *different*
things, not for fewer of them, and a scalar that nets those two to roughly
nothing is telling the truth about the total and nothing about the behaviour.
This is the projection the review asks to be reported beside the scalar, and it
is why — a flat ΔSLR is not the same statement as "nothing changed".

### What this does to the review's argument

The review's §2.5 is right that §3's second leg tested the wrong hypothesis, and
right that SLR is the endpoint that matches the goal. Its conclusion does not
follow. It expects the endpoint change to reveal a prior shift the old rule was
blind to — "the experiment could only be reported as a failure no matter how
well the prior shifted". The prior did not shift. The old rule reported a
failure, and on the review's own endpoint the failure is confirmed rather than
reversed.

That makes the endpoint change *more* worth making, not less. Under §3 the
verdict was "no win, and the two legs disagree", which leaves open why. Under
§7 the answer is specific and actionable: the SFT set taught the model to solve
these tasks slightly better and taught it nothing about the subset. The review's
F5 says why that is unsurprising and nobody had noticed — the sampling prompt
never mentions the subset, the refusal, or lypning, in either arm. The adapter
was trained on programs selected for legality but was never shown the constraint
that selected them. Context distillation (sample *with* the refusal line in the
prompt, train on the bare prompt) is the one technique aimed straight at that
gap, and it has not been tried.

### Reading this number honestly

- **It is exploratory.** The endpoint was chosen after the run finished, by
  someone who had seen the run's outcome. `PREREGISTRATION.md` §7a says so, and
  §6 is not revised by it.
- **It is relative to one engine**, quoted with its fingerprint. A different
  build gives a different SLR for both arms and is not comparable to this one.
- **The population is the refusing tail** (§3 of this document), not the
  deployment distribution. If the effect were real it would look *largest* here.
- **One seed.** The review's F7 is right that a single-seed delta is not a
  result. It is a smaller worry for a null than for a win — a seed that swung
  the answer would have to swing it past an interval that contains zero from
  both sides, and the base-vs-base null bounds how far a re-draw of one arm
  moves this number. It is not zero, and v2 runs three.
- **The gates passed, which is not the same as the gates being informative.**
  They were pre-registered against a *win*: they exist to stop a legality gain
  that was bought with correctness, imports or tokens. Against a null there was
  nothing to buy. What they usefully establish here is only that the +4.92pp of
  correctness was not itself bought — not with length (tokens fell 19%) and not
  by abandoning imports the engine serves (retention rose).
## 2. What pinning the engine turned up, which the review could not have seen

The review's F2 says: freeze the engine binary for the whole window, make it a
precondition rather than a post-hoc check. Doing that was the first action, and
it did not get as far as freezing. The engine did not build.

**`lypning-l` has been unbuildable on `main` since PR #63.** That commit added
`BinOp::MatMul` to the AST and guarded it in `Interp::binop` — correctly, and
for a good reason, because reaching the numeric fast path with `@` found an
`unreachable!()` and aborted the process at exit 134. What it missed is that
`bigint::int_op` matches exhaustively on `BinOp` and lives behind `cap-bigint`,
which only the `variant-l` feature set turns on. `variant-m` compiled; the
capability build did not:

```
error[E0004]: non-exhaustive patterns: `ast::BinOp::MatMul` not covered
  --> src/bigint.rs:548:14
```

Three things follow, and the third is the one that matters.

**It was visible and reported.** `lypning build --rust` printed
`FAILED: cargo build failed (exit 101)` in its own table and returned exit 1.
Nothing was hidden. It went unnoticed because the table was read and the exit
code was not — the build output in that session was piped through `tail`, which
reports the pipe's status and not the builder's.

**Every conformance run since then graded a stale binary.** `engines.find`
reads `$LYPNING_HOME/bin` before any cargo target, and the `lypning-l` sitting
there was built on 2026-09-12 23:36 — *before* the nineteen wrong answers PR #63
closed. So "MISMATCH 0" on that PR and the one after it was a true statement
about `lypning` and an untested one about `lypning-l`, and the routing figures
(`dispatchers agree`, IDEAL/LATE/WASTED) were computed against an engine nine
days older than the tree.

**With `lypning-l` rebuilt, conformance reports MISMATCH 7.** Those are not new
bugs introduced here; they are bugs that have been in the capability build the
whole time, invisible because the binary being graded predated them. Invariant 1
is unconditional, so they are being closed before any number from this session
is quoted — §5.

### The fix

One home for the message, in `err.rs`, reached from both paths:

```rust
pub fn matmul_type_err(a: &str, b: &str) -> LypningError {
    type_err(format!("unsupported operand type(s) for @: '{}' and '{}'", a, b))
}
```

`Interp::binop` keeps its guard and now calls it; `bigint::int_op` gains the arm
the compiler demanded. That arm is unreachable — the guard answers first — and
it **answers rather than panicking** anyway, because a panic there is exit 134,
which is not the program's own exit code, so the dispatcher cannot hand it back
and the caller learns nothing. That is precisely the failure `@` already cost
this engine once, and an `unreachable!()` in the second location would have
reintroduced it.

Deduplicating the format string made `lypning` 48 code bytes smaller. Both
variants build: `lypning` 1,142,992 B / 9 blocks, `lypning-l` 1,319,120 B /
11 blocks, inside the 9 and 32 the gate enforces.

### And a differential check on the operator, since it was open

Sixteen shapes of `@` and `@=` against CPython on both engines. All agree except
one, and it is not `@`'s:

```
x = 1; x += "a"   lypning: unsupported operand type(s) for +:  'int' and 'str'
                  CPython:  unsupported operand type(s) for +=: 'int' and 'str'
```

Every augmented operator in this engine reports the plain symbol where CPython
reports the augmented one — `+=`, `-=`, `**=`, `|=` and `@=` alike. Same
exception type, same exit code, one character of message, and uniform. It was
left before on the judgement that threading an augmented flag through the binop
path was not worth the coupling; that judgement now has the evidence under it
rather than the assertion, which is the only thing that changed.
## 3. Where the review is wrong, or stale

### F10 — "recipe details I could not verify". All nine were already right.

The review lists these as open. Every one is in `gpu/lypning_lora.py` and every
one is on the recommended side. This is the finding to strike, not to action:

| F10 item | What the file does | Where |
|---|---|---|
| completion-only loss masking | prompt tokens set to `-100`; an example that masks to nothing is dropped | `build_examples`, :161–187 |
| identical chat template, thinking off, train vs eval | one `apply_chat_template(..., enable_thinking=False)` call shape in all three places | :174, :495, :655 |
| epochs | 3, inside the review's own 2–3 recommendation | `--epochs`, :565 |
| LoRA alpha | 32 at rank 16 — exactly the 2r default the review asks for | `--alpha`, :570 |
| MLP projections targeted | `gate_proj`, `up_proj`, `down_proj` over all 64 MLPs | `TARGET_MODULES`, :138 |
| dropout | 0.0 | `--lora-dropout`, :571 |
| warmup | 5 steps, clamped to a quarter of total | :414, :574 |
| effective batch / checkpointing | global batch 16, micro 1, `use_reentrant=False` | :312, :566 |
| packing | not used — correct for this size, and the review agrees it is conditional |  |

The one item worth keeping from F10 is the validation split, and it is already
right too: `--val-frac 0.1` is carved from the *train* side, never from the
frozen held-out set (:648).

There is a real hyperparameter finding here, but it is not in F10 — see §4.

### F4 — "the prompt format must be the agent's format". It already is.

The review reads rev 1 as training on *refused program → rewrite* and asks for
*task → program*. `evaluate.py:82` renders exactly two messages: a system
prompt that says "You are a precise Python programmer", and a user turn that is
the task plus a runtime contract. **Neither mentions lypning, the subset, or a
refusal.** Both arms see the identical prompt; `prompt_sha` pins it and `nt
grade` refuses a run whose template drifted.

So the adapter had to move the prior with no prompt-side help at all — which
makes the review's F5 recommendation (sample *with* the refusal line in
context, train on the bare prompt) not a refinement of what was done but an
untried lever. It survives as the single best-matched unexploited technique,
and §4 keeps it.

### F3 — "the evaluation population was selected on the outcome". Partly.

The mechanism is not the one stated. Held-out is 74 cases:

| population | n | selected how |
|---|---|---|
| `refused:*` | 52 | a program in the corpus refused |
| `ceiling:*` | 14 | the task cannot be done in the subset at all |
| `unobserved` | 8 | written for `study/`; no refusal involved |

Two corrections. The 8 `unobserved` cases were never conditioned on a refusal,
so the set is not "all cases that refused". And the 66 that were came from
**this repository's corpus, which Claude Code wrote** — not from Qwen. The
selection is on a *different model's* prior, which is a milder confound than
"conditioned on base-model refusal" and predicts something the review's version
does not: base-arm legality on these tasks should be well above zero rather
than "near zero by construction". §1 measures it, and it is.

The substantive point survives both corrections: this is the refusing tail of
the task distribution, it is not the deployment population, and the deployment
number needs an unconditioned task sample. That is registered in §4.

### F1 — the arithmetic is two revisions stale.

F1 reasons from "3,688 corpus entries became 93 usable sampling prompts" and
"222 examples from 66 unique rewrite problems". Those were true on 2026-09-11.
The corpus has since been *derived* rather than merely published — the
distinction `README.md` §7 now explains — and the counts loaded on 2026-09-14
are: **9,064 classified entries, 517 cases, 443 train, 74 held-out**, with a
sampling pool of 294. The ceiling F1 describes had already tripled before the
review was written.

The structural argument is untouched and is still the largest lever in the
review: under a legality endpoint an item does not have to be a captured
refusal, so the supply stops being bounded by what happened to break. §4 keeps
it, at the corrected scale.

### F8 — "match the thinking condition to the deployment condition". Already matched.

Thinking is off in both arms, at sampling and at eval, and it is one of the
five fields `nt grade` treats as the arm's identity — changing it makes a run
incomparable rather than merely different (`cli.py`, `--samples`/`--temperature`
block). The review's recommendation and the code agree. The secondary
thinking-on arm it asks for is worth having and is registered in §4.
## 4. What stands, and where it went

Seven findings survive checking. None of them is answered by a paragraph, so
none of them is answered here: they are rules, and rules belong in the document
that fixes rules before a run. `PREREGISTRATION.md` **§7** now carries them, and
§7a states plainly that it was written *after* the §1 re-analysis was computed —
a threshold chosen with the answer visible is not pre-registered, and saying so
is the only thing that makes it usable.

| review | what stands | where |
|---|---|---|
| §2.5 | SLR as the single primary leg, cluster-bootstrapped by task, MDE +3pp, gates rather than a second significance leg | §7b, §7c — **and shipped as `nt legality`** |
| F2 | engine frozen by fingerprint as a *precondition*; build order never drawn from held-out | §7d — **shipped**: `nt grade --require-fingerprint`, and `nt refusals --held-out` now refuses to call its own output a build order |
| F3 | unconditioned task sample as the primary population, refusing tail as a named secondary, the frozen 74 kept for continuity | §7e |
| F1 | an item need not be a captured refusal; the task bank is buildable and the ceiling is not where rev 1's arithmetic put it | §7f |
| F5 | hinted sampling → unhinted training, which the current pipeline does not do at all | §7f |
| F6 | the verifier is a reward; the failing half of every draw is free preference data | §7f |
| F7 | ≥3 training seeds, per-seed ΔSLR and the spread — a single-seed delta is not a result | §7f |

§7f costs money and **has not been run**. It is an estimate to authorise or
refuse, not a plan in progress: task bank ~$30, baseline ~$5, SFT v2 ~$30–60,
RLVR ~$50–150. Everything in §7a–§7e is rules, costs nothing, and is in force
for whatever runs next whether or not §7f is ever funded.

---

## 5. What this session did not do

**It did not re-run anything on a GPU.** Every number in §1 comes from programs
the run of record already generated and stored. Total spend for this document
and the code under it: **$0.00**.

**It did not revise §6 of the pre-registration.** The run of record was decided
under §3's rule and §3's rule said no win. A rule replaced after seeing the data
does not reach back and re-decide a run that was already called; `REVIEW.md` §1
is exploratory re-analysis and is labelled as such everywhere it appears.

**It did not re-cut the split.** F9's fix — cluster before splitting, assign
whole clusters group-wise — is right, and applying it would change the frozen
held-out set, which would make every existing run incomparable with every future
one. It is registered as v2's splitting procedure (§7f) and is not applied to
v1's lock.

**It did not touch the ceiling slice's role.** The review's gate B reassigns it
from contributor to anti-gaming control. That is §7c, and it changes what the
ceiling slice is *for* in v2 without touching what it did in v1.
