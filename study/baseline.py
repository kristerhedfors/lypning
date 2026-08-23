"""The no-prompting baseline, taken from the field rather than from a control cell.

Every generator agent in this study sits inside this repository, whose CLAUDE.md
announces that the project is about a Python subset. That is a real confound on
the control cell and it cannot be removed by asking an agent to forget. So the
control is anchored against something the confound cannot reach: the corpus
itself — programs captured from real agent sessions that were doing something
else entirely, with no prompt about a subset in sight.

This routes every corpus program through the classifier and reports the
distribution. It executes nothing, which is what makes it safe to run over a
corpus full of programs that rewrite repositories.

ONE CAVEAT, AND IT IS THE WHOLE POINT OF THIS FILE. Run it *after* the study's
own harvest and the number is circular: several hundred of the programs it would
route are programs this study generated under nine prompts, so the baseline
would be measuring the treatments. The figure quoted in ``docs/PROMPTING.md``
was taken BEFORE the fold, over the corpus as it stood that morning, and it is
quoted with that size and that date. Re-run this on a corpus that has never had
a prompting study folded into it, or exclude
``tests/corpus/sightings/lypning-prompting-study.jsonl`` — otherwise do not
quote it as a baseline at all.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as H  # noqa: E402


def corpus_records() -> list:
    out = subprocess.run([sys.executable, "-m", "lypning", "corpus", "--json"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if out.returncode != 0:
        raise SystemExit("lypning corpus --json failed: %s" % out.stderr.decode()[-500:])
    return json.loads(out.stdout.decode("utf-8"))


def main() -> int:
    recs = corpus_records()
    engines: Counter = Counter()
    kinds: Counter = Counter()
    for r in recs:
        prog = r.get("program") or ""
        if not prog:
            continue
        rt = H.route(prog)
        engines[rt.get("engine", "?")] += 1
        if rt.get("kind"):
            kinds[rt["kind"]] += 1
    total = sum(engines.values())
    print("corpus baseline — %d programs loaded, %d routed" % (len(recs), total))
    for eng in ("lypning", "lypning-mp", "cpython", "?"):
        if engines.get(eng):
            print("  %-12s %5d  %5.1f%%" % (eng, engines[eng], 100.0 * engines[eng] / total))
    print("\ntop blockers")
    for kind, n in kinds.most_common(12):
        print("  %-14s %5d" % (kind, n))
    (H.STUDY / "data" / "baseline.json").write_text(
        json.dumps({"loaded": len(recs), "routed": total,
                    "engines": dict(engines), "kinds": dict(kinds)}, indent=2) + "\n",
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
