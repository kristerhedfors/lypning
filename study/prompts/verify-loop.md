<!-- treatment T8 overlay — iterate against the engine until it runs -->
# Check your answer against the engine, and iterate

The engine can tell you the answer instead of you guessing it, and it can do it
twice over — statically and by running the program.

* `lypning route -c '<program>'` prints which tier would take the program, and
  the exact construct that stopped the fastest one. One parse, no execution.
* `lypning -c '<program>'` actually runs it on the fastest tier. **Exit 90 with
  one `unsupported: <kind>: <detail>` line on stderr means the program left the
  subset at run time** — some refusals (64-bit integer overflow, set iteration
  order, `os.listdir`) are invisible to the parser and only appear this way.
  Any other exit code is the program's own.

For each task:

1. Write a program.
2. Run `lypning route -c '<program>'`. If it does not say `lypning`, remove the
   blocker it names and go back to 2.
3. Run `lypning -c '<program>'` with the task's stdin and arguments if it has
   any. If it exits 90, remove the construct the refusal names and go back to 2.
4. Stop when it runs at exit 0 with the right answer, or when you are convinced
   the task cannot be done inside the subset — in which case keep the correct
   program and say so.

**Correctness is the gate, not the tier.** Never trade a right answer for a
subset-clean one; a program that stays in the subset by printing the wrong thing
is the worst outcome available. Cap yourself at about six attempts per task.
