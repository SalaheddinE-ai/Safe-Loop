from __future__ import annotations

import os

from ..agents.model_router import ModelRouter
from ..agents.providers.base import CodeProvider
from ..agents.providers.litellm_provider import LiteLLMProvider
from ..agents.providers.ollama import OllamaProvider
from ..core.config import Settings
from ..observability.tracing import configure_tracing
from ..persistence.database import init_db, make_engine
from ..persistence.repositories import Repository
from ..sandbox.docker_runner import DockerSandbox
from .task_manager import TaskManager


def build_manager(settings: Settings | None = None) -> TaskManager:
    settings = settings or Settings.from_env()
    configure_tracing()  # no-op unless SAFELOOP_OTLP_ENDPOINT is set and opentelemetry is installed
    repo = Repository(init_db(make_engine(settings.db_url)))
    providers: dict[str, CodeProvider] = {"ollama": OllamaProvider(
        model=os.environ.get("SAFELOOP_OLLAMA_MODEL", "qwen2.5-coder:7b"),
        base_url=os.environ.get("SAFELOOP_OLLAMA_URL", "http://localhost:11434"))}
    if os.environ.get("SAFELOOP_LITELLM_MODEL"):
        providers["litellm"] = LiteLLMProvider(os.environ["SAFELOOP_LITELLM_MODEL"])
    order = [os.environ.get("SAFELOOP_DEFAULT_PROVIDER", "ollama")]
    sandbox = DockerSandbox(settings.sandbox)  # fails closed lazily if Docker is down
    return TaskManager(repo, ModelRouter(providers, order), lambda: sandbox, settings,
                       max_workers=settings.max_concurrent_tasks, sandbox_health=sandbox.healthcheck)
