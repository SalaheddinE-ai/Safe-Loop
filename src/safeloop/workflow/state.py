from __future__ import annotations

from typing import Any, TypedDict

from ..core.budgets import Budget
from ..core.deadlines import Deadline
from ..domain.enums import TaskStatus, TerminationReason
from ..domain.models import (
    AttemptRecord,
    CodeArtifact,
    FinalReport,
    ModelUsage,
    TaskSpec,
    VerificationResult,
)


class SafeLoopState(TypedDict, total=False):
    task: TaskSpec
    budget: Budget
    deadline: Deadline
    artifact: CodeArtifact | None
    verification: VerificationResult | None
    attempts: list[AttemptRecord]
    artifacts: list[CodeArtifact]
    usages: list[ModelUsage]
    last_prompt: str
    pending_error: str  # set when generation produced no usable artifact
    status: TaskStatus
    reason: TerminationReason | None
    policy: str
    done: bool
    report: FinalReport | None
    meta: dict[str, Any]
