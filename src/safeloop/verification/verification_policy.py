"""Layers 1-2 combined, producing a (possibly partial) VerificationResult."""
from __future__ import annotations

from ..domain.enums import FailureCategory
from ..domain.models import CodeArtifact, VerificationResult
from .ast_validator import parse_syntax
from .static_checks import StaticPolicy, run_static_checks


def validate_artifact(artifact: CodeArtifact, policy: StaticPolicy | None = None) -> VerificationResult:
    result = VerificationResult(task_id=artifact.task_id, artifact_id=artifact.artifact_id)
    tree, err = parse_syntax(artifact.source_code)
    if tree is None:
        result.failure_category = FailureCategory.SYNTAX_ERROR
        result.sanitized_diagnostics = err
        return result
    result.syntax_valid = True
    static = run_static_checks(tree, policy)
    if not static.passed:
        result.failure_category = FailureCategory.POLICY_VIOLATION
        result.sanitized_diagnostics = "; ".join(static.violations[:10])
        return result
    result.static_checks_passed = True
    return result
