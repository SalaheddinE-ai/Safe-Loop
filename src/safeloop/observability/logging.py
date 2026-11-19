"""Structured JSON logging. Prompts and generated code are never logged by default."""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

SERVICE = "safeloop"
_SECRET_KEYS = re.compile(r"(?i)(key|token|secret|password|authorization|credential)")
_SECRET_VALUE = [re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}")]
_FORBIDDEN_FIELDS = {"prompt", "source_code", "code", "raw_output"}  # unless debug flag is added later


def redact(value):
    if isinstance(value, str):
        for p in _SECRET_VALUE:
            value = p.sub("[REDACTED]", value)
        return value
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(), "severity": record.levelname,
            "service": SERVICE, "event": record.getMessage(),
        }
        extra = getattr(record, "fields", {})
        payload.update(redact({k: v for k, v in extra.items() if k not in _FORBIDDEN_FIELDS}))
        return json.dumps(payload, default=str)


def get_logger(name: str = SERVICE) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(JsonFormatter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    get_logger().log(level, event, extra={"fields": fields})
