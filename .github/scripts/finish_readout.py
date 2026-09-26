"""Read a completed finish job's four arms; print aggregates only.

The seed-1111 finish (HF job 6ab7b0b76b030d633f693e48, 2026-09-26) is the
first job to complete base and SFT arms on both the test split and eval-2. Its
log printed the two paired deltas; this prints what they are read beside
(`training/EVAL2.md` §4): each arm's summary and population slices, gates A, B
and C, and the by-kind refusal vector of each arm's correct-fallback draws,
which is what engine coverage could convert into native draws.

Gate B asks arm A's own engine, `lypning-l` at the pilot's verifier Space
revision, which modules it serves; never main's. Only reference imports are
read for it, never a draw's program. Nothing private is printed: no case id,
task, program, family or capability label; refusal kinds are printed only when
they are engine literals (`known_kinds`), and their details never. Any failure
prints a fixed message, the phase and reader line numbers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import traceback
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "training"))
from pipeline.public_view import public_view  # noqa: E402
from pipeline.training_metrics import summarize  # noqa: E402

REPO = "headforce/lypning-round02-artifacts"
SPACE = "headforce/lypning-round02-verifier"
ARMS = {"test": ("base-test", "sft-test", "pilot/bundle.json"),
        "eval2": ("base-eval2", "sft-eval2", "eval2/bundle.json")}
SUMMARY = ("correct", "correct_native", "case_weighted_correct", "case_weighted_native",
           "cases", "draws", "families", "truncation_rate", "mean_completion_tokens",
           "statuses", "engine_mismatches", "macro_rule")
GATE_A_MAX_DROP, GATE_B_MIN_RETENTION, GATE_C_MAX_GROWTH = 0.02, 0.80, 0.20


def require(condition):
    if not condition:
        raise ValueError("incomplete or inconsistent evidence")


def known_kinds():
    """Only public engine literals may become public labels; details never do."""
    kinds = set()
    for path in (ROOT / "src/lypning/assets/rust/src").rglob("*.rs"):
        kinds.update(re.findall(r'unsupported\(\s*"([a-z][a-z0-9-]*)"', path.read_text()))
    return kinds


def refusal_vector(rows, allowed):
    """Refusal kinds of the correct-fallback draws: one count per draw per kind."""
    by_kind = Counter()
    fallback = [r for r in rows if r.get("status") == "correct-fallback"]
    missing = 0
    for row in fallback:
        kinds = set()
        for item in row.get("refusals", []):
            require(isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], str))
            match = re.match(r"lypning-l: unsupported: ([a-z][a-z0-9-]*): ", item[1])
            kinds.add(match.group(1) if match and match.group(1) in allowed else "other-kind")
        missing += not kinds
        by_kind.update(kinds)
    return {"correct_fallback_draws": len(fallback), "without_refusal": missing,
            "draws_by_kind": dict(sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0])))}


def slim(summary):
    out = {k: summary.get(k) for k in SUMMARY}
    out["by_population"] = {p: {k: s.get(k) for k in SUMMARY if k in s}
                            for p, s in (summary.get("by_population") or {}).items()}
    return out


def gate_b(base_rows, sft_rows, cases, engine):
    from pipeline.legality import imports_in, modules_served
    wanted = {c["case_id"]: imports_in(c.get("reference") or "") for c in cases}
    wanted = {cid: mods for cid, mods in wanted.items() if mods}
    served = modules_served(engine, sorted({m for mods in wanted.values() for m in mods}))
    scoped = {cid: {m for m in mods if served.get(m)} for cid, mods in wanted.items()}
    scoped = {cid: mods for cid, mods in scoped.items() if mods}

    def rate(rows):
        hits = total = 0
        for row in rows:
            mods = scoped.get(row["case_id"])
            if mods is None:
                continue
            total += 1
            hits += bool(imports_in(row.get("completion") or "") & mods)
        return (hits / total if total else None), total

    b, n = rate(base_rows)
    t, _ = rate(sft_rows)
    ok = n == 0 or not b or t >= GATE_B_MIN_RETENTION * b
    return {"cases": len(scoped), "draws": n, "served_modules": len({m for v in scoped.values() for m in v}),
            "base": b, "sft": t, "retention": (t / b) if b else None,
            "floor": GATE_B_MIN_RETENTION, "pass": bool(ok)}


def main():
    phase = "authentication"
    try:
        from huggingface_hub import HfApi, hf_hub_download
        token = os.environ["HF_TOKEN"].strip()
        job = os.environ["FINISH_JOB"].strip()
        require(re.fullmatch(r"[0-9a-f]{24}", job) is not None)
        api = HfApi(token=token)
        info = api.repo_info(REPO, repo_type="dataset")
        require(info.private and re.fullmatch(r"[0-9a-f]{40}", info.sha) is not None)
        revision = info.sha

        def fetch(path):
            local = hf_hub_download(REPO, path, repo_type="dataset", revision=revision, token=token)
            raw = Path(local).read_bytes()
            return raw, hashlib.sha256(raw).hexdigest()

        phase = "manifest"
        raw, _ = fetch("round-02/%s/job-manifest.json" % job)
        manifest = json.loads(raw)
        require(manifest.get("kind") == "finish" and manifest.get("status") == "complete")
        pilot = manifest["finish_of"]
        require(re.fullmatch(r"[0-9a-f]{24}", pilot) is not None)
        space_revision = manifest["dispatch_space_revision"]
        require(re.fullmatch(r"[0-9a-f]{40}", space_revision) is not None)

        phase = "engine"
        engine = hf_hub_download(SPACE, "lypning-l", repo_type="space", revision=space_revision, token=token)
        os.chmod(engine, 0o755)
        engine_sha = hashlib.sha256(Path(engine).read_bytes()).hexdigest()
        # `refusals.probe` reads an engine that cannot start as one that took
        # the import, which would pass gate B vacuously: it must answer first.
        import subprocess
        version = subprocess.run([engine, "--version"], capture_output=True, text=True, timeout=10)
        require(version.returncode == 0 and "(lypning-l)" in version.stdout)

        allowed = known_kinds()
        result = {"engine_version": version.stdout.strip(), "job": job, "finish_of": pilot, "repository_revision": revision,
                  "space_revision": space_revision, "engine_sha256": engine_sha,
                  "selected_step": manifest.get("selected_step"), "splits": {}}
        for split, (base_dir, sft_dir, bundle_path) in ARMS.items():
            phase = split + " rows"
            rows = {}
            for arm in (base_dir, sft_dir):
                raw, _ = fetch("round-02/%s/%s/evaluations.jsonl" % (job, arm))
                rows[arm] = [json.loads(line) for line in raw.splitlines() if line.strip()]
                require(bool(rows[arm]))
            phase = split + " report"
            raw, _ = fetch("round-02/%s/reports/base-vs-sft-%s.json" % (job, split))
            report = json.loads(raw)
            policy = {"min_family_cases": (report.get("paired") or {}).get("min_family_cases", 1)}
            phase = split + " summaries"
            base, sft = summarize(rows[base_dir], **policy), summarize(rows[sft_dir], **policy)
            paired = report["paired"]["metrics"]
            phase = split + " gates"
            raw, _ = fetch("round-02/%s/%s" % (pilot, bundle_path))
            bundle = json.loads(raw)
            cases = [c for c in bundle["cases"] if split == "eval2" or c.get("split") == "test"]
            result["splits"][split] = {
                "paired": public_view(report["paired"]),
                "engine_mismatches": public_view(report.get("engine_mismatches")),
                "base": slim(base), "sft": slim(sft),
                "gates": {
                    "A_correctness": {"delta": paired["correct"]["delta"], "max_drop": GATE_A_MAX_DROP,
                                      "pass": paired["correct"]["delta"] >= -GATE_A_MAX_DROP},
                    "B_supported_import_retention": gate_b(rows[base_dir], rows[sft_dir], cases, engine),
                    "C_length": {"base": base["mean_completion_tokens"], "sft": sft["mean_completion_tokens"],
                                 "growth": sft["mean_completion_tokens"] / base["mean_completion_tokens"] - 1,
                                 "max_growth": GATE_C_MAX_GROWTH,
                                 "pass": sft["mean_completion_tokens"]
                                         <= (1 + GATE_C_MAX_GROWTH) * base["mean_completion_tokens"]}},
                "primary_rule": {"metric": "correct_native family macro (paired 'native')",
                                 "delta": paired["native"]["delta"], "ci95": paired["native"]["ci95"],
                                 "lower_bound_above": 0.03,
                                 "pass": paired["native"]["ci95"][0] > 0.03},
                "refusals": {"base": refusal_vector(rows[base_dir], allowed),
                             "sft": refusal_vector(rows[sft_dir], allowed)},
            }
        print(json.dumps(public_view(result), indent=2, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        print("Finish readout failed during %s; no private payload printed." % phase, file=sys.stderr)
        lines = [frame.lineno for frame in traceback.extract_tb(exc.__traceback__)
                 if frame.filename == __file__]
        print("Reader source lines: " + json.dumps(public_view(lines)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
