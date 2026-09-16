"""`EVAL2.md` is a pre-registration: its claims about the code must stay true.

A pre-registration that drifts from the code it names is worse than none,
because it reads as authority. These pins fire when the decoding contract, the
backend's sampling parameters or the power-analysis helper change without the
document changing with them, and when a link or section reference goes stale.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from pipeline import backends, stats
from pipeline.training_contract import decoding

TRAINING = Path(__file__).resolve().parents[1]
ROOT = TRAINING.parent
EVAL2 = TRAINING / "EVAL2.md"


def _text(path):
    return path.read_text(encoding="utf-8")


def _headings(path):
    out = set()
    for line in _text(path).splitlines():
        m = re.match(r"#{1,6}\s+(\d+[a-z]?)[.)]?\s", line)
        if m:
            out.add(m.group(1))
    return out


def test_every_registered_section_is_present():
    found = _headings(EVAL2)
    assert {str(n) for n in range(1, 11)} <= found, "EVAL2.md lost a section: " + str(sorted(found))


def test_relative_links_resolve_and_the_two_homes_link_back():
    text = _text(EVAL2)
    for m in re.finditer(r"\]\(([^)\s#]+\.md)(?:#[^)]*)?\)", text):
        target = m.group(1)
        assert (EVAL2.parent / target).exists() or (ROOT / target).exists(), target
    for doc in ("STATUS.md", "LADDER.md"):
        assert "](EVAL2.md)" in _text(TRAINING / doc), doc + " must link to EVAL2.md"


def test_section_cross_references_land_on_a_heading():
    bad = []
    for m in re.finditer(r"`([A-Za-z0-9_./-]+\.md)`\s*§\s*(\d+[a-z]?)", _text(EVAL2)):
        target, section = m.groups()
        for base in (EVAL2.parent, ROOT):
            path = base / target
            if path.exists():
                if section not in _headings(path):
                    bad.append("%s §%s" % (target, section))
                break
        else:
            bad.append(target + " (no such file)")
    assert not bad, bad


def test_the_stated_decoding_contract_is_the_frozen_one():
    section = _text(EVAL2).split("## 5.")[1].split("## 6.")[0]
    policy = decoding(1024)
    for phrase, key, want in (("temperature 0.7", "temperature", 0.7), ("top-p 0.8", "top_p", 0.8),
                              ("top-k 20", "top_k", 20), ("min-p 0", "min_p", 0.0),
                              ("repetition penalty 1", "repetition_penalty", 1.0),
                              ("1,024 new tokens", "max_new_tokens", 1024), ("one beam", "num_beams", 1)):
        assert phrase in section, phrase
        assert policy[key] == want, (key, policy[key])
    assert policy["do_sample"] is True
    assert "k = 16" in _text(EVAL2).split("## 4.")[1].split("## 5.")[0]


def test_claims_about_code_that_does_not_exist_yet_are_still_true():
    text = _text(EVAL2)
    # Section 5: the backend passes top_k since 2026-09-16, and the document
    # says so — and still says a legacy arm is a different arm.
    params = inspect.signature(backends.ChatBackend.complete).parameters
    assert "top_k" in params and "min_p" in params, \
        "backends.py stopped passing top_k/min_p; EVAL2.md section 5 must say so"
    section = text.split("## 5.")[1].split("## 6.")[0]
    assert "passes `top_k` and `min_p`" in section
    assert "`stats.SAMPLING_KEYS` carries `top_k`" in section
    assert "top_k" in stats.SAMPLING_KEYS and stats.SAMPLING_DEFAULTS.get("top_k", 0) is None
    assert "different serving stack" in section
    # Section 7: the cluster power curve exists (2026-09-16), the document says
    # so with its command, and the pilot's curve is appended there, dated, once
    # the pilot has been drawn — until then the section says it is pending.
    assert hasattr(stats, "power_curve_clustered")
    section = text.split("## 7.")[1].split("## 8.")[0]
    assert "stats.power_curve_clustered" in section
    assert "nt power --eval2" in section
    assert "2026-09-16" in section


def test_the_changelog_carries_the_entry_under_pr_79():
    text = _text(ROOT / "CHANGELOG.md")
    entry = text.split("pull/79)")[1].split("**2026-09-15** — Check question")[0]
    assert "Pre-register eval-2 in `training/EVAL2.md`" in entry


@pytest.mark.parametrize("word", ["lypning", "refuse", "unsupported", "tier", "engine", "CPython", "without importing"])
def test_the_no_runtime_name_rule_names_each_word(word):
    section = " ".join(_text(EVAL2).split("## 2.")[1].split("## 3.")[0].split())
    assert word in section
