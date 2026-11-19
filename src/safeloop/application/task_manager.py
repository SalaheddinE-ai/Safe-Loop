"""Application service shared by API, CLI and UI. Bounded worker pool, no unbounded queue."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from ..agents.model_router import ModelRouter
from ..core.config import Settings
from ..domain.enums import TaskStatus
from ..domain.exceptions import InvalidTaskError
from ..domain.models import FinalReport, TaskSpec
from ..observability import metrics as m
from ..observability.logging import log_event
from ..observability.tracing import span
from ..persistence.repositories import Repository
from ..sandbox.base import Sandbox
from ..workflow.graph import Deps, run_task


class QueueFull(Exception):
    pass


class TaskNotFound(Exception):
    pass


class TaskConflict(Exception):
    pass


class TaskManager:
    def __init__(self, repo: Repository, router: ModelRouter, sandbox_factory: Callable[[], Sandbox],
                 settings: Settings | None = None, max_workers: int = 2, max_queue: int = 16,
                 sandbox_health: Callable[[], tuple[bool, str]] | None = None):
        self.repo, self.router, self.settings = repo, router, settings or Settings()
        self._sandbox_factory = sandbox_factory
        self._sandbox_health = sandbox_health
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="safeloop-worker")
        self._capacity = max_workers + max_queue
        self._max_workers = max_workers
        self._lock = threading.Lock()
        self._inflight = 0
        self._running = 0
        self._cancel: dict[str, threading.Event] = {}
        self._futures: dict[str, object] = {}

    # ---- submission
    def submit(self, spec: TaskSpec, provider_override=None) -> str:
        route = self._route(spec, provider_override)  # raises InvalidTaskError before any model call
        with self._lock:
            if self._inflight >= self._capacity:
                raise QueueFull("task queue is full")
            self._inflight += 1
            m.queue_depth.set(max(0, self._inflight - self._running))
        self._cancel[spec.task_id] = threading.Event()
        model = route.provider.get_model_metadata().get("model", route.key)
        self.repo.create_task(spec, model=f"{route.provider.name}/{model}")
        self.repo.add_event(spec.task_id, "queued", {"route": route.reason})
        self._futures[spec.task_id] = self._pool.submit(self._run, spec, route.provider)
        return spec.task_id

    def _route(self, spec: TaskSpec, override):
        if override is not None:  # dev/test only: gated by the API setting
            from ..agents.model_router import Route
            return Route(override, override.name, "dev override")
        return self.router.select(spec)

    def cancel(self, task_id: str) -> None:
        t = self.repo.get_task(task_id)
        if t is None:
            raise TaskNotFound(task_id)
        if TaskStatus(t["status"]).is_terminal:
            raise TaskConflict("task already finished")
        ev = self._cancel.get(task_id)
        if ev is None:
            raise TaskConflict("task is not owned by this process")
        ev.set()
        self.repo.add_event(task_id, "cancel_requested")

    def wait(self, task_id: str, timeout: float = 30.0) -> None:
        fut = self._futures.get(task_id)
        if fut is not None:
            fut.result(timeout=timeout)  # type: ignore[attr-defined]

    # ---- execution
    def _run(self, spec: TaskSpec, provider) -> None:
        with self._lock:
            self._running += 1
            m.queue_depth.set(max(0, self._inflight - self._running))
        m.active_tasks.inc()
        t0 = time.monotonic()
        report: FinalReport | None = None
        try:
            self.repo.set_status(spec.task_id, TaskStatus.RUNNING.value)
            self.repo.add_event(spec.task_id, "started")
            cancel = self._cancel[spec.task_id]

            def on_attempt(task, rec, art, ver):
                self.repo.save_attempt(task.task_id, rec, art, ver)
                self.repo.add_event(task.task_id, "attempt", {
                    "attempt": rec.attempt_number, "outcome": rec.outcome, "reason": rec.repair_reason})
                if ver is not None:
                    m.verification_runs_total.labels(ver.failure_category.value).inc()
                if rec.outcome == "REPAIR":
                    m.repairs_total.inc()

            deps = Deps(provider=m.InstrumentedProvider(provider),
                        sandbox=m.InstrumentedSandbox(self._sandbox_factory()),
                        settings=self.settings, is_cancelled=cancel.is_set, on_attempt=on_attempt)
            with span("safeloop.task", task_id=spec.task_id, provider=provider.name):
                report = run_task(spec, deps)
        except Exception as exc:  # never lose a task silently
            log_event("task_crashed", error_type=type(exc).__name__, task=spec.task_id)
            from ..domain.enums import TerminationReason
            report = FinalReport(task_id=spec.task_id, final_status=TaskStatus.SANDBOX_ERROR,
                                 termination_reason=TerminationReason.SANDBOX_UNAVAILABLE,
                                 triggered_policy="internal_error",
                                 limitations=[f"internal error: {type(exc).__name__}"])
        finally:
            m.active_tasks.dec()
            with self._lock:
                self._running -= 1
                self._inflight -= 1
                m.queue_depth.set(max(0, self._inflight - self._running))
        assert report is not None
        self.repo.save_report(report)
        self.repo.add_event(spec.task_id, "finished", {"status": report.final_status.value})
        m.tasks_total.labels(report.final_status.value).inc()
        m.task_duration_seconds.observe(time.monotonic() - t0)
        if report.termination_reason.value in m.GUARDRAIL_REASONS:
            m.guardrail_terminations_total.labels(report.termination_reason.value).inc()
        log_event("task_finished", task=spec.task_id, outcome=report.final_status.value,
                  attempts=report.attempt_count, duration=round(report.total_duration, 3),
                  reason=report.termination_reason.value)

    # ---- queries
    def health(self) -> dict:
        db_ok = self.repo.ping()
        sb: dict[str, bool | str | None] = {"ok": None, "detail": "not checked"}
        if self._sandbox_health:
            ok, detail = self._sandbox_health()
            sb = {"ok": ok, "detail": detail}
        return {"status": "ok" if db_ok and sb["ok"] is not False else "degraded",
                "database": db_ok, "sandbox": sb, "workers": {"max": self._max_workers,
                "running": self._running, "queued": max(0, self._inflight - self._running)}}

    def shutdown(self) -> None:
        for ev in self._cancel.values():
            ev.set()
        self._pool.shutdown(wait=True, cancel_futures=True)


def validate_scripted(provider_key: str, scripted: list[str] | None) -> None:
    if scripted and provider_key != "fake":
        raise InvalidTaskError("scripted responses are only allowed with the fake provider")
