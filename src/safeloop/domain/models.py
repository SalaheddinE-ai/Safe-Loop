from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import CostSource, FailureCategory, TaskStatus, TerminationReason


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskSpec(_Model):
    task_id: str = Field(default_factory=lambda: new_id("task"))
    user_request: str = Field(min_length=10, max_length=8000)
    language: str = "python"
    requirements: list[str] = Field(default_factory=list, max_length=50)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    # Acceptance tests (pytest source). They import the candidate as `solution`.
    test_strategy: str = Field(min_length=1, max_length=50_000)
    created_at: datetime = Field(default_factory=utcnow)
    timeout_seconds: float = Field(default=120.0, gt=0, le=3600)
    maximum_attempts: int = Field(default=3, ge=1, le=10)
    maximum_cost: float = Field(default=1.0, ge=0)  # USD
    model_preferences: list[str] = Field(default_factory=list, max_length=10)
    status: TaskStatus = TaskStatus.PENDING

    @field_validator("language")
    @classmethod
    def _python_only(cls, v: str) -> str:
        if v.lower() != "python":
            raise ValueError("only 'python' is supported in this release")
        return v.lower()


class ModelUsage(_Model):
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    cost_source: CostSource = CostSource.UNAVAILABLE
    latency_seconds: float = 0.0
    request_status: str = "ok"


class CodeArtifact(_Model):
    artifact_id: str = Field(default_factory=lambda: new_id("art"))
    task_id: str
    attempt_number: int
    source_code: str
    content_hash: str
    created_at: datetime = Field(default_factory=utcnow)
    provider_metadata: dict[str, str] = Field(default_factory=dict)


class TestDetail(_Model):
    __test__ = False  # not a pytest class
    node_id: str
    outcome: str  # passed | failed | error | skipped
    message: str = ""


class VerificationResult(_Model):
    verification_id: str = Field(default_factory=lambda: new_id("ver"))
    task_id: str
    artifact_id: str
    syntax_valid: bool = False
    static_checks_passed: bool = False
    tests_passed: int = 0
    tests_failed: int = 0
    test_details: list[TestDetail] = Field(default_factory=list)
    execution_duration: float = 0.0
    resource_usage: dict[str, float] = Field(default_factory=dict)
    failure_category: FailureCategory = FailureCategory.NONE
    sanitized_diagnostics: str = ""

    @property
    def acceptance_met(self) -> bool:
        return (
            self.syntax_valid
            and self.static_checks_passed
            and self.tests_failed == 0
            and self.tests_passed > 0
            and self.failure_category == FailureCategory.NONE
        )


class AttemptRecord(_Model):
    attempt_number: int
    artifact_id: str | None = None
    verification_id: str | None = None
    repair_reason: str = ""
    cost_usd: float = 0.0
    duration: float = 0.0
    outcome: str = ""


class FinalReport(_Model):
    task_id: str
    final_status: TaskStatus
    final_artifact_id: str | None = None
    verification_summary: str = ""
    attempt_count: int = 0
    total_duration: float = 0.0
    total_cost_usd: float = 0.0
    cost_sources: list[CostSource] = Field(default_factory=list)
    termination_reason: TerminationReason
    triggered_policy: str = ""
    limitations: list[str] = Field(default_factory=list)
    reproducibility_metadata: dict[str, str] = Field(default_factory=dict)
