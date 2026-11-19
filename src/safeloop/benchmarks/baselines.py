"""The three compared configurations. Same acceptance tests, same sandbox, same provider."""
from __future__ import annotations

from dataclasses import dataclass

from ..verification.static_checks import StaticPolicy


@dataclass(frozen=True)
class Config:
    key: str
    name: str
    max_attempts: int | None  # None => use the benchmark-wide maximum
    static_policy: StaticPolicy


CONFIGS = {
    "A": Config("A", "Direct generation (no static gate, no repair)", 1,
                StaticPolicy(banned_imports=frozenset(), banned_calls=frozenset())),
    "B": Config("B", "Generate + deterministic verification (no repair)", 1, StaticPolicy()),
    "C": Config("C", "Safe-Loop (verification + bounded repair + guardrails)", None, StaticPolicy()),
}
