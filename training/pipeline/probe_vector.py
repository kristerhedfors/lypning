"""Describe probe-vs-base native status per train case; never score or execute."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List


def _groups(rows: Iterable[Dict[str, Any]]) -> Dict[str, Counter]:
    groups: Dict[str, Counter] = {}
    for row in rows:
        case_id = row.get("case_id")
        status = row.get("status")
        if not isinstance(case_id, str) or not case_id or not isinstance(status, str) or not status:
            raise ValueError("probe vector rows need non-empty case_id and status")
        groups.setdefault(case_id, Counter())[status] += 1
    return groups


def compare(probe_rows: Iterable[Dict[str, Any]], base_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Return every ID from both inputs, including unmatched IDs explicitly."""
    probe, base = _groups(probe_rows), _groups(base_rows)
    rows: List[Dict[str, Any]] = []
    for case_id in sorted(set(probe) | set(base)):
        p, b = probe.get(case_id, Counter()), base.get(case_id, Counter())
        pn, bn = sum(p.values()), sum(b.values())
        rows.append({"case_id": case_id, "probe": dict(sorted(p.items())),
                     "base": dict(sorted(b.items())),
                     "probe_draws": pn, "base_draws": bn,
                     "probe_native": p.get("correct-native", 0),
                     "base_native": b.get("correct-native", 0),
                     "probe_native_rate": p.get("correct-native", 0) / pn if pn else None,
                     "base_native_rate": b.get("correct-native", 0) / bn if bn else None})
    return {"probe_rows": sum(map(sum, (c.values() for c in probe.values()))),
            "base_rows": sum(map(sum, (c.values() for c in base.values()))),
            "matched_cases": len(set(probe) & set(base)),
            "probe_only": sorted(set(probe) - set(base)),
            "base_only": sorted(set(base) - set(probe)), "cases": rows}


def render(result: Dict[str, Any]) -> str:
    lines = ["probe/base native-status vector (descriptive; no gate moves)",
             "probe rows %d   base rows %d   matched cases %d   probe-only %d   base-only %d"
             % (result["probe_rows"], result["base_rows"], result["matched_cases"],
                len(result["probe_only"]), len(result["base_only"])),
             "", "%-36s %8s %8s  %-30s  %s"
             % ("case", "probe", "base", "probe statuses", "base statuses")]
    for row in result["cases"]:
        def rate(native, draws):
            return "%d/%d" % (native, draws) if draws else "-"
        def statuses(values):
            return ",".join("%s=%d" % item for item in values.items()) or "-"
        lines.append("%-36s %8s %8s  %-30s  %s"
                     % (row["case_id"][:36], rate(row["probe_native"], row["probe_draws"]),
                        rate(row["base_native"], row["base_draws"]),
                        statuses(row["probe"]), statuses(row["base"])))
    if result["probe_only"]:
        lines.extend(("", "probe-only IDs: " + " ".join(result["probe_only"])))
    if result["base_only"]:
        lines.extend(("", "base-only IDs: " + " ".join(result["base_only"])))
    return "\n".join(lines)
