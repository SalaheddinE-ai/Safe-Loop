from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class RawExecution:
    exit_code: int | None
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    oom_killed: bool = False
    truncated: bool = False


class Sandbox(Protocol):
    """Replaceable isolation boundary. Implementations MUST fail closed."""

    def create_execution(self, task_id: str, files: dict[str, str]) -> str: ...
    def execute_tests(self, execution_id: str) -> None: ...
    def collect_results(self, execution_id: str) -> RawExecution: ...
    def terminate_execution(self, execution_id: str) -> None: ...
    def cleanup_execution(self, execution_id: str) -> None: ...


def run_isolated(sandbox: Sandbox, task_id: str, files: dict[str, str]) -> RawExecution:
    """Create -> execute -> collect, always terminating and cleaning up."""
    execution_id = sandbox.create_execution(task_id, files)
    try:
        sandbox.execute_tests(execution_id)
        return sandbox.collect_results(execution_id)
    finally:
        try:
            sandbox.terminate_execution(execution_id)
        finally:
            sandbox.cleanup_execution(execution_id)
