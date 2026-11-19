"""Centralised stop-condition logic. Hard safety limits are separated from tunable ones."""
from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import FailureCategory, TaskStatus, TerminationReason
from ..domain.models import VerificationResult
from .budgets import Budget
from .deadlines import Deadline


@dataclass(frozen=True)
class StopDecision:
    stop: bool
    status: TaskStatus | None = None
    reason: TerminationReason | None = None
    policy: str = ""


CONTINUE = StopDecision(stop=False)


def evaluate_stop(
    *,
    verification: VerificationResult | None,
    budget: Budget,
    deadline: Deadline,
    stagnated: bool,
    cancelled: bool,
    next_call_cost: float | None = None,
) -> StopDecision:
    # 1. Hard limits first (never overridden by model output).
    if cancelled:
        return StopDecision(True, TaskStatus.CANCELLED, TerminationReason.USER_CANCELLED, "cancel")
    if verification is not None:
        if verification.failure_category == FailureCategory.POLICY_VIOLATION:
            return StopDecision(
                True, TaskStatus.BLOCKED_BY_POLICY, TerminationReason.POLICY_VIOLATION, "code_policy"
            )
        if verification.failure_category == FailureCategory.INFRASTRUCTURE:
            return StopDecision(
                True, TaskStatus.SANDBOX_ERROR, TerminationReason.SANDBOX_UNAVAILABLE, "sandbox"
            )
        # 2. Success is decided only by deterministic evidence.
        if verification.acceptance_met:
            return StopDecision(
                True, TaskStatus.SUCCESS, TerminationReason.ACCEPTANCE_CRITERIA_MET, "acceptance"
            )
    if deadline.expired:
        return StopDecision(True, TaskStatus.DEADLINE_EXCEEDED, TerminationReason.DEADLINE, "deadline")
    # 3. Is a repair possible?
    if verification is not None and not verification.failure_category.repairable:
        return StopDecision(
            True, TaskStatus.FAILED_VERIFICATION, TerminationReason.NON_REPAIRABLE_FAILURE, "repairable"
        )
    if not budget.can_attempt():
        return StopDecision(
            True, TaskStatus.FAILED_VERIFICATION, TerminationReason.MAX_ATTEMPTS_REACHED, "max_attempts"
        )
    if not budget.can_afford(next_call_cost):
        return StopDecision(True, TaskStatus.BUDGET_EXHAUSTED, TerminationReason.COST_LIMIT, "cost")
    if stagnated:
        return StopDecision(
            True, TaskStatus.FAILED_VERIFICATION, TerminationReason.STAGNATION, "stagnation"
        )
    return CONTINUE
