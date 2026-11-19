"""Deterministic provider for offline development and CI.

Scripted responses are returned in order; the last one repeats. Cost is always unavailable
(None) so the fake never pretends to have a price.
"""
from __future__ import annotations

from collections.abc import Sequence

from ...domain.enums import CostSource
from ...domain.exceptions import ProviderError
from ...domain.models import ModelUsage
from .base import ProviderResponse


class FakeProvider:
    name = "fake"

    def __init__(self, responses: Sequence[str | Exception], model: str = "fake-1"):
        self._responses = list(responses)
        self._i = 0
        self.model = model
        self.calls: list[tuple[str, str]] = []

    def _next(self, kind: str, prompt: str) -> ProviderResponse:
        self.calls.append((kind, prompt))
        item = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        if isinstance(item, Exception):
            raise item
        usage = ModelUsage(
            provider=self.name, model=self.model, cost_source=CostSource.UNAVAILABLE
        )
        return ProviderResponse(text=item, usage=usage)

    def generate_code(self, prompt: str, *, timeout: float) -> ProviderResponse:
        return self._next("generate", prompt)

    def repair_code(self, prompt: str, *, timeout: float) -> ProviderResponse:
        return self._next("repair", prompt)

    def get_model_metadata(self) -> dict[str, str]:
        return {"provider": self.name, "model": self.model}

    def estimate_cost(self, prompt: str) -> float | None:
        return None


__all__ = ["FakeProvider", "ProviderError"]
