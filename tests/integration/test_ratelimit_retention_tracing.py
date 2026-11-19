import time

from fastapi.testclient import TestClient

from safeloop.api.app import create_app
from safeloop.api.ratelimit import RateLimiter
from safeloop.domain.models import TaskSpec
from safeloop.observability import tracing


def test_token_bucket_math_and_isolation():
    t = [0.0]
    rl = RateLimiter(60, burst=2, clock=lambda: t[0])
    assert rl.check("a")[0] and rl.check("a")[0]
    ok, retry = rl.check("a")
    assert not ok and 0 < retry <= 1.0
    assert rl.check("b")[0]  # other client unaffected
    t[0] += 1.0
    assert rl.check("a")[0]  # refilled
    assert RateLimiter(0).check("x") == (True, 0.0)  # disabled


def test_token_bucket_memory_is_bounded():
    rl = RateLimiter(60, max_keys=5)
    for i in range(50):
        rl.check(f"k{i}")
    assert len(rl._b) <= 5


def test_api_returns_429_with_retry_after(manager):
    c = TestClient(create_app(manager, api_token=None, rate_limit_per_minute=2))
    assert c.get("/api/v1/tasks").status_code == 200
    assert c.get("/api/v1/tasks").status_code == 200
    r = c.get("/api/v1/tasks")
    assert r.status_code == 429 and "retry-after" in r.headers and r.json()["error"]["code"] == "http_error"
    assert c.get("/api/v1/health").status_code == 200  # probes are not limited


def test_retention_loop_purges_old_tasks(manager, repo):
    spec = TaskSpec(user_request="implement something", test_strategy="x")
    repo.create_task(spec)
    with TestClient(create_app(manager, api_token=None, retention_days=1, rate_limit_per_minute=0,
                               retention_interval_seconds=3600)):
        time.sleep(0.3)
        assert repo.get_task(spec.task_id) is not None  # recent task kept
    assert repo.purge_older_than(0) == 1  # and the purge primitive removes it


def test_tracing_noop_by_default_and_real_spans_when_configured():
    tracing.reset_tracing()
    with tracing.span("x", a=1):
        pass  # must not raise without configuration
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exp = InMemorySpanExporter()
    assert tracing.configure_tracing(exporter=exp)
    try:
        with (tracing.span("safeloop.task", task_id="t1", obj=object()),
              tracing.span("safeloop.model_call", kind="generate")):
            pass
        names = [s.name for s in exp.get_finished_spans()]
        assert names == ["safeloop.model_call", "safeloop.task"]  # child finishes first
        child, parent = exp.get_finished_spans()
        assert child.parent.span_id == parent.context.span_id
    finally:
        tracing.reset_tracing()
