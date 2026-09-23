"""`case_clusters` stays in the private artifacts and out of every public log.

`training_metrics.summarize` writes per-case draw/correct/native counts
(`case_clusters`) into every `by_population` slice, so they reach
`metrics.json`, `best.json` and each `training_report`, eval-2's included. The
operator decided on 2026-09-23 that they stay PRIVATE: the artifacts keep them,
because offline re-selection pairs evaluations through them, and every printer
that can put one of those objects into a world-readable Actions log strips them
through the one helper, `pipeline.public_view`.

Three parts. The helper itself; each printer driven end to end with a fixture
that carries the key; and a guard that no printer can be added, or edited,
that prints such an object without going through the helper.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from pipeline import positive_control_grade
from pipeline.public_view import PRIVATE_KEYS, public_view
from pipeline.training_metrics import CheckpointGate, summarize
from pipeline.training_types import Score

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".github" / "scripts"
ROUND_SCRIPTS = sorted((ROOT / "training" / "hf").glob("*.sh"))

# Sentinels no aggregate could produce, so finding one in a log is a leak.
DIGEST = "c1" * 32
COUNTS = [424241, 424242, 424243]


def clusters():
    return {"digest": DIGEST,
            "families": [{"draws": COUNTS, "correct": COUNTS, "native": COUNTS}]}


def slice_(**extra):
    return dict({"correct": .5, "correct_native": .25, "cases": 3, "draws": 12},
                case_clusters=clusters(), **extra)


def metrics_fixture():
    """A `summarize`-shaped object with the key at more than one depth."""
    return dict(slice_(), by_population={"coverage": slice_(), "fallback-control": slice_()})


def best_fixture():
    return {"step": 25, "observations": [{"step": 0, "metrics": metrics_fixture()},
                                         {"step": 25, "metrics": metrics_fixture()}]}


def leaks(value):
    """True when `value` still carries the key, or its per-case count arrays."""
    if isinstance(value, dict):
        if PRIVATE_KEYS & set(value):
            return True
        if ({"draws", "correct", "native"} <= set(value)
                and all(isinstance(value[k], list) for k in ("draws", "correct", "native"))):
            return True
        return any(leaks(v) for v in value.values())
    if isinstance(value, list):
        return any(leaks(v) for v in value)
    return False


def assert_public(text):
    assert "case_clusters" not in text
    assert DIGEST not in text
    assert not any(str(n) in text for n in COUNTS)


def load_script(name):
    """`load`, with `.github/scripts` importable, as the workflow runs it."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return load(name)


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- the helper ----------------------------------------------------------------

def test_the_key_is_stripped_at_every_depth_and_nothing_else_is():
    private = {"metrics": metrics_fixture(), "best": best_fixture(),
               "paired": {"deltas": (metrics_fixture(), [metrics_fixture()])}}
    before = deepcopy(private)
    public = public_view(private)
    assert not leaks(public)
    assert_public(json.dumps(public))
    assert public["metrics"]["by_population"]["coverage"] == {
        "correct": .5, "correct_native": .25, "cases": 3, "draws": 12}
    assert public["best"]["step"] == 25
    # A copy: the private object, the one that is uploaded, is untouched.
    assert private == before
    assert public_view("case_clusters") == "case_clusters"
    assert public_view(None) is None


def test_the_private_artifact_keeps_the_key():
    """What `metrics.json` / `best.json` / a report hold is not the printer's
    business: `summarize` still writes the counts the offline re-selection
    pairs evaluations through."""
    rows = [{"family": "f%d" % (i % 2), "case_id": "c%d" % i, "draw": d,
             "population": "coverage", "correct": True, "native": d == 0}
            for i in range(4) for d in range(2)]
    stats = summarize(rows)
    kept = stats["by_population"]["coverage"]["case_clusters"]
    assert set(kept) == {"digest", "families"}
    assert "case_clusters" not in json.dumps(public_view(stats))


def test_best_json_keeps_the_key_the_offline_reselection_reads():
    """`best.json` is `CheckpointGate.report()`: the selected metrics, whole.
    Offline re-selection pairs a checkpoint with base through the counts and
    the digest, so the private file must still carry both."""
    def rows(native_every):
        return [{"family": "f%d" % (i % 2), "case_id": "c%d" % i, "draw": d,
                 "population": "coverage", "correct": True, "native": (i + d) % native_every == 0}
                for i in range(8) for d in range(2)]
    base, candidate = summarize(rows(3)), summarize(rows(1))
    gate = CheckpointGate(baseline=base)
    gate.observe(25, candidate)
    best = gate.report()
    kept = best["by_population"]["coverage"]["case_clusters"]
    assert kept["digest"] == base["by_population"]["coverage"]["case_clusters"]["digest"]
    assert kept["families"]
    assert not leaks(public_view(best)) and leaks(best)


# -- each printer, end to end --------------------------------------------------

def fake_hub(monkeypatch, tmp_path, files, **api):
    """A `huggingface_hub` serving `files` (repo path -> JSON-able object)."""
    local = {}
    for name, value in files.items():
        path = tmp_path / name.replace("/", "__")
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        local[name] = str(path)
    methods = dict(whoami=lambda: {"name": "owner"},
                   list_repo_files=lambda *a, **k: sorted(files),
                   repo_info=lambda *a, **k: SimpleNamespace(private=True, sha="a" * 40),
                   list_jobs=lambda: [])
    methods.update(api)
    fake = SimpleNamespace(HfApi=lambda **kw: SimpleNamespace(**methods),
                           hf_hub_download=lambda repo, name, **kw: local[name])
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)
    monkeypatch.setenv("HF_TOKEN", "token")
    return local


def test_s0_inventory_prints_metrics_and_best_without_the_key(monkeypatch, tmp_path, capsys):
    run = "round-02/job/"
    fake_hub(monkeypatch, tmp_path, {run + "sft/metrics.json": metrics_fixture(),
                                     run + "sft/best.json": best_fixture(),
                                     run + "reports/base-vs-sft-eval2.json":
                                         {"base": metrics_fixture()}})
    assert load("s0_inventory").main() == 0
    out = capsys.readouterr().out
    assert_public(out)
    # The rest of the file is still printed: this strips, it does not censor.
    assert '"correct_native": 0.25' in out and '"step": 25' in out


def step0_evidence():
    def cases(n, split):
        return [{"case_id": split + str(i), "family": "fam" + str(i // 2),
                 "split_group": "g" + str(i // 2), "split": split,
                 "population": "coverage"} for i in range(n)]

    def rows(cs, step=0):
        return [dict(c, step=step, draw=d, seed=1111, status="correct-native",
                     correct=True, native=True, truncated=False, completion_tokens=100,
                     capabilities=[], refusals=[]) for c in cs for d in range(4)]

    m = load("step0_summary")
    dev, train = cases(4, "dev"), cases(4, "train")
    data = {"pilot/bundle.json": {"cases": dev + train},
            "eval2/bundle.json": {"cases": cases(10, "eval2")},
            "base-dev/evaluations.jsonl": rows(dev),
            "probe/probe-rollouts.jsonl": rows(train),
            **{stage + "/evaluations.jsonl": [r for step in steps for r in rows(dev, step)]
               for stage, steps in m.STEPS.items()}}
    return m, data


def test_step0_summary_prints_by_population_without_the_key(monkeypatch, tmp_path, capsys):
    m, data = step0_evidence()
    # The unstripped result DOES carry it: this is the real path, not a fixture.
    assert leaks(m.summarise(data))
    files = {"round-02/%s/%s" % (m.JOB, name):
             ("".join(json.dumps(r) + "\n" for r in value) if name.endswith(".jsonl") else value)
             for name, value in data.items()}
    adapters = ["round-02/%s/%s/adapter-%d/adapter_model.safetensors" % (m.JOB, stage, step)
                for stage, steps in m.STEPS.items() for step in steps]
    fake_hub(monkeypatch, tmp_path, files,
             list_repo_files=lambda *a, **k: sorted(files) + adapters)
    assert m.main() == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "case_clusters" not in out
    printed = json.loads(out.split("\n", 1)[1])
    assert not leaks(printed)
    assert "coverage" in printed["stages"]["sft"][0]["metrics"]["by_population"]


def test_loss_summary_prints_no_key(monkeypatch, tmp_path, capsys):
    rows = [dict({"step": i, "loss": 4.0 - i / 10, "learning_rate": 2e-5,
                  "supervised_tokens_total": 100 * i}, case_clusters=clusters())
            for i in range(1, 7)]
    fake_hub(monkeypatch, tmp_path,
             {"round-02/job/sft/loss.jsonl": "".join(json.dumps(r) + "\n" for r in rows)})
    monkeypatch.setattr(sys, "argv", ["loss_summary.py", "job"])
    assert load("loss_summary").main() == 0
    out = capsys.readouterr().out
    assert_public(out)
    assert "loss_first" in out


def test_hf_status_prints_manifest_fields_without_the_key(monkeypatch, tmp_path, capsys):
    manifest = {"status": "complete", "seed": 1111,
                "steps": {"sft": 75, "case_clusters": clusters()},
                "eval_draws": [metrics_fixture()]}
    fake_hub(monkeypatch, tmp_path, {"round-02/job/job-manifest.json": manifest})
    assert load("hf_status").main() == 0
    out = capsys.readouterr().out
    assert_public(out)
    assert "complete" in out and "'sft': 75" in out


def pilot():
    return (ROOT / "training" / "hf" / "round02_pilot.sh").read_text(encoding="utf-8")


def run_python(args, cwd, **env):
    environ = dict(os.environ, PYTHONPATH=str(ROOT / "training"), **env)
    return subprocess.run([sys.executable] + args, cwd=cwd, env=environ,
                          capture_output=True, text=True, timeout=60)


def test_pilot_report_line_prints_paired_without_the_key(tmp_path):
    line = next(l for l in pilot().splitlines() if 'print("== report"' in l)
    program = re.search(r"python3 -c '([^']*)'", line).group(1)
    report = tmp_path / "base-vs-sft-eval2.json"
    report.write_text(json.dumps({"base": metrics_fixture(), "candidate": metrics_fixture(),
                                  "paired": {"delta": .01, "by_population": {
                                      "coverage": metrics_fixture()}}}))
    done = run_python(["-c", program, str(report)], tmp_path)
    assert done.returncode == 0, done.stderr
    assert_public(done.stdout)
    assert done.stdout.startswith("== report") and '"delta": 0.01' in done.stdout
    # The file the job uploads is untouched.
    assert "case_clusters" in report.read_text()


def test_pilot_probe_verdict_prints_without_the_key(tmp_path):
    body = pilot().split('GRPO_STEPS="$GRPO_STEPS" python3 - <<\'PYEOF\'\n', 1)[1]
    program = body.split("\nPYEOF\n", 1)[0]
    probe = tmp_path / "work" / "round-02" / "probe"
    probe.mkdir(parents=True)
    (probe / "probe.json").write_text(json.dumps({
        "admitted": True, "informative_groups": {"n": 3, "case_clusters": clusters()},
        "groups": 8, "correct_draws": COUNTS, "truncated_draws": 0, "draws": 32}))
    done = run_python(["-c", program], tmp_path, GRPO_STEPS="0")
    assert done.returncode == 3, done.stderr
    verdict = next(l for l in done.stdout.splitlines() if l.startswith("== probe verdict:"))
    assert "case_clusters" not in verdict and DIGEST not in verdict


# -- the public artifacts ------------------------------------------------------
#
# A file uploaded with `actions/upload-artifact` from this public repository is
# as public as the log: `public-report.json` (Step 2 grade and merge) and
# `s4-target-floor.json`. Neither holds the key today; both pass the helper so a
# field added to either tomorrow cannot carry it out.

class _Verifier:
    def score(self, case, program):
        n = sum(map(ord, program))
        if case["population"] == "fallback-control":
            return Score(1, "correct-control", 0, 2) if n % 3 else Score(0, "incorrect", 0, 2)
        return Score(1, "correct-native", 2, 2) if n % 2 else Score(1, "correct-fallback", 0, 2)


def _step2_inputs():
    cases = [{"case_id": "c%d" % i, "family": "fam-%d" % (i % 4), "task": "task %d" % i,
              "split_group": "g%d" % (i % 3),
              "population": "fallback-control" if i % 4 == 3 else "coverage",
              "capabilities": [], "split": "train", "tests": [{}, {}]} for i in range(8)]
    completions = [{"case_id": c["case_id"], "draw": d, "arm": a, "seed": 1111 + d,
                    "completion": "```python\nprint(%r)\n```" % ("%s/%d/%s" % (c["case_id"], d, a)),
                    "finish_reason": "stop", "usage": {"completion_tokens": 8 + d}}
                   for c in cases for d in range(2) for a in ("bare", "subset-spec")]
    return cases, completions


def test_step2_public_report_is_the_public_view_and_report_json_keeps_the_key(
        monkeypatch, tmp_path):
    """Grade and merge write the same `public-report.json`; a comparison that
    carried the key would reach it through `comparison`, and must not."""
    real = positive_control_grade.population_comparison

    def with_clusters(rows, population):
        found = real(rows, population)
        return None if found is None else dict(found, case_clusters=clusters())

    monkeypatch.setattr(positive_control_grade, "population_comparison", with_clusters)
    cases, completions = _step2_inputs()
    out = tmp_path / "graded"
    public = positive_control_grade.grade(cases, completions, _Verifier(), out, samples=2,
                                          workers=2, run_id="r", lineage={"engine_sha256": "e" * 64})
    assert not leaks(public)
    assert_public((out / "public-report.json").read_text())
    # The private report keeps what it was given.
    assert "case_clusters" in json.loads((out / "report.json").read_text())["comparison"]
    merge = load_script("step2_merge")
    rows = [json.loads(l) for l in (out / "rows.jsonl").read_text().splitlines()]
    merge.aggregate(cases, completions, rows, tmp_path / "merged", samples=2, run_id="r",
                    lineage={"engine_sha256": "e" * 64}, target_arms=("subset-spec",))
    assert ((tmp_path / "merged" / "public-report.json").read_bytes()
            == (out / "public-report.json").read_bytes())
    assert "case_clusters" in (tmp_path / "merged" / "report.json").read_text()


def test_s4_target_floor_writes_the_public_view_to_its_uploaded_file():
    tree = ast.parse((SCRIPTS / "s4_target_floor.py").read_text(encoding="utf-8"))
    writes = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute) and n.func.attr == "write_text"
              and "out" in ast.unparse(n.func.value)]
    assert writes, "s4_target_floor no longer writes --out; re-point this test"
    for call in writes:
        dumps = [n for n in ast.walk(call) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "dumps"]
        assert dumps and all(_is_public_view(d.args[0]) for d in dumps), ast.unparse(call)


# -- the guard -----------------------------------------------------------------

# A script that reads or builds a metrics/best/report object: its printed JSON
# must pass the helper. The file-level pattern names the objects; the name-level
# one catches a variable called metrics/best/report/public printed as JSON.
HANDLES = re.compile(r"metrics\.json|best\.json|report\.json|summarize\(|training_report"
                     r"|paired_comparison|by_population|case_clusters")
NAMES = re.compile(r"metrics|best|report|public")
# The printers this PR routed through the helper, whether or not they call
# `json.dumps`: each must at least import it.
PRINTERS = ("s0_inventory", "step0_summary", "loss_summary", "hf_status")


def _is_public_view(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "public_view")


def _printed_dumps(tree):
    """Every `json.dumps(...)` inside a `print(...)`, and every `json.dump(x, sys.std*)`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "dumps" and sub.args):
                    yield sub
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "dump" and len(node.args) > 1
              and re.fullmatch(r"sys\.std(out|err)", ast.unparse(node.args[1]))):
            yield node


def _guarded(path, tree, call):
    if HANDLES.search(path.read_text(encoding="utf-8")):
        return True
    arg = call.args[0]
    words = {n.id for n in ast.walk(arg) if isinstance(n, ast.Name)}
    words |= {n.value for n in ast.walk(arg) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    return any(NAMES.search(w) for w in words)


def test_every_script_printing_a_metrics_best_or_report_object_uses_the_helper():
    offenders, guarded = [], set()
    for path in sorted(SCRIPTS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _printed_dumps(tree):
            if not _guarded(path, tree, call):
                continue
            guarded.add(path.stem)
            if not _is_public_view(call.args[0]):
                offenders.append("%s:%d" % (path.relative_to(ROOT), call.lineno))
    assert offenders == [], ("these print a metrics/best/report object without "
                             "pipeline.public_view: " + ", ".join(offenders))
    # Not vacuous: the two readers that print whole artifacts are in scope.
    assert {"s0_inventory", "step0_summary"} <= guarded


def test_the_named_printers_import_the_helper():
    for name in PRINTERS:
        source = (SCRIPTS / (name + ".py")).read_text(encoding="utf-8")
        assert "from pipeline.public_view import public_view" in source, name
        assert "public_view(" in source.split("import public_view", 1)[1], name


# The Python a round script runs: `python3 - <<'TAG' ... TAG` and `python3 -c '...'`.
HEREDOC = re.compile(r"^([^\n]*\bpython3?\b[^\n]*<<-?\s*'?(\w+)'?[^\n]*)\n(.*?)\n\s*\2\s*$",
                     re.M | re.S)
DASH_C = re.compile(r"\bpython3?\s+-c\s+'([^']*)'")
# A shell command that would put an artifact on stdout without any Python.
SHELL_DUMP = re.compile(r"\b(cat|jq|head|tail|less|more)\b[^|>#\n]*\.json\b")


def python_bodies(text):
    for match in HEREDOC.finditer(text):
        yield textwrap.dedent(match.group(3))
    for match in DASH_C.finditer(text):
        yield match.group(1)


def test_every_python_body_in_a_round_script_prints_json_through_the_helper():
    """The follower streams the whole job log into a PUBLIC Actions log, so in
    a round script there is no private stdout: every printed JSON, on one line
    or several, `json.dumps` or `json.dump(x, sys.stdout)`, passes the helper."""
    offenders, bodies = [], 0
    for path in ROUND_SCRIPTS:
        text = path.read_text(encoding="utf-8")
        for body in python_bodies(text):
            bodies += 1
            for call in _printed_dumps(ast.parse(body)):
                if not _is_public_view(call.args[0]):
                    offenders.append("%s: %s" % (path.name, ast.unparse(call)[:80]))
        for number, line in enumerate(text.splitlines(), 1):
            if SHELL_DUMP.search(line):
                offenders.append("%s:%d %s" % (path.name, number, line.strip()))
    assert offenders == [], "round scripts print JSON around public_view: " + "; ".join(offenders)
    # Not vacuous: the pilot's manifest, witness, probe and report printers are
    # all Python bodies this found.
    assert bodies >= 20


def test_workflow_python_printing_an_artifact_uses_the_helper():
    """Inline Python in a workflow `run:` block prints into the same public log."""
    offenders = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for body in python_bodies(text):
            if not HANDLES.search(body):
                continue
            for call in _printed_dumps(ast.parse(body)):
                if not _is_public_view(call.args[0]):
                    offenders.append("%s: %s" % (path.name, ast.unparse(call)[:80]))
        for number, line in enumerate(text.splitlines(), 1):
            if SHELL_DUMP.search(line) and HANDLES.search(line):
                offenders.append("%s:%d %s" % (path.name, number, line.strip()))
    assert offenders == [], ", ".join(offenders)


def test_round_scripts_print_json_only_through_the_helper():
    offenders = []
    for path in ROUND_SCRIPTS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "print(" in line and "json.dumps(" in line and "json.dumps(public_view(" not in line:
                offenders.append("%s:%d" % (path.relative_to(ROOT), number))
    assert offenders == [], "round scripts print JSON around public_view: " + ", ".join(offenders)
    assert 'print("== report"' in pilot()


def test_there_is_one_stripping_mechanism():
    """No printer drops the key by hand: a second mechanism is one that drifts."""
    allowed = {ROOT / "training" / "pipeline" / "public_view.py",
               ROOT / "training" / "pipeline" / "training_metrics.py"}
    sources = (sorted(SCRIPTS.glob("*.py")) + sorted((ROOT / "training" / "pipeline").glob("*.py"))
               + sorted((ROOT / "training" / "gpu").glob("*.py")))
    offenders = []
    for path in sources:
        if path in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in PRIVATE_KEYS:
                offenders.append("%s:%d" % (path.relative_to(ROOT), node.lineno))
    assert offenders == [], "name the key only through pipeline.public_view: " + ", ".join(offenders)


@pytest.mark.parametrize("path", ROUND_SCRIPTS, ids=lambda p: p.name)
def test_round_scripts_still_parse(path):
    assert subprocess.run(["bash", "-n", str(path)]).returncode == 0
