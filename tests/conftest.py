import pytest
from fastapi.testclient import TestClient

from safeloop.agents.model_router import ModelRouter
from safeloop.agents.providers.fake import FakeProvider
from safeloop.api.app import create_app
from safeloop.application.task_manager import TaskManager
from safeloop.core.config import Settings
from safeloop.persistence.database import init_db, make_engine
from safeloop.persistence.repositories import Repository
from tests.helpers import ScriptedSandbox, fail_run, ok_run


@pytest.fixture
def repo():
    return Repository(init_db(make_engine("sqlite://")))


@pytest.fixture
def sandbox():
    return ScriptedSandbox({"# GOOD": ok_run(), "# BAD": fail_run()})


@pytest.fixture
def manager(repo, sandbox):
    mgr = TaskManager(repo, ModelRouter({"fake": FakeProvider(["x = 1"])}), lambda: sandbox,
                      Settings(provider_backoff_seconds=0), max_workers=2, max_queue=2,
                      sandbox_health=lambda: (True, "test double"))
    yield mgr
    mgr.shutdown()


@pytest.fixture
def client(manager):
    return TestClient(create_app(manager, api_token=None, allow_dev_scripted=True))
