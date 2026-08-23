<!-- treatment T7 overlay — one machine-checked revision -->
# Check your answer against the engine, once

The engine can tell you the answer instead of you guessing it. `lypning` is on
this machine and `lypning route -c '<program>'` prints, in one parse and with no
execution, which tier would take the program and — if it is not the fastest one
— the exact construct that stopped it.

For each task, after you have written your first program:

1. Run `lypning route -c '<your program>'`.
2. If it prints `lypning`, you are done.
3. If it prints anything else, it also prints the blocker. **Revise the program
   once** to remove that blocker, and keep the revision only if it is still
   correct. Then stop, whatever the second answer is.

Record the program you finally settled on. Do not revise more than once. If the
task genuinely cannot be done inside the subset, say so and keep the correct
program.
