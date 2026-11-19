"""Benchmark metrics with explicit denominators. Missing data yields None ("Not yet measured")."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class RunResult:
    task_id: str
    config: str
    repeat: int
    status: str
    attempts: int
    first_attempt_success: bool
    regression: bool
    executed_attempts: int
    duration: float
    cost_usd: float | None  # None when no cost was reported/estimated
    termination_reason: str = ""


@dataclass
class Summary:
    config: str
    n_runs: int
    pass_at_1: float | None
    final_success_rate: float | None
    regression_rate: float | None
    regression_denominator: int
    mean_repair_attempts: float | None
    median_repair_attempts: float | None
    cost_per_task: float | None
    cost_known_runs: int
    cost_per_successful_task: float | None
    median_latency: float | None
    p95_latency: float | None
    provider_error_rate: float | None
    sandbox_failure_rate: float | None
    budget_exhaustion_rate: float | None
    failure_breakdown: dict[str, int] = field(default_factory=dict)


def _p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, round(0.95 * len(xs) + 0.5) - 1))]


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


FAIL_CLASS = {"FAILED_VERIFICATION": "functional", "PROVIDER_ERROR": "provider",
              "SANDBOX_ERROR": "infrastructure", "BLOCKED_BY_POLICY": "policy_block",
              "BUDGET_EXHAUSTED": "budget", "DEADLINE_EXCEEDED": "deadline", "CANCELLED": "cancelled"}


def summarize(config: str, runs: list[RunResult]) -> Summary:
    n = len(runs)
    succ = [r for r in runs if r.status == "SUCCESS"]
    multi = [r for r in runs if r.executed_attempts >= 2]  # regression needs >=2 executed attempts
    repairs = [r.attempts - 1 for r in runs if r.attempts >= 1]
    known = [r for r in runs if r.cost_usd is not None]
    succ_known = [r for r in succ if r.cost_usd is not None]
    lat = [r.duration for r in runs]
    breakdown: dict[str, int] = {}
    for r in runs:
        if r.status != "SUCCESS":
            k = FAIL_CLASS.get(r.status, r.status.lower())
            breakdown[k] = breakdown.get(k, 0) + 1
    return Summary(
        config=config, n_runs=n,
        pass_at_1=_rate(sum(r.first_attempt_success for r in runs), n),
        final_success_rate=_rate(len(succ), n),
        regression_rate=_rate(sum(r.regression for r in multi), len(multi)),
        regression_denominator=len(multi),
        mean_repair_attempts=statistics.mean(repairs) if repairs else None,
        median_repair_attempts=statistics.median(repairs) if repairs else None,
        cost_per_task=(sum(r.cost_usd for r in known) / len(known)) if known else None,  # type: ignore[misc]
        cost_known_runs=len(known),
        cost_per_successful_task=(sum(r.cost_usd for r in succ_known) / len(succ_known)) if succ_known else None,  # type: ignore[misc]
        median_latency=statistics.median(lat) if lat else None, p95_latency=_p95(lat),
        provider_error_rate=_rate(sum(r.status == "PROVIDER_ERROR" for r in runs), n),
        sandbox_failure_rate=_rate(sum(r.status == "SANDBOX_ERROR" for r in runs), n),
        budget_exhaustion_rate=_rate(sum(r.status == "BUDGET_EXHAUSTED" for r in runs), n),
        failure_breakdown=breakdown,
    )
