"""Authored question-production tasks and bounded teacher experiment profiles.

Question outputs are proposals, never executable catalog entries or test oracles.
All comparison profiles reserve the same total completion-token ceiling.
"""
from __future__ import annotations


PROFILES = {
    "baseline": {"reasoning_effort": "none", "requests": 24,
                 "output_tokens_per_request": 2048},
    "compare-none": {"reasoning_effort": "none", "requests": 6,
                     "output_tokens_per_request": 8192},
    "compare-medium": {"reasoning_effort": "medium", "requests": 6,
                       "output_tokens_per_request": 8192},
    "question-proposals": {"reasoning_effort": "none", "requests": 12,
                           "output_tokens_per_request": 4096},
}


def profile(name):
    return dict(PROFILES[name], name=name, temperature=0.7, top_p=0.8,
                reserved_output_tokens=49152)


def question_tasks():
    domains = [
        ("text", "text parsing, normalization and deterministic reports"),
        ("records", "tabular records, grouping, joins and validation"),
        ("algorithms", "graphs, scheduling, searching and streaming algorithms"),
        ("numbers", "exact integer arithmetic, counting and bounded numerical transforms"),
        ("encodings", "byte encodings, checksums and serialization contracts"),
        ("automation", "developer automation and transformations of explicitly supplied inputs"),
    ]
    tasks = []
    for name, description in domains:
        tasks.append({
            "id": "questions-" + name, "family": "question-generation-" + name,
            "capabilities": [name], "kind": "question-proposals",
            "requested_questions": 5,
            "rights_basis": "Original repository-authored generation instruction; generated proposals require review.",
            "prompt": (
                "Create questions.jsonl containing 5 diverse original Python programming questions about "
                + description + ". Do not solve them. Each JSON line must have id, proposed_family, "
                "prompt, input_contract, output_contract, edge_cases (a list), difficulty, "
                "capability_targets (a list), and novelty_basis. Use a string id; all contract fields are "
                "strings and list items are strings. Keep each entire JSON record under 1600 characters. "
                "Include exact observable behavior, "
                "deterministic tie-breaking and error behavior. Target bounded stdin/argv to stdout "
                "programs using only the standard library; include both simple and compositional tasks. "
                "Do not copy benchmark questions or require private files, network, dependencies, clocks "
                "or randomness. Specify concrete input-size limits; byte-oriented tasks should accept "
                "and emit hex or other text encodings. State the valid-input domain explicitly; avoid "
                "contradictory exceptional-input rules or unsupported stderr/exit-code test contracts. "
                "Distinct semantics matter: renaming variables, changing constants or "
                "paraphrasing is not a new family. Do not invent expected outputs or correctness claims. "
                "Write a small local check that parses all 5 lines and checks the required fields. "
                "Prioritize writing the file, then checking it; do not inspect the empty workspace repeatedly. "
                "These are unreviewed proposals, not approved tasks, independent tests or training data."
            ),
        })
    return tasks
