"""Simple, explainable routing: honour user preference, else the configured default order.
No learned routing: add it only if benchmark results justify it."""
from __future__ import annotations

from dataclasses import dataclass

from ..domain.exceptions import InvalidTaskError
from ..domain.models import TaskSpec
from .providers.base import CodeProvider


@dataclass
class Route:
    provider: CodeProvider
    key: str
    reason: str


class ModelRouter:
    def __init__(self, providers: dict[str, CodeProvider], default_order: list[str] | None = None):
        if not providers:
            raise ValueError("at least one provider is required")
        self.providers = providers
        self.default_order = default_order or list(providers)

    def select(self, task: TaskSpec) -> Route:
        for pref in task.model_preferences:
            if pref in self.providers:
                return Route(self.providers[pref], pref, f"user preference '{pref}'")
        if task.model_preferences:
            raise InvalidTaskError(f"none of the preferred models are configured: {task.model_preferences}")
        key = self.default_order[0]
        return Route(self.providers[key], key, "default order")
