"""Policy-driven static checks. Limited structural filter, not a sandbox substitute."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

DEFAULT_BANNED_IMPORTS = frozenset(
    {
        "os", "subprocess", "socket", "shutil", "ctypes", "multiprocessing", "threading",
        "requests", "urllib", "http", "ftplib", "smtplib", "pickle", "marshal", "importlib",
        "sys", "signal", "pty", "resource",
    }
)
DEFAULT_BANNED_CALLS = frozenset({"eval", "exec", "compile", "__import__", "open", "input", "breakpoint"})


DANGEROUS_ATTRS = frozenset({"__subclasses__", "__globals__", "__builtins__", "__code__", "__class__"})


@dataclass(frozen=True)
class StaticPolicy:
    banned_imports: frozenset[str] = DEFAULT_BANNED_IMPORTS
    banned_calls: frozenset[str] = DEFAULT_BANNED_CALLS


@dataclass
class StaticResult:
    passed: bool
    violations: list[str] = field(default_factory=list)


def run_static_checks(tree: ast.Module, policy: StaticPolicy | None = None) -> StaticResult:
    policy = policy or StaticPolicy()
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in policy.banned_imports:
                    violations.append(f"line {node.lineno}: import '{a.name}' is prohibited")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and (node.module or "").split(".")[0] in policy.banned_imports:
                violations.append(f"line {node.lineno}: import from '{node.module}' is prohibited")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in policy.banned_calls:
                violations.append(f"line {node.lineno}: call to '{fn.id}' is prohibited")
        elif isinstance(node, ast.Attribute) and node.attr in DANGEROUS_ATTRS:
            violations.append(f"line {node.lineno}: access to '{node.attr}' is prohibited")
    return StaticResult(passed=not violations, violations=violations)
