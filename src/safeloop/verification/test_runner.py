"""Layer 3: run acceptance tests in the sandbox and convert the evidence."""
from __future__ import annotations

from ..domain.enums import FailureCategory
from ..domain.exceptions import SandboxExecutionError, SandboxUnavailable
from ..domain.models import CodeArtifact, VerificationResult
from ..sandbox.base import Sandbox, run_isolated
from .result_parser import MalformedTestOutput, parse_junit, sanitize_diagnostics


def execute_and_verify(
    sandbox: Sandbox, artifact: CodeArtifact, tests_source: str, base: VerificationResult
) -> VerificationResult:
    files = {"solution.py": artifact.source_code, "test_solution.py": tests_source}
    try:
        raw = run_isolated(sandbox, artifact.task_id, files)
    except (SandboxUnavailable, SandboxExecutionError) as exc:
        base.failure_category = FailureCategory.INFRASTRUCTURE
        base.sanitized_diagnostics = sanitize_diagnostics(f"sandbox error: {exc}")
        return base
    base.execution_duration = raw.duration
    if raw.timed_out:
        base.failure_category = FailureCategory.TEST_TIMEOUT
        base.sanitized_diagnostics = "execution exceeded the wall-clock limit"
        return base
    if raw.oom_killed:
        base.failure_category = FailureCategory.RESOURCE_LIMIT
        base.sanitized_diagnostics = "execution exceeded the memory limit"
        return base
    try:
        details, passed, failed = parse_junit(raw.stdout)
    except MalformedTestOutput as exc:
        # Distinguish: collection/import errors print pytest text but no junit; treat as
        # infrastructure only if the runner produced nothing usable at all.
        base.failure_category = (
            FailureCategory.TEST_FAILURE if raw.stdout.strip() else FailureCategory.INFRASTRUCTURE
        )
        base.sanitized_diagnostics = sanitize_diagnostics(f"{exc}; {raw.stdout[-800:]} {raw.stderr[-400:]}")
        return base
    base.test_details = details
    base.tests_passed, base.tests_failed = passed, failed
    if failed or passed == 0:
        base.failure_category = FailureCategory.TEST_FAILURE
        lines = [f"{d.node_id}: {d.message}" for d in details if d.outcome in ("failed", "error")]
        base.sanitized_diagnostics = sanitize_diagnostics("\n".join(lines) or "no tests passed")
    return base
