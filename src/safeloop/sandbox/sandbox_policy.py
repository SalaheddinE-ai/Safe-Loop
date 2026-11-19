from __future__ import annotations

import re

from ..core.config import SandboxLimits
from ..domain.exceptions import PolicyViolation

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+\.py$")
ALLOWED_FILES = {"solution.py"}  # candidate
MAX_FILES = 4


def validate_limits(limits: SandboxLimits) -> None:
    if limits.network_enabled:
        raise PolicyViolation("sandbox_network", "network access is not allowed in this release")
    if limits.memory_mb <= 0 or limits.pids_limit <= 0 or limits.wall_clock_seconds <= 0:
        raise PolicyViolation("sandbox_limits", "limits must be positive")


def validate_files(files: dict[str, str], max_bytes_each: int = 128 * 1024) -> None:
    if not files or len(files) > MAX_FILES:
        raise PolicyViolation("sandbox_files", "invalid number of files")
    for name, content in files.items():
        if not _SAFE_NAME.match(name):
            raise PolicyViolation("sandbox_files", f"unsafe file name: {name!r}")
        if len(content.encode("utf-8")) > max_bytes_each:
            raise PolicyViolation("sandbox_files", f"file too large: {name}")
