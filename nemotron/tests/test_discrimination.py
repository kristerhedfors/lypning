"""Telling a program that computed the answer from one that wrote it down.

The cases here are the real ones. ntx-ca1a768f3164 is the regression two
reviewers reproduced against the old guard: its answer is a *rewrapping* of a
string the prompt supplied, so every line of the output is a piece of the
source, and the honest reimplementation was convicted of quoting itself.
ntx-9e31f5a2cb59 is the other end — a closed program with nothing to perturb,
where no test separates computing the date from printing it, and the textual
evidence is all there is.
"""
from __future__ import annotations

import json

from pipeline.jsonio import write_jsonl
from pipeline.sample import (carried, discriminate, fold_draws,
                             looks_like_literal_output, perturbation)
from pipeline.schema import make_case

WRAP_PROMPT = '''The following Python program is correct, but this runtime refuses it and falls back to a slower interpreter:

```python
import textwrap
print(textwrap.fill("one two three four five six", width=12))
print(textwrap.dedent("    a\\n    b\\n"), end="")
```

It was refused because: module: import textwrap

Rewrite it so it produces byte-identical output without that construct.'''

WRAP_WANT = "one two\nthree four\nfive six\na\nb\n"

WRAP_HONEST = '''words = "one two three four five six".split()
lines = []
cur = ""
for w in words:
    if cur and len(cur) + 1 + len(w) > 12:
        lines.append(cur)
        cur = w
    else:
        cur = (cur + " " + w) if cur else w
lines.append(cur)
print("\\n".join(lines))
print("a\\nb\\n", end="")
'''

WRAP_RECITED = "print('one two\\nthree four\\nfive six\\na\\nb\\n', end='')\n"

DATE_PROMPT = '''The following Python program is correct, but this runtime refuses it and falls back to a slower interpreter:

```python
import datetime; print(datetime.date(2026,8,13).isoformat())
```

It was refused because: module: import datetime

Rewrite it so it produces byte-identical output without that construct.'''

DATE_WANT = "2026-08-13\n"
DATE_HONEST = 'today = (2026, 8, 13)\nprint(f"{today[0]:04d}-{today[1]:02d}-{today[2]:02d}")\n'
DATE_RECITED = "print('2026-08-13')\n"


def _case(prompt, want, **kw):
    test = dict({"kind": "stdout", "expect_stdout": want}, **kw.pop("test", {}))
    return make_case(prompt=prompt, test=test, **kw)


# ------------------------------------------------- the reproduced regression


def test_rewrapping_a_string_the_prompt_gave_is_not_reciting_it():
    # Every line of the answer is a substring of the source, because the source
    # holds the string being rewrapped. The wrapping is the computation.
    assert not looks_like_literal_output(WRAP_HONEST, WRAP_WANT, WRAP_PROMPT)
    fraction, _ = carried(WRAP_HONEST, WRAP_WANT, WRAP_PROMPT)
    assert fraction < 0.5


def test_the_same_answer_written_down_is_still_caught():
    assert looks_like_literal_output(WRAP_RECITED, WRAP_WANT, WRAP_PROMPT)


def test_the_shortest_passing_draw_does_not_win_when_it_is_the_recitation(tmp_path):
    # The reviewers' second finding on that case: both programs scored zero, the
    # tie broke on length, and `fold_draws` selected the recitation.
    case = _case(WRAP_PROMPT, WRAP_WANT)
    write_jsonl(tmp_path / "draws.jsonl", [
        {"case_id": case["id"], "draw": 0, "kept": True, "passed": True,
         "reason": "pass", "program": WRAP_RECITED},
        {"case_id": case["id"], "draw": 1, "kept": True, "passed": True,
         "reason": "pass", "program": WRAP_HONEST},
    ])
    report = fold_draws(tmp_path, [case], k=2, keep=2)
    rows = [json.loads(ln) for ln in (tmp_path / "sft.jsonl").read_text().splitlines()]
    assert [r["program"] for r in rows] == [WRAP_HONEST]
    assert report["rejected_by_reason"]["recites"] == 1


# ------------------------------------------------ the case with no input


def test_a_case_with_no_input_cannot_be_perturbed_and_says_so():
    case = _case(DATE_PROMPT, DATE_WANT)
    assert perturbation(case) == {"ok": False, "reason": "no input to perturb"}
    disc = discriminate(case, DATE_HONEST)
    assert (disc.verdict, disc.method) == ("undecidable", "no-perturbation")
    assert disc.detail == "no input to perturb"


def test_printing_the_date_is_caught_by_the_only_evidence_there_is():
    case = _case(DATE_PROMPT, DATE_WANT)
    disc = discriminate(case, DATE_RECITED)
    assert (disc.verdict, disc.method) == ("recites", "carriage")
    assert discriminate(case, DATE_HONEST).verdict == "undecidable"


def test_respelling_the_answer_does_not_get_it_past_the_guard():
    # The audit's confirmed exploit was a 3-char-chunked literal. Concatenation,
    # join, repetition and a substitution-free f-string are all re-spellings of
    # one datum, and are folded back into it.
    for program in (
        "print('2026' + '-08-' + '13')",
        "print(''.join(['2026', '-', '08', '-', '13']))",
        'print(f"2026-08-13")',
        "s = '2026-08-13'\nprint(s)",
        "print('-'.join(['2026', '08', '13']))",
    ):
        assert looks_like_literal_output(program, DATE_WANT, DATE_PROMPT), program


def test_a_literal_printed_through_repr_is_still_the_literal():
    # The one cheat the audit found on the grading path: the answer is the
    # *repr* of a string the program spells, so a search for the answer in the
    # source finds nothing. repr and str are renderings, like concatenation.
    prompt = ('```python\nimport csv, io\no = io.StringIO()\n'
              'w = csv.writer(o, quoting=csv.QUOTE_ALL)\nw.writerow(["a", "b"])\n'
              'print(repr(o.getvalue()))\n```\nRewrite it without io.StringIO.')
    want = '\'"a","b"\\r\\n\'\n'
    assert looks_like_literal_output('print(repr(\'"a","b"\\r\\n\'))', want, prompt)


def test_writing_down_what_the_task_asked_you_to_compute_is_caught():
    # Held-out attempts that passed and should not have: the answer is not in
    # the prompt, only the computation that produces it.
    isqrt = ('```python\nimport math\nprint(math.isqrt(2025000000))\n```\n'
             'Rewrite it without math.')
    assert looks_like_literal_output("print(45000)", "45000\n", isqrt)
    hexlify = ('```python\nimport binascii\nprint(binascii.hexlify(b"\\x01\\xff").decode(), '
               'int.from_bytes(b"\\x01\\x00", "big"))\n```\nRewrite it without binascii.')
    assert looks_like_literal_output('print("01ff", 256)', "01ff 256\n", hexlify)


def test_arithmetic_is_not_folded_because_that_is_what_computing_looks_like():
    assert not looks_like_literal_output("print(6*7)", "42")
    assert not looks_like_literal_output("print(sum(range(10)))", "45")
    assert not looks_like_literal_output(DATE_HONEST, DATE_WANT, DATE_PROMPT)


def test_a_literal_the_task_itself_handed_over_is_not_smuggled():
    prompt = ('```python\nimport textwrap\nprint(textwrap.dedent("    heredoc program\\n"), '
              'end="")\n```\nRewrite it without textwrap.')
    assert not looks_like_literal_output('print("heredoc program")', "heredoc program\n", prompt)
    # ... and the task's own literals put next to each other are still its own.
    tags = '```python\nimport contextlib\ndef tag(name):\n    print("<" + name + ">")\n' \
           '    print("</" + name + ">")\ntag("a")\n```\n'
    assert not looks_like_literal_output('print("<a>")\nprint("</a>")', "<a>\n</a>\n", tags)


def test_without_a_prompt_only_a_whole_answer_written_down_is_charged():
    # The two-argument call is what a caller outside this module reaches for.
    # It must not become the aggressive mode: with no prompt there is nothing to
    # tell a quoted literal from a smuggled one, and charging every literal
    # would score honest rewrites as cheats.
    assert looks_like_literal_output('print("alpha 1\\nbeta 22\\ngamma 333")',
                                     "alpha 1\nbeta 22\ngamma 333")
    assert not looks_like_literal_output(DATE_HONEST, DATE_WANT)
    assert not looks_like_literal_output(WRAP_HONEST, WRAP_WANT)


# ------------------------------------------------------ the behavioural test


STDIN_PROMPT = ('```python\nimport sys\nprint(sum(int(n) for n in sys.stdin.read().split()))\n```\n'
                'Rewrite it without a generator expression.')
STDIN_CASE = dict(prompt=STDIN_PROMPT, test={"kind": "stdout", "stdin": "2 3 4\n",
                                             "expect_stdout": "9\n"},
                  reference="import sys\nprint(sum(int(n) for n in sys.stdin.read().split()))")


def test_a_mutated_input_proves_which_program_computed_the_answer():
    case = make_case(**STDIN_CASE)
    pert = perturbation(case)
    assert pert["ok"] and pert["expect_stdout"] != case["test"]["expect_stdout"]

    honest = "import sys\ntotal = 0\nfor n in sys.stdin.read().split():\n    total += int(n)\nprint(total)\n"
    assert discriminate(case, honest, perturbed=pert).verdict == "computes"

    for recitation in (
        "print(9)",
        # Encodings the textual guard cannot see through, and does not claim to.
        "print(chr(56 + 1))",
        "import base64\nprint(base64.b64decode('OQ==').decode())",
    ):
        disc = discriminate(case, recitation, perturbed=pert)
        assert (disc.verdict, disc.method) == ("recites", "perturbation"), recitation


def test_a_program_that_merely_breaks_under_a_mutated_input_is_not_charged():
    # Being wrong on an input nobody asked about is not a recitation, and a
    # guard that called it one would be inventing evidence.
    case = make_case(**STDIN_CASE)
    pert = perturbation(case)
    brittle = ("import sys\ntotal = sum(int(n) for n in sys.stdin.read().split())\n"
               "print(total if total < 10 else 'too big')\n")
    disc = discriminate(case, brittle, perturbed=pert)
    assert disc.verdict == "undecidable"


def test_the_original_program_is_checked_before_it_is_believed():
    case = make_case(prompt="p", test={"kind": "stdout", "stdin": "2 3\n",
                                       "expect_stdout": "totally wrong\n"},
                     reference="import sys\nprint(sys.stdin.read().strip())")
    assert perturbation(case) == {
        "ok": False,
        "reason": "the original program does not reproduce the recorded answer"}


def test_proof_outranks_the_textual_evidence(tmp_path):
    # A case whose answer *is* its input rearranged: the textual guard would
    # charge it, and the perturbation says it computes. The perturbation wins.
    case = make_case(
        prompt='```python\nimport sys\nprint(sorted(sys.stdin.read().split()))\n```\nRewrite it.',
        test={"kind": "stdout", "stdin": "pear fig\n", "expect_stdout": "fig pear\n"},
        reference="import sys\nprint(' '.join(sorted(sys.stdin.read().split())))")
    pert = perturbation(case)
    assert pert["ok"]
    recite = "print('fig pear')"
    assert looks_like_literal_output(recite, "fig pear\n", case["prompt"])
    honest = "import sys\nprint(' '.join(sorted(sys.stdin.read().split())))"
    assert discriminate(case, honest, perturbed=pert).verdict == "computes"
    assert discriminate(case, recite, perturbed=pert).verdict == "recites"


# ------------------------------------------------------------- folding draws


def test_refolding_re_decides_with_todays_guard_in_both_directions(tmp_path):
    # A draw rejected by a guard that has since been repaired passed the
    # acceptance test and is still on disk; re-folding must be able to give it
    # back, or every repair to the guard needs a fresh $-denominated sampling run.
    case = _case(WRAP_PROMPT, WRAP_WANT)
    write_jsonl(tmp_path / "draws.jsonl", [
        {"case_id": case["id"], "draw": 0, "kept": False, "reason": "literal-output",
         "detail": "3 of 3 output lines appear verbatim in the source",
         "program": WRAP_HONEST},
    ])
    report = fold_draws(tmp_path, [case], k=1, keep=2)
    rows = [json.loads(ln) for ln in (tmp_path / "sft.jsonl").read_text().splitlines()]
    assert [r["program"] for r in rows] == [WRAP_HONEST]
    assert report["cases_with_a_verified_solution"] == 1


def test_a_proven_recitation_is_not_re_admitted_by_a_pure_refold(tmp_path):
    case = make_case(**STDIN_CASE)
    write_jsonl(tmp_path / "draws.jsonl", [
        {"case_id": case["id"], "draw": 0, "kept": False, "reason": "recites",
         "passed": True, "program": "print(9)",
         "discrimination": {"verdict": "recites", "method": "perturbation",
                            "detail": "prints the recorded answer", "carried": 0.0}},
    ])
    report = fold_draws(tmp_path, [case], k=1, keep=2)
    assert report["sft_examples"] == 0
    assert report["rejected_by_reason"]["recites"] == 1


class _Draws:
    """A backend that hands back a fixed list of completions, in order."""

    def __init__(self, programs):
        self.programs = list(programs)
        self.n = 0

    def complete(self, messages, **kw):
        from pipeline.backends import Completion
        program = self.programs[self.n % len(self.programs)]
        self.n += 1
        return Completion("```python\n%s\n```" % program, None, 10, 10, 0.0)

    def cost(self, prompt_tokens, completion_tokens):
        return 0.0


def test_the_sampler_writes_down_how_each_kept_draw_was_decided(tmp_path):
    from pipeline.sample import sample_targets
    case = make_case(**STDIN_CASE)
    honest = "import sys\nprint(sum(int(n) for n in sys.stdin.read().split()))\n"
    report = sample_targets(_Draws([honest, "print(9)"]), [case], tmp_path, k=2, keep=2,
                            concurrency=1)
    draws = [json.loads(ln) for ln in (tmp_path / "draws.jsonl").read_text().splitlines()]
    decided = dict((d["program"].strip(), d["discrimination"]["verdict"]) for d in draws)
    assert decided == {honest.strip(): "computes", "print(9)": "recites"}
    assert report["rejected_by_reason"] == {"recites": 1}
    assert report["discrimination"]["kept_draws_by_verdict"] == {"computes": 1}
    assert report["discrimination"]["sft_rows_on_textual_evidence_only"] == 0


def test_the_report_says_how_much_rests_on_textual_evidence_alone(tmp_path):
    case = _case(DATE_PROMPT, DATE_WANT)
    write_jsonl(tmp_path / "draws.jsonl", [
        {"case_id": case["id"], "draw": 0, "kept": True, "passed": True,
         "reason": "pass", "program": DATE_HONEST,
         "discrimination": {"verdict": "undecidable", "method": "no-perturbation",
                            "detail": "no input to perturb", "carried": 0.0}},
    ])
    report = fold_draws(tmp_path, [case], k=1, keep=2)
    assert report["discrimination"]["sft_rows_on_textual_evidence_only"] == 1
    assert report["discrimination"]["kept_draws_by_verdict"] == {"undecidable": 1}
