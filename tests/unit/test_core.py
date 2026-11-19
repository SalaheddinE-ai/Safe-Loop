import pytest
from pydantic import ValidationError

from safeloop.core.budgets import Budget
from safeloop.core.deadlines import Deadline
from safeloop.core.fingerprints import sha256_hex, structural_fingerprint
from safeloop.core.policies import evaluate_stop
from safeloop.domain.enums import CostSource, FailureCategory, TaskStatus, TerminationReason
from safeloop.domain.models import TaskSpec, VerificationResult


def spec(**kw):
    return TaskSpec(user_request="write an add function", test_strategy="def test_x(): pass", **kw)


def test_taskspec_rejects_bad_input():
    with pytest.raises(ValidationError):
        TaskSpec(user_request="short", test_strategy="x")
    with pytest.raises(ValidationError):
        spec(language="rust")
    with pytest.raises(ValidationError):
        spec(maximum_attempts=0)
    with pytest.raises(ValidationError):
        spec(timeout_seconds=-1)
    with pytest.raises(ValidationError):
        TaskSpec(user_request="write an add function", test_strategy="x", bogus=1)


def test_budget_attempts_and_cost():
    b = Budget(2, 0.10)
    assert b.can_attempt() and b.can_afford(0.05)
    b.record_attempt(); b.record_cost(0.08, CostSource.REPORTED)
    assert not b.can_afford(0.05)
    assert b.can_afford(None)  # unknown cost is never fabricated
    b.record_attempt()
    assert not b.can_attempt()


def test_deadline_with_fake_clock():
    t = [0.0]
    d = Deadline(10, clock=lambda: t[0])
    assert not d.expired
    t[0] = 10.1
    assert d.expired and d.remaining == 0.0


def test_fingerprints():
    assert sha256_hex("a") == sha256_hex("a") != sha256_hex("b")
    a = structural_fingerprint("def f(x):\n    return x + 1\n")
    b = structural_fingerprint("def g(y):\n    return y + 2\n")
    c = structural_fingerprint("def f(x):\n    return x * x\n")
    assert a == b and a != c
    assert structural_fingerprint("def (") is None


def _ver(**kw):
    return VerificationResult(task_id="t", artifact_id="a", **kw)


def _stop(ver, budget=None, deadline=None, **kw):
    return evaluate_stop(
        verification=ver, budget=budget or Budget(3, 1.0),
        deadline=deadline or Deadline(100), stagnated=kw.get("stagnated", False),
        cancelled=kw.get("cancelled", False), next_call_cost=kw.get("cost"),
    )


def test_stop_success_requires_evidence():
    ok = _ver(syntax_valid=True, static_checks_passed=True, tests_passed=2)
    assert _stop(ok).status == TaskStatus.SUCCESS
    zero = _ver(syntax_valid=True, static_checks_passed=True, tests_passed=0,
                failure_category=FailureCategory.TEST_FAILURE)
    assert _stop(zero).status != TaskStatus.SUCCESS


def test_stop_policy_violation_is_immediate_and_not_repairable():
    d = _stop(_ver(failure_category=FailureCategory.POLICY_VIOLATION))
    assert d.status == TaskStatus.BLOCKED_BY_POLICY and d.reason == TerminationReason.POLICY_VIOLATION


def test_stop_infrastructure_fail_closed():
    assert _stop(_ver(failure_category=FailureCategory.INFRASTRUCTURE)).status == TaskStatus.SANDBOX_ERROR


def test_stop_budget_deadline_attempts_stagnation_cancel():
    fail = _ver(syntax_valid=True, static_checks_passed=True, failure_category=FailureCategory.TEST_FAILURE)
    assert not _stop(fail).stop  # repairable, budget left
    b = Budget(1, 1.0); b.record_attempt()
    assert _stop(fail, budget=b).reason == TerminationReason.MAX_ATTEMPTS_REACHED
    b2 = Budget(3, 0.01); b2.record_cost(0.01, CostSource.REPORTED)
    assert _stop(fail, budget=b2, cost=0.5).status == TaskStatus.BUDGET_EXHAUSTED
    t = [0.0]; dl = Deadline(1, clock=lambda: t[0]); t[0] = 5
    assert _stop(fail, deadline=dl).status == TaskStatus.DEADLINE_EXCEEDED
    assert _stop(fail, stagnated=True).reason == TerminationReason.STAGNATION
    assert _stop(fail, cancelled=True).status == TaskStatus.CANCELLED


def test_every_failure_category_has_defined_repairability():
    assert FailureCategory.TEST_FAILURE.repairable
    assert not FailureCategory.INFRASTRUCTURE.repairable
    assert not FailureCategory.POLICY_VIOLATION.repairable
