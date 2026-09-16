"""Dataset admission and connected-component splits; no program execution.

Family labels alone do not prevent the same source or solution crossing splits.
Components join semantic families, declared source groups and exact normalised
solution ASTs before population-stratified splitting. This catches exact reuse,
not semantic near-duplicates: independent human data review remains mandatory.
"""
from __future__ import annotations

import ast
from pathlib import PurePosixPath

from .jsonio import sha256_of
from .training_types import TrainingError

POPULATIONS = {"coverage", "fallback-control"}


def _text(value, name, *, nonempty=False, nul=False):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise TrainingError(name + " must be text" + (" and nonempty" if nonempty else ""))
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TrainingError(name + " must be valid UTF-8") from exc
    if not nul and "\0" in value:
        raise TrainingError(name + " cannot contain NUL")


def solution_fingerprint(reference):
    try:
        tree = ast.parse(reference)
    except (SyntaxError, ValueError) as exc:
        raise TrainingError("reference must parse as Python") from exc
    return sha256_of(ast.dump(tree, include_attributes=False))


def validate_cases(cases):
    if not isinstance(cases, list) or not cases:
        raise TrainingError("no training cases")
    ids, prompts = set(), set()
    for case in cases:
        if not isinstance(case, dict):
            raise TrainingError("each case must be an object")
        for key in ("case_id", "family", "task", "reference", "provenance"):
            _text(case.get(key), key, nonempty=True)
        if "source_group" in case:
            _text(case["source_group"], "source_group", nonempty=True)
        if case["case_id"] in ids or case["task"].strip() in prompts:
            raise TrainingError("duplicate case id or prompt")
        ids.add(case["case_id"])
        prompts.add(case["task"].strip())
        solution_fingerprint(case["reference"])
        if case.get("population") not in POPULATIONS:
            raise TrainingError("population must be coverage or fallback-control")
        capabilities = case.get("capabilities", [])
        if not isinstance(capabilities, list):
            raise TrainingError("capabilities must be a list of unique labels")
        for label in capabilities:
            _text(label, "capability", nonempty=True)
        if len(set(capabilities)) != len(capabilities):
            raise TrainingError("capabilities must be a list of unique labels")
        tests = case.get("tests", [])
        if not isinstance(tests, list) or len(tests) < 3:
            raise TrainingError("need at least three independently specified tests")
        inputs = set()
        for test in tests:
            if not isinstance(test, dict) or set(test) - {"stdin", "argv", "files", "stdout"}:
                raise TrainingError("unsupported observable contract")
            _text(test.get("stdout"), "expected stdout", nul=True)
            if "\ufffd" in test["stdout"]:
                raise TrainingError("expected stdout must be unambiguous UTF-8 text")
            _text(test.get("stdin", ""), "stdin", nul=True)
            if not isinstance(test.get("argv", []), list):
                raise TrainingError("argv must be a list of strings")
            for arg in test.get("argv", []):
                _text(arg, "argv element")
            files = test.get("files", {})
            if not isinstance(files, dict):
                raise TrainingError("files must be a mapping")
            for name, content in files.items():
                _text(name, "input path", nonempty=True)
                path = PurePosixPath(name)
                if (path.is_absolute() or ".." in path.parts or "\\" in name
                        or str(path) != name or name in (".", "solution.py")
                        or name.startswith("solution.py/")):
                    raise TrainingError("unsafe or reserved input file path")
                if any(parent.as_posix() in files for parent in path.parents if str(parent) != "."):
                    raise TrainingError("input file/directory path collision")
                _text(content, "input file content", nul=True)
            inputs.add(sha256_of({k: test.get(k, d) for k, d in
                                 (("stdin", ""), ("argv", []), ("files", {}))}))
        if len(inputs) < 3 or len({t["stdout"] for t in tests}) < 2:
            raise TrainingError("tests must vary inputs and distinguish constant-output answers")


def split_cases(cases, seed=1111):
    """Keep every connected source/family/solution component in one split."""
    if len({c["family"] for c in cases}) < 6:
        raise TrainingError("need at least six semantic families for train/dev/test")
    parents = list(range(len(cases)))

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    seen = {}
    for i, case in enumerate(cases):
        keys = [("family", case["family"]), ("solution", solution_fingerprint(case["reference"]))]
        if case.get("source_group"):
            keys.append(("source", case["source_group"]))
        for key in keys:
            if key in seen:
                parents[root(i)] = root(seen[key])
            seen[key] = i
    components = {}
    for i, case in enumerate(cases):
        components.setdefault(root(i), []).append(case)
    strata = {}
    for group in components.values():
        key = tuple(sorted({c["population"] for c in group}))
        strata.setdefault(key, []).append(group)
    assignment, component_ids = {}, {}
    for groups in strata.values():
        groups.sort(key=lambda g: sha256_of([seed, sorted(c["case_id"] for c in g)]))
        # A lone smoke fallback stays in train; a pilot must have independently
        # grouped controls in all three splits and is rejected below otherwise.
        n = max(2 if len(groups) >= 6 else 1, len(groups) // 6) if len(groups) >= 3 else 0
        for i, group in enumerate(groups):
            split = "test" if i < n else "dev" if i < 2 * n else "train"
            component_id = sha256_of(sorted(c["case_id"] for c in group))
            for case in group:
                assignment[case["case_id"]] = split
                component_ids[case["case_id"]] = component_id
    if set(assignment.values()) != {"train", "dev", "test"}:
        raise TrainingError("too few independent source/family/solution groups for three splits")
    return [dict(c, split=assignment[c["case_id"]], split_group=component_ids[c["case_id"]]) for c in cases]


def validate_bank(cases, purpose="pilot"):
    """The bank-wide admission a pilot and a benchmark share; splits are checked by the caller."""
    if len({c["family"] for c in cases}) < 18:
        raise TrainingError(purpose + " needs at least 18 independent semantic families; starter is smoke-only")
    for case in cases:
        if not case.get("source_group") or not case.get("capabilities"):
            raise TrainingError(purpose + " needs reviewed source_group and capability labels for every case")


def validate_pilot(cases):
    validate_bank(cases, "pilot")
    for split in ("train", "dev", "test"):
        rows = [c for c in cases if c["split"] == split]
        if {c["population"] for c in rows} != POPULATIONS:
            raise TrainingError("pilot split %s needs coverage AND fallback controls" % split)
        for population in POPULATIONS:
            population_rows = [c for c in rows if c["population"] == population]
            if (len({c["family"] for c in population_rows}) < 2 or
                    len({c.get("split_group", c["source_group"]) for c in population_rows}) < 2):
                raise TrainingError("each pilot split needs two independent families per population")


def validate_benchmark(cases):
    """A standalone bank evaluated whole: both populations somewhere, not in every split.

    It is never trained on, so the per-split population rule of a pilot does not
    apply; the split_group is still required so cluster bootstraps stay honest.
    """
    validate_bank(cases, "benchmark")
    if {c["population"] for c in cases} != POPULATIONS:
        raise TrainingError("benchmark needs coverage AND fallback controls in the bank as a whole")
    if any(not c.get("split_group") for c in cases):
        raise TrainingError("benchmark cases need a split_group; split the bank before admission")


def validate_reference_scores(cases, scores):
    if set(scores) != {c["case_id"] for c in cases}:
        raise TrainingError("reference score registry does not match cases")
    for case in cases:
        score = scores[case["case_id"]]
        n = len(case["tests"])
        if score["total_tests"] != n or score["reward"] != 1.0:
            raise TrainingError("reference fails population admission: " + case["case_id"])
        want = n if case["population"] == "coverage" else 0
        if score["native_tests"] != want:
            raise TrainingError("reference native/fallback label is stale: " + case["case_id"])
        status = "correct-native" if want else "correct-control"
        if score["status"] != status:
            raise TrainingError("reference status is inconsistent: " + case["case_id"])
