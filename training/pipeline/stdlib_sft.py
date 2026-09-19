"""The stdlib corpus as a SEPARATE, measurable SFT arm — never as bank cases.

WHY IT CANNOT BE A BANK CASE. `training_data.validate_cases` requires at least
three independently specified tests whose outputs are not all equal, because a
case the tests cannot discriminate cannot fail a wrong program. A stdlib unit
is a self-contained program with no stdin, no argv and one fixed stdout, so it
refuses on that rule and there is no honest way to give it three. That is not a
gap to work around: the unit is reference material a model reproduces, and the
bank is tasks a model answers. They teach different things and are graded by
different instruments, so they stay apart.

WHY IT IS AN ARM AND NOT A DEFAULT. Measured 2026-09-19, 14 of the 15 modules
the corpus fills also appear in the bank's own construct pool. Mixing a unit
that implements `textwrap.wrap` into a round whose held-out benchmark contains
a `textwrap` family is teaching toward the test. :func:`overlap` reports that
per module and :func:`sft_rows` refuses the rows a caller has not acknowledged,
so the contamination is a decision someone makes and records, never a default.

WHAT A ROW IS. The prompt names the surface and carries the exact checks, so
the completion is fully determined by the task: a model cannot satisfy it by
inventing its own demonstration. The completion is the unit as it ships —
helpers then cases — which the corpus already verified runs natively on the
engine that labelled it.

WHAT THE ARM MEASURES. Run the round once without these rows and once with
them, same bank, same seeds, same benchmark. The difference is what the corpus
bought. Running only the mixed arm measures nothing, because no arm is left to
compare it to.

Library code: everything returns data; `cli.py` renders it and maps exit codes.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .jsonio import digest
from .training_types import TrainingError

#: The corpus row shape this reads, as `stdlib.py` writes it.
SCHEMA = "lypning-stdlib-unit/1"

#: What the module list calls a unit that fills a builtin and has no module.
BUILTIN = "(builtins)"

#: What the prompt says. It names the surface, and forbids the import only when
#: there is one to forbid: three units fill builtins (`dict.fromkeys`,
#: `str.center`, `str.translate`), which no import brings in, so telling a model
#: not to import them would be an instruction with no referent. It never names
#: the engine, a refusal or an exit code, which is the prompt hygiene
#: `training.messages` enforces for the bank.
TASK_MODULE = ("Implement %s in pure Python, without importing %s.\n"
               "Then run exactly these checks and print their results:\n\n%s")
TASK_BUILTIN = ("Implement %s in pure Python, from scratch.\n"
                "Then run exactly these checks and print their results:\n\n%s")


def _head(token: Any) -> str:
    """The leading module-ish segment of a capability, surface or family name."""
    text = str(token).strip().lower()
    for sep in (".", "-"):
        text = text.split(sep)[0]
    return text.strip()


def module_of(unit: Dict[str, Any]) -> str:
    """The top-level module a unit fills, which is the unit of overlap."""
    return str(unit.get("reference") or "").split(".")[0].strip().lower()


def overlap(units: Sequence[Dict[str, Any]], held_out: Iterable[str]) -> Dict[str, List[str]]:
    """Units whose module a held-out family also covers, by module.

    `held_out` is whatever the caller says the benchmark reaches — capability
    labels, family names or module names. The comparison is on the top-level
    module because that is the granularity at which a unit teaches: a unit
    filling `textwrap.wrap` informs any `textwrap` task, not only the one it
    names.
    """
    # A held-out token arrives in three spellings and all three must reduce to
    # the module: a capability label (`textwrap`), a dotted surface
    # (`textwrap.fill`), and a `synth.family_of` family, which hyphenates the
    # construct (`textwrap-fill`, `bisect-bisect-left`). Splitting on `.` alone
    # matched none of the families, so the check reported a clean bank while
    # every unit overlapped it.
    reach = {_head(h) for h in held_out if str(h).strip()}
    reach.discard("")
    hits: Dict[str, List[str]] = {}
    for unit in units:
        mod = module_of(unit)
        if mod and mod in reach:
            hits.setdefault(mod, []).append(str(unit.get("name") or unit.get("id") or "?"))
    return {k: sorted(v) for k, v in sorted(hits.items())}


def to_row(unit: Dict[str, Any]) -> Dict[str, Any]:
    """One unit as a supervised row: a determined prompt and the unit as shipped."""
    for field in ("helpers", "cases", "fills", "id"):
        if not unit.get(field):
            raise TrainingError("stdlib unit is missing %s" % field)
    fills = ", ".join(str(f) for f in unit["fills"])
    program = unit["helpers"].rstrip() + "\n\n# --- cases ---\n" + unit["cases"].strip() + "\n"
    module = module_of(unit)
    task = (TASK_MODULE % (fills, module, unit["cases"].strip()) if module
            else TASK_BUILTIN % (fills, unit["cases"].strip()))
    return {
        "case_id": "lys-sft-" + digest({"id": unit["id"], "task": task}, 12),
        "task": task,
        "reference": program,
        "stdlib": {
            "unit": unit.get("name"),
            "unit_id": unit["id"],
            "module": module_of(unit),
            "fills": list(unit["fills"]),
            "requires": unit.get("requires"),
            "source_sha256": unit.get("source_sha256"),
        },
    }


def sft_rows(units: Sequence[Dict[str, Any]], *,
             held_out: Iterable[str] = (),
             allow_overlap: bool = False) -> Dict[str, Any]:
    """Supervised rows, with the contaminating ones dropped unless allowed.

    Returns ``{"rows", "dropped", "overlap", "modules"}``. A dropped row is a
    unit whose module the benchmark also reaches; it is reported by name rather
    than silently skipped, because which units a round could not use is part of
    what the arm measured.
    """
    units = [u for u in units if u.get("schema") == SCHEMA]
    if not units:
        raise TrainingError("no stdlib units with schema " + SCHEMA)
    contaminating = overlap(units, held_out)
    blocked = {name for names in contaminating.values() for name in names}
    rows, dropped = [], []
    for unit in units:
        name = str(unit.get("name") or unit.get("id") or "?")
        if name in blocked and not allow_overlap:
            dropped.append(name)
            continue
        rows.append(to_row(unit))
    # A builtin unit's module is the empty string; name it, so a reader of the
    # module list is not shown a blank and left to guess what it stands for.
    modules = sorted({r["stdlib"]["module"] or BUILTIN for r in rows})
    return {"rows": rows, "dropped": sorted(dropped), "overlap": contaminating,
            "modules": modules}


def render(result: Dict[str, Any], *, allow_overlap: bool = False) -> str:
    lines = ["stdlib SFT arm: %d row(s) over %d module(s)"
             % (len(result["rows"]), len(result["modules"]))]
    if result["modules"]:
        lines.append("  modules: " + ", ".join(result["modules"]))
    if result["overlap"]:
        verb = "KEPT (--allow-overlap)" if allow_overlap else "dropped"
        lines.append("  the benchmark also reaches these modules, so their units were %s:" % verb)
        for mod, names in result["overlap"].items():
            lines.append("    %-14s %s" % (mod, ", ".join(names)))
    if not allow_overlap and result["dropped"]:
        lines.append("  %d unit(s) held back; run with --allow-overlap only if the "
                     "benchmark result is read as contaminated" % len(result["dropped"]))
    if not result["rows"]:
        lines.append("  NOTHING TO MIX: every unit overlaps the benchmark, so this arm "
                     "cannot be measured against this bank")
    return "\n".join(lines)
