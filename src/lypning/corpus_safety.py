"""Recognise captured environment mutations before either conformance arm runs.

This is an admission net, not a sandbox or a Python security analyser. Captured
package installs modify the shared interpreter outside a temporary cwd; model
downloads modify external caches and may consume gigabytes. Neither belongs in
an ordinary replay. Keep the records, report explicit safety skips, and require
an externally isolated worker to study those programs separately.
"""

from __future__ import annotations

import ast
import re
import shlex
from typing import Dict, List, Optional


def _name(node: ast.AST, aliases: Dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return _name(node.value, aliases) + "." + node.attr
    return ""


def _mutates_packages(argv: List[str]) -> bool:
    if not argv:
        return False
    command = argv[0].rsplit("/", 1)[-1]
    rest = argv[1:]
    if command in ("sh", "bash", "zsh") and len(rest) >= 2 and rest[0] == "-c":
        return _shell_mutates(rest[1])
    if command == "sudo":
        return _mutates_packages(rest)
    if command == "env":
        while rest and "=" in rest[0]:
            rest = rest[1:]
        return _mutates_packages(rest)
    if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", command):
        while rest:
            if rest[0] == "-m":
                return _mutates_packages(rest[1:])
            if rest[0] in ("-c", "-", "--") or not rest[0].startswith("-"):
                return False  # everything after this belongs to the program
            rest = rest[2:] if rest[0] in ("-X", "-W") else rest[1:]
        return False
    if command == "uv":
        if rest and rest[0] == "pip":
            return _mutates_packages(["pip"] + rest[1:])
        return bool(rest and rest[0] in ("add", "remove", "sync"))
    if not re.fullmatch(r"pip(?:\d+(?:\.\d+)?)?", command):
        return False
    # Global options precede the subcommand; read-only `pip show install`
    # must not be confused with `pip install show`.
    values = {"--python", "--log", "--proxy", "--retries", "--timeout",
              "--cache-dir", "--cert", "--client-cert"}
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in values else rest[1:]
    return bool(rest and rest[0] in ("install", "uninstall", "sync"))


def _shell_mutates(command: str) -> bool:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    part: List[str] = []
    for token in tokens + [";"]:
        if token and all(c in ";&|" for c in token):
            if _mutates_packages(part):
                return True
            part = []
        else:
            part.append(token)
    return False


def external_mutation(tree: Optional[ast.AST]) -> str:
    """Return an explicit safety-skip reason for recognised executable calls."""
    if tree is None:
        return ""
    aliases: Dict[str, str] = {}
    assigned: Dict[str, List[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = node.module + "." + alias.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            if isinstance(node.targets[0], ast.Name):
                assigned.setdefault(node.targets[0].id, []).append(node.value)

    def argument(node: ast.AST):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, (ast.List, ast.Tuple)):
            return [item.value if isinstance(item, ast.Constant) and isinstance(item.value, str)
                    else "python" if _name(item, aliases) == "sys.executable" else ""
                    for item in node.elts]
        return None

    processes = {"subprocess." + method for method in (
        "run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput")}
    processes.update(("os.system", "os.popen", "os.execvp", "os.execv"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _name(node.func, aliases)
        if name in ("huggingface_hub.hf_hub_download", "huggingface_hub.snapshot_download"):
            return "external mutation: model download outside the replay cwd"
        pip_api = name.startswith("pip.") and name.endswith(".main")
        if name not in processes and not pip_api:
            continue
        arg = node.args[0] if node.args else next(
            (kw.value for kw in node.keywords if kw.arg in ("args", "command")), None)
        if name in ("os.execv", "os.execvp") and len(node.args) > 1:
            arg = node.args[1]
        candidates = assigned.get(arg.id, [arg]) if isinstance(arg, ast.Name) else [arg]
        shell = name in ("os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput")
        shell = shell or any(kw.arg == "shell" and isinstance(kw.value, ast.Constant)
                             and kw.value.value is True for kw in node.keywords)
        for candidate in candidates:
            value = argument(candidate) if candidate is not None else None
            if pip_api and isinstance(value, list):
                value = ["pip"] + value
            if ((isinstance(value, list) and _mutates_packages(value))
                    or (shell and isinstance(value, str) and _shell_mutates(value))):
                return "external mutation: package manager changes the replay environment"
    return ""
