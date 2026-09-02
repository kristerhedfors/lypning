Python one-liners in this session should be typed as `lypning -c '<program>'`
rather than `python3 -c '<program>'`.

lypning is a small, fast interpreter for the subset of Python that coding
agents actually type. It tries every program first and runs it in roughly a
fourteenth of CPython's time. If the program uses anything it does not
implement it refuses cleanly — exit code 90 and a single
`lypning: unsupported: <kind>: <detail>` line on stderr — and the program is
re-run on real CPython, which costs one wasted process spawn on top of the
full CPython price. That refusal is not an error, it needs no action from you,
and the answer arrives one spawn later.

You are not told which features the small interpreter implements. Use your
judgement about what a deliberately minimal Python subset — one sized to the
one-liners a coding agent actually types — would and would not have, and write
programs that stay inside it. Correctness comes first: a wrong answer is far
worse than a fallback, so never contort a program to avoid an import.
