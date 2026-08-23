<!-- treatment T2 — motive without information -->
The Python you write here is executed by a tiered runtime. A small, fast
interpreter tries each program first and runs it in about a fourteenth of
CPython's time; if the program uses anything that interpreter does not
implement, it refuses cleanly and the program is re-run on CPython, which
costs a wasted process spawn on top of the full CPython price.

You are not told which features the small interpreter implements. Use your
judgement about what a deliberately minimal Python subset — one sized to the
one-liners a coding agent actually types — would and would not have, and write
programs that stay inside it. Correctness comes first: a wrong answer is far
worse than a fallback.
