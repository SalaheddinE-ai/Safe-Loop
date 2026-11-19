from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SandboxLimits:
    image: str = "safeloop-sandbox:1"
    cpu_count: float = 1.0
    memory_mb: int = 256
    pids_limit: int = 64
    tmpfs_mb: int = 32
    wall_clock_seconds: float = 30.0
    max_output_bytes: int = 64 * 1024
    network_enabled: bool = False  # hard default; enabling is rejected by SandboxPolicy


@dataclass(frozen=True)
class Settings:
    max_artifact_bytes: int = 64 * 1024
    max_log_bytes: int = 64 * 1024
    max_concurrent_tasks: int = 4
    model_call_timeout_seconds: float = 60.0
    provider_max_retries: int = 2
    provider_backoff_seconds: float = 0.5
    # Pricing is configurable and timestamped; never assumed.
    pricing_usd_per_1k_tokens: dict[str, tuple[float, float]] = field(default_factory=dict)
    pricing_as_of: str | None = None
    db_url: str = "sqlite:///safeloop.db"
    retention_days: int = 30  # 0 disables purging
    rate_limit_per_minute: int = 60  # 0 disables
    sandbox: SandboxLimits = field(default_factory=SandboxLimits)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_url=os.environ.get("SAFELOOP_DB_URL", "sqlite:///safeloop.db"),
            retention_days=int(os.environ.get("SAFELOOP_RETENTION_DAYS", "30")),
            rate_limit_per_minute=int(os.environ.get("SAFELOOP_RATE_LIMIT_PER_MIN", "60")),
            max_concurrent_tasks=int(os.environ.get("SAFELOOP_MAX_CONCURRENT_TASKS", "4")),
            sandbox=SandboxLimits(image=os.environ.get("SAFELOOP_SANDBOX_IMAGE", "safeloop-sandbox:1")),
        )
