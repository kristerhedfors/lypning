"""The stage 0b switch: a subset spec in the system prompt, and the signature that says so.

Two things must hold or the prompt-ceiling probe (`LADDER.md` 0b) measures
nothing. The spec the model reads must be the one the crate describes, so the
committed file is held equal to its generator's output. And an arm drawn with
the spec must carry a different `prompt_sha` from the bare arm, since
`stats.comparability` refuses across signatures and that refusal is the only
thing that keeps a spec run from being differenced against a bare baseline.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from pipeline import evaluate, hints
from pipeline.backends import ChatBackend

NEMOTRON = Path(__file__).resolve().parents[1]
SPEC = NEMOTRON / "prompts" / "subset-spec.md"
RUST = NEMOTRON.parent / "src" / "lypning" / "assets" / "rust" / "src"

CASE = {
    "id": "c1", "category": "refused:module",
    "prompt": "Print the square root of 16.",
    "test": {"kind": "lypning", "expect_stdout": "4.0\n", "require_tier1": True},
}


def gen_module():
    path = NEMOTRON / "prompts" / "gen_subset_spec.py"
    spec = importlib.util.spec_from_file_location("gen_subset_spec", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _needs_crate():
    if not RUST.is_dir():
        pytest.skip("no Rust source in this tree")


# ------------------------------------------------------------------ the spec


def test_the_committed_spec_is_what_the_generator_writes():
    """Drift between the crate and the paragraph the model reads is a test failure, not a note."""
    _needs_crate()
    gen = gen_module()
    assert SPEC.read_text(encoding="utf-8") == gen.generate()


def test_every_refusal_kind_the_crate_spells_has_one_line_and_no_more():
    _needs_crate()
    gen = gen_module()
    kinds = gen.source_kinds()
    assert len(kinds) > 50, kinds
    text = SPEC.read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if l.startswith("- `")]
    spelled = [re.match(r"- `([a-z0-9-]+)` — ", l).group(1) for l in lines
               if re.match(r"- `([a-z0-9-]+)` — ", l)]
    assert sorted(spelled) == kinds
    assert len(spelled) == len(set(spelled))
    for line in lines:
        assert "Stay inside:" in line, line


def test_the_generator_refuses_a_kind_without_a_recipe():
    gen = gen_module()
    problems = gen.check(["walrus", "a-kind-nobody-wrote"])
    assert any("a-kind-nobody-wrote" in p for p in problems)
    assert any("no longer spells" in p for p in problems)   # every other recipe is orphaned


def test_the_spec_names_no_engine_and_states_both_sentences():
    text = SPEC.read_text(encoding="utf-8")
    assert "lypning" not in text.lower()
    assert "the interpreter that will run your program" in text.lower()
    assert "Correctness outranks the tier." in text
    assert "A fall-back to full Python is free and legitimate when the task needs it." in text


def test_the_partially_served_modules_carry_their_names():
    """`BASE64_SERVED` has a digit in it and the csv row spans lines; both once vanished."""
    _needs_crate()
    attrs = gen_module().served_attrs()
    assert attrs["base64"] == ["b64decode", "b64encode", "urlsafe_b64decode", "urlsafe_b64encode"]
    assert "reader" in attrs["csv"] and "DictReader" in attrs["csv"]
    assert attrs["hashlib"] == ["md5", "sha1", "sha256", "sha512"]
    assert "glob" in attrs["glob"]


def test_the_full_python_only_section_is_the_engines_own_table():
    _needs_crate()
    from lypning.engines import ONLY_CPYTHON_REFUSALS
    text = SPEC.read_text(encoding="utf-8")
    tail = text.split("### Behaviours only full Python gets right", 1)[1]
    listed = set(re.findall(r"^- `([a-z0-9-]+)` — ", tail, re.M))
    assert listed == set(ONLY_CPYTHON_REFUSALS)


# ------------------------------------------------------------- the signature


def test_prompt_sha_differs_with_and_without_the_system_file():
    spec = SPEC.read_text(encoding="utf-8")
    bare = evaluate.prompt_signature()
    with_spec = evaluate.prompt_signature(spec)
    assert bare != with_spec
    assert len(bare) == len(with_spec) == 16
    # Deterministic, and insensitive to the trailing newline a file always has.
    assert with_spec == evaluate.prompt_signature(spec.rstrip("\n"))
    assert evaluate.prompt_signature("") == bare


def test_the_bare_signature_is_pinned_and_moved_off_the_audited_value():
    """`cbb7be44937a6b41` hashed two templates and not the contract (AUDIT.md).

    Folding the contract in necessarily moved the bare value; this pins where it
    moved to, so a later change to any of the three inputs is caught here first.
    """
    assert evaluate.prompt_signature() == "d23e9420b5812443"
    assert evaluate.prompt_signature() != "cbb7be44937a6b41"


def test_a_rewritten_contract_moves_the_signature(monkeypatch):
    """The audit's own reproduction: replace render_contract, watch the hash."""
    before = evaluate.prompt_signature()
    monkeypatch.setattr(evaluate, "render_contract", lambda test: "- Do whatever.")
    assert evaluate.prompt_signature() != before


def test_the_contract_probes_take_every_branch():
    rendered = [evaluate.render_contract(t) for t in evaluate.CONTRACT_PROBES]
    joined = "\n".join(rendered)
    assert "standard input" in joined and "already exist" in joined
    assert "Exit with status 0." in joined
    assert any("Exit with status 0." not in r for r in rendered)
    assert any(" a.txt 3" in r for r in rendered)


def test_the_system_file_is_its_own_paragraph_after_the_bare_prompt():
    msgs = evaluate.render_messages(CASE, "SPEC TEXT")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == evaluate.SYSTEM_PROMPT + "\n\nSPEC TEXT"
    assert evaluate.render_messages(CASE)[0]["content"] == evaluate.SYSTEM_PROMPT
    assert evaluate.render_messages(CASE)[1] == msgs[1]
    assert evaluate.SYSTEM_PROMPT == evaluate.system_prompt(None) == evaluate.system_prompt("  \n")


def test_the_hint_stays_unreachable_from_eval():
    """The context-distillation asymmetry: the spec is a system paragraph, never a hint."""
    source = Path(evaluate.__file__).read_text(encoding="utf-8")
    assert "hints" not in source
    spec = SPEC.read_text(encoding="utf-8")
    msgs = evaluate.render_messages(CASE, spec)
    first_line = hints.HINT_TEMPLATE.splitlines()[0][:40]
    assert all(first_line not in m["content"] for m in msgs)
    assert "worked example" not in msgs[0]["content"]


def test_meta_records_the_spec_signature_and_the_file_sha(tmp_path):
    backend = ChatBackend(base_url="http://x/v1", model="m")
    bare = evaluate.Evaluation(backend, [CASE], tmp_path / "bare").meta("h")
    spec = evaluate.Evaluation(backend, [CASE], tmp_path / "spec",
                               system_extra="SPEC TEXT",
                               system_file_sha256="ab" * 32).meta("h")
    assert bare["prompt_sha"] == evaluate.prompt_signature()
    assert bare["system_file_sha256"] is None
    assert spec["prompt_sha"] == evaluate.prompt_signature("SPEC TEXT") != bare["prompt_sha"]
    assert spec["system_file_sha256"] == "ab" * 32


# ------------------------------------------------------------------- the CLI


def test_nt_eval_system_file_is_read_hashed_and_forwarded(tmp_path, monkeypatch):
    from pipeline import cli
    import hashlib

    f = tmp_path / "spec.md"
    f.write_text("SPEC TEXT\n", encoding="utf-8")
    text, sha = cli._system_file(str(f))
    assert text == "SPEC TEXT\n"
    assert sha == hashlib.sha256(b"SPEC TEXT\n").hexdigest()
    assert cli._system_file(None) == (None, None)
    with pytest.raises(OSError):
        cli._system_file(str(tmp_path / "absent.md"))

    # The detached launcher must hand the inner command an absolute path,
    # because the inner command starts with `cd ROOT`.
    launched = {}
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda argv, **kw: launched.setdefault("argv", argv))
    monkeypatch.setattr(cli, "RUNS", tmp_path / "runs")
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["eval", "--system-file", "spec.md", "--run-id", "r1"])
    assert rc == 0
    shell = launched["argv"][-1]
    assert "--system-file" in shell and str(f.resolve()) in shell


def test_nt_eval_foreground_refuses_an_unreadable_system_file(tmp_path, monkeypatch, capsys):
    from pipeline import cli

    monkeypatch.setattr(cli, "load_holdout", lambda data: [CASE])
    monkeypatch.setattr(cli, "_backend", lambda args: ChatBackend(base_url="http://x/v1", model="m"))
    monkeypatch.setattr(cli, "RUNS", tmp_path / "runs")
    rc = cli.main(["eval", "--foreground", "--run-id", "r1",
                   "--system-file", str(tmp_path / "absent.md")])
    assert rc == 1
    assert "cannot read --system-file" in capsys.readouterr().err
    assert not (tmp_path / "runs" / "r1" / "meta.json").exists()

    # Not UTF-8 is the same refusal: the model would have been sent bytes the
    # signature could not name.
    (tmp_path / "latin1.md").write_bytes(b"caf\xe9\n")
    rc = cli.main(["eval", "--foreground", "--run-id", "r2",
                   "--system-file", str(tmp_path / "latin1.md")])
    assert rc == 1
    assert "cannot read --system-file" in capsys.readouterr().err
