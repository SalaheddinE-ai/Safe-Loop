"""Repository over SQLAlchemy. Business logic never touches the ORM directly, so PostgreSQL
can replace SQLite by changing the URL (and adding migrations)."""
from __future__ import annotations

import functools
import json
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import sessionmaker

from ..domain.models import (
    AttemptRecord,
    CodeArtifact,
    FinalReport,
    ModelUsage,
    TaskSpec,
    VerificationResult,
    utcnow,
)
from .database import AttemptRow, EventRow, TaskRow, UsageRow


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self._lock:  # SQLite is single-writer; also protects the shared in-memory connection
            return fn(self, *a, **kw)
    return wrapper


class Repository:
    def __init__(self, session_factory: sessionmaker):
        self._sf = session_factory
        self._lock = threading.RLock()

    # ---- tasks
    def create_task(self, spec: TaskSpec, model: str = "") -> None:
        with self._sf.begin() as s:
            s.add(TaskRow(task_id=spec.task_id, status=spec.status.value,
                          created_at=spec.created_at.isoformat(), model=model,
                          spec_json=spec.model_dump_json()))

    def set_status(self, task_id: str, status: str) -> None:
        with self._sf.begin() as s:
            row = s.get(TaskRow, task_id)
            if row:
                row.status = status

    def save_report(self, report: FinalReport) -> None:
        with self._sf.begin() as s:
            row = s.get(TaskRow, report.task_id)
            if row is None:
                return
            row.status = report.final_status.value
            row.termination_reason = report.termination_reason.value
            row.report_json = report.model_dump_json()
            row.total_cost_usd = report.total_cost_usd
            row.duration = report.total_duration
            row.finished_at = utcnow().isoformat()

    def get_task(self, task_id: str) -> dict | None:
        with self._sf() as s:
            r = s.get(TaskRow, task_id)
            return self._task_dict(r) if r else None

    def list_tasks(self, *, limit: int = 20, offset: int = 0, status: str | None = None,
                   model: str | None = None, reason: str | None = None,
                   since: str | None = None) -> tuple[list[dict], int]:
        q = select(TaskRow)
        if status:
            q = q.where(TaskRow.status == status)
        if model:
            q = q.where(TaskRow.model == model)
        if reason:
            q = q.where(TaskRow.termination_reason == reason)
        if since:
            q = q.where(TaskRow.created_at >= since)
        with self._sf() as s:
            total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
            rows = s.scalars(q.order_by(TaskRow.created_at.desc()).limit(limit).offset(offset)).all()
            return [self._task_dict(r) for r in rows], total

    @staticmethod
    def _task_dict(r: TaskRow) -> dict:
        return {"task_id": r.task_id, "status": r.status, "created_at": r.created_at,
                "finished_at": r.finished_at, "model": r.model,
                "termination_reason": r.termination_reason or None,
                "total_cost_usd": r.total_cost_usd, "duration": r.duration}

    def get_report(self, task_id: str) -> FinalReport | None:
        with self._sf() as s:
            r = s.get(TaskRow, task_id)
            return FinalReport.model_validate_json(r.report_json) if r and r.report_json else None

    # ---- attempts
    def save_attempt(self, task_id: str, rec: AttemptRecord, art: CodeArtifact | None,
                     ver: VerificationResult | None) -> None:
        with self._sf.begin() as s:
            s.add(AttemptRow(task_id=task_id, attempt_number=rec.attempt_number,
                             record_json=rec.model_dump_json(),
                             artifact_json=art.model_dump_json() if art else None,
                             verification_json=ver.model_dump_json() if ver else None))

    def list_attempts(self, task_id: str, include_code: bool = False) -> list[dict]:
        with self._sf() as s:
            rows = s.scalars(select(AttemptRow).where(AttemptRow.task_id == task_id)
                             .order_by(AttemptRow.id)).all()
            out = []
            for r in rows:
                item = {"attempt": json.loads(r.record_json),
                        "verification": json.loads(r.verification_json) if r.verification_json else None}
                if r.artifact_json:
                    art = json.loads(r.artifact_json)
                    item["artifact"] = {k: art[k] for k in ("artifact_id", "content_hash", "created_at")}
                    if include_code:
                        item["artifact"]["source_code"] = art["source_code"]
                out.append(item)
            return out

    def save_usage(self, task_id: str, usage: ModelUsage) -> None:
        with self._sf.begin() as s:
            s.add(UsageRow(task_id=task_id, provider=usage.provider, model=usage.model,
                           usage_json=usage.model_dump_json()))

    # ---- events
    def add_event(self, task_id: str, type_: str, data: dict | None = None) -> None:
        with self._sf.begin() as s:
            s.add(EventRow(task_id=task_id, type=type_, data_json=json.dumps(data or {})))

    def events_after(self, task_id: str, after_seq: int = 0, limit: int = 200) -> list[dict]:
        with self._sf() as s:
            rows = s.scalars(select(EventRow).where(EventRow.task_id == task_id, EventRow.seq > after_seq)
                             .order_by(EventRow.seq).limit(limit)).all()
            return [{"seq": r.seq, "type": r.type, "data": json.loads(r.data_json)} for r in rows]

    # ---- analytics
    def stats(self) -> dict:
        with self._sf() as s:
            tasks = s.scalars(select(TaskRow)).all()
            usages = s.scalars(select(UsageRow)).all()
        by_status = Counter(t.status for t in tasks)
        reasons = Counter(t.termination_reason for t in tasks if t.termination_reason)
        done = [t for t in tasks if t.finished_at]
        models: dict[str, dict] = {}
        for u in usages:
            d = json.loads(u.usage_json)
            m = models.setdefault(f"{u.provider}/{u.model}", {
                "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                "cost_known_calls": 0, "latencies": []})
            m["calls"] += 1
            m["input_tokens"] += d.get("input_tokens") or 0
            m["output_tokens"] += d.get("output_tokens") or 0
            m["latencies"].append(d.get("latency_seconds", 0.0))
            if d.get("cost_usd") is not None:
                m["cost_usd"] += d["cost_usd"]; m["cost_known_calls"] += 1
        return {
            "total_tasks": len(tasks), "by_status": dict(by_status),
            "termination_reasons": dict(reasons),
            "avg_duration_seconds": (sum(t.duration for t in done) / len(done)) if done else None,
            "total_cost_usd_known": sum(t.total_cost_usd for t in tasks),
            "models": models,
        }

    def ping(self) -> bool:
        try:
            with self._sf() as s:
                s.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def purge_older_than(self, days: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self._sf.begin() as s:
            ids = s.scalars(select(TaskRow.task_id).where(TaskRow.created_at < cutoff)).all()
            if ids:
                for tbl in (EventRow, UsageRow, AttemptRow):
                    s.execute(delete(tbl).where(tbl.task_id.in_(ids)))
                s.execute(delete(TaskRow).where(TaskRow.task_id.in_(ids)))
            return len(ids)


for _name in ("create_task", "set_status", "save_report", "get_task", "list_tasks", "get_report",
              "save_attempt", "list_attempts", "save_usage", "add_event", "events_after", "stats",
              "ping", "purge_older_than"):
    setattr(Repository, _name, _locked(getattr(Repository, _name)))
