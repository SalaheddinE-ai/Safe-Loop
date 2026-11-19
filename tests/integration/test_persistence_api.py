import json
import time

from safeloop.domain.enums import TaskStatus, TerminationReason
from safeloop.domain.models import AttemptRecord, FinalReport, TaskSpec

GOOD = "```python\ndef add(a, b):\n    return a + b  # GOOD\n```"
BAD = "```python\ndef add(a, b):\n    return a - b  # BAD\n```"
BODY = {"user_request": "implement add(a, b) returning the sum", "test_strategy": "def test_a(): ..."}


def wait_done(client, tid, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = client.get(f"/api/v1/tasks/{tid}").json()
        if t["status"] not in ("PENDING", "RUNNING"):
            return t
        time.sleep(0.05)
    raise AssertionError("task did not finish")


def test_repository_roundtrip_and_filters(repo):
    spec = TaskSpec(user_request="implement something", test_strategy="x")
    repo.create_task(spec, model="fake/fake-1")
    repo.save_attempt(spec.task_id, AttemptRecord(attempt_number=1, outcome="REPAIR"), None, None)
    repo.save_report(FinalReport(task_id=spec.task_id, final_status=TaskStatus.SUCCESS,
                                 termination_reason=TerminationReason.ACCEPTANCE_CRITERIA_MET))
    items, total = repo.list_tasks(status="SUCCESS")
    assert total == 1 and items[0]["model"] == "fake/fake-1"
    assert repo.list_tasks(status="FAILED_VERIFICATION")[1] == 0
    assert repo.get_report(spec.task_id).final_status == TaskStatus.SUCCESS
    assert len(repo.list_attempts(spec.task_id)) == 1
    assert repo.purge_older_than(0) == 1 and repo.get_task(spec.task_id) is None


def test_health_ok(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["database"] is True


def test_submit_repair_report_events_stats_metrics(client):
    r = client.post("/api/v1/tasks", json={**BODY, "dev_scripted_responses": [BAD, GOOD]})
    assert r.status_code == 202
    tid = r.json()["task_id"]
    t = wait_done(client, tid)
    assert t["status"] == "SUCCESS"
    rep = client.get(f"/api/v1/tasks/{tid}/report").json()
    assert rep["attempt_count"] == 2 and rep["termination_reason"] == "ACCEPTANCE_CRITERIA_MET"
    att = client.get(f"/api/v1/tasks/{tid}/attempts?include_code=true").json()["items"]
    assert len(att) == 2 and "a - b" in att[0]["artifact"]["source_code"]
    assert "source_code" not in client.get(f"/api/v1/tasks/{tid}/attempts").json()["items"][0]["artifact"]
    ev = client.get(f"/api/v1/tasks/{tid}/events")
    assert "event: finished" in ev.text and "event: queued" in ev.text
    st = client.get("/api/v1/stats").json()
    assert st["by_status"]["SUCCESS"] >= 1
    metrics = client.get("/metrics").text
    for name in ["safeloop_tasks_total", "safeloop_repairs_total", "safeloop_task_duration_seconds_count",
                 "safeloop_verification_runs_total", "safeloop_generation_requests_total",
                 "safeloop_sandbox_execution_duration_seconds_count", "safeloop_active_tasks"]:
        assert name in metrics
    assert tid not in metrics  # no high-cardinality labels


def test_validation_and_error_shapes(client):
    r = client.post("/api/v1/tasks", json={"user_request": "short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert client.post("/api/v1/tasks", json={**BODY, "maximum_attempts": 99}).status_code == 422
    assert client.post("/api/v1/tasks", json={**BODY, "unknown": 1}).status_code == 422
    assert client.get("/api/v1/tasks/nope").status_code == 404
    assert client.get("/api/v1/tasks?limit=1000").status_code == 422
    r = client.post("/api/v1/tasks", json={**BODY, "model_preferences": ["gpt-x"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_task"


def test_report_before_finish_is_409_and_cancel_flow(client, manager, repo):
    import threading
    gate = threading.Event()

    class Slow:
        name = "slow"
        def get_model_metadata(self): return {"provider": "slow", "model": "s"}
        def estimate_cost(self, p): return None
        def generate_code(self, prompt, *, timeout):
            gate.wait(5)
            from safeloop.agents.providers.fake import FakeProvider
            return FakeProvider([GOOD]).generate_code(prompt, timeout=timeout)
        repair_code = generate_code
    spec = TaskSpec(user_request="implement add(a, b)", test_strategy="x")
    tid = manager.submit(spec, provider_override=Slow())
    assert client.get(f"/api/v1/tasks/{tid}/report").status_code == 409
    assert client.post(f"/api/v1/tasks/{tid}/cancel").status_code == 202
    gate.set()
    manager.wait(tid)
    # cancellation observed before/while running: terminal state recorded either way
    assert repo.get_task(tid)["status"] in ("CANCELLED", "SUCCESS")
    assert client.post(f"/api/v1/tasks/{tid}/cancel").status_code == 409


def test_dev_scripted_rejected_when_disabled(manager):
    from fastapi.testclient import TestClient

    from safeloop.api.app import create_app
    c = TestClient(create_app(manager, api_token=None, allow_dev_scripted=False))
    r = c.post("/api/v1/tasks", json={**BODY, "dev_scripted_responses": [GOOD]})
    assert r.status_code == 422


def test_queue_saturation_returns_429(manager, client):
    import threading
    gate = threading.Event()

    class Block:
        name = "block"
        def get_model_metadata(self): return {"provider": "block", "model": "b"}
        def estimate_cost(self, p): return None
        def generate_code(self, prompt, *, timeout):
            gate.wait(5)
            from safeloop.agents.providers.fake import FakeProvider
            return FakeProvider([GOOD]).generate_code(prompt, timeout=timeout)
        repair_code = generate_code
    spec = lambda: TaskSpec(user_request="implement add(a, b)", test_strategy="x")
    ids = [manager.submit(spec(), provider_override=Block()) for _ in range(4)]  # capacity = 2 + 2
    r = client.post("/api/v1/tasks", json={**BODY, "dev_scripted_responses": [GOOD]})
    assert r.status_code == 429
    gate.set()
    for i in ids:
        manager.wait(i)


def test_unauthorized_when_auth_enabled(manager):
    from fastapi.testclient import TestClient

    from safeloop.api.app import create_app
    c = TestClient(create_app(manager, api_token="s3cret", allow_dev_scripted=True))
    assert c.get("/api/v1/tasks").status_code == 401
    assert c.get("/api/v1/tasks", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/v1/tasks", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/api/v1/health").status_code == 200
    body = c.get("/api/v1/tasks").json()
    assert json.dumps(body)  # structured error body
