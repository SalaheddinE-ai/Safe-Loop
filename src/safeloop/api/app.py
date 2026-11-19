from __future__ import annotations

import asyncio
import hmac
import json
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError

from ..agents.providers.fake import FakeProvider
from ..application.task_manager import QueueFull, TaskConflict, TaskManager, TaskNotFound
from ..domain.exceptions import InvalidTaskError
from ..domain.models import TaskSpec
from ..observability.metrics import REGISTRY
from .ratelimit import RateLimiter
from .schemas import CreateTaskRequest, Page, TaskCreated

API_VERSION = "v1"


def _err(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message,
                                                                "details": details}})


def create_app(manager: TaskManager | None = None, *, api_token: str | None = None,
               allow_dev_scripted: bool | None = None, rate_limit_per_minute: int | None = None,
               retention_days: int | None = None, retention_interval_seconds: float = 6 * 3600) -> FastAPI:
    """MVP security note: without SAFELOOP_API_TOKEN the API is UNAUTHENTICATED and must not be
    exposed beyond localhost. No rate limiting is implemented yet."""
    token = api_token if api_token is not None else os.environ.get("SAFELOOP_API_TOKEN") or None
    dev_scripted = (allow_dev_scripted if allow_dev_scripted is not None
                    else os.environ.get("SAFELOOP_ALLOW_DEV_SCRIPTED") == "1")
    rpm = rate_limit_per_minute if rate_limit_per_minute is not None else int(
        os.environ.get("SAFELOOP_RATE_LIMIT_PER_MIN", "60"))
    limiter = RateLimiter(rpm)
    keep_days = retention_days if retention_days is not None else int(os.environ.get("SAFELOOP_RETENTION_DAYS", "30"))
    holder: dict[str, TaskManager] = {}
    if manager is not None:
        holder["m"] = manager

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async def retention_loop() -> None:
            while True:
                if "m" in holder and keep_days > 0:
                    await asyncio.to_thread(holder["m"].repo.purge_older_than, keep_days)
                await asyncio.sleep(retention_interval_seconds)

        task = asyncio.create_task(retention_loop()) if keep_days > 0 else None
        yield
        if task:
            task.cancel()
        if "m" in holder:
            holder["m"].shutdown()

    app = FastAPI(title="Safe-Loop API", version="0.1.0", lifespan=lifespan)

    def get_manager() -> TaskManager:
        if "m" not in holder:
            from ..application.bootstrap import build_manager
            holder["m"] = build_manager()
        return holder["m"]

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        if token is None:
            return
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def rate_limit(request: Request, authorization: str | None = Header(default=None)) -> None:
        key = (authorization or "") or (request.client.host if request.client else "anon")
        allowed, retry = limiter.check(key)
        if not allowed:
            raise HTTPException(status_code=429, detail="rate limit exceeded",
                                headers={"Retry-After": str(int(retry) + 1)})

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return _err(422, "validation_error", "request validation failed",
                    [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()])

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException):
        resp = _err(exc.status_code, "http_error", str(exc.detail))
        for k, v in (exc.headers or {}).items():
            resp.headers[k] = v
        return resp

    @app.exception_handler(TaskNotFound)
    async def _nf(_: Request, exc: TaskNotFound):
        return _err(404, "not_found", "task not found")

    @app.exception_handler(TaskConflict)
    async def _conflict(_: Request, exc: TaskConflict):
        return _err(409, "conflict", str(exc))

    @app.exception_handler(QueueFull)
    async def _full(_: Request, exc: QueueFull):
        return _err(429, "queue_full", "task queue is full; retry later")

    @app.exception_handler(InvalidTaskError)
    async def _invalid(_: Request, exc: InvalidTaskError):
        return _err(422, "invalid_task", str(exc))

    p = f"/api/{API_VERSION}"

    @app.get(f"{p}/health")
    def health(mgr: TaskManager = Depends(get_manager)):
        h = mgr.health()
        return JSONResponse(status_code=200 if h["status"] == "ok" else 503, content=h)

    @app.post(f"{p}/tasks", status_code=202, response_model=TaskCreated, dependencies=[Depends(require_auth), Depends(rate_limit)])
    def create_task(req: CreateTaskRequest, mgr: TaskManager = Depends(get_manager)):
        if req.dev_scripted_responses and not dev_scripted:
            raise InvalidTaskError("dev_scripted_responses is disabled on this server")
        try:
            spec = TaskSpec(**req.model_dump(exclude={"dev_scripted_responses"}))
        except ValidationError as exc:
            raise InvalidTaskError(str(exc.errors()[0]["msg"])) from exc
        override = FakeProvider(req.dev_scripted_responses) if req.dev_scripted_responses else None
        mgr.submit(spec, provider_override=override)
        return TaskCreated(task_id=spec.task_id, status=spec.status.value)

    @app.get(f"{p}/tasks", response_model=Page, dependencies=[Depends(require_auth), Depends(rate_limit)])
    def list_tasks(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                   status: str | None = None, model: str | None = None,
                   termination_reason: str | None = None, since: str | None = None,
                   mgr: TaskManager = Depends(get_manager)):
        items, total = mgr.repo.list_tasks(limit=limit, offset=offset, status=status, model=model,
                                           reason=termination_reason, since=since)
        return Page(items=items, total=total, limit=limit, offset=offset)

    @app.get(f"{p}/tasks/{{task_id}}", dependencies=[Depends(require_auth), Depends(rate_limit)])
    def get_task(task_id: str, mgr: TaskManager = Depends(get_manager)):
        t = mgr.repo.get_task(task_id)
        if t is None:
            raise TaskNotFound(task_id)
        return t

    @app.post(f"{p}/tasks/{{task_id}}/cancel", status_code=202, dependencies=[Depends(require_auth), Depends(rate_limit)])
    def cancel(task_id: str, mgr: TaskManager = Depends(get_manager)):
        mgr.cancel(task_id)
        return {"task_id": task_id, "cancel_requested": True}

    @app.get(f"{p}/tasks/{{task_id}}/attempts", dependencies=[Depends(require_auth), Depends(rate_limit)])
    def attempts(task_id: str, include_code: bool = False, mgr: TaskManager = Depends(get_manager)):
        if mgr.repo.get_task(task_id) is None:
            raise TaskNotFound(task_id)
        return {"items": mgr.repo.list_attempts(task_id, include_code=include_code)}

    @app.get(f"{p}/tasks/{{task_id}}/report", dependencies=[Depends(require_auth), Depends(rate_limit)])
    def report(task_id: str, mgr: TaskManager = Depends(get_manager)):
        if mgr.repo.get_task(task_id) is None:
            raise TaskNotFound(task_id)
        r = mgr.repo.get_report(task_id)
        if r is None:
            raise TaskConflict("report not available yet: task has not finished")
        return json.loads(r.model_dump_json())

    @app.get(f"{p}/tasks/{{task_id}}/events", dependencies=[Depends(require_auth), Depends(rate_limit)])
    async def events(task_id: str, mgr: TaskManager = Depends(get_manager), max_seconds: float = 900.0):
        if mgr.repo.get_task(task_id) is None:
            raise TaskNotFound(task_id)

        async def gen():
            seq, waited = 0, 0.0
            while waited < max_seconds:
                for ev in mgr.repo.events_after(task_id, seq):
                    seq = ev["seq"]
                    yield f"id: {seq}\nevent: {ev['type']}\ndata: {json.dumps(ev['data'])}\n\n"
                    if ev["type"] == "finished":
                        return
                await asyncio.sleep(0.25)
                waited += 0.25
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get(f"{p}/stats", dependencies=[Depends(require_auth), Depends(rate_limit)])
    def stats(mgr: TaskManager = Depends(get_manager)):
        return mgr.repo.stats()

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    return app


def app_factory() -> FastAPI:  # uvicorn --factory safeloop.api.app:app_factory
    return create_app()
