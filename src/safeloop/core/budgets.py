from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.enums import CostSource


@dataclass
class Budget:
    """Tracks attempts and cost. Cost checks happen BEFORE a model call."""

    max_attempts: int
    max_cost_usd: float
    attempts_used: int = 0
    cost_usd: float = 0.0
    sources: set[CostSource] = field(default_factory=set)

    @property
    def attempts_remaining(self) -> int:
        return max(0, self.max_attempts - self.attempts_used)

    @property
    def cost_remaining(self) -> float:
        return max(0.0, self.max_cost_usd - self.cost_usd)

    def can_attempt(self) -> bool:
        return self.attempts_remaining > 0

    def can_afford(self, estimated_cost: float | None) -> bool:
        # Unknown cost (None) is treated as 0: we never fabricate a price. If the cost is
        # unknown and max_cost is 0 the call is still allowed only for free providers.
        return self.cost_usd + (estimated_cost or 0.0) <= self.max_cost_usd

    def record_attempt(self) -> None:
        self.attempts_used += 1

    def record_cost(self, amount: float | None, source: CostSource) -> None:
        self.sources.add(source)
        if amount is not None:
            self.cost_usd += amount
