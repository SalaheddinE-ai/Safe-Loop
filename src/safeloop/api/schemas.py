from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_request: str = Field(min_length=10, max_length=8000)
    test_strategy: str = Field(min_length=1, max_length=50_000, description="pytest source importing `solution`")
    requirements: list[str] = Field(default_factory=list, max_length=50)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    timeout_seconds: float = Field(default=120.0, gt=0, le=3600)
    maximum_attempts: int = Field(default=3, ge=1, le=10)
    maximum_cost: float = Field(default=1.0, ge=0, le=100)
    model_preferences: list[str] = Field(default_factory=list, max_length=10)
    dev_scripted_responses: list[str] | None = Field(
        default=None, max_length=20, description="DEV ONLY: accepted only when the server enables it")


class TaskCreated(BaseModel):
    task_id: str
    status: str


class Page(BaseModel):
    items: list[dict]
    total: int
    limit: int
    offset: int
