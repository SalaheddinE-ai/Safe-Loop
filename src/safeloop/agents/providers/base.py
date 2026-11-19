from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...domain.models import ModelUsage


@dataclass
class ProviderResponse:
    text: str  # raw model output; MUST be treated as untrusted
    usage: ModelUsage


class CodeProvider(Protocol):
    name: str

    def generate_code(self, prompt: str, *, timeout: float) -> ProviderResponse: ...
    def repair_code(self, prompt: str, *, timeout: float) -> ProviderResponse: ...
    def get_model_metadata(self) -> dict[str, str]: ...
    def estimate_cost(self, prompt: str) -> float | None:
        """USD estimate, or None when pricing is unavailable (never fabricated)."""
        ...
